import json
from types import SimpleNamespace

from click.testing import CliRunner

from agentic_trader.cli.commands import alpha_investor_days as cmd
from tests.research.test_investor_days import Router, tickers


UA = "Test Co test@example.com"


def _cohort(path):
    path.write_text("{}")


def _patch(monkeypatch, router, symbols):
    monkeypatch.setattr(cmd, "_client", lambda user_agent: router.client())
    monkeypatch.setattr(cmd, "research_environment", lambda: {"python": "x"})

    def load(path):
        cohort = SimpleNamespace(symbols=symbols[path.name])
        return SimpleNamespace(cohort=cohort)

    monkeypatch.setattr(cmd, "load_cohort", load)

    async def no_pace():
        return None

    monkeypatch.setattr(cmd, "edgar_pacer", lambda: no_pace)


def test_user_agent_never_written(tmp_path, monkeypatch):
    router = Router(tickers(("AAA", 1), ("BBB", 2)), {1: 1, 2: 1})
    _patch(monkeypatch, router, {"a.json": ["AAA"], "b.json": ["BBB", "AAA"]})
    monkeypatch.setenv("EDGAR_USER_AGENT", UA)
    for name in ("a.json", "b.json"):
        _cohort(tmp_path / name)
    args = ["--cache", str(tmp_path / "cache"), "--output", str(tmp_path / "out")]
    for name in ("a.json", "b.json"):
        args += ["--cohort", str(tmp_path / name)]
    result = CliRunner().invoke(cmd.investor_days_group, ["fetch", *args])
    assert result.exit_code == 0, result.output
    manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
    assert manifest["user_agent_set"] is True and manifest["issuers"] == 2
    for root in (tmp_path / "cache", tmp_path / "out"):
        for file in root.rglob("*"):
            if file.is_file():
                assert b"test@example.com" not in file.read_bytes()
    assert "test@example.com" not in result.output
    again = CliRunner().invoke(cmd.investor_days_group, ["fetch", *args])
    assert again.exit_code == 1 and "exists" in again.output


def test_refuses_without_user_agent(tmp_path, monkeypatch):
    monkeypatch.delenv("EDGAR_USER_AGENT", raising=False)
    _cohort(tmp_path / "a.json")
    result = CliRunner().invoke(
        cmd.investor_days_group,
        [
            "fetch",
            "--cohort",
            str(tmp_path / "a.json"),
            "--cache",
            str(tmp_path / "c"),
            "--output",
            str(tmp_path / "o"),
        ],
    )
    assert result.exit_code == 1 and "EDGAR_USER_AGENT is required" in result.output
