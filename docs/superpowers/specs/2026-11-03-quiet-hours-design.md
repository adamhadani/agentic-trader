# Monitor quiet hours: hold incident alerts outside market hours, one morning summary

Operator request, 2026-11-03: "quiet the overnight alerts". The operator holds standing design trust ("go with your
recommended approaches"), so this document records Claude's calls; none was asked.

## Why

On the night of 2026-11-02, between 19:46 and 03:27 ET, the desk sent about 35 Telegram incident notices: openings,
hourly reminders and recoveries. Every one came from the host sleeping after the awake agent's window
(`scripts/awake.sh`, weekdays 09:15–16:30 New York). While the host is asleep, scheduled work misses its slot.
`doctor --monitor` then runs in brief dark wakes, finds stale components and pages.

The notices by component since 2026-10-20 were `scan`, `accounting`, `telegram`, `reconciliation`, `alpha_observer`,
`alpha_decisions` and `worker`, plus `trading_halt` reminders while the operator's halt was in force. None came from
the broker-work checks (`entry_recovery`, `entry_cancellation`, `close_recovery`). The noise buries the cards, which
are the point of the Telegram channel, and teaches the operator to ignore alerts.

## Goals

- Outside the alert window, queue no incident notice (opened, reminder or recovered) for an ordinary component.
- Keep every incident transition in the journal, recording which notice was withheld and why.
- At the start of the next alert window, send at most one summary of what was held and what is still open.
- At the window start, do not page on staleness that clears within the normal debounce, such as a host that has
  just woken.
- Keep broker-work checks paging at every hour, together with the checks whose failure hides them (`endpoint`,
  `storage`).

## Non-goals and anti-goals

- **Not changing what readiness measures.** Freshness limits, the 4-hourly swing job's `scan` observations and the
  incident state machine's debounce and recovery hysteresis are unchanged.
- **No suppression during the alert window.** A wake burst at 13:15 on a weekday still pages, as today. Detecting a
  host wake (a grace after a gap in monitor sampling) is a possible follow-up, not this change.
- **No heartbeat.** An empty quiet period sends nothing.
- **No restart, no halt change and no outbox semantics change** (contract 8). Delivery stays at least once.
- **Anti-goal: an unknown setting must never mute a critical check.** An exempt-component typo has to fail config
  load, not quietly drop a component from the exemption.

## The alert window (Claude's call)

The alert window covers New York weekdays that are not full NYSE holidays (`MarketHolidayCalendar.is_equity_holiday`,
which is deterministic and offline), from `alert_start_et` (09:15) up to but excluding `alert_end_et` (16:30). Every
other moment is quiet time.

- **Why these times:** they match `scripts/awake.sh`, so the host is held awake whenever alerts can page. The
  summary arrives 15 minutes before the open and 80 minutes before the 10:35 scan.
- **Calendar handling:** times are New York wall-clock (`zoneinfo` `America/New_York`), so DST changes need no
  handling. Early-close days keep the full window, which is harmless.
- **Quiet period:** the one that ends at a window's start began at the end of the previous alert day's window. That
  means Friday 16:30 → Monday 09:15, and a holiday stretches the period. The search back is bounded at 14 days.

## Config (`operations.quiet_hours`)

| Setting | Code default | Shipped `config.yaml` | Meaning |
| --- | --- | --- | --- |
| `enabled` | `false` | `true` | Hold ordinary incident notices outside the alert window |
| `alert_start_et` | `"09:15"` | — | Start of the alert window, New York time |
| `alert_end_et` | `"16:30"` | — | End of the alert window (exclusive) |
| `exempt_components` | `[entry_recovery, entry_cancellation, close_recovery, endpoint, storage]` | — | Checks that page at any hour |

- **Validation:** `alert_start_et < alert_end_et`. Every exempt name must be a known readiness check name: the
  `HealthComponent` values, the computed checks in `ReadinessService.report` (`daemon_started`, `alpha_registry`,
  `entry_recovery`, `entry_cancellation`, `outbox`, `close_recovery`, `trading_halt`, `broker_stream`,
  `macro_calendar`, `event_loop`, `storage`), and the monitor's `endpoint`. The set is one constant in `config.py`,
  and a test pins it against `HealthComponent` and a fully enabled readiness report.
- **Broker-work checks are required:** `exempt_components` replaces the default list, so config load fails unless it
  still contains `entry_recovery`, `entry_cancellation` and `close_recovery` ("broker-work checks always page").
  `endpoint` and `storage` may be removed deliberately.
- **Quoted times:** the times must be strings such as `"09:15"` (or `datetime.time`). YAML 1.1 reads an unquoted
  `10:00` as the integer 600, which pydantic would turn into `00:10+00:00` and so mute market hours; config load
  rejects numbers and any time with a UTC offset, with a message to quote the time.
- **Defaults:** the code default is off, following the repo's pattern of conservative code defaults with policy in
  config. `config.yaml` ships it on.
- **`endpoint` and `storage` are exempt (Claude's call, final review).** When the daemon is unreachable the report
  holds only `endpoint`, and when the readiness store raises it holds `storage` in place of the broker-work checks,
  so muting either would hide unresolved broker work all night. Neither has sent a notice since 2026-10-20 and
  `/readyz` is computed per request, so exempting them adds no sleep noise.
- **`trading_halt` is not exempt (Claude's call).** A halt is sticky: it stays open until `/resume` and reminds
  hourly. If it were exempt, the halt reminders would run all night, as they did through the 10-26 → 10-29 halt. The
  morning summary lists an open halt first.

## Behaviour

### Withheld notices

The monitor computes `muted = enabled and component not exempt and observed_at not in an alert window`. It passes
this to the state machine together with `alert_since`, the **alert clock** (`None` in quiet time, when quiet hours
are disabled, and always for an exempt component, which is never held and so keeps today's debounce).

**The alert clock starts at the first pass inside the window** (final review). launchd does not wake a sleeping
host, so the first pass can come long after 09:15; measured from 09:15, every held incident would page at once on
that pass and send `recovered` a minute later. Once per pass the monitor:

1. Takes `since`, the start of the alert window containing `observed_at`.
2. If `since` is set and notifications are enabled, runs the fail-open summary decision (below), then, in its own
   fail-open `try`, reads `OperationsStore.summary_decided_at(since)`: the payload `observed_at` of that window's
   `operations_summary` decision event, which is the first pass's time. If it is set, `since = max(since, decided)`.
3. If the summary build or the read fails, or notifications are disabled, `since` stays at the window start. That
   never mutes; at worst a held incident pages early.

The state machine (`diagnostics/incidents.py:advance`) is unchanged in how phases move. Only the notice decision
changes, in the following order:

1. Compute the would-be notice exactly as today.
2. An `opened` notice is due only once `observed_at − max(failed_since, alert_since) ≥ failure_seconds`. This
   applies both to an incident that was never announced (`last_notified_at is None`, phase stays `open`) and to the
   PENDING → OPEN move. The phase still moves to `open` on the plain debounce; if the notice is not due yet, the
   incident is `open` and unannounced, and the unannounced-`open` rule announces it later. With
   `alert_since = None`, or `failed_since ≥ alert_since`, this is today's behaviour. An incident that opens (or goes
   pending) while quiet and is still failing at the window's first pass therefore pages only if it is still failing
   `failure_seconds` (120 s) after that pass.
3. A `recovered` notice for an incident that was never announced is withheld, with reason `not_announced`. A
   recovery is news only when there was an opening.
4. Otherwise, if `muted`, withhold the notice with reason `quiet_hours`. If notifications are disabled, withhold it
   with reason `notifications_disabled`. Precedence runs top to bottom: `quiet_hours`, then
   `notifications_disabled`, then `not_announced`. A withheld notice never sets `last_notified_at`.

`advance` returns the new state, the notice to queue (or `None`), and the withheld notice with its reason (or
`None`). When the state changes, `OperationsStore.observe` writes the withheld notice into the `incident_changed`
payload as `"withheld": {"kind": "<notice>", "reason": "<reason>"}` (`null` otherwise).

- **Reminders:** a withheld reminder changes no state, so it journals nothing.
- **Events and replay:** the schema version stays 1. The field is additive, and `rebuild()` reads only
  `component`, `observed_at` and `state`.
- **No migration:** schema head stays 008.

**Behaviour change to document:** once notifications are disabled and later re-enabled, an incident that opened
while muted and recovered before any failing observation used to send `recovered`. Under rule 3 it is now silent.

### Morning summary

On every pass, before observing components, the monitor asks the store for the summary of the quiet period that
ended at the start of the current alert window (when quiet hours are enabled, notifications are enabled and the
pass is inside a window). It passes this pass's details and `ready_now`, the components whose check is ready. The
store, under the scope lock and in one transaction:

1. Returns early if a decision for the window exists: a `DomainEventRecord` with event key `operations-summary/{window start New York date}` (one key helper, shared with `summary_decided_at`), checked once before the lock (a plain indexed read) and again under it.
2. Reads the scope's `incident_projections` rows. Every component that ever journaled an incident has one, so
   their names give the complete list of `incident/{component}` streams.
3. Reads the `incident_changed` events in those streams. SQL bounds the read by `stream IN (…)` (index
   `ix_domain_event_stream`; unlike a `'incident/' ≤ stream < 'incident0'` range it does not depend on the
   database collation) and by `recorded_at` within one hour either side of the period; one read of that shape
   measured 3.7 ms on production. Python then keeps exactly the events whose payload `observed_at` falls in
   `[period_start, period_end)` and whose `withheld.reason` is `"quiet_hours"`. The open incidents are the
   projections whose phase is `open` or `recovering`.
4. If both reads are empty, it queues nothing. Either way it appends one `operations_summary` event (stream
   `operations/summary`, that key, payload `queued`) in the same transaction, so the first pass of each alert window
   decides once and an empty decision is final for that window. A build failure rolls back and the next pass retries.
5. Otherwise it also queues one `NotificationKind.OPERATIONS_SUMMARY` work item carrying a typed `QuietHoursSummary`
   payload.

The summary contains:

- `period_start`, `period_end` and `observed_at` (the pass time).
- Per component, the counts of withheld `opened` and `recovered` notices. These are sorted by total, then name, and
  capped at 12 lines with "+N more components".
- The open incidents: component, phase, `failed_since`, `ready_now` and detail, truncated to 120 characters.
  `trading_halt` comes first, then the rest by `failed_since`, capped at 10 with "+N more". The phase comes from
  the projection before this pass, while `ready_now` (the component's check is ready on this pass) and the detail
  come from this pass. An open incident that is ready now renders as `(open since … ET; ready now)` without its
  detail; a recovering one renders as `(recovering; failing since … ET)`, with its detail only when it is not ready
  now. The tally overflow line reads "+1 more component" or "+N more components".

The rendered text (`presentation/operations.py`) uses type and component names only, the same as today's notices.
New York times are written like `Mon 16:30`.

```
<b>{APP}: Overnight operations summary</b>
Quiet hours Mon 16:30 → Tue 09:15 ET: 14 alerts and 13 recoveries held across 9 components.
• accounting: 2 opened, 2 recovered
• scan: 1 opened
Still open:
• trading_halt (open since Tue 03:00 ET): …
• scan (open since Mon 22:20 ET): Observation age …
• accounting (open since Mon 23:05 ET; ready now)
• telegram (recovering; failing since Tue 01:10 ET)
An incident still failing 2 min after this summary sends its own alert.
Incidents that open and recover overnight usually mean the host slept (pmset -g log).
Inspect copilot doctor --readiness and copilot db incidents.
```

- **Timing:** the summary is queued before this pass's component observations, so it describes the state at the
  end of the quiet period, and any `opened` alert follows it.
- **Duplicates and failures:** concurrent supervisors are deduplicated by the outbox key. A summary that fails to
  build raises in `monitor_once`, as any store failure does today. The watchdog logs it, and incident observation
  for that pass is lost exactly as a store outage loses it. **Claude's call:** build the summary inside a
  `try`/`except Exception` that logs (`quiet_summary_failed`) and continues, so a summary bug can never stop the
  component observations that follow it. The decision read has its own `try` and logs `quiet_summary_read_failed`;
  either failure leaves the alert clock at the window start.

### Unchanged

- Startup grace, the debounce, recovery hysteresis, the reminder delivery requirement and `db incidents --rebuild`.
- Watchdog delivery of one existing outbox item, and the scan notices (`pead-unavailable`, `regime-unavailable`,
  `suggestion-scan-missed`), which are plain messages and not incidents.
- Exempt components behave exactly as today at every hour.

## Docs and contracts

- `docs/operational-monitoring.md`: a "Quiet hours" section and config rows.
- `docs/production.md`: one line that the alert window should stay aligned with `scripts/awake.sh`.
- `CLAUDE.md` contract 8: one sentence, saying that outside the `operations.quiet_hours` alert window (NYSE
  weekdays 09:15–16:30 ET) ordinary incident notices are withheld and journaled, then summarized once by the
  window's first pass, which also starts the alert clock, while `entry_recovery`, `entry_cancellation`,
  `close_recovery`, `endpoint` and `storage` page at any hour.

## What is left to the builder

Names of private helpers, the exact module split inside `diagnostics/`, and test layout, which follows the existing
`tests/operations/` files.

## Verification after deploy

- `verify_runtime` passes, and `load_config().operations.quiet_hours.enabled` is true.
- The first quiet period starts at 16:30 ET on the deploy day. The next morning, the journal shows `withheld`
  events for the night, no incident notices were queued between 16:30 and 09:15 except exempt ones, and one summary
  arrives on the first pass after 09:15 ET.
