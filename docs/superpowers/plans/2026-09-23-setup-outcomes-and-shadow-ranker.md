# Setup Outcomes and Shadow Ranker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure whether `setup_quality` and causal cross-sectional features predict the bracket outcome (R) of native-strategy setups, under a predeclared protocol with a one-shot time-interval holdout. Then record the same features, and an optional frozen ranker score, on every live ranked candidate without changing live ranking.

**Architecture:** A new `agentic_trader/research/setups/` package holds four modules:
- `labels.py` (bracket labeler) and `features.py` (cross-sectional feature vector): pure, and shared verbatim by the study and the live scan;
- `replay.py`: rebuilds the live `ContractMarketData` at each configured suggestion-scan instant and runs the unchanged `StrategyEngine`;
- `study.py`: the protocol, statistics, model selection and the artifact lifecycle, where holdout labels are read only after the frozen ranker is saved.

The live RANK phase attaches the shadow block and journals one `scan_candidates_ranked` domain event per scan. A read-only `cards outcomes` command labels them.

**Tech Stack:** Python 3.14, pandas/numpy, scikit-learn (already a dependency), pydantic, async SQLAlchemy (`domain_events`, no migration), alpaca-py via the existing `data/providers.py`, click CLI.

**Spec:** `docs/superpowers/specs/2026-09-23-setup-outcomes-and-shadow-ranker-design.md`

## Global Constraints

- Work only in the worktree `/Users/adamhadani/Development/agentic-trader-setups` (branch `feat/setup-outcome-study`). Never read-write or run anything in `/Users/adamhadani/Development/agentic-trader`, which runs the live daemon.
- Run tools as `env -u VIRTUAL_ENV uv run …` from the worktree.
- Tests must do no real network I/O (it is blocked). Use fakes, `httpx.MockTransport` or injected callables.
- No new migration. Schema head stays `008_alpha_pipeline`. `domain_events.kind` is a String column.
- Do not change live ranking order, card budget, gates, sizing, orders or any LLM prompt text.
- The study authorizes nothing: `authorizes_promotion: false`, no alpha registry or trial-ledger writes.
- Features and labels have exactly one implementation each, imported by both research and live code.
- Each behaviour starts with a failing test (RED), then the code, then GREEN.
- Implementers stage with `git add` and do NOT commit (the controller commits). Implementers never dispatch subagents.
- Before reporting, run the affected tests, then the full `env -u VIRTUAL_ENV uv run pytest -q`, then `env -u VIRTUAL_ENV uv run pre-commit run --all-files`.

---

## PR-1: research/setups + study

### Task 1: Bracket labeler

**Files:**
- Create: `agentic_trader/research/setups/__init__.py` (empty docstring module)
- Create: `agentic_trader/research/setups/labels.py`
- Test: `tests/research/setups/test_labels.py` (+ `tests/research/setups/__init__.py` if the tests tree uses packages; mirror `tests/research/`)

**Interfaces — Produces:**
```python
REGULAR_SESSION_HOURS_NY  # re-use: from agentic_trader.screeners.coverage import REGULAR_SESSION_HOURS_NY

class BracketHit(StrEnum):
    TARGET = "target"; STOP = "stop"; TIMEOUT = "timeout"; IMMATURE = "immature"

@dataclass(frozen=True)
class SetupLevels:
    direction: str          # "LONG" | "SHORT" (upper-case; validate)
    entry: float            # planned entry (risk unit reference)
    stop: float
    target: float
    # validate: LONG → stop < entry < target; SHORT → target < entry < stop; else ValueError

@dataclass(frozen=True)
class BracketOutcome:
    hit: BracketHit
    entry_time: datetime | None
    entry_price: float | None
    exit_time: datetime | None
    exit_price: float | None
    r: float | None          # None iff IMMATURE
    r_cost: float | None
    holding_sessions: int    # distinct NY session dates touched from entry bar through exit bar

def label_bracket(
    levels: SetupLevels,
    decision_at: datetime,            # tz-aware UTC
    hourly: pd.DataFrame,             # UTC DatetimeIndex = bar START, cols Open/High/Low/Close; any hours
    *,
    max_hold_sessions: int,
    cost_bps_per_side: float = 0.0,
) -> BracketOutcome
```

**Rules (exactly):**
1. Keep only regular-session bars: bar start converted to `ET_TZ` has `hour in REGULAR_SESSION_HOURS_NY`. Alpaca 1h bars are labelled top-of-hour UTC. Bracket stops on Alpaca rest during regular hours only.
2. The entry bar is the first regular bar with `start >= decision_at`. With none, return IMMATURE (all None, `holding_sessions=0`).
3. `risk_unit = abs(levels.entry - levels.stop)`. `entry_price = entry_bar.Open`.
4. Gap at entry: for a LONG with `Open <= stop`, exit at Open as STOP. With `Open >= target`, exit at Open as TARGET. Mirror for SHORT. Exit time is the entry bar start.
5. Otherwise walk bars from the entry bar inclusive. For a LONG, `stop_hit = Low <= stop` and `target_hit = High >= target`. For bars after the entry bar, when `Open <= stop` the fill is `Open` (gap through), else `stop`. Likewise, when `Open >= target` the fill is `Open`, else `target`. If both hit in the same bar, STOP wins. Mirror for SHORT (stop when `High >= stop`, target when `Low <= target`).
6. Timeout: session dates are the ET dates of regular bars from the entry bar onwards. When a bar's session date would be the `(max_hold_sessions + 1)`-th distinct date, exit TIMEOUT at the **Close of the previous regular bar** (the last bar of the `max_hold_sessions`-th session). If bars run out before a hit or timeout, return IMMATURE with `r=None`.
7. `r = sign * (exit_price - entry_price) / risk_unit`, where sign is +1 LONG and −1 SHORT. `r_cost = r - 2 * cost_bps_per_side / 1e4 * entry_price / risk_unit`.

- [ ] **Step 1: Write failing tests.** A small helper `bars(rows)` builds a UTC-indexed frame from `(iso_start, o, h, l, c)` tuples on 2026-03-02..2026-03-06, using regular-hour starts 14:00–20:00 UTC (EST, before the DST switch). Include one 13:00 UTC pre-market bar and one 21:00 UTC after-hours bar that must be ignored. Tests:
  - `test_long_target_first` → TARGET, r == (target-entry_open)/risk_unit
  - `test_long_stop_first` → STOP, r ≈ −(entry_open−stop)/risk_unit
  - `test_same_bar_stop_wins`
  - `test_gap_through_stop_fills_at_open_r_below_minus_one`
  - `test_gap_at_entry_beyond_target_is_target_at_open`
  - `test_short_mirror_target`
  - `test_timeout_exits_last_close_of_nth_session` (max_hold_sessions=2, no hits → exit at the last regular close of the 2nd session date; holding_sessions == 2)
  - `test_immature_when_bars_run_out`
  - `test_extended_hours_bars_are_ignored` (an after-hours bar that pierces the stop does not trigger)
  - `test_cost_reduces_r` (10 bp per side)
  - `test_invalid_levels_raise`
- [ ] **Step 2:** Run `env -u VIRTUAL_ENV uv run pytest tests/research/setups/test_labels.py -q` and confirm it fails (ImportError).
- [ ] **Step 3:** Implement `labels.py` per the rules. Pure; no I/O.
- [ ] **Step 4:** Re-run and confirm PASS.
- [ ] **Step 5:** `git add` the new files.

### Task 2: Cross-sectional feature vector

**Files:**
- Create: `agentic_trader/research/setups/features.py`
- Test: `tests/research/setups/test_features.py`

**Interfaces — Consumes:** `agentic_trader.research.alpha.panel.group_neutralize` (signature `group_neutralize(panel: DataFrame, groups: Series) -> DataFrame`; groups indexed exactly by panel columns, no NaN).

**Interfaces — Produces:**
```python
FEATURES_VERSION = "setup_features_v1"
CROSS_SECTIONAL = ("mom_252_21", "mom_60", "rev_5", "vol_20", "dist_52w_high",
                   "dollar_volume_20", "resid_mom_60", "sector_rel_mom_60")
DIRECTIONAL = frozenset({"mom_252_21", "mom_60", "rev_5", "dist_52w_high",
                         "resid_mom_60", "sector_rel_mom_60"})
MARKET = ("spy_above_200", "spy_vol20_pct")
SETUP = ("setup_quality", "stop_atr", "reward_risk")
# Frozen sector → ETF map for residuals. Keys are the sector tags used in config/config.yaml `universe:`
# (grep the distinct `sector:` values there and map every equity sector; etf_* sectors → "SPY").
SECTOR_ETF: Mapping[str, str]

@dataclass(frozen=True)
class CrossSection:
    as_of: date                    # last CLOSED session included
    ranks: pd.DataFrame            # index=symbol, columns=CROSS_SECTIONAL, pct ranks in (0,1], NaN allowed
    market: dict[str, float]       # MARKET keys; NaN when SPY history is short/missing

def cross_section(
    daily: Mapping[str, pd.DataFrame],   # symbol → daily OHLCV (Open/High/Low/Close/Volume), index = session date (tz-aware or naive date-like)
    sectors: Mapping[str, str],          # symbol → sector tag
    as_of: date,                          # use only rows whose session date <= as_of
) -> CrossSection

def setup_vector(
    cs: CrossSection, *, symbol: str, direction: str, strategy: str, timeframe: str,
    setup_quality: float, stop_atr: float, reward_risk: float,
) -> dict[str, float]
# Returns keys: every CROSS_SECTIONAL (direction-aware: SHORT uses 1 - rank for DIRECTIONAL keys; vol_20 and dollar_volume_20 unchanged),
# every MARKET, every SETUP, plus one-hot "strategy=<strategy>" and "timeframe=<timeframe>" = 1.0. Missing symbol → CROSS_SECTIONAL NaN.
```

**Definitions** (per symbol, using sessions ≤ `as_of`, close = `Close`):
- `mom_252_21 = close[-22] / close[-253] - 1`. Needs ≥253 rows, else NaN.
- `mom_60 = close[-1] / close[-61] - 1`
- `rev_5 = -(close[-1] / close[-6] - 1)`
- `vol_20 = std(pct_change of last 21 closes, ddof=1)`
- `dist_52w_high = close[-1] / max(High[-252:]) - 1`
- `dollar_volume_20 = mean(Close*Volume over last 20)`
- `resid_mom_60`:
  - The ETF is `SECTOR_ETF[sectors[symbol]]`. SPY regresses on nothing, so it is NaN. A missing ETF frame gives NaN.
  - Daily returns are aligned on common dates.
  - Fit OLS `r_sym = a + b * r_etf` on the 126 returns that end 60 returns before the last one.
  - The feature is the sum of `(r_sym - a - b*r_etf)` over the last 60 returns.
  - Needs ≥ 187 aligned returns, else NaN.
- `sector_rel_mom_60`: `group_neutralize` of a one-row panel of `mom_60` over symbols with finite `mom_60`, grouped by sector tag.
- Ranks: `Series.rank(pct=True)` across symbols with finite values; others NaN.
- `spy_above_200 = 1.0 if SPY close[-1] > mean(last 200 closes) else 0.0` (NaN if < 200 rows)
- `spy_vol20_pct` = percentile of the current SPY `vol_20` within the last 252 rolling `vol_20` values (NaN if short).

- [ ] **Step 1: Failing tests.** Build a synthetic 320-session daily panel for 6 symbols in 2 sectors plus SPY and 2 sector ETFs (deterministic trends). Tests:
  - `test_prefix_invariance`: appending 30 extra future sessions (random values) leaves `cross_section(..., as_of=d)` identical, via `pd.testing.assert_frame_equal`.
  - `test_rows_after_as_of_are_ignored`.
  - `test_short_direction_inverts_directional_ranks_only`.
  - `test_short_history_gives_nan_not_error`.
  - `test_resid_mom_uses_only_prior_window`: perturbing returns older than 186 sessions does not change it; perturbing the regression window does.
  - `test_sector_rel_mom_is_group_demeaned_before_rank`.
  - `test_setup_vector_one_hot_and_keys`.
  - `test_sector_etf_covers_every_config_sector`: load `config/config.yaml` with `yaml.safe_load`, collect all `sector` tags under `universe.groups`, and assert every tag is in `SECTOR_ETF`.
- [ ] **Step 2:** Run and confirm RED.
- [ ] **Step 3:** Implement, vectorized per symbol. No I/O except in tests.
- [ ] **Step 4:** Confirm GREEN.
- [ ] **Step 5:** `git add`.

### Task 3: Live-clock replay

**Files:**
- Create: `agentic_trader/research/setups/replay.py`
- Test: `tests/research/setups/test_replay.py`

**Interfaces — Consumes:**
- `MarketDataFetcher.compute_daily_indicators(df)`, `compute_intraday_indicators(df)`, `resample_to_4h(df_1h)` (`agentic_trader/data/market_data.py`). Construct a fetcher that does no I/O. Check its `__init__`; if it builds providers eagerly, call these as unbound methods or factor them. Do not change live behaviour.
- `ContractMarketData(contract=, ticker=, daily=, four_hour=, hourly=)`.
- `StrategyEngine(config).scan_contract(data, asset_class=AssetClass.EQUITY)` (`agentic_trader/screeners/strategies.py`).
- `RiskEvaluator(config, calendar=<stub>, regime_detector=<stub>).calculate_levels_deterministic(candidate)` → `.stop_loss`, `.take_profit`, `.stop_distance`. It is pure given config. Pass stub collaborators so that construction does no I/O.
- `MarketCalendarDay` from `agentic_trader/market/session.py` (fields for date/open/close; read the dataclass).

**Interfaces — Produces:**
```python
LIVE_DAILY_SESSIONS = 252      # live fetch_data daily_period="1y"
LIVE_HOURLY_DAYS = 60          # live fetch_data hourly_period="60d" (calendar days back from t)

@dataclass(frozen=True)
class SetupRecord:
    decision_at: datetime; symbol: str; strategy: str; timeframe: str; direction: str
    setup_quality: float; entry: float; stop: float; target: float; atr: float

def decision_instants(days: Sequence[MarketCalendarDay], scan_times_et: Sequence[str]) -> list[datetime]
# Each day × each "HH:MM" New York time → UTC instant, kept only if open <= instant < close (drops early-close afternoons).

def frames_at(symbol: str, daily_all: pd.DataFrame, hourly_all: pd.DataFrame, t: datetime) -> ContractMarketData
# Exactly what live fetch_data would have produced at t:
#   hourly = hourly_all bars with start + 1h <= t and start >= t - 60 calendar days → compute_intraday_indicators
#   four_hour = compute_intraday_indicators(resample_to_4h(<the same clean hourly slice>))
#   daily = last 252 completed sessions with session date < t's NY date, PLUS one in-progress bar for t's NY date
#           aggregated from that date's hourly slice bars (Open=first, High=max, Low=min, Close=last, Volume=sum), when any exist
#           (Alpaca returns the current day's daily bar intraday) → compute_daily_indicators
# Indicators are computed from scratch on these windows. EMA seeding depends on the window, so never precompute on full history.

def replay_symbol(symbol: str, daily_all, hourly_all, instants: Sequence[datetime], config: AppConfig,
                  dedup_hours: int) -> list[SetupRecord]
# For each instant: frames_at → StrategyEngine.scan_contract → for each candidate compute levels → SetupRecord.
# Live duplicate rule: skip if the same (symbol, strategy, timeframe) produced a record within the effective window,
# where effective = dedup_hours, min(dedup_hours, 4) for 1h, min(dedup_hours, 2) for 15m.
# Skip instants before 252 completed daily sessions exist.
```

- [ ] **Step 1: Failing tests.**
  - `test_decision_instants_convert_ny_and_drop_after_early_close`: a 13:00 close day drops the 14:35 instant; DST dates convert correctly.
  - `test_frames_at_hides_bars_ending_after_t`: a bar starting 10:00 NY is excluded at 10:35; at 11:00 it is included.
  - `test_frames_at_partial_4h_bucket_matches_live_resample`: equals `resample_to_4h` of the same slice.
  - `test_frames_at_in_progress_daily_bar`.
  - `test_future_perturbation_does_not_change_setups`: mutate all bars after t by ×1.5 and assert identical `replay_symbol` output up to t.
  - `test_dedup_matches_live_rule`.
  - `test_replay_uses_live_strategy_engine`: monkeypatch `StrategyEngine.scan_contract` to a spy and assert the call and `asset_class=EQUITY`.

  Use small synthetic frames. For the strategy-output tests, the spy returns a crafted `ScreenerCandidate` so the test does not depend on strategy thresholds.
- [ ] **Step 2:** Confirm RED.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Confirm GREEN.
- [ ] **Step 5:** `git add`.

### Task 4: Study statistics, selection and artifact lifecycle

**Files:**
- Create: `agentic_trader/research/setups/study.py`
- Create: `config/research/setup-outcomes-v1.json`
- Test: `tests/research/setups/test_study.py`

**Interfaces — Consumes:** Task 1 `BracketOutcome`, Task 3 `SetupRecord`, Task 2 feature key constants. `agentic_trader.storage.artifacts.save_json_report(obj, path)`.

**Interfaces — Produces:**
```python
class SetupStudyProtocol(BaseModel, frozen=True):
    version: Literal["setup_outcomes_v1"]
    universe_source: str                       # "config/config.yaml universe (current membership; survivorship caveat)"
    feed: Literal["alpaca:sip"]; adjustment: Literal["all"]
    scan_times_et: tuple[str, ...]             # ("10:35", "14:35")
    max_hold_sessions: int                     # 20
    cost_bps_per_side: tuple[float, ...]       # (0.0, 5.0); primary = 5.0 → "R_cost"
    development: tuple[date, date]             # (2021-06-01, 2026-01-29) decision dates inclusive; ≥20 sessions before holdout start
    holdout: tuple[date, date]                 # (2026-03-02, 2026-08-14)
    data_cutoff: date                          # 2026-09-22 — last date of bars fetched; validator: >= holdout[1] + ~max_hold_sessions sessions (use 30 calendar days)
    embargo_sessions: int                      # 20; validator: holdout[0] is >= embargo sessions after development[1] (business-day approximation is OK in the validator; the runner enforces with the real calendar)
    bootstrap: dict                            # {"block_mean": 10, "draws": 2000, "seed": 20260923}
    cv_folds: int                              # 5
    hgb_params: dict                           # {"max_depth": 3, "learning_rate": 0.05, "max_iter": 200, "min_samples_leaf": 50, "random_state": 20260923}
    ridge_alpha: float                         # 10.0
    logistic_C: float                          # 1.0
    acceptance: dict                           # {"top_k": 2, "min_sessions": 60, "ci": 0.90}
    features_version: Literal["setup_features_v1"]
    sector_etf: dict[str, str]                 # copy of features.SECTOR_ETF at freeze time; runner asserts equality
    strategy_config: dict                      # config/config.yaml `strategies:` section at freeze time; runner asserts equality with the loaded config
    @property identity -> str                  # sha256 of canonical JSON (sort_keys, separators=(",", ":"))

def session_block_bootstrap(values_by_session: pd.Series, stat: Callable[[pd.Series], float], *, block_mean: int, draws: int, seed: int) -> np.ndarray
# Stationary (Politis–Romano) bootstrap over the ordered sessions; returns `draws` statistics.

def spearman_by_session_bootstrap(frame: pd.DataFrame, feature: str, target: str, **boot) -> dict
# {"rho": float, "ci90": [lo, hi], "p_one_sided": float, "n_setups": int, "n_sessions": int}
# rho = Spearman over all setups; bootstrap resamples whole sessions (all setups of a session together).

def holm(pvalues: Mapping[str, float]) -> dict[str, float]

def top_k_selection(frame: pd.DataFrame, score: str, target: str, k: int) -> pd.Series
# per decision SESSION (NY date of decision_at — both scans of a day form one session): mean target of the k highest-score rows
# (ties broken by symbol, strategy); sessions with < 1 row absent. Returns Series indexed by session date.
def random_selection(frame, target, k) -> pd.Series   # expected value = session mean of target (analytic, no RNG)

SCORERS = ("setup_quality", "logistic", "ridge", "hgb")   # plus "random" baseline
def purged_walk_forward(sessions: Sequence[date], folds: int, embargo: int) -> list[tuple[list[date], list[date]]]
# Expanding window: split development sessions into folds+1 equal chunks; fold i trains on chunks[0..i] minus the
# last `embargo` sessions before test start, tests on chunk[i+1].

def fit_scorer(name, train: pd.DataFrame, features: Sequence[str], protocol) -> Fitted   # Fitted.predict(frame)->np.ndarray
# logistic: target=(hit == "target"); ridge/hgb: target=r_cost. Pipelines: SimpleImputer(median) + StandardScaler for linear; HGB native NaN.

def execute_setup_study(protocol: SetupStudyProtocol, directory: Path, *, development: Callable[[], pd.DataFrame],
                        holdout: Callable[[], pd.DataFrame], environment: dict) -> dict
```

**Lifecycle (in this order):**
1. `directory.mkdir(mode=0o700, parents=True, exist_ok=False)`. Save `protocol.json` (with identity), then `manifest.json` with protocol_id, environment, started_at, the `hypotheses` list (H1, H2a–H2h, H3) and `"authorizes_promotion": false`.
2. `dev = development()`. This is a frame with one row per labelled setup: the feature columns plus `decision_at, session, symbol, strategy, timeframe, direction, hit, r, r_cost`. IMMATURE rows are dropped with a count.
3. Save `development.json`:
   - H0 base rates by (strategy, timeframe, direction);
   - H1/H2 via `spearman_by_session_bootstrap` on `r_cost`, Holm-adjusted;
   - per-fold top-2 metric for every scorer plus random.

   The winner is the highest median fold metric among logistic/ridge/hgb. Ties go by the order in `SCORERS`.
4. Refit the winner on all development rows and save `ranker.json`:
   - model type, feature list and `features_version`;
   - for linear models, coefficients, intercept, imputer medians and scaler mean/scale;
   - for HGB, a `ranker.pkl` via `pickle` plus its sha256;
   - the training window and protocol identity.

   Save `selection.json` with the winner and the `ranker.json` sha256. **Only after both are saved**, call `holdout()`.
5. `hold = holdout()`. Save `holdout.json`:
   - top-2 per-session series for winner / setup_quality / random;
   - the paired difference winner − setup_quality with a session-block bootstrap 90% CI;
   - H1 on the holdout;
   - `acceptance_passed` = diff CI lo > 0 AND winner top-2 mean > 0 AND sessions with ≥2 setups ≥ 60 AND winner mean > setup_quality mean.
6. If `development()` raises, save `development.json` with `{"status": "failed", "error": ...}` and return **without** calling `holdout()`.

- [ ] **Step 1: Failing tests.** All on synthetic frames:
  - `test_protocol_identity_stable_and_changes_with_any_field`
  - `test_protocol_rejects_holdout_inside_embargo`
  - `test_bootstrap_deterministic_with_seed`
  - `test_bootstrap_resamples_whole_sessions`: a frame where one session holds all positive rows gives a wide CI
  - `test_top_k_groups_both_scans_of_a_day`
  - `test_random_selection_is_session_mean`
  - `test_purged_walk_forward_respects_embargo`: no train session within `embargo` sessions before test start, and train sessions strictly before test
  - `test_holm`
  - `test_holdout_not_called_before_ranker_saved`: the holdout callable asserts `(directory/"ranker.json").exists() and (directory/"selection.json").exists()`
  - `test_development_failure_leaves_holdout_unexamined`: the holdout callable raises if called; the dev callable raises; assert `development.json` has status failed
  - `test_planted_signal_detected`: a synthetic setup frame where one feature linearly drives r_cost. The winner's holdout diff CI lo > 0, and `acceptance_passed` is True when there are ≥60 sessions.
  - `test_null_signal_not_accepted`: pure noise gives `acceptance_passed` False with fixed seed.
- [ ] **Step 2:** Confirm RED.
- [ ] **Step 3:** Implement `study.py` and write `config/research/setup-outcomes-v1.json` with the values above. For `strategy_config`, copy the `strategies:` mapping from `config/config.yaml`. For `sector_etf`, use the Task 2 map.
- [ ] **Step 4:** Confirm GREEN.
- [ ] **Step 5:** `git add`.

### Task 5: Data acquisition runner + CLI

**Files:**
- Create: `agentic_trader/research/setups/runner.py`
- Modify: `agentic_trader/cli/commands/` — add `alpha setup-study`. Find how `alpha panel-study` is registered (grep `panel-study` in `agentic_trader/cli/`) and follow the same pattern and file placement.
- Test: `tests/research/setups/test_runner.py`, plus a CLI smoke test mirroring the existing panel-study CLI test style.

**Interfaces — Produces:**
```python
class BarSource(Protocol):
    def fetch_bars(self, symbol: str, timeframe: str, start: datetime, end: datetime, *, adjustment: str) -> pd.DataFrame
class CalendarSource(Protocol):
    async def get_calendar_range(self, start_date: date, end_date: date) -> list[MarketCalendarDay]

async def build_setup_frames(protocol, universe: Sequence[tuple[str, str]],   # (symbol, sector)
                             bars: BarSource, calendar: CalendarSource, cache_dir: Path,
                             config: AppConfig, *, max_workers: int) -> tuple[Callable[[], pd.DataFrame], Callable[[], pd.DataFrame], dict]
# 1. Fetch daily (1d) and hourly (1h) bars, adjustment="all", from (development[0] - 400 calendar days) through protocol.data_cutoff
#    (fixed in the protocol so the study is reproducible and labels mature), one symbol at a time via asyncio.to_thread, with the existing
#    market-data pacer if one is reachable (grep `pacer` in agentic_trader/data/), else a 0.35 s sleep between requests.
#    Cache each frame as parquet under cache_dir/{symbol}_{tf}.parquet; reuse the cache if present.
#    Record per-symbol failures in the returned `coverage` dict. A symbol missing either frame is EXCLUDED before any
#    labelling, with its reason.
# 2. Instants = decision_instants(calendar days in [development[0], holdout[1]], protocol.scan_times_et).
# 3. Replay (ProcessPoolExecutor(max_workers)) per symbol over ALL instants → SetupRecords (no labels yet).
# 4. Return two lazy callables:
#    development() = records with decision date in the development window → features (cross_section per decision date,
#    as_of = last session strictly before the decision's NY date, computed once per date over all symbols' daily frames)
#    + label_bracket for each primary/secondary cost → frame.
#    holdout() does the same for the holdout window.
#    Labels are computed only inside these callables.
```
- CLI `copilot alpha setup-study PROTOCOL --output NEW_DIR [--max-workers 6] [--cache DIR]`:
  - load `load_config()`;
  - assert `protocol.strategy_config == config.strategies` (dumped) and `protocol.sector_etf == features.SECTOR_ETF`, else exit non-zero with a clear message;
  - the universe is the config universe's equity/ETF members with their sectors;
  - bars come from the Alpaca provider with the feed forced to `sip`. Find how `data/providers.py` selects the feed and construct it explicitly; do not mutate the environment.
  - the calendar is the Alpaca calendar from `market/session.py`;
  - `environment` records the git revision, python version and package versions of pandas/sklearn/numpy.
- Refuse an existing `--output` directory.

- [ ] **Step 1: Failing tests** with a `FakeBarSource` (deterministic synthetic 1d/1h frames for 4 symbols + SPY + one sector ETF) and a `FakeCalendar`:
  - `test_missing_symbol_excluded_before_labelling`
  - `test_cache_reused_without_refetch`
  - `test_labels_only_computed_inside_callables`: patch `label_bracket` with a counter; zero calls until `development()`
  - `test_holdout_callable_only_labels_holdout_window`
  - `test_cli_refuses_existing_output_and_strategy_config_mismatch`
- [ ] **Step 2:** Confirm RED.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Confirm GREEN.
- [ ] **Step 5:** `git add`.

### Task 6 (controller): run the study on real data and write the result doc

Not delegated.
1. Run `env -u VIRTUAL_ENV uv run copilot alpha setup-study config/research/setup-outcomes-v1.json --output ~/agentic-trader-research/setup-outcomes-v1-<date>` outside the repo, private, mode 700.
2. Write `docs/setup-outcomes-2026-09-XX.md` covering:
   - the protocol hash;
   - coverage and exclusions;
   - H0 base rates;
   - H1/H2 with Holm;
   - CV fold table;
   - the frozen winner;
   - the holdout result and the acceptance verdict;
   - caveats (survivorship, adjusted-price proxies, hourly granularity, 11:00 entry approximation).
3. Update `docs/alpha-roadmap.md` WS2 status and `docs/alpha-expansion-survey-2026-09-23.md` if needed.
4. Commit, open PR-1, CI, merge.

---

## PR-2: live shadow + candidate journal + outcomes report

### Task 7: Shadow block at RANK + `scan_candidates_ranked` journal

**Files:**
- Modify: `agentic_trader/config.py`: `ScanConfig.shadow_ranker_artifact: Path | None = None` (find the scan config model that holds `max_cards_per_session`).
- Create: `agentic_trader/research/setups/ranker.py`, with `load_ranker(path) -> LoadedRanker | None` (verifies sha256 of the model file and `features_version == FEATURES_VERSION`; returns None and logs a warning on mismatch) and `LoadedRanker.score(vector: dict[str, float]) -> float`.
- Modify: `agentic_trader/execution/durable.py`: `EventKind.SCAN_CANDIDATES_RANKED = "scan_candidates_ranked"`.
- Modify: `agentic_trader/agent/copilot.py`, in the RANK/SEND section (`ranked = sorted(...)` through the end of the send loop).
- Test: `tests/agent/test_scan_shadow_ranker.py` (reuse fixtures from `tests/agent/test_scan_budget.py`).

**Behaviour:**
- After `ranked` is built, compute one `CrossSection` from the daily frames **already fetched in this scan**. Find where the fetch phase keeps per-contract `ContractMarketData` and reuse its `.daily`; never refetch. Use `as_of` = the last completed session before the scan's NY date, and sectors from the config universe.
- For each ranked candidate, build `setup_vector(...)`:
  - `stop_atr = |entry−stop|/atr_14` from its deterministic levels (`_det_res`);
  - `reward_risk = target_distance/stop_distance`.
- The shadow block is `{"features_version", "features", "score", "ranker_sha"}`. Score comes from the loaded ranker, else None.
- The whole computation sits in one try/except. On failure, log `event="shadow_ranker_failed"`, set `summary["shadow_ranker_error"]`, and continue with blocks = None.
- **Ranking order and budget behaviour must be byte-for-byte unchanged.**
- Add `"shadow_ranker": block` to `decision_provenance` of recorded signals.
- After the loop, for non-dry scans with `budget != ScanBudget.NONE` and at least one ranked candidate, append one domain event:
  - `stream=f"scan/{et_date}"`, `kind=EventKind.SCAN_CANDIDATES_RANKED`, `key=f"scan_candidates_ranked/{scan_id}"`;
  - payload `{scan_id, decided_at (UTC iso), budget, ranking_key: "setup_quality", candidates: [...]}`;
  - each candidate: contract, strategy, timeframe, direction, entry, stop, target, atr_14, setup_quality, rank, outcome (`"sent"` or the runner-up reason string), shadow.

  Use the workflow journal's `append` inside its own `session.begin()` with the scope lock, the same pattern as `record_health` in `storage/workflow.py`. Find the journal instance the copilot already holds (grep `record_health(`). A journal failure is logged (`event="scan_journal_failed"`) and never raises.
- The scan_id comes from the existing scan summary if one exists (grep `scan_id`), else a `uuid4().hex`.

- [ ] **Step 1: Failing tests:**
  - `test_rank_order_unchanged_with_shadow_block`: same fixtures, with and without an artifact; identical sent contracts and order
  - `test_sent_card_provenance_has_shadow_block`
  - `test_runners_up_and_sent_are_journaled_once`
  - `test_journal_failure_does_not_block_card`
  - `test_artifact_mismatch_records_features_only`
  - `test_dry_run_does_not_journal`
  - `test_feature_failure_is_contained`
- [ ] **Step 2:** Confirm RED.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Confirm GREEN.
- [ ] **Step 5:** `git add`.

### Task 8: `copilot cards outcomes` + docs

**Files:**
- Create or modify a CLI command module following existing command placement (grep for the `db queue` command registration to find the pattern). Command: `copilot cards outcomes [--days 30]`.
- Create: `agentic_trader/research/setups/outcomes.py`, with `label_journaled(events, bars: BarSource, max_hold_sessions=20, cost_bps=5.0) -> pd.DataFrame` and `summarize(frame) -> dict`.
- Modify: `docs/production.md`: a "Shadow card ranker" subsection under suggestion scans (what is recorded, where, how to read `cards outcomes`, that ranking is unchanged).
- Test: `tests/research/setups/test_outcomes.py`, plus a CLI smoke test.

**Behaviour:**
- Read `scan_candidates_ranked` events from the last N days via the journal's `events(stream=...)` reader (read-only; no lock needed).
- For each candidate, fetch 1h bars (adjustment raw, the live feed) from the decision time to now via the existing provider. Label with `label_bracket(max_hold_sessions=20, cost_bps_per_side=5.0)`.
- Print:
  - counts by outcome (sent vs runner-up), with IMMATURE rows counted separately;
  - base rates and mean R_cost;
  - per-session top-1/top-2 mean R_cost for `setup_quality`, shadow `score` (when present) and random.
- No orders, no Telegram, no writes.

- [ ] **Step 1: Failing tests:** `test_label_journaled_marks_immature`, `test_summary_selection_by_scorer`, `test_cli_outputs_table_with_fake_sources`.
- [ ] **Step 2:** Confirm RED.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Confirm GREEN.
- [ ] **Step 5:** `git add`.

After Task 8 the controller commits and opens PR-2, runs CI, merges, deploys through the controlled-restart procedure and verifies.

The deployment check: after the next suggestion scan, `copilot db` / events show one `scan_candidates_ranked` event and the card's provenance has `shadow_ranker`.
