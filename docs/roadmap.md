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
| **Phase 7** | Dynamic Strategy Configuration & Parameter Export | **Completed** | Parameterize strategy screeners via config, export optimal params from CLI |
| **Phase 8** | Telegram Interactive Commands (`/perf`, `/regime`, `/backtest`) | **Completed** | Mobile oversight, live portfolio performance attribution, real-time regime view |
| **Phase 9** | Real-Time WebSocket Streaming for Alpaca (`TradingStream`) | **Completed** | Sub-second event-driven fills, bracket execution, and liquidation push alerts |
| **Phase 10** | Portfolio Risk Budgeting & Correlation Filtering | **Completed** | Sector/asset class allocation caps, cross-asset correlation guardrails |
| **Phase 11** | Walk-Forward Out-of-Sample Validation in Research | **Completed** | Rolling train/test windows in research to guard against parameter overfitting |
| **Phase 12** | Monte Carlo Risk Simulation in Backtester | **Completed** | Resample trade returns and drawdown distributions with 95%/99% VaR and CVaR confidence bounds |

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
3. **`/backtest` Command**:
   - On-demand backtesting from Telegram (e.g. `/backtest SPY 1y`) returning Total Net Return, CAGR, Sharpe, Drawdown, and Cash-Plus Yield.
4. **Interactive Buttons**:
   - Inline action buttons to trigger `/scan`, check `/positions`, view `/perf`, or inspect `/regime` without typing.

### Implementation Summary
- **Database Analytics (`agentic_trader/storage/db.py`)**: Added `get_closed_positions_stats()` aggregating total trades, wins/losses, win rate %, gross profit/loss, and profit factor.
- **Bot Handlers (`agentic_trader/notifier/telegram_bot.py`)**: Implemented `/perf`, `/regime`, and `/backtest` command handlers, alongside inline buttons (`cmd_scan`, `cmd_positions`, `cmd_perf`, `cmd_regime`).
- **Copilot Integration (`agentic_trader/main.py`)**: Added `get_performance_summary_html`, `get_regime_summary_html`, and `run_backtest_summary_html` to `FuturesCopilot` and wired them to `TelegramNotifier`.
- **Test Suite (`tests/test_telegram_interactive.py`)**: 3 test cases validating closed positions stats math, HTML formatting, command dispatch, and interactive button callbacks.


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

### Implementation Summary
- **Broker Streaming (`agentic_trader/broker/alpaca.py`)**: Implemented `start_trade_stream` and `stop_trade_stream` using Alpaca's `TradingStream`, binding `_on_trade_update` handlers with deduplication checks.
- **Position Reconciliation Deduplication (`agentic_trader/main.py`)**: Updated `process_reconciliation_event` and `on_stream_trade_update` with an in-memory lock set (`_reconciliation_lock`) to prevent concurrent race conditions between WebSocket events and periodic polling loops.
- **Daemon Lifecycle Management (`agentic_trader/main.py`)**: Launched the trade stream as a persistent background asyncio task in `start_daemon()`, cleanly terminating during shutdown.
- **Test Suite (`tests/test_alpaca_stream.py`)**: 4 unit and integration tests verifying WebSocket stream startup, callback event parsing, bracket fill detection, and reconciliation deduplication.

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

### Implementation Summary
- **Risk Budget Configuration (`agentic_trader/config.py`)**: Added `max_futures_exposure`, `max_equity_exposure`, `max_crypto_exposure`, and `max_correlated_positions` to `PortfolioConfig`.
- **Correlation Groups & Dynamic Return Correlation (`agentic_trader/evaluator/risk.py`, `agentic_trader/data/market_data.py`)**:
  - Predefined correlation clusters for US Tech (`QQQ`, `/MNQ`), US Broad Market (`SPY`, `/MES`), Precious Metals (`GLD`, `/MGC`), and Energy (`XLE`, `/MCL`).
  - Added `calculate_correlation(ticker1, ticker2, lookback_days)` to `MarketDataFetcher` for dynamic 60-day Pearson log-return correlation.
  - Added asset-class capacity filters and correlation limit enforcement (> 0.75 threshold) in `RiskEvaluator.evaluate_candidate`.
- **Test Suite (`tests/test_risk_budgeting.py`)**: 6 comprehensive unit tests verifying futures/equity exposure caps, correlation cluster enforcement, statistical return correlation matrix, and entry rejection mechanics.

---

## Phase 11: Walk-Forward Out-of-Sample Validation in Research

### Objective
Prevent hyperparameter overfitting and quantify strategy parameter stability over rolling market regimes.

### Key Deliverables
1. **Rolling Walk-Forward Engine**:
   - Split historical data into in-sample optimization windows and out-of-sample test windows (expanding/rolling anchor).
2. **Robustness Scoring**:
   - Calculate parameter efficiency ratio (Walk-Forward Efficiency: out-of-sample return / in-sample return, and out-of-sample Sharpe).
3. **CLI Command**:
   - Add `--walk-forward` and `--splits` flags to `copilot optimize`.

### Implementation Summary
- **Data Models (`agentic_trader/research/models.py`, `agentic_trader/research/__init__.py`)**:
  - `WalkForwardFold`: Dataclass encapsulating fold index, train/test date windows, winning in-sample parameters, out-of-sample return, out-of-sample Sharpe, and WFE ratio.
  - `ParameterCandidate`: Extended with `is_return_pct`, `oos_return_pct`, `is_sharpe`, `oos_sharpe`, and `wfe_ratio`.
  - `OptimizationResult`: Extended with `is_walk_forward`, `walk_forward_folds`, and `avg_wfe_ratio`.
- **Walk-Forward Optimizer (`agentic_trader/research/optimizer.py`)**:
  - Added `_evaluate_parameters()` for targeted single-candidate validation.
  - Implemented expanding anchor cross-validation over `splits` rolling folds with WFE calculation.
  - Full-grid candidate ranking prioritizing out-of-sample Sharpe and positive Walk-Forward Efficiency.
- **Reporting (`agentic_trader/research/reporting.py`)**:
  - Formatted ASCII walk-forward report with fold-by-fold chronological breakdown, train vs test return comparisons, and robustness rating (PASS if WFE >= 0.50).
- **CLI Subcommand (`agentic_trader/main.py`)**:
  - Added `--walk-forward` and `--splits` arguments to `copilot optimize`.
- **Test Suite (`tests/test_walk_forward.py`, `tests/test_optimizer.py`)**:
  - 4 unit tests verifying fold generation, candidate out-of-sample attributes, reporting tables, and fallback on short histories.

---

## Phase 12: Monte Carlo Risk Simulation & Confidence Intervals in Backtester

### Objective
Quantify tail risk, drawdown distributions, and risk of ruin beyond single historical execution paths using bootstrap resampling.

### Key Deliverables
1. **Bootstrap Resampling Engine (`agentic_trader/backtest/monte_carlo.py`)**:
   - Resample historical trades with replacement across $N$ iterations (default 1,000).
   - Compute full simulated equity and drawdown curves.
2. **Quant Risk & Ruin Metrics**:
   - 90% Confidence Interval on ending portfolio equity (5th vs 95th percentile).
   - 95th Percentile Worst-Case Drawdown.
   - Risk of Ruin (% simulations experiencing $\ge 10\%$ or $\ge 20\%$ drawdowns).
   - 95% Value at Risk (VaR) and 95% Conditional Value at Risk (CVaR / Expected Shortfall).
3. **CLI & Mobile Integration**:
   - Added `--monte-carlo` and `--mc-sims` flags to `copilot backtest`.
   - Integrated into Telegram `/backtest` response cards.

### Implementation Summary
- **Data Models (`agentic_trader/backtest/models.py`, `agentic_trader/backtest/__init__.py`)**: Added `MonteCarloResult` dataclass and `monte_carlo` attribute to `BacktestResult`.
- **Simulation Engine (`agentic_trader/backtest/monte_carlo.py`)**: Implemented `run_monte_carlo_simulation()` with vectorized NumPy matrix operations and zero-division protections.
- **Reporting (`agentic_trader/backtest/reporting.py`)**: Formatted dedicated Monte Carlo risk attribution section in institutional ASCII backtest report.
- **CLI & Telegram (`agentic_trader/main.py`)**: Added `--monte-carlo` flag to `backtest` command and embedded worst-case drawdown / VaR into Telegram `/backtest` summaries.
- **Test Suite (`tests/test_monte_carlo.py`, `tests/test_backtest.py`)**: 4 unit tests verifying sample sizing, metric calculations, random seed determinism, report formatting, and CLI arguments.

---

## Next Horizon: Upcoming Strategic Targets

| Priority | Target Area | Status | Focus |
|---|---|---|---|
| **Phase 13** | Slippage & Realistic Fee/Commission Modeling | **Planned** | Exchange clearing fees, NFA fees, broker commissions, and volume-weighted bid-ask spread slippage |
| **Phase 14** | Automated Scheduled Retuning Daemon | **Planned** | Background weekend calibration job updating strategy config thresholds based on rolling WFE |
| **Phase 15** | Tradovate WebSocket Stream & Broker Redundancy | **Planned** | Real-time WebSocket connection to Tradovate broker API with multi-broker fallback |
