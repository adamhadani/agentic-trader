# A Priori Run-up Leg Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze catalog entry `runup` v1 and build the offline development read of the uncertainty-conditioned pre-earnings run-up on cohort v3, 2016–2026, with matched controls, a pseudo-event negative control and a planted-effect check.

**Architecture:** Mirrors the `insider` leg: a pydantic entry pinned by SHA-256, a runner that assembles digest-checked inputs from the pooled cache (cube, adjusted daily, hourly), the Nasdaq page archive and the corporate-actions acquisition, an events module (legs, announcers, placebos, control candidates), a study module (labels via `label_bracket` with no target, `net_excess_r_v1`, P1–P4, placebo, planted, executor) and the `alpha apriori-study` dispatch.

**Tech Stack:** Python 3.14, pandas/numpy, pydantic, click, pytest.

**Spec:** `docs/superpowers/specs/2026-11-06-apriori-runup-design.md`

## Global Constraints

- Research only: no database, registry, broker, Telegram, shadow, probe or promotion credit; `authorizes_promotion` is always `false`.
- `protocol.json` and `manifest.json` are written before any provider access; every digest (pooled protocol, cohort, cube, pages, actions) is checked before its input is used, failing closed with both digests named.
- The decision vocabulary is exactly `survives_development`, `failed`, `inconclusive_underpowered`; never `eligible_for_probe`.
- Frozen values (copy verbatim): `lead_sessions` 10; `vol_window` 60 with at least 55 finite returns; terciles by `floor(3r / n)` over cube-eligible names ranked ascending, ties by the cube's `tiebreak`; `recent_previous_report_sessions` 40; `structural_exclusion_days` 5; stop 2.0 × ATR(14); no target; costs 5, 15, 25 bp, decisive 15; 5 matched controls; control coverage 130 sessions; placebo span ±63 sessions, quiet band `[D' − 10, D' + 20]`, seed 20261107; bootstrap block mean 10, 2,000 draws, seed 20261106; planted return 0.0063 of the decision price; P4 at least 1,000 legs and 300 from 2023-01-01; trim 1%; decisions 2016-08-01 → 2026-07-17, `bars_through` 2026-09-01, `recent_from` 2023-01-01.
- No local absolute paths in committed code, config or docs. Run output lives under `~/agentic-trader-research`.
- Tests use synthetic frames and cubes, no network, no provider.
- `uv run pre-commit run --all-files` passes. Implementers stage and report; the controller commits.

## Review Focus

1. A leg whose label walk ends on or after `E` (fill slipped a session) is excluded as `exit_on_or_after_report`, never kept. Test in Task 3.
2. Controls never include a name reporting inside its own hold, nor another leg on `D`, nor a name the archive does not cover. Test in Task 2.
3. Placebo draws are reproducible (same seed → same `D'`) and never put a release inside `[D' − 10, D' + 20]`. Test in Task 2.
4. `net_excess_r_v1` is `NaN` (not 0) when beta, cost inputs or the SPY hourly leg are missing, and P1/P3/P4 count only finite rows. Test in Task 3/4.
5. The planted check runs on placebo rows and is written before `result.json`; a planted failure yields `inconclusive_underpowered` whatever the legs show. Test in Task 4.

---

### Task 1: Catalog entry, frozen config and ledger row

**Files:**
- Modify: `agentic_trader/research/apriori/catalog.py` (`RunupEvent`, `RunupTrade`, `RunupControl`, `RunupPlacebo`, `RunupEntry`, `LoadedRunupEntry`, `load_runup_entry`)
- Create: `config/research/apriori/runup-v1.json`
- Modify: `docs/research-window-ledger.md` (row L43, α-table line, window read lists)
- Test: `tests/research/apriori/test_catalog.py` (or the existing catalog test file)

**Interfaces:**
- Produces: `RunupEntry` with `id: Literal["runup"]`, `version`, `title`, `references`, `hypotheses`, `decision_rule`, `window: PeadWindow`, `pooled_protocol: str`, `pooled_protocol_sha256`, `cube_sha256`, `event: RunupEvent(lead_sessions, vol_window, min_vol_returns, recent_previous_report_sessions, structural_exclusion_days)`, `trade: RunupTrade(stop_atr_multiple, atr_window)` (no target field), `costs_bps_per_side`, `decision_cost_bps`, `control: RunupControl(matched_controls, match_return_sessions, liquidity_buckets, coverage_sessions)`, `placebo: RunupPlacebo(span_sessions, seed, planted_return)`, `bootstrap: BootstrapSpec`, `pass_rule: PassRule`; `RunupEntry.check_against(spec: CubeSpec)` refuses a decision window outside the cube's decisions or an `atr_window` different from the cube bracket's; `load_runup_entry(path) -> LoadedRunupEntry(entry, sha256, path)`.

- [ ] **Step 1: Failing tests:** `runup-v1.json` loads and its SHA-256 is recorded; an unknown key, `decision_cost_bps` not in `costs_bps_per_side`, and a `bars_through` under 30 days after the last decision are refused; `check_against` refuses a spec whose decisions do not cover the window or whose ATR window differs.
- [ ] **Step 2: Run; expect failures.** `uv run pytest tests/research/apriori -q -k catalog`
- [ ] **Step 3: Implement** the models, then write `runup-v1.json` with the Global Constraints' values, `pooled_protocol` `config/research/pooled/cohort3-events-v1.json` and its SHA-256 (`shasum -a 256` of the file), and `cube_sha256` computed from the cached cohort-v3 cube without provider access: load `~/agentic-trader-research/pooled-cache-v1/cube-8c0d807aa47b19d3-1e167ed644b3becf.npz` with the pooled cube loader and read its content hash; confirm the file name matches `cube_cache_path` for cohort v3 and the events protocol's cube spec, and report the hash (the insider entry pins `75dbb80a…`; say whether it is the same cube). `hypotheses` and `decision_rule` state the spec's sections 4–7 in prose, like `insider-v1.json`; references: Gao, Hu and Zhang (pre-announcement run-up and uncertainty); Frazzini and Lamont (2007); Barber, De George, Lehavy and Trueman (2013); Johnson and So (2018).
- [ ] **Step 4: Ledger.** Add row L43 under a new "Pre-earnings run-up" heading (date 2026-11-06, lane `runup` v1, cohort v3, "outcomes, decisions 2016-08-01 → 2026-07-17; development only, cannot pass", result doc "pending", verdict "pending"), an α-table line ("Pre-earnings run-up, development read | `runup` v1 | No α: development only | `runup-v1.json`, committed with L43 | Development read; a prospective read declares its α in its own row"), add L43 to the read lists of W1, W2 and W6, and update the header's "holds reads L01–L42" sentence.
- [ ] **Step 5: Run tests; expect PASS.** Stage and report (include both SHA-256 values in the report).

### Task 2: Events, placebos and control candidates

**Files:**
- Create: `agentic_trader/research/apriori/runup_events.py`
- Test: `tests/research/apriori/test_runup_events.py`

**Interfaces:**
- Consumes: Task 1's `RunupEntry`; `LabelCube`/`CubeView` (`research/pooled/cube.py`: `eligible`, `dollar_volume`, `tiebreak`, `sessions`, `symbols`); `CalendarRow`; `CorporateAction`, `CorporateActionKind`, `between`; `dedupe_rows`; the insider study's `_buckets` rule (reuse it or move it to a shared place without changing its behaviour).
- Produces:
  - `RunupInputs` (frozen dataclass): `cube: LabelCube`, `trading_days: tuple[date, ...]`, `adjusted: Mapping[str, pd.DataFrame]`, `hourly: Mapping[str, pd.DataFrame]`, `reports: Mapping[str, tuple[CalendarRow, ...]]` (cohort symbols, deduplicated, date-sorted), `actions: tuple[CorporateAction, ...]`, `identity_breaks: Mapping[str, date]`, `market_daily: pd.DataFrame`, `market_hourly: pd.DataFrame`, `decision_time: time`, `provenance: Mapping[str, Any]`.
  - `RUNUP_EVENT_COLUMNS = ("event_id", "role", "pair_id", "symbol", "session", "report_date", "timing", "decision_at", "rv", "tercile", "atr", "ret20", "bucket", "dv", "past_move", "year")`; `role` is `leg`, `announcer` or `placebo`; a leg's `pair_id` is its own `event_id`; a placebo's `pair_id` is its leg's `event_id` and its `report_date`/`timing` are `None`.
  - `build_runup_events(inputs: RunupInputs, entry: RunupEntry) -> tuple[pd.DataFrame, dict]` — counts carry `reasons` and `reasons_by_year` for every exclusion of spec section 4, plus `placebo_reasons`.
  - `CONTROL_CANDIDATE_COLUMNS = ("pair_id", "role", "session", "control_symbols")`; `match_controls(events: pd.DataFrame, inputs: RunupInputs, entry: RunupEntry) -> tuple[pd.DataFrame, dict]` — controls for `leg` and `placebo` rows (spec section 6), `control_symbols` a comma-joined string of exactly 5 names; counts carry `unpaired_by_year` per role.

- [ ] **Step 1: Failing tests** on a synthetic cube (copy the construction helpers the insider study tests use) with about 12 symbols and 200 sessions: a report on a session yields a leg at `E − 10` only when its `rv` is in the top tercile, else an `announcer`; each exclusion reason fires once in a dedicated case (`non_session_report_date`, `outside_window`, `recent_previous_report`, `identity_break`, `structural_action_nearby` (a split fires it, a cash dividend does not), `not_eligible`, `no_rv` with 54 returns, `no_atr`); the tercile rule `floor(3r / n)` with a tie resolved by `tiebreak`; the placebo is reproducible for a fixed seed, lies within ±63 sessions, and no report of the symbol falls in `[D' − 10, D' + 20]`; a placebo that fails a gate is counted, not kept; control candidates exclude a name reporting in `[D, D + 10]`, another leg on `D`, a name outside the leg's dollar-volume or `rv` tercile and a name with no report in the 130 prior sessions, and pick the 5 nearest by 20-session return with `tiebreak` breaking ties; fewer than 5 candidates is unpaired. `past_move` is the mean absolute 2-session excess reaction of up to the last 4 prior reports (reaction session `E` for pre-market, `E + 1` otherwise), `NaN` with fewer than 2.
- [ ] **Step 2: Run; expect failures.** `uv run pytest tests/research/apriori/test_runup_events.py -q`
- [ ] **Step 3: Implement** per spec sections 4 and 6. Sessions and positions are the observed trading days, never frame rows; a missing bar is `NaN`.
- [ ] **Step 4: Run; expect PASS.** Stage and report.

### Task 3: Labels and net excess

**Files:**
- Create: `agentic_trader/research/apriori/runup_study.py` (labelling part)
- Test: `tests/research/apriori/test_runup_study.py`

**Interfaces:**
- Consumes: Task 2's frames and `RunupInputs`; `label_bracket`, `BracketArm`, `BracketHit` (`research/setups/labels.py`); `decision_price`, `utc_index`, `save_frame` (`pead_study`); `add_net_excess` (`pead_base_rate`).
- Produces: `RUNUP_ARM = BracketArm(name="runup_time_exit", target_r=None, trail=None)`; `label_runup(events: pd.DataFrame, candidates: pd.DataFrame, inputs: RunupInputs, entry: RunupEntry) -> tuple[pd.DataFrame, pd.DataFrame, dict]` returning `(labels, controls, counts)`: `labels` has one row per labelled event (`RUNUP_EVENT_COLUMNS` plus `decision_price`, `entry_price`, `entry`, `stop`, `entry_time`, `exit_time`, `hit`, `r`, `r_cost`, `r_cost_5bps`, `r_cost_15bps`, `r_cost_25bps`, `holding_sessions`, `beta`, `name_cost_bps`, `market_r`, `net_excess_r`, `last_day_excess`); `controls` has one row per paired event (`pair_id`, `role`, `session`, `r_cost`, `net_excess_r`, `control_symbols`), the means over the 5 control cells (a control cell that fails to label makes its pair unpaired, counted); each distinct `(symbol, session)` cell is labelled once.

- [ ] **Step 1: Failing tests** with synthetic hourly bars: a leg's time exit is the close of session `E − 1` (the 10th session from `D`) with no target even after a +5R rally; a stop touch exits at the stop; a gap through the stop fills at the open; `exit_on_or_after_report` when the decision session has no regular bar after 10:35; `missing_session_bars` when one held session lacks bars; `r_cost` at 15 bp equals `r_cost_15bps`; `net_excess_r` is `NaN` when SPY hourly bars are missing; `last_day_excess` is the adjusted `E − 2 → E − 1` return minus beta × SPY's over the same day (in R units) and `NaN` for placebos; the control row is the mean of its 5 labelled cells, and a shared cell is labelled once.
- [ ] **Step 2: Run; expect failures.**
- [ ] **Step 3: Implement.** Prepare each symbol's regular-session hourly frame once (as the cube builder does) rather than slicing per call.
- [ ] **Step 4: Run; expect PASS.** Stage and report.

### Task 4: Evaluation, placebo, planted effect and the executor

**Files:**
- Modify: `agentic_trader/research/apriori/runup_study.py`
- Test: `tests/research/apriori/test_runup_study.py`

**Interfaces:**
- Consumes: Task 3's frames; `mean_test`, `paired_test`, `trimmed_mean` (`statistics.py`, which read the `r_cost` column: pass frames with the tested unit renamed to `r_cost`); `save_json_report`, `_finite_json`.
- Produces: `evaluate_runup(labels: pd.DataFrame, controls: pd.DataFrame, entry: RunupEntry) -> dict` with keys `criteria` (`P1`–`P4`), `placebo` (`P1`, `P2`), `planted` (`P1`, `P2`, `detected`), `decision`, `diagnostics` (spec section 8); `planted_check(labels, controls, entry) -> dict`; `execute_runup_study(loaded: LoadedRunupEntry, directory: Path, *, build: Callable[[], Awaitable[RunupInputs]], environment: dict) -> dict`.

- [ ] **Step 1: Failing tests:** on synthetic labels, P1 uses `net_excess_r` of finite legs only and P2 uses paired `r_cost` minus the control mean; P3 and P4 thresholds at their boundaries; a placebo whose P2 holds makes the decision `failed` even when P1–P4 hold; a planted check that is not detected gives `inconclusive_underpowered`; all hold → `survives_development`; the planted shift is `0.0063 × decision_price / abs(entry - stop)` per row; the executor writes `protocol.json` and `manifest.json` before calling `build`, writes `planted.json` before `result.json`, records an exception from `build` as `status: failed` with the type and message, refuses a cube whose hash differs from `cube_sha256`, and never writes `eligible_for_probe`.
- [ ] **Step 2: Run; expect failures.**
- [ ] **Step 3: Implement** (diagnostics include the design effect: variance of per-session leg means × mean legs per session ÷ per-leg variance).
- [ ] **Step 4: Run; expect PASS.** Stage and report.

### Task 5: Runner, CLI dispatch and catalog docs

**Files:**
- Create: `agentic_trader/research/apriori/runup_runner.py`
- Modify: `agentic_trader/cli/commands/alpha.py` (dispatch `runup`; new `--earnings-pages` option for it; `--corporate-actions` and `--cache` required for it)
- Modify: `docs/apriori-alphas.md` (a `runup` section: rule, inputs, pass rule, what each decision means), `CLAUDE.md` (one paragraph beside the `insider` a priori paragraph: entry, what it reads, that it cannot pass, result pending)
- Test: `tests/research/apriori/test_runup_runner.py`, `tests/cli/` (the apriori-study command tests)

**Interfaces:**
- Consumes: Tasks 1–4; `build_cube_inputs(..., require_cached=True, load_hourly=True)` and `load_event_sources` (`research/pooled/runner.py`), `load_cohort`, `load_campaign_protocol`, `identity_breaks`.
- Produces: `build_runup_inputs(entry: RunupEntry, *, repo_root: Path, bars: BarSource, calendar: CalendarSource, cache_dir: Path, earnings_pages: Path, actions_file: Path, market_cache_dir: Path, pace, progress=None) -> RunupInputs` — checks the pooled protocol digest on its bytes first, the protocol type (an events protocol), the cohort digest, the cube hash (`cube_sha256`), and the pages and actions digests (`load_event_sources`) before use; fetches SPY hourly bars for `[bars_from, bars_through]` once into `market_cache_dir` (reused when present and digest-recorded in `provenance`).

- [ ] **Step 1: Failing tests:** a protocol with a changed byte, a cohort or cube hash mismatch, and a pages digest mismatch each raise naming both digests; a non-events protocol raises `TypeError`; the CLI refuses `runup` without `--earnings-pages`, `--corporate-actions` or `--cache`, and refuses `--earnings-pages` for other entries.
- [ ] **Step 2: Run; expect failures.**
- [ ] **Step 3: Implement** and write the docs.
- [ ] **Step 4: Run** `uv run pytest tests/research/apriori tests/cli -q`; expect PASS. Stage and report.

## After the tasks (controller)

The final review and fix wave, then the run outside 10:20–10:50 and 14:20–14:50 New York:
`uv run copilot alpha apriori-study config/research/apriori/runup-v1.json --output ~/agentic-trader-research/apriori-runup-v1-<date> --cache ~/agentic-trader-research/pooled-cache-v1 --earnings-pages <archive> --corporate-actions <cohort-v3 actions.json>`
(from the worktree with the installed `.envrc` sourced read-only for the research data keys), then the results doc, the ledger verdict, `docs/alpha-roadmap.md` and `CLAUDE.md` updates bundled into the PR.
