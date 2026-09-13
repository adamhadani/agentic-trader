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
