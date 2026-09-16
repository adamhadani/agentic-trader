# A2a return-timeline study — 2026-09-16

## Decision and progress

**Ship the accounting correction; retain the statistical gates.** All **1,952**
planned jobs completed with zero unavailable comparisons. The eight primary
adaptive-search null endpoints and four strong fixed-panel endpoints met their
criteria; the other twelve did not. Overall status is `criteria_not_met`, not an
incomplete computation. No alpha is qualified or promoted by this synthetic study.

This removes a measurement defect without demonstrating adequate discovery power.
Next is **A2b session/finer-bar execution replay**, followed by **A3 forecast versus
strategy alignment**, as recorded in the [roadmap](alpha-roadmap.md). Widening the
universe or search budget now would amplify unresolved execution/objective issues.

## Frozen design and provenance

- Source revision: `312639693e93d34f25bd4bfd8a26c31c08436e75`, committed before the run.
- Protocol: [a2a-v1.json](../config/research/a2a-v1.json), SHA256
  `1038e35b873a6ca8c8d9e6e1f77df5ccf6a266a5ff42c16d64043e6bf3d670d6`.
- Fresh root seed: **10472909262026**; separate development/validation namespaces.
- Completion SHA256: `c2fd10c467d886ab4b27c65628db2ecb2fc761a4fa6089e9c4722f121c5af8d9`.
- Python 3.14.7, ARCH 8.0.0, NumPy 2.5.3, pandas 3.0.5, SciPy 1.18.1.
- **832 adaptive-search and 1,120 fixed-panel jobs**, comprising 160 development
  and 1,792 validation jobs. **13,312 reserved/evaluated synthetic trials**.
- Zero missing records, failed jobs, unavailable endpoints or search timeouts.
  Approximately **931 seconds** of replicate computation; CLI exit code zero.
- Protocol, manifest, every replicate and completion remain in private research
  storage. No production DB, provider, broker or notifier is constructed.

The [return contract](alpha-return-timeline.md) and fresh seeds are the changes
from the [A1b design](alpha-study-protocol.md). Budgets, selection, costs, thresholds,
effect sizes, blocks and acceptance criteria stayed frozen throughout evaluation.
The frozen 7,049 historical attempts and variance scalar are a synthetic sensitivity
assumption, not an estimate of the newly partitioned production variance family.
No holdout result changed candidate selection or the implementation during this run.

The [original A1b report](alpha-study-2026-09-16.md) and its 22 unavailable comparisons
remain unchanged. These are fresh streams, so differences in detection counts are
not paired estimates of the fix's effect. Fixed-panel calculations themselves did
not change; their different counts reflect fresh samples.

## Adaptive search: validation results

Both methods use the real miner, including an explicit known synthetic control,
and freeze one discovery-ranked winner before examining holdout. This tests
recognition of controlled effects, not invention of unknown financial alpha.

### Null cases

Each of independent/clustered nulls × random/genetic search has **0/128** acceptances
for current lifetime and block-20 holdout-only procedures. Each simultaneous
one-sided upper bound is **4.71%**, meeting the predeclared 5% bound. Local-family
and block-5/10 sensitivity comparisons also have zero acceptances.

This is error-control evidence for these finite synthetic scenarios and frozen
procedures; it is not universal lifetime error control or a probability of live profit.

### Planted effects

`Lifetime` uses the frozen 7,065-attempt approximation; `local` uses 16 attempts.
The alternative uses stationary bootstrap plus holdout economic gates, without
discovery/family DSR or the IC gate. Bounds are Bonferroni-adjusted over the 24
primary endpoints. All rows have complete outcomes.

| Scenario | Search | Replicates | Lifetime | Local | Alternative | Alternative power lower bound |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Dense | Random | 64 | 0 | 4 | 28 | 26.46% |
| Dense | Genetic | 64 | 0 | 1 | 23 | 19.91% |
| Sparse | Random | 64 | 0 | 0 | 17 | 12.67% |
| Sparse | Genetic | 64 | 0 | 0 | 12 | 7.29% |

Required lower bounds were 80% dense and 50% sparse; none passed. The alternative
made identical decisions at blocks 5/10/20. These results do not justify replacing
the lifetime gate, reducing the trade minimum, or favoring a search algorithm.

## Fixed candidate panels

Primary block length 20. These comparisons do not replay adaptive discovery.

| Profile | Procedure | Null acceptances | Null upper bound | Strong acceptances | Strong power lower bound |
| --- | --- | ---: | ---: | ---: | ---: |
| Independent | Joint max-t | 13/256 | 10.31% | 128/128 | 95.29% |
| Independent | ARCH SPA upper | 12/256 | 9.79% | 128/128 | 95.29% |
| Dependent | Joint max-t | 15/256 | 11.33% | 128/128 | 95.29% |
| Dependent | ARCH SPA upper | 17/256 | 12.34% | 128/128 | 95.29% |

Strong-positive power passes; null upper bounds remain above 5%. That is inconclusive
calibration, not proof that true size exceeds 5%. Requiring an upper confidence
bound at or below a test's nominal size is stringent even for a correctly sized test.

Dependent-panel null counts at blocks 5/10/20 are 23/20/15 for joint max-t and
24/21/17 for SPA. Weak-effect block-20 detection is 110/128 for both independent
procedures and 89/128 versus 91/128 under dependence. These descriptive sensitivities
do not select a replacement gate after viewing validation results.

## Remaining obstacles to promotable strategies

1. **Execution coverage:** dense winners have median seven completed holdout trades.
   Random/genetic runs fall below ten trades in 36/64 and 41/64 cases, with a pending
   entry at the boundary in 43/64 and 42/64. Sparse medians are nine; 47/64 and 52/64
   miss the minimum. A forecast can be useful while a pending limit misses its move.
   A2b must test actual sessions and finer execution bars before changing policy.
2. **Objective alignment:** next-step IC fails in 48/64 and 49/64 dense winners,
   and 44/64 and 48/64 sparse winners. Lifetime holdout DSR fails in 60/64 and 57/64
   dense cases and every sparse case. Rejection reasons overlap; these are not
   independent causal effects. A3 must separate forecast horizon/coverage from
   the complete tradable holding/bracket policy.
3. **Forward evidence:** synthetic results earn no shadow dates, decisions or
   deployment-feed credentials. Actual candidates still need untouched data,
   current-policy qualification, complementary evidence, and the existing entry,
   risk and protection boundaries. There is no quota-driven promotion.

## Correctness evidence distinct from fresh validation

A diagnostic replay of the 22 historical failures under the new simulator produces
complete finite return clocks. The known all-cash example changes from 24 observed
returns to all 248 supplied bars: 24 scored, 224 unscored, zero trades and zero
profit. This does not repair/reclassify the original study or create new evidence
for qualification.

Review also reproduced a hidden-loss case: a pending order fills and stops out on
the same bar after its feature disappears. The old return series reported **0%**
despite a **−2.198% long / −2.202% short** trade including both fees. With identical
entries and exits, the new series matches those ledger losses. Parameterized
regressions now cover both directions. This is an accounting fix, not extra alpha.

TDD covers cash/feature clocks, held positions, missing prices, causal prefixes,
strict return consumers, evidence versioning, lifetime counts, variance partitioning,
journal replay and entry authorization. Initial full CI passed **952 tests** with
17 intentional skips; **62 real TCP/WebSocket/PostgreSQL integration tests** passed.
After the final two same-bar regressions, the focused timeline suite passed **38**.
Final CI and deployed verification are recorded separately in
[PR #37](https://github.com/adamhadani/agentic-trader/pull/37).

The installed paper daemon stayed ready during the isolated, single-process,
reduced-priority study. That observation is separate from deployment verification
and does not establish order-submission behavior or general research-worker isolation.
