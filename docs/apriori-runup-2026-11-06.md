# A priori pre-earnings run-up v1 — November 6, 2026

**Result: `failed` (P1, P2, P3 and P5).** The rule buys the most volatile third of cohort-v3 announcers ten
sessions before a scheduled report and sells at the close the day before it. Net of a beta-matched SPY and
15 bp per side, it returns **−0.070R per leg** (10,395 legs, ci90 [−0.101, −0.037]).

Against 5 matched non-announcers on the same session, the legs differ by **−0.006R** (ci90 [−0.026, +0.014]).
The planted check found a 0.63% effect, and the minimum detectable effect on the paired test is 0.039R. A
run-up of the published size would have shown, so this is a powered null, not an inconclusive read.

This was a development read on a spent window: it could fail but not pass. It failed, so there is no
prospective read, no journal-only recording of 2027 events and no probe. The pre-earnings run-up is closed
on this cohort.

## Protocol and run

- **Entry.** `config/research/apriori/runup-v1.json`, SHA-256 `77e4f20b…2431`. It was amended in place
  before any outcome was read; the [spec](superpowers/specs/2026-11-06-apriori-runup-design.md) records the
  amendment. The pinned inputs:
  - the pooled protocol `cohort3-events-v1.json` (`b692d7f9…`) and its cohort (`8c0d807a…`, 1,000 names in
    liquidity ranks 401–1,400);
  - the cached cohort-v3 cube (`75dbb80a…`, the cube the insider leg used);
  - the Nasdaq page archive (`9273e40a…`, 2,724 pages through 2026-07-31);
  - the cohort-v3 corporate actions (`8a547b06…`).
- **Rule.** The [catalog](apriori-alphas.md#entry-4-uncertainty-conditioned-pre-earnings-run-up) has the
  full rule:
  - decide on D = E − 10 at 10:35 New York and exit at the close of E − 1, so the hold never includes the
    report;
  - legs are the top tercile of 60-session realised volatility among cube-eligible names on D;
  - stop 2×ATR14, no target, 15 bp per side;
  - controls are 5 same-session names in the same dollar-volume tercile and the top volatility tercile,
    with no report in [D − 30, D + 10], covered by the archive and nearest in 20-session return;
  - each leg has one same-name pseudo-event (negative control);
  - a planted 0.63% return tests power.
- **Run.** 2026-11-06, 10:51:04 to 11:04:32 ET, at clean revision `db8c39b`, run `725114f8`.
  - Everything came from cache except SPY hourly bars, fetched once into the run directory.
  - The trading calendar has 2,681 sessions (SHA-256 `e829346d…`, the insider leg's).
  - Output is in `~/agentic-trader-research/apriori-runup-v1-20261106`.
- **Bootstrap.** A stationary session block (mean block 10), 2,000 draws, seed 20261106.

## Event funnel

| Step | Count |
| --- | ---: |
| Archive reports of cohort names (deduplicated) | 38,102 |
| Decision session outside 2016-08-01 → 2026-07-17 | 1,722 |
| Identity break inside the volatility window | 1,854 |
| Not cube-eligible on D | 2,074 |
| Previous report within 40 sessions | 317 |
| No 60-session volatility / report date not a session / structural action nearby | 73 / 34 / 39 |
| **Events** | **31,989** |
| of which legs (top volatility tercile) | 10,484 |
| of which announcers (descriptive only) | 21,505 |
| Legs labelled / with a finite net excess | 10,469 / 10,395 |
| Legs paired with 5 labelled controls | 9,992 |
| Pseudo-events labelled / paired | 8,374 / 6,758 |

- **Year keys.** Exclusions are keyed by the report's year; kept rows are keyed by the decision year.
- **Unpaired legs.** 428 had fewer than 5 candidates; 49 more lost a control cell to a labelling exclusion.
- **Legs per decision session.** Median 3, p90 19, maximum 52.

## Rules

| Rule | Value | 90% CI | Holds |
| --- | ---: | --- | --- |
| P1: legs' mean `net_excess_r_v1` | −0.070R | [−0.101, −0.037] | ❌ |
| P2: legs − matched controls (`r_cost`) | −0.006R (controls −0.014R) | [−0.026, +0.014] | ❌ |
| P3: 1%-trimmed mean; mean from 2023-01-01 | −0.080R; −0.114R | — | ❌ |
| P4: ≥ 1,000 legs, ≥ 300 from 2023 | 10,395; 4,351 | — | ✅ |
| P5: legs' paired difference − pseudo-events' | −0.028R (−0.006 vs +0.022) | [−0.059, +0.004] | ❌ |
| Negative control: pseudo-events' P2 must not hold | +0.022R | [−0.005, +0.047] | ✅ (does not hold) |
| Planted 0.63%: P1 and P2 on the pseudo-events | +0.038R; +0.099R | [+0.005, +0.070]; [+0.072, +0.124] | ✅ detected |

- P1 has 1,538 decision sessions, one-sided p = 1.00; P2 has p = 0.67.
- The planted shift averaged 0.077R per pseudo-event.
- The pseudo-events' P2 bootstrap SD is 0.0158R, so the MDE is (1.645 + 0.84) × 0.0158 = 0.039R.

## Descriptives (never decisive)

### Where the −0.070R comes from

The legs' gross return to the stop or time exit is **+0.031R**. `net_excess_r_v1` subtracts:
- the per-name cost (median 9.8 bp per side), about 0.026R;
- beta × SPY's move over the same hold, 0.075R (mean beta 1.44, SPY +0.049R).

That leaves **−0.070R**. The decisive 15 bp `r_cost` is −0.008R before the market is removed.

The controls do the same: on `net_excess_r` the leg-minus-control difference is −0.001R (ci90 [−0.019,
+0.020]), with control beta 1.42. The shortfall is a property of high-volatility mid-caps over 2016–2026: they
lag a beta-scaled SPY whether or not a report is coming. There is no run-up to subtract it from.

| Cost per side | 5 bp | 15 bp | 25 bp |
| --- | ---: | ---: | ---: |
| Legs' mean `r_cost` | +0.018R | −0.008R | −0.035R |

### The run-up exists only before costs and beta, and not in the legs

| Group | n | `r_cost` | `net_excess_r` |
| --- | ---: | ---: | ---: |
| All announcers (every tercile) | 31,925 | +0.014R | −0.050R |
| Bottom volatility tercile | 10,743 | +0.027R | −0.032R |
| Legs (top tercile) | 10,469 | −0.008R | −0.070R |

The raw pre-announcement drift is small, falls with volatility, and is negative net of the market in every
tercile. That is the opposite of the uncertainty-conditioned version the leg tested.

### Exits, years and splits

- **Exits.**
  - 3,190 legs (30%) hit the stop, mean −1.04R.
  - 7,279 timed out at E − 1, mean +0.44R.
  - Mean hold 8.7 sessions.
  - The stop rate is 24–41% in every year.
- **Last day.** The E − 1 session's own excess is +0.005R, so no late run-up is cut off by exiting before the
  report.
- **By year (`net_excess_r`).**

  | Year | n | `net_excess_r` |
  | --- | ---: | ---: |
  | 2016 | 224 | −0.018R |
  | 2017 | 849 | −0.166R |
  | 2018 | 884 | −0.124R |
  | 2019 | 928 | +0.013R |
  | 2020 | 948 | +0.135R |
  | 2021 | 1,070 | −0.140R |
  | 2022 | 1,141 | +0.031R |
  | 2023 | 1,197 | −0.103R |
  | 2024 | 1,225 | −0.109R |
  | 2025 | 1,245 | −0.175R |
  | 2026 | 684 | −0.033R |

  Excluding 2020 gives −0.090R.
- **Dollar-volume bucket (`net_excess_r`).** Low −0.067R, mid −0.058R, high −0.086R.
- **Past move, by tercile (`net_excess_r`).** The past move is the mean absolute two-session excess reaction
  to up to the last 4 reports before D (139 legs have fewer than 2). Low −0.066R, mid −0.080R, high −0.062R:
  no gradient. Garbled near-duplicate archive listings can double count a reaction; this is descriptive only.
- **Report timing.** The archive's historical pages leave timing unspecified for 99.4% of legs (25
  pre-market, 42 after hours), so the timing split is uninformative.
- **Archive coverage.** 22 legs had no archive report in the 130 sessions before D (−0.228R); the other
  10,373 had −0.069R.
- **Jumps in the hold.** A session with |excess| > 3 × the 60-session RV hit 3.8% of legs and 4.6% of
  pseudo-events. Legs show no more jumps than quiet periods do, so there is no sign of reports misdated into
  the hold.
- **Dispersion.** Per-leg SD 0.87R. Legs cluster on sessions (design effect 4.2), which the session-block
  bootstrap carries.

### Control-band sensitivity (committed before the read)

A control may not report in [D − 30, D + 10], so its next report can fall 11–20 sessions after D: inside
its own pre-report window. If a run-up existed, it would leak into the control mean, biasing P2 toward
failing, while P5 would cancel it to first order.

Measured from `control_cells.csv.gz` and the archive (`sensitivity.json` in the run directory), it does not
leak. Leg controls by sessions from D to their next archive report:

| Next report after D | Cells | `r_cost` | `net_excess_r` |
| --- | ---: | ---: | ---: |
| 11–20 sessions | 34,682 | −0.011R | −0.074R |
| 21–40 | 13,681 | −0.005R | −0.072R |
| 41–63 | 703 | −0.050R | −0.117R |
| > 63 | 447 | −0.145R | −0.119R |
| none in the archive | 447 | −0.274R | — |

Controls in their own run-up window do no better than controls a month from a report.

Restricting each pair's control mean to cells whose next report is more than 20 sessions after D:
- **Legs' P2:** −0.017R (7,472 pairs, ci90 [−0.051, +0.018]).
- **Pseudo-events' P2:** +0.031R (ci90 [+0.004, +0.056]).
- **P5:** −0.048R (ci90 [−0.091, −0.005]).

Restricting to more than 40 sessions leaves 985 legs:
- **Legs' P2:** +0.126R (ci90 [+0.020, +0.238]), against controls at −0.086R.
- **Pseudo-events' P2:** +0.066R.
- **P5:** +0.060R (ci90 [−0.054, +0.174]).

Names that go two or more months without reporting are off-cycle or leaving the archive, and they
underperform. That lifts every comparison against them; the run-up itself does not. The verdict does not
depend on the band.

## Known limits

- **Pseudo-events near the archive's end.** For decisions after about 2026-07-02, the quiet band
  [D' − 10, D' + 20] runs past the archive's last date (2026-07-31), so a report there is not seen. This
  affects about 11 sessions of pseudo-events and biases toward `failed`. The read failed on the legs' own
  P1 and P2, so it does not matter here.
- **`planted.json` ordering.** It is written after the labels, not before them; no leg statistic is
  computed before it.
- **Window.** The window was spent (W1, W2 and W6), which is why this read could only fail.

## What this changes

- **Run-up lane: closed.** `runup` v1 is closed. A v2 would need a new hypothesis, not a threshold change.
  Even raw pre-announcement drift does not survive costs and the market here.
- **Earnings capture (PR #141): its main purpose is gone, but it still earns its keep.** It was justified as
  the clean prospective sample for this lane. It is still the only free dated record of the forward
  calendar, which any future pre-event test, the EDGAR veto layer and an audit of the live `earnings` risk
  rule's dates need. It costs about 25–40 MB of journal a year.
- **Ledger.** Row L43 records the verdict.
