# Development and debugging handoff

Updated **2026-09-17**. Read [CLAUDE.md](../CLAUDE.md), [operations](production.md)
and the [architecture review](architecture-review.md). Detailed workflow contracts
live in [durable execution](durable-execution.md), [accounting](account-ledger.md)
and [operational monitoring](operational-monitoring.md).

Alpha closure/clock checks now live in `market/bars.py`, shared by research,
screening and shadow. Fixed-duration versions reject explicit session/unknown
layouts and ambiguous timestamps. [New session versions](alpha-session-decisions.md)
share receipt/delay/expiry selection between replay, screening and shadow. The dedicated
forward worker now acquires raw minutes and persists decisions through the same journal;
native scans remain separate and execution/qualification evidence is still required.
See [replay contracts](alpha-session-replay.md#clock-isolation-before-live-migration).

## Runtime ownership

The installed checkout runs `com.agentictrader.copilot` under launchd. Its shell
sources `.envrc` and runs `uv run copilot daemon`. The desk uses **Alpaca paper**,
PostgreSQL 17.11 (Homebrew) on localhost and one Telegram poller. The historical
`data/signals.db` SQLite file is not the active database. Compose's declared image
version does not establish the installed local server version.

| Task | Schedule |
| --- | --- |
| Swing scan | Every 4 hours from startup, immediate first run, no timeframe filter |
| Intraday scan | Every 15 minutes, session gated, `15m` filter |
| Position reconciliation | Every minute plus broker-stream wakeups |
| Account activity reconciliation | Independent worker, default 60 seconds |
| Entry / notification workers | Independent loops, default 2 seconds |
| Macro briefing | Weekdays 12:30, scheduler/system timezone |
| Retuning | Saturday 02:00, scheduler/system timezone |
| External watchdog | Every 60 seconds: process check, readiness incidents and due compaction |
| Session data observer | 30-second wall-clock polls; SPY/15m captures only within three minutes of observed closes; no scoring/trading |
| Alpha miner | Saturday 03:00 local launchd time; ETF32, 9 genetic + 7 catalog trials/symbol; no promotion |

Trading scan intervals are not candle-close aligned. The independent
[forward observer](alpha-forward-observations.md) samples around observed session-bar
closes; it does not migrate the strategy clock. Cron logs mentioning UTC do not change
the scheduler timezone. Market sessions use exchange time; DB/log timestamps use
UTC. Configuration loads at construction and requires restart after external edits.
The journal-backed alpha registry reloads atomically between scans. Telegram/metrics initialize before immediate jobs; scheduler
jobs coalesce delays and allow one instance each.

Never start another daemon, `listen` or Compose stack alongside the installed bot.
Use a worktree for supervisor changes. Unload watchdog before daemon for maintenance;
a simple stop can be undone by the watchdog. See the controlled deployment runbook.

## Configuration and isolated development

`load_config()` is the explicit application boundary; importing config loads no
secrets and never changes `os.environ`. Production merges YAML, `.envrc` and
environment values. Explicit environment values, even empty ones, win.
`COPILOT_ENV_FILE=''` disables dotenv. Explicit `environ={...}` does not read local
secrets unless an environment file is also explicitly supplied.

`COPILOT_ENV` selects production/development/test. Nonproduction requires explicit
config and storage. Bare `AppConfig()` cannot select a runtime DB. Load-time DB
precedence is `DB_PATH` → `DATABASE_URL` → explicit `DB_NAME` → YAML → production
default. A constructed config never consults ambient DB variables. `--db-path`
accepts a path or URL; named sandboxes live under `data/<environment>/`. PostgreSQL
has no automatic SQLite failover.

A dry scan uses a temporary SQLite DB and empty PaperBroker portfolio, disables
Telegram and skips broker connection/monitoring. Market data and optional LLM
calls still occur. `test-alert` previews locally; explicit sending requires a
separate test token/chat and creates no signal. Nonproduction messages are labeled.

## Test and review workflow

- Function-scoped autouse isolation removes credentials, selects fixture YAML and
  temporary DB paths. Socket/libcurl guards block external I/O; native psycopg2 and
  SQLAlchemy guards prevent bypassing DB isolation.
- Inject config/storage/providers before construction. Prefer local fixtures for
  shared domain setup and `pytest.mark.parametrize` for meaningful variations.
  Avoid fixtures that share mutable trading state across tests.
- Real SDK tests use marked loopback HTTP/WebSocket. PostgreSQL is explicit:
  `TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_trader uv run pytest tests/integration --run-postgres`.
  Its database must be disposable and named `test_*`; tests migrate/downgrade it.
- Run `uv run pytest` for cross-cutting changes, then
  `uv run pre-commit run --all-files`. Hooks include formatting, lockfile checks,
  mypy and impacted tests. Full transport/PG integration remains required before
  deploying persistence or execution changes.
- Do not disable safety guards or replace integration with permissive mocks. The
  optional in-process SDK adapter does not prove TCP/WebSocket or PG behavior.

## State and evidence

Schema head: `008_alpha_pipeline`.

| Storage | Responsibility |
| --- | --- |
| `signals` | Tracked proposals/lifecycle, entry/exit identities, provenance and quarantine |
| `system_state` | Persistent halt and operational keys |
| `close_requests` | Exclusive durable close intent and retained terminal history |
| `workflow_locks`, `work_items` | Scoped coordination, entry reservations/queue and notification outbox |
| `domain_events` | Versioned workflow/broker/account/incident evidence and health observations |
| `order_projections` | Exact cumulative order views |
| `activity_projections`, `ledger_checkpoints` | Account-bound activity evidence and reconciled reports |
| `incident_projections` | Replayable incident lifecycle plus latest observation watermarks |
| `alpha_projections` | Replayable research, qualification, registry and forecast aggregates |
| `audit_events` | Operational command, fill, valuation, delivery and startup traces |

Queries exclude quarantined and other-environment rows. Historical unknown-mode
signals remain visible until reviewed. Signals use `contract`; new signals persist nullable timeframe, alpha version,
execution policy and decision provenance. Historical rows are not backfilled with inferred metadata. Construction currently checks migrations, including many informational
CLI paths—explicit bootstrap is a documented refactor, not silently assumed done.

`SUBMITTING` precedes broker POST. `EXECUTED` means tracked/accepted; confirmed
complete entry fills set `executed_at`. Exact full exit fills plus conditional SQL
govern tracked closure. Partial account executions appear in the account ledger
without fabricating per-signal ownership. `/positions` is broker-authoritative;
`/perf` separates account and tracked scopes; `/status` is configured risk capital
and tracked exposure. Accepted orders without fills say they await broker fill.

Use `db audit`, `db events`, `db queue`, `db orders`, `db ledger`, `db outbox` and
`db incidents` for investigation. Preserve raw evidence privately. Financial events
and dead letters have no TTL; only redundant old healthy journal observations are
compacted. `db clear` refuses to reset signal IDs while events/work exist. Contamination
repairs use audited quarantine; see the [September 15 incident](incident-2026-09-15.md).

## Async and operator surfaces

Blocking SDK/provider/calculation work is offloaded at boundaries; scans and
reconciliation serialize related shared state. Threads do not cancel synchronous
calls or isolate shared executors. Remaining contention and slow serialized Telegram
handlers are documented in the architecture review. Keep request timeouts and
loop-lag audits when extending these paths.

Telegram has shared transport retries, command/update correlation, polling
observations and persistent delivery audits. Retry HTTP delivery, never handlers.
`/macro` is the single combined market context; published feed dates and missing
enrichment are explicit. Missing VIX fails regime evaluation; missing enrichment
can leave volatility-only policy. Daily-feed admission age remains a gap.
GEX is an option-chain/model estimate with quality notes and a required real spot.

Research/retuning outputs do not automatically change running strategy parameters.
Alpha state now uses the [journal-backed pipeline](alpha-pipeline.md), schema 008.
The [active alpha roadmap](alpha-roadmap.md) owns research priorities. The
[A1b study](alpha-study-2026-09-16.md) retained 1,952 synthetic replicates, including
22 unavailable comparisons; it does not authorize new gates or promotion.
[A2a](alpha-return-timeline.md) corrects execution return coverage with a versioned
validation policy. Its [fresh calibration](alpha-timeline-study-2026-09-16.md)
completed 1,952 jobs without unavailable comparisons; power remains insufficient
and gates stay unchanged. [A2b session replay](alpha-session-replay.md) now provides
observed-calendar/minute diagnostics and durable input/decision/event artifacts.
Live signal-clock migration and broker execution evidence remain before intraday
qualification. Replay is a charged research diagnostic, not synthetic calibration.
CLI, chat and Telegram inspect the same version registry; activation/demotion is
visible between scans. Imported historical definitions remain unqualified shadow
versions. `/alphas` shows generation/versions, not fabricated performance or allocation.
Combined forecast/optimizer targets are shadow-only. Readiness checks current-run
registry acknowledgment; `alpha status` exposes latest research separately.
The [historical review](alpha-stack-review.md) and [implementation evidence](alpha-pipeline-implementation.md)
record the defect reproductions, fixes, tests and remaining empirical gates.

## Verify deployment separately

```bash
launchctl list | rg 'com\.agentictrader\.'
uv run copilot doctor --readiness
uv run python scripts/verify_runtime.py
uv run copilot db incidents
```

Verify clean committed revision/PID/run, current-run component freshness,
authenticated broker stream, actual Telegram poll freshness/menu registration,
account reconciliation and position parity. The verifier sends no messages/orders.
`/healthz` only proves liveness; a separate `getMe` does not prove polling health.
Active `doctor` is CLI-only; `/healthcheck` has been removed.

Keep UTC/run-correlated logs and private verification artifacts separate from Git.
Test results/CI establish source behavior; startup and observation audits establish
deployment evidence. Consult [ranked remaining work](architecture-review.md#remaining-findings-ranked)
instead of appending duplicate historical status reports to this handoff.

The [active alpha roadmap](alpha-roadmap.md) is the canonical long-horizon research queue. Keep its acceptance criteria and progress current; the [September 16 campaign](alpha-research-2026-09-16.md) records the baseline rejection and control evidence.

`alpha benchmark` now evaluates explicit-horizon forecast components, separately from
trading-policy P&L. It retains charged diagnostic artifacts, never promotes, and does
not add forecast statistics to the strategy-Sharpe variance sample. See the
[forecast benchmark contract](alpha-forecast-benchmarks.md) and canonical alpha roadmap.

`alpha benchmark` supports explicit `--label`/`--feature` and optional per-side
`--cost-bps` scenarios. These are charged daily bar-price payoff diagnostics with
no promotion or broker-fill claim; see [timing/cost contracts](alpha-forecast-policy.md).

Benchmark reports now expose fold stability, signed error influence, forecast/action
distributions, cost attribution and fitted-model evidence. These are descriptive
diagnostics, not promotion gates; see [report contracts](alpha-forecast-benchmarks.md#automatic-diagnosis-before-lead-selection).


The [frozen ETF session campaign](alpha-session-campaign-2026-09-17.md) completed all 81 attempts with full execution
coverage. None passed every rule: QQQ momentum's positive cost-stressed returns had
only 11 closed trades. Six predeclared SPY/QQQ hypotheses are designated for diagnostic
forward observation, not activation or qualifying shadow credit. Preserve the failed
triage result; next priorities are measured forward/lifecycle evidence, explicit
order/holding lifetimes and a longer frozen study before causal panel expansion.
