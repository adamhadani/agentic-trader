# Setup recurrence test — October 14, 2026

**Result: the predeclared rule did not fire (`no_change`).** On replayed 2021–2026 history,
native setups that recurred within 20 sessions did **not** resolve better than first
occurrences; if anything they did slightly worse. The live journal's +0.96R-versus-−0.46R split
of October 14 does not replicate, and no repeat-preferring construction rule is proposed. The live
`recurrence` block keeps measuring.

## Protocol and run

- Frozen protocol `config/research/setup-recurrence-v1.json`, identity `53bd0c475e5119b5…`,
  committed before any data was read ([spec](superpowers/specs/2026-10-14-setup-recurrence-design.md)).
- **Replay.** The unchanged live `StrategyEngine` at the 10:35 and 14:35 New York scan instants
  over the 159-name current universe, with the current long-only strategy configuration
  (`allow_short: false`), deterministic levels and the live bracket labeller (20-session timeout,
  R_cost at 5 bp per side), from the two existing bar caches (SIP, all-adjusted). Run 2026-10-14
  07:00 ET from branch revision `a7a9b58`, about one hour.
- **Windows.** Primary: decisions 2021-06-01 → 2026-01-29 (the setup-outcome study's development
  window, never used for this hypothesis; bars through 2026-09-22). Robustness: 2017-01-03 →
  2021-04-30 (bars through 2021-06-15). The study's one-shot holdout (2026-03-02 → 2026-08-14) and
  the live period were not read.
- **Recurrence.** A setup is a *repeat* when the same (symbol, strategy, timeframe, direction) was
  set up in an earlier session within the last 20 trading sessions and that prior's bracket had
  resolved (its hourly exit bar closed) by this decision; *first* when no such prior exists;
  *repeat_open* when the latest prior had not resolved (excluded from R1/R2).
- **Scale.** Primary: 8,768 labelled setups over 1,109 sessions (0 immature): 2,895 first, 3,447
  repeat, 2,426 repeat_open. Robustness: 8,119 setups (2 immature dropped): 2,576 / 3,052 / 2,491.
- **Artifacts.** `~/agentic-trader-research/setup-recurrence-v1-20261014/` (`protocol.json`,
  `manifest.json` with both cache ranges, `result.json`, `frames/primary.csv.gz`,
  `frames/robustness.csv.gz`, the run log).

## Hypotheses

Setup-weighted means at 5 bp per side; stationary session-block bootstrap (block mean 10, 2,000
draws, seed 20261014), whole sessions resampled once per draw for both groups; one-sided.

| Rule | Statistic | Estimate | 90% CI | Holds |
| --- | --- | ---: | --- | :---: |
| **R1** repeat − first (primary) | 3,447 vs 2,895 setups, 1,109 sessions | **−0.067R** | [−0.138, +0.005] | ❌ |
| **R2** repeat after a stop (primary) | 2,202 setups | **−0.021R** | [−0.111, +0.069] | ❌ |
| **R3** repeat − first (robustness, sign) | 3,052 vs 2,576 | +0.575R | [+0.024, +1.598] | ✅ (artifact, below) |
| **R4** sample floors | 3,447 repeats / 918 sessions / 2,202 after-stop vs 300 / 60 / 100 | — | — | ✅ |

**Decision as pre-registered: `no_change`.**

## Descriptives (never decisive)

**Primary window (2021-06 → 2026-01).**

| State | n | target | stop | mean R | mean R_cost | median R_cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| first | 2,895 | 35.6% | 59.6% | +0.143 | **+0.063** | −0.78 |
| repeat | 3,447 | 33.2% | 61.9% | +0.063 | **−0.004** | −0.86 |
| repeat_open | 2,426 | 36.6% | 60.7% | +0.158 | +0.071 | — |

- Repeats by prior outcome: after a **stop** (2,202) −0.021R, 32.4% target; after a **target**
  (1,240) +0.026R, 34.6%; after a timeout (5) +0.159R.
- **Within-session** difference (777 sessions holding both groups): repeat − first **−0.153R**,
  ci90 [−0.230, −0.070]. In the same session, repeats did worse than first occurrences.
- **Live definition** (repeat ∪ repeat_open vs first, the `cards outcomes` block's own rule):
  −0.036R, ci90 [−0.108, +0.031].
- **Tape:** SPY above its 200-session mean (3,094 vs 2,502): −0.066R [−0.141, +0.009]; below
  (353 vs 393): −0.123R [−0.344, +0.079].
- **Lookback:** 5 sessions −0.053R [−0.124, +0.021]; 10 sessions −0.060R [−0.133, +0.010];
  20 sessions −0.067R. The sign does not depend on the lookback.
- **By setup:** TREND_PULLBACK 4h first +0.030R (1,902) vs repeat +0.003R (3,190);
  SQUEEZE_BREAKOUT 1h first +0.120R (875) vs repeat −0.087R (256).
- **By year** (first / repeat mean R_cost): 2021 −0.10 / −0.22; 2022 −0.13 / −0.33; 2023 +0.02 /
  −0.14; 2024 +0.21 / +0.12; 2025 +0.11 / +0.15; 2026 (January) +0.44 / +0.41. Repeats trailed
  firsts in four of six years and tied in the two rising years.

**Robustness window (2017-01 → 2021-04).** Repeat mean R_cost +0.697R against first +0.122R,
but the repeat median is −0.72R and the 1%-trimmed mean +0.187R: the mean is carried by one
cell, GPOR (SQUEEZE_BREAKOUT, decided 2020-11-24, after a stop) labelled at **+1,546R** — a
degenerate risk unit on adjusted bars around that name's late-2020 restructuring, exactly the kind
of value the sign-only R3 cannot defend against. Without it the window's difference is of the same
small magnitude as the primary window's, with the opposite sign (2020 alone: repeat +2.64R mean,
every other year within ±0.3R of first). R3 "holds" on paper and carries no information.

## What this says

- The live split (18 repeats at +0.96R, 28 firsts at −0.46R over 45 days) was a small, correlated,
  rising-tape sample, not a property of the setups. On 6,342 historical setups the difference is
  slightly negative and stable across lookbacks, tapes and strategies, and negative within sessions.
- A first-stop-then-repeat pattern (+1.52R live on 7 rows) is +0.00R to −0.02R on 2,202 historical
  cases.
- The native funnel's base rate is what the September study said: about +0.03R to +0.06R per
  TREND_PULLBACK long after cost, 33–36% target, in a sample that over-represents stops. Recurrence
  does not change that; construction rules cannot be built on it.
- Measurement did its job cheaply: one day from hypothesis to a frozen negative result, with no
  live change.

## Caveats

- Current-member universe (survivorship); the same limit as every setup study.
- `data_cutoff` does not clip cached bars; labels end at the cutoff because both caches end there
  and the 20-session cap bounds every label.
- The labeller does not cap R: a near-zero risk unit on adjusted bars can produce an extreme R
  (the GPOR cell). Means are reported with medians and trimmed means for that reason; a bounded
  risk-unit rule for the setup labeller is a separate improvement.
- The executor drops immature rows before tagging (0 and 2 here), so an immature prior cannot
  yield `repeat_open`; the first 20 sessions of each window can only be `first`.
- R2 resamples the full session calendar; `within_session` resamples only sessions holding both
  groups.
- Both windows are now used for this hypothesis; a second look would need a new window or a new
  frozen definition.
