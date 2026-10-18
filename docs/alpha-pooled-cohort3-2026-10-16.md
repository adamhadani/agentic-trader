# Pooled cohort v3 (the mid-liquidity band): checks and campaigns (2026-10-15/16)

Research only. Nothing here promotes, journals shadow credit, or touches the registry, broker or
Telegram. Cohort v3 and its three re-frozen fixed sets are described in
[pooled mining](alpha-pooled-mining.md) and the
[design](superpowers/specs/2026-10-15-pooled-cohort-v3-design.md).

## What was run

| Item | Value |
| --- | --- |
| Cohort | v3, `config/research/pooled/cohort-v3.json`, SHA-256 `8c0d807aa47b19d3…` (1,000 names: ranks 401–1,400 by 60-session median dollar volume ending 2023-12-29 on the 2026-10-15 equity snapshot; no config-group source) |
| Eligibility and cost | absolute floor: 20-session median raw dollar volume ≥ $5M at D−1, close ≥ $10, ≥ 30 eligible names per session; `decision_cost_bps` 15 (v2 protocols: mega-cap static percentile, 5 bp) |
| Cube | spec identity `1e167ed644b3becf…`, cube SHA-256 `75dbb80a710f64dd…`, file `cube-8c0d807aa47b19d3-1e167ed644b3becf.npz`; 2,514 sessions 2016-08-01 → 2026-07-31, 2,199,508 eligible cells, 2,198,811 labelled |
| Protocols | `cohort3-events-v1.json` `b692d7f9a1ec1d79…` (`pooled-events-campaign`), `cohort3-daily-v1.json` `002b0ac27e291210…` (`pooled-campaign`), `cohort3-intraday-v1.json` `e6cda73e2c157a18…` (`pooled-intraday-campaign`); each byte-identical to its cohort-v2 parent except cohort, universe, cost, version and prose |
| Event inputs | Nasdaq calendar pages (digest `9273e40a…`, shared with events-v1); cohort-v3 corporate actions 2016-01-01 → 2027-03-31 (23,617 actions, `content_sha256` `8a547b0694d9f171…`); both verified before any bar was read |
| Code revision | main `fe10fc6` (PR #122), clean detached worktree |
| Runs | cube build and events check A 2026-10-15 08:09 → 2026-10-16 01:14 ET (see below); events check C 01:14 → 01:15; daily checks 01:16 → 04:51; intraday checks 04:51 → 08:30; campaigns 08:39 → 08:42 ET (operator-approved, one approval covering all three); all outside scan windows |
| Artifacts | `~/agentic-trader-research/pooled-power-a-cohort3-{events,daily,intraday}-v1-*/`, `pooled-null-check-cohort3-*-v1-*/`, `pooled-campaign-cohort3-*-v1-20261016/` (private) |

Event-field coverage on cohort v3: 1,000 symbols with an eligible cell, 11 with no earnings report
in the calendar, 98 identity breaks (a `name_change` moving a ticker onto or off the symbol;
fields NaN before the break). The ratio of identity breaks is three times cohort v2's (29 of 437):
mid-cap tickers change hands more often.

### Building the cube

The first check-A launch (08:09 ET, 2026-10-15) downloaded daily bars for 1,118 symbols (the band
plus the static reference set, which the absolute floor does not read but which the SPY benchmark
reaches the cube through) in 12 minutes, computed eligibility in about 45 minutes, and then fetched
ten years of hourly bars for 1,000 symbols sequentially in one-year chunks at roughly 2.3 s per
request, 25–60 s per symbol: about eleven hours, suspended with `SIGSTOP` across the 10:35 and
14:35 ET scan windows. Two symbols (BFH, CHRD) hit the 10 s read timeout and the run failed closed
after acquisition, as designed. The rerun (19:00 ET, cache hits for everything else, a 60 s data
timeout through a `COPILOT_CONFIG` copy of `config.yaml`, worktree untouched) fetched both, built the
cube in about two hours and ran the replicates in three. A 1,000-name cube is a day's download;
the "≈85 minutes" estimate in the design was wrong.

## Check A: power — passed (all three protocols)

Check A is formula-independent, so the three protocols replay the same curve on the same cube.

| Planted edge (R per pick) | Detection | False acceptance |
| --- | --- | --- |
| 0.00 | 0.00 | 0.01 |
| 0.05 | 0.51 | 0.01 |
| 0.08 | 0.76 | 0.01 |
| 0.10 | 0.85 | 0.01 |
| 0.12 | 0.92 | 0.01 |
| 0.15 (gate) | 0.98 | 0.01 |

Against the cohort-v2 cube (0.40 / 0.84 / 0.95 / 0.98 / 0.99, false acceptance 0.00): the same
detection at the gate, lower in the middle of the curve, and a 1% false-acceptance rate at every
edge, consistent with a noisier label (wider spreads at 15 bp, more names, smaller names).

## Check C: real-formula false acceptance — passed (all three protocols)

| Protocol | Replicates | False acceptances (limit 2) | Seed-formula discovery t (n 320) | Errors |
| --- | --- | --- | --- | --- |
| events | 40 | 0 | mean +0.03, sd 1.27 | 0 |
| daily | 40 | 0 | mean +0.11, sd 1.19 | 0 |
| intraday | 40 | 0 | mean +0.11, sd 1.11 | 0 |

## Campaigns: no finalists (all three protocols)

Each campaign charged its eight formulas to the pooled ledger (journal scope
`production/alpaca:paper`) before scoring them: 8 charged, 8 evaluated, 0 errors, 0 rejected in each.
None passed the discovery gate (t ≥ +3 with three of four positive session blocks and a positive
edge); nothing was carried, selected or frozen; the lane-wide confirmation window is **still
unused**. Discovery window 2016-08-01 → 2021-12-31, top-decile picks per session among names with
a finite score, 15 bp decisive cost; `edge` is the session-paired pick-minus-pool mean R, `t` its
session-block statistic, `leg` the picks' own mean R.

### Events (`pooled-events-campaign-v1-b692d7f9a1ec1d79`)

| Family | Expression | Sessions | Edge (R) | t | Leg (R) |
| --- | --- | --- | --- | --- | --- |
| pead_surprise_decay | `earnings_surprise / (1 + earnings_age)` | 1,360 | +0.019 | +1.28 | +0.098 |
| pead_reaction_decay | `earnings_reaction / (1 + earnings_age)` | 1,362 | +0.032 | +1.85 | +0.108 |
| pead_reaction_window | `earnings_reaction / (earnings_age <= 20)` | 1,354 | −0.022 | −0.76 | +0.078 |
| pead_agreement | `sign(earnings_surprise) * earnings_reaction / (earnings_age <= 20)` | 1,356 | +0.006 | +0.22 | +0.088 |
| surprise_x_reaction | `earnings_surprise * earnings_reaction / (1 + earnings_age)` | 1,358 | +0.022 | +1.55 | +0.101 |
| quiet_momentum | `ts_sum(returns, 20) / (earnings_age > 20)` | 1,360 | −0.044 | −1.69 | +0.021 |
| quiet_reversal | `-ts_sum(returns, 5) / (earnings_age > 20)` | 1,360 | +0.027 | +1.49 | +0.115 |
| dividend_yield | `dividend_yield` | 1,253 | −0.007 | −0.22 | +0.078 |

### Daily (`pooled-campaign-v1-002b0ac27e291210`)

| Family | Expression | Sessions | Edge (R) | t | Leg (R) |
| --- | --- | --- | --- | --- | --- |
| reversal_5 | `-1.0 * roc(close, 5)` | 1,360 | +0.022 | +1.44 | +0.102 |
| signed_volume | `ts_sum(sign(returns) * volume, 10) / ts_sum(volume, 10)` | 1,361 | −0.011 | −0.74 | +0.069 |
| overnight_intraday | `ts_sum(open_gap, 21) - ts_sum(oc_spread, 21)` | 1,362 | +0.003 | +0.14 | +0.080 |
| abnormal_volume | `volume / ts_mean(volume, 50)` | 1,363 | −0.005 | −0.71 | +0.075 |
| momentum_12_1 | `delay(close, 21) / delay(close, 252) - 1.0` | 1,256 | +0.033 | +1.04 | +0.113 |
| price_volume_corr | `-1.0 * ts_corr(returns, volume, 21)` | 1,357 | +0.008 | +0.81 | +0.090 |
| illiquidity | `ts_mean(hl_spread, 21)` | 1,360 | +0.015 | +0.50 | +0.104 |
| reversal_x_volume | `-1.0 * roc(close, 5) * (volume / ts_mean(volume, 50))` | 1,361 | +0.020 | +1.45 | +0.100 |

### Intraday (`pooled-intraday-campaign-v1-e6cda73e2c157a18`)

| Family | Expression | Sessions | Edge (R) | t | Leg (R) |
| --- | --- | --- | --- | --- | --- |
| first_hour_momentum | `oc_spread` | 1,360 | +0.001 | +0.10 | +0.082 |
| overnight_persistence | `open_gap` | 1,363 | −0.016 | −1.18 | +0.063 |
| overnight_vs_intraday | `open_gap - oc_spread` | 1,361 | −0.005 | −0.36 | +0.077 |
| relative_volume | `volume / ts_mean(volume, 140)` | 1,348 | +0.002 | +0.16 | +0.082 |
| range_expansion | `hl_spread / ts_mean(hl_spread, 140)` | 1,345 | +0.004 | +0.55 | +0.083 |
| range_location | `(close - low) / (high - low)` | 1,360 | −0.002 | −0.32 | +0.078 |
| first_bucket_efficiency | `(close - open) / (high - low)` | 1,359 | +0.002 | +0.34 | +0.082 |
| intraday_high_proximity | `close / ts_max(high, 140)` | 1,348 | −0.045 | −2.33 | +0.036 |

## Reading

Twenty-four predeclared formulas, none within reach of the gate: the largest positive statistic on
cohort v3 is `pead_reaction_decay` at t +1.85 (+0.032R), and the only |t| above 2 is negative
(`intraday_high_proximity`, −0.045R, t −2.33: names near their 140-session high did worse than the
pool over the bracket). Two observations against cohort v2:

- **The population changed the picture less than the bet assumed.** On v2 the same 24 formulas
  produced seven negative edges out of eight in two sets and one large negative (`quiet_momentum`,
  t −3.34). On v3 the signs are mixed, the magnitudes are similar, and `quiet_momentum`'s negative
  edge shrinks to −0.044R (t −1.69), so the v2 observation does not replicate on an independent
  population and stays a hypothesis.
- **The long-bracket base rate fell by half.** Leg means on v3 are about +0.08R against about
  +0.18R on v2, at 15 bp instead of 5 bp. The band's names do not carry the mega caps' long drift
  over 20 sessions, which is the direction the literature predicts for the bracket but the opposite
  of what the long-only picks needed.

Negative statistics are hypotheses, not results: the sign-flipped formulas were not predeclared and
were read on the same discovery window, so a long-only lane cannot use them and the window they
would be read on is already used.

**Conclusion.** Cohort v3 is closed as an alpha source. Across two populations (the 452 most liquid
names and the next 1,000) the pooled fixed-set lane has now tested 48 formula-cohort pairs under
the frozen 20-session 2-ATR-stop 3R-target long bracket and found no finalist; the confirmation
window is unused. The constant across every negative is the bracket, not the population or the
formula family, so the next question is whether the bracket itself is the binding constraint: the
predeclared bracket counterfactual on the replayed native setups (the live card funnel is measured descriptively) (2R versus 3R target, trailing
ratchet on versus off, with an index control) is the next workstream. A fourth cohort or a fifth
formula family on this bracket is not recommended. Remaining untested sources on this lane: news
fields as events v2 (after an `alpha news` cohort acquisition) and a real-time-SIP intraday
decision, both operator decisions.

## Operational notes

The run was the first full cube build since v2 and it was slow: one-year hourly chunks at about
2.3 s per request (Alpaca-side), fetched sequentially, with the per-symbol cache the only resume
mechanism. Research downloads must not overlap suggestion scans, so the run was suspended with
`SIGSTOP`/`SIGCONT` across the two windows rather than killed (eligibility is not cached and costs
45 minutes a restart). The replicate phases pin six performance cores for about three hours per
protocol; the operator noticed the heat. Both are recorded here so the next cube build is planned
as a day's job, not an afternoon's.
