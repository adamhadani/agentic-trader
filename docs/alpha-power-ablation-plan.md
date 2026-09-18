# Alpha power diagnosis plan

**Planning status — September 18, 2026.** This document specifies the next bounded
research diagnosis. No runner, frozen execution protocol, new study, statistical
tolerance or production gate change is delivered by this documentation change.
Follow the [canonical roadmap](alpha-roadmap.md); prospective collection is a
separate track that can accumulate evidence while this diagnosis is developed.

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

Three confounders need explicit controls:

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
`0.0028764548818829777`. The [September 18 survey](alpha-stack-survey-2026-09-18.md)
last recorded **7,826 attempts**; neither that count nor a current variance has
been refreshed for this plan. Attempts are not independent economic hypotheses.

Before any validation run, freeze a read-only, explicitly sourced snapshot of the
current global trial count and applicable native-daily/return-timeline variance
family. Retain source journal revision, capture time, family identity, variance
observation count, variance value and artifact hash. A historical scalar or the
last documented count cannot silently substitute for missing current evidence.
If unavailable, mark the current-family comparison unavailable and retain only
explicitly labelled historical/local sensitivity results.

Production qualification obtains global count and narrower variance evidence in
`storage/alpha.py:begin_holdout`; that method **consumes a holdout and writes the
journal**, so do not invoke it to export a diagnostic snapshot. The offline runner
must consume an immutable snapshot artifact without runtime DB construction.

The frozen protocol must state whether a counterfactual adds the 16 newly searched
candidates to its starting count and how it updates variance. An exact replay of
that update requires sufficient frozen variance-family evidence; simply adding 16
to a count while holding variance fixed is a labelled sensitivity approximation.
Each synthetic replicate is an independent counterfactual; synthetic trials remain
in local study accounting and never increment the production research ledger.

## Proposed finite matrix

This is an implementation target, not a promise to execute the matrix this turn.
Review numerical budgets and freeze the complete protocol and source revision
before running it. Development may reveal mechanical defects; changing a scientific
choice requires a new protocol, and inspected validation seeds cannot be reused.

| Dimension | Proposed fixed design |
| --- | --- |
| Profiles | Dense pulses every 8 bars with independent volatility; sparse pulses every 20 bars with clustered volatility |
| Effects | Matched null/positive pairs: 0/.004 dense and 0/.02 sparse, retaining A2a effect sizes |
| Length | 2,500 native synthetic daily bars for every profile/effect; equal null and positive lengths |
| Development | 4 seeds per profile/effect |
| Validation | 128 null and 64 positive seeds per profile |
| Search | Existing random and genetic methods; existing 16-expression budget and discovery-only selection |
| Routes | Known volume control and selected winner from each same search run |
| Execution | Existing bracket/limit policy, complete cash-inclusive return clock and doubled-cost stress |
| Sensitivity | Cross local versus historical count with local versus historical variance, varying one factor at a time; add separately identified current-family evidence only when sourced |

The proposed acquisition-free budget is **400 datasets, 800 searches and 12,800
expression evaluations**. Known controls are already inside each search catalog;
their additional held-out evaluations and all gate comparisons must also appear
in the frozen compute budget. Do not rerun a control as an independent one-trial
search or count its matched outcomes as independent discoveries. Fixed-return-panel
comparisons from A2a are outside this diagnosis.

Use a fresh root seed and versioned namespaces. Null/positive cases share innovations
within their profile; random/genetic methods share the same generated dataset.
Development, validation, search and resampling have separate streams. Current
`evaluate_job` derives seeds from scenario names, so paired effects need an explicit
coupling identity rather than two independently named scenarios.

Freeze the set of primary endpoints, interval multiplicity correction, missing-job
semantics and resampling settings. The nominal statistical test level, confidence
level for estimated error/power, maximum tolerable false-positive rate and minimum
useful power are **different protocol parameters**. Their scientific tolerances
remain pending protocol review; this plan approves no new `.10` or other error
bound. The earlier nominal `.05` and dense/sparse power targets `.80/.50` are
reference choices, not newly calibrated conclusions. Calculate attainable interval
precision before freezing; an inconclusive bound is not proof of miscalibration.

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

## Minimal implementation and RED tests

Reuse `study.py`, `AlphaMiner`, `simulation.py`, `promotion.assess_statistical_evidence`
and `study_artifacts.py`; keep pure computation separate from artifact I/O. Factor
shared measurements from criterion evaluation so family sensitivities do not need
another simulator or repeated bootstrap computation. Preserve existing full-policy
decisions exactly. Extend the existing bounded artifact workflow through injected
job/evaluation/summary functions instead of a parallel journal or campaign queue.

Write the smallest failing behavioral tests first, parameterized where appropriate:

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

After implementation, report RED/GREEN tests, tiny mechanical integration results,
the committed frozen protocol and any actual study execution separately. The full
matrix is research work with an explicit run record, not part of this docs-only
delivery. Keep prospective observations collecting under their separate contracts;
broader adaptive search waits for the diagnosis and its recorded next decision.
