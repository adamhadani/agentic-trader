from datetime import date, time, timedelta
from pathlib import Path

import numpy as np

from agentic_trader.research.apriori.catalog import InsiderEntry, load_insider_entry
from agentic_trader.research.apriori.insider_events import (
    INSIDER_EVENT_COLUMNS,
    build_insider_events,
    opportunistic_flags,
    purchase_dates,
)
from agentic_trader.research.insider_filings import InsiderTransaction


SHIPPED = Path(__file__).resolve().parents[3] / "config/research/apriori/insider-v1.json"


def _calendar(n=40, first=date(2020, 3, 2), skip=()):
    days, d = [], first
    while len(days) < n:
        if d.weekday() < 5 and d not in skip:
            days.append(d)
        d += timedelta(days=1)
    return days


DAYS = _calendar()


def _entry(decisions=(date(2020, 1, 1), date(2020, 12, 31))):
    base = load_insider_entry(SHIPPED).entry.model_dump(mode="json")
    base["window"]["decisions"] = [d.isoformat() for d in decisions]
    base["window"]["recent_from"] = (decisions[0] + timedelta(days=1)).isoformat()
    base["window"]["bars_through"] = (decisions[1] + timedelta(days=400)).isoformat()
    return InsiderEntry.model_validate(base)


def _tx(day, owner, symbol="AAA", dollars=1000.0, code="P"):
    return InsiderTransaction(symbol, day, day, code, dollars, owner)


def _build(txs, days=DAYS, breaks=None, entry=None):
    grouped = {}
    for t in txs:
        grouped.setdefault(t.symbol, []).append(t)
    return build_insider_events(grouped, days, breaks or {}, entry or _entry(), decision_time=time(10, 35))


def test_two_owners_within_window_fire():
    frame, counts = _build([_tx(DAYS[2], 1), _tx(DAYS[22], 2, dollars=500.0)])
    assert list(frame.columns) == list(INSIDER_EVENT_COLUMNS)
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row.buyers == 2
    assert row.opp_dollars == 1500.0
    assert row.filing_date == DAYS[22]
    assert row.session == DAYS[23]
    assert row.event_id == f"AAA:{DAYS[22].isoformat()}"
    assert counts["triggers"] == 1
    assert counts["accepted_by_year"] == {2020: 1}


def test_owners_22_sessions_apart_do_not_fire():
    frame, counts = _build([_tx(DAYS[2], 1), _tx(DAYS[23], 2)])
    assert frame.empty
    assert counts["triggers"] == 0


def test_same_owner_twice_does_not_fire():
    frame, _ = _build([_tx(DAYS[2], 1), _tx(DAYS[10], 1)])
    assert frame.empty


def test_routine_purchase_ignored():
    days = _calendar(60, first=date(2020, 6, 1))
    prior = _tx(days[25].replace(year=2019), 1)
    routine = _tx(days[25], 1)
    frame, _ = _build([prior, routine, _tx(days[30], 2)], days)
    assert frame.empty
    assert opportunistic_flags([prior, routine]) == [True, False]
    frame, _ = _build([routine, _tx(days[30], 2)], days)
    assert len(frame) == 1


def test_cooldown_suppresses_second_cluster():
    days = _calendar(120)
    txs = [_tx(days[2], 1), _tx(days[5], 2), _tx(days[35], 3), _tx(days[36], 4)]
    frame, counts = _build(txs, days)
    assert len(frame) == 1
    assert frame.iloc[0].filing_date == days[5]
    assert counts["suppressed_by_cooldown"] >= 1
    assert counts["triggers"] == 1


def test_cooldown_runs_from_ineligible_trigger():
    days = _calendar(120)
    entry = _entry(decisions=(days[20], date(2020, 12, 31)))
    txs = [_tx(days[2], 1), _tx(days[5], 2), _tx(days[15], 3), _tx(days[16], 4)]
    frame, counts = _build(txs, days, entry=entry)
    assert frame.empty
    assert counts["excluded"]["outside_window"] == {2020: 1}
    assert counts["suppressed_by_cooldown"] >= 1


def test_decision_is_first_session_after_filing():
    holiday = date(2020, 3, 11)
    days = _calendar(40, skip=(holiday,))
    assert holiday.weekday() < 5
    frame, _ = _build([_tx(date(2020, 3, 10), 1), _tx(holiday, 2, dollars=1.0)], days)
    assert frame.iloc[0].filing_date == holiday
    assert frame.iloc[0].session == date(2020, 3, 12)
    frame, _ = _build([_tx(days[2], 1), _tx(days[3], 2)], days)
    assert frame.iloc[0].session == days[4]
    assert frame.iloc[0].decision_at.hour == 15  # 10:35 EST in UTC (DST starts 2020-03-08)


def test_identity_break_excludes_earlier_filings():
    txs = [_tx(DAYS[2], 1), _tx(DAYS[5], 2)]
    frame, counts = _build(txs, breaks={"AAA": DAYS[10]})
    assert frame.empty
    assert counts["excluded"]["identity_break"] == {2020: 1}
    frame, _ = _build(txs, breaks={"AAA": DAYS[5]})
    assert len(frame) == 1


def test_counts_by_reason_and_year():
    days = _calendar(60)
    txs = [
        _tx(days[2], 1),
        _tx(days[3], 2),
        _tx(days[58], 3, symbol="BBB"),
        _tx(days[59], 4, symbol="BBB"),
        _tx(days[10], 5, symbol="CCC"),
        _tx(days[11], 6, symbol="CCC"),
    ]
    frame, counts = _build(txs, days, breaks={"CCC": days[30]})
    assert counts["triggers"] == 3
    assert counts["excluded"] == {"after_calendar": {2020: 1}, "identity_break": {2020: 1}}
    assert counts["accepted_by_year"] == {2020: 1}
    assert list(frame.symbol) == ["AAA"]


def test_deterministic_order():
    txs = [_tx(DAYS[5], 2, "ZZZ"), _tx(DAYS[4], 1, "ZZZ"), _tx(DAYS[5], 2, "AAA"), _tx(DAYS[4], 1, "AAA")]
    frame, _ = _build(txs)
    assert list(zip(frame.session, frame.symbol, strict=True)) == sorted(zip(frame.session, frame.symbol, strict=True))
    assert list(frame.symbol) == ["AAA", "ZZZ"]


def test_purchase_dates_cover_every_purchase_only():
    out = purchase_dates({"AAA": [_tx(DAYS[5], 1), _tx(DAYS[2], 2), _tx(DAYS[3], 1, code="S")]})
    assert out["AAA"].tolist() == [DAYS[2].toordinal(), DAYS[5].toordinal()]
    assert isinstance(out["AAA"], np.ndarray)
