# Matched screened-equity DSL panel — September 19, 2026

## Purpose and frozen protocol

This diagnostic compares five newly mined causal DSL families with three
controls on the exact 64-name cohort selected by the completed
`equity_liquidity_screen_v1` IEX screen. The arms are `constant`,
`market_residual`, `volatility20`, `vol_scaled_trend`, `range_location`,
`signed_volume`, `serial_dependence`, and `slope_zscore`.

Every arm uses the same symbols, native daily raw IEX bars, next-open-to-close
20-session labels, non-overlapping rank baskets, and 0/1/5 bp per-side cost
scenarios. The panel service reserves and excludes the complete declared matrix
before any provider read. Missing members or outcomes remain visible; they are
never removed to improve a result. These are current-cohort historical
development diagnostics, not point-in-time membership, broker fills, or
promotion evidence.

The report records absolute weight change from the previous disjoint basket
(turnover) and trailing 60-session benchmark beta through the signal bar
(market factor exposure). Neither changes eligibility or basket membership. The
beta fit ends at the signal bar and cannot read the held label.

## Full-history attempt: correctly failed closed

Protocol: `config/research/screened-equity-matched-dsl-iex-v1.json`
Plan: `bce35858bbe2015657861f255eab7f9c29b716295d95273d5b6d292c75e69535`
Run: `bbf0dc46-1207-4280-9b2b-6530a1512609`
Result artifact hash: `0e7ab2ca8be42e6a105ec7351d0ea8118c2dc2992d3c97d6ff9964a8e3d41dc8`

The 2021–2025 protocol reserved all 96 charged comparisons and captured every
member, but computation stopped with `PanelCoverageError` before scoring. Newer
screen members such as AUGO and CHYM do not have bars over the declared history;
other members have isolated missing dates. This is intended fail-closed behavior.
Intersecting the panel after seeing coverage would have changed the universe and
invalidated the experiment.

## Coverage-limited recent diagnostic

After retaining the failed attempt, a separately identified protocol was declared
for the complete cohort over July 16–December 31, 2025. It has one short fold,
60 minimum IC observations, and five scheduled 20-session baskets. These limits
are explicit and far below a qualification-quality longitudinal sample.

Protocol: `config/research/screened-equity-matched-dsl-iex-recent-v1.json`
Plan: `d56fc3908e54e236c9d21c6baa84a4590b2b74157e933eadf775a93ef9ccc1f3`
Run: `2d557fd4-f9d6-4ecd-a0b7-d0b7182ded65`
Result artifact hash: `ac8830b187f788b2df06876de0d42bfce0f383607bbbd587caac372370fe0271`
Completion: **32/32 comparisons**; no arm advanced and no arm authorizes promotion.

| Arm | mature IC dates / coverage | mean Rank IC | 1 bp net | 5 bp net | basket turnover | mean abs market beta |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| constant | 0 / 0.0% | — | 0.00% | 0.00% | 0.00 | 0.00 |
| market residual | 38 / 38.8% | −0.0855 | −1.52% | −1.68% | 2.50 | 0.55 |
| volatility20 | 78 / 79.6% | 0.0877 | +6.55% | +6.21% | 4.38 | 0.80 |
| volatility-scaled trend | 78 / 79.6% | −0.0398 | −0.38% | −0.70% | 6.63 | 0.65 |
| range location | 79 / 80.6% | −0.0424 | −4.44% | −4.75% | 6.50 | 0.64 |
| signed volume | 79 / 80.6% | 0.0289 | +1.14% | +0.81% | 6.50 | 0.68 |
| serial dependence | 77 / 78.6% | −0.0521 | −3.33% | −3.56% | 4.25 | 0.58 |
| standardized slope | 60 / 61.2% | 0.0130 | +0.96% | +0.72% | 4.50 | 0.64 |

The volatility control is the strongest descriptive result, but it has only 78
observed IC dates, unavailable outcomes, and five baskets. The triage contract
rejects it on coverage and sample sufficiency. Positive signed-volume and slope
marks are concentrated and fail the fixed concentration limit. The market
residual arm is negative. None of these numbers is live P&L.

## Findings and next action

1. The broadened screen is not a valid five-year panel without an explicit
   historical-availability contract. Current membership and historical support
   must remain separate.
2. IEX bars were obtainable for the recent window, but endpoint-volume masks
   withheld many labels. A short result cannot establish stable IC/ICIR.
3. New DSL families did not produce a gate-passing arm. Carry volatility as a
   comparator only, not an alpha to promote.
4. Turnover and benchmark beta are measurable and non-trivial. Any future paper
   probe must bind them to explicit cost, factor, and risk limits rather than
   treating gross returns as standalone evidence.

The next high-value experiment is a predeclared, source-qualified prospective
window for an availability-bounded cohort (or delayed SIP), with enough sessions
to mature the 20-session labels. No threshold is lowered, no historical member is
silently dropped, and no paper order is authorized by this report.
