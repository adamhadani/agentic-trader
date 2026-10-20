import json
from datetime import UTC, datetime

import httpx
import pytest

from agentic_trader.research import insider_filings as mod
from agentic_trader.research.insider_filings import (
    INDEX_URL,
    TICKERS_URL,
    InsiderFilingStore,
    acquire_insider_filings,
    files_sha256,
    parse_quarter,
    quarters_between,
    resolve_archive_urls,
    validate_archive,
)
from tests.research.insider_fixtures import make_archive as _archive


NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
UA = "Test Desk test@example.com"
PREFIX = "/files/structureddata/data/insider-transactions-data-sets/"
TICKERS = json.dumps({"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple"}}).encode()


def q(text):
    return parse_quarter(text)


async def _noop():
    return None


def test_quarter_parsing_ordering_and_ranges():
    assert str(parse_quarter("2015q1")) == "2015q1" and parse_quarter("2015q4") < parse_quarter("2016q1")
    assert len(quarters_between(parse_quarter("2015q1"), parse_quarter("2026q3"))) == 47
    with pytest.raises(ValueError, match="2015q1"):
        parse_quarter("2015q5")
    with pytest.raises(ValueError):
        quarters_between(parse_quarter("2016q1"), parse_quarter("2015q4"))


def test_resolve_archive_urls_handles_both_path_prefixes():
    html = (
        '<a href="/files/structureddata/data/insider-transactions-data-sets/2026q1_form345.zip">x</a>'
        '<a href="/files/datastandardsinnovation/data/insider-transactions-data-sets/2026q2_form345.zip">y</a>'
    )
    urls = resolve_archive_urls(html)
    assert urls["2026q1"].startswith("https://www.sec.gov/files/structureddata/") and "2026q2" in urls


def test_validate_archive_requires_the_three_tables_and_their_columns():
    assert validate_archive(_archive())["NONDERIV_TRANS.tsv"] == 1
    with pytest.raises(ValueError, match="REPORTINGOWNER"):
        validate_archive(_archive(drop="REPORTINGOWNER.tsv"))
    with pytest.raises(ValueError, match="TRANS_CODE"):
        validate_archive(_archive(nonderiv_header="ACCESSION_NUMBER\tTRANS_DATE\n"))
    with pytest.raises(ValueError, match="zip"):
        validate_archive(b"not a zip")


def test_store_round_trips_and_refuses_a_corrupt_archive(tmp_path):
    store = InsiderFilingStore(tmp_path)
    store.save(parse_quarter("2025q1"), b"body", url="u", requested_at=NOW, received_at=NOW)
    assert store.load(parse_quarter("2025q1")).read_bytes() == b"body"
    store.paths(parse_quarter("2025q1"))[0].write_bytes(b"tampered")
    with pytest.raises(ValueError, match="SHA-256"):
        store.load(parse_quarter("2025q1"))


def test_store_reading_creates_nothing_and_tickers_round_trip(tmp_path):
    store = InsiderFilingStore(tmp_path / "cache")
    assert store.load(q("2025q1")) is None and store.load_tickers() is None and not (tmp_path / "cache").exists()
    store.save_tickers(TICKERS, url=TICKERS_URL, requested_at=NOW, received_at=NOW)
    assert store.load_tickers()["0"]["ticker"] == "AAPL"


def _world(*, quarters=("2025q1", "2025q2"), bodies=None, index_html=None, tickers=TICKERS):
    seen: list[httpx.Request] = []
    bodies = bodies or {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        url = str(request.url)
        if url == INDEX_URL:
            html = index_html or "".join(f'<a href="{PREFIX}{name}_form345.zip">x</a>' for name in quarters)
            return httpx.Response(200, content=html.encode())
        if url == TICKERS_URL:
            return httpx.Response(200, content=tickers)
        name = request.url.path.rsplit("/", 1)[1].removesuffix("_form345.zip")
        return httpx.Response(200, content=bodies.get(name, _archive()))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={"User-Agent": UA})
    return client, seen


async def test_acquisition_resolves_the_index_fetches_tickers_and_archives_and_digests(tmp_path):
    store = InsiderFilingStore(tmp_path)
    quarters = quarters_between(q("2025q1"), q("2025q2"))
    client, seen = _world()
    async with client:
        manifest = await acquire_insider_filings(client, quarters, store, pace=_noop)
        assert manifest["quarters_fetched"] == 2 and manifest["tickers_reused"] is False
        assert manifest["files_sha256"] == files_sha256(store, quarters)
        assert manifest["window"] == ["2025q1", "2025q2"] and len(manifest["files"]) == 3
        assert all(r.headers["user-agent"] == UA for r in seen)
        again = await acquire_insider_filings(client, quarters, store, pace=_noop)
    assert again["quarters_reused"] == 2 and again["tickers_reused"] is True and again["quarters_fetched"] == 0
    assert sum(str(r.url) == INDEX_URL for r in seen) == 1


async def test_missing_quarter_on_the_index_fails_closed(tmp_path):
    client, _ = _world()
    async with client:
        with pytest.raises(ValueError, match="2025q3"):
            await acquire_insider_filings(
                client, quarters_between(q("2025q1"), q("2025q3")), InsiderFilingStore(tmp_path), pace=_noop
            )


async def test_invalid_archive_is_kept_on_disk_and_fails(tmp_path):
    store = InsiderFilingStore(tmp_path)
    client, _ = _world(bodies={"2025q2": _archive(drop="REPORTINGOWNER.tsv")})
    async with client:
        with pytest.raises(ValueError, match="REPORTINGOWNER"):
            await acquire_insider_filings(client, quarters_between(q("2025q1"), q("2025q2")), store, pace=_noop)
    assert store.paths(q("2025q2"))[0].exists() and store.meta(q("2025q2"))["status"] == "invalid"
    with pytest.raises(ValueError, match="invalid"):
        store.load(q("2025q2"))


async def test_byte_budget_and_foreign_hosts_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "MAX_ARCHIVE_BYTES", 1024)
    client, _ = _world(bodies={"2025q1": b"x" * 4096})
    async with client:
        with pytest.raises(ValueError, match="byte budget"):
            await acquire_insider_filings(client, [q("2025q1")], InsiderFilingStore(tmp_path / "a"), pace=_noop)
    html = '<a href="https://evil.example/files/x/data/2025q1_form345.zip">x</a>'
    client, seen = _world(index_html=html)
    async with client:
        with pytest.raises(ValueError, match="host"):
            await acquire_insider_filings(client, [q("2025q1")], InsiderFilingStore(tmp_path / "b"), pace=_noop)
    assert [str(r.url) for r in seen] == [INDEX_URL]


async def test_transport_error_is_retried_once(tmp_path):
    calls = {"n": 0}

    def handler(request):
        if str(request.url) == INDEX_URL:
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("boom")
            return httpx.Response(200, content=f'<a href="{PREFIX}2025q1_form345.zip">x</a>'.encode())
        if str(request.url) == TICKERS_URL:
            return httpx.Response(200, content=TICKERS)
        return httpx.Response(200, content=_archive())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        manifest = await acquire_insider_filings(client, [q("2025q1")], InsiderFilingStore(tmp_path), pace=_noop)
    assert calls["n"] == 2 and manifest["quarters_fetched"] == 1


def test_files_digest_changes_with_the_ticker_map(tmp_path):
    digests = []
    for name, tickers in (("a", TICKERS), ("b", TICKERS + b" ")):
        store = InsiderFilingStore(tmp_path / name)
        store.save(q("2025q1"), b"zip", url="u", requested_at=NOW, received_at=NOW)
        store.save_tickers(tickers, url=TICKERS_URL, requested_at=NOW, received_at=NOW)
        digests.append(files_sha256(store, [q("2025q1")]))
    assert digests[0] != digests[1]
    with pytest.raises(ValueError, match="2025q2"):
        files_sha256(store, [q("2025q2")])
