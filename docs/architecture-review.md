---
layout: default
title: Architecture review — September 2026
---

# Architecture review — September 16, 2026

## Remediation status — September 17

[Contract hardening](forecast-contract-hardening.md) fixes trade participation,
calibration boundaries, incompatible forecast/risk units, uncertainty use and cloned
family weighting. It adds immutable solve diagnostics, bounded provider reads and
an exact-feed access probe. The divergent retuner and uncalibrated Kelly mode are
retired; a further minimum-size/hard-gate defect is fixed. The findings below remain
the original review evidence. Executable plans and empirical profitability remain open.

## Assessment

The central architecture is sound for an operator-approved paper desk: application
services coordinate business transitions, broker adapters own remote contracts,
repositories own transactional concurrency, and one outbox owns durable delivery.
The main structural debt is still `TradingCopilot`, which combines composition,
orchestration, reporting and research. Avoid adding new responsibilities there.

The subsequent [alpha-stack review](alpha-stack-review.md) records the mining,
DSL, validation, promotion and portfolio-construction findings and stress tests.
The [alpha pipeline](alpha-pipeline.md) implements its causal, shared-policy, journal and
qualification foundations. Remaining empirical/portfolio gates are tracked there.
The historical priority was to repair research/live parity, causality, confidence-statistic and promotion
gaps before increasing search volume or connecting optimized weights to trading.
The previously ranked work below remains deferred, not completed or superseded.

The [September 17 forecast-to-fill comparison](forecast-to-fill-review.md) reviews
the supplied optimization tutorial against the actual production and shadow paths.
It reproduces a shadow participation-limit defect and a separate legacy retuner
accounting error, and defines target/horizon, uncertainty and execution-plan gaps.
Its ordered follow-up is in the [canonical roadmap](alpha-roadmap.md#forecast-to-fill-follow-up).

## Boundaries and mechanisms

| Concern | Mechanism and reason |
| --- | --- |
| Entry admission | Durable FIFO, atomic reservations and fenced preflight leases. A committed submission never expires/replays; exact client-ID lookup resolves uncertainty. |
| Closing | Per-symbol exclusive intent plus broker cancellation/revalidation. Its lifecycle differs from entry admission; sharing broker mutations through a generic retry queue would be unsafe. |
| Broker evidence | Versioned `domain_events`, exact identities, replayable order/activity projections. Account reconciliation uses Decimal and broker cost basis, without guessed tax lots. |
| Operator traces | `audit_events` records commands, transport outcomes, valuations and startup identity. This complements replayable domain facts; it is not a second execution state machine. |
| Critical notifications | Transactional outbox, fenced claims, bounded retries and preserved dead letters. HTTP retries handle individual delivery attempts; the outbox survives process failure. Neither repeats a trading action. |
| Health | One passive `/readyz` contract; external watchdog persists debounced incidents and uses the same journal/outbox. Active `doctor` probes remain CLI-only. |
| Retention | Bounded compaction of redundant old healthy observations, preserving failures/recovery boundaries/latest observations and all financial evidence. |
| Data resilience | Explicit market-data/calendar provider fallbacks. Broker valuations and fills have no alternative-feed/zero fallback. SQLite is explicit test storage, never runtime DB failover. |

Application services receive dependencies. Short DB transactions never span remote
calls. Protocol/SDK conversion belongs in adapters; policy belongs in validated
config; rendering belongs in presentation/transport modules. No Redis/pubsub or
second job infrastructure is justified for the current single-destination outbox.

## Changes from this review

- Removed active `/healthcheck` HTTP diagnostics. It could run migrations and
  external calls and expose connection details. `/readyz` is passive; active CLI
  diagnostics redact DB destinations and Telegram identities/errors. The Finnhub
  probe now calls its exchange-holiday provider instead of mislabeling a ForexFactory
  lookup as authenticated Finnhub success.
- Added an external operational monitor with a pure incident state machine,
  transactional projection/journal/outbox writes, stale-observation fencing and
  delivery-aware reminders. Replay never resends a notification.
- Added bounded healthy-observation compaction with independent maintenance locking.
  Financial evidence, notification deduplication records and dead letters stay intact.
- Removed duplicated report field aliases/post-init copying, a cointegration alias,
  an economic-calendar alias, research metrics alias, unused broker forwarding method
  and a screener type re-export. Callers now use canonical fields/modules; collection
  fields use dataclass factories. Deterministic levels are a dataclass, not a tuple
  with duplicate attributes. Removed fixed leverage wording and the unused sizing
  wrapper that substituted $100; parametrized sizing tests now exercise real prices
  and notional caps.
- Active `doctor` no longer constructs the whole copilot. Supervisor DB construction
  runs off the asyncio loop. No generic retry wrapper was introduced around trades.
- Consolidated current docs and distinguished historical designs from present behavior.
  Live status risk capital, account performance, research returns and broker cash
  have different meanings and are now described explicitly.

## Remaining findings, ranked

| Priority | Finding and use case | Recommendation / tradeoff |
| --- | --- | --- |
| P1 | Current entitlement rejects recent SIP; operator selected separate IEX evaluation. | PR #54 fixed read deadlines/offloading and added the exact-feed probe. Worker health is not data completeness; changing SIP to IEX requires a separately validated data contract. |
| Resolved in PR #54 | The legacy retuner overcounted unrealized P&L. | Retired its engine, scheduler, commands and config-export path. The canonical alpha/panel studies and broker P&L never used this engine; preserve the [counterexample](forecast-to-fill-review.md#f7--a-legacy-simulator-still-diverges-from-the-hardened-research-path). |
| P1 before allocation execution | PR #54 fixed shadow trade-participation and forecast/risk/uncertainty contracts; executable target plans remain absent. | Validate persistent cost-aware portfolios in research. Keep execution gated on rounding, pending exposure, partial fills and protection ownership. |
| P1 | Corporate actions or stock transfers make account reconciliation unsupported; per-signal partial exits remain conservative. | Add exact typed activity semantics and replay fixtures, then explicit ownership/allocation/protection models. Keep totals withheld and sliced trading disabled until complete; never assign by symbol. |
| P1 | Missing macro enrichment permits volatility-only evaluation; daily feeds have no maximum-age admission policy. The economic-calendar fetch can also turn a provider error into an empty event list. | Define per-feed age/calendar policy and fail/size decisions before increasing automation. Strict gates improve safety but can block valid trades on provider holidays/outages. |
| P1 | Live trailing uses recorded risk distance, while research supports ATR/high-water marks; replacement uncertainty has limited durable requested/acknowledged modeling. | Unify policy inputs and persistent stop intent, retaining exact broker confirmation and thesis/risk. Avoid changing existing protective orders during an incidental refactor. |
| P2 | `TradingCopilot` still constructs several services and returns some transport-specific strings. Constructors run migrations. | Extract composition/bootstrap and typed report/reconciliation services incrementally. Explicit migration startup needs coordinated changes to every CLI/daemon path; do not leave a compatibility fallback. |
| P2 | Concurrent explicit account imports safely fence older tokens, but the losing caller/worker reports a superseded refresh. | Runtime verification now passively reads the daemon ledger; consider coalesced importer ownership for concurrent CLI `perf` requests. Preserve account binding, stale-write fencing and failure visibility. |
| P2 | Heavy research shares executor capacity with trading I/O; Telegram handlers serialize. | Add a bounded research job service with cancellation/status and separate capacity. Do not enable blanket concurrent trade handlers. |
| Resolved in alpha pipeline | Timeframe filtering formerly followed conflict resolution and signal rows lacked timeframe/version. | Filtering now precedes arbitration; schema 008 persists optional timeframe/version/policy/provenance. Historical rows remain unknown. Regression and replay evidence live in [alpha implementation](alpha-pipeline-implementation.md). |
| Resolved foundation; portfolio execution gated | Schema 008 replaces YAML ownership with journal-backed immutable versions and CAS activation, installed between scans. | Forecast combination and constrained portfolios stay shadow-only until partial-fill attribution, plan revalidation and protection ownership are implemented. |
| P2 | Monitoring depends on the same host/DB/Telegram destination; local files and audit/financial history still grow. | Add an independent external alert destination and backup/restore/log-rotation policy. Archive durable evidence only with tested replay and deduplication preservation. |
| P3 | Some symbol/contract aliases and dictionary/SDK shape handling remain; older tests can exercise implicit provider failure paths. | Consolidate typed boundary models when touching those services and use explicit provider fixtures. Preserve SDK transport contracts, not unnecessary internal aliases. |

## Testing and review evidence

Safety fixtures are function-scoped; credentials and native network/DB guards
apply before collection and per test. Parametrized tests exercise state transitions,
races, recovery, unknown submissions, order ownership and projection replay.
Integration uses the actual Alpaca SDK over loopback HTTP/WebSocket plus disposable
PostgreSQL. New monitoring tests exercise passive HTTP → incident transaction →
Telegram SDK HTTP delivery, including permanent failure/dead letters; PostgreSQL
checks independent observers and maintenance/admission locks.

Source tests do not prove deployed broker or Telegram freshness. Use the current
run/revision verifier, `/readyz`, account reconciliation and actual poll metrics
following a controlled single-daemon restart. See [operations](production.md),
[monitoring](operational-monitoring.md) and [Alpaca coverage](alpaca-integration-review.md).


## Alpha pipeline architecture follow-up

The implementation reuses application services, pure domain functions, the existing
journal, transactional projections and execution FIFO. No second notification bus,
order queue or runtime YAML fallback was added. Scientific runs/qualification and
operational registry acknowledgments are distinct evidence. New DSL/config contracts
are validated; simulation assumptions are explicit and fail-closed gates cover
missing, stale, mismatched and reused data.

Next priorities: session-correct fine-bar intraday execution replay (intraday
promotion is blocked meanwhile); deployment-feed shadow/forward evidence and paper fill review;
point-in-time eligibility and leave-cohort-out validation; semantic expression
clustering; daily decay/capacity reports; versioned portfolio rebalance plans with
post-rounding/partial-fill risk and protection attribution. Preserve the other
operational priorities above. Do not enable RL or shared-position alpha execution
merely because the new infrastructure passes regression tests.

The ordered research work queue now lives in [alpha-roadmap.md](alpha-roadmap.md), including calibration before broader search. This document continues to own the operational priorities above.

[A1b evidence](alpha-study-2026-09-16.md) identified feature-dependent return
coverage: flat periods with undefined scores were dropped from simulation
statistics, as were some pending orders' same-bar exit losses. Implemented
[A2a](alpha-return-timeline.md) defines the execution clock separately
from feature availability, with compatible variance samples and policy fencing.
The [fresh study](alpha-timeline-study-2026-09-16.md) has no unavailable comparisons
and passes null-search criteria, but positive-control power remains insufficient.
The [A2b replay foundation](alpha-session-replay.md) now shares one bracket engine
between coarse and minute clocks, consumes observed calendars and fails closed on
missing minutes. Existing journal/artifact mechanisms retain all real-data attempts
and event traces. New [session decision versions](alpha-session-decisions.md) now share receipt-aware
screening/shadow and replay delay/expiry. Bounded acquisition and durable session
decisions now run through the same journal; measured publication/broker execution
evidence remains. A3 objective alignment continues.
Preserve the original incomplete study and gates;
do not treat missing comparisons as null rejections or loosen trade-count requirements.

The [A2b forward observer](alpha-forward-observations.md) reuses the replay adapter,
session-window clock, journal and artifact format. It lives outside `TradingCopilot`
and owns dedicated SDK readers, with blocking work offloaded and orderly shutdown.
No new schema, execution queue or notification mechanism was added. It measures REST
receipt/revision evidence only; the dedicated diagnostic decision worker now adds
receipt-aware scoring and durable cursors. Execution observations and research executor
capacity remain concerns.

## Alpha research boundary update — September 17

Forecast-component evaluation now uses an explicit target and purged folds, separate
from trade simulation. The application service injects the existing alpha repository;
no new schema, queue or deployment authority was introduced. Plans, trial charges,
exposure intervals and diagnostic artifact hashes use the existing journal.
See [current contract](alpha-forecast-benchmarks.md). Remaining target-aware pooling,
complete discovery exposure fencing, job recovery/checkpoints, panel data and modern
search comparisons are ranked in the [canonical alpha roadmap](alpha-roadmap.md#funnel-expansion-ranked-experiments-september-17-review).

The [forecast timing/cost screen](alpha-forecast-policy.md) extends the same benchmark
service with explicit outcome endpoints and a pure stateless daily payoff evaluator.
It does not replace the shared bracket execution engine or broker services. New
policy variants are charged and arrays remain private. Actual auction/quote execution
coverage remains a roadmap prerequisite; the A2b durable worker is implemented.

The [frozen open-gap follow-up](alpha-open-gap-policy-2026-09-17.md) completed all
20 attempts but failed its trading criterion. Prediction accuracy alone did not
justify an executable strategy. The following increments added durable decisions and a bounded ETF campaign; actual
forward evidence, lifecycle semantics and aligned panels remain. No qualification bypass
is justified by these results.


## Session worker and campaign architecture review — September 17

The decision worker injects repository, read-only source, policy and clock, reusing
shared bar/score contracts and the alpha journal. Cursor CAS, immutable claims and
late-result fencing add no execution bus/schema. The daemon shares polling/lifecycle
configuration and drains SDK reads on shutdown. Native scans do not duplicate session
candidate diagnostics. No compatibility fallback or credentialed test path was added.

The [ETF campaign](alpha-session-campaign-2026-09-17.md) uses a bounded research runner
composed over `AlphaReplayService`, with one frozen snapshot per cohort. It does not
introduce another engine, registry, qualification rule or service/poller. Independent
fill/fee arithmetic matched every run. Tests include actual SDK/TCP/PostgreSQL replay.

Remaining high-value issues: explicit versioned GTC entry lifetime/holding horizon
with equivalent broker cancellation/recovery; adequate continuous sample length;
measured forward delay/completeness; causal panel alignment; portfolio attribution for
correlated alphas. Snapshot batching for the bounded diagnostic worker is justified
only if measured latency requires it. Research executor/checkpoint and artifact/log
capacity planning remain open. See the [ordered roadmap](alpha-roadmap.md#next-work-after-the-etf-session-campaign).

## Forward evidence reporting boundary — September 17

The [CLI/Telegram report](alpha-forward-evidence.md) shares one injected application query and pure builder over existing projections. SQL is bounded; decoding/statistics run off the event loop. Registry generation fencing rejects mixed cohorts. Readiness retains its lightweight query. No schema, queue, broker client, compatibility shim or business-event write was added.

Accepted limit: the denominator is recorded decisions. Current cursor gaps are visible, but this is not an exhaustive historical expected-window census or historical as-of reconstruction. Add calendar/enrollment interval reconstruction if operator decisions require full coverage accounting; never relabel the present fraction as that metric. Actual forward sampling and the versioned execution-horizon contract remain next on the roadmap.

## Trade lifetime boundary — September 17

[Timed execution](alpha-trade-lifetimes.md) now uses a pure immutable deadline value object, injected application service and repository over existing workflow tables. Research references the pure execution policy; execution does not import research. The execution package no longer eagerly imports broker engines, preventing an import cycle. Cancellation, admission, journal projections and notifications share the trading lock/transaction; no network call holds that transaction. Holding close uses the canonical close service and deterministic command identity.

Accepted limits: partial fills/replacements require operator review; OHLC fill times and instantaneous simulated cancellation are assumptions; monitoring/market hours bound actual response time. Production exchange latency and protective behavior still require prospective evidence before session promotion. Research worker isolation/checkpoints, untouched confirmation, causal panel alignment and per-signal portfolio attribution remain on the [roadmap](alpha-roadmap.md#next-work-after-the-etf-session-campaign).

## Continuous research acquisition — September 17

The [longer ETF study](alpha-continuous-campaign.md) extends the shared read-only
source with bounded disjoint acquisitions, then invokes the existing aggregation and
execution state machine once. No second simulator, research queue, schema or promotion
path is added. Actual SDK/PostgreSQL tests cover pending/held positions across chunks,
DST boundaries and retained failures. The explicit 366-date replay bound caps memory;
streaming checkpoints, worker isolation and long-term artifact capacity remain open.

The [completed campaign](alpha-continuous-campaign-2026-09-17.md) has sufficient
QQQ trade counts but fails cost stress; SPY has an unresolved raw/normalized coverage
gap. The next panel increment should reuse explicit forecast targets, artifact
persistence and cumulative trials. [IC review](alpha-information-coefficient.md)
identifies overlapping time-series ICIR, gap/fold compression and numeric unavailable
fallbacks as measurement debt; replace through a versioned contract, not aliases or
retroactive qualification. Keep P&L/cost and statistical evidence separate.

## Sector-panel measurement boundary — September 17

The [panel increment](alpha-sector-panel.md) reuses the observed-calendar SDK adapter,
causal DSL/forecast targets, pure panel operators and diagnostic journal/artifacts.
An injected service owns acquisition/offloading; no new schema, execution queue,
notification path or live strategy authority is added. Rank IC observations carry
explicit target/axis/policy and retain unavailable dates. HAC runs per fold rather
than stitching windows or inventing effective sample sizes. Basket proxies have
explicit gross/net weights, ties and entry/exit notional costs; native bar labels
are distinct from assumed availability and cannot masquerade as broker fill times.

At panel-study delivery, inputs were normalized rather than raw responses. Automatic
[bar evidence](market-data-evidence.md) now closes that provenance gap for new Alpaca
acquisitions; original historical snapshots remain unchanged. Remaining limits: publication/auction
assumptions; no intrahorizon marking/protection, borrow or corporate-action accounting;
curated rather than point-in-time membership; uncheckpointed CPU work and legacy miner
IC migration/calibration. A research screen cannot bypass existing qualification or
single-owner execution. Preserve these limits when evaluating any apparent winner.

The [completed panel audit](alpha-sector-panel-2026-09-17.md) found full native daily
coverage and independently matching arithmetic, but zero passes. The shared data boundary
now retains provider/normalization evidence. Next address explicit total-return,
turnover and execution assumptions for a new bounded study. Preserve the distinction
between a pure payoff diagnostic and a broker-capable strategy; a different horizon or
cost model must not become an undocumented shim to rescue a failed hypothesis.

### Persistent ETF book boundary and source evaluation

The [frozen book study](alpha-persistent-book.md) reuses `AlphaPanelService`, the
existing journal, acquisition evidence, DSL/panel operators and IC reports. Its
pure cash/inventory book models retained targets; it does not replace the shared
bracket simulator or add live execution authority. Separate SIP/IEX plans preserve
feed identity and charge all comparisons before access. Minimum-variance rank-score
blending models redundancy only; learned expected-return combinations remain open.

Follow-up: independent corporate-action/total-return reconciliation, real borrow and
quote/fill costs, prospective IEX availability if historical evidence warrants it,
physical-share/partial-fill protection, and source/calibration identity in executable
plans. Do not infer those contracts from adjusted-price research results. Retain the
existing artifact archival/resource isolation/checkpoint priorities.

The [completed SIP/IEX book audit](alpha-persistent-book-2026-09-17.md) confirms
accounting consistency but no passing alpha. Covariance evidence now pins ordered
factor axes independently of JSON object-key order. Original IEX missing warmup
is retained as a failed acquisition; a separately charged coverage follow-up
completed. No automatic truncation, imputation or alternate-feed substitution was
added. Prospective IEX timing and broader expected-return benchmarks are next on
the [alpha roadmap](alpha-roadmap.md#persistent-book-delivery-result).


### Source calibration and broader discovery (September 17)

[Daily volume profiles](alpha-volume-calibration.md) add a pure source-bound transform
and injected study computation to the existing panel workflow. The shared
`DailyStudyPlan` protocol expresses acquisition/accounting requirements; studies do
not inherit irrelevant portfolio fields. No new schema, queue, feed fallback or
execution authority. `/alphas` aggregates the existing evidence report without
changing its query/count semantics. Full details remain in CLI.

The [current alpha roadmap](alpha-roadmap.md#source-calibration-and-individual-equities--current-ordered-priorities)
records individual-equity universe expansion, point-in-time membership/delisting
contracts, bounded broader-panel resources, intraday volume seasonality, prospective
IEX receipts and exact calibration/plan execution identity. These are unfinished;
source normalization alone creates neither predictive edge nor paper eligibility.

Read-only forward audit during PR #58: 78/78 current session evaluations were
unavailable, each with retained HTTP 403 evidence. [Explicit per-worker research feeds](alpha-iex-forward.md) now replace trading-feed
inheritance. Exact-feed filtering precedes enrollment and budgeting; reports retain
uncollected other-feed candidates. Six fixed IEX controls receive new identities.
The same injected workers/journal/source boundary handles both feeds, with no fallback,
constructor compatibility shim, extra poller or execution authority. Actual successful
IEX observations remain a deployment check alongside dated stock-universe preparation.
Worker/process readiness alone cannot establish alpha data quality.

### Prospective equity universe capture

The [dated equity cohort](alpha-equity-universe.md) uses pure UUID-based selection,
strict public-directory parsing, injected bounded read-only transports and the shared
alpha journal/artifact store. All blocking CLI/source/hash work is off-loop. Review
closed receipt-clock backdating, raw malformed-response loss and UTC/ET fixture
issues with regressions. No new schema, compatibility fallback, execution path or
background process was added. Instrument subtypes/sectors, historical membership and
delistings, source-specific trailing liquidity, dynamic missing-member panels and
bounded acquisition checkpoints remain on the [ordered roadmap](alpha-roadmap.md).

Actual IEX verification distinguishes transport from modeling: current SPY/QQQ
session observations are complete, but 14-day decision history has source-absent
minutes and all six first decisions fail closed. Raw JSON/NPZ audit found no local
row/value loss. Review a versioned sparse signal-bar contract separately from the
strict replay execution clock; a missing IEX trade bar is not automatically an unknown
feed outage or an executable unchanged price. Existing results/semantics stay immutable.
The [measured evidence](alpha-iex-forward.md#deployed-evidence--september-17-2026)
and daily-equity next priority are recorded in the roadmap.

`alpha liquidity-study` now applies a frozen daily liquidity/coverage screen to the
complete [dated equity cohort](alpha-equity-universe.md#daily-liquidity-and-coverage-screen).
It reuses the daily study service and diagnostic journal, with bounded paced reads,
immutable member checkpoints and fail-closed selection. IEX activity is source-specific;
this development screen neither establishes historical membership nor qualifies alphas.

The actual daily screen selected 64 of 150 eligible members from all 300 candidates;
independent raw/normalized reconstruction and journal ordering passed. Review also
reproduced SDK/normalization row loss being mistaken for ordinary missing coverage.
Canonical source-quality metadata now crosses the provider/panel boundary, so
strict studies reject that loss without a liquidity-specific transport or another
persistence schema. The actual run had no row loss. Next priorities remain bounded
economic/Ridge experiments and prospective evidence, plus authoritative subtypes,
corporate actions and historical eligibility before qualification.

### Screened equity forecasts and gate power

The [new forecast study](alpha-panel-forecasts.md) reuses daily acquisition, the
existing event journal and the shared model/scaler boundary. Its separate versioned
computation handles causal per-date eligibility without weakening complete-book
contracts. No fallback prices, outcome-selected constituents, new schema or
production execution authority is introduced. Forecast, label and basket support
remain separate; unavailable held outcomes prevent full-path compounding.

Open high-value findings: the legacy one-step IC is not aligned with multi-session
bracket outcomes, lifetime gate power failed planted-edge tests, and lifetime trial
counts are not demonstrated independent tests. Preserve accounting while running a
fresh predeclared power ablation before changing that rule. Historical current-cohort
results retain survivorship/liquidity-selection bias; subtype, delisting, borrow and
measured execution evidence remain required for qualification.

### Retained forecast comparison boundary

The [style/endpoint follow-up](alpha-forecast-controls.md) adds a read-only source adapter to `AlphaPanelService`, not a second acquisition or journal lifecycle. Pure forecast input preparation and basket/cost accounting are shared with the original study. The versioned plan binds the parent result and fixed comparisons; verification loads the same hashed dataset bytes. Original source metadata stays intact, while the new manifest identifies artifact-read receipts. Strict endpoint evidence affects outcomes only. Accepted limits remain original finite-price training, post-selection/current-cohort bias, unmatched beta/sector risk, absent borrow/capacity evidence and small economic sample size.

The [completed controls run](alpha-forecast-controls-2026-09-18.md) confirms no separate model or payoff engine was needed. Its remaining findings concern source-qualified training/evaluation labels and prospective evidence: the original finite-price model must not silently acquire strict endpoint semantics or be promoted because of absolute historical returns.

### Whole-stack survey — September 18

The [current survey](alpha-stack-survey-2026-09-18.md) found four open boundaries:
production omits the configured drawdown input; legacy scheduled mining acquires
before trial reservation; live stop-distance sizing and shadow convex allocation
remain separate; legacy CVaR reporting has sign/time-basis defects and supplies no
live tail-risk protection. Qualification power, overlapping residual significance
and statistical-family mapping also need calibrated evidence. Keep one journal and
execution path, and follow the [revised canonical ordering](alpha-roadmap.md#current-priorities-after-the-whole-stack-survey).
The survey itself changed no behavior. The subsequent
[account-risk tranche](account-ledger.md#cash-flow-adjusted-drawdown-and-entry-admission)
connects S1 through scan/reservation/submission using the existing ledger projection
and fixes S4 report semantics. [Entry capacity](entry-capacity.md) now addresses
S3 funding/borrowability, observed-equity capital and aggregate planned stop risk
using typed adapter evidence, one pure policy and the existing FIFO/journal. Final
admission and economic writers share the trading lock; no network I/O occurs inside
it. No fallback account model, protection inference by symbol, or second workflow
was added. S2, calibrated gate power, measured portfolio volatility/tail constraints
and executable portfolio plans remain open. The [power-ablation plan](alpha-power-ablation-plan.md)
separates planted-predictor recovery, selected winner, execution and individual gates;
its [completed diagnosis](alpha-power-diagnosis-2026-09-18.md) finds poor positive
power and a concrete GTC entry-lifetime mismatch; production gates remain unchanged.


### Power diagnosis and factor research (September 18)

The [power harness](alpha-power-ablation-plan.md) composes pure jobs and summaries
through the existing synthetic artifact lifecycle. Qualification splits expensive
immutable measurements from cheap criteria assessment; this avoids rerunning
bootstrap or novelty calculations for count/variance sensitivities. The shared
simulator supplies observation-only traces. No alternative simulator, journal,
provider adapter or queue was introduced. Missing/nonfinite evidence now fails
closed explicitly; valid thresholds and decisions remain unchanged.

The next [factor-controls experiment](alpha-factor-research-plan.md) should reuse
retained-panel provenance, the study reservation boundary and existing IC/basket
kernels. Distinguish explanatory risk exposures from predictive characteristics
and executable portfolios; a residual score alone supplies none of the missing
borrow, protection or portfolio-order contracts. The plan is not an executed study.


### Measured power loss

The [800-search diagnosis](alpha-power-diagnosis-2026-09-18.md) separates a functioning
forecast from poor conversion into trades: persistent pending orders suppress about
90% of known-control pulses. Search ranks bracket economics while one-step IC tests a
different target; lifetime DSR and trade-count failures coexist. Reuse immutable
execution-policy identities and timed lifetimes for a fresh causal comparison.
Do not introduce a second simulator, rewrite prior evidence or tune away joint gates.

### Retained factor implementation

[Factor controls](alpha-factor-controls.md) now share one retained-study CLI
composition and a structural parent-binding interface with forecast controls.
Parent validation, original Ridge accounting checks and paired comparisons live
in `retained_forecasts.py`; expected-clock HAC is shared through `information.py`.
The causal feature kernel performs one checked SVD per return date and reuses it
across symbols with complete support. There is no second optimizer, acquisition
engine or execution path. All risk exposures remain diagnostic. Missing source
history reduces reported breadth; it cannot shorten the frozen fit window.
