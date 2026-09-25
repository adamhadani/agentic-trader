"""Paced historical acquisition of the Nasdaq earnings calendar for a priori studies.

One GET per weekday, at most one request per ``interval_seconds``, with bounded retries
(GET only). Each accepted page is saved verbatim with its SHA-256 and receipt time; a
rerun reuses saved pages after re-checking their hashes and never re-fetches a saved
date. A date that still fails is recorded, not saved, so the caller can fail closed on
a gappy sample. Parsing uses the live blackout's parser (``agent.earnings``).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import tempfile
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx

from agentic_trader.agent.earnings import NASDAQ_REQUEST_HEADERS, CalendarRow, parse_calendar_payload
from agentic_trader.constants import NASDAQ_EARNINGS_CALENDAR_URL


__all__ = [
    "RETRY_DELAYS",
    "CalendarAcquisition",
    "CalendarPageStore",
    "acquire_calendar",
    "dedupe_rows",
    "weekdays",
]

logger = logging.getLogger(__name__)

RETRY_DELAYS: tuple[float, ...] = (2.0, 10.0)
_TIMEOUT_SECONDS = 15.0


def _write_private(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically and re-savably: a temp file (mode 0600,
    fsynced) is renamed onto ``path`` so a crash between the page and meta writes
    leaves at most an orphaned temp file, never a half-written or unreplaceable
    final file, and a later ``save`` for the same day simply overwrites it.
    """
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


class CalendarPageStore:
    """``<directory>/<YYYY-MM-DD>.json`` (raw bytes) plus ``<YYYY-MM-DD>.meta.json`` (hash, receipt)."""

    def __init__(self, directory: Path):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory = directory

    def _paths(self, day: date) -> tuple[Path, Path]:
        stem = day.isoformat()
        return self.directory / f"{stem}.json", self.directory / f"{stem}.meta.json"

    def load(self, day: date) -> bytes | None:
        page, meta = self._paths(day)
        if not page.exists() or not meta.exists():
            return None
        body = page.read_bytes()
        recorded = json.loads(meta.read_text())["sha256"]
        if hashlib.sha256(body).hexdigest() != recorded:
            raise ValueError(f"Calendar page {page} does not match its recorded SHA-256; refusing a corrupt cache")
        return body

    def save(self, day: date, body: bytes, received_at: datetime) -> None:
        page, meta = self._paths(day)
        _write_private(page, body)
        record = {"sha256": hashlib.sha256(body).hexdigest(), "received_at": received_at.isoformat()}
        _write_private(meta, json.dumps(record, sort_keys=True).encode())


@dataclass(frozen=True)
class CalendarAcquisition:
    rows: tuple[CalendarRow, ...]
    requested_dates: int
    fetched_dates: int
    reused_dates: int
    failed_dates: tuple[date, ...]

    @property
    def failed_fraction(self) -> float:
        return len(self.failed_dates) / self.requested_dates if self.requested_dates else 0.0


def weekdays(start: date, end: date) -> list[date]:
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def dedupe_rows(rows: Iterable[CalendarRow]) -> tuple[list[CalendarRow], int]:
    seen: set[tuple[str, date]] = set()
    kept: list[CalendarRow] = []
    duplicates = 0
    for row in rows:
        key = (row.symbol, row.date)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        kept.append(row)
    return kept, duplicates


async def _get_page(
    client: httpx.AsyncClient,
    day: date,
    interval_seconds: float,
    sleep: Callable[[float], Awaitable[None]],
    retry_delays: Sequence[float],
) -> bytes | None:
    """The page body for ``day`` when the endpoint returns HTTP 200 and a JSON object, else None."""
    for delay in (*retry_delays, None):
        await sleep(interval_seconds)
        try:
            response = await client.get(
                NASDAQ_EARNINGS_CALENDAR_URL, params={"date": day.isoformat()}, headers=NASDAQ_REQUEST_HEADERS
            )
            if response.status_code == 200 and isinstance(response.json(), dict):
                return response.content
            logger.warning("Nasdaq calendar HTTP %s for %s", response.status_code, day.isoformat())
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Nasdaq calendar request failed for %s: %s", day.isoformat(), exc)
        if delay is None:
            return None
        await sleep(delay)
    return None


async def acquire_calendar(
    days: Sequence[date],
    store: CalendarPageStore,
    *,
    interval_seconds: float,
    transport: httpx.AsyncBaseTransport | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    retry_delays: Sequence[float] = RETRY_DELAYS,
) -> CalendarAcquisition:
    rows: list[CalendarRow] = []
    fetched = reused = 0
    failed: list[date] = []
    async with httpx.AsyncClient(transport=transport, timeout=_TIMEOUT_SECONDS) as client:
        for day in days:
            body = store.load(day)
            if body is not None:
                reused += 1
            else:
                body = await _get_page(client, day, interval_seconds, sleep, retry_delays)
                if body is None:
                    failed.append(day)
                    continue
                store.save(day, body, datetime.now(UTC))
                fetched += 1
            rows.extend(parse_calendar_payload(json.loads(body), day))
    return CalendarAcquisition(
        rows=tuple(rows),
        requested_dates=len(days),
        fetched_dates=fetched,
        reused_dates=reused,
        failed_dates=tuple(failed),
    )
