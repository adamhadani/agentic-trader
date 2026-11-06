import math
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agentic_trader.agent.earnings import CalendarRow, EarningsTiming
from agentic_trader.data.corporate_actions import CorporateAction, CorporateActionKind
from agentic_trader.research.apriori.catalog import RunupEntry, load_runup_entry
from agentic_trader.research.apriori.pead_events import decision_at
from agentic_trader.research.apriori.runup_events import (
    CONTROL_CANDIDATE_COLUMNS,
    RUNUP_EVENT_COLUMNS,
    RunupInputs,
    build_runup_events,
    match_controls,
)
from agentic_trader.research.pooled.cube import LabelCube


SHIPPED = Path(__file__).resolve().parents[3] / "config/research/apriori/runup-v1.json"
CLOCK = time(10, 35)
LEAD = 10


def _calendar(n, first):
    days, d = [], first
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return tuple(days)


DAYS = _calendar(260, date(2021, 1, 4))
CUBE_FROM = 62  # the cube's sessions are DAYS[62:]
SESSIONS = DAYS[CUBE_FROM:]
SYMS = tuple(f"S{i:02d}" for i in range(30))
# Alternating log returns mu + 2 sigma (-1)^p: rv rises with the index (tercile i // 10), ret20 = exp(20 mu) - 1.
SIGMA = {s: 0.004 + 0.001 * i for i, s in enumerate(SYMS)}
D, E = 150, 160  # the reference decision and report positions
COVERAGE = 75  # a report here is in the 130 sessions before D and decides outside the window


def _entry(**update):
    base = load_runup_entry(SHIPPED).entry.model_dump(mode="json")
    base["window"] = {
        "decisions": [DAYS[70].isoformat(), DAYS[230].isoformat()],
        "bars_through": "2022-06-01",
        "recent_from": DAYS[150].isoformat(),
    }
    base.update(update)
    return RunupEntry.model_validate(base)


ENTRY = _entry()


def _closes(mu=0.0, sigma=0.01, jumps=()):
    p = np.arange(len(DAYS))
    closes = 100.0 * np.exp(mu * p + sigma * (-1.0) ** p)
    for at, factor in jumps:
        closes[at:] *= factor
    return closes


def _bars(closes, drop=()):
    index = pd.DatetimeIndex([datetime.combine(d, time(5), tzinfo=UTC) for d in DAYS])
    frame = pd.DataFrame(
        {"Open": closes, "High": closes * 1.01, "Low": closes * 0.99, "Close": closes, "Volume": 1e6}, index=index
    )
    return frame.drop(index[list(drop)])


def _report(symbol, day, timing=EarningsTiming.UNSPECIFIED):
    return CalendarRow(symbol, day, timing, None, None, None, None, None)


def _world(
    *,
    reports=(),
    sigma=None,
    mu=None,
    adjusted=None,
    eligible=None,
    dollar_volume=None,
    tiebreak=None,
    actions=(),
    breaks=None,
    spy=None,
    covered=(),
):
    """Every name in ``covered`` also reports on ``DAYS[COVERAGE]`` (a decision before the window)."""
    shape = (len(SESSIONS), len(SYMS))
    elig = np.ones(shape, bool) if eligible is None else eligible
    arrays = {
        "eligible": elig,
        "labelled": elig.copy(),
        "r_gross": np.zeros(shape),
        "r_cost": np.zeros(shape),
        "holding": np.full(shape, 10, np.int16),
        "hit": np.ones(shape, np.int8),
        "tiebreak": (np.tile(1000 - np.arange(len(SYMS)), (shape[0], 1)) if tiebreak is None else tiebreak).astype(
            np.uint64
        ),
        "dollar_volume": (
            np.tile((np.arange(len(SYMS)) + 1) * 1e6, (shape[0], 1)) if dollar_volume is None else dollar_volume
        ),
    }
    cube = LabelCube(
        spec_identity="spec", cohort_sha256="c" * 64, sessions=SESSIONS, symbols=SYMS, arrays=arrays, coverage={}
    )
    sigmas = {**SIGMA, **(sigma or {})}
    mus = {**dict.fromkeys(SYMS, 0.0), **(mu or {})}
    bars = {s: _bars(_closes(mus[s], sigmas[s])) for s in SYMS}
    bars.update(adjusted or {})
    rows = [*reports, *(_report(s, DAYS[COVERAGE]) for s in covered)]
    by_symbol = {
        symbol: tuple(sorted((r for r in rows if r.symbol == symbol), key=lambda r: r.date))
        for symbol in {*SYMS, *(r.symbol for r in rows)}
    }
    return RunupInputs(
        cube=cube,
        trading_days=DAYS,
        adjusted=bars,
        hourly={},
        reports=by_symbol,
        actions=tuple(actions),
        identity_breaks=breaks or {},
        market_daily=_bars(np.full(len(DAYS), 400.0) if spy is None else spy),
        market_hourly=pd.DataFrame(),
        decision_time=CLOCK,
        provenance={"cube_sha256": cube.sha256},
    )


def _row(frame, role, symbol):
    rows = frame[(frame["role"] == role) & (frame["symbol"] == symbol)]
    assert len(rows) == 1, rows
    return rows.iloc[0]


def _split(symbol, on, kind=CorporateActionKind.FORWARD_SPLIT):
    if kind is CorporateActionKind.CASH_DIVIDEND:
        return CorporateAction(id=f"{symbol}-div-{on}", kind=kind, symbol=symbol, effective_on=on, rate=0.5)
    return CorporateAction(id=f"{symbol}-split-{on}", kind=kind, symbol=symbol, effective_on=on, old_rate=1, new_rate=2)


# --- events ---------------------------------------------------------------------------------------


def test_top_tercile_report_is_a_leg_ten_sessions_before_and_others_are_announcers():
    inputs = _world(reports=[_report("S25", DAYS[E], EarningsTiming.PRE_MARKET), _report("S05", DAYS[E])])
    frame, counts = build_runup_events(inputs, ENTRY)

    assert tuple(frame.columns) == RUNUP_EVENT_COLUMNS
    leg = _row(frame, "leg", "S25")
    assert leg["event_id"] == f"S25:{DAYS[E].isoformat()}"
    assert leg["pair_id"] == leg["event_id"]
    assert leg["session"] == DAYS[D]
    assert leg["report_date"] == DAYS[E]
    assert leg["timing"] == "pre_market"
    assert leg["decision_at"] == decision_at(DAYS[D], CLOCK)
    assert leg["rv"] == pytest.approx(2 * SIGMA["S25"] * math.sqrt(60 / 59))
    assert leg["tercile"] == 2
    assert leg["atr"] > 0
    assert leg["ret20"] == pytest.approx(0.0, abs=1e-12)
    assert leg["bucket"] == 2
    assert leg["dv"] == 26e6
    assert leg["year"] == 2021

    announcer = _row(frame, "announcer", "S05")
    assert announcer["tercile"] == 0
    assert announcer["bucket"] == 0
    assert announcer["timing"] == "unspecified"
    assert announcer["pair_id"] == announcer["event_id"]

    placebo = _row(frame, "placebo", "S25")
    assert placebo["event_id"] == f"{leg['event_id']}:placebo"
    assert placebo["pair_id"] == leg["event_id"]
    assert placebo["report_date"] is None
    assert placebo["timing"] is None
    assert placebo["tercile"] == 2

    assert counts["legs"] == 1
    assert counts["announcers"] == 1
    assert counts["placebos"] == 1
    assert counts["reasons"] == {}
    assert counts["placebo_reasons"] == {}


def _saturday_after(day):
    return day + timedelta(days=5 - day.weekday())


@pytest.mark.parametrize(
    ("reason", "world"),
    [
        ("non_session_report_date", {"reports": [_report("S25", _saturday_after(DAYS[E]))]}),
        ("outside_window", {"reports": [_report("S25", DAYS[75])]}),
        ("outside_calendar", {"reports": [_report("S25", DAYS[-1] + timedelta(days=7))]}),
        ("not_in_cube", {"reports": [_report("ZZZ", DAYS[E])]}),
        ("identity_break", {"reports": [_report("S25", DAYS[E])], "breaks": {"S25": DAYS[D - 60]}}),
        (
            "structural_action_nearby",
            {"reports": [_report("S25", DAYS[E])], "actions": [_split("S25", DAYS[E] + timedelta(days=5))]},
        ),
        (
            "structural_action_nearby",
            {"reports": [_report("S25", DAYS[E])], "actions": [_split("S25", DAYS[D] - timedelta(days=5))]},
        ),
        (
            "not_eligible",
            {
                "reports": [_report("S25", DAYS[E])],
                "eligible": np.where(
                    (np.arange(len(SESSIONS))[:, None] == D - CUBE_FROM) & (np.arange(len(SYMS)) == 25),
                    False,
                    True,
                ),
            },
        ),
        # 54 finite returns in the 60 ending D - 1: three interior closes missing (each removes two returns).
        (
            "no_rv",
            {
                "reports": [_report("S25", DAYS[E])],
                "adjusted": {"S25": _bars(_closes(0, SIGMA["S25"]), (95, 100, 105))},
            },
        ),
        (
            "no_atr",
            {"reports": [_report("S25", DAYS[E])], "adjusted": {"S25": _bars(_closes(0, SIGMA["S25"]), (145,))}},
        ),
        (
            "no_return",
            {"reports": [_report("S25", DAYS[E])], "adjusted": {"S25": _bars(_closes(0, SIGMA["S25"]), (D - 21,))}},
        ),
    ],
)
def test_each_exclusion_reason_is_counted_by_reason_and_year(reason, world):
    frame, counts = build_runup_events(_world(**world), ENTRY)
    assert frame.empty
    assert counts["reasons"] == {reason: 1}
    assert set(counts["reasons_by_year"][reason].values()) == {1}
    assert counts["legs"] == counts["announcers"] == counts["placebos"] == 0


@pytest.mark.parametrize(("prior", "covered"), [(None, False), (D - 130, True), (D - 131, False), (D - 41, True)])
def test_archive_covered_is_a_report_in_the_130_sessions_before_the_decision(prior, covered):
    reports = [_report("S25", DAYS[E]), *(() if prior is None else (_report("S25", DAYS[prior]),))]
    frame, counts = build_runup_events(_world(reports=reports), ENTRY)
    assert counts["reasons"].get("recent_previous_report", 0) == 0
    leg = frame[(frame["role"] == "leg") & (frame["report_date"] == DAYS[E])].iloc[0]
    assert frame["archive_covered"].dtype == bool and bool(leg["archive_covered"]) is covered
    placebo = frame[(frame["role"] == "placebo") & (frame["pair_id"] == leg["event_id"])].iloc[0]
    d = DAYS.index(placebo["session"])
    since = DAYS[d - 130] if d >= 130 else date.min  # a short calendar opens the look-back
    expected = any(since <= report.date < DAYS[d] for report in reports)  # the leg's own E counts too
    assert bool(placebo["archive_covered"]) is expected


def test_recent_previous_report_within_forty_sessions_excludes_the_later_report():
    frame, counts = build_runup_events(_world(reports=[_report("S25", DAYS[E]), _report("S25", DAYS[E - 40])]), ENTRY)
    assert counts["reasons"] == {"recent_previous_report": 1}
    assert list(frame.loc[frame["role"] == "leg", "report_date"]) == [DAYS[E - 40]]

    frame, counts = build_runup_events(_world(reports=[_report("S25", DAYS[E]), _report("S25", DAYS[E - 41])]), ENTRY)
    assert counts["reasons"] == {}
    assert sorted(frame.loc[frame["role"] == "leg", "report_date"]) == [DAYS[E - 41], DAYS[E]]


def test_identity_break_before_the_volatility_window_keeps_the_event():
    frame, counts = build_runup_events(_world(reports=[_report("S25", DAYS[E])], breaks={"S25": DAYS[D - 61]}), ENTRY)
    assert counts["reasons"] == {}
    assert _row(frame, "leg", "S25")["session"] == DAYS[D]


def test_cash_dividend_and_distant_split_are_not_structural_exclusions():
    actions = [
        _split("S25", DAYS[E - 3], CorporateActionKind.CASH_DIVIDEND),
        _split("S25", DAYS[E] + timedelta(days=6)),
        _split("S25", DAYS[D] - timedelta(days=6)),
        _split("S24", DAYS[E - 3]),
    ]
    frame, counts = build_runup_events(_world(reports=[_report("S25", DAYS[E])], actions=actions), ENTRY)
    assert counts["reasons"] == {}
    assert _row(frame, "leg", "S25")["report_date"] == DAYS[E]


def test_fifty_five_finite_returns_are_enough_for_rv():
    # The close at D - 61 removes one window return; two interior closes remove four more: 55 remain.
    adjusted = {"S25": _bars(_closes(0, SIGMA["S25"]), (D - 61, 95, 100))}
    frame, counts = build_runup_events(_world(reports=[_report("S25", DAYS[E])], adjusted=adjusted), ENTRY)
    assert counts["reasons"] == {}
    assert np.isfinite(_row(frame, "leg", "S25")["rv"])


def test_tercile_is_floor_3r_over_n_among_eligible_names_with_rv():
    # S00 has no bars (no rv) and S01 is not eligible at D: n = 28, so rank 18 (S20) is tercile 1
    # (floor(54 / 28) = 1) and rank 19 (S21) tercile 2 (floor(57 / 28) = 2).
    eligible = np.ones((len(SESSIONS), len(SYMS)), bool)
    eligible[D - CUBE_FROM, 1] = False
    adjusted = {"S00": _bars(_closes(), range(len(DAYS)))}
    inputs = _world(reports=[_report("S20", DAYS[E]), _report("S21", DAYS[E])], adjusted=adjusted, eligible=eligible)
    frame, _ = build_runup_events(inputs, ENTRY)
    assert _row(frame, "announcer", "S20")["tercile"] == 1
    assert _row(frame, "leg", "S21")["tercile"] == 2


def test_rv_tie_is_resolved_by_tiebreak():
    # S19 and S20 have identical bars, so identical rv: the smaller tiebreak ranks lower (19 of 30 -> tercile 1).
    sigma = {"S20": SIGMA["S19"]}
    tiebreak = np.tile(1000 - np.arange(len(SYMS)), (len(SESSIONS), 1))  # S20 (980) below S19 (981)
    frame, _ = build_runup_events(_world(reports=[_report("S20", DAYS[E])], sigma=sigma, tiebreak=tiebreak), ENTRY)
    assert _row(frame, "announcer", "S20")["tercile"] == 1

    tiebreak[:, [19, 20]] = tiebreak[:, [20, 19]]
    frame, _ = build_runup_events(_world(reports=[_report("S20", DAYS[E])], sigma=sigma, tiebreak=tiebreak), ENTRY)
    assert _row(frame, "leg", "S20")["tercile"] == 2


def test_dollar_volume_bucket_ranks_eligible_names_with_tiebreak():
    dv = np.tile((np.arange(len(SYMS)) + 1) * 1e6, (len(SESSIONS), 1))
    dv[:, 25] = 10e6  # ties S09 (10e6): S25's tiebreak (975) is below S09's (991), so S25 ranks 9 -> bucket 0
    frame, _ = build_runup_events(_world(reports=[_report("S25", DAYS[E])], dollar_volume=dv), ENTRY)
    leg = _row(frame, "leg", "S25")
    assert leg["bucket"] == 0
    assert leg["dv"] == 10e6


# --- placebo --------------------------------------------------------------------------------------


def _quiet_sessions(entry, reports, d):
    """Independent restatement of the placebo candidate rule for one leg at position ``d``."""
    start, end = entry.window.decisions
    span = entry.placebo.span_sessions
    out = []
    for q in range(d - span, d + span + 1):
        if not start <= DAYS[q] <= end:
            continue
        if any(DAYS[q - LEAD] <= r.date <= DAYS[q + 2 * LEAD] for r in reports):
            continue
        out.append(q)
    return out


def test_placebo_is_a_seeded_draw_among_quiet_sessions_within_the_span():
    reports = [_report("S25", DAYS[E]), _report("S26", DAYS[E]), _report("S26", DAYS[E - 55])]
    frame, counts = build_runup_events(_world(reports=reports), ENTRY)
    again, _ = build_runup_events(_world(reports=reports), ENTRY)
    pd.testing.assert_frame_equal(frame, again)

    # Legs draw in event_id order: S25's report, then S26's earlier and later reports.
    legs = sorted(frame.loc[frame["role"] == "leg", "event_id"])
    assert legs == [f"S25:{DAYS[E].isoformat()}", f"S26:{DAYS[E - 55].isoformat()}", f"S26:{DAYS[E].isoformat()}"]
    rng = np.random.default_rng(ENTRY.placebo.seed)
    for event_id in legs:
        leg = frame[frame["event_id"] == event_id].iloc[0]
        d = DAYS.index(leg["session"])
        quiet = _quiet_sessions(ENTRY, [r for r in reports if r.symbol == leg["symbol"]], d)
        expected = quiet[int(rng.integers(len(quiet)))]
        placebo = frame[(frame["role"] == "placebo") & (frame["pair_id"] == event_id)].iloc[0]
        assert placebo["session"] == DAYS[expected]
        assert abs(expected - d) <= ENTRY.placebo.span_sessions
        assert not any(
            DAYS[expected - 10] <= r.date <= DAYS[expected + 20] for r in reports if r.symbol == leg["symbol"]
        )
        assert placebo["decision_at"] == decision_at(DAYS[expected], CLOCK)
        assert placebo["year"] == DAYS[expected].year
    assert counts["placebos"] == 3


def test_placebo_seed_changes_the_draw():
    reports = [_report("S25", DAYS[E])]
    sessions = {
        build_runup_events(_world(reports=reports), _entry(placebo={**ENTRY.placebo.model_dump(), "seed": seed}))[0]
        .query("role == 'placebo'")["session"]
        .iloc[0]
        for seed in range(6)
    }
    assert len(sessions) > 1


def test_placebo_that_fails_a_gate_is_counted_not_kept():
    eligible = np.ones((len(SESSIONS), len(SYMS)), bool)
    eligible[:, 25] = False
    eligible[D - CUBE_FROM, 25] = True  # S25 is eligible only on the leg's own session
    frame, counts = build_runup_events(_world(reports=[_report("S25", DAYS[E])], eligible=eligible), ENTRY)
    assert list(frame["role"]) == ["leg"]
    assert counts["placebo_reasons"] == {"not_eligible": 1}
    assert counts["placebo_reasons_by_year"] == {"not_eligible": {2021: 1}}
    assert counts["placebos"] == 0


def test_placebo_outside_the_top_tercile_is_counted_not_kept():
    # S25 is a middle-volatility name; on the leg's session the 13 more volatile names are not eligible,
    # so S25 ranks 16 of 17 there (tercile 2) but 16 of 30 at any placebo session (tercile 1).
    eligible = np.ones((len(SESSIONS), len(SYMS)), bool)
    eligible[D - CUBE_FROM, [c for c in range(16, 30) if c != 25]] = False
    inputs = _world(reports=[_report("S25", DAYS[E])], sigma={"S25": 0.0195}, eligible=eligible)
    frame, counts = build_runup_events(inputs, ENTRY)
    assert _row(frame, "leg", "S25")["tercile"] == 2
    assert list(frame["role"]) == ["leg"]
    assert counts["placebo_reasons"] == {"not_top_tercile": 1}


def test_a_second_draw_of_a_drawn_placebo_session_is_dropped_and_counted():
    # Two S25 legs 32 sessions apart (D and D + 32) in a window [D, D + 32]: every session from D - 10 to
    # D + 52 but D + 21 has an S25 report in [q - 10, q + 20], so both legs can only draw D + 21.
    window = {"decisions": [DAYS[D].isoformat(), DAYS[D + 32].isoformat()], "bars_through": "2022-06-01"}
    event = {**ENTRY.event.model_dump(), "recent_previous_report_sessions": 30}
    entry = _entry(window={**window, "recent_from": DAYS[D + 1].isoformat()}, event=event)
    reports = [_report("S25", DAYS[E]), _report("S25", DAYS[E + 32])]
    frame, counts = build_runup_events(_world(reports=reports), entry)

    assert counts["legs"] == 2 and counts["placebos"] == 1
    placebo = _row(frame, "placebo", "S25")
    assert placebo["session"] == DAYS[D + 21]
    assert placebo["pair_id"] == f"S25:{DAYS[E].isoformat()}"  # the first leg in event_id order keeps it
    assert counts["placebo_reasons"] == {"duplicate_session": 1}
    assert counts["placebo_reasons_by_year"] == {"duplicate_session": {2021: 1}}


def test_leg_without_a_quiet_session_counts_placebo_no_session():
    window = {"decisions": [DAYS[D - 5].isoformat(), DAYS[D + 5].isoformat()], "bars_through": "2022-06-01"}
    entry = _entry(window={**window, "recent_from": DAYS[D].isoformat()})
    frame, counts = build_runup_events(_world(reports=[_report("S25", DAYS[E])]), entry)
    assert list(frame["role"]) == ["leg"]
    assert counts["placebo_reasons"] == {"placebo_no_session": 1}


# --- past_move ------------------------------------------------------------------------------------


def test_past_move_is_the_mean_absolute_two_session_excess_reaction_of_the_last_four_reports():
    # Each prior report's price jump lands in its reaction window only under the right timing rule:
    # pre-market reacts on E (close E-1 -> E+1), otherwise on E+1 (close E -> E+2).
    jumps = ((11, 1.50), (50, 1.02), (92, 1.04), (132, 0.95), (170, 1.10))
    reports = [
        _report("S25", DAYS[10], EarningsTiming.PRE_MARKET),  # the fifth-latest: ignored
        _report("S25", DAYS[50], EarningsTiming.PRE_MARKET),
        _report("S25", DAYS[90], EarningsTiming.UNSPECIFIED),
        _report("S25", DAYS[130], EarningsTiming.AFTER_HOURS),
        _report("S25", DAYS[170], EarningsTiming.PRE_MARKET),
        _report("S25", DAYS[220]),
    ]
    spy = np.full(len(DAYS), 400.0)
    spy[170:] *= 1.03  # SPY moves with the 170 report: its excess reaction is 0.10 - 0.03
    inputs = _world(reports=reports, adjusted={"S25": _bars(_closes(0, SIGMA["S25"], jumps))}, spy=spy)
    frame, _ = build_runup_events(inputs, ENTRY)
    event = frame[frame["event_id"] == f"S25:{DAYS[220].isoformat()}"].iloc[0]
    assert event["past_move"] == pytest.approx((0.07 + 0.05 + 0.04 + 0.02) / 4)


def test_past_move_needs_two_prior_reports():
    reports = [_report("S25", DAYS[100]), _report("S25", DAYS[E])]
    frame, _ = build_runup_events(_world(reports=reports), ENTRY)
    first = frame[frame["event_id"] == f"S25:{DAYS[100].isoformat()}"].iloc[0]
    second = frame[frame["event_id"] == f"S25:{DAYS[E].isoformat()}"].iloc[0]
    assert math.isnan(first["past_move"])
    assert math.isnan(second["past_move"])

    reports = [_report("S25", DAYS[60]), _report("S25", DAYS[105]), _report("S25", DAYS[E])]
    frame, _ = build_runup_events(_world(reports=reports), ENTRY)
    third = frame[frame["event_id"] == f"S25:{DAYS[E].isoformat()}"].iloc[0]
    assert third["past_move"] == pytest.approx(0.0, abs=1e-12)


# --- matched controls -----------------------------------------------------------------------------


def _events(*rows):
    """Hand-built event rows ``(role, symbol, position)``; a placebo row is paired with ``pair_id``."""
    out = []
    for role, symbol, p, *pair in rows:
        session = DAYS[p]
        own = f"{symbol}:{DAYS[p + LEAD].isoformat()}"
        record = dict.fromkeys(RUNUP_EVENT_COLUMNS)
        record |= {
            "event_id": f"{pair[0]}:placebo" if pair else own,
            "role": role,
            "pair_id": pair[0] if pair else own,
            "symbol": symbol,
            "session": session,
            "year": session.year,
        }
        out.append(record)
    return pd.DataFrame(out, columns=list(RUNUP_EVENT_COLUMNS))


# S19 and S24 swap volatility: S19 is top-tercile rv in dollar-volume bucket 1, S24 bucket 2 with tercile-1 rv.
SWAPPED = {"S19": SIGMA["S24"], "S24": SIGMA["S19"]}
# Every barred name ties the leg (S20) at distance 0 (mu 0 makes every ret20 exactly 0); the admissible
# S25-S29 are progressively farther.
NEAR = {
    **dict.fromkeys(("S19", "S20", "S21", "S22", "S23", "S24"), 0.0),
    **{f"S{25 + i}": 0.002 + 0.001 * i for i in range(5)},
}


def test_controls_exclude_reporters_other_legs_other_terciles_and_uncovered_names():
    reports = [_report("S21", DAYS[D + LEAD])]  # reports inside the leg's hold [D, D + 10]
    covered = [s for s in SYMS if s != "S23"]  # S23 has no report in the 130 sessions before D
    inputs = _world(reports=reports, sigma=SWAPPED, mu=NEAR, covered=covered)
    events = _events(("leg", "S20", D), ("leg", "S22", D))
    frame, counts = match_controls(events, inputs, ENTRY)

    assert tuple(frame.columns) == CONTROL_CANDIDATE_COLUMNS
    row = frame[frame["pair_id"] == events.iloc[0]["pair_id"]].iloc[0]
    assert row["role"] == "leg"
    assert row["session"] == DAYS[D]
    assert row["control_symbols"] == "S25,S26,S27,S28,S29"
    assert counts["paired"]["leg"] == 2
    assert counts["unpaired_by_year"] == {"leg": {}, "placebo": {}}


def test_report_just_after_the_hold_and_coverage_at_130_sessions_admit_a_control():
    reports = [_report("S21", DAYS[D + LEAD + 1]), _report("S23", DAYS[D - 130])]
    covered = [s for s in SYMS if s != "S23"]
    inputs = _world(reports=reports, sigma=SWAPPED, mu=NEAR, covered=covered)
    frame, _ = match_controls(_events(("leg", "S20", D), ("leg", "S22", D)), inputs, ENTRY)
    # S21 and S23 are now admissible and nearest; tiebreak (979 vs 977) puts S23 first.
    assert frame.iloc[0]["control_symbols"] == "S23,S21,S25,S26,S27"


@pytest.mark.parametrize("role", ["leg", "placebo"])
@pytest.mark.parametrize(
    ("offset", "admitted"), [(-31, True), (-30, False), (-1, False), (LEAD, False), (LEAD + 1, True)]
)
def test_controls_have_no_report_from_thirty_sessions_before_to_the_end_of_the_hold(role, offset, admitted):
    assert ENTRY.control.quiet_before_sessions == 30
    reports = [_report("S21", DAYS[D + offset])]
    covered = [s for s in SYMS if s != "S23"]
    inputs = _world(reports=reports, sigma=SWAPPED, mu=NEAR, covered=covered)
    pair = (f"S20:{DAYS[E].isoformat()}",) if role == "placebo" else ()
    other = ("S22:x",) if role == "placebo" else ()
    frame, _ = match_controls(_events((role, "S20", D, *pair), (role, "S22", D, *other)), inputs, ENTRY)
    row = frame[frame["role"] == role].iloc[0]
    expected = "S21,S25,S26,S27,S28" if admitted else "S25,S26,S27,S28,S29"
    assert row["control_symbols"] == expected


def test_report_131_sessions_back_does_not_cover():
    reports = [_report("S23", DAYS[D - 131])]
    covered = [s for s in SYMS if s != "S23"]
    inputs = _world(reports=reports, sigma=SWAPPED, mu=NEAR, covered=covered)
    frame, _ = match_controls(_events(("leg", "S20", D)), inputs, ENTRY)
    assert "S23" not in frame.iloc[0]["control_symbols"].split(",")


def test_fewer_than_five_candidates_leaves_the_leg_unpaired():
    eligible = np.ones((len(SESSIONS), len(SYMS)), bool)
    eligible[D - CUBE_FROM, 25] = False
    reports = [_report("S21", DAYS[D + LEAD])]
    covered = [s for s in SYMS if s != "S23"]
    inputs = _world(reports=reports, sigma=SWAPPED, mu=NEAR, covered=covered, eligible=eligible)
    frame, counts = match_controls(_events(("leg", "S20", D), ("leg", "S22", D)), inputs, ENTRY)
    assert frame.empty
    assert counts["unpaired_by_year"]["leg"] == {2021: 2}
    assert counts["paired"]["leg"] == 0


def test_nearest_twenty_session_return_with_tiebreak_breaking_ties():
    # S25 and S26 have identical bars (so identical ret20); one of them fills the fifth slot.
    mu = {"S20": 0.0, "S21": 0.001, "S22": 0.0011, "S23": 0.0012, "S27": 0.0013, "S25": 0.002, "S26": 0.002}
    mu |= {"S24": 0.005, "S28": 0.003, "S29": 0.004}
    sigma = {"S26": SIGMA["S25"]}
    inputs = _world(sigma=sigma, mu=mu, covered=SYMS)
    frame, _ = match_controls(_events(("leg", "S20", D)), inputs, ENTRY)
    assert frame.iloc[0]["control_symbols"] == "S21,S22,S23,S27,S26"  # S26's tiebreak 974 < S25's 975

    tiebreak = np.tile(1000 - np.arange(len(SYMS)), (len(SESSIONS), 1))
    tiebreak[:, [25, 26]] = tiebreak[:, [26, 25]]
    inputs = _world(sigma=sigma, mu=mu, covered=SYMS, tiebreak=tiebreak)
    frame, _ = match_controls(_events(("leg", "S20", D)), inputs, ENTRY)
    assert frame.iloc[0]["control_symbols"] == "S21,S22,S23,S27,S25"


def test_placebo_controls_bar_other_placebos_on_their_session():
    mu = {"S20": 0.0, "S21": 0.0, **{f"S{25 + i}": 0.001 * (i + 1) for i in range(5)}}
    inputs = _world(mu=mu, covered=SYMS)
    leg_id = f"S20:{DAYS[E].isoformat()}"
    events = _events(("placebo", "S20", 120, leg_id), ("placebo", "S21", 120, "S21:x"))
    frame, counts = match_controls(events, inputs, ENTRY)
    row = frame[frame["pair_id"] == leg_id].iloc[0]
    assert row["role"] == "placebo"
    assert row["session"] == DAYS[120]
    assert set(row["control_symbols"].split(",")).isdisjoint({"S20", "S21"})
    assert counts["paired"] == {"leg": 0, "placebo": 2}


def test_announcers_get_no_controls():
    frame, counts = match_controls(_events(("announcer", "S05", D)), _world(covered=SYMS), ENTRY)
    assert frame.empty
    assert counts["paired"] == {"leg": 0, "placebo": 0}
