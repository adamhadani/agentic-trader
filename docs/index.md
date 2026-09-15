---
layout: default
title: Home - Agentic Trader
---

# 🤖 Agentic Trader
### Autonomous Multi-Asset Quantitative Trading System

The **Agentic Trader** is an algorithmic trading system designed around a **"Cash-Plus" (portable alpha)** portfolio architecture ($100,000 baseline cash generating risk-free Treasury yield). The system continuously screens multi-asset markets, validates setups through an LLM agent with macro calendar awareness, and executes bracket orders across Tradovate (CME micro futures) and Alpaca (equities, ETFs, and crypto) with real-time Telegram oversight.

---

## Current runtime baseline

As of September 15, 2026: launchd, Alpaca paper, PostgreSQL, 4-hour/15-minute scans,
1-minute reconciliation, brokerage-derived valuations, audit provenance, and
isolated tests. Start with [development notes](development-notes.md) and the
[incident report](incident-2026-09-15.md). Roadmap phases are historical milestones,
not a guarantee that every research feature is integrated into live sizing.

## 🚀 Quick Navigation

- [**Production Operations Guide**](production.md): The single source of truth on 24/7 steady-state deployment, Docker Compose, Prometheus metrics, and operator runbooks.
- [**CLI Command Reference**](cli-reference.md): Comprehensive reference guide covering all Click CLI subcommands (scanning, execution, research, and alpha mining).
- [**Quantitative Strategies & Models**](strategies.md): Mathematical formulations for Trend-Pullback, Squeeze Breakout, Options GEX surface, Pairs Trading, and Formulaic Alpha DSL.
- [**System Development Roadmap**](roadmap.md): Complete chronological record of completed phases (Phases 1 through 45) and future milestones.

---

## 🛡️ Core Institutional Risk Invariants

1. **Instrument Universe**: Micro futures (`/MES`, `/MNQ`, `/MGC`, `/MCL`) and liquid ETFs (`SPY`, `QQQ`, `IWM`, `GLD`, `USO`).
2. **Fixed Margin Sizing**: Static mode defaults to one micro contract; equities use dollar-risk sizing. Total active open notional exposure across all concurrent positions must not exceed **$60,000** (0.6x effective leverage on $100k cash base).
3. **Reward-to-Risk (R:R)**: Strictly **$\ge 2.0$**. Stop distance must be **$\ge 1.5 \times \text{ATR}(14)$** to avoid noise stop-outs.
4. **Macro Event Lockout**: Zero entry alerts permitted within **$[-60\text{m}, +30\text{m}]$** of Tier-1 economic releases (CPI, PPI, FOMC, NFP).
5. **Deduplication Rule**: Zero duplicate signals for the same contract + strategy within 12 hours.
6. **Emergency Kill Switch**: Instant liquidation of all active positions and cancellation of resting orders with persistent DB halt state via `/panic` or `copilot panic`.

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
  (Alpaca / YFinance / PostgreSQL 18.6 /       Fill & Cancellation   & Manual Command     Grafana & Docker
  Finnhub Macro)       SQL + Audit             Events                Dispatch             Healthchecks
                       (signals / state)
```

---

## 📦 Steady-State Production Launch

```bash
# 1. Launch production services (PostgreSQL 18.6 + Copilot Daemon) with Docker Compose
docker compose up -d

# 2. Run database migrations inside container
docker compose exec copilot copilot db upgrade head

# 3. View live logs
docker compose logs -f trading-copilot
```

For detailed instructions, see the [Production Operations Guide](production.md).
