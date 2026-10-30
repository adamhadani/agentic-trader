# PEAD v2 base rate in the card unit (2026-10-30)

Research only. `config/research/pead-base-rate-v1.json` recomputes the PEAD v2 study's LONG events in the
desk's card unit, `net_excess_r_v1` (bracket R after a per-name cost, minus the trailing 120-session beta
times the market's R over the same hold). It is descriptive, decides nothing, and earns no probe, shadow or
promotion credit. The PEAD card shows its expected figure.

## Provenance

| Input | Value |
| --- | --- |
| Frozen entry | `config/research/apriori/pead-v2.json`, sha256 `fc8a5b3ec14845a2a6ce730e2bc4159e7ee579bbb6b03164a01490e90d818627` (equals the study manifest's `sha256`; the script refuses otherwise) |
| Study | `~/agentic-trader-research/apriori-pead-v2-20260926` (run 2026-09-26): `events.csv.gz` sha256 `23ce01f7…`, `labels.csv.gz` `239d8526…`, `protocol.json` `d5defdfd…` |
| Bars | `apriori-pead-v1-cache` (hourly and daily per symbol, SPY daily): the file records the cache name, the sha256 of `bars/cache_range.json` (`d9d9b64e…`) and a digest over the sorted names (content hashes) of the hourly and daily `.npz` files used (`24961e72…`). SPY hourly from `setup-cache-2017` and `setup-cache-v1`, concatenated and de-duplicated: names digest `851e3c8a…`. The daily bars set beta and cost, which the reproduction check does not cover, so the digests pin them |
| Window | decisions 2016-08-01 to 2026-07-31; recent window from 2025-01-01 |
| Output | `config/research/pead-base-rate-v1.json`, sha256 `b92308070957a0bc4f3cb917bfb2e57ab3d59f73b74e38245cf0431d69890551` |

```
uv run python scripts/pead_base_rate.py ~/agentic-trader-research/apriori-pead-v2-20260926 \
  ~/agentic-trader-research/apriori-pead-v1-cache \
  '~/agentic-trader-research/setup-cache-2017,~/agentic-trader-research/setup-cache-v1' \
  config/research/pead-base-rate-v1.json
```

Offline: no provider, network, database or broker. Quote the comma-separated SPY cache argument (the shell does not
expand a `~` after the comma; the script does), and a listed cache without `SPY_1h` bars stops the script.

**Rehearsal check.** The script re-labels every event's LONG row with the study's own `decision_price`,
`levels_for` and `label_bracket` at the decisive 5 bp cost and refuses to write unless the 23,980 LONG rows
match the study's `labels.csv.gz` one for one (`r` within 1e-9, `hit` identical). They matched.

**Coverage.** 23,980 LONG rows labelled. 112 had no trailing beta (`n_no_beta`: too little daily history
or no daily bars for the name, spread across all years); none lacked a market return (`n_no_market` 0: SPY hourly covers the whole
window). They are excluded, never filled, which leaves `n_all` 23,868 reporters and `n_leg` 4,712 leg events.

## Results

Leg means are the mean `net_excess_r` of leg events (surprise and reaction agree LONG). "All" is every
reporter in the same window (the control). P2 is each leg event minus its own session's all-reporter mean, so it asks
whether the leg beats the other reporters of the same day. Standard errors are session-clustered.

| Year | Leg n | Leg mean | se | All n | All mean | se | P2 mean | P2 se |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2016 | 112 | +0.091 | 0.164 | 565 | -0.087 | 0.108 | +0.221 | 0.165 |
| 2017 | 334 | +0.017 | 0.094 | 1,712 | -0.082 | 0.043 | +0.084 | 0.075 |
| 2018 | 338 | -0.019 | 0.068 | 1,841 | -0.015 | 0.043 | +0.043 | 0.049 |
| 2019 | 394 | +0.010 | 0.070 | 1,906 | +0.041 | 0.041 | -0.037 | 0.055 |
| 2020 | 385 | -0.235 | 0.071 | 2,170 | -0.149 | 0.049 | -0.053 | 0.058 |
| 2021 | 526 | +0.023 | 0.069 | 2,550 | -0.066 | 0.047 | +0.101 | 0.060 |
| 2022 | 494 | +0.104 | 0.059 | 2,566 | +0.075 | 0.036 | +0.033 | 0.047 |
| 2023 | 597 | -0.159 | 0.049 | 2,809 | -0.161 | 0.038 | +0.016 | 0.041 |
| 2024 | 642 | +0.056 | 0.070 | 2,893 | -0.051 | 0.043 | +0.069 | 0.055 |
| 2025 | 550 | -0.014 | 0.064 | 3,012 | -0.007 | 0.044 | -0.029 | 0.052 |
| 2026 | 340 | -0.098 | 0.091 | 1,844 | -0.119 | 0.062 | -0.010 | 0.074 |

| Window | Leg n | Leg mean | se | All n | All mean | se | P2 mean | P2 se |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Overall 2016-2026 | 4,712 | -0.024 | 0.022 | 23,868 | -0.054 | 0.014 | +0.029 | 0.018 |
| Recent 2025-01-01 to 2026-07-31 | 890 | -0.046 | 0.052 | 4,856 | -0.050 | 0.036 | -0.022 | 0.043 |

Expected excess (the figure on the card): between-quarter tau of the leg's `net_excess_r` is 0.137
(`card_prior.between_quarter_tau`, floor and 20-row quarter minimum as the card prior), so
`B = tau^2 / (tau^2 + se_recent^2) = 0.872`, and the expected excess is `B x recent mean = -0.040R`, se
`sqrt(B) x se_recent = 0.049R`. The `±` is the se of the shrunk 2025-26 mean. It is not comparable with a native card's `±`, which at
n=0 is sqrt(tau^2 + se^2): 0.175 for TREND_PULLBACK 4h and 0.215 for SQUEEZE_BREAKOUT 4h; the same formula
gives 0.146 for PEAD. The 2025-26 quarter means ran from -0.27R to +0.20R. The shrinkage toward zero is
mild only under the per-quarter tau: tau is per quarter and the window spans seven quarters, and
tau/sqrt(7) would give B = 0.49 and -0.023R +/- 0.037. The card uses the per-quarter tau, as the spec
states, and the R5 outcome (below zero, within noise) is the same either way.

## Reading

In the card unit the PEAD v2 long leg has lost money on average: -0.024R overall and -0.046R since 2025.
The study figure comparable with that leg mean is P1, +0.149R at 5 bp. Most of the gap between them is the
market, and that is true of P1: the mean of beta times market R is 0.165R and `cost_r` is 0.027R.

The study's P2, +0.055R, is also at 5 bp (not raw R) and is a pooled difference of means (0.1489 - 0.0939),
not a within-session beat. This file's P2 is a within-session difference: the leg beat the all-reporter
control in the same session by +0.029R +/- 0.018 overall (1.6 se) and by -0.022R +/- 0.043 in the recent
window. On the study's pooled estimand the recent figure is +0.004R, and 2026 is +0.021R where the
within-session figure is -0.010R. So the recent sign depends on the estimand, and both are within noise.

## Caveats

- EPS and consensus are as served today, not point-in-time.
- Cohorts are survivorship-biased (current Alpaca listings).
- The 2016-2026 window was already read by v1 and v2, so none of this is out-of-sample for the entry's design.
- The figure is descriptive, not validated alpha. The card unit's cost is the desk's per-name model, not
  realised fills.
- The recent window overlaps the market's 2025-26 path; the leg is long-only and is compared with the
  beta-matched market, not with a market-neutral hedge.

## Economic gate (R5)

R5 asks that a source's `net_excess_r_v1` on its most recent window, shrunk, be at least zero before any
auto-entry. PEAD v2 does not meet it: the shrunk recent figure is -0.040R +/- 0.049R. It is within one standard
error of zero, so the data do not show a loss either, but a gate that requires the figure to be at least zero
fails. PEAD cards stay tap-only, as they are today, and the figure goes on the card so the operator sees it
beside the card's own risk.
