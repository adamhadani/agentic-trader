"""The daily Nasdaq forward earnings-calendar capture: schedule, all-or-nothing journal, freshness.

Fake HTTP only (``httpx.MockTransport``) keyed on the ``date`` query parameter.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import types
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from agentic_trader.config import AppConfig
from agentic_trader.data import earnings_capture
from agentic_trader.data.earnings_capture import (
    CAPTURE_MAX_AGE_SECONDS,
    CAPTURE_STREAM,
    COLUMNS,
    CaptureIncomplete,
    EarningsCaptureStore,
    EarningsCaptureWorker,
    capture_calendar,
    capture_targets,
    run_earnings_capture_worker,
)
from agentic_trader.diagnostics.readiness import EARNINGS_CAPTURE_MAX_AGE_SECONDS
from agentic_trader.execution.durable import EventKind
from agentic_trader.market.session import ET_TZ


FRIDAY = date(2026, 11, 6)
ROW = {
    "symbol": "pbr",
    "name": "Petrobras",
    "marketCap": "$126,374,027,000",
    "fiscalQuarterEnding": "Sep/2026",
    "epsForecast": "$1.38",
    "noOfEsts": "2",
    "lastYearRptDt": "11/06/2025",
    "lastYearEPS": "$0.82",
    "time": "time-after-hours",
}


def page(rows=(), as_of="Fri, Nov 6, 2026") -> bytes:
    return json.dumps({"data": {"asOf": as_of, "rows": list(rows)}, "status": {"rCode": 200}}).encode()


def et(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET_TZ).astimezone(UTC)


class FakeNasdaq:
    def __init__(self, default=None, responses=None):
        self.default = default if default is not None else (lambda day: httpx.Response(200, content=page([ROW])))
        self.responses: dict[str, list] = {k: list(v) for k, v in (responses or {}).items()}
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        day = request.url.params["date"]
        self.requests.append(day)
        if self.responses.get(day):
            return self.responses[day].pop(0)
        return self.default(day)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


async def _no_pace() -> None:
    return None


async def _no_sleep(_seconds: float) -> None:
    return None


@pytest.fixture
def store(temp_db, tmp_path):
    return EarningsCaptureStore(temp_db.workflows, tmp_path / "private" / "earnings")


def worker_for(fake, store, clock_time):
    config = AppConfig(execution_mode="alpaca", alpaca_paper=True)
    return EarningsCaptureWorker(fake.client(), store, config, clock=lambda: clock_time, pace=_no_pace, sleep=_no_sleep)


def test_targets_are_weekdays_inclusive():
    targets = capture_targets(FRIDAY, 28)
    assert targets[0] == FRIDAY
    assert targets[-1] == date(2026, 12, 4)
    assert len(targets) == 21
    assert all(day.weekday() < 5 for day in targets)


async def test_run_once_statuses(store):
    fake = FakeNasdaq()
    saturday = FRIDAY + timedelta(days=1)
    worker = worker_for(fake, store, et(FRIDAY, 6, 1))
    assert await worker.run_once(et(saturday, 12)) == "weekend"
    assert await worker.run_once(et(FRIDAY, 10, 31)) == "scan_window"
    assert await worker.run_once(et(FRIDAY, 5, 59)) == "waiting"
    assert fake.requests == []
    assert await worker.run_once(et(FRIDAY, 6, 1)) == "captured"
    assert len(fake.requests) == 21
    assert await worker.run_once(et(FRIDAY, 7)) == "present"
    again = worker_for(fake, store, et(FRIDAY, 8))
    assert await again.run_once(et(FRIDAY, 8)) == "present"
    assert len(fake.requests) == 21


async def test_payload_shape(store):
    fake = FakeNasdaq()
    worker = worker_for(fake, store, et(FRIDAY, 6, 1))
    await worker.run_once()
    (event,) = await store.workflows.events(CAPTURE_STREAM)
    assert event["kind"] == EventKind.EARNINGS_CALENDAR_CAPTURE
    payload = event["payload"]
    assert payload["capture_date"] == "2026-11-06"
    assert payload["source"] == "nasdaq-calendar"
    assert payload["horizon_days"] == 28
    assert payload["columns"] == list(COLUMNS)
    assert payload["started_at"] and payload["completed_at"]
    assert [p["date"] for p in payload["pages"]] == [d.isoformat() for d in capture_targets(FRIDAY, 28)]
    first = payload["pages"][0]
    assert first["as_of"] == "Fri, Nov 6, 2026"
    assert first["rows"] == 1 and first["skipped"] == 0 and first["fetched_at"]
    assert first["body_sha256"] == hashlib.sha256(page([ROW])).hexdigest()
    assert payload["rows"]["2026-11-06"] == [
        ["PBR", "after_hours", "Sep/2026", 1.38, 2, "2025-11-06", 0.82, 126374027000.0]
    ]
    saved = store.directory / "2026-11-06" / "2026-11-06.json.gz"
    assert gzip.decompress(saved.read_bytes()) == page([ROW])


async def test_null_data_is_accepted_empty_day(store):
    body = json.dumps({"data": None, "status": {"rCode": 200}}).encode()
    fake = FakeNasdaq(default=lambda day: httpx.Response(200, content=body))
    await worker_for(fake, store, et(FRIDAY, 6, 1)).run_once()
    (event,) = await store.workflows.events(CAPTURE_STREAM)
    first = event["payload"]["pages"][0]
    assert first["rows"] == 0 and first["as_of"] is None
    assert event["payload"]["rows"]["2026-11-06"] == []


async def _failing_pass(store, responses, *, targets=2):
    fake = FakeNasdaq(responses=responses)
    days = capture_targets(FRIDAY, 28)[:targets]
    async with fake.client() as client:
        with pytest.raises(CaptureIncomplete) as caught:
            await capture_calendar(
                client,
                FRIDAY,
                days,
                horizon_days=28,
                clock=lambda: et(FRIDAY, 6, 1),
                pace=_no_pace,
                sleep=_no_sleep,
                stop=lambda: False,
            )
    assert await store.workflows.events(CAPTURE_STREAM) == []
    assert not store.directory.exists()
    return fake, caught.value


async def test_payload_horizon_is_the_argument(store):
    fake = FakeNasdaq()
    days = capture_targets(FRIDAY, 28)[:2]
    async with fake.client() as client:
        payload, _ = await capture_calendar(
            client,
            FRIDAY,
            days,
            horizon_days=21,
            clock=lambda: et(FRIDAY, 6, 1),
            pace=_no_pace,
            sleep=_no_sleep,
            stop=lambda: False,
        )
    assert payload is not None and payload["horizon_days"] == 21


async def test_default_pacer_spaces_requests(monkeypatch):
    now = [100.0]
    waits: list[float] = []

    async def record_sleep(seconds: float) -> None:
        waits.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(earnings_capture, "monotonic_time", types.SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(earnings_capture.asyncio, "sleep", record_sleep)
    pace = earnings_capture._spacer()
    await pace()
    assert waits == []
    now[0] += 0.25
    await pace()
    assert waits == [pytest.approx(earnings_capture.REQUEST_INTERVAL_SECONDS - 0.25)]


async def test_retry_once_on_503_then_success(store):
    fake = FakeNasdaq(responses={"2026-11-06": [httpx.Response(503)]})
    days = capture_targets(FRIDAY, 28)[:2]
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async with fake.client() as client:
        payload, bodies = await capture_calendar(
            client,
            FRIDAY,
            days,
            horizon_days=28,
            clock=lambda: et(FRIDAY, 6, 1),
            pace=_no_pace,
            sleep=sleep,
            stop=lambda: False,
        )
    assert payload is not None and set(bodies) == set(days)
    assert fake.requests == ["2026-11-06", "2026-11-06", "2026-11-09"]
    assert sleeps == [5.0]


async def test_429_twice_fails_and_journals_nothing(store):
    fake, error = await _failing_pass(store, {"2026-11-06": [httpx.Response(429), httpx.Response(429)]})
    assert error.target == FRIDAY and error.reason == "HTTP 429"
    assert fake.requests == ["2026-11-06", "2026-11-06"]


async def test_404_fails_without_retry(store):
    fake, error = await _failing_pass(store, {"2026-11-06": [httpx.Response(404)]})
    assert error.reason == "HTTP 404"
    assert fake.requests == ["2026-11-06"]


async def test_non_json_body_fails(store):
    _, error = await _failing_pass(store, {"2026-11-06": [httpx.Response(200, content=b"<html>")]})
    assert error.reason == "body is not JSON"


async def test_bad_rcode_and_non_object_fail(store):
    body = json.dumps({"data": None, "status": {"rCode": 400}}).encode()
    _, error = await _failing_pass(store, {"2026-11-06": [httpx.Response(200, content=body)]})
    assert error.reason == "status.rCode 400"
    _, error = await _failing_pass(store, {"2026-11-06": [httpx.Response(200, content=b"[]")]})
    assert error.reason == "body is not a JSON object"


async def test_transport_error_reason_is_the_type_name(store):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret detail")

    async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as client:
        with pytest.raises(CaptureIncomplete) as caught:
            await capture_calendar(
                client,
                FRIDAY,
                [FRIDAY],
                horizon_days=28,
                clock=lambda: et(FRIDAY, 6),
                pace=_no_pace,
                sleep=_no_sleep,
                stop=lambda: False,
            )
    assert caught.value.reason == "ConnectError"
    assert "secret" not in str(caught.value)


async def test_scan_window_mid_pass_journals_nothing(store):
    fake = FakeNasdaq()
    worker = worker_for(fake, store, et(FRIDAY, 6, 1))
    clock_time = [et(FRIDAY, 6, 1)]
    worker._clock = lambda: clock_time[0]

    original = fake.handler

    def handler(request: httpx.Request) -> httpx.Response:
        response = original(request)
        if len(fake.requests) == 3:
            clock_time[0] = et(FRIDAY, 10, 31)
        return response

    worker.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await worker.run_once(et(FRIDAY, 6, 1)) == "scan_window"
    assert len(fake.requests) == 3
    assert await store.workflows.events(CAPTURE_STREAM) == []


async def test_failed_pass_then_success_records_once(store):
    fake = FakeNasdaq(responses={"2026-11-10": [httpx.Response(404)]})
    worker = worker_for(fake, store, et(FRIDAY, 6, 1))
    with pytest.raises(CaptureIncomplete):
        await worker.run_once()
    assert await store.workflows.events(CAPTURE_STREAM) == []
    assert not store.directory.exists() or not [p for p in store.directory.rglob("*") if p.is_file()]
    assert await worker.run_once() == "captured"
    assert await worker.run_once() == "present"
    assert len(await store.workflows.events(CAPTURE_STREAM)) == 1


async def _seed(store, capture_date: date):
    payload = {
        "capture_date": capture_date.isoformat(),
        "pages": [{"date": capture_date.isoformat()}],
        "rows": {capture_date.isoformat(): [["A"], ["B"]]},
    }
    assert await store.record(payload, {}) is True


async def test_freshness(store):
    fake = FakeNasdaq()
    worker = worker_for(fake, store, et(FRIDAY, 5))
    assert await worker.freshness(et(FRIDAY, 5), "waiting") == (True, "not_due; no capture yet")
    worker._pass_ran = True
    assert await worker.freshness(et(FRIDAY, 7), "present") == (False, "no capture yet")
    await _seed(store, FRIDAY)
    ready, detail = await worker.freshness(et(FRIDAY + timedelta(days=4), 7), "waiting")
    assert ready and detail == "latest capture 2026-11-06 (1 pages, 2 rows)"
    ready, detail = await worker.freshness(et(FRIDAY + timedelta(days=5), 7), "waiting")
    assert not ready and detail == "stale: latest capture 2026-11-06 (5 days)"


async def _poll_failing(store, caplog=None):
    fake = FakeNasdaq(default=lambda day: httpx.Response(503))
    observed: list[tuple[bool, str]] = []
    shutdown = asyncio.Event()

    async def observe(ok: bool, detail: str) -> None:
        observed.append((ok, detail))
        shutdown.set()

    now = et(FRIDAY, 7)
    await run_earnings_capture_worker(
        AppConfig(execution_mode="alpaca", alpaca_paper=True),
        store,
        observe,
        shutdown,
        transport=httpx.MockTransport(fake.handler),
        clock=lambda: now,
        pace=_no_pace,
        sleep=_no_sleep,
    )
    return observed


async def test_failed_poll_observes_freshness(store, caplog):
    await _seed(store, FRIDAY - timedelta(days=1))
    with caplog.at_level(logging.WARNING, logger="copilot"):
        observed = await _poll_failing(store)
    ready, detail = observed[0]
    assert ready and detail.startswith("failed: CaptureIncomplete")
    (record,) = [r for r in caplog.records if r.__dict__.get("event") == "earnings_capture_failed"]
    assert record.__dict__["reason"] == "CaptureIncomplete"
    assert record.__dict__["target"] == "2026-11-06"
    assert record.__dict__["detail"] == "HTTP 503"


async def test_other_exception_logs_type_name_only(store, caplog, monkeypatch):
    async def boom(capture_date):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(store, "recorded", boom)
    with caplog.at_level(logging.WARNING, logger="copilot"):
        await _poll_failing(store)
    (record,) = [r for r in caplog.records if r.__dict__.get("event") == "earnings_capture_failed"][:1]
    assert record.__dict__["reason"] == "RuntimeError"
    assert "target" not in record.__dict__ and "detail" not in record.__dict__
    assert "secret" not in repr(record.__dict__) and "secret" not in record.getMessage()


async def test_failed_poll_without_capture_is_not_ready(store):
    observed = await _poll_failing(store)
    assert observed[0][0] is False
    assert observed[0][1].startswith("failed: CaptureIncomplete")


def test_max_age_constants_agree():
    assert EARNINGS_CAPTURE_MAX_AGE_SECONDS == CAPTURE_MAX_AGE_SECONDS == 4 * 86400
