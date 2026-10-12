# Spread-reversion lane (pairs v2): checks A and C, and the study (2026-10-11)

Protocol `config/research/spread/pairs-v2.json` (SHA-256 `17d21cd1…b49ba`, `second_look: true`) on
cohort `cohort-v1.json` (SHA-256 `6f3a4a32…dbea`, unchanged from v1), run from the installed checkout
at the clean merged revision `f98d7a3` (PR #114) on Sunday 2026-10-11 at 04:22 New York, outside
every scan window; the whole chain (A, C, study) took two minutes. Contract and definitions:
[spread lane](alpha-spread-lane.md); the v1 run: [results 2026-10-10](alpha-spread-results-2026-10-10.md).
Everything below carries `authorizes_promotion: false`.

**What v2 changed.** One trading-rule change, declared before the run: a pair enters only when
abs(z) crosses 2.0 from inside (the previous close had abs(z) < 2.0), only while abs(z) < 4.0 at the
signal, and a stop retires the pair for the rest of its window. Everything else (cohort, windows,
formation, levels, fills, cost, rules, bootstrap, one-use confirmation key) is byte-identical to v1.

**Second look, declared.** v2 is the second protocol evaluated on the same discovery window. The
crossing and retirement facets came from construction statistics (84% of v1 entries were already
beyond the stop; stopped pairs re-entered next session); the upper-band facet was motivated after
the v1 results document's descriptive read of in-band returns, so for that facet discovery returns
were seen before v2 was frozen. The protocol file says the same in `second_look_note`. A v2 pass
would therefore have been weaker evidence than a v1 pass; it did not pass, so no discounting
question arises.

## Check A — statistical power (passed, 10 of 10 seeds)

The same synthetic factor worlds as v1 (30 planted Ornstein–Uhlenbeck pairs, half-life 10 sessions,
innovation 0.008, weekday calendar, 12 tiles). Every seed completed and held all four rules. Planted
recall 0.53–0.62; lane mean 1.3–1.8 bp per day at the 5 bp decision cost; 380–433 closed trades per
seed (v1: 609–901, because the crossing rule enters once per excursion). The v2 rule still detects
what the lane is designed to detect.

## Check C — null on shifted real bars (passed, 0 of 10 seeds)

The same per-symbol circular shifts as v1 (multiples of 63 sessions, distinct within a sector).
Every seed completed; 219–251 closed trades per seed; lane means −0.82 to −0.00 bp per day; no seed
held all four rules. The v2 rule does not pass on alignment-destroyed data.

## The study — discovery 2018-01-02 → 2023-12-29 (failed_discovery)

Formation is identical to v1 by construction (108–114 eligible names, 676–740 same-sector pairs
tested, 27–74 passing, top-20 cap bound in all eleven tiles; median half-life 11.9 sessions, median
|β| 0.74). Trading closed 234 trades over 1,386 lane sessions (v1: 2,309).

| Rule | Value | Holds |
| --- | --- | --- |
| S1 lane mean (5 bp) and bootstrap CI90 | +0.02 bp/day, CI90 [−0.75, +0.67], one-sided p 0.47 | no |
| S2 trade mean / 1%-trimmed mean (net) | +2.7 bp / +0.1 bp | yes |
| S3 counts | 234 trades (≥ 100), 11 of 11 windows traded (≥ 8), 6 of 11 (55%) positive windows (< 60%) | no |
| S4 SPY beta | 0.024 (HAC(5) se 0.010) | yes |

Descriptives: hit rate 54%, annualised lane Sharpe +0.02 at 5 bp (+0.11 at 0 bp, −0.06 at 10 bp),
mean holding 29.9 sessions, median 23.5 (v1: 4.9 and 1). Exits: 93 reversions (mean gross
+596 bp), 92 stops (mean gross −606 bp), 49 window-end closes (+66 bp). Entry abs(z) ran from 2.03
(first decile) to 2.53 (ninth); no entry at or beyond 4 (v1: 84%). Per-window mean net return per
trade ranged from −295 bp (window 4, trading H1 2020) to +263 bp (window 5); six of eleven windows
were positive. SPY beta is indistinguishable from zero: the lane as traded was market-neutral.

Decision as pre-registered: **failed_discovery**. The confirmation window (2024-01-02 →
2026-07-31) was not journaled, read or consumed; `confirmation_record` is null. Nothing promotes.

## What this says

- The v1 construction defect is gone. Entries sit just outside the band, pairs are held for weeks,
  and the stop and the reversion are now nearly symmetric outcomes (about ±6% gross each). That
  symmetry is the result: with a 54% reversion rate against a 46% stop rate at equal magnitude,
  the net edge per trade is a few basis points and the lane mean is +0.02 bp per day, zero within
  ±0.7 bp. A frozen Engle-Granger spread on this cohort, at this cost, does not revert often
  enough to pay for the times it breaks.
- The one facet motivated by a returns read (the upper band) did what the read suggested, and it
  was not enough. Had v2 passed, that facet would have been the reason to distrust the pass; since
  it failed, the second look cost nothing but the window's second use.
- The machinery again worked end to end: both gates bound to the same protocol hash, cohort and
  clean revision; every bar came from the cache without failure; the one-use window stayed untouched.
- Two protocols have now been evaluated on this discovery window. The lane document already rules
  out a third without a new specification and operator approval, and nothing in these results
  motivates one: the churn hypothesis (v1) and the clean-entry hypothesis (v2) bracket the design
  space of a two-leg z-score trader on daily bars. Baskets (Johansen or PCA residuals) are the same
  label on more legs; they stay unbuilt.

## Decision

The spread-reversion lane is **closed as an alpha source** on this cohort and discovery window.
`/pairs` remains display-only; no probe or two-leg execution contract exists. The next desk item
(the event leg, with corporate-actions ingestion first) proceeds as planned.

Artifacts (private, outside Git): `~/agentic-trader-research/spread-power-v2-20261011`,
`spread-null-v2-20261011`, `spread-study-v2-20261011` (`result.json`, `manifest.json`,
`protocol.json`, `discovery-trades.csv.gz`, `discovery-lane.csv.gz`).
