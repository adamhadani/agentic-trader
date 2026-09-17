# Screened-cohort forecast discovery

`alpha forecast-study PROTOCOL --selection LIQUIDITY_DIRECTORY --output NEW_DIRECTORY`
compares explicit-horizon forecasts through the existing daily acquisition service
and diagnostic journal. This development study has **no promotion authority**.

## Why another study contract

No alpha was qualified after the 300-candidate liquidity exercise: that exercise
selected 64 of 150 eligible listings and did not measure predictive returns.
The [A2a power study](alpha-timeline-study-2026-09-16.md) also found that the legacy
lifetime gate accepted zero winners in four planted-edge groups. Removing several
gates improved detection, but still failed the frozen power requirements. Zero
promotions therefore does not establish that our filters are appropriately calibrated.

There are economic weaknesses too. Prior QQQ momentum earned +26.24% at 1 bp per
side and -9.50% at 5 bp, with 415 closes. Those costs were assumptions, not measured
fills. The sector panel's best mean Rank IC was 0.0136, but stressed economics and
gain concentration also failed. Lowering the IC threshold would not rescue that
experiment. See [continuous results](alpha-continuous-campaign-2026-09-17.md) and
[sector results](alpha-sector-panel-2026-09-17.md).

The new contract separates feature availability, predictive evidence and economic
conversion. It does not change legacy qualification gates or reinterpret past
attempts as untouched evidence. The legacy miner's one-step IC versus multi-session
bracket-policy mismatch remains a separate migration/calibration task.

## Frozen first comparison

The [v1 protocol](../config/research/screened-equity-forecast-iex-v1.json) binds the
complete 64-member selected equity cohort to the prior snapshot, liquidity result,
manifest and input hashes. The observation fence is the last actual screen source
receipt, not acquisition start. Nine fixed sector ETFs provide a separate cohort
with the same model budget. Their results cannot be pooled as independent replications.

- IEX native daily bars, `adjustment=all`, January 2021–December 2025.
- Evaluation folds: 2023, 2024 and 2025; next-open to 20-session-close labels.
- Economic scores: 60-session momentum and negative five-session momentum.
- Ridge uses both scores plus 20-session realized volatility; alpha=100, fixed.
- A training-mean constant is the negative discrimination/control baseline.
- Trailing 252 decision dates, at least 126 distinct training dates and 500 paired
  rows, refitted every 20 observed sessions. Every training label must mature
  **strictly before** the refit decision date. Scaling fits training rows only.
- Eligible rows require 61 consecutive observed historical bars and all finite
  features. New listings can acquire eligibility; future outcome availability
  never chooses the historical membership or basket.
- Equity baskets require at least 16 eligible assets, with eight per tail;
  ETF baskets require six, with three per tail. Boundary ties share weights.
- Fixed nonoverlapping 20-session baskets; 0/1/5 bp per-side assumed costs.
  Four models × two cohorts × three folds × (forecast + three costs) = **96**
  charged comparisons, reserved before all calendar/bar reads.

The current liquidity screen and today's surviving membership condition this
historical cohort. This is **not point-in-time universe validation**. Instrument
subtypes, historical borrow/eligibility and actual execution capacity remain unknown.
Alpaca adjusted price ratios are return proxies; no physical share accounting,
dividend payment-date cash flow, borrow fees or funding costs are modeled here.

## Evidence and missing observations

The shared calendar retains every expected session and every frozen symbol. A
legitimate empty or shorter history creates unavailable features, not an invented
price or a reason to remove the symbol retrospectively. Provider failures or
malformed/source-loss evidence fail the study with member checkpoints preserved.
Existing complete-panel/persistent-book contracts remain unchanged.

Each model/fold retains eligibility, prediction and label support, fitted training
evidence, daily cross-sectional Spearman IC and nonoverlapping basket decisions.
Statistics use dates as observations, not correlated stock rows as independent
replications. Constant scores legitimately have undefined Rank IC. HAC is a
per-fold serial-dependence diagnostic, not an adaptive-search correction.

Basket weights are fixed using available forecasts before inspecting outcomes.
A missing outcome for a held name makes that basket unavailable; the evaluator
cannot replace it, assume zero return or compound an incomplete path. A missing
outcome for an unheld name does not erase an otherwise observable basket.
Full metrics remain visible instead of hiding all candidates behind a single gate.

The application workflow handles pacing, checkpoints, failed attempts, exclusions
and immutable private artifacts. No second journal, schema, execution queue or
daemon is introduced. Scikit-learn's train-only pipeline is the shared model
boundary; see its [leakage guidance](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage).

## Follow-up decisions

Use actual forecast and cost results to select a small prospective confirmation
exercise. Do not retune thresholds on these inspected years and call the result
qualification. Independently run a fresh-seed power ablation separating known
predictor, learned/search-selected predictor, bracket execution and individual
promotion gates. Predeclare nominal test size separately from maximum acceptable
false-positive rate; retain lifetime accounting while calibrating its decision rule.
