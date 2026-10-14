from __future__ import annotations

import gzip
import json
import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from agentic_trader.research.setups import recurrence as module
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
    assert pd.Timedelta("1h") == BAR
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
        rows.extend(
            _row(f"F{i}_{k}", day, decided, "target", exit_time=f"{day}T18:30Z", r_cost=first_r + rng.normal(0, 0.05))
            for k in range(firsts_per_session)
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
    with gzip.open(tmp_path / "out" / "frames" / "primary.csv.gz") as handle:
        saved = pd.read_csv(handle)
    assert "state" in saved.columns and len(saved) == len(primary)
    manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
    assert manifest["hypotheses"] == list(HYPOTHESES) and manifest["authorizes_promotion"] is False


def test_r1_is_paired_on_the_same_session_draws(tmp_path, monkeypatch):
    """Both group means in a draw come from the same resampled sessions."""
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
