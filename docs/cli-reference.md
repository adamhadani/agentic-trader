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
  - Sub-second Alpaca WebSocket `TradingStream` and Tradovate user-sync stream.
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
Displays account cash balance, active notional exposure, leverage utilization, and upcoming macro calendar events.
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

# Dry-run scan (evaluates setups without saving to DB or dispatching alerts)
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
Manually authorizes and submits an approved signal to the configured broker (`paper`, `tradovate`, or `alpaca`).
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
Flatten includes broker-only positions, without inventing trade-history P&L for them.
Ambiguous tracked ownership or partial quantities are reported for reconciliation.
Orders on position symbols are cancelled; unfilled entries in other symbols remain.
Each position has its own result: a rejected close does not stop other closes.
Equity closes outside regular hours are refused before protective orders are changed.
The command supports the Alpaca authoritative-position adapter; other adapters
continue to use their existing single-position close behavior.
See [close operations and recovery](production.md#coordinated-close-and-flatten).

### `copilot panic [--confirm] [--reason "TEXT"]`
Institutional emergency kill switch: cancels all resting broker orders, liquidates all active positions at market, and engages a persistent trading halt.
```bash
# Prompt for interactive confirmation
uv run copilot panic

# Immediate execution (bypass confirmation prompt)
uv run copilot panic --confirm --reason "Extreme volatility circuit breaker"
```

### `copilot resume`
Clears persistent emergency trading halt and restores automated universe scans and signal executions.
```bash
uv run copilot resume
```

### `copilot test-alert`
Previews a non-actionable `[TEST]` message locally. Add `--send` with dedicated `TELEGRAM_TEST_BOT_TOKEN` and `TELEGRAM_TEST_CHAT_ID` to test a separate bot/chat; no signal or broker call is created.
```bash
uv run copilot test-alert
```

### `copilot gex [symbol]`
Analyzes market maker dealer gamma exposure (GEX), zero-crossing gamma flip, call/put pinning walls, and near-the-money gamma concentration.
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
uv run copilot metrics --serve --port 9108
```

### `copilot explain-macro`
Generates an educational, executive tutorial briefing synthesizing live quantitative macroeconomic telemetry (10Y-2Y yield curve slope in bps, High Yield OAS credit spreads, VIX volatility context, 5Y/10Y TIPS inflation breakevens, and Copilot risk sizing). Powered by LLM synthesis with an exhaustive deterministic rule-based fallback.
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

## 4. Formulaic Alpha Mining & Expression DSL (`copilot alpha`)

Institutional-grade formulaic alpha generation, genetic expression search, overfitting protection (DSR, Rank IC), and strategy lifecycle promotion.

### `copilot alpha catalog`
Displays the pre-cataloged library of institutional alpha formulas (WorldQuant 101, factor models).
```bash
uv run copilot alpha catalog
```

### `copilot alpha list`
Displays all currently promoted production alphas, active allocations, out-of-sample Sharpe ratios, DSR scores, eligible asset universe (`ELIGIBLE SYMBOLS`), and promotion audit metadata.
```bash
uv run copilot alpha list
```

### `copilot alpha mine`
Executes genetic formula generation across historical market bars, applying In-Sample / Out-of-Sample cross-validation, Deflated Sharpe Ratio (DSR) multi-testing penalties, cross-asset qualification matrices, and Gram-Schmidt signal orthogonalization against active production alphas.
```bash
# Mine alphas across SPY using daily bars (2y lookback, 20 iterations)
uv run copilot alpha mine --symbol SPY --lookback 2y --interval 1d --iterations 20

# Multi-asset mining matrix & signal orthogonalization check against active desk
uv run copilot alpha mine --symbols NVDA,AMD,AAPL,MSFT,QQQ,SPY --iterations 15

# Mine across high-beta tech with strict statistical gating
uv run copilot alpha mine --symbol QQQ --min-sharpe 1.2 --min-dsr 0.90

# Mine and automatically promote winning alpha to production desk
uv run copilot alpha mine --symbol NVDA --auto-promote
```

### `copilot alpha inspect <alpha_id>`
Computes and renders an institutional quantitative tearsheet for any catalog or promoted alpha across historical data.
```bash
# Evaluate WorldQuant Alpha 006 on SPY
uv run copilot alpha inspect alpha_wq_006 --symbol SPY --interval 1d

# Evaluate trend expansion alpha on QQQ
uv run copilot alpha inspect alpha_trend_expansion --symbol QQQ --interval 1d
```

### `copilot alpha promote <alpha_id>`
Promotes an alpha from the catalog or mining candidates into the production paper trading portfolio, persisting configuration in `config/promoted_alphas.yaml` and registering the alpha in `StrategyRegistry`. Optionally routes execution to a designated subset of symbols via `--symbols`.
```bash
# Promote alpha across all supported symbols
uv run copilot alpha promote alpha_wq_006 --allocation 0.15 --notes "Baseline institutional alpha"

# Promote alpha restricted to high-beta semiconductor universe
uv run copilot alpha promote alpha_wq_053 --allocation 0.15 --symbols NVDA,AMD --notes "Semiconductor momentum factor"
```

### `copilot alpha demote <alpha_id>`
Demotes and retires an active alpha from the production trading desk with zombie position safeguards.
```bash
# Standard demotion: leaves attributed positions open under orphan status (managed by trailing stops)
uv run copilot alpha demote alpha_wq_006 --reason "Performance decay"

# Demote and immediately close/liquidate any open positions attributed to this alpha
uv run copilot alpha demote alpha_wq_006 --reason "Risk override" --liquidate-positions
```

### `copilot alpha test <expression>`
Tests an ad-hoc formulaic DSL expression directly against market data and generates an instant quantitative tearsheet.
```bash
# Note: Use '--' before expressions starting with negative numbers to avoid Click option parsing collisions
uv run copilot alpha test --symbol NVDA --interval 1d -- "-1.0 * delta(ts_rank(volume, 10), 5)"
```

---

## 5. Database Schema Migrations & Administration (`copilot db`)

Manage SQLite schema versions and database maintenance via Alembic and administration subcommands:
```bash
# Apply all pending database migrations to latest revision
uv run copilot db upgrade head

# View current database revision
uv run copilot db current

# View migration history
uv run copilot db history

# Roll back one migration revision
uv run copilot db downgrade -1

# Purge historical test/dev signals and reset autoincrement ID (schema preserved)
uv run copilot db clear
uv run copilot db clear --yes  # bypass interactive confirmation
```

### Global Database Override Options
Commands interacting with storage accept options or environment variables to redirect to alternate database files:
- `--db-name <name>`: Resolve database inside `data/<name>.db` (or `DB_NAME` env var).
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
Telegram, and skips monitoring. `/perf` reports broker open-position unrealized
P&L and tracked confirmed closed-trade P&L before fees; these are distinct from a
broker account-day return. `/healthz` is liveness; `doctor`/`/healthcheck` include
active database and LLM probes. Do not launch `listen` alongside the running daemon.


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
