"""Public commands use journal evidence, never direct unverified YAML promotion."""

import json
from types import SimpleNamespace

import pandas as pd
import pytest
from click.testing import CliRunner

from agentic_trader.cli.commands.alpha import download_bars
from agentic_trader.cli.main import cli
from agentic_trader.research.alpha.models import AlphaDefinition


@pytest.mark.parametrize("command", ["catalog", "list", "export"])
def test_alpha_read_commands(command):
    result = CliRunner().invoke(cli, ["alpha", command])
    assert result.exit_code == 0, result.output


def test_catalog_inspect_is_read_only():
    result = CliRunner().invoke(cli, ["alpha", "inspect", "alpha_wq_006"])
    assert result.exit_code == 0, result.output
    assert "ts_corr" in result.output


def test_import_shadow_promote_rejected_demote(tmp_path):
    path = tmp_path / "alphas.yaml"
    path.write_text(
        "promoted_alphas:\n- definition:\n    alpha_id: alpha_test\n    name: Test\n    expression: close\n"
    )
    runner = CliRunner()
    imported = runner.invoke(cli, ["alpha", "import", str(path)])
    assert imported.exit_code == 0, imported.output
    exported = runner.invoke(cli, ["alpha", "export"])
    snapshot = json.loads(exported.output)
    assert len(snapshot["shadow"]) == 1

    version = AlphaDefinition.from_dict(snapshot["shadow"][0]).version_id
    promoted = runner.invoke(cli, ["alpha", "promote", version, "--generation", "1"])
    assert promoted.exit_code != 0
    assert "qualification" in promoted.output
    demoted = runner.invoke(cli, ["alpha", "demote", version, "--generation", "1"])
    assert demoted.exit_code == 0, demoted.output
    assert "existing positions retain protection" in demoted.output


def test_unattended_auto_promotion_removed():
    result = CliRunner().invoke(cli, ["alpha", "mine", "--auto-promote"])
    assert result.exit_code != 0
    assert "No such option" in result.output


def test_diagnostic_command_records_exposure_before_a_failed_evaluation(monkeypatch):
    frame = pd.DataFrame(
        {"Open": [100.0] * 80, "High": [101.0] * 80, "Low": [99.0] * 80, "Close": [100.0] * 80},
        index=pd.date_range("2025-01-01", periods=80, tz="UTC"),
    )
    frame.attrs["timeframe"] = "1d"
    monkeypatch.setattr("agentic_trader.cli.commands.alpha.download_bars", lambda *a, **kw: frame)

    def failed(*args):
        raise ValueError("injected evaluation interruption")

    monkeypatch.setattr("agentic_trader.cli.commands.alpha.AlphaMiner.evaluate_alpha", failed)
    runner = CliRunner()
    result = runner.invoke(cli, ["alpha", "test", "close"])
    assert result.exit_code != 0
    status = runner.invoke(cli, ["alpha", "status"])
    assert status.exit_code == 0, status.output
    assert json.loads(status.output)["research_family"]["trial_count"] == 1


def test_research_download_cannot_relabel_crypto_as_a_stock_feed(monkeypatch, config):
    frame = pd.DataFrame(
        {"Open": [100.0], "High": [101.0], "Low": [99.0], "Close": [100.0]},
        index=pd.date_range("2025-01-01", periods=1, tz="UTC"),
    )
    frame.attrs.update(feed="alpaca:crypto", adjustment="raw", timeframe="1d")
    monkeypatch.setattr(
        "agentic_trader.cli.commands.alpha.AlpacaDataProvider",
        lambda **kw: SimpleNamespace(fetch_bars=lambda *a, **kw: frame),
    )
    with pytest.raises(ValueError, match="feed"):
        download_bars("BTC/USD", "1y", "1d", feed="alpaca", config=config)


def test_calibration_cli_is_synthetic_and_cannot_construct_runtime_services(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("Calibration cannot access runtime config, database or market provider")

    for name in ("load_config", "SignalDatabase", "AlpacaDataProvider"):
        monkeypatch.setattr(f"agentic_trader.cli.commands.alpha.{name}", forbidden)
    output = tmp_path / "calibration.json"
    result = CliRunner().invoke(
        cli,
        [
            "alpha",
            "calibrate",
            "--seeds",
            "1",
            "--observations",
            "600",
            "--bootstrap-samples",
            "99",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    report = json.loads(output.read_text())
    assert report["synthetic_only"] and not report["authorizes_promotion"]
    assert report["strategy_controls"] and report["family_controls"]
    assert output.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob("*.db"))
