from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from agentic_trader.cli.commands.alpha_ic import ic_group
from agentic_trader.research.ic.sectors import acquire_sectors, load_ff12, load_sectors


FF12_PATH = Path(__file__).resolve().parents[3] / "config" / "research" / "ic" / "ff12-sic-v1.json"
TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple"},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft"},
    "2": {"cik_str": 1, "ticker": "BLNK", "title": "Blank"},
    "3": {"cik_str": 2, "ticker": "NOSIC", "title": "Nokey"},
}
SENTINEL_AGENT = "sentinel-agent person@example.invalid"


async def _pace() -> None:
    return None


@pytest.fixture(scope="module")
def ff12():
    return load_ff12(FF12_PATH)


def _handler(submissions, seen=None):
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        if request.url.path.endswith("company_tickers.json"):
            return httpx.Response(200, json=TICKERS)
        result = submissions.get(request.url.path.rsplit("/", 1)[-1])
        if callable(result):
            return result(request)
        return result if result is not None else httpx.Response(404)

    return handle


async def _run(handler, symbols, ff12):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"User-Agent": SENTINEL_AGENT},
        follow_redirects=False,
    ) as client:
        return await acquire_sectors(symbols, client=client, pace=_pace, ff12=ff12, progress=None)


@pytest.mark.parametrize(
    "sic,group",
    [(3693, "Hlth"), (3694, "BusEq"), (3692, "BusEq"), (2830, "Hlth"), (2829, "Chems"), (9999, "Other")],
)
def test_ff12_boundaries(ff12, sic, group):
    assert ff12.group(sic) == group


def test_ff12_no_sic_is_unknown(ff12):
    assert ff12.group(None) == "unknown"


def test_ff12_ranges_do_not_overlap(ff12):
    seen: dict[int, str] = {}
    for name, low, high in ff12.ranges:
        for sic in range(low, high + 1):
            assert sic not in seen, (sic, name, seen.get(sic))
            seen[sic] = name


async def test_acquire_maps_ticker_to_cik_to_group(ff12):
    handler = _handler({"CIK0000320193.json": httpx.Response(200, json={"sic": "3571"})})
    doc = await _run(handler, ["AAPL"], ff12)
    assert doc["symbols"]["AAPL"] == {"cik": 320193, "sic": 3571, "group": "BusEq"}
    assert doc["failures"] == {}
    assert doc["ff12_sha256"] == ff12.sha256


async def test_acquire_pads_cik_to_ten_digits_in_the_url(ff12):
    seen: list[str] = []
    await _run(_handler({"CIK0000000001.json": httpx.Response(200, json={"sic": "6199"})}, seen), ["BLNK"], ff12)
    assert "https://data.sec.gov/submissions/CIK0000000001.json" in seen


async def test_acquire_records_a_failed_name_and_continues(ff12):
    handler = _handler({"CIK0000789019.json": httpx.Response(200, json={"sic": "7372"})})
    doc = await _run(handler, ["AAPL", "MSFT"], ff12)
    assert doc["failures"] == {"AAPL": "HTTPStatusError"}
    assert doc["symbols"]["MSFT"]["group"] == "BusEq"


async def test_acquire_treats_blank_sic_as_unknown(ff12):
    handler = _handler(
        {
            "CIK0000000001.json": httpx.Response(200, json={"sic": ""}),
            "CIK0000000002.json": httpx.Response(200, json={}),
        }
    )
    doc = await _run(handler, ["BLNK", "NOSIC"], ff12)
    assert doc["symbols"]["BLNK"] == {"cik": 1, "sic": None, "group": "unknown"}
    assert doc["symbols"]["NOSIC"]["group"] == "unknown"


async def test_acquire_handles_non_json_body(ff12):
    handler = _handler({"CIK0000320193.json": httpx.Response(200, content=b"<html>secret body text</html>")})
    doc = await _run(handler, ["AAPL"], ff12)
    assert doc["failures"] == {"AAPL": "ValueError"}
    assert "secret body text" not in json.dumps(doc)


async def test_acquire_refuses_another_host(ff12):
    redirect = httpx.Response(302, headers={"location": "https://evil.example/x.json"})
    seen: list[str] = []
    doc = await _run(_handler({"CIK0000320193.json": redirect}, seen), ["AAPL"], ff12)
    assert "AAPL" in doc["failures"]
    assert not any("evil.example" in url for url in seen)


async def test_acquire_retries_once_on_transport_error(ff12):
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json={"sic": "2911"})

    doc = await _run(_handler({"CIK0000320193.json": flaky}), ["AAPL"], ff12)
    assert calls["n"] == 2
    assert doc["symbols"]["AAPL"]["group"] == "Enrgy"


async def test_acquire_records_unmapped_ticker(ff12):
    doc = await _run(_handler({}), ["ZZZZ"], ff12)
    assert doc["failures"] == {"ZZZZ": "no_ticker"}


async def test_user_agent_never_reaches_the_document(ff12):
    handler = _handler({"CIK0000320193.json": httpx.Response(200, json={"sic": "3571"})})
    doc = await _run(handler, ["AAPL", "ZZZZ"], ff12)
    assert SENTINEL_AGENT not in json.dumps(doc)


def test_load_sectors_refuses_a_digest_mismatch(tmp_path):
    path = tmp_path / "sectors.json"
    path.write_text(json.dumps({"symbols": {}}))
    assert load_sectors(path).sha256
    with pytest.raises(ValueError, match="digest"):
        load_sectors(path, "0" * 64)


def test_groups_unmapped_symbol_is_unknown(tmp_path):
    path = tmp_path / "sectors.json"
    path.write_text(json.dumps({"symbols": {"AAPL": {"cik": 1, "sic": 3571, "group": "Manuf"}}}))
    assert list(load_sectors(path).groups(["AAPL", "ZZZ"])) == ["Manuf", "unknown"]


def test_cli_requires_edgar_user_agent_and_writes_nothing_without_it(tmp_path, monkeypatch):
    monkeypatch.delenv("EDGAR_USER_AGENT", raising=False)
    cohort = Path(__file__).resolve().parents[3] / "config" / "research" / "pooled" / "cohort-v2.json"
    out = tmp_path / "sectors.json"
    result = CliRunner().invoke(
        ic_group, ["sectors", "--cohort", str(cohort), "--ff12", str(FF12_PATH), "--output", str(out)]
    )
    assert result.exit_code != 0
    assert "EDGAR_USER_AGENT" in result.output
    assert not out.exists()
