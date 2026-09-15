# Development and debugging handoff

Updated **2026-09-15**. Start with [CLAUDE.md](../CLAUDE.md), the
[operations guide](production.md), and [incident notes](incident-2026-09-15.md).

## Runtime and ownership

This checkout runs under `com.agentictrader.copilot` in macOS launchd. Its shell
sources `.envrc` and runs `uv run copilot daemon`. The configured execution adapter
is **Alpaca paper**, with PostgreSQL `agentic_trader` on localhost. `data/signals.db`
is historical SQLite storage, not the active database. Never run a second daemon,
Telegram poller, or Compose stack alongside the installed service.

| Task | Actual schedule |
| --- | --- |
| Swing scan | Every 4 hours from startup, immediate first run; no timeframe filter |
| Intraday scan | Every 15 minutes from startup, session gated, `timeframe="15m"` |
| Position reconciliation | Every minute, plus broker stream wakeups |
| Macro briefing | Weekdays 12:30 in the scheduler/system timezone |
| Retuning | Saturday 02:00 in the scheduler/system timezone |
| launchd watchdog | Every 60 seconds; checks registered PID, not readiness |
| launchd alpha miner | Saturday 02:00 local time, 25 iterations, ten stock/ETF symbols; no automatic promotion |

APScheduler interval jobs are not aligned to candle boundaries. Cron logs that
mention UTC do not change the scheduler timezone. Market calendars use New York
exchange time; database timestamps and JSON logs use UTC. A watchdog can undo a
simple stop; unload it for a maintenance pause, then restore it afterward.

## Code map

| Concern | Entry points |
| --- | --- |
| CLI / daemon | `main.py` entry point → `cli/main.py`, `cli/commands/service.py` |
| Orchestration | `agent/copilot.py:TradingCopilot`; canonical class with DB/broker/data/notifier injection |
| Risk / sizing | `agent/evaluator.py`, `position_sizing.py`, `regime.py`, `macro.py`, `calendar.py` |
| Data / calendars | `data/market_data.py`, `data/providers.py`, `market/session.py`, `resilience/fallback.py` |
| Strategies | `screeners/strategies.py`, `registry.py`, `formulaic.py`, `config/promoted_alphas.yaml` |
| Execution | `broker/`, `execution/`; current runtime uses immediate orders |
| Persistence | `storage/db.py`, `models.py`, `migrations.py`, `alembic/versions/` |
| Telegram / reports | `notifier/telegram_bot.py`, `presentation/formatters.py` |
| Chat tools | `agent/copilot_graph.py`, `copilot_tools.py`; in-memory conversation history |
| Research | `backtest/`, `research/`, `research/alpha/`, `options/`, `pairs/` |
| Diagnostics | `diagnostics/doctor.py`, `telemetry/`, `runtime.py` |

### Signal lifecycle and reconciliation

Scans read halt/risk state, fetch candles, evaluate strategies, deduplicate, and
stage `PENDING` signals with Telegram cards. An operator explicitly executes them.
`SUBMITTING` precedes broker submission. An accepted order becomes `EXECUTED`,
which currently means tracked/submitted; `executed_at` identifies a confirmed
complete entry fill. The monitor refreshes average entry, quantity, notional and
risk from the exact broker entry order.

Alpaca exits must be full fills of that entry's bracket legs or an explicitly
recorded manual close order. Matching only a symbol or an old opposing order is
forbidden. Price, quantity, side, symbol and chronological checks must pass.
Partial fills trigger reconciliation and remain tracked. Polling and WebSocket
wakeups share a local lock; the database's conditional close gates metrics and
notification delivery across processes. Alpaca manual close/panic do not record
estimated exits. Panic persists its halt before broker operations.

`/positions` and CLI `positions` share one report builder. Alpaca quantity, cost
basis, current price and unrealized P&L come from a single account snapshot. Quotes
from another feed are not substituted. Untracked/ambiguous broker positions appear
once, with notes; a failed snapshot reports an error. Simulation valuations are
explicitly marked estimates. Reports preserve source and retrieval time in audit.

### Storage and audit

Head revision: `003_audit_provenance`. Tables:

- `signals`: lifecycle, sizing, entry/exit order IDs, execution time, environment,
  execution mode/account type, process run ID, and reversible quarantine flag.
- `system_state`: halt flags and operational key/value state.
- `audit_events`: append-only creation, fills, stream/reconciliation evidence,
  valuations, close submissions/completions, notification results, repairs, and
  daemon startup identity. JSON payloads avoid credentials and chat identifiers.

Operational queries exclude quarantined signals and other environments/execution
modes. Legacy rows have `production`/`unknown` provenance and remain eligible until
reviewed. Signal dictionaries use `contract`; timeframe is not yet a stored column.
`copilot db audit --signal-id N --limit 50` retrieves the evidence. Database
construction still applies migrations, including for informational CLI commands.

## Configuration and isolated development

`config.py` has **no import-time dotenv side effect**. `load_config()` is the
application boundary. Production reads `.envrc` without modifying `os.environ`;
existing environment values, including empty strings, win. Explicit `environ={...}`
never reads local secrets unless `env_file` is also explicit. LLM clients receive
the selected API key from the resulting model.

`COPILOT_ENV` selects `production`, `development`, or `test`. Nonproduction runs
require an explicit `COPILOT_CONFIG` and database; `COPILOT_ENV_FILE=''` disables
dotenv loading. Bare `AppConfig()` cannot select a database: inject a DB or set an
explicit path/URL. An already constructed config never consults ambient DB variables.

At load time DB selection is `DB_PATH` → `DATABASE_URL` → explicit `DB_NAME` → YAML
path/URL → production default. `DB_PATH` supports a SQLite path or full connection
URL. A named SQLite sandbox resides under `data/<environment>/`. Explicit paths
on `AppConfig` win over its URL fields. YAML backtest, research, trailing-stop and
broker-stream settings are passed through.

`scan --dry-run` constructs an isolated temporary SQLite database and PaperBroker,
disables Telegram, skips broker connection and position monitoring, and prints
`[DRY RUN]`. It evaluates an **empty simulated portfolio**, while market-data/LLM
calls remain possible. `test-alert` previews locally by default. `test-alert --send`
requires both a separate test bot token and separate test chat ID and sends a
non-actionable `[TEST]` message; it creates no signal. Development/test notifier
messages carry an environment label.

### Test rules

- Use `uv run pytest`. Autouse fixtures remove inherited credentials, select
  `tests/fixtures/config.yaml`, and allocate per-test SQLite files.
- Python network sockets and native libcurl I/O are blocked. Only marked HTTP
  server tests allow loopback. SQLAlchemy, SQLite and native psycopg2 guards reject
  DB paths outside the test root and URLs outside the explicit disposable DB.
- PostgreSQL tests are opt-in: provision a database whose name starts `test_`, set
  `TEST_POSTGRES_URL`, then run `uv run pytest tests/integration --run-postgres`.
  The fixture migrates/downgrades this DB only. Never supply the application DB.
- Inject `TradingCopilot(config, db=mock_or_temp_db)` before construction. Mock
  external providers explicitly. Fixtures should use their returned DB/config
  together, including environment and execution-mode provenance.
- Before committing: `uv run pre-commit run --all-files`. It runs formatting,
  dependency locking, mypy, and impacted tests; also run the full suite for changes
  to isolation, persistence or execution. Loopback tests need a sandbox that permits
  binding localhost. Do not disable isolation to make a test pass.

## Reloads and inspection

Configuration and strategy registry load at construction; there is no file watcher.
External alpha promotion writes YAML but requires a daemon reload to activate.
`/alphas` reads YAML and can differ from a long-running registry. Retuner calibration
JSON is not automatically consumed by the scanner. `ConvexAlphaPortfolioOptimizer`
is a library; no `alpha optimize` CLI or live allocation integration exists.

```bash
launchctl list | rg 'com\.agentictrader\.'
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:9108/healthz
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:9108/metrics
uv run copilot db audit --limit 20
```

`/healthz` is liveness. `/healthcheck`, `doctor`, and `launchd.sh health` run active
probes, including migrations and an LLM request. `copilot metrics` has a separate
in-process registry; scrape the daemon's endpoint for its metrics. JSON logs in
`data/copilot.err.log` retain UTC timestamps/run IDs through migrations and redact
Telegram token URLs. Printed cards go to `data/copilot.log`. Keep raw logs,
incident snapshots, DB files, `.envrc`, and derived calibration files out of Git.

## High-priority follow-ups

1. Atomic execution claims/reservations and risk/session revalidation at approval;
   same-signal claims are atomic; different signals can still race total exposure limits.
2. A fill ledger for partial entries/exits, replacements, cancellations, and broker
   trades created outside the copilot; current matching deliberately defers uncertain
   cases. Persist timeframe and original risk separately from mutable stop levels.
3. Readiness/freshness monitoring for broker stream, Telegram poller, successful
   scan and quote age; watchdog liveness alone cannot establish trading readiness.
4. Update local trailing stops only after confirmed broker modification. Audit
   stop discrepancies and preserve the original thesis instead of overwriting it.
5. Move blocking market-data calls off the event loop; verify timeframe filtering
   before cross-strategy netting. Review missing-data macro fallbacks and drawdown
   plumbing before increasing automation or enabling live money.
6. Keep sliced execution disabled until partial plans and protective brackets are
   implemented. Integrate alpha allocation into risk sizing only after validation.

## Validation baseline

September 15 verification before deployment: **397 tests passed**, including four
PostgreSQL integration tests on an explicitly provisioned disposable `test_` database.
Mypy passed for 98 application modules. Expected socket-block warnings in older
fallback tests and a third-party WebSocket deprecation remain. Deployment identity
and later smoke checks are recorded separately in startup/valuation audit events.
