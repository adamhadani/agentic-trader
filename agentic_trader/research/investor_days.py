"""Digest-pinned cached acquisition of SEC investor-day candidate filings (research only).

For each cohort issuer (CIK from the SEC ticker map, share classes grouped) it reads the submissions
document (to learn whether the issuer files 10-K/10-Q, i.e. is a domestic filer), pages the EDGAR
full-text search for 8-K exhibits that mention an investor or analyst day, and caches the ``.htm``,
``.html`` and ``.txt`` documents gzipped. Nothing is parsed or priced here. A 403 from any SEC host
stops the run. The ``EDGAR_USER_AGENT`` contact travels in request headers only and is never stored.
No database, broker or Telegram.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from agentic_trader.data.edgar_http import TICKERS_URL, edgar_fetch
from agentic_trader.research.insider_filings import symbol_ciks
from agentic_trader.research.short_volume import write_private


__all__ = [
    "ARCHIVE_URL",
    "MAX_HITS_PER_CIK",
    "QUERY",
    "SEARCH_URL",
    "SUBMISSIONS_URL",
    "InvestorDayStore",
    "IssuerRecord",
    "SearchHit",
    "acquire_investor_days",
    "content_sha256",
    "visible_from_submissions",
]

SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
QUERY = '"investor day" OR "analyst day" OR "capital markets day" OR "investor meeting" OR "analyst meeting"'
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{file}"
MAX_HITS_PER_CIK = 1000
_HTML_SUFFIXES = (".htm", ".html", ".txt")
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


@dataclass(frozen=True)
class SearchHit:
    accession: str
    file: str
    filed: date
    items: tuple[str, ...]
    file_type: str
    description: str


@dataclass(frozen=True)
class IssuerRecord:
    cik: int
    symbols: tuple[str, ...]
    visible: bool
    capped: bool
    hits: tuple[SearchHit, ...]
    doc_failures: Mapping[str, int] = field(default_factory=dict)

    @property
    def clean(self) -> bool:
        return not self.capped and not self.doc_failures


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _hit_json(hit: SearchHit) -> dict[str, Any]:
    return {
        "accession": hit.accession,
        "file": hit.file,
        "filed": hit.filed.isoformat(),
        "items": list(hit.items),
        "file_type": hit.file_type,
        "description": hit.description,
    }


def _hit_from_json(raw: Mapping[str, Any]) -> SearchHit:
    return SearchHit(
        accession=raw["accession"],
        file=raw["file"],
        filed=date.fromisoformat(raw["filed"]),
        items=tuple(raw["items"]),
        file_type=raw["file_type"],
        description=raw["description"],
    )


class InvestorDayStore:
    """``tickers.json``, ``submissions/``, ``hits.json`` and ``docs/{accession}_{file}.gz`` under ``root``."""

    def __init__(self, root: Path):
        self.root = root

    @property
    def tickers_path(self) -> Path:
        return self.root / "tickers.json"

    @property
    def hits_path(self) -> Path:
        return self.root / "hits.json"

    def submissions_path(self, cik: int) -> Path:
        return self.root / "submissions" / f"CIK{cik:010d}.json"

    def doc_path(self, accession: str, file: str) -> Path:
        return self.root / "docs" / f"{accession}_{_UNSAFE.sub('_', file)}.gz"

    def _write(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_private(path, data)

    def write_tickers(self, data: bytes) -> None:
        self._write(self.tickers_path, data)

    def write_submissions(self, cik: int, data: bytes) -> None:
        self._write(self.submissions_path(cik), data)

    def write_document(self, accession: str, file: str, data: bytes) -> None:
        self._write(self.doc_path(accession, file), gzip.compress(data, mtime=0))

    def document(self, accession: str, file: str) -> bytes | None:
        path = self.doc_path(accession, file)
        return gzip.decompress(path.read_bytes()) if path.exists() else None

    def write_hits(self, issuers: Sequence[IssuerRecord], unmapped: Sequence[str]) -> None:
        body = {
            "issuers": [
                {
                    "cik": record.cik,
                    "symbols": list(record.symbols),
                    "visible": record.visible,
                    "capped": record.capped,
                    "hits": [_hit_json(hit) for hit in record.hits],
                    "doc_failures": dict(record.doc_failures),
                }
                for record in issuers
            ],
            "unmapped": list(unmapped),
        }
        self._write(self.hits_path, _canonical(body))

    def _read_hits(self) -> dict[str, Any]:
        return json.loads(self.hits_path.read_text())

    def issuers(self) -> tuple[IssuerRecord, ...]:
        return tuple(
            IssuerRecord(
                cik=raw["cik"],
                symbols=tuple(raw["symbols"]),
                visible=raw["visible"],
                capped=raw["capped"],
                hits=tuple(_hit_from_json(hit) for hit in raw["hits"]),
                doc_failures=dict(raw["doc_failures"]),
            )
            for raw in self._read_hits()["issuers"]
        )

    def unmapped(self) -> tuple[str, ...]:
        return tuple(self._read_hits()["unmapped"])


def visible_from_submissions(document: Mapping[str, Any]) -> bool:
    forms = document.get("filings", {}).get("recent", {}).get("form", [])
    return any(str(form).startswith(("10-K", "10-Q")) for form in forms)


def content_sha256(store: InvestorDayStore) -> str:
    """SHA-256 over ``hits.json`` and the sorted ``"{accession}_{file} {sha256(document)}"`` lines."""
    lines = []
    for record in store.issuers():
        for hit in record.hits:
            body = store.document(hit.accession, hit.file)
            if body is not None:
                lines.append(f"{hit.accession}_{hit.file} {hashlib.sha256(body).hexdigest()}")
    digest = hashlib.sha256(store.hits_path.read_bytes())
    digest.update(b"\n" + "\n".join(sorted(set(lines))).encode())
    return digest.hexdigest()


async def _get(
    client: httpx.AsyncClient, url: str, user_agent: str, pace: Callable[[], Awaitable[None]], *, missing_ok: bool
) -> bytes | None:
    try:
        return await edgar_fetch(client, url, user_agent, pace, missing_ok=missing_ok)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            raise ValueError("SEC refused the request (403); stopping") from exc
        raise


async def _search(
    client: httpx.AsyncClient,
    cik: int,
    user_agent: str,
    pace: Callable[[], Awaitable[None]],
    filings_from: date,
    filings_through: date,
) -> tuple[list[SearchHit], bool]:
    hits: list[SearchHit] = []
    total = 0
    while True:
        params = {
            "q": QUERY,
            "forms": "8-K",
            "ciks": f"{cik:010d}",
            "dateRange": "custom",
            "startdt": filings_from.isoformat(),
            "enddt": filings_through.isoformat(),
            "from": len(hits),
        }
        body = await _get(client, f"{SEARCH_URL}?{urlencode(params)}", user_agent, pace, missing_ok=False)
        assert body is not None
        payload = json.loads(body)["hits"]
        total = int(payload["total"]["value"])
        page = payload["hits"]
        for row in page:
            accession, _, file = row["_id"].partition(":")
            source = row["_source"]
            hits.append(
                SearchHit(
                    accession=accession,
                    file=file,
                    filed=date.fromisoformat(source["file_date"]),
                    items=tuple(source.get("items") or ()),
                    file_type=source.get("file_type") or "",
                    description=source.get("file_description") or "",
                )
            )
        if not page or len(hits) >= min(total, MAX_HITS_PER_CIK):
            break
    return hits[:MAX_HITS_PER_CIK], total > MAX_HITS_PER_CIK


async def acquire_investor_days(
    client: httpx.AsyncClient,
    symbols: Sequence[str],
    store: InvestorDayStore,
    *,
    user_agent: str,
    filings_from: date,
    filings_through: date,
    pace: Callable[[], Awaitable[None]],
) -> dict[str, Any]:
    if not store.tickers_path.exists():
        body = await _get(client, TICKERS_URL, user_agent, pace, missing_ok=False)
        assert body is not None
        store.write_tickers(body)
    tickers_bytes = store.tickers_path.read_bytes()
    by_cik, unmapped = symbol_ciks(json.loads(tickers_bytes), symbols)

    failures: Counter[str] = Counter()
    fetched = reused = 0
    records: list[IssuerRecord] = []
    for cik, cik_symbols in by_cik.items():
        issuer_failures: Counter[str] = Counter()
        sub_path = store.submissions_path(cik)
        sub_body = sub_path.read_bytes() if sub_path.exists() else None
        if sub_body is None:
            try:
                sub_body = await _get(client, SUBMISSIONS_URL.format(cik=cik), user_agent, pace, missing_ok=False)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    raise
                issuer_failures["submissions_http_404"] += 1
            else:
                assert sub_body is not None
                store.write_submissions(cik, sub_body)
        visible = sub_body is not None and visible_from_submissions(json.loads(sub_body))
        try:
            hits, capped = await _search(client, cik, user_agent, pace, filings_from, filings_through)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            hits, capped = [], False
            issuer_failures["search_http_404"] += 1
        for hit in hits:
            if not hit.file.lower().endswith(_HTML_SUFFIXES):
                failures["not_html"] += 1
                continue
            if store.doc_path(hit.accession, hit.file).exists():
                reused += 1
                continue
            url = ARCHIVE_URL.format(cik=cik, accession_nodash=hit.accession.replace("-", ""), file=hit.file)
            document = await _get(client, url, user_agent, pace, missing_ok=True)
            if document is None:
                issuer_failures["http_404"] += 1
                continue
            store.write_document(hit.accession, hit.file, document)
            fetched += 1
        failures.update(issuer_failures)
        records.append(
            IssuerRecord(
                cik=cik,
                symbols=cik_symbols,
                visible=visible,
                capped=capped,
                hits=tuple(hits),
                doc_failures=dict(issuer_failures),
            )
        )
    store.write_hits(records, unmapped)
    return {
        "issuers": len(records),
        "hits": sum(len(record.hits) for record in records),
        "documents_fetched": fetched,
        "documents_reused": reused,
        "failures": dict(failures),
        "capped_ciks": [record.cik for record in records if record.capped],
        "unmapped": list(unmapped),
        "tickers_sha256": hashlib.sha256(tickers_bytes).hexdigest(),
        "content_sha256": content_sha256(store),
        "filings_from": filings_from.isoformat(),
        "filings_through": filings_through.isoformat(),
    }
