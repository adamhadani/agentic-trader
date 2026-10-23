# A priori insider cluster leg Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and run catalog entry `insider` v1: opportunistic officer/director cluster purchases on
cohort v3, labelled from the cached cohort-v3 cube, against 5 same-session controls matched on
liquidity and 20-session return, judged by P1–P4.

**Architecture:** A new frozen entry pins the pooled protocol `cohort3-insider-v1.json` (cube spec
and insider inputs) and the cube's content hash. `insider_events.py` turns cached Form 4 purchases
into one trigger per cluster episode. `insider_study.py` reads each event's cube cell, builds its
matched control from the same cube session, and applies the shared a priori statistics.
`insider_runner.py` loads everything through the pooled loaders with `require_cached=True`.

**Tech Stack:** Python 3.14, pydantic v2, numpy/pandas, click; pytest with the suite's network
guard.

**Spec:** `docs/superpowers/specs/2026-10-23-apriori-insider-leg-design.md`

## Global Constraints

- Research only: no database, registry, broker, Telegram or promotion credit;
  `authorizes_promotion: false` in the manifest and in every result.
- No migration (head `008`), no daemon change, no config change.
- Never change `cohort3-insider-v1.json`, the cube, or any existing catalog entry.
- `research/apriori/statistics.py` is used unchanged.
- The existing `tests/research/pooled/test_insider_fields*.py` pass unchanged after Task 1.
- Tests are synthetic only (no network; the socket guard applies).
- ruff strict (no C408, no PLC0415), mypy clean, `uv run pre-commit run --all-files` before
  staging.
- Implementers stage only; the controller commits.

## Review Focus

1. A filing date that is a non-session (an EDGAR-open market holiday): `D` must be the first
   session strictly **after** `F`, never `pos(F)` itself. Test in Task 2.
2. The cooldown starting from an ineligible trigger: a later in-episode day must not fire after a
   price recovery. Test in Task 2.
3. A control candidate that bought shares itself in the 126 sessions before `D`, including a
   routine purchase or a share-class sibling: it must be excluded. Test in Task 3.
4. Two events on the same `D`: neither may serve as the other's control. Test in Task 3.
5. A cube whose `sha256` or spec identity differs from the entry's: the run fails closed after the
   manifest and never computes statistics. Test in Task 4.

---

### Task 1: Frozen entry, catalog model, public plausibility filter

**Files:**
- Create: `config/research/apriori/insider-v1.json`
- Modify: `agentic_trader/research/apriori/catalog.py`
- Modify: `agentic_trader/research/pooled/insider_fields.py` (extract the filter)
- Test: `tests/research/apriori/test_insider_catalog.py`; the existing insider-fields tests stay
  unchanged

**Interfaces:**
- Produces:
  - `InsiderEvent(cluster_window_sessions: int ≥ 1, min_buyers: int ≥ 2, cooldown_sessions: int ≥ 1,
    routine_rule: Literal["same_owner_same_month_prior_year"])`;
  - `InsiderControl(matched_controls: int ≥ 1, match_return_sessions: int ≥ 1,
    liquidity_buckets: int ≥ 2, exclusion_sessions: int ≥ 1)`;
  - `InsiderEntry` with fields `id: Literal["insider"]`, `version`, `title`, `references`,
    `hypotheses`, `decision_rule`, `window: PeadWindow`, `pooled_protocol: str`,
    `pooled_protocol_sha256` (64 hex), `cube_sha256` (64 hex), `event`, `control`,
    `descriptive_hold_sessions: int ≥ 1`, `bootstrap: BootstrapSpec`, `pass_rule: PassRule`;
  - `LoadedInsiderEntry(entry, sha256, path)` and `load_insider_entry(path) -> LoadedInsiderEntry`;
  - `InsiderEntry.check_against(spec: CubeSpec) -> None`, which raises `ValueError` when
    `cooldown_sessions <= spec.bracket.max_hold_sessions` or `window.decisions` is not inside
    `spec.decisions`. It is called by the runner, because the cube spec lives in the protocol file;
  - in `insider_fields.py`, `plausible_transactions(transactions, day_ordinals, dollar_volume,
    max_trade_rel) -> tuple[list[InsiderTransaction], int]`, which returns the visible plausible
    transactions in input order and the implausible count. `_symbol_fields` calls it.

- [ ] **Step 1: Write the entry file** exactly as below. Use JSON with 2-space indent and a
  trailing newline:

```json
{
  "id": "insider",
  "version": 1,
  "title": "Opportunistic insider cluster purchases: two or more officers or directors buy within 21 sessions",
  "references": [
    "Seyhun (1986), Insiders' Profits, Costs of Trading, and Market Efficiency, Journal of Financial Economics",
    "Lakonishok and Lee (2001), Are Insider Trades Informative?, Review of Financial Studies",
    "Jeng, Metrick and Zeckhauser (2003), Estimating the Returns to Insider Trading, Review of Economics and Statistics",
    "Cohen, Malloy and Pomorski (2012), Decoding Inside Information, Journal of Finance",
    "Alldredge and Blank (2019), Do Insiders Cluster Trades with Colleagues?, Journal of Financial and Quantitative Analysis"
  ],
  "hypotheses": "LONG only, over decisions 2016-08-01..2026-07-31 on cohort v3 (config/research/pooled/cohort-v3.json, the 1,000-name liquidity band) with the bracket, eligibility and 15 bp decisive cost of the cached cohort-v3 label cube (10:35 New York decision, 2 x ATR14 stop, 3R target, 20-session time exit): an event fires on an SEC Form 4 filing date F when at least two distinct officers or directors have opportunistic open-market purchases of the issuer filed within the 21 sessions ending at F (a purchase is routine, and ignored, when the same owner filed a purchase of the same issuer in the same calendar month of the previous year; implausible trades above 10 x the 20-session median raw dollar volume are dropped first), at most once per name per 63 sessions; the trade is the cube cell on D, the first session strictly after F. Control = the mean of the 5 cube cells on the same session D in the event's dollar-volume tercile, without any qualifying purchase filed in the 126 sessions before D and not themselves events, nearest in 20-session return to D-1. P1: mean R_cost > 0 with the bootstrap ci90 lower bound > 0. P2: mean R_cost(leg) - mean R_cost(control) > 0 with the paired ci90 lower bound > 0, pairs keyed by D. P3: the 1%-trimmed mean > 0 and the mean over decisions from 2023-01-01 > 0. P4: at least 300 labelled events, at least 100 from 2023-01-01. Stationary session-block bootstrap over decision sessions, block mean 10, 2000 draws, seed 20261023; ci90 = [5th, 95th] percentiles. See docs/superpowers/specs/2026-10-23-apriori-insider-leg-design.md.",
  "decision_rule": "The leg becomes eligible for a capped live paper probe (its own spec) only if P1, P2, P3 and P4 all hold; otherwise it stays off and is recorded as failed. No threshold, window, buyer count, cluster window, cooldown or control parameter is revisited after the run; any change is a new version with a new, non-overlapping window. The 60-session hold and every split are descriptive diagnostics and decide nothing. Running this leg reads insider-conditioned outcomes over the pooled lane's confirmation slice, so the pooled insider fixed sets stay retired.",
  "window": {
    "decisions": ["2016-08-01", "2026-07-31"],
    "bars_through": "2026-09-01",
    "recent_from": "2023-01-01"
  },
  "pooled_protocol": "config/research/pooled/cohort3-insider-v1.json",
  "pooled_protocol_sha256": "969fc5df733e6147d9014082b2d83771cbee6a60a81d495383ea934cfa05a38d",
  "cube_sha256": "75dbb80a710f64ddf0d7ef34c70eceef4afa5fe1bd291a324543b8514906bdb6",
  "event": {
    "cluster_window_sessions": 21,
    "min_buyers": 2,
    "cooldown_sessions": 63,
    "routine_rule": "same_owner_same_month_prior_year"
  },
  "control": {
    "matched_controls": 5,
    "match_return_sessions": 20,
    "liquidity_buckets": 3,
    "exclusion_sessions": 126
  },
  "descriptive_hold_sessions": 60,
  "bootstrap": {"block_mean": 10, "draws": 2000, "seed": 20261023},
  "pass_rule": {"min_events": 300, "min_recent_events": 100, "trim_fraction": 0.01}
}
```

- [ ] **Step 2: Write the failing tests:**
  - `test_shipped_insider_entry_loads`: `load_insider_entry(REPO/config/research/apriori/insider-v1.json)`
    has `entry.id == "insider"` and `entry.event.min_buyers == 2`, and `sha256` equals `hashlib.sha256`
    of the file bytes;
  - `test_entry_pins_shipped_protocol`: the SHA-256 of `cohort3-insider-v1.json` equals
    `entry.pooled_protocol_sha256`;
  - `test_min_buyers_below_two_refused` (`ValidationError`);
  - `test_check_against_refuses_short_cooldown`: with `cooldown_sessions=20` and a spec whose
    `max_hold_sessions=20`, `check_against` raises;
  - `test_check_against_refuses_window_outside_cube`;
  - `test_catalog_entry_id_reads_insider` (`catalog_entry_id` returns `"insider"`);
  - `test_plausible_transactions_matches_lane`: on a synthetic symbol with one implausible
    purchase (> 10 × DV) and one filing after the last session, it returns the other
    transactions in order and `1`, and `build_insider_frames` output is unchanged (compare
    against the existing fixture helper in the insider-fields tests).
- [ ] **Step 3: Run them and see them fail:**
  `env -u VIRTUAL_ENV uv run pytest tests/research/apriori/test_insider_catalog.py -q`.
- [ ] **Step 4: Implement.**
  - Put the models beside the `Exdiv*` models, with the same `extra="forbid"`, `frozen=True` and
    `load_*` pattern.
  - `plausible_transactions` is the body of `_symbol_fields` from `visible = ...` through
    `seen = ...`, unchanged in behaviour.
- [ ] **Step 5: Run the new tests and `tests/research/pooled -q`.** Everything passes.
- [ ] **Step 6: Stage the files.** Do not commit. Report the SHA-256 of `insider-v1.json`.

### Task 2: Event builder

**Files:**
- Create: `agentic_trader/research/apriori/insider_events.py`
- Test: `tests/research/apriori/test_insider_events.py`

**Interfaces:**
- Consumes: `InsiderTransaction` (`research/insider_filings.py`), `InsiderEntry`.
- Produces:
  - `opportunistic_flags(purchases: Sequence[InsiderTransaction]) -> list[bool]`. A purchase is
    opportunistic when it is not routine. Implement it as the negation of
    `insider_fields._routine` (import it; do not copy).
  - `INSIDER_EVENT_COLUMNS = ("event_id", "symbol", "filing_date", "session", "decision_at",
    "buyers", "opp_dollars", "year")`. `event_id` is `f"{symbol}:{filing_date.isoformat()}"`;
    `session` is `D` as a `date`; `opp_dollars` is the sum over the trigger window.
  - `build_insider_events(transactions: Mapping[str, Sequence[InsiderTransaction]], trading_days:
    Sequence[date], identity_breaks: Mapping[str, date], entry: InsiderEntry, *, decision_time:
    time) -> tuple[pd.DataFrame, dict]`. It returns triggers that passed placement, identity and
    cooldown, and counts: `{"triggers": n, "suppressed_by_cooldown": n, "excluded": {reason:
    {year: n}}, "accepted_by_year": {...}}`. The reasons are `outside_window`, `identity_break`
    and `after_calendar` (no session after `F`). Share-class and cube gates are Task 3's.
  - `purchase_dates(transactions) -> dict[str, np.ndarray]`: per symbol, the sorted filing-date
    ordinals of every purchase (routine or not). Task 3 uses it for the control exclusion.

- [ ] **Step 1: Write the failing tests** with a 40-session synthetic calendar and helper-built
  transactions:
  - `test_two_owners_within_window_fire`: owners A and B file purchases 20 sessions apart, so one
    event fires on B's date with `buyers == 2`;
  - `test_owners_22_sessions_apart_do_not_fire`;
  - `test_same_owner_twice_does_not_fire`;
  - `test_routine_purchase_ignored`: A bought in the same month the year before, so only B is
    opportunistic and nothing fires;
  - `test_cooldown_suppresses_second_cluster`: a second cluster 30 sessions later is counted under
    `suppressed_by_cooldown`;
  - `test_cooldown_runs_from_ineligible_trigger`: a trigger outside the window still starts the
    cooldown, so a cluster day 10 sessions later inside the window does not fire;
  - `test_decision_is_first_session_after_filing`: a weekday non-session `F` (a holiday) gives
    `D` = the next session; a session `F` gives the session after it;
  - `test_identity_break_excludes_earlier_filings`;
  - `test_counts_by_reason_and_year`;
  - `test_deterministic_order`, sorted by `(session, symbol)`.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement it.**
  - Use positions from `np.searchsorted(day_ordinals, F, "left")`.
  - `D = trading_days[bisect_right(trading_days, F)]`.
  - `decision_at` comes from `pead_events.decision_at(D, decision_time)`.
- [ ] **Step 4: Run the tests and see them pass.** Stage.

### Task 3: Labels, matched controls, evaluation, executor

**Files:**
- Create: `agentic_trader/research/apriori/insider_study.py`
- Test: `tests/research/apriori/test_insider_study.py`

**Interfaces:**
- Consumes:
  - `LabelCube.window(...)` (fields `sessions`, `symbols`, `eligible`, `labelled`, `r_gross`,
    `r_cost`, `hit`, `holding`, `tiebreak`, `dollar_volume`);
  - `mean_test`, `paired_test`, `trimmed_mean`, which take frames with columns `session` and
    `r_cost`;
  - Task 2's outputs.
- Produces:
  - `InsiderLegInputs(cube: LabelCube, trading_days: tuple[date, ...], adjusted: Mapping[str,
    pd.DataFrame], transactions: Mapping[str, tuple[InsiderTransaction, ...]], identity_breaks:
    Mapping[str, date], issuer_of: Mapping[str, int], provenance: Mapping[str, Any])`. `issuer_of`
    maps each symbol to its issuer CIK. `provenance` holds the cube sha, spec identity, the
    protocol digest, the insider and actions digests, and the loader drops, including
    `implausible_size`.
  - `label_insider_events(events, inputs, entry) -> tuple[pd.DataFrame, pd.DataFrame, dict]`,
    returning `(legs, controls, counts)`:
    - `legs` holds the event columns plus `r_cost`, `r_gross`, `hit`, `holding`, `ret20`, `bucket`,
      `dv`, `opp_rel` (`opp_dollars / dv`) and `hold60` (the descriptive close-to-close return
      from `D` to `D + 60`, or `NaN` when immature);
    - `controls` holds `pair_id`, `session`, `r_cost`, `r_gross` and `hold60` (each a mean over
      the 5);
    - `counts` adds the reasons `share_class_sibling`, `not_in_cube`, `not_eligible`,
      `unlabelled`, `no_return` (by year) and `unpaired`.
  - `evaluate_insider(legs, controls, entry) -> dict` returns `{"decision": "eligible_for_probe" |
    "failed", "criteria": {"P1": {...}, "P2": {...}, "P3": {...}, "P4": {...}}, "diagnostics":
    {...}}`, with the diagnostics listed in spec §6.
  - `execute_insider_study(loaded: LoadedInsiderEntry, directory: Path, *, build: Callable[[],
    Awaitable[InsiderLegInputs]], environment: dict) -> dict`, modelled on
    `execute_exdiv_study`:
    - it writes `protocol.json` and `manifest.json` before `build`;
    - it saves `events.csv`, `legs.csv`, `controls.csv` and `result.json` (through `save_frame`
      and `save_json_report`, with `_finite_json`);
    - any exception gives `{"status": "failed", "error": "Type: message", "authorizes_promotion":
      False}`.

- [ ] **Step 1: Write the failing tests** on a hand-built `LabelCube`: 30 sessions, 12 symbols,
  known `r_cost`, adjusted closes giving known `ret20`.
  - `test_leg_reads_cube_cell_at_decision_session`;
  - `test_not_in_cube_not_eligible_unlabelled_counted`;
  - `test_ret20_uses_close_d_minus_1_over_20_sessions_earlier`;
  - `test_bucket_is_dollar_volume_tercile_among_labelled_cells`;
  - `test_control_excludes_recent_buyer_including_routine_and_sibling`, where a name whose
    purchase was filed 100 sessions before `D` is not a candidate;
  - `test_same_session_events_never_control_each_other`;
  - `test_five_nearest_by_ret20_tie_broken_by_tiebreak`, with the expected symbols listed;
  - `test_unpaired_below_five_candidates`, where the leg row is kept and no control row exists;
  - `test_share_class_sibling_keeps_larger_dv`;
  - `test_planted_edge_passes_all_criteria`, where legs are +0.5R, controls 0R and n = 400 with
    150 from 2023 → `eligible_for_probe`;
  - `test_null_fails_p1_p2`, where legs equal controls at 0 ± noise → `failed`;
  - `test_p4_fails_below_floor`;
  - `test_executor_writes_manifest_before_build_and_fails_closed`, where `build` raises and both
    `manifest.json` and `result.json` (`status: failed`) exist;
  - `test_results_never_authorize_promotion`.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement it.**
  - Build the session lookup: cube session → row.
  - For candidate exclusion, take `purchase_dates` and test with `searchsorted` against
    `[ordinal(trading_days[pos(D) − 126]), ordinal(D))`.
  - `hold60` reads `adjusted["Close"]` at `D` and at `D + 60` sessions.
- [ ] **Step 4: Run the tests and see them pass.** Stage.

### Task 4: Runner, CLI dispatch, docs

**Files:**
- Create: `agentic_trader/research/apriori/insider_runner.py`
- Modify: `agentic_trader/cli/commands/alpha.py` (the `apriori-study` command)
- Modify: `docs/apriori-alphas.md` (add an `insider` v1 row marked "pending run", and an
  "Entry 3" section that summarises the spec in ≤ 15 lines)
- Test: `tests/research/apriori/test_insider_runner.py`, `tests/cli/test_alpha_apriori_cli.py`
  (or the existing apriori CLI test file)

**Interfaces:**
- Produces
  `build_insider_leg_inputs(entry: InsiderEntry, *, repo_root: Path, bars: BarSource, calendar:
  CalendarSource, cache_dir: Path, insider_filings_dir: Path, actions_file: Path, pace: Callable[[],
  Awaitable[None]], progress: Callable[[str], None] | None = None) -> InsiderLegInputs`. In order:
  1. read the protocol bytes and verify `pooled_protocol_sha256`; a mismatch raises before any I/O;
  2. load the protocol with `load_campaign_protocol`;
  3. call `entry.check_against(protocol.cube_spec())`;
  4. `load_cohort`;
  5. `build_cube_inputs(cohort, spec, ..., require_cached=True, insider=(protocol.insider_inputs,
     InsiderSources(...)))`;
  6. verify `cube.sha256 == entry.cube_sha256`, or raise `ValueError` naming both;
  7. reload the transactions with `load_insider_sources`, then apply `plausible_transactions`
     per symbol with the build's raw `DV` (`insider_fields._dollar_volume`, made public as
     `dollar_volume_series` if needed) and `max_trade_rel`;
  8. compute `identity_breaks(actions, symbols)`;
  9. build `issuer_of` from `symbol_ciks`;
  10. fill `provenance`.
- The CLI's `apriori-study` gains `--insider-filings` and `--corporate-actions` (paths, default
  `None`):
  - they are required when `catalog_entry_id == "insider"`, and the error says so;
  - they are refused for `pead` and `exdiv`;
  - for `insider`, `--cache` is required: the pooled cache, where the cube must already exist;
  - the summary echo is unchanged.

- [ ] **Step 1: Write the failing tests.**
  - Runner, with fake sources: monkeypatch `build_cube_inputs` and `load_insider_sources` to
    return fixtures.
    - `test_protocol_digest_mismatch_raises_before_io`;
    - `test_cube_sha_mismatch_raises`;
    - `test_short_cooldown_refused_by_check_against`;
    - `test_implausible_dropped_before_events`.
  - CLI:
    - `test_insider_requires_filings_and_actions`;
    - `test_filings_flag_refused_for_pead`;
    - `test_existing_output_refused`.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.** Follow the `exdiv` branch of the CLI and `exdiv_runner.py` for
  structure.
- [ ] **Step 4: Run the full suite**
  (`env -u VIRTUAL_ENV uv run pytest -q -p no:cacheprovider`), mypy, and
  `uv run pre-commit run --all-files`. Stage.

### Task 5 (controller): run and write up

- [ ] Final whole-branch review on the most capable model, then the fix wave.
- [ ] From the clean branch revision, outside the scan windows, run spec §10's command, then
  `docs/apriori-insider-<date>.md`.
- [ ] Update the catalog row.
- [ ] Add one CLAUDE.md sentence after the `exdiv` sentence.
- [ ] Update the memory.
- [ ] Open the PR.
