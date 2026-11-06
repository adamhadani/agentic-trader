"""The daily dated capture of the Nasdaq forward earnings calendar.

Spec: docs/superpowers/specs/2026-11-06-earnings-capture-design.md, sections 4-6.

Once per New York weekday, after 06:00 and outside every suggestion-scan window, the worker reads one
Nasdaq calendar page per weekday in ``[today, today + horizon_days]`` (one request per second, at most one
retry per request) and journals one ``earnings/calendar/<capture_date>`` event holding every forward row and
each page's body digest. The compressed raw bodies go to private files first. A capture is all or nothing: any
target still unaccepted after its retry raises ``CaptureIncomplete`` and nothing is journaled; a scan window
that opens between two requests stops the pass quietly.

Nothing journaled, observed or logged carries exception text from the Nasdaq request, only type names.
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import hashlib
import logging
import time as monotonic_time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import httpx
from sqlalchemy import select

from agentic_trader.agent.earnings import NASDAQ_REQUEST_HEADERS, EarningsTiming, ForwardRow, parse_forward_rows
from agentic_trader.constants import NASDAQ_EARNINGS_CALENDAR_URL
from agentic_trader.execution.durable import EventKind
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.setups.card_stats_worker import in_scan_window
from agentic_trader.research.short_volume import write_private
from agentic_trader.runtime import state_directory
from agentic_trader.storage.models import DomainEventRecord


if TYPE_CHECKING:
    from agentic_trader.config import AppConfig
    from agentic_trader.storage.workflow import WorkflowStore

__all__ = [
    "CAPTURE_AFTER_ET",
    "CAPTURE_MAX_AGE_DAYS",
    "CAPTURE_MAX_AGE_SECONDS",
    "CAPTURE_STREAM",
    "COLUMNS",
    "POLL_SECONDS",
    "CaptureIncomplete",
    "CapturedReports",
    "EarningsCaptureStore",
    "EarningsCaptureWorker",
    "capture_calendar",
    "capture_targets",
    "default_capture_directory",
    "run_earnings_capture_worker",
]

logger = logging.getLogger("copilot")

CAPTURE_STREAM = "earnings/calendar"
_CAPTURE_LOCK = "earnings-calendar"
SOURCE = "nasdaq-calendar"
CAPTURE_AFTER_ET = time(6, 0)
POLL_SECONDS = 1800
REQUEST_INTERVAL_SECONDS = 1.0
RETRY_DELAY_SECONDS = 5.0
REQUEST_TIMEOUT_SECONDS = 15.0
# The insider capture's limit: it spans a long weekend (Friday's capture is 4 days old on a Tuesday after a holiday).
CAPTURE_MAX_AGE_DAYS = 4
CAPTURE_MAX_AGE_SECONDS = 4 * 86400
COLUMNS = (
    "symbol",
    "timing",
    "fiscal_quarter",
    "eps_forecast",
    "n_estimates",
    "last_year_report_date",
    "last_year_eps",
    "market_cap",
)

WorkerStatus = Literal["weekend", "scan_window", "waiting", "present", "captured"]


class CaptureIncomplete(RuntimeError):
    """A target page stayed unaccepted: nothing is journaled. ``reason`` never carries exception text."""

    def __init__(self, target: date, reason: str):
        super().__init__(f"{target.isoformat()}: {reason}")
        self.target, self.reason = target, reason


@dataclass(frozen=True)
class CapturedReports:
    """One capture's reports over a date range (``EarningsCaptureStore.reports_between``).

    ``rows`` maps each date of the range the capture holds (weekdays; a capture never targets a weekend) to its
    ``(SYMBOL, timing)`` pairs in served order; an empty tuple is a captured day with no report.
    """

    capture_date: date
    rows: Mapping[date, tuple[tuple[str, EarningsTiming], ...]]


def _captured_timing(value: Any) -> EarningsTiming:
    """A stored timing value; anything unrecognised reads as unspecified (it exits on both sides of a night)."""
    try:
        return EarningsTiming(value)
    except ValueError:
        return EarningsTiming.UNSPECIFIED


def _captured_reports(
    payload: Mapping[str, Any], start: date, end: date, *, today: date, max_age_days: int
) -> CapturedReports | None:
    """``reports_between`` over one payload; raises KeyError/IndexError/TypeError/ValueError when it is malformed."""
    capture_date = date.fromisoformat(payload["capture_date"])
    if (today - capture_date).days > max_age_days:
        return None
    columns, served = payload["columns"], payload["rows"]
    if not isinstance(columns, list) or not isinstance(served, dict):
        raise TypeError("columns must be a list and rows an object")
    symbol_at, timing_at = columns.index("symbol"), columns.index("timing")
    rows: dict[date, tuple[tuple[str, EarningsTiming], ...]] = {}
    day = start
    while day <= end:
        day_rows = served.get(day.isoformat())
        if day_rows is None:
            if day.weekday() < 5:
                return None
        else:
            if not isinstance(day_rows, list):
                raise TypeError("a day's rows must be a list")
            parsed: list[tuple[str, EarningsTiming]] = []
            for row in day_rows:
                if not isinstance(row, list) or not isinstance(row[symbol_at], str) or not row[symbol_at]:
                    raise TypeError("a row must be a list with a symbol")
                parsed.append((row[symbol_at].upper(), _captured_timing(row[timing_at])))
            rows[day] = tuple(parsed)
        day += timedelta(days=1)
    return CapturedReports(capture_date=capture_date, rows=rows)


def default_capture_directory() -> Path:
    return state_directory() / "data" / "earnings_calendar"


def capture_targets(capture_date: date, horizon_days: int) -> list[date]:
    """The weekdays in ``[capture_date, capture_date + horizon_days]``, in date order."""
    days = (capture_date + timedelta(days=i) for i in range(horizon_days + 1))
    return [day for day in days if day.weekday() < 5]


def _row_values(row: ForwardRow) -> list[Any]:
    return [
        row.symbol,
        row.timing.value,
        row.fiscal_quarter,
        row.eps_forecast,
        row.n_estimates,
        row.last_year_report_date.isoformat() if row.last_year_report_date else None,
        row.last_year_eps,
        row.market_cap,
    ]


class EarningsCaptureStore:
    """``earnings/calendar/<D>`` journal events (stream ``earnings/calendar``) and their private raw files."""

    def __init__(self, workflows: WorkflowStore, directory: Path):
        self.workflows, self.directory = workflows, directory

    @staticmethod
    def _key(capture_date: date) -> str:
        return f"{CAPTURE_STREAM}/{capture_date.isoformat()}"

    async def recorded(self, capture_date: date) -> bool:
        async with self.workflows.db.session_factory() as session:
            existing = await session.scalar(
                select(DomainEventRecord.id).where(
                    DomainEventRecord.scope == self.workflows.scope,
                    DomainEventRecord.event_key == self._key(capture_date),
                )
            )
        return existing is not None

    async def latest(self) -> dict[str, Any] | None:
        """The newest capture: its ``capture_date`` (a date), ``pages`` count and total ``rows`` count."""
        events = await self.workflows.events(CAPTURE_STREAM, limit=1)
        if not events:
            return None
        payload = events[0]["payload"]
        return {
            "capture_date": date.fromisoformat(payload["capture_date"]),
            "pages": len(payload["pages"]),
            "rows": sum(len(rows) for rows in payload["rows"].values()),
        }

    async def reports_between(
        self, start: date, end: date, *, today: date, max_age_days: int = CAPTURE_MAX_AGE_DAYS
    ) -> CapturedReports | None:
        """The latest capture's reports for ``[start, end]``, or None when it cannot vouch for that range.

        None when there is no capture, the latest is more than ``max_age_days`` before ``today`` (New York), a
        weekday of the range is missing from its ``rows``, or the payload is malformed (logged by type name; the
        capture is then unusable, never an error). A capture targets weekdays only (``capture_targets``), so a
        weekend date is never required and has no key.
        """
        events = await self.workflows.events(CAPTURE_STREAM, limit=1)
        if not events:
            return None
        try:
            return _captured_reports(events[0]["payload"], start, end, today=today, max_age_days=max_age_days)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            logger.warning(
                "Earnings capture payload unreadable (%s); treated as unusable",
                type(exc).__name__,
                extra={"event": "earnings_capture_unreadable", "reason": type(exc).__name__},
            )
            return None

    def _write(self, capture_date: date, bodies: Mapping[date, bytes]) -> None:
        folder = self.directory / capture_date.isoformat()
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        for target, body in bodies.items():
            write_private(folder / f"{target.isoformat()}.json.gz", gzip.compress(body))

    async def record(self, payload: Mapping[str, Any], bodies: Mapping[date, bytes]) -> bool:
        """Write the bodies privately, then journal ``payload`` once per capture date; False when already journaled."""
        capture_date = date.fromisoformat(payload["capture_date"])
        if bodies:
            await asyncio.to_thread(self._write, capture_date, bodies)
        async with self.workflows.db.session_factory() as session, session.begin():
            await self.workflows.lock(session, resource=_CAPTURE_LOCK)
            existing = await session.scalar(
                select(DomainEventRecord.id).where(
                    DomainEventRecord.scope == self.workflows.scope,
                    DomainEventRecord.event_key == self._key(capture_date),
                )
            )
            if existing is not None:
                return False
            await self.workflows.append(
                session,
                stream=CAPTURE_STREAM,
                kind=EventKind.EARNINGS_CALENDAR_CAPTURE,
                payload=dict(payload),
                key=self._key(capture_date),
            )
        return True


def _spacer() -> Callable[[], Awaitable[None]]:
    last = [0.0]

    async def pace() -> None:
        wait = last[0] + REQUEST_INTERVAL_SECONDS - monotonic_time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        last[0] = monotonic_time.monotonic()

    return pace


def _rejection(response: httpx.Response) -> tuple[Any, str | None]:
    """The parsed payload of an accepted response, else ``(None, reason)``; mirrors ``reported_rows``."""
    if response.status_code != 200:
        return None, f"HTTP {response.status_code}"
    try:
        payload = response.json()
    except ValueError:
        return None, "body is not JSON"
    if not isinstance(payload, dict):
        return None, "body is not a JSON object"
    status = payload.get("status")
    code = status.get("rCode") if isinstance(status, dict) else None
    if code != 200:
        return None, f"status.rCode {code!r}"
    return payload, None


def _retryable(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


async def _fetch_page(
    client: httpx.AsyncClient, target: date, sleep: Callable[[float], Awaitable[None]]
) -> tuple[Any, bytes]:
    """One accepted page and its served bytes; one retry on a transport error, 429 or 5xx."""
    reason = ""
    for attempt in range(2):
        try:
            response = await client.get(
                NASDAQ_EARNINGS_CALENDAR_URL,
                params={"date": target.isoformat()},
                headers=NASDAQ_REQUEST_HEADERS,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.TransportError as exc:
            reason = type(exc).__name__
        else:
            payload, rejection = _rejection(response)
            if rejection is None:
                return payload, response.content
            reason = rejection
            if not _retryable(response.status_code):
                break
        if attempt == 0:
            await sleep(RETRY_DELAY_SECONDS)
    raise CaptureIncomplete(target, reason)


async def capture_calendar(
    client: httpx.AsyncClient,
    capture_date: date,
    targets: Sequence[date],
    *,
    horizon_days: int,
    clock: Callable[[], datetime],
    pace: Callable[[], Awaitable[None]],
    sleep: Callable[[float], Awaitable[None]],
    stop: Callable[[], bool],
) -> tuple[dict[str, Any] | None, dict[date, bytes]]:
    """Read every target page in date order: ``(payload, bodies)``, or ``(None, {})`` once ``stop()`` is true."""
    started_at = clock()
    pages: list[dict[str, Any]] = []
    rows: dict[str, list[list[Any]]] = {}
    bodies: dict[date, bytes] = {}
    for target in targets:
        if stop():
            return None, {}
        await pace()
        payload, body = await _fetch_page(client, target, sleep)
        parsed, skipped = parse_forward_rows(payload, target)
        data = payload.get("data")
        as_of = data.get("asOf") if isinstance(data, dict) else None
        pages.append(
            {
                "date": target.isoformat(),
                "fetched_at": clock().isoformat(),
                "as_of": as_of if isinstance(as_of, str) else None,
                "rows": len(parsed),
                "skipped": skipped,
                "body_sha256": hashlib.sha256(body).hexdigest(),
            }
        )
        rows[target.isoformat()] = [_row_values(row) for row in parsed]
        bodies[target] = body
    result = {
        "capture_date": capture_date.isoformat(),
        "started_at": started_at.isoformat(),
        "completed_at": clock().isoformat(),
        "source": SOURCE,
        "horizon_days": horizon_days,
        "columns": list(COLUMNS),
        "pages": pages,
        "rows": rows,
    }
    return result, bodies


class EarningsCaptureWorker:
    """One poll of the daily capture (spec section 5): weekend, scan window and 06:00 gates, then one capture."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        store: EarningsCaptureStore,
        config: AppConfig,
        *,
        clock: Callable[[], datetime] | None = None,
        pace: Callable[[], Awaitable[None]] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ):
        self.client, self.store, self.config = client, store, config
        self._clock = clock or (lambda: datetime.now(UTC))
        self._pace = pace or _spacer()
        self._sleep = sleep or asyncio.sleep
        self._done: date | None = None
        # Set once a pass gets past the weekend, scan-window and waiting gates in this process.
        self._pass_ran = False

    def _in_scan_window(self, now: datetime) -> bool:
        return in_scan_window(now.astimezone(ET_TZ), self.config.scheduler.suggestion_scan_times_et)

    async def run_once(self, now: datetime | None = None) -> WorkerStatus:
        now = now or self._clock()
        now_et = now.astimezone(ET_TZ)
        today = now_et.date()
        if today.weekday() >= 5:
            return "weekend"
        if self._in_scan_window(now):
            return "scan_window"
        if now_et.time() < CAPTURE_AFTER_ET:
            return "waiting"
        self._pass_ran = True
        if self._done == today:
            return "present"
        if await self.store.recorded(today):
            self._done = today
            return "present"
        payload, bodies = await capture_calendar(
            self.client,
            today,
            capture_targets(today, self.config.earnings_capture.horizon_days),
            horizon_days=self.config.earnings_capture.horizon_days,
            clock=self._clock,
            pace=self._pace,
            sleep=self._sleep,
            stop=lambda: self._in_scan_window(self._clock()),
        )
        if payload is None:
            return "scan_window"
        await self.store.record(payload, bodies)
        self._done = today
        return "captured"

    async def freshness(self, now: datetime, status: str) -> tuple[bool, str]:
        """Ready while the latest capture is at most ``CAPTURE_MAX_AGE_DAYS`` before today (New York).

        With no capture yet the worker is ready (``not_due``) while its status is ``waiting``, ``scan_window``
        or ``weekend`` and no pass has run in this process; otherwise it is not ready.
        """
        today = now.astimezone(ET_TZ).date()
        latest = await self.store.latest()
        if latest is None:
            if status in ("waiting", "scan_window", "weekend") and not self._pass_ran:
                return True, "not_due; no capture yet"
            return False, "no capture yet"
        age = (today - latest["capture_date"]).days
        label = latest["capture_date"].isoformat()
        if age <= CAPTURE_MAX_AGE_DAYS:
            return True, f"latest capture {label} ({latest['pages']} pages, {latest['rows']} rows)"
        return False, f"stale: latest capture {label} ({age} days)"


async def _wait(shutdown: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(shutdown.wait(), timeout=seconds)


async def _observe_safely(observe: Callable[[bool, str], Awaitable[None]], ok: bool, detail: str) -> None:
    try:
        await observe(ok, detail)
    except Exception as exc:
        logger.warning(
            "Earnings-capture readiness could not be recorded",
            extra={"event": "earnings_capture_readiness_failed", "reason": type(exc).__name__},
        )


async def run_earnings_capture_worker(
    config: AppConfig,
    store: EarningsCaptureStore,
    observe: Callable[[bool, str], Awaitable[None]],
    shutdown: asyncio.Event,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    clock: Callable[[], datetime] | None = None,
    pace: Callable[[], Awaitable[None]] | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> None:
    """The daemon loop: one poll every ``POLL_SECONDS`` until shutdown; it never raises into the daemon.

    A failed poll is logged and observed by exception type name only, under the same freshness rule as a
    good poll: a transient failure stays ready while a recent capture exists.
    """
    now = clock or (lambda: datetime.now(UTC))
    async with httpx.AsyncClient(follow_redirects=False, transport=transport) as client:
        worker = EarningsCaptureWorker(client, store, config, clock=now, pace=pace, sleep=sleep)
        while not shutdown.is_set():
            status = "failed"
            try:
                status = await worker.run_once()
            except Exception as exc:
                status = f"failed: {type(exc).__name__}"
                extra: dict[str, str] = {"event": "earnings_capture_failed", "reason": type(exc).__name__}
                if isinstance(exc, CaptureIncomplete):
                    extra |= {"target": exc.target.isoformat(), "detail": exc.reason}
                logger.warning("Earnings capture failed", extra=extra)
            try:
                ready, detail = await worker.freshness(now(), status)
                await _observe_safely(observe, ready, f"{status}; {detail}")
            except Exception as exc:
                logger.warning(
                    "Earnings-capture freshness could not be read",
                    extra={"event": "earnings_capture_failed", "reason": type(exc).__name__},
                )
                await _observe_safely(observe, False, type(exc).__name__)
            await _wait(shutdown, POLL_SECONDS)
