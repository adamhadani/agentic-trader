# Pooled alpha mining protocol v3: top-decile basket selection

**Status:** direction chosen by the operator on 2026-10-02 (top-decile selection); awaiting written-spec review.
**Extends:** [Part 1](2026-09-28-pooled-alpha-mining-design.md) and [Part 1b](2026-10-02-pooled-alpha-mining-part1b-design.md). Everything not changed here stands.
**Scope:** research only, one PR. Same cohort v2, same cube, same ledger, same checks, same campaign runner.

## Why

Part 1b's check B failed on campaign protocol v2: 1 of 10 planted edges was recovered
([result](../../alpha-pooled-checks-2026-10-02.md)). Each formula picks the top 3 of about
367 eligible names, skipping names it already holds. At that ratio two near-identical
formulas pick almost different names, so a planted edge does not reach a formula's
neighbours, and the search can only find it by proposing the exact formula.

Literature anomalies are defined on decile portfolios, not on the extreme few names. A
read-only probe of the v2 cube's discovery window (eligibility and price features only; no
outcome read) measured the pick overlap (Jaccard) between near-neighbour formulas:

| Pair | Top 3 (hold-skipping) | Top decile |
| --- | --- | --- |
| `close / ts_max(high, 252)` vs `… 126` | 0.50 | 0.76 |
| range location 20/20 vs 20/40 | 0.48 | 0.71 |
| `volume / ts_mean(volume, 50)` vs `… 100` | 0.63 | 0.72 |
| `-roc(close, 5)` vs `zscore(-roc(close, 5), 60)` | 0.19 | 0.48 |
| `-roc(close, 5)` vs `-roc(close, 10)` | 0.15 | 0.35 |
| `-roc(close, 21)` vs `zscore(-roc(close, 21), 5)` | 0.04 | 0.09 |

The decile shares most picks between genuine neighbours. A transform that changes what is
measured, as in the last row, stays apart, as it should. **Expectation:** check B should
now recover many near-seed plants, but a pass is not guaranteed. The outcome is
pre-registered below.

## The change

### Selection rule `top_fraction`

On session D, among names that are eligible, have a finite score and pass every filter
(n names):

1. Rank by score, descending. Break ties by the existing hash key (`tiebreak`).
2. Pick the first `m = ceil(fraction × n)` names, with `fraction = 0.10`.

There is **no hold-skipping**: the picks are a daily decile basket, and a name may be
picked on consecutive sessions. Its labels overlap, exactly as the control's already do.
The paired edge, the leg mean, the session-block bootstrap (block mean 20), purging, the
stage gates and every threshold are unchanged. Gates on session counts (≥ 400, 150, 300)
are met by construction.

Part 1's rule `top_k` (k = 3, hold-skipping) stays for campaign protocols v1 and v2 and for
the literature entries. Their pinned outputs are unchanged.

### Campaign protocol v3

`config/research/pooled/campaign-v3.json` equals `campaign-v2.json` except for:

- `version: 3` and a new title;
- `selection: {"rule": "top_fraction", "fraction": 0.10}`, which replaces `k`.

`k` becomes optional in the model. A protocol has either `k` (meaning `top_k` with
hold-skipping) or a `selection`, never both. It still pins `cohort-v2.json`; the cube
identity does not depend on selection, so the **v2 cube is reused from cache**. No bars are
downloaded. The confirmation window (2024-01-02 → 2026-07-31) is still unused, because v2
never ran a campaign.

### Where selection is applied

One function, `select(scores, allowed, view, protocol)`, chooses `top_k` or `top_fraction`
from the protocol. It is used everywhere picks are made:

- discovery, selection and confirmation;
- check A's planted formula;
- check B's hidden expression and its plant;
- the literature-overlap rule;
- check C (through the stages).

### Literature-overlap rule under v3

The rule's intent is: a finalist that is a literature formula or a close relative was
already tested.

- Each literature entry's formula (score plus its filters) is evaluated **under the
  campaign's selection rule**, so the overlap compares like with like.
- The Jaccard threshold is unchanged (0.5).
- The entries' own verdicts (both failed, on top 3) are unchanged.

### Frozen documents and results

- Frozen formula documents record the selection rule with the score expression.
- Confirmation rows also report, **descriptively and not as a gate**, each candidate's top-3
  hold-skipping edge on the confirmation window. That is closer to what a live card would
  trade.

### Pick cells, stored compactly

A decile basket has about 33 to 41 picks per session, so each formula has about 50,000
discovery cells. Cells are stored as sorted `int64` codes (`session × names + symbol`).
Jaccard is computed by sorted intersection, not on Python sets of tuples. This keeps 200
formulas, and 40 check C replicates across workers, within memory.

## Gates (unchanged definitions, rerun at the final revision on protocol v3)

| Check | Pass rule |
| --- | --- |
| A | Detection ≥ 80% at 0.15R, false acceptance ≤ 5% at 0 |
| B | ≥ 8 of 10 recovered at Jaccard ≥ 0.5, δ 0.15R, 17 formulas per family. Seeds and families as frozen in v2. |
| C | ≤ 2 of 40 false acceptances. The seed-t standard deviation is reported (v2: 1.12). Decile baskets carry persistent factor exposures, so this diagnostic matters more now. |

All three run from the cached v2 cube, in roughly one to one and a half hours, with the
thermal watch armed.

**If B fails again,** the pooled genetic campaign is **parked**. The machinery stays merged
and the lane's effort moves to the card funnel and the PEAD probe. There is no further
re-design of selection or of B's rule in this workstream. **If A, B and C pass,** the
campaign runs only after explicit operator approval, as before.

## Caveats (additions)

- **The tested claim changes.** A confirmation now means the top-decile basket beat the
  eligible cohort. Live cards (Part 2) would trade the top names of that decile, which
  extrapolates beyond the tested claim. The descriptive top-3 edge in the confirmation rows
  shows how far the extrapolation reaches, and the paper probe is the forward test.
- **Factor exposure.** A decile basket can load steadily on style factors (momentum, size,
  volatility). The paired control removes the market, not style factors. Check C measures
  the size consequence, and the survivorship caveat for cohort v2 is unchanged.
- **Serial dependence.** Persistent baskets carry dependence beyond the 20-session block.
  Check C's seed-t standard deviation is the measure.

## Testing

- **`top_fraction`:**
  - m = ceil(0.10 × n);
  - eligible, finite and filtered names only;
  - the hash tie-break;
  - no hold-skipping;
  - a session with no candidates gives no picks.
- **Protocol model:**
  - exactly one of `k` and `selection`;
  - v1 and v2 still load with their hashes;
  - the v3 file pinned in a test.
- **Pins:** the v1 `run_stages` pin and the check A determinism tests are unchanged.
- **v3 stages on the mini world:**
  - a planted decile edge is confirmed;
  - the dedupe works on cell codes;
  - check B's recovery uses decile cells;
  - the literature overlap is evaluated under v3.
- **Memory:** cell codes round-trip, and Jaccard on codes equals Jaccard on sets.

## Documentation (same PR)

`docs/alpha-pooled-mining.md` (selection rules, protocol v3), a v3 results doc, the roadmap and CLAUDE.md.
