# Pooled alpha mining: one formula across the cohort (Part 1)

**Status:** design approved in conversation 2026-09-28; awaiting written-spec review.
**Scope:** research only. No live trading behaviour, schema migration, registry or
Telegram change. Part 1 ships as two PRs under this spec (1a, then 1b). Live cards
for a passing formula are Part 2, a separate spec, built only if something passes.

## Why

The per-symbol miner cannot find an edge of the size real anomalies have:

- The scheduled ETF32 run on 2026-09-26 charged 512 trials and produced 0 discovery
  finalists. The median formula makes 14 trades per validation window, so its standard
  error is about 0.33R. The best-looking candidates (DSR 0.66–0.80, 6–10 trades) have
  in-sample Sharpe ≤ 0, validation Sharpe ≈ 2 and negative IC: selection noise.
- Qualification deflates by the global trial family, now 8,879 trials. At that count
  DSR ≥ 0.95 behaves like a t ≈ 5.5 hurdle, about 1.8R of mean profit per trade at
  14 trades. Planted-signal power was 0/64
  ([power diagnosis](../../alpha-power-diagnosis-2026-09-18.md)).
- The one study that passed pooled one pre-registered hypothesis over 4,726 events and
  paired each session with a control
  ([PEAD result](../../apriori-pead-2026-09-26.md)). Pairing cut its standard error
  2.4-fold.

The [research report](../../alpha-pooled-mining-research-2026-09-28.md) (literature
pass, 2026-09-28) recommends the same shape for mining: evaluate one formula across a
wide cohort, test a session-paired bracket-R edge with a session-block bootstrap,
ration the number of formulas, and confirm a few frozen finalists once on a period the
search never read. Calibrated on PEAD's variance, that lane detects paired edges of
about 0.09–0.10R. Smaller edges remain the a priori catalog's job.

## Decisions already made (operator, 2026-09-28)

| Topic | Decision |
| --- | --- |
| Approach | A precomputed long-bracket **label cube** per (name, session); a formula selects cells from it. |
| Confirmation | The pooled lane keeps **its own ledger** of confirmation windows. The first campaign confirms on 2024-01-02 → 2026-07-31. Families that showed a positive result in a study that read 2021–2026 are excluded. The capped paper probe is the forward check (Part 2). |
| First run | **Both tracks**: two single-hypothesis literature entries, and one budgeted genetic campaign. |
| Universe | The 127 scan-universe equities plus the 300-name prospective cohort, eligible per session by the dynamic-universe liquidity rule. ETFs excluded. |
| Weekly miner | The per-symbol ETF32 launchd job is paused (disabled and unloaded, plist kept) so it stops charging 512 trials a week. Recorded in the roadmap by this workstream. |

## Cohort

`config/research/pooled/cohort-v1.json`, committed before any data is read. Its
SHA-256 is recorded in every manifest; a changed file is a new version.

- `symbols`: sorted union of the configured `mega_caps` and `research_cohort`
  universe groups (the scan universe's equities) and the 300 symbols of the
  prospective equity snapshot v2 (`snapshot_id` and artifact hash recorded as
  provenance). Symbols outside `^[A-Z]{1,5}$` are excluded and listed.
- `sources`: the config groups (with the hash of their sorted symbol list) and the
  snapshot identity.
- `survivorship`: `"current membership (2026-09); not point-in-time"`.

**Eligibility per session D** (decision at 10:35 New York on D), exactly the PEAD rule
with `as_of` = D−1, the last completed session:

- raw (unadjusted) `as_of` close ≥ $10;
- `median_dollar_volume(raw daily, as_of)` over 20 sessions ≥
  `static_reference(static raw daily, 0.25, as_of)` over the configured static-universe
  equities, both from `screeners/dynamic_universe.py`, reused, not reimplemented;
- an ATR14 is available from adjusted daily bars through `as_of`.

Eligibility is a property of the cube, independent of any formula, so every formula
is compared with the same control. A formula can pick only eligible names whose score
is finite (a name without enough history for the formula's lookback has no score). A
session with fewer than 30 eligible names is skipped and counted.

## Data

- Alpaca SIP. Daily bars, adjustment `all` (formulas, ATR) and `raw` (eligibility), for
  every cohort symbol and the static equities, from 2015-07-01 (52-week lookback before
  the first decision) through 2026-09-01. Hourly bars, adjustment `all`, for every
  symbol with at least one eligible session, over its eligible span, in one-year
  chunks. Reuse the setup study's `.npz` bar cache and range claims
  (`research/setups/runner.py`); default cache `~/agentic-trader-research/pooled-cache-v1`.
- Decisions: 2016-08-01 → 2026-07-31. Labels need 20 further sessions of bars.
- A symbol without bars for part of the window is ineligible there and counted by
  year; a symbol with no bars at all is listed.

## Label cube

For every eligible (symbol, session D), one **long** bracket, the PEAD bracket:

- decision price: `decision_price(hourly, D 10:35 NY)` (`research/apriori/pead_study.py`);
- ATR14: simple mean of the 14 true ranges ending D−1 (adjusted daily);
- levels: stop = price − 2×ATR, target = price + 3 × (2×ATR);
- label: `label_bracket(levels, decision_at, hourly, max_hold_sessions=20,
  cost_bps_per_side=c)` for c ∈ {0, 5} (`research/setups/labels.py`). 5 bps decides.
  `IMMATURE` cells are excluded and counted.

Stored per cell: eligibility, `r_cost_0bps`, `r_cost_5bps`, hit, `holding_sessions`,
decision price, ATR. The cube is written once as a hash-identified artifact
(`labels.npz` plus a JSON index of symbols, sessions and source-bar digests) and reused
by every Part 1 run on the same cohort, bracket and window.

**Window guard.** Code reads the cube only through `LabelCube.window(start, end)`. The
cube build reports coverage counts only, never a return statistic. The campaign runner
opens the selection and confirmation windows only at the stages below, and the
confirmation window only after its consumption is journaled.

## Selection rule (formula → picks)

A **formula** is a frozen document:

- `score`: a DSL expression (`research/alpha/dsl.py`) whose compiled units are
  **dimensionless** (price exponent 0, volume exponent 0); anything else is rejected;
- `filters` (optional): a list of `{expression, min_quantile?, max_quantile?}`. Each
  expression must also be dimensionless. On each session the quantile is taken across
  eligible names with a finite value; a name outside any bound is dropped;
- `k` = 3, direction long.

On session D:

1. Evaluate each expression per symbol on adjusted daily bars **through D−1**
   (`AlphaExpressionEvaluator`, single instrument, causal). The live scan at 10:35 sees
   the same completed bars; the in-progress session bar is never used.
2. Keep eligible names with a finite score that pass every filter.
3. Skip names the formula already holds: a name picked on an earlier session stays
   held through that pick's exit session (from `holding_sessions`), as the desk holds
   one position per name.
4. Rank by score, descending; break ties by SHA-256 of `symbol|D` ascending
   (deterministic, not alphabetical).
5. Pick up to k. A session with no pick contributes nothing.

## Statistics

Unit: `R_cost` at 5 bps per side (0 bps reported alongside).

- **Paired edge.** On each session with a pick,
  `d_D = mean R(picks on D) − mean R(all eligible cells on D)`; the control includes
  every eligible name, overlapping holds allowed. The statistic is the mean of `d_D`
  over sessions with a pick.
- **Leg mean.** The mean `R_cost` of all picks, weighting each session by its number of
  picks, as PEAD.
- **Confidence and p-values.** A stationary bootstrap over sessions with **block mean
  20** (the hold length), reusing `research/setups/study.py:_stationary_index_draws`
  and the one-sided helpers in `research/setups/baserates.py`. `ci90` is the
  [5th, 95th] percentile interval; a criterion holds when its lower bound is above
  zero. The one-sided p-value is the share of bootstrap means ≤ 0.
- **Cross-checks, reported, never decisive.** A calendar-time portfolio (each
  session's open picks' daily mark-to-market R, averaged) with Newey-West lag 19; two-way
  date × symbol clustered standard errors on the pick rows; the design effect
  `1 + (m̄−1)ρ̂` and effective sample size. A disagreement is reported.
- **Descriptive.** Target/stop/timeout shares, results by year and by liquidity
  tercile, picks per session, share of picks from the 300 cohort vs scan equities.

## Part 1a — literature entries (single hypotheses)

Two entries, each committed before any data is read, one test each at the PEAD bar.
They use the whole decision window (2016-08-01 → 2026-07-31), the same basis PEAD was
run on.

- `config/research/pooled/high52-v1.json` — **52-week-high proximity** (George & Hwang,
  2004): `score = close / ts_max(high, 252)`, no filter. Lookback 252 sessions.
- `config/research/pooled/reversal-lowmax-v1.json` — **one-month reversal among
  low-MAX names** (Jegadeesh, 1990; Bali, Cakici & Whitelaw, 2011, lottery-like losers
  keep underperforming): `score = -1.0 * roc(close, 21)`, filter
  `{expression: "ts_max(returns, 21)", max_quantile: 0.5}`. Lookback 22 sessions.

Each file records `id`, `version`, `title`, `references`, `hypothesis`, the formula,
`k`, windows (`recent_from: "2023-01-01"`), bracket and cost fields, bootstrap
(`block_mean: 20`, `draws: 2000`, seed) and the pass rule.

**Pass rule** (all must hold):

- **P1 edge.** Leg mean `R_cost` > 0 and its CI lower bound > 0.
- **P2 signal adds value.** Paired edge > 0 and its CI lower bound > 0.
- **P3 robustness.** 1%-trimmed leg mean > 0 (each tail) and the paired edge over
  decisions from 2023-01-01 > 0.
- **P4 sample.** At least 500 sessions with a pick, at least 150 of them from
  2023-01-01.

A passing entry becomes eligible for a Part 2 probe; a failing one is written up and
marked failed. Thresholds are never revisited after a run; a change is a new version.

## Part 1b — the budgeted genetic campaign

### Protocol (frozen in Part 1a's PR, run in 1b)

`config/research/pooled/campaign-v1.json` is committed in the Part 1a PR, **before any
real-data run of either literature entry**, so their results cannot shape it. Its
SHA-256 is recorded by every Part 1 manifest. Fields:

- `formula_budget: 200` total, split evenly across families (remainder to the first
  families in file order); `k: 3`; the bracket and cost fields above;
- windows: discovery 2016-08-01 → 2021-12-31, selection 2022-01-03 → 2023-12-29,
  confirmation 2024-01-02 → 2026-07-31. At each boundary, picks whose hold would cross
  into the next window are dropped (a 20-session purge);
- `families`: 12 entries, each with an id, rationale, seed expressions and the operator
  and field subset its search may use:
  1. 52-week-high proximity; 2. short-term reversal (5 and 21 sessions);
  3. MAX / lottery; 4. 12-1 momentum; 5. intermediate (12-7) momentum;
  6. range location; 7. standardized trend slope; 8. abnormal volume;
  9. overnight vs intraday return (open gap); 10. price–volume correlation;
  11. signed volume; 12. high–low spread (illiquidity);
- `excluded_families` with reasons: 20-session volatility (`vol_20`) and sector-relative
  momentum (both survived Holm in the setup-outcome study, which read 2021–2026);
  residual momentum and volatility-scaled momentum (factor and sector-panel studies read
  2021–2026 and reported partly positive results);
- search: the existing `TypedGeneticSearch` ask/tell (`research/alpha/search.py`),
  seeded with the family's seeds and restricted to its operator/field subset, seed,
  population and generation counts. Only dimensionless scores are evaluated; an invalid
  expression is rejected before evaluation and not charged;
- `complexity_penalty_per_node: 0.02` (t units), `dedupe_jaccard: 0.5`;
- `power_search_families`: the three family ids used by search check B (below), and
  its seeds;
- bootstrap: `block_mean: 20`, discovery and selection `draws: 1000`, confirmation
  `draws: 10000`, seeds;
- gates below.

### Discovery (2016-08-01 → 2021-12-31)

- Fitness (search ranking only): `t = paired edge / bootstrap SE` over the discovery
  window, minus the complexity penalty.
- Gate, on the unpenalized t: t ≥ 3.0; paired edge > 0 in at least 3 of 4 contiguous, equal-length time
  blocks of the window (purged at block boundaries); leg mean > 0; at least 400
  sessions with a pick.
- Dedupe: two survivors whose discovery pick sets have Jaccard overlap ≥ 0.5 are
  duplicates; the higher fitness is kept.
- Carry: the top 5 distinct survivors by fitness. None → the campaign ends with
  `no_finalists`; the selection and confirmation windows stay unread.

### Selection (2022-01-03 → 2023-12-29, read once)

- A candidate is kept when its paired edge is > 0 and at least half its discovery
  paired edge, with at least 150 sessions with a pick.
- The kept candidates (m ≤ 5) are **frozen**: their documents and hashes are written and
  journaled. None → `no_confirmation_candidates`; the confirmation window stays unread
  and unconsumed.

### Confirmation (2024-01-02 → 2026-07-31, used once)

1. Journal the consumption of the confirmation interval for this cohort, with the
   campaign id and the frozen candidate hashes, **before** computing anything on it.
   An overlapping, already-consumed interval refuses the run.
2. For each frozen candidate: one-sided bootstrap p-value of the paired edge
   (10,000 draws). Holm step-down at one-sided α = 0.05 over m (first step p ≤ 0.05/m;
   with m = 5, t ≈ 2.33).
3. A candidate **passes** when it survives Holm and: leg mean CI lower bound > 0;
   1%-trimmed leg mean > 0; paired edge over the last 12 months of the window > 0; at
   least 300 sessions with a pick.

A passing formula becomes eligible for a Part 2 probe. Later campaigns cannot reuse
this confirmation window; they need a new interval (prospective data) or a new cohort
identity, and they get a new protocol version.

### Ledger (journal, no migration)

`AlphaRepository` gains pooled-lane methods writing `EventKind.ALPHA_RESEARCH` events
and projections in the existing store. The pooled lane never touches `family/all`.

- `pooled/campaign/<campaign_id>`: protocol SHA-256, cohort SHA-256, budget, status and
  stage timestamps. Written (reserved) before any provider access; immutable fields
  cannot change.
- `pooled/ledger`: cumulative formulas charged, campaigns and confirmation tests.
- Charging: each family's budget is reserved before its search; every evaluated
  formula is recorded (expression, hash, family, discovery stats); the reservation can
  never be exceeded; a crash leaves the charges.
- `pooled/confirmation/<cohort_sha16>`: consumed intervals, each with campaign id and
  candidate hashes; overlap refused under the alpha lock.

`copilot alpha status` shows the pooled ledger's counts.

## Power checks (gates before real-formula outcomes)

**A. Statistical power (Part 1a; gates the literature runs and the campaign).**
Uses only discovery-window cells (2016-08-01 → 2021-12-31):

- Build 100 synthetic ten-year panels per planted edge by stationary block resampling
  (block mean 20) of whole discovery-window sessions, the real eligibility mask and
  labels, laid onto the real 2016-08 → 2026-07 session calendar.
- On each panel: 200 null formulas are per-name AR(1) score fields (φ = 0.95); one
  planted formula's picks get +δ added to their `R_cost`, δ ∈ {0, 0.05, 0.08, 0.10,
  0.12, 0.15}. Run the campaign's discovery, dedupe, selection and confirmation code,
  unchanged, with an in-memory ledger (the stages take the ledger as an injected
  dependency; power runs never write the journal).
- Report the detection rate (planted confirmed) and false acceptance (any null
  confirmed) per δ.
- **Acceptance:** detection ≥ 80% at δ = 0.15R; false acceptance ≤ 5% at δ = 0. A
  failure stops Part 1 for redesign; no real formula outcome is computed.

**B. Search power (Part 1b; gates the campaign's real run).**
On real discovery-window bars and cells: for 10 seeds, hide a random expression from
one of three named families' grammars, add δ = 0.15R to its picks' labels, and run
that family's search with its budget. A seed succeeds when a discovery survivor's pick
set has Jaccard ≥ 0.5 with the planted picks. **Acceptance:** ≥ 8 of 10. A failure
stops the campaign for redesign.

Both checks write their own manifests and results; neither charges the ledger or
produces a real-formula statistic.

## Code layout

`agentic_trader/research/pooled/`:

- `cohort.py`: cohort file loader (SHA-256, validation) and point-in-time eligibility
  (reusing `screeners/dynamic_universe.py`).
- `cube.py`: cube build (levels, `label_bracket`), artifact write/read, `LabelCube`
  with the window guard.
- `formula.py`: frozen formula document, dimensionless validation, score and filter
  evaluation per symbol, `select_picks` (steps 1–5).
- `stats.py`: paired edge, leg mean, bootstrap CIs and p-values (reused helpers),
  calendar-time Newey-West, two-way clustering, design effect, Holm (reuse
  `research/setups/study.py:holm`).
- `entry.py`: literature-entry model and pass rule P1–P4.
- `campaign.py` (1b): protocol model, family search, dedupe, the three stages, ledger
  calls.
- `power.py`: power check A (1a) and B (1b).
- `runner.py`: bar acquisition (shared cache helpers), manifest-before-provider-access,
  result assembly, as `apriori/pead_runner.py`.

CLI (`cli/commands/alpha.py`, `copilot alpha pooled …`), each refusing a non-empty
output directory and writing `protocol.json` and `manifest.json` (entry/protocol SHA,
cohort SHA, campaign protocol SHA, code revision, `authorizes_promotion: false`) before
any provider access, and `result.json` even on failure:

- `pooled power --output DIR` (A in 1a; `--search` for B in 1b);
- `pooled study ENTRY --power POWER_DIR --output DIR`;
- `pooled campaign PROTOCOL --power POWER_DIR --search-power SEARCH_DIR --output DIR`
  (1b; requires the ledger).

`study` and `campaign` refuse to start unless each given power result has
`status: passed` and matches the cohort, cube and campaign-protocol SHA-256 of the run,
so the gates cannot be skipped by ordering mistakes.

Every command accepts `--cache DIR` for the shared bar and cube cache.

## Failure handling

- Fail closed: a coverage failure (static reference unavailable on more than 2% of
  sessions, or more than 5% of eligible cells without hourly bars in any year) writes
  `status: failed` with the counts; nothing is tested on a gappy sample.
- Provider GETs retry within bounds; nothing mutates a broker.
- A formula that raises during evaluation is recorded as `error` (charged if it was
  reserved and evaluated), never retried with changes.
- Ledger writes run under the alpha lock; a violated invariant raises and stops the run.

## Documentation (same PRs; no docs-only PR)

- `docs/alpha-pooled-mining.md`: the lane's contract (cohort, cube, selection rule,
  statistics, ledger, windows, power checks).
- `docs/alpha-pooled-mining-research-2026-09-28.md`: the literature report, with links
  made repository-relative.
- Results: `docs/alpha-pooled-power-<date>.md` and `docs/alpha-pooled-<entry>-<date>.md`
  (1a), `docs/alpha-pooled-campaign-<date>.md` (1b).
- `docs/apriori-alphas.md`: index the two literature entries and link the lane.
- `docs/alpha-roadmap.md`: the pooled lane, the weekly-miner pause (2026-09-28) with its
  resume command, and each result.
- `CLAUDE.md`: one short paragraph pointing to the lane and its research-only status.

## Testing

- Cohort: SHA-256 identity, unknown fields rejected, eligibility on raw bars at `as_of`
  (no same-day data), the $10 floor, sessions below 30 eligible names.
- Cube: levels from ATR through D−1; delegation to `label_bracket`; immature cells;
  the window guard refuses unopened windows; the build reports no return statistic.
- Formula: dimensionless validation (a price-unit score is rejected); scores use bars
  through D−1 only (a planted future bar changes nothing); quantile filters; hold
  skipping; hash tie-break; k.
- Statistics: paired edge, leg mean and bootstrap on hand-sized frames with known
  answers; Holm; Newey-West and two-way clustering against small reference
  computations; design effect.
- Pass rules: P1–P4 and the campaign gates on synthetic cubes, each failing alone.
- Campaign: budget never exceeded; invalid expressions uncharged; dedupe; stage order
  (the selection window unread when discovery has no survivors; confirmation unread and
  unconsumed when selection keeps none); consumption journaled before any confirmation
  read; overlap refused.
- Ledger: SQLite unit tests plus the disposable PostgreSQL integration test
  (`--run-postgres`, `test_*` database): immutability, concurrent reservation, overlap
  refusal.
- Power: a small fixture where a planted +1R edge is always detected and a null is
  rejected; fixed seeds reproduce exactly.
- CLI: manifests before any provider call; refuses a non-empty output directory.

## Runtime estimate

About 350 symbols: two daily requests each (≈5 minutes), hourly bars for about 250
eligible symbols over ten years in one-year chunks (≈2,500 requests, 20–40 minutes).
Cube: about 500k eligible cells through `label_bracket` (≈10–20 minutes, cached).
Power check A: about 30 minutes. Each literature study: minutes. Campaign: 200 formulas
× about 350 symbols of DSL evaluation (≈15 minutes) plus bootstraps. Search check B:
10 family searches (≈1–2 hours).

## Caveats

- **Survivorship.** Today's membership flatters long R. The paired edge cancels the
  shared part; a formula that selects future survivors is still flattered. Absolute-R
  claims wait for a point-in-time listing and delisting source.
- **Confirmation freshness** is by the pooled lane's own ledger. Earlier studies read
  2021–2026 for other hypotheses; families with positive results there are excluded,
  and the paper probe is the forward check.
- **Hourly-bar entry** at the first bar at or after 10:35 is the setup-study
  convention, not a fill model.
- **Liquidity** uses SIP raw bars, as PEAD did; the live dynamic screen uses IEX.
- **Discovery power** is for paired edges of about 0.08–0.10R; PEAD-sized edges
  (≈0.055R) are for the a priori catalog.
- **Sector concentration** is not capped in research (no point-in-time sector for the
  300 cohort); picks per sector are reported where known, and live cards keep their
  existing correlation caps.

## Part 2 (outline only; not in this spec)

Built only for a passing formula: a `pooled` catalog kind enrolled as a capped paper
probe through the existing probe path; a live selector at the 10:35 suggestion scan that
evaluates the frozen formula over the cohort on completed daily bars through the prior
session (batched SIP daily bars, as the PEAD probe), applies the same eligibility and
hold-skipping rules, and emits at most one card per entry per session with the
`apriori_bracket_v1` bracket and a `session_count_v1` 20-session time exit; fail closed
on data gaps. It gets its own spec.
