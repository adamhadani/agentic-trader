# A priori catalog, entry 1: post-earnings drift — September 26, 2026

**Result: the LONG leg passed all four predeclared criteria; the SHORT leg failed.**

- **LONG** (EPS beat ≥ 5% and a ≥ +1σ SPY-relative reaction, entered on the second
  session after the report): **+0.149R per trade after 5 bps per side**
  (ci90 [+0.070, +0.232], 4,726 trades over 1,275 sessions). It beat the control (the same
  bracket on every liquid reporter) by **+0.055R** (ci90 [+0.021, +0.089]). It is eligible
  for Part 2: a capped live paper probe, which needs its own spec.
- **SHORT** (miss ≤ −5% and a ≤ −1σ reaction): **−0.064R** (ci90 [−0.151, +0.032],
  1,408 trades). It beat its control, but in absolute terms it loses, and it lost in the
  recent window. It stays off.
- Nothing trades yet. The study grants no registry, shadow or promotion credit
  (`authorizes_promotion: false`).

## Protocol

- **Frozen entry:** `config/research/apriori/pead-v2.json`, SHA-256 `fc8a5b3e…d818627`.
  The design is the [PEAD study spec](superpowers/specs/2026-09-25-apriori-pead-study-design.md).
  Code revision `02a2ffd`, clean tree.
- **Version 1 failed closed, 2026-09-25.** It stopped before computing any outcome.
  Nasdaq's calendar is empty for nearly every session from 2016-06-06 to 2016-07-08, so
  2016 had 25 empty session pages out of 218, over the frozen 10% per-year limit. Version 2
  is identical except that decisions start on 2016-08-01, after the gap. The threshold was
  not changed.
- **Window:** decisions from 2016-08-01 to 2026-07-31, bars through 2026-09-01. "Recent"
  means decisions from 2023-01-01.
- **Events:** the Nasdaq earnings calendar, one page per weekday. EPS surprise is
  recomputed as `(eps − forecast)/|forecast|`, requiring |forecast| ≥ $0.05 and at least
  one estimate. The reaction is `z = (r_i − r_SPY)/(σ_20 · √2)` over close(D−1) to
  close(D+1), with σ taken from the 20 sessions before D.
- **Liquidity at decision time, from raw prices:** the live dynamic-universe rule. Median
  20-session dollar volume must be at least the 25th percentile of the 159 static
  equities, and the close must be at least $10, both as of D+1.
- **Trade:** the decision is at 10:35 New York on D+2, priced from the 09:00 hourly
  close. The stop is 2×ATR14, the target is 3R and the time exit is the close of the 20th
  session. The existing conservative bracket labeller is used: a bar that touches both
  levels counts as the stop, and gaps fill at the open.
- **Statistics:**
  - Event-weighted means with a stationary bootstrap over whole decision sessions
    (block mean 10, 2,000 draws, seed 20260925).
  - ci90 is the 5th–95th percentile interval. A criterion holds when the mean is above
    zero **and** the lower bound is above zero.
  - P2 resamples each session once for both the leg and the control.

## Scale

- **Calendar:** 2,615 pages, all reused from the version 1 cache and none failed. They
  hold 147,031 rows (34 duplicates removed).
- **Empty session pages per year:** between 1 and 6, all holidays or quiet days.
- **Events:** 23,983 liquid events.
- **Rows not used, by reason:**

  | Reason | Rows |
  | --- | ---: |
  | Below the dollar-volume threshold | 73,450 |
  | Below $10 | 44,610 |
  | Short history | 3,266 |
  | Decision outside the window | 777 |
  | Unsupported symbol | 742 |
  | Non-session date | 184 |
  | No reaction bars | 14 |
  | No daily bars | 5 |

- **Labelling:** 63 events had degenerate levels (the stop would fall at or below zero)
  and 4 were immature. One symbol had no bars at all (WCCB).
- **Static reference:** 159 names, with between 141 and 159 contributing on each of the
  2,476 decision dates (median 152).
- **Surprise sign:** the recomputed surprise and Nasdaq's reported surprise disagree in
  sign on 19 rows.

## Criteria

| Leg | P1 mean R (ci90) | P2 vs control (ci90) | P3 trimmed / recent | P4 n / recent | Result |
| --- | --- | --- | --- | --- | --- |
| LONG | **+0.149** (+0.070, +0.232) ✅ | **+0.055** (+0.021, +0.089) ✅; control +0.094 | +0.135 / +0.161 ✅ | 4,726 / 2,133 ✅ | **eligible for probe** |
| SHORT | −0.064 (−0.151, +0.032) ❌ | +0.100 (+0.048, +0.157) ✅; control −0.164 | −0.082 / −0.160 ❌ | 1,408 / 619 ✅ | **failed** |

## Diagnostics (descriptive, not used for the decision)

| | LONG | SHORT |
| --- | --- | --- |
| Mean R at 0 bps | +0.168 | −0.050 |
| Surprise-only rule | +0.115 (n 11,842) | −0.083 (n 2,998) |
| Reaction-only rule | +0.120 (n 7,869) | −0.125 (n 7,712) |
| 60-session hold | +0.252 (n 4,679) | −0.103 |
| Liquidity tercile (low / mid / high) | +0.189 / +0.126 / +0.132 | −0.105 / −0.065 / −0.024 |
| Exits (stop / target / time) | 40% / 5% / 55% | 41% / 2% / 57% |
| Leg events per decision session (median / p90 / max) | 2 / 9 / 25 | 1 / 4 / 9 |

LONG by year, as mean R (n):

| Year | Mean R | n |
| --- | ---: | ---: |
| 2016 (Aug–Dec) | +0.40 | 112 |
| 2017 | +0.34 | 335 |
| 2018 | +0.01 | 338 |
| 2019 | +0.12 | 396 |
| 2020 | +0.19 | 389 |
| 2021 | +0.13 | 527 |
| 2022 | +0.02 | 496 |
| 2023 | +0.08 | 597 |
| 2024 | +0.41 | 645 |
| 2025 | +0.05 | 550 |
| 2026 (to July) | +0.01 | 341 |

Every year is positive, but 2018, 2022, 2025 and 2026 are close to zero.

## Reading the result

- **Most of the long leg's absolute edge is market drift, not the signal.** The control
  (long every liquid reporter) earns +0.094R, reflecting a rising 2016–2026 market and
  survivorship. The signal adds +0.055R per trade, about 37% of the leg's mean. P2 was
  required so that this share is measured rather than assumed. Survivorship affects the
  leg and its control alike, so the difference is the more robust number.
- **The two conditions help each other.** Requiring both a beat and a strong reaction
  beats either condition alone (+0.149R vs +0.115R and +0.120R), as the literature
  expects.
- **A longer hold looks better,** at +0.25R over 60 sessions, consistent with drift
  running for about a quarter. This is descriptive only. A 60-session protocol would be
  a new version with a fresh window.
- **Candidate supply fits the Telegram goal.** A median of 2 and a p90 of 9 leg events
  per decision session is enough to fill 1–2 cards in earnings season. Off-season weeks
  are thin.
- **Shorts:** negative surprises underperform the control, but shorting reporters in
  this period lost money overall (control −0.164R), so the leg itself is negative. This
  matches the native-short result of September 23.

## Caveats

- **Survivorship:** Nasdaq's history lacks delisted names. Early years are thinner: 9.7k
  rows in 2016 against 17.7k in 2025. This flatters absolute long results; P2 is less
  affected.
- **Report time is unknown historically.** Entry waits until D+2, forgoing any day-one
  drift.
- **EPS and consensus values are as served today,** not guaranteed to be point-in-time.
  Ticker changes can lose events.
- **No sector-relative reaction.** Nasdaq rows carry no sector.
- **Hourly-bar entry follows the setup-study convention.** Entry is the 11:00 bar open,
  with levels from the 10:00 close. This is not a fill model.
- **Paper evidence only.** This is a historical, bar-level study. Live fills, slippage
  on earnings-season names and the live Nasdaq feed are untested.

## Decision

- **LONG:** eligible for Part 2, a capped live paper probe of catalog cards, with its own
  spec. That spec covers a live earnings-event source, time exits on cards, probe caps,
  and tolerance of calendar outages.
- **SHORT:** off, recorded as failed.

**Artifacts** (private, not in Git):

- `~/agentic-trader-research/apriori-pead-v2-20260926/` holds the protocol, manifest,
  `events.csv.gz`, `labels.csv.gz` and `result.json`.
- `~/agentic-trader-research/apriori-pead-v1-20260925/` holds the version 1 run that
  failed closed.
- `~/agentic-trader-research/apriori-pead-v1-cache/` holds the calendar pages and bars.
