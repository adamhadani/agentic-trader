# Pooled mining Part 1b: cohort v2 and checks A, B and C (2026-10-02)

**Result:** checks A and C passed and check B (search power) **failed** (1 of 10 recovered;
8 required). By the pre-registered rule, the campaign does not run and the lane goes back
to redesign. The pooled ledger is untouched, and the 2024-01-02 → 2026-07-31 confirmation
window is still unused.

All three gates ran at revision `65bf31d` (clean), on campaign protocol v2
(`config/research/pooled/campaign-v2.json`, SHA-256 `6470ca97…`), cohort v2
(`cohort-v2.json`, SHA-256 `c2c79410…`) and one cube (SHA-256 `9bedbf9b4b8e7541…`).
Research only: no registry, broker, Telegram or promotion credit.

## Cohort v2

The liquidity screen (`config/research/pooled/screen-v2.json`) ran on 2026-10-02 against
the prospective snapshot of 2026-09-17:

- **Candidates:** 6,721 listed non-ETF names. Of these, 5,315 were kept and 1,406 excluded:
  - 751 by NASDAQ fifth letter;
  - 274 funds;
  - 153 `%` (fixed income or preferred);
  - 138 by symbol form;
  - 82 preferreds;
  - 8 warrants, units or debentures.
- **Ranked:** 2,744 names had bars on at least 55 of the 60 sessions ending 2023-12-29 and
  closed at $10 or more that day. Of the rest, 1,654 closed below $10, 892 had no bars in
  the window, and 25 had too few sessions.
- **Selected:** the top 400 by median dollar volume, with a cutoff of about $145M a day.
  Together with the 127 scan-universe equities, that gives **452 names**. Among the 400 are
  ADRs, one MLP (ET) and one dual-class pair (GOOG and GOOGL, kept by ruling).

**Breadth:**

| | v1 (2026-09-30) | v2 |
| --- | --- | --- |
| Eligible names per session (mean) | ~85 | **366.8** (min 265, max 414) |
| Names ever eligible | 130 of 353 | 437 of 452 |
| Eligible cells | 214,308 | 922,013 (921,900 labelled) |

The mean number of eligible names per session rises from 277 in 2016 to about 405 from
2023 on.

## Check A — statistical power (passed)

| Planted δ (R) | 0 | 0.05 | 0.08 | 0.10 | 0.12 | 0.15 |
| --- | --- | --- | --- | --- | --- | --- |
| Detection | 0.00 | 0.12 | 0.56 | 0.82 | 0.91 | **0.96** |
| False acceptance | **0.00** | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |

Acceptance requires detection of at least 0.80 at 0.15R and false acceptance of at most
0.05 at 0, and both hold. At 0.15R, the planted formula was confirmed in 96 replicates,
failed selection in 3, and failed confirmation in 1. Detection at small δ is a little
lower than on v1 (0.92 at 0.10, 0.68 at 0.08). The control is now the mean over about 367
names rather than 85, and the planted picks are a smaller share of each session.

## Check C — real-formula false acceptance (passed)

| Outcome | Replicates |
| --- | --- |
| Total | 40 |
| Full 200-formula search (no evaluation errors) | 40 |
| Ended `no_finalists` | 25 |
| Ended `no_confirmation_candidates` | 10 |
| Ended `none_confirmed` | 5 |
| **False acceptances** | **0** (one-sided 95% Clopper-Pearson upper bound 7.2%; gate ≤ 2 of 40) |

The discovery t of the 23 family seeds, pooled over all replicates (n = 920), has mean
0.07 and standard deviation **1.12**. For persistent-exposure formulas, the session-block
bootstrap therefore understates the variance by about 12%. That is milder than the 1.16–1.26
in the Part 1a reviewer's simulation, and the stage gates absorb it: no null campaign was
confirmed.

## Check B — search power (failed)

Each seed planted +0.15R on the discovery picks of a near-seed hidden expression. It then
ran that family's real search, with the campaign seed and budget (17 formulas), and asked
for a gate-passing formula whose picks overlap the hidden picks at a Jaccard of at least
0.5.

| Seed | Family | Hidden expression | Best Jaccard (any) | Best Jaccard (passing) |
| --- | --- | --- | --- | --- |
| 0 | reversal | `zscore(-1.0 * roc(close, 5), 60)` | 0.19 | 0.00 |
| 1 | range_location | range location with mixed 60/120 windows | 0.04 | 0.00 |
| 2 | abnormal_volume | `volume / ts_mean(volume, 100)` | 0.75 | 0.00 |
| 3 | reversal | `-1.0 * roc(close, 10)` | **1.00** | **1.00** (recovered) |
| 4 | range_location | range location with 20/40 windows | 0.07 | 0.00 |
| 5 | abnormal_volume | `ts_rank(volume / ts_mean(volume, 50), 50)` | 0.33 | 0.00 |
| 6 | reversal | `zscore(-1.0 * roc(close, 21), 5)` | 0.06 | 0.00 |
| 7 | range_location | `delay(delay(range location 60/60))` | 0.01 | 0.00 |
| 8 | abnormal_volume | `ts_mean(volume / ts_mean(volume, 20), 3)` | 0.28 | 0.00 |
| 9 | reversal | `ts_rank(-1.0 * roc(close, 21), 5)` | 0.02 | 0.00 |

**Recovered: 1 of 10.** There were no evaluation errors. Each search spent its full budget,
and 1 to 4 proposals per family were rejected as not dimensionless.

### Why it failed

The pick set is the **top 3 of about 367 names**, so it is extremely sensitive to the
exact formula. Near neighbours of the planted formula pick almost different names:

- `zscore(·, 60)` around the seed `-roc(close, 5)` shares 19% of the seed's picks;
- `zscore(-roc(close, 21), 5)` shares 6% with `-roc(close, 21)`.

A planted edge is recovered only when the search proposes the hidden formula almost
exactly, as in seed 3. The edge does not spread to neighbouring formulas, so the search has
no gradient to climb, and 17 formulas per family cannot find it by chance. Even 75%
overlap (seed 2) did not pass the gate: the planted 0.15R reaches the neighbour only
diluted, on top of that formula's own real edge.

This is a property of the selection rule at v2's breadth (k = 3 of hundreds), not a code
fault. Check A planted the exact formula, so it could not see the problem. With v1's ~85
names, top-3 sets overlapped far more.

## What happens next

The campaign does **not** run on protocol v2. Nothing was charged or consumed in the
journal. Redesign candidates for a protocol v3, each to be checked by B before any
campaign:

1. **Rank-quantile selection instead of top-3.** Pick the top decile (or a fixed fraction)
   of eligible names and test the paired edge of that basket. Neighbouring formulas then
   share most of their picks, so the search can climb. Literature anomalies are defined
   on deciles, not on the extreme three names. Live cards (Part 2) would still take only
   the top names of a confirmed formula.
2. **Overlap measured on scores rather than exact picks:** for example, the rank
   correlation of discovery scores with the hidden formula, with recovery defined on that
   measure. This makes B fairer, but on its own it does not make the search better at
   finding an edge.
3. **Fewer, deeper families.** A larger per-family budget helps only marginally while the
   fitness landscape stays flat.

Option 1 changes the tested statistic, so it needs a new protocol version, re-checks A, B
and C, and a spec amendment.
