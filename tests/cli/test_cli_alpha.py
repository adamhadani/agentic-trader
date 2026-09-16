"""Public commands use journal evidence, never direct unverified YAML promotion."""

import json
from contextlib import nullcontext
from types import SimpleNamespace

import pandas as pd
import pytest
from click.testing import CliRunner

from agentic_trader.cli.commands.alpha import download_bars
from agentic_trader.cli.main import cli
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.study import MarketScenario, PanelScenario, StudyProtocol


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


@pytest.mark.parametrize("seed", [0, 2**127 + 9])
def test_study_plan_freezes_explicit_seed_without_evaluation(tmp_path, seed):
    path = tmp_path / "protocol.json"
    result = CliRunner().invoke(cli, ["alpha", "study-plan", "--seed", str(seed), "--output", str(path)])
    assert result.exit_code == 0, result.output
    restored = StudyProtocol.from_document(json.loads(path.read_text()))
    assert restored.seed == seed
    assert restored.identity in result.output


def test_study_cli_runs_actual_calculation_without_runtime_services(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("Study cannot access runtime services")

    for name in ("load_config", "SignalDatabase", "AlpacaDataProvider"):
        monkeypatch.setattr(f"agentic_trader.cli.commands.alpha.{name}", forbidden)
    protocol = StudyProtocol(
        seed=441,
        development_search_replicates=1,
        development_panel_replicates=1,
        validation_null_search_replicates=1,
        validation_edge_search_replicates=1,
        validation_null_panel_replicates=1,
        validation_edge_panel_replicates=1,
        generated_candidates=1,
        bootstrap_samples=99,
        block_lengths=(20,),
        panel_effects=(0.0,),
        market_scenarios=(
            MarketScenario(name="fixture", observations=600, interval=8, effect=0.0, volatility_persistence=0.0),
        ),
        panel_scenarios=(
            PanelScenario(name="fixture", observations=80, serial_correlation=0.0, cross_correlation=0.25),
        ),
    )
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps(protocol.document()))
    destination = tmp_path / "run"
    result = CliRunner().invoke(cli, ["alpha", "study", str(path), "--output", str(destination)])
    assert result.exit_code == 0, result.output
    summary = json.loads((destination / "completion.json").read_text())
    assert summary["recorded_jobs"] == summary["expected_jobs"]
    assert summary["failed_jobs"] == 0
    assert summary["status"] == "criteria_not_met"
    assert summary["authorizes_promotion"] is False
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in destination.rglob("*.json"))


def test_session_replay_cli_persists_failed_attempt_without_broker_or_notifier(monkeypatch, tmp_path):
    def fail_capture(*args):
        raise OSError("fixture capture failed")

    monkeypatch.setattr(
        "agentic_trader.cli.commands.alpha.replay_source",
        lambda config, feed: nullcontext(SimpleNamespace(capture=fail_capture)),
        raising=False,
    )
    output = tmp_path / "session-run"
    result = CliRunner().invoke(
        cli,
        [
            "alpha",
            "replay",
            "close",
            "--symbol",
            "SPY",
            "--start",
            "2024-11-27",
            "--end",
            "2024-11-29",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code != 0
    assert (output / "manifest.json").exists(), result.output
    report = json.loads((output / "result.json").read_text())
    assert report["status"] == "failed" and not report["authorizes_promotion"]
    status = CliRunner().invoke(cli, ["alpha", "status"])
    assert json.loads(status.output)["research_family"]["trial_count"] == 1
