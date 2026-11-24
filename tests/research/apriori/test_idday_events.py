import dataclasses
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agentic_trader.agent.earnings import CalendarRow, EarningsTiming
from agentic_trader.data.corporate_actions import CorporateAction, CorporateActionKind
from agentic_trader.research.apriori.catalog import IddayEntry, load_idday_entry
from agentic_trader.research.apriori.idday_events import (
    CANDIDATE_COLUMNS,
    CONTROL_CANDIDATE_COLUMNS,
    IDDAY_EVENT_COLUMNS,
    IddayInputs,
    build_idday_events,
    candidate_legs,
    cohort_bands,
    idday_candidates,
    issuer_reports,
    match_controls,
    sample_parser_check,
    validate_parser_check,
)
from agentic_trader.research.apriori.idday_parser import PARSER_VERSION, EventTable, IddayEvent
from agentic_trader.research.apriori.pead_events import decision_at
from agentic_trader.research.investor_days import IssuerRecord, SearchHit
from agentic_trader.research.pooled.cohort import load_cohort
from agentic_trader.research.pooled.cube import LabelCube


REPO = Path(__file__).resolve().parents[3]
ENTRY = load_idday_entry(Path(__file__).parent / "fixtures" / "idday-v1-test.json").entry
CLOCK = time(10, 35)
HOLIDAYS = {date(2024, 1, 1), date(2024, 3, 29), date(2024, 7, 4), date(2024, 12, 25), date(2025, 1, 1)}


def _calendar(first, last):
    days, d = [], first
    while d <= last:
        if d.weekday() < 5 and d not in HOLIDAYS:
            days.append(d)
        d += timedelta(days=1)
    return tuple(days)


DAYS = _calendar(date(2023, 1, 2), date(2025, 12, 31))
POS = {d: i for i, d in enumerate(DAYS)}
# GOOG/GOOGL are one issuer (CIK 1); every other name is its own issuer. Dollar volume rises with the index,
# so the buckets are indices 0-14, 15-29 and 30-44.
SYMS = ("GOOG", "GOOGL", *(f"S{i:02d}" for i in range(2, 45)))
CIK = {s: 1 if s in ("GOOG", "GOOGL") else 100 + i for i, s in enumerate(SYMS)}


def _entry(**update):
    base = ENTRY.model_dump(mode="json")
    base.update(update)
    return IddayEntry.model_validate(base)


def _records(**changes):
    """Default issuer records; ``changes`` maps a CIK (as ``c<cik>``) to replaced fields or ``None`` (unmapped)."""
    groups: dict[int, list[str]] = {}
    for s in SYMS:
        groups.setdefault(CIK[s], []).append(s)
    records = {cik: IssuerRecord(cik, tuple(syms), True, False, ()) for cik, syms in groups.items()}
    for key, change in changes.items():
        cik = int(key[1:])
        if change is None:
            del records[cik]
        else:
            records[cik] = dataclasses.replace(records[cik], **change)
    return records


def _closes(mu=0.0, sigma=0.01):
    p = np.arange(len(DAYS))
    return 100.0 * np.exp(mu * p + sigma * (-1.0) ** p)


def _bars(closes, drop=()):
    index = pd.DatetimeIndex([datetime.combine(d, time(5), tzinfo=UTC) for d in DAYS])
    frame = pd.DataFrame(
        {"Open": closes, "High": closes * 1.01, "Low": closes * 0.99, "Close": closes, "Volume": 1e6}, index=index
    )
    return frame.drop(index[list(drop)])


def _report(symbol, day):
    return CalendarRow(symbol, day, EarningsTiming.UNSPECIFIED, None, None, None, None, None)


_ACCESSION = iter(range(10**6))


def _event(cik, filed, event_date, *, status="announced", symbols=None):
    announced = status == "announced"
    return IddayEvent(
        event_id=f"{cik}:{filed if announced else 'none'}:{event_date}",
        cik=cik,
        symbols=symbols or tuple(s for s in SYMS if CIK[s] == cik),
        status=status,
        filed=filed if announced else None,
        event_date=event_date,
        n_announcing=1 if announced else 0,
        event_day_material=False,
        keyword="investor day",
        sentence="Example Corp will host its investor day.",
        accession=f"0000000000-24-{next(_ACCESSION):06d}",
        url="https://example.invalid/doc.htm",
        item_2_02=False,
    )


def _table(*events):
    return EventTable(PARSER_VERSION, "0" * 64, tuple(events))


def _world(
    *,
    events=(),
    reports=(),
    records=None,
    mu=None,
    eligible=None,
    dollar_volume=None,
    adjusted=None,
    actions=(),
    breaks=None,
    cube_from=0,
):
    """The cube's sessions are ``DAYS[cube_from:]``; ``eligible`` and ``dollar_volume`` are cube-shaped."""
    sessions = DAYS[cube_from:]
    shape = (len(sessions), len(SYMS))
    elig = np.ones(shape, bool) if eligible is None else eligible
    arrays = {
        "eligible": elig,
        "labelled": elig.copy(),
        "r_gross": np.zeros(shape),
        "r_cost": np.zeros(shape),
        "holding": np.full(shape, 10, np.int16),
        "hit": np.ones(shape, np.int8),
        "tiebreak": np.tile(1000 - np.arange(len(SYMS)), (shape[0], 1)).astype(np.uint64),
        "dollar_volume": (
            np.tile((np.arange(len(SYMS)) + 1) * 1e6, (shape[0], 1)) if dollar_volume is None else dollar_volume
        ),
    }
    cube = LabelCube(
        spec_identity="spec", cohort_sha256="c" * 64, sessions=sessions, symbols=SYMS, arrays=arrays, coverage={}
    )
    mus = {**dict.fromkeys(SYMS, 0.0), **(mu or {})}
    bars = {s: _bars(_closes(mus[s])) for s in SYMS}
    bars.update(adjusted or {})
    by_symbol = {s: tuple(sorted((r for r in reports if r.symbol == s), key=lambda r: r.date)) for s in SYMS}
    recs = _records() if records is None else records
    return IddayInputs(
        cube=cube,
        trading_days=DAYS,
        adjusted=bars,
        hourly={},
        reports=by_symbol,
        actions=tuple(actions),
        identity_breaks=breaks or {},
        market_daily=_bars(np.full(len(DAYS), 400.0)),
        market_hourly=pd.DataFrame(),
        decision_time=CLOCK,
        provenance={},
        events=_table(*events),
        issuers=recs,
        issuer_of={s: cik for cik, rec in recs.items() for s in rec.symbols},
        band_of=dict.fromkeys(SYMS, "v3"),
    )


def _only(frame, **where):
    rows = frame
    for key, value in where.items():
        rows = rows[rows[key] == value]
    assert len(rows) == 1, rows
    return rows.iloc[0]


# --- candidate legs (steps 1-4, price-free) --------------------------------------------------------


def test_timing_D_X_H():
    p = POS[date(2024, 8, 7)]  # D of an F on Monday 2024-08-05
    events = [
        _event(102, date(2024, 3, 4), date(2024, 3, 21)),
        _event(103, date(2024, 3, 29), date(2024, 4, 19)),  # F on Good Friday (no session)
        _event(104, date(2024, 6, 10), date(2024, 7, 4)),  # E on Independence Day (no session)
        _event(105, date(2024, 5, 6), date(2024, 5, 8)),  # H 0
        _event(106, date(2024, 8, 5), DAYS[p + 64]),  # H 64
        _event(107, date(2024, 8, 5), DAYS[p + 63]),  # H 63
        _event(108, date(2024, 9, 9), date(2024, 9, 12)),  # H 1
        _event(109, None, date(2024, 10, 1), status="event_only"),
    ]
    frame, counts = candidate_legs(_table(*events), DAYS, {}, ENTRY)

    assert tuple(frame.columns) == CANDIDATE_COLUMNS
    first = _only(frame, cik=102)
    assert (first["F"], first["E"]) == (date(2024, 3, 4), date(2024, 3, 21))
    assert (first["D"], first["X"], first["H"]) == (date(2024, 3, 6), date(2024, 3, 20), 11)
    assert first["klass"] == "standalone"
    assert first["event_id"] == events[0].event_id
    assert _only(frame, cik=103)["D"] == date(2024, 4, 2)  # the second session after Good Friday
    assert _only(frame, cik=104)["X"] == date(2024, 7, 3)  # the last session before the holiday
    assert _only(frame, cik=107)["H"] == 63
    one = _only(frame, cik=108)
    assert (one["D"], one["X"], one["H"]) == (date(2024, 9, 11), date(2024, 9, 11), 1)
    assert set(frame["cik"]) == {102, 103, 104, 107, 108}
    assert counts["reasons"] == {"lead_too_long": 1, "lead_too_short": 1}
    assert counts["reasons_by_year"] == {"lead_too_long": {2024: 1}, "lead_too_short": {2024: 1}}
    assert counts["event_only"] == 1
    assert counts["candidates"] == 5


def test_window_and_calendar_edges():
    window = {
        "decisions": ["2023-06-01", "2024-06-28"],
        "bars_through": "2024-09-30",
        "recent_from": "2024-01-02",
        "last_exit": "2024-07-31",
    }
    entry = _entry(window=window)
    events = [
        _event(102, date(2024, 6, 26), date(2024, 7, 12)),  # D 2024-06-28: the last decision
        _event(103, date(2024, 6, 27), date(2024, 7, 12)),  # D 2024-07-01: after the window
        _event(104, date(2024, 6, 3), date(2024, 8, 2)),  # X 2024-08-01: after last_exit
        _event(105, date(2024, 6, 3), date(2024, 8, 1)),  # X 2024-07-31: the last exit
        _event(106, date(2022, 12, 30), date(2023, 1, 20)),  # F before the calendar
        _event(107, date(2025, 12, 1), date(2026, 1, 5)),  # E after the calendar
    ]
    frame, counts = candidate_legs(_table(*events), DAYS, {}, entry)
    assert set(frame["cik"]) == {102, 105}
    assert counts["reasons"] == {"outside_window": 4}


def test_anything_needing_a_session_outside_the_calendar_is_outside_window():
    calendar = DAYS[100:]
    first = calendar[0]
    events = [
        _event(102, first - timedelta(days=1), calendar[15]),  # F's session is before the calendar
        _event(103, first, calendar[15]),  # the 3-session band ending at F's session starts before it
        _event(104, calendar[2], calendar[15]),  # the band is the calendar's first three sessions
    ]
    frame, counts = candidate_legs(_table(*events), calendar, {}, ENTRY)
    assert set(frame["cik"]) == {104}
    assert counts["reasons"] == {"outside_window": 2}
    # The same events on the longer calendar are placed: the calendar, not the event, excluded them.
    frame, counts = candidate_legs(_table(*events), DAYS, {}, ENTRY)
    assert set(frame["cik"]) == {102, 103, 104}


def test_study_and_parser_check_gate_on_the_cube_sessions():
    cube_from = 300
    first = DAYS[cube_from]
    events = [
        _event(CIK["S10"], first - timedelta(days=1), DAYS[cube_from + 15]),  # placeable on DAYS, not on the cube
        _event(CIK["S11"], DAYS[cube_from + 20], DAYS[cube_from + 35]),
        _event(CIK["S12"], DAYS[cube_from + 30], DAYS[cube_from + 50]),
    ]
    inputs = _world(events=events, cube_from=cube_from)
    # The parser-check path: the event table, the cube's sessions and the issuers' archive rows (as the sample CLI).
    reports = {cik: issuer_reports(inputs.reports, record.symbols) for cik, record in inputs.issuers.items()}
    sampled, sample_counts = candidate_legs(inputs.events, inputs.cube.sessions, reports, ENTRY)
    candidates, counts = idday_candidates(inputs, ENTRY)
    frame, build_counts = build_idday_events(inputs, ENTRY)

    assert list(sampled["event_id"]) == list(candidates["event_id"]) == [events[1].event_id, events[2].event_id]
    assert sample_counts == counts == build_counts["candidates"]
    assert counts["reasons"] == {"outside_window": 1}
    assert sorted(frame.loc[frame["role"] == "leg", "event_id"]) == sorted(candidates["event_id"])
    sample = sample_parser_check(
        sampled, inputs.events, _entry(parser_check={**ENTRY.parser_check.model_dump(), "sample_size": 2})
    )
    assert {row["event_id"] for row in sample["sample"]} == set(candidates["event_id"])


def test_overlap_per_issuer():
    p = POS[date(2024, 2, 5)]
    events = [
        _event(102, DAYS[p], DAYS[p + 12]),
        _event(102, DAYS[p + 40], DAYS[p + 52]),  # D 40 sessions after the first
        _event(103, DAYS[p], DAYS[p + 12]),
        _event(103, DAYS[p + 63], DAYS[p + 75]),  # D 63 sessions after the first
        _event(104, DAYS[p], DAYS[p + 12]),
        _event(104, DAYS[p + 64], DAYS[p + 76]),  # D 64 sessions after the first
        _event(105, DAYS[p + 40], DAYS[p + 52]),  # another issuer is never an overlap
        _event(106, DAYS[p], DAYS[p + 12]),  # earnings_in_hold: does not block ...
        _event(106, DAYS[p + 40], DAYS[p + 52]),  # ... this event 40 sessions later
        _event(107, DAYS[p], DAYS[p + 80]),  # lead_too_long: does not block ...
        _event(107, DAYS[p + 40], DAYS[p + 52]),  # ... this one either
    ]
    reports = {106: (DAYS[p + 5],)}  # inside the first 106 event's hold [F + 1, X], before the second's band
    frame, counts = candidate_legs(_table(*events), DAYS, reports, ENTRY)
    assert counts["reasons"] == {"earnings_in_hold": 1, "lead_too_long": 1, "overlapping_event": 2}
    kept = set(frame["event_id"])
    assert kept == {events[i].event_id for i in (0, 2, 4, 5, 6, 8, 10)}


def test_earnings_class_and_hold():
    f = POS[date(2024, 4, 15)]
    x = f + 14
    events = [_event(cik, DAYS[f], DAYS[f + 15]) for cik in range(102, 108)]
    reports = {
        102: (DAYS[f],),  # on F's session
        103: (DAYS[f - 2],),  # 2 sessions before F's session
        104: (DAYS[f - 3],),  # 3 sessions before F's session
        105: (DAYS[f] + timedelta(days=1),),  # F + 1
        106: (DAYS[x],),  # on X
        107: (DAYS[f + 15],),  # on E, after the hold
    }
    frame, counts = candidate_legs(_table(*events), DAYS, reports, ENTRY)
    assert counts["reasons"] == {"earnings_in_hold": 2}
    assert dict(zip(frame["cik"], frame["klass"], strict=True)) == {
        102: "with_earnings",
        103: "with_earnings",
        104: "standalone",
        107: "standalone",
    }
    assert counts["by_class"] == {"standalone": 2, "with_earnings": 2}


def test_issuer_reports_is_the_deduplicated_sorted_union():
    reports = {
        "GOOG": (_report("GOOG", date(2024, 4, 25)), _report("GOOG", date(2024, 1, 30))),
        "GOOGL": (_report("GOOGL", date(2024, 4, 25)), _report("GOOGL", date(2024, 7, 23))),
        "S05": (_report("S05", date(2024, 2, 1)),),
    }
    assert issuer_reports(reports, ("GOOG", "GOOGL")) == (date(2024, 1, 30), date(2024, 4, 25), date(2024, 7, 23))
    assert issuer_reports(reports, ("ZZZ",)) == ()


# --- legs (steps 3 and 5-7) -------------------------------------------------------------------------


def test_share_class_one_leg_most_liquid():
    f = POS[date(2024, 4, 15)]
    event = _event(1, DAYS[f], DAYS[f + 15])
    frame, counts = build_idday_events(_world(events=[event]), ENTRY)

    assert tuple(frame.columns) == IDDAY_EVENT_COLUMNS
    leg = _only(frame, role="leg")
    assert leg["symbol"] == "GOOGL"  # 2e6 of dollar volume against GOOG's 1e6
    assert leg["cik"] == 1
    assert leg["event_id"] == leg["pair_id"] == event.event_id
    assert leg["session"] == DAYS[f + 2]
    assert leg["decision_at"] == decision_at(DAYS[f + 2], CLOCK)
    assert (leg["F"], leg["E"], leg["X"]) == (DAYS[f], DAYS[f + 15], DAYS[f + 14])
    assert leg["hold_sessions"] == 13
    assert leg["klass"] == "standalone"
    assert leg["band"] == "v3"
    assert leg["atr"] > 0
    assert leg["ret_20"] == pytest.approx(0.0, abs=1e-12)
    assert leg["bucket"] == 0
    assert (leg["keyword"], leg["item_2_02"]) == ("investor day", False)
    assert counts["legs"] == 1

    dv = np.tile((np.arange(len(SYMS)) + 1) * 1e6, (len(DAYS), 1))
    dv[:, 0] = 5e6
    frame, _ = build_idday_events(_world(events=[event], dollar_volume=dv), ENTRY)
    assert _only(frame, role="leg")["symbol"] == "GOOG"

    eligible = np.ones((len(DAYS), len(SYMS)), bool)
    eligible[f + 2, 1] = False  # GOOGL is not eligible at D
    frame, _ = build_idday_events(_world(events=[event], eligible=eligible), ENTRY)
    assert _only(frame, role="leg")["symbol"] == "GOOG"


def _split(symbol, on):
    return CorporateAction(
        id=f"{symbol}-split-{on}",
        kind=CorporateActionKind.FORWARD_SPLIT,
        symbol=symbol,
        effective_on=on,
        old_rate=1,
        new_rate=2,
    )


F10 = POS[date(2024, 4, 15)]
D10 = F10 + 2


@pytest.mark.parametrize(
    ("reason", "world"),
    [
        ("identity_break", {"breaks": {"S10": DAYS[D10 - 60]}}),
        ("structural_action_nearby", {"actions": [_split("S10", DAYS[F10 + 15] + timedelta(days=5))]}),
        ("structural_action_nearby", {"actions": [_split("S10", DAYS[D10] - timedelta(days=5))]}),
        ("not_eligible", {"eligible": np.tile(np.arange(len(DAYS))[:, None] != D10, (1, len(SYMS)))}),
        ("no_atr", {"adjusted": {"S10": _bars(_closes(), (D10 - 5,))}}),
        ("no_return", {"adjusted": {"S10": _bars(_closes(), (D10 - 21,))}}),
    ],
)
def test_each_gate_reason_is_counted(reason, world):
    event = _event(CIK["S10"], DAYS[F10], DAYS[F10 + 15])
    frame, counts = build_idday_events(_world(events=[event], **world), ENTRY)
    assert frame.empty
    assert counts["reasons"] == {reason: 1}
    assert counts["reasons_by_year"] == {reason: {2024: 1}}
    assert counts["legs"] == 0


def test_identity_break_before_the_lookback_and_distant_split_keep_the_leg():
    event = _event(CIK["S10"], DAYS[F10], DAYS[F10 + 15])
    world = _world(
        events=[event],
        breaks={"S10": DAYS[D10 - 61]},
        actions=[_split("S10", DAYS[F10 + 15] + timedelta(days=6)), _split("S10", DAYS[D10] - timedelta(days=6))],
    )
    frame, counts = build_idday_events(world, ENTRY)
    assert counts["reasons"] == {}
    assert _only(frame, role="leg")["symbol"] == "S10"


# --- placebos ---------------------------------------------------------------------------------------


def test_placebo_with_earnings_uses_other_report():
    f1 = POS[date(2024, 4, 15)]
    r = f1 + 160  # the other report: 160 sessions after F1, 80 after F2
    first = _event(CIK["S10"], DAYS[f1], DAYS[f1 + 15])
    second = _event(CIK["S10"], DAYS[f1 + 80], DAYS[f1 + 95])
    reports = [_report("S10", DAYS[f1]), _report("S10", DAYS[f1 + 80]), _report("S10", DAYS[r])]
    frame, counts = build_idday_events(_world(events=[first, second], reports=reports), ENTRY)

    legs = frame[frame["role"] == "leg"]
    assert set(legs["klass"]) == {"with_earnings"}
    assert set(legs["hold_sessions"]) == {13}
    placebo = _only(frame, role="placebo")
    # Each leg's only clean report is DAYS[r]: the other leg's report sits next to that leg's own event.
    assert placebo["pair_id"] == first.event_id
    assert placebo["event_id"] == f"{first.event_id}:placebo"
    assert placebo["symbol"] == "S10"
    assert placebo["session"] == DAYS[r + 2]
    assert placebo["F"] == DAYS[r]
    assert placebo["X"] == DAYS[r + 2 + 12]
    assert placebo["hold_sessions"] == 13
    assert placebo["klass"] == "with_earnings"
    assert placebo["E"] is None and placebo["keyword"] is None
    assert placebo["decision_at"] == decision_at(DAYS[r + 2], CLOCK)
    assert counts["placebos"] == 1
    assert counts["placebo_reasons"] == {"duplicate_session": 1}
    assert counts["placebo_reasons_by_year"] == {"duplicate_session": {2024: 1}}


def test_with_earnings_leg_without_a_clean_report_counts_placebo_no_session():
    f1 = POS[date(2024, 4, 15)]
    event = _event(CIK["S10"], DAYS[f1], DAYS[f1 + 15])
    reports = [_report("S10", DAYS[f1]), _report("S10", DAYS[f1 + 253])]  # beyond 252 sessions of F
    frame, counts = build_idday_events(_world(events=[event], reports=reports), ENTRY)
    assert list(frame["role"]) == ["leg"]
    assert counts["placebo_reasons"] == {"placebo_no_session": 1}


F1 = POS[date(2024, 4, 15)]
R1 = F1 + 160  # the other report; its placebo is D' = R1 + 2, X' = R1 + 14


@pytest.mark.parametrize(
    ("hit_at", "report_at", "drawn"),
    [
        (R1 - 5, None, False),
        (R1 - 6, None, True),
        (R1 + 14 + 10, None, False),
        (R1 + 14 + 11, None, True),
        (None, R1 + 6, False),  # a report inside [r' + 1, X'] (itself beyond the 165-session span)
    ],
)
def test_with_earnings_draw_needs_a_clean_report_window(hit_at, report_at, drawn):
    entry = _entry(placebo={**ENTRY.placebo.model_dump(), "earnings_span_sessions": 165})
    event = _event(CIK["S10"], DAYS[F1], DAYS[F1 + 15])
    reports = [_report("S10", DAYS[F1]), _report("S10", DAYS[R1])]
    if report_at is not None:
        reports.append(_report("S10", DAYS[report_at]))
    hits = () if hit_at is None else (SearchHit("0000000000-24-777777", "a.htm", DAYS[hit_at], (), "8-K", ""),)
    records = _records(**{f"c{CIK['S10']}": {"hits": hits}})
    frame, counts = build_idday_events(_world(events=[event], reports=reports, records=records), entry)
    if drawn:
        assert _only(frame, role="placebo")["session"] == DAYS[R1 + 2]
    else:
        assert list(frame["role"]) == ["leg"]
        assert counts["placebo_reasons"] == {"placebo_no_session": 1}


def test_standalone_placebo_is_a_seeded_draw_among_clean_sessions():
    f = POS[date(2024, 6, 3)]
    d, h = f + 2, 13
    event = _event(CIK["S10"], DAYS[f], DAYS[f + 15])
    reports = [_report("S10", DAYS[d - 70]), _report("S10", DAYS[d + 50])]
    hit = SearchHit("0000000000-24-999999", "doc.htm", DAYS[d + 90], (), "EX-99.1", "press release")
    records = _records(**{f"c{CIK['S10']}": {"hits": (hit,)}})
    frame, counts = build_idday_events(_world(events=[event], reports=reports, records=records), ENTRY)

    report_days = [row.date for row in reports]
    event_days = [event.filed, event.event_date, hit.filed]
    clean = []
    for q in range(d - 126, d + 127):
        xq = q + h - 1
        if any(DAYS[q - 30] <= day <= DAYS[xq] for day in report_days):
            continue
        if any(DAYS[q - 63] <= day <= DAYS[xq + 10] for day in event_days):
            continue
        clean.append(q)
    rng = np.random.default_rng(ENTRY.placebo.seed)
    expected = clean[int(rng.integers(len(clean)))]
    placebo = _only(frame, role="placebo")
    assert placebo["session"] == DAYS[expected]
    assert placebo["X"] == DAYS[expected + h - 1]
    assert placebo["F"] is None
    assert placebo["klass"] == "standalone"
    assert counts["placebos"] == 1

    again, _ = build_idday_events(_world(events=[event], reports=reports, records=records), ENTRY)
    pd.testing.assert_frame_equal(frame, again)


# --- controls ---------------------------------------------------------------------------------------


D30 = POS[date(2024, 6, 5)]
H30 = 10
X30 = D30 + H30 - 1


def _row(symbol, d=D30, h=H30, *, klass="standalone", role="leg"):
    record = dict.fromkeys(IDDAY_EVENT_COLUMNS)
    event_id = f"{CIK[symbol]}:{DAYS[d - 2]}:{DAYS[d + h]}"
    record |= {
        "event_id": event_id if role == "leg" else f"{event_id}:placebo",
        "pair_id": event_id,
        "role": role,
        "symbol": symbol,
        "cik": CIK[symbol],
        "klass": klass,
        "session": DAYS[d],
        "F": DAYS[d - 2],
        "X": DAYS[d + h - 1],
        "hold_sessions": h,
    }
    return record


def _rows(*records):
    return pd.DataFrame(list(records), columns=list(IDDAY_EVENT_COLUMNS))


# The barred names S31-S38 tie the leg at distance 0; the admissible S39-S44 are progressively farther.
NEAR = {**{f"S{i}": 0.0 for i in range(30, 39)}, **{f"S{39 + i}": 0.001 * (i + 1) for i in range(6)}}
COVER = [_report(s, DAYS[D30 - 100]) for s in SYMS]  # one report in the 130 sessions before D, before the quiet span


def _control_world(*, extra_reports=(), hit_at=D30 - 63, uncovered=(), **kw):
    cik = CIK
    records = _records(
        **{
            f"c{cik['S31']}": {"visible": False},
            f"c{cik['S32']}": {"capped": True},
            f"c{cik['S33']}": {"doc_failures": {"http_404": 1}},
            f"c{cik['S30']}": {"symbols": ("S30", "S34")},
            f"c{cik['S34']}": None,
            f"c{cik['S35']}": {"hits": (SearchHit("0000000000-24-000001", "a.htm", DAYS[hit_at], (), "8-K", ""),)},
            f"c{cik['S38']}": None,  # unmapped: no issuer, so visibility is unknown
        }
    )
    parsed = _event(cik["S37"], DAYS[X30 - 20], DAYS[X30 + 10])
    reports = [*(r for r in COVER if r.symbol not in uncovered), _report("S36", DAYS[D30 - 30]), *extra_reports]
    return _world(events=[parsed], reports=reports, records=records, mu=NEAR, **kw)


def test_control_rules():
    frame, counts = match_controls(_rows(_row("S30")), _control_world(), ENTRY)
    assert tuple(frame.columns) == CONTROL_CANDIDATE_COLUMNS
    row = _only(frame, role="leg")
    assert row["pair_id"] == _row("S30")["pair_id"]
    assert row["session"] == DAYS[D30]
    assert row["hold_sessions"] == H30
    assert row["control_symbols"] == "S39,S40,S41,S42,S43"
    assert counts["paired"] == {"leg": 1, "placebo": 0}

    # Archive coverage: a name with no report in the 130 sessions before D is not a standalone control.
    frame, _ = match_controls(_rows(_row("S30")), _control_world(uncovered=("S39",)), ENTRY)
    assert frame.iloc[0]["control_symbols"] == "S40,S41,S42,S43,S44"

    # Another leg on D is never a control.
    frame, _ = match_controls(_rows(_row("S30"), _row("S39")), _control_world(), ENTRY)
    assert _only(frame, pair_id=_row("S30")["pair_id"])["control_symbols"] == "S40,S41,S42,S43,S44"


@pytest.mark.parametrize(
    ("hit_at", "admitted"), [(D30 - 64, True), (D30 - 63, False), (X30 + 10, False), (X30 + 11, True)]
)
def test_raw_hit_bars_a_control_from_63_sessions_before_d_to_10_after_x(hit_at, admitted):
    frame, _ = match_controls(_rows(_row("S30")), _control_world(hit_at=hit_at), ENTRY)
    expected = "S35,S39,S40,S41,S42" if admitted else "S39,S40,S41,S42,S43"
    assert frame.iloc[0]["control_symbols"] == expected


def test_with_earnings_controls_are_reporting_peers():
    band = D30 - 3  # inside the 3-session band ending at F's session (D - 2)
    reports = [
        *(_report(f"S{i}", DAYS[band]) for i in range(39, 45)),
        _report("S39", DAYS[D30 + 1]),  # a peer that also reports inside [F + 1, X]
    ]
    mu = {**{f"S{i}": 0.0 for i in range(30, 39)}, **{f"S{39 + i}": 0.001 * (i + 1) for i in range(6)}}
    world = _world(reports=reports, mu=mu)
    frame, _ = match_controls(_rows(_row("S30", klass="with_earnings")), world, ENTRY)
    assert frame.iloc[0]["control_symbols"] == "S40,S41,S42,S43,S44"


def test_fewer_than_five_candidates_leaves_the_row_unpaired_by_year_and_class():
    eligible = np.ones((len(DAYS), len(SYMS)), bool)
    eligible[D30, 39] = False
    world = _control_world(eligible=eligible, uncovered=("S40",))
    frame, counts = match_controls(_rows(_row("S30")), world, ENTRY)
    assert frame.empty
    assert counts["unpaired_by_year"]["leg"] == {2024: 1}
    assert counts["unpaired_by_class"]["leg"] == {"standalone": 1}


def test_placebo_rows_get_controls_too():
    frame, counts = match_controls(_rows(_row("S30", role="placebo")), _control_world(), ENTRY)
    row = _only(frame, role="placebo")
    assert row["control_symbols"] == "S39,S40,S41,S42,S43"
    assert counts["paired"] == {"leg": 0, "placebo": 1}


# --- cohort bands -----------------------------------------------------------------------------------


def test_cohort_bands_from_the_xu_screens():
    v3 = load_cohort(REPO / "config/research/pooled/cohort-v3.json").cohort
    xu = load_cohort(REPO / "config/research/pooled/cohort-xu.json").cohort
    bands = cohort_bands(v3, xu)
    assert len(bands) == len(v3.symbols) + len(xu.symbols)
    counts = pd.Series(bands).value_counts().to_dict()
    large = next(s for s in xu.sources if (s.screen.rank_from if s.screen else 1) == 1)
    assert counts["v3"] == len(v3.symbols)
    assert counts["xu_large"] == len(set(large.symbols) & set(xu.symbols))
    assert counts["xu_large"] + counts["xu_small"] == len(xu.symbols)

    single = xu.model_copy(update={"sources": xu.sources[:1]})
    assert set(cohort_bands(v3, single).values()) == {"v3", "xu"}


# --- parser check -----------------------------------------------------------------------------------


EXCLUDED = ("INTU", "KMI", "AAPL")


def _check_table():
    events = []
    for i in range(80):
        symbols = (EXCLUDED[i // 20],) if i % 20 == 7 and i < 60 else (f"T{i:03d}",)
        f = DAYS[100 + 3 * i]
        events.append(_event(5000 + i, f, DAYS[100 + 3 * i + 20], symbols=symbols))
    return _table(*events)


def test_parser_check_sample_and_validate():
    table = _check_table()
    candidates, _ = candidate_legs(table, DAYS, {}, ENTRY)
    assert len(candidates) == 80
    sample = sample_parser_check(candidates, table, ENTRY)
    assert sample_parser_check(candidates, table, ENTRY) == sample
    assert sample["events_sha256"] == table.events_sha256
    assert (sample["seed"], sample["sample_size"]) == (20261119, 60)

    by_id = {event.event_id: event for event in table.events}
    eligible = sorted(eid for eid, event in by_id.items() if not set(event.symbols) & set(EXCLUDED))
    assert len(eligible) == 77
    drawn = np.random.default_rng(20261119).choice(len(eligible), size=60, replace=False)
    expected = sorted(eligible[i] for i in drawn)
    assert [row["event_id"] for row in sample["sample"]] == expected
    first = sample["sample"][0]
    event = by_id[first["event_id"]]
    assert first == {
        "event_id": event.event_id,
        "symbols": list(event.symbols),
        "filed": event.filed.isoformat(),
        "event_date": event.event_date.isoformat(),
        "keyword": event.keyword,
        "sentence": event.sentence,
        "url": event.url,
    }

    def document(errors, **change):
        labels = [
            {"event_id": row["event_id"], "correct": i >= errors, "reason": "own investor day on the parsed date"}
            for i, row in enumerate(sample["sample"])
        ]
        body = {"events_sha256": sample["events_sha256"], "seed": 20261119, "sample_size": 60, "labels": labels}
        body.update(change)
        return body

    assert validate_parser_check(document(9), sample, ENTRY) == {
        "errors": 9,
        "sample_size": 60,
        "precision": pytest.approx(51 / 60),
    }
    with pytest.raises(ValueError, match="errors"):
        validate_parser_check(document(10), sample, ENTRY)
    with pytest.raises(ValueError, match="events_sha256"):
        validate_parser_check(document(0, events_sha256="f" * 64), sample, ENTRY)
    missing = document(0)
    missing["labels"][3]["reason"] = " "
    with pytest.raises(ValueError, match="reason"):
        validate_parser_check(missing, sample, ENTRY)
    no_key = document(0)
    del no_key["labels"][4]["reason"]
    with pytest.raises(ValueError, match="reason"):
        validate_parser_check(no_key, sample, ENTRY)
    not_bool = document(0)
    not_bool["labels"][5]["correct"] = "yes"
    with pytest.raises(ValueError, match="correct"):
        validate_parser_check(not_bool, sample, ENTRY)
    other = document(0)
    other["labels"][0]["event_id"] = next(eid for eid in eligible if eid not in expected)
    with pytest.raises(ValueError, match="event_id"):
        validate_parser_check(other, sample, ENTRY)
    short = document(0)
    short["labels"].pop()
    with pytest.raises(ValueError, match="event_id"):
        validate_parser_check(short, sample, ENTRY)


def test_parser_check_seed_changes_the_draw_and_small_population_refuses():
    table = _check_table()
    candidates, _ = candidate_legs(table, DAYS, {}, ENTRY)
    check = {**ENTRY.parser_check.model_dump(), "seed": 7}
    other = sample_parser_check(candidates, table, _entry(parser_check=check))
    assert other["sample"] != sample_parser_check(candidates, table, ENTRY)["sample"]
    with pytest.raises(ValueError, match="candidate"):
        sample_parser_check(candidates.iloc[:50], table, ENTRY)
