"""Investor-day labels with per-row holds, controls, P1-P5 with the issuer-cluster P3, the planted check and the executor."""

import json
import math
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.apriori import idday_study
from agentic_trader.research.apriori.catalog import LoadedIddayEntry, load_idday_entry
from agentic_trader.research.apriori.idday_events import CONTROL_CANDIDATE_COLUMNS, IDDAY_EVENT_COLUMNS, IddayInputs
from agentic_trader.research.apriori.idday_parser import PARSER_VERSION, EventTable, IddayEvent
from agentic_trader.research.apriori.idday_study import (
    IDDAY_ARM,
    IDDAY_CONTROL_CELL_COLUMNS,
    IDDAY_CONTROL_COLUMNS,
    IddayDecision,
    evaluate_idday,
    execute_idday_study,
    idday_label_columns,
    label_idday,
    planted_check,
)
from agentic_trader.research.apriori.pead_events import decision_at
from agentic_trader.research.investor_days import IssuerRecord
from agentic_trader.research.pooled.cube import LabelCube
from agentic_trader.research.setups.labels import BracketArm, label_bracket


FIXTURE = Path(__file__).parent / "fixtures" / "idday-v1-test.json"
ENTRY = load_idday_entry(FIXTURE).entry
CLOCK = time(10, 35)
HOLIDAY = date(2021, 9, 6)  # Labor Day: the Friday before it is four calendar days from the next session
DAYS = tuple(day.date() for day in pd.bdate_range("2021-01-04", periods=260) if day.date() != HOLIDAY)
D = 150
SPY_RET = np.random.default_rng(7).normal(0.0, 0.01, len(DAYS))
SPY_CLOSE = 400.0 * np.cumprod(1.0 + SPY_RET)
SPY_OPEN = SPY_CLOSE * (1.0 + np.random.default_rng(8).normal(0.0, 0.002, len(DAYS)))
CONTROLS = ("C1", "C2", "C3", "C4", "C5")
COSTS = tuple(f"r_cost_{cost:g}bps" for cost in ENTRY.costs_bps_per_side)


def _daily(closes, *, opens=None, high=None, low=None, drop=()):
    closes = np.asarray(closes, dtype=float)
    index = pd.DatetimeIndex([datetime.combine(day, time(5), tzinfo=UTC) for day in DAYS])
    frame = pd.DataFrame(
        {
            "Open": closes if opens is None else opens,
            "High": closes * 1.01 if high is None else high,
            "Low": closes * 0.99 if low is None else low,
            "Close": closes,
            "Volume": 1e6,
        },
        index=index,
    )
    return frame.drop(index[list(drop)])


def _flat_daily():
    """Constant closes with a one-dollar true range: ATR(14) is 1.0 and beta 0."""
    closes = np.full(len(DAYS), 100.0)
    return _daily(closes, high=closes + 0.5, low=closes - 0.5)


def _beta_two():
    """Adjusted closes whose simple returns are exactly twice SPY's (trailing beta 2)."""
    return 50.0 * np.cumprod(1.0 + 2.0 * SPY_RET)


def _hourly(level=100.0, *, bars=None, drop=(), hours=None):
    """Seven regular bars (09:00-15:00 New York) a session at ``level`` plus a 07:00 bar the labeller ignores.

    ``bars`` overrides ``(position, hour) -> (open, high, low, close)``; ``drop`` removes whole sessions;
    ``hours`` maps a position to the regular hours it keeps.
    """
    index, rows = [], []
    for p, day in enumerate(DAYS):
        if p in drop:
            continue
        price = level(p) if callable(level) else level
        for hour in (7, *(hours or {}).get(p, range(9, 16))):
            ohlc = (price, price, 1.0, price) if hour == 7 else (bars or {}).get((p, hour), (price,) * 4)
            index.append(datetime.combine(day, time(hour), tzinfo=ET_TZ).astimezone(UTC))
            rows.append((*ohlc, 1000.0))
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "Volume"], index=pd.DatetimeIndex(index))


FLAT = _hourly(100.0)
SPY_HOURLY = _hourly(400.0)


def _event(symbol, d=D, hold=10, *, role="leg", pair_id=None, atr=1.0, **extra):
    event_id = f"{pair_id}:placebo" if role == "placebo" else f"{symbol}:{DAYS[d].isoformat()}"
    row = {
        "event_id": event_id,
        "pair_id": pair_id or event_id,
        "role": role,
        "symbol": symbol,
        "cik": 1,
        "klass": "standalone",
        "band": "v3",
        "session": DAYS[d],
        "decision_at": decision_at(DAYS[d], CLOCK),
        "F": DAYS[d - 2],
        "E": None if role == "placebo" else DAYS[d + hold],
        "X": DAYS[d + hold - 1],
        "hold_sessions": hold,
        "atr": atr,
        "ret_20": 0.0,
        "bucket": 1,
        "keyword": None if role == "placebo" else "investor day",
        "item_2_02": None if role == "placebo" else False,
    }
    row.update(extra)
    return row


def _events(*rows):
    frame = pd.DataFrame(list(rows), columns=list(IDDAY_EVENT_COLUMNS))
    for column in ("F", "E", "keyword", "item_2_02"):
        frame[column] = frame[column].astype(object).where(frame[column].notna(), None)
    return frame


def _candidates(*rows):
    return pd.DataFrame(
        [
            {
                "pair_id": pair_id,
                "role": role,
                "session": DAYS[d],
                "control_symbols": ",".join(symbols),
                "hold_sessions": h,
            }
            for pair_id, role, d, symbols, h in rows
        ],
        columns=list(CONTROL_CANDIDATE_COLUMNS),
    )


def _cube(symbols):
    shape = (len(DAYS), len(symbols))
    arrays = {
        "eligible": np.ones(shape, bool),
        "labelled": np.ones(shape, bool),
        "r_gross": np.zeros(shape),
        "r_cost": np.zeros(shape),
        "holding": np.full(shape, 10, np.int16),
        "hit": np.ones(shape, np.int8),
        "tiebreak": np.zeros(shape, np.uint64),
        "dollar_volume": np.ones(shape),
    }
    return LabelCube(
        spec_identity="spec", cohort_sha256="c" * 64, sessions=DAYS, symbols=tuple(symbols), arrays=arrays, coverage={}
    )


def _inputs(hourly, *, adjusted=None, spy_hourly=SPY_HOURLY, provenance=None, issuers=None):
    daily = {**{s: _flat_daily() for s in ("AAA", "BBB", *CONTROLS)}, **(adjusted or {})}
    issuers = issuers or {}
    return IddayInputs(
        cube=_cube(sorted(daily)),
        trading_days=DAYS,
        adjusted=daily,
        hourly=hourly,
        reports={},
        actions=(),
        identity_breaks={},
        market_daily=_daily(SPY_CLOSE, opens=SPY_OPEN),
        market_hourly=spy_hourly,
        decision_time=CLOCK,
        provenance=provenance or {},
        events=EventTable(PARSER_VERSION, "0" * 64, ()),
        issuers=issuers,
        issuer_of={symbol: cik for cik, record in issuers.items() for symbol in record.symbols},
        band_of=dict.fromkeys(sorted(daily), "v3"),
    )


def _label(events, hourly, *, candidates=None, **kwargs):
    return label_idday(events, _candidates() if candidates is None else candidates, _inputs(hourly, **kwargs), ENTRY)


def _ny_date(stamp):
    return pd.Timestamp(stamp).tz_convert(ET_TZ).date()


def _spy_on_label_bracket(monkeypatch, seen):
    def spy(levels, when, hourly, **kwargs):
        seen.append({"kwargs": kwargs, "first": hourly.index.min(), "last": hourly.index.max()})
        return label_bracket(levels, when, hourly, **kwargs)

    monkeypatch.setattr(idday_study, "label_bracket", spy)


def _control_hourly(hold=10):
    """Control ``Ck`` closes its ``hold``-th session at ``100 + 2k``: ``r = k`` at ATR 1 (risk 2)."""
    return {
        symbol: _hourly(100.0, bars={(D + hold - 1, 15): (100.0, 100.0 + 2 * k, 100.0, 100.0 + 2 * k)})
        for k, symbol in enumerate(CONTROLS, start=1)
    }


# --- labels: per-row holds ---------------------------------------------------------------------------


def test_the_arm_has_no_target_and_no_trail():
    assert BracketArm(name="idday_time_exit", target_r=None, trail=None) == IDDAY_ARM


def test_long_hold_not_immature(monkeypatch):
    seen: list[dict] = []
    _spy_on_label_bracket(monkeypatch, seen)
    labels, controls, _, counts = _label(_events(_event("AAA", hold=60)), {"AAA": FLAT})

    assert tuple(labels.columns) == idday_label_columns(ENTRY)
    assert tuple(controls.columns) == IDDAY_CONTROL_COLUMNS
    row = labels.iloc[0]
    assert row["hit"] == "timeout" and row["holding_sessions"] == 60
    assert _ny_date(row["exit_time"]) == DAYS[D + 59] == row["X"]
    assert pd.Timestamp(row["exit_time"]).tz_convert(ET_TZ).hour == 15
    assert counts["reasons"] == {} and counts["labelled"] == {"leg": 1, "placebo": 0}
    [call] = seen
    assert call["kwargs"] == {"max_hold_sessions": 60, "cost_bps_per_side": ENTRY.decision_cost_bps, "arm": IDDAY_ARM}
    # The slice runs from the decision minus a day to the close of the H-th session plus 3 calendar days,
    # far past the cube's 45-calendar-day label span.
    when = pd.Timestamp(decision_at(DAYS[D], CLOCK))
    assert call["first"] >= when - timedelta(days=1)
    assert call["last"] <= pd.Timestamp(datetime.combine(DAYS[D + 59] + timedelta(days=3), time(16), tzinfo=ET_TZ))
    assert call["last"] > when + timedelta(days=45)


def test_a_time_exit_before_a_long_weekend_still_resolves():
    x = DAYS.index(date(2021, 9, 3))  # a Friday; the next session is Tuesday 2021-09-07
    assert DAYS[x + 1] == date(2021, 9, 7)
    labels, _, _, counts = _label(_events(_event("AAA", x - 4, 5)), {"AAA": FLAT})
    assert counts["reasons"] == {}
    row = labels.iloc[0]
    assert row["hit"] == "timeout" and _ny_date(row["exit_time"]) == date(2021, 9, 3)


def test_cells_keyed_by_hold(monkeypatch):
    seen: list[dict] = []
    _spy_on_label_bracket(monkeypatch, seen)
    short, long = _event("AAA", hold=5), _event("BBB", hold=20)
    candidates = _candidates((short["pair_id"], "leg", D, CONTROLS, 5), (long["pair_id"], "leg", D, CONTROLS, 20))
    hourly = {"AAA": FLAT, "BBB": FLAT, **dict.fromkeys(CONTROLS, FLAT)}
    _, controls, cells, counts = _label(_events(short, long), hourly, candidates=candidates)

    assert len(seen) == 12  # two legs and each control symbol once per hold
    assert sorted(call["kwargs"]["max_hold_sessions"] for call in seen) == [5] * 6 + [20] * 6
    assert tuple(cells.columns) == IDDAY_CONTROL_CELL_COLUMNS
    c1 = cells[cells["symbol"] == "C1"].set_index("hold_sessions")
    assert _ny_date(c1.at[5, "exit_time"]) == DAYS[D + 4]
    assert _ny_date(c1.at[20, "exit_time"]) == DAYS[D + 19]
    assert list(controls["hold_sessions"]) == [5, 20]
    assert counts["control_cells"] == {"labelled": 10, "reasons": {}}
    assert counts["paired"] == {"leg": 2, "placebo": 0}


def test_hold_outside_window_excluded(monkeypatch):
    def late(levels, when, hourly, **kwargs):
        outcome = label_bracket(levels, when, hourly, **kwargs)
        return replace(outcome, exit_time=datetime.combine(DAYS[D + 10], time(10), tzinfo=ET_TZ))

    monkeypatch.setattr(idday_study, "label_bracket", late)
    placebo = _event("BBB", role="placebo", pair_id="ZZZ:x")
    labels, _, _, counts = _label(_events(_event("AAA"), placebo), {"AAA": FLAT, "BBB": FLAT})
    assert labels.empty  # both exit after the close of X, the 10th session
    assert counts["reasons"] == {"hold_outside_window": 2}
    assert counts["reasons_by_role"] == {"hold_outside_window": {"leg": 1, "placebo": 1}}
    assert counts["reasons_by_year"] == {"hold_outside_window": {DAYS[D].year: 2}}


def test_a_fill_that_slips_past_the_decision_session_is_outside_the_hold_window():
    frame = _hourly(100.0, hours={D: (9, 10)})  # no regular bar after 10:35 on D: the fill moves to D + 1
    labels, _, _, counts = _label(_events(_event("AAA")), {"AAA": frame})
    assert labels.empty and counts["reasons"] == {"hold_outside_window": 1}


def test_a_stop_touch_exits_at_the_stop_and_costs_come_from_one_walk():
    frame = _hourly(100.0, bars={(D + 3, 12): (99.5, 99.5, 97.5, 98.5)})
    row = _label(_events(_event("AAA", hold=30)), {"AAA": frame})[0].iloc[0]
    assert row["hit"] == "stop" and row["r"] == pytest.approx(-1.0) and row["holding_sessions"] == 4
    assert row["r_cost"] == row["r_cost_15bps"]
    assert row["r_cost_5bps"] == pytest.approx(-1.0 - 2 * 5 / 1e4 * 100.0 / 2.0)
    assert row["r_cost_25bps"] == pytest.approx(-1.0 - 2 * 25 / 1e4 * 100.0 / 2.0)


def test_a_held_session_without_bars_excludes_the_row_but_a_session_after_the_hold_does_not():
    labels, _, _, counts = _label(_events(_event("AAA", hold=18)), {"AAA": _hourly(100.0, drop=(D + 17,))})
    assert labels.empty and counts["reasons"] == {"missing_session_bars": 1}

    # X is a Wednesday: without Thursday's bars, Friday's (inside the 3-day margin) resolves the time exit.
    assert DAYS[D + 17].weekday() == 2
    labels, _, _, counts = _label(_events(_event("AAA", hold=18)), {"AAA": _hourly(100.0, drop=(D + 18,))})
    assert len(labels) == 1 and counts["reasons"] == {}
    assert _ny_date(labels.iloc[0]["exit_time"]) == DAYS[D + 17]


@pytest.mark.parametrize(
    ("reason", "hourly", "atr"),
    [
        ("no_hourly_bars", {}, 1.0),
        ("no_decision_price", {"AAA": _hourly(100.0, hours={D: (10, 11, 12, 13, 14, 15)})}, 1.0),
        ("degenerate_levels", {"AAA": FLAT}, 60.0),
        ("immature", {"AAA": _hourly(100.0, drop=tuple(range(D + 10, len(DAYS))))}, 1.0),
    ],
)
def test_exclusions_are_counted_by_reason_year_and_role(reason, hourly, atr):
    labels, _, _, counts = _label(_events(_event("AAA", role="placebo", pair_id="P:x", atr=atr)), hourly)
    assert labels.empty
    assert counts["reasons"] == {reason: 1}
    assert counts["reasons_by_year"] == {reason: {DAYS[D].year: 1}}
    assert counts["reasons_by_role"] == {reason: {"placebo": 1}}


def test_empty_events_give_empty_frames_with_their_columns():
    labels, controls, cells, counts = _label(_events(), {})
    assert labels.empty and tuple(labels.columns) == idday_label_columns(ENTRY)
    assert controls.empty and tuple(controls.columns) == IDDAY_CONTROL_COLUMNS
    assert cells.empty and tuple(cells.columns) == IDDAY_CONTROL_CELL_COLUMNS
    assert counts["paired"] == {"leg": 0, "placebo": 0}


# --- net excess and the excess without the stop -------------------------------------------------------


def test_net_excess_and_the_no_stop_excess_use_the_rows_own_hold():
    adjusted = {"AAA": _daily(_beta_two())}
    leg = _event("AAA", hold=20)
    placebo = _event("AAA", D - 40, 20, role="placebo", pair_id=leg["pair_id"])  # 109 returns: a beta
    labels = _label(_events(placebo, leg), {"AAA": FLAT}, adjusted=adjusted)[0]

    row = labels[labels["role"] == "leg"].iloc[0]
    assert row["beta"] == pytest.approx(2.0, rel=1e-9) and np.isfinite(row["net_excess_r"])
    assert row["market_r"] == 0.0  # flat SPY hourly bars over the hold
    x = D + 19
    closes = _beta_two()
    spy = SPY_CLOSE[x] / SPY_OPEN[D] - 1.0
    expected = ((closes[x] - 100.0) - 2.0 * spy * 100.0) / 2.0
    assert row["no_stop_excess_r"] == pytest.approx(expected, rel=1e-9)
    assert np.isfinite(labels[labels["role"] == "placebo"].iloc[0]["no_stop_excess_r"])

    missing = {"AAA": _daily(_beta_two(), drop=(x,))}
    row = _label(_events(leg), {"AAA": FLAT}, adjusted=missing)[0].iloc[0]
    assert np.isnan(row["no_stop_excess_r"]) and np.isfinite(row["net_excess_r"])


def test_net_excess_is_nan_without_spy_hourly_bars():
    row = _label(_events(_event("AAA")), {"AAA": FLAT}, spy_hourly=pd.DataFrame())[0].iloc[0]
    assert np.isnan(row["market_r"]) and np.isnan(row["net_excess_r"])


# --- controls --------------------------------------------------------------------------------------


def test_the_control_row_is_the_mean_of_its_five_cells_labelled_with_the_legs_hold():
    leg = _event("AAA", hold=10)
    candidates = _candidates((leg["pair_id"], "leg", D, CONTROLS, 10))
    _, controls, cells, counts = _label(_events(leg), {"AAA": FLAT, **_control_hourly(10)}, candidates=candidates)

    row = controls.iloc[0]
    assert row["pair_id"] == leg["pair_id"] and row["role"] == "leg" and row["session"] == DAYS[D]
    assert row["hold_sessions"] == 10 and row["control_symbols"] == ",".join(CONTROLS)
    assert row["r_cost"] == pytest.approx(3.0 - 2 * 15 / 1e4 * 100.0 / 2.0)
    assert list(cells["symbol"]) == list(CONTROLS) and set(cells["hold_sessions"]) == {10}
    assert set(cells["hit"]) == {"timeout"}
    assert cells["r_cost"].tolist() == pytest.approx([k - 2 * 15 / 1e4 * 100.0 / 2.0 for k in range(1, 6)])
    assert cells["r_cost"].mean() == pytest.approx(row["r_cost"])
    assert counts["paired"] == {"leg": 1, "placebo": 0}
    assert counts["unpaired"] == {"leg": {}, "placebo": {}}


def test_a_failing_control_cell_unpairs_its_row_by_reason_year_and_class():
    leg = _event("AAA", hold=10, klass="with_earnings")
    placebo = _event("AAA", D - 40, 10, role="placebo", pair_id=leg["pair_id"], klass="with_earnings")
    candidates = _candidates(
        (leg["pair_id"], "leg", D, CONTROLS, 10), (leg["pair_id"], "placebo", D - 40, CONTROLS, 10)
    )
    hourly = {"AAA": FLAT, **_control_hourly(10)}
    hourly["C3"] = _hourly(100.0, drop=(D + 2,))
    del hourly["C5"]
    labels, controls, _, counts = _label(_events(placebo, leg), hourly, candidates=candidates)

    assert sorted(labels["role"]) == ["leg", "placebo"] and controls.empty
    assert counts["unpaired"] == {"leg": {"missing_session_bars": 1}, "placebo": {"no_hourly_bars": 1}}
    assert counts["unpaired_by_year"] == {"leg": {DAYS[D].year: 1}, "placebo": {DAYS[D].year: 1}}
    assert counts["unpaired_by_class"] == {"leg": {"with_earnings": 1}, "placebo": {"with_earnings": 1}}
    for role in ("leg", "placebo"):
        assert counts["labelled"][role] == counts["paired"][role] + sum(counts["unpaired"][role].values())


def test_inconsistent_inputs_raise():
    leg = _event("AAA")
    hourly = {"AAA": FLAT, **_control_hourly()}
    with pytest.raises(ValueError, match="clock"):
        _label(_events({**leg, "decision_at": decision_at(DAYS[D], time(9, 35))}), hourly)
    with pytest.raises(ValueError, match="10-session hold"):
        _label(_events({**leg, "X": DAYS[D + 10]}), hourly)
    with pytest.raises(ValueError, match="has 4 controls, not 5"):
        _label(_events(leg), hourly, candidates=_candidates((leg["pair_id"], "leg", D, CONTROLS[:4], 10)))
    with pytest.raises(ValueError, match="controls on"):
        _label(_events(leg), hourly, candidates=_candidates((leg["pair_id"], "leg", D + 1, CONTROLS, 10)))
    with pytest.raises(ValueError, match="hold"):
        _label(_events(leg), hourly, candidates=_candidates((leg["pair_id"], "leg", D, CONTROLS, 12)))


# --- evaluation ------------------------------------------------------------------------------------

FAST = ENTRY.model_copy(
    update={
        "bootstrap": ENTRY.bootstrap.model_copy(update={"draws": 200}),
        "cluster_bootstrap": ENTRY.cluster_bootstrap.model_copy(update={"draws": 200}),
    }
)
OLD = tuple(day.date() for day in pd.bdate_range("2022-03-01", periods=120))
NEW = tuple(day.date() for day in pd.bdate_range("2023-01-02", periods=60))  # every one from recent_from
DECISIONS = {"survives_development", "failed", "inconclusive_underpowered"}
SHIFT = ENTRY.placebo.planted_return * 100.0 / 4.0  # 0.4625 R at decision price 100, entry 100 and stop 96


def _row(pair_id, role, session, r_cost, net=None, **extra):
    row: dict[str, Any] = dict.fromkeys(idday_label_columns(ENTRY), math.nan)
    row.update(
        event_id=f"{pair_id}:placebo" if role == "placebo" else pair_id,
        pair_id=pair_id,
        role=role,
        symbol=pair_id.split(":")[0],
        cik=0,
        klass="standalone",
        band="v3",
        session=session,
        hold_sessions=10,
        bucket=1,
        keyword=None if role == "placebo" else "investor day",
        decision_price=100.0,
        entry=100.0,
        stop=96.0,
        hit="timeout",
        holding_sessions=10,
        r_cost=r_cost,
        net_excess_r=r_cost if net is None else net,
    )
    row.update({column: r_cost for column in COSTS})
    row.update(extra)
    return row


def _control(pair_id, role, session, r_cost):
    return {
        "pair_id": pair_id,
        "role": role,
        "session": session,
        "hold_sessions": 10,
        "r_cost": r_cost,
        "net_excess_r": r_cost,
        "beta": 1.0,
        "control_symbols": ",".join(CONTROLS),
    }


def _frames(rows, controls=()):
    return (
        pd.DataFrame(list(rows), columns=list(idday_label_columns(ENTRY))),
        pd.DataFrame(list(controls), columns=list(IDDAY_CONTROL_COLUMNS)),
    )


def _world(n=400, recent=100, *, leg=0.5, leg_gap=0.5, placebo=0.0, placebo_gap=0.0, noise=0.2, seed=11):
    """``n`` paired legs of distinct issuers (the last ``recent`` from ``recent_from``), each with a paired placebo."""
    rng = np.random.default_rng(seed)
    rows, controls = [], []
    for i in range(n):
        session = OLD[i % len(OLD)] if i < n - recent else NEW[i % len(NEW)]
        pair = f"S{i}:{session.isoformat()}"
        value = leg + rng.normal(0.0, noise)
        rows.append(_row(pair, "leg", session, value, cik=1000 + i))
        controls.append(_control(pair, "leg", session, value - leg_gap))
        if placebo is not None:
            value = placebo + rng.normal(0.0, noise)
            rows.append(_row(pair, "placebo", session, value, cik=1000 + i))
            controls.append(_control(pair, "placebo", session, value - placebo_gap))
    return _frames(rows, controls)


def _one_host_world():
    """Two legs a session: an annual host (CIK 1) beats its controls by 1 R, every other issuer by nothing."""
    rng = np.random.default_rng(5)
    rows, controls = [], []
    for i, session in enumerate((*OLD[:110], *NEW[:40])):
        for k, (cik, gap) in enumerate(((1, 1.0), (2000 + i, 0.0))):
            pair = f"H{i}{k}:{session.isoformat()}"
            rows.append(_row(pair, "leg", session, gap, net=0.5 + rng.normal(0.0, 0.2), cik=cik))
            controls.append(_control(pair, "leg", session, 0.0))
            value = rng.normal(0.0, 0.2)
            rows.append(_row(pair, "placebo", session, value, cik=cik))
            controls.append(_control(pair, "placebo", session, value))
    return _frames(rows, controls)


def _evaluate(labels, controls, entry=FAST):
    result = evaluate_idday(labels, controls, entry)
    assert {"criteria", "placebo", "planted", "decision", "diagnostics"} <= set(result)
    assert set(result["criteria"]) == {"P1", "P2", "P3", "P4", "P5"}
    assert result["decision"] in DECISIONS
    return result


def test_the_decision_vocabulary_is_exactly_three_values():
    assert {decision.value for decision in IddayDecision} == DECISIONS


def test_planted_detected():
    rng = np.random.default_rng(3)
    rows, controls = [], []
    for i in range(400):
        session = (*OLD, *NEW)[i % (len(OLD) + len(NEW))]
        pair = f"P{i}:{session.isoformat()}"
        value = rng.normal(0.0, 0.5)
        rows.append(_row(pair, "placebo", session, value, net=rng.normal(0.0, 0.5)))
        controls.append(_control(pair, "placebo", session, rng.normal(0.0, 0.5)))
    labels, frame = _frames(rows, controls)
    planted = planted_check(labels, frame, FAST)

    assert planted["planted_return"] == 0.0185 and planted["n_paired"] == 400
    assert planted["shift_r"]["mean"] == pytest.approx(SHIFT) == pytest.approx(0.4625)
    assert planted["P1"]["holds"] and planted["P2"]["holds"] and planted["detected"] is True
    assert planted["mde"]["mde_r"] == pytest.approx((1.645 + 0.84) * planted["mde"]["boot_sd"])
    assert 0 < planted["mde"]["mde_r"] < SHIFT


def test_the_planted_shift_is_the_planted_return_in_r_per_paired_placebo_only():
    s1, s2 = OLD[:2]
    rows = [
        _row("A:1", "placebo", s1, 0.1),
        _row("B:1", "placebo", s2, -0.3, decision_price=80.0, entry=80.5, stop=76.5),  # risk 4
        _row("C:1", "placebo", s2, 5.0),  # unpaired: never shifted
        _row("A:1", "leg", s1, 9.0),
    ]
    controls = [_control("A:1", "placebo", s1, 0.0), _control("B:1", "placebo", s2, 0.0), _control("A:1", "leg", s1, 0)]
    labels, frame = _frames(rows, controls)
    planted = planted_check(labels, frame, FAST)
    shifts = [0.0185 * 100.0 / 4.0, 0.0185 * 80.0 / 4.0]
    assert planted["n_paired"] == 2
    assert planted["shift_r"]["min"] == pytest.approx(min(shifts)) and planted["shift_r"]["max"] == pytest.approx(
        max(shifts)
    )
    assert planted["P1"]["mean_r_cost"] == pytest.approx(np.mean([0.1 + shifts[0], -0.3 + shifts[1]]))


def test_decision_rules_all_hold_survives_development():
    result = _evaluate(*_world())
    assert all(criterion["holds"] for criterion in result["criteria"].values())
    assert result["criteria"]["P3"]["issuer_cluster"]["holds"]
    assert result["planted"]["detected"] and not result["placebo"]["P2"]["holds"]
    assert result["decision"] == "survives_development"


def test_decision_rules_an_undetected_planted_effect_is_inconclusive():
    result = _evaluate(*_world(placebo=-2.0))  # +0.46 R cannot lift a -2 R placebo above zero
    assert not result["planted"]["detected"]
    assert result["decision"] == "inconclusive_underpowered"


def test_decision_rules_a_failing_issuer_cluster_test_fails_the_read_while_p1_p2_hold():
    labels, controls = _one_host_world()
    result = _evaluate(labels, controls)
    criteria = result["criteria"]
    assert criteria["P1"]["holds"] and criteria["P2"]["holds"] and criteria["P4"]["holds"] and criteria["P5"]["holds"]
    p3 = criteria["P3"]
    assert p3["trimmed_mean"] > 0 and p3["recent_mean"] > 0
    cluster = p3["issuer_cluster"]
    assert cluster["unit"] == "r_cost" and cluster["n"] == 300 and cluster["n_clusters"] == 151
    assert cluster["mean_difference"] == pytest.approx(criteria["P2"]["mean_diff"]) == pytest.approx(0.5)
    assert not cluster["holds"] and not p3["holds"]
    assert result["planted"]["detected"] and not result["placebo"]["P2"]["holds"]
    assert result["decision"] == "failed"


def test_decision_rules_a_placebo_whose_p2_holds_fails_the_read():
    result = _evaluate(*_world(leg_gap=1.0, placebo_gap=0.5))
    assert all(criterion["holds"] for criterion in result["criteria"].values())
    assert result["placebo"]["P2"]["holds"] and result["placebo"]["negative_control_fails"] is True
    assert result["decision"] == "failed"


def test_the_issuer_cluster_test_pairs_each_leg_with_its_control_row_by_cik(monkeypatch):
    seen = []

    def spy(leg, control, clusters, spec):
        seen.append((leg.copy(), control.copy(), pd.Series(clusters).tolist(), spec))
        return {"n": len(leg), "n_clusters": 0, "mean_difference": 0.0, "ci90": [0.0, 0.0], "holds": False}

    monkeypatch.setattr(idday_study, "cluster_paired_test", spy)
    s1, s2 = OLD[:2]
    rows = [
        _row("A:1", "leg", s1, 2.0, cik=7),
        _row("B:1", "leg", s2, 3.0, cik=7),
        _row("C:1", "leg", s2, 4.0, cik=9),  # unpaired: no control row
    ]
    controls = [_control("B:1", "leg", s2, 1.0), _control("A:1", "leg", s1, 0.5), _control("A:1", "placebo", s1, 9)]
    _evaluate(*_frames(rows, controls))
    [(leg, control, clusters, spec)] = seen
    assert leg["r_cost"].tolist() == [2.0, 3.0] and control["r_cost"].tolist() == [0.5, 1.0]
    assert clusters == [7, 7] and spec == FAST.cluster_bootstrap


def test_p4_counts_legs_with_a_finite_net_excess_overall_and_from_recent_from():
    labels, controls = _world(250, 60, placebo=None)
    assert _evaluate(labels, controls)["criteria"]["P4"]["holds"]
    labels.loc[labels.index[-1], "net_excess_r"] = math.nan
    p4 = _evaluate(labels, controls)["criteria"]["P4"]
    assert p4["n"] == 249 and p4["n_recent"] == 59 and not p4["holds"]


def test_p5_is_the_legs_paired_difference_minus_the_placebos():
    result = _evaluate(*_world(leg_gap=0.5, placebo_gap=0.2))
    p5 = result["criteria"]["P5"]
    assert p5["leg_diff"] == pytest.approx(result["criteria"]["P2"]["mean_diff"], rel=1e-12)
    assert p5["other_diff"] == pytest.approx(result["placebo"]["P2"]["mean_diff"], rel=1e-12)
    assert p5["mean_diff"] == pytest.approx(0.3, rel=1e-9) and p5["holds"]


def test_empty_frames_evaluate_to_inconclusive():
    result = _evaluate(*_frames([]))
    assert result["decision"] == "inconclusive_underpowered"
    assert not any(criterion["holds"] for criterion in result["criteria"].values())
    assert result["criteria"]["P3"]["issuer_cluster"]["n"] == 0


# --- diagnostics -----------------------------------------------------------------------------------


def test_the_diagnostics_split_legs_and_measure_concentration_exits_costs_and_the_no_stop_excess():
    s1, s2, s3 = date(2021, 3, 1), date(2022, 3, 1), date(2023, 3, 1)
    specs = [  # symbol, session, klass, band, keyword, hold, bucket, net, r_cost, hit, no_stop
        ("A", s1, "with_earnings", "v3", "Investor Day", 5, 0, 1.0, 1.5, "stop", 0.5),
        ("A", s2, "with_earnings", "v3", "investor  day", 30, 0, 3.0, 3.5, "timeout", 2.5),
        ("B", s2, "standalone", "xu_large", "Analyst Day", 40, 1, 5.0, 5.5, "timeout", math.nan),
        ("C", s3, "standalone", "xu_small", "capital markets day", 60, 2, 7.0, 7.5, "timeout", 6.0),
        ("D", s3, "standalone", "xu_large", "Analyst Day", 10, 2, math.nan, 9.0, "timeout", 1.0),
        ("E", s1, "standalone", "v3", "investor day", 20, 1, 2.0, 2.5, "timeout", 1.5),
        ("F", s1, "standalone", "v3", "investor day", 25, 1, 4.0, 4.5, "timeout", 3.5),
    ]
    rows = [
        _row(
            f"{symbol}:{session.isoformat()}",
            "leg",
            session,
            r_cost,
            net=net,
            klass=klass,
            band=band,
            keyword=keyword,
            hold_sessions=hold,
            holding_sessions=4 if hit == "stop" else hold,
            bucket=bucket,
            hit=hit,
            no_stop_excess_r=no_stop,
            r_cost_5bps=r_cost + 0.1,
            r_cost_25bps=r_cost - 0.1,
            cik=ord(symbol),
        )
        for symbol, session, klass, band, keyword, hold, bucket, net, r_cost, hit, no_stop in specs
    ]
    rows.append(_row(f"A:{s1.isoformat()}", "placebo", s1, 40.0, no_stop_excess_r=-1.0))
    controls = [_control(f"A:{s1.isoformat()}", "leg", s1, 0.5), _control(f"C:{s3.isoformat()}", "leg", s3, 1.5)]
    diagnostics = _evaluate(*_frames(rows, controls))["diagnostics"]

    by_class = diagnostics["by_class"]
    assert by_class["with_earnings"]["net_excess_r"] == {"n": 2, "mean": pytest.approx(2.0)}
    assert by_class["with_earnings"]["paired_diff_r_cost"] == {"n": 1, "mean": pytest.approx(1.0)}
    assert by_class["standalone"]["r_cost"] == {"n": 5, "mean": pytest.approx(29.0 / 5)}
    assert by_class["standalone"]["paired_diff_r_cost"] == {"n": 1, "mean": pytest.approx(6.0)}
    assert {k: v["r_cost"]["n"] for k, v in diagnostics["by_year"].items()} == {2021: 3, 2022: 2, 2023: 2}
    assert {k: v["r_cost"]["n"] for k, v in diagnostics["by_band"].items()} == {"v3": 4, "xu_large": 2, "xu_small": 1}
    keywords = {k: v["r_cost"]["n"] for k, v in diagnostics["by_keyword"].items()}
    assert keywords == {"investor day": 4, "analyst day": 2, "capital markets day": 1}
    holds = diagnostics["by_hold_tercile"]["terciles"]
    assert {k: v["r_cost"]["n"] for k, v in holds.items()} == {"low": 3, "mid": 2, "high": 2}
    assert holds["low"]["hold_range"] == [5, 20] and holds["high"]["hold_range"] == [40, 60]
    assert {k: v["r_cost"]["n"] for k, v in diagnostics["by_bucket"].items()} == {0: 2, 1: 3, 2: 2}

    concentration = diagnostics["concentration"]
    assert concentration["excluding_top_symbols"]["symbols"] == ["A", "B", "C", "D", "E"]
    assert concentration["excluding_top_symbols"]["r_cost"] == {"n": 1, "mean": pytest.approx(4.5)}  # F only
    assert concentration["first_leg_per_symbol"]["r_cost"] == {"n": 6, "mean": pytest.approx(30.5 / 6)}
    assert concentration["issuers"] == {"n": 6, "max_legs": 2}

    no_stop = diagnostics["no_stop_excess"]
    assert "daily open on D" in no_stop["definition"]
    assert no_stop["legs"] == {"n": 6, "mean": pytest.approx(15.0 / 6)}
    assert no_stop["placebo"] == {"n": 1, "mean": pytest.approx(-1.0)}
    assert no_stop["by_class"]["with_earnings"] == {"n": 2, "mean": pytest.approx(1.5)}

    exits = diagnostics["exits"]
    assert exits["hits"] == {"stop": 1, "timeout": 6}
    assert exits["mean_holding_sessions"] == pytest.approx((4 + 30 + 40 + 60 + 10 + 20 + 25) / 7)
    assert exits["mean_hold_sessions"] == pytest.approx((5 + 30 + 40 + 60 + 10 + 20 + 25) / 7)
    assert set(diagnostics["costs"]) == set(COSTS)
    assert diagnostics["costs"]["r_cost_5bps"]["mean"] == pytest.approx(34.0 / 7 + 0.1)
    assert diagnostics["costs"]["r_cost_25bps"]["mean"] == pytest.approx(34.0 / 7 - 0.1)
    assert diagnostics["dispersion"]["n"] == 6 and diagnostics["dispersion"]["n_sessions"] == 3
    assert diagnostics["unpaired_legs"] == 5


def test_the_diagnostics_survive_empty_groups():
    rows = [_row("A:1", "leg", OLD[0], 1.0), _row("B:1", "leg", OLD[0], 2.0)]  # one session: no design effect
    diagnostics = _evaluate(*_frames(rows))["diagnostics"]
    assert diagnostics["by_class"]["with_earnings"]["r_cost"]["n"] == 0
    assert diagnostics["by_class"]["with_earnings"]["paired_diff_r_cost"] == {
        "n": 0,
        "mean": pytest.approx(math.nan, nan_ok=True),
    }
    assert diagnostics["by_hold_tercile"]["terciles"] == {}
    assert diagnostics["no_stop_excess"]["legs"]["n"] == 0
    assert np.isnan(diagnostics["dispersion"]["design_effect"])


# --- the executor ----------------------------------------------------------------------------------

CUBE_SHA = _inputs({}).cube.sha256
V3_SHA, XU_SHA = (cohort.cube_sha256 for cohort in ENTRY.cohorts)
EVENTS_SHA = _inputs({}).events.events_sha256  # the fixture inputs' (empty) event table
PINNED = FAST.model_copy(update={"investor_days": FAST.investor_days.model_copy(update={"events_sha256": EVENTS_SHA})})
PASSING = {"errors": 0, "sample_size": ENTRY.parser_check.sample_size, "precision": 1.0}
GOOD = {"cube_sha256_v3": V3_SHA, "cube_sha256_xu": XU_SHA, "merged_cube_sha256": CUBE_SHA, "parser_check": PASSING}
ARTIFACTS = {
    "protocol.json",
    "manifest.json",
    "events.csv.gz",
    "labels.csv.gz",
    "controls.csv.gz",
    "control_cells.csv.gz",
    "placebo.csv.gz",
    "planted.json",
    "result.json",
}


def _loaded(entry=PINNED):
    return LoadedIddayEntry(entry=entry, sha256="f" * 64, path=FIXTURE)


def _pipeline(monkeypatch, labels, controls):
    """Replace events, control matching and labelling with ``labels``/``controls``; returns the call order."""
    calls = []
    events = labels[list(IDDAY_EVENT_COLUMNS)]

    def events_of(inputs, entry):
        calls.append("events")
        return events, {"legs": int((events["role"] == "leg").sum())}

    def matched(frame, inputs, entry):
        assert frame is events
        calls.append("controls")
        return _candidates(), {"paired": {"leg": 0, "placebo": 0}}

    def labelled(frame, candidates, inputs, entry):
        assert frame is events
        calls.append("labels")
        cells = controls.assign(symbol="C1", hit="timeout", exit_time=None)[list(IDDAY_CONTROL_CELL_COLUMNS)]
        return labels, controls, cells, {"labelled": {"leg": int((labels["role"] == "leg").sum())}}

    monkeypatch.setattr(idday_study, "build_idday_events", events_of)
    monkeypatch.setattr(idday_study, "match_controls", matched)
    monkeypatch.setattr(idday_study, "label_idday", labelled)
    return calls


async def test_executor_writes_protocol_and_manifest_first(tmp_path):
    directory = tmp_path / "run"
    seen = {}

    async def build():
        seen["written"] = [(directory / name).exists() for name in ("protocol.json", "manifest.json")]
        raise RuntimeError("cube cache missing")

    result = await execute_idday_study(_loaded(), directory, build=build, environment={"e": 1})
    assert seen == {"written": [True, True]}
    assert result == {"status": "failed", "error": "RuntimeError: cube cache missing", "authorizes_promotion": False}
    assert json.loads((directory / "result.json").read_text()) == result
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["entry_id"] == "idday" and manifest["version"] == 1 and manifest["sha256"] == "f" * 64
    pinned = manifest["pinned"]
    assert [cohort["name"] for cohort in pinned["cohorts"]] == ["v3", "xu"]
    assert [cohort["cube_sha256"] for cohort in pinned["cohorts"]] == [V3_SHA, XU_SHA]
    assert pinned["investor_days"]["events_sha256"] == EVENTS_SHA
    assert pinned["investor_days"]["content_sha256"] == ENTRY.investor_days.content_sha256
    assert pinned["parser_check"]["sha256"] == ENTRY.parser_check.sha256
    assert manifest["environment"] == {"e": 1} and manifest["authorizes_promotion"] is False
    protocol = json.loads((directory / "protocol.json").read_text())
    assert protocol["id"] == "idday" and protocol["sha256"] == "f" * 64
    assert sorted(path.name for path in directory.iterdir()) == ["manifest.json", "protocol.json", "result.json"]


@pytest.mark.parametrize(
    ("provenance", "message"),
    [
        ({**GOOD, "cube_sha256_v3": "0" * 64}, "cube_sha256_v3"),
        ({**GOOD, "cube_sha256_xu": "0" * 64}, "cube_sha256_xu"),
        ({k: v for k, v in GOOD.items() if k != "cube_sha256_xu"}, "cube_sha256_xu"),
        ({**GOOD, "merged_cube_sha256": "0" * 64}, "merged cube"),
    ],
    ids=["v3-digest", "xu-digest", "xu-missing", "merged-digest"],
)
async def test_the_executor_refuses_cube_digests_that_do_not_match(tmp_path, monkeypatch, provenance, message):
    calls = _pipeline(monkeypatch, *_world(10, 5))
    directory = tmp_path / "run"

    async def build():
        return _inputs({}, provenance=provenance)

    result = await execute_idday_study(_loaded(), directory, build=build, environment={})
    assert result["status"] == "failed" and result["authorizes_promotion"] is False
    assert result["error"].startswith("ValueError: ") and message in result["error"]
    assert calls == [] and not (directory / "events.csv.gz").exists()


OTHER_TABLE = EventTable(
    PARSER_VERSION,
    "0" * 64,
    (
        IddayEvent(
            event_id="1:none:2021-06-01",
            cik=1,
            symbols=("AAA",),
            status="event_only",
            filed=None,
            event_date=date(2021, 6, 1),
            n_announcing=0,
            event_day_material=True,
            keyword="investor day",
            sentence="Example Corp is hosting its investor day today.",
            accession="0000000000-21-000001",
            url="https://example.invalid/doc.htm",
            item_2_02=False,
        ),
    ),
)
LIMIT = ENTRY.parser_check.max_errors


@pytest.mark.parametrize(
    ("events", "parser_check", "message"),
    [
        (OTHER_TABLE, PASSING, "events_sha256"),
        (None, {**PASSING, "errors": LIMIT + 1}, f"{LIMIT + 1} errors, above the frozen limit of {LIMIT}"),
        (None, None, "no parser_check"),
        (None, {**PASSING, "sample_size": 59}, "sampled 59"),
        (None, {**PASSING, "errors": "0"}, "integer error count"),
        (None, {**PASSING, "errors": True}, "integer error count"),
    ],
    ids=["events-digest", "too-many-errors", "missing", "sample-size", "string-errors", "bool-errors"],
)
async def test_the_executor_refuses_an_unpinned_event_table_or_a_failing_parser_check(
    tmp_path, monkeypatch, events, parser_check, message
):
    calls = _pipeline(monkeypatch, *_world(10, 5))
    directory = tmp_path / "run"
    provenance = {k: v for k, v in GOOD.items() if k != "parser_check"}
    if parser_check is not None:
        provenance["parser_check"] = parser_check

    async def build():
        inputs = _inputs({}, provenance=provenance)
        return inputs if events is None else replace(inputs, events=events)

    result = await execute_idday_study(_loaded(), directory, build=build, environment={})
    assert result["status"] == "failed" and result["authorizes_promotion"] is False
    assert result["error"].startswith("ValueError: ") and message in result["error"]
    assert calls == [] and not (directory / "events.csv.gz").exists()


async def test_a_parser_check_at_the_error_limit_completes(tmp_path):
    directory = tmp_path / "run"
    provenance = {**GOOD, "parser_check": {**PASSING, "errors": LIMIT, "precision": 1 - LIMIT / 60}}

    async def build():
        return _inputs({}, provenance=provenance)

    result = await execute_idday_study(_loaded(), directory, build=build, environment={})
    assert result["status"] == "completed", result.get("error")
    assert result["inputs"]["parser_check"]["errors"] == LIMIT and result["authorizes_promotion"] is False


async def test_the_executor_writes_every_artifact_with_planted_before_any_leg_statistic(tmp_path, monkeypatch):
    labels, controls = _world()
    calls = _pipeline(monkeypatch, labels, controls)
    directory = tmp_path / "run"
    order, tested = [], []
    real_json, real_frame = idday_study.save_json_report, idday_study.save_frame

    def save_json(payload, path):
        order.append(path.name)
        return real_json(payload, path)

    def save_frame(frame, path):
        order.append(path.name)
        return real_frame(frame, path)

    monkeypatch.setattr(idday_study, "save_json_report", save_json)
    monkeypatch.setattr(idday_study, "save_frame", save_frame)
    for name in ("mean_test", "paired_test", "difference_in_paired_test", "cluster_paired_test"):
        real = getattr(idday_study, name)

        def spy(*args, _real=real, _name=name):
            tested.append((_name, (directory / "planted.json").exists()))
            return _real(*args)

        monkeypatch.setattr(idday_study, name, spy)

    async def build():
        return _inputs({}, provenance={**GOOD, "pages_sha256": "p" * 64})

    result = await execute_idday_study(_loaded(), directory, build=build, environment={})

    assert result["status"] == "completed", result.get("error")
    assert calls == ["events", "controls", "labels"]
    assert {path.name for path in directory.iterdir()} == ARTIFACTS
    assert order == [
        "protocol.json",
        "manifest.json",
        "events.csv.gz",
        "labels.csv.gz",
        "controls.csv.gz",
        "control_cells.csv.gz",
        "placebo.csv.gz",
        "planted.json",
        "result.json",
    ]
    assert tested[:3] == [("mean_test", False), ("paired_test", False), ("paired_test", False)]
    assert all(exists for _, exists in tested[3:]) and ("cluster_paired_test", True) in tested
    assert result["decision"] == "survives_development" and result["authorizes_promotion"] is False
    assert result["inputs"] == {**GOOD, "pages_sha256": "p" * 64, "cube_sha256": CUBE_SHA}
    saved = json.loads((directory / "result.json").read_text())
    assert saved["planted"] == json.loads((directory / "planted.json").read_text())
    assert saved["authorizes_promotion"] is False
    placebo = pd.read_csv(directory / "placebo.csv.gz")
    assert len(placebo) == 400 and set(placebo["role"]) == {"placebo"}
    assert len(pd.read_csv(directory / "labels.csv.gz")) == 800
    for path in directory.glob("*.json"):
        assert '"authorizes_promotion": true' not in path.read_text(), path.name


async def test_the_real_pipeline_on_inputs_without_events_completes_inconclusive(tmp_path):
    directory = tmp_path / "run"
    issuers = {1: IssuerRecord(1, ("AAA",), True, True, (), {"404": 2}), 2: IssuerRecord(2, ("BBB",), False, False, ())}

    async def build():
        return _inputs({}, provenance=GOOD, issuers=issuers)

    result = await execute_idday_study(_loaded(), directory, build=build, environment={})
    assert result["status"] == "completed", result.get("error")
    assert result["decision"] == "inconclusive_underpowered" and result["authorizes_promotion"] is False
    assert {path.name for path in directory.iterdir()} == ARTIFACTS
    for name in ("events", "labels", "controls", "control_cells", "placebo"):
        assert pd.read_csv(directory / f"{name}.csv.gz").empty
    pipeline = result["diagnostics"]["pipeline"]
    assert pipeline["events"] == 0 and pipeline["event_only"] == 0
    assert pipeline["symbols_with_leg"] == 0
    acquisition = pipeline["acquisition"]
    assert acquisition["issuers"] == 2 and acquisition["visible"] == 1 and acquisition["capped"] == 1
    assert acquisition["document_failures"] == {"404": 2}
    assert acquisition["unmapped_symbols"] == len(_inputs({}).cube.symbols) - 2


async def test_a_result_that_cannot_be_encoded_is_recorded_as_failed(tmp_path, monkeypatch):
    _pipeline(monkeypatch, *_world(10, 5))
    directory = tmp_path / "run"

    async def build():
        return _inputs({}, provenance={**GOOD, "read_on": date(2026, 11, 19)})  # a date is not JSON

    result = await execute_idday_study(_loaded(), directory, build=build, environment={})
    assert result["status"] == "failed" and result["error"].startswith("TypeError: ")
    assert json.loads((directory / "result.json").read_text()) == result
