# Alpha research roadmap

**Active long-horizon plan — updated 2026-09-19.** This is the canonical alpha work
queue. [Pipeline contracts](alpha-pipeline.md) describe current behavior; the
[architecture review](architecture-review.md) retains broader operational priorities.
The [original alpha review](alpha-stack-review.md) is historical defect evidence.

## Objective and constraints

Discover reproducible, complementary forecasts and qualify their actual execution
policy before admitting paper risk. Three or four deployed alphas is an aspiration,
not a quota. Rejecting everything does not prove calibration; a good pipeline must
also recognize controlled positive cases and quantify its statistical power.

- Keep immutable strategy/data/policy identity, all attempts and failed experiments.
- Preserve causal features, purged temporal validation and one-use final holdouts.
- Never select gate parameters to make already-seen candidates pass. Predeclare
  calibration scenarios, thresholds and acceptance criteria before running them.
- Synthetic evidence is diagnostic only: no production journal/shadow credit,
  broker orders or notifier access. Statistical evidence alone cannot activate.
- Reuse the journal, artifact adapters, entry FIFO and close/protection services.
  No parallel execution queues, YAML ownership or statistical fallback promotion.
- Keep active paper service and test configuration isolated. Develop in a worktree.
- Each change starts with behavioral tests; retain RED → GREEN evidence, then review,
  relevant integrations, pre-commit/CI and a controlled deployment if code changes.

## Current priorities after the whole-stack survey

The [September 18 survey](alpha-stack-survey-2026-09-18.md) distinguishes production
from shadow infrastructure and supersedes older next-step ordering below. No new
research run or runtime policy change was made for this review.

1. **Account risk and entry-capacity tranches implemented (S1/S3).** The first
   [account-risk tranche](account-ledger.md#cash-flow-adjusted-drawdown-and-entry-admission)
   connects persisted drawdown to sizing, authorization and final submission, with
   every tier bounded and concurrent checkpoint/lease fencing. The
   [entry-capacity tranche](entry-capacity.md) adds current buying power,
   tradability/borrowability, exact working protection, observed-equity capital and
   aggregate planned stop risk through that same FIFO/lock boundary. TDD and SDK/PG
   integrations cover stale inputs, changed brackets and concurrent economic writers.
   Measured portfolio volatility, factor and tail constraints remain future work;
   a stop-loss budget is not a guaranteed loss bound.
   Legacy CVaR signs/units and unsupported Sharpe annualization are corrected (S4);
   the IID report remains descriptive, not live protection. Source/test completion
   and installed-service verification are recorded separately.
2. **Mechanistic lifetime run complete; confirmatory power run is the next action.**
   The [800-search result](alpha-power-diagnosis-2026-09-18.md) meets the frozen null
   bounds but fails positive-control power. The known forecast works; GTC pending
   orders suppress about 90% of later opportunities. Search objectives and joint
   gates add distinct losses. The [September 21 mechanistic lifetime run](alpha-lifetime-attribution-2026-09-21.md)
   is complete: bounding the resting entry to one session restores trade conversion
   for the planted forecast and separates it from the null by a wide margin; a
   one-session holding expiry imposed after selection is strongly harmful. That run
   does not itself evaluate current-family full-policy acceptance, so it is
   exploratory, not confirmatory. Semantics version 5 (the unclocked, one-session,
   entry-only native-daily contract) and policy-aware mining
   (`AlphaMiner.mine(..., execution=)`, `alpha mine`/`alpha test --entry-policy
   [session|gtc]`, `alpha power-plan --entry-policy [gtc|session]`) are now
   implemented and tested; the scheduled ETF32 job is pinned to `--entry-policy gtc`
   in `scripts/launchd.sh` so it keeps reproducing the historical benchmark until an
   operator changes that pin. Next: freeze and run the confirmatory
   `alpha power-plan --entry-policy session` protocol with a fresh seed and a newly
   sourced read-only family snapshot, per the [attribution plan](alpha-lifetime-attribution-plan.md)'s
   unchanged endpoint and bounds. The simulator grants a session-bounded entry the
   entire next session including its opening print and can never take the open
   live, so its fill rate is an upper bound and forward/probe execution evidence
   is still required before attributing any improvement to live trading (see
   [trade lifetimes](alpha-trade-lifetimes.md#known-limits)). Keep gates and lifetime history unchanged. Paper
   probes should not be enrolled under GTC entries; enrol version-5 candidates.
   The [native-daily collector](alpha-daily-panel.md) now implements parallel
   observation with source-qualified labels, actual receipts and frozen decisions.
   Actual forward forecasts and mature outcomes remain separately measured evidence.

   **Desk goal and measured supply constraint.** The overarching desk goal is one or
   two reasonable trade suggestions per market session in Telegram on the paper
   account; work above is ranked against that goal. A September 21 replay of the
   built-in (non-alpha) strategies found the measured constraint is scan breadth,
   not gates: about 0.8 candidates per session on today's 9 equity/ETF names, versus
   about 6.6 candidates per session on ETF32 plus 65 mega-cap names. A wider scan
   universe with a per-session suggestion budget is the next workstream after this
   one. The confirmatory `alpha power-plan --entry-policy session` run above is a
   separate, operator-decided next step — its result changes no gate.
3. **Factor comparison and prospective daily collector implemented; gather evidence.** The
   [54-comparison study](alpha-factor-controls-2026-09-18.md) and independent audit
   found mixed residual-momentum results, no consistent incremental winner, major
   early support loss and some concentrated gains. The fixed rank-blend benchmark,
   volatility control, residual-blend challenger and declared daily Ridge refit now
   share the [frozen prospective protocol](alpha-daily-panel.md). Receipt-stamped
   inputs, mature training, immutable forecasts/residual state and terminal first
   outcomes use the existing journal. Enrollment/worker health are distinct from
   successful forecasts and 20-session outcomes. The new stress tests show one
   missed residual innovation can suppress the strict shared comparison for 231
   later decisions. Three [independent baseline variants](alpha-daily-panel.md#independent-baseline-portfolios)
   now share captures/fits/outcomes with the primary while retaining separate
   reservations, immutable enrollment pins and outcome ownership after configuration
   removal. Their prospective coverage remains unobserved until the first window.
   Parent-campaign retirement/outcome draining remains required before replacement;
   it is separate from companion enrollment. Investigate missing-history/IEX
   support separately and evaluate delayed SIP under its own source identity.
   Expand forecast breadth toward 150–300 only with bounded resource and dynamic
   membership tests. Historical current-cohort returns grant no qualification.
   The [matched screened-equity DSL panel](alpha-matched-panel-2026-09-19.md)
   compares the new causal families with constant, volatility and benchmark-
   residual controls, including turnover and signal-bar beta. Its complete
   64-name 2021–2025 protocol correctly failed closed on missing historical
   members; a separately identified recent window completed all 32 comparisons
   but failed coverage/sample gates. Carry volatility as a comparator only and
   require an availability-bounded prospective cohort.
4. **Consolidate ongoing campaigns before increasing cadence (S2).** Scheduled
   `alpha mine` remains the reproducible ETF32 benchmark, while the manual CLI now
   accepts a verified, capped prospective equity snapshot (`alpha-mining-universe.md`).
   Every symbol is reserved before provider I/O, so entitlement/timeouts are charged
   failures. Run a matched 32–64-name IEX campaign next, then add an explicit
   stage/rejection/maturity scoreboard before scaling to all 300 names. Daily
   outcomes, bounded discovery and prospective collection serve different purposes;
   repeated same-history jobs are not new evidence.
   The first [32-name prospective campaign](alpha-equity-mining-2026-09-19.md)
   charged all 1,024 reserved trials, completed 30 symbols and found no leader
   meeting both policy gates. Its short-history/special-share failures make a
   coverage and liquidity scoreboard the next prerequisite before a 300-name run.
   The completed IEX screen is now consumable directly through
   `alpha mine --universe screen`, retaining its result hash and deterministic
   ranking. The [screened breadth/DSL campaign](alpha-screened-equity-dsl-2026-09-19.md)
   ran 64 names (2,048 trials) and a 16-name seed-coverage rerun (512 trials);
   neither produced a gate-passing candidate. Genetic discovery now evaluates
   every declared DSL seed before evolution, so adding causal families widens
   the funnel without silently spending the budget on mutated variants only.
   Holdout consumption is keyed by symbol alone (`storage/alpha.py:begin_holdout`),
   so one qualification attempt by any alpha locks that symbol for roughly one
   holdout length; measure consumed-symbol coverage before scaling campaigns.
5. **Complete the portfolio deployment lane when evidence warrants it.** Version
   qualification for the actual forecast/holding policy, observed factor/cost/risk
   inputs and target-to-rounded-order plans. Test pending/partial-fill exposure,
   exact protection and contributor attribution through existing execution services.
   Add scenario CVaR/hard volatility constraints against measured needs; keep MPC,
   advanced impact/RL search and options/futures later.

6. **Paper-probe lane implemented (September 21).** The IEX-native smoke campaign
   ([record](alpha-iex-mining-2026-09-19.md)) shows that the source contract passes
   while current candidates fail statistical/economic gates. The
   [paper-probe design](superpowers/specs/2026-09-21-alpha-paper-probe-design.md)
   is now built and tested: a third durable registry status (`probe`), an explicit
   paper-account guard, predeclared risk/expiry/kill limits and `🧪 PAPER PROBE`
   card/notification tags. Probe outcomes earn no shadow, holdout or production
   promotion credit, and `active` does not depend on the broker being paper. See
   [paper probes](alpha-pipeline.md#paper-probes) for the current state machine and
   policy defaults. Deployment (enrolling an actual candidate on the paper service)
   is separate, operator-approved evidence and is not recorded here. Once
   session-bounded-entry (semantics version 5) candidates exist, paper probes
   should be enrolled from that population rather than GTC-entry candidates:
   GTC limit entries were measured to fill mainly when the forecast is wrong,
   an adverse-selection bias that a probe's forward record cannot distinguish
   from genuine edge. Probe enrolment still requires a qualification record
   (`assess_probe` reads `qualification/{version_id}`), and holdout consumption
   is keyed by symbol and rejects overlapping intervals
   (`storage/alpha.py:begin_holdout`), so a version-5 twin of an existing alpha is
   a new identity that needs its own fresh qualification, and on a symbol whose
   holdout is already consumed it cannot get one without a genuinely new
   untouched period; switching the daily mining default to `session` does not by
   itself unlock qualification on already-consumed ETF32 names. Recorded follow-up: make the probe risk cap an admission
   invariant — check the signal's `risk_dollars` against the enrolment's pinned
   cap in `_alpha_entry_rejection`. Today the cap is applied when the candidate
   is sized, and the enrolment record pins it only as evidence.
7. **Advisory exit cards (A2, approved September 21).** The engine will propose
   Close/Keep when the reason to hold goes away: the owning alpha is demoted, its
   probe expires or is killed, or (later) the allocator's target reaches zero. The
   bracket's target and stop will remain the default exit. Close will route only
   through `PositionCloseService` (exclusive intent, exact bracket-leg
   cancellation, submit once); Keep will be a no-op. Full closes only. Signal-driven
   exits are a strategy rule: they must be versioned in the execution policy and
   mined under it, and are deferred until the entry-expiry confirmation run. Not
   yet implemented.
8. **Scan universe and suggestion budget implemented (September 22).** The
   [design](superpowers/specs/2026-09-21-scan-universe-and-suggestion-budget-design.md)
   is built: a validated `universe:` of 159 equity/ETF names in three groups
   (etf32, mega_caps, research_cohort) whose sectors merge into the correlation
   groups; a paced, bounded fetch phase with failure accounting and a scan-duration
   histogram; a reference-relative hourly-coverage gate that skips strategy scanning
   for thin names while alpha-shadow observation still records them; COLLECT → RANK →
   SEND with a derived per-session card budget; two New York cron suggestion scans
   and an end-of-session digest. The north-star metric is unchanged and is now
   directly measurable: **one or two reasonable suggestions per market session in
   Telegram**, counted as cards recorded per New York trading day. `setup_quality`
   is a transparent prioritisation heuristic stored in `decision_provenance` with
   the rank and the candidate count it beat — it is **not validated alpha** and must
   be evaluated against realised R before it is trusted. Follow-ups, none started:
   session-bounded entries for the built-in strategies (after the entry-expiry
   confirmation run); universe-wide mined alphas over the same names; evaluating
   `setup_quality` against realised R; fixing the interval scan's drift from candle
   closes. Deployment and the first two sessions' digests are separate evidence
   (see [production operations](production.md#suggestion-scans)). Further recorded
   follow-ups: process-wide per-feed pacer; `setup_quality` cross-strategy scale
   calibration.
9. **A priori alpha catalog (September 25).** The [a priori catalog](docs/apriori-alphas.md)
   tests literature anomalies as one frozen hypothesis per leg (`config/research/apriori/*.json`),
   pooled across many names to avoid the mining funnel's multiple-testing penalty. Studies
   are research only (no registry/trial/shadow/broker credit); a passing leg becomes
   eligible for a separately specified capped paper probe (Part 2). The PEAD (post-earnings
   drift) entry is pending study; see [PEAD study design](superpowers/specs/2026-09-25-apriori-pead-study-design.md).

- **Later: overcome sparse per-symbol trade histories in alpha mining** (operator,
  2026-09-25). The funnel qualifies per symbol, so each hypothesis sees few trades and
  planted-signal power is 0/64. Research how quant practice handles this — pooled or
  cross-sectional panel estimation, hierarchical/shrinkage estimators, meta-labeling,
  event pooling, cross-asset transfer, synthetic or bootstrapped paths — then design ways
  to unblock the mining funnel. Research online first; no build until reviewed.

No threshold was lowered, alpha promoted or trading configuration changed by this
survey. Zero promotions alone proves neither correct pruning nor absence of alpha.

### Prospective daily acceptance boundary

Implemented by the [daily panel collector](alpha-daily-panel.md); retain these
contracts when interpreting its still-maturing observations or extending its universe.

The current session observer cannot become a native-daily panel collector by
changing its timeframe. Reuse its journal, conditional claims and retained-read
mechanisms, with a frozen campaign/cohort/date identity and a receipt deadline
before the forecast's next-open entry. Native daily prices need their own observed
calendar and source identity. All declared names remain visible when unavailable.

Persist feature inputs, fitted artifacts, support, forecasts and basket decisions
before outcomes. H20 labels require actual endpoint receipts and a consistent
adjustment vintage for both endpoints; an entry saved today cannot be divided by
a later differently adjusted exit price. Later corrections add evidence and never
rewrite prior decisions or residual innovations. Tests must cover restart/duplicate
claims, late receipts, source revisions, holidays, immature labels and unavailable
held outcomes. These observations earn no automatic qualification or order authority.

Reuse native daily reads/raw evidence, `AlphaRepository` claim/finalization primitives,
the panel/factor kernels and the existing daemon client lifecycle/shared forward
report. Claim all arms together per campaign/cohort/session. Historical Ridge
prediction matrices cannot forecast new dates: the protocol must choose a frozen
fitted artifact or a declared refit schedule, requiring every training label's
actual receipt before the fit cutoff. Test two-client PostgreSQL races, late-result
fencing and the real SDK capture → forecast → mature outcome path with no orders.

### September 18 progress — isolate the loss of detection power

The [power-ablation harness](alpha-power-ablation-plan.md) implements 800 searches
over 400 datasets (12,800 expression evaluations), with 16 primary current-family
full-policy endpoints. A read-only snapshot confirms 7,826 prior global attempts
and an existing current native-daily projection containing zero Sharpe samples. Each separate 16-trial counterfactual
therefore uses count 7,842 and that run's evaluated Sharpe samples; it does not infer
variance from 7,826 independent hypotheses. A2a's 7,065 reference stays historical.

Review and tests verified mechanical development stopping, predictor/execution
boundaries, frozen bootstrap settings and stratified cofailures. The completed
[800-search diagnosis](alpha-power-diagnosis-2026-09-18.md) has no failed jobs or
unavailable full-policy endpoints, but fails every positive-power criterion.
The [factor follow-up](alpha-factor-controls-2026-09-18.md) completed all 54
comparisons and its independent numerical audit; prospective collection remains
separate. At that study's completion, lifetime attempts were 7,880 (7,826 + 54);
subsequent daily-panel enrollments add their own immutable reservations. Registry generation 16,
active 0/shadow 16 are unchanged. No threshold reduction,
promotion or paper allocation follows from either diagnostic.

### Alpha expansion workstreams (September 23)

The [expansion survey](alpha-expansion-survey-2026-09-23.md) finds that operator
breadth is not the binding constraint. The constraints are per-symbol statistics and
holdouts, and the missing activation lane for cross-sectional forecasts. It refines
A4 and funnel items 3–5 below into approved workstreams, serving the north star of
one or two reasonable cards per session:

1. **WS1 — earnings blackout** (delivered, [PR #90](https://github.com/adamhadani/agentic-trader/pull/90)). A deterministic evaluator gate rejects
   equity entries when an announcement is still ahead within
   `risk.earnings_blackout_days`, and each card shows an earnings line. The gate
   fails open with an "Unverified" note when the calendar is down.
2. **WS2 — measure the cards first, then shadow-rank them** (delivered; re-sequenced
   from "panel lane v1").
   - The predeclared [setup-outcome study](setup-outcomes-2026-09-23.md) replays the
     live native strategies at the live scan clock. It labels 15,644 setups with the
     live bracket in R, and tests `setup_quality` plus eight causal cross-sectional
     features under a one-shot time-interval holdout.
   - Results:
     - `setup_quality` is uninformative.
     - The native edge is thin: longs are about +0.03R after costs and shorts are
       negative.
     - Only `vol_20` and `sector_rel_mom_60` survive Holm.
     - The frozen Ridge ranker **failed** its holdout and was worse than
       `setup_quality`.
   - Live ranking is unchanged. Scheduled suggestion scans now journal the same
     `setup_features_v2` vector for every ranked candidate (`scan_candidates_ranked`),
     and `copilot cards outcomes` labels them prospectively.
   - The panel-mining lane follows, using this outcome target. Its first hypotheses are
     the two surviving features and a short-suppression test on fresh data.
   - **Decision (September 24): prospective only.** No historical panel build for
     `vol_20`/`sector_rel_mom_60`. Every scheduled suggestion scan already journals both
     features per ranked candidate, so evaluate them on untouched live evidence with
     `copilot cards outcomes` around October 21 – November 4. If revisited, `alpha
     panel-study` needs a group-neutralized hypothesis kind and cohorts of at most 64 names.
3. **WS3 — dynamic universe (September 23) — delivered.** Liquidity screen plus
   movers/most-actives for the scheduled suggestion scan only; the intraday job,
   swing scan and manual scans stay unchanged
   ([design](superpowers/specs/2026-09-23-dynamic-universe-design.md),
   [production behaviour](production.md#suggestion-scans)).
   - Each suggestion scan adds up to 20 deterministically filtered US equities, with
     a synthetic per-scan contract (multiplier 1). `config.contracts` is never
     mutated. Dynamic names run native strategies only.
   - Liquidity is relative: at least the 25th percentile of the static universe
     equities' median dollar volume, measured in the same scan on the same (IEX)
     bars. The scan fails closed when there is no static reference.
   - All dynamic names share one `dynamic` correlation group, so the desk issues at
     most one dynamic card per session. Cards and `scan_candidates_ranked` candidates
     carry `dynamic`/`dynamic_source`.
   - Each scan journals one `dynamic_universe_built` event, and the digest counts
     dynamic names. A screener failure leaves a static-only scan and an
     `available: false` event.
   - Follow-ups:
     - Measure dynamic cards separately in `cards outcomes`; they are in-play names,
       selected by today's movers.
     - Decide whether admission's `max_correlated_positions` should also group open
       dynamic positions; today only the scan's card cap does.
5. **Short-suppression test (September 23) — delivered.** A predeclared test on a
   fresh 2017–2021 window, which the setup study never used, found native short
   setups at −0.33R per setup after costs (90% CI [−0.43, −0.21]), with longs ahead
   by +0.69R. Per the frozen rule, `strategies.{trend_pullback,squeeze_breakout}.allow_short`
   is now `false`, so native cards are long-only
   ([result](setup-baserates-short-2026-09-23.md)).
   - Follow-up: add a data-quality exclusion for implausible labels. One GPOR row from
     the bankruptcy era had R = +1,546.
4. **Card freshness (September 23) — delivered.** The first live card (signal #16,
   XOM LONG, issued 10:38 NY) was read more than an hour later; a tap judged only by
   entry admission would submit a stale bracket or dead-end refuse the operator with
   no path to a fresh one. [`execution/freshness.py`](../agentic_trader/execution/freshness.py)
   now re-assesses a tapped card against the current price, session and gates and
   returns one of `EXECUTE`/`REPRICE`/`MISSED`/`EXPIRED` (see
   [production behaviour](production.md#suggestion-scans) and
   [the entry-authorization contract](durable-execution.md#card-freshness-precedes-authorize-september-23));
   `REPRICE` atomically
   replaces the card with its own outbox row, `MISSED`/`EXPIRED` offer a one-tap
   **Re-evaluate** that runs a fresh single-symbol scan, and every card shows its
   validity window. Proactively striking a card's buttons at session close is now
   **delivered** (September 24): a `card_expiry_sweep` scheduler job (every 5 minutes,
   also at startup, independent of halt state) calls `TradingCopilot.expire_stale_cards()`,
   which runs the shared `card_is_stale`/`card_session_over` rule against every untapped
   `PENDING` signal, atomically expires the stale ones and enqueues one new
   `NotificationKind.CARD_EXPIRED` outbox notification per row in the same transaction;
   delivery (`TelegramNotifier.strike_expired_card`) edits the card's message down to a
   single Re-evaluate button, or removes the buttons entirely for a non-configured
   contract (see [production behaviour](production.md#suggestion-scans)). A `/scan SYM`
   Telegram command for an arbitrary symbol is also **delivered** (September 24): a
   configured contract or a dynamic-universe-screened US equity, the latter gated by the
   latest journaled liquidity reference and the one-dynamic-card cap. Remaining
   follow-up, out of scope here: labeling delayed entries 1–3h after the decision in the
   setup-outcome study, to quantify decay against measured tap latency.

Later and operator-gated:

- a residual-reversal single-leg feature;
- the pool-contribution search objective and PBO diagnostic;
- a foundation-model volatility feature compared with HAR;
- a panel-driven card source, which needs a horizon/exit-policy decision under
  semantics v5;
- a small deep cross-sectional model, only after panel IC exists.

Foundation models for return direction and full LLM-agent miners are deliberately
not planned.

## Ordered work queue

| ID | Status | Milestone / acceptance boundary |
| --- | --- | --- |
| A1a | Implemented — [PR #35](https://github.com/adamhadani/agentic-trader/pull/35) | Calibration instruments and permanent control/reference tests; diagnostic outputs cannot authorize promotion. [Pilot evidence](alpha-calibration-2026-09-16.md). |
| A1b | Implemented — [PR #36](https://github.com/adamhadani/agentic-trader/pull/36), [study results](alpha-study-2026-09-16.md) | All 1,952 jobs retained; scientific status incomplete (22 unavailable comparisons). No replacement gate accepted. |
| A2 | A2a implemented — [PR #37](https://github.com/adamhadani/agentic-trader/pull/37); A2b replay groundwork — [PR #38](https://github.com/adamhadani/agentic-trader/pull/38) | [Replay contract](alpha-session-replay.md) and [four-run evidence](alpha-session-replay-2026-09-16.md): SIP coverage complete, IEX incomplete in both windows. Live signal-clock migration and broker execution observations remain; intraday promotion stays blocked. |
| A3 | Forecast benchmark prerequisite — [PR #43](https://github.com/adamhadani/agentic-trader/pull/43); [PR #44](https://github.com/adamhadani/agentic-trader/pull/44) timing/cost screen complete; standalone open-gap policy paused; combined execution shadow-only | Distinguish forecast components from a fully specified tradable strategy; validate combinations causally. |
| A4 | 64-equity forecasts and matched controls completed; broader/prospective evidence next | Broader economic hypotheses and point-in-time universe/data coverage. |
| A5 | SPY/QQQ session diagnostics and native-daily panel collection implemented; first daily forward evidence and broader campaign orchestration remain | Bounded research campaigns and a shadow observation universe independent of trading permissions. |

A1a makes A1b reproducible. A2 can follow those foundations while any longer A1b
forward experiment accumulates evidence. A3–A5 may need small prerequisite adapters;
record dependencies here instead of creating a second competing roadmap. Update each
row with the implementing PR, reproducible evidence and any remaining activation gate.

### A1 — Calibration before more search

A1a now provides `alpha calibrate`, shared pure statistical assessment, independent
references and fail-closed invalid-evidence checks. The 32-seed pilot detected dense
positive controls, rejected null strategy controls and exposed limited sparse-control
power. PR review required separating data and resampling random streams; retain both
the initial and corrected study evidence. Neither is approval of a 5% error-control
claim. A1b adds predeclared scenarios, block-length sensitivity, ARCH comparisons
and adaptive-search replay. Its [results](alpha-study-2026-09-16.md) expose return
coverage and execution/objective limitations; no gate replacement is justified.
**A2b replay groundwork is implemented**; forward/execution evidence remains. A2a corrected the return clock
and repeated calibration with a versioned policy and fresh validation: all 1,952
jobs completed, null-search criteria passed, but planted-signal power remains
insufficient. No replacement gate is justified by the [fresh results](alpha-timeline-study-2026-09-16.md).

The September 16 campaign found zero qualifying candidates among 3,648 trials.
Another 1,368 trials exercised 24 nested walk-forward folds. Artificial dense edges
passed, sparse profitable controls failed cumulative DSR, and zero-edge controls
failed. These small diagnostics do not measure reliable error rates. See
[campaign evidence](alpha-research-2026-09-16.md).

A1a acceptance:

- Independent numeric DSR references, realistic per-observation units, valid moments,
  finite/discrete budget checks, and deterministic edge cases.
- Reusable causal synthetic generators with null, sparse and dense positive cases;
  shared scoring/simulation/statistical assessment, explicitly synthetic provenance.
- Reproducible reports of per-gate rejection, detection/false-positive rates and
  uncertainty intervals, with bounded compute and frozen protocol identity.
- Joint block-resampling diagnostics preserve serial/cross-strategy dependence;
  duplicate/permuted/scaled columns have tested semantics. They cover supplied fixed
  candidates only; they cannot claim to replay adaptive search or replace lifetime
  trial accounting. No diagnostic output is a promotion credential.
- CLI integration exercises the real calculation and private artifact output with
  config/DB/provider construction forbidden; no synthetic production state.

A1b acceptance:

- Freeze unseen seeds, sample sizes, effect sizes, trade frequencies, autocorrelation,
  cross-candidate dependence and block-length sensitivity before evaluation.
- Measure confidence intervals for false acceptance and power, including a replay of
  the adaptive search process. Keep a validation set of calibration scenarios.
- Distinguish raw attempt count from independent trials; preserve the entire ledger.
  Any dependence estimate uses discovery/training data, never the final holdout.
- Compare the existing DSR/holdout contract with justified alternatives. Report
  assumptions and failures. Do not present a fixed-panel bootstrap as adaptive-search
  family-wise error control, or DSR as a calibrated probability of live profit.
- Change a runtime gate only after the agreed statistical acceptance criteria pass;
  version the policy and require fresh evidence. No automatic reinterpretation of
  past rejections or consumed holdouts.

### A2 — Session-correct execution replay

A2a is implemented: missing feature scores no longer remove known cash observations
or same-bar pending-fill/exit losses from the return series. TDD covers warmup,
intermittent features, pending/held exposure, missing prices and causal prefixes.
Full execution clocks, coverage and compatible variance samples are versioned;
lifetime trials and original A1b failures remain intact. The [contract](alpha-return-timeline.md)
and [fresh study](alpha-timeline-study-2026-09-16.md) record evidence and remaining limits.

A2b's [first increment](alpha-session-replay.md) implements observed exchange
sessions, 15m/1h/4h/session aggregation, a shared minute execution state machine,
explicit decision latency and durable diagnostic artifacts. It preserves the
existing daily validation semantics. The predeclared four-run deployment-feed
smoke test checks DST/early-close coverage without tuning a formula.
Its [results](alpha-session-replay-2026-09-16.md) retain two SIP completions and two
IEX coverage failures; neither successful replay generated an entry. No live-feed
change or alpha qualification follows from this mechanics/data check.
Clock-boundary hardening now rejects session-derived/unknown layouts and ambiguous
timestamps in fixed-duration research, live screening and shadow paths. Shared
closure logic lives in `market/bars.py`; new manifests record the fixed clock.
This preserves existing version identities and does not complete live migration.

The [prospective observer](alpha-forward-observations.md) now samples the configured
feed around actual session-bar closes, retains immutable raw inputs/receipts/revisions
through the existing journal, and excludes inspected dates before price access.
It measures the REST data boundary without scoring, trading or promotion credit.
The desk enables SPY/15m; actual forward evidence requires subsequent live sessions.

The [versioned decision contract](alpha-session-decisions.md) now preserves old
identities, gives new session versions shared receipt/delay/expiry screening and
shadow semantics, and prevents expired replay proposals from crossing closed sessions.
New versions remain diagnostic-only, including session-derived daily definitions.

The durable session worker now covers receipt-aware acquisition, restart/revision/missed
windows, cursor/registry CAS and interrupted/late completions in the shared journal.
Remaining A2b: collect actual publication/acknowledgment/fill evidence.
Native provider bars cannot be silently treated as session-derived bars.
Cover timezone/DST, holidays/early closes, extended-hours aggregation, closed-bar
availability, GTC pending entries, conservative same-bar paths, gap/partial fills,
trailing updates and broker-held protection. Keep signal and execution clocks separate.

Acceptance: deterministic event replay and actual-SDK fixtures agree on order eligibility
and working protection; causal prefix tests pass; transaction costs/corporate actions
have explicit contracts; real paper-fill observations quantify the remaining mismatch.
Only then replace the unconditional intraday gate with evidence-based eligibility.

### A3 — Forecast components and executable combinations

The [forecast benchmark](alpha-forecast-benchmarks.md) now separates explicit
close-return horizons from bracket-policy P&L, compares single-feature/Ridge/boosted
predictions on purged walk-forward folds, and retains all attempts/artifacts in the
existing journal. This is a bounded A3 prerequisite while A2b forward data accumulates;
it does not supersede A2b forward/execution evidence or enable trading.


A useful next-day predictor need not be profitable under an unrelated bracket/holding
policy. Define forecast horizon/label/uncertainty separately from trade execution.
Train calibration and combination weights only on preceding observations; retain
component attribution and evaluate incremental evidence against a frozen incumbent set.

Acceptance: nested walk-forward tests cover complementary, duplicate and opposing
forecasts, missing/stale data and changing incumbents. A combined tradable definition
versions contributors, weights and execution policy together. It passes the same
qualification/registry/risk boundaries. Keep portfolio targets shadow-only until
post-rounding risk, partial fills and protective-order ownership are supported.

### A4 — Widen information and hypotheses

Retain ETF32 as a benchmark. Add hypotheses with an economic rationale: sector-relative
momentum/reversal, residual signals, overnight versus session behavior, and liquidity/
volatility conditioning. Extend typed/panel operators only with alignment, dimensional,
missing-data and future-perturbation tests.

Expand toward 200–500 liquid individual equities with point-in-time membership, delistings,
corporate actions, historical eligibility and feed coverage. Do not use today's list
as survivorship-safe history or silently replace raw execution prices with adjusted
feature prices. Require cohort-transfer tests and measured cost/borrow/capacity.

### A5 — Operate the research lifecycle

Separate a shadow observation universe from permission to trade an instrument. Run
bounded jobs with durable status/cancellation, immutable protocols and private artifacts.
Use daily forward outcome/decay/cost observations, bounded weekly discovery, and
periodic matched-budget experiments. Collect shadow observations before spending a
qualification holdout when that avoids evidence expiring during the waiting period.

Acceptance: no missing/unconfigured instrument silently earns shadow credit; changing
registry generations, duplicate candles, restart/recovery and worker contention are
covered. Observe actual paper fills before scaling. GP/Ridge/boosted methods remain
benchmarks; RL/AlphaGen/AlphaForge follow only after the evaluation/ensemble objectives
and simpler baselines are established.

## Funnel expansion: ranked experiments (September 17 review)

1. **Forecast objective and simple baselines (completed bounded follow-up).** The frozen
   78-trial ETF comparison is [complete](alpha-forecast-comparison-2026-09-17.md).
   Three one-bar single-feature leads improved every discovery fold; none is qualified.
   The [open-gap timing/cost protocol](alpha-forecast-policy.md#predeclared-open-gap-follow-up)
   froze next-open targets, QQQ transfer, four cost scenarios and triage criteria.
   [All 20 attempts completed](alpha-open-gap-policy-2026-09-17.md); SPY failed the
   every-fold net/excess-return criterion. Pause this standalone policy; no promotion
   or gate change. Pooled forecast error improves, but the evidence is concentrated
   and this trading rule is unstable/cost-sensitive.
   The [failure postmortem](alpha-open-gap-postmortem-2026-09-17.md) found no numeric
   discrepancy, but unstable selection, concentrated error gains and a loss/action
   mismatch. Automatic fold/influence/action and fitted-model diagnostics are now
   implemented and deployed in [PR #46](https://github.com/adamhadani/agentic-trader/pull/46). Predeclare
   the economic use of each forecast; do not retune this result.
2. **A2b acquisition and durable decisions implemented; forward evidence remains.** Measure publication delay
   before choosing production timing; collect prospective forecasts and actual paper
   acknowledgments/fills. More historic search cannot substitute for this evidence.
3. **Aligned panel/relative hypotheses (A3/A4).** Add sector-relative residual momentum,
   reversal, overnight/session decomposition and liquidity conditioning. Implement a
   panel dataset contract and causal cross-sectional alignment before DSL `rank` or
   peer regressions. Compare incremental forecasts against frozen incumbent factors.
4. **Grammar-guided exploration (A4/A5).** Existing search already has typed AST
   mutation/crossover, a structural diversity archive and score deduplication. First
   benchmark bounded grammar productions and subtree novelty against existing random
   and GP search at identical trial/compute budgets. Only then evaluate MCTS/RL.
   [AlphaCFG (2026)](https://arxiv.org/abs/2601.22119) combines bounded grammar with
   syntax-aware policy/value learning and MCTS; [RiskMiner](https://arxiv.org/abs/2402.07080)
   is another MCTS comparator. Their published gains are hypotheses for our data,
   not transferable validation or permission to expand trial budgets indefinitely.
5. **Combination, then assisted generation.**
   [AlphaForge](https://arxiv.org/abs/2406.18394) studies generation plus changing
   factor combinations; [AlphaGen](https://arxiv.org/abs/2306.12964) targets collections.
   Compare these only after simple causal Ridge/linear combinations and attribution.
   [Microsoft RD-Agent(Q)](https://github.com/microsoft/RD-Agent) offers automated
   factor/model research workflows; evaluate bounded proposal generation behind our
   existing DSL, trial ledger and validator, without a second execution authority.
6. **Broader assets only with data/execution contracts.** ETF32 remains the initial
   benchmark, followed by point-in-time liquid equities with delistings and corporate
   actions. Options require historical contract chains/quotes, spread/slippage/Greeks,
   expiration/exercise/assignment accounting and protection ownership; Alpaca's
   [documented option lifecycle](https://docs.alpaca.markets/us/docs/options-trading-overview)
   includes these events. Futures require contract rolls, multipliers, margin/session
   data and a separately verified broker adapter. Neither is just adding ticker strings.
   Reuse equity ETFs for broad economic exposures while those capabilities are absent.

**Cadence:** keep bounded weekly discovery; collect daily forward outcomes. Do not
increase frequency while power/objective and deployment-clock gaps remain. Later
campaigns should include null/positive controls, parameter-budget matching, failure
rates, fold coverage, turnover/cost and cohort transfer, with a fresh untouched final
period. Current-list equities must never be described as survivorship-free history.

**Fallout retained:** benchmark failures keep reservations but lack automatic job
recovery/per-trial checkpoints (A5). The broader discovery/exposure audit should ensure
all inspected prefixes, not only explicit diagnostics, are fenced against later
holdout reuse. Forecast pooling still identifies horizon by bar timeframe; versioned
multi-bar target/uncertainty compatibility must precede deploying combined forecasts
(A3). Research-worker isolation and operational dependencies remain below.

## Operational dependencies retained

Corporate-action/partial-fill account semantics, macro/calendar freshness admission,
stop-intent recovery, composition/bootstrap and independent operations monitoring
remain in [architecture-review.md](architecture-review.md#remaining-findings-ranked).
Portfolio execution cannot bypass those ownership/protection constraints.

## References

- [DSR, including independent-trial treatment in Appendix 3](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf).
- [ARCH multiple-comparison studies and established SPA/StepM procedures](https://bashtage.github.io/arch/multiple-comparison/multiple-comparison_examples.html).
- [AlphaGen: optimizing complementary collections](https://arxiv.org/abs/2306.12964).
- [Alpaca historical bar adjustments](https://docs.alpaca.markets/us/reference/stockbars).


## Authorized delivery sequence — September 17

The operator approved the following three increments after the postmortem:

1. Automatic benchmark diagnostics and reproducible fitted-model evidence. Reuse
   the existing benchmark/report/journal; retain the failed open-gap example as a
   descriptive attribution check. No new promotion gates.
2. Durable, receipt-aware session candidate evaluation in the existing daemon.
   Test restart, revisions, missed windows, competing workers and slow reads; reuse
   the journal and session clock. Remain diagnostic until execution/qualification
   evidence permits promotion. Healthy idle operation is not a forward sample.
3. A bounded, predeclared ETF hypothesis campaign. Freeze information timing,
   target, action, costs, comparators and budget before results. Prefer aligned
   relative/session hypotheses; any panel adapter needs causal alignment tests.
   Retain every outcome and keep final holdouts untouched until a strategy is frozen.

Finish each increment with review, affected integration/CI and deployment verification;
then continue the next. Success is candidates worth prospective observation, not a
required number of promotions. Real forward sessions cannot be fabricated or backdated.


September 17 delivery status: increment 1 merged/deployed as PR #46. Increment 2 merged/deployed as
[PR #47](https://github.com/adamhadani/agentic-trader/pull/47), verified with 16 readiness
checks and real SDK/PostgreSQL evidence. Increment 3's [frozen ETF campaign](alpha-session-campaign.md)
completed all 81 attempts; its [results](alpha-session-campaign-2026-09-17.md) retain zero
complete triage passes. Six fixed SPY/QQQ controls are designated for diagnostic forward
observation. No session alpha is activated by these increments.


### Next work after the ETF session campaign

1. Accumulate actual receipts/scores and unavailable/missed-window rates for the fixed
   six-version SPY/QQQ forward cohort; healthy idle periods earn no evidence. The
   [shared CLI/Telegram evidence report](alpha-forward-evidence.md) now exposes
   canonical counts, receipt timing, cursor gaps and truncation. Real sessions
   must still accumulate; recorded-score fractions are not a full window census.
2. Resolve execution-horizon semantics before increasing mining volume. Momentum
   produced 9–12 completed trades over three sampled blocks, sometimes held for days;
   GTC entries could fill days after submission. Specify immutable resting-order
   lifetime/holding intent, preserve uncertain-cancel recovery, and compare broker
   lifecycle observations. The [shared timed policy and lifecycle implementation](alpha-trade-lifetimes.md) now covers cancellation/recovery and deterministic holding closes. Prospective broker timing/protection evidence remains required before session promotion.
3. The [continuous timed study](alpha-continuous-campaign-2026-09-17.md) completed
   36/48 comparisons; 12 SPY comparisons retain a four-minute coverage failure.
   QQQ momentum now has 415 closes but fails the frozen 5 bp cost stress. No complete
   triage pass. Annual development runs are not untouched confirmation; old failures
   remain failed and calendar/acquisition partitions never reset trading state.
4. The [causal sector-panel study](alpha-sector-panel-2026-09-17.md) now implements
   aligned relative/beta-adjusted diagnostics: 32/32 complete, zero passes. Retain
   benchmark and joint-risk comparisons for subsequent studies. Momentum variants and SPY/QQQ returns were highly
   correlated in the inspected sample; separate formulas are not independent risk.

Keep execution/qualification gates, weekly bounded discovery and existing single-owner
admission. Research job checkpoints/resource isolation and longer-term artifact
retention remain architectural dependencies; no new search engine is justified yet.

## Research delivery cadence and progress — September 17

The operator wants credible candidates alongside hardening. Pair each change to
research semantics with a bounded, predeclared real-data experiment before moving
to unrelated infrastructure, unless a concrete safety/validity blocker prevents it.
Keep the existing weekly bounded daily miner; do not increase search volume without
reviewing failure attribution. Reproducible negative evidence is useful, but new
infrastructure alone is not evidence of an alpha.

The [48-attempt continuous timed ETF campaign](alpha-continuous-campaign-2026-09-17.md) is complete:
SPY/QQQ, two calendar years, three costs, three retained controls/leads and one new
trend/pullback hypothesis. The limited acquisition extension directly enables that
experiment. Report attempts/completion, coverage failures, closed-trade density,
primary/stressed returns, benchmark excess/concentration, and exact rejection reasons.
Then report qualification, actual forward dates/decisions and activation eligibility
as separate stages. No quota of promoted alphas and no threshold relaxation.

Original profitable trades remain anecdotal unless attributed and evaluated as a
sufficient out-of-sample strategy sample. Active alphas remain zero at this protocol's
freeze; six session controls and four historical shadows are not qualified strategies.
The session execution gate is an additional engineering/evidence requirement, not a
statistical rejection. Default observed-shadow requirements still require 20 dates
and 10 triggered decisions; elapsed wall time or a healthy collector is insufficient.

### Next delivery after the continuous campaign

1. The [sector-panel implementation and frozen study](alpha-sector-panel.md) now
   pair causal alignment and relative/beta-adjusted hypotheses with the
   [IC measurement contract](alpha-information-coefficient.md). Existing rolling
   single-symbol ICIR is unannualized and overlaps; it cannot use cross-sectional
   or IID t-stat rules directly. Units, coverage, fold boundaries and uncertainty
   are tested; the [actual-data study](alpha-sector-panel-2026-09-17.md) is complete.
2. Trace raw SPY coverage/normalization without replacing the frozen failed evidence.
   Retain omitted/null raw rows and normalization outcomes; existing `dropna()`
   prevents definitive supplier-versus-cleaner attribution from saved frames.
3. Gather independent quote/fill cost evidence and actual session decisions. Five bp
   is a stress assumption, not a measured cost; do not reduce it to rescue this study.
   Session qualification/execution and multi-owner portfolio gates remain unchanged.

Continuous-campaign scoreboard at its completion: 48 charged attempts; 36 complete/12 unavailable; 0 full triage
passes; 0 qualifications/promotions; 7,280 lifetime attempts; registry generation 10
with 0 active/10 shadow versions. Three QQQ momentum variants pass the primary
economic screens but all fail cost stress. This supports testing a different
information/turnover structure instead of another search over the same formulas.

### Sector-panel delivery

The frozen `sector-panel-v1` study expands from two single-ETF entry policies to
nine sector ETFs ranked cross-sectionally, using SPY only as a beta reference.
Four fixed hypotheses × two annual development folds × IC/three cost scenarios
charge 32 comparisons. Native daily acquisition, five-session nonoverlapping basket
proxies and explicit rank/uncertainty contracts test different information and turnover
patterns. Preserve the distinction between a contextual 0.03 IC research screen,
conditional inference, formal qualification and actual portfolio execution.

The [completed independent audit](alpha-sector-panel-2026-09-17.md) retains 32/32
comparisons, complete daily coverage and zero passes. Volatility-scaled momentum has
mean IC 0.0136, +3.63% at 1 bp, −4.27% at 5 bp and 84.78% gain concentration. Other
hypotheses have negative mean IC and lose before costs. No detected arithmetic/coverage
bug explains the outcome; no threshold or direction was changed. At study completion, lifetime
attempts: **7,312**; registry generation 10, active 0/shadow 10, no new qualification.

**Historical ordering after the sector-panel result (completed below):** the deadline/retuner safeguards and SPY postmortem are
complete. [Automatic raw-provider evidence](market-data-evidence.md) now retains
pages before SDK parsing and preserves acquisition failures. Recent-SIP permission
still requires an operator entitlement/feed decision. Freeze a small economically different, slower-turnover
relative/residual ETF experiment with explicit total-return/borrow assumptions;
charge every horizon/buffer/cohort comparison before acquisition. No post-hoc rerun
belongs to the failed protocol and no best-of-failures candidate gains forward or
paper-trading credit. Continue actual receipts and independent quote/fill cost evidence.
Corporate actions, temporal availability, legacy miner metric migration/calibration,
multi-owner protection and worker checkpoints remain explicit dependencies.

### Forecast-to-fill follow-up

The [tutorial comparison and reproduced findings](forecast-to-fill-review.md) refine
A2/A3 without authorizing optimized portfolio execution or changing promotion gates.
Production still arbitrates individual candidates; the convex allocator is shadow-only.

1. **Implemented: operational safeguards / legacy containment.** Shared bounded
   read capacity, Alpaca socket deadlines and an exact-feed recent-access doctor probe.
   The divergent retuner, scheduler/config-export path and optional VectorBT engine
   are removed. Recent-SIP permission remains unavailable; the operator selected separate IEX
   evaluation (implemented below), without authorizing a silent live feed switch; success of a process or empty request is not freshness evidence.
2. **Implemented: automated data provenance.** The [SPY-minute postmortem](alpha-spy-minute-postmortem-2026-09-17.md)
   found all four gaps already absent from two fresh raw SIP responses, with no cleaning
   loss or revisions in 16,654 shared rows. Original failed evidence remains immutable.
   [Automatic raw-row/normalization retention](market-data-evidence.md) now covers
   the shared Alpaca bar boundary, including typed parsing failures and interrupted
   pagination. Original failed studies remain immutable; data capture grants no promotion credit.
3. **Implemented: A3 allocation contract corrections.** Explicit target/feed/price/clock/
   currency contracts, causal calibration cutoffs, forecast-mean HAC error, duplicate
   family fences, conservative uncertainty penalties and trade participation bounds.
   Nonzero pending orders block shadow allocation. Reports retain input hashes,
   objective components and solver diagnostics. [Limits and details](forecast-contract-hardening.md).
   Targets remain non-executable; learned dependence and a full KKT certificate remain open.
4. **A3/A4 persistent-book experiment.** Predeclare a bounded slower-turnover ETF
   study with full costs, total-return/borrow assumptions, terminal inventory and
   causal dependence-aware blending. Compare fixed baskets with persistent holdings,
   including null/planted-edge and duplicate/complementary controls. No forced alpha
   promotion quota and no post-hoc discount of the failed study's costs.
5. **A3 execution gate.** Pin exact source/calibration references to native signals
   and target plans. Version target/plan identity, rounding, stale-snapshot
   rejection, partial-fill attribution and protection before any paper execution.
   Reuse entry FIFO, close services, journal and outbox. Verify intermediate exposure
   and actual fills through SDK HTTP/WebSocket plus disposable PostgreSQL tests.

Defer MPC, advanced impact scheduling, CVaR and new asset classes
until these contracts and a credible economic case exist. The tutorial's optimization
methods are useful tools, not evidence that our current hypotheses have an edge.

Automatic data-capture verification retained 16,654 actual SIP bars over two SDK
pages with no normalization loss/revision and reproduced the known raw gap. The two
engineering checks were conservatively charged (**7,317** lifetime attempts); they
are not new alpha hypotheses. See [evidence](alpha-spy-minute-postmortem-2026-09-17.md#automatic-capture-follow-up).
Private evidence archival/capacity monitoring remains required; no automatic pruning.

### Persistent holdings and IEX evaluation — frozen September 17

[Monthly book protocols](alpha-persistent-book.md) now predeclare 80 comparisons each
on SIP and separately on IEX (operator-selected feed evaluation): slow relative and
residual momentum, reversal, causal dependence blending; reset/net/buffered books;
explicit adjustment and borrow/funding stress. Commit and charge before access.
[Entitlement probes](research-data-sources.md) establish SIP subscription refusal,
not throttling; delayed SIP and recent IEX work, Finnhub candles refuse this key,
and yfinance daily/action connectivity works. Retain both feed matrices, source
receipts and all failures. No automatic production switch or promotion.

### Persistent-book delivery result

[Completed results](alpha-persistent-book-2026-09-17.md): SIP 80/80 and IEX coverage
follow-up 80/80 complete; original IEX 80 remain unavailable/charged for missing
early-2020 warmup. Zero passes/promotions; 7,557 lifetime attempts; generation 10,
active 0/shadow 10. Independent Decimal accounting and IC/covariance audits agree.
The SIP/IEX persistent blends gain 2.91%/2.84% at primary costs but lose 0.36%/0.41%
under stress and have unstable annual returns/negative IC. Retain every failure.

Next ordered work is refined by the operator's September 17 feed-calibration and
individual-stock requests below. ETF32 remains a control cohort, not the ceiling
of the discovery universe.
Pause further sector-momentum/reversal parameter searches and new search engines.
Physical-share accounting, plan identity, partial-fill protection and untouched
qualification/forward evidence remain required before portfolio execution.


### Source calibration and individual equities — current ordered priorities

1. **Implemented and measured: source-specific volume calibration.** The [frozen daily
   SIP/IEX protocol](alpha-volume-calibration.md) fits prior-period relative-volume
   distributions and retains later drift/coverage evidence. No fixed IEX-to-SIP
   multiplier, live threshold replacement or promotion credit. Intraday seasonality
   and prospective IEX receipt/coverage/latency remain separate requirements.
2. **Metadata cohort captured; daily liquidity screen executed and independently verified.** Expand the information
   set to 200–500 individual equities using a dated,
   immutable prospective universe snapshot with stable asset IDs, type/sector source,
   historical/as-of eligibility and observed trailing liquidity. Preserve additions,
   removals, missing bars and delistings; do not select history using today's survivors
   or future full-window coverage. Acquire/validate point-in-time membership and
   delisting returns for confirmatory historical claims. In parallel, a bounded
   explicitly survivor-conditioned development pilot may test mechanics and generate
   hypotheses, but cannot claim survivorship-safe performance or qualify. ETF32 stays
   as a control. Widen the current 64-symbol panel bound only with bounded acquisition,
   memory/CPU tests and a dynamic membership/missing-data contract; simply increasing
   this constant does not implement a valid historical equity panel.
3. **First matched-budget stock/ETF forecasts and exposure/endpoint controls completed.** Predeclare a few economically
   distinct families (sector/market-residual momentum, short-horizon reversal with
   liquidity conditioning, overnight/session decomposition), constant/Ridge controls,
   purged walk-forward folds, turnover/cost/borrow stress and factor-neutral incremental
   tests. Use the exact source volume profiles where relevant. Select on stable net
   economic outcomes and uncertainty, not a minimum number of passing alphas. Record
   all trials and reserve fresh confirmation before data access. More names increase
   breadth, but sector/market correlations do not create independent samples.
4. **Move credible leads through forward and paper execution gates.** Freeze exact
   feed/formula/calibration/holding identities, collect actual receipts and independent
   quote/borrow/fill evidence, then qualify untouched evidence. Pin source/calibration
   IDs to executable plans and complete rounding/partial-fill/protection contracts
   before a combined target can send orders. Collect prospectively while research runs.

Why equities now: broader cross-sectional dispersion and more economic hypotheses
are useful after the validation/accounting fixes. ETFs reduced early debugging and
corporate-action complexity; they were not an architectural requirement. Alpaca's
[current asset master](https://docs.alpaca.markets/us/reference/get-v2-assets-1) supplies
asset/status metadata. We must not infer historical index membership or historical
shortability from that current response. Options/futures still require separate
contract/lifecycle/data work; they rank below liquid equities and simple baselines.

Source calibration completed: [80/80 retained comparisons](alpha-volume-calibration-2026-09-17.md),
40 feed/symbol/fold profiles and exact independent numeric agreement; 7,637 lifetime
attempts, registry unchanged at generation 10 / active 0 / shadow 10. Annual and
per-symbol event-rate drift persists. Read-only operational review found **78/78 session captures unavailable**, all with
retained HTTP 403 evidence; this is consistent with the separately probed recent-SIP
entitlement refusal. Collector readiness is not successful observation evidence.
**Deployed in PR #59:** [separately configured prospective IEX workers](alpha-iex-forward.md)
and six predeclared IEX diagnostic controls preserve original SIP identities/failures.
Actual receipt, coverage and scoring evidence remains a separate deployment check;
daily volume calibration does not authorize intraday reuse. Item 2's dated 300-name
individual-equity candidate universe is captured (below); preceding-only liquidity
and coverage screening comes before the bounded stock/ETF forecast campaign.

### Prospective equity cohort delivered

The [dated equity universe](alpha-equity-universe.md) now retains 33,509 Alpaca
asset records and both current Nasdaq symbol directories. The frozen v2 policy
selected 300 candidates from 6,721 eligible listings by stable asset-ID hashing;
all selections match independent reconstruction. A first failed metadata attempt
exposed historical symbol reuse, and remains retained/charged. Two metadata attempts
are not alpha comparisons. Current non-ETF flags do not establish common-stock
subtype, sectors, historical membership, shortability, or liquidity.

**Daily screen executed and independently verified:** the
[source-specific liquidity/coverage screen](alpha-equity-universe.md#daily-liquidity-and-coverage-screen)
binds all 300 snapshot identities and reuses the daily acquisition/journal path.
The first declared IEX/raw study inspects August 1–September 16 and ranks the last
20 observed sessions by median `close × volume`, selecting up to 64. Eligibility
requires complete trailing coverage, 20/20 positive-volume sessions and a latest
close of at least $5. All members and read failures remain evidence. Known coverage
shortfalls are reported; unknown or malformed source evidence withholds the entire
selection. Source-specific activity is not consolidated liquidity or capacity.
All 300 reads completed; 150 candidates met the rules and 64 were selected.
Independent reconstruction matched all 9,450 raw/normalized bars and every selected
UUID/metric, with no cleaning loss. Journal ordering was verified separately.
Lifetime attempts are 7,640; active alphas remain zero. See the linked actual report.

**Completed next exercise:** the [screened forecast campaign](alpha-panel-forecasts-2026-09-17.md)
retained all 96 declared comparisons across 64 equities and nine ETF controls,
with strictly mature chronological labels and declared cost stress. The equity Ridge
model is a research lead: positive annual basket proxies at both 1 and 5 bp,
but negative 2024 IC and no annual HAC significance. No alpha was promoted. Current membership
and current liquidity select a survivor-conditioned development cohort; retrospective
results cannot establish historical eligibility, a point-in-time tradable universe,
or qualification. Preserve this limitation rather than selecting historical members
using future coverage. Prospective confirmation begins after actual selection.

The separately versioned forecast computation now keeps all frozen members and
uses only past bars for per-date eligibility. Existing <=64 complete-panel/book
contracts remain unchanged. Bounded acquisition checkpointing is shared across
daily studies; all actual forecast/model/basket metrics remain visible.
Do not broaden the panel constant alone, silently intersect available history,
reuse future coverage to select historical members, or substitute unavailable names.
Record coverage and economic results separately, including null or failed outcomes.

**IEX deployment measured:** the first SPY/QQQ session-prefix captures are complete
(255/255 minutes, receipt upper bounds 5.65s/6.11s), while all six 17:45 UTC formula
decisions remain unavailable under the dense historical-minute contract. A raw-data
audit reproduced 2 SPY and 52 QQQ absent minutes with zero local row/value loss.
[Evidence and interpretation](alpha-iex-forward.md#deployed-evidence--september-17-2026).
Record a separate sparse-trade-bar/unknown-execution-price contract and condition-level
source investigation; preserve existing version semantics and failed windows. This
intraday limitation does not block the next native-daily equity liquidity/forecast stage.


### Next work after the screened-equity forecast study

1. **Completed:** the [matched-control and endpoint study](alpha-forecast-controls-2026-09-18.md)
   ran all 90 frozen comparisons. Ridge's original result reproduces, but the
   volatility style has higher mean IC in every year; incremental learning is
   unproven. Strict endpoint evidence withholds four 2023 Ridge baskets. Preserve
   the original result semantics and all comparison/inspection accounting.
2. The [54-comparison factor follow-up](alpha-factor-controls-2026-09-18.md) is
   also complete, with no consistent incremental winner. The
   [prospective daily panel](alpha-daily-panel.md) implements receipt-qualified
   collection with the fixed blend, volatility and residual-blend controls
   plus a newly declared daily Ridge refit, using the existing journal. The source-qualified
   label contract requires positive endpoint volume for training and evaluation; the retained
   historical study did not refit its original finite-price labels. Any changed
   historical fit is a separately charged diagnostic, not a rewrite of this run.
   Persist the eligible universe, strictly mature training cutoff, fitted artifact,
   forecasts and frozen baskets before outcomes arrive. Do not privilege Ridge
   solely for its original absolute return. Keep every model diagnostic until
   complete horizon labels, stability, risk/exposure and execution/borrow evidence
   exist. Current-cohort results cannot enter the formula/bracket promotion path.
3. The [fresh-seed power ablation](alpha-power-diagnosis-2026-09-18.md) is complete.
   Next test horizon-aligned entry/holding lifetimes against the frozen GTC baseline,
   with constant-exposure/null controls and fresh seeds. Migrate legacy one-step IC
   only with horizon-aligned evidence.
   Keep lifetime accounting; calibrate the decision rule rather than lowering
   thresholds to pass the newly inspected lead. Nominal test level and acceptable
   false-positive bound must be distinct protocol parameters.
4. Obtain authoritative instrument subtype, historical constituent/delisting and
   borrow/corporate-action evidence; extend economic/capacity tests and eventual
   executable portfolio ownership before any panel allocation can trade.

No additional genetic/DSL search is justified merely by the previous zero-promotion
count: fixed simple styles and a model now provide concrete leads for prospective comparison.
