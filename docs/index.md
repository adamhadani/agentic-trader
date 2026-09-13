---
layout: default
title: Home - Cash-Plus Trading Copilot
---

# 🤖 Cash-Plus Trading Copilot
### Autonomous Multi-Asset Quantitative Trading System

The **Cash-Plus Trading Copilot** is an algorithmic trading system designed around a **"Cash-Plus" (portable alpha)** portfolio architecture ($100,000 baseline cash generating risk-free Treasury yield). The system continuously screens multi-asset markets, validates setups through an LLM agent with macro calendar awareness, and executes bracket orders across Tradovate (CME micro futures) and Alpaca (equities, ETFs, and crypto) with real-time Telegram oversight.

---

## 🚀 Quick Navigation

- [**Production Operations Guide**](production.md): The single source of truth on 24/7 steady-state deployment, Docker Compose, Prometheus metrics, and operator runbooks.
- [**CLI Command Reference**](cli-reference.md): Comprehensive reference guide covering all Click CLI subcommands.
- [**Quantitative Strategies & Models**](strategies.md): Mathematical formulations for Trend-Pullback, Squeeze Breakout, Options GEX surface, and Pairs Trading.
- [**System Development Roadmap**](roadmap.md): Complete chronological record of completed phases (Phases 1 through 22) and future milestones.

---

## 🛡️ Core Institutional Risk Invariants

1. **Instrument Universe**: Micro futures (`/MES`, `/MNQ`, `/MGC`, `/MCL`) and liquid ETFs (`SPY`, `QQQ`, `IWM`, `GLD`, `USO`).
2. **Fixed Margin Sizing**: 1 micro contract per signal. Total active open notional exposure across all concurrent positions must not exceed **$60,000** (0.6x effective leverage on $100k cash base).
3. **Reward-to-Risk (R:R)**: Strictly **$\ge 2.0$**. Stop distance must be **$\ge 1.5 \times \text{ATR}(14)$** to avoid noise stop-outs.
4. **Macro Event Lockout**: Zero entry alerts permitted within **$[-60\text{m}, +30\text{m}]$** of Tier-1 economic releases (CPI, PPI, FOMC, NFP).
5. **Deduplication Rule**: Zero duplicate signals for the same contract + strategy within 12 hours.

---

## ⚡ Architecture At A Glance

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
  (yfinance /          SQLite Sync             Fill & Cancellation   & Manual Command     Grafana & Docker
  Finnhub Macro)       (signals.db)            Events                Dispatch             Healthchecks
```

---

## 📦 Steady-State Production Launch

```bash
# 1. Run database migrations
uv run copilot db upgrade head

# 2. Launch production daemon with Docker Compose
docker compose up -d

# 3. View live logs
docker compose logs -f trading-copilot
```

For detailed instructions, see the [Production Operations Guide](production.md).
