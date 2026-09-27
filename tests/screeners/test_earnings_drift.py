"""``EarningsDriftService``: catalog-only PEAD drift candidates for the 10:35 scan.

Events are faked (``live_events`` is monkeypatched); the point here is the service's own
decision logic -- applicability, live-version lookup, owned-symbol skipping, the
session-counted time exit and the locked probe card it builds -- never the study math
covered by ``tests/research/test_pead_*``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from agentic_trader.market.session import MarketCalendarDay
from agentic_trader.research.alpha.models import RegistrySnapshot
from agentic_trader.research.apriori.pead_live import LiveEvents
from agentic_trader.research.apriori.probe import catalog_alpha_id, load_catalog_probe
from agentic_trader.screeners import earnings_drift
from agentic_trader.screeners.earnings_drift import (
    PEAD_STRATEGY_ID,
    DriftPreparation,
    EarningsDriftService,
    drift_card_facts,
)
from tests.research.test_apriori_catalog_probe import ENTRY, study


NEW_YORK = ZoneInfo("America/New_York")


class _WeekdayCalendar:
    """Every Mon-Fri is a regular 09:30-16:00 New York session; weekends are closed."""

    async def get_calendar_range(self, start: date, end: date) -> list[MarketCalendarDay]:
        days = []
        current = start
        while current <= end:
            regular = current.weekday() < 5
            days.append(
                MarketCalendarDay(
                    date=current,
                    is_trading_day=regular,
                    is_early_close=False,
                    open_time=time(9, 30) if regular else None,
                    close_time=time(16, 0) if regular else None,
                )
            )
            current += timedelta(days=1)
        return days


def _event(symbol: str, z: float, *, surprise_pct: float = 10.0, atr: float = 2.0) -> dict:
    return {
        "symbol": symbol,
        "report_date": "2026-10-26",
        "session": "2026-10-28",
        "decision_at": "2026-10-28T14:35:00+00:00",
        "z": z,
        "surprise_pct": surprise_pct,
        "surprise_pct_reported": surprise_pct,
        "atr": atr,
        "median_dollar_volume": 5_000_000.0,
        "reference": 1_000_000.0,
        "leg": "LONG",
        "surprise_long": True,
        "surprise_short": False,
        "reaction_long": True,
        "reaction_short": False,
    }


def _service(tmp_path, *, earnings=None, calendar=None, bars=None, static_symbols=()) -> EarningsDriftService:
    return EarningsDriftService(
        Path(ENTRY),
        earnings=earnings,
        calendar=calendar or _WeekdayCalendar(),
        bars=bars,
        static_symbols=static_symbols,
    )


@pytest.fixture
def definition(tmp_path):
    return load_catalog_probe(ENTRY, study(tmp_path), "long")


@pytest.fixture
def snapshot(definition):
    return RegistrySnapshot(0, (), (), catalog_probes=(definition,))


NOW = datetime(2026, 10, 28, 10, 40, tzinfo=NEW_YORK)


def test_pead_strategy_id_matches_the_catalog_alpha_id():
    assert PEAD_STRATEGY_ID == catalog_alpha_id("pead", "LONG") == "pead_long"


def test_holding_sessions_reads_the_entry_trade_block(tmp_path):
    service = _service(tmp_path)
    assert service.holding_sessions == 20


def test_applies_only_at_the_entry_decision_time(tmp_path):
    service = _service(tmp_path)
    assert service.decision_time_et == "10:35"
    assert service.applies("10:35") is True
    assert service.applies("14:35") is False
    assert service.applies(None) is False


async def test_no_live_catalog_version_is_inactive_and_reads_nothing(tmp_path, monkeypatch):
    service = _service(tmp_path)
    empty_snapshot = RegistrySnapshot(0, (), (), catalog_probes=())

    called = False

    async def fake_live_events(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("live_events must not be called when no live catalog version exists")

    monkeypatch.setattr(earnings_drift, "live_events", fake_live_events)

    prep = await service.prepare(now=NOW.astimezone(UTC), snapshot=empty_snapshot, owned={})
    assert prep.status == "inactive"
    assert called is False


async def test_a_version_for_another_entry_sha_is_inactive(tmp_path, definition, monkeypatch):
    service = _service(tmp_path)
    mismatched = {**definition, "entry_sha256": "0" * 64}
    snapshot = RegistrySnapshot(0, (), (), catalog_probes=(mismatched,))

    async def fake_live_events(*args, **kwargs):
        raise AssertionError("live_events must not be called for a mismatched catalog version")

    monkeypatch.setattr(earnings_drift, "live_events", fake_live_events)

    prep = await service.prepare(now=NOW.astimezone(UTC), snapshot=snapshot, owned={})
    assert prep.status == "inactive"


async def test_ok_events_skip_owned_symbols_with_reasons(tmp_path, definition, snapshot, monkeypatch):
    service = _service(tmp_path)
    events = (_event("WINR", 2.1), _event("ABCD", 1.5))

    async def fake_live_events(entry, session, *, earnings, calendar, bars, static_symbols):
        return LiveEvents("ok", session, date(2026, 10, 26), events, {"reporters": 2}, None, None)

    monkeypatch.setattr(earnings_drift, "live_events", fake_live_events)

    prep = await service.prepare(now=NOW.astimezone(UTC), snapshot=snapshot, owned={"ABCD": "open position"})

    assert prep.status == "ok"
    assert [event["symbol"] for event in prep.events] == ["WINR"]
    assert prep.skipped == {"ABCD": "open position"}
    assert prep.skipped_events == (events[1],)  # the full document, for the counterfactual set
    assert prep.policy == definition["execution"]
    assert prep.version_id == definition["version_id"]

    expected_exit = datetime(2026, 11, 24, 15, 45, tzinfo=NEW_YORK).astimezone(UTC)
    assert prep.time_exit_at == expected_exit.isoformat()


async def test_unavailable_passes_the_reason_through(tmp_path, snapshot, monkeypatch):
    service = _service(tmp_path)

    async def fake_live_events(entry, session, *, earnings, calendar, bars, static_symbols):
        return LiveEvents("unavailable", session, None, (), {}, "calendar_empty", None)

    monkeypatch.setattr(earnings_drift, "live_events", fake_live_events)

    prep = await service.prepare(now=NOW.astimezone(UTC), snapshot=snapshot, owned={})
    assert prep.status == "unavailable"
    assert prep.reason == "calendar_empty"


def _daily_frame(rows: int = 60) -> pd.DataFrame:
    index = pd.date_range("2026-08-01", periods=rows, freq="B", tz="UTC")
    closes = [100.0 + i * 0.1 for i in range(rows)]
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 1.0 for c in closes],
            "Low": [c - 1.0 for c in closes],
            "Close": closes,
            "Volume": [1_000_000] * rows,
        },
        index=index,
    )


def _hourly_frame(rows: int = 7, last_close: float = 123.45) -> pd.DataFrame:
    index = pd.date_range("2026-10-28 09:30", periods=rows, freq="h", tz="UTC")
    closes = [120.0 + i for i in range(rows - 1)] + [last_close]
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 0.5 for c in closes],
            "Low": [c - 0.5 for c in closes],
            "Close": closes,
            "Volume": [10_000] * rows,
        },
        index=index,
    )


def test_candidate_is_a_locked_probe_card(tmp_path, definition, snapshot):
    service = _service(tmp_path)
    event = _event("WINR", 2.1, surprise_pct=10.0, atr=3.25)
    data = SimpleNamespace(daily=_daily_frame(), hourly=_hourly_frame(last_close=123.45))
    prep = DriftPreparation(
        "ok",
        date(2026, 10, 28),
        version_id=definition["version_id"],
        policy=definition["execution"],
        events=(event,),
        time_exit_at="2026-11-24T20:45:00+00:00",
    )

    candidate = service.candidate(event, data, prep)

    assert candidate is not None
    assert candidate.contract == "WINR"
    assert candidate.strategy == "pead_long"
    assert candidate.direction == "LONG"
    assert candidate.timeframe == "1d"
    assert candidate.current_price == 123.45
    assert candidate.atr_14 == event["atr"]
    assert candidate.probe is True
    assert candidate.setup_quality == 0.0
    assert candidate.alpha_version == prep.version_id
    assert candidate.alpha_policy == prep.policy
    assert candidate.catalog_event == event
    assert "EPS beat +10.0%" in candidate.trigger_detail
    assert "+2.1σ" in candidate.trigger_detail


def test_candidate_without_intraday_bars_has_a_reason(tmp_path):
    service = _service(tmp_path)
    event = _event("WINR", 2.1)
    empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    data = SimpleNamespace(daily=_daily_frame(), hourly=empty, four_hour=empty)
    prep = DriftPreparation("ok", date(2026, 10, 28))

    assert service.candidate(event, data, prep) == "no intraday price"
    assert service.candidate(event, None, prep) == "no intraday price"
    short_daily = SimpleNamespace(daily=_daily_frame(rows=5), hourly=_hourly_frame())
    assert service.candidate(event, short_daily, prep) == "no intraday price"


def test_candidate_fetch_failure_names_the_exception(tmp_path):
    service = _service(tmp_path)
    prep = DriftPreparation("ok", date(2026, 10, 28))

    assert service.candidate(_event("WINR", 2.1), TimeoutError("slow"), prep) == "fetch failed: TimeoutError"


def test_candidate_with_a_previous_session_price_is_stale(tmp_path):
    service = _service(tmp_path)
    # The last hourly bar is 2026-10-27 (New York); the decision session is 2026-10-28.
    yesterday = _hourly_frame()
    yesterday.index = yesterday.index - pd.Timedelta(days=1)
    data = SimpleNamespace(daily=_daily_frame(), hourly=yesterday)
    prep = DriftPreparation("ok", date(2026, 10, 28))

    assert service.candidate(_event("WINR", 2.1), data, prep) == "stale intraday price"


def test_candidate_reads_a_naive_intraday_stamp_as_utc(tmp_path):
    service = _service(tmp_path)
    # 2026-10-29 02:00 UTC is still 2026-10-28 22:00 in New York: the same session.
    late = _hourly_frame(rows=1)
    late.index = pd.DatetimeIndex([pd.Timestamp("2026-10-29 02:00")])
    data = SimpleNamespace(daily=_daily_frame(), hourly=late)
    prep = DriftPreparation("ok", date(2026, 10, 28))

    assert not isinstance(service.candidate(_event("WINR", 2.1), data, prep), str)


def test_drift_card_facts_reports_the_frozen_card_terms():
    event = _event("WINR", 2.1, surprise_pct=10.0)
    prep = DriftPreparation("ok", date(2026, 10, 28), time_exit_at="2026-11-24T20:45:00+00:00")

    facts = drift_card_facts(event, prep, holding_sessions=20)

    assert facts == {
        "surprise_pct": 10.0,
        "z": 2.1,
        "report_date": "2026-10-26",
        "holding_sessions": 20,
        "time_exit_at": "2026-11-24T20:45:00+00:00",
    }
