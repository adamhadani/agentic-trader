# Insider cluster leg v2: breadth replication — November 14, 2026

**Result: not replicated (P2 fails).** v1's frozen rule was run on cohort xu, 1,156 names v1 never read, and
produced 1,048 labelled events. Cluster names beat their same-session matched controls by **+0.011R per trade,
ci90 [−0.049, +0.074]** (one-sided p 0.37). P4 holds: 1,048 events, 430 of them from 2023.

The frozen decision is P2 and P4, so the replication does not hold. The insider lane closes as an alpha
source, and the watch cards stay unvalidated. No threshold is revisited.

v1's +0.070R sits at the top edge of this interval. The read does not rule out a small effect. It says the
effect v1 measured does not appear on new names, at a sample the size of v1's.

## Protocol and run

- **Entry.** `config/research/apriori/insider-v2.json`, SHA-256 `4c826782…39ee`, committed before the read.
  - It copies v1's event, control, bootstrap and pass-rule blocks verbatim.
  - Its decision rule declares the [spec](superpowers/specs/2026-11-14-insider-breadth-replication-design.md)'s
    exception to v1's "new window" clause.
  - The replication holds iff P2 and P4 hold.
  - Ledger row L44 was written before the read.
- **Population.** Cohort xu (`6ece7c54…`) is the union of two bands of screen-v3's ranking, both outside
  cohort v3's ranks 401–1,400:
  - ranks 1–400 (large);
  - ranks 1,401–2,156 (small/mid, at least $2M median dollar volume on the screen window).
- **Inputs.**
  - The pooled protocol `cohort-xu-insider-v1.json` (`33445bda…`).
  - A new label cube under v1's cube spec (`f9c4c5ea…`, 1,998,673 labelled of 2,000,677 eligible cells), with
    the same bracket, $5M floor, $10 minimum price and 15 bp.
  - The cohort-xu corporate actions (`9f38dffd…`, 29,067 actions) and the unchanged insider archive
    (`67a262c0…`).
  - The same 2,681-session calendar as v1 (`e829346d…`).
- **Cube build.** `alpha pooled power` (check A) downloaded bars for the names without cached bars and saved
  the cube. It was then stopped at replicate 12 of 100. The a priori read does not use check A's result, and
  the pooled insider campaign stays retired.
- **Run.** 2026-11-14 09:05 ET from revision `fd74783`, a clean tree that was later rebased onto main as
  `312b440` with the entry unchanged. Output is in `~/agentic-trader-research/apriori-insider-v2-20261114`.
- **Sources.**
  - 104 trades were dropped as implausible.
  - 82 cohort names have no qualifying transaction. Most are foreign private issuers, which file no Form 4;
    two of the 82 have no CIK.

## Event funnel

| Step | Count |
| --- | ---: |
| Triggers (after the 63-session cooldown; 2,162 further cluster days suppressed) | 1,989 |
| Outside the decision window (2016 before 08-01; 2026 after 07-31) | 131 |
| Before the name's latest identity break | 134 |
| After the calendar | 1 |
| Accepted triggers | 1,723 |
| Not eligible in the cube (price, dollar-volume floor or ATR as of D−1) | 672 |
| No return / unlabelled | 1 / 2 |
| **Labelled events, every one paired with 5 controls** | **1,048** |

- **Sizing check.** The count-only step found 1,724 accepted triggers. The one-trigger difference is the
  after-calendar exclusion.
- **Eligibility.** 39% of accepted triggers fail cube eligibility, against 17% in v1. Many small/mid events
  fall below the $5M per-session floor, as the spec expected.
- **Density.** Events per session: a median of 1, a p90 of 2 and a maximum of 10, over 754 decision sessions.

## Rules

| Rule | Role | Value | 90% CI | Holds |
| --- | --- | ---: | --- | --- |
| P2: leg − matched control | **Primary** | +0.011R (leg +0.030R, control +0.019R) | [−0.049, +0.074] | ❌ |
| P4: ≥ 300 events, ≥ 100 from 2023 | Required | 1,048; 430 | — | ✅ |
| P1: mean `r_cost` > 0, CI lower bound > 0 | Descriptive | +0.030R | [−0.067, +0.133] | ❌ |
| P3: 1%-trimmed mean; mean from 2023-01-01 | Descriptive | +0.020R; +0.071R | — | ✅ |

- **Bootstrap.** A stationary session block, mean block 10, 2,000 draws, seed 20261023.
- **p-values.** One-sided p = 0.37 (P2) and 0.30 (P1). The bootstrap SD of the P2 difference is 0.037R.
- **The `decision` field.** The runner's field reads `failed` because it applies v1's all-four rule. The entry's
  own rule, P2 and P4, also fails.

## Descriptives (never decisive)

- **By band** (paired difference, leg − control):

  | Band | Events | Since 2023 | Leg | Control | Difference |
  | --- | ---: | ---: | ---: | ---: | ---: |
  | Large (ranks 1–400) | 412 | 141 | +0.058R | +0.058R | +0.000R |
  | Small/mid (ranks 1,401–2,156) | 636 | 289 | +0.012R | −0.006R | +0.018R |

  Neither band shows v1's +0.07R.
- **By buyers:**
  - Mean `r_cost`: 2 buyers +0.041R (n 845); 3 or more −0.015R (n 203). v1's gradient (+0.066R, +0.165R)
    reverses.
  - Paired difference, large band: 2 buyers +0.036R, 3+ −0.162R.
  - Paired difference, small/mid band: 2 buyers +0.016R, 3+ +0.024R.
- **By year:**

  | | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
  | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
  | Events | 34 | 64 | 99 | 99 | 141 | 79 | 102 | 136 | 86 | 130 | 78 |
  | Leg `r_cost` | +0.23 | +0.18 | −0.18 | +0.26 | −0.14 | +0.07 | −0.12 | −0.04 | +0.17 | +0.24 | −0.13 |
  | Leg − control | +0.28 | −0.02 | −0.07 | +0.13 | −0.06 | +0.15 | +0.04 | −0.26 | +0.13 | +0.23 | −0.21 |

  The difference is positive in 6 of 11 years, with no trend.
- **By 20-session return before entry:**
  - bottom tercile +0.028R;
  - middle +0.079R;
  - top −0.017R.

  v1's pattern was −0.021R, +0.125R, +0.157R. The two do not match.
- **By opportunistic dollars / dollar volume:**
  - low +0.063R;
  - middle −0.017R;
  - high +0.044R.
- **Exits:** 24 targets, 383 stops, 641 timeouts. Raw `r` is +0.075R for the leg against +0.066R for the
  controls.
- **Leg minus the mean of every labelled cell that session:** +0.010R.
- **60-session close-to-close:** leg +0.058, matched controls +0.058, difference −0.001 (16 immature).

## What this says

1. **v1's P2 does not replicate on new names.** The rule, window and controls are the same, and the sample is
   comparable (1,048 events against 1,062). The relative effect falls from +0.070R to +0.011R. Neither band nor
   the buyer count recovers it, and v1's descriptive gradients do not reappear either. The likeliest reading is
   that v1's P2 (one-sided p 0.026) was largely a draw from one band. That band was one of several a priori
   reads on the same window.
2. **What the frozen decision closes:**
   - **The lane.** The insider lane closes as an alpha source, and no market-neutral insider probe is specified.
   - **The Sharadar case.** `insider-oos-v1`, H2a of the Sharadar era manifest, loses its premise. The event
     lane's case for buying Sharadar now rests on `pead-sue-v3` alone.
   - **The pooled fixed sets.** They stay retired.
3. **The watch cards are unchanged.**
   - They remain the operator-requested, unvalidated paper lane: auto-entered on paper since #153, with a $100
     risk cap, at most 2 a session and 4 open.
   - Their live record is the insider-watch row of the weekly scorecard.
   - Whether to keep auto-entering them is an operator decision. This read gives no reason to expect an edge.
4. **A pooled average is not a test.** Across both cohorts the simple average difference is about +0.04R over
   about 2,110 events. That figure is descriptive only. The rule decides on v2 alone, and pooling two reads
   after seeing the second would be a new hypothesis.

## Caveats

- **Survivorship.** Cohort xu is a current-membership snapshot (2026-10-15), as v1's was. The bias is
  stronger in the small/mid band. The matched controls share it.
- **Shared market path.** v1 and v2 share a regime path. The paired difference removes most of it, which is why
  P1 is descriptive.
- **Two different bands.**
  - Large caps: insider purchases are rarer and widely followed.
  - Small/mids: many events fail the $5M floor.

  A prospective read on v1's own band from 2026-08-01 would be the cleaner test. At about 9 events a month it
  would take about three years, and nothing here schedules it.
- **v1's caveats still apply:**
  - filing-date timing;
  - earnings proximity;
  - the one-year routine definition;
  - share classes counted separately;
  - the latest-identity-break exclusion.
