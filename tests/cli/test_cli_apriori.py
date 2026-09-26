import json
from pathlib import Path

from click.testing import CliRunner

from agentic_trader.cli.main import cli


ENTRY = Path(__file__).resolve().parents[2] / "config/research/apriori/pead-v1.json"


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
    def __init__(self):
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
