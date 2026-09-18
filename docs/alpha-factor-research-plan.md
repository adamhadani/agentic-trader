# Bounded factor research plan

**September 18, 2026 — proposal, not executed.** Keep the
[power diagnosis](alpha-power-ablation-plan.md) as the immediate research task.
The next useful factor experiment is a small test of information beyond our
existing reversal/volatility styles. Prospective collection can proceed separately.
This document adds no model, provider acquisition, journal write or promotion.

## What our evidence supports

The [64-equity controls](alpha-forecast-controls-2026-09-18.md) reproduce Ridge but
do not establish its incremental value: volatility20 has higher mean Rank IC in all
three years, and Ridge's daily rank correlation with the reversal/volatility blend
is 0.861–0.903. Its 2024 advantage is concentrated; four strict 2023 baskets have
unknown held endpoints. This is a concrete style lead worth prospectively testing,
not six independent discoveries or a validated alpha portfolio.

Keep three meanings separate:

- **Risk factor:** an observed common return/exposure used to explain co-movement.
  Explaining realized returns does not make future factor returns predictable.
- **Predictive characteristic:** a score known before the target outcome, such as
  past momentum. Its future cross-sectional information must be measured.
- **Investable strategy:** that forecast plus sizing, costs, source coverage,
  eligibility, borrow, protection and an executable holding policy. A regression
  residual or factor-neutral score is not automatically a tradeable portfolio.

## Useful hypotheses with present data

| Approach | Evidence and decision for this stack |
| --- | --- |
| Skipped-month momentum | French's daily momentum construction uses prior 2–12 returns. Test a fixed daily approximation against our existing 60-day reversal style; do not call it a replication of the size-sorted French factor. [Construction](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/Data_Library/det_mom_factor_daily.html). |
| Residual momentum | Blitz, Huij and Martens remove common-factor return components before ranking momentum. This directly motivates checking whether a score adds information beyond common exposures. Our ETF-based daily version below is an adaptation, not their Fama–French/monthly implementation. [Original manuscript](https://repub.eur.nl/pub/22252/ResidualMomentum-2011.pdf). |
| Beta/volatility | Retain volatility20 as a strong existing control; measure beta as risk attribution. The low-beta strategy in Frazzini–Pedersen is a financed long/short construction, not a justification for our observed high-volatility long tilt. Do not equate opposite directions. [Betting Against Beta](https://www.aqr.com/insights/research/journal-article/betting-against-beta). |
| Liquidity/volume | Amihud's measure uses absolute return divided by dollar volume. IEX activity is not consolidated dollar capacity; begin any later volume hypothesis with our exact feed-specific calibration and economic controls. Defer adding it to this first matrix. [Original working paper](https://w4.stern.nyu.edu/finance/docs/WP/2000/pdf/wpa00041.pdf), [local volume contract](alpha-volume-calibration.md). |
| Nonlinear models | Gu–Kelly–Xiu find useful interactions among momentum, liquidity and volatility. Our inference is to establish distinct feature information first, then compare one fixed nonlinear model through the existing estimator factory; a large tree/neural/genetic search is premature. [Paper](https://images.aqr.com/-/media/AQR/Documents/Journal-Articles/Empirical-Asset-Pricing-in-Machine-Learning.pdf). |

Value, profitability, investment and market capitalization need accounting items
and historical share/price information absent from OHLCV. French's construction
explicitly uses book equity, revenues/expenses and lagged assets. We would also need
filing/availability times, revisions, historical membership and corporate actions;
today's financial statements or sector labels cannot be backdated. Downloaded
factor returns can support separately labelled ex-post attribution, but cannot
stand in for point-in-time firm characteristics. [Five-factor construction](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/Data_Library/f-f_5_factors_2x3.html).

## One proposed retained-data experiment

Freeze a new protocol before reading retained prices. Bind the original forecast
result, input, calendar, source and universe hashes; use its 64 equities and nine
sector ETFs, IEX `adjustment=all`, 2021–2025 history and 2023/2024/2025 folds.
There is no SPY in that retained parent. The nine ETF returns are explicit common
return proxies, not inferred stock sectors, Fama–French factors or a market index.

**Six score arms, one 20-session next-open-to-close target, three annual folds,
IC plus 1/5 bp per-side economics: 54 charged comparisons.** Three arms are existing
controls: frozen Ridge, volatility20 and the reversal60/volatility20 rank blend.
Three are raw skipped-month momentum, residual momentum, and a fixed equal-rank
blend of the existing blend with residual momentum. These are related controls and
combinations, not six independent economic hypotheses. No parameter or sign search.

Proposed causal residual feature, to freeze and test before execution:

1. At each historical return date `s`, fit a stock's daily return on an intercept
   and the nine ETF daily returns using exactly the preceding 126 sessions, ending
   `s-1`. Use SVD least squares with explicitly frozen rank/conditioning checks.
   Require complete finite source-qualified returns; unavailable fits stay missing.
2. After the return at `s` is observed, save
   `epsilon[s] = stock_return[s] - intercept[s-1] - beta[s-1] @ ETF_returns[s]`.
   Later refits never rewrite earlier residuals. Retain fit cutoffs, coefficients,
   source hashes and support. A contemporaneous factor return is used only after it
   is observed, never as a future predictor at the preceding decision.
3. At decision `t`, raw momentum is `close[t-21] / close[t-252] - 1`.
   Residual momentum is the mean of `epsilon[t-251:t-21]`, inclusive, divided by
   its sample standard deviation (`ddof=1`). Both skip the latest 21 sessions; zero residual variance
   or missing required history means unavailable, not a neutral score.
4. Rank on the observed cross-section with the existing tie policy. Match all arms
   on support available at the decision, including the longer residual warmup;
   never require future outcome availability for eligibility. Preserve every
   symbol/date and report lost breadth and abstentions. Recompute controls on this
   matched support; do not compare their narrower-cohort returns directly with the
   original reported curves.

The 126-session one-step residual fit is a deliberately bounded daily adaptation,
not the paper's 36-month regression. It introduces no supervised return forecast
fit. Frozen Ridge retains its original training semantics. If a later variant
refits Ridge or learns blend weights, all 20-session training labels must be
source-qualified and mature strictly before the refit; reserve it as a new study.

Use positive source volume at relevant return endpoints when constructing the new
features, and positive entry/exit endpoint volume for evaluation labels. Those masks
are necessary source checks, not proof of executable fills. The latter mask affects
outcomes only: unknown held outcomes withhold the basket/full curve, and missing
predicted outcomes withhold that IC date. Never delete a difficult stock using a
future endpoint. Adjusted-history and current-cohort limitations remain unchanged.

## What would make this useful

- **Predictive increment:** daily cross-sectional Spearman IC and paired IC changes
  versus the existing blend, with expected-calendar coverage and the shared 20-lag
  HAC diagnostic. Dates, not stock rows, are the inference units. HAC addresses
  serial dependence, not selection across this already inspected research family.
- **Economic increment:** shared disjoint 20-session baskets, eight names per tail,
  minimum 16 eligible names, fixed unit gross and matched cost assumptions. Report
  paired net basket differences, turnover, break-even costs and gain concentration
  versus the original blend. Keep missing scheduled baskets in denominators; twelve
  baskets per year do not establish a reliable economic significance claim.
- **Risk attribution:** retain basket exposures from past-only ETF loadings and
  compare raw versus residual momentum. Equal gross/net is not equal factor or
  volatility risk. Sorting residual scores does not guarantee factor-neutral
  holdings, and a positive residual alone is not market-adjusted performance proof.
- **Funnel decision:** a stable, incremental, cost-tolerant lead earns prospective
  observation under a frozen definition. Poor breadth, disappearing cost edge or
  concentrated gains earns a recorded rejection or targeted data repair. Do not
  invent a threshold to accept the best arm after inspection. These reused years
  cannot qualify anything; next steps need fresh forward labels and the portfolio
  execution/borrow/ownership boundary before paper allocation.

## Reuse and smallest implementation

Already available: `AlphaPanelService` reservation/exclusion/checkpoints,
`RetainedPanelSource` hash validation, `prepare_forecast_inputs`,
`evaluate_forecast_trial`, `basket_weights`, `cross_sectional_ic`, estimator evidence
and paired basket reporting. The parent comprises 73 inspected members; exclude
all of them, including ETF controls and warmup, before the first retained read.
Current artifact reads retain their identity separately from original provider
receipts. No new acquisition or research engine is required.

Missing: a typed frozen factor-controls plan, the causal rolling residual feature,
and its saved fit/support evidence. `PanelForecastPlan` currently rejects
`beta_window`; the older panel beta adjustment is not this feature contract.
`orthogonalization.factor_neutralize` provides a projection primitive, but its WLS
residuals are not portfolio weights. Do not reuse legacy `residual_validation`'s
ordinary correlation p-value for overlapping panel targets.

The proposed application API is
`compute_factor_controls(batch, clock, plan, sessions, *, parent_result)` composed
through `AlphaPanelService(..., compute=...).run(...)`, following
`cli/commands/alpha_controls.py`. Share the retained-parent binding instead of
copying its hash verifier; share evaluation kernels instead of rewriting IC/costs.
There is **no existing CLI/config-only path for this exact experiment today**.
A same-turn run would require those small additions, tests, review and a committed
protocol first; the present task delivers the plan only.

Minimal RED tests: hand-computed rolling residuals; future-price/volume invariance
of prior fits and decisions; rank-deficient/missing fits; exact skipped-month
boundaries; no forward-label leakage; frozen Ridge unchanged; common support chosen
before outcomes; unknown-held withholding; matched fees/ties; and journal reservation
before artifact access with zero provider calls. Keep substantive statistical tests
separate from tiny mechanical integration fixtures.
