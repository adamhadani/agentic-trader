import json
import sys

import httpx
from click.testing import CliRunner

from agentic_trader.cli.commands import alpha_insider_filings as cmd
from agentic_trader.research.insider_filings import INDEX_URL, TICKERS_URL
from tests.research.insider_fixtures import make_archive


UA = "Test Desk test@example.com"
PREFIX = "/files/structureddata/data/insider-transactions-data-sets/"
TICKERS = json.dumps({"0": {"cik_str": 1, "ticker": "AAA", "title": "A"}}).encode()


def _patch(monkeypatch, seen):
    def handler(request):
        seen.append(str(request.url))
        if str(request.url) == INDEX_URL:
            html = "".join(f'<a href="{PREFIX}{q}_form345.zip">x</a>' for q in ("2025q1", "2025q2"))
            return httpx.Response(200, content=html.encode())
        if str(request.url) == TICKERS_URL:
            return httpx.Response(200, content=TICKERS)
        return httpx.Response(200, content=make_archive())

    real = httpx.AsyncClient
    monkeypatch.setattr(cmd.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(cmd, "research_environment", lambda: {"python": "x"})

    async def no_pace():
        return None

    monkeypatch.setattr(cmd, "_pacer", lambda: no_pace)


def _args(tmp_path, start="2025q1", end="2025q2"):
    return ["--from", start, "--through", end, "--cache", str(tmp_path / "cache"), "--output", str(tmp_path / "out")]


def test_refuses_without_a_declared_user_agent(tmp_path, monkeypatch):
    seen: list[str] = []
    _patch(monkeypatch, seen)
    for value in (None, "   "):
        if value is None:
            monkeypatch.delenv("EDGAR_USER_AGENT", raising=False)
        else:
            monkeypatch.setenv("EDGAR_USER_AGENT", value)
        result = CliRunner().invoke(cmd.insider_filings_cmd, _args(tmp_path))
        assert result.exit_code == 1 and "EDGAR_USER_AGENT is required" in result.output
    assert seen == [] and not (tmp_path / "out").exists()


def test_writes_a_manifest_that_never_contains_the_user_agent(tmp_path, monkeypatch):
    seen: list[str] = []
    _patch(monkeypatch, seen)
    monkeypatch.setenv("EDGAR_USER_AGENT", UA)
    argv = ["copilot", "alpha", "insider-filings", *_args(tmp_path)]
    monkeypatch.setattr(sys, "argv", argv)
    result = CliRunner().invoke(cmd.insider_filings_cmd, _args(tmp_path))
    assert result.exit_code == 0, result.output
    raw = (tmp_path / "out" / "manifest.json").read_bytes()
    manifest = json.loads(raw)
    assert manifest["files_sha256"] and manifest["user_agent_set"] is True and manifest["command"] == argv
    assert manifest["environment"] == {"python": "x"} and manifest["quarters_fetched"] == 2
    assert b"test@example.com" not in raw


def test_refuses_existing_output_and_reversed_window(tmp_path, monkeypatch):
    seen: list[str] = []
    _patch(monkeypatch, seen)
    monkeypatch.setenv("EDGAR_USER_AGENT", UA)
    (tmp_path / "out").mkdir()
    result = CliRunner().invoke(cmd.insider_filings_cmd, _args(tmp_path))
    assert result.exit_code != 0 and "exists" in result.output
    (tmp_path / "out").rmdir()
    result = CliRunner().invoke(cmd.insider_filings_cmd, _args(tmp_path, "2025q2", "2025q1"))
    assert result.exit_code != 0 and "precedes" in result.output
    assert seen == []
