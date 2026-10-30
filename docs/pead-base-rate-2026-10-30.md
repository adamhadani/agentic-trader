# PEAD v2 base rate in the card unit (2026-10-30)

Research only. `config/research/pead-base-rate-v1.json` recomputes the PEAD v2 study's LONG events in the
desk's card unit, `net_excess_r_v1` (bracket R after a per-name cost, minus the trailing 120-session beta
times the market's R over the same hold). It is descriptive, decides nothing, and earns no probe, shadow or
promotion credit. The PEAD card (Task 3 of the honest-stream PR) shows its expected figure.

## Provenance

| Input | Value |
| --- | --- |
| Frozen entry | `config/research/apriori/pead-v2.json`, sha256 `fc8a5b3ec14845a2a6ce730e2bc4159e7ee579bbb6b03164a01490e90d818627` (equals the study manifest's `sha256`; the script refuses otherwise) |
| Study | `~/agentic-trader-research/apriori-pead-v2-20260926` (run 2026-09-26): `events.csv.gz` sha256 `23ce01f7…`, `labels.csv.gz` `239d8526…`, `protocol.json` `d5defdfd…` |
| Bars | `~/agentic-trader-research/apriori-pead-v1-cache/bars` (hourly and daily per symbol, SPY daily); SPY hourly from `setup-cache-2017` and `setup-cache-v1`, concatenated and de-duplicated |
| Window | decisions 2016-08-01 to 2026-07-31; recent window from 2025-01-01 |
| Output | `config/research/pead-base-rate-v1.json`, sha256 `08c02fed9abb3264acf77aecd94364e5bf1d1318d1f08b3792a06434145e267a` (generated from a dirty tree; regenerated from a clean commit before it is pinned) |

```
uv run python scripts/pead_base_rate.py ~/agentic-trader-research/apriori-pead-v2-20260926 \
  ~/agentic-trader-research/apriori-pead-v1-cache \
  ~/agentic-trader-research/setup-cache-2017,~/agentic-trader-research/setup-cache-v1 \
  config/research/pead-base-rate-v1.json
```

Offline: no provider, network, database or broker. Spell the two SPY cache paths out; the shell does not
expand a `~` after the comma, and a missing listed cache now stops the script.

**Rehearsal check.** The script re-labels every event's LONG row with the study's own `decision_price`,
`levels_for` and `label_bracket` at the decisive 5 bp cost and refuses to write unless the 23,980 LONG rows
match the study's `labels.csv.gz` one for one (`r` within 1e-9, `hit` identical). They matched.

**Coverage.** 23,980 LONG rows labelled. 112 had no trailing beta (`n_no_beta`: too little daily history
or no daily bars for the name, spread across all years); none lacked a market return (`n_no_market` 0: SPY hourly covers the whole
window). They are excluded, never filled, which leaves `n_all` 23,868 reporters and `n_leg` 4,712 leg events.

## Results

Leg means are the mean `net_excess_r` of leg events (surprise and reaction agree LONG). Control is every
reporter in the same window. P2 is each leg event minus its own session's all-reporter mean, so it asks
whether the leg beats the other reporters of the same day. Standard errors are session-clustered.

| Year | Leg n | Leg mean | Control mean | P2 mean | P2 se |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2016 | 112 | +0.091 | -0.087 | +0.221 | 0.165 |
| 2017 | 334 | +0.017 | -0.082 | +0.084 | 0.075 |
| 2018 | 338 | -0.019 | -0.015 | +0.043 | 0.049 |
| 2019 | 394 | +0.010 | +0.041 | -0.037 | 0.055 |
| 2020 | 385 | -0.235 | -0.149 | -0.053 | 0.058 |
| 2021 | 526 | +0.023 | -0.066 | +0.101 | 0.060 |
| 2022 | 494 | +0.104 | +0.075 | +0.033 | 0.047 |
| 2023 | 597 | -0.159 | -0.161 | +0.016 | 0.041 |
| 2024 | 642 | +0.056 | -0.051 | +0.069 | 0.055 |
| 2025 | 550 | -0.014 | -0.007 | -0.029 | 0.052 |
| 2026 | 340 | -0.098 | -0.119 | -0.010 | 0.074 |

| Window | Leg n | Leg mean | se | Control mean | P2 mean | P2 se |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Overall 2016-2026 | 4,712 | -0.024 | 0.022 | -0.054 | +0.029 | 0.018 |
| Recent 2025-01-01 to 2026-07-31 | 890 | -0.046 | 0.052 | -0.050 | -0.022 | 0.043 |

Expected excess (the figure on the card): between-quarter tau of the leg's `net_excess_r` is 0.137
(`card_prior.between_quarter_tau`, floor and 20-row quarter minimum as the card prior), so
`B = tau^2 / (tau^2 + se_recent^2) = 0.872`, and the expected excess is `B x recent mean = -0.040R`, se
`sqrt(B) x se_recent = 0.049R`. The shrinkage toward zero is mild because the recent standard error is small
beside the quarter-to-quarter spread.

## Reading

In the card unit the PEAD v2 long leg has lost money on average: -0.024R overall and -0.046R since 2025,
against the study's P2 of +0.055R over all reporters in raw R. Most of that gap is the market: the control (all
reporters) is -0.054R overall, so a long position in a reporter drifted below its beta-matched SPY after
cost. The leg beat that control by +0.029R (1.6 se), the study's P2 sign, but the recent window's
paired difference is -0.022R +/- 0.043R, indistinguishable from zero and of the wrong sign.

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
