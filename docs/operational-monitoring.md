---
layout: default
title: Readiness incidents and retention
---

# Operational monitoring

Schema `007_operational_incidents` adds a replayable `incident_projections` read
model to the existing event journal. Notifications use the existing transactional
outbox; there is no separate queue, retry engine, Telegram poller or trade consumer.

## Health contract

- `/healthz`: process liveness.
- `/readyz`: passive, current-run freshness; HTTP 200 when ready, 503 otherwise.
  Includes run/start/check times, component observations, stream connection, event
  loop lag, entry/close recovery, halt and outbox backlog/dead letters.
  `BoundedTransport` retries an idempotent Alpaca GET once on a transport-level
  connection failure (reset by peer or connect timeout — e.g. the daily-panel
  worker's periodic `/calendar` poll on a pooled connection that idled for
  minutes), so a single transient reset is no longer reported as a failed worker
  poll; mutations still never retry automatically.
- `doctor --readiness`: validates that endpoint's types, aggregate status, HTTP
  status and observation age. It performs no DB writes or external service probes.
- `doctor`: active CLI diagnostics, including DB migration checks and external
  API/LLM calls. Connection destinations, credentials and chat identifiers are not
  included in the diagnostic report. The former HTTP `/healthcheck` returns 404.
- `doctor --monitor`: consumes the same readiness contract, persists observations
  and incident transitions, queues notifications and runs due retention maintenance.

`launchd.sh watchdog` runs the monitor every 60 seconds after checking the daemon
registration/PID. Missing/stopped processes retain the existing recovery behavior.
A running unready process is reported, not repeatedly restarted. The external
probe can detect an event-loop stall that prevents an in-process monitor running.
Non-ready doctor calls exit nonzero; the watchdog records that result in its logs.

Readiness does not itself change the trading halt or certify entry eligibility.
A deliberate `/panic` halt therefore appears as an operational incident until
cleared. Each entry still performs its own session, quote, macro and risk checks.

### `macro_calendar`

A computed check, like `trading_halt` and `entry_cancellation`, not an observed
component. It is ready iff the static tier-1 calendar (`config/calendar/us-tier1-vN.json`,
see [renewal](production.md#tier-1-calendar-renewal)) is `ok` with at least
`risk.macro_calendar_renewal_days` (default 30) New York days left. The detail is stable text:

- `us-tier1-v1 valid through YYYY-MM-DD (N days)`
- `us-tier1-v1 expires YYYY-MM-DD (N days); publish v2`
- `us-tier1-v1 expired YYYY-MM-DD; lockout unverified (policy open)`
- `us-tier1-v1 not valid until YYYY-MM-DD; lockout unverified (policy open)`
- `invalid: DigestMismatch`, or another exception type name for a calendar that fails to load

The live-feed status is deliberately not part of readiness: it is journaled per scan,
and the static calendar stays authoritative through a feed outage. The monitor's existing
debounced incident covers this check, so it needs no monitor change. A failed check does
not change the trading halt.

### Scan notices

Three scan-path conditions queue a plain Telegram message through the existing outbox,
each deduplicated by its outbox key, so a repeat adds no second message:

| Outbox key | Sent when | Once per |
| --- | --- | --- |
| `pead-unavailable/{et_date}` | The 10:35 PEAD drift decision could not be prepared (`pead_decision` `status: unavailable`) | New York date |
| `suggestion-scan-missed/{et_date}/{HH:MM}` | APScheduler dropped a suggestion-scan slot past its misfire grace ([production](production.md)) | slot and New York date |
| `regime-unavailable/{et_date}` | A scheduled suggestion scan's regime read raised (a missing VIX, or any other error by type name), so the scan was skipped | New York date |

`regime-unavailable` reads, for example, "⚠️ Market regime unavailable (vix: ReadTimeout)
at 10:35 NY: the suggestion scan was skipped — no cards, no PEAD decision. Later scans
retry; this notice is sent once per New York date." It names inputs and exception type
names only. It is written in the same transaction as the scan's `scan_candidates_ranked`
event (`status: regime_unavailable`, `gate.missing`, `gate.errors`), so the event and the
notice commit or roll back together. The notice is queued only by a scheduled suggestion
scan, because it exists to say a card slot (and the 10:35 PEAD decision) was lost: the swing
job, an operator `/scan`, the chat tool and the CLI journal the event (when budgeted) and
observe `scan` not ready, but never spend the date's notice, and an operator gets the scan's
own reply. A restricted or dry scan journals nothing and sends nothing; a dry run prints the
reason. A failed VIX read is read once more, fresh, after 2 seconds (a `vix_read_retry`
warning); only a second failure is a missing VIX. No other input is retried. `/macro`, the
macro button and the morning macro briefing answer "Market regime unavailable (vix:
ReadTimeout); scans skip until VIX is readable."

The health signal is the existing `scan` readiness component, observed not ready with
"Regime unavailable (vix: ReadTimeout); scan skipped": a lost scan is a scan failure, and
the monitor's debounced `scan` incident pages on it. There is deliberately no
`macro_inputs` readiness component. Macro inputs are read only inside scans, taps and
`/macro` (and a scan that is idle or locked out reads none), so an observed component
would go stale every night and weekend; a missing non-VIX decision input is already
handled conservatively (the `UNKNOWN` stress policy) and journaled with names, sources and
observation dates on every scan; and the case that must page, a missing VIX, already does
so through `scan` readiness and this notice. A `macro_inputs` component with per-input age
contracts belongs with the macro source upgrade, once observation dates have an agreed
staleness rule.

## Incident lifecycle and delivery

The pure state machine progresses through `healthy → pending → open → recovering
→ healthy`. A brief failure clears without a message. Sustained failure opens an
incident; recovery requires a stable healthy interval. A relapse while recovering
returns to open. Observations are periodic samples, not proof of continuous health
between samples. A startup grace period suppresses failing component observations;
an unreachable endpoint has no startup evidence and does not get that exemption.

State transitions, journal events and notification intents commit together under
the existing scope lock. A component's latest observation timestamp rejects older
or duplicate probes from concurrent supervisors. Ordinary unchanged healthy samples
only update that watermark. Missing checks never clear existing incidents.
`db incidents --rebuild` replays lifecycle events and retains newer observation
watermarks; it never enqueues or sends notifications.

Disabling notifications suppresses new intents while preserving incident state.
Re-enabling them reports an incident that opened while muted on the next failing
observation; already closed incidents are not replayed.

A reminder is eligible only after the previous notice is **delivered**. Pending or
dead notices suppress further reminders. Recovery still records a separate notice.
Notices include component, incident ID and observation time. Delivery is at least
once, may be delayed or out of order, and never proves the current state; inspect
readiness and the incident journal. Dead letters remain visible and require explicit
`db outbox --retry JOB_ID` after fixing delivery. Requeue can duplicate a message.

Normally the daemon's outbox worker delivers alerts. If the endpoint or delivery
worker is unavailable, the watchdog can claim **one** existing outbox job using
the same fencing/retry protocol and Telegram transport. It does not call
`getUpdates` or consume entries. Existing earlier jobs may precede a new alert.
If the host, PostgreSQL or Telegram is unavailable, this mechanism cannot guarantee
notification; an independent external monitor/destination remains recommended.

## Configuration

All policy below is under `operations` in YAML; omitted values use validated defaults.
The installed launchd sampling interval is separately defined by its plist.

| Setting | Default | Meaning |
| --- | --- | --- |
| `notifications_enabled` | `true` | Persist incidents either way; enable new alert intents |
| `startup_grace_seconds` | `120` | Grace for failing components of a reachable new daemon |
| `failure_seconds` | `120` | Sustained failure before opening |
| `recovery_seconds` | `60` | Healthy period before recovery notification |
| `reminder_seconds` | `3600` | Minimum time between notices; prior delivery required |
| `probe_timeout_seconds` | `5` | Passive HTTP request timeout |
| `snapshot_max_age_seconds` | `30` | Maximum accepted readiness report age |
| `healthy_observation_days` | `30` | Age before redundant healthy samples qualify for compaction |
| `retention_interval_seconds` | `3600` | Minimum interval between automatic sweeps |
| `retention_batch_size` | `1000` | Maximum health events deleted per sweep (max 10000) |

The debounce clock begins with the first failed sample. With 60-second polling and
120-second failure debounce, alert creation normally needs three failing samples.
Scheduling, startup grace and outbox delivery can add delay. Existing component
freshness thresholds remain under `telemetry` and `accounting`; monitoring does not
define another copy of those thresholds.

## Retention contract

The maintenance repository uses a separate resource lock, so PostgreSQL compaction
does not hold entry admission's scope lock. It deletes only schema-1
`health_observed` events older than the configured cutoff whose payload is exactly
successful with blank detail, whose preceding observation in that stream is the
same schema/payload, and which have a following observation. It preserves:

- Every failure and nonempty detail, plus first recovery observations.
- The first and latest observation in each run/component stream.
- All financial/account/order/incident events, work items and deduplication keys.
- All dead letters and operational `audit_events`.

A journal record retains cutoff, eligible/deleted counts and deleted ID bounds.
This compacts repeated evidence; it does not archive financial history or bound
total DB/log size. Back up PostgreSQL and monitor storage growth. Do not add a
blanket TTL to events or delivery keys: that breaks replay and duplicate protection.

```bash
uv run copilot doctor --readiness
uv run copilot doctor --monitor      # stateful supervisor pass; can deliver outbox work
uv run copilot db incidents
uv run copilot db events --stream incident/outbox
uv run copilot db incidents --rebuild
uv run copilot db retention          # preview only
uv run copilot db retention --apply  # one bounded batch
uv run copilot db outbox
```

## Deployment and verification

Develop supervisor changes in an isolated worktree. The registered watchdog runs
repository scripts directly; merely leaving the daemon on an old PID does not
isolate script edits. Finish integration/pre-commit/CI, then pause watchdog and
daemon before updating the installed checkout, backing up and applying migration
007. Restore daemon then watchdog. Verify current revision/run, fresh readiness,
healthy incident projections and the watchdog's completed external observations.

Fault injection belongs in isolated tests: real loopback HTTP, actual Telegram SDK
transport and a disposable PostgreSQL database exercise failures, delivery and
races. Do not create synthetic incidents or test messages in production to prove
alerting. Read-only broker/Telegram checks establish deployed connectivity/freshness
separately; neither source tests nor a healthy PID alone establishes that.
