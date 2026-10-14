from __future__ import annotations

import gzip
import json
import math
from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from agentic_trader.cli.commands import alpha as alpha_cmd
from agentic_trader.cli.main import cli
from agentic_trader.config import WORKSPACE_ROOT, load_config
from agentic_trader.research.setups import recurrence as module
from agentic_trader.research.setups.features import SECTOR_ETF
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
        # The prior stopped out in an earlier session: closed.
        _row("AAA", "2024-01-04", "2024-01-04T14:35Z", "timeout", r_cost=0.1),
    ]
    out = with_setup_recurrence(_frame(rows), lookback_sessions=20)
    assert out["state"].tolist() == ["first", "repeat_open", "repeat"]
    assert out["prior_outcome"].tolist() == ["none", "none", "stop"]


def test_prior_closes_exactly_one_bar_after_its_exit_bar_opens():
    rows = [
        _row("AAA", "2024-01-02", "2024-01-02T15:35Z", "stop", exit_time="2024-01-03T14:35Z", r_cost=-1.0),
        _row("AAA", "2024-01-03", "2024-01-03T15:35Z", "target", exit_time="2024-01-03T18:30Z", r_cost=1.9),
    ]
    out = with_setup_recurrence(_frame(rows), lookback_sessions=20)
    # exit_time + 1h == decision_at: resolved by this decision.
    assert pd.Timestamp("2024-01-03T14:35Z") + BAR == pd.Timestamp("2024-01-03T15:35Z")
    assert out["state"].tolist() == ["first", "repeat"]
    assert out["prior_outcome"].tolist() == ["none", "stop"]


def test_latest_prior_still_open_makes_repeat_open_even_if_an_earlier_prior_resolved():
    rows = [
        _row("AAA", "2024-01-02", "2024-01-02T15:35Z", "stop", exit_time="2024-01-02T17:30Z", r_cost=-1.0),
        # Session 2's exit bar opens 15:00Z on 01-04, so it closes (16:00Z) after session 3's 15:35Z decision.
        _row("AAA", "2024-01-03", "2024-01-03T15:35Z", "target", exit_time="2024-01-04T15:00Z", r_cost=1.9),
        _row("AAA", "2024-01-04", "2024-01-04T15:35Z", "timeout", r_cost=0.1),
    ]
    out = with_setup_recurrence(_frame(rows), lookback_sessions=20)
    assert out["state"].tolist() == ["first", "repeat", "repeat_open"]
    assert out["prior_outcome"].tolist() == ["none", "stop", "none"]
    assert math.isnan(out["prior_r_cost"].iloc[2])


def test_two_priors_in_one_session_the_later_decided_prior_wins():
    rows = [
        _row("AAA", "2024-01-02", "2024-01-02T15:35Z", "stop", exit_time="2024-01-02T17:30Z", r_cost=-1.0),
        _row("AAA", "2024-01-02", "2024-01-02T19:35Z", "target", exit_time="2024-01-02T20:30Z", r_cost=1.9),
        _row("AAA", "2024-01-03", "2024-01-03T15:35Z", "timeout", r_cost=0.1),
    ]
    out = with_setup_recurrence(_frame(rows), lookback_sessions=20)
    assert out["state"].tolist() == ["first", "first", "repeat"]
    assert out["prior_outcome"].tolist() == ["none", "none", "target"]
    assert out["prior_r_cost"].iloc[2] == pytest.approx(1.9)
    assert out["prior_sessions_k"].tolist() == [0, 0, 1]


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
    with pytest.raises(ValidationError, match="one label horizon"):
        _protocol(robustness_window=(date(2017, 1, 3), date(2021, 5, 20)), robustness_data_cutoff=date(2021, 7, 1))
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
    assert {"n_repeat", "n_first", "mean_repeat", "mean_first"} <= set(r1)
    assert not {"n_a", "n_b", "mean_a", "mean_b"} & set(r1)
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


def test_paired_difference_uses_the_same_resampled_sessions_for_both_groups(monkeypatch):
    """Two fixed, non-identity draws; expected values are hand-computed from session sums/counts.

    Sessions s0, s1, s2 (group a: sum, n | group b: sum, n):
      s0: a 1.0, 1 | b 0.0, 1
      s1: a 4.0, 2 | b 2.0, 1
      s2: a -1.0, 1 | b 4.0, 2
    Draw [0, 0, 1]: a = (1+1+4)/(1+1+2) = 1.5; b = (0+0+2)/(1+1+1) = 2/3; diff = 5/6.
    Draw [2, 2, 1]: a = (-1-1+4)/(1+1+2) = 0.5; b = (4+4+2)/(2+2+1) = 2.0; diff = -1.5.
    Point (all sessions): a = 4/4 = 1.0; b = 6/4 = 1.5; diff = -0.5.
    """

    def fake_draws(n, block_mean, draws, seed):
        assert n == 3
        return np.array([[0, 0, 1], [2, 2, 1]])

    monkeypatch.setattr(module, "_stationary_index_draws", fake_draws)
    days = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    rows = [
        (days[0], 1.0, "a"),
        (days[0], 0.0, "b"),
        (days[1], 3.0, "a"),
        (days[1], 1.0, "a"),
        (days[1], 2.0, "b"),
        (days[2], -1.0, "a"),
        (days[2], 1.0, "b"),
        (days[2], 3.0, "b"),
    ]
    frame = pd.DataFrame(rows, columns=["session", "r_cost", "group"])
    result = module._paired_difference(frame, frame["group"] == "a", frame["group"] == "b", _protocol())
    assert result["n_repeat"] == 4 and result["n_first"] == 4 and result["n_sessions"] == 3
    assert result["mean_repeat"] == pytest.approx(1.0) and result["mean_first"] == pytest.approx(1.5)
    assert result["mean_diff"] == pytest.approx(-0.5)
    high, low = 5 / 6, -1.5
    assert result["ci90"][0] == pytest.approx(low + 0.05 * (high - low))
    assert result["ci90"][1] == pytest.approx(low + 0.95 * (high - low))
    assert result["finite_draws"] == 2
    named = module._paired_difference(
        frame, frame["group"] == "a", frame["group"] == "b", _protocol(), names=("x", "y")
    )
    assert {"n_x", "n_y", "mean_x", "mean_y"} <= set(named)


def test_r2_resamples_the_full_session_calendar(tmp_path):
    primary = _study_frame(n_sessions=80)
    result = execute_recurrence(
        _protocol(),
        tmp_path / "out",
        frames={"primary": lambda: primary, "robustness": lambda: primary},
        environment={},
    )
    r2 = result["r2"]
    assert r2["n_sessions"] == primary["session"].nunique() == 80
    assert r2["n_sessions_with_rows"] == 40


def test_only_r2_failing_is_no_change(tmp_path):
    # Repeats after a stop lose 0.9R; the other repeats lose 1.0R; firsts lose 2.0R, so R1 still holds.
    primary = _study_frame(n_sessions=80, repeat_r=-0.9, first_r=-2.0)
    result = execute_recurrence(
        _protocol(),
        tmp_path / "out",
        frames={"primary": lambda: primary, "robustness": lambda: _study_frame(n_sessions=30, seed=2)},
        environment={},
    )
    assert result["r1"]["holds"] and result["r3"]["holds"] and result["r4"]["holds"]
    assert result["r2"]["holds"] is False
    assert result["decision"] == "no_change"


@pytest.mark.parametrize("floor", ["min_repeats", "min_repeat_sessions", "min_after_stop"])
def test_each_r4_floor_alone_blocks_the_decision(tmp_path, floor):
    primary = _study_frame(n_sessions=80)
    frames = {"primary": lambda: primary, "robustness": lambda: _study_frame(n_sessions=30, seed=2)}
    baseline = execute_recurrence(_protocol(), tmp_path / "base", frames=frames, environment={})
    assert baseline["r4"]["holds"] and baseline["decision"] == "eligible_repeat_preference"
    result = execute_recurrence(_protocol(**{floor: 1000}), tmp_path / "out", frames=frames, environment={})
    assert result["r4"]["holds"] is False
    assert result["decision"] == "no_change"


def test_descriptives_split_by_session_tape_and_live_definition(tmp_path):
    primary = _study_frame(n_sessions=80)
    ordinal = {session: i for i, session in enumerate(sorted(primary["session"].unique()))}
    primary["spy_above_200"] = [1.0 if ordinal[session] < 40 else 0.0 for session in primary["session"]]
    primary.loc[primary.index[:3], "spy_above_200"] = np.nan
    shuffled = primary.sample(frac=1.0, random_state=0)
    result = execute_recurrence(
        _protocol(),
        tmp_path / "out",
        frames={"primary": lambda: shuffled, "robustness": lambda: _study_frame(n_sessions=30, seed=2)},
        environment={},
    )
    desc = result["descriptives"]["primary"]
    within = desc["within_session"]
    assert within["n_sessions"] == 79
    assert within["mean_diff"] == pytest.approx(0.51, abs=0.05)
    assert within["ci90"][0] <= within["mean_diff"] <= within["ci90"][1]
    tape = desc["by_tape"]
    assert set(tape) == {"above_200", "below_200", "unknown"}
    assert tape["above_200"]["n_repeat"] == 39 * 5 and tape["below_200"]["n_repeat"] == 40 * 5
    assert tape["unknown"] == {"n_repeat": 0, "n_first": 3}
    live = desc["live_definition"]
    assert live["n_repeat_or_open"] == result["r1"]["n_repeat"] and live["n_first"] == result["r1"]["n_first"]
    for bucket in (desc["by_setup"]["TREND_PULLBACK/4h"], desc["by_year"]["2024"]):
        assert set(bucket) == {*STATES, "repeat_by_prior_outcome"}
        assert set(bucket["repeat_by_prior_outcome"]) == set(PRIOR_OUTCOMES)
    with gzip.open(tmp_path / "out" / "frames" / "primary.csv.gz") as handle:
        saved = pd.read_csv(handle)
    assert saved["decision_at"].is_monotonic_increasing


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


def _protocol_document(**overrides) -> dict:
    doc = _protocol_kwargs(**overrides)
    doc["window"] = [d.isoformat() for d in doc["window"]]
    doc["robustness_window"] = [d.isoformat() for d in doc["robustness_window"]]
    doc["data_cutoff"] = doc["data_cutoff"].isoformat()
    doc["robustness_data_cutoff"] = doc["robustness_data_cutoff"].isoformat()
    doc["sector_etf"] = dict(SECTOR_ETF)
    doc["strategy_config"] = load_config().strategies.model_dump(mode="json")
    return doc


def _cache_args(tmp_path) -> list[str]:
    return ["--cache", str(tmp_path / "cache-a"), "--robustness-cache", str(tmp_path / "cache-b")]


def test_cli_setup_recurrence_is_registered():
    result = CliRunner().invoke(cli, ["alpha", "setup-recurrence", "--help"])
    assert result.exit_code == 0
    assert "--robustness-cache" in result.output


def test_cli_setup_recurrence_requires_both_caches_before_any_work(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("config must not load before the required options are checked")

    monkeypatch.setattr(alpha_cmd, "load_config", forbidden)
    protocol_path = tmp_path / "protocol.json"
    protocol_path.write_text(json.dumps(_protocol_document()))
    result = CliRunner().invoke(
        cli,
        ["alpha", "setup-recurrence", str(protocol_path), "--output", str(tmp_path / "out"), "--cache", str(tmp_path)],
    )
    assert result.exit_code != 0
    assert "Missing option" in result.output and "--robustness-cache" in result.output
    assert not (tmp_path / "out").exists()


def test_cli_setup_recurrence_refuses_existing_output(tmp_path):
    protocol_path = tmp_path / "protocol.json"
    protocol_path.write_text(json.dumps(_protocol_document()))
    existing = tmp_path / "out"
    existing.mkdir()
    result = CliRunner().invoke(
        cli, ["alpha", "setup-recurrence", str(protocol_path), "--output", str(existing), *_cache_args(tmp_path)]
    )
    assert result.exit_code != 0
    assert "already exists" in result.output


def test_cli_setup_recurrence_refuses_a_stale_strategy_config(tmp_path):
    protocol_path = tmp_path / "protocol.json"
    document = _protocol_document()
    document["strategy_config"] = {"mode": "stale"}
    protocol_path.write_text(json.dumps(document))
    result = CliRunner().invoke(
        cli,
        ["alpha", "setup-recurrence", str(protocol_path), "--output", str(tmp_path / "out"), *_cache_args(tmp_path)],
    )
    assert result.exit_code != 0
    assert "strategy_config" in result.output


def test_cli_setup_recurrence_fails_fast_on_a_mismatched_cache_range(tmp_path, monkeypatch):
    async def never(*args, **kwargs):
        raise AssertionError("build_window_frames must not run after a cache-range mismatch")

    monkeypatch.setattr(alpha_cmd, "build_window_frames", never)
    protocol_path = tmp_path / "protocol.json"
    protocol_path.write_text(json.dumps(_protocol_document()))
    stale = tmp_path / "cache-b"
    stale.mkdir()
    (stale / "cache_range.json").write_text(
        json.dumps({"start": "2030-01-01T00:00:00+00:00", "end": "2030-02-01T00:00:00+00:00"})
    )
    result = CliRunner().invoke(
        cli,
        ["alpha", "setup-recurrence", str(protocol_path), "--output", str(tmp_path / "out"), *_cache_args(tmp_path)],
    )
    assert result.exit_code != 0
    assert "use a fresh cache directory" in result.output
    assert not (tmp_path / "out").exists()


class _FakeClient:
    def __init__(self, *args, **kwargs):
        self._session = SimpleNamespace(close=lambda: None)


def _fake_command_environment(monkeypatch, result: dict) -> dict:
    """Stub clients, frame building and the executor; returns a dict capturing the environment."""
    captured: dict = {}

    async def fake_build(windows, *args, **kwargs):
        return {name: (lambda: pd.DataFrame()) for name in windows}, {"covered": True}

    def fake_execute(protocol, output, *, frames, environment):
        captured["environment"] = environment
        return result

    for name in ("BoundedTradingClient", "BoundedStockDataClient"):
        monkeypatch.setattr(alpha_cmd, name, _FakeClient)
    for name in ("AlpacaDataProvider", "BarEvidenceStore", "AlpacaCalendarProvider"):
        monkeypatch.setattr(alpha_cmd, name, lambda *args, **kwargs: object())
    monkeypatch.setattr(alpha_cmd, "build_window_frames", fake_build)
    monkeypatch.setattr(alpha_cmd, "execute_recurrence", fake_execute)
    monkeypatch.setattr(alpha_cmd, "research_environment", dict)
    return captured


def test_cli_setup_recurrence_exits_nonzero_when_the_study_failed(tmp_path, monkeypatch):
    _fake_command_environment(monkeypatch, {"status": "failed", "error": "RuntimeError: boom"})
    protocol_path = tmp_path / "protocol.json"
    protocol_path.write_text(json.dumps(_protocol_document()))
    result = CliRunner().invoke(
        cli,
        ["alpha", "setup-recurrence", str(protocol_path), "--output", str(tmp_path / "out"), *_cache_args(tmp_path)],
    )
    assert result.exit_code != 0
    assert "setup recurrence failed: RuntimeError: boom" in result.output


def test_cli_setup_recurrence_records_the_caches_in_the_manifest_environment(tmp_path, monkeypatch):
    captured = _fake_command_environment(monkeypatch, {"status": "completed"})
    protocol_path = tmp_path / "protocol.json"
    protocol_path.write_text(json.dumps(_protocol_document()))
    result = CliRunner().invoke(
        cli,
        ["alpha", "setup-recurrence", str(protocol_path), "--output", str(tmp_path / "out"), *_cache_args(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    caches = captured["environment"]["caches"]
    assert caches["primary"]["path"] == str(tmp_path / "cache-a")
    assert caches["robustness"]["path"] == str(tmp_path / "cache-b")
    assert caches["primary"]["range"]["start"].startswith((date(2021, 6, 1) - timedelta(days=400)).isoformat())
    assert caches["primary"]["range"]["end"].startswith("2026-09-22")
    assert caches["robustness"]["range"]["end"].startswith("2021-06-15")


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
    # The test environment loads a fixture config; the frozen file pins the deployed config.yaml.
    deployed = load_config(str(WORKSPACE_ROOT / "config" / "config.yaml"))
    assert protocol.strategy_config == deployed.strategies.model_dump(mode="json")
    assert dict(protocol.sector_etf) == dict(SECTOR_ETF)
    assert protocol.strategy_config["trend_pullback"]["allow_short"] is False
