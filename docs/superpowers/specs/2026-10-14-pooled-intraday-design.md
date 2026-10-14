# Intraday pooled mining lane (protocol `pooled-intraday-campaign` v1) — design

The mining expansion the operator asked for on 2026-10-14 ("enhance the DSL, the universe of
symbols, make sure it runs on a reasonable cadence"), in the signal space they chose first:
**intraday bars**. Every daily-bar lane on the 452-name cohort is closed (pooled genetic v2/v3
parked on check B, fixed-set v4 no finalists, spread pairs v1/v2, ex-dividend). The pooled lane's
label cube is already built from **hourly SIP bars with a 10:35 New York decision**, so this lane
changes what a formula may read — hourly bars through the first completed bar of the decision
session — not how a pick is labelled. Research only: no registry, broker or Telegram access; a
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

```python
class ScoreRead(BaseModel, frozen=True, extra="forbid"):
    bars_before_decision: int = Field(ge=1)  # 1 = the last hourly bar whose close is at or before the decision instant


class IntradayCampaignProtocol(BaseModel, frozen=True, extra="forbid"):
    id: Literal["pooled-intraday-campaign"]
    version: int
    title: str
    rationale: str | None = None
    search_mode: Literal["fixed_set"]  # v1; a genetic mode needs check B and its own spec
    score_inputs: Literal["hourly"]
    score_read: ScoreRead
    (
        cohort,
        cohort_sha256,
        feed,
        bars_from,
        bars_through,
        windows,
        selection,
        bracket,
        universe,
    )
    (
        decision_cost_bps,
        coverage,
        formula_budget,
        families,
        excluded_families,
        search,
    )
    (
        complexity_penalty_per_node,
        dedupe_jaccard,
        bootstrap,
        discovery_gate,
        selection_gate,
    )
    confirmation_gate, power, null_check  # same component models and validators as CampaignProtocol
```

`identity` = SHA-256 of the sorted JSON document (own namespace by `id`). `cube_spec()` returns the
same `CubeSpec` as the daily protocol would for identical values, so the cached cube is reused.
`search_mode` is `fixed_set` only in v1 (`power_search` absent, check B not applicable).

### 3.2 Hourly score inputs — `research/pooled/formula.py`, `scoring.py`

- `prepare_hourly(hourly: Mapping[str, DataFrame]) -> dict[str, DataFrame]`: regular-session bars
  only (`regular_session_bars`, the labeller's filter), sorted, UTC `DatetimeIndex`, columns the DSL
  reads (`open, high, low, close, volume, vwap` when present). A symbol without bars is absent.
- `evaluate_prepared_hourly(expression, prepared, sessions, symbols, *, decision_time_et, bars_before_decision) -> np.ndarray`:
  evaluates the expression once per symbol over its whole hourly series (the DSL's `returns`,
  `open_gap`, `oc_spread`, `hl_spread` are bar-over-bar: on the session's first bar `open_gap` is
  the overnight gap and `oc_spread` the first-hour return; operator windows are in bars, seven
  regular bars per session), then for each session reads the value at the `bars_before_decision`-th
  last bar whose **close** (`index + 1h`) is at or before the decision instant on that session's New
  York date (`10:35` → the `09:30` bar). No bar → NaN. Nothing after the decision instant is ever
  read.
- `ScoreBook.hourly(hourly, sessions, symbols, *, decision_time_et, bars_before_decision, capacity)`:
  an alternate constructor; `panel()` and `formula()` keep their interface and label-blindness.
  Dimensionlessness (`require_dimensionless`) applies unchanged.

### 3.3 Runner and dispatch — `research/pooled/runner.py`, `campaign_run.py`, `power.py`, `null_check.py`, `cli`

- `CubeBuild` gains `hourly: Mapping[str, pd.DataFrame]` (the frames the build loaded; on a cube
  cache hit they are re-read from the bar cache without provider access). Memory: 452 names ×
  ~17,500 regular bars ≈ 8M rows; acceptable in the research process.
- A `score_book(protocol, built)` helper returns the daily or the hourly book by
  `protocol.score_inputs`; `power`, `null-check` and `campaign` use it. The protocol loader
  dispatches on `id` (`load_campaign_protocol(path) -> CampaignProtocol | IntradayCampaignProtocol`).
- Power check A's null formulas are drawn from the protocol's families (hourly seeds), as today.
- The manifests record `score_inputs`, `score_read`, the hourly history span and, per formula, the
  first discovery session with a finite score.

### 3.4 The fixed set — `config/research/pooled/intraday-v1.json`

Eight predeclared formulas, one per family, all dimensionless, read at the 09:30 bar (close 10:30)
of the decision session; decile selection as v4; literature-overlap rule as v4. Signs are the
long-side hypothesis.

| Family | Score | Hypothesis |
| --- | --- | --- |
| `first_hour_momentum` | `oc_spread` | first-hour return continues (intraday momentum, Gao–Han–Li–Zhou 2018) |
| `overnight_persistence` | `open_gap` | overnight return persists (Lou–Polk–Skouras 2019) |
| `overnight_vs_intraday` | `open_gap - oc_spread` | overnight strength not yet traded away intraday |
| `relative_volume` | `volume / ts_mean(volume, 140)` | first-hour volume shock (high-volume premium, Gervais–Kaniel–Mingelgrin 2001) |
| `range_expansion` | `hl_spread / ts_mean(hl_spread, 140)` | opening-range expansion |
| `range_location` | `(close - low) / (high - low)` | close near the first-hour high |
| `vwap_strength` | `(close - vwap) / close` | first bar closes above its VWAP |
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
- A symbol without hourly bars has NaN scores and is never picked; the manifest counts them.
- Nothing after the decision instant is read: a bar whose close exceeds the instant is excluded,
  and the test suite pins the 09:30/10:30 boundary and a late-opening session.
- Clean-revision and gate-binding refusals are unchanged.

## 6. Testing

Fakes only: `prepare_hourly` (regular bars only, tz, missing symbols); `evaluate_prepared_hourly`
(reads the 09:30 bar for a 10:35 decision; a 10:30-stamped bar is never read; a session with only
late bars is NaN; `bars_before_decision=2` reads the previous bar; values at the first bar of a
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
2. **Separate protocol model, not a new field.** Adding a defaulted field to `CampaignProtocol`
   would change every v1–v4 identity; a sibling model with its own `id` keeps the old hashes
   reproducible.
3. **Fixed set, not genetic.** Check B failed twice on daily inputs; a search on hourly inputs would
   need its own power demonstration. Eight predeclared formulas test the signal space honestly.
4. **Read the 09:30 bar, not the 10:30 one.** At 10:35 the 10:30 bar is open; its close is 11:30.
   `bars_before_decision` is explicit so a later protocol can read earlier bars.
5. **Scores may be NaN early in discovery.** The hourly cache starts seven days before discovery;
   140-bar windows need about 20 sessions. The manifest records each formula's first finite session;
   no refetch, no new cube.
6. **Retire the ETF32 job rather than reschedule it.** A job that never found a finalist is not
   cadence; campaigns run when a protocol is frozen and approved.
7. **Live parity deferred.** Research on historical SIP (operator's choice); a confirmed formula's
   probe spec must state its live feed and its 15-minute-delay consequences.
