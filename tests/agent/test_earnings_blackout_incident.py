"""The 2026-11-10 incident shape end to end: the real Nasdaq client answers HTTP 403 (unverified, not raised) and
the real journaled capture store decides the blackout."""

from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from agentic_trader.agent.earnings import (
    NasdaqEarningsCalendar,
    earnings_blackout_reason,
    earnings_note,
    resolve_earnings,
)
from agentic_trader.data.earnings_capture import COLUMNS, EarningsCaptureStore, capture_targets


NOW = datetime(2026, 9, 23, 14, 35, tzinfo=UTC)  # Wednesday 10:35 New York
TODAY = date(2026, 9, 23)
BLACKOUT = 7


@pytest.fixture
def store(temp_db, tmp_path):
    return EarningsCaptureStore(temp_db.workflows, tmp_path / "private" / "earnings")


def _forbidden() -> NasdaqEarningsCalendar:
    return NasdaqEarningsCalendar(transport=httpx.MockTransport(lambda request: httpx.Response(403, text="denied")))


async def _record(store, capture_date: date, report: tuple[str, date] | None) -> None:
    rows: dict[str, list[list]] = {day.isoformat(): [] for day in capture_targets(capture_date, 28)}
    if report is not None:
        symbol, day = report
        row = [None] * len(COLUMNS)
        row[COLUMNS.index("symbol")] = symbol
        row[COLUMNS.index("timing")] = "after_hours"
        rows[day.isoformat()].append(row)
    payload = {
        "capture_date": capture_date.isoformat(),
        "columns": list(COLUMNS),
        "pages": [{"date": day} for day in rows],
        "rows": rows,
    }
    assert await store.record(payload, {}) is True


async def test_fresh_capture_with_a_report_blocks_with_the_capture_suffix(store):
    await _record(store, TODAY, ("AAPL", TODAY + timedelta(days=2)))
    lookup = await resolve_earnings("AAPL", NOW, BLACKOUT, live=_forbidden(), captures=store)
    assert lookup.source == "capture" and lookup.verified
    reason = earnings_blackout_reason(lookup, "AAPL", NOW, BLACKOUT, unknown="closed")
    assert reason is not None and reason.endswith(f"(calendar capture {TODAY.isoformat()})")


async def test_fresh_capture_without_a_report_clears_with_the_suffix(store):
    await _record(store, TODAY, None)
    lookup = await resolve_earnings("AAPL", NOW, BLACKOUT, live=_forbidden(), captures=store)
    assert earnings_blackout_reason(lookup, "AAPL", NOW, BLACKOUT, unknown="closed") is None
    assert earnings_note(lookup, NOW, BLACKOUT) == f"No report within 7 days (calendar capture {TODAY.isoformat()})"


async def test_stale_capture_under_closed_refuses_as_unknown(store):
    await _record(store, TODAY - timedelta(days=5), None)
    lookup = await resolve_earnings("AAPL", NOW, BLACKOUT, live=_forbidden(), captures=store)
    assert lookup.source == "live" and not lookup.verified and lookup.event is None
    reason = earnings_blackout_reason(lookup, "AAPL", NOW, BLACKOUT, unknown="closed")
    assert reason is not None and "earnings calendar unavailable" in reason
    assert earnings_blackout_reason(lookup, "AAPL", NOW, BLACKOUT, unknown="open") is None
