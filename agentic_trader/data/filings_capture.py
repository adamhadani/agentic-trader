"""The filing-veto evidence capture: each covered symbol's recent negative EDGAR filings.

Spec: docs/superpowers/specs/2026-11-06-release-exit-filing-veto-design.md, sections 4.1, 4.2 and 4.5.

On each New York weekday the worker takes one pass per slot: ``open`` at the first poll from 06:00, and
``pre-HHMM`` ``filing_veto.pre_scan_minutes`` before each suggestion scan. Only the latest due slot runs, so a
missed earlier slot is skipped, and no pass runs inside a suggestion-scan window (a window that opens between
two requests stops the pass and journals nothing). A pass reads the SEC ticker map once, then
``filings.recent`` of each mapped symbol's CIK, at two requests per second with one retry after 5 seconds on a
transport error, HTTP 429 or 5xx. Each symbol is ``covered``, ``unmapped`` (absent from the map, or under two
CIKs) or ``failed`` (its HTTP status or exception type name). A failed ticker map, or ``MAX_CONSECUTIVE_FAILURES``
CIKs failing in a row (an EDGAR outage), aborts the pass: nothing is journaled and the next poll retries the
slot. A covered symbol's veto-category filings dated
within the widest window are journaled with the pass (``filings/capture/<ET date>/<slot>``, once per slot); a
covered symbol with none has an empty list, which is the evidence of "no filing". Raw bodies are not kept.

The ``EDGAR_USER_AGENT`` contact is read from the environment by ``run_filings_capture_worker`` only and sent
as the ``User-Agent`` header. Nothing journaled, observed or logged carries it or any exception text.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import math
import os
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any, Literal

import httpx
from sqlalchemy import select

from agentic_trader.data.edgar_filings import MatchedFiling, parse_recent, ticker_ciks
from agentic_trader.data.edgar_http import (
    SEC_DATA_HOST,
    TICKERS_URL,
    TIMEOUT_SECONDS,
    TRANSIENT_RETRY,
    USER_AGENT_ENV,
    edgar_get,
    edgar_pacer,
    retrying,
)
from agentic_trader.execution.durable import EventKind
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.setups.card_stats_worker import in_scan_window
from agentic_trader.storage.models import DomainEventRecord


if TYPE_CHECKING:
    from agentic_trader.config import AppConfig
    from agentic_trader.storage.workflow import WorkflowStore

__all__ = [
    "CAPTURE_AFTER_ET",
    "COVERAGE_READY_FRACTION",
    "FILINGS_STREAM",
    "MAX_CONSECUTIVE_FAILURES",
    "POLL_SECONDS",
    "SUBMISSIONS_URL_TEMPLATE",
    "FilingsCaptureFailed",
    "FilingsCaptureStore",
    "FilingsCaptureWorker",
    "FilingsLookup",
    "capture_filings",
    "capture_key",
    "capture_slots",
    "coverage_ready",
    "run_filings_capture_worker",
]

logger = logging.getLogger("copilot")

FILINGS_STREAM = "filings/capture"
_CAPTURE_LOCK = "filings-capture"
SUBMISSIONS_URL_TEMPLATE = f"https://{SEC_DATA_HOST}/submissions/CIK{{cik:010d}}.json"
CAPTURE_AFTER_ET = time(6, 0)
POLL_SECONDS = 300
# A pass is a readiness pass when it covered at least this fraction of its mapped symbols.
COVERAGE_READY_FRACTION = 0.9
# This many CIKs failing in a row abort the pass (an outage, not a few bad symbols); a success resets the count.
MAX_CONSECUTIVE_FAILURES = 5
# A malformed ticker map can surface as any of these (``ticker_ciks`` indexes the decoded JSON directly).
_TICKER_MAP_ERRORS = (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError, AttributeError)
# A symbol's own failure: its request (after the retry), the body budget, or its submissions payload.
_SYMBOL_ERRORS = (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError)

LookupStatus = Literal["ok", "stale", "unavailable"]
WorkerStatus = Literal["weekend", "scan_window", "waiting", "present", "captured", "missing_user_agent"]


class FilingsCaptureFailed(RuntimeError):
    """The pass records nothing: the ticker map could not be read (``reason`` is its HTTP status or type name),
    or ``MAX_CONSECUTIVE_FAILURES`` CIKs failed in a row (``consecutive_failures``)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def failure_reason(exc: BaseException) -> str:
    """``http_<status>`` for an HTTP status failure, otherwise the exception's type name (never its text)."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http_{exc.response.status_code}"
    return type(exc).__name__


def capture_key(day: date, slot: str) -> str:
    return f"{FILINGS_STREAM}/{day.isoformat()}/{slot}"


def capture_slots(scan_times_et: Sequence[str], pre_scan_minutes: int) -> list[tuple[time, str]]:
    """The day's slots in time order: ``open`` at 06:00 and ``pre-HHMM`` ``pre_scan_minutes`` before each scan.

    A refresh that would fall before 06:00 is the open pass's (it is dropped).
    """
    slots = {CAPTURE_AFTER_ET: "open"}
    for scan in scan_times_et:
        scan_at = datetime.combine(date(2000, 1, 3), time.fromisoformat(scan))
        due = scan_at - timedelta(minutes=pre_scan_minutes)
        if due.date() == scan_at.date() and due.time() > CAPTURE_AFTER_ET:
            slots.setdefault(due.time(), f"pre-{due:%H%M}")
    return sorted(slots.items())


def coverage_ready(payload: Mapping[str, Any]) -> bool:
    """At least one symbol, and ``COVERAGE_READY_FRACTION`` of the pass's mapped (requested, not unmapped) ones,
    were covered: a pass that covers nothing (nothing requested or nothing mapped) is never ready."""
    mapped = len(payload["requested"]) - len(payload["unmapped"])
    covered = len(payload["covered"])
    return covered > 0 and covered >= COVERAGE_READY_FRACTION * mapped


def _map_tickers(body: bytes, requested: Sequence[str]) -> tuple[dict[str, int], list[str]]:
    return ticker_ciks(json.loads(body), requested)


def _parse(body: bytes, symbols: Sequence[str], since: date) -> dict[str, list[dict[str, Any]]]:
    loaded = json.loads(body)
    return {symbol: [f.to_payload() for f in parse_recent(loaded, symbol, since)] for symbol in symbols}


async def capture_filings(
    fetch: Callable[[str], Awaitable[bytes]],
    symbols: Iterable[str],
    *,
    today: date,
    lookback_days: int,
    pace: Callable[[], Awaitable[None]],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], datetime] | None = None,
    stop: Callable[[], bool] | None = None,
) -> dict[str, Any] | None:
    """One pass over ``symbols``: the journal payload, or ``None`` once ``stop()`` is true between requests.

    ``fetch`` is one unpaced GET; every attempt here is paced, and one retry follows a transport error, an
    HTTP 429 or a 5xx after 5 seconds. A failed ticker map, or ``MAX_CONSECUTIVE_FAILURES`` failed CIKs in a
    row, raises ``FilingsCaptureFailed``; otherwise a symbol's own failure lands in ``failed`` and the pass
    continues. Symbols sharing a CIK share its one request. JSON bodies are parsed in worker threads.
    """
    fetched_at = (clock or (lambda: datetime.now(UTC)))()
    requested = sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
    try:
        tickers = await retrying(partial(fetch, TICKERS_URL), pace, retry=TRANSIENT_RETRY, sleep=sleep)
        ciks, unmapped = await asyncio.to_thread(_map_tickers, tickers, requested)
    except _TICKER_MAP_ERRORS as exc:
        raise FilingsCaptureFailed(failure_reason(exc)) from None
    by_cik: dict[int, list[str]] = {}
    for symbol, cik in ciks.items():
        by_cik.setdefault(cik, []).append(symbol)
    since = today - timedelta(days=lookback_days)
    filings: dict[str, list[dict[str, Any]]] = {}
    failed: dict[str, str] = {}
    consecutive = 0
    for cik, cik_symbols in by_cik.items():
        if stop is not None and stop():
            return None
        target = SUBMISSIONS_URL_TEMPLATE.format(cik=cik)
        try:
            body = await retrying(partial(fetch, target), pace, retry=TRANSIENT_RETRY, sleep=sleep)
            filings |= await asyncio.to_thread(_parse, body, cik_symbols, since)  # a large body parses off the loop
        except _SYMBOL_ERRORS as exc:
            failed |= dict.fromkeys(cik_symbols, failure_reason(exc))
            consecutive += 1
            if consecutive >= MAX_CONSECUTIVE_FAILURES:
                raise FilingsCaptureFailed("consecutive_failures") from None
        else:
            consecutive = 0
    return {
        "requested": requested,
        "covered": sorted(filings),
        "unmapped": unmapped,
        "failed": dict(sorted(failed.items())),
        "ticker_map_sha256": hashlib.sha256(tickers).hexdigest(),
        "fetched_at": fetched_at.isoformat(),
        "lookback_days": lookback_days,
        "filings": dict(sorted(filings.items())),
    }


class FilingsCaptureStore:
    """``filings/capture/<ET date>/<slot>`` journal events on stream ``filings/capture``."""

    def __init__(self, workflows: WorkflowStore):
        self.workflows = workflows

    async def recorded(self, key: str) -> bool:
        async with self.workflows.db.session_factory() as session:
            existing = await session.scalar(
                select(DomainEventRecord.id).where(
                    DomainEventRecord.scope == self.workflows.scope, DomainEventRecord.event_key == key
                )
            )
        return existing is not None

    async def record(self, key: str, payload: Mapping[str, Any]) -> bool:
        """Journal ``payload`` once under ``key``; False when that slot is already journaled."""
        if not key.startswith(f"{FILINGS_STREAM}/"):
            raise ValueError(f"{key}: not a {FILINGS_STREAM} key")
        async with self.workflows.db.session_factory() as session, session.begin():
            await self.workflows.lock(session, resource=_CAPTURE_LOCK)
            existing = await session.scalar(
                select(DomainEventRecord.id).where(
                    DomainEventRecord.scope == self.workflows.scope, DomainEventRecord.event_key == key
                )
            )
            if existing is not None:
                return False
            await self.workflows.append(
                session, stream=FILINGS_STREAM, kind=EventKind.FILINGS_CAPTURE, payload=dict(payload), key=key
            )
        return True

    async def recent(self, limit: int) -> list[dict[str, Any]]:
        """The newest ``limit`` passes' payloads, newest first."""
        return [event["payload"] for event in await self.workflows.events(FILINGS_STREAM, limit=limit)]

    async def latest(self) -> dict[str, Any] | None:
        """The newest pass's payload, whatever its coverage."""
        passes = await self.recent(1)
        return passes[0] if passes else None


@dataclass(frozen=True)
class FilingsLookup:
    """What one scan sees: the latest pass, whether it is fresh, and which symbols it covered.

    A symbol outside ``covered`` (unmapped, failed or never requested) has no evidence: callers label it
    uncovered and never refuse. A ``stale`` or ``unavailable`` lookup covers nothing.
    """

    status: LookupStatus
    as_of: datetime | None
    covered: frozenset[str] = frozenset()
    filings: Mapping[str, tuple[MatchedFiling, ...]] = field(default_factory=dict)

    def covers(self, symbol: str) -> bool:
        return symbol.strip().upper() in self.covered

    def for_symbol(self, symbol: str) -> tuple[MatchedFiling, ...]:
        return self.filings.get(symbol.strip().upper(), ())

    @classmethod
    def build(cls, latest_payload: Mapping[str, Any] | None, now: datetime, max_age_seconds: int) -> FilingsLookup:
        if latest_payload is None:
            return cls("unavailable", None)
        as_of = datetime.fromisoformat(str(latest_payload["fetched_at"]))
        if not 0 <= (now - as_of).total_seconds() <= max_age_seconds:
            return cls("stale", as_of)
        filings = {
            str(symbol).upper(): tuple(MatchedFiling.from_payload(row) for row in rows)
            for symbol, rows in latest_payload["filings"].items()
        }
        covered = frozenset(str(symbol).upper() for symbol in latest_payload["covered"])
        return cls("ok", as_of, covered, filings)


def _describe(payload: Mapping[str, Any]) -> str:
    reasons = Counter(payload["failed"].values())
    breakdown = f" ({', '.join(f'{r}: {n}' for r, n in sorted(reasons.items()))})" if reasons else ""
    return (
        f"latest pass {payload['fetched_at']}: {len(payload['covered'])} covered, "
        f"{len(payload['unmapped'])} unmapped, {len(payload['failed'])} failed{breakdown}"
    )


class FilingsCaptureWorker:
    """One poll (spec section 4.1): user-agent, weekend, scan-window and slot gates, then at most one pass."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        store: FilingsCaptureStore,
        config: AppConfig,
        *,
        symbols: Callable[[], Awaitable[Sequence[str]]],
        user_agent: str,
        pace: Callable[[], Awaitable[None]] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.client, self.store, self.config = client, store, config
        self._symbols, self._user_agent = symbols, user_agent
        self._pace = pace or edgar_pacer()
        self._sleep = sleep or asyncio.sleep
        self._clock = clock or (lambda: datetime.now(UTC))
        self._done: str | None = None  # the key of the last slot known to be journaled
        # Set once a poll gets past the weekend, scan-window and waiting gates in this process.
        self._pass_ran = False

    def _slots(self) -> list[tuple[time, str]]:
        return capture_slots(self.config.scheduler.suggestion_scan_times_et, self.config.filing_veto.pre_scan_minutes)

    def _in_scan_window(self, now: datetime) -> bool:
        return in_scan_window(now.astimezone(ET_TZ), self.config.scheduler.suggestion_scan_times_et)

    async def _fetch(self, url: str) -> bytes:
        return await edgar_get(self.client, url, self._user_agent)

    async def run_once(self, now: datetime | None = None) -> WorkerStatus:
        if not self._user_agent:
            return "missing_user_agent"
        now = now or self._clock()
        now_et = now.astimezone(ET_TZ)
        today = now_et.date()
        if today.weekday() >= 5:
            return "weekend"
        if self._in_scan_window(now):
            return "scan_window"
        due = [slot for at, slot in self._slots() if at <= now_et.time()]
        if not due:
            return "waiting"
        self._pass_ran = True
        key = capture_key(today, due[-1])
        if self._done == key:
            return "present"
        if await self.store.recorded(key):
            self._done = key
            return "present"
        payload = await capture_filings(
            self._fetch,
            await self._symbols(),
            today=today,
            lookback_days=max(self.config.filing_veto.windows().values()),
            pace=self._pace,
            sleep=self._sleep,
            clock=self._clock,
            stop=lambda: self._in_scan_window(self._clock()),
        )
        if payload is None:
            return "scan_window"
        await self.store.record(key, payload)
        self._done = key
        return "captured"

    async def freshness(self, now: datetime, status: str) -> tuple[bool, str]:
        """Ready while the newest pass covering ``COVERAGE_READY_FRACTION`` of its mapped symbols is at most
        ``filing_veto.max_age_seconds`` old; the detail counts the newest pass's outcomes.

        With no pass yet the worker is ready (``not_due``) while its status is ``waiting``, ``scan_window`` or
        ``weekend`` and no poll got past those gates in this process; otherwise it is not ready.
        """
        max_age = self.config.filing_veto.max_age_seconds
        # Every pass that can still be within the limit: one per slot per calendar day.
        passes = await self.store.recent(len(self._slots()) * (math.ceil(max_age / 86400) + 1))
        if not passes:
            if status in ("waiting", "scan_window", "weekend") and not self._pass_ran:
                return True, "not_due; no capture yet"
            return False, "no capture yet"

        def fresh(payload: Mapping[str, Any]) -> bool:
            return 0 <= (now - datetime.fromisoformat(payload["fetched_at"])).total_seconds() <= max_age

        latest = passes[0]
        summary = _describe(latest)
        good = next((payload for payload in passes if coverage_ready(payload)), None)
        if good is not None and fresh(good):
            suffix = "" if good is latest else f"; last {COVERAGE_READY_FRACTION:.0%} pass {good['fetched_at']}"
            return True, summary + suffix
        return False, f"{'low coverage' if fresh(latest) else 'stale'}: {summary}"


async def _wait(shutdown: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(shutdown.wait(), timeout=seconds)


async def _observe_safely(observe: Callable[[bool, str], Awaitable[None]], ok: bool, detail: str) -> None:
    try:
        await observe(ok, detail)
    except Exception as exc:
        logger.warning(
            "Filings-capture readiness could not be recorded",
            extra={"event": "filings_capture_readiness_failed", "reason": type(exc).__name__},
        )


async def run_filings_capture_worker(
    config: AppConfig,
    store: FilingsCaptureStore,
    observe: Callable[[bool, str], Awaitable[None]],
    shutdown: asyncio.Event,
    *,
    symbols: Callable[[], Awaitable[Sequence[str]]],
    environ: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    clock: Callable[[], datetime] | None = None,
    pace: Callable[[], Awaitable[None]] | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> None:
    """The daemon loop: one poll every ``POLL_SECONDS`` until shutdown; it never raises into the daemon.

    A missing ``EDGAR_USER_AGENT`` observes ``missing_user_agent`` and sends nothing. A failed poll is logged
    and observed by exception type name (and a ticker-map failure's reason) only, under the same freshness rule
    as a good poll, so a transient failure stays ready while a recent well-covered pass exists.
    """
    source = os.environ if environ is None else environ
    user_agent = (source.get(USER_AGENT_ENV) or "").strip()
    if not user_agent:
        logger.warning(
            "The EDGAR user agent is not set; the filings capture is idle",
            extra={"event": "filings_capture_missing_user_agent"},
        )
    now = clock or (lambda: datetime.now(UTC))
    async with httpx.AsyncClient(follow_redirects=False, timeout=TIMEOUT_SECONDS, transport=transport) as client:
        worker = FilingsCaptureWorker(
            client, store, config, symbols=symbols, user_agent=user_agent, pace=pace, sleep=sleep, clock=now
        )
        while not shutdown.is_set():
            status: str
            try:
                status = await worker.run_once()
            except Exception as exc:
                status = f"failed: {type(exc).__name__}"
                extra: dict[str, str] = {"event": "filings_capture_failed", "reason": type(exc).__name__}
                if isinstance(exc, FilingsCaptureFailed):
                    status += f" ({exc.reason})"
                    extra["detail"] = exc.reason
                logger.warning("Filings capture failed", extra=extra)
            if status == "missing_user_agent":
                await _observe_safely(observe, False, status)
            else:
                try:
                    ready, detail = await worker.freshness(now(), status)
                    await _observe_safely(observe, ready, f"{status}; {detail}")
                except Exception as exc:
                    logger.warning(
                        "Filings-capture freshness could not be read",
                        extra={"event": "filings_capture_failed", "reason": type(exc).__name__},
                    )
                    await _observe_safely(observe, False, type(exc).__name__)
            await _wait(shutdown, POLL_SECONDS)
