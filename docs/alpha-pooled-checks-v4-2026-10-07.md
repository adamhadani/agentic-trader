# Pooled fixed-set protocol v4: checks A and C, and the campaign (2026-10-07)

**Result:** checks A and C passed and the campaign ran with the operator's approval: **no formula
passed the discovery gate** (`no_finalists`). Nothing froze, so the one-use confirmation window
(2024-01-02 → 2026-07-31) is **still unused**. All eight formulas were charged to the pooled
ledger (`campaigns 1, formulas_charged 8, confirmations 0`). By the pre-registered rule in the
[v4 spec](superpowers/specs/2026-10-07-pooled-fixed-set-design.md), the fixed-set lane is
closed on this window and the next alpha source is decided with the operator.

Everything ran at the clean deployed revision `46d5767` (PR #109 merged the same morning), on
protocol v4 (`config/research/pooled/campaign-v4.json`, SHA-256 `a72f2e65…`), cohort v2
(`c2c79410…`) and the cached cohort-v2 cube (`9bedbf9b…`), journal scope
`production/alpaca:paper`. Research only: no registry, broker, Telegram or promotion credit.

## The set

Eight predeclared literature formulas, one per family, decile selection (`ceil(0.10 × n)` names
per session, no hold-skipping), no genetic search. A label-blind probe on the discovery window
before the hash was frozen showed the 126-session high overlapping the `high52-v1` entry at pick
Jaccard 0.76, so it was replaced by signed-volume order imbalance; every formula in the set
overlaps both earlier literature entries at Jaccard ≤ 0.18.

## Check A — statistical power (passed, about 1 h 40 min on 6 workers)

| Planted δ (R) | 0 | 0.05 | 0.08 | 0.10 | 0.12 | 0.15 |
| --- | --- | --- | --- | --- | --- | --- |
| Detection | 0.00 | 0.40 | 0.84 | 0.95 | 0.98 | **0.99** |
| False acceptance | **0.00** | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |

Identical to v3's curve: A reads no family information and reran only because the protocol hash
changed. Acceptance needs detection ≥ 0.80 at 0.15R and false acceptance ≤ 0.05 at 0.

## Check C — real-formula false acceptance (passed, 42 s)

40 replicates on demeaned, session-block-resampled panels: **0 false acceptances** (one-sided
95% Clopper-Pearson upper bound 7.2%; gate ≤ 2 of 40). Three replicates carried a candidate past
discovery; none reached confirmation. With no search, each replicate scores exactly the eight
formulas, which is why C took seconds instead of the half hour the genetic protocols needed.

## The campaign — discovery window 2016-08-01 → 2021-12-31 (no finalists)

The gate needs paired-edge t ≥ 3.0, 3 of 4 blocks positive, leg mean > 0 and ≥ 400 sessions.

| Family | Score | Edge t | Mean edge (R/session) | Leg mean (R) | Sessions |
| --- | --- | --- | --- | --- | --- |
| `reversal_5` | `-1.0 * roc(close, 5)` | −0.86 | −0.021 | +0.170 | 1,357 |
| `signed_volume` | `ts_sum(sign(returns) * volume, 10) / ts_sum(volume, 10)` | −1.27 | −0.026 | +0.166 | 1,357 |
| `overnight_intraday` | `ts_sum(open_gap, 21) - ts_sum(oc_spread, 21)` | −1.95 | −0.050 | +0.140 | 1,357 |
| `abnormal_volume` | `volume / ts_mean(volume, 50)` | −2.58 | −0.029 | +0.165 | 1,357 |
| `momentum_12_1` | `delay(close, 21) / delay(close, 252) - 1.0` | −0.64 | −0.025 | +0.177 | 1,249 |
| `price_volume_corr` | `-1.0 * ts_corr(returns, volume, 21)` | +0.76 | +0.015 | +0.209 | 1,357 |
| `illiquidity` | `ts_mean(hl_spread, 21)` | −0.40 | −0.016 | +0.179 | 1,357 |
| `reversal_x_volume` | `-1.0 * roc(close, 5) * (volume / ts_mean(volume, 50))` | −0.75 | −0.017 | +0.175 | 1,357 |

The leg means of about +0.17R are the label's own base rate: a long 2-ATR stop / 3R target /
20-session bracket on these liquid names earns that on average regardless of selection. The
edge column is what selection adds over the eligible cohort, and for seven of eight formulas
it is negative. Nothing here is a near miss.

## What this says

- Decile baskets of these literature signals do not select names that do better than the
  cohort on this label and cost (5 bps per side), on this cohort, in 2016–2021. The
  earlier top-3 tests of the 52-week high and the low-MAX reversal pointed the same way.
- The lane's machinery worked as designed end to end: both checks bound to the exact revision
  and cube, the campaign charged before scoring, and the window stayed untouched because no
  candidate froze.
- The label itself is a fixed hypothesis (long bracket, 3R target). A selection edge that
  exists in simpler return space can vanish under a bracket with a 2-ATR stop; the lane does
  not measure that, and this result should not be read beyond its label.

## Decision

As pre-registered: the fixed-set lane is **closed on this window**. No further formulas are
tried against it. The confirmation window stays unused and could still serve a future lane
with a different label or cohort, under a new spec and operator approval. The next alpha
source is decided with the operator.

Artifacts: `~/agentic-trader-research/pooled-power-a-v4-20261007`,
`pooled-null-check-v4-20261007`, `pooled-campaign-v4-20261007` (`result.json`, `outcome.json`,
`formulas.jsonl`); logs under `~/agentic-trader-research/logs/`.

## Label diagnostic (2026-10-07, read-only, discovery window only)

Operator question after the campaign: does the bracket label hide a real signal, or is there none?
A read-only script re-scored the eight formulas on the cached bars and the cube's eligibility
mask, discovery window only (1,366 sessions, 452 names; nothing after 2021-12-31 read; the cube's
labels never read), with plain forward close-to-close returns, the lane's own 20-session block
bootstrap for t, a Spearman rank IC per session, a top-minus-bottom decile spread, and an AR(1)
random score as control. Costs: 10 bps round trip on picks changes no conclusion.

| Formula | Edge, 20 sessions (bps, t) | Edge, 5 sessions (bps, t) | Rank IC 20 (mean, bootstrap t, % sessions > 0) | Long–short decile 20 (bps, t) | Edge per ATR, no bracket vs campaign bracket |
| --- | --- | --- | --- | --- | --- |
| `reversal_5` | +40 (1.61) | +16 (1.30) | +0.013 (1.47, 53%) | +19 (0.66) | −0.048 vs −0.021 |
| `signed_volume` | −9 (−0.55) | −4 (−0.81) | −0.014 (−1.56, 46%) | −24 (−0.93) | −0.029 vs −0.026 |
| `overnight_intraday` | +24 (0.72) | +7 (0.65) | −0.004 (−0.33, 49%) | +1 (0.01) | −0.058 vs −0.050 |
| `abnormal_volume` | −9 (−0.90) | −4 (−0.93) | −0.008 (−1.49, 46%) | −45 (−2.26) | −0.044 vs −0.029 |
| `momentum_12_1` | +96 (2.16) | +26 (2.06) | +0.013 (0.63, 56%) | +92 (1.29) | +0.007 vs −0.025 |
| `price_volume_corr` | +20 (1.43) | +3 (0.60) | +0.021 (2.47, 57%) | +18 (0.61) | +0.019 vs +0.015 |
| `illiquidity` | +147 (2.40) | +39 (2.24) | +0.034 (1.59, 58%) | +198 (2.35) | −0.006 vs −0.016 |
| `reversal_x_volume` | +27 (1.32) | +14 (1.29) | +0.013 (1.46, 52%) | +23 (0.84) | −0.046 vs −0.017 |
| random AR(1) φ 0.95 (control) | −16 (−1.15) | −7 (−1.79) | −0.006 (−1.09, 45%) | −20 (−0.99) | −0.032 |

- No formula reaches |t| ≥ 3 on any return-space statistic under the session-block bootstrap.
  Naive IC t statistics (which ignore session dependence) exceed 3 for four formulas, but so do
  about half of 200 persistent random scores (95th percentile 6.8 to 8.3); they are not evidence.
- Measured per unit of ATR without a stop or target, the edges reproduce the campaign's bracket
  edges in sign for seven of eight formulas. `illiquidity` and `momentum_12_1` look good in raw
  returns only because their deciles hold high-volatility names in a rising 2016–2021 market;
  per unit of risk the edge is gone.
- The smallest p-value against a matched random control is 0.03 before any correction for
  eight formulas.

**Answer:** these formulas carry no reliable cross-sectional signal on this cohort and window
in return space either; the bracket label is not what removes an edge. Caveats: adjusted prices;
cohort v2 is today's members, which if anything flatters momentum and illiquidity.
Artifacts: `~/agentic-trader-research/pooled-label-diagnostic-20261007/` (`report.md`, script,
tables).
