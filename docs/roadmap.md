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
| **Phase 13** | Slippage & Realistic Fee/Commission Modeling | **Completed** | Exchange clearing fees, NFA fees, broker commissions, and volume-weighted bid-ask spread slippage |
| **Phase 14** | Automated Scheduled Retuning Daemon | **Completed** | Background weekend calibration job updating strategy config thresholds based on rolling WFE |
| **Phase 15** | Tradovate WebSocket Stream & Broker Redundancy | **Completed** | Real-time WebSocket connection to Tradovate order socket with circuit-breaker failover |
| **Phase 16** | Cross-Asset Factor & Regime Attribution | **Completed** | Factor decomposition (Momentum, Volatility, Carry) and Sharpe attribution across market regimes |
| **Phase 17** | Dynamic Volatility-Targeted Position Sizing | **Completed** | Continuous ATR / Kelly risk scaling adapting contract and equity size to real-time volatility |
| **Phase 18** | Execution Microstructure & Adaptive TWAP/VWAP Slicing | **Completed** | Algorithmic execution slicing for larger equity and multi-contract orders to minimize market impact |
| **Phase 19** | Portfolio Stress Testing & Historical Macro Crisis Replay | **Completed** | Historical crisis scenario replay (2008 GFC, 2020 COVID Crash, 2022 Inflation Shock) |
| **Phase 20** | Options Implied Volatility Surface & GEX Tracking | **Completed** | Market maker gamma exposure (GEX), Call/Put walls, Gamma Flip, Put/Call ratios, and Telegram `/gex` |
| **Phase 21** | Prometheus Telemetry Exporter & Modular CLI Hierarchy | **Completed** | Production metrics endpoints, Grafana-ready telemetry, Click modular command hierarchy |
| **Phase 22** | Cointegration & Statistical Pairs Trading Screener | **Completed** | Cross-asset pairs cointegration, Ornstein-Uhlenbeck half-life modeling, rolling Z-score arbitrage |
| **Phase 23** | Regular Trading Hours (RTH) & Market Session Filtering | **Completed** | `MarketSessionProtocol`, Alpaca dynamic exchange clock, CME Globex schedule, Crypto 24/7 |
| **Phase 24** | Dynamic Trailing Stops & Breakeven Position Management | **Completed** | Breakeven triggers (+1.0R), ATR trailing stop ratchets (+1.5R), SQLite tracking, Telegram alerts |
| **Phase 25** | Production Deployment Packaging (launchd & Watchdog) | **Completed** | macOS launchd service supervision, automated 60s background watchdog probe, doctor health checks |
| **Phase 26** | Quant Chandelier ATR Trailing Stop Benchmarking | **Completed** | Empirical backtest benchmark of retail BE vs Chandelier ATR, eliminating negative expectancy anchoring |
| **Phase 27** | Decoupled Presentation Layer & TradingCopilot Generalization | **Completed** | Domain DTOs (`presentation/formatters.py`), Terminal & Telegram cards, `TradingCopilot` generalization |
| **Phase 28** | CME Globex Holiday Calendar & Timezone Normalization | **Completed** | `MarketHolidayCalendar` accounting for holiday closures & early halts, robust ET timezone conversions |
| **Phase 29** | Resilient Market Data Provider Cascade (`RunnableWithFallbacks`) | **Completed** | Generic LangChain-style fallback engine, Alpaca historical bars primary, Yahoo Finance fallback |
| **Phase 30** | Broker-Side Trailing Stop Synchronization | **Completed** | Dynamic bracket stop modification on exchange brokers (Alpaca & Tradovate) with graceful degradation |
| **Phase 31** | Institutional Emergency Kill Switch & Telegram Autocomplete | **Completed** | 4-tier waterfall liquidation (`cancel_all_orders`, market flatten, persistent halt), `/panic` & `/resume`, `set_my_commands` |
| **Phase 32** | Multi-Signal Portfolio Diffing & Transition Engine | **Planned** | Transition between optimal position allocations across long sessions without over-allocation |
| **Phase 33** | Level-2 / Order Book Microstructure Flow Streaming | **Planned** | CME top-of-book (BBO) and DOM queue imbalance streaming via Tradovate WebSocket |
| **Phase 34** | Interactive Brokers (IBKR) Native Driver | **Planned** | Direct DMA execution via `ib_insync` or IBKR Client Portal REST API |
| **Phase 35** | Cloud Infrastructure & AWS Container Deployment | **Planned** | Containerized deployment on AWS ECS/Fargate or EC2 with Terraform/Ansible automation |

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

## Phase 13: Slippage & Realistic Fee/Commission Modeling

### Objective
Incorporate real-world exchange clearing fees, regulatory costs, broker commissions, and market impact/slippage into historical simulation and backtesting.

### Key Deliverables
1. **Friction Configuration (`agentic_trader/config.py`)**:
   - `FrictionConfig`: Futures clearing fees ($0.62/contract/side), equity commission ($0.005/share), micro futures tick slippage (0.25 pts), and equity spread slippage (2 bps).
2. **Backtest Engine Execution Friction (`agentic_trader/backtest/engine.py`)**:
   - Dynamic slippage penalty on trade entry (higher price for LONG, lower price for SHORT) and trade exit.
   - Entry and exit commission deduction from net realized P&L and cash reserves.
   - `BacktestTrade` and `BacktestResult` attributes for `commission`, `slippage_dollars`, `gross_strategy_pnl`, `total_commissions`, and `total_slippage`.
3. **Institutional Reporting & CLI (`agentic_trader/backtest/reporting.py`, `agentic_trader/main.py`)**:
   - Performance attribution breakdown: Gross Strategy Alpha, Execution Commissions, Bid-Ask Slippage Drag, Net Strategy Alpha.
   - Added `--no-friction` flag to `copilot backtest` for baseline frictionless comparison.

### Implementation Summary
- **Configuration (`agentic_trader/config.py`)**: Added `FrictionConfig` to `AppConfig` and `load_config()`.
- **Simulation Engine (`agentic_trader/backtest/engine.py`)**: Supported `apply_friction` in `BacktestEngine`, applying adverse price impact and commission tracking across both futures and equities.
- **Reporting (`agentic_trader/backtest/reporting.py`)**: Formatted detailed friction deduction lines in ASCII performance attribution table.
- **CLI Subcommand (`agentic_trader/main.py`)**: Added `--no-friction` argument to `backtest` command.
- **Test Suite (`tests/test_friction.py`, `tests/test_backtest.py`)**: 4 unit tests verifying config defaults, futures friction deduction, frictionless pure alpha mode, and report formatting.

---

## Phase 14: Automated Scheduled Retuning Daemon

### Objective
Maintain optimal, non-stale algorithmic strategy parameters by scheduling automatic walk-forward recalibration during weekend market closures, filtering overfitted candidates via Walk-Forward Efficiency (WFE) and Sharpe metrics, persisting robust parameters, and broadcasting audit reports to Telegram.

### Key Deliverables
1. **`AutoRetuner` Engine (`agentic_trader/research/retuner.py`)**:
   - Executes multi-symbol, multi-strategy walk-forward optimization across rolling historical folds.
   - Enforces minimum Walk-Forward Efficiency (WFE >= 0.50) and out-of-sample Sharpe thresholds (Sharpe >= 0.70) to discard overfitted parameter regimes.
   - Persists robust parameters into timestamped JSON/YAML calibration records.
   - Generates HTML executive summaries formatted for Telegram mobile broadcast.
   - Directly exports calibrated parameters to active `config.yaml` with backup preservation.
2. **Scheduled Daemon Execution (`agentic_trader/main.py`)**:
   - Integrated cron job into APScheduler (`copilot daemon`) running during weekend closures (e.g. Sunday 18:00 UTC).
   - Async execution via `asyncio.to_thread()` ensuring zero blocking of active risk management loops.
   - Mobile notification delivery via `TelegramNotifier.send_message()`.
3. **CLI Subcommand (`agentic_trader/main.py`)**:
   - Dedicated `copilot retune` command with `--symbols`, `--strategy`, `--min-wfe`, `--min-sharpe`, and `--export-config` flags.

### Implementation Summary
- **Daemon Engine (`agentic_trader/research/retuner.py`, `agentic_trader/research/__init__.py`)**: Implemented `AutoRetuner` class with calibration persistence, config updates, and HTML reporting.
- **Config & Schedule (`agentic_trader/config.py`)**: Added `retune_enabled`, `retune_day_of_week`, `retune_hour`, and `retune_minute` to `SchedulerConfig`.
- **CLI & Dispatch (`agentic_trader/main.py`)**: Added `retune` parser subcommand and integrated `run_auto_retune()` into daemon scheduler.
- **Telegram Dispatch (`agentic_trader/notifier/telegram_bot.py`)**: Added generic `send_message()` helper for formatted audit broadcasts.
- **Test Suite (`tests/test_retuner.py`)**: 5 unit tests verifying initialization, successful retune filtering, strict threshold rejection, persistence save/load, and config export.

---

## Phase 15: Tradovate WebSocket Stream & Broker Redundancy

### Objective
Provide sub-second real-time event-driven trade updates for Tradovate futures executions, proactive REST order/position reconciliation, and high-availability broker redundancy with circuit-breaker automated failover and recovery probing.

### Key Deliverables
1. **Tradovate Real-Time WebSocket Streaming (`TradovateBroker.start_trade_stream`, `stop_trade_stream`)**:
   - Establishes persistent WebSocket connection to Tradovate order socket (`wss://demo.tradovateapi.com/v1/websocket` or live).
   - Handles SockJS open frames (`o`), heartbeat keep-alive responses (`h` -> `[]`), and session authorization frames (`authorize\n1\n\n{token}`).
   - Parses incoming data frames (`a[...]`) for `props` entity updates (`fill` and filled `order` bracket executions).
   - Classifies stop-loss (`STOP_LOSS`), take-profit (`TAKE_PROFIT`), and manual exits, dispatching `ReconciliationEvent` instances.
2. **Tradovate REST Order & Position Reconciliation (`TradovateBroker.reconcile_positions`)**:
   - Queries `/position/list` to inspect active contracts and open positions.
   - Detects when active positions in SQLite are no longer open at the broker.
   - Inspects `/order/list` and `/fill/list` to match filled exit orders, calculate realized P&L based on contract point multipliers (/MES=5, /MNQ=2, /MGC=10, /MCL=100), and return `ReconciliationEvent` records.
3. **High-Availability Broker Redundancy (`RedundantBroker`)**:
   - Implements `BaseBroker` interface wrapping primary broker (e.g. Tradovate or Alpaca) and fallback broker (e.g. PaperBroker).
   - Circuit Breaker pattern with states `CLOSED` (healthy), `OPEN` (failover active), and `HALF_OPEN` (recovery probing).
   - Configurable `max_consecutive_failures` (default 3), `recovery_probe_interval_seconds` (default 60s), and `auto_failback`.
   - Aggregates positions and non-duplicating reconciliation events across both primary and fallback brokers.
4. **Configuration & Factory Wiring**:
   - `RedundancyConfig` added to `config.py` with environment variable overrides (`BROKER_REDUNDANCY_ENABLED`, `BROKER_FALLBACK_MODE`).
   - `create_broker` automatically wraps primary and fallback brokers into `RedundantBroker` when enabled.

### Implementation Summary
- **Tradovate Broker Enhancements (`agentic_trader/broker/tradovate.py`)**: Added WebSocket SockJS streaming listener, REST position reconciliation, and `/cashBalance/get` balance discovery.
- **Redundant Broker Engine (`agentic_trader/broker/redundant.py`)**: Implemented `RedundantBroker` with circuit-breaker state machine, order failover, and recovery cooldown probing.
- **Factory & Config (`agentic_trader/broker/__init__.py`, `agentic_trader/config.py`)**: Exported `RedundantBroker`, `CircuitState`, and wired `RedundancyConfig` into `AppConfig` and `create_broker()`.
- **Test Suite (`tests/test_tradovate_stream_and_redundancy.py`)**: 6 comprehensive unit tests verifying account balance fetching, REST exit reconciliation with contract root matching, WebSocket SockJS parsing, circuit breaker failover, half-open recovery, and factory instantiation.

---

## Phase 16: Cross-Asset Factor & Regime Attribution

### Objective
Quantify alpha sources and risk concentrations through multi-dimensional performance attribution: decomposing portfolio P&L across quantitative strategy factors (Momentum, Volatility Breakout, Cash Carry Yield), segmenting execution expectancy across macro market regimes (`COMPRESSED`, `NORMAL`, `ELEVATED`, `EXTREME`), and analyzing cross-asset class and sector cluster contributions.

### Key Deliverables
1. **Multi-Factor Return Decomposition (`agentic_trader/backtest/attribution.py`)**:
   - Isolates Trend-Pullback momentum alpha, Squeeze-Breakout expansion alpha, and cash-plus risk-free treasury accrual.
   - Calculates trade counts, win rates, profit factors, return contributions (% of capital), and factor alpha share (% of total strategy gains).
2. **Macro Volatility Regime Attribution**:
   - Matches trade entry timestamps against historical VIX levels to classify execution conditions (`COMPRESSED` < 15, `NORMAL` 15-22, `ELEVATED` 22-30, `EXTREME` > 30).
   - Computes regime-specific expectancy, win rates, profit factors, and average P&L to evaluate which environments generate genuine strategy alpha versus chop.
3. **Cross-Asset & Sector Cluster Analytics**:
   - Segregates performance across asset classes (Futures, Equities, Crypto) and sector clusters (`US Broad Market`, `US Tech`, `Precious Metals`, `Energy`, `US Treasuries`).
4. **Institutional Reporting & CLI Integration**:
   - Integrated `FACTOR & REGIME ATTRIBUTION` section into institutional ASCII report (`format_backtest_report`).
   - Added `--no-attribution` flag to `copilot backtest` CLI command.
   - Embedded top alpha driver highlighting in Telegram mobile `/backtest` summaries.

### Implementation Summary
- **Attribution Engine (`agentic_trader/backtest/attribution.py`)**: Implemented `calculate_performance_attribution()` supporting VIX alignment, factor isolation, and sector mapping.
- **Data Models (`agentic_trader/backtest/models.py`, `agentic_trader/backtest/__init__.py`)**: Added `FactorAttribution`, `RegimeAttribution`, `AssetClassAttribution`, and `PerformanceAttributionResult`.
- **Simulation Engine (`agentic_trader/backtest/engine.py`)**: Attached automated attribution calculation to `BacktestEngine.run()` output.
- **Reporting & Mobile (`agentic_trader/backtest/reporting.py`, `agentic_trader/main.py`)**: Enhanced ASCII report with factor/regime tables and Telegram summary cards.
- **Test Suite (`tests/test_attribution.py`)**: 3 unit tests verifying factor decomposition, VIX regime segmentation, asset class attribution, and report formatting.

---

## Phase 17: Dynamic Volatility-Targeted & Fractional Kelly Position Sizing

### Objective
Scale trade allocation dynamically inversely with market volatility and statistical setup expectancy, replacing fixed 1-contract sizing with continuous ATR risk scaling and Fractional Kelly optimization while enforcing rigorous safety guardrails.

### Key Deliverables
1. **Configurable Position Sizing Engine (`PositionSizingConfig`)**:
   - Modes supported: `static`, `volatility_targeted`, and `fractional_kelly`.
   - Tunable parameters: `target_risk_pct` (default 0.5%), `target_futures_risk_dollars` ($300.0), `default_equity_risk_dollars` ($250.0), `max_contracts_per_trade` (4), `min_contracts` (1), `max_shares_per_trade` (500), `min_shares` (1.0), and `kelly_fraction` (0.50).
2. **Dynamic Volatility Targeting**:
   - Calculates contract and share size continuously based on stop distance $D_{\text{stop}}$ and point multiplier:
     $$Q_{\text{raw}} = \frac{B_{\text{risk}}}{D_{\text{stop}} \times \text{multiplier}}$$
   - Compressed volatility regimes scale position size up toward `max_contracts_per_trade` / `max_shares_per_trade`.
   - Elevated volatility regimes scale position size down toward minimum contract/share bounds to defend against outsized stop-out drag.
3. **Fractional Kelly Scaling**:
   - Computes full Kelly fraction $f^* = p - \frac{1 - p}{b}$ from estimated win rate $p$ and setup payoff ratio $b = R:R$.
   - Multiplies base risk budget by half-Kelly scaling factor $K_{\text{mult}} \in [0.5, 2.0]$.
4. **Seamless System Integration & Full Backward Compatibility**:
   - Integrated into `RiskEvaluator.calculate_levels_deterministic()` and utilized across both live risk evaluation and vectorized `BacktestEngine`.
   - Defaults to `static` mode preserving exact historical sizing behaviors across legacy test suites.

### Implementation Summary
- **Position Sizing Module (`agentic_trader/agent/position_sizing.py`)**: Implemented `calculate_position_size` and `compute_fractional_kelly_multiplier`.
- **Configuration (`agentic_trader/config.py`, `config/config.yaml`)**: Added `PositionSizingConfig` model, wired into `AppConfig`, `load_config()`, and `config.yaml`.
- **Evaluator Integration (`agentic_trader/agent/evaluator.py`)**: Delegated deterministic level sizing to `calculate_position_size`.
- **Test Suite (`tests/test_position_sizing.py`)**: 5 unit tests validating static backward compatibility, volatility-targeted futures scaling, equity scaling, and Fractional Kelly evaluation.

---

## Phase 18: Execution Microstructure & Adaptive TWAP/VWAP Slicing

### Objective
Minimize market impact, spread crossing penalty, and adverse selection for larger trade allocations (e.g. multi-contract micro futures $\ge 2$ and large equity allocations $\ge 100$ shares) through algorithmic execution slicing (TWAP, Adaptive VWAP) combined with microstructure price-collar limits.

### Key Deliverables
1. **Microstructure Execution Models (`agentic_trader/execution/models.py`)**:
   - `ChildSlice`: Slice ID, index, slice quantity, target price, collar limit price, status (`PENDING`, `SUBMITTED`, `FILLED`, `SKIPPED_COLLAR`, `FAILED`), fill price, timestamp, and broker order ID.
   - `ExecutionPlan`: Master execution plan tracking total quantity, filled quantity, weighted average fill price, child slice breakdown, and bracket order IDs.
2. **Algorithmic Slicing & Price Collar Engine (`agentic_trader/execution/slicer.py`)**:
   - `compute_price_collar()`: Computes directional price collar ceiling (LONG) or floor (SHORT) based on ticks (futures) or percentage (equities), protecting against momentum runaways.
   - `slice_quantities()`: Supports discrete integer distributions for futures contracts (e.g. 3 contracts over 2 slices $\to$ [2, 1]) and continuous volume-weighted profiles for equities.
   - `plan_order()`: Automatically routes sub-threshold orders to immediate execution, while slicing multi-contract / multi-share orders into TWAP or VWAP execution schedules.
3. **Execution Engine (`agentic_trader/execution/engine.py`)**:
   - `SlicedExecutionEngine`: Dispatches child slices sequentially across configured time intervals (`twap_interval_seconds`).
   - Checks real-time market quote before slice dispatch to verify that price action has not breached the microstructure collar.
   - Aggregates child fills, computes true volume-weighted average fill price, and returns an unified `OrderResult`.
4. **Configuration & Live Dispatch Wiring**:
   - `ExecutionConfig` added to `config.py` (`algorithm`, `twap_slices`, `twap_interval_seconds`, `price_collar_ticks`, `price_collar_pct`, `vwap_intraday_profile`).
   - `FuturesCopilot.execute_signal()` routed through `self.execution_engine.execute_order()`, preserving 100% backward compatibility when `algorithm == "immediate"`.

### Implementation Summary
- **Execution Package (`agentic_trader/execution/`)**: Built `models.py`, `slicer.py`, and `engine.py`.
- **Configuration (`agentic_trader/config.py`, `config/config.yaml`)**: Added `ExecutionConfig`, wired into `AppConfig` and `load_config()`.
- **Copilot Integration (`agentic_trader/main.py`)**: Attached `self.execution_engine` to `FuturesCopilot` and routed signal executions.
- **Test Suite (`tests/test_execution_microstructure.py`)**: 6 unit tests validating price collar calculations, discrete futures slicing, equity VWAP profiles, size threshold filtering, TWAP execution simulation, and price collar breach protection.

---

## Phase 19: Portfolio Stress Testing & Historical Macro Crisis Replay

### Objective
Provide quantitative stress testing and tail-risk evaluation by replaying strategy execution across severe historical macro crises (2008 Global Financial Crisis, 2020 COVID Liquidity Crash, 2022 Inflation & Rate Hiking Shock, 2011 US Debt Downgrade) and simulating instantaneous cross-asset factor shocks.

### Key Deliverables
1. **Curated Crisis Scenario Catalog (`CRISIS_CATALOG`)**:
   - `2008_gfc`: 2008 Global Financial Crisis & Lehman Collapse (`2008-01-01` to `2009-03-31`, SPY -50.8%).
   - `2020_covid`: 2020 COVID Liquidity Shock (`2020-02-01` to `2020-04-30`, SPY -33.7%).
   - `2022_inflation`: 2022 Fed Rate Hiking & Tech Drawdown (`2022-01-01` to `2022-10-31`, SPY -24.5%).
   - `2011_debt_ceiling`: 2011 US Debt Ceiling & Sovereign Downgrade (`2011-07-01` to `2011-10-31`, SPY -18.6%).
   - `2015_flash_crash`: 2015 China Devaluation & August Flash Crash (`2015-08-01` to `2015-10-31`, SPY -12.1%).
2. **Automated Pre-2019 Micro-Futures Proxy Resolution**:
   - CME launched micro futures (`/MES`, `/MNQ`, `/MGC`, `/MCL`) in May 2019.
   - For historical windows prior to May 2019, `CrisisReplayEngine.resolve_proxy_symbols()` automatically maps micro contracts to high-liquidity ETF proxies (`/MES` $\to$ `SPY`, `/MNQ` $\to$ `QQQ`, `/MGC` $\to$ `GLD`, `/MCL` $\to$ `USO`), guaranteeing complete historical data coverage back to 2007.
3. **Historical Crisis Strategy Replay**:
   - Replays strategy bar-by-bar through the exact crisis timeframe using `BacktestEngine` with dynamic `start_date` and `end_date` bounds.
   - Computes peak-to-trough maximum drawdown, strategy total return, annualized return, benchmark return, relative alpha, Sharpe ratio, win rate, and drawdown duration (days).
4. **Instantaneous Parametric Factor Shock Engine**:
   - Evaluates instantaneous mark-to-market portfolio drawdowns under severe macro stress without requiring bar feeds:
     - `equity_market_crash`: Equities -20%, Tech -25%, Gold +5%, Crude -15%, Treasuries +8%.
     - `stagflation_shock`: Equities -10%, Crude +35%, Gold +12%, Treasuries -6%.
     - `rate_shock`: Equities -8%, Treasuries -15%, Gold -5%.
     - `liquidity_crisis`: Equities -15%, Gold -8%, Crude -20%, Treasuries +5%.
5. **Institutional Stress Reporting & CLI Integration**:
   - Added `format_stress_test_report()` and `format_instantaneous_shock_report()` to `reporting.py`.
   - Integrated `copilot stress` CLI command with flags: `--scenario` (`all`, `2008_gfc`, `2020_covid`, `2022_inflation`, `2011_debt_ceiling`, `2015_flash_crash`, `shock`), `--symbols`, `--strategy`, and `--cash`.

### Implementation Summary
- **Stress Engine (`agentic_trader/backtest/stress.py`)**: Implemented `CrisisReplayEngine`, `CrisisScenario`, `ScenarioStressResult`, `InstantaneousShockResult`, and `CRISIS_CATALOG`.
- **Backtest Boundaries (`agentic_trader/backtest/engine.py`)**: Added `start_date` and `end_date` support to data fetching and simulation execution.
- **Reporting (`agentic_trader/backtest/reporting.py`)**: Added institutional crisis tables with colorized drawdown risk levels.
- **CLI Subcommand (`agentic_trader/main.py`)**: Added `stress` subcommand to CLI.
- **Test Suite (`tests/test_stress_testing.py`)**: 7 unit tests covering scenario lookups, proxy resolution, replay execution, instantaneous shocks, and CLI commands.

---

## Phase 20: Options Implied Volatility Surface & GEX Tracking

### Objective
Provide quantitative options market telemetry by analyzing real-time option chains to compute market maker Gamma Exposure (GEX), zero-crossing Gamma Flip levels, Call/Put pinning walls, and Put/Call positioning ratios across equity indexes and liquid ETFs (SPY, QQQ, IWM, and futures proxies `/MES`, `/MNQ`).

### Key Deliverables
1. **Black-Scholes Analytical Greeks Engine (`agentic_trader/options/gex.py`)**:
   - `black_scholes_gamma()`: Exact analytical calculation of option gamma $\Gamma = \frac{N'(d_1)}{S \sigma \sqrt{T}}$ with numerical boundary safeguards for expiration singularities ($T \to 0$) and extreme volatility conditions.
2. **Dealer Gamma Exposure (GEX) Model**:
   - Computes aggregated market maker gamma exposure in $ Millions per 1% underlying price move:
     $$\text{Call GEX}_K = \Gamma_K \times \text{OI}_K \times 100 \times S^2 \times 0.01 \times 10^{-6}$$
     $$\text{Put GEX}_K = -\Gamma_K \times \text{OI}_K \times 100 \times S^2 \times 0.01 \times 10^{-6}$$
   - Classifies structural gamma regime:
     - `POSITIVE_GAMMA` (+GEX): Dealer inventory suppresses volatility; dip-buying and mean-reversion strategies thrive.
     - `NEGATIVE_GAMMA` (-GEX): Dealer delta-hedging amplifies volatility; momentum breakouts accelerate.
     - `NEUTRAL`: Balanced dealer inventory.
3. **Key Structural Pinning Walls & Gamma Flip Level**:
   - **Call Wall**: Strike with peak call open interest (major upside resistance / expiration pinning target).
   - **Put Wall**: Strike with peak put open interest (major downside support / dealer hedging floor).
   - **Gamma Flip Level**: Linear interpolation solving for the underlying index price where total net dealer gamma crosses zero.
4. **Options Data Pipeline & Micro Futures Proxy Resolution (`agentic_trader/options/fetcher.py`)**:
   - Resolves micro futures (`/MES`, `/MNQ`, `/M2K`, `/MGC`, `/MCL`) to high-liquidity ETF option proxies (`SPY`, `QQQ`, `IWM`, `GLD`, `USO`).
   - Fetches option chains across near-term expirations with TTL-based response caching to protect against rate limits.
5. **Institutional Reporting & Telegram Interactive Bot Integration**:
   - `format_gex_report()`: Institutional ASCII table displaying net GEX, regime status, Call/Put walls, Gamma Flip, Put/Call ratios, and near-the-money gamma concentration by strike.
   - `format_gex_telegram()`: HTML-formatted card for mobile Telegram delivery.
   - `copilot gex [SYMBOL]` CLI subcommand supporting `--expirations` and `--json`.
   - `/gex [SYMBOL]` Telegram interactive command.

### Implementation Summary
- **Options Package (`agentic_trader/options/`)**: Created `models.py`, `gex.py`, `fetcher.py`, `reporting.py`, and `__init__.py`.
- **Configuration (`agentic_trader/config.py`, `config/config.yaml`)**: Added `OptionsConfig` model with configurable default symbols, expirations, and risk-free rate.
- **Telegram Bot (`agentic_trader/notifier/telegram_bot.py`)**: Registered `/gex` command handler with `gex_provider` callback.
- **CLI & Copilot Integration (`agentic_trader/main.py`)**: Attached `self.options_fetcher` to `FuturesCopilot` and wired `gex` subcommand.
- **Test Suite (`tests/test_options_gex.py`)**: 8 comprehensive unit tests covering Black-Scholes gamma calculation, synthetic GEX aggregation, gamma flip detection, ETF proxy mapping, ASCII/Telegram formatters, and Telegram bot command dispatch.

---

---

## Phase 21: Real-Time Prometheus Metrics & Observability + Modular Click CLI Refactoring

### Objective
Provide enterprise-grade operational telemetry and observability via native Prometheus exposition (gauges, counters, histograms), standalone or daemon-embedded async HTTP exporter server (`/metrics` and `/healthz`), and refactor monolithic `main.py` into a clean, modular Click-based CLI package architecture (`agentic_trader/cli/`). Additionally, integrate rust-accelerated impacted test analysis (`pytest-impacted[fast]`) into developer workflows and pre-commit hooks.

### Key Deliverables
1. **Thread-Safe Prometheus Metrics Collector (`agentic_trader/telemetry/collector.py`, `models.py`)**:
   - `MetricsCollector`: Fully thread-safe in-memory store supporting `set_gauge`, `inc_counter`, `observe_histogram`, and `reset`.
   - Formats live metrics strictly complying with Prometheus plain-text exposition format 0.0.4.
   - Built-in operational metrics: `trader_up`, `trader_account_cash_dollars`, `trader_active_positions_count`, `trader_orders_filled_total`, and order execution latency histograms.
2. **Lightweight Asynchronous HTTP Exporter Server (`agentic_trader/telemetry/server.py`)**:
   - `MetricsServer`: Native `asyncio.start_server` lightweight HTTP daemon with zero external web framework dependencies.
   - Serves `GET /metrics` with `Content-Type: text/plain; version=0.0.4; charset=utf-8` and `GET /healthz` for Kubernetes/Docker container liveness probes.
   - Embeds seamlessly into continuous background trading daemons (`copilot daemon`) or runs standalone (`copilot metrics --serve`).
3. **Modular Click CLI Architecture (`agentic_trader/cli/`)**:
   - Refactored monolithic 1,578-line `main.py` into 11-line entrypoint forwarding to modular Click CLI package.
   - Subcommand implementations cleanly separated into dedicated modules:
     - `scan.py`: Universe scanning and LLM risk gating (`copilot scan`).
     - `trade.py`: Position tracking, manual orders, and closure (`status`, `positions`, `execute`, `close`, `test-alert`).
     - `backtest.py`: Historical backtesting with friction and attribution (`copilot backtest`).
     - `research.py`: Walk-forward parameter grid optimization and auto-retuning (`optimize`, `retune`).
     - `stress.py`: Macro crisis replay and instantaneous factor shocks (`copilot stress`).
     - `options.py`: Market maker gamma exposure and volatility surface (`copilot gex`).
     - `telemetry.py`: Live metrics snapshot and exporter server (`copilot metrics`).
     - `service.py`: Daemon scheduling, streaming, and bot polling (`daemon`, `listen`, `eval`).
     - `db.py`: Alembic database migration management (`copilot db upgrade`, `downgrade`, `current`, `history`).
   - 100% backward-compatible: `from agentic_trader.main import FuturesCopilot` and all subprocess CLI test invocations preserved.
4. **Rust-Accelerated Impacted Test Runner (`pytest-impacted[fast]`)**:
   - Added `pytest-impacted[fast]` (powered by Ruff's Rust parser + Rayon parallel AST discovery) to development dependencies.
   - Configured `.pre-commit-config.yaml` to run `pytest --impacted --impacted-module=agentic_trader --impacted-tests-dir=tests`, reducing pre-commit overhead while guaranteeing test coverage.

### Implementation Summary
- **Telemetry Package (`agentic_trader/telemetry/`)**: Implemented `models.py`, `collector.py`, `server.py`, and `__init__.py`.
- **Configuration (`agentic_trader/config.py`, `config/config.yaml`)**: Added `TelemetryConfig` model (`metrics_enabled`, `metrics_host`, `metrics_port`).
- **CLI Submodule Package (`agentic_trader/cli/`)**: Created modular command hierarchy and decorators in `agentic_trader/cli/`.
- **Copilot Extraction (`agentic_trader/agent/copilot.py`)**: Extracted core orchestration logic into clean copilot module.
- **Test Suite (`tests/test_telemetry.py`)**: 8 comprehensive unit tests covering gauge/counter/histogram collection, reset operations, HTTP `/metrics` and `/healthz` endpoints, and Click/subprocess CLI invocations. Total unit test suite: 148 passed tests.

---

---

## Phase 22: Cointegration & Statistical Pairs Trading Screener

### Objective
Provide institutional-grade statistical arbitrage capabilities by scanning cross-asset pairs for cointegration (Engle-Granger two-step method), estimating mean-reversion speed via Ornstein-Uhlenbeck continuous-time / AR(1) half-life modeling, and generating dynamic rolling $Z$-score entry/exit spread signals.

### Key Deliverables
1. **Engle-Granger Two-Step Cointegration Test (`agentic_trader/pairs/cointegration.py`)**:
   - Computes OLS hedge ratio $\beta$ and intercept $\alpha$: $Y_t = \beta X_t + \alpha + \epsilon_t$.
   - Performs Augmented Dickey-Fuller (ADF) unit root test on residuals $\epsilon_t$, checking for stationarity and extracting exact $p$-values and critical values (1%, 5%, 10%).
2. **Ornstein-Uhlenbeck Half-Life Modeling (`agentic_trader/pairs/cointegration.py`)**:
   - Fits mean-reverting process $\Delta \epsilon_t = \theta \epsilon_{t-1} + c + \eta_t$.
   - For mean-reverting spreads ($\theta < 0$), calculates exact half-life $T_{\text{half}} = -\frac{\ln(2)}{\theta}$ in trading bars.
   - Filters out non-stationary or unfeasibly slow-reverting pairs ($T_{\text{half}} > 60$ bars).
3. **Dynamic Rolling Spread & $Z$-Score Signal Engine**:
   - Tracks instantaneous spread $S_t = Y_t - (\beta X_t + \alpha)$ with rolling mean and rolling standard deviation over a 30-day window.
   - Computes rolling $Z$-score: $Z_t = \frac{S_t - \mu_{S, t}}{\sigma_{S, t}}$.
   - Triggers signals:
     - $Z \le -2.0$: `BUY_SPREAD` (Long Asset Y, Short Asset X).
     - $Z \ge +2.0$: `SELL_SPREAD` (Short Asset Y, Long Asset X).
     - $|Z| \le 0.5$: `EXIT_SPREAD` (Mean-reversion achieved, close spread).
     - Otherwise: `NEUTRAL`.
4. **Institutional Pairs Screener & Universe Scanning (`agentic_trader/pairs/screener.py`)**:
   - Evaluates custom candidate pairs or default institutional pairs: `SPY/QQQ`, `SPY/IWM`, `QQQ/IWM`, `GLD/SLV`, `XLE/USO`, `V/MA`, `EWA/EWC`.
   - Automatically maps micro-futures symbols (`/MES`, `/MNQ`, `/MGC`, `/MCL`) to liquid ETF proxies.
   - Supports arbitrary pairwise combination generation (`screener.generate_pairwise_combinations(["SPY", "QQQ", "IWM", "GLD"])`).
5. **Institutional Reporting & Telegram Interactive Bot Integration**:
   - `format_pairs_report()`: Institutional ASCII table displaying pair, hedge ratio $\beta$, ADF $p$-value, half-life, current spread, $Z$-score, signal, and leg actions.
   - `format_pairs_telegram()`: HTML card for mobile Telegram delivery.
   - `copilot pairs` Click CLI command supporting `--pair`, `--symbols`, `--lookback`, `--p-value`, `--z-entry`, `--z-exit`, and `--json`.
   - `/pairs` Telegram interactive command.

### Implementation Summary
- **Pairs Package (`agentic_trader/pairs/`)**: Created `models.py`, `cointegration.py`, `screener.py`, `reporting.py`, and `__init__.py`.
- **Configuration (`agentic_trader/config.py`, `config/config.yaml`)**: Added `PairsConfig` model (`p_value_threshold`, `min_half_life_bars`, `max_half_life_bars`, `lookback_days`, `z_score_lookback`, `z_entry_threshold`, `z_exit_threshold`, `default_pairs`).
- **CLI Subcommand (`agentic_trader/cli/commands/pairs.py`, `agentic_trader/cli/main.py`)**: Registered `copilot pairs` Click command.
- **Telegram Bot (`agentic_trader/notifier/telegram_bot.py`)**: Registered `/pairs` command handler and help documentation.
- **Copilot Integration (`agentic_trader/agent/copilot.py`)**: Attached `self.pairs_screener` to `FuturesCopilot`.
- **Test Suite (`tests/test_pairs.py`)**: 9 comprehensive unit tests covering synthetic cointegrated series detection, independent random walk rejection, Ornstein-Uhlenbeck half-life math, rolling Z-score calculation, screener scanning, ASCII/Telegram formatters, Click CLI execution, and Telegram bot dispatch. Total unit test suite: 157 passed tests.

---

---

## Phase 23: Regular Trading Hours (RTH) & Market Session Filtering

### Objective
Ensure trading algorithms and LLM evaluations execute exclusively during valid market sessions. Filter out low-liquidity overnight gaps, respect exchange holidays, and enforce Regular Trading Hours (RTH) for cash equities and index futures.

### Key Deliverables
1. **`MarketSessionProtocol` & Session Taxonomy (`agentic_trader/market/session.py`)**:
   - `MarketSessionType` enum: `RTH` (Regular Trading Hours), `ETH` (Extended Trading Hours / Globex), `CLOSED`, `DAILY_HALT` (17:00-18:00 ET maintenance), `WEEKEND_HALT`.
   - `MarketSessionInfo` dataclass: `symbol`, `asset_class`, `is_open`, `is_rth`, `session_type`, `current_time`, `next_open`, `next_close`, `source`.
2. **Dynamic Provider Backends**:
   - `AlpacaMarketSessionProvider`: Authoritative exchange clock via Alpaca `get_clock()` and `get_calendar()`, with in-memory TTL caching and static NYSE fallback.
   - `CMEFuturesSessionProvider`: Deterministic schedule for CME equity and commodity micro-futures (/MES, /MNQ, /MGC, /MCL).
   - `CryptoSessionProvider`: Continuous 24/7/365 trading.
   - `CompositeMarketSessionProvider`: Unified router delegating by asset class/symbol prefix.
3. **Configuration & Evaluator Integration**:
   - `SessionConfig` schema (`enforce_rth: true`, `allow_extended_hours: false`, `timezone: "America/New_York"`).
   - Wired into `RiskEvaluator.evaluate_candidate()` to reject candidates outside approved sessions.

---

## Phase 24: Dynamic Trailing Stops & Breakeven Management

### Objective
Protect profits and eliminate open downside exposure as trades progress by dynamically advancing stop losses to breakeven and trailing favorable price excursions.

### Key Deliverables
1. **Configurable Trailing Parameters (`TrailingStopConfig`)**:
   - `breakeven_trigger_r: 1.0`: Automatically advances stop to entry + buffer once trade gains +1.0R.
   - `breakeven_buffer_dollars: 5.0`: Small buffer above entry to cover execution friction and clearing fees.
   - `trail_trigger_r: 1.5`: Activates dynamic trailing once trade reaches +1.5R.
   - `trail_atr_multiple: 1.5`: Trails price by $1.5 \times \text{ATR}$.
   - `trail_step_ticks: 4`: Minimum step threshold before ratcheting stop price.
2. **Reconciliation & Copilot Integration**:
   - Embedded into `FuturesCopilot.monitor_positions()`: checks active open positions on every tick/reconciliation cycle.
   - Updates `stop_loss` in SQLite via `SignalDatabase.update_position_stop()`.
   - Dispatches rich Telegram notification (`send_trailing_stop_alert`) when a position is moved to breakeven or trailed upward.

---

## Phase 25: Production Deployment Packaging (macOS launchd & Watchdog)

### Objective
Provide institutional local production deployment for dedicated trading machines (e.g. Mac Mini desk), complete with automated watchdog supervision, heartbeat monitoring, and self-healing restart.

### Key Deliverables
1. **Unified Service Management Script (`scripts/launchd.sh`)**:
   - Subcommands: `install`, `uninstall`, `start`, `stop`, `restart`, `status`, `health`, `watchdog`, `logs`, `watchdog-logs`.
2. **Automated 60-Second Watchdog Supervision (`com.agentictrader.watchdog.plist`)**:
   - Runs periodic probe every 60 seconds.
   - Checks daemon registration and PID liveness.
   - Restarts dead or hung processes automatically via `launchctl kickstart -k`.
   - Logs timestamped events to `data/watchdog.log`.
3. **Healthcheck Integration**:
   - Seamless invocation of `copilot doctor` diagnostics.

---

## Phase 26: Quant Chandelier ATR Trailing Stop Benchmarking & Policy Formulation

### Objective
In retail trading, shifting stops to breakeven at 1.0R is taught as risk mitigation. In quantitative reality, moving stops to entry prices truncates right-tail momentum and degrades mathematical expectancy ($EV$). This phase empirically benchmarked trailing stop configurations across historical data to establish an institutional Chandelier ATR policy.

### Key Deliverables
1. **Empirical Historical Benchmark**:
   - Tested 6 policies across `/MES`, `/MNQ`, `SPY`, `QQQ`, `IWM`.
   - Confirmed retail 1.0R break-even prematurely choked winning runners (reducing take-profit hits from 10 to 6).
   - Chandelier ATR (1.5R activation, 1.5x ATR trail from high-water mark, no break-even jump) increased Profit Factor from 0.51 to 0.67 (+31%) and preserved all 10 runner take profits.
2. **Modular Configuration**:
   - Set `chandelier_atr` as default mode with `breakeven_trigger_r: null` (opt-in).

---

## Phase 27: Decoupled Presentation Layer & TradingCopilot Generalization

### Objective
Decouple string interpolation, ASCII formatting, and HTML card generation from core business logic into domain DTOs and clean formatters, and generalize `FuturesCopilot` to multi-asset `TradingCopilot`.

### Key Deliverables
1. **Domain Presentation DTOs (`agentic_trader/presentation/formatters.py`)**:
   - `PositionView`, `PositionsReport`, `PortfolioStatusReport`, `ExecutionResultView`, `PerformanceSummaryReport`.
   - `TerminalFormatter` for aligned CLI tables and `TelegramHtmlFormatter` for mobile HTML cards.
2. **Core Class Generalization**:
   - Renamed orchestration class to `TradingCopilot` with `FuturesCopilot` backward-compatible alias.

---

## Phase 28: CME Globex Holiday Calendar & Timezone Normalization

### Objective
Account for CME exchange holidays, half-day early closures, and timezone conversions to prevent false trade signals during closed market hours.

### Key Deliverables
1. **`MarketHolidayCalendar`**:
   - Supports Martin Luther King Jr. Day, Washington's Birthday, Good Friday, Memorial Day, Juneteenth, Independence Day, Labor Day, Thanksgiving (and Black Friday early close at 13:00 ET), and Christmas / New Year.
2. **Timezone Normalization**:
   - `ensure_et()` handles naive and timezone-aware datetimes with daylight saving transitions.

---

## Phase 29: Resilient Market Data Provider Cascade (`RunnableWithFallbacks`)

### Objective
Eliminate single points of failure in market data by pulling bars directly from the authenticated Alpaca Historical Bars API and cascading to Yahoo Finance with timeout and retry policies.

### Key Deliverables
1. **Generic Resilience Engine (`agentic_trader/resilience/fallback.py`)**:
   - LangChain-inspired `RunnableWithFallbacks[T, R]` with synchronous (`invoke`) and asynchronous (`ainvoke`) execution.
   - `RetryPolicy`: Configurable max retries, exponential backoff jitter, and per-attempt timeout execution.
   - Structured logging with `extra={...}` on fallback events.
2. **Provider Implementations (`agentic_trader/data/providers.py`)**:
   - `MarketDataProvider` protocol.
   - `AlpacaDataProvider`: Fast, rate-limit exempt bars for equities, ETFs, and crypto. Routes CME futures to upstream fallbacks.
   - `YFinanceDataProvider`: Downloader for CME futures and fallback for equities.
   - `CompositeMarketDataProvider`: Seamless fallback orchestration.

---

## Phase 30: Broker-Side Trailing Stop Synchronization

### Objective
Ensure that when internal trailing stops ratchet higher, the resting bracket stop-loss orders on the exchange brokers are amended in real time with graceful degradation for offline brokers.

### Key Deliverables
1. **Broker Interface Extension (`agentic_trader/broker/base.py`)**:
   - `supports_order_modification: bool` property and `modify_order_stop()` hook.
2. **Broker Implementations**:
   - `AlpacaBroker`: Traverses order legs and invokes `replace_order_by_id` with new stop price.
   - `TradovateBroker`: Amends resting stops via `POST /order/modifyorder` with graceful offline fallback.
   - `PaperBroker`: Simulates resting stop replacement in memory.
3. **TradingCopilot Integration**:
   - Trailing stop evaluation ratchets broker stops simultaneously with SQLite records and alerts Telegram.

---

## Phase 31: Institutional Emergency Kill Switch & Telegram Autocomplete

### Objective
Provide institutional quant-grade emergency risk controls (SEC Rule 15c3-5, CFTC 1.73, MiFID II RTS 6 compliant) to immediately withdraw resting orders, liquidate active risk exposure at market, engage a persistent trading halt, and offer native client-side slash command autocomplete in Telegram.

### Key Deliverables
1. **Four-Tier Kill Switch Waterfall**:
   - **Ingress Cancellation**: `cancel_all_orders()` method on `BaseBroker`, implemented across `PaperBroker`, `AlpacaBroker` (via `cancel_orders()`), `TradovateBroker` (querying `/order/list` and cancelling working orders via `/order/cancelorder`), and `RedundantBroker`.
   - **Egress Liquidation**: Rapid market closure of all open positions in SQLite via `broker.close_position(exit_reason=ExitReason.EMERGENCY_EXIT)` with realized P&L calculation.
   - **Persistent Circuit Breaker**: State persistence in new `system_state` table (`Alembic revision 002_system_state`), gating automated scans and signal execution until explicitly resumed.
   - **Telemetry & High-Visibility Alerting**: Dedicated Prometheus metrics (`copilot_kill_switch_triggered_total`, `copilot_trading_halted`), rich HTML notification cards, and ASCII terminal reports.
2. **Interactive Safety in Telegram**:
   - Two-step confirmation for `/panic` command (`[ 🔴 CONFIRM EMERGENCY LIQUIDATE & HALT ]` and `[ ❌ Cancel ]`) to prevent accidental taps, plus `/panic confirm` for immediate emergency action.
   - `/resume` command and `copilot resume` CLI command to verify unhalt and restore trading operations.
3. **Telegram Slash Command Autocomplete (`setMyCommands`)**:
   - Automatic registration of command palette with descriptions via Telegram Bot API `set_my_commands` upon application initialization.
4. **Calendar Refinements**:
   - Added conditional July 3rd early closes (when July 4th falls on Thursday or Friday) for equities and equity index futures.

---

## Next Horizon: Strategic Initiatives (Phases 32+)

| Priority | Target Area | Status | Focus |
|---|---|---|---|
| **Phase 32** | Multi-Signal Portfolio Diffing & Transition Engine | **Planned** | Transition between optimal position allocations across long sessions without over-allocation |
| **Phase 33** | Level-2 / Order Book Microstructure Flow Streaming | **Planned** | CME top-of-book (BBO) and DOM queue imbalance streaming via Tradovate WebSocket |
| **Phase 34** | Interactive Brokers (IBKR) Native Driver | **Planned** | Direct DMA execution via `ib_insync` or IBKR Client Portal REST API |
| **Phase 35** | Cloud Infrastructure & AWS Container Deployment | **Planned** | Containerized deployment on AWS ECS/Fargate or EC2 with Terraform/Ansible automation |
