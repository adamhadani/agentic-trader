# Setup recurrence test — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the predeclared setup-recurrence test (R1–R4, decision rule) over the setup-outcome replay frame, with a frozen protocol file and an `alpha setup-recurrence` command, so it can be run once on the two declared windows.

**Architecture:** `runner._build_frame` adds `entry_time`, `exit_time`, `holding_sessions` to the labelled frame. A new pure module `research/setups/recurrence.py` tags each row's recurrence state with the exit-bar-close no-lookahead rule, defines `SetupRecurrenceProtocol` (the base-rate protocol's shape plus a robustness window and recurrence parameters), computes R1–R4 with the shared stationary session-block bootstrap (paired draws for two-group comparisons) and writes the artifacts. The CLI replays each window from its own cache via `build_window_frames` and runs the test in a thread.

**Tech Stack:** Python 3.14, `uv`, pandas, numpy, pydantic v2 (frozen), click, pytest (network blocked, fakes only).

**Spec:** `docs/superpowers/specs/2026-10-14-setup-recurrence-design.md`

## Global Constraints

- Run everything with `env -u VIRTUAL_ENV uv run …` from the worktree `/Users/adamhadani/Development/agentic-trader-recur`; never touch `/Users/adamhadani/Development/agentic-trader`.
- ruff strict (C408: no `dict()`/`list()` where a literal works, PLC0415: imports at module top, RUF059, A002, UP038, RUF046); mypy must pass on touched modules; pristine test output. `env -u VIRTUAL_ENV uv run pre-commit run --all-files` before each commit (the ruff-format hook reformats fenced code in Markdown; re-add and recommit).
- Research only: no database, registry, broker or Telegram access; every artifact carries `authorizes_promotion: false`; no change to `run_scan`, ranking, `card_policy`, `card_stats`, `risk/`, cards.
- The setup-outcome study's identity and results are unchanged: `study.model_features` must return the same list for a frame with and without the three new columns.
- `frames` callables in `execute_recurrence` are each called exactly once, inside the try block; any failure writes `{"status": "failed", "error": "<Type>: <message>"}`.
- Protocol v1 values (spec §3.3 and §4) are binding and copied verbatim below.
- Implementers STAGE their files and report; they never commit and never dispatch subagents.

## Review Focus

1. A prior row of the same setup whose exit bar opened 30 minutes before this decision → not resolved → `repeat_open`, not `repeat` (Task 2 `test_prior_counts_only_once_its_exit_bar_has_closed`).
2. Two scans of one session producing the same setup → the earlier is never the later's prior (Task 2 `test_same_session_rows_are_never_priors`).
3. A holiday gap: sessions 20 trading days apart but 30 calendar days apart → inside a 20-session lookback (Task 2 `test_lookback_counts_sessions_not_calendar_days`).
4. A frame with no repeats at all → R1/R2 `NaN`, `holds` false, R4 false, decision `no_change`, no exception (Task 2 `test_no_repeats_is_no_change_without_error`).
5. The replay frame's new timestamp columns are tz-aware and survive a `label_bracket` outcome with `None` times (immature) (Task 1 `test_build_frame_writes_entry_exit_and_holding`).

---

### Task 1: Replay frame columns `entry_time`, `exit_time`, `holding_sessions`

**Files:**
- Modify: `agentic_trader/research/setups/runner.py` (`_FRAME_METADATA_COLUMNS` ~line 54; the `row.update({...})` in `_build_frame` ~line 340)
- Test: `tests/research/setups/test_runner.py` (append)

**Interfaces:**
- Produces: frame columns `entry_time` (tz-aware `datetime | None`), `exit_time` (same), `holding_sessions` (int) on every row `_build_frame` emits, from the zero-cost `BracketOutcome`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/research/setups/test_runner.py` (read its existing imports and helpers first; it already imports `runner`, `SetupRecord`, `BracketHit`, `BracketOutcome` and builds daily/hourly frames — reuse whichever helper constructs records and bar frames; if none builds a frame through `_build_frame` directly, use the pattern below with `monkeypatch`):

```python
from agentic_trader.research.setups.study import model_features


def test_build_frame_writes_entry_exit_and_holding(monkeypatch):
    """The labelled frame carries the zero-cost outcome's entry/exit instants and holding count."""
    decision_at = datetime(2024, 1, 3, 15, 35, tzinfo=UTC)
    record = SetupRecord(
        decision_at=decision_at,
        symbol="AAA",
        strategy="TREND_PULLBACK",
        timeframe="4h",
        direction="LONG",
        setup_quality=0.5,
        entry=100.0,
        stop=98.0,
        target=104.0,
        atr=1.0,
    )
    entry_time = pd.Timestamp("2024-01-03T16:30:00Z")
    exit_time = pd.Timestamp("2024-01-04T15:30:00Z")
    outcomes = iter(
        [
            BracketOutcome(BracketHit.TARGET, entry_time, 100.0, exit_time, 104.0, 2.0, 1.9, 2),
            BracketOutcome(BracketHit.TARGET, entry_time, 100.0, exit_time, 104.0, 2.0, 1.9, 2),
        ]
    )
    monkeypatch.setattr(runner, "label_bracket", lambda *args, **kwargs: next(outcomes))
    monkeypatch.setattr(
        runner,
        "_live_cross_section",
        lambda daily, sectors, as_of, instant: runner.live_cross_section(daily, sectors, as_of),
    )
    daily = {"AAA": _daily_frame(decision_at, days=260), "SPY": _daily_frame(decision_at, days=260)}
    hourly = {"AAA": _hourly_frame(decision_at)}
    frame = runner._build_frame(
        [record],
        daily,
        hourly,
        {"AAA": "technology", "SPY": "etf_broad_equity"},
        max_hold_sessions=20,
        cost_bps_per_side=(0.0, 5.0),
        trading_days=[date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
        first_instants={date(2024, 1, 3): decision_at},
    )
    assert list(frame.columns[-3:]) == ["entry_time", "exit_time", "holding_sessions"] or {
        "entry_time",
        "exit_time",
        "holding_sessions",
    } <= set(frame.columns)
    assert frame["entry_time"].iloc[0] == entry_time and frame["entry_time"].iloc[0].tzinfo is not None
    assert frame["exit_time"].iloc[0] == exit_time
    assert int(frame["holding_sessions"].iloc[0]) == 2
    assert set(runner._FRAME_METADATA_COLUMNS) >= {"entry_time", "exit_time", "holding_sessions"}


def test_immature_outcome_leaves_times_none(monkeypatch):
    decision_at = datetime(2024, 1, 3, 15, 35, tzinfo=UTC)
    record = SetupRecord(
        decision_at=decision_at,
        symbol="AAA",
        strategy="TREND_PULLBACK",
        timeframe="4h",
        direction="LONG",
        setup_quality=0.5,
        entry=100.0,
        stop=98.0,
        target=104.0,
        atr=1.0,
    )
    immature = BracketOutcome(BracketHit.IMMATURE, None, None, None, None, None, None, 0)
    monkeypatch.setattr(runner, "label_bracket", lambda *args, **kwargs: immature)
    monkeypatch.setattr(
        runner,
        "_live_cross_section",
        lambda daily, sectors, as_of, instant: runner.live_cross_section(daily, sectors, as_of),
    )
    daily = {"AAA": _daily_frame(decision_at, days=260), "SPY": _daily_frame(decision_at, days=260)}
    frame = runner._build_frame(
        [record],
        daily,
        {"AAA": _hourly_frame(decision_at)},
        {"AAA": "technology", "SPY": "etf_broad_equity"},
        max_hold_sessions=20,
        cost_bps_per_side=(0.0, 5.0),
        trading_days=[date(2024, 1, 2), date(2024, 1, 3)],
        first_instants={date(2024, 1, 3): decision_at},
    )
    assert frame["entry_time"].iloc[0] is None and frame["exit_time"].iloc[0] is None
    assert int(frame["holding_sessions"].iloc[0]) == 0


def test_model_features_ignore_the_new_columns():
    base = pd.DataFrame({"mom_60": [0.1], "setup_quality": [0.5], "strategy=TREND_PULLBACK": [1.0], "r_cost": [0.1]})
    extended = base.assign(entry_time=pd.Timestamp("2024-01-03T16:30:00Z"), exit_time=None, holding_sessions=2)
    assert model_features(extended) == model_features(base)
```

If `test_runner.py` has no `_daily_frame`/`_hourly_frame`, copy the two helpers from `tests/research/setups/test_baserates.py` (they are small) into `test_runner.py` rather than importing across test modules. If `runner._live_cross_section` has a different signature in the file, read it and adapt the monkeypatch so the cross-section is built from the daily frames; the assertions do not depend on feature values.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/setups/test_runner.py -q -k "entry_exit or immature_outcome or model_features_ignore"`
Expected: FAIL with `KeyError: 'entry_time'` (the first two) and PASS for the third (it pins current behaviour; keep it).

- [ ] **Step 3: Implement**

In `agentic_trader/research/setups/runner.py`:

```python
_FRAME_METADATA_COLUMNS: tuple[str, ...] = (
    "decision_at",
    "session",
    "symbol",
    "strategy",
    "timeframe",
    "direction",
    "hit",
    "r",
    "r_cost",
    # The zero-cost outcome's instants and holding count (the primary-cost outcome shares
    # them): consumed by research/setups/recurrence.py, ignored by the setup study.
    "entry_time",
    "exit_time",
    "holding_sessions",
)
```

and in `_build_frame`'s `row.update({...})`, after `"r_cost": outcome_primary.r_cost,`:

```python
                    "entry_time": outcome_zero.entry_time,
                    "exit_time": outcome_zero.exit_time,
                    "holding_sessions": outcome_zero.holding_sessions,
```

- [ ] **Step 4: Run the touched suites**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/setups/test_runner.py tests/research/setups/test_study.py tests/research/setups/test_baserates.py -q`
Expected: PASS.

- [ ] **Step 5: Lint, type-check, stage**

Run: `env -u VIRTUAL_ENV uv run ruff check agentic_trader/research/setups/runner.py tests/research/setups/test_runner.py && env -u VIRTUAL_ENV uv run ruff format --check agentic_trader/research/setups/runner.py tests/research/setups/test_runner.py && env -u VIRTUAL_ENV uv run mypy agentic_trader/research/setups/runner.py`
Expected: clean. Stage both files and report. Commit message: `Replay frame carries entry_time, exit_time and holding_sessions`.

---

### Task 2: `research/setups/recurrence.py` — recurrence state, protocol, R1–R4, executor

**Files:**
- Create: `agentic_trader/research/setups/recurrence.py`
- Test: `tests/research/setups/test_recurrence.py`

**Interfaces:**
- Consumes: Task 1's columns; `study._stationary_index_draws`, `study._drop_immature`, `study._finite_json`, `study._base_rates`; `storage.artifacts.save_json_report`.
- Produces: `BAR`, `STATES`, `PRIOR_OUTCOMES`, `HYPOTHESES = ("R1", "R2", "R3", "R4")`, `with_setup_recurrence(frame, *, lookback_sessions)`, `SetupRecurrenceProtocol`, `execute_recurrence(protocol, directory, *, frames, environment) -> dict`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/research/setups/test_recurrence.py
from __future__ import annotations

import gzip
import json
import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from agentic_trader.research.setups.recurrence import (
    BAR,
    HYPOTHESES,
    PRIOR_OUTCOMES,
    STATES,
    SetupRecurrenceProtocol,
    execute_recurrence,
    with_setup_recurrence,
)


def _row(
    symbol,
    session,
    decided,
    hit,
    *,
    exit_time=None,
    r_cost=0.0,
    strategy="TREND_PULLBACK",
    timeframe="4h",
    direction="LONG",
    r=None,
):
    return {
        "decision_at": pd.Timestamp(decided),
        "session": date.fromisoformat(session),
        "symbol": symbol,
        "strategy": strategy,
        "timeframe": timeframe,
        "direction": direction,
        "hit": hit,
        "r": r if r is not None else r_cost,
        "r_cost": r_cost,
        "entry_time": pd.Timestamp(decided) + pd.Timedelta("55min"),
        "exit_time": pd.Timestamp(exit_time) if exit_time else None,
        "holding_sessions": 1 if exit_time else 0,
    }


def _frame(rows):
    return pd.DataFrame(rows)


def test_constants():
    assert BAR == pd.Timedelta("1h")
    assert STATES == ("first", "repeat", "repeat_open")
    assert PRIOR_OUTCOMES == ("target", "stop", "timeout", "none")
    assert HYPOTHESES == ("R1", "R2", "R3", "R4")


def test_first_repeat_and_prior_outcome():
    rows = [
        _row("AAA", "2024-01-02", "2024-01-02T15:35Z", "stop", exit_time="2024-01-02T17:30Z", r_cost=-1.05),
        _row("AAA", "2024-01-04", "2024-01-04T15:35Z", "target", exit_time="2024-01-05T15:30Z", r_cost=1.9),
        _row("AAA", "2024-01-08", "2024-01-08T15:35Z", "timeout", r_cost=0.2),
        _row("BBB", "2024-01-08", "2024-01-08T15:35Z", "target", exit_time="2024-01-09T15:30Z", r_cost=1.9),
    ]
    out = with_setup_recurrence(_frame(rows), lookback_sessions=20)
    assert out["state"].tolist() == ["first", "repeat", "repeat", "first"]
    assert out["prior_outcome"].tolist() == ["none", "stop", "target", "none"]
    assert out["prior_sessions_k"].tolist() == [0, 1, 2, 0]
    assert out["prior_r_cost"].tolist()[:2] == [None, -1.05] or (
        math.isnan(out["prior_r_cost"].iloc[0]) and out["prior_r_cost"].iloc[1] == -1.05
    )


def test_prior_counts_only_once_its_exit_bar_has_closed():
    rows = [
        # Exit bar opens 30 minutes before the later decision: still open at that decision.
        _row("AAA", "2024-01-02", "2024-01-02T15:35Z", "target", exit_time="2024-01-03T15:05Z", r_cost=1.9),
        _row("AAA", "2024-01-03", "2024-01-03T15:35Z", "stop", exit_time="2024-01-03T17:30Z", r_cost=-1.0),
        # Exit bar opened exactly one bar before: closed.
        _row("AAA", "2024-01-04", "2024-01-04T14:35Z", "timeout", r_cost=0.1),
    ]
    out = with_setup_recurrence(_frame(rows), lookback_sessions=20)
    assert out["state"].tolist() == ["first", "repeat_open", "repeat"]
    assert out["prior_outcome"].tolist() == ["none", "none", "stop"]


def test_an_immature_prior_is_repeat_open():
    rows = [
        _row("AAA", "2024-01-02", "2024-01-02T15:35Z", "immature"),
        _row("AAA", "2024-01-03", "2024-01-03T15:35Z", "stop", exit_time="2024-01-03T17:30Z", r_cost=-1.0),
    ]
    out = with_setup_recurrence(_frame(rows), lookback_sessions=20)
    assert out["state"].tolist() == ["first", "repeat_open"]


def test_same_session_rows_are_never_priors():
    rows = [
        _row("AAA", "2024-01-02", "2024-01-02T15:35Z", "target", exit_time="2024-01-02T16:30Z", r_cost=1.9),
        _row("AAA", "2024-01-02", "2024-01-02T19:35Z", "stop", exit_time="2024-01-02T20:30Z", r_cost=-1.0),
    ]
    out = with_setup_recurrence(_frame(rows), lookback_sessions=20)
    assert out["state"].tolist() == ["first", "first"]
    assert out["prior_sessions_k"].tolist() == [0, 0]


def test_lookback_counts_sessions_not_calendar_days():
    # 21 distinct sessions spanning a holiday gap; the first row is 20 sessions before the last.
    sessions = [
        "2024-01-02",
        "2024-01-03",
        "2024-01-04",
        "2024-01-05",
        "2024-01-08",
        "2024-01-09",
        "2024-01-10",
        "2024-01-11",
        "2024-01-12",
        "2024-01-16",
        "2024-01-17",
        "2024-01-18",
        "2024-01-19",
        "2024-01-22",
        "2024-01-23",
        "2024-01-24",
        "2024-01-25",
        "2024-01-26",
        "2024-01-29",
        "2024-01-30",
        "2024-02-01",
    ]
    rows = [_row("AAA", sessions[0], f"{sessions[0]}T15:35Z", "stop", exit_time=f"{sessions[0]}T17:30Z", r_cost=-1.0)]
    rows += [_row("ZZZ", s, f"{s}T15:35Z", "timeout", r_cost=0.0) for s in sessions[1:-1]]
    rows += [
        _row("AAA", sessions[-1], f"{sessions[-1]}T15:35Z", "target", exit_time=f"{sessions[-1]}T18:30Z", r_cost=1.9)
    ]
    out = with_setup_recurrence(_frame(rows), lookback_sessions=20)
    assert out.loc[out["symbol"] == "AAA", "state"].tolist() == ["first", "repeat"]
    narrow = with_setup_recurrence(_frame(rows), lookback_sessions=19)
    assert narrow.loc[narrow["symbol"] == "AAA", "state"].tolist() == ["first", "first"]


def test_recurrence_is_per_setup_not_per_symbol():
    rows = [
        _row("AAA", "2024-01-02", "2024-01-02T15:35Z", "stop", exit_time="2024-01-02T17:30Z", r_cost=-1.0),
        _row("AAA", "2024-01-03", "2024-01-03T15:35Z", "stop", exit_time="2024-01-03T17:30Z", timeframe="1h"),
        _row(
            "AAA", "2024-01-04", "2024-01-04T15:35Z", "stop", exit_time="2024-01-04T17:30Z", strategy="SQUEEZE_BREAKOUT"
        ),
        _row("AAA", "2024-01-05", "2024-01-05T15:35Z", "stop", exit_time="2024-01-05T17:30Z", strategy=None),
    ]
    out = with_setup_recurrence(_frame(rows), lookback_sessions=20)
    assert out["state"].tolist() == ["first"] * 4


def test_unique_index_required_and_empty_frame_ok():
    frame = _frame([_row("AAA", "2024-01-02", "2024-01-02T15:35Z", "stop")])
    doubled = pd.concat([frame, frame])
    with pytest.raises(ValueError, match="unique index"):
        with_setup_recurrence(doubled, lookback_sessions=20)
    empty = with_setup_recurrence(_frame([]).reindex(columns=list(frame.columns)), lookback_sessions=20)
    assert empty.empty and {"state", "prior_outcome", "prior_sessions_k", "prior_r_cost"} <= set(empty.columns)


# --- Protocol ---------------------------------------------------------------------------------


def _protocol_kwargs(**overrides) -> dict:
    fields = {
        "version": "setup_recurrence_v1",
        "window": (date(2021, 6, 1), date(2026, 1, 29)),
        "data_cutoff": date(2026, 9, 22),
        "robustness_window": (date(2017, 1, 3), date(2021, 4, 30)),
        "robustness_data_cutoff": date(2021, 6, 15),
        "lookback_sessions": 20,
        "scan_times_et": ("10:35", "14:35"),
        "max_hold_sessions": 20,
        "cost_bps_per_side": (0.0, 5.0),
        "bootstrap": {"block_mean": 10, "draws": 200, "seed": 20261014},
        "feed": "alpaca:sip",
        "adjustment": "all",
        "strategy_config": {"mode": "parallel"},
        "sector_etf": {"technology": "XLK", "etf_broad_equity": "SPY"},
        "min_repeats": 300,
        "min_repeat_sessions": 60,
        "min_after_stop": 100,
        "hypotheses": "R1..R4",
        "decision_rule": "eligible_repeat_preference if R1-R4 hold else no_change",
    }
    fields.update(overrides)
    return fields


def _protocol(**overrides) -> SetupRecurrenceProtocol:
    return SetupRecurrenceProtocol(**_protocol_kwargs(**overrides))


def test_protocol_identity_and_validation():
    assert _protocol().identity == _protocol().identity
    assert _protocol(lookback_sessions=10).identity != _protocol().identity
    with pytest.raises(ValidationError):
        _protocol(window=(date(2026, 1, 29), date(2021, 6, 1)))
    with pytest.raises(ValidationError, match="data_cutoff"):
        _protocol(data_cutoff=date(2026, 2, 1))
    with pytest.raises(ValidationError, match="robustness_data_cutoff"):
        _protocol(robustness_data_cutoff=date(2021, 5, 1))
    with pytest.raises(ValidationError, match="robustness_window"):
        _protocol(robustness_window=(date(2017, 1, 3), date(2021, 5, 20)))
    with pytest.raises(ValidationError):
        _protocol(lookback_sessions=0)
    with pytest.raises(ValidationError):
        SetupRecurrenceProtocol(**{**_protocol_kwargs(), "extra_key": 1})


# --- Hypotheses and executor ------------------------------------------------------------------


def _study_frame(n_sessions=80, repeats_per_session=5, firsts_per_session=5, repeat_r=1.0, first_r=-0.5, seed=1):
    """A frame shaped for with_setup_recurrence's rule.

    Every session holds `firsts_per_session` fresh symbols (first occurrences, r_cost near
    `first_r`). The `repeats_per_session` symbols R0..Rk recur every session: in even sessions
    (and session 0) they hit the stop at -1.0R, in odd sessions they hit the target at `repeat_r`.
    So from session 1 on every R-row is a repeat; the odd-session repeats follow a stop (the R2
    population, r_cost near `repeat_r`) and the even-session repeats follow a target (r_cost -1.0).
    With the defaults the repeat mean is about (repeat_r - 1) / 2 and the first mean `first_r`.
    """
    rng = np.random.default_rng(seed)
    rows = []
    sessions = []
    d = date(2024, 1, 2)
    while len(sessions) < n_sessions:
        if d.weekday() < 5:
            sessions.append(d)
        d += timedelta(days=1)
    for i, session in enumerate(sessions):
        day = session.isoformat()
        decided = f"{day}T15:35Z"
        for k in range(firsts_per_session):
            rows.append(
                _row(
                    f"F{i}_{k}", day, decided, "target", exit_time=f"{day}T18:30Z", r_cost=first_r + rng.normal(0, 0.05)
                )
            )
        for k in range(repeats_per_session):
            if i % 2 == 0:
                rows.append(_row(f"R{k}", day, decided, "stop", exit_time=f"{day}T17:30Z", r_cost=-1.0))
            else:
                rows.append(
                    _row(
                        f"R{k}",
                        day,
                        decided,
                        "target",
                        exit_time=f"{day}T18:30Z",
                        r_cost=repeat_r + rng.normal(0, 0.05),
                    )
                )
    return pd.DataFrame(rows)


def test_r1_r2_r3_r4_hold_on_a_clear_split(tmp_path):
    primary = _study_frame(n_sessions=80)
    robustness = _study_frame(n_sessions=30, seed=2)
    protocol = _protocol()
    result = execute_recurrence(
        protocol,
        tmp_path / "out",
        frames={"primary": lambda: primary, "robustness": lambda: robustness},
        environment={},
    )
    assert result["status"] == "completed"
    r1, r2, r3, r4 = result["r1"], result["r2"], result["r3"], result["r4"]
    assert r1["holds"] and r1["mean_diff"] > 0.3 and r1["ci90"][0] > 0
    assert r2["holds"] and r2["n"] >= 100 and r2["mean_r_cost"] > 0.8
    assert r3["holds"] and r3["mean_diff"] > 0
    assert r4["holds"] and r4["n_repeat"] >= 300 and r4["n_repeat_sessions"] >= 60 and r4["n_after_stop"] >= 100
    assert result["decision"] == "eligible_repeat_preference"
    assert result["authorizes_promotion"] is False
    assert set(result["descriptives"]["primary"]["by_state"]) == set(STATES)
    assert set(result["descriptives"]["primary"]["by_prior_outcome"]) == set(PRIOR_OUTCOMES)
    assert set(result["descriptives"]["primary"]["by_lookback"]) == {"5", "10", "20"}
    for name in ("protocol.json", "manifest.json", "result.json", "frames/primary.csv.gz", "frames/robustness.csv.gz"):
        path = tmp_path / "out" / name
        assert path.exists()
    assert oct((tmp_path / "out" / "frames" / "primary.csv.gz").stat().st_mode)[-3:] == "600"
    saved = pd.read_csv(gzip.open(tmp_path / "out" / "frames" / "primary.csv.gz"))
    assert "state" in saved.columns and len(saved) == len(primary)
    manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
    assert manifest["hypotheses"] == list(HYPOTHESES) and manifest["authorizes_promotion"] is False


def test_r1_is_paired_on_the_same_session_draws(tmp_path, monkeypatch):
    """Both group means in a draw come from the same resampled sessions."""
    import agentic_trader.research.setups.recurrence as module

    seen = []

    def fake_draws(n, block_mean, draws, seed):
        out = np.tile(np.arange(n), (draws, 1))
        seen.append(n)
        return out

    monkeypatch.setattr(module, "_stationary_index_draws", fake_draws)
    primary = _study_frame(n_sessions=70)
    result = execute_recurrence(
        _protocol(),
        tmp_path / "out",
        frames={"primary": lambda: primary, "robustness": lambda: primary},
        environment={},
    )
    r1 = result["r1"]
    # Identity draws: every draw equals the point estimate, so the CI collapses onto it.
    assert r1["ci90"][0] == pytest.approx(r1["mean_diff"]) and r1["ci90"][1] == pytest.approx(r1["mean_diff"])
    assert seen and all(n == primary["session"].nunique() for n in seen[:1])


def test_no_repeats_is_no_change_without_error(tmp_path):
    primary = _study_frame(n_sessions=70, repeats_per_session=0)
    result = execute_recurrence(
        _protocol(),
        tmp_path / "out",
        frames={"primary": lambda: primary, "robustness": lambda: primary},
        environment={},
    )
    assert result["status"] == "completed"
    assert math.isnan(result["r1"]["mean_diff"]) or result["r1"]["mean_diff"] is None
    assert result["r1"]["holds"] is False and result["r2"]["holds"] is False and result["r4"]["holds"] is False
    assert result["decision"] == "no_change"


def test_r4_floor_blocks_a_small_sample(tmp_path):
    primary = _study_frame(n_sessions=30, repeats_per_session=2, first_r=-1.5)
    result = execute_recurrence(
        _protocol(),
        tmp_path / "out",
        frames={"primary": lambda: primary, "robustness": lambda: primary},
        environment={},
    )
    assert result["r1"]["holds"] is True
    assert result["r4"]["holds"] is False and result["r4"]["n_repeat"] == 58
    assert result["decision"] == "no_change"


def test_r3_sign_only_on_the_robustness_window(tmp_path):
    primary = _study_frame(n_sessions=80)
    robustness = _study_frame(n_sessions=30, repeat_r=-0.8, first_r=-0.5, seed=3)
    result = execute_recurrence(
        _protocol(),
        tmp_path / "out",
        frames={"primary": lambda: primary, "robustness": lambda: robustness},
        environment={},
    )
    assert result["r3"]["holds"] is False and result["r3"]["mean_diff"] < 0
    assert result["decision"] == "no_change"


def test_each_frame_callable_runs_once_and_failure_writes_status_failed(tmp_path):
    calls = {"primary": 0}

    def primary():
        calls["primary"] += 1
        return _study_frame(n_sessions=10)

    def boom():
        raise RuntimeError("synthetic failure")

    result = execute_recurrence(
        _protocol(), tmp_path / "out", frames={"primary": primary, "robustness": boom}, environment={}
    )
    assert calls["primary"] == 1
    assert result["status"] == "failed" and "synthetic failure" in result["error"]
    saved = json.loads((tmp_path / "out" / "result.json").read_text())
    assert saved["status"] == "failed"
    assert (tmp_path / "out" / "protocol.json").exists() and (tmp_path / "out" / "manifest.json").exists()


def test_immature_rows_are_dropped_and_counted(tmp_path):
    primary = _study_frame(n_sessions=70)
    primary.loc[primary.index[:3], "hit"] = "immature"
    result = execute_recurrence(
        _protocol(),
        tmp_path / "out",
        frames={"primary": lambda: primary, "robustness": lambda: primary},
        environment={},
    )
    assert result["immature_dropped"] == {"primary": 3, "robustness": 3}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/setups/test_recurrence.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentic_trader.research.setups.recurrence'`.

- [ ] **Step 3: Implement the module**

```python
# agentic_trader/research/setups/recurrence.py
"""Predeclared setup-recurrence test (R1–R4) over the setup-outcome replay frame.

See ``docs/superpowers/specs/2026-10-14-setup-recurrence-design.md``. The live
``cards outcomes`` ``recurrence`` block showed repeat setups resolving far better than
first occurrences on 46 correlated rows; this freezes that hypothesis and tests it on
replayed history the live record never touched. Like ``baserates.py`` it reuses
``runner.build_window_frames`` and the study's stationary session-block bootstrap, fits
no model and grants no promotion, shadow or registry credit. Every frame callable is
called exactly once inside the try block; any failure is recorded as ``status: failed``.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, model_validator

from agentic_trader.research.setups.labels import BracketHit
from agentic_trader.research.setups.study import _base_rates, _drop_immature, _finite_json, _stationary_index_draws
from agentic_trader.storage.artifacts import save_json_report


__all__ = [
    "BAR",
    "HYPOTHESES",
    "PRIOR_OUTCOMES",
    "STATES",
    "SetupRecurrenceProtocol",
    "execute_recurrence",
    "with_setup_recurrence",
]

# The labeller's bar: a prior counts as resolved only once its exit bar has closed.
BAR = pd.Timedelta("1h")
STATES = ("first", "repeat", "repeat_open")
RESOLVED = (BracketHit.TARGET.value, BracketHit.STOP.value, BracketHit.TIMEOUT.value)
PRIOR_OUTCOMES = (*RESOLVED, "none")
HYPOTHESES: tuple[str, ...] = ("R1", "R2", "R3", "R4")
DESCRIPTIVE_LOOKBACKS = (5, 10, 20)

_MIN_MATURATION_DAYS = 30
_SETUP_KEY = ["symbol", "strategy", "timeframe", "direction"]


def _is_timestamp(value: Any) -> bool:
    return value is not None and not pd.isna(value)


def with_setup_recurrence(frame: pd.DataFrame, *, lookback_sessions: int) -> pd.DataFrame:
    """Tag each row's recurrence state over the same (symbol, strategy, timeframe, direction).

    A prior is a row of the same setup in a strictly earlier session within
    ``lookback_sessions`` trading sessions (distinct ``session`` values of the frame, so a
    holiday gap is not a session). ``state`` is ``first`` without a prior, ``repeat`` when the
    most recent prior had resolved by this decision (``hit`` in RESOLVED and
    ``exit_time + BAR <= decision_at``), else ``repeat_open``. ``prior_outcome`` is the resolved
    prior's hit or ``"none"``; ``prior_sessions_k`` counts the distinct earlier sessions holding
    the setup inside the lookback; ``prior_r_cost`` is the resolved prior's ``r_cost`` (NaN
    otherwise). Rows without a strategy, timeframe or direction are ``first``. Requires a
    unique index. Pure.
    """
    if lookback_sessions < 1:
        raise ValueError("lookback_sessions must be at least 1")
    if not frame.index.is_unique:
        raise ValueError("with_setup_recurrence requires a unique index")
    out = frame.copy()
    state = pd.Series("first", index=out.index, dtype=object)
    prior_outcome = pd.Series("none", index=out.index, dtype=object)
    prior_sessions = pd.Series(0, index=out.index, dtype=int)
    prior_r_cost = pd.Series(np.nan, index=out.index, dtype=float)
    if len(out):
        ordinal = {session: i for i, session in enumerate(sorted(out["session"].unique()))}
        keyed = out.dropna(subset=_SETUP_KEY)
        for _, group in keyed.groupby(_SETUP_KEY, sort=False):
            records = list(group[["session", "decision_at", "hit", "exit_time", "r_cost"]].itertuples())
            for row in records:
                here = ordinal[row.session]
                priors = [
                    other
                    for other in records
                    if ordinal[other.session] < here and here - ordinal[other.session] <= lookback_sessions
                ]
                if not priors:
                    continue
                prior_sessions.loc[row.Index] = len({other.session for other in priors})
                latest = max(priors, key=lambda other: pd.Timestamp(other.decision_at))
                decided = pd.Timestamp(row.decision_at)
                resolved = (
                    latest.hit in RESOLVED
                    and _is_timestamp(latest.exit_time)
                    and pd.Timestamp(latest.exit_time) + BAR <= decided
                )
                if resolved:
                    state.loc[row.Index] = "repeat"
                    prior_outcome.loc[row.Index] = str(latest.hit)
                    prior_r_cost.loc[row.Index] = float(latest.r_cost)
                else:
                    state.loc[row.Index] = "repeat_open"
    out["state"] = state
    out["prior_outcome"] = prior_outcome
    out["prior_sessions_k"] = prior_sessions
    out["prior_r_cost"] = prior_r_cost
    return out


class SetupRecurrenceProtocol(BaseModel, frozen=True, extra="forbid"):
    """A predeclared, hash-identified recurrence protocol (spec §3.3). ``hypotheses`` and
    ``decision_rule`` are recorded verbatim for the human record; the computation lives in
    ``execute_recurrence``."""

    version: Literal["setup_recurrence_v1"]
    window: tuple[date, date]
    data_cutoff: date
    robustness_window: tuple[date, date]
    robustness_data_cutoff: date
    lookback_sessions: int
    scan_times_et: tuple[str, ...]
    max_hold_sessions: int
    cost_bps_per_side: tuple[float, ...]
    bootstrap: dict
    feed: Literal["alpaca:sip"]
    adjustment: Literal["all"]
    strategy_config: dict
    sector_etf: dict[str, str]
    min_repeats: int
    min_repeat_sessions: int
    min_after_stop: int
    hypotheses: str
    decision_rule: str

    @model_validator(mode="after")
    def _check(self) -> SetupRecurrenceProtocol:
        if self.lookback_sessions < 1:
            raise ValueError("lookback_sessions must be at least 1")
        if self.max_hold_sessions < 1:
            raise ValueError("max_hold_sessions must be at least 1")
        if not self.cost_bps_per_side:
            raise ValueError("cost_bps_per_side must be non-empty")
        for name, (start, end), cutoff in (
            ("window", self.window, self.data_cutoff),
            ("robustness_window", self.robustness_window, self.robustness_data_cutoff),
        ):
            if start >= end:
                raise ValueError(f"{name} must be non-empty and ordered (start < end)")
            required = end + timedelta(days=_MIN_MATURATION_DAYS)
            if cutoff < required:
                label = "data_cutoff" if name == "window" else "robustness_data_cutoff"
                raise ValueError(
                    f"{label} must be at least {_MIN_MATURATION_DAYS} days after {name} end (need >= {required.isoformat()})"
                )
        if self.robustness_window[1] + timedelta(days=_MIN_MATURATION_DAYS) > self.window[0]:
            raise ValueError("robustness_window must end more than one label horizon (30 days) before window starts")
        return self

    def document(self) -> dict:
        return self.model_dump(mode="json")

    @property
    def identity(self) -> str:
        encoded = json.dumps(self.document(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(encoded.encode()).hexdigest()


def _draws(n_sessions: int, protocol: SetupRecurrenceProtocol) -> np.ndarray:
    spec = protocol.bootstrap
    return _stationary_index_draws(n_sessions, spec["block_mean"], spec["draws"], spec["seed"])


def _ci90(boot: np.ndarray) -> list[float]:
    finite = boot[np.isfinite(boot)]
    if finite.size == 0:
        return [float("nan"), float("nan")]
    return [float(np.percentile(finite, 5)), float(np.percentile(finite, 95))]


def _p_positive(boot: np.ndarray) -> float:
    finite = boot[np.isfinite(boot)]
    if finite.size == 0:
        return float("nan")
    return (int(np.sum(finite <= 0.0)) + 1) / (finite.size + 1)


def _session_table(frame: pd.DataFrame, masks: Mapping[str, pd.Series]) -> pd.DataFrame:
    """Per session: r_cost sum and count for each named mask (sessions with any setup at all)."""
    sessions = pd.Index(sorted(frame["session"].unique()))
    table = pd.DataFrame(index=sessions)
    for name, mask in masks.items():
        grouped = frame.loc[mask].groupby("session")["r_cost"].agg(["sum", "count"])
        table[f"sum_{name}"] = grouped["sum"].reindex(sessions).fillna(0.0)
        table[f"n_{name}"] = grouped["count"].reindex(sessions).fillna(0).astype(int)
    return table


def _weighted(sums: np.ndarray, counts: np.ndarray, rows: np.ndarray) -> float:
    n = counts[rows].sum()
    return float(sums[rows].sum() / n) if n > 0 else float("nan")


def _paired_difference(frame: pd.DataFrame, a: pd.Series, b: pd.Series, protocol: SetupRecurrenceProtocol) -> dict:
    """Setup-weighted mean(a) − mean(b); each draw resamples whole sessions once for both."""
    table = _session_table(frame, {"a": a, "b": b})
    sa, na = table["sum_a"].to_numpy(float), table["n_a"].to_numpy(float)
    sb, nb = table["sum_b"].to_numpy(float), table["n_b"].to_numpy(float)

    def diff(rows: np.ndarray) -> float:
        return _weighted(sa, na, rows) - _weighted(sb, nb, rows)

    n_sessions = len(table)
    boot = np.array([diff(rows) for rows in _draws(n_sessions, protocol)], dtype=float) if n_sessions else np.zeros(0)
    ci90 = _ci90(boot)
    point = diff(np.arange(n_sessions)) if n_sessions else float("nan")
    return {
        "n_a": int(na.sum()),
        "n_b": int(nb.sum()),
        "n_sessions": n_sessions,
        "mean_a": _weighted(sa, na, np.arange(n_sessions)) if n_sessions else float("nan"),
        "mean_b": _weighted(sb, nb, np.arange(n_sessions)) if n_sessions else float("nan"),
        "mean_diff": point,
        "ci90": ci90,
        "finite_draws": int(np.isfinite(boot).sum()),
        "p_one_sided": _p_positive(boot),
        "holds": bool(np.isfinite(ci90[0]) and ci90[0] > 0.0),
    }


def _group_mean(frame: pd.DataFrame, mask: pd.Series, protocol: SetupRecurrenceProtocol) -> dict:
    """Setup-weighted mean r_cost of the masked rows; whole sessions holding them resampled."""
    rows_only = frame.loc[mask]
    sessions = pd.Index(sorted(rows_only["session"].unique()))
    grouped = rows_only.groupby("session")["r_cost"].agg(["sum", "count"]).reindex(sessions)
    sums, counts = grouped["sum"].to_numpy(float), grouped["count"].to_numpy(float)
    n_sessions = len(sessions)
    boot = (
        np.array([_weighted(sums, counts, rows) for rows in _draws(n_sessions, protocol)], dtype=float)
        if n_sessions
        else np.zeros(0)
    )
    ci90 = _ci90(boot)
    return {
        "n": int(counts.sum()) if n_sessions else 0,
        "n_sessions": n_sessions,
        "mean_r_cost": _weighted(sums, counts, np.arange(n_sessions)) if n_sessions else float("nan"),
        "ci90": ci90,
        "finite_draws": int(np.isfinite(boot).sum()),
        "p_one_sided": _p_positive(boot),
        "holds": bool(np.isfinite(ci90[0]) and ci90[0] > 0.0),
    }


def _bucket(rows: pd.DataFrame) -> dict:
    if rows.empty:
        return {
            "n": 0,
            "mean_r_cost": None,
            "mean_r": None,
            "target_rate": None,
            "stop_rate": None,
            "mean_holding_sessions": None,
        }
    hits = rows["hit"].astype(str)
    return {
        "n": len(rows),
        "mean_r_cost": float(rows["r_cost"].mean()),
        "mean_r": float(rows["r"].mean()),
        "target_rate": float((hits == BracketHit.TARGET.value).mean()),
        "stop_rate": float((hits == BracketHit.STOP.value).mean()),
        "mean_holding_sessions": float(pd.to_numeric(rows["holding_sessions"], errors="coerce").mean()),
    }


def _descriptives(tagged: pd.DataFrame, clean: pd.DataFrame, protocol: SetupRecurrenceProtocol) -> dict:
    by_state = {name: _bucket(tagged.loc[tagged["state"] == name]) for name in STATES}
    by_prior = {
        name: _bucket(tagged.loc[(tagged["state"] == "repeat") & (tagged["prior_outcome"] == name)])
        for name in PRIOR_OUTCOMES
    }
    by_setup: dict[str, dict] = {}
    for (strategy, timeframe), group in tagged.groupby(["strategy", "timeframe"]):
        by_setup[f"{strategy}/{timeframe}"] = {
            name: _bucket(group.loc[group["state"] == name]) for name in ("first", "repeat")
        }
    years = tagged["session"].map(lambda value: str(value.year))
    by_year = {
        year: {name: _bucket(group.loc[group["state"] == name]) for name in ("first", "repeat")}
        for year, group in tagged.groupby(years)
    }
    by_lookback = {}
    for k in DESCRIPTIVE_LOOKBACKS:
        retagged = tagged if k == protocol.lookback_sessions else with_setup_recurrence(clean, lookback_sessions=k)
        by_lookback[str(k)] = _paired_difference(
            retagged, retagged["state"] == "repeat", retagged["state"] == "first", protocol
        )
    return {
        "by_state": by_state,
        "by_prior_outcome": by_prior,
        "by_setup": by_setup,
        "by_year": by_year,
        "by_lookback": by_lookback,
        "repeat_open": int((tagged["state"] == "repeat_open").sum()),
        "base_rates": _base_rates(tagged),
    }


def _write_private_gzip(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb") as compressed:
        frame.to_csv(compressed, index=False)


def execute_recurrence(
    protocol: SetupRecurrenceProtocol,
    directory: Path,
    *,
    frames: Mapping[str, Callable[[], pd.DataFrame]],
    environment: dict,
) -> dict:
    """Run R1–R4 on the primary window and R3 on the robustness window; write the artifacts."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    save_json_report({**protocol.document(), "identity": protocol.identity}, directory / "protocol.json")
    save_json_report(
        {
            "protocol_id": protocol.identity,
            "environment": environment,
            "started_at": datetime.now(UTC).isoformat(),
            "hypotheses": list(HYPOTHESES),
            "authorizes_promotion": False,
        },
        directory / "manifest.json",
    )
    try:
        tagged: dict[str, pd.DataFrame] = {}
        clean: dict[str, pd.DataFrame] = {}
        immature: dict[str, int] = {}
        for name in ("primary", "robustness"):
            raw = frames[name]()
            clean[name], immature[name] = _drop_immature(raw)
            tagged[name] = with_setup_recurrence(clean[name], lookback_sessions=protocol.lookback_sessions)
            _write_private_gzip(tagged[name], directory / "frames" / f"{name}.csv.gz")
        primary = tagged["primary"]
        repeat = primary["state"] == "repeat"
        first = primary["state"] == "first"
        after_stop = repeat & (primary["prior_outcome"] == BracketHit.STOP.value)
        r1 = _paired_difference(primary, repeat, first, protocol)
        r2 = _group_mean(primary, after_stop, protocol)
        robustness = tagged["robustness"]
        r3_diff = _paired_difference(
            robustness, robustness["state"] == "repeat", robustness["state"] == "first", protocol
        )
        r3 = {**r3_diff, "holds": bool(np.isfinite(r3_diff["mean_diff"]) and r3_diff["mean_diff"] > 0.0)}
        n_repeat = int(repeat.sum())
        n_repeat_sessions = int(primary.loc[repeat, "session"].nunique())
        n_after_stop = int(after_stop.sum())
        r4 = {
            "n_repeat": n_repeat,
            "n_repeat_sessions": n_repeat_sessions,
            "n_after_stop": n_after_stop,
            "min_repeats": protocol.min_repeats,
            "min_repeat_sessions": protocol.min_repeat_sessions,
            "min_after_stop": protocol.min_after_stop,
            "holds": bool(
                n_repeat >= protocol.min_repeats
                and n_repeat_sessions >= protocol.min_repeat_sessions
                and n_after_stop >= protocol.min_after_stop
            ),
        }
        decision = "eligible_repeat_preference" if all(rule["holds"] for rule in (r1, r2, r3, r4)) else "no_change"
        result = {
            "status": "completed",
            "immature_dropped": immature,
            "n_setups": {name: len(tagged[name]) for name in tagged},
            "r1": r1,
            "r2": r2,
            "r3": r3,
            "r4": r4,
            "decision": decision,
            "descriptives": {name: _descriptives(tagged[name], clean[name], protocol) for name in tagged},
            "authorizes_promotion": False,
        }
    except Exception as exc:
        result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "authorizes_promotion": False}
    save_json_report(_finite_json(result), directory / "result.json")
    return result
```

Notes for the implementer: `_finite_json` turns NaN into `None` for JSON (`allow_nan=False`); the in-memory `result` keeps `float("nan")`, which the tests accept either way. `_base_rates` needs the `r` column (the frame has it). If `study._stationary_index_draws` returns shape `(draws, n)`, `for rows in _draws(...)` iterates draws as the base-rate module does.

- [ ] **Step 4: Run the tests**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/setups/test_recurrence.py -q -W error::FutureWarning`
Expected: PASS. If `test_r1_is_paired_on_the_same_session_draws` asserts on `seen[:1]` fail because descriptives call the draws with other session counts first, relax it to `primary["session"].nunique() in seen`.

- [ ] **Step 5: Lint, type-check, stage**

Run: `env -u VIRTUAL_ENV uv run ruff check agentic_trader/research/setups/recurrence.py tests/research/setups/test_recurrence.py && env -u VIRTUAL_ENV uv run ruff format --check agentic_trader/research/setups/recurrence.py tests/research/setups/test_recurrence.py && env -u VIRTUAL_ENV uv run mypy agentic_trader/research/setups/recurrence.py`
Expected: clean. Stage both files and report. Commit message: `Setup recurrence test: state tagging, protocol, R1–R4 and executor`.

---

### Task 3: `alpha setup-recurrence`, the frozen protocol file, docs pointers

**Files:**
- Modify: `agentic_trader/cli/commands/alpha.py` (after `alpha_setup_baserates_cmd` ~line 1262; imports ~line 108)
- Create: `config/research/setup-recurrence-v1.json`
- Modify: `docs/card-evidence.md` (one sentence in "Regime and recurrence context"), `docs/alpha-roadmap.md` (desk item 1 status), `CLAUDE.md` (one sentence after the regime/recurrence sentence in contract 8)
- Test: `tests/research/setups/test_recurrence.py` (append CLI tests), `tests/cli/test_cli_smoke.py` (registration, if that file lists commands)

**Interfaces:**
- Consumes: Task 2's protocol and executor; `build_window_frames`, `research_environment`, the Alpaca clients exactly as `alpha_setup_baserates_cmd` builds them.
- Produces: `copilot alpha setup-recurrence PROTOCOL --output DIR [--cache DIR] [--robustness-cache DIR] [--max-workers N]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/research/setups/test_recurrence.py` (mirror `tests/research/setups/test_baserates.py`'s CLI tests: `_protocol_document`, `CliRunner`, `load_config`, `SECTOR_ETF`):

```python
from click.testing import CliRunner

from agentic_trader.cli.main import cli
from agentic_trader.config import load_config
from agentic_trader.research.setups.features import SECTOR_ETF


def _protocol_document(**overrides) -> dict:
    doc = _protocol_kwargs(**overrides)
    doc["window"] = [d.isoformat() for d in doc["window"]]
    doc["robustness_window"] = [d.isoformat() for d in doc["robustness_window"]]
    doc["data_cutoff"] = doc["data_cutoff"].isoformat()
    doc["robustness_data_cutoff"] = doc["robustness_data_cutoff"].isoformat()
    doc["sector_etf"] = dict(SECTOR_ETF)
    doc["strategy_config"] = load_config().strategies.model_dump(mode="json")
    return doc


def test_cli_setup_recurrence_is_registered():
    result = CliRunner().invoke(cli, ["alpha", "setup-recurrence", "--help"])
    assert result.exit_code == 0
    assert "--robustness-cache" in result.output


def test_cli_setup_recurrence_refuses_existing_output(tmp_path):
    protocol_path = tmp_path / "protocol.json"
    protocol_path.write_text(json.dumps(_protocol_document()))
    existing = tmp_path / "out"
    existing.mkdir()
    result = CliRunner().invoke(cli, ["alpha", "setup-recurrence", str(protocol_path), "--output", str(existing)])
    assert result.exit_code != 0
    assert "already exists" in result.output


def test_cli_setup_recurrence_refuses_a_stale_strategy_config(tmp_path):
    protocol_path = tmp_path / "protocol.json"
    protocol_path.write_text(json.dumps(_protocol_document(strategy_config={"mode": "stale"})))
    result = CliRunner().invoke(
        cli, ["alpha", "setup-recurrence", str(protocol_path), "--output", str(tmp_path / "out")]
    )
    assert result.exit_code != 0
    assert "strategy_config" in result.output


def test_frozen_protocol_file_matches_the_spec_and_the_loaded_config():
    document = json.loads((WORKSPACE_ROOT / "config" / "research" / "setup-recurrence-v1.json").read_text())
    protocol = SetupRecurrenceProtocol(**document)
    assert protocol.window == (date(2021, 6, 1), date(2026, 1, 29)) and protocol.data_cutoff == date(2026, 9, 22)
    assert protocol.robustness_window == (
        date(2017, 1, 3),
        date(2021, 4, 30),
    ) and protocol.robustness_data_cutoff == date(2021, 6, 15)
    assert protocol.lookback_sessions == 20 and protocol.bootstrap == {
        "block_mean": 10,
        "draws": 2000,
        "seed": 20261014,
    }
    assert (protocol.min_repeats, protocol.min_repeat_sessions, protocol.min_after_stop) == (300, 60, 100)
    assert protocol.cost_bps_per_side == (0.0, 5.0) and protocol.max_hold_sessions == 20
    assert protocol.strategy_config == load_config().strategies.model_dump(mode="json")
    assert dict(protocol.sector_etf) == dict(SECTOR_ETF)
    assert protocol.strategy_config["trend_pullback"]["allow_short"] is False
```

(`WORKSPACE_ROOT` is importable from `agentic_trader.config`, as `test_baserates.py` does.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/setups/test_recurrence.py -q -k "cli or frozen_protocol"`
Expected: FAIL (`No such command 'setup-recurrence'`; the protocol file missing).

- [ ] **Step 3: Implement the command**

In `agentic_trader/cli/commands/alpha.py`, add the import `from agentic_trader.research.setups.recurrence import SetupRecurrenceProtocol, execute_recurrence` beside the baserates import, and after `alpha_setup_baserates_cmd`:

```python
@alpha_group.command("setup-recurrence")
@click.argument("protocol_path", type=click.Path(exists=True, path_type=Path))
@click.option("--output", type=click.Path(path_type=Path), required=True, help="New private directory; no overwrite")
@click.option("--max-workers", type=int, default=6, show_default=True, help="Replay ProcessPoolExecutor width")
@click.option(
    "--cache", type=click.Path(path_type=Path), default=None, help="Primary-window bar cache; defaults to OUTPUT/raw"
)
@click.option(
    "--robustness-cache",
    type=click.Path(path_type=Path),
    default=None,
    help="Robustness-window bar cache; defaults to OUTPUT/raw-robustness",
)
@coro
async def alpha_setup_recurrence_cmd(protocol_path, output, max_workers, cache, robustness_cache):
    """Replay/label the two frozen windows (each from its own cache) and run the predeclared
    setup-recurrence test (R1-R4); research only, no promotion credit."""
    document = json.loads(await asyncio.to_thread(protocol_path.read_text))
    protocol = SetupRecurrenceProtocol(**document)

    if output.exists():
        raise click.ClickException(f"Output directory already exists; refusing to overwrite: {output}")

    config = load_config()
    expected_strategy_config = config.strategies.model_dump(mode="json")
    if protocol.strategy_config != expected_strategy_config:
        raise click.ClickException(
            "Protocol strategy_config does not match the loaded config.yaml strategies; "
            "refusing to run a study against a stale strategy configuration"
        )
    if dict(protocol.sector_etf) != dict(SECTOR_ETF):
        raise click.ClickException(
            "Protocol sector_etf does not match research.setups.features.SECTOR_ETF; "
            "refusing to run a study against a stale sector map"
        )

    universe = [(entry.symbol, entry.sector) for group in config.universe.groups.values() for entry in group]
    caches = {
        "primary": cache if cache is not None else output / "raw",
        "robustness": robustness_cache if robustness_cache is not None else output / "raw-robustness",
    }
    windows = {"primary": protocol.window, "robustness": protocol.robustness_window}
    cutoffs = {"primary": protocol.data_cutoff, "robustness": protocol.robustness_data_cutoff}

    frames: dict[str, Callable[[], pd.DataFrame]] = {}
    coverage: dict[str, dict] = {}
    with ExitStack() as clients:
        calendar_client = BoundedTradingClient(
            config.alpaca_api_key,
            config.alpaca_api_secret,
            paper=config.alpaca_paper,
            request_timeout=config.market_data.timeout_seconds,
        )
        clients.callback(calendar_client._session.close)
        data_client = BoundedStockDataClient(
            config.alpaca_api_key,
            config.alpaca_api_secret,
            request_timeout=config.market_data.timeout_seconds,
        )
        clients.callback(data_client._session.close)
        bars = AlpacaDataProvider(
            stock_client=data_client,
            feed="sip",
            evidence=BarEvidenceStore(state_directory() / "market-data", config.market_data.evidence),
        )
        calendar = AlpacaCalendarProvider(trading_client=calendar_client)

        for name in ("primary", "robustness"):
            built, window_coverage = await build_window_frames(
                {name: windows[name]},
                universe,
                bars,
                calendar,
                caches[name],
                config,
                data_cutoff=cutoffs[name],
                scan_times_et=protocol.scan_times_et,
                adjustment=protocol.adjustment,
                max_hold_sessions=protocol.max_hold_sessions,
                cost_bps_per_side=protocol.cost_bps_per_side,
                max_workers=max_workers,
            )
            frames[name] = built[name]
            coverage[name] = window_coverage

    environment = await asyncio.to_thread(research_environment)
    environment["coverage"] = coverage
    result = await asyncio.to_thread(execute_recurrence, protocol, output, frames=frames, environment=environment)
    click.echo(json.dumps(result, indent=2, default=str))
```

Check the module already imports `Callable` and `pd` (add `from collections.abc import Callable` / `import pandas as pd` at the top if not). Important: `build_window_frames` returns callables that label lazily, and the replay itself happens inside `build_window_frames` for each call — the two calls run sequentially; that is intended (each with its own cache).

- [ ] **Step 4: Write the frozen protocol file**

Generate `config/research/setup-recurrence-v1.json` with this script (so `strategy_config` and `sector_etf` are the loaded values, exactly as the CLI validates), then review it by eye:

```bash
env -u VIRTUAL_ENV uv run python - <<'EOF'
import json
from pathlib import Path
from agentic_trader.config import load_config
from agentic_trader.research.setups.features import SECTOR_ETF
hypotheses = ("Over the primary window 2021-06-01 -> 2026-01-29 (decisions; bars through 2026-09-22), setups replayed as the live desk scans them (10:35 and 14:35 New York, current long-only strategy configuration, 159-name current universe, SIP all-adjusted), labelled by the live bracket (20-session timeout, R_cost at 5 bp per side): a setup is a repeat when the same (symbol, strategy, timeframe, direction) was set up in an earlier session within the last 20 trading sessions and that prior's bracket had resolved (its hourly exit bar closed) by this decision; it is first when no such prior exists within 20 sessions; otherwise it is repeat_open and excluded from R1/R2. Setup-weighted means; stationary session-block bootstrap (block mean 10, 2000 draws, seed 20261014), whole sessions resampled once per draw for both groups; one-sided. R1: mean R_cost(repeat) - mean R_cost(first) > 0, 90% CI lower bound > 0. R2: mean R_cost(repeat whose prior hit the stop) > 0, 90% CI lower bound > 0. R3: R1's point estimate on the robustness window 2017-01-03 -> 2021-04-30 (bars through 2021-06-15) > 0. R4: at least 300 repeats over at least 60 sessions and at least 100 repeats-after-stop in the primary window. Per-strategy, per-year, per-lookback (5/10) and 0 bp figures are descriptive only. See docs/superpowers/specs/2026-10-14-setup-recurrence-design.md.")
decision = ("If R1, R2, R3 and R4 all hold: eligible_repeat_preference - a separately specified card-construction rule that prefers repeat setups (preview before enforce, operator approval) becomes eligible; nothing changes until that PR. Otherwise: no_change; the live recurrence block keeps measuring and no construction rule is proposed. Both windows are used once for this test.")
doc = {
    "version": "setup_recurrence_v1",
    "window": ["2021-06-01", "2026-01-29"],
    "data_cutoff": "2026-09-22",
    "robustness_window": ["2017-01-03", "2021-04-30"],
    "robustness_data_cutoff": "2021-06-15",
    "lookback_sessions": 20,
    "scan_times_et": ["10:35", "14:35"],
    "max_hold_sessions": 20,
    "cost_bps_per_side": [0.0, 5.0],
    "bootstrap": {"block_mean": 10, "draws": 2000, "seed": 20261014},
    "feed": "alpaca:sip",
    "adjustment": "all",
    "strategy_config": load_config().strategies.model_dump(mode="json"),
    "sector_etf": dict(SECTOR_ETF),
    "min_repeats": 300,
    "min_repeat_sessions": 60,
    "min_after_stop": 100,
    "hypotheses": hypotheses,
    "decision_rule": decision,
}
Path("config/research/setup-recurrence-v1.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
print("written")
EOF
```

- [ ] **Step 5: Docs**

- `docs/card-evidence.md`, end of the "Regime and recurrence context" section's "Reading" paragraph, add: "The recurrence split is under a predeclared test on replayed history (`alpha setup-recurrence`, protocol `config/research/setup-recurrence-v1.json`, [spec](superpowers/specs/2026-10-14-setup-recurrence-design.md)); until that test reports, it is a hypothesis."
- `docs/alpha-roadmap.md`, desk item 1, after the October 14 status paragraph: "*Status (October 14, later):* the first live `recurrence` block split sharply (first occurrences −0.46R, repeats +0.96R, repeats after a stop +1.52R on 46 correlated rows), so the predeclared setup-recurrence test ([spec](superpowers/specs/2026-10-14-setup-recurrence-design.md)) freezes R1–R4 on the setup-outcome development window with a 2017–2021 sign check; a pass only makes a repeat-preferring construction rule eligible. The bracket counterfactual proposed on October 13 was already answered by the October 8 funnel diagnostic and is dropped."
- `CLAUDE.md` contract 8, after the sentence ending "…carries it in `decision_provenance[\"regime\"]`.", add: "`alpha setup-recurrence` is the predeclared, research-only test of the recurrence split (R1–R4 on replayed history; a pass only makes a construction rule eligible)."

- [ ] **Step 6: Run, lint, stage**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/setups/test_recurrence.py tests/research/setups/test_baserates.py tests/cli/test_cli_smoke.py -q && env -u VIRTUAL_ENV uv run ruff check agentic_trader/cli/commands/alpha.py tests/research/setups/test_recurrence.py && env -u VIRTUAL_ENV uv run ruff format --check agentic_trader/cli/commands/alpha.py tests/research/setups/test_recurrence.py && env -u VIRTUAL_ENV uv run mypy agentic_trader/cli/commands/alpha.py && env -u VIRTUAL_ENV uv run pre-commit run --files docs/card-evidence.md docs/alpha-roadmap.md CLAUDE.md config/research/setup-recurrence-v1.json`
Expected: PASS and clean. Stage `agentic_trader/cli/commands/alpha.py`, `config/research/setup-recurrence-v1.json`, the three docs and the test file; report. Commit message: `alpha setup-recurrence command, frozen protocol v1 and doc pointers`.
