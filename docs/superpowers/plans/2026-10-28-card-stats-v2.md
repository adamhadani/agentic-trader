# card_stats v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every native card states a posterior expected excess (beta-matched SPY, per-name cost) per
(strategy, timeframe, direction). The posterior combines a frozen replay prior with a time-matured,
de-duplicated live record, and `card_policy` previews on it.

**Architecture:**
- One pure unit module (`net_excess.py`) is shared by the labeller, the prior builder and `card_stats`.
- A frozen, digest-pinned prior file is built offline from the bracket-counterfactual frame, with a pure
  estimator and posterior (`card_prior.py`).
- `card_stats` v2 selects the population and maturity, and writes prior and posterior per key into the
  daily snapshot.
- Cards and `card_policy` read only the snapshot.

**Tech Stack:** Python 3.14, uv, pandas, numpy, pydantic, pytest.

**Spec:** `docs/superpowers/specs/2026-10-28-card-stats-v2-design.md` (binding).

## Global Constraints

- **Unit id:** `NET_EXCESS_UNIT = "net_excess_r_v1"`.
- **Constants** in `net_excess.py`, recorded in the prior file and checked against it on load:
  - `COST_FLOOR_BPS = 5.0`
  - `IMPACT_COEFFICIENT = 1.0`
  - `REFERENCE_RISK_USD = 1000.0`
  - `COST_CAP_BPS = 200.0`
  - `COST_WINDOW_SESSIONS = 20`
  - `COST_MIN_SESSIONS = 15`
  - `R_CLIP = 10.0`
- **Unit formula:**
  - `cost_r = 2·c/1e4·fill/risk_unit`
  - `r_net = clip(r − cost_r, ±10)`
  - `net_excess_r = clip(r_net − β·market_r, ±10)`
  - `fill` = the labeller's entry-bar open; `risk_unit = |planned entry − planned stop|`.
- **Cost formula:**
  - `c = min(COST_CAP_BPS, COST_FLOOR_BPS + IMPACT_COEFFICIENT·σ·sqrt(N/ADV)·1e4)`
  - `N = REFERENCE_RISK_USD·fill/risk_unit`
  - With σ or ADV missing, `c = COST_FLOOR_BPS`.
- **Prior constants:**
  - `PRIOR_VERSION = "card-stats-prior-v1"`
  - `PRIOR_MIN_ROWS_PER_QUARTER = 20`
  - `PRIOR_MIN_QUARTERS = 8`
  - `PRIOR_TAU_FLOOR = 0.05`. This is a controller ruling beyond the spec: it keeps a prior from becoming
    immovable when the moment estimate is 0.
- **Posterior:**
  - `v0 = tau² + se_session²`
  - `k = sd²/v0`
  - `posterior = (n·x̄ + k·mean)/(n + k)`
  - `post_se = sqrt(1/(n/sd² + 1/v0))`
  - n = 0 gives the prior.
- **Population:** `trigger == "suggestion_scan"`, `scope == "universe"`, contract not in
  `scan.broad_index_etfs`.
- **De-duplication:** one row per (contract, strategy, timeframe, direction, NY decision date), earliest
  decision kept.
- **Maturity:** eligible iff the count of exchange sessions strictly after the decision date and strictly
  before the snapshot's NY date is ≥ `max_hold_sessions + 1` (21). Count with
  `MarketHolidayCalendar.is_equity_holiday`, weekdays only.
- **Daily bars** (beta and cost inputs) come from SIP in every caller (`build_daily_bar_source`). Hourly
  labelling keeps the configured feed.
- **Snapshot key** becomes `card_stats/net_excess_r_v1/{et_date}`, on the same stream `card_stats`. A
  snapshot whose `unit` is not `net_excess_r_v1` is read as `unavailable`.
- **Shipped config:**
  - `card_policy.mode: "preview"`
  - `min_measured_ev: 0.0`
  - `stats_window_days: 180`
  - `prior_path: config/research/card-stats-prior-v1.json`
  - `prior_sha256: <digest of the committed file>`
- **Messages:** type names only in readiness, logs and notices (`CardStatsUnavailable` text). Imports at
  module top; no `dict()` calls (ruff C408).
- **Tests:** temp SQLite, blocked network, `test_*` PostgreSQL only. No migration. No change to `risk/`,
  admission, sizing or broker code.

## Review Focus

1. **The first scan after deploy reads yesterday's v1 snapshot**, which has no `unit`. Every card must
   show "no statistics in this scope" and `decide` must never withhold. The worker must not skip
   today's run because a v1 key exists. Task 3 owns the key; Task 4 owns the read.
2. **Delivery retries re-render an older notification** whose stored `card_evidence` is a v1 payload.
   `parse_card_evidence` must still validate it, since all new fields default, and it renders as "no
   statistics". Task 4.
3. **A live key with no prior** (a SHORT direction or a new strategy) gets a snapshot row with
   `prior_source == "skeptical"`, and the card reads "no replay prior (shrunk toward 0)". Tasks 3 and 4.
4. **The SPY hourly or SPY daily read fails**: the worker raises `CardStatsUnavailable` and writes
   nothing. A single failed symbol only makes its key partial. Task 3.
5. **The same contract in two timeframes in one session** stays two rows (the de-duplication key has
   the timeframe). Task 3.

---

### Task 1: The net-excess unit, per-name cost and SIP daily bars in the labeller

**Files:**
- Create: `agentic_trader/research/setups/net_excess.py`, `tests/research/setups/test_net_excess.py`
- Modify:
  - `agentic_trader/research/setups/outcomes.py` (`_COLUMNS`, `_row`, `_label_one`, `label_journaled`,
    `_exposure`)
  - `agentic_trader/research/setups/sources.py` (`build_bar_source`, new `build_daily_bar_source`)
  - `agentic_trader/cli/commands/cards.py` (~L94)
  - `docs/card-evidence.md` ("Beta-matched and buy-and-hold controls")
- Test: `tests/research/setups/test_net_excess.py`, the existing outcomes tests file for
  `label_journaled`, and the cards CLI test.

**Interfaces:**
- Produces:
  - **Constants:** the constants above.
  - **`CostInputs`:** `@dataclass(frozen=True) class CostInputs: sigma: float | None; adv_usd: float | None; reason: str | None`
  - **`cost_inputs`:** `cost_inputs(symbol_daily: pd.DataFrame | None, session: date) -> CostInputs`
    - Uses the last 21 finite closes dated strictly before `session` (NY date).
    - σ is the ddof=1 std of their 20 log returns. ADV is the median close·volume over the same 20
      later sessions.
    - With fewer than `COST_MIN_SESSIONS` valid returns, the reason is `"short_history"`; with no bars
      before the session, `"no_daily_bars"`.
  - **`name_cost_bps`:** `name_cost_bps(fill: float, risk_unit: float, inputs: CostInputs) -> float`
  - **`net_excess_r`:** `net_excess_r(r: float | None, fill: float | None, risk_unit: float | None, cost_bps: float, beta: float | None, market_r: float | None) -> float | None`.
    It is None when any input it needs is None or non-finite, or when `risk_unit <= 0`.
  - **`build_daily_bar_source`:** `build_daily_bar_source(config: AppConfig) -> AlpacaDataProvider`,
    which is `build_bar_source(config, feed="sip")`.
  - **`build_bar_source`:** gains a keyword `feed: str | None = None`; None means
    `config.market_data.alpaca_feed`.
  - **`label_journaled`:** gains a keyword `daily_bars: BarSource | None = None`. The `1d` beta fetches
    use it when given, else `bars`; the hourly and tape fetches always use `bars`.
  - **New frame columns:** `timeframe` (from the candidate), `fill_price` (the labeller's
    `entry_price`, None when unlabelled), `risk_unit`, `name_cost_bps`, `cost_reason` and
    `net_excess_r`.
    - `cost_reason` is the `CostInputs.reason`, or `"fetch_failed: {Type}"` when that symbol's daily
      read failed.
    - `name_cost_bps` is `COST_FLOOR_BPS` whenever the inputs are missing.
  - **`_exposure`:** gains `n_net_excess`, `mean_net_excess_r` and `mean_name_cost_bps`.

- [ ] **Step 1: Write the failing tests** in `tests/research/setups/test_net_excess.py`:
  - **`test_cost_floor_without_inputs`:**
    - `name_cost_bps(100.0, 2.0, CostInputs(None, None, "short_history")) == 5.0`
  - **`test_cost_square_root_impact`:**
    - Inputs: σ=0.02, ADV=5e7, fill 100, risk_unit 2.
    - N = 1000·100/2 = 50,000.
    - Expected: `5.0 + 0.02·sqrt(50_000/5e7)·1e4 == pytest.approx(5.0 + 6.324555, rel=1e-6)`.
  - **`test_cost_is_capped`:** ADV=1.0 gives `200.0`.
  - **`test_cost_inputs_use_only_past_sessions`:**
    - A synthetic daily frame where session D's bar has a huge volume.
    - `cost_inputs(frame, D)` ignores it. σ matches `np.std(np.diff(np.log(closes[-21:])), ddof=1)`
      on the 21 closes before D.
  - **`test_cost_inputs_short_history`:**
    - 10 bars before D gives `reason == "short_history"`.
    - None gives `"no_daily_bars"`.
  - **`test_net_excess_matches_flat_cost_and_beta_excess`:**
    - `r=1.3`, fill 100, risk_unit 2, cost 5.0, β 0.8, `market_r` 0.4.
    - Result is `pytest.approx(1.3 − 2·5/1e4·100/2 − 0.32)`.
  - **`test_net_excess_clips`:**
    - `r=50` gives `10.0 − β·market_r` (then clipped).
    - Expected: `net_excess_r(50, 100, 2, 5, 1.0, -20) == 10.0`.
  - **`test_net_excess_none_inputs`:** beta None, `market_r` None, `risk_unit` 0 and r NaN all give
    None.
- [ ] **Step 2: Add the outcomes tests** beside the existing `label_journaled` tests, using the same
  fake `BarSource`:
  - **`test_label_journaled_adds_net_excess_columns`:**
    - The `timeframe` column equals the candidate's.
    - A resolved row has `fill_price == entry-bar open`.
    - Its `net_excess_r == pytest.approx(beta_excess_r)` when the cost inputs are missing, since cost
      is then the floor and it is inside ±10R.
  - **`test_label_journaled_daily_bars_source`:**
    - A distinct `daily_bars` fake receives every `"1d"` `adjustment="all"` request.
    - `bars` receives none of them.
  - **`test_daily_fetch_failure_reason`:**
    - The symbol's daily fake raises `ConnectionError`.
    - The row has `cost_reason == "fetch_failed: ConnectionError"` and `name_cost_bps == 5.0`.
  - **`test_exposure_reports_net_excess`:** `summarize(...)`'s exposure block has `mean_net_excess_r`
    and `n_net_excess`.
- [ ] **Step 3: Run** `uv run pytest tests/research/setups -q -k "net_excess or label_journaled or exposure"`.
  Expected: the new tests FAIL.
- [ ] **Step 4: Implement** `net_excess.py`.
  - Thread `CostInputs` through `label_journaled`, alongside `beta_for`, and from there into
    `_label_one` and `_row`.
  - Add the columns.
  - Update `cards.py` to pass `daily_bars=build_daily_bar_source(config)`.
  - Extend `_exposure`.
  - Add one paragraph to the `docs/card-evidence.md` controls section: the unit, the SIP daily source,
    the cost formula and its floor, and the Abdi–Ranaldo ruling in one sentence citing the spec §4.2.
- [ ] **Step 5: Run**:
  - `uv run pytest tests/research tests/cli -q`
  - `uv run pytest -q`
  - `uv run pre-commit run --all-files`

  Expected: all pass.
- [ ] **Step 6: Stage** the changed files (`git add`). Do not commit.

### Task 2: The frozen replay prior

**Files:**
- Create:
  - `agentic_trader/research/setups/card_prior.py`
  - `scripts/card_stats_prior.py`
  - `config/research/card-stats-prior-v1.json`, generated
  - `docs/card-stats-prior-2026-10-28.md`
  - `tests/research/setups/test_card_prior.py`
- Modify:
  - `agentic_trader/config.py` (`CardPolicyConfig`)
  - `config/config.yaml` (`card_policy:`: `prior_path`, `prior_sha256` only; mode, threshold and
    window move in Task 4)
  - the shipped-config test

**Interfaces:**
- Consumes: Task 1's `cost_inputs`, `name_cost_bps`, `net_excess_r` and constants;
  `trailing_beta`; `load_dataset` (as `scripts/yardstick_erratum.py` uses it).
- Produces (`card_prior.py`):
  - `PRIOR_VERSION`, `PRIOR_MIN_ROWS_PER_QUARTER`, `PRIOR_MIN_QUARTERS`, `PRIOR_TAU_FLOOR`.
  - **`PriorEntry`:**
    `class PriorEntry(BaseModel, frozen=True, allow_inf_nan=False): strategy: str; timeframe: str; direction: str; n_rows: int; n_sessions: int; mean: float; sd: float; se_session: float; tau: float; tau_source: Literal["key", "pooled"]`
  - **`CardStatsPrior`:**
    `class CardStatsPrior(BaseModel, frozen=True, allow_inf_nan=False)` with:
    - `version: str`, `unit: str`;
    - `cost_model: dict[str, float]`, holding the six Task 1 cost constants by lower-case name;
    - `r_clip: float`, `beta_window_sessions: int`;
    - `tau_floor: float`, `min_rows_per_quarter: int`, `min_quarters: int`;
    - `source: dict[str, str]`: frame, `frame_sha256`, `protocol_sha256`, arm, feed, `window_start`,
      `window_end`;
    - `excluded_symbols: tuple[str, ...]`;
    - `pooled_sd: float`, `pooled_tau: float`, `pooled_n_rows: int`;
    - `keys: tuple[PriorEntry, ...]`;
    - `cost_bps_quantiles: dict[str, float]`;
    - `n_no_beta: int`, `code_revision: str`.

    It has `entry(strategy, timeframe, direction) -> PriorEntry`, which returns the matching key or,
    when none matches, a skeptical `PriorEntry` with `mean=0.0`, `sd=pooled_sd`, `se_session=0.0`,
    `tau=pooled_tau`, `n_rows=0`, `n_sessions=0`, `tau_source="pooled"`. The caller tells the two
    apart with `is_replay(strategy, timeframe, direction) -> bool`.
  - **`PriorUnavailable(RuntimeError)`:** its message is type names or fixed text only.
  - **`load_prior`:** `load_prior(path: Path, sha256: str | None) -> CardStatsPrior`. It raises
    `PriorUnavailable` when:
    - the sha is None;
    - the file is missing or unreadable;
    - the digest of the raw bytes mismatches;
    - validation fails;
    - `unit != NET_EXCESS_UNIT`;
    - `cost_model`, `r_clip` or `beta_window_sessions` differ from the Task 1 constants.
  - **`estimate_prior`:**
    `estimate_prior(rows: pd.DataFrame) -> tuple[tuple[PriorEntry, ...], float, float, int]`.
    - Input columns: `strategy`, `timeframe`, `direction`, `session` (ISO date), `net_excess_r`.
    - It returns `(keys, pooled_sd, pooled_tau, pooled_n_rows)` over rows with a finite `net_excess_r`.
    - `tau² = max(var(quarter means, ddof=1) − mean(var_q/n_q), 0)`, over calendar quarters with
      ≥ `PRIOR_MIN_ROWS_PER_QUARTER` rows. `tau = max(sqrt(tau²), PRIOR_TAU_FLOOR)`.
    - A key with fewer than `PRIOR_MIN_QUARTERS` such quarters takes `pooled_tau`, computed the same
      way over all rows.
    - `se_session` is the ddof=1 std of per-session means over sqrt(n_sessions), and 0.0 when there is
      one session.
  - **`prior_rows`:**
    `prior_rows(frame: pd.DataFrame, dailies: Mapping[str, pd.DataFrame | None], market_daily: pd.DataFrame) -> pd.DataFrame`.
    - Input: the frozen frame's `A0_live` rows minus excluded symbols.
    - It adds `beta`, `beta_reason`, `name_cost_bps` and `net_excess_r` per row, using `trailing_beta`
      and the Task 1 functions. `fill = entry_price` and `risk_unit = |entry − stop|`.
  - **`posterior`:**
    `posterior(n: int, live_mean: float | None, entry: PriorEntry) -> tuple[float, float, float]`
    returns `(posterior_mean, posterior_se, prior_weight_k)`.
- Produces (config): `CardPolicyConfig.prior_path: str = "config/research/card-stats-prior-v1.json"`
  and `prior_sha256: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")`. The path resolves the
  same way `risk.macro_calendar_path` does.

- [ ] **Step 1: Write the failing tests** in `tests/research/setups/test_card_prior.py`:
  - **`test_posterior_is_prior_at_zero`:** with `n=0`, the posterior mean equals the prior mean and
    `se == sqrt(tau² + se_session²)`.
  - **`test_posterior_weights`:**
    - Inputs: `sd=1.5`, `tau=0.25`, `se_session=0.0`, `n=20`, `x̄=0.10`, `mean=-0.05`.
    - `k = 2.25/0.0625 = 36`.
    - `posterior == pytest.approx((20·0.10 + 36·(−0.05))/56)`.
    - `se == pytest.approx(sqrt(1/(20/2.25 + 1/0.0625)))`.
  - **`test_estimate_prior_hand_computed`:**
    - A synthetic frame: one key, 9 quarters × 25 rows. Quarter means are drawn so the expected tau is
      computable, with fixed values and no RNG. Hand-compute tau.
    - Assert `mean`, `sd`, `se_session` and `tau` within 1e-9.
    - `tau_source == "key"`.
  - **`test_estimate_prior_sparse_key_uses_pooled_tau`:** a second key with 3 quarters gets
    `tau_source == "pooled"`.
  - **`test_tau_floor`:** identical quarter means give `tau == 0.05`.
  - **`test_skeptical_entry_for_unknown_key`:**
    - `entry("TREND_PULLBACK", "4h", "SHORT").mean == 0.0`.
    - `is_replay(...)` is False.
  - **`test_load_prior_refuses_digest_mismatch`**, plus missing-file, None-sha, unit-mismatch and
    cost-model-mismatch variants. Each raises `PriorUnavailable`, whose message contains no file
    content.
  - **`test_prior_rows_excess`:** on a two-symbol synthetic frame with synthetic dailies, `net_excess_r`
    equals the Task 1 function applied by hand.
  - **`test_shipped_prior_matches_config`:**
    - `load_prior(repo path, load_config(...).card_policy.prior_sha256)` validates.
    - It has a `TREND_PULLBACK/4h/LONG` replay entry.
    - Add it to the shipped-config test file.
- [ ] **Step 2: Run** `uv run pytest tests/research/setups/test_card_prior.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `card_prior.py`, the config fields and `scripts/card_stats_prior.py`.
  - **Usage:** `uv run python scripts/card_stats_prior.py STUDY_DIR CACHE OUT_JSON`.
  - **The script, offline:**
    1. Reads `STUDY_DIR/frames/primary.csv.gz` and `protocol.json`, and SHA-256s both files' bytes.
    2. Keeps `arm == "A0_live"`.
    3. Excludes BIL, SHY and `load_config(...).scan.broad_index_etfs`.
    4. Loads each symbol's `CACHE/{SYM}_1d/*.npz` with `load_dataset`, and SPY the same way.
    5. Calls `prior_rows` then `estimate_prior`.
    6. Writes the JSON:
       - sorted keys, `indent=2`;
       - `code_revision` from `git describe --always --dirty`;
       - `cost_bps_quantiles` p50, p90, p99 and max over rows;
       - `n_no_beta`;
       - the source window from the protocol's primary window.
    7. Prints the file's SHA-256.
  - The script never reads a database, a provider or the network.
- [ ] **Step 4: Generate the prior.** Run:

  ```
  uv run python scripts/card_stats_prior.py \
    ~/agentic-trader-research/bracket-counterfactual-v1-20261018 \
    ~/agentic-trader-research/setup-cache-v1 \
    config/research/card-stats-prior-v1.json
  ```

  - Set `card_policy.prior_path` and `prior_sha256` in `config/config.yaml` to the printed digest.
  - Write `docs/card-stats-prior-2026-10-28.md`: the provenance, the per-key table (n_rows,
    n_sessions, mean, sd, se_session, tau, the implied prior weight `k`), the cost quantiles, the
    `n_no_beta` count, and the statement that the prior never includes live rows.
  - Expected: the `TREND_PULLBACK/4h/LONG` mean is near −0.05R, and the `n_rows` values are within a
    few rows of 7,049 / 1,087 / 114. Report any large difference rather than tuning anything.
- [ ] **Step 5: Run** `uv run pytest -q` and `uv run pre-commit run --all-files`. Expected: pass.
- [ ] **Step 6: Stage** the files, including the generated JSON. Do not commit.

### Task 3: card_stats v2 snapshot and worker

**Files:**
- Modify:
  - `agentic_trader/research/setups/card_stats.py`
  - `agentic_trader/research/setups/card_stats_worker.py`
  - `agentic_trader/execution/durable.py` (only the `ScanTrigger` docstring: `card_stats` now reads
    `trigger`)
- Test:
  - `tests/research/setups/test_card_stats.py`
  - `tests/agent/test_card_stats_worker.py`
  - `tests/execution/card_stats_fixtures.py`

**Interfaces:**
- Consumes: Task 1's frame columns; Task 2's `CardStatsPrior`, `load_prior`, `PriorUnavailable` and
  `posterior`; the `CardPolicyConfig` prior fields.
- Produces (`card_stats.py`):
  - **`card_stats_key`:** `card_stats_key(et_date: date) -> str` returns
    `f"card_stats/{NET_EXCESS_UNIT}/{et_date.isoformat()}"`.
  - **`CardPopulation`:** `@dataclass(frozen=True) class CardPopulation: events: list[dict[str, Any]]; malformed: dict[tuple[str, str, str], int]; considered: int`
  - **`card_population`:**
    `card_population(events: list[dict[str, Any]], *, excluded: frozenset[str]) -> CardPopulation`.
    - It returns copies of the suggestion-scan universe events with candidates filtered and
      de-duplicated.
    - A malformed candidate has no direction in LONG/SHORT, a non-finite entry or stop, or
      `entry == stop`. It is counted under its (strategy, timeframe, direction), with missing parts as
      `"?"`, and dropped.
  - **`complete_sessions_between`:** `complete_sessions_between(start: date, end: date) -> int`
    counts exchange sessions strictly between the two dates.
  - **`CardKeyStats`:** gains these fields, all defaulted so a v1 payload still validates:
    - `timeframe: str = "*"`
    - `n_young`, `n_unresolved`, `n_malformed`, `n_no_beta: int = 0`
    - `live_mean`, `live_se`, `posterior_mean`, `posterior_se`, `prior_mean`, `prior_se`,
      `prior_weight`, `mean_name_cost_bps: float | None = None`
    - `prior_n_rows: int = 0`
    - `prior_source: Literal["replay", "skeptical"] | None = None`
  - **`CardStatsSnapshot`:** gains these fields, defaulted:
    - `unit: str | None = None`
    - `prior_sha256`, `prior_window_start`, `prior_window_end`, `prior_feed: str | None = None`
    - `cohort_end: date | None = None`
    - `n_malformed: int = 0`

    Its `stats` becomes `stats(strategy: str, timeframe: str, direction: str) -> CardKeyStats | None`.
  - **`compute_card_stats`:**
    `compute_card_stats(frame, *, prior: CardStatsPrior, prior_sha256: str, malformed: Mapping[tuple[str, str, str], int], window_start, window_end, feed, max_hold_sessions, code_revision, now, events_considered) -> dict[str, Any]`.
    - `cost_bps_per_side` is written as `COST_FLOOR_BPS`.
    - Keys are the union of live keys, prior keys and malformed keys, plus `("*", "*", "*")`, which has
      no prior or posterior fields.
  - **Per key:**
    - **Eligible rows:** the maturity rule against `window_end`.
    - **`n_young`:** the rows that are not eligible.
    - **`n_unresolved`:** eligible rows with hit `immature`.
    - **`n_fetch_failed`:** eligible rows with hit `fetch_failed`, or whose `beta_reason` or
      `cost_reason` starts with `fetch_failed`.
    - **`n_no_beta`:** eligible, resolved rows with `net_excess_r` None that are not fetch failures.
    - **`n_mature`:** eligible rows with a finite `net_excess_r`.
    - **Rates, `mean_r_cost`, holding and decided-at** are computed over the `n_mature` rows.
      `n_immature = n_young + n_unresolved`.
    - **`live_mean`, `live_se`:** the mean of `net_excess_r` and its ddof=1 std over √n, which is None
      below 2 rows.
    - **`posterior_*`, `prior_*`:** from `prior.entry(...)` and `posterior(...)`. `prior_se = sqrt(v0)`.
- Worker:
  - **`CardStatsWorker.__init__`:** gains `daily_bar_source: Callable[[AppConfig], BarSource] = build_daily_bar_source`.
  - **`_snapshot`:**
    1. Loads the prior with `load_prior(resolved prior_path, policy.prior_sha256)`. A `PriorUnavailable`
       raises `CardStatsUnavailable(f"card statistics prior unavailable: {type(exc).__name__}")`.
    2. Builds `card_population(events, excluded=frozenset(config.scan.broad_index_etfs))`.
    3. Calls `label_journaled(population.events, ..., daily_bars=self.daily_bar_source(self.config), with_beta=True, tape_symbol=None)`.
    4. Raises `CardStatsUnavailable` when the frame is non-empty and either:
       - every row has `market_reason` set (the SPY hourly read failed); or
       - every row's `beta_reason` starts with `fetch_failed` (the SPY daily read failed, or every
         symbol's did).

       The message names exception types only. The existing all-hourly-failed rule stays.
    5. Calls `compute_card_stats` with `events_considered=population.considered`.

- [ ] **Step 1: Write the failing tests** in `tests/research/setups/test_card_stats.py`, using small
  hand-built frames plus a hand-built `CardStatsPrior` fixture in `tests/execution/card_stats_fixtures.py`:
  - **`test_population_suggestion_only_and_dedup`:**
    - One swing event plus two suggestion events on the same date: 10:35 and 14:35, same contract and
      key.
    - The result has one candidate, from the 10:35 event.
    - A broad-index contract (`SPY`) is dropped.
  - **`test_population_keeps_timeframes_apart`:** the same contract at `1h` and `4h` on the same
    session gives two candidates.
  - **`test_population_counts_malformed`:**
    - Candidates with `entry == stop`, or with direction None, land in `malformed` under their key.
    - They are not in `events`.
  - **`test_complete_sessions_between_skips_holidays`:**
    - `complete_sessions_between(date(2026, 11, 20), date(2026, 11, 30)) == 4`. Nov 23–25 and 27 are
      sessions; Nov 26 is Thanksgiving; weekends are excluded.
  - **`test_maturity_boundary`:**
    - A row with 21 complete sessions before `window_end` is counted in `n_mature`.
    - A row with 20 is in `n_young`.
  - **`test_young_resolved_row_excluded`:**
    - A stop-hit row decided 3 sessions before `window_end` is excluded from every statistic.
    - It counts in `n_young`. This is the censoring regression.
  - **`test_fetch_failures_partial`:**
    - An eligible row with `cost_reason="fetch_failed: ConnectionError"` counts in `n_fetch_failed`,
      not in `n_no_beta`.
    - An eligible row with `beta_reason="short_history"` counts in `n_no_beta`.
  - **`test_every_prior_key_has_a_row`:**
    - An empty live frame gives a row for each prior key.
    - Each row has `n_mature == 0`, `posterior_mean == prior_mean` and `prior_source == "replay"`.
  - **`test_unknown_key_skeptical`:** a live SHORT key's row has `prior_source == "skeptical"` and
    `prior_mean == 0.0`.
  - **`test_v1_payload_still_validates`:**
    - A v1-shaped payload (no `unit` or `timeframe`) validates as a `CardStatsSnapshot` with
      `unit is None`.
    - A v2 payload validates under a copy of the v1 model. Paste the v1 field list, as in
      `card_stats.py` at 0c02a97, into the test as a local model.
  - **`test_snapshot_key_has_unit`:** `card_stats_key(date(2026, 10, 28)) == "card_stats/net_excess_r_v1/2026-10-28"`.
- [ ] **Step 2: Add worker tests** in `tests/agent/test_card_stats_worker.py`, using the existing fakes:
  - **`test_worker_raises_without_prior`:** with `prior_sha256=None`, a `CardStatsUnavailable` whose
    message is exactly `"card statistics prior unavailable: PriorUnavailable"`, and nothing recorded.
  - **`test_worker_raises_on_market_failure`:** the SPY hourly fake raises, giving
    `CardStatsUnavailable` and nothing recorded.
  - **`test_worker_uses_sip_daily_source`:** the injected `daily_bar_source` fake receives the `1d`
    requests.
  - **`test_worker_recomputes_when_only_v1_key_exists`:** a v1 `card_stats/{date}` event for today
    exists, and `run_once` returns `"recorded"`.
- [ ] **Step 3: Run** `uv run pytest tests/research/setups/test_card_stats.py tests/agent/test_card_stats_worker.py -q`.
  Expected: FAIL.
- [ ] **Step 4: Implement** per the Interfaces.
  - Update `docs/card-evidence.md` "Card statistics snapshot": the population, de-duplication,
    maturity, counts, partial rule, prior, posterior, key format and failure modes.
  - Keep the existing `test_card_stats*` assertions that still hold. Rewrite those that encode v1
    censoring, and name each rewrite in the report.
- [ ] **Step 5: Run**:
  - `uv run pytest -q`
  - `TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_agentic_trader_codex_20260915 uv run pytest tests/integration --run-postgres -q`
  - `uv run pre-commit run --all-files`

  Expected: pass.
- [ ] **Step 6: Stage.** Do not commit.

### Task 4: Card evidence, card_policy on the posterior, wiring, shipped config and docs

**Files:**
- Modify:
  - `agentic_trader/execution/card_evidence.py`
  - `agentic_trader/execution/card_policy.py`
  - `agentic_trader/agent/copilot.py`: the lookup at ~L1536, the scan-level `card_policy` block at
    ~L2527, and the evidence comment at ~L1528
  - `config/config.yaml` (`card_policy`)
  - `agentic_trader/config.py` (the docstring of `min_measured_ev`)
  - `CLAUDE.md` (contract 8: the `card_policy` sentence and the `cards outcomes` exposure clause)
  - `docs/card-evidence.md` ("Evidence block", "Send policy", "Configuration")
  - `docs/alpha-roadmap.md` (1.2 done)
  - `docs/holistic-review-2026-10-21/README.md` (D3 status)
- Test:
  - `tests/execution/test_card_evidence.py`
  - `tests/execution/test_card_policy.py`
  - `tests/notifier/test_card_evidence_rendering.py`
  - `tests/agent/test_scan_card_evidence.py`
  - `tests/agent/test_scan_card_policy.py`
  - `tests/config/test_card_policy_config.py`
  - the shipped-config test

**Interfaces:**
- Consumes: Task 3's snapshot fields and `stats(strategy, timeframe, direction)`.
- Produces:
  - **`CardEvidence`** gains these fields, all defaulted:
    - `timeframe: str | None = None`
    - `unit: str | None = None`
    - `n_unresolved: int = 0`
    - `live_mean`, `posterior_mean`, `posterior_se`, `prior_mean: float | None = None`
    - `prior_n_rows: int = 0`
    - `prior_source: str | None = None`
    - `prior_window_start`, `prior_window_end: date | None = None`

    `implied_ev` is removed, and its callers are updated.
  - **`lookup`:** `lookup(snapshot, strategy, timeframe, direction, *, now, max_age, min_mature) -> CardEvidence`.
    - A snapshot with `unit != NET_EXCESS_UNIT` gives `unavailable`.
    - `insufficient` now carries the prior and posterior fields from the key's row.
    - `measured` requires `n_mature ≥ min_mature` and a finite `posterior_mean`.
  - **`decide`** uses `evidence.posterior_mean`.
    - Partial means `n_fetch_failed > 0 or n_unresolved > 0`.
    - `CardPolicyDecision` keeps its fields; `measured_ev` now carries the posterior.
    - Its reason is `f"card policy: expected excess {value:+.{p}f}R over {n} < {threshold:+.{p}f}R"`,
      with the existing two- or three-place rule.
  - **`journal_block`** and the scan-level block gain `"unit": NET_EXCESS_UNIT`. The all-null block
    has `"unit": None`.
  - **Card lines, exact:**
    - **Line 1:**
      `• {b('Expected excess')} ({strategy} {timeframe}, {direction}): {posterior:+.2f}R ± {post_se:.2f} vs beta-matched SPY after cost`
    - **Line 2** starts with two spaces, then the prior part, `" · "`, then the live part:
      - **prior part:** `replay {start.year}–{end.year % 100:02d}: {prior_mean:+.2f}R over {prior_n_rows:,} setups`,
        or `no replay prior (shrunk toward 0)` when `prior_source == "skeptical"`;
      - **live part, measured:** `live: {live_mean:+.2f}R over {n} mature since {since} ({t:.0%} target / {s:.0%} stop / {o:.0%} timeout)`;
      - **live part, insufficient:** `live: {n}/{min} mature, not yet decisive`;
      - when `n_fetch_failed + n_unresolved > 0`, append ` · {k} unreadable (never withheld)`.
    - **Caveat**, in italic in HTML:
      `Not validated alpha. Replay prior (SIP {start.year}–{end.year % 100:02d}) and live record ({feed}) on journaled candidates' deterministic brackets; mature = 21+ sessions old; per-name cost ≥ 5 bp/side.`
    - **`stale`, `unavailable`:** unchanged. Evidence with `unit is None` renders the existing
      "no statistics in this scope" line plus the caveat.
- **Config:** `config/config.yaml` `card_policy` gets `mode: "preview"`, `min_measured_ev: 0.0`
  (comment: "posterior expected excess R, net_excess_r_v1") and `stats_window_days: 180`.

- [ ] **Step 1: Write the failing tests:**
  - **`test_lookup_v1_snapshot_unavailable`.**
  - **`test_lookup_insufficient_carries_prior`:**
    - `n_mature=17`, `min=20`.
    - Status is `insufficient`, and `posterior_mean` equals the row's value.
  - **`test_lookup_by_timeframe`:** two rows that differ only in timeframe; each lookup gets its own.
  - **`test_card_lines_measured`, `test_card_lines_insufficient`, `test_card_lines_skeptical`:**
    exact strings, built from a fixture with posterior −0.06, se 0.10, prior −0.05 over 7049, live
    −0.12 over 23 since 2026-09-24, rates 0.43/0.52/0.04, window 2021-06-01..2026-01-29 and feed
    `iex`. Line 2 is:

    `  replay 2021–26: -0.05R over 7,049 setups · live: -0.12R over 23 mature since 2026-09-24 (43% target / 52% stop / 4% timeout)`

    Python formats the sign as `-`; the test expects what `:+.2f` produces.
  - **`test_card_lines_partial`:** the `· 2 unreadable (never withheld)` suffix.
  - **`test_card_html_escapes`:** a strategy name with `<` is escaped.
  - **`test_v1_notification_evidence_renders_no_statistics`:** a v1 `card_evidence` dict parses and
    renders the "no statistics" line.
  - **`test_decide_withholds_on_posterior`:**
    - Preview, threshold 0.0, measured with posterior −0.06 and n 23.
    - `would_withhold is True`, `withhold is False`.
    - The reason is `card policy: expected excess -0.06R over 23 < +0.00R`.
  - **`test_decide_never_on_partial_insufficient_stale`:** each case gives `would_withhold is False`.
  - **`test_journal_block_has_unit`.**
  - **`test_scan_passes_timeframe_to_lookup`:** wiring, using the existing scan harness.
  - **`test_shipped_card_policy`:** `load_config` gives preview, 0.0, 180, the prior path and a 64-hex
    sha.
- [ ] **Step 2: Run** the listed test files. Expected: FAIL.
- [ ] **Step 3: Implement** per the Interfaces.
  - Docs:
    - `docs/card-evidence.md`: the evidence block, send policy and configuration, including the
      operator consequence paragraph from spec §4.8 and the enforce caveat.
    - CLAUDE.md, contract 8: replace "withholding native cards only on complete measured evidence above
      a sample floor" with "withholding native cards only on complete, time-matured evidence above a
      sample floor, judged on the posterior expected excess over a beta-matched SPY after per-name
      cost (`net_excess_r_v1`, a frozen replay prior shrunk toward the live record;
      [card evidence](docs/card-evidence.md))". Note "ships `preview` at 0.0 since 2026-10-28".
    - Roadmap: mark 1.2 done.
    - Review README: mark D3 done.
- [ ] **Step 4: Run**:
  - `uv run pytest -q`
  - `TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_agentic_trader_codex_20260915 uv run pytest tests/integration --run-postgres -q`
  - `uv run pre-commit run --all-files`

  Expected: all pass.
- [ ] **Step 5: Stage.** Do not commit.
