import json
from datetime import UTC, date, datetime

import httpx
import pytest

from agentic_trader.research import insider_filings as mod
from agentic_trader.research.insider_filings import (
    DROP_RULES,
    INDEX_URL,
    TICKERS_URL,
    InsiderFilingStore,
    InsiderTransaction,
    acquire_insider_filings,
    files_sha256,
    parse_quarter,
    quarters_between,
    read_transactions,
    resolve_archive_urls,
    symbol_ciks,
    validate_archive,
)
from tests.research.insider_fixtures import (
    SUBMISSION_COLUMNS,
    TRANS_COLUMNS,
    make_archive as _archive,
    trade,
    trades_archive,
)


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


async def test_byte_budget_aborts_the_stream_before_all_chunks_are_read(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "MAX_ARCHIVE_BYTES", 1024)
    yielded = {"n": 0}

    async def body():
        for _ in range(100):
            yielded["n"] += 1
            yield b"x" * 512

    def handler(request):
        url = str(request.url)
        if url == INDEX_URL:
            return httpx.Response(200, content=f'<a href="{PREFIX}2025q1_form345.zip">x</a>'.encode())
        if url == TICKERS_URL:
            return httpx.Response(200, content=TICKERS)
        return httpx.Response(200, content=body())

    store = InsiderFilingStore(tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="byte budget"):
            await acquire_insider_filings(client, [q("2025q1")], store, pace=_noop)
    assert yielded["n"] < 100 and not store.paths(q("2025q1"))[0].exists()


@pytest.mark.parametrize(
    "href",
    [
        "https://evil.example/files/x/data/2025q1_form345.zip",
        "http://www.sec.gov/files/x/data/2025q1_form345.zip",
        "https://www.sec.gov:8443/files/x/data/2025q1_form345.zip",
    ],
)
async def test_foreign_hosts_fail_closed(tmp_path, href):
    client, seen = _world(index_html=f'<a href="{href}">x</a>')
    async with client:
        with pytest.raises(ValueError, match="host"):
            await acquire_insider_filings(client, [q("2025q1")], InsiderFilingStore(tmp_path), pace=_noop)
    assert [str(r.url) for r in seen] == [INDEX_URL]


def test_resolve_refuses_conflicting_duplicates_and_allows_identical_ones():
    one = f'<a href="{PREFIX}2025q1_form345.zip">x</a>'
    other = '<a href="/files/datastandardsinnovation/data/2025q1_form345.zip">y</a>'
    assert resolve_archive_urls(one + one) == resolve_archive_urls(one)
    with pytest.raises(ValueError, match="conflicting"):
        resolve_archive_urls(one + other)


def test_parse_quarter_refuses_a_trailing_newline():
    with pytest.raises(ValueError, match="quarter must look like 2015q1"):
        parse_quarter("2015q1\n")


async def test_empty_quarters_are_refused(tmp_path):
    client, seen = _world()
    async with client:
        with pytest.raises(ValueError, match="no quarters requested"):
            await acquire_insider_filings(client, [], InsiderFilingStore(tmp_path), pace=_noop)
    assert seen == []


@pytest.mark.parametrize("status", [302, 500])
async def test_non_200_archive_response_raises_and_saves_nothing(tmp_path, status):
    def handler(request):
        url = str(request.url)
        if url == INDEX_URL:
            return httpx.Response(200, content=f'<a href="{PREFIX}2025q1_form345.zip">x</a>'.encode())
        if url == TICKERS_URL:
            return httpx.Response(200, content=TICKERS)
        return httpx.Response(status, headers={"location": "https://www.sec.gov/elsewhere"})

    store = InsiderFilingStore(tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await acquire_insider_filings(client, [q("2025q1")], store, pace=_noop)
    assert store.meta(q("2025q1")) is None and not store.paths(q("2025q1"))[0].exists()


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


ROLES = ("Officer", "Director")


def _read(tmp_path, trades, *, ciks=None, exclude=True, submission_columns=SUBMISSION_COLUMNS):
    path = tmp_path / "2025q1_form345.zip"
    path.write_bytes(trades_archive(trades, submission_columns=submission_columns))
    return read_transactions(
        path,
        cik_to_symbols={1000: ("AAA",)} if ciks is None else ciks,
        owner_roles=ROLES,
        exclude_10b5_1=exclude,
    )


def test_reader_keeps_open_market_officer_and_director_trades_only(tmp_path):
    rows = [
        trade(),  # P/A officer: kept
        trade(code="S", ad="D", role="Director", shares="250.0", price="4"),  # kept
        trade(ad="D"),  # a purchase reported as a disposition
        trade(code="M"),  # option exercise
        trade(doc="4/A"),  # amendment
        trade(role="TenPercentOwner"),
        trade(role="Director,Officer", owner=8),  # kept
        trade(price=0),
        trade(trans="06-MAR-2025"),  # traded after it was filed
    ]
    txs, drops, unscored = _read(tmp_path, rows)
    assert [t.code for t in txs] == ["P", "S", "P"]
    assert txs[0] == InsiderTransaction(
        symbol="AAA",
        filing_date=date(2025, 3, 5),
        trans_date=date(2025, 3, 3),
        code="P",
        dollars=pytest.approx(1000.0),
        owner_cik=7,
    )
    assert txs[1].dollars == pytest.approx(1000.0) and txs[2].owner_cik == 8
    assert drops == {
        "document_type": 1,
        "code": 1,
        "acquired_disposed": 1,
        "owner_role": 1,
        "plan_10b5_1": 0,
        "non_positive": 1,
        "trade_after_filing": 1,
    }
    assert tuple(drops) == DROP_RULES and unscored == 0


def test_reader_drops_non_positive_or_missing_shares_and_parses_month_names(tmp_path):
    rows = [
        trade(shares=0),
        trade(shares=""),
        trade(price=""),
        trade(price=-1),
        trade(filing="31-dec-2024", trans="30-DEC-2024"),
    ]
    txs, drops, _ = _read(tmp_path, rows)
    assert drops["non_positive"] == 4 and [t.filing_date for t in txs] == [date(2024, 12, 31)]


def test_reader_drops_plan_trades_only_when_asked_and_reads_archives_without_the_column(tmp_path):
    rows = [trade(plan="1"), trade(plan="0"), trade(plan="")]
    txs, drops, _ = _read(tmp_path, rows)
    assert len(txs) == 2 and drops["plan_10b5_1"] == 1
    txs, drops, _ = _read(tmp_path, rows, exclude=False)
    assert len(txs) == 3 and drops["plan_10b5_1"] == 0
    old = tuple(column for column in SUBMISSION_COLUMNS if column != "AFF10B5ONE")
    txs, drops, _ = _read(tmp_path, rows, submission_columns=old)
    assert len(txs) == 3 and drops["plan_10b5_1"] == 0


def test_reader_fans_a_shared_cik_out_and_counts_unmapped_issuers(tmp_path):
    rows = [trade(cik=2000), trade(cik=3000), trade(cik=3000, code="M")]
    txs, drops, unscored = _read(tmp_path, rows, ciks={2000: ("GOOG", "GOOGL")})
    assert [(t.symbol, t.code, t.dollars) for t in txs] == [("GOOG", "P", 1000.0), ("GOOGL", "P", 1000.0)]
    # Only a qualifying row outside the map is unscored; a dropped one counts under its rule.
    assert unscored == 1 and drops["code"] == 1


def test_reader_takes_the_first_qualifying_owner_of_a_joint_filing_and_keeps_every_row(tmp_path):
    joint = trade(owners=[(9, "TenPercentOwner"), (7, "Officer"), (8, "Director")])
    joint["trans"].append({**joint["trans"][0], "TRANS_SHARES": "50"})
    txs, _, _ = _read(tmp_path, [joint, trade(owner=7)])
    assert [(t.owner_cik, t.dollars) for t in txs] == [(7, 1000.0), (7, 500.0), (7, 1000.0)]


def test_reader_treats_quotes_literally_and_fails_closed_on_a_malformed_archive(tmp_path):
    columns = (*SUBMISSION_COLUMNS[:-1], "REMARKS", "AFF10B5ONE")
    rows = [trade(), trade()]
    rows[0]["submissions"][0]["REMARKS"] = '"an unbalanced quote'
    txs, _, _ = _read(tmp_path, rows, submission_columns=columns)
    assert len(txs) == 2  # the quote does not swallow the next row
    orphan = tmp_path / "orphan.zip"
    orphan.write_bytes(_archive(trans=[{"ACCESSION_NUMBER": "9", "TRANS_CODE": "P"}]))
    with pytest.raises(ValueError, match="no SUBMISSION row"):
        read_transactions(orphan, cik_to_symbols={}, owner_roles=ROLES, exclude_10b5_1=True)
    ragged = tmp_path / "ragged.zip"
    ragged.write_bytes(_archive(nonderiv_header="\t".join(TRANS_COLUMNS) + "\nx\ty\n"))
    with pytest.raises(ValueError, match="NONDERIV_TRANS.tsv line 2"):
        read_transactions(ragged, cik_to_symbols={}, owner_roles=ROLES, exclude_10b5_1=True)
    with pytest.raises(ValueError, match="31-MAR-2025"):
        _read(tmp_path, [trade(filing="2025-03-31")])


def test_symbol_ciks_maps_tickers_and_reports_missing():
    tickers = {
        "0": {"cik_str": 1, "ticker": "BF-B", "title": "Brown-Forman"},
        "1": {"cik_str": 2, "ticker": "GOOGL", "title": "Alphabet"},
        "2": {"cik_str": 2, "ticker": "goog", "title": "Alphabet"},
        "3": {"cik_str": 4, "ticker": "OTHER", "title": "Not in the cohort"},
    }
    ciks, missing = symbol_ciks(tickers, ["BF.B", "GOOG", "GOOGL", "ZZZZ"])
    assert ciks == {1: ("BF.B",), 2: ("GOOG", "GOOGL")} and missing == ("ZZZZ",)
    clash = {**tickers, "4": {"cik_str": 5, "ticker": "GOOG", "title": "Another issuer"}}
    with pytest.raises(ValueError, match="GOOG"):
        symbol_ciks(clash, ["GOOG"])
