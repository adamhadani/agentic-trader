"""Digest-pinned cached acquisition of FINRA Reg SHO daily short-sale volume files.

One consolidated file per NYSE session (``CNMSshvol{YYYYMMDD}.txt``), fetched paced and
saved verbatim with its SHA-256 and receipt times. FINRA answers 403 for a file that does
not exist; an absent session fails closed unless the caller lists it in ``allow_absent``
and is then recorded by a meta file only. A file that fails validation is kept on disk with
status ``invalid`` and the acquisition raises. The files digest binds every session of a
window: its present files' hashes and its absences. Research only: no database, broker or
Telegram.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import tempfile
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import httpx


__all__ = [
    "FINRA_DAILY_URL",
    "MAX_FILE_BYTES",
    "REQUESTS_PER_SECOND",
    "ShortVolumeRow",
    "ShortVolumeStore",
    "acquire_short_volume",
    "files_sha256",
    "parse_file",
    "read_sessions",
    "write_private",
]

FINRA_DAILY_URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{stamp}.txt"
MAX_FILE_BYTES = 8 * 1024 * 1024
REQUESTS_PER_SECOND = 4
MIN_ROWS = 1000
_HEADER = "Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market"
_TIMEOUT_SECONDS = 20.0
_ABSENT_STATUSES = frozenset({403, 404})

_META_KEYS = frozenset({"sha256", "requested_at", "received_at", "status", "bytes", "rows"})

Status = Literal["present", "absent", "invalid"]


@dataclass(frozen=True)
class ShortVolumeRow:
    date: date
    symbol: str
    short_volume: float
    short_exempt_volume: float
    total_volume: float
    market: str


def _volume(text: str, line_no: int) -> float:
    try:
        value = float(text)
    except ValueError as exc:
        raise ValueError(f"line {line_no}: non-numeric volume {text!r}") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"line {line_no}: invalid volume {text!r}")
    return value


def parse_file(text: str, expected_date: date) -> tuple[ShortVolumeRow, ...]:
    """Parse one daily file; raise ``ValueError`` on any structural or numeric defect.

    The final non-blank line must be FINRA's integer data-row count; a file without it is a
    truncated download.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _HEADER:
        raise ValueError("unexpected header")
    while len(lines) > 1 and not lines[-1].strip():
        lines.pop()
    if len(lines) < 2 or not lines[-1].strip().isdigit():
        raise ValueError("missing trailer line")
    trailer = int(lines.pop().strip())
    stamp = expected_date.strftime("%Y%m%d")
    rows: list[ShortVolumeRow] = []
    for line_no, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        parts = line.strip().split("|")
        if len(parts) != 6:
            raise ValueError(f"line {line_no}: expected 6 fields, got {len(parts)}")
        if parts[0] != stamp:
            raise ValueError(f"line {line_no}: row dated {parts[0]!r}, expected {stamp}")
        rows.append(
            ShortVolumeRow(
                date=expected_date,
                symbol=parts[1].replace("/", "."),
                short_volume=_volume(parts[2], line_no),
                short_exempt_volume=_volume(parts[3], line_no),
                total_volume=_volume(parts[4], line_no),
                market=parts[5],
            )
        )
    if trailer != len(rows):
        raise ValueError(f"trailer row count {trailer} does not match {len(rows)} data rows")
    if len(rows) < MIN_ROWS:
        raise ValueError(f"only {len(rows)} rows; at least {MIN_ROWS} expected")
    return tuple(rows)


def write_private(path: Path, data: bytes) -> None:
    """Atomic, fsynced write of a mode-0600 file (temp file renamed onto ``path``)."""
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


class ShortVolumeStore:
    """``<directory>/<YYYY-MM-DD>.txt`` (raw bytes) plus ``<YYYY-MM-DD>.meta.json``.

    An ``absent`` session has the meta file only. Reading never creates the directory.
    """

    def __init__(self, directory: Path):
        self.directory = directory

    def paths(self, day: date) -> tuple[Path, Path]:
        stem = day.isoformat()
        return self.directory / f"{stem}.txt", self.directory / f"{stem}.meta.json"

    def meta(self, day: date) -> dict[str, Any] | None:
        _, meta_path = self.paths(day)
        if not meta_path.exists():
            return None
        try:
            record = json.loads(meta_path.read_text())
        except (OSError, ValueError) as exc:
            raise ValueError(f"{meta_path}: unreadable meta file: {exc}") from exc
        if not isinstance(record, dict) or not record.keys() >= _META_KEYS:
            raise ValueError(f"{meta_path}: meta file is incomplete")
        return record

    def load(self, day: date) -> str | None:
        meta = self.meta(day)
        body_path, _ = self.paths(day)
        if meta is None or not body_path.exists():
            return None
        body = body_path.read_bytes()
        if hashlib.sha256(body).hexdigest() != meta["sha256"]:
            raise ValueError(f"{body_path}: SHA-256 does not match its meta file")
        return body.decode()

    def save(
        self,
        day: date,
        body: bytes,
        *,
        requested_at: datetime,
        received_at: datetime,
        status: Status,
        rows: int,
    ) -> None:
        if status == "absent" and body:
            raise ValueError(f"{day.isoformat()}: an absent session has no body")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        body_path, meta_path = self.paths(day)
        if status != "absent":
            write_private(body_path, body)
        record = {
            "sha256": hashlib.sha256(body).hexdigest(),
            "requested_at": requested_at.isoformat(),
            "received_at": received_at.isoformat(),
            "status": status,
            "bytes": len(body),
            "rows": rows,
        }
        write_private(meta_path, json.dumps(record, sort_keys=True).encode())


def files_sha256(store: ShortVolumeStore, sessions: Sequence[date]) -> str:
    """The files digest: SHA-256 over the sorted lines ``<date>:<sha256>`` of the present sessions
    and ``<date>:absent`` of the absent ones, so a window's absences are bound like its files.

    Every session needs a meta file with status ``present`` or ``absent``; anything else raises.
    """
    return hashlib.sha256("\n".join(_digest_lines(store, sessions)).encode()).hexdigest()


def _digest_lines(store: ShortVolumeStore, sessions: Sequence[date]) -> list[str]:
    lines: list[str] = []
    for day in sessions:
        meta = store.meta(day)
        if meta is None:
            raise ValueError(f"{day.isoformat()}: no meta file")
        if meta["status"] == "present":
            lines.append(f"{day.isoformat()}:{meta['sha256']}")
        elif meta["status"] == "absent":
            lines.append(f"{day.isoformat()}:absent")
        else:
            raise ValueError(f"{day.isoformat()}: short-volume file is {meta['status']}")
    return sorted(lines)


def read_sessions(store: ShortVolumeStore, sessions: Sequence[date]) -> Iterator[tuple[date, str | None]]:
    """Yield each session's body, verified against its meta file, or ``None`` for an absent session.

    A session without a meta file or a present session without its body is missing; an ``invalid``
    session or a body whose SHA-256 differs from its meta raises ``ValueError``.
    """
    for day in sessions:
        meta = store.meta(day)
        if meta is None:
            raise ValueError(f"short-volume file for {day.isoformat()} is missing from {store.directory}")
        if meta["status"] == "absent":
            yield day, None
            continue
        if meta["status"] != "present":
            raise ValueError(f"short-volume file for {day.isoformat()} is {meta['status']}")
        body = store.load(day)
        if body is None:
            raise ValueError(f"short-volume file for {day.isoformat()} is missing from {store.directory}")
        yield day, body


async def _fetch(client: httpx.AsyncClient, url: str) -> tuple[int, bytes]:
    async with client.stream("GET", url, timeout=_TIMEOUT_SECONDS) as response:
        if response.status_code in _ABSENT_STATUSES:
            return response.status_code, b""
        response.raise_for_status()
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise ValueError(f"{url}: exceeded the {MAX_FILE_BYTES}-byte budget")
            chunks.append(chunk)
        return response.status_code, b"".join(chunks)


async def _request(
    client: httpx.AsyncClient, url: str, pace: Callable[[], Awaitable[None]]
) -> tuple[int, bytes, datetime, datetime]:
    for attempt in (1, 2):
        await pace()
        requested_at = datetime.now(UTC)
        try:
            status, body = await _fetch(client, url)
        except httpx.TransportError:
            if attempt == 2:
                raise
            continue
        return status, body, requested_at, datetime.now(UTC)
    raise AssertionError("unreachable")


async def acquire_short_volume(
    client: httpx.AsyncClient,
    sessions: Sequence[date],
    store: ShortVolumeStore,
    *,
    allow_absent: Sequence[date] = (),
    pace: Callable[[], Awaitable[None]],
) -> dict[str, Any]:
    """Fetch (or reuse) one file per session and return the digest manifest."""
    allowed = set(allow_absent)
    reused = 0
    absent: list[date] = []
    for day in sessions:
        meta = store.meta(day)
        if meta is not None and meta["status"] == "present" and store.load(day) is not None:
            reused += 1
            continue
        url = FINRA_DAILY_URL.format(stamp=day.strftime("%Y%m%d"))
        status, body, requested_at, received_at = await _request(client, url, pace)
        if status in _ABSENT_STATUSES:
            if day not in allowed:
                raise ValueError(f"{day.isoformat()}: file absent (HTTP {status}); pass --allow-absent to accept")
            store.save(day, b"", requested_at=requested_at, received_at=received_at, status="absent", rows=0)
            absent.append(day)
            continue
        try:
            parsed = parse_file(body.decode(), day)
        except ValueError as exc:
            store.save(day, body, requested_at=requested_at, received_at=received_at, status="invalid", rows=0)
            raise ValueError(f"{day.isoformat()}: invalid file kept on disk: {exc}") from exc
        store.save(day, body, requested_at=requested_at, received_at=received_at, status="present", rows=len(parsed))
    lines = _digest_lines(store, sessions)
    present = [meta for meta in (store.meta(day) for day in sessions) if meta and meta["status"] == "present"]
    return {
        "window": [sessions[0].isoformat(), sessions[-1].isoformat()] if sessions else [],
        "sessions_requested": len(sessions),
        "sessions_present": len(present),
        "sessions_absent": [day.isoformat() for day in absent],
        "sessions_reused": reused,
        "files_sha256": files_sha256(store, sessions),
        "bytes_total": sum(meta["bytes"] for meta in present),
        "files": lines,
    }
