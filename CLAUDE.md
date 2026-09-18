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
| Entry/close application services | `execution/entries.py`, `admission.py`, `capacity.py`, `closing.py` |
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
   new risk and recover by exact client ID, including after a 404. Alpaca admission
   requires fresh journaled account drawdown; every tier shares its cap. Final
   submission pins the risk fingerprint and rechecks the lease after ledger locking. Rejected/unfilled
   terminal evidence releases capacity; acceptance is not a fill.
2. **Closure:** `PositionCloseService` persists exclusive intent, confirms exact
   order-group cancellation, revalidates position/session and submits once.
   `/flatten` previews by default and preserves halt state. Partial/ambiguous
   ownership remains blocked; exact full fills and conditional SQL govern closure.
   Direct replies and durable notices retain refusal reasons. Closed-session refusals
   show the broker's next regular open in UTC; a cancellation attempt, even with a
   lost acknowledgement, must warn that protection may have been removed.
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

The [active alpha roadmap](docs/alpha-roadmap.md#current-priorities-after-the-whole-stack-survey) owns the ordered long-horizon plan. Read the [September 18 whole-stack survey](docs/alpha-stack-survey-2026-09-18.md) before further risk or mining changes. Account drawdown and [broker entry capacity](docs/entry-capacity.md) now share the existing FIFO/ledger boundaries: funding, borrowing, exact protection, observed-equity sizing and aggregate planned stop risk are rechecked before submission. Pre-reservation research acquisition, [gate-power diagnosis](docs/alpha-power-ablation-plan.md) and measured portfolio volatility/tail constraints remain research priorities. The [800-search result](docs/alpha-power-diagnosis-2026-09-18.md) identifies GTC entry conversion and joint-gate power losses; distinguish it from the separately delivered [factor controls](docs/alpha-factor-research-plan.md). The convex allocator remains shadow-only. Update milestones and evidence when completing work.

The [forecast-to-fill review](docs/forecast-to-fill-review.md) separates production
candidate arbitration from shadow allocation. [Contract fixes](docs/forecast-contract-hardening.md)
now bound traded notional, reject pending inventory, require matching forecast/risk
units, enforce calibration outcome availability and persist solver evidence. Legacy
retuning/config exports and uncalibrated Kelly sizing are removed. Provider reads use
bounded shared capacity and socket deadlines; doctor separates feed access from freshness.
Recent-SIP entitlement, dependence-aware experiments and
executable target plans remain priorities. Never connect shadow weights directly to orders.

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
data cannot establish point-in-time availability or actual partial fills. Durable
session acquisition/scheduling is implemented; actual feed and broker execution
evidence remains necessary.
Existing fixed-duration research/screening/shadow paths share the market clock
guard and reject explicit session/unknown layouts or ambiguous timestamps. Do not
silently reinterpret an old alpha version as using session-derived bars.
Preserve historical study evidence and use fresh validation after policy changes.

A2b's [prospective data observer](docs/alpha-forward-observations.md) runs in the
existing daemon, independently of scans/halt state. The desk observes SPY/QQQ 15m on
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
the dedicated diagnostic worker now owns receipt-aware acquisition and durable session
decisions; native strategy scans retain their own clock.

`alpha benchmark` now evaluates explicit-horizon forecast components, separately from
trading-policy P&L. It retains charged diagnostic artifacts, never promotes, and does
not add forecast statistics to the strategy-Sharpe variance sample. See the
[forecast benchmark contract](docs/alpha-forecast-benchmarks.md) and canonical alpha roadmap.

`alpha benchmark` supports explicit `--label`/`--feature` and optional per-side
`--cost-bps` scenarios. These are charged daily bar-price payoff diagnostics with
no promotion or broker-fill claim; see [timing/cost contracts](docs/alpha-forecast-policy.md).

The [open-gap timing/cost follow-up](docs/alpha-open-gap-policy-2026-09-17.md)
completed 20 attempts and failed its frozen trading criterion. Keep this standalone
long/cash policy paused. Subsequent ETF campaigns are recorded below; measured
forward/execution evidence and the current roadmap govern further work.

The [open-gap postmortem](docs/alpha-open-gap-postmortem-2026-09-17.md) reproduced
calculations but found outlier-concentrated forecast gains and unstable selection.
Fold/influence/action diagnostics now accompany every new benchmark; a rejected
experiment alone is not evidence of a calibrated or bug-free pipeline.

Benchmark reports now expose fold stability, signed error influence, forecast/action
distributions, cost attribution and fitted-model evidence. These are descriptive
diagnostics, not promotion gates; see [report contracts](docs/alpha-forecast-benchmarks.md#automatic-diagnosis-before-lead-selection).


The [frozen ETF session campaign](docs/alpha-session-campaign-2026-09-17.md) completed all 81 attempts with full execution
coverage. None passed every rule: QQQ momentum's positive cost-stressed returns had
only 11 closed trades. Six predeclared SPY/QQQ hypotheses are designated for diagnostic
forward observation, not activation or qualifying shadow credit. Preserve the failed
triage result; timed order/holding lifetimes are implemented. The continuous and sector-panel campaigns are complete; measured forward/lifecycle evidence continues to accumulate.

Inspect `copilot alpha forward --days 7` or `/alphas` for the [shared forward evidence report](docs/alpha-forward-evidence.md). It counts canonical outcomes and measured receipts, exposes gaps/truncation, and grants no qualification credit. Recorded-score fractions are not complete calendar coverage; readiness remains a separate freshness check.

## Timed alpha execution

Read [trade lifetimes](docs/alpha-trade-lifetimes.md) before changing expiry. Pure elapsed-UTC deadlines are shared by replay and the injected `TradeLifetimeService`; optional timed policies create new immutable version-3 identities. Existing policy hashes/positions are unchanged. Entry cancellation persists one `entry_cancel` work item before DELETE, retains risk and blocks fresh admission until exact group confirmation, and recovers by lookup only. Holding expiry reuses `PositionCloseService` with a stable request ID, including failed attempts. Partial/uncertain evidence halts new risk; `/resume` cannot waive it. Journal/outbox writes are atomic; readiness includes `entry_cancellation`. No new schema/queue, automatic promotion, or live qualification claim.

The [continuous timed ETF campaign](docs/alpha-continuous-campaign.md) pairs the new lifetime contract with actual-data discovery: 48 frozen comparisons and a new trend/pullback hypothesis. Replay supports one continuous year with bounded acquisition chunks; trial/holdout accounting and session activation gates remain intact. Report research yield separately from deployment readiness.

The [completed continuous study](docs/alpha-continuous-campaign-2026-09-17.md)
retains 36 complete runs and 12 SPY coverage failures. QQQ momentum has 415 closes
and positive primary-cost returns, but fails cost stress; zero alphas promoted.
The sector-panel path adds explicit [Rank IC/ICIR contracts](docs/alpha-information-coefficient.md).
Existing miner ICIR is overlapping, single-symbol and unannualized.

`copilot alpha panel-study PROTOCOL --output NEW_PRIVATE_DIRECTORY` runs the
[frozen sector-panel research path](docs/alpha-sector-panel.md): observed-calendar
native daily inputs, causal relative features, cross-sectional IC/HAC and five-session
basket/cost diagnostics. All 32 declared comparisons and every member's inspected
interval are journaled before provider access. It sends no orders or notifications,
grants no qualification/registry credit and leaves weekly mining unchanged.

The [completed sector-panel study](docs/alpha-sector-panel-2026-09-17.md) retains
32/32 complete comparisons and zero passes. Volatility-scaled momentum has mean
Rank IC 0.0136 and +3.63% at 1 bp per side, but −4.27% at 5 bp and concentrated
gains. All formulas remain research-only; active alphas remain zero. The [persistent-book follow-up](docs/alpha-persistent-book-2026-09-17.md) is also complete;
use the [current roadmap](docs/alpha-roadmap.md#source-calibration-and-individual-equities--current-ordered-priorities) for next work. [Automatic Alpaca bar evidence](docs/market-data-evidence.md) now retains raw pages and normalization outcomes.

The [persistent ETF book experiment](docs/alpha-persistent-book.md) uses the shared
panel journal and explicit adjusted-price accounting. It is research-only: reserve
the frozen matrix before access; no promotion or live portfolio execution.
[Verified feed access and alternatives](docs/research-data-sources.md) distinguish
recent-SIP entitlement from throttling; historical SIP remains usable.

Source-specific [volume calibration](docs/alpha-volume-calibration.md) uses the shared
daily study harness with frozen training/forward intervals and exact feed identity.
`alpha volume-study` is research-only. `/alphas` now provides a compact explanation
and status summary; use `alpha forward` and `alpha list` for full evidence/definitions.

Prospective research workers have explicit `alpha_pipeline.{observations,decisions}.feed`
settings, independent of trading data. Desk IEX collection and the separately identified
control cohort are documented in [IEX forward evidence](docs/alpha-iex-forward.md).
Keep original SIP failures/identities; other-feed candidates remain visible but uncollected.

[Prospective equity candidates](docs/alpha-equity-universe.md) are captured by
`alpha universe-snapshot`: exact UUIDs, raw metadata, exclusions, receipt fences and
stable-hash selection use the existing diagnostic journal. The actual 300-name
cohort is not a historical constituent list or a verified common-stock/liquidity universe.
Use its snapshot ID and preceding-only screening before any broader forecast study.

`alpha liquidity-study` now applies a frozen daily liquidity/coverage screen to the
complete [dated equity cohort](docs/alpha-equity-universe.md#daily-liquidity-and-coverage-screen).
It reuses the daily study service and diagnostic journal, with bounded paced reads,
immutable member checkpoints and fail-closed selection. IEX activity is source-specific;
this development screen neither establishes historical membership nor qualifies alphas.

[Screened panel forecasts](docs/alpha-panel-forecasts.md) reuse the daily acquisition/journal service. Keep all frozen members on the exchange clock, derive eligibility only from past bars, and train Ridge only on strictly mature labels with training-only scaling. Freeze weights before inspecting outcomes; missing held outcomes withhold the full curve. The old complete-panel and production qualification contracts remain unchanged. No active alpha or calibrated promotion power is implied by a completed diagnostic.

[Retained forecast controls](docs/alpha-forecast-controls.md) reuse the same daily workflow and payoff kernels. Charge and exclude all parent members before reading artifacts; preserve source hashes and original receipt times. Strict endpoint volume masks outcomes only. Never change decisions based on future availability, refit the frozen parent, compound paired return differences, or present long-only context as matched market-neutral risk.


`alpha power-plan`/`power-study` reuse the synthetic artifact lifecycle. Keep paired
known/winner routes, frozen bootstrap and sourced count/variance scenarios. Persist
selection before holdout; mechanical development failures must leave validation
unexamined. Measurement reuse changes no valid qualification thresholds. Missing or
nonfinite sampling evidence is unavailable and fails closed. Synthetic diagnostics
cannot authorize promotion, journal production trials or earn shadow credit.
