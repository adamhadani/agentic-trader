# Assistant handoff

Read [AGENTS.md](AGENTS.md), [development notes](docs/development-notes.md) and
[operations](docs/production.md) before changing runtime behavior. This file is a
starting map; the linked domain guides own detailed contracts.

## Running system and safe workflow

- The installed checkout owns one launchd **Alpaca paper** daemon and Telegram
  poller. Inspect `git status` and launchd registration first; preserve existing work.
- `EXECUTION_MODE=alpaca`, `ALPACA_PAPER=true` is brokerage paper trading.
  `EXECUTION_MODE=paper` is the simulator. Do not confuse their data or credentials.
- Use an isolated worktree for development, especially supervisor script changes:
  the watchdog executes `scripts/launchd.sh` every 60 seconds. Pause watchdog then
  daemon before updating the installed checkout. Do not start a second poller.
- Python 3.14 and `uv`. Run full affected tests, disposable PostgreSQL integration
  for persistence changes, and `uv run pre-commit run --all-files` before committing.
- Tests select explicit fixture config, temporary SQLite, stripped credentials and
  blocked Python/libcurl network I/O. PostgreSQL requires `--run-postgres` and a
  guarded `TEST_POSTGRES_URL` database named `test_*`. Never relax these guards.
- Inject explicit config/storage before constructing services. Bare `AppConfig()`
  has no runtime database. `load_config()` is the explicit environment boundary;
  it does not mutate `os.environ`. There is no PostgreSQL-to-SQLite failover.
- Dry scans use an empty temporary simulated portfolio with no broker mutations
  or Telegram. `test-alert` previews; explicit sending requires a separate bot/chat.
- Keep secrets, chat identifiers, raw logs, DBs and incident snapshots out of Git.
  Tests and deployed verification are separate evidence. A healthy PID is insufficient.

## Code map

| Boundary | Canonical modules |
| --- | --- |
| CLI composition and lifecycle | `cli/main.py`, `cli/utils.py`, `cli/commands/` |
| Application orchestration | `agent/copilot.py:TradingCopilot` |
| Signal evaluation and risk | `agent/evaluator.py`, `position_sizing.py`, `regime.py`, `macro.py`, `calendar.py` |
| Market data and sessions | `data/`, `market/session.py`, `resilience/fallback.py` |
| Strategies | `screeners/base.py`, `strategies.py`, `registry.py`, `formulaic.py` |
| Entry/close application services | `execution/entries.py`, `admission.py`, `closing.py` |
| Broker adapters and transport | `broker/base.py`, `alpaca.py`, `paper.py`, `tradovate.py` |
| Durable workflows and accounting | `storage/workflow.py`, `storage/ledger.py`, `accounting/` |
| Operational incidents and maintenance | `diagnostics/incidents.py`, `monitor.py`, `probe.py`, `storage/operations.py`, `maintenance.py` |
| Telegram and rendering | `notifier/telegram_bot.py`, `transport.py`, `outbox.py`, `presentation/` |
| Diagnostics and telemetry | `diagnostics/readiness.py`, `doctor.py`, `telemetry/`, `runtime.py` |
| Research and chat | `backtest/`, `research/`, `options/`, `pairs/`, `agent/copilot_graph.py`, `copilot_tools.py` |

`TradingCopilot` still combines orchestration and construction; prefer injected
services and typed results when changing a boundary. Do not introduce compatibility
aliases for internal callers. Update callers to canonical names in the same change.
Reuse domain enums; operator policy belongs in validated config. Leave SDK wire
keys, frozen migrations, mathematical identities and explicit test examples intact.

## Contracts to preserve

1. **Entry authorization:** CLI/buttons call `EntryExecutionService`, atomically
   reserve risk and join the durable FIFO. Only a valid preflight token commits
   `submitting`; never expire or replay a broker submission. Unknown outcomes halt
   new risk and recover by exact client ID, including after a 404. Rejected/unfilled
   terminal evidence releases capacity; acceptance is not a fill.
2. **Closure:** `PositionCloseService` persists exclusive intent, confirms exact
   order-group cancellation, revalidates position/session and submits once.
   `/flatten` previews by default and preserves halt state. Partial/ambiguous
   ownership remains blocked; exact full fills and conditional SQL govern closure.
3. **Broker evidence:** never match exits by symbol alone, infer a fill price from
   an order request, or persist a stop ratchet before exact replacement confirmation.
   Preserve original thesis/risk. POST/PATCH/DELETE are never automatically retried.
4. **Reports:** CLI/Telegram positions share one broker snapshot. `/perf` separates
   reconciled account activities from tracked full-close statistics. Decimal cash,
   inventory, account binding and broker cost basis govern account totals; unknown,
   failed or stale evidence withholds realized P&L. Never infer partial signal ownership.
5. **Persistence:** schema head is `008_alpha_pipeline`; Alembic exclusively
   owns migrations. Financial/order/work/incident events and dead letters are retained.
   Only redundant old healthy observations may be compacted by the documented policy.
6. **Delivery:** critical business transitions and notification intents share a
   transaction. The outbox retries delivery only; Telegram is at least once.
   Rebuilds do not send messages. Never retry a trade handler to recover its reply.
7. **Async:** offload blocking SDK/provider/research work; check shared-state races
   before adding threads. Keep loop-lag, request/update correlation and poll metrics.
   Long Telegram handlers still serialize; do not enable blanket concurrent trading.
8. **Operations:** HTTP `/healthz` is liveness and `/readyz` passive freshness.
   `doctor` is active CLI diagnostics; `/healthcheck` is removed. External
   `doctor --monitor` persists debounced incidents and uses the existing outbox;
   it never polls Telegram or consumes entry work. Degraded readiness alerts; it
   does not blindly restart or automatically change the trading halt.
9. **Language:** `/macro` owns combined volatility/macro context; no `/regime` alias.
   Render configured policy and real feed dates. GEX is a research estimate with
   quality notes, never fabricated spot data or measured dealer inventory.

Detailed contracts: [durable execution](docs/durable-execution.md),
[Alpaca integration](docs/alpaca-integration-review.md),
[account ledger](docs/account-ledger.md),
[operational monitoring](docs/operational-monitoring.md).

## Validation and useful commands

```bash
uv run pytest
TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_trader uv run pytest tests/integration --run-postgres
uv run pre-commit run --all-files
uv run copilot doctor --readiness
uv run copilot db queue
uv run copilot db outbox
uv run copilot db incidents
uv run copilot db retention            # preview; --apply compacts one bounded batch
uv run python scripts/verify_runtime.py
```

Full TCP/WebSocket and PostgreSQL checks remain mandatory; the optional in-memory
SDK harness is only an aid for restricted environments. Runtime verification must
match the clean committed revision, current run, poll/stream freshness, broker
positions and account reconciliation. Record deployment evidence separately.

Next priorities and accepted limits live in the [architecture review](docs/architecture-review.md).
Corporate actions, per-signal partial allocation/protection, macro-age admission,
live trailing-policy parity and research executor separation remain unfinished.

Read the [alpha pipeline](docs/alpha-pipeline.md) before research, promotion or portfolio changes.
The DSL rejects lookahead and invalid contracts; research/live share scores and
versioned bracket policy. All trials and one-use holdout intervals are journaled.
The registry is transactional and loaded between scans; historical YAML imports
are unqualified shadow-only. No automatic promotion or combined-portfolio execution.
Use actual deployment-feed evidence, 20 shadow dates/10 decisions by default,
and exact immutable versions. Keep historical position protection and broker authority.
Schema 008 adds alpha projections and optional signal attribution. The original
[alpha-stack review](docs/alpha-stack-review.md) remains historical evidence;
its strict expected failures have become passing regression tests.

The [active alpha roadmap](docs/alpha-roadmap.md) owns the ordered long-horizon plan: calibration, session-correct replay, forecast/strategy separation, broader data, and forward observation. Update its milestones and evidence when completing work.

`alpha calibrate` is a bounded synthetic diagnostic: no runtime config/DB/provider,
no promotion or shadow credit. Keep the shared statistical assessment separate from
deployment permissions. Its fixed-panel bootstrap does not replay adaptive search;
never use its output to discount cumulative trials or relax gates retroactively.

A1b uses the [predeclared study protocol](docs/alpha-study-protocol.md) and
`config/research/a1b-v1.json`. `alpha study` persists its manifest before computation
and every replicate afterward; development failures preserve unexamined validation.
Keep development/validation seed namespaces separate, freeze the winner before
holdout, retain incomplete denominators, and never tune gates on validation results.
The [A1b result](docs/alpha-study-2026-09-16.md) retains all 1,952 jobs but is
scientifically incomplete (22 unavailable comparisons). No gate change is justified.
A2a's [return-timeline contract](docs/alpha-return-timeline.md) includes cash bars
independently of feature availability and persists coverage. The validation policy
versions this calculation; old evidence cannot promote or commit new alpha risk.
Lifetime trial counts persist; variance uses comparable current-clock samples.
The fresh protocol is `config/research/a2a-v1.json`. Next is A2b session replay;
preserve historical study evidence and use fresh validation after policy changes.
