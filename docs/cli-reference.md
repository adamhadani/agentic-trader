---
layout: default
title: CLI Command Reference - Agentic Trader
---

# 💻 CLI Command Reference Manual

The `copilot` CLI provides a unified command hierarchy for production daemon execution, day-to-day desk monitoring, quantitative backtesting, parameter optimization, and database migrations.

---

## 1. Production Daemon & Services

### `copilot daemon`
Runs the continuous steady-state trading service.
```bash
uv run copilot daemon
```
- **Components Executed**:
  - APScheduler 4-hour swing scans and session-gated 15-minute intraday scans, relative to startup.
  - 1-minute position reconciliation and bracket take-profit / stop-loss reconciler.
  - Alpaca WebSocket `TradingStream` wakeups (no latency guarantee).
  - Interactive two-way Telegram bot listener.
  - Embedded Prometheus metrics and health check HTTP server on `0.0.0.0:9108`.

### `copilot listen`
Runs the two-way interactive Telegram bot in isolation without running the quantitative scheduler or position monitor.
```bash
uv run copilot listen
```

---

## 2. Day-to-Day Operator Runbook

### `copilot status`
Displays configured risk capital, tracked notional exposure, leverage utilization and macro calendar events. It does not report broker cash.
```bash
uv run copilot status
```

### `copilot positions`
Displays the broker account snapshot: actual average entry, quantity, current price and unrealized P&L, plus tracked stops/targets. CLI and Telegram share the same builder. Source/time and tracking mismatches are shown; failures are not reported as empty positions.
```bash
uv run copilot positions
```

### `copilot scan`
Triggers an immediate quantitative universe scan across configured assets.
```bash
# Live scan with LLM evaluation and alerting
uv run copilot scan

# Dry-run scan (evaluates setups using temporary storage and no production writes/alerts)
uv run copilot scan --dry-run

# Run scan using deterministic rules (bypassing LLM)
uv run copilot scan --no-llm

# Filter by asset class
uv run copilot scan --asset-class futures
uv run copilot scan --asset-class equities

# Specific symbol override
uv run copilot scan --symbols /MES,/MNQ,SPY

# Bypass session hours and market closure check (e.g. testing off-hours)
uv run copilot scan --bypass-session-filter --dry-run
```

### `copilot execute <signal_id>`
Authorizes a signal through the durable entry queue. Fresh admission supports Alpaca equities and local simulation; other adapters fail closed until they implement that contract.
```bash
uv run copilot execute 4
```

### `copilot close <signal_id> [--price PRICE] [--dry-run]`
Close a tracked position without halting trading. Alpaca verifies the exact entry,
position quantity/direction/cost basis, cancels symbol orders, waits for their
terminal states and released quantity, then submits a market close with a durable
client order ID. P&L uses confirmed fills. `--price` is optional and only affects
simulation/manual adapters. `--dry-run` reads the position without changing orders.

```bash
uv run copilot close 5 --dry-run
uv run copilot close 5
```

### `copilot flatten [--confirm] [--dry-run]`
Preview all current broker positions by default. `--confirm` submits coordinated
closes; `--dry-run` always takes precedence. This preserves the existing halt state:
it neither halts trading nor clears a prior panic. Scans and recommendations continue
subject to the existing risk/session/deduplication checks.

```bash
uv run copilot flatten --dry-run
uv run copilot flatten --confirm
```

Telegram equivalents: `/close 5`, `/flatten`, `/flatten dry-run`, `/flatten confirm`.
Flatten includes broker-only positions without inventing tracked trade history. Actual executions still enter reconciled account-level performance.
Ambiguous tracked ownership or partial quantities are reported for reconciliation.
Orders on position symbols are cancelled; unfilled entries in other symbols remain.
Each position has its own result: a rejected close does not stop other closes.
Equity closes outside regular hours are refused before protective orders are changed.
The command supports the Alpaca authoritative-position adapter; other adapters
continue to use their existing single-position close behavior.
See [close operations and recovery](production.md#coordinated-close-and-flatten).

### `copilot panic [--confirm] [--reason "TEXT"]`
Persists a trading halt, requests order cancellation and market exits. Pending/rejected exits remain visible; acceptance is not confirmed liquidation.
```bash
# Prompt for interactive confirmation
uv run copilot panic

# Immediate execution (bypass confirmation prompt)
uv run copilot panic --confirm --reason "Extreme volatility circuit breaker"
```

### `copilot resume`
Clears persistent emergency trading halt and restores universe scans and operator-approved entry flow, after unresolved-submission checks.
```bash
uv run copilot resume
```

### `copilot test-alert`
Previews a non-actionable `[TEST]` message locally. Add `--send` with dedicated `TELEGRAM_TEST_BOT_TOKEN` and `TELEGRAM_TEST_CHAT_ID` to test a separate bot/chat; no signal or broker call is created.
```bash
uv run copilot test-alert
```

### `copilot gex [symbol]`
Estimates GEX and strike-based gamma flip/walls from Yahoo option chains and model assumptions. It does not observe dealer inventory.
```bash
# Standard ASCII report
uv run copilot gex SPY

# Specify expiration depth
uv run copilot gex QQQ --expirations 5

# JSON output
uv run copilot gex /MES --json
```

### `copilot pairs`
Screens cross-asset pairs for cointegration (Engle-Granger test), Ornstein-Uhlenbeck mean-reversion half-life, and rolling spread $Z$-score arbitrage signals.
```bash
# Scan default institutional pairs
uv run copilot pairs

# Evaluate a specific pair
uv run copilot pairs --pair SPY/QQQ

# Screen all pairwise combinations among a list
uv run copilot pairs --symbols SPY,QQQ,IWM,GLD

# Customize lookback and thresholds
uv run copilot pairs --lookback 252 --p-value 0.05 --z-entry 2.0 --z-exit 0.5

# Raw JSON output
uv run copilot pairs --json
```

### `copilot metrics`
Dumps a point-in-time Prometheus exposition snapshot or launches a standalone HTTP metrics server.
```bash
# Dump Prometheus exposition text to stdout
uv run copilot metrics

# Launch standalone metrics server on custom port
uv run copilot metrics --serve --port 9109
```

### `copilot explain-macro`
Generates an educational, executive tutorial briefing synthesizing latest published macroeconomic observations (10Y-2Y yield curve slope in bps, High Yield OAS credit spreads, VIX volatility context, 5Y/10Y TIPS inflation breakevens, and Copilot risk sizing). Powered by LLM synthesis with an exhaustive deterministic rule-based fallback.
```bash
uv run copilot explain-macro
```

---

## 3. Quantitative Research & Backtesting

### `copilot backtest`
Runs an offline historical backtest with transaction friction, slippage, Cash-Plus attribution, and optional Monte Carlo simulation.
```bash
# Run across default futures universe
uv run copilot backtest

# Backtest specific symbols and strategy
uv run copilot backtest --symbols SPY,QQQ,IWM --strategy trend_pullback --lookback 2y

# Run with Monte Carlo simulation (1,000 runs)
uv run copilot backtest --symbols /MES --monte-carlo --mc-sims 1000

# Trailing stop policy options:
# --trailing-stop-mode: none, breakeven_and_trail, chandelier_atr (default)
uv run copilot backtest --symbols SPY --trailing-stop-mode chandelier_atr --trail-trigger-r 1.5 --trail-atr-multiple 1.5

# Retail breakeven test (moving stop to entry at 1.0R):
uv run copilot backtest --symbols /MES --trailing-stop-mode breakeven_and_trail --breakeven-trigger-r 1.0

# Frictionless benchmark
uv run copilot backtest --no-friction
```

### `copilot optimize`
Performs vectorized parameter grid searches and rolling out-of-sample walk-forward cross-validation.
```bash
# Standard parameter grid search
uv run copilot optimize --symbol SPY --strategy trend_pullback

# Rolling Walk-Forward Out-of-Sample Validation
uv run copilot optimize --symbol IWM --strategy trend_pullback --walk-forward --lookback 2y

# Export top parameter candidate as YAML
uv run copilot optimize --symbol IWM --walk-forward --export-config stdout
```

### `copilot retune`
Automated parameter recalibration across all watchlist symbols with minimum Walk-Forward Efficiency (WFE) threshold filtering.
```bash
# Retune all strategies
uv run copilot retune

# Require minimum WFE of 0.60 and Sharpe of 1.0
uv run copilot retune --min-wfe 0.60 --min-sharpe 1.0

# Export winning parameters directly into config/config.yaml
uv run copilot retune --export-config config/config.yaml
```

### `copilot stress`
Performs tail-risk evaluation by replaying strategy execution through historical macro crises or simulating instantaneous cross-asset factor shocks.
```bash
# Replay all historical crisis scenarios
uv run copilot stress --scenario all

# Specific crisis replay
uv run copilot stress --scenario 2008_gfc
uv run copilot stress --scenario 2020_covid
uv run copilot stress --scenario 2022_inflation

# Instantaneous parametric factor shocks
uv run copilot stress --scenario shock
```

### `copilot eval`
Runs Promptfoo benchmark evaluations of trade decision prompts against hard risk invariants.
```bash
uv run copilot eval
```

---

## 4. Alpha discovery and promotion (`copilot alpha`)

The [alpha pipeline guide](alpha-pipeline.md#commands-and-cadence) is the canonical
command and contract reference. `catalog` lists hypotheses; `list`, `status`,
`inspect VERSION_ID` and `export` read journal state. `inspect CATALOG_ID` displays
a definition without downloading data or inventing a performance result.

```bash
uv run copilot alpha calibrate --seeds 10 --bootstrap-samples 499
uv run copilot alpha study-plan --seed 10472909262026 --output /private/path/new-protocol.json
uv run copilot alpha study config/research/a2a-v1.json --output /private/path/new-study-directory
uv run copilot alpha replay 'delta(close,3)' --symbol SPY --interval 15m --start 2024-11-27 --end 2024-11-29 --output /private/path/new-replay
uv run copilot alpha mine --symbol SPY --feed alpaca --interval 1d --lookback 5y --iterations 25 --method random
uv run copilot alpha mine --universe etf32 --feed alpaca --method genetic --iterations 9 --max-seconds 120
uv run copilot alpha benchmark RUN_ID --method ridge --budget 5 --horizon 1
uv run copilot alpha benchmark RUN_ID --method single --budget 7 --horizon 5
uv run copilot alpha qualify RUN_ID VERSION_ID
uv run copilot alpha shadow VERSION_ID --generation N
uv run copilot alpha promote VERSION_ID --generation N
uv run copilot alpha demote VERSION_ID --generation N
uv run copilot alpha portfolio /private/path/observed-snapshot.json
uv run copilot alpha import config/promoted_alphas.yaml
uv run copilot alpha test --symbol NVDA --interval 1d -- '-1.0 * delta(ts_rank(volume, 10), 5)'
```

`mine` persists all trials and leaves holdout untouched. `qualify` consumes the
frozen holdout once. Promotion requires exact version, generation, passing evidence,
deployment data contract and observed shadow history. Import is shadow-only;
`--auto-promote`, allocation metadata and symbol/timeframe-changing promotion flags
are removed. Demotion changes future screening only; use the shared close/flatten
commands separately when liquidation is intended. Portfolio solving is shadow-only.

`calibrate` is synthetic-only, with no runtime config, DB or network access. It writes
a private report, never promotion evidence. Explicit family count/variance parameters
define the comparison scenario. See [calibration contracts](alpha-pipeline.md#synthetic-calibration)
and the [active research roadmap](alpha-roadmap.md).

`study-plan` freezes settings without evaluating observations. `study` runs the
[predeclared comparison](alpha-pipeline.md#predeclared-calibration-studies), retaining
all replicates in a new private directory. Failed or missing replicates cannot pass
the criteria; even a complete passing study is not a promotion credential.

`replay` captures observed exchange sessions and raw minute bars, then runs the
shared execution engine on completed regular-session observations. It charges one
real research attempt and excludes the inspected period from fresh holdouts before
provider access, including failed diagnostics. Results earn no qualification or
shadow credit; intraday promotion remains blocked. See [session replay](alpha-session-replay.md).

## 5. Database Schema Migrations & Administration (`copilot db`)

Manage PostgreSQL runtime or explicitly selected SQLite sandbox schema versions and database maintenance via Alembic and administration subcommands:
```bash
# Apply all pending database migrations to latest revision
uv run copilot db upgrade head

# View current database revision
uv run copilot db current

# View migration history
uv run copilot db history

# Roll back one migration revision
uv run copilot db downgrade -1

# Clear a disposable sandbox; refused when durable events/work exist
uv run copilot db clear
uv run copilot db clear --yes  # bypass interactive confirmation
```

### Global Database Override Options
Commands interacting with storage accept options or environment variables to redirect to alternate database files:
- `--db-name <name>`: Resolve a named SQLite sandbox under `data/<environment>/` (or `DB_NAME` env var).
- `--db-path <path>`: Explicit filesystem path to database (or `DB_PATH` env var).

## Configuration, test isolation and audit

`--db-path` accepts an explicit SQLite path or database URL. `--db-name` explicitly
selects a named SQLite sandbox and overrides inherited DB selection. Production
uses `.envrc` and the runtime YAML; tests use separate settings without dotenv.
See [development notes](development-notes.md#configuration-and-isolated-development).

```bash
uv run copilot db audit --signal-id 5 --limit 50
uv run copilot test-alert                 # local preview
# Explicit opt-in, separate test destination only:
uv run copilot test-alert --send
```

`scan --dry-run` uses an empty temporary portfolio and a simulated broker, disables
Telegram, and skips monitoring. `/perf` separates reconciled account performance
from tracked full-close statistics; neither is account-day return. `/healthz` is
liveness; `/readyz` is passive freshness. Active DB/LLM probes are CLI `doctor` only;
HTTP `/healthcheck` is removed. Do not launch `listen` alongside the daemon.


### Options data quality

`gex --json` emits only JSON on stdout; progress goes to stderr and failures return
nonzero. Missing option-chain counts and modeled numeric defaults appear in
`data_quality_notes`; invalid underlying prices fail instead of using fixed ETF
prices. GEX is a Yahoo option-chain estimate, not brokerage P&L or observed dealer
inventory. `--expirations` defaults to the configured options policy.

### Telegram macro command consolidation

Use `/macro` for VIX classification, Treasury curve, credit, inflation, published
data dates and combined trading filters. `/regime` has been removed.
`/explain_macro` explains the macro indicators; `copilot explain-macro` is its CLI
counterpart. Telegram `/backtest` uses configured `backtest.lookback` when omitted.
Conversational positions use the same broker report as `/positions` and the CLI.

## Durable execution and diagnostics

Entries approved through CLI or Telegram enter the same persistent FIFO and
reserve portfolio capacity. `execute` can return queued, rejected, accepted or
unconfirmed; accepted does not mean filled. Conditions are rechecked before POST;
changed conditions require a new scan/approval. `resume` refuses unresolved entries.
`listen` also runs the entry/outbox workers but does not schedule scans/reconciliation.

- `copilot doctor --readiness`: passive daemon freshness report; nonzero if unready.
- `copilot db queue`: inspect persisted entry requests/outcomes.
- `copilot db events [--stream NAME] [--limit N]`: immutable workflow/broker events.
- `copilot db orders [--rebuild]`: inspect/replay order views; no broker mutations.
- `copilot db outbox [--retry JOB_ID]`: inspect delivery jobs or explicitly requeue a dead letter.

See [workflow guarantees, limits and configuration](durable-execution.md).

## Account activity performance

`copilot perf` refreshes read-only broker accounting and uses the same performance
renderer as Telegram `/perf`. Account-wide realized/unrealized values and
tracked full-close metrics are separate.

- `copilot db ledger`: cached reconciliation status.
- `copilot db ledger --sync`: read-only activity import; no orders/messages.
- `copilot db ledger --rebuild`: replay journal projections; no broker requests.
- `copilot db activities`: private raw fill/cash evidence with exact IDs.

See [account ledger](account-ledger.md) for unsupported activities, freshness and
reconciliation rules. A failed reconciliation never becomes zero profit.

## Operational incidents and maintenance

- `copilot doctor --monitor`: stateful supervisor pass using the passive readiness
  contract; persists debounced incidents, queues alerts and runs due compaction.
  May deliver one existing outbox job when endpoint/delivery freshness fails.
- `copilot db incidents [--rebuild]`: inspect/replay incident lifecycle; no resending.
- `copilot db retention [--apply]`: preview by default; apply one bounded batch of
  redundant old healthy-observation compaction. Financial events/dead letters stay.

See [monitoring semantics and defaults](operational-monitoring.md). `--monitor`
and `--readiness` are mutually exclusive. Active `doctor` probes are separate.

`alpha replay` creates a new session-clock version with `--decision-delay-seconds`
(default 60) and `--max-lateness-seconds` (default 120). Expired proposals cannot
become new orders at a later session; already-submitted GTC orders persist. See
[session decision contracts](alpha-session-decisions.md) and their diagnostic-only limits.

Alpha benchmarks are [forecast diagnostics](alpha-forecast-benchmarks.md) with private
artifacts and charged trials; they do not simulate orders or authorize promotion.
