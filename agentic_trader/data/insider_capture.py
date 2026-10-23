"""The nightly SEC Form 4 capture for the insider watch and the watch history seed.

Spec: docs/superpowers/specs/2026-10-23-insider-watch-cards-design.md, section 2.

For each SEC filing date F the capture reads the EDGAR daily form index, keeps the ``4`` rows of
cohort-v3 issuers, fetches each filing's full-submission ``.txt``, parses it with
``edgar_form4.parse_form4`` (the data-set reader's rules) and journals one ``insider/form4/<F>`` event:
every qualifying transaction, the index and row counts, the per-rule drops and one record per
document. Raw bytes go to private files under ``<directory>/<F>/``. A date is journaled only once every
one of its documents was read, so the journal is never partial. A document that answers 404
(``document_not_found``), whose body exceeds ``MAX_DOCUMENT_BYTES`` (``document_too_large``) or that does
not parse (its exception type name) is recorded as that document's ``error``: the date is journaled and the
document's issuer is incomplete there (``WatchHistory.incomplete``), so one bad document never holds the
capture forever. Any other failed request (a transport error after its one retry, a 429 or a 5xx, an
index over the budget) raises and records nothing for that date, which the next poll retries.

Every calendar date is journaled in order, so the journal is contiguous: a weekend or a federal holiday
is ``no_index`` (``non_business_day``) without a request; a business day whose index is a 404 is held
as not yet published. Once a later date's index returns 200 in the same run, each held business day is
requested once more: a 200 captures it, a second 404 records it ``no_index`` (``closed_inferred``, a
closure inferred from the later index). Nothing after a held date is journaled until then. Every request
has a total deadline (``REQUEST_DEADLINE_SECONDS``), and index and document parsing run in worker threads.

The ``EDGAR_USER_AGENT`` contact is read from the environment by ``run_insider_capture_worker`` only,
sent as the request's ``User-Agent`` header, and never logged, journaled or written: failures are
logged and observed by exception type name.

``load_watch_history`` merges the pinned quarterly archives with the journaled captures (the archive
wins on every filing date its quarters cover; the captures continue it while contiguous), then applies
the study's plausibility filter and identity breaks.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import math
import os
import re
import time as monotonic_time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from functools import lru_cache, partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlparse

import httpx
import numpy as np
from pandas.tseries.holiday import USFederalHolidayCalendar
from sqlalchemy import select

from agentic_trader.data.edgar_form4 import IndexRow, parse_daily_index, parse_form4, select_form4
from agentic_trader.execution.durable import EventKind
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.apriori.catalog import load_insider_entry
from agentic_trader.research.apriori.watch_contract import WATCH_ENTRY
from agentic_trader.research.insider_filings import (
    DROP_RULES,
    REQUESTS_PER_SECOND,
    SEC_HOST,
    InsiderFilingStore,
    InsiderTransaction,
    Quarter,
    files_sha256,
    parse_quarter,
    quarters_between,
    symbol_ciks,
)
from agentic_trader.research.pooled.campaign import InsiderCampaignProtocol, InsiderFilingInputs, load_campaign_protocol
from agentic_trader.research.pooled.cohort import load_cohort
from agentic_trader.research.pooled.events import identity_breaks
from agentic_trader.research.pooled.insider_fields import (
    dollar_volume_series,
    load_insider_filings,
    plausible_transactions,
)
from agentic_trader.research.setups.card_stats_worker import in_scan_window
from agentic_trader.research.short_volume import write_private
from agentic_trader.runtime import state_directory
from agentic_trader.storage.models import DomainEventRecord


if TYPE_CHECKING:
    import pandas as pd

    from agentic_trader.config import AppConfig, InsiderWatchConfig
    from agentic_trader.data.corporate_actions import CorporateAction
    from agentic_trader.storage.workflow import WorkflowStore

__all__ = [
    "CAPTURE_AFTER_ET",
    "CAPTURE_MAX_AGE_DAYS",
    "CAPTURE_MAX_AGE_SECONDS",
    "CAPTURE_STREAM",
    "RETRY_SECONDS",
    "USER_AGENT_ENV",
    "WATCH_ENTRY",
    "CaptureResult",
    "CaptureScope",
    "CaptureStatus",
    "InsiderArchiveMismatch",
    "InsiderCaptureStore",
    "InsiderCaptureWorker",
    "WatchArchive",
    "WatchCohort",
    "WatchHistory",
    "capture_filing_dates",
    "capture_scope",
    "daily_index_url",
    "default_capture_directory",
    "edgar_business_day",
    "edgar_pacer",
    "load_watch_archive",
    "load_watch_cohort",
    "load_watch_history",
    "merge_watch_history",
    "run_insider_capture_worker",
]

logger = logging.getLogger("copilot")

CAPTURE_STREAM = "insider/form4"
_CAPTURE_LOCK = "insider-form4"
ARCHIVES_URL = f"https://{SEC_HOST}/Archives/"
CAPTURE_AFTER_ET = time(6, 0)
RETRY_SECONDS = 1800
# The corporate-actions limit: it spans a long weekend (Friday's filings, read Saturday, are 4 days old on Tuesday).
CAPTURE_MAX_AGE_DAYS = 4
CAPTURE_MAX_AGE_SECONDS = CAPTURE_MAX_AGE_DAYS * 86400
USER_AGENT_ENV = "EDGAR_USER_AGENT"
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
_TIMEOUT_SECONDS = 60.0
# The bounded total of one request attempt (connect, headers and the whole body).
REQUEST_DEADLINE_SECONDS = 120.0
# Document parse errors over this many trailing days are appended to the readiness detail.
RECENT_ERROR_DAYS = 30
_DOCUMENT_PATH = re.compile(r"edgar/data/\d{1,10}/(\d{10}-\d{2}-\d{6})\.txt")
_NON_BUSINESS_DAY = "non_business_day"
_CLOSED_INFERRED = "closed_inferred"
_UNPUBLISHED = "unpublished"
# Fixed per-document error tokens (a parse failure records its exception type name instead).
DOCUMENT_NOT_FOUND = "document_not_found"
DOCUMENT_TOO_LARGE = "document_too_large"


class BodyTooLarge(ValueError):
    """A response body exceeded ``MAX_DOCUMENT_BYTES``."""


class InsiderArchiveMismatch(ValueError):
    """The insider archive on disk is not the one the frozen protocol pins (``insider_filings.files_sha256``)."""


class CaptureStatus(StrEnum):
    CAPTURED = "captured"  # the index was read and every cohort Form 4 on it parsed
    NO_INDEX = "no_index"  # EDGAR published no index for the date (non-business day or closure)


def default_capture_directory() -> Path:
    """The private raw-document root: ``<state>/data/insider`` (outside the repository)."""
    return state_directory() / "data" / "insider"


@lru_cache(maxsize=32)
def _federal_holidays(year: int) -> frozenset[date]:
    days = USFederalHolidayCalendar().holidays(date(year - 1, 12, 1), date(year + 1, 1, 31))
    return frozenset(day.date() for day in days)


def edgar_business_day(day: date) -> bool:
    """A weekday that is not a US federal holiday (EDGAR's calendar, not the exchange's)."""
    return day.weekday() < 5 and day not in _federal_holidays(day.year)


def daily_index_url(day: date) -> str:
    quarter = (day.month - 1) // 3 + 1
    return f"{ARCHIVES_URL}edgar/daily-index/{day.year}/QTR{quarter}/form.{day:%Y%m%d}.idx"


def _index_name(day: date) -> str:
    return f"form.{day:%Y%m%d}.idx"


def edgar_pacer(per_second: float = REQUESTS_PER_SECOND) -> Callable[[], Awaitable[None]]:
    """At most ``per_second`` requests per second (the SEC fair-access pacing of the research acquisition)."""
    interval = 1.0 / per_second
    last = -math.inf

    async def pace() -> None:
        nonlocal last
        wait = last + interval - monotonic_time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        last = monotonic_time.monotonic()

    return pace


@dataclass(frozen=True)
class CaptureScope:
    """Which filings a capture reads (the cohort's issuer CIKs) and the parser's owner and plan rules."""

    cik_to_symbols: Mapping[int, tuple[str, ...]]
    owner_roles: tuple[str, ...]
    exclude_10b5_1: bool

    @property
    def sha256(self) -> str:
        body = "\n".join(
            [
                *(f"{cik}:{','.join(symbols)}" for cik, symbols in sorted(self.cik_to_symbols.items())),
                f"roles:{','.join(self.owner_roles)}",
                f"exclude_10b5_1:{self.exclude_10b5_1}",
            ]
        )
        return hashlib.sha256(body.encode()).hexdigest()


@dataclass(frozen=True)
class CaptureResult:
    """``recorded`` dates were journaled (``captured`` the ones with an index); ``pending`` is the first
    business day whose index is not yet published (nothing from it on was journaled); ``stopped`` means
    ``stop`` ended the run between dates."""

    recorded: tuple[date, ...]
    captured: tuple[date, ...]
    pending: date | None
    stopped: bool


class InsiderCaptureStore:
    """``insider/form4/<F>`` journal events (stream ``insider/form4``) and their private raw files."""

    def __init__(self, workflows: WorkflowStore, directory: Path):
        self.workflows, self.directory = workflows, directory

    async def captures(self) -> list[dict[str, Any]]:
        """Every journaled filing date's payload, in filing-date order."""
        rows = await self.workflows.events(CAPTURE_STREAM)
        return sorted((row["payload"] for row in rows), key=lambda payload: payload["filing_date"])

    async def latest_recorded_date(self) -> date | None:
        """The last journaled filing date (captured or without an index): where a catch-up resumes."""
        captures = await self.captures()
        return date.fromisoformat(captures[-1]["filing_date"]) if captures else None

    async def latest_captured_date(self) -> date | None:
        """The last filing date whose index was read: the readiness clock."""
        captured = [c for c in await self.captures() if c.get("status") == CaptureStatus.CAPTURED]
        return date.fromisoformat(captured[-1]["filing_date"]) if captured else None

    def _write(self, day: date, files: Mapping[str, bytes]) -> None:
        folder = self.directory / day.isoformat()
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        for name, body in files.items():
            write_private(folder / name, body)

    async def record(self, payload: Mapping[str, Any], files: Mapping[str, bytes]) -> bool:
        """Write ``files`` privately, then journal ``payload`` once per filing date; False when already journaled."""
        day = date.fromisoformat(payload["filing_date"])
        key = f"{CAPTURE_STREAM}/{day.isoformat()}"
        if files:
            await asyncio.to_thread(self._write, day, files)
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
                session, stream=CAPTURE_STREAM, kind=EventKind.INSIDER_FORM4_CAPTURE, payload=dict(payload), key=key
            )
        return True


async def _stream(client: httpx.AsyncClient, url: str, user_agent: str, *, missing_ok: bool) -> bytes | None:
    async with client.stream("GET", url, headers={"User-Agent": user_agent}, timeout=_TIMEOUT_SECONDS) as response:
        if missing_ok and response.status_code == 404:
            return None
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError(f"{url}: unexpected HTTP {response.status_code}")
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > MAX_DOCUMENT_BYTES:
                raise BodyTooLarge(f"{url}: exceeded the {MAX_DOCUMENT_BYTES}-byte budget")
            chunks.append(chunk)
        return b"".join(chunks)


async def _fetch(
    client: httpx.AsyncClient,
    url: str,
    user_agent: str,
    pace: Callable[[], Awaitable[None]],
    *,
    missing_ok: bool = False,
) -> bytes | None:
    """One paced GET on www.sec.gov (a transport error is retried once); a 404 is ``None`` when ``missing_ok``."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != SEC_HOST:
        raise ValueError(f"{url}: host is not https://{SEC_HOST}")
    await pace()
    try:
        async with asyncio.timeout(REQUEST_DEADLINE_SECONDS):
            return await _stream(client, url, user_agent, missing_ok=missing_ok)
    except httpx.TransportError:
        await pace()
        async with asyncio.timeout(REQUEST_DEADLINE_SECONDS):
            return await _stream(client, url, user_agent, missing_ok=missing_ok)


def _no_index(day: date, reason: str) -> dict[str, Any]:
    return {
        "filing_date": day.isoformat(),
        "status": CaptureStatus.NO_INDEX.value,
        "reason": reason,
        "index_url": daily_index_url(day),
        "index_sha256": None,
        "form4_rows": 0,
        "cohort_rows": 0,
        "documents": [],
        "transactions": [],
        "drops": {},
        "errors": {},
        "captured_at": datetime.now(UTC).isoformat(),
    }


def _transaction_json(transaction: InsiderTransaction) -> dict[str, Any]:
    return {
        "symbol": transaction.symbol,
        "filing_date": transaction.filing_date.isoformat(),
        "trans_date": transaction.trans_date.isoformat(),
        "code": transaction.code,
        "dollars": transaction.dollars,
        "owner_cik": transaction.owner_cik,
    }


async def _capture_day(
    client: httpx.AsyncClient,
    day: date,
    index: bytes,
    *,
    scope: CaptureScope,
    user_agent: str,
    pace: Callable[[], Awaitable[None]],
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """The journal payload and the private files of one published index; every document is read first."""
    rows = await asyncio.to_thread(parse_daily_index, index.decode("utf-8", errors="replace"))
    form4 = [row for row in rows if row.form == "4"]
    selected = select_form4(rows, scope.cik_to_symbols.keys())
    by_path: dict[str, IndexRow] = {}
    for row in selected:
        if _DOCUMENT_PATH.fullmatch(row.path) is None:
            raise ValueError(f"index path {row.path!r} is outside the EDGAR archive layout")
        by_path.setdefault(row.path, row)
    files = {_index_name(day): index}
    documents: list[dict[str, Any]] = []
    transactions: list[InsiderTransaction] = []
    drops = dict.fromkeys(DROP_RULES, 0)
    errors: dict[str, int] = {}
    for path, row in by_path.items():
        error: str | None = None
        found: list[InsiderTransaction] = []
        try:
            # An indexed document that is missing or over the budget is this document's error, not the date's.
            body = await _fetch(client, ARCHIVES_URL + path, user_agent, pace, missing_ok=True)
        except BodyTooLarge:
            body, error = None, DOCUMENT_TOO_LARGE
        else:
            if body is None:
                error = DOCUMENT_NOT_FOUND
        if body is not None:
            try:
                found, dropped = await asyncio.to_thread(
                    partial(
                        parse_form4,
                        body.decode("utf-8", errors="replace"),
                        filing_date=row.date_filed,
                        cik_to_symbols=scope.cik_to_symbols,
                        owner_roles=scope.owner_roles,
                        exclude_10b5_1=scope.exclude_10b5_1,
                    )
                )
            except ValueError as exc:  # recorded by type name; the rest of the date is still read
                error = type(exc).__name__
            else:
                for rule, count in dropped.items():
                    drops[rule] += count
            match = _DOCUMENT_PATH.fullmatch(path)
            assert match is not None
            files[f"{match.group(1)}.txt"] = body
        if error is not None:
            errors[error] = errors.get(error, 0) + 1
        transactions.extend(found)
        documents.append(
            {
                "path": path,
                "index_cik": row.cik,
                "date_filed": row.date_filed.isoformat(),
                "sha256": hashlib.sha256(body).hexdigest() if body is not None else None,
                "bytes": len(body) if body is not None else None,
                "transactions": len(found),
                "error": error,
            }
        )
    payload = {
        "filing_date": day.isoformat(),
        "status": CaptureStatus.CAPTURED.value,
        "reason": None,
        "index_url": daily_index_url(day),
        "index_sha256": hashlib.sha256(index).hexdigest(),
        "index_bytes": len(index),
        "form4_rows": len(form4),
        "cohort_rows": len(selected),
        "scope_sha256": scope.sha256,
        "documents": documents,
        "transactions": [_transaction_json(t) for t in transactions],
        "drops": drops,
        "errors": dict(sorted(errors.items())),
        "captured_at": datetime.now(UTC).isoformat(),
    }
    return payload, files


async def capture_filing_dates(
    client: httpx.AsyncClient,
    store: InsiderCaptureStore,
    dates: Iterable[date],
    *,
    scope: CaptureScope,
    user_agent: str,
    pace: Callable[[], Awaitable[None]],
    stop: Callable[[], bool] | None = None,
) -> CaptureResult:
    """Capture and journal ``dates`` in order (see the module docstring); raises on a failed request."""
    recorded: list[date] = []
    captured: list[date] = []
    held: list[tuple[date, str]] = []

    def result(*, stopped: bool) -> CaptureResult:
        return CaptureResult(tuple(recorded), tuple(captured), held[0][0] if held else None, stopped)

    for day in sorted(dates):
        if stop is not None and stop():
            return result(stopped=True)
        if not edgar_business_day(day):
            if held:
                held.append((day, _NON_BUSINESS_DAY))
            else:
                await store.record(_no_index(day, _NON_BUSINESS_DAY), {})
                recorded.append(day)
            continue
        index = await _fetch(client, daily_index_url(day), user_agent, pace, missing_ok=True)
        if index is None:
            held.append((day, _UNPUBLISHED))
            continue
        # A later index exists: ask once more for each held business day; a second 404 infers a closure.
        for held_day, reason in held:
            again = None
            if reason == _UNPUBLISHED:
                again = await _fetch(client, daily_index_url(held_day), user_agent, pace, missing_ok=True)
            if again is None:
                await store.record(_no_index(held_day, _CLOSED_INFERRED if reason == _UNPUBLISHED else reason), {})
            else:
                payload, files = await _capture_day(
                    client, held_day, again, scope=scope, user_agent=user_agent, pace=pace
                )
                await store.record(payload, files)
                captured.append(held_day)
            recorded.append(held_day)
        held.clear()
        payload, files = await _capture_day(client, day, index, scope=scope, user_agent=user_agent, pace=pace)
        await store.record(payload, files)
        recorded.append(day)
        captured.append(day)
    return result(stopped=False)


WorkerStatus = Literal["scan_window", "waiting", "present", "captured", "pending"]


class InsiderCaptureWorker:
    """One poll: capture every filing date since the last journaled one, through yesterday (New York).

    Polls inside a suggestion-scan window or before 06:00 New York do nothing; a backfill that reaches a
    scan window stops between dates. The first run starts at ``insider_watch.backfill_from``. Once the
    journal reaches yesterday the date is done (in memory, or by the journal after a restart).
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        store: InsiderCaptureStore,
        config: AppConfig,
        *,
        scope: Callable[[], Awaitable[CaptureScope]],
        user_agent: str,
        pace: Callable[[], Awaitable[None]],
        clock: Callable[[], datetime] | None = None,
    ):
        self.client, self.store, self.config = client, store, config
        self._scope_loader, self._user_agent, self._pace = scope, user_agent, pace
        self._clock = clock or (lambda: datetime.now(UTC))
        self._scope: CaptureScope | None = None
        self._done: date | None = None

    def _in_scan_window(self, now: datetime) -> bool:
        return in_scan_window(now.astimezone(ET_TZ), self.config.scheduler.suggestion_scan_times_et)

    async def run_once(self, now: datetime | None = None) -> WorkerStatus:
        now = now or self._clock()
        now_et = now.astimezone(ET_TZ)
        today = now_et.date()
        if self._in_scan_window(now):
            return "scan_window"
        if now_et.time() < CAPTURE_AFTER_ET:
            return "waiting"
        if self._done == today:
            return "present"
        latest = await self.store.latest_recorded_date()
        start = latest + timedelta(days=1) if latest is not None else self.config.insider_watch.backfill_from
        target = today - timedelta(days=1)  # today's index is incomplete until the evening
        if start > target:
            self._done = today
            return "present"
        if self._scope is None:
            self._scope = await self._scope_loader()
        result = await capture_filing_dates(
            self.client,
            self.store,
            [start + timedelta(days=i) for i in range((target - start).days + 1)],
            scope=self._scope,
            user_agent=self._user_agent,
            pace=self._pace,
            stop=lambda: self._in_scan_window(self._clock()),
        )
        if result.stopped:
            return "scan_window"
        if result.pending is not None:
            return "pending"
        self._done = today
        return "captured"

    async def freshness(self, now: datetime) -> tuple[bool, str]:
        """Ready while the latest captured filing date is at most ``CAPTURE_MAX_AGE_DAYS`` before today (New York)."""
        today = now.astimezone(ET_TZ).date()
        captures = await self.store.captures()
        captured = [c for c in captures if c.get("status") == CaptureStatus.CAPTURED]
        if not captured:
            return False, "no capture yet"
        latest = date.fromisoformat(captured[-1]["filing_date"])
        since = (today - timedelta(days=RECENT_ERROR_DAYS)).isoformat()
        errors = sum(sum(c.get("errors", {}).values()) for c in captured if c["filing_date"] >= since)
        suffix = f"; document errors ({RECENT_ERROR_DAYS} days): {errors}"
        age = (today - latest).days
        if age <= CAPTURE_MAX_AGE_DAYS:
            return True, f"latest filing date {latest.isoformat()}{suffix}"
        return False, f"stale: latest filing date {latest.isoformat()} ({age} days){suffix}"


async def _wait(shutdown: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(shutdown.wait(), timeout=seconds)


async def _observe_safely(observe: Callable[[bool, str], Awaitable[None]], ok: bool, detail: str) -> None:
    try:
        await observe(ok, detail)
    except Exception as exc:
        logger.warning(
            "Insider-capture readiness could not be recorded",
            extra={"event": "insider_capture_readiness_failed", "reason": type(exc).__name__},
        )


async def run_insider_capture_worker(
    config: AppConfig,
    store: InsiderCaptureStore,
    observe: Callable[[bool, str], Awaitable[None]],
    shutdown: asyncio.Event,
    *,
    scope: Callable[[], Awaitable[CaptureScope]],
    environ: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    clock: Callable[[], datetime] | None = None,
    pace: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """The daemon loop: one poll every ``RETRY_SECONDS`` until shutdown; it never raises into the daemon.

    Each poll observes readiness with the worker's status and the capture's freshness. A missing
    ``EDGAR_USER_AGENT`` observes ``missing_user_agent`` and sends nothing; a failed poll is logged and
    observed by exception type name only (an exception's text may carry request headers).
    """
    source = os.environ if environ is None else environ
    user_agent = (source.get(USER_AGENT_ENV) or "").strip()
    if not user_agent:
        logger.warning(
            "The EDGAR user agent is not set; the insider capture is idle",
            extra={"event": "insider_capture_missing_user_agent"},
        )
        while not shutdown.is_set():
            await _observe_safely(observe, False, "missing_user_agent")
            await _wait(shutdown, RETRY_SECONDS)
        return
    now = clock or (lambda: datetime.now(UTC))
    async with httpx.AsyncClient(follow_redirects=False, timeout=_TIMEOUT_SECONDS, transport=transport) as client:
        worker = InsiderCaptureWorker(
            client, store, config, scope=scope, user_agent=user_agent, pace=pace or edgar_pacer(), clock=now
        )
        while not shutdown.is_set():
            try:
                status = await worker.run_once()
                ready, detail = await worker.freshness(now())
                await observe(ready, f"{status}; {detail}")
            except Exception as exc:
                status_code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                logger.warning(
                    "Insider capture failed",
                    extra={"event": "insider_capture_failed", "reason": type(exc).__name__, "status": status_code},
                )
                await _observe_safely(observe, False, type(exc).__name__)
            await _wait(shutdown, RETRY_SECONDS)


# --- the watch history seed ---------------------------------------------------------------------


@dataclass(frozen=True)
class WatchCohort:
    """Cohort v3 and the insider filing rules the frozen ``insider`` v1 entry pins (through its pooled protocol)."""

    filings: InsiderFilingInputs
    symbols: tuple[str, ...]
    cik_to_symbols: Mapping[int, tuple[str, ...]]
    symbols_without_cik: tuple[str, ...]
    entry_sha256: str
    protocol_sha256: str
    cohort_sha256: str

    def scope(self) -> CaptureScope:
        return CaptureScope(
            cik_to_symbols=self.cik_to_symbols,
            owner_roles=tuple(self.filings.owner_roles),
            exclude_10b5_1=self.filings.exclude_10b5_1,
        )


def _verify_archive(archive_dir: Path, filings: InsiderFilingInputs) -> tuple[Quarter, ...]:
    """The pinned quarters, once the archive's digest (every quarter and the ticker map) is the pinned one."""
    pinned = quarters_between(parse_quarter(filings.quarters_from), parse_quarter(filings.quarters_through))
    mismatch = "the insider archive does not match the protocol's insider_filings.files_sha256"
    try:
        digest = files_sha256(InsiderFilingStore(archive_dir), pinned)
    except ValueError as exc:  # a pinned quarter or the ticker map is missing or not present
        raise InsiderArchiveMismatch(mismatch) from exc
    if digest != filings.files_sha256:
        raise InsiderArchiveMismatch(mismatch)
    return tuple(pinned)


def capture_scope(cohort: WatchCohort, archive_dir: Path) -> CaptureScope:
    """The capture's scope, only over the pinned archive: the issuer CIKs come from its ticker map, so a
    refreshed map would capture under a scope the history never reads (``InsiderArchiveMismatch``, which
    fails the poll by type name). Blocking: call it in a worker thread."""
    _verify_archive(archive_dir, cohort.filings)
    return cohort.scope()


def load_watch_cohort(repo_root: Path, archive_dir: Path) -> WatchCohort:
    """Load the frozen entry, its pooled protocol and cohort (each digest-checked) and the archive's ticker map."""
    loaded_entry = load_insider_entry(repo_root / WATCH_ENTRY)
    entry = loaded_entry.entry
    loaded = load_campaign_protocol(repo_root / entry.pooled_protocol)
    if loaded.sha256 != entry.pooled_protocol_sha256:
        raise ValueError(f"{entry.pooled_protocol} does not match the insider entry's pooled_protocol_sha256")
    protocol = loaded.protocol
    if not isinstance(protocol, InsiderCampaignProtocol):
        raise ValueError(f"{entry.pooled_protocol} is a {protocol.id} protocol, not an insider protocol")  # noqa: TRY004
    cohort = load_cohort(repo_root / protocol.cohort)
    if cohort.sha256 != protocol.cohort_sha256:
        raise ValueError(f"{protocol.cohort} does not match the pooled protocol's cohort_sha256")
    tickers = InsiderFilingStore(archive_dir).load_tickers()
    if tickers is None:
        raise ValueError("insider ticker map (company_tickers.json) is missing from the archive directory")
    symbols = tuple(cohort.cohort.symbols)
    cik_to_symbols, without_cik = symbol_ciks(tickers, symbols)
    return WatchCohort(
        filings=protocol.insider_inputs.insider_filings,
        symbols=symbols,
        cik_to_symbols=cik_to_symbols,
        symbols_without_cik=without_cik,
        entry_sha256=loaded_entry.sha256,
        protocol_sha256=loaded.sha256,
        cohort_sha256=cohort.sha256,
    )


@dataclass(frozen=True)
class WatchHistory:
    """The insider history a watch decision reads.

    ``transactions`` holds every cohort symbol (empty when it has none): the plausible qualifying trades
    from the archive and the contiguous live captures, including those filed before the calendar (the
    routine rule's input). ``latest_filing_date`` is the last filing date the history covers completely:
    the archive's end, extended day by day by the journaled captures until the first missing date or the
    first capture taken under another scope (``provenance["scope_mismatch"]``). Every calendar date is
    journaled (weekends and closures as ``no_index``) and the worker runs from 06:00 New York, so on a
    decision date D the history is current only when it reaches D - 1 (``current_for``); a pending
    business day, such as Good Friday's index still unpublished on the Monday, keeps it short.
    ``incomplete`` maps a symbol to the live filing dates with a document that could not be read (a parse
    error, a 404 or an over-budget body): its trades on those dates may be missing
    (``provenance["document_errors"]`` counts the documents).
    """

    transactions: Mapping[str, tuple[InsiderTransaction, ...]]
    identity_breaks: Mapping[str, date]
    latest_filing_date: date
    archive_through: date
    live_dates: tuple[date, ...]
    incomplete: Mapping[str, tuple[date, ...]]
    provenance: Mapping[str, Any]
    # Each cohort symbol's issuer CIK (from the archive's ticker map), for links to the issuer's filings.
    issuer_ciks: Mapping[str, int] = field(default_factory=dict)

    def current_for(self, decision_date: date) -> bool:
        """Whether every filing date through the calendar day before ``decision_date`` is in the history."""
        return self.latest_filing_date >= decision_date - timedelta(days=1)


def _quarter_of(day: date) -> Quarter:
    return Quarter(day.year, (day.month - 1) // 3 + 1)


def _quarter_end(quarter: Quarter) -> date:
    first_of_next = date(quarter.year + 1, 1, 1) if quarter.index == 4 else date(quarter.year, quarter.index * 3 + 1, 1)
    return first_of_next - timedelta(days=1)


def _live_transaction(row: Mapping[str, Any]) -> InsiderTransaction:
    code = row["code"]
    if code not in ("P", "S"):
        raise ValueError(f"journaled insider transaction code {code!r} is not P or S")
    return InsiderTransaction(
        symbol=str(row["symbol"]),
        filing_date=date.fromisoformat(row["filing_date"]),
        trans_date=date.fromisoformat(row["trans_date"]),
        code="P" if code == "P" else "S",
        dollars=float(row["dollars"]),
        owner_cik=int(row["owner_cik"]),
    )


@dataclass(frozen=True)
class WatchArchive:
    """The pinned quarterly archive's cohort transactions (digest-checked), read once and merged per date."""

    by_symbol: Mapping[str, tuple[InsiderTransaction, ...]]
    archive_through: date
    quarters_read: tuple[str, ...]


def load_watch_archive(archive_dir: Path, cohort: WatchCohort, first_day: date) -> WatchArchive:
    """Check the pinned digest over every quarter and read the quarters a calendar starting at ``first_day``
    needs: the routine rule looks one calendar year back from its first filing month. Blocking (seconds per
    quarter archive): call it in a worker thread."""
    filings = cohort.filings
    pinned = _verify_archive(archive_dir, filings)
    first_needed = _quarter_of(date(first_day.year - 1, first_day.month, 1))
    read = tuple(quarter for quarter in pinned if quarter >= first_needed)
    archived: Mapping[str, tuple[InsiderTransaction, ...]] = {}
    if read:
        narrowed = filings.model_copy(update={"quarters_from": str(read[0])})
        archived = load_insider_filings(archive_dir, narrowed, cohort.symbols).by_symbol
    return WatchArchive(
        by_symbol=archived, archive_through=_quarter_end(pinned[-1]), quarters_read=tuple(str(q) for q in read)
    )


def _build_history(
    archive_dir: Path,
    cohort: WatchCohort,
    days: tuple[date, ...],
    raw_bars: Mapping[str, pd.DataFrame],
    actions: Sequence[CorporateAction],
    captures: Sequence[Mapping[str, Any]],
) -> WatchHistory:
    archive = load_watch_archive(archive_dir, cohort, days[0])
    return merge_watch_history(archive, cohort, days, raw_bars, actions, captures)


def merge_watch_history(
    archive: WatchArchive,
    cohort: WatchCohort,
    days: tuple[date, ...],
    raw_bars: Mapping[str, pd.DataFrame],
    actions: Sequence[CorporateAction],
    captures: Sequence[Mapping[str, Any]],
) -> WatchHistory:
    """Continue ``archive`` with the contiguous journaled ``captures`` on ``days`` and apply the plausibility
    filter (``raw_bars``) and the identity breaks (``actions``). ``archive`` must cover ``days[0]``."""
    filings = cohort.filings
    archive_through = archive.archive_through
    archived = archive.by_symbol

    wanted = set(cohort.symbols)
    scope_sha256 = cohort.scope().sha256
    live: dict[str, list[InsiderTransaction]] = {}
    live_dates: list[date] = []
    incomplete: dict[str, list[date]] = {}
    document_errors = 0
    scope_mismatch: str | None = None
    in_archive = 0
    expected = archive_through + timedelta(days=1)
    for capture in captures:
        day = date.fromisoformat(capture["filing_date"])
        if day <= archive_through:  # the archive covers this filing date in full
            in_archive += 1
            continue
        if day != expected:  # a missing filing date: nothing after it is read
            break
        if capture.get("status") == CaptureStatus.CAPTURED and capture.get("scope_sha256") != scope_sha256:
            scope_mismatch = day.isoformat()  # read under another cohort or owner rule: not this history
            break
        for document in capture.get("documents", ()):
            if document.get("error") is None:
                continue
            document_errors += 1
            for symbol in cohort.cik_to_symbols.get(int(document["index_cik"]), ()):
                if day not in incomplete.setdefault(symbol, []):
                    incomplete[symbol].append(day)
        for row in capture.get("transactions", ()):
            transaction = _live_transaction(row)
            if transaction.symbol in wanted:
                live.setdefault(transaction.symbol, []).append(transaction)
        live_dates.append(day)
        expected = day + timedelta(days=1)

    ordinals = np.array([day.toordinal() for day in days], dtype=np.int64)
    transactions: dict[str, tuple[InsiderTransaction, ...]] = {}
    implausible = 0
    for symbol in cohort.symbols:
        combined = [*archived.get(symbol, ()), *live.get(symbol, ())]
        if not combined:
            transactions[symbol] = ()
            continue
        kept, dropped = plausible_transactions(
            combined, ordinals, dollar_volume_series(raw_bars.get(symbol), days), filings.max_trade_rel
        )
        transactions[symbol] = tuple(kept)
        implausible += dropped
    return WatchHistory(
        transactions=transactions,
        identity_breaks=identity_breaks(actions, cohort.symbols),
        latest_filing_date=expected - timedelta(days=1),
        archive_through=archive_through,
        live_dates=tuple(live_dates),
        incomplete={symbol: tuple(found) for symbol, found in incomplete.items()},
        issuer_ciks={symbol: cik for cik, symbols in cohort.cik_to_symbols.items() for symbol in symbols},
        provenance={
            "insider_files_sha256": filings.files_sha256,
            "quarters_read": list(archive.quarters_read),
            "archive_through": archive_through.isoformat(),
            "live_dates": len(live_dates),
            "live_dates_in_archive": in_archive,
            "scope_mismatch": scope_mismatch,
            "document_errors": document_errors,
            "archive_transactions": sum(len(found) for found in archived.values()),
            "live_transactions": sum(len(found) for found in live.values()),
            "implausible_size": implausible,
            "max_trade_rel": filings.max_trade_rel,
            "entry_sha256": cohort.entry_sha256,
            "protocol_sha256": cohort.protocol_sha256,
            "cohort_sha256": cohort.cohort_sha256,
        },
    )


async def load_watch_history(
    config: InsiderWatchConfig,
    store: InsiderCaptureStore,
    *,
    cohort: WatchCohort,
    trading_days: Sequence[date],
    raw_bars: Mapping[str, pd.DataFrame],
    actions: Sequence[CorporateAction],
) -> WatchHistory:
    """Merge the pinned archive (``config.archive_dir``) with the journaled captures on ``trading_days``.

    ``raw_bars`` are the unadjusted daily bars the plausibility filter's dollar volume reads (a symbol
    without them keeps its trades, as in the study); ``actions`` give the identity breaks. Only the archive
    quarters the routine rule needs (one year before the calendar's first month on) are read, but the
    pinned digest is checked over every quarter. The archive read runs in a worker thread.
    """
    days = tuple(trading_days)
    if not days or list(days) != sorted(set(days)):
        raise ValueError("trading_days must be non-empty, sorted and distinct")
    captures = await store.captures()
    return await asyncio.to_thread(_build_history, config.archive_dir, cohort, days, raw_bars, actions, captures)
