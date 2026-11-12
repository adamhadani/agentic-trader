import hashlib
import logging
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from agentic_trader.agent.earnings import (
    CalendarRow,
    EarningsEvent,
    EarningsLookup,
    EarningsTiming,
    ForwardRow,
    NasdaqEarningsCalendar,
    earnings_blackout_reason,
    earnings_note,
    parse_calendar_payload,
    parse_forward_rows,
    resolve_earnings,
)
from agentic_trader.data.earnings_capture import CapturedReports


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


# --- parse_calendar_payload: row parsing and field formatting ---------------


def _payload(*rows):
    return {"data": {"asOf": "Thu, Apr 29, 2021", "rows": list(rows)}}


def test_parse_calendar_payload_reads_eps_fields():
    row = {
        "symbol": "amzn",
        "time": "time-not-supplied",
        "eps": "$15.79",
        "epsForecast": "$9.75",
        "surprise": "61.95",
        "noOfEsts": "15",
        "fiscalQuarterEnding": "Mar/2021",
        "marketCap": "$2,750,294,260,000",
    }
    [parsed] = parse_calendar_payload(_payload(row), date(2021, 4, 29))
    assert parsed == CalendarRow(
        symbol="AMZN",
        date=date(2021, 4, 29),
        timing=EarningsTiming.UNSPECIFIED,
        eps=15.79,
        eps_forecast=9.75,
        surprise_pct_reported=61.95,
        n_estimates=15,
        fiscal_quarter="Mar/2021",
    )


def test_parse_calendar_payload_value_formats():
    rows = [
        {"symbol": "NEG", "eps": "($0.95)", "epsForecast": "($1.07)", "surprise": "-16", "noOfEsts": "3"},
        {"symbol": "BIG", "eps": "$1,234.50", "epsForecast": "$2", "surprise": "N/A", "noOfEsts": "N/A"},
        {"symbol": "BLANK", "eps": "", "epsForecast": "", "surprise": "", "noOfEsts": ""},
        {"symbol": "JUNK", "eps": "abc", "epsForecast": None, "surprise": "nan", "noOfEsts": "2.5"},
    ]
    parsed = {row.symbol: row for row in parse_calendar_payload(_payload(*rows), date(2021, 4, 29))}
    assert (parsed["NEG"].eps, parsed["NEG"].eps_forecast, parsed["NEG"].surprise_pct_reported) == (-0.95, -1.07, -16.0)
    assert parsed["NEG"].n_estimates == 3
    assert (parsed["BIG"].eps, parsed["BIG"].eps_forecast, parsed["BIG"].surprise_pct_reported) == (1234.5, 2.0, None)
    assert parsed["BIG"].n_estimates is None
    for symbol in ("BLANK", "JUNK"):
        row = parsed[symbol]
        assert (row.eps, row.eps_forecast, row.surprise_pct_reported, row.n_estimates) == (None, None, None, None)


def test_parse_calendar_payload_empty_and_malformed_days():
    day = date(2021, 5, 1)
    assert parse_calendar_payload({"data": None}, day) == []
    assert parse_calendar_payload({"data": {"rows": None}}, day) == []
    assert parse_calendar_payload([], day) == []
    assert parse_calendar_payload(_payload("not a row", {"symbol": ""}, {"eps": "$1"}), day) == []


# --- NasdaqEarningsCalendar.reported_rows: every row for one report day ---


async def test_reported_rows_parses_a_200_page():
    body = {
        "status": {"rCode": 200},
        "data": {
            "rows": [
                {
                    "symbol": "NVDA",
                    "eps": "$1.10",
                    "epsForecast": "$1.00",
                    "surprise": "10",
                    "noOfEsts": "5",
                    "time": "time-not-supplied",
                    "fiscalQuarterEnding": "Oct/2026",
                }
            ]
        },
    }
    response_bytes = httpx.Response(200, json=body).content
    expected_digest = hashlib.sha256(response_bytes).hexdigest()

    calendar = NasdaqEarningsCalendar(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)))
    page = await calendar.reported_rows(date(2026, 10, 26))

    assert page.rows is not None
    assert len(page.rows) == 1
    assert page.rows[0].symbol == "NVDA"
    assert page.rows[0].eps == 1.10
    assert page.http_status == 200
    assert page.detail is None
    assert page.body_sha256 == expected_digest


async def test_reported_rows_http_error_is_unavailable():
    calendar = NasdaqEarningsCalendar(
        transport=httpx.MockTransport(lambda request: httpx.Response(503, text="service unavailable"))
    )
    page = await calendar.reported_rows(date(2026, 10, 26))

    assert page.rows is None
    assert page.http_status == 503


async def test_reported_rows_non_200_rcode_is_unavailable():
    body = {"status": {"rCode": 400}, "data": None}
    calendar = NasdaqEarningsCalendar(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)))
    page = await calendar.reported_rows(date(2026, 10, 26))

    assert page.rows is None
    assert page.detail is not None
    assert "rCode" in page.detail


async def test_reported_rows_list_body_is_unavailable():
    calendar = NasdaqEarningsCalendar(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[1, 2])))
    page = await calendar.reported_rows(date(2026, 10, 26))

    assert page.rows is None


async def test_reported_rows_transport_exception_is_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("connection reset")

    calendar = NasdaqEarningsCalendar(transport=httpx.MockTransport(handler))
    page = await calendar.reported_rows(date(2026, 10, 26))

    assert page.rows is None
    assert page.detail is not None
    assert "RuntimeError" in page.detail


def test_parse_forward_rows_reads_every_column():
    payload = {
        "data": {
            "rows": [
                {
                    "symbol": "PBR",
                    "name": "Petroleo Brasileiro",
                    "marketCap": "$126,374,027,000",
                    "fiscalQuarterEnding": "Sep/2026",
                    "epsForecast": "$1.38",
                    "noOfEsts": "2",
                    "lastYearRptDt": "11/06/2025",
                    "lastYearEPS": "$0.82",
                    "time": "time-after-hours",
                }
            ]
        }
    }
    rows, skipped = parse_forward_rows(payload, date(2026, 11, 6))
    assert rows == [
        ForwardRow("PBR", EarningsTiming.AFTER_HOURS, "Sep/2026", 1.38, 2, date(2025, 11, 6), 0.82, 126374027000.0)
    ]
    assert skipped == 0


def test_parse_forward_rows_missing_values():
    payload = {
        "data": {
            "rows": [
                {
                    "symbol": "abc",
                    "marketCap": "N/A",
                    "fiscalQuarterEnding": "",
                    "epsForecast": "N/A",
                    "noOfEsts": "",
                    "lastYearRptDt": "13/45/2025",
                    "lastYearEPS": "($0.95)",
                    "time": "time-not-supplied",
                }
            ]
        }
    }
    rows, skipped = parse_forward_rows(payload, date(2026, 11, 6))
    assert skipped == 0
    assert rows == [ForwardRow("ABC", EarningsTiming.UNSPECIFIED, None, None, None, None, -0.95, None)]


def test_parse_forward_rows_skips_rows_without_symbol():
    payload = {"data": {"rows": [{"symbol": " "}, {"time": "x"}, {"symbol": "XYZ"}]}}
    rows, skipped = parse_forward_rows(payload, date(2026, 11, 6))
    assert [r.symbol for r in rows] == ["XYZ"]
    assert skipped == 2


def test_parse_forward_rows_null_data_is_empty():
    assert parse_forward_rows({"data": None}, date(2026, 11, 6)) == ([], 0)


# --- resolve_earnings: live first, then the dated capture, else unverified ---

CAPTURE_DATE = date(2026, 11, 12)


class _Live:
    def __init__(self, result: EarningsLookup | Exception):
        self.result = result
        self.calls = 0

    async def next_earnings(self, symbol, now, horizon_days):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _Captures:
    def __init__(self, result: CapturedReports | None | Exception):
        self.result = result
        self.calls: list[tuple[date, date, date]] = []

    async def reports_between(self, start, end, *, today):
        self.calls.append((start, end, today))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _captured(rows: dict[date, tuple[tuple[str, EarningsTiming], ...]]) -> CapturedReports:
    return CapturedReports(capture_date=CAPTURE_DATE, rows=rows)


def _live_lookup(*, event: EarningsEvent | None, verified: bool) -> EarningsLookup:
    return EarningsLookup(event=event, verified=verified, horizon_end=TODAY + timedelta(days=7))


async def test_verified_live_result_is_used_without_reading_the_capture():
    live_result = _live_lookup(event=None, verified=True)
    captures = _Captures(_captured({}))

    result = await resolve_earnings("AAPL", NOW, 7, live=_Live(live_result), captures=captures)

    assert result == live_result and result.source == "live"
    assert captures.calls == []


@pytest.mark.parametrize("error", [httpx.HTTPStatusError("403", request=None, response=None), RuntimeError("boom")])
async def test_live_failure_falls_back_to_capture_report_in_window(error):
    reports = date(2026, 9, 26)
    captures = _Captures(
        _captured({reports: (("OTHER", EarningsTiming.PRE_MARKET), ("AAPL", EarningsTiming.AFTER_HOURS))})
    )

    result = await resolve_earnings("aapl", NOW, 7, live=_Live(error), captures=captures)

    assert result.source == "capture" and result.verified and result.capture_date == CAPTURE_DATE
    assert result.event == EarningsEvent("AAPL", reports, EarningsTiming.AFTER_HOURS)
    assert result.horizon_end == TODAY + timedelta(days=7)
    assert captures.calls == [(TODAY, TODAY + timedelta(days=7), TODAY)]
    reason = earnings_blackout_reason(result, "AAPL", NOW, 7)
    assert reason is not None and "2026-09-26" in reason


async def test_live_failure_with_capture_absence_is_verified_clear():
    captures = _Captures(_captured({TODAY + timedelta(days=1): (("OTHER", EarningsTiming.PRE_MARKET),)}))

    result = await resolve_earnings("AAPL", NOW, 7, live=_Live(RuntimeError("x")), captures=captures)

    assert result.event is None and result.verified and result.source == "capture"
    assert earnings_blackout_reason(result, "AAPL", NOW, 7, unknown="closed") is None
    assert earnings_note(result, NOW, 7).endswith(" (calendar capture 2026-11-12)")
    assert earnings_note(result, NOW, 7) == "No report within 7 days (calendar capture 2026-11-12)"


async def test_unverified_live_without_event_falls_back_to_capture():
    reports = date(2026, 9, 25)
    captures = _Captures(_captured({reports: (("AAPL", EarningsTiming.PRE_MARKET),)}))
    live = _Live(_live_lookup(event=None, verified=False))

    result = await resolve_earnings("AAPL", NOW, 7, live=live, captures=captures)

    assert result.source == "capture" and result.verified and result.event is not None
    assert result.event.date == reports


async def test_unverified_live_with_event_still_blocks_without_capture():
    event = EarningsEvent("AAPL", date(2026, 9, 25), EarningsTiming.PRE_MARKET)
    live_result = _live_lookup(event=event, verified=False)
    captures = _Captures(None)

    result = await resolve_earnings("AAPL", NOW, 7, live=_Live(live_result), captures=captures)

    assert result == live_result
    assert captures.calls == []
    assert earnings_blackout_reason(result, "AAPL", NOW, 7) is not None


async def test_capture_today_premarket_is_not_ahead_but_after_close_is():
    pre = _Captures(_captured({TODAY: (("AAPL", EarningsTiming.PRE_MARKET),)}))
    after = _Captures(_captured({TODAY: (("AAPL", EarningsTiming.AFTER_HOURS),)}))

    pre_result = await resolve_earnings("AAPL", NOW, 7, live=None, captures=pre)
    after_result = await resolve_earnings("AAPL", NOW, 7, live=None, captures=after)

    assert pre_result.event is None and pre_result.verified
    assert after_result.event == EarningsEvent("AAPL", TODAY, EarningsTiming.AFTER_HOURS)


async def test_no_capture_gives_unverified():
    captures = _Captures(None)

    result = await resolve_earnings("AAPL", NOW, 7, live=_Live(RuntimeError("x")), captures=captures)

    assert (result.event, result.verified, result.source, result.capture_date) == (None, False, "live", None)
    assert result.horizon_end == TODAY + timedelta(days=7)
    assert earnings_blackout_reason(result, "AAPL", NOW, 7, unknown="closed") == (
        "AAPL: earnings calendar unavailable and no fresh capture covers the next 7 days (policy closed)"
    )
    assert earnings_blackout_reason(result, "AAPL", NOW, 7, unknown="open") is None
    assert earnings_blackout_reason(result, "AAPL", NOW, 7) is None
    assert earnings_note(result, NOW, 7) == "Unverified — earnings calendar unavailable"


async def test_no_sources_gives_unverified():
    result = await resolve_earnings("AAPL", NOW, 7, live=None, captures=None)

    assert result.event is None and not result.verified


async def test_capture_read_error_is_treated_as_unavailable(caplog):
    captures = _Captures(RuntimeError("secret-detail"))

    with caplog.at_level(logging.WARNING):
        result = await resolve_earnings("AAPL", NOW, 7, live=_Live(RuntimeError("live-secret")), captures=captures)

    assert not result.verified and result.event is None
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "RuntimeError" in text
    assert "secret-detail" not in text and "live-secret" not in text


def test_closed_policy_with_zero_blackout_days_never_refuses():
    lookup = _live_lookup(event=None, verified=False)

    assert earnings_blackout_reason(lookup, "AAPL", NOW, 0, unknown="closed") is None


class _RecordingCaptures:
    def __init__(self, reports=None):
        self.calls = 0
        self.reports = reports

    async def reports_between(self, start, end, *, today):
        self.calls += 1
        return self.reports


class _FixedLive:
    def __init__(self, lookup):
        self.lookup = lookup

    async def next_earnings(self, symbol, now, horizon_days):
        return self.lookup


def test_capture_sourced_refusal_carries_the_capture_suffix():
    event = EarningsEvent(symbol="AAPL", date=TODAY + timedelta(days=2), timing=EarningsTiming.AFTER_HOURS)
    lookup = EarningsLookup(
        event=event, verified=True, horizon_end=TODAY + timedelta(days=7), source="capture", capture_date=TODAY
    )
    reason = earnings_blackout_reason(lookup, "AAPL", NOW, 7)
    assert reason is not None and reason.endswith("(calendar capture 2026-09-23)")
    live = EarningsLookup(event=event, verified=True, horizon_end=TODAY + timedelta(days=7))
    assert "calendar capture" not in earnings_blackout_reason(live, "AAPL", NOW, 7)


async def test_resolution_logs_one_info_for_capture_or_unverified_only(caplog):
    caplog.set_level(logging.INFO)
    reports = CapturedReports(capture_date=TODAY, rows={TODAY: ()})
    unverified = EarningsLookup(event=None, verified=False, horizon_end=TODAY)

    await resolve_earnings("AAPL", NOW, 0, live=_FixedLive(unverified), captures=_RecordingCaptures(reports))
    await resolve_earnings("AAPL", NOW, 0, live=_FixedLive(unverified), captures=None)
    verified = EarningsLookup(event=None, verified=True, horizon_end=TODAY)
    await resolve_earnings("AAPL", NOW, 0, live=_FixedLive(verified), captures=_RecordingCaptures(reports))

    records = [r.__dict__ for r in caplog.records if r.__dict__.get("event") == "earnings_lookup_resolved"]
    assert [(r["contract"], r["source"], r["capture_date"], r["verified"]) for r in records] == [
        ("AAPL", "capture", "2026-09-23", True),
        ("AAPL", "live", None, False),
    ]


async def test_verified_live_report_blocks_without_reading_the_capture():
    event = EarningsEvent(symbol="AAPL", date=TODAY + timedelta(days=2), timing=EarningsTiming.AFTER_HOURS)
    live = EarningsLookup(event=event, verified=True, horizon_end=TODAY + timedelta(days=7))
    captures = _RecordingCaptures(CapturedReports(capture_date=TODAY, rows={}))
    result = await resolve_earnings("AAPL", NOW, 7, live=_FixedLive(live), captures=captures)
    assert result is live and captures.calls == 0
