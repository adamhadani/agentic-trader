"""Run-up labels: the time-exit bracket, exclusions, cost columns, net excess, last-day excess and control rows."""

import math
from datetime import UTC, datetime, time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.apriori import runup_study
from agentic_trader.research.apriori.catalog import load_runup_entry
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
    label_runup,
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
