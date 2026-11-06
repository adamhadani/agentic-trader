"""The filing-veto evidence capture: the pass, its journal, the lookup, the worker's slots and readiness.

Fake HTTP only: an injected ``fetch`` for ``capture_filings`` and ``httpx.MockTransport`` for the worker.
The user agent is a fake contact; it never reaches a journal payload or a log.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from agentic_trader.config import AppConfig, FilingVetoConfig
from agentic_trader.data.edgar_filings import FilingCategory, MatchedFiling
from agentic_trader.data.filings_capture import (
    COVERAGE_READY_FRACTION,
    FILINGS_STREAM,
    SUBMISSIONS_URL_TEMPLATE,
    TICKERS_URL,
    FilingsCaptureFailed,
    FilingsCaptureStore,
    FilingsCaptureWorker,
    FilingsLookup,
    capture_filings,
    capture_slots,
    run_filings_capture_worker,
)
from agentic_trader.diagnostics.readiness import FILINGS_CAPTURE_MAX_AGE_SECONDS
from agentic_trader.execution.durable import EventKind
from agentic_trader.market.session import ET_TZ
from agentic_trader.storage.models import DomainEventRecord


UA = "Filings Test ops-test@example.invalid"
FRIDAY = date(2026, 11, 6)
TICKERS = {
    "0": {"cik_str": 101, "ticker": "AAA", "title": "Alpha Corp"},
    "1": {"cik_str": 202, "ticker": "BBB", "title": "Beta Inc"},
    "2": {"cik_str": 303, "ticker": "CCC", "title": "Gamma Ltd"},
    "3": {"cik_str": 404, "ticker": "BF-B", "title": "Brown-Forman"},
}
TICKERS_BODY = json.dumps(TICKERS).encode()


def url(cik: int) -> str:
    return SUBMISSIONS_URL_TEMPLATE.format(cik=cik)


def submissions(*rows: tuple[str, str, str, str]) -> bytes:
    """``(form, items, filing_date, accession)`` rows as EDGAR's ``filings.recent`` columns."""
    recent = {
        "accessionNumber": [r[3] for r in rows],
        "filingDate": [r[2] for r in rows],
        "acceptanceDateTime": [f"{r[2]}T16:05:00.000Z" for r in rows],
        "form": [r[0] for r in rows],
        "items": [r[1] for r in rows],
    }
    return json.dumps({"cik": "x", "filings": {"recent": recent, "files": []}}).encode()


AAA_BODY = submissions(
    ("8-K", "2.05,9.01", "2026-10-27", "0000000101-26-000010"),  # adverse, 10 days before the pass
    ("10-Q", "", "2026-10-20", "0000000101-26-000009"),  # not a veto category
    ("8-K", "2.05", "2026-08-01", "0000000101-26-000001"),  # adverse but before the lookback
)
BBB_BODY = submissions(("10-K", "", "2026-10-01", "0000000202-26-000003"))


def status_error(code: int, target: str = "https://data.sec.gov/x") -> httpx.HTTPStatusError:
    request = httpx.Request("GET", target)
    return httpx.HTTPStatusError("failed", request=request, response=httpx.Response(code, request=request))


class FakeFetch:
    """URL -> responses in order (bytes, or an exception to raise); the last one repeats."""

    def __init__(self, responses: dict[str, list[bytes | Exception]]):
        self.responses = {key: list(value) for key, value in responses.items()}
        self.calls: list[str] = []

    async def __call__(self, target: str) -> bytes:
        self.calls.append(target)
        queue = self.responses[target]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item


class Counter:
    def __init__(self) -> None:
        self.paced = 0
        self.slept: list[float] = []

    async def pace(self) -> None:
        self.paced += 1

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET_TZ).astimezone(UTC)


async def _capture(fake: FakeFetch, symbols, counter: Counter | None = None, **kwargs):
    counter = counter or Counter()
    return await capture_filings(
        fake,
        symbols,
        today=FRIDAY,
        lookback_days=60,
        pace=counter.pace,
        sleep=counter.sleep,
        clock=lambda: at(FRIDAY, 6, 1),
        **kwargs,
    )


def _fake(**overrides: list[bytes | Exception]) -> FakeFetch:
    responses: dict[str, list[bytes | Exception]] = {
        TICKERS_URL: [TICKERS_BODY],
        url(101): [AAA_BODY],
        url(202): [BBB_BODY],
        url(303): [status_error(503), status_error(503)],
        url(404): [submissions()],
    }
    responses.update({url(int(k.removeprefix("cik"))): v for k, v in overrides.items()})
    return FakeFetch(responses)


# --- capture_filings ----------------------------------------------------------------------------


async def test_a_pass_covers_mapped_symbols_and_records_failures_by_status():
    fake, counter = _fake(), Counter()
    payload = await _capture(fake, ["CCC", "aaa", "BBB", "ZZZ", "AAA"], counter)
    assert payload is not None
    assert payload["requested"] == ["AAA", "BBB", "CCC", "ZZZ"]
    assert payload["covered"] == ["AAA", "BBB"]
    assert payload["unmapped"] == ["ZZZ"]
    assert payload["failed"] == {"CCC": "http_503"}
    assert payload["ticker_map_sha256"] == hashlib.sha256(TICKERS_BODY).hexdigest()
    assert payload["fetched_at"] == at(FRIDAY, 6, 1).isoformat()
    assert payload["lookback_days"] == 60
    assert payload["filings"]["BBB"] == []
    [filing] = payload["filings"]["AAA"]
    assert MatchedFiling.from_payload(filing) == MatchedFiling(
        symbol="AAA",
        category=FilingCategory.ADVERSE_8K,
        form="8-K",
        items=("2.05", "9.01"),
        filing_date=date(2026, 10, 27),
        acceptance="2026-10-27T16:05:00.000Z",
        accession="0000000101-26-000010",
    )
    assert set(payload["filings"]) == {"AAA", "BBB"}
    assert fake.calls.count(url(303)) == 2 and counter.slept == [5.0]  # one retry after 5 s
    assert fake.calls[0] == TICKERS_URL and url(101) == "https://data.sec.gov/submissions/CIK0000000101.json"
    json.dumps(payload)  # journal-ready


async def test_the_ticker_map_reads_dashes_as_dots():
    payload = await _capture(_fake(), ["BF.B"])
    assert payload is not None and payload["covered"] == ["BF.B"] and payload["unmapped"] == []


async def test_a_lookback_boundary_day_is_kept_and_the_day_before_is_not():
    body = submissions(
        ("NT 10-Q", "", "2026-09-07", "0000000101-26-000002"),  # exactly 60 days before
        ("424B5", "", "2026-09-06", "0000000101-26-000001"),  # 61 days before
    )
    payload = await _capture(_fake(cik101=[body]), ["AAA"])
    assert payload is not None
    assert [f["form"] for f in payload["filings"]["AAA"]] == ["NT 10-Q"]


async def test_symbols_under_one_cik_share_one_request():
    tickers = {**TICKERS, "9": {"cik_str": 101, "ticker": "AAA.B", "title": "Alpha Corp"}}
    fake = _fake()
    fake.responses[TICKERS_URL] = [json.dumps(tickers).encode()]
    payload = await _capture(fake, ["AAA", "AAA.B"])
    assert payload is not None and payload["covered"] == ["AAA", "AAA.B"]
    assert fake.calls.count(url(101)) == 1
    assert [f["symbol"] for f in payload["filings"]["AAA.B"]] == ["AAA.B"]


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (status_error(503), "http_503"),
        (httpx.ConnectError("refused"), "ConnectError"),
        (b"<html>not json</html>", "JSONDecodeError"),
        (b"[1, 2]", "AttributeError"),
        (json.dumps({"x": {"cik_str": "abc", "ticker": "AAA"}}).encode(), "ValueError"),
    ],
)
async def test_a_failed_ticker_map_raises_and_nothing_else_is_requested(failure, reason):
    fake = _fake()
    fake.responses[TICKERS_URL] = [failure]
    with pytest.raises(FilingsCaptureFailed) as caught:
        await _capture(fake, ["AAA", "BBB"])
    assert caught.value.reason == reason and str(caught.value) == reason
    assert set(fake.calls) == {TICKERS_URL}


@pytest.mark.parametrize(
    ("first", "retried", "reason"),
    [
        (httpx.ConnectError("refused"), True, None),
        (httpx.ReadTimeout("slow"), True, None),
        (status_error(429), True, None),
        (status_error(500), True, None),
        (status_error(404), False, "http_404"),
        (status_error(403), False, "http_403"),
        (b"{}", False, "ValueError"),
    ],
)
async def test_a_symbol_is_retried_once_only_after_a_transport_error_429_or_5xx(first, retried, reason):
    fake, counter = _fake(cik101=[first, AAA_BODY]), Counter()
    payload = await _capture(fake, ["AAA", "BBB"], counter)
    assert payload is not None
    assert fake.calls.count(url(101)) == (2 if retried else 1)
    assert counter.slept == ([5.0] if retried else [])
    if reason is None:
        assert payload["covered"] == ["AAA", "BBB"] and payload["failed"] == {}
    else:
        assert payload["covered"] == ["BBB"] and payload["failed"] == {"AAA": reason}
        assert "AAA" not in payload["filings"]


async def test_every_attempt_is_paced_through_the_injected_pacer():
    fake, counter = _fake(), Counter()
    await _capture(fake, ["AAA", "BBB", "CCC", "ZZZ"], counter)
    assert counter.paced == len(fake.calls) == 5  # the map, AAA, BBB and CCC twice; ZZZ is unmapped


async def test_a_stop_between_requests_returns_nothing():
    fake = _fake()
    payload = await _capture(fake, ["AAA", "BBB", "CCC"], stop=lambda: len(fake.calls) >= 2)
    assert payload is None and fake.calls == [TICKERS_URL, url(101)]


# --- the journal --------------------------------------------------------------------------------


@pytest.fixture
def store(temp_db):
    return FilingsCaptureStore(temp_db.workflows)


def _pass(fetched_at: datetime, *, requested=11, unmapped=1, covered=9, failed=1, filings=None) -> dict:
    names = [f"S{i:02d}" for i in range(requested)]
    return {
        "requested": names,
        "covered": names[:covered],
        "unmapped": names[requested - unmapped :],
        "failed": {name: "http_503" for name in names[covered : covered + failed]},
        "ticker_map_sha256": "0" * 64,
        "fetched_at": fetched_at.isoformat(),
        "lookback_days": 60,
        "filings": filings if filings is not None else {name: [] for name in names[:covered]},
    }


async def test_a_slot_is_journaled_once(store):
    key = f"{FILINGS_STREAM}/2026-11-06/open"
    payload = _pass(at(FRIDAY, 6, 1))
    assert await store.recorded(key) is False and await store.latest() is None
    assert await store.record(key, payload) is True
    assert await store.record(key, _pass(at(FRIDAY, 7))) is False
    assert await store.recorded(key) is True
    [event] = await store.workflows.events(FILINGS_STREAM)
    assert event["kind"] == EventKind.FILINGS_CAPTURE and event["payload"] == payload
    later = _pass(at(FRIDAY, 10, 6), covered=8)
    assert await store.record(f"{FILINGS_STREAM}/2026-11-06/pre-1005", later) is True
    assert await store.latest() == later
    assert await store.recent(5) == [later, payload]


# --- the lookup ---------------------------------------------------------------------------------

AAA_FILING = MatchedFiling(
    symbol="AAA",
    category=FilingCategory.OFFERING,
    form="424B5",
    items=(),
    filing_date=date(2026, 11, 4),
    acceptance=None,
    accession="0000000101-26-000020",
)


def _lookup_payload() -> dict:
    return {
        "requested": ["AAA", "BBB", "CCC", "ZZZ"],
        "covered": ["AAA", "BBB"],
        "unmapped": ["ZZZ"],
        "failed": {"CCC": "http_503"},
        "ticker_map_sha256": "0" * 64,
        "fetched_at": at(FRIDAY, 6, 1).isoformat(),
        "lookback_days": 60,
        "filings": {"AAA": [AAA_FILING.to_payload()], "BBB": []},
    }


def test_no_pass_is_unavailable():
    lookup = FilingsLookup.build(None, at(FRIDAY, 10), 345600)
    assert (lookup.status, lookup.as_of, lookup.covered) == ("unavailable", None, frozenset())
    assert not lookup.covers("AAA") and lookup.for_symbol("AAA") == ()


def test_a_fresh_pass_is_ok_and_case_insensitive():
    lookup = FilingsLookup.build(_lookup_payload(), at(FRIDAY, 10), 345600)
    assert lookup.status == "ok" and lookup.as_of == at(FRIDAY, 6, 1)
    assert lookup.covered == frozenset({"AAA", "BBB"})
    assert lookup.covers("aaa") and lookup.covers(" BBB ") and lookup.covers("AAA")
    assert not lookup.covers("CCC") and not lookup.covers("zzz")  # failed and unmapped are uncovered
    assert lookup.for_symbol("aaa") == (AAA_FILING,) and lookup.for_symbol("BBB") == ()


def test_a_pass_older_than_the_limit_is_stale():
    fetched = at(FRIDAY, 6, 1)
    edge = FilingsLookup.build(_lookup_payload(), fetched + timedelta(seconds=345600), 345600)
    assert edge.status == "ok"
    stale = FilingsLookup.build(_lookup_payload(), fetched + timedelta(seconds=345601), 345600)
    assert stale.status == "stale" and stale.as_of == fetched
    assert not stale.covers("AAA") and stale.for_symbol("AAA") == ()


# --- the worker's slots -------------------------------------------------------------------------


def test_slots_are_the_open_and_each_scan_less_the_lead():
    assert [(slot.isoformat(), name) for slot, name in capture_slots(["10:35", "14:35"], 30)] == [
        ("06:00:00", "open"),
        ("10:05:00", "pre-1005"),
        ("14:05:00", "pre-1405"),
    ]
    assert [name for _, name in capture_slots(["06:20", "09:45"], 30)] == ["open", "pre-0915"]
    assert [name for _, name in capture_slots([], 30)] == ["open"]


class FakeSec:
    def __init__(self, bodies: dict[str, bytes] | None = None):
        self.bodies = bodies if bodies is not None else {TICKERS_URL: TICKERS_BODY, url(101): AAA_BODY}
        self.requests: list[tuple[str, str | None]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((str(request.url), request.headers.get("user-agent")))
        body = self.bodies.get(str(request.url))
        return httpx.Response(200, content=body) if body is not None else httpx.Response(404)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


async def _no_pace() -> None:
    return None


async def _no_sleep(_seconds: float) -> None:
    return None


def _config(**policy) -> AppConfig:
    config = AppConfig(execution_mode="alpaca", alpaca_paper=True)
    config.scheduler.suggestion_scan_times_et = ["10:35", "14:35"]
    return config.model_copy(update={"filing_veto": FilingVetoConfig(capture=True, **policy)})


def _worker(sec: FakeSec, store, *, user_agent: str = UA, now=None, symbols=("AAA",)) -> FilingsCaptureWorker:
    async def load() -> list[str]:
        return list(symbols)

    return FilingsCaptureWorker(
        sec.client(),
        store,
        _config(pre_scan_minutes=30),
        symbols=load,
        user_agent=user_agent,
        pace=_no_pace,
        sleep=_no_sleep,
        clock=now or (lambda: at(FRIDAY, 6, 1)),
    )


async def _keys(store) -> list[str]:
    async with store.workflows.db.session_factory() as session:
        rows = await session.scalars(
            select(DomainEventRecord.event_key)
            .where(DomainEventRecord.stream == FILINGS_STREAM)
            .order_by(DomainEventRecord.id)
        )
        return list(rows)


SATURDAY_NOON = at(FRIDAY + timedelta(days=1), 12)


async def test_run_once_follows_the_slots(store):
    sec = FakeSec()
    worker = _worker(sec, store)
    assert await worker.run_once(at(FRIDAY, 5, 59)) == "waiting"
    assert sec.requests == []
    assert await worker.run_once(at(FRIDAY, 6, 1)) == "captured"
    assert len(sec.requests) == 2 and {agent for _, agent in sec.requests} == {UA}
    assert await worker.run_once(at(FRIDAY, 9, 0)) == "present"
    assert await _worker(sec, store).run_once(at(FRIDAY, 10, 4)) == "present"  # a restart reads the journal
    assert await worker.run_once(at(FRIDAY, 10, 6)) == "captured"
    assert await worker.run_once(at(FRIDAY, 10, 31)) == "scan_window"
    assert await worker.run_once(at(FRIDAY, 10, 50)) == "scan_window"
    assert await worker.run_once(at(FRIDAY, 11, 0)) == "present"
    assert await worker.run_once(SATURDAY_NOON) == "weekend"
    assert len(sec.requests) == 4
    assert await _keys(store) == [f"{FILINGS_STREAM}/2026-11-06/open", f"{FILINGS_STREAM}/2026-11-06/pre-1005"]
    events = await store.workflows.events(FILINGS_STREAM)
    assert UA not in json.dumps([e["payload"] for e in events])


async def test_a_missed_earlier_slot_is_skipped_for_the_due_one(store):
    sec = FakeSec()
    worker = _worker(sec, store)
    assert await worker.run_once(at(FRIDAY, 10, 6)) == "captured"
    assert await worker.run_once(at(FRIDAY, 10, 7)) == "present"
    assert await _keys(store) == [f"{FILINGS_STREAM}/2026-11-06/pre-1005"]
    assert await worker.run_once(at(FRIDAY, 16, 0)) == "captured"
    assert await _keys(store) == [f"{FILINGS_STREAM}/2026-11-06/pre-1005", f"{FILINGS_STREAM}/2026-11-06/pre-1405"]


async def test_a_missing_user_agent_never_requests(store):
    sec = FakeSec()
    worker = _worker(sec, store, user_agent="")
    for when in (at(FRIDAY, 6, 1), at(FRIDAY, 10, 6), SATURDAY_NOON):
        assert await worker.run_once(when) == "missing_user_agent"
    assert sec.requests == [] and await store.latest() is None


async def test_a_scan_window_opening_mid_pass_journals_nothing(store):
    sec = FakeSec()
    clock = [at(FRIDAY, 10, 6)]
    original = sec.handler

    def handler(request: httpx.Request) -> httpx.Response:
        clock[0] = at(FRIDAY, 10, 31)  # the window opens after the ticker map
        return original(request)

    worker = _worker(sec, store, now=lambda: clock[0], symbols=("AAA", "BBB"))
    worker.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await worker.run_once(at(FRIDAY, 10, 6)) == "scan_window"
    assert [u for u, _ in sec.requests] == [TICKERS_URL]
    assert await store.latest() is None


async def test_the_lookback_is_the_widest_window(store):
    sec = FakeSec({TICKERS_URL: TICKERS_BODY, url(101): AAA_BODY})
    worker = FilingsCaptureWorker(
        sec.client(),
        store,
        AppConfig(execution_mode="alpaca", alpaca_paper=True).model_copy(
            update={"filing_veto": FilingVetoConfig(capture=True, adverse_8k_days=20, late_filing_days=90)}
        ),
        symbols=lambda: _symbols("AAA"),
        user_agent=UA,
        pace=_no_pace,
        sleep=_no_sleep,
    )
    assert await worker.run_once(at(FRIDAY, 6, 1)) == "captured"
    assert (await store.latest())["lookback_days"] == 90


async def _symbols(*names: str) -> list[str]:
    return list(names)


# --- readiness ----------------------------------------------------------------------------------


def test_the_coverage_rule_counts_mapped_symbols_only():
    assert COVERAGE_READY_FRACTION == 0.9


async def test_freshness_needs_a_recent_pass_covering_ninety_percent_of_mapped_symbols(store):
    worker = _worker(FakeSec(), store)
    assert await worker.freshness(at(FRIDAY, 5), "waiting") == (True, "not_due; no capture yet")
    assert await worker.freshness(at(FRIDAY, 5), "weekend") == (True, "not_due; no capture yet")
    worker._pass_ran = True
    assert await worker.freshness(at(FRIDAY, 7), "present") == (False, "no capture yet")

    thursday = at(FRIDAY - timedelta(days=1), 6, 1)
    await store.record(f"{FILINGS_STREAM}/2026-11-05/open", _pass(thursday))  # 9 of 10 mapped
    ready, detail = await worker.freshness(at(FRIDAY, 7), "present")
    assert ready and detail == f"latest pass {thursday.isoformat()}: 9 covered, 1 unmapped, 1 failed (http_503: 1)"

    friday = at(FRIDAY, 6, 1)
    await store.record(f"{FILINGS_STREAM}/2026-11-06/open", _pass(friday, covered=8, failed=2))  # 8 of 10
    ready, detail = await worker.freshness(at(FRIDAY, 7), "present")
    assert ready and detail == (
        f"latest pass {friday.isoformat()}: 8 covered, 1 unmapped, 2 failed (http_503: 2); "
        f"last 90% pass {thursday.isoformat()}"
    )
    stale_good = thursday + timedelta(seconds=345601)
    ready, detail = await worker.freshness(stale_good, "present")
    assert not ready and detail.startswith(f"low coverage: latest pass {friday.isoformat()}: 8 covered")
    ready, detail = await worker.freshness(friday + timedelta(seconds=345601), "present")
    assert not ready and detail.startswith("stale: latest pass")


async def test_a_pass_with_no_mapped_symbols_is_not_a_coverage_failure(store):
    worker = _worker(FakeSec(), store)
    vacuous = _pass(at(FRIDAY, 6, 1), requested=3, unmapped=3, covered=0, failed=0)
    await store.record(f"{FILINGS_STREAM}/2026-11-06/open", vacuous)
    ready, _ = await worker.freshness(at(FRIDAY, 7), "present")
    assert ready


def test_readiness_duplicates_the_default_age_limit():
    assert FILINGS_CAPTURE_MAX_AGE_SECONDS == FilingVetoConfig().max_age_seconds == 4 * 86400


# --- the daemon loop ----------------------------------------------------------------------------


async def _loop(store, sec_handler, *, environ, clock=lambda: at(FRIDAY, 6, 1)):
    shutdown = asyncio.Event()
    seen: list[tuple[bool, str]] = []

    async def observe(ok: bool, detail: str) -> None:
        seen.append((ok, detail))
        shutdown.set()

    await run_filings_capture_worker(
        _config(),
        store,
        observe,
        shutdown,
        symbols=lambda: _symbols("AAA"),
        environ=environ,
        transport=httpx.MockTransport(sec_handler),
        clock=clock,
        pace=_no_pace,
        sleep=_no_sleep,
    )
    return seen


async def test_a_missing_user_agent_degrades_readiness_without_requests(store, caplog):
    sec = FakeSec()
    with caplog.at_level(logging.WARNING, logger="copilot"):
        seen = await _loop(store, sec.handler, environ={})
    assert seen == [(False, "missing_user_agent")] and sec.requests == []
    assert any(getattr(r, "event", None) == "filings_capture_missing_user_agent" for r in caplog.records)


async def test_a_successful_loop_observes_counts_and_never_logs_the_user_agent(store, caplog):
    sec = FakeSec()
    with caplog.at_level(logging.DEBUG):
        seen = await _loop(store, sec.handler, environ={"EDGAR_USER_AGENT": UA})
    [(ok, detail)] = seen
    assert ok and detail.startswith("captured; latest pass") and "1 covered, 0 unmapped, 0 failed" in detail
    assert {agent for _, agent in sec.requests} == {UA}
    assert UA not in caplog.text


async def test_a_failed_ticker_map_is_observed_by_reason_and_never_echoes_the_user_agent(store, caplog):
    def echo(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"refused for {request.headers['user-agent']}", request=request)

    with caplog.at_level(logging.DEBUG):
        seen = await _loop(store, echo, environ={"EDGAR_USER_AGENT": UA})
    assert seen == [(False, "failed: FilingsCaptureFailed (ConnectError); no capture yet")]
    assert UA not in caplog.text and "ops-test" not in caplog.text
    [record] = [r for r in caplog.records if getattr(r, "event", None) == "filings_capture_failed"]
    assert record.__dict__["reason"] == "FilingsCaptureFailed" and record.__dict__["detail"] == "ConnectError"
