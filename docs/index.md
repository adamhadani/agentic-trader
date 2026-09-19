---
layout: default
title: Home — Agentic Trader
---

# Agentic Trader

Quantitative screening and operator-approved trading. The current desk uses
**Alpaca paper equities/ETFs**, PostgreSQL, one launchd daemon and Telegram.
Local simulation and research are separate from broker account performance.

## Operating the desk

- [Production operations](production.md): ownership, schedules, restart and verification.
- [CLI reference](cli-reference.md): commands, previews and recovery.
- [Operational monitoring](operational-monitoring.md): readiness alerts, dead letters and retention.
- [Account ledger](account-ledger.md): broker-authoritative positions and reconciled performance.

Four-hour and session-gated 15-minute scans stage suggestions for approval.
Positions reconcile every minute and on trade-stream wakeups. Independent workers
process approved entries, notifications and account activities. The external
60-second watchdog checks process state and sustained readiness failures.

Use `/positions` for broker valuations, `/perf` for account and tracked performance,
and `/macro` for combined market context. `/flatten` previews by default; confirmation
requests closes without changing the trading halt. `/panic` persists a halt and
requests emergency exits. Order acceptance is not a confirmed fill.

## Developing and reviewing

- [Development handoff](development-notes.md) and [assistant guide](../CLAUDE.md).
- [Architecture review and priorities](architecture-review.md).
- [Active alpha research roadmap](alpha-roadmap.md).
- [Alpha mining universe and charged campaign contract](alpha-mining-universe.md).
- [Prospective equity mining campaign — 2026-09-19](alpha-equity-mining-2026-09-19.md).
- [Screened equity breadth and DSL coverage — 2026-09-19](alpha-screened-equity-dsl-2026-09-19.md).
- [Durable entry queue, events and outbox](durable-execution.md).
- [Alpaca contracts and real transport coverage](alpaca-integration-review.md).
- [September 15 incident and remediation](incident-2026-09-15.md).

Inspect the registered service before starting anything. Develop in an isolated
worktree, especially supervisor edits; never start another poller/Compose stack
alongside launchd. Tests enforce explicit temporary/disposable storage and blocked
external network/credentials. Deployment requires its own current-run checks.

## Research and history

[Strategies and models](strategies.md) describes screening/research calculations.
Allocation optimization, GEX estimates and backtest returns do not imply production
integration or broker profit. [Historical milestones](roadmap.md), the original
specification and reference PDF are historical design/source material. Current
contracts and remaining limits are maintained in the guides above.

Read these pages directly as Markdown or preview locally with Jekyll. Alternative
Docker deployment instructions are in the operations guide; this desk currently
uses launchd.
