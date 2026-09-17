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
| Market data and sessions | `data/`, `market/session.py`, `market/bars.py`, `resilience/fallback.py` |
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
   Keep a notice for **every** confirmed trailing-stop change (operator preference,
   September 16). Frequent distinct ratchets are expected; correlate exact stop,
   outbox and Telegram request evidence before diagnosing duplicates.
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
SDK harness is only an aid for restricted environments. The runtime verifier reads
the daemon's fresh persisted ledger without starting a competing import. Verification
must match the clean committed revision, current run, poll/stream freshness, broker
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
The [fresh study](docs/alpha-timeline-study-2026-09-16.md), using
`config/research/a2a-v1.json`, completed 1,952 jobs without unavailable comparisons.
Null-search criteria passed; positive-control power is still insufficient. Keep
the gates unchanged. A2b's [session replay groundwork](docs/alpha-session-replay.md)
uses observed calendars, complete minute coverage and the shared execution engine.
`alpha replay` charges one real-data research attempt and excludes the inspected
interval before I/O; it never qualifies or earns shadow credit. Historical minute
data cannot establish point-in-time availability or actual partial fills. Next:
complete acquisition/durable scheduling for the now-versioned session clock and
verify broker execution assumptions.
Existing fixed-duration research/screening/shadow paths share the market clock
guard and reject explicit session/unknown layouts or ambiguous timestamps. Do not
silently reinterpret an old alpha version as using session-derived bars.
Preserve historical study evidence and use fresh validation after policy changes.

A2b's [prospective data observer](docs/alpha-forward-observations.md) runs in the
existing daemon, independently of scans/halt state. The desk observes SPY/15m on
its configured feed. Read `alpha status` for retained capture quality; `alpha_observer`
readiness means collector progress, including idle session waits, not complete data.
Raw receipts/revisions use the existing journal/private artifacts and earn no trial,
shadow or promotion credit. Inspected dates are excluded before price reads. Preserve
existing alpha identities; strategy clock migration and actual execution evidence
remain pending. Shutdown must finish reads before closing the observer's SDK clients.

New [session alpha definitions](docs/alpha-session-decisions.md) use semantics version 3
and an explicit clock inside their immutable identity. Replay and screening/shadow
share delay/expiry; live snapshots require actual request/receipt times. Old version-2
hashes remain exact. Session versions cannot qualify, activate or reserve entry risk;
normal scan acquisition and durable session decision scheduling remain pending.
