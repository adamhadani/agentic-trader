# Bracket Counterfactual v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A frozen, predeclared research test of four exit policies (3R target, live 1.5R ratchet with 2R or 3R target, ratchet only) against the live 2R fixed bracket on replayed native setups, plus the same arms measured descriptively on live journaled cards.

**Architecture:** The shared bracket labeller (`research/setups/labels.py`) learns an optional `BracketArm` (target multiple + trailing spec) while staying byte-identical without one. The setup runner labels each replayed setup under every arm into one long frame. A new module `research/setups/bracket_counterfactual.py` holds the pydantic protocol, the paired session-block statistics (B1–B5 with Holm), and the write-once executor, following `recurrence.py`. A new CLI `alpha bracket-counterfactual` replays the three windows from the existing SIP caches. `cards outcomes` gains per-arm columns and a descriptive `counterfactual` block built from the same `BracketArm` objects.

**Tech Stack:** Python 3.14, pandas/numpy, pydantic v2 (frozen, `extra="forbid"`), click, pytest; `env -u VIRTUAL_ENV uv run` for every command; ruff strict (C408, PLC0415: no `dict()` calls for literals, no function-level imports).

**Spec:** `docs/superpowers/specs/2026-10-18-bracket-counterfactual-design.md`

## Global Constraints

- Research only: no database, registry, broker, Telegram or `risk/` access from the new study code; the `cards outcomes` block is descriptive and never read by `risk/`, `card_policy`, `card_stats` or admission.
- `label_bracket(..., arm=None)` must produce exactly today's outcomes (regression-pinned); `card_stats` and the recurrence frames must not change.
- Arms are the five of spec §4 with names `A0_live`, `A1_3r`, `A2_2r_trail`, `A3_3r_trail`, `A4_trail_only`; ratchet trigger 1.5R and distance 1.5R; evaluated on the completed regular-session hourly bar's **close**, effective from the **next** bar; stop never moves down; the trigger compares `close − entry_price` (the labeller's fill) against `trigger_r × risk_unit` (planned risk `|levels.entry − levels.stop|`).
- Cost 5 bp per side decisive; `r_cap` 10 for statistics only (frames keep raw values); bootstrap block mean 10, 2,000 draws, seed 20261018; Holm one-sided alpha 0.05; floors 3,000 setups / 600 sessions / 95% mature / 300 divergent pairs.
- Windows: primary 2021-06-01→2026-01-29 (cutoff 2026-09-22), robustness 2017-01-03→2021-04-30 (cutoff 2021-06-15), confirmation 2026-03-02→2026-08-14 (cutoff 2026-09-22), read once, for one arm, after a declaration file is written.
- Artifacts are write-once (`save_json_report` links exclusively); frames are gzip CSV at mode 0600 via `_write_private_gzip`.
- Commit each task with its tests (pre-commit runs ruff, mypy and impacted pytest; it stashes unstaged tracked changes but not untracked files, so stage every new file).
- No new migration; schema head stays `008_alpha_pipeline`. No docs-only commits in the PR: docs ride with Tasks 6 and 7.

## Review Focus

1. A ratcheted stop that is gapped through at the next bar's open: the exit must fill at the open (below the ratcheted stop), never at the stop level — Task 1 test `test_gap_through_ratcheted_stop_fills_at_open`.
2. A setup whose A0 outcome is `TIMEOUT` but whose ratchet arm resolved earlier: pairing must keep both rows (both mature) and the contrast must count them as divergent — Task 4 test `test_divergent_counts_differing_hit_or_exit_time`.
3. A SHORT setup with the ratchet (direction symmetry even though v1 runs long-only) — Task 1 test `test_short_ratchet_mirrors_long`.
4. The confirmation frame must never be read when no arm passes, and when one passes the declaration file must exist before the frame callable runs — Task 5 tests `test_no_pass_never_calls_confirmation` and `test_declaration_written_before_confirmation_read`.
5. A journaled candidate with no `target` (older events) or a malformed geometry: the live block must leave the `cf_*` columns `None` and not raise — Task 7 test `test_counterfactual_columns_none_without_target`.

---

### Task 1: Bracket arms in the labeller

**Files:**
- Modify: `agentic_trader/research/setups/labels.py`
- Test: `tests/research/setups/test_labels.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class TrailSpec:
      trigger_r: float
      distance_r: float

  @dataclass(frozen=True)
  class BracketArm:
      name: str
      target_r: float | None     # None → no target unless this is LIVE_ARM
      trail: TrailSpec | None

  LIVE_ARM = BracketArm(name="A0_live", target_r=None, trail=None)

  def label_bracket(levels, decision_at, hourly, *, max_hold_sessions, cost_bps_per_side=0.0,
                    arm: BracketArm | None = None) -> BracketOutcome
  ```
  `BracketOutcome` gains `final_stop: float | None = None` and `ratchet_moves: int = 0` (defaulted, so existing constructors and `_immature`/`_resolved` call sites still work). `__all__` lists `TrailSpec`, `BracketArm`, `LIVE_ARM`.

- [ ] **Step 1: Write the failing tests** in `tests/research/setups/test_labels.py` (reuse `bars`, `LONG`, `SHORT`, `BEFORE_OPEN`):

```python
A1 = BracketArm(name="A1_3r", target_r=3.0, trail=None)
TRAIL = TrailSpec(trigger_r=1.5, distance_r=1.5)
A2 = BracketArm(name="A2_2r_trail", target_r=2.0, trail=TRAIL)
A4 = BracketArm(name="A4_trail_only", target_r=None, trail=TRAIL)   # None + not LIVE_ARM → no target

def test_arm_none_is_byte_identical_to_live_arm():
    frame = bars([...])  # any existing fixture frame
    assert label_bracket(LONG, BEFORE_OPEN, frame, max_hold_sessions=5) == \
           label_bracket(LONG, BEFORE_OPEN, frame, max_hold_sessions=5, arm=LIVE_ARM)

def test_3r_target_arm_hits_where_live_times_out():
    # bars: entry open 100.5, later high 103.6 (≥ entry 100 + 3×1.0 = 103.0 planned-entry target) but never 102.0 first?
    # Build: entry bar (100.5,100.8,100.2,100.6), then (100.6,101.9,100.3,101.8) [no 2R hit: 102.0 not touched],
    # then (101.8,103.6,101.5,103.2). Live arm: TARGET at bar 3 (high ≥ 102). A1: TARGET at bar 3 with fill 103.0 (level) since open 101.8 < 103.0.
    out = label_bracket(LONG, BEFORE_OPEN, frame, max_hold_sessions=5, arm=A1)
    assert out.hit == BracketHit.TARGET and out.exit_price == pytest.approx(103.0)
    assert out.r == pytest.approx((103.0 - 100.5) / 1.0)

def test_ratchet_triggers_on_close_and_is_effective_next_bar():
    # entry open 100.0 (risk 1.0); bar2 close 101.5 (= entry_price + 1.5R) → proposed stop 100.0; bar2's own low 99.95 must NOT exit
    # bar3 low 99.9 → STOP at 100.0 (ratcheted), r = 0.0 exactly; ratchet_moves == 1; final_stop == 100.0
    out = label_bracket(LONG, BEFORE_OPEN, frame, max_hold_sessions=5, arm=A4)
    assert out.hit == BracketHit.STOP and out.exit_price == pytest.approx(100.0)
    assert out.r == pytest.approx(0.0) and out.ratchet_moves == 1 and out.final_stop == pytest.approx(100.0)

def test_ratchet_never_moves_down():
    # closes 102.0 (stop → 100.5), then 101.0 (proposed 99.5 < 100.5 → unchanged), then low 100.4 → STOP at 100.5
    assert out.ratchet_moves == 1 and out.final_stop == pytest.approx(100.5)

def test_trail_only_arm_never_returns_target():
    # bars run to 110 and stay there until the 5-session timeout → TIMEOUT, r > 0, final_stop rising
    assert out.hit == BracketHit.TIMEOUT and out.ratchet_moves >= 2

def test_gap_through_ratcheted_stop_fills_at_open():
    # ratchet to 100.5, next bar opens 99.0 → STOP with exit_price 99.0 (the open), not 100.5
    assert out.exit_price == pytest.approx(99.0)

def test_both_touched_bar_counts_as_stop_with_ratcheted_level():
    # after ratchet to 100.5, a bar with low 100.4 and high 103.5 under A4 (no target) → STOP at 100.5; under A3 (3R + trail) → STOP, not TARGET

def test_short_ratchet_mirrors_long():
    # SHORT entry 100, stop 101: close 98.5 → stop 100.0; then high 100.1 → STOP at 100.0, r == 0.0

def test_arm_outcome_defaults_without_trail():
    out = label_bracket(LONG, BEFORE_OPEN, frame, max_hold_sessions=5, arm=A1)
    assert out.ratchet_moves == 0 and out.final_stop == pytest.approx(LONG.stop)
```

Use explicit hourly timestamps inside `REGULAR_SESSION_HOURS_NY` (14:00–20:00 UTC in March) across consecutive weekdays as the existing tests do.

- [ ] **Step 2: Run tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/setups/test_labels.py -q`
Expected: FAIL with `ImportError` (TrailSpec/BracketArm) then `TypeError` on the `arm` keyword.

- [ ] **Step 3: Implement** in `labels.py`:
  - Add `TrailSpec`, `BracketArm`, `LIVE_ARM`; add `final_stop: float | None = None`, `ratchet_moves: int = 0` to `BracketOutcome` and thread them through `_resolved`/`_immature` (keyword-only, defaulted).
  - In `label_bracket`: resolve the effective target once: `arm is None or arm is LIVE_ARM` → `levels.target`; `arm.target_r is None` → `None` (no target); else `levels.entry + sign × arm.target_r × risk_unit`. Resolve `trail = None if arm is None else arm.trail`.
  - Walk with a mutable `stop = levels.stop` and `moves = 0`. Per bar, in this order: timeout check (unchanged), stop check against the **current** `stop` (fill `min(open, stop)` for longs), target check only when a target exists, then — if `trail` — compute `gain = sign × (close − entry_price)`; if `gain ≥ trail.trigger_r × risk_unit`, `proposed = close − sign × trail.distance_r × risk_unit`; if `sign × (proposed − stop) > 0`: `stop = proposed; moves += 1`. The gap-at-entry branches are unchanged (they use the planned stop and the effective target; with no target, `gapped_target` is False).
  - Every `_resolved`/`_immature` return passes `final_stop=stop` (or `levels.stop` before entry) and `ratchet_moves=moves`.

- [ ] **Step 4: Run the whole labels test file and the outcome/card_stats/recurrence consumers**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/setups/test_labels.py tests/research/setups/test_outcomes.py tests/research/setups/test_card_stats.py tests/research/setups/test_recurrence.py tests/research/setups/test_runner.py -q`
Expected: all PASS (existing tests unchanged).

- [ ] **Step 5: Commit**

```bash
git add agentic_trader/research/setups/labels.py tests/research/setups/test_labels.py
git commit -m "Labeller: optional BracketArm (target multiple, close-based 1.5R ratchet) with byte-identical default"
```

---

### Task 2: Arm frames in the setup runner

**Files:**
- Modify: `agentic_trader/research/setups/runner.py` (`_build_frame` untouched; add `_build_arm_frame`; `build_window_frames` gains `arms`)
- Test: `tests/research/setups/test_runner.py`

**Interfaces:**
- Consumes: `BracketArm`, `LIVE_ARM`, `label_bracket(..., arm=)` from Task 1; `market_r` from `research/setups/outcomes.py` (import at module top; `outcomes.py` does not import `runner.py`, so no cycle).
- Produces:
  ```python
  ARM_FRAME_COLUMNS: tuple[str, ...] = (
      "decision_at", "session", "symbol", "strategy", "timeframe", "direction",
      "entry", "stop", "target", "atr", "setup_quality", "arm",
      "hit", "entry_time", "entry_price", "exit_time", "exit_price",
      "holding_sessions", "ratchet_moves", "final_stop",
      "r", "r_cost", "market_r", "excess_r",
  )

  def _build_arm_frame(records, hourly_by_symbol, arms: Sequence[BracketArm], *,
                       max_hold_sessions: int, cost_bps_per_side: float,
                       market_symbol: str = "SPY") -> pd.DataFrame   # long: one row per (record, arm)

  async def build_window_frames(..., arms: Sequence[BracketArm] | None = None)
  ```
  With `arms`, each returned callable yields `_build_arm_frame(window_records[name], hourly_by_symbol, arms, max_hold_sessions=..., cost_bps_per_side=cost_bps_per_side[-1], market_symbol="SPY")`; without, behaviour is unchanged.

- [ ] **Step 1: Write failing tests** in `test_runner.py` using `_hourly_frame`-style fixtures and hand-built `SetupRecord`s:

```python
def test_build_arm_frame_one_row_per_record_and_arm():
    # 2 records × 3 arms → 6 rows; columns == list(ARM_FRAME_COLUMNS); "arm" values in arm names
def test_build_arm_frame_market_r_uses_the_arms_own_window():
    # SPY hourly rising 1%/bar; an arm that exits one bar later than A0 has a larger market_r; excess_r == r_cost − market_r
def test_build_arm_frame_skips_invalid_levels_and_missing_hourly():
    # stop == entry → no rows for that record; symbol without hourly bars → no rows
def test_build_arm_frame_without_spy_leaves_market_r_none():
async def test_build_window_frames_with_arms_returns_long_frames(...):
    # reuse the existing build_window_frames test scaffold (FakeBarSource, sync_process_pool); pass arms=[LIVE_ARM, A1]; callable result has "arm" column with both names and len == 2 × (rows without arms)
```

- [ ] **Step 2: Run to verify failure**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/setups/test_runner.py -q -k "arm"`
Expected: FAIL with `ImportError: cannot import name 'ARM_FRAME_COLUMNS'`.

- [ ] **Step 3: Implement** `_build_arm_frame` (label each record under each arm at the decisive cost; `r` from the same outcome; `market_r(direction, levels.entry, levels.stop, outcome.entry_time, outcome.exit_time, market)` when the arm resolved and SPY hourly exists, else `None`; `excess_r = r_cost − market_r` or `None`; `session` as the ET date like `_build_frame`). Thread `arms` through `build_window_frames` → `_make_frame`.

- [ ] **Step 4: Run** `env -u VIRTUAL_ENV uv run pytest tests/research/setups/test_runner.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add agentic_trader/research/setups/runner.py tests/research/setups/test_runner.py
git commit -m "Setup runner: long per-arm frames with the arm's own SPY control (build_window_frames arms=)"
```

---

### Task 3: Protocol model and the frozen file

**Files:**
- Create: `agentic_trader/research/setups/bracket_counterfactual.py` (protocol part)
- Create: `config/research/bracket-counterfactual-v1.json`
- Test: `tests/research/setups/test_bracket_counterfactual.py`

**Interfaces:**
- Consumes: `TrailSpec`, `BracketArm`, `LIVE_ARM` (Task 1).
- Produces:
  ```python
  class ArmSpec(BaseModel, frozen=True, extra="forbid"):
      name: str
      target_r: float | Literal["planned"] | None
      trail: dict | None            # {"trigger_r": float, "distance_r": float}
      def to_arm(self) -> BracketArm   # "planned" → LIVE_ARM (name must be "A0_live")

  class BracketCounterfactualProtocol(BaseModel, frozen=True, extra="forbid"):
      version: Literal["bracket_counterfactual_v1"]
      window: tuple[date, date]; data_cutoff: date
      robustness_window: tuple[date, date]; robustness_data_cutoff: date
      confirmation_window: tuple[date, date]; confirmation_data_cutoff: date
      confirmation_prior_use: str
      embargo_sessions: int
      scan_times_et: tuple[str, ...]
      max_hold_sessions: int
      cost_bps_per_side: tuple[float, ...]
      arms: tuple[ArmSpec, ...]
      r_cap: float
      bootstrap: dict
      holm_alpha: float
      min_setups: int; min_sessions: int; min_mature_fraction: float; min_divergent: int
      feed: Literal["alpaca:sip"]; adjustment: Literal["all"]
      strategy_config: dict; sector_etf: dict[str, str]
      hypotheses: str; decision_rule: str
      def document(self) -> dict; identity -> str (sha256 of sorted JSON)
      def bracket_arms(self) -> tuple[BracketArm, ...]
      def counterfactual_arm_names(self) -> tuple[str, ...]   # every arm except A0_live, protocol order

  HYPOTHESES = ("B1", "B2", "B3", "B4", "B5")
  LIVE_ARM_NAME = "A0_live"
  ```
  Validators (spec §7): each cutoff ≥ its window end + 30 days; robustness end + 30 days ≤ primary start; confirmation start ≥ primary end + 28 days; exactly one arm named `A0_live`, first, with `target_r == "planned"` and `trail is None`; `"planned"` only on `A0_live`; arm names unique; `r_cap > 0`; `0 < holm_alpha < 0.5`; `max_hold_sessions ≥ 1`; `cost_bps_per_side` non-empty; trail dicts have exactly `trigger_r`, `distance_r` > 0.

- [ ] **Step 1: Write failing tests** (`_protocol_kwargs(**overrides)` helper mirroring `test_recurrence.py`):

```python
def test_protocol_identity_stable_and_sensitive(): ...
def test_protocol_rejects_each_bad_shape():
    # cutoff too early (match "data_cutoff"), robustness too close ("one label horizon"), confirmation inside embargo ("embargo"),
    # missing A0_live ("A0_live"), A0 not first, "planned" on A1, duplicate names, r_cap 0, holm_alpha 0.6, extra key
def test_bracket_arms_maps_planned_to_live_arm_and_dicts_to_trailspec():
    arms = _protocol().bracket_arms()
    assert arms[0] is LIVE_ARM and arms[2].trail == TrailSpec(1.5, 1.5) and arms[4].target_r is None
def test_frozen_protocol_file_matches_the_spec_and_the_loaded_config():
    # like the recurrence test: windows, cutoffs, arms (names in order, the five targets listed below, trails), r_cap 10.0,
    # bootstrap {10, 2000, 20261018}, holm_alpha 0.05, floors (3000, 600, 0.95, 300), cost (0.0, 5.0), max_hold 20,
    # strategy_config == load_config(config/config.yaml).strategies.model_dump(mode="json"), sector_etf == SECTOR_ETF,
    # trend_pullback.allow_short is False
```
  Arm targets in the frozen file: `A0_live: "planned"`, `A1_3r: 3.0`, `A2_2r_trail: 2.0`, `A3_3r_trail: 3.0`, `A4_trail_only: null`. (A2 uses the explicit 2.0 rather than `"planned"` so that it is exact in R even for cards whose regime raised the live ratio to 2.2/2.5; the frozen-file test asserts these five values.)

- [ ] **Step 2: Run** `env -u VIRTUAL_ENV uv run pytest tests/research/setups/test_bracket_counterfactual.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement the module's protocol part and write the frozen JSON.** Copy `strategy_config` and `sector_etf` verbatim from `config/research/setup-recurrence-v1.json`. `hypotheses` is the prose of spec §4–§6 (arms, ratchet rule, statistics, B1–B5) in one paragraph; `decision_rule` the pre-registered reading of spec §6; `confirmation_prior_use`: "setup-outcomes-v1 one-shot holdout, read once on 2026-09-23 for the shadow ranker's acceptance test (docs/setup-outcomes-2026-09-23.md); declared second use, different hypothesis, read at most once for at most one arm". After writing, record the file's SHA-256 prefix in the test as a comment, not an assertion.

- [ ] **Step 4: Run** the test file → PASS.

- [ ] **Step 5: Commit**

```bash
git add agentic_trader/research/setups/bracket_counterfactual.py config/research/bracket-counterfactual-v1.json tests/research/setups/test_bracket_counterfactual.py
git commit -m "Bracket counterfactual v1: frozen protocol (five arms, B1-B5) and model"
```

---

### Task 4: Paired statistics, Holm, floors and the decision table

**Files:**
- Modify: `agentic_trader/research/setups/bracket_counterfactual.py`
- Test: `tests/research/setups/test_bracket_counterfactual.py`

**Interfaces:**
- Consumes: `_stationary_index_draws` (`study.py`), `_ci90`, `_p_positive` (`recurrence.py`); `ARM_FRAME_COLUMNS` (Task 2).
- Produces:
  ```python
  SETUP_KEY = ("decision_at", "symbol", "strategy", "timeframe", "direction")

  def winsorized(long: pd.DataFrame, r_cap: float) -> pd.DataFrame
      # copy with r, r_cost clipped to ±r_cap and excess_r = r_cost − market_r recomputed (NaN market_r → NaN excess)
  def pair_arms(long: pd.DataFrame, live: str, other: str) -> pd.DataFrame
      # inner join on SETUP_KEY of the two arms' resolved rows (hit != immature); columns: session, excess_live, excess_other,
      # r_cost_live, r_cost_other, divergent (bool: hit or exit_time differ)
  def paired_contrast(pairs: pd.DataFrame, protocol) -> dict
      # mean(excess_other − excess_live) setup-weighted with whole-session draws (_stationary_index_draws over sorted sessions):
      # {"n_pairs", "n_sessions", "n_divergent", "mean_diff", "ci90", "p_one_sided", "finite_draws"}
  def arm_mean(long: pd.DataFrame, arm: str, protocol) -> dict
      # setup-weighted mean r_cost of the arm's resolved rows with ci90: {"n", "n_sessions", "mean_r_cost", "ci90", "holds"}  (holds: ci90[0] >= 0)
  def holm(p_values: Mapping[str, float], alpha: float) -> dict[str, dict]
      # {"arm": {"p": p, "holm_p": adjusted, "rejected": bool}}; standard step-down; NaN p never rejected
  def sample_floors(long: pd.DataFrame, pairs_by_arm: Mapping[str, pd.DataFrame], protocol) -> dict
      # n_setups (A0 rows incl. immature), n_sessions (A0 distinct sessions), mature_fraction (A0), n_divergent per arm, holds per arm
  def discovery(primary_long, robustness_long, protocol) -> dict
      # per counterfactual arm: b1 {contrast..., "holm_p", "holds"}, b2 arm_mean, b3 {robust contrast..., "holds": mean_diff > 0}, b4 floors;
      # "passing": [arm names], "selected_arm": name|None (largest b1 mean_diff among passing; tie → earliest protocol order)
  def confirmation(confirmation_long, arm: str, protocol) -> dict
      # {"contrast": paired_contrast on excess, "arm_mean": arm_mean, "holds": mean_diff > 0 and mean_r_cost >= 0}
  ```

- [ ] **Step 1: Write failing tests** with a synthetic long frame builder `_long(n_sessions, arms, per_session, seed)` producing resolved rows per arm with controllable per-arm `r_cost`/`market_r`:

```python
def test_winsorized_clips_statistics_only():          # a +1546 r becomes 10.0; original frame untouched
def test_pair_arms_inner_joins_resolved_rows_only():  # an immature A1 row drops the pair
def test_divergent_counts_differing_hit_or_exit_time()
def test_paired_contrast_identical_arms_is_exactly_zero_with_zero_width_ci()
def test_paired_contrast_recovers_a_planted_advantage():  # other = live + 0.2 on every row → mean_diff ≈ 0.2, ci90[0] > 0, p < 0.05
def test_paired_contrast_uses_one_session_draw_for_both_arms():  # same seed, swapping arms negates mean_diff exactly
def test_holm_step_down():  # p = {A1: .01, A2: .02, A3: .04, A4: .5}, alpha .05 → A1,A2 rejected (0.04 vs 0.025 fails), A4 not; NaN never rejected
def test_sample_floors_use_a0_counts_and_per_arm_divergence()
def test_discovery_selects_largest_b1_among_passing_and_breaks_ties_by_order()
def test_discovery_no_pass_has_no_selected_arm()
def test_confirmation_holds_requires_positive_contrast_and_nonnegative_mean()
```

- [ ] **Step 2: Run** `-k "winsor or pair or contrast or holm or floors or discovery or confirmation"` → FAIL (`ImportError`).

- [ ] **Step 3: Implement.** Session resampling: sort distinct `session` values, draw index sequences with `_stationary_index_draws(n_sessions, block_mean, draws, seed)`, aggregate per-session sums/counts of the difference (and of the arm's `r_cost`) and weight as `recurrence._weighted` does (import `_weighted`, `_ci90`, `_p_positive` from `recurrence`). For `discovery`, B1 `holds = holm[arm]["rejected"]`, B3 `holds = mean_diff > 0`, B4 `holds` = all four floors.

- [ ] **Step 4: Run** the test file → PASS.

- [ ] **Step 5: Commit**

```bash
git add agentic_trader/research/setups/bracket_counterfactual.py tests/research/setups/test_bracket_counterfactual.py
git commit -m "Bracket counterfactual: paired session-block contrasts, Holm, floors, discovery and confirmation rules"
```

---

### Task 5: Executor

**Files:**
- Modify: `agentic_trader/research/setups/bracket_counterfactual.py`
- Test: `tests/research/setups/test_bracket_counterfactual.py`

**Interfaces:**
- Consumes: Tasks 3–4; `_write_private_gzip` and `_finite_json`, `_drop_immature` is **not** used (immature rows stay in the frames; statistics drop them per arm).
- Produces:
  ```python
  def execute_bracket_counterfactual(protocol, directory: Path, *, frames: Mapping[str, Callable[[], pd.DataFrame]],
                                     environment: dict) -> dict
  ```
  `frames` has keys `primary`, `robustness`, `confirmation`. Writes, in order: `protocol.json` (+`identity`), `manifest.json` (`protocol_id`, `environment`, `started_at`, `hypotheses`, `arms` (names), `windows`, `authorizes_promotion: False`, `confirmation_declaration: "confirmation.json"`); then inside `try`: primary frame → `frames/primary.csv.gz`, robustness frame → `frames/robustness.csv.gz`, `discovery(...)`; if `selected_arm`: write `confirmation.json` (`protocol_id`, `confirmation_read: True`, `confirmation_arm`, `declared_at`) **then** call `frames["confirmation"]()`, write `frames/confirmation.csv.gz`, `confirmation(...)`; decision `eligible_bracket_change` if `b5.holds` else `no_change`; `result.json` = `{"status": "completed", "decision", "selected_arm", "b1".."b5" (b5 None when not read), "descriptives", "n_rows", "authorizes_promotion": False}`. On exception: `{"status": "failed", "error": "<Type>: <msg>", "authorizes_promotion": False}` and still write `result.json`.
  Descriptives per window: per-arm `{n, target_rate, stop_rate, timeout_rate, mean_r_cost, mean_r, mean_holding_sessions, mean_market_r, mean_excess_r, mean_ratchet_moves}`, per `strategy/timeframe` stratum the same per arm, per year the paired contrast per arm (point estimates only), the 0 bp arm means (`r` instead of `r_cost`), unpaired arm means.

- [ ] **Step 1: Write failing tests**

```python
def test_executor_writes_protocol_manifest_frames_and_result_in_order(tmp_path)
def test_no_pass_never_calls_confirmation(tmp_path):            # recording callable raises if called; result b5 is None, decision no_change
def test_declaration_written_before_confirmation_read(tmp_path):  # confirmation callable asserts (tmp_path/"out"/"confirmation.json").exists() when invoked
def test_pass_then_confirmation_fail_is_no_change_with_selected_arm_recorded(tmp_path)
def test_pass_then_confirmation_pass_is_eligible_bracket_change(tmp_path)
def test_frame_failure_writes_status_failed(tmp_path)           # primary callable raises RuntimeError → result.json status failed, error names the type
def test_frames_are_private_gzip(tmp_path)                       # mode 0o600
```

- [ ] **Step 2: Run** `-k executor or confirmation or failed or private` → FAIL.

- [ ] **Step 3: Implement** `execute_bracket_counterfactual` as specified; the planted-advantage synthetic frames from Task 4's helper make the pass/fail paths deterministic (protocol override: `bootstrap draws 200`, floors lowered in the test protocol).

- [ ] **Step 4: Run** the test file → PASS; also `env -u VIRTUAL_ENV uv run ruff check agentic_trader/research/setups/bracket_counterfactual.py`.

- [ ] **Step 5: Commit**

```bash
git add agentic_trader/research/setups/bracket_counterfactual.py tests/research/setups/test_bracket_counterfactual.py
git commit -m "Bracket counterfactual: write-once executor with declared one-shot confirmation read"
```

---

### Task 6: CLI `alpha bracket-counterfactual`, roadmap and CLAUDE.md

**Files:**
- Modify: `agentic_trader/cli/commands/alpha.py` (beside `alpha_setup_recurrence_cmd`)
- Modify: `docs/alpha-roadmap.md` (a *Status (October 18)* paragraph after the October 16 status in the desk-direction section), `CLAUDE.md` (one sentence in the Operations contract after the `alpha setup-recurrence` sentence)
- Test: `tests/research/setups/test_bracket_counterfactual.py` (CLI section, mirroring `test_recurrence.py`'s CLI tests)

**Interfaces:**
- Consumes: `BracketCounterfactualProtocol`, `execute_bracket_counterfactual`, `build_window_frames(..., arms=)`, `claim_cache_range`, `CACHE_RANGE_FILE`, `BARS_LOOKBACK_DAYS`, `research_environment`, `SECTOR_ETF`.
- Produces: `copilot alpha bracket-counterfactual PROTOCOL --output DIR --cache DIR --robustness-cache DIR [--max-workers N]`.

- [ ] **Step 1: Write failing CLI tests**

```python
def test_cli_bracket_counterfactual_is_registered()                       # --help lists --robustness-cache
def test_cli_refuses_existing_output(tmp_path)
def test_cli_refuses_a_stale_strategy_config(tmp_path)
def test_cli_refuses_a_stale_sector_map(tmp_path)
def test_cli_fails_fast_on_a_mismatched_cache_range(tmp_path, monkeypatch)   # build_window_frames must not run
def test_cli_builds_primary_and_confirmation_from_one_replay(tmp_path, monkeypatch)
    # monkeypatch build_window_frames to record calls: first call windows == {"primary", "confirmation"} with cache=--cache and arms == protocol.bracket_arms(); second call {"robustness"} with --robustness-cache; execute_* receives frames with the three keys and environment["caches"] has both paths
def test_cli_exits_nonzero_when_the_study_fails(tmp_path, monkeypatch)
```

- [ ] **Step 2: Run** `-k cli` → FAIL (`No such command`).

- [ ] **Step 3: Implement the command** by following `alpha_setup_recurrence_cmd` line for line, with these differences: one `build_window_frames` call for `{"primary": protocol.window, "confirmation": protocol.confirmation_window}` on `--cache` (shared `data_cutoff`), one for `{"robustness": ...}` on `--robustness-cache`; both calls pass `arms=protocol.bracket_arms()`; the cache claim for `--cache` spans `min(primary start, confirmation start) − BARS_LOOKBACK_DAYS` to `data_cutoff`; refuse a feed other than `alpaca:sip` (the model already forbids it, so the refusal is the pydantic error surfaced as a `ClickException`); failure message `bracket counterfactual failed: <error>`.

- [ ] **Step 4: Docs.** Roadmap status paragraph: what was built, the five arms, the rules, "run pending; a pass only makes a card-construction PR eligible". CLAUDE.md: one sentence naming `alpha bracket-counterfactual` as the predeclared, research-only exit-policy test (B1–B5 on replayed history, one-shot confirmation read declared before the read) and that `cards outcomes` reports the same arms descriptively.

- [ ] **Step 5: Run** `env -u VIRTUAL_ENV uv run pytest tests/research/setups/test_bracket_counterfactual.py tests/cli/test_cli_smoke.py -q` → PASS.

- [ ] **Step 6: Commit**

```bash
git add agentic_trader/cli/commands/alpha.py tests/research/setups/test_bracket_counterfactual.py docs/alpha-roadmap.md CLAUDE.md
git commit -m "alpha bracket-counterfactual: replay the three frozen windows from the SIP caches and run B1-B5; roadmap and handoff notes"
```

---

### Task 7: Live measurement in `cards outcomes`

**Files:**
- Modify: `agentic_trader/research/setups/outcomes.py`, `agentic_trader/cli/commands/cards.py`
- Modify: `docs/card-evidence.md` (new subsection "Bracket counterfactual arms (descriptive)")
- Test: `tests/research/setups/test_outcomes.py`, `tests/research/setups/test_card_stats.py` (one assertion), `tests/cli/test_cli_smoke.py` (the existing `cards outcomes` smoke keeps passing)

**Interfaces:**
- Consumes: `BracketArm`, `TrailSpec`, `LIVE_ARM`, `label_bracket(..., arm=)`; `BracketCounterfactualProtocol` only in the test that pins the constant to the frozen file.
- Produces:
  ```python
  LIVE_COUNTERFACTUAL_ARMS: tuple[BracketArm, ...]   # A1_3r, A2_2r_trail, A3_3r_trail, A4_trail_only — hard-coded, test-pinned to the frozen protocol file
  # _COLUMNS gains, after "stop": "target", "atr_14"; and after "excess_r": for each arm name N in LIVE_COUNTERFACTUAL_ARMS:
  #   f"cf_{N}_hit", f"cf_{N}_r_cost", f"cf_{N}_market_r"
  CF_COLUMN_PREFIX = "cf_"
  def _counterfactual_summary(frame, mature) -> dict   # summary["counterfactual"]
  ```
  `summary["counterfactual"]` = `{"caveat": "descriptive; not a gate", "arms": {N: {"n_pairs", "target_rate", "stop_rate", "timeout_rate", "mean_r_cost", "mean_excess_r", "advantage_vs_live_excess"}}}`, where `n_pairs` counts rows with both the live `r_cost` and `cf_N_r_cost` resolved, and `advantage_vs_live_excess = mean((cf_r_cost − cf_market_r) − (r_cost − market_r))` over rows where all four are present (else `None`). The empty-frame branch of `summarize` returns the same shape with `None`/0 values.

- [ ] **Step 1: Write failing tests**

```python
def test_live_counterfactual_arms_match_the_frozen_protocol():
    doc = json.loads((WORKSPACE_ROOT / "config/research/bracket-counterfactual-v1.json").read_text())
    assert LIVE_COUNTERFACTUAL_ARMS == BracketCounterfactualProtocol(**doc).bracket_arms()[1:]
def test_columns_include_target_atr_and_cf_columns()
def test_label_journaled_fills_cf_columns_from_the_same_bars()          # a candidate whose 3R arm resolves TARGET while live resolves TARGET too: cf_A1_3r_hit == "target"; cf market_r uses the arm's exit
def test_counterfactual_columns_none_without_target()                    # candidate dict lacking "target" → every cf_* None, hit immature (existing behaviour)
def test_counterfactual_columns_none_on_fetch_failure()
def test_counterfactual_summary_shapes_and_advantage()
def test_summarize_empty_frame_has_counterfactual_block()
def test_card_stats_ignores_cf_columns()                                 # in test_card_stats.py: computing the snapshot from a frame with cf_* filled equals one with them None
```

- [ ] **Step 2: Run** `env -u VIRTUAL_ENV uv run pytest tests/research/setups/test_outcomes.py -q -k "counterfactual or cf_ or columns"` → FAIL.

- [ ] **Step 3: Implement.** In `_label_one`, after the live outcome, loop `LIVE_COUNTERFACTUAL_ARMS`: `label_bracket(levels, decided_at, hourly, max_hold_sessions=..., cost_bps_per_side=cost_bps, arm=arm)`; fill `cf_{N}_hit`, `cf_{N}_r_cost`, `cf_{N}_market_r` (the latter via `market_r` with the arm's entry/exit times when resolved). `_row` gains `target=entry.get("target")`, `atr_14=entry.get("atr_14")` and a `counterfactual: dict[str, Any] | None` keyword whose keys are merged; `_immature_row`/`_fetch_failed_row` leave them `None`. `summarize` adds `"counterfactual": _counterfactual_summary(frame, mature)`. In `cards.py` print the table without the `cf_*` columns (`frame.drop(columns=[c for c in frame.columns if c.startswith(CF_COLUMN_PREFIX)] + [...])`). Keep `card_stats.py` unchanged (its `extra` columns test proves it never reads `cf_*`).

- [ ] **Step 4: Docs.** `docs/card-evidence.md`: subsection describing the four arms, the ratchet approximation, the `cf_*` columns, the `counterfactual` summary block, and the standing caveat (descriptive; never a gate; the frozen study is the decisive test).

- [ ] **Step 5: Run** `env -u VIRTUAL_ENV uv run pytest tests/research/setups tests/cli/test_cli_smoke.py -q` → PASS; `env -u VIRTUAL_ENV uv run ruff check agentic_trader tests` → clean.

- [ ] **Step 6: Commit**

```bash
git add agentic_trader/research/setups/outcomes.py agentic_trader/cli/commands/cards.py tests/research/setups/test_outcomes.py tests/research/setups/test_card_stats.py docs/card-evidence.md
git commit -m "cards outcomes: the four counterfactual bracket arms as descriptive columns and a summary block"
```

---

## Self-review notes

- Spec coverage: §4 arms → Tasks 1, 3; §5 labeller → Task 1; §6 statistics and rules → Task 4; §7 protocol → Task 3; §8 executor/CLI/live block → Tasks 5, 6, 7; §9 outputs → Task 5; §10 tests → each task; §11 runtime → Task 6's one-replay-for-two-windows; §12 rulings carried in prose. The result doc (§9) is written after the run, outside this plan.
- Type consistency: `BracketArm`/`TrailSpec`/`LIVE_ARM` (Task 1) are the only arm types; `ARM_FRAME_COLUMNS` (Task 2) is what Task 4's `SETUP_KEY` and `pair_arms` read; `bracket_arms()` (Task 3) feeds Tasks 6 and 7; `_write_private_gzip` is imported from `recurrence.py`.
- Review Focus items 1–5 each have a named test in Tasks 1, 4, 1, 5 and 7.
