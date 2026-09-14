# Production Operations & Steady-State Deployment Guide

This document is the **single source of truth** for operating the **Cash-Plus Trading Copilot** in production. It defines what runs in continuous steady state, how to deploy and monitor containerized instances, and categorizes every system command by operational lifecycle.

---

## 1. Production Architecture Overview

The system is architected as an event-driven quantitative trading daemon running asynchronously on Python 3.14.

```
                                  +---------------------------------------+
                                  |         COPILOT PRODUCTION DAEMON      |
                                  |             (`copilot daemon`)        |
                                  +---------------------------------------+
                                                      |
         +--------------------+-----------------------+---------------------+--------------------+
         |                    |                       |                     |                    |
         v                    v                       v                     v                    v
+-----------------+  +-----------------+     +-----------------+   +-----------------+  +-----------------+
|   APScheduler   |  | Position Monitor|     | Real-Time WS    |   | Telegram Bot    |  | Prometheus HTTP |
| 4-Hour Scans    |  | & Reconciler    |     | Streams         |   | Async Poller    |  | Exporter Server |
|                 |  | (15-Min Loop)   |     | (Alpaca/Trad.)  |   | (Two-Way Comms) |  | (Port :9108)    |
+-----------------+  +-----------------+     +-----------------+   +-----------------+  +-----------------+
         |                    |                       |                     |                    |
         v                    v                       v                     v                    v
  Market Data          Broker REST &           Sub-Second Bracket    Operator Approval    Prometheus /
  (Composite Provider: SQLite Sync             Fill & Cancellation   & Manual Command     Grafana & Docker
  Alpaca / YFinance)   (signals.db)            Events                Dispatch             Healthchecks
```

---

## 2. The Steady-State Production Service

In production, **only one continuous process runs**:

```bash
copilot daemon
```

### What `copilot daemon` Executes Under Its Unified Event Loop:

1. **Scheduled Quantitative Scanner (APScheduler)**:
   - Wakes every 4 hours aligned with CME/equity candle closes (`00:00`, `04:00`, `08:00`, `12:00`, `16:00`, `20:00` UTC).
   - Fetches multi-timeframe candles (Daily, 4-Hour, 1-Hour) via `CompositeMarketDataProvider` (Alpaca primary, Yahoo Finance fallback).
   - Runs Trend-Pullback and Squeeze Breakout screeners.
   - Evaluates macro lockout windows (blocks entries within $[-60\text{m}, +30\text{m}]$ of Tier-1 releases).
   - Queries the configured LLM agent for thesis evaluation and risk gating.
   - Dispatches rich Telegram alert cards with interactive approval buttons (`[ 🚀 Execute ]` / `[ ❌ Dismiss ]`).

2. **Position Monitor & Reconciliation Loop**:
   - Polls active positions every 15 minutes.
   - Checks take-profit and stop-loss levels against live market quotes.
   - Reconciles resting broker orders with SQLite database records (`data/signals.db`).

3. **Sub-Second WebSocket Trade Streams**:
   - **Alpaca `TradingStream`**: Connects via persistent WebSocket to capture instant bracket order fills, partial fills, cancellations, and liquidations. Automatically marks trades `CLOSED_WIN` or `CLOSED_LOSS` in SQLite and fires immediate Telegram notifications.
   - **Tradovate WebSocket**: Syncs user accounts, positions, and order states with CME.
   - **Automatic Reconnection**: Includes exponential backoff and transparent fallback to polling reconciliation if the stream disconnects.

4. **Two-Way Telegram Interactive Bot**:
   - Continuous async polling listener allowing operators to interactively query the trading desk from any mobile device or desktop.
   - Built-in Telegram slash command autocomplete (`set_my_commands`) automatically registered at startup.
   - Supports `/status`, `/positions`, `/perf`, `/regime`, `/gex`, `/pairs`, `/scan`, `/backtest`, `/close`, `/panic` (emergency kill switch with 2-step confirmation), `/resume`, and interactive callback buttons.

5. **Native Prometheus Observability Server**:
   - Runs a lightweight async HTTP server on `0.0.0.0:9108`.
   - Exposes standard Prometheus 0.0.4 metrics at `GET /metrics`.
   - Exposes container liveness and readiness probe at `GET /healthz`.

6. **Dynamic Trailing Stop & Broker Synchronization**:
   - Continuously evaluates active positions for Chandelier ATR high-water mark trailing stops.
   - Automatically synchronizes resting bracket stop orders directly on exchange brokers (Alpaca and Tradovate) with graceful degradation.

---

## 3. Pre-Flight Production Checklist

Before launching the production daemon, verify the following steps:

### 1. Pre-Flight System Doctor (`copilot doctor`)
Execute automated diagnostic probes across all 8 subsystems (database, risk limits, exchange market calendar, Telegram bot, Alpaca Paper API, Tradovate, Finnhub macro calendar, and LLM model):
```bash
uv run copilot doctor
```
Ensure all required checks display `✅ PASS` before proceeding.

### 2. Environment Secrets (`.env` or `.envrc`)
Ensure all credentials are populated:
```bash
# Verify environment configuration
uv run python -c "from agentic_trader.config import load_config; cfg = load_config(); print('Config loaded:', cfg.execution_mode, 'Cash: $', cfg.portfolio.cash)"
```

### 3. Database Schema Migration
Ensure SQLite database schema is migrated to the latest revision:
```bash
uv run copilot db upgrade head
```

### 4. Verify Notification Pipeline & Sizing Buttons
Send a synthetic signal card to verify Telegram connectivity and broker button dispatch:
```bash
uv run copilot test-alert
```

---

## 4. Production Deployment Methods

### Method A: Docker Compose (Recommended for Servers & Cloud VMs)

Docker Compose provides container isolation, automated restarts, persistent SQLite volumes, and built-in health monitoring.

```bash
# 1. Build and start in detached mode
docker compose up -d

# 2. Inspect container status and health
docker compose ps
# Output:
# NAME              IMAGE                    COMMAND            SERVICE   CREATED         STATUS                    PORTS
# trading-copilot   agentic-trader-copilot   "copilot daemon"   copilot   2 minutes ago   Up 2 minutes (healthy)    0.0.0.0:9108->9108/tcp

# 3. Follow live production logs
docker compose logs -f trading-copilot

# 4. Stop service gracefully
docker compose down
```

#### Healthcheck & Container Supervision:
The container automatically runs an internal health check every 30 seconds querying `http://localhost:9108/healthz`. If the internal event loop stalls or terminates, Docker flags the container `unhealthy` and auto-restarts (`restart: unless-stopped`).

---

### Method B: macOS `launchd` (Recommended for Dedicated Mac Mini Desks)

For local trading machines, `launchd` keeps the daemon running across reboots with automated process supervision and an independent 60-second watchdog monitor.

```bash
# 1. Install LaunchAgent daemon and background 60s watchdog
./scripts/launchd.sh install

# 2. Check service and watchdog registration/PID status
./scripts/launchd.sh status

# 3. Run comprehensive diagnostic health check
./scripts/launchd.sh health

# 4. Trigger an immediate watchdog liveness probe
./scripts/launchd.sh watchdog

# 5. Restart daemon via kickstart
./scripts/launchd.sh restart

# 6. Follow live log stream
./scripts/launchd.sh logs

# 7. Follow watchdog supervision log
./scripts/launchd.sh watchdog-logs

# 8. Pause service
./scripts/launchd.sh stop

# 9. Uninstall both daemon and watchdog
./scripts/launchd.sh uninstall
```

---

### Method C: Linux `systemd` Unit Template

For production Ubuntu/Debian/RHEL servers:

```ini
# /etc/systemd/system/trading-copilot.service
[Unit]
Description=Cash-Plus Trading Copilot Daemon
After=network.target

[Service]
Type=simple
User=trader
WorkingDirectory=/opt/agentic-trader
EnvironmentFile=/opt/agentic-trader/.env
ExecStartPre=/home/trader/.local/bin/uv run copilot db upgrade head
ExecStart=/home/trader/.local/bin/uv run copilot daemon
Restart=always
RestartSec=10
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now trading-copilot
sudo journalctl -u trading-copilot -f
```

---

## 5. Complete Command Taxonomy & Lifecycle Matrix

To eliminate confusion between production daemons, day-to-day operations, and research/eval utilities, use this lifecycle matrix:

| Subcommand | Lifecycle Stage | Frequency | Purpose |
|---|---|---|---|
| **`copilot daemon`** | **Production Steady-State** | **24/7 Persistent** | Core daemon running APScheduler, WebSocket streams, Position reconciler, Telegram bot, and Prometheus server |
| **`copilot listen`** | **Production Debug** | On-Demand | Runs Telegram bot listener only without scheduling scans |
| **`copilot status`** | **Day-to-Day Operations** | Ad-Hoc / Daily | Inspects portfolio cash base, active notional exposure, leverage, and recent signals |
| **`copilot positions`** | **Day-to-Day Operations** | Ad-Hoc / Intraday | Displays tracked positions, live quotes, stop/target prices, and unrealized P&L |
| **`copilot scan`** | **Day-to-Day Operations** | Ad-Hoc | Triggers on-demand market scan (`--dry-run`, `--no-llm`, `--asset-class`, `--symbols`) |
| **`copilot execute <id>`** | **Day-to-Day Operations** | Ad-Hoc | Manually authorizes and submits a pending trade signal to the broker |
| **`copilot close <id>`** | **Day-to-Day Operations** | Ad-Hoc | Liquidates an open position, records realized P&L, releases notional exposure |
| **`copilot panic`** | **Emergency Risk Control** | Emergency / Critical | Institutional kill switch: cancels all resting orders, liquidates positions, halts trading (`--confirm`) |
| **`copilot resume`** | **Emergency Risk Control** | Post-Halt | Clears persistent emergency halt and restores automated universe scans and trade executions |
| **`copilot gex [sym]`** | **Day-to-Day Operations** | Daily / Morning | Analyzes dealer gamma exposure, call/put pinning walls, and gamma flip level |
| **`copilot pairs`** | **Day-to-Day Operations** | Daily / Morning | Evaluates cross-asset cointegration and rolling spread $Z$-score arbitrage signals |
| **`copilot metrics`** | **Day-to-Day Operations** | Ad-Hoc / CI | Dumps Prometheus exposition text or runs standalone metrics server (`--serve`) |
| **`copilot test-alert`** | **Setup & Verification** | Once / Post-Deploy | Emits a synthetic trade card to test Telegram buttons and broker hooks |
| **`copilot db upgrade`** | **Database Maintenance** | Post-Deploy / Migration | Applies pending Alembic migrations to SQLite schema (`upgrade head`) |
| **`copilot db current`** | **Database Maintenance** | Ad-Hoc | Displays current database migration revision |
| **`copilot db history`** | **Database Maintenance** | Ad-Hoc | Lists historical Alembic database migrations |
| **`copilot db clear`** | **Database Maintenance** | Ad-Hoc / Maintenance | Purges historical signals and resets autoincrement ID without dropping schema (`--yes`) |
| **`copilot backtest`** | **Research & Evaluation** | Ad-Hoc / Weekly | Historical backtest with friction, Cash-Plus attribution, and Monte Carlo |
| **`copilot optimize`** | **Research & Evaluation** | Ad-Hoc / Monthly | Parameter grid search and rolling walk-forward cross-validation (`--walk-forward`) |
| **`copilot retune`** | **Research & Evaluation** | Scheduled / Monthly | Automated parameter recalibration with Walk-Forward Efficiency (WFE) thresholding |
| **`copilot stress`** | **Research & Evaluation** | Ad-Hoc / Quarterly | Crisis replay (2008 GFC, 2020 COVID, 2022 Inflation) and instantaneous factor shocks |
| **`copilot eval`** | **Model & Prompt Testing** | Pre-Commit / CI | Runs Promptfoo benchmark against hard risk invariants |

---

## 6. Prometheus & Grafana Monitoring

When `copilot daemon` is running, operational metrics are continuously exposed on port `:9108`:

### Sample Prometheus Scrape Configuration (`prometheus.yml`):
```yaml
scrape_configs:
  - job_name: 'trading_copilot'
    scrape_interval: 10s
    metrics_path: '/metrics'
    static_configs:
      - targets: ['localhost:9108']
```

### Key Metrics to Alert On:
- `trader_up == 1`: Copilot process availability.
- `trader_account_cash_dollars`: Liquid cash reserves.
- `trader_notional_exposure_dollars > 60000`: Alert on exposure ceiling breach.
- `trader_active_positions_count`: Number of open positions.
- `trader_order_execution_latency_seconds_bucket`: Execution latency round-trip to broker.

---

## 7. Interactive Telegram Command Reference

When `copilot daemon` is running, operators can execute these commands in Telegram:

| Command | Action | Example |
|---|---|---|
| `/status` | View cash base, active exposure, open leverage, and recent signals | `/status` |
| `/positions` | View active trades, live quotes, and mark-to-market unrealized P&L | `/positions` |
| `/perf` | View cumulative closed trade performance, win rate, and profit factor | `/perf` |
| `/regime` | View real-time VIX, 10Y yield, and Dollar Index macro regime | `/regime` |
| `/gex [sym]` | View dealer gamma exposure, call/put pinning walls, and gamma flip | `/gex SPY` |
| `/pairs` | View cross-asset cointegration, half-life, and rolling spread $Z$-scores | `/pairs` |
| `/backtest [sym] [lookback]` | Trigger an on-demand offline backtest simulation | `/backtest SPY 1y` |
| `/scan` | Trigger an immediate quantitative scan across the universe | `/scan` |
| `/close <id> [price]` | Manually close an active trade and record fill | `/close 3 5850.25` |
| `/panic [confirm]` | 🔴 Institutional kill switch: cancel orders, liquidate open positions, and halt trading | `/panic` or `/panic confirm` |
| `/resume` | 🟢 Clear emergency trading halt and resume automated operations | `/resume` |
| `/help` | Display command menu and enforced risk invariants | `/help` |

> [!NOTE]
> On startup, the Telegram bot registers slash commands via `set_my_commands` and configures the native chat menu button via `set_chat_menu_button(MenuButtonCommands())` across default, private, and chat-specific scopes. Modern Telegram mobile, desktop, and web clients display a dedicated `[Menu]` / `[/]` button with interactive autocomplete for instant command discovery.
