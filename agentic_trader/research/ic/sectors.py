"""SEC SIC codes mapped to the Fama-French 12 groups: a $0 sector map for the IC lane (research only).

``acquire_sectors`` maps each cohort ticker to a CIK with ``company_tickers.json``, then reads ``sic`` from
the SEC submissions API: paced, GET only, https, host allow-list, no redirects followed, one retry on a
transport error. A failed name is recorded with its error's type name only. The User-Agent (an email
address) never enters the document.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import numpy as np

from agentic_trader.research.insider_filings import TICKERS_URL, _fetch, _validate_tickers


__all__ = [
    "ALLOWED_HOSTS",
    "SUBMISSIONS_URL",
    "UNKNOWN",
    "Ff12Map",
    "LoadedSectors",
    "acquire_sectors",
    "load_ff12",
    "load_sectors",
]

ALLOWED_HOSTS = frozenset({"www.sec.gov", "data.sec.gov"})
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
UNKNOWN = "unknown"
NO_TICKER = "no_ticker"
SECTORS_VERSION = "sectors-v1"


@dataclass(frozen=True)
class Ff12Map:
    ranges: tuple[tuple[str, int, int], ...]
    other: str
    sha256: str

    def group(self, sic: int | None) -> str:
        if sic is None:
            return UNKNOWN
        for name, low, high in self.ranges:
            if low <= sic <= high:
                return name
        return self.other


def load_ff12(path: Path) -> Ff12Map:
    raw = path.read_bytes()
    doc = json.loads(raw)
    ranges = tuple((name, int(low), int(high)) for name, spans in doc["groups"].items() for low, high in spans)
    ordered = sorted(ranges, key=lambda item: item[1])
    for (name_a, _, high_a), (name_b, low_b, _) in itertools.pairwise(ordered):
        if low_b <= high_a:
            raise ValueError(f"FF12 ranges overlap: {name_a} and {name_b}")
    if any(low > high for _, low, high in ranges):
        raise ValueError("FF12 range with low above high")
    return Ff12Map(ranges=ranges, other=str(doc.get("other_group", "Other")), sha256=hashlib.sha256(raw).hexdigest())


async def _request(client: httpx.AsyncClient, url: str, pace: Callable[[], Awaitable[None]]) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError("host is not on the SEC allow-list")
    await pace()
    try:
        return await _fetch(client, url)
    except httpx.TransportError:
        await pace()
        return await _fetch(client, url)


def _parse_sic(body: bytes) -> int | None:
    try:
        loaded = json.loads(body)
    except ValueError as exc:
        raise ValueError("submissions body is not JSON") from exc
    if not isinstance(loaded, dict):
        raise ValueError("submissions body is not a JSON object")  # noqa: TRY004
    raw = loaded.get("sic")
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


def _ticker_ciks(body: bytes) -> dict[str, int]:
    _validate_tickers(body)
    out: dict[str, int] = {}
    for entry in json.loads(body).values():
        out.setdefault(str(entry["ticker"]).upper(), int(entry["cik_str"]))
    return out


async def acquire_sectors(
    symbols: Sequence[str],
    *,
    client: httpx.AsyncClient,
    pace: Callable[[], Awaitable[None]],
    ff12: Ff12Map,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Map each symbol to ``{cik, sic, group}``; a failed name goes to ``failures`` as an error type name."""
    if client.follow_redirects:
        raise ValueError("a client that follows redirects can leave the SEC allow-list")
    ciks = _ticker_ciks(await _request(client, TICKERS_URL, pace))
    found: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}
    names = sorted(set(symbols))
    for done, symbol in enumerate(names, start=1):
        cik = ciks.get(symbol.upper()) or ciks.get(symbol.upper().replace(".", "-"))
        if cik is None:
            failures[symbol] = NO_TICKER
        else:
            try:
                sic = _parse_sic(await _request(client, SUBMISSIONS_URL.format(cik=cik), pace))
            except (httpx.HTTPError, ValueError) as exc:
                failures[symbol] = type(exc).__name__
            else:
                found[symbol] = {"cik": cik, "sic": sic, "group": ff12.group(sic)}
        if progress is not None:
            progress(done, len(names))
    return {
        "version": SECTORS_VERSION,
        "as_of": datetime.now(UTC).date().isoformat(),
        "ff12_sha256": ff12.sha256,
        "symbols": found,
        "failures": failures,
    }


@dataclass(frozen=True)
class LoadedSectors:
    document: dict[str, Any]
    sha256: str

    def groups(self, symbols: Sequence[str]) -> np.ndarray:
        table = self.document["symbols"]
        return np.array([table.get(symbol, {}).get("group", UNKNOWN) for symbol in symbols], dtype=str)


def load_sectors(path: Path, sha256: str | None = None) -> LoadedSectors:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if sha256 is not None and digest != sha256:
        raise ValueError(f"sector map digest {digest} does not match the pinned {sha256}")
    return LoadedSectors(document=json.loads(raw), sha256=digest)
