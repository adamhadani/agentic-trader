---
layout: default
title: CLI Command Reference - Cash-Plus Trading Copilot
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
  - APScheduler 4-hour scan aligned with candle closes (`00:00`, `04:00`, `08:00`, `12:00`, `16:00`, `20:00` UTC).
  - 15-minute position monitoring and bracket take-profit / stop-loss reconciler.
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
Displays all currently tracked positions, live quotes, stop-loss / take-profit prices, and mark-to-market unrealized P&L.
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
uv run copilot scan --asset-class equity

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

### `copilot close <signal_id> [exit_price]`
Liquidates an active position, records realized P&L, releases notional exposure, and emits an exit alert card.
```bash
uv run copilot close 4 5845.50
```

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
Emits a synthetic trade card to test Telegram formatting, execution buttons, and terminal display.
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

## 4. Database Schema Migrations & Administration (`copilot db`)

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
