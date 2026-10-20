"""Digest-pinned cached acquisition of the SEC Form 3/4/5 insider-transactions data sets.

One zip per calendar quarter (``{YYYY}q{N}_form345.zip``) resolved from the SEC index page,
plus the ``company_tickers.json`` map, fetched paced from ``www.sec.gov`` with the operator's
declared User-Agent and saved verbatim with SHA-256 and receipt times. An archive that fails
validation is kept on disk with status ``invalid`` and the acquisition raises. The files digest
binds every quarter's hash and the ticker map's. Research only: no database, broker or Telegram.

``read_transactions`` extracts the qualifying open-market Form 4 trades of one cached archive from
disk (``SUBMISSION``, ``REPORTINGOWNER`` and ``NONDERIV_TRANS``; ``FOOTNOTES`` is never opened).
The tables are tab-delimited without quoting: a quote character is data, and a row whose field count
differs from its header fails closed.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import sys
import zipfile
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urljoin, urlparse

import httpx

from agentic_trader.research.short_volume import write_private


__all__ = [
    "DROP_RULES",
    "INDEX_URL",
    "MAX_ARCHIVE_BYTES",
    "REQUESTS_PER_SECOND",
    "REQUIRED_TABLES",
    "SEC_HOST",
    "TICKERS_URL",
    "InsiderFilingStore",
    "InsiderTransaction",
    "Quarter",
    "acquire_insider_filings",
    "files_sha256",
    "parse_quarter",
    "quarters_between",
    "read_transactions",
    "resolve_archive_urls",
    "symbol_ciks",
    "validate_archive",
]

SEC_HOST = "www.sec.gov"
INDEX_URL = "https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
REQUESTS_PER_SECOND = 2
_TIMEOUT_SECONDS = 60.0

REQUIRED_TABLES: dict[str, tuple[str, ...]] = {
    "SUBMISSION.tsv": ("ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE", "ISSUERCIK"),
    "REPORTINGOWNER.tsv": ("ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNER_RELATIONSHIP"),
    "NONDERIV_TRANS.tsv": (
        "ACCESSION_NUMBER",
        "TRANS_DATE",
        "TRANS_CODE",
        "TRANS_SHARES",
        "TRANS_PRICEPERSHARE",
        "TRANS_ACQUIRED_DISP_CD",
    ),
}

# The reader's drop rules, in the order a row is tested against them (spec 2026-10-20 insider, section 3).
DROP_RULES = (
    "document_type",
    "code",
    "acquired_disposed",
    "owner_role",
    "plan_10b5_1",
    "non_positive",
    "trade_after_filing",
)
# Open-market purchases are acquisitions and sales dispositions; anything else is a contrary row.
_SIDES = {"P": "A", "S": "D"}
_MONTHS = {
    name: number
    for number, name in enumerate(
        ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), start=1
    )
}

_QUARTER = re.compile(r"(\d{4})q([1-4])")
_LINK = re.compile(r'href="((?:https?://[^"/]+)?/files/[^"]*?(\d{4}q[1-4])_form345\.zip)"')
_META_KEYS = frozenset({"sha256", "url", "requested_at", "received_at", "status", "bytes"})
_TICKER_KEYS = frozenset({"cik_str", "ticker", "title"})


@dataclass(frozen=True, order=True)
class Quarter:
    year: int
    index: int

    def __str__(self) -> str:
        return f"{self.year}q{self.index}"


def parse_quarter(text: str) -> Quarter:
    match = _QUARTER.fullmatch(text)
    if match is None:
        raise ValueError(f"quarter must look like 2015q1, got {text!r}")
    return Quarter(int(match.group(1)), int(match.group(2)))


def quarters_between(start: Quarter, end: Quarter) -> tuple[Quarter, ...]:
    if end < start:
        raise ValueError(f"{end} precedes {start}")
    out: list[Quarter] = []
    current = start
    while current <= end:
        out.append(current)
        current = Quarter(current.year + 1, 1) if current.index == 4 else Quarter(current.year, current.index + 1)
    return tuple(out)


def resolve_archive_urls(index_html: str) -> dict[str, str]:
    """Map each quarter on the SEC index page to its absolute archive URL; any foreign host raises."""
    urls: dict[str, str] = {}
    for href, quarter in _LINK.findall(index_html):
        url = urljoin(INDEX_URL, href)
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != SEC_HOST or parsed.port is not None:
            raise ValueError(f"{quarter}: archive link host {parsed.netloc!r} is not https://{SEC_HOST}")
        if urls.get(quarter, url) != url:
            raise ValueError(f"{quarter}: the index lists conflicting archive URLs")
        urls[quarter] = url
    return urls


def validate_archive(data: bytes) -> dict[str, int]:
    """Row counts per required table; raise ``ValueError`` naming a missing table or column."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("not a zip archive") from exc
    counts: dict[str, int] = {}
    with archive:
        names = set(archive.namelist())
        for table, columns in REQUIRED_TABLES.items():
            if table not in names:
                raise ValueError(f"archive lacks {table}")
            with archive.open(table) as handle:
                header = handle.readline().decode("utf-8-sig", errors="replace").rstrip("\r\n").split("\t")
                missing = [column for column in columns if column not in header]
                if missing:
                    raise ValueError(f"{table} lacks column {missing[0]}")
                counts[table] = sum(1 for line in handle if line.strip())
    return counts


class InsiderFilingStore:
    """``<directory>/<quarter>_form345.zip`` + ``.meta.json`` and ``company_tickers.json`` + meta.

    Reading never creates the directory.
    """

    def __init__(self, directory: Path):
        self.directory = directory

    def paths(self, quarter: Quarter) -> tuple[Path, Path]:
        return self.directory / f"{quarter}_form345.zip", self.directory / f"{quarter}_form345.meta.json"

    def tickers_paths(self) -> tuple[Path, Path]:
        return self.directory / "company_tickers.json", self.directory / "company_tickers.meta.json"

    @staticmethod
    def _read_meta(meta_path: Path) -> dict[str, Any] | None:
        if not meta_path.exists():
            return None
        try:
            record = json.loads(meta_path.read_text())
        except (OSError, ValueError) as exc:
            raise ValueError(f"{meta_path}: unreadable meta file: {exc}") from exc
        if not isinstance(record, dict) or not record.keys() >= _META_KEYS:
            raise ValueError(f"{meta_path}: meta file is incomplete")
        return record

    @staticmethod
    def _verified(body_path: Path, meta: dict[str, Any] | None) -> Path | None:
        if meta is None or not body_path.exists():
            return None
        if meta["status"] != "present":
            raise ValueError(f"{body_path}: file is {meta['status']}")
        if hashlib.sha256(body_path.read_bytes()).hexdigest() != meta["sha256"]:
            raise ValueError(f"{body_path}: SHA-256 does not match its meta file")
        return body_path

    def meta(self, quarter: Quarter) -> dict[str, Any] | None:
        return self._read_meta(self.paths(quarter)[1])

    def tickers_meta(self) -> dict[str, Any] | None:
        return self._read_meta(self.tickers_paths()[1])

    def load(self, quarter: Quarter) -> Path | None:
        return self._verified(self.paths(quarter)[0], self.meta(quarter))

    def load_tickers(self) -> dict[str, Any] | None:
        path = self._verified(self.tickers_paths()[0], self.tickers_meta())
        if path is None:
            return None
        loaded = json.loads(path.read_bytes())
        if not isinstance(loaded, dict):
            raise ValueError(f"{path}: not a JSON object")  # noqa: TRY004
        return loaded

    def _save(
        self,
        body_path: Path,
        meta_path: Path,
        body: bytes,
        *,
        url: str,
        requested_at: datetime,
        received_at: datetime,
        status: str,
    ) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_private(body_path, body)
        record = {
            "sha256": hashlib.sha256(body).hexdigest(),
            "url": url,
            "requested_at": requested_at.isoformat(),
            "received_at": received_at.isoformat(),
            "status": status,
            "bytes": len(body),
        }
        write_private(meta_path, json.dumps(record, sort_keys=True).encode())

    def save(
        self,
        quarter: Quarter,
        body: bytes,
        *,
        url: str,
        requested_at: datetime,
        received_at: datetime,
        status: str = "present",
    ) -> None:
        body_path, meta_path = self.paths(quarter)
        self._save(
            body_path, meta_path, body, url=url, requested_at=requested_at, received_at=received_at, status=status
        )

    def save_tickers(self, body: bytes, *, url: str, requested_at: datetime, received_at: datetime) -> None:
        body_path, meta_path = self.tickers_paths()
        self._save(
            body_path, meta_path, body, url=url, requested_at=requested_at, received_at=received_at, status="present"
        )


def _digest_lines(store: InsiderFilingStore, quarters: Sequence[Quarter]) -> list[str]:
    lines: list[str] = []
    for quarter in quarters:
        meta = store.meta(quarter)
        if meta is None:
            raise ValueError(f"{quarter}: no meta file")
        if meta["status"] != "present":
            raise ValueError(f"{quarter}: insider archive is {meta['status']}")
        lines.append(f"{quarter}:{meta['sha256']}")
    tickers = store.tickers_meta()
    if tickers is None:
        raise ValueError("tickers: no meta file")
    if tickers["status"] != "present":
        raise ValueError(f"tickers: ticker map is {tickers['status']}")
    lines.append(f"tickers:{tickers['sha256']}")
    return sorted(lines)


def files_sha256(store: InsiderFilingStore, quarters: Sequence[Quarter]) -> str:
    """SHA-256 over the sorted lines ``<quarter>:<sha256>`` and ``tickers:<sha256>``."""
    return hashlib.sha256("\n".join(_digest_lines(store, quarters)).encode()).hexdigest()


async def _fetch(client: httpx.AsyncClient, url: str) -> bytes:
    async with client.stream("GET", url, timeout=_TIMEOUT_SECONDS) as response:
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError(f"{url}: unexpected HTTP {response.status_code}")
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > MAX_ARCHIVE_BYTES:
                raise ValueError(f"{url}: exceeded the {MAX_ARCHIVE_BYTES}-byte budget")
            chunks.append(chunk)
        return b"".join(chunks)


async def _request(
    client: httpx.AsyncClient, url: str, pace: Callable[[], Awaitable[None]]
) -> tuple[bytes, datetime, datetime]:
    if urlparse(url).hostname != SEC_HOST:
        raise ValueError(f"{url}: host is not {SEC_HOST}")
    await pace()
    requested_at = datetime.now(UTC)
    try:
        body = await _fetch(client, url)
    except httpx.TransportError:
        await pace()
        requested_at = datetime.now(UTC)
        body = await _fetch(client, url)
    return body, requested_at, datetime.now(UTC)


def _cached(store: InsiderFilingStore, quarter: Quarter) -> bool:
    meta = store.meta(quarter)
    return meta is not None and meta["status"] == "present" and store.load(quarter) is not None


def _validate_tickers(body: bytes) -> None:
    try:
        loaded = json.loads(body)
    except ValueError as exc:
        raise ValueError(f"ticker map is not JSON: {exc}") from exc
    if not isinstance(loaded, dict) or not loaded:
        raise ValueError("ticker map is not a non-empty JSON object")
    for value in loaded.values():
        if not isinstance(value, dict) or not value.keys() >= _TICKER_KEYS:
            raise ValueError("ticker map entry lacks cik_str, ticker or title")


async def acquire_insider_filings(
    client: httpx.AsyncClient,
    quarters: Sequence[Quarter],
    store: InsiderFilingStore,
    *,
    pace: Callable[[], Awaitable[None]],
) -> dict[str, Any]:
    """Fetch (or reuse) the ticker map and one archive per quarter; return the digest manifest."""
    if not quarters:
        raise ValueError("no quarters requested")
    missing = [quarter for quarter in quarters if not _cached(store, quarter)]
    urls: dict[str, str] = {}
    if missing:
        body, _, _ = await _request(client, INDEX_URL, pace)
        urls = resolve_archive_urls(body.decode(errors="replace"))
        for quarter in missing:
            if str(quarter) not in urls:
                raise ValueError(f"no archive on the SEC index for {quarter}")
    tickers_reused = store.tickers_meta() is not None and store.load_tickers() is not None
    if not tickers_reused:
        body, requested_at, received_at = await _request(client, TICKERS_URL, pace)
        _validate_tickers(body)
        store.save_tickers(body, url=TICKERS_URL, requested_at=requested_at, received_at=received_at)
    for quarter in missing:
        url = urls[str(quarter)]
        body, requested_at, received_at = await _request(client, url, pace)
        try:
            validate_archive(body)
        except ValueError as exc:
            store.save(quarter, body, url=url, requested_at=requested_at, received_at=received_at, status="invalid")
            raise ValueError(f"{quarter}: invalid archive kept on disk: {exc}") from exc
        store.save(quarter, body, url=url, requested_at=requested_at, received_at=received_at)
    lines = _digest_lines(store, quarters)
    metas = [store.meta(quarter) for quarter in quarters]
    return {
        "window": [str(quarters[0]), str(quarters[-1])],
        "quarters": [str(quarter) for quarter in quarters],
        "quarters_fetched": len(missing),
        "quarters_reused": len(quarters) - len(missing),
        "tickers_reused": tickers_reused,
        "files_sha256": files_sha256(store, quarters),
        "bytes_total": sum(meta["bytes"] for meta in metas if meta),
        "files": lines,
    }


@dataclass(frozen=True)
class InsiderTransaction:
    """One qualifying open-market Form 4 trade, keyed by the cohort symbol its issuer CIK maps to.

    ``dollars`` is shares times price; ``owner_cik`` is the first qualifying reporting owner's CIK.
    """

    symbol: str
    filing_date: date
    trans_date: date
    code: Literal["P", "S"]
    dollars: float
    owner_cik: int


def _edgar_date(text: str) -> date:
    """``31-MAR-2025`` (the data sets' ``DD-MON-YYYY``), independent of the process locale."""
    parts = text.strip().split("-")
    if len(parts) != 3 or parts[1].upper() not in _MONTHS or not (parts[0].isdigit() and parts[2].isdigit()):
        raise ValueError(f"date must look like 31-MAR-2025, got {text!r}")
    return date(int(parts[2]), _MONTHS[parts[1].upper()], int(parts[0]))


def _positive(text: str) -> float | None:
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) and value > 0 else None


def _rows(
    archive: zipfile.ZipFile, table: str, columns: Sequence[str], optional: str | None = None
) -> Iterator[list[str]]:
    """The ``columns`` (then ``optional``, ``""`` when the header lacks it) of every non-blank row."""
    with archive.open(table) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="") as text:
        reader = csv.reader(text, delimiter="\t", quoting=csv.QUOTE_NONE)
        header = next(reader, None)
        if header is None:
            raise ValueError(f"{table} is empty")
        position = {name: i for i, name in enumerate(header)}
        missing = [column for column in columns if column not in position]
        if missing:
            raise ValueError(f"{table} lacks column {missing[0]}")
        picks = [position[column] for column in columns]
        extra = position.get(optional) if optional is not None else None
        for row in reader:
            if not row:
                continue
            if len(row) != len(header):
                raise ValueError(f"{table} line {reader.line_num}: {len(row)} fields, the header has {len(header)}")
            picked = [row[i] for i in picks]
            if optional is not None:
                picked.append(row[extra] if extra is not None else "")
            yield picked


def _owner_ciks(archive: zipfile.ZipFile, roles: frozenset[str]) -> dict[str, int]:
    """Per accession, the CIK of the first reporting owner whose relationship lists one of ``roles``."""
    owners: dict[str, int] = {}
    for accession, owner, relationship in _rows(
        archive, "REPORTINGOWNER.tsv", ("ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNER_RELATIONSHIP")
    ):
        if accession not in owners and roles.intersection(part.strip() for part in relationship.split(",")):
            owners[accession] = int(owner)
    return owners


def _listing_rule(document: str, code: str, side: str, has_owner: bool, plan: str, exclude_10b5_1: bool) -> str | None:
    """The first of the filing-level rules (``DROP_RULES[:5]``) a row fails, or ``None``."""
    if document != "4":
        return "document_type"
    if code not in _SIDES:
        return "code"
    if side != _SIDES[code]:
        return "acquired_disposed"
    if not has_owner:
        return "owner_role"
    if exclude_10b5_1 and plan == "1":
        return "plan_10b5_1"
    return None


def read_transactions(
    archive: Path,
    *,
    cik_to_symbols: Mapping[int, tuple[str, ...]],
    owner_roles: Sequence[str],
    exclude_10b5_1: bool,
) -> tuple[tuple[InsiderTransaction, ...], dict[str, int], int]:
    """The archive's qualifying trades for the mapped issuer CIKs, the per-rule drop counts, and the
    count of qualifying rows whose issuer CIK is not mapped (``unscored_rows``).

    A row is tested against ``DROP_RULES`` in order and counted under the first it fails, whatever its
    issuer: an original Form 4 (``DOCUMENT_TYPE == "4"``); code ``P`` acquired or ``S`` disposed; an
    officer or director among the accession's reporting owners; not a 10b5-1 plan trade
    (``AFF10B5ONE == "1"``) when ``exclude_10b5_1`` (an archive without the column has none);
    positive shares and price; traded on or before its filing date. A row whose issuer maps to several
    symbols yields one transaction per symbol.
    """
    csv.field_size_limit(sys.maxsize)
    drops = dict.fromkeys(DROP_RULES, 0)
    out: list[InsiderTransaction] = []
    unscored = 0
    with zipfile.ZipFile(archive) as zipped:
        submissions: dict[str, tuple[str, str, str, str]] = {}
        for accession, filed, document, issuer, plan in _rows(
            zipped, "SUBMISSION.tsv", ("ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE", "ISSUERCIK"), "AFF10B5ONE"
        ):
            if accession in submissions:
                raise ValueError(f"SUBMISSION.tsv repeats accession {accession}")
            submissions[accession] = (filed, document.strip(), issuer, plan.strip())
        owners = _owner_ciks(zipped, frozenset(owner_roles))
        filing_dates: dict[str, date] = {}
        for accession, traded, code, shares, price, side in _rows(
            zipped,
            "NONDERIV_TRANS.tsv",
            (
                "ACCESSION_NUMBER",
                "TRANS_DATE",
                "TRANS_CODE",
                "TRANS_SHARES",
                "TRANS_PRICEPERSHARE",
                "TRANS_ACQUIRED_DISP_CD",
            ),
        ):
            submission = submissions.get(accession)
            if submission is None:
                raise ValueError(f"NONDERIV_TRANS.tsv: accession {accession} has no SUBMISSION row")
            filed, document, issuer, plan = submission
            code = code.strip()
            rule = _listing_rule(document, code, side.strip(), accession in owners, plan, exclude_10b5_1)
            if rule is not None:
                drops[rule] += 1
                continue
            share_count, unit_price = _positive(shares), _positive(price)
            if share_count is None or unit_price is None:
                drops["non_positive"] += 1
                continue
            if accession not in filing_dates:
                filing_dates[accession] = _edgar_date(filed)
            trans_date = _edgar_date(traded)
            if trans_date > filing_dates[accession]:
                drops["trade_after_filing"] += 1
                continue
            symbols = cik_to_symbols.get(int(issuer))
            if symbols is None:
                unscored += 1
                continue
            out.extend(
                InsiderTransaction(
                    symbol=symbol,
                    filing_date=filing_dates[accession],
                    trans_date=trans_date,
                    code="P" if code == "P" else "S",
                    dollars=share_count * unit_price,
                    owner_cik=owners[accession],
                )
                for symbol in symbols
            )
    return tuple(out), drops, unscored


def symbol_ciks(
    tickers: Mapping[str, Any], symbols: Sequence[str]
) -> tuple[dict[int, tuple[str, ...]], tuple[str, ...]]:
    """The ticker map restricted to ``symbols`` as ``{cik: symbols}`` (cohort order), and the symbols without a CIK.

    A ticker is upper-cased with ``-`` read as ``.`` (``BF-B`` is ``BF.B``). Several symbols may share
    a CIK (share classes); a symbol listed under two CIKs fails closed.
    """
    wanted = set(symbols)
    cik_of: dict[str, int] = {}
    for entry in tickers.values():
        symbol = str(entry["ticker"]).upper().replace("-", ".")
        if symbol not in wanted:
            continue
        cik = int(entry["cik_str"])
        if cik_of.setdefault(symbol, cik) != cik:
            raise ValueError(f"ticker map lists {symbol} under CIKs {cik_of[symbol]} and {cik}")
    mapped: dict[int, list[str]] = {}
    for symbol in dict.fromkeys(symbols):
        if symbol in cik_of:
            mapped.setdefault(cik_of[symbol], []).append(symbol)
    missing = tuple(symbol for symbol in dict.fromkeys(symbols) if symbol not in cik_of)
    return {cik: tuple(found) for cik, found in mapped.items()}, missing
