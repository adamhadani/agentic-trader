# A priori entry `exdiv` v1: pre-ex-dividend run-up study (2026-10-12)

Entry `config/research/apriori/exdiv-v1.json` (SHA-256 `61afecdf…5edd`) on cohort
`config/research/spread/cohort-v1.json` (SHA-256 `6f3a4a32…dbea`, 127 names, verified before any
provider call), run on Monday 2026-10-12 at 03:49 New York from the clean branch revision `8be4e6e`
(the merged code is identical), outside every scan window, with a fresh bar cache. Contract and
definitions: [catalog, Entry 2](apriori-alphas.md#entry-2-pre-ex-dividend-run-up),
[spec](superpowers/specs/2026-10-12-exdiv-leg-design.md). Everything below carries
`authorizes_promotion: false`.

## Hypothesis (frozen before the run)

LONG only, decisions 2016-08-01 → 2026-07-31: for a non-special cash dividend whose previous
non-special ex-date of the same name lies 77–105 calendar days earlier, with no other corporate
action of the name within ten days of the span and no other ex-date inside it, enter at 10:35 New
York five sessions before the ex-date (2×ATR14 stop, 3R target; liquidity gate on raw bars as of the
session before) and exit at the close of the last cum-dividend session; 5 bp per side decides.
Control: the same name, same bracket and hold, decided 32 sessions after the leg (mid-quarter), with
the same gates. Pass = P1 (edge), P2 (edge over the placebo), P3 (robustness), P4 (sample), all
holding.

## Funnel

| Stage | Count |
| --- | --- |
| Cash dividends in the acquisition (process window 2016-01-01 → 2026-12-01; 4,033 actions in all) | 3,906 on 107 payers |
| Excluded before eligibility: outside the calendar 162, outside the decision window 112, special 9 | 283 |
| Excluded by eligibility: irregular gap 273, no predecessor 26, same-day duplicate record 29 | 328 |
| Excluded by the gates: illiquid volume 591, illiquid price 43, structural action nearby 10 | 644 |
| **Leg events** | **2,651** |
| Controls (dropped: illiquid volume 33, structural action nearby 11, illiquid price 1) | 2,606 (45 legs unpaired) |
| Labelled legs / controls (excluded: 3 leg exits on or after the ex-date, 2 rows with a missing session) | 2,647 / 2,605 |
| Paired for P2 | 2,601 pairs over 1,403 sessions |

No bar failures; the static liquidity reference used 141–159 names on every date; no cohort symbol
was unsupported. The acquisition came from the cache written minutes earlier by an aborted first
start of the same run (identical request hash), so the content digest `cc5e27f5…fd62` is the one
Alpaca returned that morning.

## Result

| Rule | Value | Holds |
| --- | --- | --- |
| P1 mean R_cost (5 bp) and bootstrap ci90 | +0.069R, [+0.035, +0.102], one-sided p 0.002 | yes |
| P2 leg − control, paired ci90 | control +0.046R; difference +0.025R, [−0.026, +0.072], p 0.19 | **no** |
| P3 1%-trimmed mean / mean from 2023-01-01 | +0.062R / +0.069R | yes |
| P4 events / from 2023-01-01 | 2,647 / 1,013 | yes |

Decision as pre-registered: **failed** (P2). Nothing is eligible for a probe.

## Descriptives (never decisive)

- Costs: +0.094R at 0 bp, +0.069R at 5 bp, +0.044R at 10 bp per side. The median risk unit
  (2×ATR14) is 4.2% of price, so +0.069R is about +29 bp net per five-session hold; the leg's mean
  gross return is +41 bp against the control's +29 bp.
- Exits: 2,281 time exits at the cum-dividend close (+0.23R mean), 356 stops (−1.02R), 10 targets
  (+3.05R); mean holding 4.8 sessions; hit rate 55% against the control's 52%.
- Post-ex secondary hold (10 sessions, dividend folded into adjusted bars): +0.106R, i.e. no
  reversal in the five sessions after the ex-date on this cohort.
- Yield terciles (raw dividend / raw D−1 close): low +0.038R, mid +0.051R, high +0.117R. The
  Hartzmark–Solomon ordering is present, and it is the one descriptive the control does not speak
  to (the control is not yield-sorted).
- Liquidity terciles are flat (+0.061 / +0.070 / +0.076R); the 113 foreign-flagged events match the
  rest (+0.073R).
- By year the leg is positive in ten of eleven years (2022 −0.049R; best 2017 +0.150R); the control
  is positive in eight of eleven (2018 −0.089R, 2020 −0.076R, 2026 −0.038R). The two series do not
  move together year by year, which is why the paired interval is wide.
- Median one event per decision session, p90 four, max seven.

## What this says

- The frozen leg has a positive, significant cost-adjusted edge on this cohort. It is **not**
  distinguishable from simply being long these names for five sessions at a random mid-quarter
  time: a same-name placebo earns two thirds of it. The run-up premium over the drift is +0.025R
  (about +10 bp), with an interval that includes zero. Under the pre-registered rule that is a
  failed leg, and the right reading is "large-cap long drift plus a possible small dividend
  premium", not "a dividend anomaly".
- The protocol worked end to end: the cohort was pinned by digest, the acquisition was complete
  (every in-window ex-date and its predecessor inside the process window), every exclusion was
  counted, no label crossed the ex-date, and the paired test paired each leg with its own control.
- The yield gradient (+0.04R → +0.12R from low to high yield) is the only structure the placebo
  cannot explain by construction, and it is the direction the literature predicts. It is a post hoc
  observation and cannot pass or fail anything.

## Decision

The `exdiv` v1 leg is **failed** and stays off; no probe. The catalog row records the result.

If the desk wants a second look, the one change this evidence motivates is a **yield-conditioned
v2** (for example the top yield tercile of quarterly-regular payers, with the same lead, bracket
and placebo), declared as a second look on the same window in its own frozen entry, with the
control kept so that drift is netted out. Everything else should stay as in v1 so the comparison
is clean. Reading this window a second time with a changed rule is a second look at the same data
and must be declared as such.

## Caveats

- Current-member cohort (not point-in-time). The study knows the ex-date five sessions ahead because
  the acquisition is historical; live, the declaration decides (the 2026-10-12 daemon capture saw
  17 future ex-dates up to 60 days ahead).
- The decision price is the close of the last regular hourly bar ending at or before 10:35 (the
  09:30–10:30 bar) with the fill at the next bar's open; no broker slippage is measured. Costs are
  the desk's standing 5 bp per side.
- The control sits inside a later run-up window for about six of the 2,606 controls (next ex-date
  at D_c+6..10); a v2 could widen the control's exclusion span.
- One hypothesis, one test; nothing was tuned on this data.

Artifacts: `~/agentic-trader-research/apriori-exdiv-v1-20261012` (`protocol.json`, `manifest.json`,
`result.json`, `events.csv.gz`, `labels.csv.gz`), bar and action cache
`~/agentic-trader-research/apriori-exdiv-cache`. SDD ledger archived under
`~/agentic-trader-research/exdiv-sdd-20261012/`.
