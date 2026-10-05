# Pooled mining protocol v3: checks A, B and C (2026-10-05)

**Result:** checks A and C passed and check B (search power) **failed again**: 3 of 10
recovered, 8 required. By the rule pre-registered in the
[v3 spec](superpowers/specs/2026-10-03-pooled-alpha-mining-v3-design.md), the pooled
genetic campaign is **parked**. No campaign ran, the pooled ledger is untouched, and the
2024-01-02 → 2026-07-31 confirmation window is still unused.

All three gates ran at revision `0b627ea` (clean), on campaign protocol v3
(`config/research/pooled/campaign-v3.json`, SHA-256 `d713b575…`), cohort v2
(`cohort-v2.json`, SHA-256 `c2c79410…`) and the v2 cube (SHA-256 `9bedbf9b4b8e7541…`),
reused from cache with no bar downloads. Research only: no registry, broker, Telegram or
promotion credit.

Protocol v3 changes selection only. Each formula picks the top decile of eligible names per
session (`ceil(0.10 × n)`, about 27 to 41 names, ties by hash, no hold-skipping) instead of
the top 3 with hold-skipping. Everything else, including B's frozen seeds, hidden
expressions and pass rule, is as on 2026-10-02 ([v2 checks](alpha-pooled-checks-2026-10-02.md)).

## Check A — statistical power (passed)

| Planted δ (R) | 0 | 0.05 | 0.08 | 0.10 | 0.12 | 0.15 |
| --- | --- | --- | --- | --- | --- | --- |
| Detection, v3 | 0.00 | 0.40 | 0.84 | 0.95 | 0.98 | **0.99** |
| Detection, v2 | 0.00 | 0.12 | 0.56 | 0.82 | 0.91 | 0.96 |
| False acceptance, v3 | **0.00** | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |

Acceptance requires detection of at least 0.80 at 0.15R and false acceptance of at most
0.05 at 0; both hold. Detection is higher than on v2 although the plant is effectively
smaller: the control mean includes the planted cells, so the paired edge is δ(1 − m/n),
about 0.135R per 0.15R for a decile against about 0.149R for top 3. A basket of about 37
names averages away most of the per-name noise that 3 names carry.

## Check C — real-formula false acceptance (passed)

| Outcome | Replicates |
| --- | --- |
| Total | 40 (no evaluation errors) |
| Discovery survivors carried to selection | 11 |
| Reached confirmation | 4 |
| **False acceptances** | **0** (one-sided 95% Clopper-Pearson upper bound 7.2%; gate ≤ 2 of 40) |

The discovery t of the 23 family seeds, pooled over all replicates (n = 920), has mean
0.04 and standard deviation **1.24** (v2: 1.12). Decile baskets carry steadier style
exposures, so the session-block bootstrap understates their standard error by about a
quarter; the stage gates still confirmed no null campaign. C's null is built from blocks
of mean length 20, so it cannot show dependence longer than that.

## Check B — search power (failed)

As on v2, each seed planted +0.15R on the discovery picks of a near-seed hidden expression,
ran that family's real search with the campaign seed and budget (17 formulas), and asked
for a gate-passing formula whose picks overlap the hidden picks at a Jaccard of at least
0.5. The hidden expressions are the same as on v2.

| Seed | Family | Hidden expression | Best Jaccard v2 (any) | Best Jaccard v3 (any) | Best Jaccard v3 (passing) |
| --- | --- | --- | --- | --- | --- |
| 0 | reversal | `zscore(-1.0 * roc(close, 5), 60)` | 0.19 | 0.48 | 0.00 |
| 1 | range_location | range location, low 60 / high 120 / low 20 | 0.04 | 0.25 | 0.00 |
| 2 | abnormal_volume | `volume / ts_mean(volume, 100)` | 0.75 | 0.85 | **0.85** (recovered) |
| 3 | reversal | `-1.0 * roc(close, 10)` | 1.00 | 1.00 | **1.00** (recovered) |
| 4 | range_location | range location, low 20 / high 40 / low 120 | 0.07 | 0.24 | 0.00 |
| 5 | abnormal_volume | `ts_rank(volume / ts_mean(volume, 50), 50)` | 0.33 | 0.58 | **0.58** (recovered) |
| 6 | reversal | `zscore(-1.0 * roc(close, 21), 5)` | 0.06 | 0.14 | 0.00 |
| 7 | range_location | range location 60/60 delayed 3 then 20 | 0.01 | 0.11 | 0.00 |
| 8 | abnormal_volume | `ts_mean(volume / ts_mean(volume, 20), 3)` | 0.28 | 0.37 | 0.37 |
| 9 | reversal | `ts_rank(-1.0 * roc(close, 21), 5)` | 0.02 | 0.13 | 0.00 |

**Recovered: 3 of 10.** There were no evaluation errors; every search spent its full budget,
and 1 to 4 proposals per family were rejected as not dimensionless.

**Unplanted companion.** None of the ten hidden expressions passes the discovery gate on the
unplanted window, and none of the three recovering formulas does either (discovery t −2.88,
−0.53 and −2.58). The three recoveries therefore come from the plant, not from a style tilt
that would pass anyway (`recovered_without_plant` = 0).

### Why it failed

Decile selection did what it was designed to do: every seed's best overlap rose, and
recoveries went from 1 to 3. The seven misses are hidden expressions whose baskets differ
from anything the 17-formula search proposed:

- **Range location with mismatched windows** (seeds 1 and 4) and a 20-session delayed copy
  (seed 7): the closest proposals share 11–25% of the basket.
- **Reversal transforms over short windows** (seeds 6 and 9: a 5-session z-score or rank of
  the 21-session return): 13–14%. These change what is measured, so their baskets stay
  apart from the seed's, as the spec's probe predicted.
- **Near misses:** seed 0's closest proposal reached 0.48 but did not pass the discovery
  gate; seed 8 had a passing formula at 0.37.

One or two mutations from a seed can land far from it in basket space, and 17 formulas per
family do not cover that neighbourhood. Selection was the first bottleneck; the search's
reach is the next one.

## Decision

As pre-registered, the pooled genetic campaign is **parked**:

- The machinery stays merged: protocol v3, the selection rules, checks A, B and C, the
  journal ledger and the campaign runner. Protocols v1–v3 keep their hashes.
- Nothing was charged and nothing was consumed. The confirmation window is still unused.
- This workstream makes no further redesign of selection or of check B. Lane effort moves
  to the suggestion-card funnel and the PEAD paper probe.

Reopening the lane would need a new spec and operator approval. Two directions remain open,
neither planned: a larger search budget per family, re-checked by B; or a small fixed set
of literature-defined decile formulas tested without search, which needs no check B.

**Runtime.** Check A took about 2 h 10 min on 6 workers (v3 scores each formula about 4×
more slowly than v2, over about 50,000 picks). Checks B and C ran in parallel in about
25 minutes. No thermal or performance warnings were recorded.
