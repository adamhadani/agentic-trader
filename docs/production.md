---
layout: default
title: Production Operations — Agentic Trader
---

# Production operations

Current local baseline: **Alpaca paper**, PostgreSQL, one macOS launchd daemon and
one Telegram poller. `EXECUTION_MODE=alpaca` plus `ALPACA_PAPER=true` selects the
broker's paper account. `EXECUTION_MODE=paper` selects the local simulator.

See [development notes](development-notes.md), [CLI reference](cli-reference.md),
[incident remediation](incident-2026-09-15.md), and [architecture review](architecture-review.md).

## Ownership and scheduling

`com.agentictrader.copilot` runs `uv run copilot daemon` from this checkout after
its launchd shell sources `.envrc`. `com.agentictrader.watchdog` checks the PID and runs the external readiness monitor every
60 seconds; `com.agentictrader.alphaminer` runs weekly. Do not start a second daemon,
`listen`, or Compose service while the installed poller owns the bot.

- Swing scans: every four hours from startup, immediate first run, all timeframes.
- Intraday scans: every 15 minutes from startup, session gated, `15m` filter.
- Position monitor: every minute, with additional broker stream wakeups.
- Macro briefing: weekdays 12:30. Automatic legacy retuning is removed. Cron schedules
  inherit scheduler/system timezone. Intervals are not candle-close aligned.
- Alpha miner: Saturday 03:00 local launchd time; ETF32, 9 genetic + 7 catalog trials/symbol,
  5y daily Alpaca data, 120s compute/symbol; no automatic qualification or promotion.

Scans stage suggestions; an operator approves entry orders. Configuration loads at construction. The journal-backed alpha registry reloads
atomically between scans; external config edits still require restart.

## Configuration and state

Production `load_config()` merges YAML, `.envrc` values and environment overrides,
without modifying the process environment. Explicit environment values (even empty
ones) win. `COPILOT_ENV_FILE=''` disables dotenv. `COPILOT_CONFIG` selects a YAML.

Database precedence: `DB_PATH` → `DATABASE_URL` → explicit `DB_NAME` → YAML →
production default. `--db-path` accepts a URL or SQLite path; `--db-name` explicitly
selects a SQLite sandbox. There is no automatic SQLite failover. `data/signals.db`
is historical and must not be mistaken for the current PostgreSQL database.

Head migration is `008_alpha_pipeline`. `signals`, `system_state` and
`audit_events`, `close_requests`, `work_items`, `domain_events`, `order_projections`,
`workflow_locks`, `activity_projections`, `ledger_checkpoints`, `incident_projections` and `alpha_projections` hold trading and
operational state. Construction currently checks
migrations, even for informational copilot commands. Back up PostgreSQL before
schema or historical repairs. Do not use `db clear` to fix contamination: quarantine
preserves original evidence and removes rows from operational queries.

## Broker accounting and reports

`copilot positions` and `/positions` use one Alpaca account snapshot for quantity,
average entry, current price and unrealized P&L. Source/retrieval time and tracking
mismatches are shown and audited. Broker-only positions are displayed once. A
failed snapshot reports an error; another feed or zero is not substituted.

`/perf` and `copilot perf` separate reconciled account performance from tracked
full-close statistics. The account section includes partial/external fills and
signed fees/income, uses broker cost basis and withholds realized totals on
cash/inventory mismatch, unsupported activity or stale/failed imports. Telegram
uses the timestamped worker cache; CLI `perf` refreshes it read-only first. The
60-second accounting worker has a separate current-run readiness check. This is
all available history, not account-day return or tax accounting. See the
[ledger contracts and recovery commands](account-ledger.md).

Tracked metrics still require exact entry/exit IDs, actual full fill prices and
chronological order; quarantined/unverified signals are excluded. Account-level
partial realization does not assign partial exits to a signal by symbol.

Alpaca manual close submits an order and waits for confirmed fill accounting.
A requested `exit_price` cannot become brokerage profit. Panic persists the halt
first and requests closes; inspect broker positions for pending or failed exits.

## Safe verification

```bash
launchctl list | rg 'com\.agentictrader\.'
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:9108/healthz
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:9108/metrics
uv run copilot db current
uv run copilot db audit --limit 20
uv run copilot positions
```

`/healthz` proves liveness; `/readyz` and `copilot doctor --readiness` assess passive
current-run freshness. `copilot doctor` and `launchd.sh health` perform active CLI
diagnostics, including migrations and an LLM request. HTTP `/healthcheck` is removed. A separate
`copilot metrics` instance does not contain the running daemon's metrics.

```bash
uv run copilot scan --dry-run --no-llm --symbols IWM
uv run copilot test-alert
```

A dry scan has an empty temporary database and simulated portfolio, no Telegram,
no broker connection/execution and no monitoring. Market-data calls still occur.
`test-alert` previews locally. Explicit `test-alert --send` requires a different
`TELEGRAM_TEST_BOT_TOKEN` and `TELEGRAM_TEST_CHAT_ID` and sends a labeled,
non-actionable message without persistence. Never test order buttons using real
production signals merely to check connectivity.

## Controlled maintenance and restart

1. Finish isolated tests, type/lint checks and `uv run pre-commit run --all-files`.
   Commit/merge in an isolated worktree so the daemon's startup revision is identifiable.
   Supervisor scripts execute directly from the installed checkout every minute;
   pause services before pulling the merged code into that checkout.
2. For a brief ordinary restart use `./scripts/launchd.sh restart`. For a maintenance
   pause, unload the watchdog first, then the daemon. A plain `stop` is not durable
   while the watchdog is loaded. Preserve the installed plist files.
3. Update the installed checkout to the reviewed merged revision and back up PostgreSQL.
   Apply migrations/validated data repair. Keep original rows/snapshots; run the
   incident repair tool in preview mode before `--apply`.
4. Reload the daemon and then watchdog. The installed launch agents reside under
   `~/Library/LaunchAgents/`; use `launchctl bootout/bootstrap gui/<uid>` with their
   exact labels/plist paths. Editing `scripts/launchd.sh` does not regenerate plists.
5. Verify the new PID and `runtime_started` audit event match the committed revision;
   verify `/healthz`, recent reconciliation, Alpaca paper account access, Telegram
   bot identity/command registration, and the shared position report.

Do not restore old code against a newer schema without checking compatibility.
Quarantined records can be reviewed/restored with an audited migration; they were
not deleted. Keep broker-held protective orders in place during a daemon restart.

## Logs, audit and monitoring

- `data/copilot.err.log`: JSON application logs with UTC time and process run ID.
- `data/copilot.log`: printed cards; `data/watchdog.log`: process checks and readiness reports; `.err.log`: probe errors.
- `data/alphaminer.log` and `.err.log`: scheduled research output.
- `audit_events`: signal creation, exact fill changes, stream and REST evidence,
  valuations, close submissions/completions, notification message IDs/results,
  quarantine/restoration, and source revision at daemon startup.
- Prometheus includes active positions, fills, entry-fill synchronization and
  existing scan/data metrics. Scrape the daemon at `:9108/metrics`.

Alembic preserves logging configuration; Telegram token URLs are redacted and
HTTP transport info logging is suppressed. Never commit credentials, raw logs,
DB files or private incident snapshots. Operational audit is complemented by the schema 005 event journal and transactional
notification outbox. See [durable workflows](durable-execution.md) for recovery,
readiness, dead-letter requeue and deployment checks.

## Alternative deployment

Docker Compose defines PostgreSQL and copilot services with persistent PostgreSQL
storage and healthchecks. Configure credentials and `DATABASE_URL` explicitly;
review port exposure and default example passwords before using it on a server.
Do not start Compose on this desk alongside launchd.

```bash
docker compose up -d --build
docker compose logs -f copilot
docker compose exec copilot copilot db current
```

For systemd or another supervisor, run the same `copilot daemon` command with one
process, an explicit working directory/environment, persistent DB storage and restart
policy. Review data/Telegram freshness in addition to process health.

## Polling freshness and event-loop stalls

Run `uv run python scripts/verify_runtime.py` after restart. Besides broker/report
agreement, it checks the running daemon's successful-poll timestamp and poll-health
gauge; a separate `getMe` request alone cannot establish poller health.

Telegram request retries and timeouts live under `telegram` in YAML. Poll errors
are recovered by the SDK and audited with recovery/periodic success. Every handler
records update ID and lifecycle; request audits retain delivery outcome/message ID.
No handler or trade action is automatically replayed. A lost Telegram HTTP response
may produce a duplicate message on retry. Slow interactive commands still queue
later commands because handler execution remains serialized.

The generic event-loop monitor exports lag and records `event_loop_stall` above
`telemetry.event_loop_warning_seconds` (default 2s), sampled every
`telemetry.event_loop_sample_seconds` (default 1s). Consult those records alongside
poll/command audits when a command appears delayed. Market-data, simulation quote,
correlation and research computation boundaries now offload blocking work.

## Operator report checks after deployment

After restart, verify the registered menu contains `/macro` and no `/regime`.
`/macro` includes combined volatility/macro filters and published-data dates;
`/explain_macro` is educational. `/gex` discloses missing-chain fields and requires
a real spot quote. `/positions`, `/perf` and conversational position queries share
broker-backed valuation. Backtest defaults come from loaded configuration.

`scripts/verify_runtime.py` checks the clean source revision against the running
startup audit, Alpaca paper access and exact position parity, Telegram identity,
menu and actual daemon poll freshness. Account reconciliation is read through
`AccountLedgerService.current()` using the daemon's fresh persisted report; the
verifier never starts a competing activity import. Missing, stale, failed or
unreconciled evidence fails verification. It sends no messages or orders. `/healthz`
is liveness; successful read checks do not exercise order submission.

### Scheduler startup timing

Telegram/metrics initialize before immediate jobs receive their first deadline.
`scheduler.misfire_grace_seconds` defaults to 60; jobs coalesce delayed executions
and allow one concurrent instance per job. This prevents initialization latency
from silently skipping the first scan/monitor run. The deployment log review
reproduced a 1.5-second delay exceeding APScheduler's former one-second default.

Old Telegram messages can retain removed callback names. Use `/help` or the current
command menu for `/macro`; historical `/regime` buttons are no longer active.

## Coordinated close and flatten

`/close <id>` / `copilot close <id>` close one tracked position; the CLI no longer
requires a simulated price. `/flatten` / `copilot flatten` preview all open Alpaca
positions. Submit with `/flatten confirm` or `copilot flatten --confirm`.
`copilot flatten --dry-run` and `copilot close <id> --dry-run` are read-only, including
when `--confirm` is also supplied. Preview is informational; confirmation reads a
fresh snapshot. No Telegram test message or order is needed to validate registration.

Both operations preserve the existing trading halt. Flatten operates on positions
present in the account snapshot, including broker-only positions; it cancels orders
on those symbols only. Unfilled entries in other symbols remain active. An account
snapshot can change during execution: inspect each result and `/positions` afterward.
Tracking ambiguity, mismatched cost basis/quantity/direction, partial entries/exits,
working market orders and unconfirmed cancellations block a new close. A failure
on one symbol does not prevent attempts on the other symbols. Untracked closes do
not invent tracked entries or trade statistics. Their actual executions are included
in account-level `/perf` reconciliation.

For ordinary equity close/flatten requests the broker clock must indicate regular
trading hours before any cancellation; after-hours requests retain protection.
Direct replies and durable result notices include the same refusal reason, the
broker's next regular open in UTC, and a prompt to retry during regular hours.
No close is queued by an ordinary closed-session refusal; the trading halt is unchanged.
Panic explicitly retains its emergency policy: cancel orders, permit market exits
queued for the next session, and persist the halt. A queued order is not a confirmed
liquidation.

Alpaca OPEN queries omit held bracket stops, even with nested results. The adapter
expands exact entry/order groups and confirms every active leg. For broker-only
brackets it resolves the working exit ID against bounded nested history; missing
or ambiguous group identity refuses the close before cancellation. The
workflow checks the clock again before submission; if the market closes or the
broker fails after cancellation, protective orders may already be removed. The
response explicitly reports that condition: inspect the account and restore
protection or close through the broker as appropriate. No automatic restoration
or blind close retry is attempted after an uncertain mutation.
Cancellation attempts are audited before DELETE, so even a lost cancellation
acknowledgement carries this protection warning. Only a pre-mutation refusal may
say existing protective orders were left unchanged by this request. External SDK
errors expose their type/HTTP status, not raw broker response text.

`close_requests` persists a UUID client order ID before broker mutations. A partial
unique index allows only one active request per environment/account mode/symbol,
including requests from another CLI process. Statuses are `claimed`, `submitted`,
`unknown`, `completed`, `failed`; terminal history is retained. Minute monitoring
and subsequent commands recover exact client IDs and attach broker exit IDs to
tracked signals. Confirmed full fills drive normal trade accounting/notifications.
Confirmed unfilled cancellation/rejection permits a later explicit request;
partial or ambiguous outcomes retain exclusivity. A crash before submission can
leave a `claimed` request without a matching order: it is intentionally not expired
or replayed automatically. Review broker orders and the audit trail before an
operator repairs that request; elapsed time alone does not prove no order exists.

Audit events `close_request`, `close_broker_step`, and `flatten` record intent,
request/order IDs, cancellation confirmation, submission, recovery and per-symbol
failures. They complement `exit_order_submitted`, reconciliation and Telegram
handler/delivery audits. `execution.close_cancel_timeout_seconds` (default 10) and
`execution.close_cancel_poll_seconds` (default 0.25) control cancellation polling;
SDK calls run off the event loop. These are not guarantees of broker HTTP latency.

### September 17 close-message investigation

The requests at 13:06:55 and 13:07:17 UTC were refused before any cancellation:
Alpaca's clock reported the next regular open at 13:30 UTC. Both Telegram handlers
completed and their direct replies and outbox messages received HTTP 200 responses.
The durable result notice had omitted the useful market-closed explanation and
showed only a request ID and `failed`; it now includes the symbol and full safe
explanation. Private audit evidence is retained outside Git. Loopback SDK and
isolated database regressions cover both commands, post-cancellation session closure,
lost cancellation acknowledgements and broker-error redaction; these test results
are separate from subsequent deployment verification.

Deployment checks must verify `/flatten` in default, private-chat and operator-chat
command scopes, the chat menu button, current daemon revision and poll freshness.
`scripts/verify_runtime.py` performs these checks without messages or orders.


### Broker transport and stop replacement

Broker HTTP calls have a per-attempt timeout (`execution.broker_request_timeout_seconds`,
default 10 seconds). GET retains the SDK retry policy; POST/PATCH/DELETE are not
replayed automatically. An uncertain entry records `entry_submission_unknown`,
leaves its signal `SUBMITTING`, and halts new entries. Inspect `entry_submission`
audit for its client ID, retrieve that exact Alpaca order and reconcile its state
before resuming. The entry worker and monitoring perform exact client-ID lookup recovery; neither
404 nor elapsed time permits resubmission. Do not reset a claim after a timeout.

Stop changes resolve the exact bracket and replacement chain, then read back the
working price before updating storage or sending a ratchet alert. A pending/failed
replacement preserves the previous local stop and original thesis/risk. Review
`stop_replacement` request/result events and `stop_updated` for acknowledged changes.
`execution.stop_replace_timeout_seconds` defaults to 10 seconds. Native bracket
fills and minute reconciliation continue independently of Telegram response delivery.

See [Alpaca contract review and integration coverage](alpaca-integration-review.md).

### Repeated trailing-stop notices

One position may generate many valid notices as its stop tightens. The monitor
runs every minute and on broker-stream wakeups; `trail_step_ticks` controls the
minimum proposed stop improvement. A short stop ratchets downward, a long stop
upward. The current non-alpha implementation uses recorded initial risk distance
for its trigger/trail calculation; the named ATR/high-water policy mismatch remains
in the [architecture review](architecture-review.md#remaining-findings-ranked).

The operator explicitly prefers **every confirmed stop-change notice**. Keep that
cadence; do not raise the broker ratchet threshold merely to quiet Telegram.
For suspected duplicates, correlate `stop_replacement` request/result, `stop_updated`,
the notification work item and `telegram_request.outbox_id`. An outbox attempt alone
does not establish one Telegram HTTP attempt; inspect transport retries/message IDs.

The September 16 IWM investigation found 12 strictly tightening, broker-confirmed
changes and 12 successful first-attempt Telegram sends, each with a distinct message
ID. Exact Alpaca bracket retrieval agreed with the current stored stop. No duplicate
delivery or reset loop was found, and no protective order or notification policy was
changed for the investigation. Raw snapshots remain private. A stop price is a trigger,
not a guaranteed realized gain; broker fills remain authoritative.

## Current schema and monitoring verification

Apply head migration `008_alpha_pipeline` after the backup and controlled
pause. Verify `/readyz` includes fresh successful accounting, reconciliation,
worker/delivery and Telegram observations. `db ledger` should show no issues and
zero quantity differences. The runtime verifier must match the clean source
revision and current run; keep its output private.

The watchdog now consumes passive readiness and persists debounced incidents,
including dead-letter failures, through the shared journal/outbox. Inspect
`copilot db incidents`, `copilot db outbox` and recent watchdog output. A running
unready process is alerted, not blindly restarted. Default startup/failure grace
is 120 seconds; recovery is 60 seconds. See [monitoring policy and recovery](operational-monitoring.md).

Only redundant successful blank-detail health observations older than 30 days are
compacted, in hourly batches of at most 1000. Failures, recovery boundaries, latest
observations, financial/order/incident events, audit traces, work records and dead
letters are retained. Preview with `db retention`; `--apply` compacts one batch.
Local files and durable financial/audit history still need capacity planning,
backups and a tested archive/log-rotation policy.


## Alpha pipeline deployment

Back up PostgreSQL, apply schema `008_alpha_pipeline`, and explicitly import
`config/promoted_alphas.yaml` using `copilot alpha import` once. This retains four
historical hypotheses in shadow, discarding their unverified promotion claims.
Existing positions remain monitored with their original protection. No new alpha
is activated by the migration/import. Confirm `alpha list`, `alpha status` and the
`alpha_registry` readiness component after the first scan. `/alphas` uses the same
registry. See [pipeline contracts](alpha-pipeline.md) before any qualification.

Regenerate only the miner plist with `./scripts/launchd.sh install-miner` to apply
its staggered schedule. This does not start another daemon or Telegram poller.
Private research artifacts live in `~/.local/state/agentic-trader/research` with
atomic writes and mode 0600. Retain them with the journal for reproducibility.

`alpha replay` is a read-only Alpaca data diagnostic, but intentionally writes its
attempt and inspected-period exclusion to the configured research journal before
provider access. It starts no daemon/poller and submits no orders or notifications.
Retain failed results as well as successful ones; no automatic retry, feed fallback
or promotion is performed. See [session replay](alpha-session-replay.md) for its
private artifact contract and remaining live-clock limitations.


### Prospective session data observer

The existing daemon now owns the [forward data observer](alpha-forward-observations.md).
Desk YAML enables SPY/15m on the configured stock feed, sampling around observed
session closes. It is independent of trading scans and does not emit signals or
routine Telegram messages. `alpha status` exposes capture results; `/readyz` includes
current-run `alpha_observer` progress. A healthy idle collector is not evidence of
complete prices. Check capture coverage/status and metrics separately. After a
post-close deployment, retain that no forward sample exists yet; do not backdate a
historical fetch to pass verification. No schema change or alpha activation is needed.

### Sleep and wake

launchd supervises process exits; it does not keep this Mac awake. During sleep,
scans, reconciliation, notifications and collection pause; dark wakes can generate
transport failures and scheduler misfires. After a full wake, inspect current-run
`/readyz`, watchdog progress and `scripts/verify_runtime.py`. The same healthy process
may resume and reconnect without a restart. Restart only if recovery fails, preserving
evidence and the single-poller rule. Broker-held protection remains at Alpaca while
the host sleeps, but local trailing/monitoring cannot run. Continuous paper operation
requires an awake host or an always-on deployment.

New session alpha versions use [explicit decision windows](alpha-session-decisions.md)
but remain blocked from activation and entry admission. The native scan schedule and
forward collector are unchanged; a healthy collector is not evidence of deployed
session strategies or of complete forward samples.

### Forecast research operations

`alpha benchmark` runs explicit-horizon forecast diagnostics against saved discovery
data. It charges trials and retains private artifacts through the shared journal;
it sends no orders or notifications and does not change the weekly miner cadence.
See [forecast benchmarks](alpha-forecast-benchmarks.md). Run research outside the
daemon; broader worker-resource isolation remains a roadmap item.

`alpha benchmark` supports explicit `--label`/`--feature` and optional per-side
`--cost-bps` scenarios. These are charged daily bar-price payoff diagnostics with
no promotion or broker-fill claim; see [timing/cost contracts](alpha-forecast-policy.md).


### Durable candidate decisions

Use `uv run copilot alpha forward --days 7` (JSON) or Telegram `/alphas` for the
[read-only cohort report](alpha-forward-evidence.md). Missing/truncated history is
explicit; measured receipt lag includes configured delay and polling. `/readyz`
continues to measure worker freshness separately from successful data/scoring.

The daemon also owns the [receipt-aware diagnostic evaluator](alpha-session-decisions.md#durable-diagnostic-worker).
`alpha_pipeline.decisions` bounds its universe/history/work; `alpha_decisions` readiness
is separate from capture quality. Immutable claims/cursors/results use the existing
journal and private `forward-decisions` artifacts. A healthy worker with no eligible
version-3 candidates is idle, not evidence of a forward score. No promotion, orders,
qualified shadow credit or synthetic Telegram messages follow from these diagnostics.


The frozen ETF campaign designates six SPY/QQQ session hypotheses for forward diagnostics
alongside four historical shadows. Enrollment uses immutable registration and registry
CAS during controlled deployment; restart installs the new generation before readiness
is verified. All remain unqualified, and active-alpha count remains zero. Inspect the
[results and limits](alpha-session-campaign-2026-09-17.md); positive historical marks
are not Alpaca paper-account P&L.

### Timed alpha lifecycle operations

Only explicitly timed alpha positions participate in [trade lifetimes](alpha-trade-lifetimes.md); historical positions receive no inferred limits. Resting deadlines request one exact entry cancellation, then reconcile by GET. `entry_cancellation` readiness and `trader_entry_cancellations_unresolved` expose unresolved work. Entry admission remains blocked during cancellation even before an uncertainty halt. Partial fills, replacements or ambiguous outcomes require protection review and retain journal/outbox evidence; never manually replay the DELETE. `/resume` rechecks unresolved cancellation and timed-position evidence.

Holding expiry waits for a current eligible broker session and uses the existing close service with a stable ID. A failed attempt is retained for operator review, not retried every monitor cycle. General close claims/results now also create durable journal/outbox notifications; confirmed financial accounting remains separate. Monitor cadence and host availability bound response time: an elapsed lifetime is not a guaranteed fill time. Session-alpha promotion remains disabled.

Inspect durable cancellation evidence with `copilot db queue --kind entry_cancel` and `copilot db events --stream entry-cancel/COMMAND_ID`. These are read-only; unresolved intents have no resend operation.

The [continuous timed ETF campaign](alpha-continuous-campaign.md) pairs the new lifetime contract with actual-data discovery: 48 frozen comparisons and a new trend/pullback hypothesis. Replay supports one continuous year with bounded acquisition chunks; trial/holdout accounting and session activation gates remain intact. Report research yield separately from deployment readiness.

The [completed continuous study](alpha-continuous-campaign-2026-09-17.md)
retains 36 complete runs and 12 SPY coverage failures. QQQ momentum has 415 closes
and positive primary-cost returns, but fails cost stress; zero alphas promoted.
The sector-panel path adds explicit [Rank IC/ICIR contracts](alpha-information-coefficient.md).
Existing miner ICIR is overlapping, single-symbol and unannualized.

`copilot alpha panel-study PROTOCOL --output NEW_PRIVATE_DIRECTORY` runs the
[frozen sector-panel research path](alpha-sector-panel.md): observed-calendar
native daily inputs, causal relative features, cross-sectional IC/HAC and five-session
basket/cost diagnostics. All 32 declared comparisons and every member's inspected
interval are journaled before provider access. It sends no orders or notifications,
grants no qualification/registry credit and leaves weekly mining unchanged.

The [completed sector-panel study](alpha-sector-panel-2026-09-17.md) retains
32/32 complete comparisons and zero passes. Volatility-scaled momentum has mean
Rank IC 0.0136 and +3.63% at 1 bp per side, but −4.27% at 5 bp and concentrated
gains. All formulas remain research-only; active alphas remain zero. The next
priority is a newly frozen turnover-aware study. [Automatic Alpaca bar evidence](market-data-evidence.md) now retains raw pages and normalization outcomes.

## Provider read and forecast hardening

See [forecast contract hardening](forecast-contract-hardening.md). Active `doctor` includes
a recent request against the exact configured Alpaca feed; HTTP permission and actual
bar freshness are distinct. Read fallbacks share bounded executor capacity and Alpaca
socket timeouts. Unfinished reads are never replayed by the fallback wrapper.

## Raw bar evidence operations

The daemon and research compositions now retain [Alpaca bar evidence](market-data-evidence.md)
in the private state directory. Budget failures reject the Alpaca acquisition and
retain a failure receipt; no evidence is automatically deleted. Monitor disk growth
and back up referenced pages with research journals. Capture success, worker readiness
and recent-feed entitlement remain separate checks.
