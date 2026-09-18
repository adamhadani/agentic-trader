# Alpha power diagnosis plan

**Harness implemented; full study pending — September 18, 2026.** `alpha power-plan`
and `alpha power-study` now compose this diagnosis through the existing synthetic
artifact workflow. Review fixes and final verification are in progress; this document
does not claim a completed 800-search run. Freeze the protocol/source revision before
validation. Follow the [canonical roadmap](alpha-roadmap.md); prospective collection
is separate, and the [54-comparison factor experiment](alpha-factor-research-plan.md)
is the following retained-data research priority. No promotion gate changes.

## Question and existing evidence

Determine where a known causal predictor loses power: feature measurement, search
selection, conversion into bracket trades, or the statistical decision rule.
Complete this diagnosis before increasing adaptive search budgets or adding more
search algorithms. Zero promotions alone distinguishes none of those explanations.

The [A2a study](alpha-timeline-study-2026-09-16.md) completed all 1,952 jobs but
accepted zero of 64 planted cases in each of four lifetime-gate groups. Its
holdout-only alternative accepted 12–28 of 64. That comparator removes multiple
criteria at once; it cannot attribute failure to one threshold. Original protocols,
artifacts, failures and decisions remain immutable.

The new harness separates three confounders in that earlier study:

- `study.search_comparison` follows only the discovery-selected winner through
  holdout, although `study_catalog` includes the known volume predictor.
- `_compare_frozen_winner` changes both family trial count and Sharpe variance
  between its local and historical comparisons.
- The synthetic drift lasts one following bar, while the shared policy submits
  a tick-rounded prior-close GTC limit and can remain pending or invested across
  later pulses. Missed entries and suppressed opportunities are possible execution
  losses, not established defects without paired evidence.

The existing positive-control regression in `test_alpha_calibration.py` uses the
older generator and a one-candidate search. It does not establish power for the
exact A2a generator, search family and selected-winner pipeline.

## Family provenance before validation

**7,065 is a historical sensitivity reference, not the current lifetime count.**
A2a used 7,049 historical attempts plus its 16-trial search, with variance
`0.0028764548818829777`. A read-only projection snapshot now confirms **7,826 global
attempts and an empty native-daily Sharpe sample** in the current-timeline
projection (the projection itself exists). Confirmed empty observations mean zero prior samples; an unavailable or failed source read
must never be interpreted as an empty family.

Before any validation run, freeze a read-only, explicitly sourced snapshot of the
current global trial count and applicable native-daily/return-timeline variance
family. Retain source journal revision, capture time, family identity, underlying
Sharpe observations and artifact hash. A historical scalar cannot substitute for
missing current evidence.
If unavailable, mark the current-family comparison unavailable and retain only
explicitly labelled historical/local sensitivity results.

Production qualification obtains global count and narrower variance evidence in
`storage/alpha.py:begin_holdout`; that method **consumes a holdout and writes the
journal**, so do not invoke it to export a diagnostic snapshot. The offline runner
must consume an immutable snapshot artifact without runtime DB construction.

Each current-family counterfactual appends that search exactly once, reproducing
`record_run`: all 16 attempts enter the count, and evaluated candidates' per-bar
Sharpes enter the variance sample. Thus the sourced case has **7,842 attempts** and
variance estimated from that run's evaluated samples only; fewer than two samples
leaves variance unavailable. It is not a variance estimate from 7,826 independent
hypotheses. Replicates are separate counterfactuals against the same frozen prior,
not cumulative updates. Synthetic accounting never increments the production ledger.

## Implemented protocol and finite matrix

`alpha power-plan --seed FRESH_SEED --family-snapshot SNAPSHOT --output PROTOCOL`
creates the immutable contract without observations. `alpha power-study PROTOCOL
--family-snapshot SNAPSHOT --output NEW_DIRECTORY` verifies that same snapshot and
runs the artifact-only study off the event loop. These are schematic arguments;
the fresh seed and private artifact paths belong to the committed run record.
Neither command constructs runtime storage, a provider, broker or notifier.

Development may reveal mechanical defects; changing a scientific choice requires a
new protocol, and inspected validation seeds cannot be reused.

| Dimension | Default frozen design |
| --- | --- |
| Profiles | Dense pulses every 8 bars with independent volatility; sparse pulses every 20 bars with clustered volatility |
| Effects | Matched null/positive pairs: 0/.004 dense and 0/.02 sparse, retaining A2a effect sizes |
| Length | 2,500 native synthetic daily bars for every profile/effect; equal null and positive lengths |
| Development | 4 seeds per profile/effect |
| Validation | 128 null and 64 positive seeds per profile |
| Search | Existing random and genetic methods; existing 16-expression budget and discovery-only selection |
| Routes | Known volume control and selected winner from each same search run |
| Execution | Existing bracket/limit policy, complete cash-inclusive return clock and doubled-cost stress |
| Sensitivity | Four local/historical count × variance cells, plus the separately sourced current-family counterfactual |

The acquisition-free budget is **400 datasets, 800 searches and 12,800 expression
evaluations**, with at most 1,600 route evaluations and 8,000 family assessments.
Known controls are already inside each search; coincident control/winner routes
reuse measurements. Never rerun the control as a one-trial family or count matched
outcomes as independent discoveries. A2a's fixed-return panels are outside scope.

Use a fresh root seed and versioned namespaces. Null/positive cases share innovations
within their profile; random/genetic methods share the same generated dataset.
There are **264 distinct innovation streams**. Development, validation, search and
resampling use separate namespaces; explicit profile identities couple effects.

There are **16 primary endpoints**: current-family full-policy acceptance for each
profile × null/planted effect × method × route. Exact one-sided binomial bounds use
Bonferroni allocation of `.05/16`, giving simultaneous 95% coverage for the selected
primary bounds. Retain the earlier reference criteria: null upper bound at most
`.05`, dense power lower bound at least `.80`, sparse at least `.50`. These are
diagnostic reference criteria, not newly accepted live thresholds. The null rule is
stringent: with 128 replicates it requires zero false accepts; failing it does not
prove the underlying false-positive probability exceeds `.05`.

The unchanged qualification bootstrap is separately frozen: moving blocks of
10 observations, 1,000 samples, and `.025/.975` mean-return quantiles. Its nominal
two-sided 95% interval is distinct from the across-replicate confidence level and
maximum false-positive bound. Unavailable comparisons keep their full denominators
and pessimistic bounds; they cannot become rejections or silent successes.

Full-policy outcomes are primary; each individual-gate counterfactual is descriptive.
Do not choose a replacement decision rule on the validation outcomes. Such a rule
would require its own predeclared acceptance criteria and fresh validation streams.

## Required stage evidence

1. **Predictor.** Retain causal pulse times, normalized features, complete observation
   support, one-step target timestamps/availability and forecast statistics. Compare
   with a constant-exposure control so unconditional positive drift is not mistaken
   for incremental information. Any next-open-to-close payoff is an explicitly
   labelled bar-price diagnostic, not a broker fill or replacement execution policy.
2. **Selection.** Persist the entire search, known-control discovery rank and chosen
   winner identity before holdout evaluation. Assess both routes on identical folds,
   clocks, costs and family evidence, retaining equality when the known control wins.
3. **Execution.** Retain eligible opportunities, valid intents, created orders,
   entries and completed trades, plus pending/holding durations, suppressed pulses,
   boundary censoring and fees. Reuse `simulate_execution(trace=True)` through the
   existing strategy adapter; tracing cannot alter decisions or returns.
4. **Decision.** Record each criterion's measured value, threshold and
   pass/fail/unavailable state. Derive full and drop-one outcomes from those same
   measurements; retain co-failure patterns and the count/variance sensitivity.
   Never manufacture a pass by deleting rejection strings or hiding unavailable data.

Interpret the paired results as follows:

| Observation | What it diagnoses |
| --- | --- |
| Planted control lacks the expected causal forecast effect | Generator, target clock, feature/support or measurement problem; gates are not yet the explanation |
| Known control succeeds, selected winner loses forecast skill | Search objective or selection loss under the same discovery budget |
| Forecast skill exists but bracket outcomes lose it | Entry timing, persistent pending/holding state, protection, costs or horizon mismatch |
| Economically successful known/winner routes fail otherwise valid individual criteria | A concrete gate-power/alignment hypothesis to test, with paired null error evidence |
| Count-only change destroys acceptance at fixed variance | Family-size sensitivity, distinct from variance estimation; not permission to erase trials |
| Several gates fail together | Joint incompatibility; single drop-one failure cannot establish that one criterion caused the outcome |

Evidence of overfiltering requires preserved causal skill and credible execution
economics, rejection attributable to specified criteria, and bounded false-positive
behavior for the proposed alternative on untouched controls. A low acceptance rate,
profitable synthetic path, or post-hoc drop-one pass alone establishes none of these.
Synthetic results never qualify an alpha, create shadow dates or authorize trading.

## Implementation and review acceptance

`power_study.py` reuses the causal generator, `AlphaMiner` and shared scientific
measurements; `power_trace.py` observes the existing execution engine.
`power_artifacts.py` injects jobs/evaluation/summary into `study_artifacts.py`.
Family sensitivities reuse immutable measurements without another bootstrap or
execution policy. No parallel journal or campaign queue is introduced.

Accepted review fixes must be verified before full execution: mechanically
unavailable development measurements prevent access to validation; predictor and
execution windows include the same first decision; bootstrap settings enter the
protocol identity; and cofailure summaries separate phase, profile, effect, method
and route, with unavailable criteria distinguished from scientific failures.

RED-first regression coverage and tiny integration fixtures address:

- Future-bar mutation leaves discovery selection unchanged; synthetic prefixes are
  causal; matching innovations and independent phase/role streams are reproducible.
- Control and winner share folds, family evidence and costs; evaluating the control
  does not replace its 16-trial family with a one-trial family.
- Count-only and variance-only comparisons remain distinct; an unsourced current
  family is unavailable, never replaced by the historical reference.
- Structured criterion conjunction exactly reproduces existing full-policy results;
  missing measurements remain unavailable in full and drop-one comparisons.
- Trace on/off preserves all fills and cash returns. Hand-computable pending-limit
  and long-holding fixtures account for blocked later pulses and boundary censoring.
- Protocol/reservations and winner identity persist before holdout access; failed
  comparisons retain their denominators and original artifacts cannot be overwritten.
- A small CLI/artifact integration fixture exercises the actual miner, simulator and
  gates while runtime config, DB, provider, broker and notifier construction fails.

Record final test results, the committed protocol and actual study completion
separately. The [full run completed all 800 searches](alpha-power-diagnosis-2026-09-18.md),
with no unavailable endpoints and insufficient positive-control power. The subsequent
[bounded factor comparison](alpha-factor-controls-2026-09-18.md) completed 54 retained-data
comparisons of existing styles, skipped-month momentum, causal residual momentum
and incremental blend value. Prospective collection remains separate; a reused
historical lead still needs fresh observations before paper allocation.
