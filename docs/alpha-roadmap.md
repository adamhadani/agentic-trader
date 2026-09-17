# Alpha research roadmap

**Active long-horizon plan — updated 2026-09-17.** This is the canonical alpha work
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

## Ordered work queue

| ID | Status | Milestone / acceptance boundary |
| --- | --- | --- |
| A1a | Implemented — [PR #35](https://github.com/adamhadani/agentic-trader/pull/35) | Calibration instruments and permanent control/reference tests; diagnostic outputs cannot authorize promotion. [Pilot evidence](alpha-calibration-2026-09-16.md). |
| A1b | Implemented — [PR #36](https://github.com/adamhadani/agentic-trader/pull/36), [study results](alpha-study-2026-09-16.md) | All 1,952 jobs retained; scientific status incomplete (22 unavailable comparisons). No replacement gate accepted. |
| A2 | A2a implemented — [PR #37](https://github.com/adamhadani/agentic-trader/pull/37); A2b replay groundwork — [PR #38](https://github.com/adamhadani/agentic-trader/pull/38) | [Replay contract](alpha-session-replay.md) and [four-run evidence](alpha-session-replay-2026-09-16.md): SIP coverage complete, IEX incomplete in both windows. Live signal-clock migration and broker execution observations remain; intraday promotion stays blocked. |
| A3 | Forecast benchmark prerequisite — [PR #43](https://github.com/adamhadani/agentic-trader/pull/43); [PR #44](https://github.com/adamhadani/agentic-trader/pull/44) timing/cost screen complete; standalone open-gap policy paused; combined execution shadow-only | Distinguish forecast components from a fully specified tradable strategy; validate combinations causally. |
| A4 | Planned | Broader economic hypotheses and point-in-time universe/data coverage. |
| A5 | Planned | Bounded research campaigns and a shadow observation universe independent of trading permissions. |

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
**Current: A2b**, session/fine-bar execution replay. A2a corrected the return clock
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

Expand toward 100–200 liquid equities only with point-in-time membership, delistings,
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
bug explains the outcome; no threshold or direction was changed. Latest lifetime
attempts: **7,312**; registry generation 10, active 0/shadow 10, no new qualification.

**Next ordered increment:** resolve the recent-SIP/deadline prerequisites and contain
the divergent legacy retuner identified in the [forecast-to-fill follow-up](#forecast-to-fill-follow-up).
Then add lossless raw-provider/normalization provenance and complete the retained
SPY-minute postmortem. Freeze a small economically different, slower-turnover
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
   are removed. Recent-SIP permission still requires an operator entitlement/feed
   decision; success of a process or empty request is not freshness evidence.
2. **Next: automated data provenance.** The [SPY-minute postmortem](alpha-spy-minute-postmortem-2026-09-17.md)
   found all four gaps already absent from two fresh raw SIP responses, with no cleaning
   loss or revisions in 16,654 shared rows. Original failed evidence remains immutable.
   Automate lossless raw-row/normalization retention at the shared data boundary,
   including failed parsing/interrupted pagination; forensic capture alone is insufficient.
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
5. **A3 execution gate.** Version target/plan identity, rounding, stale-snapshot
   rejection, partial-fill attribution and protection before any paper execution.
   Reuse entry FIFO, close services, journal and outbox. Verify intermediate exposure
   and actual fills through SDK HTTP/WebSocket plus disposable PostgreSQL tests.

Defer MPC, advanced impact scheduling, CVaR and new asset classes
until these contracts and a credible economic case exist. The tutorial's optimization
methods are useful tools, not evidence that our current hypotheses have an edge.
