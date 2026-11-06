"""Run-up labels: the time-exit bracket, exclusions, cost columns, net excess, last-day excess and control rows."""

import json
import math
from dataclasses import replace
from datetime import UTC, date, datetime, time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.apriori import runup_study
from agentic_trader.research.apriori.catalog import LoadedRunupEntry, load_runup_entry
from agentic_trader.research.apriori.pead_base_rate import add_net_excess
from agentic_trader.research.apriori.pead_events import decision_at
from agentic_trader.research.apriori.runup_events import (
    CONTROL_CANDIDATE_COLUMNS,
    RUNUP_EVENT_COLUMNS,
    RunupInputs,
)
from agentic_trader.research.apriori.runup_study import (
    RUNUP_ARM,
    RUNUP_CONTROL_COLUMNS,
    RunupDecision,
    cost_column,
    evaluate_runup,
    execute_runup_study,
    label_runup,
    planted_check,
    runup_label_columns,
)
from agentic_trader.research.pooled.cube import LabelCube
from agentic_trader.research.setups.labels import BracketArm, SetupLevels, label_bracket


SHIPPED = Path(__file__).resolve().parents[3] / "config/research/apriori/runup-v1.json"
ENTRY = load_runup_entry(SHIPPED).entry
CLOCK = time(10, 35)
LEAD = ENTRY.event.lead_sessions
DAYS = tuple(day.date() for day in pd.bdate_range("2021-01-04", periods=175))
D, E = 150, 160  # the reference decision and report positions: E - 1 is the 10th session from D
SPY_RET = np.random.default_rng(7).normal(0.0, 0.01, len(DAYS))
SPY_CLOSE = 400.0 * np.cumprod(1.0 + SPY_RET)
CONTROLS = ("C1", "C2", "C3", "C4", "C5")


def _daily(closes, *, high=None, low=None, drop=()):
    closes = np.asarray(closes, dtype=float)
    index = pd.DatetimeIndex([datetime.combine(day, time(5), tzinfo=UTC) for day in DAYS])
    frame = pd.DataFrame(
        {
            "Open": closes,
            "High": closes * 1.01 if high is None else high,
            "Low": closes * 0.99 if low is None else low,
            "Close": closes,
            "Volume": 1e6,
        },
        index=index,
    )
    return frame.drop(index[list(drop)])


def _beta_two(jump=None):
    """Adjusted closes whose simple returns are exactly twice SPY's (trailing beta 2), with an optional jump."""
    closes = 50.0 * np.cumprod(1.0 + 2.0 * SPY_RET)
    if jump is not None:
        at, factor = jump
        closes[at:] *= factor
    return closes


def _flat_daily():
    """Constant closes with a one-dollar true range: ATR(14) is 1.0, beta 0 and the cost the floor."""
    closes = np.full(len(DAYS), 100.0)
    return _daily(closes, high=closes + 0.5, low=closes - 0.5)


def _hourly(level=100.0, *, bars=None, drop=(), hours=None, extended=True):
    """Seven regular bars (09:00-15:00 New York) a session at a flat ``level`` (a number or ``position -> number``).

    ``bars`` overrides ``(position, hour) -> (open, high, low, close)``; ``drop`` removes whole sessions;
    ``hours`` maps a position to the regular hours it keeps; ``extended`` adds a 07:00 bar far below
    every level, which the labeller must ignore.
    """
    index, rows = [], []
    for p, day in enumerate(DAYS):
        if p in drop:
            continue
        price = level(p) if callable(level) else level
        stamps = [*((7,) if extended else ()), *(hours or {}).get(p, range(9, 16))]
        for hour in stamps:
            if hour == 7:
                ohlc = (price, price, 1.0, price)
            else:
                ohlc = (bars or {}).get((p, hour), (price, price, price, price))
            index.append(datetime.combine(day, time(hour), tzinfo=ET_TZ).astimezone(UTC))
            rows.append((*ohlc, 1000.0))
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "Volume"], index=pd.DatetimeIndex(index))


SPY_HOURLY = _hourly(400.0, extended=False)


def _event(symbol, d=D, *, role="leg", atr=1.0, pair_id=None):
    report = DAYS[d + LEAD] if role != "placebo" else None
    event_id = f"{pair_id}:placebo" if role == "placebo" else f"{symbol}:{report.isoformat()}"
    return {
        "event_id": event_id,
        "role": role,
        "pair_id": pair_id or event_id,
        "symbol": symbol,
        "session": DAYS[d],
        "report_date": report,
        "timing": None if role == "placebo" else "unspecified",
        "decision_at": decision_at(DAYS[d], CLOCK),
        "rv": 0.03,
        "tercile": 2 if role != "announcer" else 0,
        "atr": atr,
        "ret20": 0.0,
        "bucket": 2,
        "dv": 1e7,
        "past_move": math.nan,
        "year": DAYS[d].year,
    }


def _events(*rows):
    frame = pd.DataFrame(list(rows), columns=list(RUNUP_EVENT_COLUMNS))
    for column in ("report_date", "timing"):
        frame[column] = frame[column].astype(object).where(frame[column].notna(), None)
    return frame


def _candidates(*rows):
    return pd.DataFrame(
        [
            {"pair_id": pair_id, "role": role, "session": DAYS[d], "control_symbols": ",".join(symbols)}
            for pair_id, role, d, symbols in rows
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
        "holding": np.full(shape, LEAD, np.int16),
        "hit": np.ones(shape, np.int8),
        "tiebreak": np.zeros(shape, np.uint64),
        "dollar_volume": np.ones(shape),
    }
    return LabelCube(
        spec_identity="spec", cohort_sha256="c" * 64, sessions=DAYS, symbols=tuple(symbols), arrays=arrays, coverage={}
    )


def _inputs(hourly, *, adjusted=None, spy_hourly=SPY_HOURLY):
    daily = {"AAA": _daily(_beta_two()), **{s: _flat_daily() for s in (*CONTROLS, "BBB")}, **(adjusted or {})}
    return RunupInputs(
        cube=_cube(sorted(daily)),
        trading_days=DAYS,
        adjusted=daily,
        hourly=hourly,
        reports={},
        actions=(),
        identity_breaks={},
        market_daily=_daily(SPY_CLOSE),
        market_hourly=spy_hourly,
        decision_time=CLOCK,
        provenance={},
    )


def _label(events, hourly, *, candidates=None, **kwargs):
    return label_runup(events, _candidates() if candidates is None else candidates, _inputs(hourly, **kwargs), ENTRY)


def _ny_date(stamp):
    return pd.Timestamp(stamp).tz_convert(ET_TZ).date()


def _rally():
    """100 through D (decision price 100, fill 100 at 11:00), +6R from D + 1 (stop 98 at ATR 1), 113 at E - 1's close."""
    return _hourly(lambda p: 100.0 if p <= D else 112.0, bars={(E - 1, 15): (112.0, 113.0, 112.0, 113.0)})


# --- the bracket --------------------------------------------------------------------------------


def test_the_arm_has_no_target_and_no_trail():
    assert BracketArm(name="runup_time_exit", target_r=None, trail=None) == RUNUP_ARM


def test_a_leg_time_exits_at_the_close_of_the_session_before_the_report_even_after_a_5r_rally():
    labels, controls, counts = _label(_events(_event("AAA")), {"AAA": _rally()})

    assert tuple(labels.columns) == runup_label_columns(ENTRY)
    assert tuple(controls.columns) == RUNUP_CONTROL_COLUMNS
    row = labels.iloc[0]
    assert row["decision_price"] == 100.0 and row["entry"] == 100.0 and row["stop"] == 98.0
    assert row["entry_price"] == 100.0
    assert pd.Timestamp(row["entry_time"]) == pd.Timestamp(datetime.combine(DAYS[D], time(11), tzinfo=ET_TZ))
    assert row["hit"] == "timeout" and row["holding_sessions"] == LEAD
    assert _ny_date(row["exit_time"]) == DAYS[E - 1]  # the 10th session from D
    assert pd.Timestamp(row["exit_time"]).tz_convert(ET_TZ).hour == 15
    assert row["r"] == pytest.approx(6.5)  # 113 at the last close; +6R for nine sessions never exits
    assert counts["labelled"] == {"leg": 1, "announcer": 0, "placebo": 0}
    assert counts["reasons"] == {}


def test_label_bracket_is_called_with_the_runup_arm_and_the_lead_as_the_hold(monkeypatch):
    seen = []

    def spy(levels, when, hourly, **kwargs):
        seen.append(kwargs)
        return label_bracket(levels, when, hourly, **kwargs)

    monkeypatch.setattr(runup_study, "label_bracket", spy)
    _label(_events(_event("AAA")), {"AAA": _rally()})
    assert seen == [{"max_hold_sessions": LEAD, "cost_bps_per_side": ENTRY.decision_cost_bps, "arm": RUNUP_ARM}]


def test_a_stop_touch_exits_at_the_stop():
    bars = {(D + 3, 12): (99.5, 99.5, 97.5, 98.5)}
    frame = _hourly(100.0, bars=bars)
    row = _label(_events(_event("AAA")), {"AAA": frame})[0].iloc[0]
    assert row["hit"] == "stop" and row["r"] == pytest.approx(-1.0)
    assert pd.Timestamp(row["exit_time"]) == pd.Timestamp(datetime.combine(DAYS[D + 3], time(12), tzinfo=ET_TZ))
    assert row["holding_sessions"] == 4


def test_a_gap_through_the_stop_fills_at_the_open():
    bars = {(D + 3, 9): (95.0, 96.0, 94.0, 95.5)}
    row = _label(_events(_event("AAA")), {"AAA": _hourly(100.0, bars=bars)})[0].iloc[0]
    assert row["hit"] == "stop" and row["r"] == pytest.approx(-2.5)


def test_cost_columns_come_from_one_walk_and_the_decisive_one_is_r_cost():
    frame = _rally()
    row = _label(_events(_event("AAA")), {"AAA": frame})[0].iloc[0]
    assert row["r_cost"] == row["r_cost_15bps"]
    levels = SetupLevels("LONG", 100.0, 98.0, 100.0 + 1000 * 2.0)
    when = decision_at(DAYS[D], CLOCK)
    for cost in (5, 15, 25):
        outcome = label_bracket(levels, when, frame, max_hold_sessions=LEAD, cost_bps_per_side=cost, arm=RUNUP_ARM)
        assert row[f"r_cost_{cost}bps"] == pytest.approx(outcome.r_cost, rel=0, abs=1e-12)
    assert row["r_cost_5bps"] > row["r_cost_15bps"] > row["r_cost_25bps"]
    assert row["r_cost"] == pytest.approx(6.5 - 2 * 15 / 1e4 * 100.0 / 2.0)


# --- exclusions ---------------------------------------------------------------------------------


def test_a_fill_that_slips_past_the_decision_session_ends_on_the_report_and_is_excluded():
    only_morning = {D: (9, 10)}  # D has no regular bar after 10:35, so the fill moves to D + 1
    frame = _hourly(100.0, hours=only_morning)
    placebo = _event("BBB", role="placebo", pair_id="AAA:x")
    labels, _, counts = _label(_events(_event("AAA"), placebo), {"AAA": frame, "BBB": frame})

    assert counts["reasons"] == {"exit_on_or_after_report": 1}
    assert counts["reasons_by_year"] == {"exit_on_or_after_report": {2021: 1}}
    assert counts["reasons_by_role"] == {"exit_on_or_after_report": {"leg": 1}}
    # A placebo has no report: the same walk is kept (it ends on its own 10th session).
    assert list(labels["role"]) == ["placebo"]
    assert _ny_date(labels.iloc[0]["exit_time"]) == DAYS[D + LEAD]


def test_a_held_session_without_bars_excludes_the_row_but_a_session_after_the_hold_does_not():
    labels, _, counts = _label(_events(_event("AAA")), {"AAA": _hourly(100.0, drop=(D + 4,))})
    assert labels.empty and counts["reasons"] == {"missing_session_bars": 1}

    labels, _, counts = _label(_events(_event("AAA")), {"AAA": _hourly(100.0, drop=(E,))})
    assert len(labels) == 1 and counts["reasons"] == {}
    assert _ny_date(labels.iloc[0]["exit_time"]) == DAYS[E - 1]


@pytest.mark.parametrize(
    ("reason", "hourly", "atr"),
    [
        ("no_hourly_bars", {}, 1.0),
        ("no_decision_price", {"AAA": _hourly(100.0, hours={D: (10, 11, 12, 13, 14, 15)})}, 1.0),
        ("degenerate_levels", {"AAA": _hourly(100.0)}, 60.0),
        ("immature", {"AAA": _hourly(100.0, drop=tuple(range(E, len(DAYS))))}, 1.0),
    ],
)
def test_exclusions_are_counted_by_reason_year_and_role(reason, hourly, atr):
    labels, _, counts = _label(_events(_event("AAA", role="announcer", atr=atr)), hourly)
    assert labels.empty
    assert counts["reasons"] == {reason: 1}
    assert counts["reasons_by_year"] == {reason: {2021: 1}}
    assert counts["reasons_by_role"] == {reason: {"announcer": 1}}
    assert counts["labelled"] == {"leg": 0, "announcer": 0, "placebo": 0}


def test_empty_events_give_empty_frames_with_their_columns():
    labels, controls, counts = _label(_events(), {})
    assert labels.empty and tuple(labels.columns) == runup_label_columns(ENTRY)
    assert controls.empty and tuple(controls.columns) == RUNUP_CONTROL_COLUMNS
    assert counts["paired"] == {"leg": 0, "placebo": 0}


# --- net excess and the last-day excess -----------------------------------------------------------


def test_net_excess_is_nan_without_spy_hourly_bars_and_otherwise_matches_the_base_rate_helper():
    frame = _rally()
    row = _label(_events(_event("AAA")), {"AAA": frame}, spy_hourly=pd.DataFrame())[0].iloc[0]
    assert row["beta"] == pytest.approx(2.0) and np.isfinite(row["name_cost_bps"])
    assert np.isnan(row["market_r"]) and np.isnan(row["net_excess_r"])

    labels = _label(_events(_event("AAA")), {"AAA": frame})[0]
    expected = add_net_excess(
        labels[["symbol", "session", "entry_price", "entry", "stop", "entry_time", "exit_time", "r"]],
        {"AAA": _daily(_beta_two())},
        _daily(SPY_CLOSE),
        SPY_HOURLY,
    )
    for column in ("beta", "name_cost_bps", "market_r", "net_excess_r"):
        assert labels.iloc[0][column] == expected.iloc[0][column]
    assert np.isfinite(labels.iloc[0]["net_excess_r"]) and labels.iloc[0]["market_r"] == 0.0


def test_last_day_excess_is_the_adjusted_day_minus_one_return_net_of_beta_times_spy_in_r_units():
    adjusted = {"AAA": _daily(_beta_two(jump=(E - 1, 1.05)))}
    events = _events(_event("AAA"), _event("AAA", d=D - 40, role="placebo", pair_id="AAA:x"))
    labels = _label(events, {"AAA": _rally()}, adjusted=adjusted)[0]

    leg = labels[labels["role"] == "leg"].iloc[0]
    closes = _beta_two(jump=(E - 1, 1.05))
    stock = closes[E - 1] / closes[E - 2] - 1.0
    market = SPY_CLOSE[E - 1] / SPY_CLOSE[E - 2] - 1.0
    assert leg["beta"] == pytest.approx(2.0, rel=1e-9)
    assert leg["last_day_excess"] == pytest.approx((stock - 2.0 * market) * 100.0 / 2.0, rel=1e-9)
    assert leg["last_day_excess"] == pytest.approx(0.05 * (1 + 2 * SPY_RET[E - 1]) * 50.0, rel=1e-9)
    assert np.isnan(labels[labels["role"] == "placebo"].iloc[0]["last_day_excess"])

    missing = {"AAA": _daily(_beta_two(), drop=(E - 2,))}
    leg = _label(_events(_event("AAA")), {"AAA": _rally()}, adjusted=missing)[0].iloc[0]
    assert np.isnan(leg["last_day_excess"]) and np.isfinite(leg["net_excess_r"])


# --- controls -------------------------------------------------------------------------------------


def _control_hourly():
    """Control ``Ck`` closes its 10th session at ``100 + 2k``: ``r = k`` at ATR 1 (risk 2)."""
    return {
        symbol: _hourly(100.0, bars={(D + LEAD - 1, 15): (100.0, 100.0 + 2 * k, 100.0, 100.0 + 2 * k)})
        for k, symbol in enumerate(CONTROLS, start=1)
    }


def test_the_control_row_is_the_mean_of_its_five_labelled_cells():
    leg = _event("AAA")
    candidates = _candidates((leg["pair_id"], "leg", D, CONTROLS))
    labels, controls, counts = _label(_events(leg), {"AAA": _rally(), **_control_hourly()}, candidates=candidates)

    assert len(labels) == 1 and len(controls) == 1
    row = controls.iloc[0]
    assert row["pair_id"] == leg["pair_id"] and row["role"] == "leg" and row["session"] == DAYS[D]
    assert row["control_symbols"] == ",".join(CONTROLS)
    assert row["r_cost"] == pytest.approx(3.0 - 2 * 15 / 1e4 * 100.0 / 2.0)  # mean k, less 15 bp a side
    assert row["net_excess_r"] == pytest.approx(3.0 - 2 * 5 / 1e4 * 100.0 / 2.0)  # beta 0, the 5 bp floor
    assert counts["paired"] == {"leg": 1, "placebo": 0}
    assert counts["unpaired"] == {"leg": {}, "placebo": {}}
    assert counts["control_cells"] == {"labelled": 5, "reasons": {}}


def test_a_shared_cell_is_labelled_once(monkeypatch):
    calls = []

    def spy(levels, when, hourly, **kwargs):
        calls.append(when)
        return label_bracket(levels, when, hourly, **kwargs)

    monkeypatch.setattr(runup_study, "label_bracket", spy)
    first, second = _event("AAA"), _event("BBB")
    placebo = _event("C1", role="placebo", pair_id="ZZZ:2021-01-01")  # C1's own cell is also a control cell
    candidates = _candidates(
        (first["pair_id"], "leg", D, CONTROLS),
        (second["pair_id"], "leg", D, CONTROLS),
    )
    hourly = {"AAA": _rally(), "BBB": _rally(), **_control_hourly()}
    _, controls, counts = _label(_events(first, second, placebo), hourly, candidates=candidates)

    assert len(calls) == 7  # AAA, BBB, C1 (placebo and control) and C2..C5
    assert list(controls["pair_id"]) == [first["pair_id"], second["pair_id"]]
    assert controls.iloc[0]["r_cost"] == controls.iloc[1]["r_cost"]
    assert counts["paired"] == {"leg": 2, "placebo": 0}
    assert counts["unpaired"] == {"leg": {}, "placebo": {"no_candidates": 1}}
    assert counts["unpaired_by_year"] == {"leg": {}, "placebo": {2021: 1}}


def test_a_control_cell_that_fails_to_label_leaves_its_pair_unpaired_and_is_counted():
    leg = _event("AAA")
    placebo = _event("AAA", d=D - 40, role="placebo", pair_id=leg["pair_id"])
    candidates = _candidates((leg["pair_id"], "leg", D, CONTROLS), (leg["pair_id"], "placebo", D - 40, CONTROLS))
    hourly = {"AAA": _rally(), **_control_hourly()}
    hourly["C3"] = hourly["C3"].loc[pd.DatetimeIndex(hourly["C3"].index).tz_convert(ET_TZ).date != DAYS[D + 2]]
    del hourly["C5"]
    labels, controls, counts = _label(_events(placebo, leg), hourly, candidates=candidates)

    assert sorted(labels["role"]) == ["leg", "placebo"]  # the rows themselves stay labelled
    assert controls.empty
    assert counts["paired"] == {"leg": 0, "placebo": 0}
    # The first failing cell in nearest-first order names the reason.
    assert counts["unpaired"] == {"leg": {"missing_session_bars": 1}, "placebo": {"no_hourly_bars": 1}}
    assert counts["unpaired_by_year"] == {"leg": {2021: 1}, "placebo": {2021: 1}}
    assert counts["control_cells"]["reasons"] == {"missing_session_bars": 1, "no_hourly_bars": 2}


def test_a_control_without_adjusted_bars_has_no_atr():
    leg = _event("AAA")
    candidates = _candidates((leg["pair_id"], "leg", D, CONTROLS))
    inputs = _inputs({"AAA": _rally(), **_control_hourly()})
    adjusted = {s: f for s, f in inputs.adjusted.items() if s != "C2"}
    inputs = RunupInputs(**{**inputs.__dict__, "adjusted": adjusted})
    _, controls, counts = label_runup(_events(leg), candidates, inputs, ENTRY)
    assert controls.empty and counts["unpaired"]["leg"] == {"no_atr": 1}


def test_counts_add_up_per_matched_role():
    leg, other = _event("AAA"), _event("BBB")
    candidates = _candidates((leg["pair_id"], "leg", D, CONTROLS))
    _, _, counts = _label(
        _events(leg, other), {"AAA": _rally(), "BBB": _rally(), **_control_hourly()}, candidates=candidates
    )
    for role in ("leg", "placebo"):
        assert counts["labelled"][role] == counts["paired"][role] + sum(counts["unpaired"][role].values())
    assert counts["unpaired"]["leg"] == {"no_candidates": 1}


def test_inconsistent_inputs_raise():
    leg = _event("AAA")
    with pytest.raises(ValueError, match="clock"):
        _label(_events({**leg, "decision_at": decision_at(DAYS[D], time(9, 35))}), {"AAA": _rally()})
    with pytest.raises(ValueError, match="has 4 controls, not 5"):
        _label(
            _events(leg),
            {"AAA": _rally(), **_control_hourly()},
            candidates=_candidates((leg["pair_id"], "leg", D, CONTROLS[:4])),
        )
    with pytest.raises(ValueError, match="controls on"):
        _label(
            _events(leg),
            {"AAA": _rally(), **_control_hourly()},
            candidates=_candidates((leg["pair_id"], "leg", D + 1, CONTROLS)),
        )


# --- evaluation: P1-P4, the placebo, the planted effect and the decision ---------------------------

FAST = ENTRY.model_copy(update={"bootstrap": ENTRY.bootstrap.model_copy(update={"draws": 200})})
OLD = tuple(day.date() for day in pd.bdate_range("2022-03-01", periods=120))
NEW = tuple(day.date() for day in pd.bdate_range("2023-01-02", periods=60))  # every one from recent_from
DECISIONS = {"survives_development", "failed", "inconclusive_underpowered"}
SHIFT = ENTRY.placebo.planted_return * 100.0 / 2.0  # 0.315 R at decision price 100, entry 100 and stop 98


def _row(pair_id, role, session, r_cost, net=None, **extra):
    row = dict.fromkeys(runup_label_columns(ENTRY), math.nan)
    row.update(
        event_id=f"{pair_id}:placebo" if role == "placebo" else pair_id,
        role=role,
        pair_id=pair_id,
        symbol=pair_id.split(":")[0],
        session=session,
        report_date=None if role == "placebo" else session,
        timing=None if role == "placebo" else "unspecified",
        tercile=0 if role == "announcer" else 2,
        bucket=1,
        decision_price=100.0,
        entry=100.0,
        stop=98.0,
        hit="timeout",
        holding_sessions=LEAD,
        r_cost=r_cost,
        net_excess_r=r_cost if net is None else net,
        year=session.year,
    )
    row.update({cost_column(cost): r_cost for cost in ENTRY.costs_bps_per_side})
    row.update(extra)
    return row


def _control(pair_id, role, session, r_cost):
    return {
        "pair_id": pair_id,
        "role": role,
        "session": session,
        "r_cost": r_cost,
        "net_excess_r": r_cost,
        "control_symbols": "C1,C2,C3,C4,C5",
    }


def _frames(rows, controls=()):
    return (
        pd.DataFrame(list(rows), columns=list(runup_label_columns(ENTRY))),
        pd.DataFrame(list(controls), columns=list(RUNUP_CONTROL_COLUMNS)),
    )


def _world(n=1200, recent=400, *, leg=0.5, leg_gap=0.5, placebo=0.0, placebo_gap=0.0, noise=0.2, seed=11):
    """``n`` paired legs (the last ``recent`` from ``recent_from``), each with one paired placebo.

    A leg's ``r_cost`` and ``net_excess_r`` are ``leg`` plus noise and its control is ``leg_gap`` below it;
    a placebo is ``placebo`` plus noise and its control ``placebo_gap`` below it (``placebo=None``: none).
    """
    rng = np.random.default_rng(seed)
    rows, controls = [], []
    for i in range(n):
        session = OLD[i % len(OLD)] if i < n - recent else NEW[i % len(NEW)]
        pair = f"S{i}:{session.isoformat()}"
        value = leg + rng.normal(0.0, noise)
        rows.append(_row(pair, "leg", session, value))
        controls.append(_control(pair, "leg", session, value - leg_gap))
        if placebo is not None:
            value = placebo + rng.normal(0.0, noise)
            rows.append(_row(pair, "placebo", session, value))
            controls.append(_control(pair, "placebo", session, value - placebo_gap))
    return _frames(rows, controls)


def _evaluate(labels, controls, entry=FAST):
    result = evaluate_runup(labels, controls, entry)
    assert {"criteria", "placebo", "planted", "decision", "diagnostics"} <= set(result)
    assert set(result["criteria"]) == {"P1", "P2", "P3", "P4"}
    assert {"P1", "P2"} <= set(result["placebo"]) and {"P1", "P2", "detected"} <= set(result["planted"])
    assert result["decision"] in DECISIONS
    assert "eligible_for_probe" not in json.dumps(result, default=str)
    return result


def test_the_decision_vocabulary_is_exactly_three_values():
    assert {decision.value for decision in RunupDecision} == DECISIONS


def test_everything_holding_survives_development():
    result = _evaluate(*_world())
    assert all(criterion["holds"] for criterion in result["criteria"].values())
    assert result["planted"]["detected"] and not result["placebo"]["P2"]["holds"]
    assert result["decision"] == "survives_development"


def test_p1_uses_finite_net_excess_and_p2_paired_r_cost_minus_the_control_mean():
    labels, controls = _world()
    session = OLD[0]
    unpaired = [_row(f"U{i}:x", "leg", session, 50.0, net=7.0) for i in range(5)]  # P1 only
    no_net = [_row(f"N{i}:x", "leg", session, 3.0, net=math.nan) for i in range(5)]  # P2 only
    extra_labels, extra_controls = _frames(
        [*unpaired, *no_net], [_control(f"N{i}:x", "leg", session, 1.0) for i in range(5)]
    )
    labels = pd.concat([labels, extra_labels], ignore_index=True)
    controls = pd.concat([controls, extra_controls], ignore_index=True)
    criteria = _evaluate(labels, controls)["criteria"]

    legs = labels[labels["role"] == "leg"]
    finite = legs[np.isfinite(legs["net_excess_r"].astype(float))]
    assert criteria["P1"]["n"] == 1205 and criteria["P4"]["n"] == 1205
    assert criteria["P1"]["mean_r_cost"] == pytest.approx(finite["net_excess_r"].mean(), rel=1e-12)
    paired = legs[legs["pair_id"].isin(set(controls.loc[controls["role"] == "leg", "pair_id"]))]
    leg_controls = controls[controls["role"] == "leg"]
    assert criteria["P2"]["n_leg"] == 1205 and criteria["P2"]["n_control"] == 1205
    expected = paired["r_cost"].mean() - leg_controls["r_cost"].mean()
    assert criteria["P2"]["mean_diff"] == pytest.approx(expected, rel=1e-12)


@pytest.mark.parametrize(
    ("n", "recent", "nan_legs", "holds"),
    [(1000, 300, 0, True), (999, 300, 0, False), (1000, 299, 0, False), (1001, 301, 1, True), (1000, 300, 1, False)],
)
def test_p4_counts_legs_with_a_finite_net_excess_at_its_boundaries(n, recent, nan_legs, holds):
    labels, controls = _world(n, recent, placebo=None)
    recent_rows = labels.index[labels["session"] >= ENTRY.window.recent_from][:nan_legs]
    labels.loc[recent_rows, "net_excess_r"] = math.nan
    p4 = _evaluate(labels, controls)["criteria"]["P4"]
    assert p4["n"] == n - nan_legs and p4["n_recent"] == recent - nan_legs
    assert p4["holds"] is holds


def test_a_session_on_recent_from_counts_as_recent():
    labels, controls = _world(1000, 300, placebo=None)
    window = ENTRY.window.model_copy(update={"recent_from": NEW[0]})
    p4 = _evaluate(labels, controls, FAST.model_copy(update={"window": window}))["criteria"]["P4"]
    assert p4["n_recent"] == 300 and p4["holds"]  # NEW[0] is itself a leg session


@pytest.mark.parametrize(("outliers", "holds"), [(10, False), (11, True)])
def test_p3_trims_one_percent_of_net_excess_from_each_end(outliers, holds):
    rows = [
        _row(f"S{i}:x", "leg", NEW[i % len(NEW)] if i < outliers else OLD[i % len(OLD)], 9.0, net=net)
        for i, net in enumerate([100.0] * outliers + [-0.01] * (1000 - outliers))
    ]
    p3 = _evaluate(*_frames(rows))["criteria"]["P3"]
    assert p3["recent_mean"] > 0
    assert (p3["trimmed_mean"] > 0) is holds and p3["holds"] is holds


def test_p3_needs_a_positive_mean_from_recent_from():
    rows = [_row(f"S{i}:x", "leg", OLD[i % len(OLD)], 0.0, net=1.0) for i in range(700)]
    rows += [_row(f"R{i}:x", "leg", NEW[i % len(NEW)], 0.0, net=1.0 if i % 2 else -1.0) for i in range(300)]
    p3 = _evaluate(*_frames(rows))["criteria"]["P3"]
    assert p3["trimmed_mean"] > 0 and p3["recent_mean"] == 0.0 and p3["holds"] is False


def test_a_placebo_whose_p2_holds_fails_the_read_even_when_p1_to_p4_hold():
    result = _evaluate(*_world(placebo_gap=0.5))
    assert all(criterion["holds"] for criterion in result["criteria"].values())
    assert result["planted"]["detected"] and result["placebo"]["P2"]["holds"]
    assert result["placebo"]["negative_control_fails"] is True
    assert result["decision"] == "failed"


@pytest.mark.parametrize("leg", [0.5, -0.5])
def test_an_undetected_planted_effect_is_inconclusive_whatever_the_legs_show(leg):
    result = _evaluate(*_world(leg=leg, placebo=-2.0))  # +0.315 R cannot lift a -2 R placebo above zero
    assert not result["planted"]["detected"] and not result["planted"]["P1"]["holds"]
    assert result["decision"] == "inconclusive_underpowered"


def test_a_null_leg_with_a_detected_planted_effect_fails():
    result = _evaluate(*_world(leg=0.0, leg_gap=0.0))
    assert result["planted"]["detected"] and result["criteria"]["P2"]["mean_diff"] == 0.0
    assert not result["criteria"]["P2"]["holds"] and result["decision"] == "failed"


def test_the_placebo_statistics_are_the_legs_functions_on_placebo_rows():
    labels, controls = _world(placebo=0.3, placebo_gap=0.1)
    placebo = _evaluate(labels, controls)["placebo"]
    rows = labels[labels["role"] == "placebo"]
    assert placebo["P1"]["n"] == len(rows)
    assert placebo["P1"]["mean_r_cost"] == pytest.approx(rows["net_excess_r"].mean(), rel=1e-12)
    assert placebo["P2"]["mean_diff"] == pytest.approx(0.1, rel=1e-9) and placebo["P2"]["holds"]


def test_the_planted_shift_is_the_planted_return_in_r_per_row_on_paired_placebos_only(monkeypatch):
    s1, s2, s3 = OLD[:3]
    rows = [
        _row("A:1", "placebo", s1, 0.1, net=0.2),
        _row("B:1", "placebo", s2, -0.3, net=0.0, decision_price=80.0, entry=80.5, stop=76.5),  # risk 4
        _row("C:1", "placebo", s3, 0.4, net=math.nan, decision_price=20.0, entry=20.2, stop=19.0),  # risk 1.2
        _row("D:1", "placebo", s3, 5.0, net=5.0),  # unpaired: never shifted, never tested
        _row("A:1", "leg", s1, 9.0),  # a leg sharing the pair id is never shifted
    ]
    controls = [_control(p, "placebo", s, v) for p, s, v in (("A:1", s1, 0.0), ("B:1", s2, -0.1), ("C:1", s3, 0.2))]
    controls.append(_control("A:1", "leg", s1, -7.0))
    labels, controls = _frames(rows, controls)
    seen = {}
    for name in ("mean_test", "paired_test"):
        real = getattr(runup_study, name)

        def spy(*args, _real=real, _name=name):
            seen[_name] = [frame.copy() for frame in args[:-1]]
            return _real(*args)

        monkeypatch.setattr(runup_study, name, spy)

    planted = planted_check(labels, controls, FAST)

    shifts = [0.0063 * 100.0 / 2.0, 0.0063 * 80.0 / 4.0, 0.0063 * 20.0 / 1.2]
    p1_frame = seen["mean_test"][0]
    assert list(p1_frame["session"]) == [s1, s2]  # C has no finite net excess
    assert p1_frame["r_cost"].tolist() == pytest.approx([0.2 + shifts[0], 0.0 + shifts[1]], rel=1e-12)
    leg_frame, control_frame = seen["paired_test"]
    assert list(leg_frame["session"]) == [s1, s2, s3]
    assert leg_frame["r_cost"].tolist() == pytest.approx([0.1 + shifts[0], -0.3 + shifts[1], 0.4 + shifts[2]])
    assert control_frame["r_cost"].tolist() == [0.0, -0.1, 0.2]  # controls unchanged
    assert planted["n_paired"] == 3
    assert planted["shift_r"]["mean"] == pytest.approx(np.mean(shifts), rel=1e-12)
    assert planted["detected"] is (planted["P1"]["holds"] and planted["P2"]["holds"])


def test_the_diagnostics_describe_announcers_splits_exits_costs_dispersion_and_the_past_reaction():
    s1, s2, s3 = date(2020, 3, 2), date(2021, 3, 1), date(2022, 3, 1)
    specs = [  # pair, session, timing, bucket, net, r_cost, hit, holding, past_move, last_day_excess
        ("L1:x", s1, "pre_market", 0, 1.0, 1.5, "stop", 4, 0.01, 0.2),
        ("L2:x", s1, "after_hours", 1, 3.0, 3.5, "timeout", LEAD, 0.03, 0.4),
        ("L3:x", s2, "unspecified", 2, 5.0, 5.5, "timeout", LEAD, 0.02, math.nan),
        ("L4:x", s2, "unspecified", 2, 7.0, 7.5, "timeout", LEAD, math.nan, math.nan),
        ("L5:x", s3, "pre_market", 0, 2.0, 2.5, "timeout", LEAD, 0.05, math.nan),
        ("L6:x", s3, "pre_market", 0, math.nan, 9.0, "timeout", LEAD, 0.04, math.nan),
    ]
    rows = [
        _row(
            pair,
            "leg",
            session,
            r_cost,
            net=net,
            timing=timing,
            bucket=bucket,
            hit=hit,
            holding_sessions=holding,
            past_move=past,
            last_day_excess=last,
            r_cost_5bps=r_cost + 0.1,
            r_cost_25bps=r_cost - 0.1,
        )
        for pair, session, timing, bucket, net, r_cost, hit, holding, past, last in specs
    ]
    rows += [
        _row("A1:x", "announcer", s1, -0.5, net=-1.0, tercile=0),
        _row("A2:x", "announcer", s2, 0.5, net=0.0, tercile=1),
        _row("A3:x", "announcer", s3, -2.5, net=-3.0, tercile=0),
        _row("L1:x", "placebo", s1, 40.0),
    ]
    diagnostics = _evaluate(*_frames(rows))["diagnostics"]

    assert diagnostics["announcers"]["all"] == {
        "net_excess_r": {"n": 8, "mean": pytest.approx(1.75)},
        "r_cost": {"n": 9, "mean": pytest.approx(3.0)},
    }
    assert diagnostics["announcers"]["bottom_tercile"]["net_excess_r"] == {"n": 2, "mean": pytest.approx(-2.0)}
    assert diagnostics["announcers"]["bottom_tercile"]["r_cost"] == {"n": 2, "mean": pytest.approx(-1.5)}
    by_year = diagnostics["by_year"]
    assert set(by_year) == {2020, 2021, 2022}
    assert by_year[2022] == {"net_excess_r": {"n": 1, "mean": 2.0}, "r_cost": {"n": 2, "mean": pytest.approx(5.75)}}
    assert {k: v["net_excess_r"]["mean"] for k, v in diagnostics["by_timing"].items()} == {
        "pre_market": pytest.approx(1.5),
        "after_hours": pytest.approx(3.0),
        "unspecified": pytest.approx(6.0),
    }
    assert {k: v["net_excess_r"]["n"] for k, v in diagnostics["by_bucket"].items()} == {0: 2, 1: 1, 2: 2}
    assert diagnostics["excluding_2020"]["net_excess_r"] == {"n": 3, "mean": pytest.approx(14 / 3)}
    assert diagnostics["excluding_2020"]["r_cost"] == {"n": 4, "mean": pytest.approx(6.125)}
    exits = diagnostics["exits"]
    assert exits["hits"] == {"stop": 1, "timeout": 5} and exits["mean_holding_sessions"] == pytest.approx(9.0)
    assert exits["by_hit"]["stop"]["net_excess_r"] == {"n": 1, "mean": 1.0}
    costs = diagnostics["costs"]
    assert set(costs) == {"r_cost_5bps", "r_cost_15bps", "r_cost_25bps"}
    assert costs["r_cost_5bps"]["mean"] == pytest.approx(29.5 / 6 + 0.1)
    assert costs["r_cost_25bps"]["mean"] == pytest.approx(29.5 / 6 - 0.1)
    assert diagnostics["last_day_excess"] == {"n": 2, "mean": pytest.approx(0.3)}

    dispersion = diagnostics["dispersion"]
    values = np.array([1.0, 3.0, 5.0, 7.0, 2.0])
    session_means = np.array([2.0, 6.0, 2.0])
    assert dispersion["n"] == 5 and dispersion["n_sessions"] == 3
    assert dispersion["legs_per_session"] == pytest.approx(5 / 3)
    assert dispersion["sd_per_leg"] == pytest.approx(values.std(ddof=1))
    assert dispersion["var_session_means"] == pytest.approx(session_means.var(ddof=1))
    expected = session_means.var(ddof=1) * (5 / 3) / values.var(ddof=1)
    assert dispersion["design_effect"] == pytest.approx(expected) and expected == pytest.approx(1.5326, abs=1e-4)

    past = diagnostics["past_move_terciles"]
    assert past["n_missing"] == 1  # L4
    assert past["terciles"]["low"]["net_excess_r"] == {"n": 2, "mean": pytest.approx(3.0)}  # L1, L3
    assert past["terciles"]["mid"]["net_excess_r"] == {"n": 1, "mean": pytest.approx(3.0)}  # L2 (L6 has no net)
    assert past["terciles"]["mid"]["r_cost"] == {"n": 2, "mean": pytest.approx(6.25)}
    assert past["terciles"]["high"]["net_excess_r"] == {"n": 1, "mean": pytest.approx(2.0)}  # L5


def test_the_design_effect_is_nan_without_two_sessions():
    rows = [_row("L1:x", "leg", OLD[0], 1.0), _row("L2:x", "leg", OLD[0], 2.0)]
    dispersion = _evaluate(*_frames(rows))["diagnostics"]["dispersion"]
    assert dispersion["n_sessions"] == 1 and np.isnan(dispersion["design_effect"])


def test_empty_frames_evaluate_to_inconclusive():
    result = _evaluate(*_frames([]))
    assert result["decision"] == "inconclusive_underpowered"
    assert not any(criterion["holds"] for criterion in result["criteria"].values())


# --- the executor -----------------------------------------------------------------------------------


def _loaded(entry=FAST):
    return LoadedRunupEntry(entry=entry, sha256="f" * 64, path=SHIPPED)


def _pinned(**provenance):
    return replace(_inputs({}), provenance={"cube_sha256": ENTRY.cube_sha256, **provenance})


def _pipeline(monkeypatch, labels, controls):
    """Replace events, control matching and labelling with ``labels``/``controls``; returns the call order."""
    calls = []
    events = labels[list(RUNUP_EVENT_COLUMNS)]

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
        return labels, controls, {"labelled": {"leg": int((labels["role"] == "leg").sum())}}

    monkeypatch.setattr(runup_study, "build_runup_events", events_of)
    monkeypatch.setattr(runup_study, "match_controls", matched)
    monkeypatch.setattr(runup_study, "label_runup", labelled)
    return calls


def _json_texts(directory):
    return {path.name: path.read_text() for path in sorted(directory.glob("*.json"))}


async def test_the_executor_writes_protocol_and_manifest_before_build_and_records_its_failure(tmp_path):
    directory = tmp_path / "run"
    seen = {}

    async def build():
        seen["written"] = [(directory / name).exists() for name in ("protocol.json", "manifest.json")]
        raise RuntimeError("cube cache missing")

    result = await execute_runup_study(_loaded(), directory, build=build, environment={"e": 1})
    assert seen == {"written": [True, True]}
    assert result == {"status": "failed", "error": "RuntimeError: cube cache missing", "authorizes_promotion": False}
    assert json.loads((directory / "result.json").read_text()) == result
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["entry_id"] == "runup" and manifest["version"] == 1 and manifest["sha256"] == "f" * 64
    assert manifest["pinned"]["pooled_protocol_sha256"] == ENTRY.pooled_protocol_sha256
    assert manifest["pinned"]["cube_sha256"] == ENTRY.cube_sha256
    assert manifest["environment"] == {"e": 1} and manifest["authorizes_promotion"] is False
    assert manifest["started_at"]
    protocol = json.loads((directory / "protocol.json").read_text())
    assert protocol["id"] == "runup" and protocol["sha256"] == "f" * 64
    assert sorted(path.name for path in directory.iterdir()) == ["manifest.json", "protocol.json", "result.json"]


@pytest.mark.parametrize("provenance", [{"cube_sha256": "0" * 64}, {"cube_sha256": None}])
async def test_the_executor_refuses_inputs_whose_cube_differs_from_the_pinned_hash(tmp_path, monkeypatch, provenance):
    calls = _pipeline(monkeypatch, *_world(10, 5))
    directory = tmp_path / "run"

    async def build():
        return replace(_inputs({}), provenance=provenance)

    result = await execute_runup_study(_loaded(), directory, build=build, environment={})
    assert result["status"] == "failed" and result["authorizes_promotion"] is False
    assert result["error"].startswith("ValueError: ") and ENTRY.cube_sha256 in result["error"]
    assert str(provenance["cube_sha256"]) in result["error"]
    assert calls == [] and not (directory / "events.csv.gz").exists()


async def test_a_pipeline_exception_after_build_is_recorded_as_failed(tmp_path, monkeypatch):
    _pipeline(monkeypatch, *_world(10, 5))

    def broken(*args):
        raise KeyError("decision_price")

    monkeypatch.setattr(runup_study, "label_runup", broken)
    directory = tmp_path / "run"

    async def build():
        return _pinned()

    result = await execute_runup_study(_loaded(), directory, build=build, environment={})
    assert result == {"status": "failed", "error": "KeyError: 'decision_price'", "authorizes_promotion": False}
    assert not (directory / "planted.json").exists()


async def test_a_result_that_cannot_be_encoded_is_recorded_as_failed(tmp_path, monkeypatch):
    _pipeline(monkeypatch, *_world(10, 5))
    directory = tmp_path / "run"

    async def build():
        return _pinned(read_on=date(2026, 11, 6))  # a date is not JSON

    result = await execute_runup_study(_loaded(), directory, build=build, environment={})
    assert result["status"] == "failed" and result["error"].startswith("TypeError: ")
    assert json.loads((directory / "result.json").read_text()) == result


async def test_the_executor_saves_frames_and_planted_before_the_criteria_and_result(tmp_path, monkeypatch):
    labels, controls = _world()
    calls = _pipeline(monkeypatch, labels, controls)
    directory = tmp_path / "run"
    written, tested = [], []
    real_save = runup_study.save_json_report

    def save(payload, path):
        written.append(path.name)
        return real_save(payload, path)

    monkeypatch.setattr(runup_study, "save_json_report", save)
    for name in ("mean_test", "paired_test"):
        real = getattr(runup_study, name)

        def spy(*args, _real=real, _name=name):
            tested.append((_name, (directory / "planted.json").exists()))
            return _real(*args)

        monkeypatch.setattr(runup_study, name, spy)

    async def build():
        return _pinned(pages_sha256="p" * 64)

    result = await execute_runup_study(_loaded(), directory, build=build, environment={})

    assert calls == ["events", "controls", "labels"]
    assert written == ["protocol.json", "manifest.json", "planted.json", "result.json"]
    # The planted check (its P1 and P2) runs first; every placebo and leg criterion after planted.json exists.
    assert tested[:2] == [("mean_test", False), ("paired_test", False)]
    assert len(tested) == 6 and all(exists for _, exists in tested[2:])
    assert result["status"] == "completed" and result["decision"] == "survives_development"
    assert result["authorizes_promotion"] is False
    assert result["inputs"] == {"cube_sha256": ENTRY.cube_sha256, "pages_sha256": "p" * 64}
    assert result["events"] == {"legs": 1200} and result["labels"] == {"labelled": {"leg": 1200}}
    saved = json.loads((directory / "result.json").read_text())
    assert saved["planted"] == json.loads((directory / "planted.json").read_text())
    assert saved["planted"]["detected"] is True
    placebo = pd.read_csv(directory / "placebo.csv.gz")
    assert len(placebo) == 1200 and set(placebo["role"]) == {"placebo"}
    assert len(pd.read_csv(directory / "labels.csv.gz")) == 2400
    assert len(pd.read_csv(directory / "controls.csv.gz")) == 2400
    assert len(pd.read_csv(directory / "events.csv.gz")) == 2400
    for name, text in _json_texts(directory).items():
        assert "eligible_for_probe" not in text, name


@pytest.mark.parametrize(
    ("world", "decision"),
    [({"placebo_gap": 0.5}, "failed"), ({"placebo": -2.0}, "inconclusive_underpowered")],
)
async def test_the_executor_never_writes_eligible_for_probe(tmp_path, monkeypatch, world, decision):
    _pipeline(monkeypatch, *_world(**world))
    directory = tmp_path / "run"

    async def build():
        return _pinned()

    result = await execute_runup_study(_loaded(), directory, build=build, environment={})
    assert result["decision"] == decision
    texts = _json_texts(directory)
    assert set(texts) == {"protocol.json", "manifest.json", "planted.json", "result.json"}
    for name, text in texts.items():
        assert "eligible_for_probe" not in text, name
        assert '"authorizes_promotion": true' not in text, name


async def test_the_real_pipeline_on_inputs_without_reports_completes_inconclusive(tmp_path):
    directory = tmp_path / "run"

    async def build():
        return _pinned()

    result = await execute_runup_study(_loaded(), directory, build=build, environment={})
    assert result["status"] == "completed", result.get("error")
    assert result["decision"] == "inconclusive_underpowered" and result["events"]["legs"] == 0
    for name in ("events", "labels", "controls", "placebo"):
        assert pd.read_csv(directory / f"{name}.csv.gz").empty
    assert json.loads((directory / "result.json").read_text())["decision"] == "inconclusive_underpowered"
