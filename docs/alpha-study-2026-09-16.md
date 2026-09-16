# A1b calibration study — 2026-09-16

## Decision

**Keep the production gates unchanged; prioritize A2 execution/observation semantics.**
The predeclared study processed every job, but its scientific status is **incomplete**:
22 adaptive-search replicates could not supply all holdout comparisons. Fixed-panel
strong-positive controls passed the power criterion; no other primary endpoint
passed its criterion. This is diagnostic evidence, not permission to promote alphas.

The [roadmap](alpha-roadmap.md) owns next steps. The [frozen protocol](alpha-study-protocol.md)
and [machine-readable configuration](../config/research/a1b-v1.json) were committed
before evaluation. No thresholds, seeds, candidate selection or numerical procedures
were changed after inspecting development or validation observations.

## Provenance and accounting

- Scientific run revision: `b273946b96f7c466b0ab235e4eabb381a7b8c0a4`.
- Protocol SHA256: `918a6c28e3abe6db220fa9e08db9ad6bf1b8fc1f71340e36e383eda2f5ca0a59`.
- Completion artifact SHA256: `820e8af694aa0aaeb4d53d56b36dff7e72d9a629a8d4bddd0f0ab6cfb016a999`.
- Python 3.14.7; ARCH 8.0.0, NumPy 2.5.3, pandas 3.0.5, SciPy 1.18.1.
- **1,952/1,952 records retained**, zero missing: 832 adaptive-search and 1,120
  fixed-panel jobs. Development contributes 160; validation contributes 1,792.
- **13,312 reserved synthetic discovery trials**, all search budgets completed.
  These trials do not enter the production research ledger.
- 1,930 complete comparisons; 22 unavailable comparisons, all in validation:
  20 lacked the current policy's bootstrap observations, two lacked the alternative's
  minimum return length. No unexpected exception or search timeout occurred.
- Approximately 931 seconds of replicate computation, single-process and reduced
  priority. Raw protocol, manifest, every replicate and completion summary remain
  under the private research state directory, outside Git.

The CLI correctly exited nonzero for the incomplete study. Failed jobs remain in
all planned denominators. An endpoint with unavailable decisions has no reported
point estimate; lower bounds treat unknown decisions as failures and upper bounds
treat them as successes. Do not silently convert them to rejected candidates.

## Adaptive search: held-out validation

These compare one finalist chosen by discovery-only composite ranking, with identical
selection for all procedures. `Current lifetime` uses the frozen 7,065-trial family
approximation; `local` uses 16 trials. The alternative is the block-20 stationary
bootstrap plus holdout economic gates, with no discovery/family DSR or IC requirement.
It is not a proposed drop-in replacement or a lifetime error-control claim.

### Null scenarios

All procedures recorded zero acceptances. Unavailable comparisons prevent claiming
a zero false-positive rate. Bounds below apply to both primary procedures and use
the full planned denominator with conservative missing-outcome treatment.

| Scenario | Search | Planned | Unavailable | Simultaneous upper bound |
| --- | --- | ---: | ---: | ---: |
| Independent returns | Random | 128 | 2 | 7.82% |
| Independent returns | Genetic | 128 | 1 | 6.39% |
| Clustered volatility | Random | 128 | 4 | 10.38% |
| Clustered volatility | Genetic | 128 | 2 | 7.82% |

### Planted return effects

Counts are known acceptances out of the **planned** sample, accompanied by unavailable
counts; they are not complete-case power estimates. The final column is the
alternative's conservative lower bound. Required lower bounds were 80% dense and
50% sparse; none passed.

| Scenario | Search | Planned | Unavailable | Current lifetime | Local | Alternative | Alternative lower bound |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Dense | Random | 64 | 0 | 0 | 5 | 26 | 23.79% |
| Dense | Genetic | 64 | 1 | 0 | 2 | 26 | 23.79% |
| Sparse | Random | 64 | 6 | 0 | 0 | 11 | 6.31% |
| Sparse | Genetic | 64 | 6 | 0 | 0 | 14 | 9.36% |

The alternative made identical decisions at block lengths 5/10/20. Its failures
therefore cannot be addressed merely by picking a different block in this study.
The planted next-bar return effect is not a guarantee of executable bracket profit.
These experiments measure recognition with an explicit known control in the catalog,
not the ability to invent unknown profitable formulas in financial data.

## Fixed candidate panels

Primary block length 20; these panels do **not** replay adaptive discovery. Null rows
have 256 validation replicates; positive rows have 128. Bounds are the predeclared
decision-relevant one-sided bounds, Bonferroni-adjusted over 24 primary endpoints.

| Profile | Procedure | Null acceptances | Null upper bound | Strong effect acceptances | Strong power lower bound |
| --- | --- | ---: | ---: | ---: | ---: |
| Independent | Joint max-t | 15/256 (5.86%) | 11.33% | 128/128 | 95.29% |
| Independent | ARCH SPA upper | 15/256 (5.86%) | 11.33% | 128/128 | 95.29% |
| Dependent | Joint max-t | 18/256 (7.03%) | 12.83% | 128/128 | 95.29% |
| Dependent | ARCH SPA upper | 15/256 (5.86%) | 11.33% | 128/128 | 95.29% |

The upper bounds exceed the required 5%; this does **not** establish that true size
exceeds 5%. The criterion is deliberately stringent: even an exactly 5%-size method
will usually fail to establish an upper bound at or below 5% from a finite sample.
Do not interpret inconclusive calibration as proof of miscalibration.

Sensitivity: under dependence, null acceptances for joint max-t were 26/22/18 at
blocks 5/10/20; SPA yielded 24/18/15. Dependence/block choice matters. The weak-effect
primary comparisons detected 104/128 and 106/128 in independent panels, versus
88/128 and 87/128 under dependence (joint/SPA respectively). These are descriptive,
paired comparisons; they do not establish superiority of a method or search algorithm.

## Findings and next regressions

1. **Define the complete execution return timeline before further calibration.**
   The simulator records NaN while flat when the preceding feature score is missing;
   later statistics drop those bars. A deterministic diagnostic replay of validation
   `clustered_null/genetic/71` had 248 holdout bars, 224 missing preceding scores,
   only 24 finite return observations, zero entries and zero closed trades. An
   undefined feature is distinct from missing prices: known cash equity still has a
   return on the execution clock. A2 must test warmup, intermittent undefined scores,
   pending orders and held positions separately, preserving zero cash returns where
   appropriate and rejecting genuinely unknown valuations. Recheck annualization,
   resampling spacing, DSR sample length and explicit feature coverage. This study
   preserves the existing semantics; it does not reclassify its failures afterward.
2. **Execution coverage limits detection as well as statistical gates.** Among
   available dense holdout summaries, random/genetic median completed trades were
   6.5/8; 38/64 and 37/63 had fewer than ten trades, and 43/64 and 39/63 ended with a
   pending entry. Sparse medians were 9/9; 47/58 and 44/58 were below ten trades.
   Pending limits can miss the initial move. A2 must replay order eligibility on
   actual sessions/finer bars; do not lower the trade minimum to hide this behavior.
3. **Forecast and strategy objectives remain different.** Dense winners failed
   one-step IC in 43/64 random and 50/64 genetic runs; sparse counts were 37/64 and
   51/64. Lifetime holdout DSR rejected most available dense and all available sparse
   winners. Reasons overlap and are not independent causal attributions. A3 should
   separate forecast horizon/coverage from tradable holding policy before optimizing
   ensembles or interpreting a profitable strategy as a good next-bar predictor.
4. **Retain the existing conservative gates.** Replacing lifetime DSR with the
   sample-split comparator alone did not meet the declared criteria. After A2 changes
   simulation semantics, version the policy and predeclare fresh validation streams;
   this report becomes historical evidence, not a reusable qualification holdout.

## Implementation review and verification

Computation is a pure research module; the artifact adapter owns exclusive, atomic
local persistence, and the async CLI offloads computation. There is no runtime DB,
provider, notifier or broker construction. Existing scoring, mining, simulation and
scientific assessment are reused; ARCH supplies established reference procedures.

TDD covered protocol validation, causal prefixes and lagged planted effects,
future-holdout perturbation, reference SPA orientation, full trial accounting,
missing/duplicate endpoint rejection, simultaneous confidence bounds and isolated CLI
execution. Fault injection additionally showed that an unexpected exception after
mining could discard completed search detail. The reviewed fix retains the frozen
winner, full search and partial assessments while marking all matched outcomes
unavailable. Both injected assessment/bootstrap failures now pass their regressions.
A successful development replicate replay matches every scientific field from the
original run; this is implementation parity evidence, not new validation.

Persistence is per completed/failed replicate. A hard process kill mid-replicate
retains the initial reserved budget and previous records, but not every in-memory
trial of that interrupted replicate. Durable per-trial campaign recovery remains A5.

Before the final review fix, CI passed 903 tests and 60 real transport/PostgreSQL
integration tests; the final affected suite passed 33 tests. Full final CI and deployed
verification are recorded separately in [PR #36](https://github.com/adamhadani/agentic-trader/pull/36).
The running paper daemon stayed ready during the study. That observation does not
prove a deployment of this branch or exercise broker order submission.
