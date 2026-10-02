# Pooled alpha mining

The pooled lane evaluates **one formula across a frozen cohort of equities** and tests a
session-paired bracket-R edge with a session-block bootstrap. It is research only: no
registry, broker or Telegram access, no trial, shadow or promotion credit. Its only
database writes are the campaign's pooled-ledger research events.
Every manifest and result carries `"authorizes_promotion": false`.

This page is the contract for the lane. It follows the
[design spec](superpowers/specs/2026-09-28-pooled-alpha-mining-design.md) and the
[Part 1b addendum](superpowers/specs/2026-10-02-pooled-alpha-mining-part1b-design.md);
where the built code sharpens a spec, this page describes the code. Part 1a delivered the
cohort, label cube, formula selection, statistics, two literature entries, the campaign
protocol and its stage logic, power check A and the `alpha pooled power|study`
commands. Part 1b adds cohort v2 by liquidity screen, campaign protocol v2, the
per-family genetic search, the journal ledger, checks B and C and the campaign runner.

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

**Cohort v2** (`config/research/pooled/cohort-v2.json`, 452 names) replaces the random
300-name snapshot with the liquidity screen's top 400 plus the scan equities (the 127
`mega_caps` and `research_cohort` symbols, as in v1). The campaign v2 protocol pins it;
v1 below is the cohort the literature entries and the first power check ran on.

- 353 supported symbols. Sources: the config groups `mega_caps` and `research_cohort`
  (127 symbols, the scan universe's equities) and the 300 symbols of the prospective
  equity snapshot v2 ([equity universe](alpha-equity-universe.md)). The two overlap.
- Symbols outside `^[A-Z]{1,5}$` are listed under `excluded` (10, such as `BH.A` and the
  `.WS` and `.U` warrant and unit forms).
- `survivorship`: "current membership (2026-09); not point-in-time".

The v1 snapshot is a random hash sample of currently tradable names. It still contains
SPAC units, warrants and preferreds whose tickers match `^[A-Z]{1,5}$`; the per-session
price and liquidity gate below removes most of them. Only 130 of its 353 names were ever
eligible, which is why v2 exists.

Frozen identities (SHA-256 of file bytes):

| File | SHA-256 |
| --- | --- |
| `cohort-v1.json` | `966b67c53ad617d698089dd5b5786b05d4d5248f8844e3326d7addbe8d4ac5a9` |
| `campaign-v1.json` | `897fd8e59d912975de9377389a75d8954a747ab49dfa6ae368a30879059ca5ba` |
| `cohort-v2.json` | `c2c7941014932075110b33a7b853db7899f7b8fa43d5433d38b2e92bab1407df` |
| `campaign-v2.json` | `6470ca97a50f5dd0f0af89136590d63f942331c21d46c64387d9c7c8c7feaba1` |
| `high52-v1.json` | `9c3369278cb667db6016e93453cea7a55231234362718b3643d7bcce31626930` |
| `reversal-lowmax-v1.json` | `da39ef0b49e2af96ebbc328bbc9c59fc0802a449bc94e456b4f46b3fb9c18967` |

Entries and the campaign protocol pin the cohort and campaign SHA-256, so the campaign
is provably frozen before any literature result exists. The v2 protocol pins
`cohort-v2.json`; the literature entries keep their v1 pins and are not rerun.

### Cohort v2: the liquidity screen

`config/research/pooled/screen-v2.json` is committed before the screen reads any bars; its
SHA-256 is recorded in the cohort file. The rule:

- **Snapshot.** The prospective equity snapshot v2 (id
  `bf162c80350e390248813ad461c437787731f76efc15c5f6c5bf49693da9735f`, file SHA-256
  `41a7d11eb264cecf57f5e97b447cc17dc2f931bf4f624dcd733e3137a40f303c`, observed
  2026-09-17), `listed_non_etf_equity_candidate` members only. A snapshot file with a
  different hash is refused.
- **Window.** SIP raw daily bars for the 60 sessions ending 2023-12-29, the last session
  before the confirmation window, so the ranking does not favour names whose dollar
  volume grew with their 2024-2026 rallies. At least 55 of the 60 sessions must have
  bars, and the raw close on 2023-12-29 must be at least $10.
- **Rank.** Median raw close x volume over the window, descending, ties by symbol; the
  first 400 are taken (the cutoff was about $145M a day).

Exclusions, each recorded per symbol with its reason in `screen.csv`:

- **Symbol form:** anything outside `^[A-Z]{1,5}$` (the NYSE `.U`, `.WS` and `.A` forms).
- **NASDAQ fifth letter:** a five-letter symbol listed in the exchange directory's
  `nasdaqlisted` file (not NYSE or AMEX names) ending in `P`, `Q`, `R`, `U`, `V`, `W` or
  `Z` (preferred, bankruptcy, rights, units, when-issued, warrants, miscellaneous).
- **Security name** (case-insensitive): warrants; rights listed as such
  (`-\s*rights?\.?\s*$`; a bare `\brights?\b` would drop ADRs "representing the right to
  receive" shares); preferreds; fixed income (`%`, notes, debentures); funds; SPAC and
  structured units.

Kept kinds: MLP common units, ADRs and multiple share classes (`CMCSA`, `GOOGL`).

`copilot alpha pooled screen` writes `screen.csv` (every candidate with its exclusion
reason or rank, median dollar volume, last close and sessions with bars) and
`cohort.json`, which is copied unchanged to `cohort-v2.json`. It reads no returns and no
labels. Eligibility per session is unchanged: the screen only chooses candidates.

## Point-in-time eligibility

A decision at 10:35 New York on session D can use only completed sessions, so
eligibility is evaluated at `as_of` = D-1, the same rule as PEAD and the live
dynamic-universe screen (the shared `median_dollar_volume` and `static_reference`
functions are reused, not reimplemented):

- the **raw** (unadjusted) close at D-1 is at least $10;
- the 20-session median dollar volume of **raw** bars at D-1 is at least the 25th
  percentile of the same statistic over the configured static-universe equities;
- an ATR14 is available from adjusted daily bars through D-1.

The static reference set is every configured equity contract, the ETFs included, as in
the live gate and PEAD. Only a name with raw bars enters it, and the set the build
actually used is recorded (`static_used`, below).

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
arrays are read-only. The cube's coverage report holds counts and the build's
provenance, never a return statistic. The campaign opens its selection window only after
discovery leaves a finalist, and its confirmation window only after consumption is
recorded.

**Coverage fails closed.** The run stops with `status: failed` and the counts when the
static reference is missing on more than 2% of sessions, when more than 5% of eligible
cells in any year have no hourly decision data, or when the cube has no eligible cell or
no labelled cell at all.

### Acquisition, failures and provenance

One cube is shared by power check A and every study, so the run that builds it is strict
and leaves a record.

- **Bars read.** Adjusted and raw daily bars for every cohort and static symbol over
  2016-01-01 to 2026-09-01. Adjusted hourly bars for every symbol with at least one
  eligible session, over one span that depends only on the spec: the first decision date
  minus 7 days through 2026-09-01. The span does not depend on the symbol's own eligible
  sessions, so a cached hourly frame is never too short for a later build.
- **A fetch that raises fails the build.** Provider GETs are retried twice by the shared
  cache helper. If a fetch still raises, the build carries on and attempts every other
  symbol, so everything that can be cached is cached, and then stops with
  `bar acquisition failed for N symbols: ...; rerun to resume from the cache`, naming each
  symbol and reason. No cube is labelled or saved from bars known to be incomplete.
  Rerun with a new `--output` and the same `--cache`: only what failed is fetched.
- **An empty frame is not an error.** The provider has no bars for that symbol and
  request. It is recorded under `bar_failures` and the symbol is ineligible (no daily
  bars) or its cells are unlabelled (`no_hourly_bars`, which the coverage gate counts).
- **Provenance in the coverage report.** The cube's `coverage` is outside the cube hash,
  so recording how the labels were acquired never changes the cube's identity. The build
  stores there:
  - `bar_failures`: symbol to reason, the empty-frame cases;
  - `static_used`: the sorted static reference names that had raw bars, and
    `static_used_sha256`, the SHA-256 of that list joined by newlines;
  - `bars_sha256`: one SHA-256 over the sorted lines
    `symbol|timeframe|adjustment|<cache file name>` of every bar cache file the build
    read (a cache file's name is its content digest), and `bar_files`, their count.
- **Cache hit.** A later run that finds the cube loads it, checks its hash, cohort, spec
  and that the provenance is present, and reports the stored `bar_failures` and
  `static_used`, that is, what the build saw. It still loads the trading calendar and the
  cohort's adjusted daily bars, which a study scores on. It does not recompute
  eligibility and reads no raw or hourly bars.

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

The unit is `R_cost` at 5 bps per side; 0 bps is reported alongside (a study result's
`gross` key holds the paired edge and leg mean at 0 bps, on the same picks and bootstrap
draws). Only the 5 bps figures decide.

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
session, and the share of picks from each cohort source. Picks per sector are not
computed by the executor: the result document derives them from `picks.csv.gz` for the
symbols whose sector is known.

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
both entries: run on 2026-09-30 and failed (P2 and P3); see
[First runs](#first-runs).

Each entry is a single bootstrap test and inherits the size caveat described under
[what power check A does and does not show](#what-power-check-a-does-and-does-not-show):
both formulas load on persistent common factors, so their nominal 5% level is likely
somewhat optimistic. That is one reason a pass grants a capped paper probe and nothing
more. Both entries are evaluated on the whole decision window, which includes the
campaign's confirmation window; the
[literature overlap rule](#literature-overlap-rule) fixes what that
means for the campaign.

## Campaign protocol and stages

`config/research/pooled/campaign-v1.json` is frozen before any real-data run of either
entry, so their results cannot shape it. It remains the record of the literature runs and
power check A on cohort v1; it cannot run a campaign.

`campaign-v2.json` equals v1 except for the cohort pin, `version: 2` and three additions:

- **Per-family `windows`**: the set integer constants inside a family's expressions may
  mutate to (wrapper operators such as `ts_mean(x, w)` keep the global set 3, 5, 8, 10,
  14, 20, 30, 60). Without it a 252-session family would mutate into the reversal
  horizon.

  | Family | `windows` |
  | --- | --- |
  | high52 | 126, 189, 252 |
  | reversal | 3, 5, 10, 21 |
  | max_lottery | 5, 10, 21, 42 |
  | momentum_12_1 | 21, 63, 105, 126, 189, 252 |
  | momentum_12_7 | 84, 105, 126, 147, 168 |
  | range_location | 10, 20, 40, 60, 120 |
  | trend_slope | 10, 20, 40, 60, 120 |
  | abnormal_volume | 5, 10, 20, 50, 100 |
  | overnight_intraday | 5, 10, 21, 42 |
  | price_volume_corr | 5, 10, 21, 42 |
  | signed_volume | 5, 10, 20, 40 |
  | hl_spread | 5, 10, 21, 42 |

- **`power_search.seed`** (20261002): the random source for check B's hidden expressions.
- **`null_check`**: `{"replicates": 40, "max_false_acceptances": 2, "seed": 20261003}`.

The new fields are optional in the model, so v1 still loads with its hash; a protocol
without `null_check` cannot run a campaign.

- **Windows.** Discovery 2016-08-01 to 2021-12-31; selection 2022-01-03 to 2023-12-29;
  confirmation 2024-01-02 to 2026-07-31.
- **Budget and search.** The protocol fixes a budget of 200 formulas in total over 12
  families (52-week-high proximity, short-term reversal, MAX/lottery, 12-1 momentum, 12-7
  momentum, range location, standardized trend slope, abnormal volume, overnight versus
  intraday return, price-volume correlation, signed volume, high-low spread). Each
  family has seed expressions and a mutation-operator subset. Realized-volatility
  operators (`realized_vol`, `ts_std`, `ts_mad`) are forbidden, and a family or seed
  that uses one is rejected at load. The search splits the budget across the families
  and keeps mutation away from the forbidden operators (see
  [the genetic search](#the-genetic-search)).
- **Bootstrap.** Block mean 20 throughout; 1,000 draws for discovery, 1,000 for
  selection and 10,000 for confirmation.
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
per-symbol family ([the pooled ledger](#the-pooled-ledger)). The in-memory ledger used by
power check A, check C and the tests enforces the same single-use rule within one process.
The first campaign confirms on 2024-01-02 to 2026-07-31. The consumption is **lane-wide**:
a later campaign that overlaps a consumed interval is refused whatever its cohort, so a
new cohort identity cannot re-test 2024-2026. It needs prospective data after 2026-07-31
(a 12-month window around August 2027) or a spec that defines a disjoint cohort rule.

### The genetic search

`TypedGeneticSearch` (`research/alpha/search.py`) takes optional `seeds`, `operators` and
`windows`; the defaults are the module constants, so the per-symbol miner is unchanged.
The campaign builds one search per family (`research/pooled/genetic.py`):

- seeds are the family's seeds, operators its `mutation_operators`, constant windows its
  `windows`; the random seed is `search.seed * 100 + index` (the family's index in file
  order); the archive size is `search.archive_size`;
- the mutation fallback and empty-archive parents use the family's seeds, never the global
  list, which contains forbidden operators.

**Budget.** 200 formulas split evenly, the remainder going to the first families in file
order: 17 each for the first eight families and 16 each for the last four (17 x 8 + 16 x 4).

**Rejected without charge**, counted per reason and never evaluated: not dimensionless,
calls a forbidden operator (any letter case), fails to compile, or already proposed in this
campaign. After 200 consecutive rejections a family stops short and the shortfall is
reported.

**Charge before scoring.** Each formula is charged to the ledger before it is evaluated. A
formula that raises during scoring is recorded as an error, stays charged and is told to
the search with fitness minus infinity.

**Label blindness.** Search code receives a `CampaignWindows` object, never the
`LabelCube`. It checks the cube's cohort SHA-256 against the protocol, and `ScoreBook`
panels read only a view's `offset`, `sessions` and `eligible` arrays; fitness comes back as
a number. Scores are computed once per expression over the full calendar from adjusted
daily bars, read at D-1, and cached in a bounded in-memory cache.

### The pooled ledger

The ledger is held by `AlphaRepository` methods that write `ALPHA_RESEARCH` journal events
and projections under the alpha lock. There is no schema migration, and the pooled lane
never touches `family/all`. A `JournalLedger` adapter implements the campaign's `Ledger`
protocol from the worker thread. Five keys:

- `pooled/campaign/<campaign_id>`, where `campaign_id =
  pooled-campaign-v<version>-<protocol sha16>`: the protocol, cohort and cube SHA-256, the
  code revision, budget, journal scope, status and stage timestamps;
- `pooled/campaign/<campaign_id>/family/<family_id>`: that family's reserved budget,
  written before its search;
- `pooled/formula/<campaign_id>/<expression sha16>`: one charge per evaluated formula
  (family, canonical expression, nodes);
- `pooled/ledger`: cumulative formulas charged, campaigns and confirmation intervals
  consumed;
- `pooled/confirmation`: the lane-wide consumed intervals, each with the campaign id,
  cohort SHA-256 and frozen candidate hashes.

Rules:

- **Reservation before any read.** The campaign is reserved before it reads anything. Its
  immutable fields (protocol, cohort, cube, code revision, budget, journal scope) can never
  change, so a protocol runs at most once.
- **Journal scope.** "Lane-wide" means per journal scope (`<environment>/<execution
  mode>`, `WorkflowStore.scope`): a run against another database or mode would consume in a
  parallel ledger. The reservation pins the scope; `manifest.json` and `result.json` record
  the journal's scope, dialect and database name (never credentials). `campaign` takes a
  required `--journal-scope` and refuses, right after opening the journal and before any
  reservation, a scope that differs from the opened journal's or a journal that is not
  PostgreSQL.
- **Charges.** Charging the same expression again in a campaign is a no-op; a family's
  charges can never exceed its reservation; a crash keeps its charges.
- **Resume.** A rerun after a crash or a cancellation resumes only if the immutable fields
  match, the confirmation window has not been consumed and the campaign has not completed.
  A consumed or completed campaign is refused. The search is deterministic, so a resume
  evaluates the same formulas in the same order and charges nothing twice.
- **Confirmation.** Consumption is lane-wide and any overlap is refused. The campaign
  records it, with the frozen candidate hashes, before it reads the window.

`copilot alpha status` shows `pooled_ledger`.

### Literature overlap rule

**Decided on 2026-09-30, before any literature study was run. The campaign runner
implements it.**

The two literature entries are evaluated on 2016-08-01 to 2026-07-31, which includes the
campaign's confirmation window (2024-01-02 to 2026-07-31), and the campaign's `high52`,
`reversal` and `max_lottery` families contain the entries' formulas or close relatives.
Without a rule, such a formula would get a second look at 2024-2026. The rule:

- A campaign finalist whose **discovery-window pick set** overlaps a literature entry's
  discovery-window pick set at Jaccard >= the protocol's `dedupe_jaccard` (0.5) is
  reported as **"already tested by literature entry `<id>`"** (`already_tested_by` in its
  frozen document and the result). The entries' formulas are evaluated on the v2 cube;
  their 2026-09-30 verdicts stand and they are not rerun.
- It **keeps its Holm slot** in the confirmation family. It is not removed to make the
  other candidates' thresholds easier.
- It gains **no probe eligibility beyond that literature entry's own verdict**: if the
  entry failed, a campaign confirmation of the overlapping formula makes nothing eligible.

The rule is deliberately conservative. It may be tightened later; it cannot honestly be
relaxed once a literature result exists.

**Implementation.** The campaign computes the overlap before the search, from the
discovery view, and journals `already_tested` at freeze, before the confirmation is
consumed. The stage logic (`run_stages`: discovery, dedupe, selection, one-shot
confirmation, with the ledger injected) is what power check A and check C run against an
in-memory ledger; the campaign runs the same pieces with the journal ledger.

## First runs

Power check A and both literature entries ran on 2026-09-30. Power check A passed
(detection 0.98 at delta = 0.15, false acceptance 0.00 at delta = 0) after a first
attempt failed closed on an hourly read timeout; see the
[power result](alpha-pooled-power-2026-09-30.md). Both entries failed on P2 and P3: the
picks earned about +0.15R, the session-paired edge was about +0.01R; see the
[literature result](alpha-pooled-literature-2026-09-30.md). The cube holds about 85
eligible names per session and 130 of the 353 cohort names were ever eligible, so the
lane mostly mines the scan-universe equities.

## Power check A

Power check A asks whether the stages can detect a planted edge and reject noise. It
reads discovery-window cells only.

- Each replicate fills the ten-year calendar with independent stationary block
  resamples (block mean 20) of whole discovery-window sessions, keeping the real
  eligibility mask, same-day dependence and labels.
- 200 null formulas and one planted formula are per-name AR(1) score fields with phi
  0.95. The planted formula's picks gain +delta R, for delta in {0, 0.05, 0.08, 0.10,
  0.12, 0.15}.
- The campaign's stages run unchanged with an in-memory ledger. `detection` is the share
  of replicates in which the planted formula was confirmed; `false_acceptance` is the
  share of replicates in which at least one null formula was confirmed. At delta = 0 the
  planted formula is itself a null, so its confirmation is reported under that delta's
  `detection`, not under `false_acceptance`.
- 100 replicates. Each replicate is seeded only by the protocol seed and its index, so
  the result is identical for any `--workers` count.
- **Acceptance:** detection >= 80% at delta = 0.15 and false acceptance <= 5% at
  delta = 0. A failure writes `status: gate_failed` and stops Part 1 for redesign; no
  real-formula outcome is computed.

The result records the campaign protocol, cohort and cube SHA-256. Because this is the
run that builds the shared cube, it also records the build: `cube` (`sha256` and the
whole `coverage`, provenance included), `bar_failures` and `static_used`. Search check B
(real bars, planted expressions recovered by a family search) is described [below](#checks-b-and-c).

**Diagnostics.** A failed gate has to leave something to redesign from, so the result
also says where the planted formula stopped:

- `planted_stage_counts`: for each delta, how many replicates ended at each stage:
  `failed_discovery`, `not_carried` (passed the discovery gate but was deduplicated
  against a fitter formula or fell outside the top 5), `failed_selection`,
  `failed_confirmation`, `confirmed`.
- `replicates_detail`: one item per replicate, keyed by the curve's delta strings. Each
  holds the stage, the planted formula's discovery row (`passes`, `edge_t`, `edge_mean`,
  `leg_mean`, `positive_blocks`, `n_sessions`), whether it was carried, its selection
  row if reached (`kept`, `edge_mean`), its confirmation row if reached (`passes`,
  `holm_p`, `edge_mean`, `leg_ci90_low`, `trimmed_mean`, `recent_edge`, `n_sessions`)
  and `confirmed_nulls`, the ids of any null formula confirmed in that run. A non-finite
  statistic is stored as `null`.

The curve, the gate decision and the diagnostics are identical for any worker count.

### What power check A does and does not show

Power check A certifies the **stage machinery and the statistic under label-independent
null formulas**: AR(1) score fields that know nothing about the labels. Read its two
figures with these limits.

- **False acceptance does not certify factor-loaded formulas.** A reviewer simulation
  (20-session -1R/+3R brackets, 120 names, a true null) found that picks sharing a
  persistent exposure to a zero-mean common factor are rejected at about 6.5-6.8% at a
  nominal 5%: the standard deviation of t is about 1.16-1.26 rather than 1. Longer
  bootstrap blocks did not repair it. Momentum-like and 52-week-high-like formulas have
  exactly that kind of exposure, and the AR(1) nulls do not. The false-acceptance figure
  therefore says nothing about their size.
- **Detection is an upper bound.** The planted edge is a constant +delta on every pick;
  the planted formula meets a Holm family of about one; and the 2016-2021 market drift in
  the resampled labels feeds the leg-mean gates. A real edge of the same average size is
  noisier and harder to confirm.
- **The literature entries inherit the size caveat.** Each is a single bootstrap test of
  a factor-loaded formula. A pass grants only a capped paper probe, whose own risk cap
  and -4R kill bound the cost of a false pass.
- **Check C** (below) is the false-acceptance check with real DSL expressions on real
  bars, which this check cannot provide.

## Checks B and C

Both read discovery-window cells only, charge nothing, and gate the campaign. Both take
`--power A_DIR` and run on the cube check A built.

### Check B: search power

For seed i in 0..9:

1. Take family `power_search.families[i mod 3]` (reversal, range_location,
   abnormal_volume).
2. Draw a hidden expression with `random.Random(power_search.seed * 1000 + i)`: one or two
   mutations (equal odds), using that family's operators and windows, of one of its seeds
   chosen uniformly. Redraw until it compiles, is dimensionless, is not one of the family's
   seeds and has at least 400 discovery sessions with a pick.
3. Add delta = 0.15R, at both cost levels, to the hidden expression's discovery picks
   (`LabelCube.with_shift`).
4. Run that family's real search (its campaign seed and budget) on the shifted discovery
   view, applying the discovery gate and dedupe.
5. The seed **recovers** when a formula passing the discovery gate has pick-set Jaccard
   >= 0.5 with the hidden expression's picks.

**Acceptance:** at least 8 of 10 recovered (`power_search.min_recovered`). The result
reports each hidden expression, the best Jaccard and where the search stopped.

Check B certifies recovery of edges **near the family seeds**, which is the only place a
16-17 formula budget can search. It does not show that the search finds edges far from them.

### Check C: real-formula false acceptance

For replicate r in 0..39, with an RNG seeded `[null_check.seed, r]`:

1. **Null panel.** Stationary-block-resample whole discovery sessions (block mean 20) onto
   the full calendar, as check A does. Then, at each cost level, replace every labelled R
   with `R - mu_name + mu_all` (each mean over eligible, labelled discovery cells). Each
   cross-section is a real session, so names keep their real co-movement and factor
   exposures; each name's mean is the cohort mean, so no formula has a true edge. The leg
   mean keeps its real level, so the leg gates behave as they will on real data.
2. **Campaign.** Run the full campaign unchanged: the real per-family search with the
   protocol's seed and budget, discovery, dedupe, selection, and confirmation with an
   in-memory ledger. Scores use real bars; bars are inputs to scoring, not outcomes.
3. **Count.** The replicate is a false acceptance if any formula is confirmed.

**Acceptance:** at most 2 of 40 false acceptances (`null_check.max_false_acceptances`,
5%). Check C reports, without gating on them, the rate with its Clopper-Pearson 95% upper
bound, stage counts per replicate, and the **discovery t of each family's seed expressions
across the 40 replicates (mean and standard deviation)**. Seeds are evaluated in every
replicate before any selection, so their t values are an unselected null sample. A standard
deviation well above 1 means the bootstrap understates the variance of persistent-exposure
formulas, the issue the final review of Part 1a simulated. Results are identical for any
`--workers` count.

Check C covers the size problem only. A formula that tilts toward names that outperformed
because they survived would still be flattered on real data; the demeaned nulls carry real
co-movement but no persistent name effects.

Demeaning removes each name's **raw** mean over its labelled discovery cells, not its
session-relative mean (its R minus each session's cross-section). A name labelled mostly in
weak or strong sessions keeps a residual offset against the sessions it trades in. That
offset is an edge the search can find, so the null panels hold slightly more apparent edge
than an exact null: false acceptances are, if anything, overstated. The check is
conservative.

Checks B and C also report `errors_total`, the formulas whose evaluation raised (B per seed,
C per replicate). It must be 0 before a campaign: a systematic evaluation error would charge
the campaign's budget for formulas that are never scored.

## Commands

```bash
copilot alpha pooled screen RULE --snapshot SNAPSHOT --output DIR --cache DIR
copilot alpha pooled power PROTOCOL --output DIR --cache DIR [--workers N]
copilot alpha pooled study ENTRY --power POWER_DIR --output DIR --cache DIR
copilot alpha pooled search-power PROTOCOL --power A_DIR --output DIR --cache DIR
copilot alpha pooled null-check PROTOCOL --power A_DIR --output DIR --cache DIR [--workers N]
copilot alpha pooled campaign PROTOCOL --power A_DIR --search-power B_DIR --null-check C_DIR --output DIR --cache DIR --journal-scope SCOPE [--recover]
```

- `PROTOCOL` is `config/research/pooled/campaign-v1.json` for `study` and the literature
  runs, and `campaign-v2.json` for `search-power`, `null-check` and `campaign`; `ENTRY` is
  a literature entry file. `--workers` exists on `power` and `null-check` and runs the
  replicates in N processes.
- `--cache` is **required** on every command. For `power`, `study`, `search-power`,
  `null-check` and `campaign` it must be the same directory: it holds the bars and the one
  cube, and each later command passes its gate only on the cube the power check built.
  For `screen` it holds the screen's batched raw bars.
- **Cache hit required.** `search-power`, `null-check` and `campaign` never build a cube:
  without the cached cube for this cohort and spec they fail with `no cached cube ...; run
  alpha pooled power first`, before any provider access.
- Progress goes to stderr. A build reports the trading calendar, daily bars every 25
  symbols, eligibility start and end (and every 100 sessions), hourly bars every 25
  symbols, and the cube build start and end with its cell counts (and every 100
  sessions). A cache hit reports the calendar, one cache-hit line and the daily bars as
  they load. `power` then reports each finished replicate.
- A provider fetch that raises fails the run after every symbol was attempted; rerun
  with a new `--output` and the same `--cache` to resume (see
  [acquisition](#acquisition-failures-and-provenance)).
- Every command refuses an existing output directory, checks the pinned cohort SHA-256
  (or the screen's snapshot hash) before any provider access, and writes its manifest
  (`protocol.json` and `manifest.json`) before the first provider call. `result.json` is
  written even on failure.
- `study` refuses unless `POWER_DIR/result.json` has `status: passed` for the same
  cohort and campaign protocol, and, once the cube is built, the same cube. The gate
  cannot be skipped by ordering mistakes.
- A study result reports the pass rule, the 0 bps figures (`gross`), cross-checks,
  diagnostics, dropped-pick counts, the cube's SHA-256 and coverage, the build's
  `bar_failures` and `static_used` as stored in that coverage, and `picks.csv.gz`.
- **Gate binding.** `campaign` refuses to start unless checks A, B and C each passed for
  this cohort, protocol and one cube, at the same clean code revision as the running
  command. A dirty or unknown revision is refused, and so is any uncommitted or untracked
  file under `agentic_trader`, `config` or `tests` (`git status --porcelain`; `git describe
  --dirty` misses untracked files). The executor also refuses, before reserving anything,
  non-passed gates, gates without a cube, gates on different cubes, a cohort mismatch and a
  v1 protocol. `--journal-scope` must name the opened PostgreSQL journal's scope (see
  [the pooled ledger](#the-pooled-ledger)).
- **Preflight.** After loading the cube and before any family is reserved, the campaign
  computes, label-blind, every family's seed panels on the discovery window and one wrapped
  expression per mutation operator (`op(first seed, first constant window)`). Nothing is
  selected, evaluated or charged. An exception, or a seed whose scores are entirely NaN
  over the discovery window, fails the campaign with zero charges and an error naming the
  expression; the reservation stays resumable.
- **Circuit breaker.** Five consecutive failed provider fetches stop acquisition (screen or
  cube build) with a `failed` result; a rerun resumes from the cache.
- **Bar-file re-check.** On a cube-cache hit the runner re-checks the cube's `bars_sha256`
  against the bar files it reads; a mismatch fails closed.
- **Campaign statuses.** `no_finalists` (selection and confirmation stay unread),
  `no_confirmation_candidates` (confirmation unread and unconsumed), `none_confirmed`,
  `confirmed` (with a `probe_eligible` list that excludes `already_tested_by` formulas),
  `failed` (with the error; charges remain) and `cancelled`. Every result records
  `confirmation_consumed`, read back from the ledger on failure and cancellation.
- **`result.json` on every exit**, cancellation included. Ctrl-C or SIGTERM cancels the
  campaign. The search's worker thread cannot be cancelled, so the journal ledger is aborted
  (no further journal write starts; one in flight may finish) and the worker is waited for.
  Before consumption it stops at its next ledger call: nothing is consumed and a rerun
  resumes. After consumption it only computes, so the outcome survives. The result is
  `status: cancelled` with `confirmation_consumed`, the ledger is advanced to `failed`
  (`{"error": "cancelled"}`) and the command exits on the interrupt. Do not interrupt a
  campaign; this path only limits the damage.
- **The outcome is kept after consumption.** `outcome.json` is written as soon as the
  stages return, including on cancellation. If the confirmation was consumed but no outcome
  was kept (for example, an error inside the confirmation computation), rerun the same
  command with `--recover` and a new `--output`. Recovery runs only for this protocol's
  campaign when the ledger records the consumption and the campaign is not completed, at the
  reservation's code revision and on its cube (refused otherwise; the gate directories are
  still required and checked). It takes the frozen ids from the lane-wide
  `pooled/confirmation` record, each expression from its charge and the overlap marks from
  the freeze, recomputes the confirmation on the consumed window, writes `outcome.json` and
  `result.json` (`recovered: true`) and completes the campaign. It charges and consumes
  nothing.
- **Campaign outputs.** `protocol.json`, `manifest.json` (with the literature entries'
  SHA-256), `outcome.json`, `formulas.jsonl` (every proposal, including rejections and
  their reasons, with discovery statistics for evaluated formulas), `frozen/<id>.json`
  (each with `authorizes_promotion: false` and `already_tested_by`) and `result.json`. A
  confirmed, probe-eligible formula earns eligibility for a Part 2 spec and nothing more.
- `power`, `search-power` and `null-check` exit non-zero unless the gate passed. `study` and
  `campaign` exit non-zero only when the run failed (or was cancelled); a completed study
  whose entry failed its pass rule exits zero.

## What it never does

No registry write (the campaign's only database writes are the pooled ledger's research
events, never `family/all`), no broker or Telegram access, no charge to the global
trial family, no shadow credit and no promotion. A passing entry or formula becomes
eligible for a capped paper probe, which needs its own spec (Part 2).

## Caveats

- **Survivorship.** Today's membership flatters long R. The paired edge cancels the
  shared part, but a formula that selects future survivors is still flattered. Absolute
  claims wait for a point-in-time listing and delisting source.
- **Cohort hygiene.** Cohort v1 let SPAC units, warrants and preferreds through the
  symbol pattern; v2's screen excludes them by NASDAQ fifth letter and security name.
- **Survivorship is stronger in v2.** Membership requires being listed on 2026-09-17 and
  liquid in late 2023. Ranking before the confirmation window avoids selecting on
  2024-2026 dollar volume, but not on survival; check C does not cover a tilt toward
  survivors.
- **Confirmation freshness** rests on the pooled ledger. Earlier studies read 2021-2026
  for other hypotheses, which is why their positive families are excluded. The paper
  probe is the forward check.
- **Entry.** The first hourly bar at or after 10:35 is the setup-study convention, not a
  fill model.
- **Liquidity** uses SIP raw bars, as PEAD did; the live dynamic screen uses IEX.
- **Power.** Discovery power is for paired edges of about 0.08-0.10R; PEAD-sized edges
  (about 0.055R) belong to the a priori catalog. Power check A's detection figure is an
  upper bound and its false-acceptance figure does not cover factor-loaded formulas
  ([limits](#what-power-check-a-does-and-does-not-show)).
- **Literature entries and the campaign share 2024-2026.** The
  [overlap rule](#literature-overlap-rule) removes the second look.
- **Sector concentration** is not capped in research; live cards keep their existing
  correlation caps.
