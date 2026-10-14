# Intraday pooled mining lane (protocol `pooled-intraday-campaign` v1) — design

The mining expansion the operator asked for on 2026-10-14 ("enhance the DSL, the universe of
symbols, make sure it runs on a reasonable cadence"), in the signal space they chose first:
**intraday bars**. Every daily-bar lane on the 452-name cohort is closed (pooled genetic v2/v3
parked on check B, fixed-set v4 no finalists, spread pairs v1/v2, ex-dividend). The pooled lane's
label cube is already built from **hourly SIP bars with a 10:35 New York decision**, so this lane
changes what a formula may read — hourly bars through the first completed regular bucket of the decision
session (the 09:00 New York bucket, closing 10:00) — not how a pick is labelled. Research only: no registry, broker or Telegram access; a
finalist becomes eligible for a separately specified capped paper probe. Decisions in §8.

## 1. Why

- The DSL today sees adjusted daily bars read at D−1. Intraday effects (overnight-return
  persistence, first-hour momentum and reversal, opening-range expansion, volume shocks) are among
  the best-documented short-horizon regularities and have never been in reach of this desk's
  search. The live desk already trades hourly bars at the same 10:35 decision.
- The pooled lane's machinery (cohort v2 of 452 names, point-in-time eligibility, the hourly label
  cube with a 2×ATR stop / 3R target / 20-session hold, session-paired statistics, checks A and C,
  the one-use ledger) is built, reviewed and cached (`~/agentic-trader-research/pooled-cache-v1`,
  cube `9bedbf9b…`). Reusing it makes this a score-input extension, not a new lane.
- Historical SIP minute/hourly bars are available to the desk's keys (the entitlement gap is only
  the latest 15 minutes); the operator ruled "research first on historical SIP; live parity decided
  later".

## 2. Scope

**In this PR:**

1. `IntradayCampaignProtocol` (`id: "pooled-intraday-campaign"`, `version: 1`): the campaign
   protocol's component models plus `score_inputs: "hourly"` and `score_read` (the bar to read);
   its own SHA-256 identity. The existing `CampaignProtocol` and every v1–v4 hash are untouched.
2. Hourly score inputs: `prepare_hourly`, `evaluate_prepared_hourly`, and `ScoreBook.hourly(...)`
   with the same `panel` / `formula` interface as the daily book, so `power`, `null-check` and
   `campaign` run unchanged on the new book.
3. The runner keeps the hourly frames it loads for the cube (`CubeBuild.hourly`), and the pooled
   commands dispatch on the protocol's `id`.
4. The frozen fixed-set protocol `config/research/pooled/intraday-v1.json` (eight predeclared
   intraday formulas, one per family; checks A and C; same cohort, cube spec and windows as v4 so
   the cached cube is a hit).
5. Cadence: the weekly per-symbol ETF32 miner (not loaded in launchd; 62 runs, 0 finalists) is
   retired from `scripts/launchd.sh` (`install-miner` prints the retirement notice and installs
   nothing); mining runs as predeclared campaigns under operator approval, and the daemon's
   forward observers keep their own schedules.
6. Docs: `docs/alpha-pooled-mining.md` (new section "Intraday score inputs", commands), the
   roadmap desk item 5 (un-deferred), CLAUDE.md, `docs/production.md` (miner retirement), this
   spec and the plan; after the runs, `docs/alpha-pooled-intraday-v1-<date>.md`.

**Out of scope (named so they are not implied):** any change to labels, the cube, the bracket,
cohort or windows; a genetic search (check B's instability is unresolved; v1 is a fixed set);
event-conditioned DSL fields (second leg, its own spec); minute bars (hourly first; 15-minute
bars need a new cube and a 15-minute decision contract); a live intraday alpha, a probe or any
IEX/SIP live-parity decision; `features.py`; any runtime change.

## 3. Components

### 3.1 Protocol — `research/pooled/campaign.py`

`IntradayCampaignProtocol` is a **subclass** of `CampaignProtocol` (every field, validator and
method inherited) with three fields of its own:

- `id: Literal["pooled-intraday-campaign"]` (overrides the base's literal);
- `score_inputs: Literal["hourly"]`;
- `score_read: ScoreRead` where `ScoreRead(bars_before_decision: int >= 1)`; `1` reads the last
  hourly bucket whose close is at or before the decision instant.

`CampaignProtocol` gains a defaulted field `score_inputs: Literal["daily"] = "daily"` declared
`exclude=True`, so it never appears in a dump and the v1–v4 documents and SHA-256 hashes are
unchanged; the subclass narrows it to `"hourly"` and includes it in its dump.
The subclass's validator requires `search_mode == "fixed_set"` in v1 (a genetic mode on hourly
inputs needs check B and its own spec). `load_campaign_protocol` reads the document's `id` and
validates with the matching class, so every pooled command accepts either file; `LoadedProtocol`
keeps its `CampaignProtocol` type. `cube_spec()` is inherited: identical cube values give the cached
cube `f1c835a3…` on cohort v2.

### 3.2 Hourly score inputs — `research/pooled/formula.py`, `scoring.py`

- `prepare_hourly(hourly: Mapping[str, DataFrame]) -> dict[str, DataFrame]`: regular-session bars
  only (`regular_session_bars`, the labeller's filter), sorted, UTC `DatetimeIndex`, columns the DSL
  reads (the cached Alpaca frames carry exactly `Open, High, Low, Close, Volume`, lower-cased; any
  extra column is kept but no protocol field may need it). A symbol without bars is absent.
- `evaluate_prepared_hourly(expression, prepared, sessions, symbols, *, decision_time_et, bars_before_decision) -> np.ndarray`:
  evaluates the expression once per symbol over its whole hourly series (the DSL's `returns`,
  `open_gap`, `oc_spread`, `hl_spread` are bar-over-bar: on the session's first bar `open_gap` is
  the overnight gap and `oc_spread` the return of the first regular bucket; operator windows are in
  bars, seven regular buckets per session), then for each session reads the value at the `bars_before_decision`-th
  last bar whose **close** (`index + 1h`) is at or before the decision instant on that session's New
  York date (`10:35` → the `09:00` bucket, which closes 10:00). No bar → NaN. Nothing after the decision instant is ever
  read.
- **Bucket semantics.** The cached Alpaca hourly bars are clock-aligned: buckets open at 04:00,
  05:00, ..., 19:00 New York (extended hours included) and `regular_session_bars` keeps start hours
  9-15. The first regular bucket (09:00-10:00) therefore includes the opening auction and about 30
  minutes of pre-market trades. For a 10:35 decision the read is the 09:00 bucket; the 10:00 bucket
  (closing 11:00) is never read. It is the same bar `pead_study.decision_price` uses as the cube's
  reference price, and a test pins the equality. `open_gap` on that bucket is the return from the
  last regular bucket's close to the first trade of the 09:00 bucket.
- `ScoreBook.hourly(hourly, sessions, symbols, *, decision_time_et, bars_before_decision, capacity)`:
  an alternate constructor; `panel()` and `formula()` keep their interface and label-blindness.
  Dimensionlessness (`require_dimensionless`) applies unchanged.

### 3.3 Runner and dispatch — `research/pooled/runner.py`, `scoring.py`, `campaign_run.py`, `null_check.py`, `cli`

- `build_cube_inputs(..., load_hourly: bool = False)`: `CubeBuild` gains `hourly:
  Mapping[str, pd.DataFrame]` (empty unless requested). On a build the frames the cube used are
  kept; on a cube cache hit they are re-read from the bar cache (no provider access). The CLI
  passes `load_hourly=protocol.score_inputs == "hourly"` for `power`, `null-check` and `campaign`
  (`power` needs no book, but building with the flag keeps one code path).
- `score_book(protocol, built) -> ScoreBook` (in `scoring.py`) returns the daily book or
  `ScoreBook.hourly(built.hourly, ...)` by `protocol.score_inputs`; `campaign_run` and
  `null_check` use it instead of constructing `ScoreBook` directly. Literature entries are daily
  formulas, so an intraday campaign computes their overlap codes from a daily book on the same
  session x symbol grid.
- The null check's process pool must not pickle the hourly frames (hundreds of MB per worker). For
  a fixed-set protocol the only expressions a replicate scores are the seeds, so the parent computes
  their panels once and hands workers `book.freeze(protocol.seed_expressions)`: a `FrozenPanels`
  book with the same `panel`/`formula` interface that raises `ValueError` on any other expression.
  The frozen pool is used for every fixed-set protocol (daily and hourly); only a genetic daily
  protocol keeps passing `adjusted` and builds its own book per worker. Check A needs no book (its formulas are synthetic).
- Power check A is otherwise unchanged; the manifests record `score_inputs`, `score_read` and,
  in the campaign, each formula's first discovery session with a finite score and the number of
  cube symbols without hourly bars (`symbols_without_hourly` in `result.json`).

### 3.4 The fixed set — `config/research/pooled/intraday-v1.json`

Eight predeclared formulas, one per family, all dimensionless, read at the first regular bucket
(09:00, close 10:00) of the decision session; decile selection as v4; literature-overlap rule as v4. Signs are the
long-side hypothesis.

| Family | Score | Hypothesis |
| --- | --- | --- |
| `first_hour_momentum` | `oc_spread` | first-bucket return continues (intraday momentum, Gao–Han–Li–Zhou 2018) |
| `overnight_persistence` | `open_gap` | overnight return persists (Lou–Polk–Skouras 2019) |
| `overnight_vs_intraday` | `open_gap - oc_spread` | overnight strength not yet traded away intraday |
| `relative_volume` | `volume / ts_mean(volume, 140)` | first-bucket volume shock (high-volume premium, Gervais–Kaniel–Mingelgrin 2001) |
| `range_expansion` | `hl_spread / ts_mean(hl_spread, 140)` | opening-range expansion |
| `range_location` | `(close - low) / (high - low)` | close near the first-bucket high |
| `first_bucket_efficiency` | `(close - open) / (high - low)` | net move of the first regular bucket relative to its range (candlestick body-to-range ratio); replaces `vwap_strength`, because the cache has no VWAP |
| `intraday_high_proximity` | `close / ts_max(high, 140)` | near the 20-session intraday high (the 126/252-session daily variants are excluded by the overlap rule and the v4 history) |

Windows of 140 bars = 20 sessions × 7 regular bars. Realized-volatility operators stay forbidden.
Selection, gates, bootstrap, power and null-check parameters are v4's; cohort, cube spec and
windows are v4's (discovery 2016-08-01 → 2021-12-31, selection 2022-01-03 → 2023-12-29,
confirmation 2024-01-02 → 2026-07-31, bars through 2026-09-01), so the cached cube is a hit and
the confirmation window remains the lane's single unused one.

### 3.5 Cadence — `scripts/launchd.sh`, docs

`install-miner` and `run-miner` print "retired 2026-10-14: the per-symbol ETF32 miner produced no
finalist in 62 runs; mining runs as predeclared pooled campaigns under operator approval (see
docs/alpha-pooled-mining.md)" and exit 0 without generating or loading a plist; `uninstall-miner`
still removes a stale plist. `status` reports the miner as retired. The forward observers (daily
panel, PEAD probe, shadow) are the daemon's own schedules and are unchanged.

## 4. Runs and decision rule (pre-registered)

In order, each from `pooled-cache-v1`, each refusing an existing output: `alpha pooled power
intraday-v1.json` (check A on the hourly book; pass rule as v4), `alpha pooled null-check` (check
C), then, **with the operator's approval**, `alpha pooled campaign --journal-scope
production/alpaca:paper`. The campaign charges all eight formulas to the pooled ledger, applies the
discovery gate, and opens selection and confirmation only for finalists (v4's stage logic). A
confirmed formula becomes eligible for a capped paper probe under its own spec; `no_finalists`
closes the intraday fixed set on this window. Either way the result is documented.

## 5. Failure behaviour

- A protocol whose cube spec differs from the cached cube's is a cache miss and refused by
  `null-check`/`campaign` (as today) until `power` builds it.
- A symbol without hourly bars has NaN scores and is never picked; `result.json` counts them (`symbols_without_hourly`).
- Nothing after the decision instant is read: a bar whose close exceeds the instant is excluded,
  and the test suite pins the 10:00/11:00 close boundary and a late-opening session.
- Clean-revision and gate-binding refusals are unchanged.

## 6. Testing

Fakes only: `prepare_hourly` (regular bars only, tz, missing symbols); `evaluate_prepared_hourly`
(reads the 09:00 bucket for a 10:35 decision on cache-shaped, clock-aligned frames; the 10:00 bucket is never read; the score bar's close equals `decision_price`; a session with only
late bars is NaN; `bars_before_decision=2` reads the previous session's 15:00 bucket; every seed evaluates on a cache-shaped frame; values at the first bar of a
session equal the DSL's bar-over-bar semantics — `open_gap` is the overnight gap); `ScoreBook.hourly`
panel/formula interface and label-blindness; protocol identity, `id` dispatch and refusal of a
genetic mode; `CubeBuild.hourly` populated on build and on cache hit; the fixed-set file loads, all
eight scores are dimensionless, families unique, forbidden operators absent; `launchd.sh`
retirement message (shell test as existing script tests do, or a docs-only check if none exist).

## 7. Documentation

`docs/alpha-pooled-mining.md` ("Intraday score inputs", the protocol table row, commands),
`docs/alpha-roadmap.md` (desk item 5 un-deferred with this lane and the operator's decisions),
`CLAUDE.md` (two sentences), `docs/production.md` (miner retirement), results doc after the runs.

## 8. Design decisions

1. **Reuse the hourly cube; change the score inputs.** Labels, bracket, cohort, windows and the
   cached cube are v4's; the only new object is what a formula may read. Cost if wrong: none to the
   label contract; a rerun of checks A and C (hours, cached).
2. **A subclass, not a new field on the base.** A subclass with an excluded defaulted base
   field keeps v1–v4 dumps and hashes unchanged, and its own `id` adds fields only to the new
   documents while inheriting every validator, gate and method.
3. **Fixed set, not genetic.** Check B failed twice on daily inputs; a search on hourly inputs would
   need its own power demonstration. Eight predeclared formulas test the signal space honestly.
4. **Read the 09:00 bucket, not the 10:00 one.** At 10:35 the 10:00 bucket is open; its close is 11:00.
   `bars_before_decision` is explicit so a later protocol can read earlier bars.
5. **Scores may be NaN early in discovery.** The hourly cache starts seven days before discovery;
   140-bar windows need about 20 sessions. The manifest records each formula's first finite session;
   no refetch, no new cube.
6. **Retire the ETF32 job rather than reschedule it.** A job that never found a finalist is not
   cadence; campaigns run when a protocol is frozen and approved.
7. **Live parity deferred.** Research on historical SIP (operator's choice); a confirmed formula's
   probe spec must state its live feed and its 15-minute-delay consequences.
