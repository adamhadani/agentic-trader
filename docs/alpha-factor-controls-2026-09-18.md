# Retained factor controls — September 18, 2026

## Conclusion

All **54/54 frozen comparisons completed**, and an independent numerical audit
found no discrepancy in the calculations it checked. The new residual-momentum
features do **not establish a consistent improvement over the existing rank blend**.
Their 2025 results contain useful leads, but performance changes sign across years,
incremental IC is uncertain, and large individual-stock moves contribute much of
the gains. No alpha was promoted or activated by this study.

This is current-cohort historical development using already inspected data.
Source verification and causal calculations do not turn it into independent
validation, historical point-in-time membership, or executable paper-trading evidence.

## Frozen exercise and accounting

The [protocol](../config/research/equity-factor-controls-iex-v1.json) and code were
committed as `c321d804f571faa3735fabccff2b55c1dbfc599c` before the retained-data run.
The [implementation contract](alpha-factor-controls.md) uses the original 64 equities
and nine named sector ETFs, 1,255 observed sessions in 2021–2025, IEX all-adjusted
prices and separate 2023/2024/2025 evaluation folds.

Six fixed score arms × three folds × (Rank IC plus 1/5 bp per-side economics)
give **54 charged comparisons**. The shared application reserved the matrix and
excluded all 73 inspected members, including warmup, before artifact access. The
manifest was frozen at `2026-09-18T11:20:15.617038+00:00`; the first artifact read
started at `2026-09-18T11:20:16.071056+00:00`. There were no provider requests or
supervised forecast refits. The run did fit 54,860 causal factor regressions;
all 80,320 symbol/date fit records, including unavailable fits, remain in the result.

The original parent Ridge predictions and their original IC, baskets and cost
accounting reproduced exactly before applying the new common support. All six arms
then used that same narrower, past-only support. These are related feature controls
and combinations, not six independent economic discoveries.

## Measured results

Annual compounded **adjusted-price payoff proxies after 5 bp per side**. Each arm
has twelve disjoint 20-session baskets per year. Prices and positive endpoint
volume are source observations; these returns omit borrow, financing, executable
spreads/auction fills and intraperiod risk. The 1 bp scenario is retained too.

| Score | 2023 | 2024 | 2025 |
| --- | ---: | ---: | ---: |
| Frozen Ridge | +11.93% | −9.44% | +38.51% |
| Volatility20 | +9.27% | −4.64% | +40.40% |
| Existing reversal60/volatility20 rank blend | +10.56% | −9.18% | +42.07% |
| Skipped-month momentum | −9.75% | +11.18% | −2.88% |
| Residual momentum | −20.18% | +6.91% | +11.59% |
| Existing blend plus residual momentum | −8.98% | +12.60% | +37.69% |

| Mean cross-sectional Spearman IC | 2023 | 2024 available dates | 2025 |
| --- | ---: | ---: | ---: |
| Frozen Ridge | 0.0528 | −0.0323 | 0.0761 |
| Volatility20 | −0.0082 | −0.0244 | 0.0969 |
| Existing rank blend | 0.0660 | −0.0348 | 0.0766 |
| Skipped-month momentum | −0.1268 | 0.0456 | 0.0365 |
| Residual momentum | −0.1160 | 0.0766 | 0.0402 |
| Existing blend plus residual momentum | −0.0569 | 0.0278 | 0.1013 |

The augmented blend's 2025 absolute IC has nominal HAC20 `p=0.00241`, but its
**paired increment over the existing blend is only 0.02465**, with `p=0.6053`
and a 95% interval of −0.06883 to +0.11813. Its 2023 increment is −0.12287
(`p=0.01999`); 2024 inference is unavailable. These HAC statistics account for
specified serial dependence, not repeated inspection or research-family selection.
The larger IID statistics retained in the artifacts are not evidence of stronger
independent support.

At 5 bp, the augmented blend's mean paired basket difference against the existing
blend is −1.642, +1.806 and −0.292 percentage points in 2023/2024/2025. It wins
4/12, 9/12 and 4/12 paired baskets respectively. These are descriptive differences;
they are not compounded as a traded spread or treated as independent daily samples.

## Support loss and unknown outcomes

Longer residual history materially changes the cross-section. Counts below include
every frozen decision date, including each fold's final 20 unmatured target dates.

| Fold | Frozen Ridge symbol/decisions | Common symbol/decisions | Lost | Daily common breadth |
| --- | ---: | ---: | ---: | ---: |
| 2023 | 14,810 | 7,409 | 7,401 | 17–55 |
| 2024 | 15,624 | 14,279 | 1,345 | 55–59 |
| 2025 | 15,638 | 14,862 | 776 | 57–60 |

No date fell below the required 16 names. Nevertheless, roughly half of the
original 2023 Ridge support disappeared. The new common-support results cannot
be compared directly with the wider-cohort returns in the
[earlier controls study](alpha-forecast-controls-2026-09-18.md), or interpreted as a
repair of its unknown endpoints. The new filter uses past evidence, not outcomes.

All six arms have IC on 230/230 mature dates in 2023, 231/232 in 2024 and 230/230
in 2025. The missing decision is **November 22, 2024**: eligible CR and IBP have
positive November 25 entry volume but zero IEX volume at their December 23 exit.
Prices exist, but the frozen endpoint rule withholds those outcomes. The whole
IC date and all complete-fold 2024 HAC inference are correctly unavailable; the
date is neither removed from the expected clock nor filled with zero. Its 2024
mean IC is explicitly an available-date description.

All twelve scheduled economic baskets per fold have known held outcomes on this
common support. Their different decision schedule explains why the full economic
curves can be available while one daily IC observation is missing.

## Gain concentration and exposure limits

The augmented blend's best 2025 basket, decided April 30, returns +14.36% after
5 bp and supplies **38.88% of that year's positive-basket gains**. NVTS contributes
+11.17 percentage points to that basket's +14.47% gross payoff, about 77.2% of its
gross gain. The existing blend holds the same NVTS contribution in that period.
Pure residual momentum's best 2025 basket, decided September 23, supplies 52.50%
of positive-basket gains; NVTS contributes +7.35 points of its +10.26% gross payoff.
These are attribution descriptions of already declared baskets, not new exclusion
experiments or a basis for selecting a different universe after seeing returns.

Residual scores also do not guarantee factor-neutral holdings. In 2025 the augmented
blend's mean Euclidean norm of its nine net ETF loading coefficients is 0.586,
with a maximum absolute individual ETF loading of 0.650. These correlated-proxy
coefficient summaries are not measured portfolio volatility or a hedged-risk bound.

One exposure is unavailable: the existing blend's January 2, 2025 basket holds IBP,
whose current fit has only 124/126 qualified training returns after the December 23
zero-volume observation. Its skipped-month score remains available because that
observation lies in the skipped interval. The result correctly retains the holding
and withholds its complete exposure instead of changing eligibility retroactively.

## Independent audit and artifact identities

The private run is under
`~/.local/state/agentic-trader/research/equity-factor-controls-20260918/v1`.
Its exclusively published, mode-0600 `independent-numerical-audit-v1.json` verifies:

- Both result → manifest/input/calendar chains, all 73 copied datasets, 146 parent
  and child member artifacts, and 73 original raw-evidence results/pages. Original
  provider receipts remain distinct from current artifact-read receipts.
- Nine selected 231-residual windows using independent least squares: 1,779 valid
  fits, with unavailable source windows preserved. Maximum coefficient difference
  from the saved fits is `6.66e-15`; skipped-month formulas and cutoffs agree.
- 4,844 expected daily Rank IC observations across the 18 new arm/fold cells and
  three original Ridge cells; manual Bartlett/Newey–West HAC calculations and all
  15 paired IC series; 252 baskets with independently rebuilt ties, weights, costs
  and compounded curves; all 216 new basket exposure records.
- Exact original Ridge reproduction in all three folds, supplemented by the
  independent correlation, weighting and payoff calculations above.

No discrepancy was found within that scope. This is not a second full recomputation
of every residual fit. The audit reads no runtime journal; reservation/exclusion
ordering is established by the application workflow and integration coverage.
It makes no provider call, database mutation, new hypothesis or promotion decision.

Frozen identities:

- Plan: `e3be39fbd093059b4e6e700b21c9d891306c96cf47fde63a64f0ad12b12218f7`.
- Result SHA-256: `3d829ef72b0f29c628091b7d212d29ff87f948d901e495cea031121261645693`.
- Parent result SHA-256: `ab6f71367132db6b1ac0b8eb3e6e409883460934bb9c4bd2fb2fa72fe2862166`.
- Independent audit SHA-256: `f2ba5987c73c0db8247fd4875cb7e755a550ab16cc2f3f3fe107e5333db71c05`.

## Next priorities and delivery scope

1. Preserve this negative/mixed incremental result. Do not reverse signs, tune the
   window, weaken source rules or choose acceptance thresholds on these years.
2. Follow the [active roadmap](alpha-roadmap.md): complete the power-diagnosis
   decision and build source-qualified prospective daily Ridge/style observations
   with frozen predictions and mature outcomes. Retained factor controls can be
   explicit comparators; this study grants no promotion authority to them.
3. Evaluate separately identified delayed-SIP coverage alongside IEX and widen the
   declared equity cohort through the existing journal. Keep unavailable members;
   do not treat current membership or adjusted historical prices as point-in-time data.
4. Require incremental net value, breadth and concentration evidence before a larger
   adaptive search. Any eventual paper portfolio also needs the forecast-policy,
   borrow, risk, rounding, fill attribution and protection contracts in the roadmap.

Source verification passed **2,110 tests / 112 opt-in skips**; real SDK HTTP/WebSocket
and disposable PostgreSQL integrations passed **374 tests**. Post-review focused
checks and the final eight SQLite/PostgreSQL factor cases also passed. Ruff, mypy
and all-file pre-commit are clean.
The measured experiment and this audit establish research behavior only. They do
not verify which revision launchd runs, or broker/feed/Telegram readiness; deployment
and service health must be recorded separately.
