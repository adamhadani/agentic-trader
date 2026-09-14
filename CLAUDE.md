# CLAUDE.md

Guidelines and reference commands for AI coding assistants working in the `agentic-trader` repository.

---

## 1. Project Overview
The **Cash-Plus Trading Copilot** is an automated multi-asset trading system designed around a $100k cash portfolio (portable alpha). It runs quantitative scans across liquid micro futures (`/MES`, `/MNQ`, `/MGC`, `/MCL`) and ETF proxies (`SPY`, `QQQ`, `IWM`, `GLD`, `USO`), validates candidates with an LLM agent with real-time macro calendar awareness, and executes or tracks trades across multi-asset brokers (Paper simulation, Tradovate CME micro futures, and Alpaca Trading SDK for equities & crypto) with interactive Telegram alert cards and Prometheus observability.

---

## 2. Common Commands & Operational Lifecycle

### Virtual Environment & Dependencies
- Python >= 3.14 managed exclusively with `uv`.
- Sync dependencies: `uv sync`
- Install pre-commit git hooks: `uv run pre-commit install`
- Apply database migrations: `uv run copilot db upgrade head`

### Command Taxonomy by Lifecycle Stage

#### A. Production Steady-State (What runs 24/7)
- `uv run copilot daemon`: Core continuous process running APScheduler (4-hour scans), 15-minute position monitor & reconciler, Alpaca/Tradovate WebSocket trade streams, Telegram bot poller, and Prometheus metrics server (`:9108`).
- `docker compose up -d`: Runs `trading-copilot` container in background with healthchecks and persistent SQLite volume.
- `uv run copilot listen`: Runs Telegram bot listener only in isolation.

#### B. Day-to-Day Operator Desk Commands
- `uv run copilot status`: Portfolio cash base, open notional exposure, leverage, and macro calendar.
- `uv run copilot positions`: Tracked positions, live quotes, stop-loss / take-profit prices, and unrealized P&L.
- `uv run copilot scan`: On-demand quantitative market scan (`--dry-run`, `--no-llm`, `--asset-class`, `--symbols`).
- `uv run copilot execute <signal_id>`: Manually authorize and submit an approved signal to broker.
- `uv run copilot close <signal_id> [exit_price]`: Liquidate open position, record realized P&L, release exposure.
- `uv run copilot panic [--confirm]`: Emergency kill switch: cancel resting orders, market liquidate active positions, halt trading.
- `uv run copilot resume`: Clear emergency trading halt and resume autonomous scanning/execution.
- `uv run copilot gex [symbol]`: Market maker dealer gamma exposure, pinning walls, and gamma flip.
- `uv run copilot pairs`: Cross-asset cointegration, mean-reversion half-life, and rolling spread Z-scores.
- `uv run copilot metrics`: Dump Prometheus exposition snapshot or launch standalone server (`--serve`).
- `uv run copilot test-alert`: Send synthetic signal card to verify Telegram buttons and terminal display.

#### C. Quantitative Research, Backtesting & Calibration (Offline)
- `uv run copilot backtest`: Offline backtest with friction, Cash-Plus attribution, & Monte Carlo (`--symbols`, `--strategy`, `--lookback`).
- `uv run copilot optimize`: Parameter grid search and rolling walk-forward validation (`--walk-forward`, `--splits`).
- `uv run copilot retune`: Automated parameter recalibration with Walk-Forward Efficiency (WFE) filtering.
- `uv run copilot stress`: Crisis replay (2008 GFC, 2020 COVID, 2022 Inflation) & factor shocks (`--scenario`).
- `uv run copilot eval`: Benchmark LLM decision prompts against risk invariants (Promptfoo).

#### D. Database Schema Migrations (`copilot db`)
- `uv run copilot db upgrade head`: Apply pending migrations.
- `uv run copilot db current`: View current revision.
- `uv run copilot db history`: View migration history.
- `uv run copilot db downgrade -1`: Rollback last migration.

---

## 3. Testing, Linting & Quality Control

This repository enforces strict code quality and 100% pre-commit compliance before every commit:

- **Pre-commit checks (mandatory before any commit)**:
  `uv run pre-commit run --all-files`
- **Run test suite**:
  `uv run pytest` (247 unit tests across modular subdirectories)
- **Run targeted component tests**:
  `uv run pytest tests/agent/ tests/broker/ tests/market/`
- **Run test suite with code coverage**:
  `uv run pytest --cov=agentic_trader --cov-report=term-missing`
- **Rust-accelerated impacted tests**:
  `uv run pytest tests --impacted --impacted-module=agentic_trader --impacted-tests-dir=tests`
- **Linter & Formatter**:
  `uv run ruff check --fix && uv run ruff format`
- **Type Checking**:
  `uv run mypy agentic_trader`
- **Promptfoo Evaluations**:
  `uv run copilot eval` (or `npx -y promptfoo eval -c evals/promptfooconfig.yaml --no-cache`)

---

## 4. Architecture & Key Directory Layout

- `agentic_trader/constants.py`: Centralized domain constants, symbols, multipliers, tick sizes, HTTP timeouts, URLs, and status strings.
- `agentic_trader/cli/`: Modular Click CLI package hierarchy (`main.py` and `commands/` for `scan`, `trade`, `backtest`, `research`, `stress`, `options`, `pairs`, `telemetry`, `service`, `db`).
- `agentic_trader/agent/`:
  - `copilot.py`: `TradingCopilot` orchestration engine (`FuturesCopilot` backward-compatibility alias).
  - `evaluator.py`: LiteLLM trade evaluator and risk invariant gating.
  - `calendar.py`: Economic calendar with macro lockout detection (`ForexFactoryCalendar`).
  - `regime.py`: Real-time market regime classifier (VIX, 10Y Treasury yield, Dollar Index).
- `agentic_trader/presentation/`:
  - `formatters.py`: Decoupled presentation DTOs (`PositionView`, `PositionsReport`, `PortfolioStatusReport`, `ExecutionResultView`, `PerformanceSummaryReport`, `PanicReportView`) and formatters (`TerminalFormatter`, `TelegramHtmlFormatter`).
- `agentic_trader/market/`:
  - `session.py`: Market session & trading hours protocol (`MarketSessionProtocol`), CME Globex holiday calendar (`MarketHolidayCalendar`), timezone normalization (`ensure_et`), and composite routing (`CompositeMarketSessionProvider`).
- `agentic_trader/resilience/`:
  - `fallback.py`: Generic LangChain-inspired resilience engine (`RunnableWithFallbacks`, `RetryPolicy`, `AllFallbacksExhaustedError`) with timeout and exponential backoff retry.
- `agentic_trader/data/`:
  - `providers.py`: Provider protocol (`MarketDataProvider`), `AlpacaDataProvider` (stock/crypto), `YFinanceDataProvider` (futures/fallback), and `CompositeMarketDataProvider`.
  - `market_data.py`: Technical indicator calculations and market data fetcher delegating to `CompositeMarketDataProvider`.
- `agentic_trader/broker/`:
  - `base.py`: Standardized broker interface (`BaseBroker`, `OrderRequest`, `OrderResult`, `supports_order_modification`, `modify_order_stop`, `cancel_all_orders`).
  - `paper.py`: Simulated paper execution with dynamic contract multipliers and resting bracket stop modification.
  - `tradovate.py`: Headless REST API execution with native server-side OCO brackets, `/order/modifyorder`, and `/order/cancelorder`.
  - `alpaca.py`: Official `alpaca-py` TradingClient SDK integration with bracket orders, `TradingStream` WebSocket, resting stop leg replacement, and mass cancellation (`cancel_orders`).
- `agentic_trader/pairs/`:
  - `cointegration.py`: Engle-Granger two-step cointegration test, Ornstein-Uhlenbeck half-life modeling, and rolling spread Z-scores.
  - `screener.py`: `PairsScreener` cross-asset scanner with futures proxy mapping.
  - `reporting.py`: ASCII and Telegram HTML report formatters.
- `agentic_trader/options/`:
  - `gex.py`: Analytical Black-Scholes Greeks, dealer Gamma Exposure (GEX), and Gamma Flip detection.
  - `fetcher.py`: Option chain fetcher with TTL caching and ETF proxy mapping.
- `agentic_trader/telemetry/`:
  - `collector.py`: Thread-safe Prometheus metrics registry (`MetricsCollector`).
  - `server.py`: Lightweight async HTTP server serving `/metrics` and `/healthz`.
- `agentic_trader/screeners/`: Technical indicators (EMA, Wilder RSI, ATR, Bollinger, Keltner, Squeeze) and strategy rules.
- `agentic_trader/backtest/`: Backtest engine, transaction friction, Cash-Plus attribution, Monte Carlo simulation, crisis replay, and dynamic Chandelier ATR trailing stop ratcheting.
- `agentic_trader/research/`: VectorBT-based parameter grid optimizer and rolling walk-forward cross-validation engine.
- `agentic_trader/storage/`: SQLAlchemy 2.0 ORM models (`SignalRecord`, `PositionModel`, `TradeAuditModel`, `SystemStateRecord`) backing SQLite (`data/signals.db`).
- `agentic_trader/notifier/telegram_bot.py`: Interactive Telegram bot with command handlers (`/status`, `/positions`, `/perf`, `/regime`, `/gex`, `/pairs`, `/backtest`, `/close`, `/scan`, `/panic`, `/resume`), client-side slash autocomplete (`set_my_commands`), and execution buttons.
- `tests/`: Modular test hierarchy mirroring `agentic_trader/` packages (`tests/agent/`, `tests/broker/`, etc.) with root `tests/conftest.py` shared fixtures.
- `docs/`: GitHub Pages Jekyll documentation site (`_config.yml`, `index.md`, `production.md`, `cli-reference.md`, `strategies.md`, `roadmap.md`).

---

## 5. Hard Domain Invariants & Business Rules

When writing or modifying logic, NEVER violate these core constraints:
1. **Scope**: Micro futures (`/MES`, `/MNQ`, `/MGC`, `/MCL`) and liquid ETF proxies (`SPY`, `QQQ`, `IWM`, `GLD`, `USO`).
2. **Fixed Sizing**: 1 micro contract per signal.
3. **Notional Exposure Ceiling**: $\le \$60,000$ total active open notional exposure across all concurrent positions (0.6x effective leverage on $100k cash).
4. **Risk-to-Reward Ratio**: Strictly $R:R \ge 2.0$.
5. **Structural Stop Distance**: Minimum stop distance $\ge 1.5 \times \text{ATR}(14)$.
6. **Macro Lockout**: NO entry alerts within $[-60\text{m}, +30\text{m}]$ of Tier-1 releases (CPI, PPI, FOMC, NFP).
7. **Signal Deduplication**: No duplicate signal for the same contract + strategy within 12 hours.
8. **Execution Safety**: Execution buttons must route through `broker.submit_entry_order()`. If rejected, status becomes `FAILED` without locking notional capacity.
9. **Single Steady-State Daemon**: Production runs only `copilot daemon`.
10. **Emergency Kill Switch & Halt Integrity**: When halted (`trading_halted=true` in `system_state`), all market scans and order submissions are strictly blocked across CLI, scheduled daemon jobs, and Telegram. Resumption requires explicit `/resume` or `copilot resume`.
