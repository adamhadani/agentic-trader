# Bracket counterfactual v1 — October 18, 2026

**Result: `no_change`.** None of the four predeclared exit policies beats the live 2R fixed
bracket on the replayed native setups, net of cost and net of SPY. The live trailing ratchet
(A2) is reliably *worse* than the fixed bracket by about 0.013R per setup (90% CI entirely below
zero). Every arm, including the live one, trails SPY by 0.10–0.15R over its own holding window in
both the 2021–2026 and the 2017–2021 windows. The bracket is closed as an explanation for the
native funnel's base rate: the entries lag the index, and no exit rule changes that. The
confirmation window was not read.

> **Erratum (2026-10-23, descriptive).** The 0.10–0.15R gap is measured against a beta-one SPY
> control, and the T-bill funds' R scale inflates it. Against a past-only beta-matched control, the
> setups the desk now scans (ex BIL/SHY) trail SPY by:
> - 0.04–0.06R in 2021–2026;
> - 0.00–0.01R in 2017–2021;
> - near-equity-only: 0.05–0.07R and 0.01–0.02R.
>
> No arm is positive, and the `no_change` decision is unchanged
> ([Erratum](#erratum-beta-matched-control-2026-10-23)).

## Protocol and run

- Frozen protocol `config/research/bracket-counterfactual-v1.json`, identity `a667acd2d7de662f…`,
  file SHA-256 `b86777d55bd956e0…`, committed before any bar was labelled
  ([spec](superpowers/specs/2026-10-18-bracket-counterfactual-design.md)).
- **Replay.** The unchanged live `StrategyEngine` at the 10:35 and 14:35 New York scan instants
  over the 159-name current universe, current long-only strategy configuration, deterministic
  levels, from the two existing SIP all-adjusted bar caches (`setup-cache-v1`, `setup-cache-2017`).
  Run 2026-10-18 14:30–14:35 UTC from branch revision `39b1f1e` (PR #123); five minutes after a
  ~25-minute replay.
- **Arms**, all sharing the entry (first regular hourly bar at or after the decision), the planned
  stop, planned risk R, a 20-session timeout and 5 bp per side:

  | Arm | Target | Ratchet |
  | --- | --- | --- |
  | `A0_live` | planned (2R) | off |
  | `A1_3r` | 3R | off |
  | `A2_2r_trail` | 2R | on |
  | `A3_3r_trail` | 3R | on |
  | `A4_trail_only` | none | on |

  The ratchet approximates the live `trailing_stop` rule (trigger 1.5R, trail 1.5R behind price)
  on hourly closes: once close ≥ fill + 1.5R the stop becomes max(current, close − 1.5R),
  effective from the next bar; ratcheted exits count as stops.
- **Windows.** Primary: decisions 2021-06-01 → 2026-01-29 (bars through 2026-09-22); robustness:
  2017-01-03 → 2021-04-30 (bars through 2021-06-15); confirmation (2026-03-02 → 2026-08-14,
  declared second use of the setup-outcome holdout): **not read**.
- **Scale.** Primary 8,768 setups over 1,109 sessions (43,840 arm rows; 100% mature);
  robustness 8,119 setups (40,605 rows).
- **Statistics.** Setup-weighted means; `r`, `r_cost` and `excess_r` winsorised at ±10R;
  stationary session-block bootstrap (block mean 10, 2,000 draws, seed 20261018), whole sessions
  resampled once per draw and applied to every arm (paired); one-sided p = (k+1)/(N+1).
- **Artifacts.** `~/agentic-trader-research/bracket-counterfactual-v1-20261018/` (`protocol.json`,
  `manifest.json`, `result.json`, `frames/{primary,robustness}.csv.gz`, the run log). No
  `confirmation.json` and no confirmation frame exist, as the rules require.

## Rules

B1 is the paired advantage in excess R (`r_cost − SPY R over the arm's own window`) over `A0_live`,
Holm-adjusted over the four arms at one-sided α 0.05 and requiring a positive mean; B2 is the arm's
own mean `r_cost` with a 90% CI lower bound ≥ 0; B3 is the robustness-window sign of the paired
advantage; B4 the floors (3,000 setups, 600 sessions, 95% mature, 300 divergent pairs).

| Arm | B1 advantage | 90% CI | p | Holm p | B1 | B2 own mean `r_cost` | 90% CI | B2 | B3 (2017–21) | B3 | divergent | B4 |
| --- | ---: | --- | ---: | ---: | :---: | ---: | --- | :---: | ---: | :---: | ---: | :---: |
| `A1_3r` | +0.009 | [−0.022, +0.042] | 0.32 | 1.00 | ❌ | +0.078 | [−0.019, +0.175] | ❌ | +0.002 | ✅ | 2,800 | ✅ |
| `A2_2r_trail` | **−0.013** | **[−0.020, −0.006]** | 1.00 | 1.00 | ❌ | +0.030 | [−0.045, +0.108] | ❌ | +0.005 | ✅ | 467 | ✅ |
| `A3_3r_trail` | −0.011 | [−0.036, +0.015] | 0.77 | 1.00 | ❌ | +0.054 | [−0.031, +0.141] | ❌ | +0.001 | ✅ | 3,096 | ✅ |
| `A4_trail_only` | −0.038 | [−0.082, +0.008] | 0.91 | 1.00 | ❌ | +0.068 | [−0.024, +0.163] | ❌ | −0.013 | ❌ | 3,343 | ✅ |

No arm passes B1, so nothing was selected, B5 was never read and the decision is **`no_change`**
as pre-registered.

## Descriptives (never decisive)

**Primary window (2021-06 → 2026-01), per setup.**

| Arm | target | stop | timeout | hold (sessions) | mean R | mean `r_cost` | SPY R, own window | excess R |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `A0_live` | 34.9% | 60.8% | 4.3% | 5.6 | +0.116 | **+0.039** | +0.211 | **−0.110** |
| `A1_3r` | 24.6% | 66.4% | 9.0% | 7.0 | +0.154 | +0.078 | +0.222 | −0.101 |
| `A2_2r_trail` | 33.3% | 63.3% | 3.4% | 5.5 | +0.107 | +0.030 | +0.212 | −0.122 |
| `A3_3r_trail` | 19.8% | 75.0% | 5.2% | 6.3 | +0.131 | +0.054 | +0.220 | −0.121 |
| `A4_trail_only` | 0.0% | 91.7% | 8.4% | 7.1 | +0.145 | +0.068 | +0.283 | −0.147 |

**Robustness window (2017-01 → 2021-04).** `A0_live` +0.178R after cost (39% target, 55% stop),
SPY +0.514R over the same windows, excess −0.103R; the other arms' excess is −0.098 to −0.116R.
A rising tape doubled the arms' raw means and left their excess unchanged.

**By strategy (primary, mean `r_cost`).** Trend pullback 4h (n 7,457): A0 +0.031, A1 +0.064,
A2 +0.025, A3 +0.043, A4 +0.038. Squeeze breakout 1h (n 1,189): A0 +0.080, A1 +0.144, A2 +0.050,
A3 +0.102, A4 +0.237. Squeeze 4h (n 122): A0 +0.159, A1 +0.240, A4 +0.238. The 3R and trail-only
arms earn more raw R on squeezes by holding longer; none of it survives the index control.

**By year (paired excess advantage over A0).** The ratchet arms are negative in 2023, 2024 and
2025 (A4: −0.028, −0.086, −0.099); the 3R arm is within ±0.06 every year; 2026 (January only)
is large and positive for every arm on a handful of sessions and carries no weight in the test.

## What this says

1. **No exit policy rescues the native entries.** The paired contrasts are tight (the ratchet
   arm's 90% CI is ±0.007R wide) and none is positive. The October 8 grid's conclusion on 30 live
   candidates now holds on 8,768 replayed setups with the trailing-ratchet arm included.
2. **The live ratchet costs money.** A2 differs from A0 on only 467 of 8,768 setups (the ones that
   reached +1.5R and then pulled back), and on those it loses: moving the stop to breakeven-plus
   converts targets into small stops more often than it saves a round trip. Under the frozen
   rules this is a hypothesis, not a decision (B1 tested for an advantage, not a cost); a declared
   test of "ratchet off" as a construction change would be its own protocol and an operator
   decision, because the live desk runs the ratchet on every position today.
3. **Excess over SPY is −0.10 to −0.15R in every arm and both windows.** The native long setups
   earn less than holding the index over the same hours. Their raw positive means (+0.04R after
   cost in 2021–2026, +0.18R in 2017–2021) are market exposure in rising tapes, which is what the
   setup-outcome study's "upper bound" warning meant. That is an entry problem.
4. **B2 fails for every arm, including the live bracket's own level.** No arm's expected value is
   clearly positive after cost on this (survivorship-flattered) population.

Consequence for the desk: card construction stays as it is; `card_policy` remains the operator's
control on the native funnel; the next alpha work is on the entry side — the free point-in-time
event sources ranked in [the data-augmentation assessment](alpha-data-augmentation-2026-10-18.md)
(short-sale volume, insider filings, EDGAR earnings timestamps), each as a frozen fixed set on the
pooled lane.

## Erratum: beta-matched control (2026-10-23)

The excess columns above subtract SPY's return over each setup's own window, in the setup's R
units, at **beta one** (`research/setups/outcomes.py` `market_r`). The holistic review
(2026-10-21, item RM-R2) found that this overstates the gap. Two separate effects contribute:

- **Beta mismatch.** Low-beta setups (Treasury, credit and gold ETFs) were charged for index moves
  they never carried.
- **R-unit scale on the T-bill funds.** A 1.5× ATR stop on BIL or SHY is a few basis points wide,
  so SPY's move over the window is tens of R (BIL's median |`market_r`| is about 24R). Excess pins
  at the ±10 clip whatever the setup did. In 2017–2021 the 102 BIL/SHY setups (1.3% of rows)
  account for about half of the beta-one gap (−0.103R → −0.053R without them).

The tables recompute the excess on the same frozen frames and caches, clipped at the protocol's
`r_cap` (10) like the descriptives above. The control uses a **past-only** beta: the OLS slope of
the symbol's daily adjusted returns on SPY's over the 120 sessions before the decision
(`research/setups/beta.py`, ≥ 100 paired returns; every row has one). The median beta is 0.87 in
the primary window and 1.02 in the robustness window.

The column groups are:
- **all rows**;
- **ex BIL/SHY**, the native scan universe from 2026-10-23;
- **ex rates/metals**, which drops the research universe's whole `rates_credit` and `metals` cohorts
  and so is close to equities only.

Each cell gives the beta-one / beta-matched excess, as the mean per setup in R.

**Primary window (2021-06 → 2026-01).**

| Arm | all rows (n 8,768) | ex BIL/SHY (n 8,666) | ex rates/metals (n 8,223) |
| --- | ---: | ---: | ---: |
| `A0_live` | −0.110 / **−0.048** | −0.094 / **−0.050** | −0.081 / **−0.058** |
| `A1_3r` | −0.101 / −0.031 | −0.088 / −0.038 | −0.068 / −0.047 |
| `A2_2r_trail` | −0.122 / −0.054 | −0.103 / −0.056 | −0.086 / −0.064 |
| `A3_3r_trail` | −0.121 / −0.042 | −0.101 / −0.048 | −0.076 / −0.055 |
| `A4_trail_only` | −0.147 / −0.041 | −0.106 / −0.060 | −0.077 / −0.069 |

**Robustness window (2017-01 → 2021-04).**

| Arm | all rows (n 8,121) | ex BIL/SHY (n 8,033) | ex rates/metals (n 7,682) |
| --- | ---: | ---: | ---: |
| `A0_live` | −0.103 / **−0.014** | −0.053 / **−0.005** | −0.008 / **−0.010** |
| `A1_3r` | −0.101 / −0.008 | −0.053 / −0.000 | −0.001 / −0.006 |
| `A2_2r_trail` | −0.098 / −0.016 | −0.051 / −0.006 | −0.007 / −0.010 |
| `A3_3r_trail` | −0.102 / −0.013 | −0.054 / −0.004 | −0.003 / −0.010 |
| `A4_trail_only` | −0.116 / −0.016 | −0.066 / −0.009 | −0.009 / −0.017 |

There are no confidence intervals: this is descriptive and was not predeclared.

What changes:

- **"Every arm trails SPY by 0.10–0.15R" overstates the deficit.** For the setups the desk now
  scans (ex BIL/SHY), beta-matched, the native long setups trail the index as follows:
  - 2021–2026: about 0.04–0.06R per setup;
  - 2017–2021: 0.00–0.01R;
  - on the near-equity-only rows: 0.05–0.07R and 0.01–0.02R.
- **The all-rows beta-matched figure (−0.03 to −0.05R in 2021–2026) is the most flattering one.**
  The near-zero controls of the bond and gold rows pull it toward zero. Quote the equity figure for
  the entries that actually trade.
- **The conclusion does not reverse.** No arm shows a positive beta-matched excess in any column or
  window, so the native entries still add nothing over the index exposure they carry.
  - The frozen `no_change` (B1–B5 on beta-one excess) stays the decision of record.
  - `card_stats` v2 and any future gate use the beta-matched unit (operator decision D3,
    2026-10-23).
- **BIL and SHY are out of the static native scan universe (2026-10-23).** A 1.5× ATR stop on a
  T-bill fund implies hundreds of times notional per unit of risk. BIL's 30 primary setups averaged
  −20R of beta-one excess before clipping.

Computed 2026-10-23 with `scripts/yardstick_erratum.py` as committed alongside this erratum (feature/yardstick-erratum).
Reproduce with `uv run python scripts/yardstick_erratum.py ~/agentic-trader-research/bracket-counterfactual-v1-20261018
~/agentic-trader-research/setup-cache-v1 ~/agentic-trader-research/setup-cache-2017` (local caches only).

## Caveats

- Current-universe replay flatters longs; it cancels in the paired contrasts, not in B2.
- The ratchet is evaluated on hourly closes, the conservative side of the live one-minute poll;
  the live rule may do slightly better than A2 shows, but not by the width of its interval.
- The A2 target is exactly 2.0R where the live card's target is rounded up to the tick; a few
  sub-tick divergences are counted among the 467.
- `cards outcomes` now carries the same four arms per journaled card (`counterfactual` block),
  descriptively; the live sample (about 40 mature cards) cannot change this result.
