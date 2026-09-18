# Screened equity controls — September 18, 2026

## Conclusion

The earlier Ridge result reproduces exactly, but this follow-up does **not establish
incremental predictive value over simple styles**. High-volatility exposure explains
much of the opportunity: the fixed volatility control has higher mean Rank IC in
every year, materially higher 2023 price-proxy returns, and nearly the same 2025
return. Ridge does better economically in 2024 despite negative mean Rank IC.
These are descriptive differences, not beta/sector-risk-adjusted alpha estimates.

The stricter endpoint scope exposes an important limit: four of Ridge's twelve 2023
baskets have a held BTDR endpoint with zero IEX volume. Its strict 2023 full-path
return is therefore unavailable. The original finite-price result remains a correctly
reproduced price proxy, not evidence those endpoints could execute.

**No alpha was activated or promoted.** Registry generation remains 16 with zero
active and 16 shadow entries. The next useful step is source-qualified, prospective
panel evidence with fixed style controls, not a larger model/DSL search.

## Frozen exercise

[Protocol](../config/research/screened-equity-controls-iex-v1.json) and computation
were committed as `6b2eee2117b2b3553ccd75ba23a7200983e66839` before retained-price
access. The [contract](alpha-forecast-controls.md) uses the original 64-equity cohort,
2023–2025 folds, 20-session next-open-to-close target, original eligibility and frozen
Ridge predictions. Controls are reversal60, volatility20, their equal rank blend,
and equal-weight long-only context. Both endpoint scopes use identical holdings;
original training remains unchanged.

All **90/90 comparisons** completed with **zero provider requests and zero refits**.
The shared journal reserved 90 comparisons and excluded all 73 parent members,
including warmup and ETF controls, before the first retained read at
`2026-09-18T06:53:02.659486+00:00`. Lifetime trial accounting increased from 7,736 to
7,826. The parent current cohort is survivor/liquidity conditioned; this is inspected
historical development data, not independent validation.

## Results

Annual compounded **finite-source-price proxies**, after 5 bp per side. Each year
has twelve disjoint baskets; these omit borrow, financing, spreads, executable
auction prices and intraperiod risk. Long-only context has different net exposure.

| Strategy | 2023 | 2024 | 2025 |
| --- | ---: | ---: | ---: |
| Frozen Ridge | +43.45% | +8.67% | +37.96% |
| Reversal60 | +45.97% | +0.68% | +11.48% |
| Volatility20 | +61.38% | +3.30% | +37.46% |
| Equal rank blend | +46.63% | −6.56% | +38.20% |
| Equal-weight long-only context | +29.36% | +22.05% | +23.47% |

| Mean cross-sectional Spearman IC | 2023 | 2024 | 2025 |
| --- | ---: | ---: | ---: |
| Frozen Ridge | 0.0631 | −0.0147 | 0.0790 |
| Reversal60 | 0.0533 | −0.0265 | 0.0322 |
| Volatility20 | 0.0711 | −0.0009 | 0.0916 |
| Equal rank blend | 0.0789 | −0.0171 | 0.0818 |

The volatility control's 2025 nominal HAC statistic is 2.49 (95% interval
0.0196–0.1637), but 2024 is effectively flat. That one post-selection, unadjusted
fold is not confirmation across this research family. Overlapping daily targets
must not be assessed using the larger IID t-statistics also retained for comparison.
Long-only constant scores have undefined IC.

Positive endpoint volume withholds 82 whole IC dates in 2023, one in 2024 and none
in 2025. It withholds four 2023 baskets for Ridge, volatility and long-only, and two
for the blend; reversal's held endpoints remain known. The 2024/2025 economic curves
are unchanged. Means over the remaining IC dates are selected-support descriptions;
incomplete-calendar inference stays unavailable. Missing baskets are not dropped,
replaced, zero-filled or used to rerank the portfolio.

Paired differences retain all twelve scheduled basket dates. At 5 bp, Ridge minus
volatility has mean basket differences of −1.015, +0.361 and +0.036 percentage points
in 2023/2024/2025 under the original price scope. Ridge minus the blend is −0.129,
+1.303 and +0.002 percentage points. With twelve baskets per year, these differences
and their dispersion are descriptive; no compounded spread or economic significance
claim is made.

The 2023 unknown BTDR endpoints belong to baskets decided January 3, February 1,
March 2 and June 27. The last has an exit-only gap on July 26, which the earlier
entry-focused note missed. CR also has a zero-volume April 4 endpoint affecting two
IC dates already withheld for BTDR. The one 2024 missing IC date is November 22:
CR and IBP both have zero-volume December 23 exits. All 73 unique zero-volume
endpoint rows were checked against the retained hashed provider pages. Prices exist
in these cases; trade-activity evidence does not.

Post-hoc attribution reinforces the style interpretation: mean daily Ridge/blend
rank correlations are 0.861, 0.903 and 0.877 across the three years. At 5 bp in 2024,
Ridge beats volatility in 8/12 baskets, but its +4.33 percentage-point arithmetic
advantage remains small; HMY contributes 63.1% of that net difference. Against the
blend, the top three periods supply 85.8% of the +15.63-point arithmetic advantage,
and BTDR contributes 50.7%. These are baseline-specific descriptions of the declared
comparisons, not additional strategies or independent observations.

## Audit and delivery

The independent NumPy/pandas reconstruction passed 24,139 checks over all 73 retained
members, 86,031 normalized rows, 30 model/scope/fold cells, 360 baskets and 48 paired
contrasts. It verified source-derived controls, frozen support/weights, endpoint
masks, IC/HAC, costs, complete-curve withholding and paired denominators. Maximum
numerical disagreement was 1.78×10⁻¹⁵. The audit did not independently recompute IID
Student-t p-values; those are not used for the conclusions.

- Plan: `eb85e9f579f93702d992673bb743af28dcfdb27926334f59df765b20a908e045`
- Result: `8edea13e97787cf2c9e54250e072bc761704518e465bed542a5a468d0304960c`
- Independent audit: `91138290cad598851e099b3bdee6d47917541088e5d3c6ec273fa093519a8ffc`
- Endpoint/attribution supplement: `450cd58c751c860421ecc0be25dd3a1f6f84e6fcc11988607a917060fb3bfd1d`
- Raw endpoint audit: `052bccfbedd96832056cff28b6d205e6a0210f02c20a0274a11ed11e4425e225`
- Run: `c145941f1b7040f58720447d1bc957fe`

Private artifacts are under
`~/.local/state/agentic-trader/research/screened-equity-controls-20260918/v1`.
Source data and operational logs remain outside Git.

TDD covers future-volume invariance, original result reconstruction, unknown held
outcomes, source tampering and journal ordering. Independent review caught and fixed
late cost-order validation by reusing the shared policy. Local PostgreSQL fixtures
needed explicit loopback permission; all six new SQLite/PostgreSQL cases then passed.
All pre-commit checks passed before the frozen run. CI and deployed health evidence
are recorded separately in the PR and private operational verification directory.
