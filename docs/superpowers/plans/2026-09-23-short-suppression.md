# Short-Suppression Test Implementation Plan

**Goal:** Run the predeclared short-suppression test (`docs/superpowers/specs/2026-09-23-short-suppression-test.md`) with the existing setup-study replay and labeler. If the predeclared rule passes, add a per-strategy `allow_short` switch.

## Global Constraints

- Worktree `/Users/adamhadani/Development/agentic-trader-shorts` (branch `research/short-suppression`). Never touch `/Users/adamhadani/Development/agentic-trader`.
- Run everything as `env -u VIRTUAL_ENV uv run …`. Tests do no network I/O.
- Reuse `research/setups` (runner, replay, labels, the study's bootstrap helpers). Do not change the behaviour of `alpha setup-study`; its tests must pass unchanged.
- Use TDD with a real RED run recorded. Before reporting, run `ruff check`, `ruff format --check`, the full pytest and pre-commit, all in the foreground.
- Stage only your files. Do NOT commit. No subagents.

### Task 1: Generic window acquisition + `alpha setup-baserates`

**Files:**
- Modify: `agentic_trader/research/setups/runner.py`.
  - Factor `build_setup_frames` so the fetch, replay and lazy-labelling machinery works for an explicit list of named decision windows plus a data cutoff: `build_window_frames(windows: Mapping[str, tuple[date, date]], *, data_cutoff, scan_times_et, adjustment, ...) -> tuple[dict[str, Callable[[], pd.DataFrame]], dict]`.
  - Keep `build_setup_frames(protocol, ...)` as a thin wrapper with identical behaviour. Its existing tests must stay green.
- Create: `agentic_trader/research/setups/baserates.py`.
  - `SetupBaseRateProtocol` (pydantic, frozen, identity hash) with:
    - `version: Literal["setup_baserates_v1"]`, `window: tuple[date, date]`, `data_cutoff: date`, `scan_times_et`, `max_hold_sessions`, `cost_bps_per_side` (a tuple, primary = last), `bootstrap`, `feed`, `adjustment`, `strategy_config`, `sector_etf`, `hypotheses` (text), `decision_rule` (text);
    - a validator requiring `data_cutoff ≥ window[1] + 30` days.
  - `execute_baserates(protocol, directory, *, frame: Callable[[], pd.DataFrame], environment) -> dict`:
    1. `mkdir(0o700, exist_ok=False)`;
    2. save `protocol.json` and `manifest.json`;
    3. call `frame()` once (labels computed inside);
    4. drop IMMATURE rows with a count.

    Then compute:
    - base rates per (strategy, timeframe, direction): n, hit mix, mean r and mean r_cost;
    - **S1:** short mean r_cost with a stationary session-block bootstrap (reuse `study.session_block_bootstrap`), reporting the 90% CI and a one-sided p;
    - **S2:** the paired long − short difference over sessions containing both a long and a short setup (the per-session difference of means, bootstrapped by session), reporting the CI and p;
    - `s1_holds = ci_hi < 0`, `s2_holds = ci_lo > 0`, `decision = "suppress_native_shorts" if both else "no_change"`.

    Save `result.json` with non-finite values written as null (reuse the finite-JSON sanitizer approach from `study.py`). Any failure is saved as `{status: failed}`.
- Create: `config/research/setup-baserates-short-v1.json`. The window is 2017-01-03 to 2021-04-30, the cutoff 2021-06-15, and every other value comes from the spec. `strategy_config` equals `config.yaml` `strategies:` and `sector_etf` equals `features.SECTOR_ETF`; add a test asserting this.
- Modify: `agentic_trader/cli/commands/alpha.py`. Add `alpha setup-baserates PROTOCOL --output NEW_DIR [--cache DIR] [--max-workers 6]`, mirroring `setup-study` (the same validation, clients and SIP feed).
- Tests: `tests/research/setups/test_baserates.py`.
  - Protocol identity and validation.
  - A planted negative-short and positive-long synthetic frame gives S1 and S2 holding and `decision=suppress_native_shorts`.
  - Noise gives `no_change` with a fixed seed.
  - The pairing uses only sessions that have both directions.
  - Non-finite values are saved as null.
  - A failure writes status failed.
  - `build_window_frames` labels only inside the callable, and only its window.
  - The CLI refuses an existing output directory.

### Task 2 (controller): run on real data, write the result doc, apply the decision

The controller:
1. runs the CLI (cache `~/agentic-trader-research/setup-cache-2017`) and writes `docs/setup-baserates-short-2026-09-23.md`;
2. **only if `decision == suppress_native_shorts`**, adds the `allow_short` switch (a TDD'd config field on the trend-pullback and squeeze-breakout configs, honoured in their `evaluate`) and sets it false in `config/config.yaml`;
3. updates the roadmap, opens a PR, merges, deploys and verifies.
