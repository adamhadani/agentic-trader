# Pooled alpha mining

The pooled lane evaluates **one formula across a frozen cohort of equities** and tests a
session-paired bracket-R edge with a session-block bootstrap. It is research only: no
database, registry, broker or Telegram access, no trial, shadow or promotion credit.
Every manifest and result carries `"authorizes_promotion": false`.

This page is the contract for what Part 1a builds. It follows the
[design spec](superpowers/specs/2026-09-28-pooled-alpha-mining-design.md); where the
built code sharpens the spec, this page describes the code. Part 1a delivers the cohort,
label cube, formula selection, statistics, two literature entries, the campaign
protocol and its stage logic, power check A and the `alpha pooled power|study`
commands. Part 1b, next, adds the campaign runner, the journal-backed ledger, the
genetic search and search check B.

## Purpose

The per-symbol miner cannot detect edges of 0.05-0.15R (R is profit measured in units
of the trade's initial risk). The 2026-09-26 ETF32 run charged 512 trials and produced
0 discovery finalists. The median formula makes 14 trades per validation window, a
standard error near 0.33R. Qualification deflates by the global trial family (8,879
trials on 2026-09-28), which at that count acts like a t of about 5.5, or roughly 1.8R
of mean profit per trade at 14 trades. The [power diagnosis](alpha-power-diagnosis-2026-09-18.md)
recorded 0/64 planted signals accepted.

The one study that passed, [PEAD](apriori-pead-2026-09-26.md), pooled a single
pre-registered hypothesis over 4,726 events and paired each session with a control,
which cut its standard error 2.4-fold. The
[literature report](alpha-pooled-mining-research-2026-09-28.md) recommends the same
shape for mining: one formula counts as one trial, is scored across a wide cohort by a
session-paired edge, the number of formulas is rationed, and a few frozen finalists
are confirmed once on a period the search never read. Calibrated on PEAD's variance,
the lane can detect paired edges of about 0.08-0.10R. Smaller edges remain the
[a priori catalog's](apriori-alphas.md) job.

## Cohort

`config/research/pooled/cohort-v1.json` is committed before any data is read. Its
identity is the SHA-256 of its bytes (table below); a changed file is a new version. `load_cohort` validates that `symbols` is the sorted union of the
sources' supported symbols.

- 353 supported symbols. Sources: the config groups `mega_caps` and `research_cohort`
  (127 symbols, the scan universe's equities) and the 300 symbols of the prospective
  equity snapshot v2 ([equity universe](alpha-equity-universe.md)). The two overlap.
- Symbols outside `^[A-Z]{1,5}$` are listed under `excluded` (10, such as `BH.A` and the
  `.WS` and `.U` warrant and unit forms).
- `survivorship`: "current membership (2026-09); not point-in-time".

The snapshot is a random hash sample of currently tradable names. It still contains
SPAC units, warrants and preferreds whose tickers match `^[A-Z]{1,5}$`; the per-session
price and liquidity gate below removes most of them.

Frozen identities (SHA-256 of file bytes):

| File | SHA-256 |
| --- | --- |
| `cohort-v1.json` | `966b67c53ad617d698089dd5b5786b05d4d5248f8844e3326d7addbe8d4ac5a9` |
| `campaign-v1.json` | `897fd8e59d912975de9377389a75d8954a747ab49dfa6ae368a30879059ca5ba` |
| `high52-v1.json` | `9c3369278cb667db6016e93453cea7a55231234362718b3643d7bcce31626930` |
| `reversal-lowmax-v1.json` | `da39ef0b49e2af96ebbc328bbc9c59fc0802a449bc94e456b4f46b3fb9c18967` |

Entries and the campaign protocol pin the cohort and campaign SHA-256, so the campaign
is provably frozen before any literature result exists.

## Point-in-time eligibility

A decision at 10:35 New York on session D can use only completed sessions, so
eligibility is evaluated at `as_of` = D-1, the same rule as PEAD and the live
dynamic-universe screen (the shared `median_dollar_volume` and `static_reference`
functions are reused, not reimplemented):

- the **raw** (unadjusted) close at D-1 is at least $10;
- the 20-session median dollar volume of **raw** bars at D-1 is at least the 25th
  percentile of the same statistic over the configured static-universe equities;
- an ATR14 is available from adjusted daily bars through D-1.

Raw bars are used for price and liquidity because adjusted history depends on later
corporate actions. Eligibility never depends on a formula, so every formula is compared
with the same control. A session with fewer than 30 eligible names is skipped and
counted; a session with no static reference is skipped and counted, and coverage fails
closed when more than 2% of sessions lack one.

Data is Alpaca SIP. SIP daily history starts 2016-01, so a formula with a 252-session
lookback has no score until early 2017. Decisions run 2016-08-01 to 2026-07-31; bars
are read from 2016-01-01 through 2026-09-01 (labels need up to 20 further sessions).

## Label cube

The cube stores one **long** bracket outcome per eligible (session, symbol) cell and is
shared by every formula: a formula only selects cells.

- Decision price: `decision_price` (reused from the PEAD study) at 10:35 New York,
  from adjusted hourly bars.
- ATR14: simple mean of the 14 true ranges ending D-1. Stop = price - 2 x ATR, target =
  price + 3R, maximum hold 20 sessions.
- Label: `label_bracket` (reused from the setup study), with a conservative same-bar tie
  and gap fills at the open, at 0 and 5 bps per side. 5 bps decides. Cells whose
  outcome is not yet mature, or whose levels are degenerate, are unlabelled and counted.
- Stored per cell: eligibility, labelled flag, R at 0 and 5 bps, holding sessions, hit
  code (target, stop, timeout), a hash tie-break key and dollar volume.

The cube is written once as `cube-<cohort16>-<spec16>.npz` (the first 16 hex digits of
the cohort SHA-256 and of the cube spec identity) in the cache directory and verified by
its own SHA-256 on load. The bar cache reuses the setup study's immutable `.npz` files
and range claims; raw bars live in the sibling `bars_raw` directory.

**Window guard.** Labels are readable only through `LabelCube.window(start, end)`, whose
arrays are read-only. The cube's coverage report holds counts only, never a return
statistic. The campaign opens its selection window only after discovery leaves a
finalist, and its confirmation window only after consumption is recorded.

**Coverage fails closed.** The run stops with `status: failed` and the counts when the
static reference is missing on more than 2% of sessions, or when more than 5% of
eligible cells in any year have no hourly decision data.

## Formulas and picks

A formula is a frozen document: a `score` expression in the alpha DSL, optional
`filters`, and `k` = 3 (long only).

- The score and every filter expression must be **dimensionless** (price and volume
  exponent 0), so values rank across names; anything else is rejected at load.
- A filter is `{expression, min_quantile?, max_quantile?}`. On each session the
  quantile is taken across eligible names with a finite value, and a name outside a
  bound is dropped.
- Expressions are evaluated per symbol on adjusted daily bars and read at D-1; the
  in-progress session is never used. A name without enough history has no score.

On each session the picks are chosen as follows.

1. Keep eligible names with a finite score that pass every filter.
2. Skip names the formula still holds. A pick stays held through its exit session
   (from `holding_sessions`), as the desk holds one position per name.
3. Rank by score, descending; break ties by the SHA-256 of `symbol|D`, ascending. The
   tie-break is deterministic and not alphabetical.
4. Take up to k. A session with no pick contributes nothing.

## Statistics

The unit is `R_cost` at 5 bps per side; 0 bps is reported alongside.

- **Paired edge.** On each session with a pick, `d_D` is the mean R of the picks minus
  the mean R of all labelled eligible cells that session (the control includes every
  eligible name, overlapping holds allowed). The statistic is the mean of `d_D` over
  sessions with a pick.
- **Leg mean.** The mean `R_cost` of all picks, weighting a session by its number of
  picks, as in PEAD.
- **Intervals and p-values.** A stationary bootstrap over whole sessions with block
  mean 20 (the hold length), reusing `_stationary_index_draws`, `_ci90` and
  `_p_one_sided_positive`. A criterion holds when the point estimate is positive and
  the lower bound of the [5th, 95th] percentile interval is above zero. The one-sided
  p-value is the share of bootstrap means at or below zero. Draws must match the
  table's session count; a mismatch raises.
- **Trimmed mean.** The leg mean after dropping the lowest and highest 1% of picks.
- **Purge.** Discovery and selection drop picks whose hold would run past the window's
  last session; confirmation and the literature entries do not.

Cross-checks are reported and never decisive:

- a **calendar-time portfolio**: the cube stores final R, not a daily path, so each
  pick's residual is spread evenly over its holding sessions and averaged over the picks
  open each session, with Newey-West lag `max_hold_sessions - 1` (19). This is an
  approximation;
- **two-way clustered** standard errors (date and symbol) on the pick rows;
- the **design effect** `1 + (m - 1) rho` (m is the mean picks per session, rho the
  intra-session correlation) and the effective sample size. A disagreement between the
  cross-checks and the bootstrap is reported.

Descriptive output: hit shares, edge by year, residual by liquidity tercile, picks per
session, and the share of picks from each cohort source.

## Literature entries

Each entry is a JSON file committed before any data is read and is one test at the PEAD
bar, on the whole decision window (2016-08-01 to 2026-07-31). Each pins the cohort and
campaign protocol SHA-256 and uses the same cube spec as the campaign.

| Entry | Formula | Reference |
| --- | --- | --- |
| `high52-v1` | `close / ts_max(high, 252)`, no filter, k = 3 | George & Hwang (2004) |
| `reversal-lowmax-v1` | `-1.0 * roc(close, 21)`, filter `ts_max(returns, 21)` at or below the cross-sectional median, k = 3 | Jegadeesh (1990); Bali, Cakici & Whitelaw (2011) |

Pass rule, all of which must hold:

- **P1 edge.** Leg mean `R_cost` > 0 and its interval lower bound > 0.
- **P2 signal adds value.** Paired edge > 0 and its interval lower bound > 0.
- **P3 robustness.** 1%-trimmed leg mean > 0 and the paired edge over decisions from
  2023-01-01 > 0.
- **P4 sample.** At least 500 sessions with a pick, at least 150 of them from
  2023-01-01.

The bootstrap uses block mean 20 and 2,000 draws. A pass makes the entry eligible for a
separately specified capped paper probe; nothing else. A failed entry is written up and
marked failed. Thresholds are never revisited; a change is a new version. Status of
both entries: frozen, not yet run.

## Campaign protocol and stages

`config/research/pooled/campaign-v1.json` is frozen before any real-data run of either
entry, so their results cannot shape it.

- **Windows.** Discovery 2016-08-01 to 2021-12-31; selection 2022-01-03 to 2023-12-29;
  confirmation 2024-01-02 to 2026-07-31.
- **Budget and search.** 200 formulas in total, split evenly across 12 families
  (52-week-high proximity, short-term reversal, MAX/lottery, 12-1 momentum, 12-7
  momentum, range location, standardized trend slope, abnormal volume, overnight versus
  intraday return, price-volume correlation, signed volume, high-low spread). Each
  family has seed expressions and a mutation-operator subset. Realized-volatility
  operators (`realized_vol`, `ts_std`, `ts_mad`) are forbidden, and a family or seed
  that uses one is rejected at load.
- **Excluded families** (positive results in studies that read 2021-2026, which the
  confirmation window overlaps): `vol_20`, sector-relative momentum (both survived Holm
  in the [setup-outcome study](setup-outcomes-2026-09-23.md)), residual momentum
  ([factor controls](alpha-factor-controls-2026-09-18.md)) and volatility-scaled
  momentum ([sector panel](alpha-sector-panel-2026-09-17.md)).
- **Discovery gate** (unpenalized t of the paired edge): t >= 3.0; paired edge > 0 in at
  least 3 of 4 equal-length blocks; leg mean > 0; at least 400 sessions with a pick.
  Fitness for ranking is t minus 0.02 per expression node. Two survivors whose pick
  sets have Jaccard overlap >= 0.5 are duplicates and the fitter is kept. The top 5
  distinct survivors are carried. With none, the status is `no_finalists` and the
  selection and confirmation windows stay unread.
- **Selection gate.** Paired edge > 0 and at least 0.5 x the discovery edge, with at
  least 150 sessions with a pick. The kept candidates are frozen. With none, the status
  is `no_confirmation_candidates` and the confirmation window stays unread.
- **Confirmation** (used once). The ledger records consumption of the interval, with the
  campaign id and frozen candidate hashes, before the window opens; an overlapping
  interval is refused. Holm step-down at one-sided alpha 0.05 over the m frozen
  candidates (10,000 draws). A candidate passes when it survives Holm and its leg-mean
  interval lower bound > 0, its 1%-trimmed leg mean > 0, its paired edge over the last
  12 months > 0 and it has at least 300 sessions with a pick. "Last 12 months" means
  sessions strictly after the same calendar day twelve months before the window's last
  session (month length clamped).

The pooled lane keeps **its own ledger** of confirmation windows, separate from the
per-symbol family. The first campaign confirms on 2024-01-02 to 2026-07-31. Later
campaigns cannot reuse that window: they need prospective data or a new cohort identity,
and a new protocol version.

**Status.** The stage logic (`run_stages`: discovery, dedupe, selection, one-shot
confirmation, with the ledger injected) exists and is what power check A runs, against
an in-memory ledger. The campaign runner, the journal-backed ledger, the genetic search
and search check B arrive in Part 1b. There is no `pooled campaign` command yet.

## Power check A

Power check A asks whether the stages can detect a planted edge and reject noise. It
reads discovery-window cells only.

- Each replicate fills the ten-year calendar with independent stationary block
  resamples (block mean 20) of whole discovery-window sessions, keeping the real
  eligibility mask, same-day dependence and labels.
- 200 null formulas and one planted formula are per-name AR(1) score fields with phi
  0.95. The planted formula's picks gain +delta R, for delta in {0, 0.05, 0.08, 0.10,
  0.12, 0.15}.
- The campaign's stages run unchanged with an in-memory ledger. At delta = 0 the planted
  formula is itself a null, so its confirmation is reported under that delta's
  `detection`, and `false_acceptance` counts the 200 nulls.
- 100 replicates. Each replicate is seeded only by the protocol seed and its index, so
  the result is identical for any `--workers` count.
- **Acceptance:** detection >= 80% at delta = 0.15 and false acceptance <= 5% at
  delta = 0. A failure writes `status: gate_failed` and stops Part 1 for redesign; no
  real-formula outcome is computed.

The result records the campaign protocol, cohort and cube SHA-256. Search check B (real
bars, planted expressions recovered by a family search) belongs to Part 1b.

## Commands

```bash
copilot alpha pooled power PROTOCOL --output DIR [--cache DIR] [--workers N]
copilot alpha pooled study ENTRY --power POWER_DIR --output DIR [--cache DIR]
```

- `PROTOCOL` is `config/research/pooled/campaign-v1.json`; `ENTRY` is a literature
  entry file. `--cache` defaults to `OUTPUT/raw`. `--workers` runs the power replicates
  in N processes.
- Both commands refuse an existing output directory, check the pinned cohort SHA-256
  before any provider access, and write `protocol.json` and `manifest.json` before the
  first provider call. `result.json` is written even on failure.
- `study` refuses unless `POWER_DIR/result.json` has `status: passed` for the same
  cohort and campaign protocol, and, once the cube is built, the same cube. The gate
  cannot be skipped by ordering mistakes.
- A study result reports the pass rule, cross-checks, diagnostics, dropped-pick counts,
  cube coverage and `picks.csv.gz`.

## What it never does

No database or registry write, no broker or Telegram access, no charge to the global
trial family, no shadow credit and no promotion. A passing entry or formula becomes
eligible for a capped paper probe, which needs its own spec (Part 2).

## Caveats

- **Survivorship.** Today's membership flatters long R. The paired edge cancels the
  shared part, but a formula that selects future survivors is still flattered. Absolute
  claims wait for a point-in-time listing and delisting source.
- **Cohort hygiene.** SPAC units, warrants and preferreds pass the symbol pattern; the
  price and liquidity gate removes most of them, not all.
- **Confirmation freshness** rests on the pooled ledger. Earlier studies read 2021-2026
  for other hypotheses, which is why their positive families are excluded. The paper
  probe is the forward check.
- **Entry.** The first hourly bar at or after 10:35 is the setup-study convention, not a
  fill model.
- **Liquidity** uses SIP raw bars, as PEAD did; the live dynamic screen uses IEX.
- **Power.** Discovery power is for paired edges of about 0.08-0.10R; PEAD-sized edges
  (about 0.055R) belong to the a priori catalog.
- **Sector concentration** is not capped in research; live cards keep their existing
  correlation caps.
