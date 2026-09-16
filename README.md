# Agentic Trader

A quantitative screening and operator-approved trading desk. The current deployment
uses **Alpaca paper equities/ETFs**, PostgreSQL, Telegram and a single macOS launchd
daemon. Scans propose trades; operator approval enters a durable execution queue.

Python **3.14** · `uv` · SQLAlchemy/Alembic · `alpaca-py` · pytest · Ruff · mypy

## Start here

- [Operations and deployment](docs/production.md): ownership, schedules, restart and verification.
- [CLI reference](docs/cli-reference.md): commands and recovery tools.
- [Development handoff](docs/development-notes.md) and [assistant instructions](CLAUDE.md).
- [Architecture review and priorities](docs/architecture-review.md).
- [Entry queue, broker events and outbox](docs/durable-execution.md).
- [Account activity ledger](docs/account-ledger.md).
- [Readiness alerts and retention](docs/operational-monitoring.md).
- [Alpaca contracts and integration coverage](docs/alpaca-integration-review.md).
- [Research strategies](docs/strategies.md), [historical roadmap](docs/roadmap.md),
  and [September 15 incident](docs/incident-2026-09-15.md).

## Runtime model

`EXECUTION_MODE=alpaca` with `ALPACA_PAPER=true` selects brokerage paper trading.
`EXECUTION_MODE=paper` selects the local simulator. They have separate state scopes.
Other broker adapters exist but require the fresh-admission contract before using
the entry queue. Alpaca crypto brackets and broker-backed slicing are unsupported.

One daemon owns:

- Four-hour swing scans and session-gated 15-minute intraday scans, relative to startup.
- Minute position reconciliation plus Alpaca trade-stream wakeups.
- Independent entry, notification and 60-second account-activity workers.
- Telegram commands, approval buttons and command-menu registration.
- `/metrics`, `/healthz` liveness and `/readyz` current-run freshness on port 9108.

The external launchd watchdog runs every 60 seconds. It restores missing/stopped
processes, persists sustained readiness incidents and queues alerts through the
existing outbox. It does not blindly restart an unready process. A separate weekly
alpha-miner job performs research without automatic promotion.

Positions use the broker's quantity, average entry, current price and unrealized
P&L from one snapshot. `/perf` separates reconciled account performance—including
partial/external executions and supported fees/income—from tracked full-close
statistics. Missing, stale or unreconciled evidence withholds realized totals.
`/status` shows configured risk capital and tracked exposure, not broker cash.

Risk policy comes from `config/config.yaml` and validated configuration. Defaults
include a $100k capital baseline, $60k total notional ceiling, per-class limits,
minimum R:R 2, and minimum stop distance 1.5 ATR. The baseline is a sizing input;
it does not imply the account earns modeled Treasury yield. Entry preflight
rechecks broker positions/orders, price age/drift, session, halt and risk capacity.

## Common operator commands

```bash
uv run copilot doctor --readiness   # passive current-run freshness; nonzero if unready
uv run copilot positions            # broker-backed positions
uv run copilot perf                 # read-only account refresh + performance
uv run copilot status               # configured risk budget and tracked exposure
uv run copilot scan                # scan and stage suggestions for approval
uv run copilot execute 4            # authorize signal 4; accepted does not mean filled
uv run copilot close 4 --dry-run     # preview a tracked close
uv run copilot close 4              # request fill-confirmed closure
uv run copilot flatten              # preview all current Alpaca positions
uv run copilot flatten --confirm    # close positions, preserving trading halt state
uv run copilot panic                # confirmation, persistent halt and emergency exits
uv run copilot resume               # clear halt only after unresolved-entry checks
uv run copilot db incidents         # persisted operational incident state
uv run copilot db outbox            # deliveries, retries and dead letters
uv run copilot db retention         # preview bounded healthy-observation compaction
```

Telegram supports `/status`, `/positions`, `/perf`, `/macro`, `/explain_macro`,
`/alphas`, `/gex`, `/pairs`, `/backtest`, `/scan`, `/close`, `/flatten`, `/panic`,
`/resume` and `/help`. `/macro` owns combined market context; `/regime` was removed.
The menu is registered in default, private-chat and operator-chat scopes at startup.

Normal equity closes require regular trading hours before canceling protection.
Panic can queue emergency exits for the next session. A requested or accepted close
is not a confirmed liquidation. Inspect per-position outcomes and broker positions.

## Development setup

Inspect launchd registration before starting services. **Do not run a second daemon,
`listen`, or Compose stack alongside the installed poller.** Develop in a worktree
when changing supervisor scripts: the installed watchdog executes them every minute.

```bash
uv sync --dev
# On a new installation only: copy .envrc.example to .envrc and configure credentials/DB.
uv run copilot db upgrade head
uv run pre-commit install
uv run pytest
uv run pre-commit run --all-files
```

PostgreSQL is the runtime backend. SQLite is explicitly selected for tests and
sandboxes; there is no automatic failover. Tests strip credentials, use temporary
DBs, block Python/native network I/O and enforce DB guards. Real SDK HTTP/WebSocket
integration uses loopback only. For PostgreSQL integration, create an empty
disposable database whose name starts `test_`:

```bash
TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_trader uv run pytest tests/integration --run-postgres
```

Safe local checks:

```bash
uv run copilot scan --dry-run --no-llm --symbols IWM
uv run copilot test-alert
uv run python scripts/verify_runtime.py
```

Dry scans use an empty temporary simulator and no Telegram or broker mutations;
market-data calls remain possible. `test-alert` previews locally. Explicit sending
requires a separate test bot/chat. The runtime verifier reads broker/Telegram state,
checks current source revision and daemon freshness, and submits no orders/messages.
Keep credentials, chat identifiers, raw logs, DBs and verification output private.

## Deployment and research

The installed Mac desk uses `./scripts/launchd.sh status` and controlled restarts.
Pause watchdog then daemon before updating its checkout or applying migrations.
Verify startup revision, readiness, broker/report parity, stream and actual poll
freshness afterward. See the [operations runbook](docs/production.md).

Docker Compose is an alternative deployment with `postgres` and `copilot` services;
use `docker compose logs -f copilot`. Never start it alongside this desk's launchd
stack. Compose's PostgreSQL image is independent of the installed Homebrew version.

Research commands include `backtest`, `optimize`, `retune`, `stress`, `gex`, `pairs`
and `alpha` mining/inspection/promotion. Research results are not live account P&L.
Promotion writes configuration; external edits require a daemon restart. Allocation
weights/convex optimization are not yet integrated into live sizing.
See the [alpha-stack review](docs/alpha-stack-review.md) before relying on mining
statistics or expanding discovery/promotion: it records reproduced validation and
research/live parity defects, stress-test evidence and prioritized experiments.

Read docs as Markdown or preview with `cd docs && bundle install && bundle exec jekyll serve`.
Historical design notes and the reference PDF are source material, not runtime guarantees.
