# Short-suppression test on a fresh window — protocol (frozen before any data is read)

**Why.** In the [setup-outcome study](../../setup-outcomes-2026-09-23.md), native **short**
setups lost money in development (2021-06 to 2026-01):

- trend pullback 4h: −0.18R after 5 bp per side, n = 4,004;
- squeeze 1h: −0.16R, n = 1,139.

Longs were about +0.03R. That window was mostly a rising market, and current-universe
survivorship flatters longs. The study says the bias should be tested on data it never
saw before any config change.

**Fresh window.** Decisions from **2017-01-03 to 2021-04-30**. This is entirely before
the study's development start (2021-06-01), and the gap exceeds the 20-session
horizon, so no label overlaps the study. Alpaca SIP history starts in 2016, which gives
one year of daily lookback. Bars run through 2021-06-15, so every label can mature.

**Method.** Identical to the study:
- the same live-clock replay (10:35 and 14:35 New York), strategies, duplicate rule and
  deterministic levels;
- the same bracket labeler, with a 20-session timeout and R_cost at 5 bp per side;
- all-adjusted SIP bars and the same 159-name current universe (survivorship caveat
  retained).

No models are fitted; this is a descriptive base-rate test with predeclared
hypotheses.

**Hypotheses** (stationary session-block bootstrap, block mean 10, 2,000 draws,
seed 20260923; one-sided):

- **S1:** mean R_cost of short setups (all native strategies and timeframes pooled)
  < 0. The 90% CI upper bound must be < 0.
- **S2:** mean R_cost(long) − mean R_cost(short) > 0. This is a paired session-block
  bootstrap over sessions that contain setups; the 90% CI lower bound must be > 0.
- Per strategy and timeframe base rates are descriptive only.

**Decision rule (predeclared).**
- **If S1 and S2 both hold:** add a per-strategy `allow_short` switch (default `true`,
  so behaviour is unchanged) and set `allow_short: false` for `trend_pullback` and
  `squeeze_breakout` in `config/config.yaml`. The live desk then stops carding native
  short setups. Mined or probe alphas are unaffected.
- **Otherwise:** no config change. Record the result, and keep shorts, which remain
  covered by the prospective shadow evidence.

This window is used once for this test and recorded in the result doc.
