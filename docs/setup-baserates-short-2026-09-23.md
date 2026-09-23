# Short-suppression test — September 23, 2026

**Result: the predeclared rule fired.**

- Native short setups lost **−0.33R per setup after costs** on a fresh 2017–2021 window
  that no earlier study had used. Longs beat shorts by about +0.5–0.7R.
- Per the frozen decision rule, the native strategies (trend pullback, squeeze breakout)
  now have `allow_short: false`. Native cards are long-only.
- Mined and probe alphas are unaffected.

## Protocol

- **Frozen protocol:** [short-suppression test](superpowers/specs/2026-09-23-short-suppression-test.md),
  committed before any data was read, as `config/research/setup-baserates-short-v1.json`.
- **Window:**
  - decisions from 2017-01-03 to 2021-04-30, before the
    [setup-outcome study](setup-outcomes-2026-09-23.md)'s development period;
  - a gap longer than the 20-session label horizon;
  - bars through 2021-06-15.
- **Method:** the same live-clock replay, strategies, duplicate rule, levels and bracket
  labeler as the study; SIP, all-adjusted; 159-name current universe (survivorship
  caveat).
  - Seven names had no data in the window because they listed later: AUGO, BRZE, BTDR,
    CHYM, GEHC, KNF and KVYO.
- **Statistics:** setup-weighted means with a stationary session-block bootstrap (block
  mean 10, 2,000 draws, seed 20260923). S2 resamples whole sessions once per draw for
  both directions.
  - A pre-run review corrected an implementation that had averaged per-session means,
    before any data was read.
- **Scale:** 11,480 labelled setups (2 were still immature and were dropped).
- **Artifacts:** `~/agentic-trader-research/setup-baserates-short-v1-20260923/`.

## Hypotheses

| | Estimate | 90% CI | Holds |
| --- | ---: | --- | :---: |
| **S1** short mean R_cost < 0 (n = 3,365, 899 sessions) | **−0.325R** | [−0.430, −0.212] | ✅ |
| **S2** long − short mean R_cost > 0 (8,115 long / 3,365 short, 1,082 sessions) | **+0.692R** | [+0.387, +1.083] | ✅ |

**Decision: `suppress_native_shorts`.**

## Base rates (descriptive)

| Strategy | TF | Dir | n | Target | Stop | Mean R_cost | Median | 1%-trimmed |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Trend pullback | 4h | long | 7,243 | 39% | 55% | +0.19 | −0.71 | +0.18 |
| Trend pullback | 4h | short | 2,615 | 22% | 73% | **−0.34** | −0.94 | −0.35 |
| Squeeze | 1h | long | 768 | 38% | 58% | +2.08 ⚠︎ | −0.72 | +0.06 |
| Squeeze | 1h | short | 623 | 28% | 69% | **−0.28** | −0.88 | −0.29 |
| Squeeze | 4h | long | 106 | 30% | 56% | +0.11 | −0.81 | +0.11 |
| Squeeze | 4h | short | 127 | 20% | 69% | **−0.31** | −0.95 | −0.32 |

⚠︎ **Data artifact.** A post-decision diagnostic found that one squeeze-1h long,
**GPOR on 2020-11-24, has R = +1,546**. Gulfport Energy went through bankruptcy and
restructuring around then, which an adjusted price series can't represent, and that
single row drives the squeeze-long mean.

It doesn't affect S1, which covers shorts only: their largest R is 6.9 and the trimmed
mean equals the mean. Without that row S2's estimate falls to about +0.50R, still far
above zero.

Other large Rs (up to about 12) are target fills at a gap open, which the labeler
records honestly.

**Follow-up:** labelers should flag implausible R values (for example |R| > 20) as
data-quality exclusions, and count them.

## Consistency with the study

The setup-outcome study's development window (2021-06 to 2026-01, a different period)
shows the same pattern:
- trend pullback shorts: −0.18R;
- squeeze 1h shorts: −0.16R;
- longs: about +0.03R.

The short deficit holds across both windows. Longs look better in 2017–2021 (+0.19R on
trend pullback) than in 2021–2026, so the long edge remains thin and period-dependent.

## Change

- `strategies.trend_pullback.allow_short` and `strategies.squeeze_breakout.allow_short`
  are new fields, default `true`; `config/config.yaml` sets both to `false`. They are
  enforced where the short candidates are built, so live scans, research replay and
  backtests all respect them.
- The committed study protocols pin the strategy config they ran with. Re-running them
  now is refused as stale, which is correct: their evidence covers shorts.
- **Reversible:** set `allow_short: true`. The prospective shadow evidence
  (`copilot cards outcomes`) now accumulates long-only native cards.
