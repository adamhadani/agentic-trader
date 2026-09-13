# Cash-Plus Trading Copilot
### Autonomous Multi-Asset Quantitative Trading System

[![Python 3.14](https://img.shields.io/badge/python-3.14-blue.svg)](https://www.python.org/)
[![Checked with mypy](https://img.shields.io/badge/mypy-checked-blue)](http://mypy-lang.org/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)
[![Documentation](https://img.shields.io/badge/docs-GitHub_Pages-blue)](https://adamhadani.github.io/agentic-trader/)

The **Cash-Plus Trading Copilot** is an algorithmic trading system designed around a **"Cash-Plus" (portable alpha)** portfolio architecture ($100,000 baseline cash generating risk-free Treasury yield). The system continuously screens multi-asset markets, validates setups through an LLM agent with macro calendar awareness, and executes bracket orders across Tradovate (CME micro futures) and Alpaca (equities, ETFs, and crypto) with real-time Telegram oversight and Prometheus observability.

---

## 📚 Documentation & Reference Manuals

- [**Production Operations Guide**](docs/production.md): **Single Source of Truth** for 24/7 steady-state deployment, Docker Compose, Prometheus metrics, and operator runbooks.
- [**CLI Command Reference**](docs/cli-reference.md): Exhaustive guide to all Click CLI subcommands, flags, and outputs.
- [**Quantitative Strategies & Models**](docs/strategies.md): Mathematical formulations for Trend-Pullback, Squeeze Breakout, Options GEX, and Pairs Trading.
- [**Development Roadmap**](docs/roadmap.md): Complete chronological record of completed phases (Phases 1 through 22) and future milestones.

---

## 1. Core Risk & Portfolio Constraints

1. **Instrument Scope:** Micro futures (`/MES`, `/MNQ`, `/MGC`, `/MCL`) and liquid ETF/equity proxies (`SPY`, `QQQ`, `IWM`, `GLD`, `USO`).
2. **Fixed Sizing:** Exactly 1 micro contract per signal. Total active open notional exposure across all concurrent positions must not exceed **$60,000** (0.6x effective leverage on $100k cash).
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
1. **APScheduler Scan Job**: Scans multi-asset universe every 4 hours aligned with candle closes (`00:00`, `04:00`, `08:00`, `12:00`, `16:00`, `20:00` UTC).
2. **Position Monitor & Reconciler**: Polls active broker positions every 15 minutes, tracking stop-loss and take-profit triggers against live quotes.
3. **Sub-Second WebSocket Streams**:
   - **Alpaca `TradingStream`**: Sub-second synchronization for bracket order fills, stops, targets, and cancellations.
   - **Tradovate WebSocket**: Account, position, and CME order state synchronization.
4. **Two-Way Telegram Interactive Bot**: Continuous async polling listener processing operator commands (`/status`, `/positions`, `/perf`, `/regime`, `/gex`, `/pairs`, `/scan`, `/close`) and inline action buttons (`[ 🚀 Execute ]` / `[ ❌ Dismiss ]`).
5. **Native Prometheus Exporter**: Lightweight async HTTP server running on `0.0.0.0:9108` serving `GET /metrics` and container health probe at `GET /healthz`.

---

## 3. Production Deployment Methods

### Option A: Docker Compose (Recommended)
Docker Compose provisions the container with automatic restarts, persistent SQLite volumes, and Prometheus metrics port mapping (`:9108`):

```bash
# 1. Run database migrations
uv run copilot db upgrade head

# 2. Build and launch in detached mode
docker compose up -d

# 3. View live logs
docker compose logs -f trading-copilot

# 4. Check container health status
docker compose ps
```

### Option B: macOS `launchd` (Local Mac Mini Desk)
For dedicated local Mac machines, `launchd` keeps the daemon running across reboots:
```bash
./scripts/launchd.sh install   # Installs ~/Library/LaunchAgents/com.agentictrader.copilot.plist
./scripts/launchd.sh status    # Check daemon status
./scripts/launchd.sh logs      # Tail data/copilot.log in real time
./scripts/launchd.sh stop      # Temporarily pause service
./scripts/launchd.sh uninstall # Unload and remove plist
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
uv run copilot status              # View cash base, active notional exposure, leverage, and macro events
uv run copilot positions           # View active tracked positions, stops, targets, and unrealized P&L
uv run copilot scan                # Trigger on-demand market scan (--dry-run, --no-llm, --asset-class)
uv run copilot execute <id>        # Manually authorize and submit an approved signal to the broker
uv run copilot close <id> [price]  # Liquidate an open position, record realized P&L, release exposure
uv run copilot gex [symbol]        # View options dealer gamma exposure, call/put walls, and gamma flip
uv run copilot pairs               # Screen cross-asset pairs for cointegration and rolling spread Z-scores
uv run copilot metrics             # Dump Prometheus exposition text or run standalone server (--serve)
uv run copilot test-alert          # Send synthetic signal card to verify Telegram formatting and buttons
```

### C. Quantitative Research, Backtesting & Calibration (Offline)
```bash
uv run copilot backtest            # Historical backtest with friction, Cash-Plus attribution, & Monte Carlo
uv run copilot optimize            # Parameter grid search & rolling walk-forward validation (--walk-forward)
uv run copilot retune              # Automated parameter recalibration with Walk-Forward Efficiency (WFE) filtering
uv run copilot stress              # Crisis replay (2008 GFC, 2020 COVID, 2022 Inflation) & factor shocks
uv run copilot eval                # Benchmark LLM decision prompts against risk invariants (Promptfoo)
```

### D. Database Migrations (`copilot db`)
```bash
uv run copilot db upgrade head     # Apply pending Alembic database schema migrations
uv run copilot db current          # View current schema revision
uv run copilot db history          # View migration history
uv run copilot db downgrade -1     # Roll back last migration
```

---

## 5. Interactive Telegram Commands

When the daemon is running, operators can query and command the trading desk directly from Telegram:

| Command | Description | Example |
|---|---|---|
| `/status` | View cash base, open notional exposure, leverage, and macro events | `/status` |
| `/positions` | View active trades, live quotes, and mark-to-market unrealized P&L | `/positions` |
| `/perf` | View cumulative closed trade performance, win rate, and profit factor | `/perf` |
| `/regime` | View real-time VIX, 10Y Treasury yield, and Dollar Index macro regime | `/regime` |
| `/gex [sym]` | View dealer gamma exposure (GEX), pinning walls, and gamma flip level | `/gex SPY` |
| `/pairs` | View cross-asset cointegration, mean-reversion half-life, and Z-scores | `/pairs` |
| `/backtest [sym] [lookback]` | Trigger on-demand offline backtest simulation from mobile | `/backtest SPY 1y` |
| `/scan` | Trigger an immediate quantitative scan across the universe | `/scan` |
| `/close <id> [price]` | Manually close an active trade and record fill | `/close 3 5850.25` |
| `/help` | Display interactive command menu and enforced risk invariants | `/help` |

---

## 6. System Prerequisites & Setup

On macOS:
```bash
# Core package manager & Python runner (Python >= 3.14)
brew install uv hadolint sqlite node

# Clone and sync dependencies
uv sync

# Copy environment template
cp .envrc.example .envrc
# Fill in your API keys (Telegram, LLM, Tradovate, Alpaca, Finnhub)
direnv allow  # or source .envrc

# Run database migrations
uv run copilot db upgrade head

# Install pre-commit git hooks
uv run pre-commit install
```

---

## 7. Quality Assurance & Testing

The repository enforces 100% test passing and strict linting via `pre-commit` and `pytest-impacted[fast]`:

```bash
# Run all pre-commit hooks (ruff, mypy, hadolint, uv-lock, pytest runner)
uv run pre-commit run --all-files

# Run pytest unit test suite (157 tests)
uv run pytest
```
