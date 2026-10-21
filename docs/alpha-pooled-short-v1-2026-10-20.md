# Pooled short-volume fixed set (`short-v1`, `cohort3-short-v1`): checks and campaigns (2026-10-20)

Research only. Nothing here promotes, journals shadow credit, or touches the registry, broker or
Telegram. The lane is described in [pooled mining](alpha-pooled-mining.md#short-volume-lane-short_fields_v1)
and the [design](superpowers/specs/2026-10-20-pooled-short-volume-design.md).

## What was run

| Item | Value |
| --- | --- |
| Fields | `short_fields_v1`: `short_ratio`, `short_ratio_z`, `short_volume_rel`, `offex_share` from FINRA's Consolidated NMS daily short-sale volume files (off-exchange volume only), read at `D-1` beside the daily bars |
| Short-volume files | 2018-08-01 → 2026-09-01 on Alpaca's exchange calendar: 2,032 sessions, none absent, 666,720,874 bytes; files digest `838521ef4aedd1dc…` (manifest `~/agentic-trader-research/short-volume-20261020d/`); `z_window` 20, `min_total_volume` 10,000, `max_offex_share` 1.5 |
| Corporate actions | cohort v2 `776d4df7ef9312456…` (shared with events-v1), cohort v3 `8a547b0694d9f171…` (shared with cohort3-events-v1); both verified against the protocol before any bar was read |
| Protocols | `short-v1.json` `4be5bf3a584d7334…` (cohort v2, 5 bp, static-percentile universe) and `cohort3-short-v1.json` `817076247520a85f…` (cohort v3, 15 bp, $5M median dollar-volume floor); eight predeclared formulas each, fixed set, checks A and C only |
| Cubes | cohort v2 `9bedbf9b4b8e7541…` (cached, reused), cohort v3 `75dbb80a710f64dd…` (cached, reused); 2016-08-01 → 2026-07-31 |
| Code revision | main `08a98ba` (PR #126), clean detached worktree |
| Runs (ET) | cohort v2: check A 07:42 → 09:05, check C 09:05 → 09:11, campaign 09:11:56 → 09:12:40; cohort v3: check A 09:12 → 12:52, check C 12:52 → 12:53, campaign 12:53:56 → 12:54:52; one operator approval ("go ahead and merge, i'm ok with the follow up experiment too") covering both campaigns, chained after the checks of the same protocol passed |
| Artifacts | `~/agentic-trader-research/pooled-power-a-{short-v1,cohort3-short-v1}-20261020/`, `pooled-null-check-…-20261020/`, `pooled-campaign-…-20261020/` (private) |

### Coverage

| | Cohort v2 | Cohort v3 |
| --- | --- | --- |
| Symbols with an eligible cell | 437 | 1,000 |
| Symbols without any short-volume row | 0 | 1 (`HOS`) |
| Identity breaks (fields `NaN` before a `name_change` onto or off the ticker) | 29 | 98 |
| Thin days (`TotalVolume` below 10,000, cell unscored) | 697 | 11,392 |
| `offex_share` clipped at 1.5 | 695 | 1,539 |
| Rows for symbols outside the scored set (dropped by the loader) | 18.6M | 17.6M |

First finite sessions: `short_ratio` and `offex_share` on 2018-08-01 (first score read 2018-08-02),
`short_ratio_z` and `short_volume_rel` on 2018-08-29 (20 finite priors; first score read 2018-08-30),
the five-session means a week later. The discovery window therefore holds about 836 scored sessions
(August 2018 → December 2021) against about 1,360 for the daily and events sets, and the gate's "3 of 4
positive blocks" is 3 of 3 (the first block carries no score).

## Check A: power — passed (both protocols)

Check A is formula-independent, so each protocol replays its cohort's curve on the cached cube.

| Planted edge (R per pick) | v2 detection | v2 false acceptance | v3 detection | v3 false acceptance |
| --- | --- | --- | --- | --- |
| 0.00 | 0.00 | 0.00 | 0.00 | 0.01 |
| 0.05 | 0.40 | 0.00 | 0.51 | 0.01 |
| 0.08 | 0.84 | 0.00 | 0.76 | 0.01 |
| 0.10 | 0.95 | 0.00 | 0.85 | 0.01 |
| 0.12 | 0.98 | 0.00 | 0.92 | 0.01 |
| 0.15 (gate) | 0.99 | 0.00 | 0.98 | 0.01 |

Identical to the daily, intraday and events curves on the same cubes, as the protocol requires. Check A
plants its edge over the full window, so for this lane, whose score exists on about 60% of the discovery
sessions, it slightly overstates power for small edges (disclosed in each protocol's rationale).

## Check C: real-formula false acceptance — passed (both protocols)

| Protocol | Replicates | False acceptances (limit 2) | Seed-formula discovery t (n 320) | Replicates with a discovery survivor | Reached confirmation | Errors |
| --- | --- | --- | --- | --- | --- | --- |
| `short-v1` (v2) | 40 | 0 | mean −0.11, sd 1.13 | 0 | 0 | 0 |
| `cohort3-short-v1` (v3) | 40 | 0 | mean −0.15, sd 1.18 | 1 | 0 | 0 |

On cohort v3 one null replicate carried a seed formula through discovery and lost it at selection, the
same path the real campaign took below.

## Campaigns

Each campaign charged its eight formulas to the pooled ledger (journal scope `production/alpaca:paper`,
streams `alpha/pooled/campaign/pooled-short-volume-campaign-v1-{4be5bf3a584d7334,817076247520a85f}`)
before scoring them: 8 charged, 8 evaluated, 0 errors, 0 rejected in each. Discovery window 2016-08-01 →
2021-12-31 (scored from 2018-08-02), top-decile picks per session among names with a finite score; `edge`
is the session-paired pick-minus-pool mean R, `t` its session-block statistic, `leg` the picks' own mean R.

### Cohort v2 (`short-v1`): no finalists

| Family | Expression | Sessions | Edge (R) | t | Leg (R) |
| --- | --- | --- | --- | --- | --- |
| light_shorting | `-short_ratio` | 851 | +0.004 | +0.26 | +0.183 |
| light_shorting_week | `-ts_mean(short_ratio, 5)` | 847 | −0.012 | −0.71 | +0.166 |
| shorts_retreat | `-short_ratio_z` | 832 | +0.008 | +0.97 | +0.180 |
| shorts_retreat_week | `-ts_mean(short_ratio_z, 5)` | 828 | −0.002 | −0.18 | +0.170 |
| shorts_retreat_volume | `-short_volume_rel` | 833 | +0.014 | +0.88 | +0.190 |
| unopposed_strength | `-short_ratio_z / (ts_sum(returns, 5) > 0)` | 829 | −0.020 | −1.18 | +0.169 |
| squeeze | `short_ratio_z / (ts_sum(returns, 5) < 0)` | 830 | +0.000 | +0.00 | +0.179 |
| quiet_tape | `-offex_share` | 852 | +0.022 | +0.77 | +0.199 |

Nothing passed the discovery gate; nothing was carried; the confirmation window is unused. Every edge is
within ±0.022R and no |t| reaches 1.2.

### Cohort v3 (`cohort3-short-v1`): one discovery survivor, no confirmation candidate

| Family | Expression | Sessions | Edge (R) | t | Leg (R) |
| --- | --- | --- | --- | --- | --- |
| light_shorting | `-short_ratio` | 853 | +0.019 | +2.69 | +0.089 |
| light_shorting_week | `-ts_mean(short_ratio, 5)` | 848 | +0.018 | +1.81 | +0.088 |
| **shorts_retreat** | `-short_ratio_z` | 836 | **+0.020** | **+3.40** | +0.087 |
| shorts_retreat_week | `-ts_mean(short_ratio_z, 5)` | 828 | +0.016 | +1.61 | +0.089 |
| shorts_retreat_volume | `-short_volume_rel` | 833 | −0.000 | −0.02 | +0.070 |
| unopposed_strength | `-short_ratio_z / (ts_sum(returns, 5) > 0)` | 833 | −0.007 | −0.52 | +0.073 |
| squeeze | `short_ratio_z / (ts_sum(returns, 5) < 0)` | 833 | +0.021 | +1.52 | +0.087 |
| quiet_tape | `-offex_share` | 854 | −0.007 | −0.37 | +0.062 |

`shorts_retreat` (formula `de8a6ca81613e5e0`) passed the discovery gate: edge +0.020R, ci90
[+0.011, +0.030], t +3.40 on 836 sessions, block means [no score, +0.049, +0.018, +0.019] (3 of 3 scored
blocks positive). It was carried to the selection window (2022-01-03 → 2023-12-29, 498 sessions, picks
purged of overlap) and was **not kept**: edge +0.006R, se 0.007, t +0.88, ci90 [−0.005, +0.019], below
the required half of its discovery edge (+0.010R). With nothing frozen the campaign ended
`no_confirmation_candidates`; the confirmation window (2024-01-02 → 2026-07-31) was **not read** and is
still unused for the whole pooled lane. `light_shorting` (t +2.69) fell short of the t ≥ 3 gate.

## Reading

- **On the 452 most liquid names, off-exchange short-sale volume carries nothing** the long bracket can
  use: eight edges inside noise, signs mixed, the best |t| below 1.2. This is the population where the
  literature's short-ratio effect is weakest (Boehmer, Jones and Zhang 2008 find it concentrated in
  smaller, less liquid names), so the null is the expected result, not a surprise.
- **On the next 1,000 names, the short-ratio cluster leans the literature's way in discovery and then
  fades.** `short_ratio`, its z-score and their weekly means all sit between t +1.6 and +3.4 with edges
  of +0.016 to +0.020R; they are one signal measured four ways, not four confirmations, and the one that
  cleared the gate lost two thirds of its edge on the next two years. A discovery-window edge that does not
  survive its own selection window is the pattern a true null with eight correlated tries produces about
  as often as a small real effect decaying; the null check reproduced the path once in 40 replicates.
- **The mid-cap base rate is the same as in the cohort-v3 study**: leg means +0.06 to +0.09R at 15 bp
  against +0.17 to +0.20R on cohort v2 at 5 bp, so the picks were never close to a positive bracket
  expectation in absolute terms either.
- The lane's discovery window is short (about 836 scored sessions) and the selection window covers the
  2022 drawdown and the 2023 rebound only. Neither is a reason to reopen the window: both were frozen
  before the read.

**Conclusion.** The pre-registered closure named `no_finalists` on both cubes; cohort v2 returned exactly
that and cohort v3 returned `no_confirmation_candidates` (a discovery survivor that failed selection),
which is the same outcome one stage later: nothing was frozen, nothing is probe-eligible, the confirmation
window is unused. The short-volume fixed set is **closed** on this window as an alpha source. The
`shorts_retreat` discovery result on mid caps is recorded as a hypothesis only; a second look (for example
the same formula on a cohort of smaller names, where the literature places the effect, or with a live
daily-file feed contract) needs its own frozen protocol declared as a second look, its own checks, and
operator approval. The pooled lane has now tested 64 formula-cohort pairs under the frozen long bracket
with no finalist. The next entry-side data source, per the
[data-augmentation assessment](alpha-data-augmentation-2026-10-18.md), is an operator decision (EDGAR
insider transactions was ranked second).
