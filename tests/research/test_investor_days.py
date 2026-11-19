import gzip
import json
from datetime import date
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from agentic_trader.data.edgar_http import TICKERS_URL
from agentic_trader.research.investor_days import (
    MAX_HITS_PER_CIK,
    SEARCH_URL,
    InvestorDayStore,
    acquire_investor_days,
    content_sha256,
    visible_from_submissions,
)


UA = "Test Co test@example.com"
FROM, THROUGH = date(2016, 1, 1), date(2026, 7, 31)
PAGE = 100


async def _noop():
    return None


class Router:
    """Synthetic SEC: tickers, submissions, paged search and archive documents."""

    def __init__(self, tickers, totals, files=None, status=None, malformed=()):
        self.tickers = tickers
        self.totals = totals  # cik -> total hits
        self.files = files or {}  # cik -> file name for every hit (default doc.htm)
        # kind -> status code for every request, or a list of codes for the first requests only
        self.status = status or {}
        self.malformed = set(malformed)  # ciks whose search answers a payload without "hits"
        self.counts = {"tickers": 0, "submissions": 0, "search": 0, "archive": 0}
        self.docs: dict[str, bytes] = {}

    def _forced(self, kind):
        value = self.status.get(kind)
        if isinstance(value, list):
            return value.pop(0) if value else None
        return value

    def handler(self, request):
        url = str(request.url)
        host = request.url.host
        if url == TICKERS_URL:
            self.counts["tickers"] += 1
            return httpx.Response(200, content=json.dumps(self.tickers).encode())
        if host == "data.sec.gov":
            self.counts["submissions"] += 1
            if forced := self._forced("submissions"):
                return httpx.Response(forced)
            return httpx.Response(200, json={"filings": {"recent": {"form": ["4", "10-K"]}}})
        if url.startswith(SEARCH_URL):
            self.counts["search"] += 1
            if forced := self._forced("search"):
                return httpx.Response(forced)
            query = parse_qs(urlparse(url).query)
            cik = int(query["ciks"][0])
            if cik in self.malformed:
                return httpx.Response(200, json={"unexpected": True})
            start = int(query.get("from", ["0"])[0])
            total = self.totals[cik]
            rows = []
            for n in range(start, min(start + PAGE, total)):
                file = self.files.get(cik, "doc.htm")
                rows.append(
                    {
                        "_id": f"{cik:010d}-26-{n:06d}:{file}",
                        "_source": {
                            "file_date": "2024-05-01",
                            "items": ["7.01"],
                            "file_type": "EX-99.1",
                            "file_description": "Press release",
                        },
                    }
                )
            return httpx.Response(200, json={"hits": {"total": {"value": total}, "hits": rows}})
        if "/Archives/" in url:
            self.counts["archive"] += 1
            if forced := self._forced("archive"):
                return httpx.Response(forced)
            return httpx.Response(200, content=b"<html>Fictional Corp investor day</html>")
        return httpx.Response(500)

    def client(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def tickers(*pairs):
    return {str(i): {"cik_str": cik, "ticker": t, "title": t} for i, (t, cik) in enumerate(pairs)}


async def run(router, tmp_path, symbols, *, slept=None, progress=None):
    store = InvestorDayStore(tmp_path / "cache")

    async def sleep(seconds):
        if slept is not None:
            slept.append(seconds)

    async with router.client() as client:
        manifest = await acquire_investor_days(
            client,
            symbols,
            store,
            user_agent=UA,
            filings_from=FROM,
            filings_through=THROUGH,
            pace=_noop,
            sleep=sleep,
            progress=progress,
        )
    return store, manifest


def test_visible_from_submissions():
    assert visible_from_submissions({"filings": {"recent": {"form": ["4", "10-Q"]}}}) is True
    assert visible_from_submissions({"filings": {"recent": {"form": ["6-K", "20-F"]}}}) is False


async def test_share_classes_search_once(tmp_path):
    router = Router(tickers(("GOOG", 7), ("GOOGL", 7)), {7: 1})
    store, manifest = await run(router, tmp_path, ["GOOG", "GOOGL"])
    assert router.counts["search"] == 1
    issuers = store.issuers()
    assert len(issuers) == 1 and issuers[0].symbols == ("GOOG", "GOOGL") and issuers[0].visible
    assert manifest["issuers"] == 1


async def test_pagination_and_cap(tmp_path):
    router = Router(tickers(("AAA", 1), ("BBB", 2)), {1: 150, 2: 1200})
    store, manifest = await run(router, tmp_path, ["AAA", "BBB"])
    by_cik = {i.cik: i for i in store.issuers()}
    assert len(by_cik[1].hits) == 150 and by_cik[1].capped is False
    assert len(by_cik[2].hits) == MAX_HITS_PER_CIK and by_cik[2].capped is True
    assert by_cik[1].clean and not by_cik[2].clean
    assert manifest["capped_ciks"] == [2]
    assert by_cik[1].hits[0].filed == date(2024, 5, 1) and by_cik[1].hits[0].items == ("7.01",)


async def test_documents_cached_and_reused(tmp_path):
    router = Router(tickers(("AAA", 1)), {1: 3})
    store, _ = await run(router, tmp_path, ["AAA"])
    assert router.counts["archive"] == 3 and router.counts["tickers"] == 1
    hit = store.issuers()[0].hits[0]
    assert store.document(hit.accession, hit.file) == b"<html>Fictional Corp investor day</html>"
    assert store.document("0000000000-00-000000", "none.htm") is None
    _, manifest = await run(router, tmp_path, ["AAA"])
    assert router.counts["archive"] == 3 and router.counts["tickers"] == 1
    assert manifest["documents_reused"] == 3 and manifest["documents_fetched"] == 0


async def test_404_counted_403_stops(tmp_path):
    router = Router(tickers(("AAA", 1)), {1: 1}, status={"archive": 404})
    store, manifest = await run(router, tmp_path, ["AAA"])
    issuer = store.issuers()[0]
    assert dict(issuer.doc_failures) == {"http_404": 1} and not issuer.clean
    assert manifest["failures"]["http_404"] == 1
    forbidden = Router(tickers(("AAA", 1)), {1: 1}, status={"search": 403})
    with pytest.raises(ValueError, match="403"):
        await run(forbidden, tmp_path / "other", ["AAA"])


async def test_search_and_submissions_404_mark_issuer_unclean(tmp_path):
    router = Router(tickers(("AAA", 1)), {1: 1}, status={"search": 404, "submissions": 404})
    store, manifest = await run(router, tmp_path, ["AAA"])
    issuer = store.issuers()[0]
    assert dict(issuer.doc_failures) == {"search_http_404": 1, "submissions_http_404": 1}
    assert not issuer.clean and not issuer.visible
    assert manifest["failures"]["search_http_404"] == 1


async def test_non_html_counted(tmp_path):
    router = Router(tickers(("AAA", 1)), {1: 2}, files={1: "slides.pdf"})
    _, manifest = await run(router, tmp_path, ["AAA"])
    assert router.counts["archive"] == 0 and manifest["failures"]["not_html"] == 2


async def test_unmapped_symbols_listed(tmp_path):
    router = Router(tickers(("AAA", 1)), {1: 0})
    store, manifest = await run(router, tmp_path, ["AAA", "ZZZ"])
    assert store.unmapped() == ("ZZZ",) and manifest["unmapped"] == ["ZZZ"]


async def test_content_sha256_stable_and_sensitive(tmp_path):
    router = Router(tickers(("AAA", 1)), {1: 2})
    store, manifest = await run(router, tmp_path, ["AAA"])
    digest = content_sha256(store)
    assert digest == content_sha256(store) == manifest["content_sha256"]
    hit = store.issuers()[0].hits[0]
    path = next(store.root.glob(f"docs/{hit.accession}_*.gz"))
    path.write_bytes(gzip.compress(b"<html>changed</html>"))
    assert content_sha256(store) != digest


async def test_a_transient_status_is_retried_once_after_its_delay(tmp_path):
    router = Router(tickers(("AAA", 1)), {1: 1}, status={"search": [503], "archive": [429]})
    slept: list[float] = []
    store, manifest = await run(router, tmp_path, ["AAA"], slept=slept)
    issuer = store.issuers()[0]
    assert issuer.clean and len(issuer.hits) == 1 and manifest["documents_fetched"] == 1
    assert (router.counts["search"], router.counts["archive"]) == (2, 2)
    assert slept == [5.0, 5.0] and manifest["failures"] == {}


async def test_a_persistent_document_failure_is_counted_and_the_run_completes(tmp_path):
    router = Router(tickers(("AAA", 1), ("BBB", 2)), {1: 1, 2: 0}, status={"archive": 500})
    store, manifest = await run(router, tmp_path, ["AAA", "BBB"])
    by_cik = {i.cik: i for i in store.issuers()}
    assert dict(by_cik[1].doc_failures) == {"http_500": 1} and not by_cik[1].clean
    assert by_cik[2].clean
    assert router.counts["archive"] == 2  # one retry, then counted
    assert manifest["failures"] == {"http_500": 1} and manifest["issuers"] == 2
    assert manifest["documents_fetched"] == 0


async def test_a_malformed_search_payload_is_counted_for_that_issuer(tmp_path):
    router = Router(tickers(("AAA", 1), ("BBB", 2)), {1: 1, 2: 1}, malformed={1})
    store, manifest = await run(router, tmp_path, ["AAA", "BBB"])
    by_cik = {i.cik: i for i in store.issuers()}
    assert dict(by_cik[1].doc_failures) == {"search_KeyError": 1} and by_cik[1].hits == ()
    assert not by_cik[1].clean and by_cik[2].clean and len(by_cik[2].hits) == 1
    assert manifest["failures"] == {"search_KeyError": 1}


async def test_a_malformed_submissions_record_is_counted_and_leaves_the_issuer_invisible(tmp_path):
    router = Router(tickers(("AAA", 1)), {1: 1})
    store = InvestorDayStore(tmp_path / "cache")
    store.write_submissions(1, b"not json")
    store, manifest = await run(router, tmp_path, ["AAA"])
    issuer = store.issuers()[0]
    assert dict(issuer.doc_failures) == {"submissions_JSONDecodeError": 1}
    assert not issuer.visible and not issuer.clean and len(issuer.hits) == 1
    assert router.counts["submissions"] == 0  # the cached record is reused, not fetched
    assert manifest["failures"] == {"submissions_JSONDecodeError": 1}


@pytest.mark.parametrize("kind", ["submissions", "search", "archive"])
async def test_a_403_on_any_request_still_stops_the_acquisition(tmp_path, kind):
    router = Router(tickers(("AAA", 1)), {1: 1}, status={kind: 403})
    with pytest.raises(ValueError, match="403"):
        await run(router, tmp_path, ["AAA"])
    assert router.counts[kind] == 1  # never retried
    assert not (tmp_path / "cache" / "hits.json").exists()


async def test_progress_every_50_issuers_and_at_the_end(tmp_path):
    pairs = [(f"S{n:03d}", n) for n in range(1, 121)]
    router = Router(tickers(*pairs), dict.fromkeys(range(1, 121), 1), status={"archive": [404]})
    lines: list[str] = []
    await run(router, tmp_path, [symbol for symbol, _ in pairs], progress=lines.append)
    assert len(lines) == 3
    assert lines[0].startswith("investor days: 50/120 issuers, 50 hits, documents 49 fetched / 0 reused, 1 failures")
    assert lines[-1].startswith("investor days: 120/120 issuers, 120 hits, documents 119 fetched / 0 reused")
    assert all(UA not in line and "test@example.com" not in line for line in lines)


def test_the_cache_path_sanitizes_the_accession_and_the_file(tmp_path):
    store = InvestorDayStore(tmp_path / "cache")
    assert store.doc_path("0000320193-24-000001", "doc.htm").name == "0000320193-24-000001_doc.htm.gz"
    path = store.doc_path("../../escape", "a/b.htm")
    assert path.parent == store.root / "docs" and path.name == ".._.._escape_a_b.htm.gz"
