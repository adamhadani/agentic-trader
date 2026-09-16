# Agentic Trader
### Autonomous Multi-Asset Quantitative Trading System

[![Python 3.14](https://img.shields.io/badge/python-3.14-blue.svg)](https://www.python.org/)
[![Checked with mypy](https://img.shields.io/badge/mypy-checked-blue)](http://mypy-lang.org/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)
[![Documentation](https://img.shields.io/badge/docs-local%20markdown-blue)](docs/)

The **Agentic Trader** is an algorithmic trading system designed around a **"Cash-Plus" (portable alpha)** portfolio architecture ($100,000 baseline cash generating risk-free Treasury yield). The system continuously screens multi-asset markets, validates setups through an LLM agent with macro calendar awareness, and executes bracket orders across Tradovate (CME micro futures) and Alpaca (equities, ETFs, and crypto) with real-time Telegram oversight and Prometheus observability.

---

## 📚 Documentation & Reference Manuals

- [**Production Operations Guide**](docs/production.md): **Single Source of Truth** for 24/7 steady-state deployment, Docker Compose, Prometheus metrics, and operator runbooks.
- [**CLI Command Reference**](docs/cli-reference.md): Exhaustive guide to all Click CLI subcommands, flags, and outputs.
- [**Quantitative Strategies & Models**](docs/strategies.md): Mathematical formulations for Trend-Pullback, Squeeze Breakout, Options GEX, and Pairs Trading.
- [**Development Roadmap**](docs/roadmap.md): Complete chronological record of completed phases (Phases 1 through 45) and future milestones.

### Compiling & Viewing Documentation Locally

Since the GitHub repository is private, you can preview the complete formatted documentation site locally:

**Method 1: Local Ruby / Bundler**
```bash
cd docs
bundle install
bundle exec jekyll serve
# Open http://localhost:4000/agentic-trader/ in your browser
```

**Method 2: Docker (Zero Ruby Installation Required)**
```bash
docker run --rm -v "$PWD/docs:/srv/jekyll" -p 4000:4000 jekyll/jekyll:latest jekyll serve
# Open http://localhost:4000/agentic-trader/ in your browser
```

**Method 3: Native Markdown**
All documentation pages in [`docs/`](docs/) are formatted in standard GitHub Flavored Markdown and can be read directly in your IDE or the private GitHub web UI.

---


## Development and operational safety

The local desk uses **Alpaca paper trading with PostgreSQL** under macOS launchd.
`EXECUTION_MODE=paper` means the in-process simulator; use `EXECUTION_MODE=alpaca`
with `ALPACA_PAPER=true` for the brokerage paper account. Run one daemon/poller.

- [Architecture review and priorities](docs/architecture-review.md)
- [Development map and test isolation](docs/development-notes.md)
- [September 15 incident and fixes](docs/incident-2026-09-15.md)
- `uv run pytest`: isolated fixtures, no production credentials/database/network.
- PG tests: `TEST_POSTGRES_URL=.../test_trader uv run pytest tests/integration --run-postgres`.
- `uv run pre-commit run --all-files`: required before committing.
- `uv run copilot scan --dry-run --no-llm`: empty temporary portfolio, no Telegram,
  broker execution or monitoring; market-data access still occurs.
- `uv run copilot db audit --limit 20`: fill, valuation, notification and startup evidence.

Positions use Alpaca's account valuation. `/perf` shows broker open-position P&L
and separately labels confirmed closed-trade history before fees. Quarantined test
rows and unverified Alpaca closes are excluded. Source changes require a daemon
restart; startup audit records the deployed revision. Telegram has shared transport retries,
persistent command/request/poll audits, poll freshness metrics, and event-loop stall detection.
Run `uv run python scripts/verify_runtime.py` for a read-only paper-desk smoke check.

## 1. Core Risk & Portfolio Constraints

1. **Instrument Scope:** Micro futures (`/MES`, `/MNQ`, `/MGC`, `/MCL`) and liquid ETF/equity proxies (`SPY`, `QQQ`, `IWM`, `GLD`, `USO`).
2. **Fixed Sizing:** Static mode defaults to one micro contract; equity sizing and operator tiers obey configured risk caps. Total active open notional exposure across all concurrent positions must not exceed **$60,000** (0.6x effective leverage on $100k cash).
3. **Reward-to-Risk (R:R):** Strictly $\ge 2.0$. Stop distance must be $\ge 1.5 \times \text{ATR}(14)$ to prevent noise stop-outs.
4. **Macro Event Lockout:** Zero entry alerts permitted within **$[-60\text{m}, +30\text{m}]$** of scheduled Tier-1 economic releases (CPI, PPI, FOMC, Non-Farm Payrolls).
5. **Deduplication Rule:** Zero duplicate signals for the same contract + strategy within 12 hours.

---

## 2. Production Steady-State: What Runs 24/7

In production, **only one continuous background process runs**:

```bash
copilot daemon
```

### What `copilot daemon` Executes Under Its Unified Event Loop:
1. **APScheduler Scan Job**: Scans multi-asset universe every 4 hours relative to startup, plus session-gated 15-minute intraday scans.
2. **Position Monitor & Reconciler**: Polls active broker positions every minute, tracking stop-loss and take-profit triggers against live quotes.
3. **Sub-Second WebSocket Streams**:
   - **Alpaca `TradingStream`**: Sub-second synchronization for bracket order fills, stops, targets, and cancellations.
   - **Tradovate WebSocket**: Account, position, and CME order state synchronization.
4. **Two-Way Telegram Interactive Bot**: Continuous async polling listener processing operator commands (`/status`, `/positions`, `/perf`, `/macro`, `/gex`, `/pairs`, `/scan`, `/close`, `/flatten`) and inline action buttons (`[ 🚀 Execute ]` / `[ ❌ Dismiss ]`).
5. **Native Prometheus Exporter**: Lightweight async HTTP server running on `0.0.0.0:9108` serving `GET /metrics` and container health probe at `GET /healthz`.
6. **Dynamic Trailing Stop & Broker Sync**: Evaluates active positions for risk-distance trailing stops (the live implementation is not yet a true Chandelier ATR high-water mark calculation) and amends resting bracket stop orders directly on exchange brokers (Alpaca and Tradovate) only persisting local ratchets after broker success. Alpaca additionally verifies the working replacement price.
7. **Resilient Multi-Tier Market Data**: Dual-feed market data engine (`RunnableWithFallbacks`) querying Alpaca historical bars with automatic failover to Yahoo Finance.
8. **Institutional Emergency Kill Switch**: Persistent database halt state (`system_state`) with order cancellation and fill-confirmed position liquidation across all active brokers via `/panic` or `copilot panic`.
9. **Native Telegram Command Autocomplete**: On startup, synchronizes commands with Telegram servers via `set_my_commands` to render interactive autocomplete menus in operator chat clients.
10. **Resilient Third-Party Market Calendar Delegation**: Multi-tier calendar engine (`RunnableWithFallbacks`) querying authoritative exchange calendars from Alpaca (`GET /v2/calendar`) and Finnhub (`/stock/market-holiday`) with fallback to deterministic exchange calculation, synchronizing cash equity and CME index futures sessions.
11. **Alpaca Execution & Multi-Strategy Framework**: Seamless paper-to-live execution on Alpaca utilizing liquid ETF proxies (`SPY`, `QQQ`, `IWM`, `GLD`, `USO`) with exchange-held server-side bracket orders, plus an institutional Multi-Strategy framework supporting switchable `single` vs. `parallel` execution modes, dynamic risk budgeting, and signal conflict resolution (netting / conviction policies).

---

## 3. Production Deployment Methods

### Option A: Docker Compose (Recommended)
Docker Compose provisions a multi-container stack with a high-throughput **PostgreSQL 18.6** database container (`postgres:18.6-alpine`), automated service orchestration, persistent data volumes, and Prometheus metrics port mapping (`:9108`):

```bash
# 1. Launch PostgreSQL 18.6 & Copilot daemon in detached mode
docker compose up -d

# 2. Inspect container status and health
docker compose ps

# 3. View live logs
docker compose logs -f trading-copilot

# 4. Apply Alembic migrations inside container
docker compose exec copilot copilot db upgrade head
```

### Option B: macOS `launchd` (Local Mac Mini Desk)
For dedicated local Mac machines, `launchd` keeps the daemon running across reboots with automated 60s watchdog supervision:
```bash
./scripts/launchd.sh install       # Installs copilot daemon, watchdog, and scheduled alpha miner
./scripts/launchd.sh status        # Check daemon, watchdog, and alpha miner registration/PID status
./scripts/launchd.sh health        # Run copilot doctor diagnostic health check
./scripts/launchd.sh restart       # Gracefully restart copilot daemon
./scripts/launchd.sh run-miner     # Manually trigger offline alpha mining run
./scripts/launchd.sh logs          # Tail data/copilot.log in real time
./scripts/launchd.sh watchdog-logs # Tail data/watchdog.log in real time
./scripts/launchd.sh miner-logs    # Tail data/alphaminer.log in real time
./scripts/launchd.sh stop          # Temporarily pause service
./scripts/launchd.sh uninstall     # Unload and remove daemon, watchdog, and miner
```

---

## 4. Complete CLI Command Reference by Lifecycle

The `copilot` CLI separates commands into distinct operational lifecycles:

### A. Production Daemon & Services
```bash
uv run copilot daemon              # Run 24/7 continuous trading daemon (Scheduler, WS streams, Bot, Metrics)
uv run copilot listen              # Run Telegram bot listener only in isolation (without scheduled scans)
```

### B. Day-to-Day Desk Operations
```bash
uv run copilot doctor              # Run pre-flight diagnostic probes across DB, Telegram, Alpaca, Finnhub, LLM
uv run copilot status              # View cash base, active notional exposure, leverage, and macro events
uv run copilot positions           # View active tracked positions, stops, targets, and unrealized P&L
uv run copilot explain-macro       # Educational tutorial & breakdown of live macro indicators via LLM
uv run copilot scan                # Trigger on-demand market scan (--dry-run, --strategy, --strategy-mode, --asset-class)
uv run copilot execute <id> [--qty <N>] # Authorize signal with optional custom tiered quantity override
uv run copilot close <id>          # Bracket-aware close; accounting waits for actual broker fills
uv run copilot close <id> --dry-run # Read-only position preview
uv run copilot flatten --dry-run    # Preview all current broker positions
uv run copilot flatten --confirm    # Close positions without changing the trading halt
uv run copilot panic [--confirm]    # Emergency kill switch: cancel all resting orders, liquidate positions, halt trading
uv run copilot resume               # Clear emergency trading halt and resume autonomous trading operations
uv run copilot gex [symbol]        # View options dealer gamma exposure, call/put walls, and gamma flip
uv run copilot pairs               # Screen cross-asset pairs for cointegration and rolling spread Z-scores
uv run copilot metrics             # Prometheus exposition (:9108/metrics) & JSON healthcheck (:9108/healthcheck)
uv run copilot test-alert          # Preview a non-actionable [TEST] notification locally
```

### C. Quantitative Research, Alpha Mining & Calibration (Offline)
```bash
uv run copilot backtest            # Historical backtest with friction, Cash-Plus attribution, & Monte Carlo
uv run copilot optimize            # Parameter grid search & rolling walk-forward validation (--walk-forward)
uv run copilot retune              # Automated parameter recalibration with Walk-Forward Efficiency (WFE) filtering
uv run copilot stress              # Crisis replay (2008 GFC, 2020 COVID, 2022 Inflation) & factor shocks
uv run copilot eval                # Benchmark LLM decision prompts against risk invariants (Promptfoo)

# Formulaic Alpha Mining & Expression DSL Suite (copilot alpha)
uv run copilot alpha catalog       # Display pre-cataloged institutional alphas (WorldQuant 101, etc.)
uv run copilot alpha list          # Display production promoted alphas, weights, and eligible universe
uv run copilot alpha mine          # Mine & discover formulaic alphas with DSR & orthogonalization (--symbols)
uv run copilot alpha inspect <id>  # Display quantitative tearsheet (OOS Sharpe, DSR, Rank IC, Win Rate)
uv run copilot alpha promote <id>  # Promote an alpha into production desk (--allocation 0.15 --symbols NVDA,AMD)
uv run copilot alpha demote <id>   # Retire active alpha with zombie protection (--liquidate-positions)
# ConvexAlphaPortfolioOptimizer is a Python library; no alpha optimize CLI or live allocation integration exists.
uv run copilot alpha test <expr>   # Validate and backtest an ad-hoc formulaic DSL expression
```

### D. Database Migrations & Administration (`copilot db`)
```bash
uv run copilot db upgrade head     # Apply pending Alembic database schema migrations
uv run copilot db current          # View current schema revision
uv run copilot db history          # View migration history
uv run copilot db downgrade -1     # Roll back last migration
uv run copilot db clear [--yes]    # Purge historical test signals and reset sequence (schema preserved)
```

Global database flags (`--db-name <name>`, `--db-path <path>`) or the `DB_NAME` / `DB_PATH` environment variables can be passed to redirect execution to isolated database environments without modifying production data.

---

## 5. Interactive Telegram Commands

When the daemon is running, operators can query and command the trading desk directly from Telegram:

| Command | Description | Example |
|---|---|---|
| `/status` | View cash base, open notional exposure, leverage, and macro events | `/status` |
| `/positions` | Broker positions, actual cost basis and broker unrealized P&L | `/positions` |
| `/perf` | Confirmed closed-fill P&L and current broker unrealized P&L | `/perf` |
| `/macro` | VIX regime, Treasury curve, credit, inflation and combined trading filters | `/macro` |
| `/explain_macro` | View educational tutorial & breakdown of live macro indicators with LLM context | `/explain_macro` |
| `/alphas` | View production promoted formulaic alphas, weights, and tearsheets | `/alphas` |
| `/gex [sym]` | Yahoo option-chain gamma estimates, quality notes, walls and gamma flip | `/gex SPY` |
| `/pairs` | View cross-asset cointegration, mean-reversion half-life, and Z-scores | `/pairs` |
| `/backtest [sym] [lookback]` | Trigger on-demand offline backtest simulation from mobile | `/backtest SPY 1y` |
| `/scan` | Trigger an immediate quantitative scan across the universe | `/scan` |
| `/close <id> [price]` | Request broker closure; accounting waits for the confirmed fill | `/close 3` |
| `/flatten [confirm\|dry-run]` | Preview by default; close current broker positions without changing the halt | `/flatten` |
| `/panic [confirm]` | Emergency kill switch: cancel orders, market liquidate, halt trading | `/panic` |
| `/resume` | Clear emergency trading halt and resume scanning and operator-approved execution | `/resume` |
| `/help` | Display commands and the configured trading workflow | `/help` |

`/macro` replaces `/regime`: it includes the same volatility filter plus the richer
macro indicators, combined breakout policy, risk multiplier and configured minimum
R:R. `/explain_macro` remains an educational explanation. Macro feeds are latest
published observations, not synchronized live ticks; FRED dates are shown. Missing
values are never replaced with invented yields or a normal VIX baseline. Telegram
backtests default to `backtest.lookback`; the research symbol default is `SPY`.

> **Interactive Autocomplete & Menu Button**: On startup, the Telegram bot registers slash commands via `set_my_commands` and configures the native chat menu button via `set_chat_menu_button(MenuButtonCommands())` across default, private, and chat-specific scopes. Modern Telegram mobile, desktop, and web clients display a dedicated `[Menu]` / `[/]` button with interactive autocomplete for instant command discovery.

---

## 6. System Prerequisites & Setup

On macOS:
```bash
# Core package manager, Python runner, and database (Python >= 3.14)
brew install uv hadolint postgresql@18 sqlite node

# Clone and sync dependencies
uv sync

# Copy environment template
cp .envrc.example .envrc
# Fill in your API keys (Telegram, LLM, Tradovate, Alpaca, Finnhub)
# Optional: Set DATABASE_URL=postgresql+asyncpg://localhost:5432/agentic_trader for PostgreSQL 18.6
direnv allow  # or source .envrc

# Run database migrations
uv run copilot db upgrade head

# Install pre-commit git hooks
uv run pre-commit install
```

---

## 7. Quality Assurance & Testing

The repository enforces 100% test passing and strict linting via `pre-commit` and `pytest-impacted[fast]`. Function-scoped autouse fixtures in `tests/conftest.py` select temporary SQLite databases, strip credentials, and block external I/O. PostgreSQL integration tests require an explicitly selected disposable `test_` database; the production PostgreSQL database is never a test target:

```bash
# Run the isolated unit test suite
uv run pytest

# Run targeted component unit tests (e.g. agent, broker, market, research, storage)
uv run pytest tests/agent/ tests/broker/ tests/market/ tests/research/ tests/storage/

# Run pytest with code coverage report
uv run pytest --cov=agentic_trader --cov-report=term-missing
```

### Broker contract and integration testing

See the [Alpaca integration review](docs/alpaca-integration-review.md) for actual-SDK
HTTP/WebSocket tests, disposable PostgreSQL CI, confirmed stop replacement, and
remaining entry reservation/fill-ledger work. `/flatten` previews by default;
`/flatten confirm` closes positions without halting future trading.
