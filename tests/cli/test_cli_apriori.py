import json
from pathlib import Path
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner

from agentic_trader.cli.commands import alpha as alpha_cli
from agentic_trader.cli.main import cli
from agentic_trader.config import AppConfig


ENTRY = Path(__file__).resolve().parents[2] / "config/research/apriori/pead-v1.json"
REPO = Path(__file__).resolve().parents[2]


def test_apriori_study_refuses_an_existing_output_directory(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("must refuse before constructing services")

    for name in ("load_config", "AlpacaDataProvider"):
        monkeypatch.setattr(f"agentic_trader.cli.commands.alpha.{name}", forbidden)
    output = tmp_path / "run"
    output.mkdir()
    result = CliRunner().invoke(cli, ["alpha", "apriori-study", str(ENTRY), "--output", str(output)])
    assert result.exit_code != 0 and "refusing to overwrite" in result.output


def test_apriori_study_writes_the_manifest_before_provider_access(tmp_path, monkeypatch):
    output = tmp_path / "run"
    seen = {}

    async def fake_build(entry, **kwargs):
        seen["manifest"] = (output / "manifest.json").exists()
        raise RuntimeError("stop after the manifest")

    monkeypatch.setattr("agentic_trader.cli.commands.alpha.build_pead_inputs", fake_build)
    monkeypatch.setattr("agentic_trader.cli.commands.alpha._apriori_clients", _fake_clients)
    result = CliRunner().invoke(cli, ["alpha", "apriori-study", str(ENTRY), "--output", str(output)])
    assert seen == {"manifest": True}
    assert result.exit_code != 0
    assert json.loads((output / "result.json").read_text())["status"] == "failed"


class _FakeClients:
    ACTIONS = object()

    def __init__(self):
        self.actions = self.ACTIONS
        self.bars = object()
        self.calendar = object()
        self.static_symbols = ["S00"]
        self.pace = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_clients():
    return _FakeClients()


EXDIV = REPO / "config/research/apriori/exdiv-v1.json"


def test_apriori_study_dispatches_exdiv_by_entry_id_and_writes_the_manifest_first(tmp_path, monkeypatch):
    output = tmp_path / "run"
    seen = {}
    monkeypatch.chdir(tmp_path)  # the cohort resolves against the repository root, never the cwd

    async def fake_build(entry, **kwargs):
        seen["manifest"] = (output / "manifest.json").exists()
        seen["cohort_path"] = kwargs["cohort_path"]
        seen["actions_source"] = kwargs["actions_source"]
        raise RuntimeError("stop after the manifest")

    monkeypatch.setattr("agentic_trader.cli.commands.alpha.build_exdiv_inputs", fake_build)
    monkeypatch.setattr("agentic_trader.cli.commands.alpha._apriori_clients", _fake_clients)
    result = CliRunner().invoke(cli, ["alpha", "apriori-study", str(EXDIV), "--output", str(output)])
    cohort = REPO / "config/research/spread/cohort-v1.json"
    assert seen["manifest"] is True and seen["cohort_path"] == cohort and seen["cohort_path"].is_absolute()
    assert seen["actions_source"] is _FakeClients.ACTIONS
    assert result.exit_code != 0
    assert json.loads((output / "result.json").read_text())["status"] == "failed"
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["entry_id"] == "exdiv" and manifest["cohort"]["path"] == str(cohort)
    assert "sha256" in manifest["cohort"] and manifest["cohort"]["symbols"]


def test_apriori_study_echoes_the_exdiv_decision(tmp_path, monkeypatch):
    async def fake_execute(loaded, directory, **kwargs):
        return {"status": "completed", "decision": "failed", "authorizes_promotion": False}

    monkeypatch.setattr("agentic_trader.cli.commands.alpha.execute_exdiv_study", fake_execute)
    monkeypatch.setattr("agentic_trader.cli.commands.alpha._apriori_clients", _fake_clients)
    result = CliRunner().invoke(cli, ["alpha", "apriori-study", str(EXDIV), "--output", str(tmp_path / "run")])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"status": "completed", "decision": "failed", "decisions": None, "error": None}


class _Session:
    def __init__(self, closed, name):
        self.closed, self.name = closed, name

    def close(self):
        self.closed.append(self.name)


def _client_factory(closed, name):
    def build(*args, **kwargs):
        return SimpleNamespace(_session=_Session(closed, name))

    return build


def test_apriori_clients_refuse_missing_credentials_before_building_any_client(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("no client may be built without corporate-actions credentials")

    monkeypatch.setattr(alpha_cli, "load_config", AppConfig)
    for name in ("BoundedTradingClient", "BoundedStockDataClient"):
        monkeypatch.setattr(alpha_cli, name, forbidden)
    with pytest.raises(click.ClickException, match="credentials"):
        alpha_cli._AprioriClients().__enter__()


def test_apriori_clients_close_every_session_when_enter_fails_after_building(monkeypatch):
    closed: list[str] = []
    config = SimpleNamespace(
        alpaca_api_key="k",
        alpaca_api_secret="s",
        alpaca_paper=True,
        market_data=SimpleNamespace(timeout_seconds=5.0),
        contracts={},
    )

    def pacer(_config):
        raise RuntimeError("pacer unavailable")

    monkeypatch.setattr(alpha_cli, "load_config", lambda: config)
    monkeypatch.setattr(
        alpha_cli,
        "build_corporate_actions_source",
        lambda _config: SimpleNamespace(client=SimpleNamespace(_session=_Session(closed, "actions"))),
    )
    monkeypatch.setattr(alpha_cli, "BoundedTradingClient", _client_factory(closed, "calendar"))
    monkeypatch.setattr(alpha_cli, "BoundedStockDataClient", _client_factory(closed, "data"))
    monkeypatch.setattr(alpha_cli, "AlpacaDataProvider", lambda **kwargs: object())
    monkeypatch.setattr(alpha_cli, "AlpacaCalendarProvider", lambda **kwargs: object())
    monkeypatch.setattr(alpha_cli, "_build_pacer", pacer)
    with pytest.raises(RuntimeError, match="pacer"):
        alpha_cli._AprioriClients().__enter__()
    assert sorted(closed) == ["actions", "calendar", "data"]

    closed.clear()
    monkeypatch.setattr(alpha_cli, "_build_pacer", lambda _config: None)
    with alpha_cli._AprioriClients() as clients:
        assert clients.actions.client._session.name == "actions" and closed == []
    assert sorted(closed) == ["actions", "calendar", "data"]


def test_apriori_study_rejects_an_unknown_entry_id(tmp_path):
    entry = tmp_path / "other.json"
    entry.write_text(json.dumps({"id": "other"}))
    result = CliRunner().invoke(cli, ["alpha", "apriori-study", str(entry), "--output", str(tmp_path / "run")])
    assert result.exit_code != 0 and "Unknown catalog entry id" in result.output


INSIDER = REPO / "config/research/apriori/insider-v1.json"


def test_insider_requires_filings_and_actions(tmp_path):
    result = CliRunner().invoke(
        cli, ["alpha", "apriori-study", str(INSIDER), "--output", str(tmp_path / "run"), "--cache", str(tmp_path)]
    )
    assert result.exit_code != 0 and "--insider-filings and --corporate-actions are required" in result.output


def test_insider_requires_the_pooled_cache(tmp_path):
    args = ["--insider-filings", str(tmp_path), "--corporate-actions", str(tmp_path / "a.json")]
    result = CliRunner().invoke(cli, ["alpha", "apriori-study", str(INSIDER), "--output", str(tmp_path / "run"), *args])
    assert result.exit_code != 0 and "--cache is required" in result.output


def test_filings_flag_refused_for_pead(tmp_path):
    result = CliRunner().invoke(
        cli,
        ["alpha", "apriori-study", str(ENTRY), "--output", str(tmp_path / "run"), "--insider-filings", str(tmp_path)],
    )
    assert result.exit_code != 0 and "apply only to the insider entry" in result.output


def test_insider_existing_output_refused(tmp_path):
    output = tmp_path / "run"
    output.mkdir()
    result = CliRunner().invoke(cli, ["alpha", "apriori-study", str(INSIDER), "--output", str(output)])
    assert result.exit_code != 0 and "refusing to overwrite" in result.output


def test_apriori_study_dispatches_insider(tmp_path, monkeypatch):
    seen = {}

    async def fake_build(entry, **kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop")

    monkeypatch.setattr("agentic_trader.cli.commands.alpha.build_insider_leg_inputs", fake_build)
    monkeypatch.setattr("agentic_trader.cli.commands.alpha._apriori_clients", _fake_clients)
    args = ["--insider-filings", str(tmp_path / "f"), "--corporate-actions", str(tmp_path / "a.json")]
    out = tmp_path / "run"
    CliRunner().invoke(
        cli, ["alpha", "apriori-study", str(INSIDER), "--output", str(out), "--cache", str(tmp_path), *args]
    )
    assert seen["repo_root"] == REPO and seen["cache_dir"] == tmp_path
    assert seen["insider_filings_dir"] == tmp_path / "f" and seen["static_symbols"] == ["S00"]
    assert json.loads((out / "result.json").read_text())["status"] == "failed"
