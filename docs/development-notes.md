# Development and debugging handoff

Updated **2026-09-19**. Read [CLAUDE.md](../CLAUDE.md), [operations](production.md)
and the [architecture review](architecture-review.md). Detailed workflow contracts
live in [durable execution](durable-execution.md), [accounting](account-ledger.md)
and [operational monitoring](operational-monitoring.md).

Read the [forecast-to-fill review](forecast-to-fill-review.md) before extending joint
allocation. Legacy scheduled retuning is now retired. The review records reproduced shadow/legacy research
defects and the PR #52 recent-SIP entitlement/deadline findings. Ordered remediation
lives in the [alpha roadmap](alpha-roadmap.md#current-priorities-after-the-whole-stack-survey); the current
allocator has no execution authority.

The [whole-stack survey](alpha-stack-survey-2026-09-18.md) records newly confirmed,
drawdown wiring and legacy risk-report defects (fixed by the first account-risk
tranche), inadequate measured qualification power, and the still-missing portfolio
deployment lane. Zero active mined
alphas does not disable built-in screeners or prove that rejection gates are calibrated.

The next risk tranche adds [broker entry capacity](entry-capacity.md), retaining one
FIFO, journal and lock order. Tests include real SDK HTTP evidence and independent
PostgreSQL writers, including stale evidence after lock waits. Funding, borrowing,
protected exposure and planned stop-risk budgets are checked before POST. The
[power diagnosis](alpha-power-diagnosis-2026-09-18.md) records all 800 completed
searches and the pending-order/forecast mismatch; no gate change or promotion follows.

Alpha closure/clock checks now live in `market/bars.py`, shared by research,
screening and shadow. Fixed-duration versions reject explicit session/unknown
layouts and ambiguous timestamps. [New session versions](alpha-session-decisions.md)
share receipt/delay/expiry selection between replay, screening and shadow. The dedicated
forward worker now acquires raw minutes and persists decisions through the same journal;
native scans remain separate and execution/qualification evidence is still required.
See [replay contracts](alpha-session-replay.md#clock-isolation-before-live-migration).

## Runtime ownership

The installed checkout runs `com.agentictrader.copilot` under launchd. Its shell
sources `.envrc` and runs `uv run copilot daemon`. The desk uses **Alpaca paper**,
PostgreSQL 17.11 (Homebrew) on localhost and one Telegram poller. The historical
`data/signals.db` SQLite file is not the active database. Compose's declared image
version does not establish the installed local server version.

| Task | Schedule |
| --- | --- |
| Swing scan | Every 4 hours from startup, immediate first run, no timeframe filter |
| Intraday scan | Every 15 minutes, session gated, `15m` filter |
| Position reconciliation | Every minute plus broker-stream wakeups |
| Account activity reconciliation | Independent worker, default 60 seconds |
| Entry / notification workers | Independent loops, default 2 seconds |
| Macro briefing | Weekdays 12:30, scheduler/system timezone |
| External watchdog | Every 60 seconds: process check, readiness incidents and due compaction |
| Session candidate decisions | 30-second wall-clock polls; fixed SPY/QQQ diagnostic controls, durable per-candle claims; no promotion credit |
| Session data observer | 30-second wall-clock polls; configured SPY/QQQ 15m captures only within three minutes of observed closes; no scoring/trading |
| Alpha miner | Saturday 03:00 local launchd time; ETF32, 9 genetic + 7 catalog trials/symbol; no promotion. Manual `alpha mine --universe snapshot` supports a verified capped equity cohort, and `--universe screen` consumes a completed source-specific liquidity cohort. |

Trading scan intervals are not candle-close aligned. The independent
[forward observer](alpha-forward-observations.md) samples around observed session-bar
closes; it does not migrate the strategy clock. Cron logs mentioning UTC do not change
the scheduler timezone. Market sessions use exchange time; DB/log timestamps use
UTC. Configuration loads at construction and requires restart after external edits.
The journal-backed alpha registry reloads atomically between scans. Telegram/metrics initialize before immediate jobs; scheduler
jobs coalesce delays and allow one instance each.

The mining CLI charges each symbol's reserved trial budget before market-data
acquisition. See [the mining-universe contract](alpha-mining-universe.md) for
snapshot identity, caps and failure accounting. This keeps the scheduled ETF
benchmark reproducible while allowing reviewed 32–64-name equity campaigns at
the top of the funnel.

Never start another daemon, `listen` or Compose stack alongside the installed bot.
Use a worktree for supervisor changes. Unload watchdog before daemon for maintenance;
a simple stop can be undone by the watchdog. See the controlled deployment runbook.

## Configuration and isolated development

`load_config()` is the explicit application boundary; importing config loads no
secrets and never changes `os.environ`. Production merges YAML, `.envrc` and
environment values. Explicit environment values, even empty ones, win.
`COPILOT_ENV_FILE=''` disables dotenv. Explicit `environ={...}` does not read local
secrets unless an environment file is also explicitly supplied.

`COPILOT_ENV` selects production/development/test. Nonproduction requires explicit
config and storage. Bare `AppConfig()` cannot select a runtime DB. Load-time DB
precedence is `DB_PATH` → `DATABASE_URL` → explicit `DB_NAME` → YAML → production
default. A constructed config never consults ambient DB variables. `--db-path`
accepts a path or URL; named sandboxes live under `data/<environment>/`. PostgreSQL
has no automatic SQLite failover.

A dry scan uses a temporary SQLite DB and empty PaperBroker portfolio, disables
Telegram and skips broker connection/monitoring. Market data and optional LLM
calls still occur. `test-alert` previews locally; explicit sending requires a
separate test token/chat and creates no signal. Nonproduction messages are labeled.

## Test and review workflow

Pull requests run `pytest-impacted` in branch mode against `origin/main` with
the optional Rust backend from `pytest-impacted[fast]`. Static import analysis
selects the affected tests; changes to workflows or runtime configuration are
explicitly configured to invalidate the full test set. A core module change can
therefore still fan out to the complete dependency closure.

Pushes to `main`, the weekly scheduled workflow, and manual dispatches run the
full suite. This keeps a broad regression signal without making every leaf PR
wait for unrelated tests. PostgreSQL integration remains a separate required
job and is never skipped by the unit-test selection tier.

- Function-scoped autouse isolation removes credentials, selects fixture YAML and
  temporary DB paths. Socket/libcurl guards block external I/O; native psycopg2 and
  SQLAlchemy guards prevent bypassing DB isolation.
- Inject config/storage/providers before construction. Prefer local fixtures for
  shared domain setup and `pytest.mark.parametrize` for meaningful variations.
  Avoid fixtures that share mutable trading state across tests.
- Real SDK tests use marked loopback HTTP/WebSocket. PostgreSQL is explicit:
  `TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_trader uv run pytest tests/integration --run-postgres`.
  Its database must be disposable and named `test_*`; tests migrate/downgrade it.
- Run `uv run pytest` for cross-cutting changes, then
  `uv run pre-commit run --all-files`. Hooks include formatting, lockfile checks,
  mypy and impacted tests. Full transport/PG integration remains required before
  deploying persistence or execution changes.
- Do not disable safety guards or replace integration with permissive mocks. The
  optional in-process SDK adapter does not prove TCP/WebSocket or PG behavior.

## State and evidence

Schema head: `008_alpha_pipeline`.

| Storage | Responsibility |
| --- | --- |
| `signals` | Tracked proposals/lifecycle, entry/exit identities, provenance and quarantine |
| `system_state` | Persistent halt and operational keys |
| `close_requests` | Exclusive durable close intent and retained terminal history |
| `workflow_locks`, `work_items` | Scoped coordination, entry reservations/queue and notification outbox |
| `domain_events` | Versioned workflow/broker/account/incident evidence and health observations |
| `order_projections` | Exact cumulative order views |
| `activity_projections`, `ledger_checkpoints` | Account-bound activity evidence and reconciled reports |
| `incident_projections` | Replayable incident lifecycle plus latest observation watermarks |
| `alpha_projections` | Replayable research, qualification, registry and forecast aggregates |
| `audit_events` | Operational command, fill, valuation, delivery and startup traces |

Queries exclude quarantined and other-environment rows. Historical unknown-mode
signals remain visible until reviewed. Signals use `contract`; new signals persist nullable timeframe, alpha version,
execution policy and decision provenance. Historical rows are not backfilled with inferred metadata. Construction currently checks migrations, including many informational
CLI paths—explicit bootstrap is a documented refactor, not silently assumed done.

`SUBMITTING` precedes broker POST. `EXECUTED` means tracked/accepted; confirmed
complete entry fills set `executed_at`. Exact full exit fills plus conditional SQL
govern tracked closure. Partial account executions appear in the account ledger
without fabricating per-signal ownership. `/positions` is broker-authoritative;
`/perf` separates account and tracked scopes; `/status` is configured risk capital
and tracked exposure. Accepted orders without fills say they await broker fill.

Use `db audit`, `db events`, `db queue`, `db orders`, `db ledger`, `db outbox` and
`db incidents` for investigation. Preserve raw evidence privately. Financial events
and dead letters have no TTL; only redundant old healthy journal observations are
compacted. `db clear` refuses to reset signal IDs while events/work exist. Contamination
repairs use audited quarantine; see the [September 15 incident](incident-2026-09-15.md).

## Async and operator surfaces

Blocking SDK/provider/calculation work is offloaded at boundaries; scans and
reconciliation serialize related shared state. Threads do not cancel synchronous
calls or isolate shared executors. Remaining contention and slow serialized Telegram
handlers are documented in the architecture review. Keep request timeouts and
loop-lag audits when extending these paths.

Telegram has shared transport retries, command/update correlation, polling
observations and persistent delivery audits. Retry HTTP delivery, never handlers.
`/macro` is the single combined market context; published feed dates and missing
enrichment are explicit. The evaluator's LLM macro block now carries the evaluation
time and the deterministic lockout verdict recomputed on the same captured clock as the gate (CLEAR or LOCKOUT ACTIVE)
with full event dates, because an event time alone made the model refuse candidates
on unverifiable macro timing. Tier-1 lockout events are USD CPI, PPI, payrolls and
unemployment, and the FOMC rate decision (statement, projections, press conference);
individual Fed speakers and the minutes are not tier-1. Missing VIX fails regime evaluation; missing enrichment
can leave volatility-only policy. Daily-feed admission age remains a gap.
GEX is an option-chain/model estimate with quality notes and a required real spot. Replay evidence: [LLM prompt evaluation, 2026-09-22](llm-prompt-eval-2026-09-22.md) (old prompt 0/10 approvals on the traced candidates, new prompt 10/10, lockout control 6/6 rejected).

Research outputs do not automatically change running strategy parameters. Legacy
retuning and config exports are removed; use the canonical journal-backed pipeline.
Alpha state now uses the [journal-backed pipeline](alpha-pipeline.md), schema 008.
The [active alpha roadmap](alpha-roadmap.md) owns research priorities. The
[A1b study](alpha-study-2026-09-16.md) retained 1,952 synthetic replicates, including
22 unavailable comparisons; it does not authorize new gates or promotion.
[A2a](alpha-return-timeline.md) corrects execution return coverage with a versioned
validation policy. Its [fresh calibration](alpha-timeline-study-2026-09-16.md)
completed 1,952 jobs without unavailable comparisons; power remains insufficient
and gates stay unchanged. [A2b session replay](alpha-session-replay.md) now provides
observed-calendar/minute diagnostics and durable input/decision/event artifacts.
Live signal-clock migration and broker execution evidence remain before intraday
qualification. Replay is a charged research diagnostic, not synthetic calibration.
CLI, chat and Telegram inspect the same version registry; activation/demotion is
visible between scans. Imported historical definitions remain unqualified shadow
versions. `/alphas` shows generation/versions, not fabricated performance or allocation.
Combined forecast/optimizer targets are shadow-only. Readiness checks current-run
registry acknowledgment; `alpha status` exposes latest research separately.
The [historical review](alpha-stack-review.md) and [implementation evidence](alpha-pipeline-implementation.md)
record the defect reproductions, fixes, tests and remaining empirical gates.

## Verify deployment separately

```bash
launchctl list | rg 'com\.agentictrader\.'
uv run copilot doctor --readiness
uv run python scripts/verify_runtime.py
uv run copilot db incidents
```

Verify clean committed revision/PID/run, current-run component freshness,
authenticated broker stream, actual Telegram poll freshness/menu registration,
account reconciliation and position parity. The verifier sends no messages/orders.
`/healthz` only proves liveness; a separate `getMe` does not prove polling health.
Active `doctor` is CLI-only; `/healthcheck` has been removed.

Keep UTC/run-correlated logs and private verification artifacts separate from Git.
Test results/CI establish source behavior; startup and observation audits establish
deployment evidence. Consult [ranked remaining work](architecture-review.md#remaining-findings-ranked)
instead of appending duplicate historical status reports to this handoff.

The [active alpha roadmap](alpha-roadmap.md) is the canonical long-horizon research queue. Keep its acceptance criteria and progress current; the [September 16 campaign](alpha-research-2026-09-16.md) records the baseline rejection and control evidence.

`alpha benchmark` now evaluates explicit-horizon forecast components, separately from
trading-policy P&L. It retains charged diagnostic artifacts, never promotes, and does
not add forecast statistics to the strategy-Sharpe variance sample. See the
[forecast benchmark contract](alpha-forecast-benchmarks.md) and canonical alpha roadmap.

`alpha benchmark` supports explicit `--label`/`--feature` and optional per-side
`--cost-bps` scenarios. These are charged daily bar-price payoff diagnostics with
no promotion or broker-fill claim; see [timing/cost contracts](alpha-forecast-policy.md).

Benchmark reports now expose fold stability, signed error influence, forecast/action
distributions, cost attribution and fitted-model evidence. These are descriptive
diagnostics, not promotion gates; see [report contracts](alpha-forecast-benchmarks.md#automatic-diagnosis-before-lead-selection).


The [frozen ETF session campaign](alpha-session-campaign-2026-09-17.md) completed all 81 attempts with full execution
coverage. None passed every rule: QQQ momentum's positive cost-stressed returns had
only 11 closed trades. Six predeclared SPY/QQQ hypotheses are designated for diagnostic
forward observation, not activation or qualifying shadow credit. Preserve the failed
triage result; timed order/holding lifetimes are implemented. The continuous and sector-panel campaigns are complete; measured forward/lifecycle evidence continues to accumulate.

## Timed order and holding policies

The [lifetime contract](alpha-trade-lifetimes.md) connects session research to broker execution without adding another queue/schema. Timed definitions use shared pure UTC deadlines, immutable identities and canonical policy parsing. The injected lifecycle service runs in position monitoring; its repository uses the existing trading lock, events and outbox. Pending cancellations block both authorization and submission; exact group confirmation releases the unfilled reservation. Full holding expiry uses stable close IDs, with failed/uncertain outcomes retained. Annualized returns that overflow are unavailable; actual equity/trade evidence remains intact. Existing GTC policies and diagnostic activation gates stay unchanged.

The [continuous timed ETF campaign](alpha-continuous-campaign.md) pairs the new lifetime contract with actual-data discovery: 48 frozen comparisons and a new trend/pullback hypothesis. Replay supports one continuous year with bounded acquisition chunks; trial/holdout accounting and session activation gates remain intact. Report research yield separately from deployment readiness.

The [completed continuous study](alpha-continuous-campaign-2026-09-17.md)
retains 36 complete runs and 12 SPY coverage failures. QQQ momentum has 415 closes
and positive primary-cost returns, but fails cost stress; zero alphas promoted.
The sector-panel path adds explicit [Rank IC/ICIR contracts](alpha-information-coefficient.md).
Existing miner ICIR is overlapping, single-symbol and unannualized.

`copilot alpha panel-study PROTOCOL --output NEW_PRIVATE_DIRECTORY` runs the
[frozen sector-panel research path](alpha-sector-panel.md): observed-calendar
native daily inputs, causal relative features, cross-sectional IC/HAC and five-session
basket/cost diagnostics. All 32 declared comparisons and every member's inspected
interval are journaled before provider access. It sends no orders or notifications,
grants no qualification/registry credit and leaves weekly mining unchanged.

The [completed sector-panel study](alpha-sector-panel-2026-09-17.md) retains
32/32 complete comparisons and zero passes. Volatility-scaled momentum has mean
Rank IC 0.0136 and +3.63% at 1 bp per side, but −4.27% at 5 bp and concentrated
gains. All formulas remain research-only; active alphas remain zero. The [persistent-book follow-up](alpha-persistent-book-2026-09-17.md) is also complete;
use the [current roadmap](alpha-roadmap.md#source-calibration-and-individual-equities--current-ordered-priorities) for next work. [Automatic Alpaca bar evidence](market-data-evidence.md) now retains raw pages and normalization outcomes.

The [matched screened-equity DSL panel](alpha-matched-panel-2026-09-19.md)
extends the same journal-backed workflow with constant, volatility and
benchmark-residual controls, causal turnover, and signal-bar beta diagnostics.
Its complete 64-name full-history attempt failed closed on unavailable historical
members; a separate recent diagnostic completed all 32 comparisons but failed
coverage and sample gates. These results do not qualify or promote an alpha.

## September 17 contract fixes

See [implementation boundaries and residual risks](forecast-contract-hardening.md).
The retuner, optional VectorBT engine and uncalibrated Kelly mode are retired.
Regression tests reproduce timeout, participation, future-label, incompatible-target,
duplicate-family and zero-capacity sizing defects before their fixes.

Source verification for the contract fixes: all-file pre-commit passed; full CI suite
**1,429 passed, 53 skipped**; actual SDK TCP/WebSocket and disposable PostgreSQL suite
**151 passed**. See [SPY forensic evidence](alpha-spy-minute-postmortem-2026-09-17.md)
for the subsequent read-only investigation. Live verification belongs in the delivery
PR and private runtime evidence, separately from these source-test results.

## Automatic market-data evidence

See [capture contracts and limits](market-data-evidence.md). The shared SDK response
observer records pages before typed parsing; production adapters inject a private
evidence store. Preserve context isolation and error references through the existing
journal. `save_json_report` now belongs to `storage.artifacts`; no research-layer
compatibility import remains. Source validation and deployed verification stay separate.

The [persistent ETF book experiment](alpha-persistent-book.md) uses the shared
panel journal and explicit adjusted-price accounting. It is research-only: reserve
the frozen matrix before access; no promotion or live portfolio execution.
[Verified feed access and alternatives](research-data-sources.md) distinguish
recent-SIP entitlement from throttling; historical SIP remains usable.

Source-specific [volume calibration](alpha-volume-calibration.md) uses the shared
daily study harness with frozen training/forward intervals and exact feed identity.
`alpha volume-study` is research-only. `/alphas` now provides a compact explanation
and status summary; use `alpha forward` and `alpha list` for full evidence/definitions.

[Prospective IEX workers](alpha-iex-forward.md) now bind their own exact feed independently
from trading configuration. Mixed-feed registries retain old evidence and expose
uncollected feeds. The IEX control cohort is diagnostic-only; daily volume calibration
is not an intraday calibration and worker readiness is not scoring success.

[Equity universe capture](alpha-equity-universe.md) now has pure membership/sampling
logic, injected SDK/HTTP adapters and an application service over the existing journal.
The actual dated sample is 300 of 6,721 eligible candidates; all 33,509 broker records
remain retained. Identity/receipt/raw-response regressions and independent reconstruction
cover the two attempts. The frozen source-specific liquidity/coverage stage is implemented; see the linked report for actual-run evidence.

`alpha liquidity-study` now applies a frozen daily liquidity/coverage screen to the
complete [dated equity cohort](alpha-equity-universe.md#daily-liquidity-and-coverage-screen).
It reuses the daily study service and diagnostic journal, with bounded paced reads,
immutable member checkpoints and fail-closed selection. IEX activity is source-specific;
this development screen neither establishes historical membership nor qualifies alphas.

[Screened panel forecasts](alpha-panel-forecasts.md) compose a versioned pure computation through `AlphaPanelService`; plan validation binds the complete prior liquidity result, manifest and input evidence. Shared forecast estimator construction serves both single-symbol and panel diagnostics. No additional journal/schema or execution path is introduced. Test causal eligibility, strict label maturity, future perturbations and held/unheld missing outcomes separately from actual data and deployed verification.

The [IEX-native mining smoke test](alpha-iex-mining-2026-09-19.md) confirmed that
source-correct Alpaca research reaches the qualification contract: all eight
tested manifests used raw `alpaca:iex`, yet no finalist passed the existing
statistical/economic gates. Do not loosen production qualification to compensate.
Any paper execution experiment must use a separately tagged, capped and expiring
paper-probe status with no promotion or shadow credit.

Retained-data follow-ups use `alpha forecast-controls` and the shared daily workflow. Its [contract](alpha-forecast-controls.md) requires tests for source/hash tampering, reservation before reads, exact parent forecast reconstruction, unchanged decisions under future-volume perturbations, and unknown paired denominators. Unit fixtures contain synthetic artifacts; integration tests replay the real journal on SQLite and opt-in disposable PostgreSQL without market APIs.


## Power-diagnosis changes (September 18)

`alpha power-plan`/`power-study` are artifact-only synthetic commands. Their CLI
integration test forbids runtime config/storage/provider construction and exercises
real mining, selection checkpoints, measurements and summaries. Fault-injection
regressions preserve completed evidence and prevent validation after development
calculation failures. Trace-on/off tests prove identical execution results. See
[protocol and budget](alpha-power-ablation-plan.md); a test fixture is not the full
scientific experiment. Deployment verification must still be recorded separately.


## Lifetime attribution contract (September 18)

The horizon-alignment follow-up now has a shared `elapsed_utc_v2` policy with
independently disabled entry/holding deadlines, an explicit diagnostic
`fixed_daily_v1` clock, and a pure paired P0/P1/P2 replay. `alpha lifetime-plan`
freezes its bounded synthetic protocol; `alpha lifetime-study` retains selection
checkpoints and per-variant traces through the existing artifact lifecycle. The
runner reuses the current miner and bracket simulator, never reselects under a
counterfactual, and constructs no runtime DB, provider, broker or notifier. No
protocol has been run for production evidence and no result can authorize promotion.

## Factor-controls verification

The [factor comparison](alpha-factor-controls.md) uses RED-first causal feature and
protocol tests, existing forecast-controls regression checks, and real retained-file
SQLite/PostgreSQL integration. The shared fixture preserves guarded disposable DBs.
Integration covers pre-read charging/exclusion, immutable parent data, full 126-row
fits, unknown held outcomes, replay and no activation. Full historical results and
service deployment checks remain separate from these synthetic mechanical fixtures.

## Native-daily panel campaign

The [daily panel](alpha-daily-panel.md) runs four frozen diagnostic models on 64
equities and nine ETF proxies, inside the existing daemon. Collection starts at
00:30 New York time after each decision day. The existing alpha journal owns
exactly-once enrollment, exclusive claims, late-result retention and atomic residual
state; outcomes mature after 20 sessions. No extra poller, schema or order route.

Its independent baseline companion reuses one capture/fit/outcome acquisition and
charges three additional hypotheses. Pin exact comparison protocols at decision
claim time; never use current configuration to reinterpret a pending outcome.
Removing an enrollment path preserves both future eligible claims and old outcomes.
Keep primary status separate, including when only baselines score. Failed artifact
writes cannot publish partial comparison successes or advance the residual pointer.
