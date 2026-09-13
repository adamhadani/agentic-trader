# Cash-Plus Trading Copilot: Architectural Roadmap & High-Value Targets

This document tracks the prioritized strategic initiatives for the **Cash-Plus Trading Copilot**, establishing architectural milestones, component breakdowns, and implementation status.

---

## Strategic Initiatives Overview

| Priority | Target Area | Status | Focus |
|---|---|---|---|
| **Phase 1** | Real-Time Broker Order Reconciliation & Fill Streaming | **Completed** | Background position reconciliation loop, WebSocket fill events (Alpaca / Tradovate) |
| **Phase 2** | Multi-Asset Screener Expansion (Equities & Liquid ETFs) | **Completed** | Equity universe config, multi-asset technical screener, dollar-risk sizing |
| **Phase 3** | Persistence & Migration Hardening (Alembic) | **Completed** | Alembic database migrations, multi-backend support (PostgreSQL ready) |
| **Phase 4** | Macro & Volatility Regime Filter | **Completed** | `RegimeDetector` (`^VIX`, `^TNX`, `DX-Y`), quantitative regime context in LLM prompts |
| **Phase 5** | Offline Vectorized Backtester & Performance Analytics | **Completed** | Historical strategy backtesting, equity curve simulation, Sharpe/drawdown metrics |
| **Phase 6** | VectorBT Research & Parameter Grid Optimization | **Completed** | High-throughput tensor parameter search (`copilot optimize`), NumPy fallback |
| **Phase 7** | Dynamic Strategy Configuration & Parameter Export | **In Progress** | Parameterize strategy screeners via config, export optimal params from CLI |
| **Phase 8** | Telegram Interactive Commands (`/perf`, `/regime`, `/backtest`) | **Planned** | Mobile oversight, live portfolio performance attribution, real-time regime view |
| **Phase 9** | Real-Time WebSocket Streaming for Alpaca (`TradingStream`) | **Planned** | Sub-second event-driven fills, bracket execution, and liquidation push alerts |
| **Phase 10** | Portfolio Risk Budgeting & Correlation Filtering | **Planned** | Sector/asset class allocation caps, cross-asset correlation guardrails |
| **Phase 11** | Walk-Forward Out-of-Sample Validation in Research | **Planned** | Rolling train/test windows in research to guard against parameter overfitting |

---

## Phase 1: Real-Time Broker Order Reconciliation & Fill Streaming

### Objective
Currently, `PaperBroker` detects TP/SL hits through a 15-minute polling loop against market quotes. For real brokers (`TradovateBroker` and `AlpacaBroker`), bracket stop-loss and take-profit orders live server-side on the broker infrastructure. This phase introduces proactive order reconciliation and event-driven fill streaming.

### Key Deliverables
1. **`broker.reconcile_positions()` Interface Method**:
   - Standardized method on `BaseBroker` that queries the broker API for open/filled bracket orders.
   - Cross-references active positions in SQLite with broker order statuses.
   - Detects when a server-side bracket leg (stop-loss or take-profit) was filled.
2. **Database & Lifecycle Synchronization**:
   - Marks filled positions as `CLOSED_WIN` or `CLOSED_LOSS` in SQLite.
   - Records actual broker fill price, execution timestamp, and realized P&L.
   - Releases notional capacity in portfolio risk calculations.
   - Emits real-time exit alerts to Telegram.
3. **Background Daemon Integration**:
   - Integrates periodic reconciliation (e.g. every 60 seconds) into `APScheduler` in `copilot daemon`.
4. **WebSocket Stream Support**:
   - Scaffold event listener for Alpaca `TradingStream` (`trade_updates`) to receive sub-second fill notices.

---

## Phase 2: Multi-Asset Screener Expansion (Equities & Liquid ETFs)

### Objective
Expand quantitative scanning beyond micro futures (`/MES`, `/MNQ`, `/MGC`, `/MCL`) into liquid index ETFs (`SPY`, `QQQ`, `IWM`, `GLD`, `TLT`) and high-beta equities (`NVDA`, `AAPL`, etc.), deploying idle cash into high-conviction swing setups via Alpaca.

### Key Deliverables
1. **Configurable Universe in `config.yaml`**:
   - Define asset classes and watchlists (`universe.futures`, `universe.equities`).
2. **Strategy Engine Generalization**:
   - Allow `Trend-Pullback` and `Squeeze Breakout` algorithms to evaluate equity bar data alongside futures.
3. **Dynamic Position Sizing**:
   - Calculate fractional share quantities for equities based on fixed dollar risk (e.g., $250 or 0.25% of cash) rather than 1-contract micro futures sizing.

---

## Phase 3: Persistence & Migration Hardening (Alembic)

### Objective
Now that the database layer uses SQLAlchemy 2.0 ORM (`SignalRecord`, `Base`), introduce formal schema migration management using Alembic.

### Key Deliverables
1. **Alembic Initialization**:
   - Setup `alembic/` directory and `alembic.ini` configured for `sqlite+aiosqlite` and PostgreSQL.
2. **Migration Baselines**:
   - Initial revision capturing the current `signals` schema.
3. **Schema Evolution**:
   - Support future schema enhancements: execution commissions, fees, partial fills, slippage, and multi-leg order tracking.

---

## Phase 4: Macro & Volatility Regime Filter

### Objective
Provide macro and volatility regime awareness to the LLM evaluator to adaptively modulate strategy aggression.

### Key Deliverables
1. **`RegimeDetector` Component**:
   - Monitors `^VIX` (volatility regime: compressed < 15, elevated 15–25, extreme > 25).
   - Monitors 10-year Treasury yield (`^TNX`) and US Dollar Index (`DX-Y.NYB`).
2. **LLM Prompt Context Integration**:
   - Inject quantitative regime indicators into the prompt alongside the economic calendar.
3. **Adaptive Rules**:
   - Suppress breakout strategies in high-volatility chop; favor mean-reversion during low-volatility compression.

---

## Phase 5: Offline Vectorized Backtester & Analytics

### Objective
Validate strategy expectancy and quantify risk/return metrics across multi-year historical datasets.

### Key Deliverables
1. **`copilot backtest` CLI Subcommand**:
   - Parameters for strategy, symbol, timeframe, and historical lookback.
2. **Performance Metrics Output**:
   - Win rate, profit factor, max drawdown, Sharpe ratio, and average trade duration.
3. **Portfolio Equity Simulator**:
   - Combines baseline risk-free cash yield (money-market return) with swing trading alpha.

### Implementation Summary
- **Data Structures (`agentic_trader/backtest/models.py`)**: `BacktestTrade`, `EquityPoint`, and `BacktestResult` encapsulating simulated executions, timeline equity snapshots, and aggregate risk metrics.
- **Metric Analytics (`agentic_trader/backtest/metrics.py`)**: Pure vector calculation of win rate, profit factor, running and maximum drawdown %, annualized Sharpe ratio, and Sortino ratio with zero-variance safeguards.
- **Simulation Engine (`agentic_trader/backtest/engine.py`)**: `BacktestEngine` with historical data ingestion via `yfinance`, bar-by-bar chronological stepping, deterministic bracket TP/SL exits, dynamic position sizing, portfolio exposure constraints, and daily cash reserve interest accrual (4.5% risk-free rate).
- **Institutional ASCII Reporting (`agentic_trader/backtest/reporting.py`)**: `format_backtest_report` presenting pure strategy alpha, cash reserve yield, risk-adjusted performance, trade statistics, and top winners/losers.
- **CLI Subcommand (`agentic_trader/main.py`)**: Added `backtest` command with `--symbols`, `--strategy`, `--lookback`, `--cash`, and `--risk-free-rate`.
- **Test Suite (`tests/test_backtest.py`)**: 9 test cases verifying metric calculation edge cases, synthetic market data simulation, bracket exits, and CLI invocation.

---

## Phase 6: VectorBT Research & Parameter Grid Optimization

### Objective
High-throughput parameter sensitivity analysis and hyperparameter optimization layer for discovering robust strategy parameters.

### Implementation Summary
- **Optional Dependencies (`pyproject.toml`)**: Added `[project.optional-dependencies] research = ["vectorbt>=1.1.0", "plotly>=5.24.0,<6.0.0"]`.
- **Data Models (`agentic_trader/research/models.py`)**: `ParameterCandidate` and `OptimizationResult` storing hyperparameter sets, win rates, Sharpe ratios, drawdowns, and profit factors.
- **`ParameterGridOptimizer` (`agentic_trader/research/optimizer.py`)**: Dual-mode engine supporting VectorBT Numba JIT tensor simulation and pure vectorized NumPy fallback simulation.
- **Reporting (`agentic_trader/research/reporting.py`)**: ASCII table reporting with top parameter candidate rankings.
- **CLI Subcommand (`agentic_trader/main.py`)**: Added `copilot optimize` with `--symbol`, `--strategy`, `--lookback`, `--top-n`, and `--no-vbt`.
- **Test Suite (`tests/test_optimizer.py`)**: 5 unit and integration tests verifying parameter evaluation, report formatting, NumPy fallback, and CLI parser.

---

## Phase 7: Dynamic Strategy Configuration & Parameter Export

### Objective
Bridge the gap between research optimization and live execution by making strategy screening parameters fully configurable via `config.yaml` / CLI, and enabling direct parameter export from `copilot optimize`.

### Key Deliverables
1. **Config Models (`agentic_trader/config.py`)**:
   - `TrendPullbackConfig`: `fast_ema: int = 20`, `slow_ema: int = 50`, `rsi_oversold: float = 40.0`, `atr_multiplier: float = 1.5`
   - `SqueezeBreakoutConfig`: `volume_factor: float = 1.2`, `min_squeeze_bars: int = 4`
   - Integrated into `AppConfig.strategies`.
2. **Strategy Screener Refactoring (`agentic_trader/scanner/screeners.py`)**:
   - Update `TrendPullbackScreener` and `SqueezeBreakoutScreener` constructors to accept strategy config objects (or fall back to `AppConfig`).
   - Use dynamic indicators (e.g. dynamic fast/slow EMA periods, dynamic RSI oversold levels, dynamic volume surge multiplier).
3. **`copilot optimize --export-config`**:
   - Option to output the top parameter candidate as a YAML or JSON snippet, or update `config.yaml` directly.
4. **Test Coverage**:
   - Test custom parameters across screeners and verify default fallback behavior.

---

## Phase 8: Telegram Interactive Commands (`/perf`, `/regime`, `/backtest`)

### Objective
Empower mobile oversight, real-time risk checks, and quick analytics directly from Telegram.

### Key Deliverables
1. **`/perf` Command**:
   - Query SQLite for cumulative closed trades (`CLOSED_WIN`, `CLOSED_LOSS`), total realized P&L, win rate %, and current open positions with unrealized risk.
2. **`/regime` Command**:
   - Query current real-time VIX level, 10-Year Treasury yield (`^TNX`), and Dollar Index (`DX-Y.NYB`) with qualitative status (e.g. "Low Volatility Compression", "Normal", "Extreme Volatility").
3. **Interactive Buttons**:
   - Inline action buttons to trigger `/scan` or check `/status` without typing.

---

## Phase 9: Real-Time WebSocket Streaming for Alpaca (`TradingStream`)

### Objective
Transition from periodic polling reconciliation to sub-second event-driven order fill and bracket leg synchronization.

### Key Deliverables
1. **Alpaca `TradingStream` Scaffold**:
   - Subscribe to trade updates (`fill`, `partial_fill`, `canceled`, `expired`).
2. **Event Dispatcher**:
   - On bracket take-profit or stop-loss fill, immediately update database record to `CLOSED_WIN` or `CLOSED_LOSS`.
   - Dispatch instant Telegram alert with fill price, execution timestamp, and realized P&L.
3. **Resilience**:
   - Auto-reconnect with exponential backoff and fallback to periodic polling reconciliation if the WebSocket disconnects.

---

## Phase 10: Portfolio Risk Budgeting & Correlation Filtering

### Objective
Prevent concentrated risk across correlated assets and enforce macro allocation boundaries.

### Key Deliverables
1. **Asset Class & Sector Allocation Caps**:
   - Max capital allocation by asset class (e.g. max 50% futures margin, max 50% equity notional).
2. **Correlation Matrix & Cross-Asset Guardrails**:
   - Prevent simultaneous long entries in highly correlated assets (e.g. `/MNQ` and `QQQ`) if aggregate sector beta exceeds risk limits.
3. **Portfolio Heat Check**:
   - Reject new entries if aggregate total portfolio open dollar risk exceeds a configured threshold (e.g. 2.0% of total equity).

---

## Phase 11: Walk-Forward Out-of-Sample Validation in Research

### Objective
Prevent hyperparameter overfitting and quantify strategy parameter stability over rolling market regimes.

### Key Deliverables
1. **Rolling Walk-Forward Engine**:
   - Split historical data into in-sample optimization windows (e.g. 12 months) and out-of-sample test windows (e.g. 3 months).
2. **Robustness Scoring**:
   - Calculate parameter efficiency ratio (out-of-sample Sharpe / in-sample Sharpe).
3. **CLI Command**:
   - Add `--walk-forward` flag to `copilot optimize`.
