"""The nightly SEC Form 4 capture, its journal, the watch history seed and the insider_watch config.

Fake HTTP only (``httpx.MockTransport``): documents come from ``tests/fixtures/edgar``; indexes are built here.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import logging
import stat
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from agentic_trader.config import InsiderWatchConfig
from agentic_trader.data import insider_capture
from agentic_trader.data.corporate_actions import CorporateAction, CorporateActionKind
from agentic_trader.data.insider_capture import (
    CAPTURE_STREAM,
    CaptureScope,
    CaptureStatus,
    InsiderArchiveMismatch,
    InsiderCaptureStore,
    InsiderCaptureWorker,
    WatchCohort,
    capture_filing_dates,
    capture_scope,
    daily_index_url,
    edgar_business_day,
    load_watch_cohort,
    load_watch_history,
    run_insider_capture_worker,
)
from agentic_trader.diagnostics.readiness import INSIDER_CAPTURE_MAX_AGE_SECONDS
from agentic_trader.execution.durable import EventKind
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.apriori.catalog import load_insider_entry
from agentic_trader.research.insider_filings import InsiderFilingStore, files_sha256, parse_quarter, quarters_between
from agentic_trader.research.pooled.campaign import InsiderFilingInputs
from agentic_trader.research.pooled.entry import REPO_ROOT
from tests.research.insider_fixtures import trade, trades_archive


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "edgar"
HEADER = "\n".join((FIXTURES / "form.20260915.idx").read_text().splitlines()[:11])
UA = "Desk Operator ops-contact@example.invalid"
CROX = ("0001247154-26-000005", 1334036, "CROCS, INC.")  # one open-market officer purchase, $99,226.20
LPG = ("0000919574-26-006250", 1596993, "Dorian LPG Ltd.")  # one open-market officer sale, $649,585.50
NON_INSIDER = ("0001271373-26-000011", 851968, "MOHAWK INDUSTRIES INC")  # an owner neither officer nor director
SCOPE = CaptureScope(
    cik_to_symbols={CROX[1]: ("CROX",), LPG[1]: ("LPG",), NON_INSIDER[1]: ("MHK",)},
    owner_roles=("Officer", "Director"),
    exclude_10b5_1=False,
)
SCAN_TIMES = ["10:35", "14:35"]


def _path(filing: tuple[str, int, str]) -> str:
    return f"edgar/data/{filing[1]}/{filing[0]}.txt"


def _line(form: str, company: str, cik: int, day: date, path: str) -> str:
    return f"{form:<17}{company:<62}{cik:<12}{day:%Y%m%d}    {path}"


def index_body(day: date, *filings: tuple[str, int, str], extra: tuple[str, ...] = ()) -> bytes:
    lines = [HEADER, *(_line("4", f[2], f[1], day, _path(f)) for f in filings), *extra]
    return ("\n".join(lines) + "\n").encode()


class FakeSec:
    """Daily indexes by date and documents by index path; anything else is a 404."""

    def __init__(self, indexes=None, documents=None, failures=None, late=None):
        self.indexes: dict[date, bytes] = dict(indexes or {})
        self.documents: dict[str, bytes] = dict(documents or {})
        self.failures: dict[str, int] = dict(failures or {})
        self.late: dict[date, int] = dict(late or {})  # an index answers 404 this many times first
        self.requests: list[tuple[str, str | None]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append((url, request.headers.get("user-agent")))
        if url in self.failures:
            return httpx.Response(self.failures[url])
        for day, misses in self.late.items():
            if url == daily_index_url(day) and misses > 0:
                self.late[day] = misses - 1
                return httpx.Response(404)
        for day, body in self.indexes.items():
            if url == daily_index_url(day):
                return httpx.Response(200, content=body)
        prefix = "https://www.sec.gov/Archives/"
        if url.startswith(prefix) and url.removeprefix(prefix) in self.documents:
            return httpx.Response(200, content=self.documents[url.removeprefix(prefix)])
        return httpx.Response(404)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def index_days(self) -> list[date]:
        days = []
        for url, _ in self.requests:
            if "/daily-index/" in url:
                stamp = url.rsplit(".", 2)[-2]
                days.append(date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:])))
        return days


def documents(*filings: tuple[str, int, str]) -> dict[str, bytes]:
    return {_path(f): (FIXTURES / f"{f[0]}.txt").read_bytes() for f in filings}


async def _no_pace() -> None:
    return None


@pytest.fixture
def capture_store(temp_db, tmp_path):
    return InsiderCaptureStore(temp_db.workflows, tmp_path / "private" / "insider")


async def _capture(sec, store, dates, **kwargs):
    async with sec.client() as client:
        return await capture_filing_dates(client, store, dates, scope=SCOPE, user_agent=UA, pace=_no_pace, **kwargs)


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


# --- the EDGAR calendar and URLs ----------------------------------------------------------------


def test_business_days_skip_weekends_and_federal_holidays_but_not_good_friday():
    assert edgar_business_day(date(2026, 10, 9)) is True  # Friday
    assert edgar_business_day(date(2026, 10, 10)) is False  # Saturday
    assert edgar_business_day(date(2026, 10, 12)) is False  # Columbus Day: NYSE open, EDGAR closed
    assert edgar_business_day(date(2026, 11, 11)) is False  # Veterans Day
    assert edgar_business_day(date(2026, 4, 3)) is True  # Good Friday: NYSE closed, EDGAR open


def test_daily_index_url_names_the_quarter_directory():
    assert daily_index_url(date(2026, 10, 1)) == (
        "https://www.sec.gov/Archives/edgar/daily-index/2026/QTR4/form.20261001.idx"
    )
    assert daily_index_url(date(2026, 3, 31)).endswith("/2026/QTR1/form.20260331.idx")


# --- capture_filing_dates -----------------------------------------------------------------------


async def test_one_event_per_filing_date_with_transactions_counts_and_private_documents(capture_store, temp_db):
    oct1, oct2 = date(2026, 10, 1), date(2026, 10, 2)
    other = _line("4", "NOT IN COHORT", 999, oct2, "edgar/data/999/0000000999-26-000001.txt")
    tenk = _line("10-K", "CROCS, INC.", CROX[1], oct2, "edgar/data/1334036/0001334036-26-000099.txt")
    sec = FakeSec(
        indexes={
            oct1: index_body(oct1, CROX),
            oct2: index_body(oct2, LPG, NON_INSIDER, extra=(other, tenk)),
            date(2026, 10, 5): index_body(date(2026, 10, 5)),
        },
        documents=documents(CROX, LPG, NON_INSIDER),
    )
    result = await _capture(sec, capture_store, _days(oct1, date(2026, 10, 5)))

    assert result.recorded == tuple(_days(oct1, date(2026, 10, 5)))
    assert result.captured == (oct1, oct2, date(2026, 10, 5)) and result.pending is None and not result.stopped
    assert date(2026, 10, 3) not in sec.index_days() and date(2026, 10, 4) not in sec.index_days()
    assert {agent for _, agent in sec.requests} == {UA}

    events = await temp_db.workflows.events(CAPTURE_STREAM)
    assert [e["kind"] for e in events] == [EventKind.INSIDER_FORM4_CAPTURE] * 5
    by_day = {e["payload"]["filing_date"]: e["payload"] for e in events}
    first = by_day["2026-10-01"]
    assert first["status"] == CaptureStatus.CAPTURED and first["form4_rows"] == 1 and first["cohort_rows"] == 1
    assert first["index_sha256"] == hashlib.sha256(sec.indexes[oct1]).hexdigest()
    assert first["transactions"] == [
        {
            "symbol": "CROX",
            "filing_date": "2026-10-01",
            "trans_date": "2026-09-15",
            "code": "P",
            "dollars": pytest.approx(99226.2),
            "owner_cik": 1247154,
        }
    ]
    [doc] = first["documents"]
    assert doc["path"] == _path(CROX) and doc["transactions"] == 1 and doc["error"] is None
    assert doc["sha256"] == hashlib.sha256(documents(CROX)[_path(CROX)]).hexdigest()
    second = by_day["2026-10-02"]
    assert second["form4_rows"] == 3 and second["cohort_rows"] == 2  # the 10-K and the foreign CIK are not read
    assert [(t["symbol"], t["code"]) for t in second["transactions"]] == [("LPG", "S")]
    assert second["drops"]["owner_role"] == 1  # the reader's drop rules, counted per row
    weekend = by_day["2026-10-03"]
    assert weekend["status"] == CaptureStatus.NO_INDEX and weekend["reason"] == "non_business_day"
    assert weekend["transactions"] == [] and weekend["index_sha256"] is None
    assert by_day["2026-10-05"]["cohort_rows"] == 0 and by_day["2026-10-05"]["status"] == CaptureStatus.CAPTURED

    folder = capture_store.directory / "2026-10-01"
    assert (folder / "form.20261001.idx").read_bytes() == sec.indexes[oct1]
    saved = folder / f"{CROX[0]}.txt"
    assert saved.read_bytes() == documents(CROX)[_path(CROX)]
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert UA not in json.dumps([e["payload"] for e in events])
    assert not any(UA.encode() in p.read_bytes() for p in capture_store.directory.rglob("*") if p.is_file())


async def test_an_unpublished_business_day_is_pending_and_nothing_after_it_is_recorded(capture_store):
    mon, tue = date(2026, 10, 5), date(2026, 10, 6)
    sec = FakeSec(indexes={}, documents={})
    result = await _capture(sec, capture_store, [mon, tue])
    assert result.pending == mon and result.recorded == () and result.captured == ()
    assert await capture_store.latest_recorded_date() is None

    sec.indexes[mon] = index_body(mon)  # published later: the retry captures it
    result = await _capture(sec, capture_store, [mon, tue])
    assert result.recorded == (mon,) and result.pending == tue


async def test_a_closed_business_day_is_inferred_from_a_later_index_after_one_more_request(capture_store):
    wed, thu = date(2026, 10, 14), date(2026, 10, 15)
    sec = FakeSec(indexes={thu: index_body(thu, CROX)}, documents=documents(CROX))
    result = await _capture(sec, capture_store, [date(2026, 10, 12), date(2026, 10, 13), wed, thu])
    assert date(2026, 10, 12) not in sec.index_days()  # a federal holiday is never requested
    assert sec.index_days().count(date(2026, 10, 13)) == 2 and sec.index_days().count(wed) == 2  # asked again
    assert result.pending is None and result.recorded == tuple(_days(date(2026, 10, 12), thu))
    captures = {c["filing_date"]: c for c in await capture_store.captures()}
    assert captures["2026-10-12"]["reason"] == "non_business_day"
    assert captures["2026-10-13"]["reason"] == captures["2026-10-14"]["reason"] == "closed_inferred"
    assert captures["2026-10-15"]["status"] == CaptureStatus.CAPTURED
    assert await capture_store.latest_captured_date() == thu


async def test_a_held_day_published_by_the_second_request_is_captured_in_order(capture_store):
    tue, wed = date(2026, 10, 13), date(2026, 10, 14)
    sec = FakeSec(indexes={tue: index_body(tue, CROX), wed: index_body(wed)}, documents=documents(CROX), late={tue: 1})
    result = await _capture(sec, capture_store, [tue, wed])
    assert result.captured == (tue, wed) and result.recorded == (tue, wed)
    [first, second] = await capture_store.captures()
    assert first["filing_date"] == "2026-10-13" and first["status"] == CaptureStatus.CAPTURED
    assert [t["symbol"] for t in first["transactions"]] == ["CROX"] and second["filing_date"] == "2026-10-14"


async def test_every_request_has_a_total_deadline(capture_store, monkeypatch):
    monkeypatch.setattr(insider_capture, "REQUEST_DEADLINE_SECONDS", 0.05)

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as client:
        with pytest.raises(TimeoutError):
            await capture_filing_dates(
                client, capture_store, [date(2026, 10, 1)], scope=SCOPE, user_agent=UA, pace=_no_pace
            )
    assert await capture_store.captures() == []


async def test_a_failed_document_journals_nothing_for_its_date_and_keeps_earlier_dates(capture_store):
    oct1, oct2 = date(2026, 10, 1), date(2026, 10, 2)
    sec = FakeSec(
        indexes={oct1: index_body(oct1, CROX), oct2: index_body(oct2, LPG)},
        documents=documents(CROX, LPG),
        failures={f"https://www.sec.gov/Archives/{_path(LPG)}": 503},
    )
    with pytest.raises(httpx.HTTPStatusError):
        await _capture(sec, capture_store, [oct1, oct2])
    assert [c["filing_date"] for c in await capture_store.captures()] == ["2026-10-01"]
    assert not (capture_store.directory / "2026-10-02").exists()


T0 = datetime(2026, 10, 6, 11, 0, tzinfo=UTC)  # 07:00 New York


async def test_a_first_document_404_holds_its_date_and_records_nothing(capture_store):
    oct1, oct2 = date(2026, 10, 1), date(2026, 10, 2)
    sec = FakeSec(indexes={oct1: index_body(oct1, CROX, LPG), oct2: index_body(oct2)}, documents=documents(LPG))
    held: dict = {}
    result = await _capture(sec, capture_store, [oct1, oct2], held_documents=held, clock=lambda: T0)
    assert result.pending == oct1 and result.recorded == () and result.captured == ()
    assert await capture_store.captures() == [] and held == {(oct1, _path(CROX)): T0}
    assert not (capture_store.directory / "2026-10-01").exists()


async def test_a_held_document_answering_200_on_the_next_pass_is_captured(capture_store):
    oct1 = date(2026, 10, 1)
    sec = FakeSec(indexes={oct1: index_body(oct1, CROX, LPG)}, documents=documents(LPG))
    held: dict = {}
    await _capture(sec, capture_store, [oct1], held_documents=held, clock=lambda: T0)
    sec.documents.update(documents(CROX))  # EDGAR finished publishing it
    later = T0 + timedelta(minutes=30)
    result = await _capture(sec, capture_store, [oct1], held_documents=held, clock=lambda: later)
    assert result.recorded == (oct1,) and held == {}
    [capture] = await capture_store.captures()
    assert capture["errors"] == {} and sorted(t["symbol"] for t in capture["transactions"]) == ["CROX", "LPG"]


async def test_a_second_document_404_thirty_minutes_later_records_document_not_found(capture_store):
    oct1, oct2 = date(2026, 10, 1), date(2026, 10, 2)
    sec = FakeSec(indexes={oct1: index_body(oct1, CROX, LPG), oct2: index_body(oct2)}, documents=documents(LPG))
    held: dict = {}
    await _capture(sec, capture_store, [oct1, oct2], held_documents=held, clock=lambda: T0)
    too_soon = T0 + timedelta(minutes=29)
    result = await _capture(sec, capture_store, [oct1, oct2], held_documents=held, clock=lambda: too_soon)
    assert result.pending == oct1 and await capture_store.captures() == []
    assert held == {(oct1, _path(CROX)): T0}  # the first 404's time is kept, not reset

    confirmed = T0 + timedelta(minutes=30)
    result = await _capture(sec, capture_store, [oct1, oct2], held_documents=held, clock=lambda: confirmed)
    assert result.recorded == (oct1, oct2) and result.pending is None and held == {}
    first = (await capture_store.captures())[0]
    assert first["errors"] == {"document_not_found": 1}
    by_path = {d["path"]: d for d in first["documents"]}
    assert by_path[_path(CROX)]["error"] == "document_not_found" and by_path[_path(CROX)]["sha256"] is None
    assert by_path[_path(LPG)]["error"] is None
    assert [t["symbol"] for t in first["transactions"]] == ["LPG"]
    assert not (capture_store.directory / "2026-10-01" / f"{CROX[0]}.txt").exists()


async def test_the_worker_remembers_a_held_document_between_passes(capture_store, app_config):
    oct5 = date(2026, 10, 5)
    sec = FakeSec(indexes={oct5: index_body(oct5, CROX)})
    now = [_et(date(2026, 10, 6), 7)]
    config = _config(app_config, backfill=oct5)
    async with sec.client() as client:
        worker = _worker(client, capture_store, config, clock=lambda: now[0])
        assert await worker.run_once(now[0]) == "pending"
        assert await capture_store.captures() == []
        now[0] += timedelta(seconds=insider_capture.RETRY_SECONDS)
        assert await worker.run_once(now[0]) == "captured"
    [capture] = await capture_store.captures()
    assert capture["errors"] == {"document_not_found": 1}


async def test_an_oversized_document_is_recorded_per_document_and_the_date_is_still_journaled(
    capture_store, monkeypatch
):
    oct1 = date(2026, 10, 1)
    index = index_body(oct1, CROX)
    monkeypatch.setattr(insider_capture, "MAX_DOCUMENT_BYTES", len(index) + 1)  # the index fits; the document not
    sec = FakeSec(indexes={oct1: index}, documents={_path(CROX): b"x" * (len(index) + 2)})
    result = await _capture(sec, capture_store, [oct1])
    assert result.recorded == (oct1,)
    [capture] = await capture_store.captures()
    assert capture["errors"] == {"document_too_large": 1}
    assert capture["documents"][0]["error"] == "document_too_large" and capture["transactions"] == []


async def test_an_oversized_index_still_raises(capture_store, monkeypatch):
    oct1 = date(2026, 10, 1)
    monkeypatch.setattr(insider_capture, "MAX_DOCUMENT_BYTES", 10)
    sec = FakeSec(indexes={oct1: index_body(oct1)})
    with pytest.raises(ValueError, match="budget"):
        await _capture(sec, capture_store, [oct1])
    assert await capture_store.captures() == []


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_a_throttled_or_failed_document_still_holds_its_date(capture_store, status):
    oct1 = date(2026, 10, 1)
    sec = FakeSec(
        indexes={oct1: index_body(oct1, CROX)},
        documents=documents(CROX),
        failures={f"https://www.sec.gov/Archives/{_path(CROX)}": status},
    )
    with pytest.raises(httpx.HTTPStatusError):
        await _capture(sec, capture_store, [oct1])
    assert await capture_store.captures() == []


async def test_a_document_transport_error_after_its_retry_still_holds_its_date(capture_store):
    oct1 = date(2026, 10, 1)
    index = index_body(oct1, CROX)

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == daily_index_url(oct1):
            return httpx.Response(200, content=index)
        raise httpx.ConnectError("refused")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.ConnectError):
            await capture_filing_dates(client, capture_store, [oct1], scope=SCOPE, user_agent=UA, pace=_no_pace)
    assert await capture_store.captures() == []


async def test_a_missing_document_marks_its_issuer_incomplete_in_the_history(tmp_path, capture_store):
    archive, inputs = _archive(tmp_path, {"2026q3": _trades()})
    oct1 = date(2026, 10, 1)
    sec = FakeSec(indexes={oct1: index_body(oct1, (CROX[0], CROX_CIK, CROX[2]))}, documents={})
    scope = CaptureScope({CROX_CIK: ("CROX",), LPG_CIK: ("LPG",)}, ROLES, False)
    held: dict = {}
    async with sec.client() as client:
        for at in (T0, T0 + timedelta(minutes=30)):  # the second 404, 30 minutes on, confirms it
            await capture_filing_dates(
                client, capture_store, [oct1], scope=scope, user_agent=UA, pace=_no_pace, held_documents=held,
                clock=lambda at=at: at,
            )  # fmt: skip
    history = await _seed(capture_store, archive, _cohort(inputs), _weekdays(date(2026, 7, 1), date(2026, 10, 5)))
    assert history.incomplete == {"CROX": (oct1,)} and history.latest_filing_date == oct1


async def test_a_server_error_on_the_index_raises_rather_than_reading_as_no_filings(capture_store):
    oct1 = date(2026, 10, 1)
    sec = FakeSec(failures={daily_index_url(oct1): 500})
    with pytest.raises(httpx.HTTPStatusError):
        await _capture(sec, capture_store, [oct1])
    assert await capture_store.captures() == []


async def test_a_malformed_document_is_recorded_by_type_name(capture_store):
    oct1 = date(2026, 10, 1)
    sec = FakeSec(indexes={oct1: index_body(oct1, CROX)}, documents={_path(CROX): b"<SEC-DOCUMENT>no xml"})
    await _capture(sec, capture_store, [oct1])
    [capture] = await capture_store.captures()
    assert capture["errors"] == {"ValueError": 1} and capture["documents"][0]["error"] == "ValueError"
    assert capture["transactions"] == []


async def test_an_index_path_outside_the_archive_layout_fails_closed(capture_store):
    oct1 = date(2026, 10, 1)
    bad = _line("4", "CROCS, INC.", CROX[1], oct1, "edgar/data/1334036/../../etc/passwd.txt")
    sec = FakeSec(indexes={oct1: index_body(oct1, extra=(bad,))})
    with pytest.raises(ValueError, match="layout"):
        await _capture(sec, capture_store, [oct1])
    assert await capture_store.captures() == []


async def test_the_journal_is_idempotent_per_filing_date(capture_store):
    payload = {"filing_date": "2026-10-03", "status": "no_index", "reason": "non_business_day"}
    assert await capture_store.record(payload, {}) is True
    assert await capture_store.record({**payload, "reason": "other"}, {}) is False
    [only] = await capture_store.captures()
    assert only["reason"] == "non_business_day"


async def test_a_stop_between_dates_keeps_completed_dates_and_reports_stopped(capture_store):
    oct1, oct2 = date(2026, 10, 1), date(2026, 10, 2)
    sec = FakeSec(indexes={oct1: index_body(oct1), oct2: index_body(oct2)})
    calls = iter([False, True])
    result = await _capture(sec, capture_store, [oct1, oct2], stop=lambda: next(calls))
    assert result.stopped and result.recorded == (oct1,)


# --- the worker ---------------------------------------------------------------------------------


def _config(app_config, backfill=date(2026, 10, 1)):
    return app_config.model_copy(
        update={
            "insider_watch": InsiderWatchConfig(enabled=True, backfill_from=backfill),
            "scheduler": app_config.scheduler.model_copy(update={"suggestion_scan_times_et": SCAN_TIMES}),
        }
    )


def _et(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET_TZ).astimezone(UTC)


def _quiet_sec(*days: date) -> FakeSec:
    return FakeSec(indexes={d: index_body(d) for d in days if edgar_business_day(d)})


async def _scope() -> CaptureScope:
    return SCOPE


def _worker(client, store, config, clock=None):
    # The mid-run scan-window check reads the clock; pin it outside every window unless a test moves it.
    clock = clock or (lambda: _et(date(2026, 10, 6), 7))
    return InsiderCaptureWorker(client, store, config, scope=_scope, user_agent=UA, pace=_no_pace, clock=clock)


async def test_the_worker_backfills_once_per_new_york_date_and_a_restart_reads_the_journal(capture_store, app_config):
    config = _config(app_config)
    sec = _quiet_sec(*_days(date(2026, 10, 1), date(2026, 10, 5)))
    now = _et(date(2026, 10, 6), 7)
    async with sec.client() as client:
        worker = _worker(client, capture_store, config)
        assert await worker.run_once(now) == "captured"
        assert min(sec.index_days()) == date(2026, 10, 1)  # the backfill starts at backfill_from
        assert max(sec.index_days()) == date(2026, 10, 5)  # never today's (incomplete) index
        requested = len(sec.requests)
        assert await worker.run_once(now + timedelta(minutes=30)) == "present"
        restarted = _worker(client, capture_store, config)
        assert await restarted.run_once(now + timedelta(hours=1)) == "present"
    assert len(sec.requests) == requested
    assert await capture_store.latest_recorded_date() == date(2026, 10, 5)


async def test_the_worker_catches_up_from_the_last_recorded_date(capture_store, app_config):
    config = _config(app_config)
    sec = _quiet_sec(*_days(date(2026, 10, 1), date(2026, 10, 9)))
    async with sec.client() as client:
        worker = _worker(client, capture_store, config)
        assert await worker.run_once(_et(date(2026, 10, 3), 6, 5)) == "captured"
        sec.requests.clear()
        assert await worker.run_once(_et(date(2026, 10, 9), 8)) == "captured"
    assert sorted(set(sec.index_days())) == [date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7), date(2026, 10, 8)]
    recorded = [c["filing_date"] for c in await capture_store.captures()]
    assert recorded == [d.isoformat() for d in _days(date(2026, 10, 1), date(2026, 10, 8))]


async def test_the_worker_skips_scan_windows_and_waits_for_six_new_york(capture_store, app_config):
    config = _config(app_config)
    sec = _quiet_sec(*_days(date(2026, 10, 1), date(2026, 10, 5)))
    async with sec.client() as client:
        worker = _worker(client, capture_store, config)
        assert await worker.run_once(_et(date(2026, 10, 6), 10, 32)) == "scan_window"
        assert await worker.run_once(_et(date(2026, 10, 6), 14, 50)) == "scan_window"
        assert await worker.run_once(_et(date(2026, 10, 6), 5, 59)) == "waiting"
    assert sec.requests == []


async def test_a_scan_window_reached_mid_backfill_stops_between_dates(capture_store, app_config):
    config = _config(app_config)
    sec = _quiet_sec(*_days(date(2026, 10, 1), date(2026, 10, 5)))
    start = _et(date(2026, 10, 6), 10, 29)
    ticks = iter([start, start, _et(date(2026, 10, 6), 10, 31)])
    async with sec.client() as client:
        worker = _worker(client, capture_store, config, clock=lambda: next(ticks, _et(date(2026, 10, 6), 10, 31)))
        assert await worker.run_once() == "scan_window"
    assert [c["filing_date"] for c in await capture_store.captures()] == ["2026-10-01"]


async def test_a_pending_index_retries_on_the_next_poll(capture_store, app_config):
    config = _config(app_config)
    sec = _quiet_sec(*_days(date(2026, 10, 1), date(2026, 10, 2)))
    async with sec.client() as client:
        worker = _worker(client, capture_store, config)
        assert await worker.run_once(_et(date(2026, 10, 6), 6, 1)) == "pending"  # Monday 10-05 not yet published
        sec.indexes[date(2026, 10, 5)] = index_body(date(2026, 10, 5))
        assert await worker.run_once(_et(date(2026, 10, 6), 6, 31)) == "captured"


async def test_freshness_is_the_latest_captured_filing_date_within_four_days(capture_store, app_config):
    worker = _worker(None, capture_store, _config(app_config))
    ready, detail = await worker.freshness(_et(date(2026, 10, 6), 7), "captured")
    assert not ready and "no capture" in detail
    await capture_store.record({"filing_date": "2026-09-01", "status": "captured", "errors": {"ValueError": 5}}, {})
    await capture_store.record({"filing_date": "2026-10-02", "status": "captured", "errors": {"ValueError": 2}}, {})
    await capture_store.record({"filing_date": "2026-10-03", "status": "no_index"}, {})
    ready, detail = await worker.freshness(_et(date(2026, 10, 6), 7), "present")
    assert ready is True  # 4 days (a long weekend)
    assert detail.endswith("document errors (30 days): 2")  # the September errors are outside the window
    ready, detail = await worker.freshness(_et(date(2026, 10, 7), 7), "present")
    assert not ready and "2026-10-02" in detail  # a weekend no-index day does not refresh it


@pytest.mark.parametrize("status", ["waiting", "scan_window", "captured", "pending", "present"])
@pytest.mark.parametrize("has_capture", [False, True])
async def test_insider_freshness_not_due_before_first_pass(capture_store, app_config, status, has_capture):
    worker = _worker(None, capture_store, _config(app_config))
    if has_capture:
        await capture_store.record({"filing_date": "2026-10-05", "status": "captured", "errors": {}}, {})
    ready, detail = await worker.freshness(_et(date(2026, 10, 6), 7), status)
    if has_capture:
        assert ready is True and detail.startswith("latest filing date 2026-10-05")
    elif status in ("waiting", "scan_window"):
        assert (ready, detail) == (True, "not_due; no capture yet")
    else:
        assert (ready, detail) == (False, "no capture yet")


async def test_insider_freshness_stays_not_ready_after_a_pass_without_capture(capture_store, app_config):
    """Ruling R12: once a pass ran in this process and captured nothing, a later waiting or scan-window
    status does not flip readiness back to ``not_due``; a capture does."""
    sec = FakeSec()  # Monday 10-05's index is not yet published: the pass holds it and records nothing
    async with sec.client() as client:
        worker = _worker(client, capture_store, _config(app_config, backfill=date(2026, 10, 5)))
        assert await worker.freshness(_et(date(2026, 10, 6), 5), "waiting") == (True, "not_due; no capture yet")
        assert await worker.run_once(_et(date(2026, 10, 6), 6, 1)) == "pending"
        assert await capture_store.captures() == []
        for status, at in (("pending", (6, 2)), ("scan_window", (10, 31)), ("waiting", (5, 0))):
            day = date(2026, 10, 7) if status == "waiting" else date(2026, 10, 6)
            assert await worker.freshness(_et(day, *at), status) == (False, "no capture yet")
    await capture_store.record({"filing_date": "2026-10-05", "status": "captured", "errors": {}}, {})
    ready, detail = await worker.freshness(_et(date(2026, 10, 6), 10, 31), "scan_window")
    assert ready is True and detail.startswith("latest filing date 2026-10-05")


def test_readiness_duplicates_the_capture_age_limit():
    assert INSIDER_CAPTURE_MAX_AGE_SECONDS == insider_capture.CAPTURE_MAX_AGE_SECONDS == 4 * 86400


# --- the daemon loop: the user agent never leaves the request header ----------------------------


async def test_a_missing_user_agent_degrades_readiness_without_requests(capture_store, app_config):
    shutdown = asyncio.Event()
    seen: list[tuple[bool, str]] = []

    async def observe(ok: bool, detail: str) -> None:
        seen.append((ok, detail))
        shutdown.set()

    sec = FakeSec()
    await run_insider_capture_worker(
        _config(app_config),
        capture_store,
        observe,
        shutdown,
        environ={},
        scope=_scope,
        transport=httpx.MockTransport(sec.handler),
    )
    assert seen == [(False, "missing_user_agent")] and sec.requests == []


async def test_the_user_agent_is_never_logged_even_when_the_transport_echoes_it(capture_store, app_config, caplog):
    shutdown = asyncio.Event()
    seen: list[tuple[bool, str]] = []

    async def observe(ok: bool, detail: str) -> None:
        seen.append((ok, detail))
        shutdown.set()

    def echo(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"refused for {request.headers['user-agent']}", request=request)

    with caplog.at_level(logging.DEBUG):
        await run_insider_capture_worker(
            _config(app_config),
            capture_store,
            observe,
            shutdown,
            environ={"EDGAR_USER_AGENT": UA},
            scope=_scope,
            transport=httpx.MockTransport(echo),
            clock=lambda: _et(date(2026, 10, 6), 7),
            pace=_no_pace,
        )
    assert seen == [(False, "ConnectError")]
    assert UA not in caplog.text and "ops-contact" not in caplog.text
    assert any(getattr(r, "event", None) == "insider_capture_failed" for r in caplog.records)


async def test_a_successful_loop_observes_freshness_and_logs_no_user_agent(capture_store, app_config, caplog):
    shutdown = asyncio.Event()
    seen: list[tuple[bool, str]] = []

    async def observe(ok: bool, detail: str) -> None:
        seen.append((ok, detail))
        shutdown.set()

    sec = _quiet_sec(*_days(date(2026, 10, 1), date(2026, 10, 5)))
    with caplog.at_level(logging.DEBUG):
        await run_insider_capture_worker(
            _config(app_config),
            capture_store,
            observe,
            shutdown,
            environ={"EDGAR_USER_AGENT": UA},
            scope=_scope,
            transport=httpx.MockTransport(sec.handler),
            clock=lambda: _et(date(2026, 10, 6), 7),
            pace=_no_pace,
        )
    [(ok, detail)] = seen
    assert ok is True and detail.startswith("captured") and "2026-10-05" in detail
    assert {agent for _, agent in sec.requests} == {UA}
    assert UA not in caplog.text


# --- the history seed ---------------------------------------------------------------------------

ROLES = ("Officer", "Director")
CROX_CIK, LPG_CIK = 1334036, 1596993


def _trades(*rows) -> bytes:
    return trades_archive([trade(**row) for row in rows])


def _archive(tmp_path: Path, bodies: dict[str, bytes]) -> tuple[Path, InsiderFilingInputs]:
    directory = tmp_path / "archive"
    store = InsiderFilingStore(directory)
    at = datetime(2026, 10, 20, tzinfo=UTC)
    tickers = {
        "0": {"cik_str": CROX_CIK, "ticker": "CROX", "title": "Crocs"},
        "1": {"cik_str": LPG_CIK, "ticker": "LPG", "title": "Dorian"},
    }
    store.save_tickers(json.dumps(tickers).encode(), url="u", requested_at=at, received_at=at)
    for quarter, body in bodies.items():
        store.save(parse_quarter(quarter), body, url="u", requested_at=at, received_at=at)
    quarters = quarters_between(parse_quarter(min(bodies)), parse_quarter(max(bodies)))
    inputs = InsiderFilingInputs(
        source="sec-form345-datasets",
        files_sha256=files_sha256(store, quarters),
        quarters_from=str(quarters[0]),
        quarters_through=str(quarters[-1]),
        buy_window=63,
        net_window=126,
        owner_roles=ROLES,
        exclude_10b5_1=False,
        max_trade_rel=10,
    )
    return directory, inputs


def _cohort(inputs: InsiderFilingInputs) -> WatchCohort:
    return WatchCohort(
        filings=inputs,
        symbols=("CROX", "LPG"),
        cik_to_symbols={CROX_CIK: ("CROX",), LPG_CIK: ("LPG",)},
        symbols_without_cik=(),
        entry_sha256="e" * 64,
        protocol_sha256="p" * 64,
        cohort_sha256="c" * 64,
    )


LIVE_SCOPE = CaptureScope({CROX_CIK: ("CROX",), LPG_CIK: ("LPG",)}, ROLES, False).sha256


def _live(day: str, *transactions: dict, status: str = "captured", scope: str = LIVE_SCOPE, documents=()) -> dict:
    return {
        "filing_date": day,
        "status": status,
        "scope_sha256": scope,
        "transactions": list(transactions),
        "documents": list(documents),
    }


def _tx(symbol: str, filed: str, dollars: float, owner: int, traded: str | None = None) -> dict:
    return {
        "symbol": symbol,
        "filing_date": filed,
        "trans_date": traded or filed,
        "code": "P",
        "dollars": dollars,
        "owner_cik": owner,
    }


def _weekdays(start: date, end: date) -> tuple[date, ...]:
    return tuple(d for d in _days(start, end) if d.weekday() < 5)


def _bars(days, dollar_volume: float) -> pd.DataFrame:
    index = pd.DatetimeIndex([pd.Timestamp(d).tz_localize(ET_TZ) for d in days])  # New York midnights
    return pd.DataFrame(
        {"Close": np.full(len(days), 10.0), "Volume": np.full(len(days), dollar_volume / 10.0)}, index=index
    )


async def _seed(capture_store, archive, cohort, days, raw=None, actions=()):
    return await load_watch_history(
        InsiderWatchConfig(enabled=True, archive_dir=archive),
        capture_store,
        cohort=cohort,
        trading_days=days,
        raw_bars=raw or {},
        actions=actions,
    )


async def test_the_archive_wins_where_both_cover_a_filing_date_and_live_dates_follow(tmp_path, capture_store):
    q3 = _trades(
        {"cik": CROX_CIK, "owner": 11, "filing": "29-SEP-2026", "trans": "28-SEP-2026", "shares": 100, "price": 50}
    )
    archive, inputs = _archive(tmp_path, {"2026q2": _trades(), "2026q3": q3})
    for payload in (
        _live("2026-09-29", _tx("CROX", "2026-09-29", 1.0, 99)),  # also in the archive: the archive wins
        _live("2026-10-01", _tx("CROX", "2026-10-01", 7000.0, 12)),
        _live("2026-10-02", _tx("LPG", "2026-10-02", 8000.0, 13)),
    ):
        await capture_store.record(payload, {})
    history = await _seed(capture_store, archive, _cohort(inputs), _weekdays(date(2026, 7, 1), date(2026, 10, 5)))

    crox = history.transactions["CROX"]
    assert [(t.filing_date, t.owner_cik, t.dollars) for t in crox] == [
        (date(2026, 9, 29), 11, 5000.0),
        (date(2026, 10, 1), 12, 7000.0),
    ]
    assert [t.owner_cik for t in history.transactions["LPG"]] == [13]
    assert history.archive_through == date(2026, 9, 30)
    assert history.latest_filing_date == date(2026, 10, 2)
    assert history.provenance["live_dates_in_archive"] == 1
    assert history.provenance["insider_files_sha256"] == inputs.files_sha256


async def test_a_gap_in_the_live_journal_ends_the_history_at_the_gap(tmp_path, capture_store):
    archive, inputs = _archive(tmp_path, {"2026q3": _trades()})
    await capture_store.record(_live("2026-10-01"), {})
    await capture_store.record(_live("2026-10-03", _tx("CROX", "2026-10-03", 9000.0, 14)), {})
    history = await _seed(capture_store, archive, _cohort(inputs), _weekdays(date(2026, 7, 1), date(2026, 10, 5)))
    assert history.latest_filing_date == date(2026, 10, 1)
    assert history.transactions["CROX"] == ()  # nothing past a missing filing date is read


async def test_without_live_captures_the_history_ends_with_the_archive(tmp_path, capture_store):
    archive, inputs = _archive(tmp_path, {"2026q3": _trades()})
    history = await _seed(capture_store, archive, _cohort(inputs), _weekdays(date(2026, 7, 1), date(2026, 10, 5)))
    assert history.latest_filing_date == date(2026, 9, 30)


async def test_plausibility_drops_trades_above_ten_times_dollar_volume_and_keeps_the_routine_input(
    tmp_path, capture_store
):
    q2 = _trades(  # before the calendar: no DV, kept for the routine rule
        {"cik": CROX_CIK, "owner": 21, "filing": "10-APR-2026", "trans": "09-APR-2026", "shares": 1000, "price": 100}
    )
    archive, inputs = _archive(tmp_path, {"2026q2": q2, "2026q3": _trades()})
    await capture_store.record(
        _live(
            "2026-10-01",
            _tx("CROX", "2026-10-01", 9_999.0, 22),  # 9.999 x DV: kept
            _tx("CROX", "2026-10-01", 10_001.0, 23),  # above 10 x DV: implausible
            _tx("LPG", "2026-10-01", 1e9, 24),  # no bars for LPG: no DV, kept
        ),
        {},
    )
    days = _weekdays(date(2026, 7, 1), date(2026, 10, 5))
    history = await _seed(capture_store, archive, _cohort(inputs), days, raw={"CROX": _bars(days, 1_000.0)})
    assert [t.owner_cik for t in history.transactions["CROX"]] == [21, 22]
    assert [t.owner_cik for t in history.transactions["LPG"]] == [24]
    assert history.provenance["implausible_size"] == 1 and history.provenance["max_trade_rel"] == 10


async def test_identity_breaks_come_from_the_passed_actions(tmp_path, capture_store):
    archive, inputs = _archive(tmp_path, {"2026q3": _trades()})
    rename = CorporateAction(
        id="nc-1",
        kind=CorporateActionKind.NAME_CHANGE,
        symbol="OLDX",
        related_symbol="CROX",
        effective_on=date(2026, 8, 3),
    )
    history = await _seed(
        capture_store, archive, _cohort(inputs), _weekdays(date(2026, 7, 1), date(2026, 10, 5)), actions=[rename]
    )
    assert history.identity_breaks == {"CROX": date(2026, 8, 3)}


async def test_the_pinned_archive_digest_is_verified(tmp_path, capture_store):
    archive, inputs = _archive(tmp_path, {"2026q3": _trades()})
    tampered = inputs.model_copy(update={"files_sha256": "0" * 64})
    with pytest.raises(ValueError, match="files_sha256"):
        await _seed(capture_store, archive, _cohort(tampered), _weekdays(date(2026, 7, 1), date(2026, 10, 5)))


async def test_only_the_quarters_the_routine_rule_needs_are_read(tmp_path, capture_store):
    archive, inputs = _archive(
        tmp_path,
        {
            "2025q1": _trades(),
            "2025q2": _trades(),
            "2025q3": _trades(),
            "2025q4": _trades(),
            "2026q1": _trades(),
            "2026q2": _trades(),
            "2026q3": _trades(),
        },
    )
    (archive / "2025q1_form345.zip").write_bytes(b"corrupt: its body no longer matches its meta file")
    days = _weekdays(date(2026, 7, 1), date(2026, 10, 5))  # the routine rule reaches back to 2025-07
    history = await _seed(capture_store, archive, _cohort(inputs), days)
    assert history.provenance["quarters_read"] == ["2025q3", "2025q4", "2026q1", "2026q2", "2026q3"]


async def test_document_errors_mark_the_symbol_and_date_incomplete(tmp_path, capture_store):
    archive, inputs = _archive(tmp_path, {"2026q3": _trades()})
    bad = {"path": "edgar/data/1334036/0000000001-26-000001.txt", "index_cik": CROX_CIK, "error": "ValueError"}
    good = {"path": "edgar/data/1596993/0000000001-26-000002.txt", "index_cik": LPG_CIK, "error": None}
    await capture_store.record(_live("2026-10-01", documents=[bad, good]), {})
    await capture_store.record(_live("2026-10-02", documents=[bad]), {})
    history = await _seed(capture_store, archive, _cohort(inputs), _weekdays(date(2026, 7, 1), date(2026, 10, 5)))
    assert history.incomplete == {"CROX": (date(2026, 10, 1), date(2026, 10, 2))}
    assert history.provenance["document_errors"] == 2


async def test_a_document_error_marks_every_share_class_of_its_issuer_incomplete(tmp_path, capture_store):
    archive, inputs = _archive(tmp_path, {"2026q3": _trades()})
    classes = {CROX_CIK: ("CROX", "CROXB"), LPG_CIK: ("LPG",)}
    cohort = dataclasses.replace(_cohort(inputs), symbols=("CROX", "CROXB", "LPG"), cik_to_symbols=classes)
    bad = {"path": "edgar/data/1334036/0000000001-26-000001.txt", "index_cik": CROX_CIK, "error": "ValueError"}
    scope = CaptureScope(classes, ROLES, False).sha256
    await capture_store.record(_live("2026-10-01", documents=[bad], scope=scope), {})
    history = await _seed(capture_store, archive, cohort, _weekdays(date(2026, 7, 1), date(2026, 10, 5)))
    assert history.incomplete == {"CROX": (date(2026, 10, 1),), "CROXB": (date(2026, 10, 1),)}


async def test_a_capture_under_another_scope_ends_the_history(tmp_path, capture_store):
    archive, inputs = _archive(tmp_path, {"2026q3": _trades()})
    await capture_store.record(_live("2026-10-01"), {})
    await capture_store.record(_live("2026-10-02", _tx("CROX", "2026-10-02", 9000.0, 15), scope="f" * 64), {})
    await capture_store.record(_live("2026-10-03", status="no_index"), {})
    history = await _seed(capture_store, archive, _cohort(inputs), _weekdays(date(2026, 7, 1), date(2026, 10, 5)))
    assert history.latest_filing_date == date(2026, 10, 1) and history.transactions["CROX"] == ()
    assert history.provenance["scope_mismatch"] == "2026-10-02"


async def test_the_monday_after_good_friday_is_not_current_while_fridays_index_is_pending(tmp_path, capture_store):
    archive, inputs = _archive(tmp_path, {"2026q1": _trades()})
    good_friday = date(2026, 4, 3)  # EDGAR is open; NYSE is closed
    sec = FakeSec(
        indexes={date(2026, 4, 1): index_body(date(2026, 4, 1)), date(2026, 4, 2): index_body(date(2026, 4, 2))}
    )
    scope = CaptureScope({CROX_CIK: ("CROX",), LPG_CIK: ("LPG",)}, ROLES, False)
    days = _days(date(2026, 4, 1), date(2026, 4, 5))
    async with sec.client() as client:
        result = await capture_filing_dates(client, capture_store, days, scope=scope, user_agent=UA, pace=_no_pace)
    assert result.pending == good_friday
    calendar = tuple(d for d in _weekdays(date(2026, 1, 2), date(2026, 4, 6)) if d != good_friday)
    history = await _seed(capture_store, archive, _cohort(inputs), calendar)
    monday = date(2026, 4, 6)
    assert history.latest_filing_date == date(2026, 4, 2) and history.current_for(monday) is False
    assert history.current_for(date(2026, 4, 3)) is True

    sec.indexes[good_friday] = index_body(good_friday)
    async with sec.client() as client:
        await capture_filing_dates(client, capture_store, days[2:], scope=scope, user_agent=UA, pace=_no_pace)
    history = await _seed(capture_store, archive, _cohort(inputs), calendar)
    assert history.latest_filing_date == date(2026, 4, 5) and history.current_for(monday) is True


async def test_the_watch_cohort_loads_the_frozen_entry_protocol_and_cohort(tmp_path):
    directory, _ = _archive(tmp_path, {"2026q3": _trades()})
    cohort = load_watch_cohort(REPO_ROOT, directory)
    entry = load_insider_entry(REPO_ROOT / "config/research/apriori/insider-v1.json")
    assert cohort.entry_sha256 == entry.sha256 and cohort.protocol_sha256 == entry.entry.pooled_protocol_sha256
    assert len(cohort.symbols) == 1000 and cohort.filings.max_trade_rel == 10
    assert cohort.cik_to_symbols == {CROX_CIK: ("CROX",), LPG_CIK: ("LPG",)}
    assert cohort.scope().owner_roles == ("Officer", "Director") and cohort.scope().exclude_10b5_1 is False


def test_the_capture_scope_is_the_cohort_s_only_over_the_pinned_archive(tmp_path):
    directory, inputs = _archive(tmp_path, {"2026q3": _trades()})
    cohort = _cohort(inputs)
    assert capture_scope(cohort, directory) == cohort.scope()
    at = datetime(2026, 10, 22, tzinfo=UTC)
    body = b'{"0": {"cik_str": 1, "ticker": "NEW", "title": "x"}}'  # a refreshed ticker map: a new scope
    InsiderFilingStore(directory).save_tickers(body, url="u", requested_at=at, received_at=at)
    with pytest.raises(InsiderArchiveMismatch):
        capture_scope(cohort, directory)


async def test_a_scope_over_another_archive_fails_the_poll_by_type_name(capture_store, app_config):
    shutdown = asyncio.Event()
    seen: list[tuple[bool, str]] = []

    async def observe(ok: bool, detail: str) -> None:
        seen.append((ok, detail))
        shutdown.set()

    async def scope() -> CaptureScope:
        raise InsiderArchiveMismatch("the insider archive does not match the pinned files_sha256")

    sec = _quiet_sec(*_days(date(2026, 10, 1), date(2026, 10, 5)))
    await asyncio.wait_for(
        run_insider_capture_worker(
            _config(app_config),
            capture_store,
            observe,
            shutdown,
            scope=scope,
            environ={"EDGAR_USER_AGENT": UA},
            transport=httpx.MockTransport(sec.handler),
            clock=lambda: _et(date(2026, 10, 6), 7),
            pace=_no_pace,
        ),
        timeout=5,
    )
    assert seen == [(False, "InsiderArchiveMismatch")]
    assert sec.requests == [] and await capture_store.captures() == []


def test_the_watch_cohort_fails_closed_without_a_ticker_map(tmp_path):
    with pytest.raises(ValueError, match="ticker map"):
        load_watch_cohort(REPO_ROOT, tmp_path / "empty")


# --- config -------------------------------------------------------------------------------------


def test_insider_watch_config_defaults_off_and_expands_home():
    default = InsiderWatchConfig()
    assert default.enabled is False and default.backfill_from == date(2026, 10, 1)
    assert default.archive_dir == Path.home() / "agentic-trader-research" / "insider-filings-cache"
    assert InsiderWatchConfig(archive_dir="~/x").archive_dir == Path.home() / "x"
    with pytest.raises(ValidationError):
        InsiderWatchConfig(max_cards_per_session=0)
    with pytest.raises(ValidationError):
        InsiderWatchConfig(unknown=True)
