# Power diagnosis — September 18, 2026

## Result and decision

All **800/800 searches completed**, with no failed jobs, missing jobs or unavailable
full-policy endpoints. The frozen detection criteria were **not met**. The planted
forecast is present; persistent entry orders, search-objective mismatch and joint
statistical criteria prevent reliable recognition. Zero promotions therefore cannot
be read as evidence that our funnel is calibrated. No production gate was changed.

The [predeclared protocol](alpha-power-ablation-plan.md) uses 400 synthetic datasets,
16 expressions per search and paired random/genetic methods. Development has four
replicates per cell; validation has 128 null and 64 positive replicates per cell.
Known-control and selected-winner routes share data and execution policy. These
paired observations are not independent extra samples.

## Full-policy acceptance

Each row below applies separately to random/genetic search and known/winner routes.
Bounds are the frozen one-sided exact binomial bounds, Bonferroni-adjusted across
16 primary endpoints; they are not confidence statements about real-market returns.

| Validation profile | Accepted per endpoint | Adjusted upper bound | Frozen criterion |
| --- | ---: | ---: | --- |
| Dense null | 0/128 | 4.41% | Upper bound ≤5%: met |
| Sparse null | 0/128 | 4.41% | Upper bound ≤5%: met |
| Dense planted effect | 0/64 | 8.62% | Lower bound ≥80%: failed |
| Sparse planted effect | 0/64 | 8.62% | Lower bound ≥50%: failed |

This establishes low false acceptance for these null scenarios, alongside inadequate
positive-control power. It does not establish useful power for a different generator,
market or decision rule.

## Where the signal is lost

Means below use the 64 positive-control validation datasets per profile. Known-route
predictor and execution measurements are identical across the two search methods.

| Known predictor / execution measurement | Dense | Sparse |
| --- | ---: | ---: |
| Holdout one-step Rank IC | 0.376 | 0.158 |
| Incremental next-open/close payoff over constant exposure, bp per observation | 4.39 | 9.29 |
| Eligible pulse opportunities | 62 | 24 |
| Timely entries | 1.19 | 1.33 |
| Pulses blocked by a pending entry | 56.16 | 21.67 |
| Pulses blocked by an open position | 3.39 | 0.00 |
| Pending bars out of 498 execution bars | 453.39 | 452.44 |
| Completed holdout trades | 1.45 | 1.33 |
| Net simulated holdout return | 1.51% | 0.66% |

**Execution conversion is a concrete bottleneck.** The frozen GTC limit policy leaves
an entry pending at every positive known-control holdout boundary. Pending orders
suppress about 90% of later pulses. A short-lived forecast does not imply that a
limit order at an old price remains useful indefinitely. The forecast payoff proxy
has no fills or fees and is not a substitute for bracket economics.

**Selection adds a separate mismatch.** Neither method selects the exact planted
volume expression in any of the 64 positive runs per profile. Selected winners have
mean holdout one-step IC between −0.062 and −0.026, yet their simulated net returns
average 12.5–13.6% in dense and 39.7–40.0% in sparse scenarios. The generator has
positive unconditional drift, and the discovery objective rewards bracket economics;
these outcomes do not demonstrate recovery of incremental one-step information.
The two horizons/objectives must be assessed explicitly rather than conflated.

**Several criteria fail together.** For the known route, the ten-trade holdout minimum
fails in 64/64 dense and 63/64 sparse runs. Both current-family DSR criteria fail in
all positive known and winner runs. Validation IC passes every positive known run,
but fails 44–53 of 64 selected-winner runs, depending on profile/method. Dropping
any single criterion produces no current-family full-policy passes.

## Family sensitivity and provenance

The read-only snapshot at 10:32:24 UTC contains **7,826 lifetime attempts**. The
current native-daily projection exists but contains **zero prior Sharpe samples**.
Each current counterfactual uses 7,842 trials and the evaluated search's variance;
it does not pretend that 7,826 independent daily strategies supplied that variance.
The historical reference remains 7,065 trials with variance 0.0028764548818829777.
Later real-data research charges do not revise this frozen as-of snapshot.

Local 16-trial families produce only 0–1 positive winner passes per 64; the
variance-only sensitivity produces 0–2. Count-only, historical and current families
produce none. Even the known control fails every local-family full-policy endpoint.
Family assumptions matter, but removing lifetime history would not solve the observed
execution bottleneck. These descriptive counterfactuals do not select a replacement
rule or justify erasing research attempts.

Private artifacts retain selection checkpoints before holdout, every expression,
measurement, criterion, trace and completion record. Some null winners have no finite
predictor/outcome pairs; their descriptive forecast metric is explicitly unavailable,
not zero. This does not make their measured policy-gate result unavailable.

- Source: `5d93f45e7358413d3c645a9b6d732564e013826a`, preserved by tag
  `research/power-diagnosis-v1-20260918`.
- Protocol identity: `4e319f808a1e66d86fdaa24a3be947eb16dbefed4b37f51200dd646aab23937b`.
- Completion SHA-256: `7fa70f1b3775d4af2dbfbe8e06f8e55fe8896c5677b5390d622b429d99c1bd94`.
- Manifest SHA-256: `f317317d016c6ee880f9c2b1fbe7d18cc48f837980df593cba1fdf50e975bf3a`.

No runtime database, providers, broker or notifier were constructed by this study.

## Next steps

1. Freeze a new experiment matching forecast horizon to entry/holding lifetime,
   using the existing versioned execution engine and timed-policy contracts.
   Timed policies currently require a session clock: a native-daily variant needs
   an explicit tested clock/version extension, not an existing configuration switch.
   Include constant exposure and null controls; prove causal parity with tests,
   then use fresh development and validation streams. Preserve this failed run.
2. Begin prospective native-daily observations for fixed simple styles and factor
   challengers. Mature labels take time; historical diagnostics cannot replace them.
3. Calibrate any proposed joint decision rule only after forecast-to-policy conversion
   is explicit. Preserve lifetime accounting and distinguish uncertainty in forecast
   skill from uncertainty in executable net returns. Do not lower thresholds here.

## Engineering evidence

TDD and independent review covered causal prefixes, frozen selection, paired family
inputs, complete return timelines, fail-closed missing measurements, mechanical
development stopping, trace parity and durable artifacts. Full source verification:
**2,035 passed / 108 skipped**, then 12 focused regression checks; actual SDK
HTTP/WebSocket plus disposable PostgreSQL: **366 passed**. All-file pre-commit and
all three CI checks passed for the implementation. Deployment verification is a
separate operational step; completing a synthetic study proves no service freshness.
