from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_trader.agent.calendar import (
    BaseEconomicCalendar,
    EconomicCalendarProtocol,
    ForexFactoryCalendar,
    MacroEvent,
)


class DummyCalendar(BaseEconomicCalendar):
    def __init__(self, events: list[MacroEvent] | None = None):
        self._events = events or []

    async def fetch_events(self, force_refresh: bool = False) -> list[MacroEvent]:
        return self._events


def test_base_calendar_abstract():
    """Verify that BaseEconomicCalendar cannot be instantiated directly."""
    with pytest.raises(TypeError):
        BaseEconomicCalendar()  # type: ignore[abstract]


def test_calendar_protocol_conformance():
    """Verify runtime checkability against EconomicCalendarProtocol."""
    calendar = DummyCalendar()
    assert isinstance(calendar, EconomicCalendarProtocol)
    assert isinstance(ForexFactoryCalendar(), EconomicCalendarProtocol)
    assert issubclass(ForexFactoryCalendar, BaseEconomicCalendar)


def test_is_tier_1_classification():
    calendar = DummyCalendar()
    assert calendar.is_tier_1("CPI m/m", "USD") is True
    assert calendar.is_tier_1("Core PPI m/m", "US") is True
    assert calendar.is_tier_1("FOMC Statement", "USD") is True
    assert calendar.is_tier_1("Non-Farm Employment Change", "USD") is True
    assert calendar.is_tier_1("Unemployment Rate", "USD") is True

    # Non-USD should be rejected
    assert calendar.is_tier_1("CPI m/m", "EUR") is False
    assert calendar.is_tier_1("FOMC", "GBP") is False

    # Non-Tier-1 events
    assert calendar.is_tier_1("Initial Jobless Claims", "USD") is False
    assert calendar.is_tier_1("Crude Oil Inventories", "USD") is False


@pytest.mark.asyncio
async def test_upcoming_tier1_events():
    now = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)
    events = [
        MacroEvent(title="CPI m/m", country="USD", impact="High", timestamp=now + timedelta(hours=2)),
        MacroEvent(title="FOMC Press Conference", country="USD", impact="High", timestamp=now + timedelta(hours=10)),
        MacroEvent(title="Past CPI", country="USD", impact="High", timestamp=now - timedelta(hours=2)),
        MacroEvent(title="Distant CPI", country="USD", impact="High", timestamp=now + timedelta(hours=36)),
        MacroEvent(title="Crude Oil Inventories", country="USD", impact="Medium", timestamp=now + timedelta(hours=4)),
    ]
    calendar = DummyCalendar(events)
    upcoming = await calendar.get_upcoming_tier1_events(window_hours=24, now=now)

    assert len(upcoming) == 2
    assert [e.title for e in upcoming] == ["CPI m/m", "FOMC Press Conference"]


@pytest.mark.asyncio
async def test_lockout_window_detection():
    now = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)
    event_time = now + timedelta(minutes=45)
    events = [
        MacroEvent(title="CPI m/m", country="USD", impact="High", timestamp=event_time),
    ]
    calendar = DummyCalendar(events)

    # 45 minutes before event (within [-60m, +30m] lockout) -> In Lockout
    in_lockout, lock_event = await calendar.is_in_lockout_window(pre_minutes=60, post_minutes=30, now=now)
    assert in_lockout is True
    assert lock_event is not None
    assert lock_event.title == "CPI m/m"

    # 75 minutes before event -> Not in Lockout
    far_before = event_time - timedelta(minutes=75)
    in_lockout, lock_event = await calendar.is_in_lockout_window(pre_minutes=60, post_minutes=30, now=far_before)
    assert in_lockout is False
    assert lock_event is None

    # 20 minutes after event (within post 30m) -> In Lockout
    shortly_after = event_time + timedelta(minutes=20)
    in_lockout, lock_event = await calendar.is_in_lockout_window(pre_minutes=60, post_minutes=30, now=shortly_after)
    assert in_lockout is True

    # 45 minutes after event -> Not in Lockout
    far_after = event_time + timedelta(minutes=45)
    in_lockout, lock_event = await calendar.is_in_lockout_window(pre_minutes=60, post_minutes=30, now=far_after)
    assert in_lockout is False


@pytest.mark.asyncio
async def test_macro_summary_for_prompt():
    now = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)

    # 1. Clear macro
    calendar_clear = DummyCalendar([])
    summary = await calendar_clear.get_macro_summary_for_prompt(now=now)
    assert "Lockout verified: CLEAR" in summary

    # 2. Upcoming macro outside lockout
    events_upcoming = [
        MacroEvent(title="CPI m/m", country="USD", impact="High", timestamp=now + timedelta(hours=4)),
    ]
    calendar_upcoming = DummyCalendar(events_upcoming)
    summary_upcoming = await calendar_upcoming.get_macro_summary_for_prompt(now=now)
    assert "Upcoming Tier-1 releases" in summary_upcoming
    assert "CPI m/m" in summary_upcoming

    # 3. Active lockout
    events_lockout = [
        MacroEvent(title="FOMC Statement", country="USD", impact="High", timestamp=now + timedelta(minutes=20)),
    ]
    calendar_lockout = DummyCalendar(events_lockout)
    summary_lockout = await calendar_lockout.get_macro_summary_for_prompt(now=now)
    assert "LOCKOUT ACTIVE" in summary_lockout
    assert "FOMC Statement" in summary_lockout


@pytest.mark.asyncio
async def test_forexfactory_calendar_fetch():
    mock_payload = [
        {
            "title": "CPI m/m",
            "country": "USD",
            "date": "2026-09-13T08:30:00-04:00",
            "impact": "High",
            "forecast": "0.3%",
            "previous": "0.2%",
        },
        {
            "title": "EUR German Prelim CPI",
            "country": "EUR",
            "date": "2026-09-13T09:00:00+02:00",
            "impact": "High",
        },
    ]

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_payload

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
        calendar = ForexFactoryCalendar(cache_ttl_minutes=15)
        events = await calendar.fetch_events(force_refresh=True)

        assert len(events) == 1
        assert events[0].title == "CPI m/m"
        assert events[0].country == "USD"
        assert events[0].forecast == "0.3%"

        # Second call within cache TTL should return cached events without HTTP call
        cached = await calendar.fetch_events(force_refresh=False)
        assert len(cached) == 1
