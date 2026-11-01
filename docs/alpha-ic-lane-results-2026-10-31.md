# IC lane results: `composite-ic-v1` fails its development gate (2026-10-31)

Research only. Nothing here promotes, journals shadow credit, or touches the registry, broker, database or Telegram. Contract: [IC lane](alpha-ic-lane.md). Design: [spec](superpowers/specs/2026-10-31-ic-lane-design.md). Ledger: [L40–L42](research-window-ledger.md).

## Result

**`composite-ic-v1` fails, so it is closed and the era is never read for it.** Mean rank IC against 20-session residual returns is +0.0116 with `t` 1.53, below the required 2, on cohort v3 from 2016-08-01 to 2023-12-29.

- **The fail is well powered.** At this read's standard error (0.0076), a true IC of 0.03 would pass 97.5% of the time, so a fail rules out IC 0.03. It does not rule out IC 0.02, which would pass 74% of the time.
- **The point estimate would lose money as a card anyway.** By the report's arithmetic, IC 0.0116 is about 0.18% gross per pick against a round trip of about 30 bp, or about −0.014R per card. This is arithmetic, not a measured `net_excess_r_v1`.
- **The 64 tested pairs say nothing new when read by IC.** All eight campaigns reproduced exactly. No pair reaches `t ≥ 2` in both windows, and 2 of 8 batches carry, which is what chance alone predicts.
- **Nothing here produces a card, and the IC tools stay.** This weakens the case for Sharadar's price lane. The event lane is unaffected.

These reads used a development window that earlier lanes had read, and cohorts of current members. That is why a fail closes the composite but a pass could only have left it as a hypothesis.

## What ran

All at clean revision `d1adac3`, before any result was seen:

| Read | Ledger | Output under `~/agentic-trader-research/` | Status |
|---|---|---|---|
| Alignment spot check on real cells | L40 | a scratch script, computing no IC | Passed |
| Checks A-IC and C-IC | L40 | `ic-checks-20261031` | Passed |
| 64-pair re-score | L41 | `ic-rescore-20261031` | Completed |
| Gate | L42 | `ic-gate-20261031` | Fail |

Frozen digests: composite `63269ca1…`, re-score `961f60f9…`, sectors `56bc70fb…`, FF12 `4fac501b…`. Cohort v3 is 99.7% mapped to a sector and cohort v2 is 99.8%.

## Checks

**Alignment spot check.** 200 seeded cells compared with raw bars by date, with no mismatch: 187 at h=1, 184 at h=20, 196 SPY and 187 hand-computed `beta_60`, worst return difference 0. The rest were NaN for a stated reason, either an exit after the window or a missing bar.

**C-IC, the negative control: passed.**

| Arm | Replicates | Accepted | Mean IC | `t` sd |
|---|---|---|---|---|
| Fixed within-sector permutation (decisive) | 200 | 3.0% (limit 10%) | −0.0005 | 1.13 |
| Permuted within session and sector | 100 | 0.0% | −0.0007 | 0.85 |

The decisive arm's `t` sd of 1.13 sits beside the 1.18 simulated for this overlap. It validates h=20 only, on the composite only. It breaks name identity by design, so it cannot see the volatility bias in the [contract](alpha-ic-lane.md#the-low-risk-leg-and-the-volatility-confound).

**A-IC, the planted effect: passed, as a plumbing check.** Power was 100% at planted IC 0.02, 0.03 and 0.04, on both bases. This figure is overstated by construction, because permuting names removes the variance factor returns put into a real composite. The power statement comes from the gate's own read, above.

## The gate

| | Cohort v3 (decisive) | Cohort v2 (reported) |
|---|---|---|
| Mean IC, `e_20` | **+0.0116** | +0.0148 |
| Bootstrap se | 0.0076 | 0.0099 |
| `t` (needs ≥ 2) | **1.53** | 1.50 |
| Newey-West `t` | 1.50 | 1.47 |
| Positive blocks (needs 3 of 4) | 3 | 4 |
| Sessions evaluated / unavailable | 1,802 / 65 | 1,802 / 65 |
| Median names per session | 806 | 347 |
| `min_detectable_ic` | 0.0151 | 0.0198 |
| Power at IC 0.02 / 0.03 | 74% / 97.5% | 51% / 85% |
| Top minus bottom decile, h=20 residual | +0.159% | −0.122% |

Block means on cohort v3 are +0.0054, +0.0374, −0.0047 and +0.0081, so one block supplies most of the mean. By year, 2019 reads +0.0448 and 2020 reads −0.0188.

**Declared reading: none.** It applies only to a pass.

**Cohort v2 is not a replication.** The two cohorts are pushed apart mechanically by about 0.01 of rank IC, and its top-minus-bottom has the opposite sign.

### Decomposition (reported, never deciding)

Cohort v3, `e_20`:

| Part | Rank IC | `t` | Pearson IC |
|---|---|---|---|
| Momentum | +0.0035 | 0.35 | +0.0172 |
| Short-term reversal | +0.0149 | 1.97 | +0.0118 |
| Low risk | +0.0008 | 0.06 | −0.0218 |
| Composite excluding low risk | +0.0126 | 1.92 | +0.0198 |
| Composite | +0.0116 | 1.53 | +0.0048 |
| Composite lagged 252 sessions | +0.0040 | 0.46 | — |

- **Excluding low risk does not rescue it.** At `t` 1.92 that composite is also below 2. Choosing a leg after seeing this table is a forking path, so neither the reversal leg nor this variant is a new candidate. A re-run after this read would be a declared second look.
- **Low risk has rank IC near zero and a negative Pearson IC,** which is the shape the synthetic worlds predicted for a survivor band: the two biases roughly cancel in rank, and the arithmetic stays negative.
- **The lagged composite is small,** so the mechanical part looks small on this cohort.

### Horizons (reported)

Compare these by means, not `t`: the [calibration table](alpha-ic-lane.md#6-the-ic-statistic) shows `t` is not comparable across horizons.

| Horizon | Mean IC, residual | Top minus bottom |
|---|---|---|
| 1 | +0.0070 | +0.017% |
| 5 | +0.0110 | +0.053% |
| 20 | +0.0116 | +0.159% |
| 60 | +0.0106 | −0.184% |

The tear sheet shows `t` 2.64 at h=1. That is not strength: it is the horizon where the bootstrap is most exposed to a slow drift in the IC mean, and +0.017% is far below any round trip.

## The 64-pair re-score

**Rehearsal (R2): all eight sets reproduced.** Every pair's discovery `edge_mean` matched its campaign's `formulas.jsonl` within 1e-9, and `n_sessions` matched exactly. No code fix was needed.

**Batch-mean rule:**

| Batch | Cohort | Discovery mean IC | Selection mean IC | Carries |
|---|---|---|---|---|
| `campaign-v4` | v2 | −0.0027 | +0.0056 | No |
| `intraday-v1` (one session late) | v2 | −0.0035 | −0.0030 | No |
| `events-v1` | v2 | −0.0030 | +0.0032 | No |
| `short-v1` | v2 | +0.0018 | +0.0061 | **Yes** |
| `cohort3-daily-v1` | v3 | −0.0040 | +0.0041 | No |
| `cohort3-intraday-v1` (one session late) | v3 | −0.0018 | −0.0018 | No |
| `cohort3-events-v1` | v3 | +0.0068 | +0.0041 | **Yes** |
| `cohort3-short-v1` | v3 | +0.0044 | −0.0008 | No |

No formula fell below the 250-session floor. Short batches read about 841 discovery sessions against 1,366 for the others, because the FINRA files start in 2018-08.

**Two batches carrying is the chance rate.** Under a global null about a quarter carry, about 2 of 8. The carrying batches do not repeat across cohorts: the short set carries on v2 and not v3, and the events set on v3 and not v2. Carrying was only ever eligibility for a later spec testing incremental IC over the composite, and the composite is now closed, so nothing goes forward.

**The `t` statistics against their placebos:**

| | Mean `t` | `t` sd | `t ≥ 2` | `t ≤ −2` |
|---|---|---|---|---|
| Discovery, real | +0.05 | 1.44 | 5 | 8 |
| Discovery, placebo | −0.08 | 1.04 | 1 | 2 |
| Selection, real | +0.14 | 1.46 | 6 | 4 |
| Selection, placebo | +0.07 | 1.08 | 3 | 1 |

- **The spread is wider than the placebo, in both directions, and centred on zero.** This is what real factor tilts look like, and not evidence of edge. The placebo's sd near 1.05 also shows the old "sd 1.25" was not a null.
- **Nothing persists.** No pair reaches `t ≥ 2` in both windows, and discovery and selection `t` correlate at only +0.15 across the 64 pairs.
- **IC broadly agrees with the old edge.** Discovery mean IC correlates +0.49 with the campaigns' recorded edge, so reading by IC does not reopen the closed lanes.

**The known names:**

| Family | Batch | Discovery `t` | Selection `t` | Reading |
|---|---|---|---|---|
| `shorts_retreat` | `short-v1` | +0.83 | +2.64 | Its batch carries by chance rate |
| `shorts_retreat` | `cohort3-short-v1` | +0.98 | **−2.65** | The earlier +3.40 edge does not survive; the sign flips between cohorts |
| `quiet_momentum` (as written) | `events-v1` | −2.30 | −0.71 | Fails as written |
| `quiet_momentum` (as written) | `cohort3-events-v1` | −2.14 | −0.48 | Fails as written |

A flipped `quiet_momentum` would be a hypothesis only.

## What follows

- **`composite-ic-v1` is closed.** The era is never read for it, and a re-run would be a declared second look.
- **The tools stay:** the arrays, the statistic with its checks and placebo, the sector map, and `ts_beta` and `ts_resid_std`. With the composite closed, there is no frozen yardstick to judge a new source's incremental IC against, so a later source would be judged on its own IC against a placebo, or against a new composite under its own spec.
- **Sharadar.** The price lane's case is weaker: no composite awaits the era. The event lane (`insider-oos-v1`, `pead-sue-v3`) rests on different evidence and is unaffected.
- **No effect on cards.** `card_policy` still enforces at 0.0 and the stream stays sparse. The next scheduled reads are the PEAD first look on 2027-02-26 and the insider read in 2027-08.

## Limits

- **Survivors and a read window.** Both cohorts are current members, and W1 had been read before. A fail on them closes the composite under the report's rule. It does not prove the factors have no value in other universes or periods.
- **Current SIC,** not point-in-time. A wrong sector costs noise, not sign.
- **Price themes only.** The fundamental themes need SF1 and were never tested, so this says nothing about a composite that includes them.
- **Selection was new for 63 of the 64 pairs,** so no unread selection window is left for them.
- **Synthetic figures** in the contract bound the direction and rough size of the volatility bias, not its real size.
