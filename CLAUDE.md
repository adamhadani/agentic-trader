# CLAUDE.md

Guidelines and reference commands for AI coding assistants working in the `agentic-trader` repository.

---

## 0. Running Checkout & Development Handoff

- This checkout owns the launchd Alpaca paper daemon, Telegram poller and PostgreSQL state. Never start a second daemon or poller alongside it.
- Read [development notes](docs/development-notes.md), [operations](docs/production.md), and the [September 15 incident](docs/incident-2026-09-15.md).
- Unit tests are isolated by default: explicit fixture config, temporary SQLite, stripped credentials, blocked sockets/libcurl and DB target guards. PostgreSQL integration is opt-in on a disposable `test_` database.
- `scan --dry-run` uses an empty temporary portfolio/PaperBroker and disables Telegram and monitoring. Market-data and optional LLM calls remain possible. `doctor` performs active diagnostics and migrations.
- Keep credentials, raw logs, snapshots, databases and generated runtime data out of commits. Verify deployment separately from source tests; daemon startup audit includes PID, run ID and Git revision.

## 1. Project Overview

The **Agentic Trader** is a multi-asset trading system designed around a $100k cash portfolio (portable alpha). It scans configured micro futures, ETFs, and equities, evaluates candidates using deterministic risk rules and optional LLM reasoning, and stages `PENDING` signals with Telegram alert cards. Operators execute signals through CLI or Telegram; scheduled scans do not automatically place entry orders. Broker adapters cover local Paper simulation, Tradovate CME micro futures, and Alpaca equities/crypto. `config/config.yaml` defines the current universe; broker capability and scan eligibility are separate concerns.

---

## 2. Common Commands & Operational Lifecycle

### Virtual Environment & Dependencies

- Python >= 3.14 managed exclusively with `uv`.
- Sync dependencies: `uv sync`
- Install pre-commit git hooks: `uv run pre-commit install`
- Apply database migrations: `uv run copilot db upgrade head`

### Command Taxonomy by Lifecycle Stage

#### A. Production Steady-State (What runs 24/7)

- `uv run copilot daemon`: Core continuous process running APScheduler (4-hour unrestricted-timeframe scans, session-gated 15-minute scans, 1-minute position monitoring), Alpaca/Tradovate WebSocket trade streams, Telegram bot poller, and Prometheus metrics server (`:9108`). Interval jobs start immediately and are relative to startup, not aligned to candle closes. Do not start a second daemon alongside launchd.
- `docker compose up -d`: Runs multi-container stack (`postgres:18.6-alpine` + `trading-copilot`) with healthchecks and persistent volumes.
- `uv run copilot listen`: Runs Telegram bot listener only in isolation; do not run it concurrently with the daemon using the same bot token.

#### B. Day-to-Day Operator Desk Commands

- `uv run copilot status`: Portfolio cash base, open notional exposure, leverage, and macro calendar.
- `uv run copilot positions`: Tracked positions, live quotes, stop-loss / take-profit prices, and unrealized P&L.
- `uv run copilot explain-macro`: Educational tutorial & breakdown of live macro indicators via LLM.
- `uv run copilot scan`: On-demand quantitative market scan (`--dry-run`, `--no-llm`, `--strategy`, `--strategy-mode`, `--asset-class`, `--symbols`, `--timeframe`). A dry run uses isolated temporary storage and simulated execution, with no Telegram or monitoring.
- `uv run copilot execute <signal_id>`: Manually authorize and submit an approved signal to broker.
- `uv run copilot close <signal_id> --price <exit_price>`: Request broker closure. Alpaca accounting waits for the confirmed broker fill; the supplied price is only for simulation/manual adapters.
- `uv run copilot panic [--confirm]`: Emergency kill switch: cancel resting orders, market liquidate active positions, halt trading.
- `uv run copilot resume`: Clear emergency trading halt and resume autonomous scanning/execution.
- `uv run copilot gex [symbol]`: Market maker dealer gamma exposure, pinning walls, and gamma flip.
- `uv run copilot pairs`: Cross-asset cointegration, mean-reversion half-life, and rolling spread Z-scores.
- `uv run copilot metrics`: Dump Prometheus exposition snapshot or launch standalone server (`--serve`).
- `uv run copilot test-alert`: Preview a non-actionable `[TEST]` message locally. `--send` requires dedicated `TELEGRAM_TEST_BOT_TOKEN` and `TELEGRAM_TEST_CHAT_ID`, both different from production.

#### C. Quantitative Research, Alpha Mining & Calibration (Offline)

- `uv run copilot backtest`: Offline backtest with friction, Cash-Plus attribution, & Monte Carlo (`--symbols`, `--strategy`, `--lookback`).
- `uv run copilot optimize`: Parameter grid search and rolling walk-forward validation (`--walk-forward`, `--splits`).
- `uv run copilot retune`: Automated parameter recalibration with Walk-Forward Efficiency (WFE) filtering.
- `uv run copilot stress`: Crisis replay (2008 GFC, 2020 COVID, 2022 Inflation) & factor shocks (`--scenario`).
- `uv run copilot eval`: Benchmark LLM decision prompts against risk invariants (Promptfoo).
- `uv run copilot alpha catalog`: List institutional formulaic alphas (WorldQuant 101, factor library).
- `uv run copilot alpha list`: List production promoted alphas, weights, and eligible universe.
- `uv run copilot alpha mine`: Formulaic alpha mining with DSR & orthogonalization (`--symbols`, `--iterations`).
- `uv run copilot alpha inspect <id>`: Quantitative tearsheet evaluation across historical bars (`--interval 1d`).
- `uv run copilot alpha promote <id>`: Promote an alpha with allocation weight and eligible universe.
- `uv run copilot alpha demote <id>`: Retire active alpha with zombie protection (`--liquidate-positions`).
- `ConvexAlphaPortfolioOptimizer` in `research/alpha/optimizer.py`: Python library for constrained SLSQP portfolio weights. There is currently no registered `copilot alpha optimize` command or live sizing integration.
- `uv run copilot alpha test <expr>`: Validate and backtest an ad-hoc formulaic DSL expression.

#### D. Database Schema Migrations & Administration (`copilot db`)

- `uv run copilot db upgrade head`: Apply pending migrations.
- `uv run copilot db current`: View current revision.
- `uv run copilot db history`: View migration history.
- `uv run copilot db downgrade -1`: Rollback last migration.
- `uv run copilot db audit [--signal-id N] [--limit N]`: Read persistent operational evidence.
- `uv run copilot db clear [--yes]`: Destructively purge signal history; use reviewed quarantine for contamination incidents.

---

## 3. Testing, Linting & Quality Control

- Run `uv run pre-commit run --all-files` before committing. Hooks include auto-formatting, locking, mypy and impacted tests.
- Full isolated suite: `uv run pytest`; targeted: `uv run pytest tests/broker tests/storage tests/config`.
- PostgreSQL integration: provision an empty `test_` database, set `TEST_POSTGRES_URL`, then `uv run pytest tests/integration --run-postgres`. Fixtures migrate/downgrade only that target. Default test runs skip this group.
- Loopback HTTP tests need permission to bind localhost; they do not permit external hosts. Never disable socket/DB isolation to make a test pass.
- Inject a DB and explicit config before constructing `TradingCopilot`. Bare `AppConfig()` has no implicit database. Native psycopg2 and libcurl have separate guards because Python socket blocking alone is insufficient.
- Shared state/mode/audit vocabulary belongs in `constants.py` enums; operator policy belongs in validated config. Broker protocol states use SDK enums. Leave frozen migrations and external payload keys stable.
- Config loading is explicit and does not mutate process environment. `COPILOT_CONFIG`, `COPILOT_ENV`, `COPILOT_ENV_FILE`, `DB_PATH` and `DATABASE_URL` control the boundary; see development notes for precedence.

## 4. Architecture & Key Directory Layout

- `agentic_trader/constants.py`: Centralized domain constants, symbols, multipliers, tick sizes, HTTP timeouts, URLs, and status strings.
- `agentic_trader/cli/`: Modular Click CLI package hierarchy (`main.py` and `commands/` for `scan`, `trade`, `backtest`, `research`, `alpha`, `stress`, `options`, `pairs`, `telemetry`, `service`, `db`).
- `agentic_trader/agent/`:
  - `copilot.py`: `TradingCopilot` orchestration engine; inject DB/broker/data/notifier and import the canonical class directly.
  - `copilot_graph.py` & `copilot_tools.py`: LangGraph stateful conversational agent with ReAct tool calling.
  - `macro.py`: US Treasury yield curve (3M-30Y), public FRED OAS/Breakevens (`FredDataClient`), compound macro stress index, and morning briefing.
  - `macro_explainer.py`: LLM educational macro explanation with deterministic fallback, shared by CLI and Telegram.
  - `evaluator.py`: LiteLLM trade evaluator and risk invariant gating.
  - `calendar.py`: Economic calendar with macro lockout detection (`ForexFactoryCalendar`).
  - `regime.py`: Real-time market regime classifier (VIX, 10Y Treasury yield, Dollar Index).
- `agentic_trader/presentation/`:
  - `formatters.py`: Presentation DTOs (`PositionView`, `PositionsReport`, `PortfolioStatusReport`, `ExecutionResultView`, `PerformanceSummaryReport`, `PanicReportView`) and terminal/Telegram HTML formatters. Macro report models live in `agent/macro.py`; alpha inspection formats an `AlphaCandidate` from `research/alpha/models.py`.
- `agentic_trader/market/`:
  - `session.py`: Market session & trading hours protocol (`MarketSessionProtocol`), third-party exchange calendar delegation (`AlpacaCalendarProvider`, `FinnhubCalendarProvider`, `DeterministicCalendarProvider`, `CompositeMarketCalendar`) with `RunnableWithFallbacks`, CME Globex holiday calendar (`MarketHolidayCalendar`), timezone normalization (`ensure_et`), and composite routing (`CompositeMarketSessionProvider`).
- `agentic_trader/resilience/`:
  - `fallback.py`: Generic LangChain-inspired resilience engine (`RunnableWithFallbacks`, `RetryPolicy`, `AllFallbacksExhaustedError`) with timeout and exponential backoff retry.
- `agentic_trader/data/`:
  - `providers.py`: Provider protocol (`MarketDataProvider`), `AlpacaDataProvider` (stock/crypto), `YFinanceDataProvider` (futures/fallback), and `CompositeMarketDataProvider`.
  - `market_data.py`: Technical indicator calculations and market data fetcher delegating to `CompositeMarketDataProvider`.
- `agentic_trader/broker/`:
  - `base.py`: Standardized broker interface (`BaseBroker`, `OrderRequest`, `OrderResult`, `supports_order_modification`, `modify_order_stop`, `cancel_all_orders`).
  - `paper.py`: Simulated paper execution with dynamic contract multipliers and resting bracket stop modification.
  - `tradovate.py`: Headless REST API execution with native server-side OCO brackets, `/order/modifyorder`, and `/order/cancelorder`.
  - `alpaca.py`: Official `alpaca-py` TradingClient SDK integration with bracket orders, `TradingStream` WebSocket, resting stop leg replacement, mass cancellation (`cancel_orders`), and robust reconciliation filtering out entry orders.
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
- `agentic_trader/screeners/`: Multi-strategy framework (`StrategyRegistry`, `ConflictResolver`, `TrendPullbackStrategy`, `SqueezeBreakoutStrategy`, `FormulaicAlphaStrategy`). Loads promoted alphas from `config/promoted_alphas.yaml` when constructing the registry; CLI/YAML edits do not automatically refresh a running registry. Conversational promotion/demotion also updates that process's registry.
- `agentic_trader/backtest/`: Backtest engine, transaction friction, Cash-Plus attribution, Monte Carlo simulation, crisis replay, and dynamic Chandelier ATR trailing stop ratcheting.
- `agentic_trader/research/`: VectorBT parameter optimizer, rolling walk-forward cross-validation, and `alpha/` package (`dsl.py`, `operators.py`, `catalog.py`, `metrics.py`, `miner.py`, `promotion.py`, `orthogonalization.py`, `optimizer.py`) for formulaic alpha mining, Gram-Schmidt signal orthogonalization, convex portfolio optimization (QP/SLSQP), and DSR overfitting controls.
- `agentic_trader/storage/`: SQLAlchemy async ORM (`SignalRecord`, `SystemStateRecord`, `AuditEventRecord`), PostgreSQL via `asyncpg`, and explicitly selected SQLite. PostgreSQL is the current default; SQLite is not automatic failover. `SignalDatabase` construction applies Alembic migrations synchronously. Compose specifies PostgreSQL 18.6; the installed local server version must be checked separately.
- `agentic_trader/notifier/telegram_bot.py`: Interactive Telegram bot with command handlers (`/status`, `/macro`, `/alphas`, etc.), client-side slash autocomplete (`set_my_commands`), dedicated chat menu button (`MenuButtonCommands`), and execution buttons.
- `tests/`: Test hierarchy mirroring application packages, with shared fixtures in `tests/conftest.py`; with explicit network and database isolation described above.
- `docs/`: GitHub Pages Jekyll documentation site (`_config.yml`, `index.md`, `production.md`, `cli-reference.md`, `strategies.md`, `roadmap.md`).

---

## 5. Hard Domain Invariants & Business Rules

Preserve these intended safeguards when changing logic. They are design requirements, not proof that every path currently enforces them; inspect the implementation gaps in the development notes.
1. **Scope**: Use the configured universe and broker capabilities. Current YAML includes four micro futures, `SPY`, `QQQ`, `IWM`, `GLD`, `TLT`, `AAPL`, `MSFT`, `NVDA`, and `AMD`; crypto support exists but no crypto instruments are currently configured.
2. **Sizing**: Static mode defaults to one micro futures contract and dollar-risk equity sizing. Volatility-targeted, fractional-Kelly, and operator-selected quantities also exist; preserve risk/notional caps rather than assuming every execution is one contract.
3. **Notional Exposure Ceiling**: Default $60,000 total active open notional exposure (0.6x on $100k cash), four concurrent positions, and per-class defaults of $40k equities, $40k futures, and $20k crypto. Read effective configuration before relying on these defaults.
4. **Risk-to-Reward Ratio**: Strictly $R:R \ge 2.0$.
5. **Structural Stop Distance**: Minimum stop distance $\ge 1.5 \times \text{ATR}(14)$.
6. **Macro Lockout**: NO entry alerts within $[-60\text{m}, +30\text{m}]$ of Tier-1 releases (CPI, PPI, FOMC, NFP).
7. **Signal Deduplication**: Key is contract + strategy across eligible nonquarantined statuses in the current environment/account mode. Default window is 12 hours, capped at 2 hours for `15m`/`15min`/`fifteen_minute` candidates and 4 hours for `1h`/`hourly` candidates. Timeframe is not a database column or part of the key.
8. **Execution Safety**: Execution buttons must route through `broker.submit_entry_order()`. If rejected, status becomes `FAILED` without locking notional capacity.
9. **Single Steady-State Daemon**: One `copilot daemon` owns the bot poller, trade stream, and metrics port. Launchd additionally schedules separate watchdog and alpha-miner jobs.
10. **Emergency Kill Switch & Halt Integrity**: When halted (`trading_halted=true` in `system_state`), all market scans and order submissions are strictly blocked across CLI, scheduled daemon jobs, and Telegram. Resumption requires explicit `/resume` or `copilot resume`.
11. **Database Schema Governance**: All schema migrations must be defined exclusively via Alembic revisions (`run_migrations_head`). Never reintroduce hard-coded table creation or ALTER TABLE shims.
12. **Reconciliation & Fill Invariants**: All broker backends (Alpaca, Tradovate, Paper, and future drivers like IBKR) and the copilot reconciliation engine must adhere to the 4 fill invariants:
    - **Entry Order ID Exclusion**: Order fills matching `pos["broker_order_id"]` are entry confirmations; they must never close the position or emit exit alerts.
    - **Directional Opposing Side Rule**: Exit fills MUST strictly oppose the position direction (Sell closes Long, Buy closes Short). Fills on the same side as entry are entries/accumulations and must be ignored for exit reconciliation.
    - **Order ID Distinctness**: Bracket legs (TP/SL) and manual exits generate distinct order IDs from the entry order ID.
    - **Defense-in-Depth**: Stream updates wake the same exact-parent REST reconciliation path as polling. Full fill, chronological order, quantity, side and symbol checks are required. Conditional DB closure gates alerts; partial fills remain tracked.

13. **Brokerage as valuation authority**: Both CLI and Telegram positions use the same broker snapshot. Preserve source/time; show failures or mismatches, never fabricate zero P&L. Realized Alpaca performance includes confirmed closed fills with entry/exit IDs and actual average prices; it is all recorded closed-trade history before fees, not account-day return.
14. **Audit and provenance**: Head revision is `003_audit_provenance`; every signal has environment/account mode and run identity. Quarantine preserves original values and excludes confirmed test rows from risk, deduplication, positions and performance.

15. **Async boundaries**: Use `asyncio.to_thread` for blocking SDK/provider calls and CPU-heavy research from async handlers. Keep related scans serialized; review shared state before adding concurrency. Telegram retries belong in `notifier/transport.py`, never around a trade handler. Preserve request/update audit IDs, poll freshness metrics, and `telemetry/event_loop.py` stall monitoring.

16. **Operator language and data quality**: Use `APP_DISPLAY_NAME` for product titles. Describe scans as suggestions requiring approval and Alpaca closes as fill-confirmed. Render risk policy from config. GEX normalizes provider nulls, surfaces quality notes, and must never invent a spot quote; it remains a research estimate.

17. **One macro command**: `/macro` includes volatility classification and combined
    configured filters; `/regime` and its chat tool are removed. Do not add an alias.
    `/explain_macro` is educational. Render feed dates, expose missing enrichment,
    and never invent baseline observations. Conversational positions/status must
    delegate to the shared broker-backed report providers.

18. **Scheduler readiness**: initialize async dependencies before registering
    immediate jobs. Use the configured misfire grace, coalescing and one instance
    per job; verify initial scan/reconciliation completion in deployment logs.
