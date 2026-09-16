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
