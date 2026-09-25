import json
from datetime import UTC, date, datetime

import httpx
import pytest

from agentic_trader.agent.earnings import CalendarRow, EarningsTiming
from agentic_trader.research.apriori.earnings_history import (
    CalendarPageStore,
    acquire_calendar,
    dedupe_rows,
    weekdays,
)


def _body(*symbols: str) -> bytes:
    rows = [{"symbol": s, "eps": "$1.10", "epsForecast": "$1.00", "noOfEsts": "4"} for s in symbols]
    return json.dumps({"data": {"rows": rows}}).encode()


class _Recorder:
    def __init__(self, responses):
        self.responses = responses  # date iso -> list of (status, body) served in order
        self.calls: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        day = request.url.params["date"]
        self.calls.append((request.method, day))
        status, body = self.responses[day].pop(0)
        return httpx.Response(status, content=body)


async def _no_sleep(_seconds: float) -> None:
    return None


def test_weekdays_skips_weekends():
    assert weekdays(date(2021, 4, 29), date(2021, 5, 4)) == [
        date(2021, 4, 29),
        date(2021, 4, 30),
        date(2021, 5, 3),
        date(2021, 5, 4),
    ]


async def test_acquires_parses_saves_and_reuses_pages(tmp_path):
    store = CalendarPageStore(tmp_path / "nasdaq")
    recorder = _Recorder({"2021-04-29": [(200, _body("AAA", "BBB"))], "2021-04-30": [(200, _body("CCC"))]})
    days = [date(2021, 4, 29), date(2021, 4, 30)]
    first = await acquire_calendar(
        days, store, interval_seconds=1.0, transport=httpx.MockTransport(recorder), sleep=_no_sleep
    )
    assert [row.symbol for row in first.rows] == ["AAA", "BBB", "CCC"]
    assert (first.requested_dates, first.fetched_dates, first.reused_dates, first.failed_dates) == (2, 2, 0, ())
    assert all(method == "GET" for method, _ in recorder.calls)
    for path in (tmp_path / "nasdaq").iterdir():
        assert path.stat().st_mode & 0o777 == 0o600

    again = await acquire_calendar(
        days, store, interval_seconds=1.0, transport=httpx.MockTransport(recorder), sleep=_no_sleep
    )
    assert len(recorder.calls) == 2  # nothing re-fetched
    assert (again.fetched_dates, again.reused_dates) == (0, 2)
    assert [row.symbol for row in again.rows] == ["AAA", "BBB", "CCC"]


async def test_retries_then_records_a_failed_date_without_saving_it(tmp_path):
    store = CalendarPageStore(tmp_path / "nasdaq")
    recorder = _Recorder({"2021-04-29": [(503, b""), (200, b"not json"), (500, b"")]})
    result = await acquire_calendar(
        [date(2021, 4, 29)],
        store,
        interval_seconds=1.0,
        transport=httpx.MockTransport(recorder),
        sleep=_no_sleep,
        retry_delays=(0.0, 0.0),
    )
    assert len(recorder.calls) == 3
    assert result.failed_dates == (date(2021, 4, 29),) and result.failed_fraction == 1.0
    assert store.load(date(2021, 4, 29)) is None


async def test_a_page_that_no_longer_matches_its_hash_is_refused(tmp_path):
    store = CalendarPageStore(tmp_path / "nasdaq")
    day = date(2021, 4, 29)
    store.save(day, _body("AAA"), datetime(2026, 9, 25, tzinfo=UTC))
    page = next(p for p in (tmp_path / "nasdaq").iterdir() if p.name == "2021-04-29.json")
    page.chmod(0o600)
    page.write_bytes(_body("TAMPERED"))
    with pytest.raises(ValueError, match="SHA-256"):
        store.load(day)


async def test_paces_every_request(tmp_path):
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    recorder = _Recorder({"2021-04-29": [(200, _body("AAA"))], "2021-04-30": [(200, _body("BBB"))]})
    await acquire_calendar(
        [date(2021, 4, 29), date(2021, 4, 30)],
        CalendarPageStore(tmp_path / "nasdaq"),
        interval_seconds=1.0,
        transport=httpx.MockTransport(recorder),
        sleep=sleep,
    )
    assert slept == [1.0, 1.0]


def test_dedupe_keeps_the_first_row_per_symbol_and_date():
    def row(symbol, eps):
        return CalendarRow(symbol, date(2021, 4, 29), EarningsTiming.UNSPECIFIED, eps, 1.0, None, 3, None)

    rows, duplicates = dedupe_rows([row("AAA", 1.0), row("AAA", 2.0), row("BBB", 1.0)])
    assert [(r.symbol, r.eps) for r in rows] == [("AAA", 1.0), ("BBB", 1.0)] and duplicates == 1
