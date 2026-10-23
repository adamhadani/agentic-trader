# Yardstick (review item 1.1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `cards outcomes` reports a past-only beta-matched SPY control, an own-asset buy-and-hold
control and excess by asset class. BIL and SHY leave the native universe. The bracket-counterfactual
headline gets a descriptive erratum.

**Architecture:** A pure `trailing_beta` function sits in a new `research/setups/beta.py`.
`label_journaled` fetches adjusted daily bars per candidate symbol and for SPY, and adds per-row
columns. `summarize` adds blocks and takes an optional asset-class map, which the CLI builds from
config. A research-only script recomputes the erratum figures from the frozen frames and caches.

**Tech Stack:** Python 3.14, numpy/pandas, click, pytest.

**Spec:** `docs/superpowers/specs/2026-10-23-yardstick-design.md`

## Global Constraints

- Descriptive only. Nothing in `risk/`, admission, `card_policy`, `card_stats` or the evaluator reads
  the new columns.
- The beta-one `market_r` and `excess_r` columns, their summary keys, and the frozen bracket
  counterfactual decision are unchanged.
- No migration, no new runtime config keys, no daemon behaviour change other than the universe
  shrinking by BIL and SHY.
- `ETF_RESEARCH_UNIVERSE` stays unchanged.
- ruff strict (no C408, no PLC0415), mypy clean, `uv run pre-commit run --all-files`.
- Implementers stage only; the controller commits.

## Review Focus

1. A candidate whose first daily bar falls after `decision − 120 sessions` (a new listing): `beta`
   is `None` with `short_history`, and the row is still labelled. Test in Task 1.
2. A split inside the beta window: adjusted bars are required, so the raw hourly fetch must not
   leak into beta. Test in Task 1 by asserting the adjustment requested for the daily beta fetch.
3. SPY as a candidate: its own beta is about 1 and `hold_r == market_r`. Test in Task 1.
4. An empty frame: `summarize` returns the new keys with `None` or empty values, with no exception.
   Test in Task 2.
5. Universe-size assertions elsewhere in the suite (scan "selected 159" style fixtures): update
   them to the shipped config's new count, never by weakening the assertion. Task 2.

---

### Task 1: `trailing_beta`, per-row beta and buy-and-hold columns

**Files:**
- Create: `agentic_trader/research/setups/beta.py`
- Modify: `agentic_trader/research/setups/outcomes.py` (`_COLUMNS`, `_row`, `_label_one`,
  `label_journaled`)
- Test: `tests/research/setups/test_beta.py`, `tests/research/setups/test_outcomes.py` (extend)

**Interfaces:**
- Produces:
  - `BETA_WINDOW_SESSIONS = 120` and `BETA_MIN_OBSERVATIONS = 100`;
  - `trailing_beta(symbol_daily: pd.DataFrame, market_daily: pd.DataFrame, session: date, *,
    window: int = BETA_WINDOW_SESSIONS, min_observations: int = BETA_MIN_OBSERVATIONS) ->
    tuple[float | None, str | None]`, returning `(beta, reason)`. It uses the `Close` columns of
    bars indexed by timestamp; the date is the bar's New York date. The reason is `None` when beta
    is finite, else `short_history` or `no_daily_bars`.
  - New `_COLUMNS` after `excess_r`: `beta`, `beta_reason`, `beta_market_r`, `beta_excess_r`,
    `hold_r`, `timing_r`.
- `label_journaled`:
  - after the hourly fetches, it fetches `1d` bars with `adjustment="all"` for every candidate
    symbol (start = earliest decision − 200 calendar days) and once for SPY;
  - the inner `fetch` gains an `adjustment` parameter, defaulting to `"raw"`;
  - a failed fetch gives `beta_reason = "fetch_failed: <TypeName>"` on that symbol's rows (SPY
    failing sets it on every row).
- `_label_one` computes:
  - `hold_r = market_r(direction, entry, stop, entry_time, exit_time, hourly)` on the candidate's
    own hourly bars;
  - `timing_r = r_cost − hold_r`;
  - `beta_market_r = beta × market_value`;
  - `beta_excess_r = r_cost − beta_market_r`.

  Each is `None` when an input is missing. Immature and fetch-failed rows carry `None` for all six.

- [ ] **Step 1: Write the failing tests:**
  - `test_half_beta_series` (≈ 0.5 within 1e-9 on a constructed series);
  - `test_window_ends_before_decision_session`;
  - `test_short_history_none`;
  - `test_zero_market_variance_none`;
  - `test_alignment_by_date_with_missing_day`;
  - in `test_outcomes.py`:
    - `test_rows_carry_beta_and_hold_columns`;
    - `test_daily_beta_fetch_uses_adjusted_bars`, where the fake bar source records
      `(symbol, timeframe, adjustment)`;
    - `test_daily_fetch_failure_sets_beta_reason_only`;
    - `test_spy_candidate_beta_one_and_hold_equals_market`.
- [ ] **Step 2: Run them and see them fail**
  (`env -u VIRTUAL_ENV uv run pytest tests/research/setups/test_beta.py tests/research/setups/test_outcomes.py -q`).
- [ ] **Step 3: Implement.** Use `np.cov(x, y, ddof=1)[0, 1] / np.var(y, ddof=1)`.
- [ ] **Step 4: Run the tests and see them pass.** Then run `tests/research/setups -q`,
  `tests/cli -q -k cards`. Stage.

### Task 2: Summaries, asset classes, CLI wiring, universe change, docs

**Files:**
- Modify: `agentic_trader/research/setups/outcomes.py` (`_exposure`, `_counterfactual_summary`,
  `summarize`)
- Modify: `agentic_trader/config.py`: add `asset_class_map(universe: UniverseConfig) -> dict[str, str]`
  as a function beside `UniverseConfig`, or as a method `UniverseConfig.asset_classes()`; the
  implementer chooses and the plan accepts either
- Modify: `agentic_trader/cli/commands/cards.py` (pass the map)
- Modify: `config/config.yaml` (remove `{symbol: SHY, …}` and `{symbol: BIL, …}`; add the comment
  `# BIL and SHY removed 2026-10-23 (holistic review RM-R2): a 1.5x ATR stop on a T-bill fund implies
  hundreds of times notional per unit of risk.`)
- Modify: `docs/card-evidence.md` (new subsection "Beta-matched and buy-and-hold controls" under
  "Outcome classes and scan supply")
- Test: `tests/research/setups/test_outcomes.py`, `tests/config/test_shipped_config.py`, plus any
  test asserting the shipped universe size

**Interfaces:**
- `summarize(frame: pd.DataFrame, *, asset_classes: Mapping[str, str] | None = None) -> dict`.
- `_exposure` adds `n_beta`, `mean_beta`, `mean_beta_market_r`, `mean_beta_excess_r` (each over
  rows with a finite value), plus `mean_hold_r` and `mean_timing_r`.
- `_counterfactual_summary` arms add `mean_beta_excess_r`, defined as
  `mean(r_cost_arm − beta × arm_market_r)` over rows with both.
- `summary["by_asset_class"]` is `{class: {"n", "mean_r_cost", "mean_excess_r",
  "mean_beta_excess_r"}}` over mature rows. Without a map, every row is `unclassified`.

- [ ] **Step 1: Write the failing tests:**
  - `test_exposure_reports_beta_and_timing`;
  - `test_by_asset_class_with_map`;
  - `test_by_asset_class_unclassified_without_map`;
  - `test_counterfactual_beta_excess`;
  - `test_empty_frame_has_new_keys`;
  - `test_shipped_asset_classes`: XLK `etf_equity`, TLT `rates_credit`, GLD `metals`, AAPL
    `single_stock`, and BIL and SHY absent from both the map and `config.universe.symbols`.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.** Then update every universe-size assertion that the config change
  breaks (find them by running the full suite).
- [ ] **Step 4: Run the full suite, mypy and pre-commit.** Stage.

### Task 3 (controller): erratum and docs

- [ ] Write `scripts/yardstick_erratum.py`. It reads the frozen `frames/{primary,robustness}.csv.gz`
  and the daily `.npz` caches through the setup runner's cache reader, then prints spec §3's
  figures as JSON.
- [ ] Run it.
- [ ] Add the erratum section to `docs/bracket-counterfactual-2026-10-18.md`.
- [ ] Rewrite the CLAUDE.md clause.
- [ ] Update the holistic-review README's Phase 0 row.
- [ ] Final review, the fix wave, and the PR.
