# A1b protocol — frozen before evaluation (2026-09-16)

This predeclares the next calibration experiment. It can reject a proposed
statistical procedure or identify uncertainty; it cannot activate an alpha or
change production gates. The [roadmap](alpha-roadmap.md) owns milestone status.
The completed run's [results and limitations](alpha-study-2026-09-16.md) are separate
from this unchanged experimental design.

## Experiments

Use root seed **730241884**, with disjoint hashed namespaces for phase, scenario,
replicate and data/search/resampling. Development and validation are separate;
parameters below remain fixed after viewing development results. Engineering
fixtures use different seeds. Persist the protocol and source/dependency identity
before any computation, then every replicate (including errors and full search
trial histories). A failed/incomplete study cannot pass acceptance.

### Fixed candidate panels

Compare our joint circular-block max-t diagnostic with **ARCH SPA**, using its
conservative upper p-value, stationary bootstrap, analytic studentization and
no nested bootstrap. Both ask whether any of eight fixed candidates has positive
mean return relative to zero. They do not replay formula discovery.

- Profiles: 504 observations, serial correlation 0, cross-correlation 0.25;
  and 1008 observations, serial correlation 0.5, cross-correlation 0.75.
- Gaussian innovations; shift candidate 0's mean by 0, 0.15 or 0.30 standard
  deviations. Pair effects and methods within a replicate; never pool paired
  scenarios as independent observations.
- Block lengths **5, 10, 20**, 199 resamples, nominal one-sided level **0.05**.
  Block 20 is the predeclared primary comparison; others are sensitivity checks.
- Development: 16 replicates/scenario. Validation: 256 null and 128 positive
  replicates/scenario. This is a bounded finite-scenario study, not universal
  error control or an estimate of an independent lifetime trial count.

### Adaptive search and strategy qualification

Replay the actual `AlphaMiner` random and genetic ask/tell algorithms, with seven
catalog definitions plus an explicit synthetic volume control and eight generated
attempts (16 reserved trials). The planted control separates recognition from
the ability to invent a formula; results do not establish real-market discovery power.

Generate causal OHLC from four intrabar log-price increments with martingale drift
correction under the null. A volume pulse affects only the following bar's drift.
Volatility may cluster but return innovations remain conditionally mean-zero under
the null. Synthetic dates are business days, not claimed exchange sessions.

| Scenario | Observations | Pulse spacing | Return effect | Log-volatility persistence |
| --- | --- | --- | --- | --- |
| iid_null | 1000 | 8 | 0 | 0 |
| clustered_null | 1250 | 20 | 0 | 0.8 |
| dense_edge | 2500 | 8 | 0.004 | 0 |
| sparse_edge | 2500 | 20 | 0.02 | 0.8 |

Daily innovation scale is 0.001, log-volatility shock scale 0.25; one stream supplies
five draws per bar (four returns and one volatility innovation), preserving prefixes.
All scenarios use the same production normalization/bracket/cost policy. Study feed
identity remains synthetic. Development has 8 replicates/scenario/method; validation
has 128 null and 64 positive replicates/scenario/method. A per-search deadline of
30 seconds bounds work; interruption remains an incomplete replicate, not evidence
of rejection. Do not discard errors from denominators.

Freeze the top candidate using the miner's existing composite ranking across all
evaluated candidates (display filters disabled). Search sees only the first 80%;
only that one winner's final 20% is examined. This is a predeclared operator-selection
experiment, not a claim that every live operator would choose the same finalist.

Compare, on that frozen winner:

1. All existing scientific qualification gates with the run's reserved trial count
   and discovery Sharpe variance (descriptive local-family comparison).
2. Those same gates with **7049 + 16** trials and fixed prior per-observation Sharpe
   variance **0.0028764548818829777** (primary historical-family sensitivity). This is
   a frozen approximation; it does not read or reconstruct the production ledger.
3. A sample-split alternative: positive one-sided ARCH stationary-bootstrap
   percentile mean lower bound (95% coverage)
   on that winner's holdout, plus the existing holdout Sharpe/trade-count/drawdown
   and doubled-cost requirements. Evaluate blocks 5/10/20, primary block 20. It
   omits discovery/family DSR and IC gates and is **research-only**. Its scope is one
   predeclared selection per fresh holdout, not repeated lifetime qualification.

The alternative is an explicit independent procedure, not filtering rejection
strings or modifying the runtime decision. Shared simulation/statistics provide
the observations. Preserve raw attempts, rejections, selection identity, frozen
discovery hash, holdout evidence, seeds and all comparator outcomes.

## Decision criteria (unchanged by observed results)

Report exact one-sided Clopper–Pearson binomial bounds, with Bonferroni adjustment
across all primary validation endpoints (24 endpoints with this design). Null
acceptance upper bounds must be at most **5%**; dense/strong-positive detection lower
bounds must be at least **80%**. This simultaneous guarantee concerns the single
decision-relevant bound per endpoint (null upper or positive lower), not both
tails together. Sparse strategy power must have a lower bound of
at least **50%**. Weak fixed-panel positives are descriptive. Sensitivity rows have
pointwise 95% one-sided bounds and cannot be substituted for a failing primary result.

Report each procedure's result and a study-wide status. A failure or an inconclusive
bound is useful evidence, not permission to reduce thresholds or rerun with selected
seeds. Even passing all these finite controls does not authorize a new runtime policy;
that requires a separately versioned proposal and fresh real-market/forward evidence.

## References

- [ARCH SPA contract and conservative upper p-value](https://bashtage.github.io/arch/multiple-comparison/generated/arch.bootstrap.SPA.html).
- [SciPy exact binomial confidence intervals](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.binomtest.html).
- [DSR assumptions and independent-trial treatment](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf).
