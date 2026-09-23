from datetime import UTC, date, datetime, timedelta

import httpx

from agentic_trader.agent.earnings import (
    EarningsEvent,
    EarningsLookup,
    EarningsTiming,
    NasdaqEarningsCalendar,
    earnings_blackout_reason,
    earnings_note,
)


# 2026-09-23 14:35 UTC is 2026-09-23 10:35 America/New_York (EDT, UTC-4) — a
# fixed instant so these tests never depend on wall-clock time.
NOW = datetime(2026, 9, 23, 14, 35, tzinfo=UTC)
TODAY = date(2026, 9, 23)


def _response(rows: list[dict] | None) -> httpx.Response:
    return httpx.Response(200, json={"data": {"rows": rows} if rows is not None else None, "status": {"rCode": 200}})


def _row(symbol: str, time_value: str) -> dict:
    return {
        "symbol": symbol,
        "time": time_value,
        "name": f"{symbol} Inc",
        "fiscalQuarterEnding": "9/2026",
        "epsForecast": "1.00",
    }


# --- NasdaqEarningsCalendar: HTTP/parse behaviour --------------------------


async def test_next_earnings_parses_fixture_json():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["date"] == "2026-09-25":
            return _response([_row("COST", "time-after-hours")])
        return _response(None)

    calendar = NasdaqEarningsCalendar(transport=httpx.MockTransport(handler))
    lookup = await calendar.next_earnings("COST", now=NOW, horizon_days=7)

    assert lookup.verified is True
    assert lookup.event is not None
    assert lookup.event.symbol == "COST"
    assert lookup.event.date.isoformat() == "2026-09-25"
    assert lookup.event.timing == EarningsTiming.AFTER_HOURS


async def test_null_rows_is_a_verified_empty_day():
    calendar = NasdaqEarningsCalendar(transport=httpx.MockTransport(lambda request: _response(None)))
    lookup = await calendar.next_earnings("AAPL", now=NOW, horizon_days=3)

    assert lookup.verified is True
    assert lookup.event is None


async def test_http_error_on_one_date_marks_unverified():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["date"] == "2026-09-24":
            return httpx.Response(500, text="server error")
        return _response(None)

    calendar = NasdaqEarningsCalendar(transport=httpx.MockTransport(handler))
    lookup = await calendar.next_earnings("AAPL", now=NOW, horizon_days=3)

    assert lookup.verified is False


async def test_bad_json_on_one_date_marks_unverified():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["date"] == "2026-09-24":
            return httpx.Response(200, text="not json at all")
        return _response(None)

    calendar = NasdaqEarningsCalendar(transport=httpx.MockTransport(handler))
    lookup = await calendar.next_earnings("AAPL", now=NOW, horizon_days=3)

    assert lookup.verified is False


async def test_pre_market_today_is_not_ahead():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["date"] == "2026-09-23":
            return _response([_row("AAPL", "time-pre-market")])
        return _response(None)

    calendar = NasdaqEarningsCalendar(transport=httpx.MockTransport(handler))
    lookup = await calendar.next_earnings("AAPL", now=NOW, horizon_days=3)

    assert lookup.event is None
    assert lookup.verified is True


async def test_after_hours_today_is_ahead():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["date"] == "2026-09-23":
            return _response([_row("AAPL", "time-after-hours")])
        return _response(None)

    calendar = NasdaqEarningsCalendar(transport=httpx.MockTransport(handler))
    lookup = await calendar.next_earnings("AAPL", now=NOW, horizon_days=3)

    assert lookup.event is not None
    assert lookup.event.date.isoformat() == "2026-09-23"


async def test_earliest_ahead_event_wins():
    def handler(request: httpx.Request) -> httpx.Response:
        date_str = request.url.params["date"]
        if date_str == "2026-09-26":
            return _response([_row("AAPL", "time-after-hours")])
        if date_str == "2026-09-29":
            return _response([_row("AAPL", "time-pre-market")])
        return _response(None)

    calendar = NasdaqEarningsCalendar(transport=httpx.MockTransport(handler))
    lookup = await calendar.next_earnings("AAPL", now=NOW, horizon_days=10)

    assert lookup.event is not None
    assert lookup.event.date.isoformat() == "2026-09-26"


async def test_case_insensitive_symbol_match():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["date"] == "2026-09-24":
            return _response([_row("AAPL", "time-after-hours")])
        return _response(None)

    calendar = NasdaqEarningsCalendar(transport=httpx.MockTransport(handler))
    lookup = await calendar.next_earnings("aapl", now=NOW, horizon_days=3)

    assert lookup.event is not None
    assert lookup.event.symbol == "AAPL"


async def test_cache_avoids_second_request_within_ttl():
    request_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        request_count["n"] += 1
        return _response(None)

    calendar = NasdaqEarningsCalendar(transport=httpx.MockTransport(handler), cache_ttl_minutes=360)
    await calendar.next_earnings("AAPL", now=NOW, horizon_days=2)
    first_count = request_count["n"]
    assert first_count > 0

    await calendar.next_earnings("AAPL", now=NOW + timedelta(minutes=5), horizon_days=2)
    assert request_count["n"] == first_count


async def test_failed_date_is_not_refetched_within_the_failure_backoff():
    # A down calendar must not cost every candidate of a scan the full timeout again.
    request_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        request_count["n"] += 1
        return httpx.Response(500, text="fail")

    calendar = NasdaqEarningsCalendar(transport=httpx.MockTransport(handler), failure_retry_minutes=10)
    first = await calendar.next_earnings("AAPL", now=NOW, horizon_days=0)
    first_count = request_count["n"]
    assert first_count > 0
    assert first.verified is False

    again = await calendar.next_earnings("MSFT", now=NOW + timedelta(minutes=9), horizon_days=0)
    assert request_count["n"] == first_count
    assert again.verified is False


async def test_failed_date_is_retried_after_the_failure_backoff():
    request_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        request_count["n"] += 1
        return (
            httpx.Response(500, text="fail")
            if request_count["n"] == 1
            else _response([_row("AAPL", "time-after-hours")])
        )

    calendar = NasdaqEarningsCalendar(transport=httpx.MockTransport(handler), failure_retry_minutes=10)
    await calendar.next_earnings("AAPL", now=NOW, horizon_days=0)
    retried = await calendar.next_earnings("AAPL", now=NOW + timedelta(minutes=11), horizon_days=0)
    assert request_count["n"] == 2
    assert retried.verified is True
    assert retried.event is not None


# --- earnings_blackout_reason / earnings_note: pure helpers ---------------


def _lookup(
    offset_days: int, timing: EarningsTiming = EarningsTiming.AFTER_HOURS, verified: bool = True
) -> EarningsLookup:
    event = EarningsEvent(symbol="AAPL", date=TODAY + timedelta(days=offset_days), timing=timing)
    return EarningsLookup(event=event, verified=verified, horizon_end=TODAY + timedelta(days=30))


def test_blackout_reason_blocks_at_exact_boundary():
    reason = earnings_blackout_reason(_lookup(5), "AAPL", NOW, blackout_days=5)
    assert reason is not None
    assert "AAPL" in reason


def test_blackout_reason_does_not_block_one_day_past_boundary():
    reason = earnings_blackout_reason(_lookup(6), "AAPL", NOW, blackout_days=5)
    assert reason is None


def test_blackout_days_zero_never_blocks():
    reason = earnings_blackout_reason(_lookup(0), "AAPL", NOW, blackout_days=0)
    assert reason is None


def test_found_event_blocks_even_when_another_date_was_unverified():
    # "Unverified" only means an absence cannot be trusted; a report that was found still blocks.
    reason = earnings_blackout_reason(_lookup(1, verified=False), "AAPL", NOW, blackout_days=5)
    assert reason is not None


def test_no_event_and_unverified_fails_open():
    lookup = EarningsLookup(event=None, verified=False, horizon_end=TODAY + timedelta(days=5))
    assert earnings_blackout_reason(lookup, "AAPL", NOW, blackout_days=5) is None


def test_note_for_event_outside_blackout():
    note = earnings_note(_lookup(6), NOW, blackout_days=5)
    assert note == "Reports 2026-09-29 after close (in 6 days) — outside 5-day blackout"


def test_note_for_event_within_blackout():
    note = earnings_note(_lookup(5), NOW, blackout_days=5)
    assert note == "Reports 2026-09-28 after close (in 5 days) — within 5-day blackout"


def test_note_for_no_event():
    lookup = EarningsLookup(event=None, verified=True, horizon_end=TODAY + timedelta(days=7))
    note = earnings_note(lookup, NOW, blackout_days=7)
    assert note == "No report within 7 days"


def test_note_for_unverified():
    lookup = EarningsLookup(event=None, verified=False, horizon_end=TODAY + timedelta(days=7))
    note = earnings_note(lookup, NOW, blackout_days=7)
    assert note == "Unverified — earnings calendar unavailable"
