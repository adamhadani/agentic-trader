"""Digest-pinned cached acquisition of SEC investor-day candidate filings (research only).

For each cohort issuer (CIK from the SEC ticker map, share classes grouped) it reads the submissions
document (to learn whether the issuer files 10-K/10-Q, i.e. is a domestic filer), pages the EDGAR
full-text search for 8-K exhibits that mention an investor or analyst day, and caches the ``.htm``,
``.html`` and ``.txt`` documents gzipped. Nothing is parsed or priced here. A 403 from any SEC host
stops the run. Every GET retries a transport error, an HTTP 429 or a 5xx once (``TRANSIENT_RETRY``); any other
persistent failure of an issuer's submissions record, search or document (an HTTP status, a timeout, an
over-budget body or a malformed payload) is counted in that issuer's ``doc_failures`` by reason, which makes the
issuer unclean, and the run goes on. The ``EDGAR_USER_AGENT`` contact travels in request headers only and is
never stored or printed. No database, broker or Telegram.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from agentic_trader.data.edgar_http import TICKERS_URL, TRANSIENT_RETRY, edgar_fetch
from agentic_trader.research.insider_filings import symbol_ciks
from agentic_trader.research.short_volume import write_private


__all__ = [
    "ARCHIVE_URL",
    "MAX_HITS_PER_CIK",
    "PROGRESS_EVERY",
    "QUERY",
    "SEARCH_URL",
    "SUBMISSIONS_URL",
    "InvestorDayStore",
    "IssuerRecord",
    "SearchHit",
    "SecRefused",
    "acquire_investor_days",
    "check_coverage",
    "check_filing_range",
    "content_sha256",
    "visible_from_submissions",
]

SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
QUERY = '"investor day" OR "analyst day" OR "capital markets day" OR "investor meeting" OR "analyst meeting"'
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{file}"
MAX_HITS_PER_CIK = 1000
PROGRESS_EVERY = 50  # issuers between progress lines
_HTML_SUFFIXES = (".htm", ".html", ".txt")
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")
# A persistent request failure after the retry: an HTTP status, a transport error, the total deadline, or a
# ``ValueError`` from the helpers (an over-budget body, an unexpected 2xx status).
_FETCH_FAILURES = (httpx.HTTPError, TimeoutError, ValueError)
# A malformed JSON payload: undecodable (``ValueError``) or of the wrong shape.
_PAYLOAD_FAILURES = (KeyError, TypeError, ValueError, AttributeError)


class SecRefused(ValueError):
    """SEC answered 403: the acquisition stops."""


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
        return self.root / "docs" / f"{_UNSAFE.sub('_', accession)}_{_UNSAFE.sub('_', file)}.gz"

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


def check_coverage(
    issuers: Sequence[IssuerRecord], unmapped: Sequence[str], symbols: Iterable[str], *, what: str
) -> None:
    """Raise ``ValueError`` unless every issuer's symbols plus ``unmapped`` are exactly ``symbols``."""
    covered = {symbol for record in issuers for symbol in record.symbols} | set(unmapped)
    wanted = set(symbols)
    if covered != wanted:
        missing, extra = sorted(wanted - covered), sorted(covered - wanted)
        raise ValueError(
            f"the investor-day acquisition does not cover exactly {what}: "
            f"{len(missing)} missing {missing[:5]}, {len(extra)} extra {extra[:5]}"
        )


def check_filing_range(issuers: Sequence[IssuerRecord], filings_from: date, filings_through: date) -> None:
    """Raise ``ValueError`` if any search hit was filed outside ``[filings_from, filings_through]``."""
    outside = [hit for record in issuers for hit in record.hits if not filings_from <= hit.filed <= filings_through]
    if outside:
        first = min(outside, key=lambda hit: (hit.filed, hit.accession, hit.file))
        raise ValueError(
            f"{len(outside)} investor-day hits were filed outside the entry's filing range "
            f"{filings_from.isoformat()}..{filings_through.isoformat()} (first: {first.accession} on "
            f"{first.filed.isoformat()})"
        )


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


@dataclass(frozen=True)
class _Fetcher:
    client: httpx.AsyncClient
    user_agent: str
    pace: Callable[[], Awaitable[None]]
    sleep: Callable[[float], Awaitable[None]]

    async def get(self, url: str, *, missing_ok: bool) -> bytes | None:
        """One paced GET with ``TRANSIENT_RETRY``; a 403 raises ``SecRefused``."""
        try:
            return await edgar_fetch(
                self.client,
                url,
                self.user_agent,
                self.pace,
                missing_ok=missing_ok,
                retry=TRANSIENT_RETRY,
                sleep=self.sleep,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                raise SecRefused("SEC refused the request (403); stopping") from exc
            raise


def _reason(exc: BaseException) -> str:
    """A failure's short reason key: ``http_<code>`` for an HTTP status, else the exception's type name."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http_{exc.response.status_code}"
    return type(exc).__name__


async def _search(
    fetcher: _Fetcher, cik: int, filings_from: date, filings_through: date
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
        body = await fetcher.get(f"{SEARCH_URL}?{urlencode(params)}", missing_ok=False)
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
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    fetcher = _Fetcher(client, user_agent, pace, sleep)
    if not store.tickers_path.exists():
        body = await fetcher.get(TICKERS_URL, missing_ok=False)
        assert body is not None
        store.write_tickers(body)
    tickers_bytes = store.tickers_path.read_bytes()
    by_cik, unmapped = symbol_ciks(json.loads(tickers_bytes), symbols)

    failures: Counter[str] = Counter()
    fetched = reused = 0
    records: list[IssuerRecord] = []
    for done, (cik, cik_symbols) in enumerate(by_cik.items(), start=1):
        issuer_failures: Counter[str] = Counter()
        sub_path = store.submissions_path(cik)
        sub_body = sub_path.read_bytes() if sub_path.exists() else None
        if sub_body is None:
            try:
                sub_body = await fetcher.get(SUBMISSIONS_URL.format(cik=cik), missing_ok=False)
            except SecRefused:
                raise
            except _FETCH_FAILURES as exc:
                issuer_failures[f"submissions_{_reason(exc)}"] += 1
            else:
                assert sub_body is not None
                store.write_submissions(cik, sub_body)
        visible = False
        if sub_body is not None:
            try:
                visible = visible_from_submissions(json.loads(sub_body))
            except _PAYLOAD_FAILURES as exc:
                issuer_failures[f"submissions_{_reason(exc)}"] += 1
        try:
            hits, capped = await _search(fetcher, cik, filings_from, filings_through)
        except SecRefused:
            raise
        except (*_FETCH_FAILURES, *_PAYLOAD_FAILURES) as exc:
            hits, capped = [], False
            issuer_failures[f"search_{_reason(exc)}"] += 1
        for hit in hits:
            if not hit.file.lower().endswith(_HTML_SUFFIXES):
                failures["not_html"] += 1
                continue
            if store.doc_path(hit.accession, hit.file).exists():
                reused += 1
                continue
            url = ARCHIVE_URL.format(cik=cik, accession_nodash=hit.accession.replace("-", ""), file=hit.file)
            try:
                document = await fetcher.get(url, missing_ok=True)
            except SecRefused:
                raise
            except _FETCH_FAILURES as exc:
                issuer_failures[_reason(exc)] += 1
                continue
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
        if progress is not None and (done % PROGRESS_EVERY == 0 or done == len(by_cik)):
            progress(
                f"investor days: {done}/{len(by_cik)} issuers, {sum(len(r.hits) for r in records)} hits, "
                f"documents {fetched} fetched / {reused} reused, {sum(failures.values())} failures"
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
