# Pooled alpha mining Part 1b: cohort v2, gates B and C, the budgeted campaign

**Status:** design approved in conversation 2026-10-02; awaiting written-spec review.
**Extends:** [the Part 1 design](2026-09-28-pooled-alpha-mining-design.md) (Part 1a merged
in #103). That spec still governs everything this addendum does not change; where the two
differ, this one wins and says so.
**Scope:** research only. No live trading behaviour, schema migration, registry or
Telegram change. One PR. Live cards for a confirmed formula are Part 2, a separate spec.

## Why this addendum

Part 1a built the lane and ran both literature entries. Both failed: the picks earned about
+0.15R, but so did every eligible name, and the paired edges were +0.012R (t 0.81) and
+0.008R (t 0.52). The run also showed the v1 cohort is too narrow. Only 130 of its 353
names were ever eligible, about 85 per session, and the random 300-name snapshot supplied
1.8% of eligible cells. Power check A passed at that breadth: 92% detection at 0.10R and
68% at 0.08R.

Before the campaign spends its one confirmation window, this addendum:

- widens the cohort with a liquidity screen (cohort v2);
- freezes campaign protocol v2 on it;
- adds two gates the Part 1 spec left for 1b:
  - **B**, search power;
  - **C**, a false-acceptance check with real DSL formulas, which answers the final
    review's finding that check A does not certify factor-loaded formulas.

## Decisions

| Topic | Decision | By |
| --- | --- | --- |
| Universe | **Liquidity-screened cohort v2**: about the top 400 listed US equities by median dollar volume, plus the scan-universe equities. Membership is today's listings, recorded as a caveat. Rebuild the cube and rerun check A before any search. | Operator, 2026-10-01 |
| Part 1b design | Cohort v2, campaign v2, per-family search, journal ledger, checks A/B/C, campaign runner, one PR. | Operator, 2026-10-02 |
| Screen date | The screen ranks on the **60 sessions ending 2023-12-29**, the last session before the confirmation window, not on recent data. A recent screen would favour names whose dollar volume grew with their 2024–2026 rallies, which flatters exactly the window the campaign confirms on. | Ruling, 2026-10-02 |
| Check C method | Real DSL formulas from the real search, on synthetic panels of discovery sessions whose labels are **demeaned per name**. This keeps each formula's real factor exposures and removes any true edge. | Ruling, 2026-10-02 |
| Confirmation ledger | **Lane-wide**, not per cohort. Any later campaign that overlaps a consumed interval is refused, whatever its cohort. This is stricter than Part 1's per-cohort key, which would have let a new cohort identity re-test 2024–2026. | Ruling, 2026-10-02 |
| Literature entries | Not rerun on v2. Their 2026-09-30 verdicts (both failed) stand. The overlap rule evaluates their formulas on the v2 cube. | Ruling, 2026-10-02 |
| Gate binding | Checks A, B and C and the campaign must share the cohort, cube, protocol **and a clean code revision**. | Ruling, 2026-10-02 |

## Cohort v2

### Screen rule

`config/research/pooled/screen-v2.json` is committed before the screen reads any bars. Its
SHA-256 is recorded in the cohort file. Fields:

- `snapshot`: the prospective equity snapshot v2:
  - `snapshot_id` `bf162c80350e390248813ad461c437787731f76efc15c5f6c5bf49693da9735f`;
  - file SHA-256 `41a7d11eb264cecf57f5e97b447cc17dc2f931bf4f624dcd733e3137a40f303c`;
  - observed 2026-09-17.

  The screen refuses a snapshot file with a different hash.
- `candidate_classification`: `listed_non_etf_equity_candidate`, which is 6,721 members:
  active, tradable, NASDAQ/NYSE/AMEX, not an ETF or test issue.
- `exclude`, each with its reason recorded per symbol:
  - **Symbol form:** anything outside `^[A-Z]{1,5}$`. This drops the NYSE `.U`, `.WS`
    and `.A` forms.
  - **NASDAQ fifth-letter codes:** a five-letter symbol ending in `P`, `Q`, `R`, `U`, `V`,
    `W` or `Z` (preferred, bankruptcy, rights, units, when-issued, warrants,
    miscellaneous). Class letters such as `CMCSA` and `GOOGL` are kept.
  - **Security name patterns** from the exchange directory, case-insensitive. They cover:
    - warrants (`\bwarrants?\b`);
    - rights (`\brights?\b`);
    - preferreds (`\bpreferred\b`);
    - fixed income (`%`, `\bnotes?\b`, `\bdebentures?\b`);
    - closed-end funds (`\bfund\b`);
    - SPAC and structured units (`units?,? each consisting`, `tangible equity units?`,
      `corporate units?`, `- units?\.?\s*$`).

    MLP common units, ADRs and multiple share classes are kept.
- `feed`: `alpaca:sip`, adjustment `raw`.
- `window_end`: `2023-12-29`, `window_sessions`: 60, `min_sessions_with_bars`: 55.
- `min_price`: 10.0, applied to the raw close on `window_end`.
- `rank`: median of raw close × volume over the window's sessions, descending, with ties
  broken by symbol. Take the first `top`: 400.

### Screen command

`copilot alpha pooled screen RULE --output DIR --cache DIR`:

- refuses a non-empty output directory;
- writes `manifest.json` (rule SHA, snapshot hash, code revision) before any provider
  access;
- fetches the window's raw daily bars for every remaining candidate, 100 symbols per
  request (`fetch_daily_many`), cached under `--cache`;
- writes `screen.csv` (every candidate: exclusion reason or rank, median dollar volume,
  last close, sessions with bars) and `cohort.json`.

`cohort.json` is the proposed `config/research/pooled/cohort-v2.json`. It is copied
unchanged and committed. The screen reads no returns and no labels.

### Cohort file

`cohort-v2.json` keeps the v1 model (`id`, `version: 2`, `survivorship`, `sources`,
`excluded`, `symbols`):

- **Sources:**
  - `config_groups`: the configured `mega_caps` and `research_cohort` equities, as in v1.
  - `liquidity_screen` (new kind): the 400 screened symbols.
    - `identity` is the SHA-256 of `screen.csv`.
    - A required `screen` object records:
      - the rule file's path and SHA-256;
      - the snapshot id and hash;
      - `window_end`, `top`;
      - the candidate, excluded and ranked counts.

    `CohortSource.kind` gains this literal. `screen` is required exactly when the kind is
    `liquidity_screen`.
- **Symbols:** the sorted union of the sources. This is expected to be about 430–470
  names; the 300 random snapshot names of v1 are not carried over.
- **Survivorship:** "listed and active on 2026-09-17; ranked on 2023-10 to 2023-12 SIP
  dollar volume; not point-in-time".

Eligibility per session is unchanged: the Part 1 rule, decided from data available at the
time. The screen only chooses candidates.

## Campaign protocol v2

`config/research/pooled/campaign-v2.json`. Every field equals `campaign-v1.json` except
these:

- **Identity:** `version: 2`, a new title, and `cohort`/`cohort_sha256` pointing to
  `cohort-v2.json`.
- **Per-family mutation windows.** Each family gains `windows`, the set that integer
  constants inside its expressions may mutate to. Wrapper operators (`ts_mean(x, w)` and
  similar) keep drawing `w` from the miner's global set (3, 5, 8, 10, 14, 20, 30, 60).
  Without this, a 252-session family would mutate into the reversal family's horizon.

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

- `power_search.seed`: 20261002, the random source for check B's hidden expressions.
- `null_check`:
  `{"replicates": 40, "max_false_acceptances": 2, "seed": 20261003}`.

The model keeps v1 loadable with the same hash: the new fields are optional. A protocol
without `null_check` cannot run a campaign. `campaign-v1.json` stays as the record of the
literature runs.

## Cube v2 and check A

- **Cube:** built by the existing `alpha pooled power` path on cohort v2. The spec,
  window and bracket are unchanged.
- **Breadth:** the power result reports breadth (eligible names per session: mean,
  minimum, by year; eligible-cell share by source), so the widening is measured. Breadth
  is reported, not gated.
- **Check A:** acceptance is unchanged (detection ≥ 80% at 0.15R; false acceptance ≤ 5%
  at 0).
- **Runtime:** most of the about 300 new names need hourly bars, at about 50 seconds per
  name in one-year chunks, so the build runs for roughly four hours. It runs overnight,
  never across the 10:35 or 14:35 New York scans.
- **Cache:** reuses `~/agentic-trader-research/pooled-cache-v1`; cached frames are
  reused.

## Search

`TypedGeneticSearch` (`research/alpha/search.py`) takes optional `seeds`, `operators` and
`windows`. The defaults are today's module constants, so the per-symbol miner is
unchanged; a test pins the default sequence of proposals. The pooled campaign builds one
search per family:

- seeds = the family's seeds, operators = its `mutation_operators`, constant windows = its
  `windows`, and the seed is `[search.seed, family index]`;
- the archive size is `search.archive_size`;
- the mutation fallback and the empty-archive parents use the family's seeds, never the
  global list, which contains forbidden operators.

**Budget.** 200 formulas split evenly across the 12 families, with the remainder to the
first families in file order: 17 each for the first eight, 16 each for the last four.
Each family runs to its budget.

**Rejected without charge** (counted per reason; no evaluation):

- not dimensionless;
- calls a forbidden operator (`realized_vol`, `ts_std`, `ts_mad`), in any letter case;
- fails to compile;
- already proposed in this campaign.

After 200 consecutive rejections, the family stops short and the shortfall is reported.

**Label blindness.** The search code receives a `CampaignWindows` object, never the
`LabelCube`.

- `CampaignWindows` checks the cube's `cohort_sha256` against the protocol.
- Formula panels read only a view's `offset`, `sessions` and `eligible` arrays.
- Fitness comes back as a number.

Scores are computed once per expression over the full calendar from adjusted daily bars,
are causal (read at D−1), and are cached by expression in a bounded in-memory cache.

**Discovery refactor.** `_discovery` splits into a per-formula evaluation (edge, leg, block
means, gate, fitness, pick cells) and a pooled finish (gate, dedupe, carry). This lets the
search receive each fitness as it goes. `run_stages` is rebuilt on these pieces. A test
pins `run_stages` output on a fixture before and after the change, so check A's machinery
is unchanged.

## Ledger (journal, no migration)

The ledger is held by `AlphaRepository` methods writing `EventKind.ALPHA_RESEARCH` events and
projections under the alpha lock. The pooled lane never touches `family/all`. A
`JournalLedger` adapter implements the campaign's `Ledger` protocol from the worker thread.

**Keys:**

- `pooled/campaign/<campaign_id>`, where `campaign_id = pooled-campaign-v<version>-<protocol sha16>`.
  - Holds the protocol, cohort and cube SHA-256, the code revision, budget, status and
    stage timestamps.
  - It is reserved before the campaign reads anything; its immutable fields can never
    change.
  - A protocol therefore runs at most once. A rerun after a crash resumes only if the
    immutable fields match and the confirmation window has not been consumed. The search
    is deterministic, so a resume evaluates the same formulas in the same order.
- `pooled/campaign/<campaign_id>/family/<family_id>`: that family's reserved budget,
  written before its search.
- `pooled/formula/<campaign_id>/<expression sha16>`: one charge per evaluated formula
  (family, canonical expression, nodes), written **before** the formula is evaluated.
  - Charging the same expression again in the same campaign is a no-op.
  - A family's charges can never exceed its reservation.
  - A crash keeps its charges.
- `pooled/ledger`: cumulative formulas charged, campaigns, and confirmation intervals
  consumed.
- `pooled/confirmation`: **lane-wide** consumed intervals, each recording the campaign id,
  cohort SHA-256 and frozen candidate hashes. Any overlap is refused.

`copilot alpha status` prints `pooled/ledger`. `InMemoryLedger` follows the lane-wide rule
too.

## Check B: search power (gates the campaign)

`copilot alpha pooled search-power PROTOCOL --power A_DIR --output DIR --cache DIR`.
It reads discovery-window cells only and charges nothing. The procedure, for i in 0..9:

1. Take family `power_search.families[i mod 3]` (reversal, range_location,
   abnormal_volume).
2. Draw a hidden expression with an RNG seeded `[power_search.seed, i]`: one or two
   mutations (equal odds), using that family's operators and windows, of one of its seeds
   chosen uniformly. Redraw until it:
   - compiles;
   - is dimensionless;
   - is not one of the family's seeds;
   - has at least 400 discovery sessions with a pick.
3. Add δ = 0.15R, at both cost levels, to the hidden expression's discovery picks
   (`LabelCube.with_shift`).
4. Run that family's real search (its campaign seed and budget) on the shifted discovery
   view. Score each formula and apply the discovery gate and dedupe.
5. Seed i **recovers** when a formula passing the discovery gate has pick-set Jaccard
   ≥ 0.5 with the hidden expression's picks.

**Acceptance:** at least 8 of 10 recovered (`power_search.min_recovered`). A failure stops
the campaign for redesign.

The result reports each hidden expression, the best Jaccard and where it stopped. Check B
measures recovery of edges **near the family seeds**, which is the only place a 16–17
formula budget can search. It does not show that the search finds edges far from them.

## Check C: real-formula false acceptance (gates the campaign)

`copilot alpha pooled null-check PROTOCOL --power A_DIR --output DIR --cache DIR [--workers N]`.
It reads discovery-window labels only, charges nothing, and uses an in-memory ledger.
Scores use real bars over the full calendar; bars are inputs to scoring, not outcomes.

For replicate r in 0..39, with an RNG seeded `[null_check.seed, r]`:

1. **Null panel.** Stationary-block-resample whole discovery sessions (block mean 20) onto
   the full 2016-08 → 2026-07 calendar, as check A does (`synthetic_cube`). Then, at each
   cost level, replace every labelled R with:

   `R − μ_name + μ_all`

   - `μ_name` is that name's mean over its eligible, labelled discovery cells.
   - `μ_all` is the mean over all of them.

   Each cross-section is a real session, so names keep their real co-movement and factor
   exposures. Each name's mean is the cohort mean, so no formula has a true edge. The leg
   mean keeps its real level (about +0.15R), so the leg gates behave as they will on real
   data.
2. **Campaign.** Run the full campaign, unchanged:
   - the real per-family search with the protocol's search seed and budget;
   - discovery, dedupe, selection;
   - confirmation with an `InMemoryLedger`.
3. **Count.** The replicate is a **false acceptance** if any formula is confirmed.

**Acceptance:** at most 2 of 40 false acceptances (`null_check.max_false_acceptances`,
5%). A failure stops the campaign for redesign.

**Reported, not gated:**

- the false-acceptance rate with its Clopper-Pearson 95% upper bound;
- stage counts per replicate (survivors, carried, kept, reaching confirmation);
- the discovery t of each family's seed expressions across the 40 replicates (mean and
  standard deviation). Seeds are evaluated in every replicate before any selection, so
  their t values are an unselected null sample. A standard deviation well above 1 means
  the bootstrap understates the variance of persistent-exposure formulas, the issue the
  final review simulated.

Check C covers the size problem only. A formula that tilts toward names that outperformed
because they survived would still be flattered on real data; that remains a stated
caveat.

## Campaign runner

`copilot alpha pooled campaign PROTOCOL --power A_DIR --search-power B_DIR --null-check C_DIR --output DIR --cache DIR`.

**Refuses to start** unless:

- A, B and C each have `status: passed`;
- all three record the run's cohort, cube and protocol SHA-256;
- all three record the same clean code revision as the running command. A dirty tree is
  refused.

**Sequence:**

1. Write `protocol.json` and `manifest.json`.
2. Reserve the campaign in the ledger.
3. Load the cube from cache, re-checking its `bars_sha256` against the bar files it reads.
   A mismatch fails closed.
4. Run the 12 family searches in file order, each reserving its budget and charging each
   formula before evaluation.
5. Run the pooled discovery finish (gate, dedupe across families, carry 5).
6. Run selection (read once).
7. Freeze the kept candidates: write their documents (`Formula`: score, no filters,
   `k: 3`) and hashes to the output and the ledger.
8. Consume the lane-wide confirmation interval, then read the confirmation window.
9. Apply Holm and the confirmation gate.

**Overlap rule** (Part 1, amendment 2026-09-30). Evaluate each literature entry's formula
(`high52-v1`, `reversal-lowmax-v1`) on the v2 discovery view.

- A finalist whose discovery picks overlap an entry's picks at Jaccard ≥ 0.5 is marked
  `already_tested_by: <entry id>`.
- It keeps its Holm slot.
- It is never probe-eligible, because both entries failed.

**Statuses:**

- `no_finalists`: selection and confirmation are left unread.
- `no_confirmation_candidates`: confirmation is unread and unconsumed.
- `none_confirmed`.
- `confirmed`: with a `probe_eligible` list, which excludes `already_tested_by` formulas.
- `failed`: with the error. Charges remain.

**Outputs:**

- `formulas.jsonl`: every proposal, including rejections and their reasons, with discovery
  statistics for evaluated formulas;
- `result.json`;
- `frozen/<hash>.json`.

A confirmed, probe-eligible formula gets nothing more than eligibility for a Part 2 spec.

## Failure handling (additions)

- **Circuit breaker:** five consecutive failed provider fetches stop acquisition (screen or
  cube build) with a `failed` result. Each failing fetch already costs about 25 seconds of
  retries. A rerun resumes from the cache.
- **Progress:** progress lines name each failing symbol and its error.
- **Formula errors:** a formula that raises during scoring is recorded `error`, stays
  charged, and is told to the search with fitness −∞.

## Testing

**Screen:**

- each exclusion rule, including kept MLP units, ADRs and class shares;
- the 55-of-60 bars minimum;
- the $10 floor on `window_end`;
- the rank and tie-break;
- the snapshot-hash refusal;
- the manifest written before any fetch;
- the cohort file round-trip, including the `liquidity_screen` source validator.

**Protocol:**

- v1 still loads with its hash;
- the v2 `windows`/`null_check`/`power_search.seed` fields;
- forbidden operators in any letter case;
- validator gaps (deferred from 1a).

**Search:**

- default proposals unchanged (pinned);
- family seeds, operators and windows honoured;
- the fallback never returns a global seed;
- budget split 17×8 + 16×4;
- rejections uncharged and bounded;
- determinism for a fixed seed.

**Stages:** `run_stages` output unchanged on the fixture (pinned). Also deferred from 1a:

- the dedupe-then-carry order;
- the strict recent-window boundary;
- missing D−1 bar, filter bounds and `min_quantile`.

**Label blindness:** a spy panel proves search code never touches labels and that
confirmation labels are read only after consumption is recorded (deferred from 1a).

**Ledger:**

- SQLite unit tests:
  - reservation immutability;
  - idempotent charges;
  - the reservation cap;
  - lane-wide overlap refusal across cohorts;
  - resume rules;
  - `alpha status` output.
- The disposable PostgreSQL integration test (`--run-postgres`, `test_*` database):
  concurrent charges and concurrent confirmation consumption.

**Check B:** on a small fixture, a planted near-seed expression is recovered; fixed seeds
reproduce exactly.

**Check C:**

- demeaning (each name's mean equals the overall mean; the cross-section is unchanged
  apart from the shift);
- false acceptance counted;
- worker-count invariance.

**Campaign:**

- gate binding refuses each mismatch: status, cohort, cube, protocol, revision, dirty tree;
- the overlap rule marks and keeps the Holm slot;
- each status path;
- charges survive a crash mid-family.

**Runner (deferred from 1a):**

- bars digest re-check on a cube-cache hit;
- the circuit breaker;
- a full-span hourly test (not only the tail).

## Run order and runtime

All provider reads stay clear of the 10:35 and 14:35 New York scans.

1. **Screen:** about 55 batched requests, a few minutes. Commit `cohort-v2.json` and
   `campaign-v2.json`.
2. **Cube v2 and check A:** about four hours of hourly-bar acquisition, then about 40
   minutes for A. Overnight. This early run measures breadth, so the rest of the build
   proceeds against a known cube.
3. **Final branch revision, gates:** rerun A from the cube cache (about 40 minutes), then
   B (under an hour) and C (about an hour with several workers).
4. **Campaign:** about 15–30 minutes.

Every gate and the campaign run on the final, clean revision. Results are written up in
the same PR.

## Documentation (same PR)

- `docs/alpha-pooled-mining.md`: cohort v2 and the screen, per-family search, the ledger,
  checks B and C, the campaign command.
- `docs/alpha-pooled-checks-<date>.md`: breadth, check A on v2, check B, check C.
- `docs/alpha-pooled-campaign-<date>.md`: the campaign result.
- `docs/alpha-roadmap.md` and `CLAUDE.md`: the lane's status and results.

## Caveats (additions to Part 1)

- **Survivorship is stronger in v2.** Membership requires being listed in 2026-09 and liquid
  in late 2023. Ranking before the confirmation window avoids selecting on 2024–2026 dollar
  volume, but not on survival. The paired edge cancels what all names share. Check C does
  not cover a tilt toward survivors.
- **Liquidity rank** uses SIP raw daily dollar volume; the live dynamic screen uses IEX.
- **Check B** certifies near-seed recovery only.
- **Check C** demeans names over the discovery window; its nulls carry real co-movement but
  no persistent name effects.
- **Lane-wide confirmation.** After this campaign, the next one needs prospective data after
  2026-07-31, which reaches a 12-month window around August 2027, or a spec that defines a
  disjoint cohort rule.
