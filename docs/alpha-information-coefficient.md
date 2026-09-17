# Information coefficient: interpretation and next measurement contract

Reviewed September 17, 2026 after operator input. These are research reference
points and a documented implementation gap, **not new promotion thresholds**.
Frozen studies, historical metrics and current acceptance policies are unchanged.

## Definitions and useful references

For a cross-sectional equity factor, compute Spearman correlation **across eligible
assets at each observation date** between factor ranks and subsequent return ranks.
Specify the forecast horizon, prices, universe, availability and any sector/residual
adjustment. Average those date-level ICs over time. This is the convention implemented
by [Alphalens](https://alphalens.ml4trading.io/api-reference.html), whose
[source](https://github.com/stefan-jansen/alphalens-reloaded/blob/main/src/alphalens/performance.py)
groups the date/asset panel by date. A time-series correlation within one ETF answers
a different question. Two ETFs alone also give a nearly degenerate cross-sectional
rank statistic: with two distinct pairs Spearman is only +1 or −1.

A mean Rank IC of **0.03–0.06** can be an interesting target for a broad liquid-equity
panel, but is not a universal production-grade cutoff. It is a dimensionless
correlation, not a 3–6% expected return. Universe breadth, horizon, serial dependence,
neutralization, selection history, costs and usable capacity determine its value.
Retain this operator-suggested range as a contextual comparison, not proof of edge.

Given N equally spaced IC observations, mean m and **sample** standard deviation s:

- Per-observation ICIR = m/s. It measures consistency of IC relative to its variability.
- Under independent, identically distributed observations, annualized ICIR =
  `(m/s) * sqrt(K)`, where K is IC observations per year, explicitly declared.
- The usual one-sample statistic is `t = (m/s) * sqrt(N)`.
  Equivalently `t = annualized_ICIR * sqrt(N/K)`: the latter square root uses **years**,
  not daily observations. Annualized ICIR 1.5 over one year gives t=1.5, not 23.8.
- An annualized ICIR of **1.5** is a useful comparison target under those conventions,
  not evidence of low turnover or sufficient statistical power. Measure portfolio
  turnover, rank persistence, participation and transaction costs separately.

The [SciPy one-sample test](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.ttest_1samp.html)
assumes independent observations. A two-sided |t| near 1.96 is the large-sample 5%
reference; finite-sample Student cutoffs depend on degrees of freedom. Rejection is
conditional on the test assumptions, not a 95% probability that an alpha is real.
Overlapping targets and rolling IC windows invalidate naive independent-N inference.
Use a predeclared dependence-aware method, such as a block bootstrap or
[HAC/Newey–West standard error](https://www.statsmodels.org/stable/generated/statsmodels.stats.sandwich_covariance.cov_hac.html),
with explicit lag/block choices; irregular/gapped clocks require special care.

Repeated mining adds selection bias. [Harvey, Liu and Zhu](https://www.nber.org/papers/w20592)
show why the usual t>2 hurdle is inadequate in factor discovery; their higher hurdle
is not a universal replacement either. Preserve our cumulative attempt ledger,
untouched confirmation and existing bootstrap/deflated-Sharpe safeguards. IC alone
cannot replace strategy P&L, costs, joint risk or forward execution evidence.

## What the code actually does today

`research/alpha/metrics.py:calculate_rank_ic` uses Spearman, but calculates 20-valid-row
**single-symbol time-series** correlations every five rows by default. Windows
therefore overlap. It returns their mean, population standard deviation (`ddof=0`)
and **unannualized** mean/std with an epsilon. Constant/insufficient observations use
legacy numeric fallbacks. Those fallbacks are not significance estimates.

`miner.py:_candidate` excludes unavailable fold-end labels but concatenates validation
indices before these windows are computed. Dropping missing pairs can also compress
gaps. Windows can span distinct folds. The ranking score includes this descriptive
ICIR, and the current statistical policy includes minimum mean IC 0.01. There is no
IC t-test or annualized-ICIR promotion test. Do not label these existing numbers as
cross-sectional IC, independent-window estimates or the operator's annualized metric.

The explicit-horizon forecast benchmarks separately report pooled prediction/target
Spearman correlation; that is also not a date-level cross-sectional IC series. The
continuous ETF campaign reports execution returns and frozen economic screens, not
IC significance. Imported historical example metrics remain unqualified shadows.

## Next implementation, with TDD before use

1. Define a typed/versioned IC report with axis (time series/cross section), horizon,
   fold/date boundaries, observation frequency, sample sizes, coverage, missing reasons
   and the complete IC series. Preserve rank ties. Never convert undefined correlation
   into observed zero, stitch folds, or silently compress a gap for inference.
2. For aligned ETF panels, rank only assets actually available at each decision.
   Predeclare minimum breadth and eligibility; report coverage rather than backfill
   future listings or substitute today's universe. Use explicit execution-aligned
   targets and separately report raw/neutralized/residual definitions.
3. Report sample-standard-deviation ICIR with units; annualization only for an explicit
   eligible clock. Keep naive IID t diagnostic separate from dependence-aware uncertainty,
   costs and selection-adjusted confirmation. No automatic 252 multiplier for all data.
4. Parametrized/fixture tests: ties, inverse/constant inputs, NaNs/infinities, short/zero-
   variance samples, fold boundaries, gaps, overlapping horizons, known panel ranks,
   annualization units and dependent null/positive controls. Integration must preserve
   report identity, attempt accounting and immutable saved evidence in both DB backends.
5. Freeze the metric/validation version and a small panel experiment before new reads.
   Do not recompute old evidence into qualification or change thresholds to fit observed
   winners. Pair the implementation with that actual-data experiment per the
   [research cadence](alpha-roadmap.md#research-delivery-cadence-and-progress-september-17).
