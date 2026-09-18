# Retained factor controls

**Implementation under final verification — September 18, 2026.** The bounded
[factor plan](alpha-factor-research-plan.md) is implemented; the full retained-data
experiment has not yet run. This is historical development, not qualification.

## Shared architecture

`alpha factor-controls PROTOCOL --parent DIRECTORY --output NEW_DIRECTORY` uses
`AlphaPanelService` and `RetainedPanelSource`. The existing workflow reserves the
whole comparison budget and excludes every inspected parent member/interval before
reading any retained observations. Exact parent result, input, dataset and calendar
hashes bind the data. New receipts describe artifact reads, not fresh market-data
observations. A failed or repeated attempt cannot erase its charge.

The original forecast-controls command uses the same CLI composition, retained
parent validation, Ridge reproduction and paired-comparison kernels. No new journal,
provider, simulator or research queue is introduced. Computation runs off the event
loop. No broker, notifier, registry activation or shadow credit is involved.

## Frozen scientific contract

The real protocol declares 64 equities, nine named sector ETFs and three annual
folds from the retained IEX parent: six score arms × three folds × (Rank IC plus
1/5 bp per-side economics) = **54 charged comparisons**. ETF returns are explanatory
proxies, not verified company sectors or a replication of Fama–French factors.

- Preserve the original Ridge forecasts and reproduce their original accounting
  before applying common support. No supervised model is refitted.
- Compare Ridge, volatility20, the reversal60/volatility20 rank blend, skipped-month
  momentum, residual momentum, and an equal-rank blend including residual momentum.
- At return date `s`, fit an intercept and nine ETF coefficients on exactly the
  preceding 126 daily returns, ending `s-1`. Use a checked SVD; require full rank,
  the frozen conditioning limit and complete source-qualified return endpoints.
- Save `epsilon[s]` only after observing return `s`; later fits never rewrite it.
  Raw momentum at `t` is `close[t-21] / close[t-252] - 1`. Residual momentum is the
  mean divided by sample standard deviation of 231 residuals, `t-251` through
  `t-21` inclusive. A fixed numerical floor prevents roundoff from becoming a score.
- Missing/nonpositive/nonfinite required prices or volume remain unavailable.
  There is no interpolation, shorter fallback window, inferred zero score or
  future-outcome eligibility filter. Require common finite past-only support for
  all six arms, and report the breadth lost at each stage.
- Preserve the 20-session next-open-to-close target, eight names per tail, minimum
  breadth 16, unit gross, shared boundary-tie weighting and frozen costs. Positive
  volume at future entry/exit endpoints qualifies outcomes only. Unknown held
  outcomes withhold complete curves; unknown predicted outcomes withhold that IC date.

Daily timestamps supply assumed completion times, not historical provider receipt
evidence. Adjusted prices, current cohort selection, repeated inspection and absent
borrow/fill evidence prevent these results from becoming live execution credentials.

## Audit and interpretation

Retain the fitted coefficients, exact prior cutoffs, SVD evidence, per-fit source
hashes, residuals, availability masks, full forecasts, basket weights and fees.
Past-only ETF loadings describe basket exposures; sorting residual scores does not
make holdings factor-neutral. Missing held loadings withhold complete exposure.

Paired IC differences reuse the shared expected-date HAC calculation with 20 lags.
Missing dates retain their place and withhold inference. HAC is not a correction for
repeated research or selecting the best model. Paired basket differences are
explicitly descriptive; do not compound differences as a traded return. Twelve
scheduled baskets per year are a small economic sample.

Evaluate incremental information, cost sensitivity, breadth and gain concentration
against the existing rank blend. Report every arm, including unavailable results.
A promising historical lead requires a separately frozen prospective observation
and executable risk/borrow/ownership contract before paper allocation.

## Verification

RED-first feature tests cover hand-computed coefficients, precise skipped-month
boundaries, future-prefix invariance, invalid endpoints, rank/conditioning failure
and numerical residual degeneracy. Shared-kernel regressions preserve original
forecast-controls output. SQLite/PostgreSQL integration exercises actual retained
artifacts, charging/exclusions before reads, unknown held outcomes, source tampering,
replay and no activation. Tiny fixtures verify mechanics; the 54-comparison run
and deployed service verification are separate evidence.
