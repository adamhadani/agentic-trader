"""Public commands use journal evidence, never direct unverified YAML promotion."""

import asyncio
import json
from contextlib import nullcontext
from dataclasses import asdict
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from click.testing import CliRunner

from agentic_trader.cli.commands.alpha import alpha_repository, download_bars
from agentic_trader.cli.main import cli
from agentic_trader.data.evidence import BarAcquisitionError
from agentic_trader.research.alpha.data import save_dataset
from agentic_trader.research.alpha.lifetime_attribution import LifetimeAttributionProtocol
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.study import MarketScenario, PanelScenario, StudyProtocol
from agentic_trader.research.alpha.validation import DatasetManifest, ValidationPolicy


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


def test_mining_failure_keeps_raw_evidence_in_existing_journal(monkeypatch):
    evidence = {"artifact": "fixture/result.json", "sha256": "fixture-hash"}

    def failed(*args, **kwargs):
        raise BarAcquisitionError("APIError", evidence)

    monkeypatch.setattr("agentic_trader.cli.commands.alpha.download_bars", failed)
    result = CliRunner().invoke(cli, ["alpha", "mine", "--symbol", "SPY", "--iterations", "1"])
    assert result.exit_code != 0

    async def check():
        async with alpha_repository() as repo:
            latest = await repo.get("research/latest")
            assert latest["evidence"] == evidence
            await repo.rebuild()
            assert await repo.get("research/latest") == latest

    asyncio.run(check())


def test_mining_reserves_before_provider_io(monkeypatch):
    events = []

    class Repository:
        async def snapshot(self):
            return SimpleNamespace(active=[])

        async def reserve_run(self, *args, **kwargs):
            events.append("reserve")

        async def record_failure(self, *args, **kwargs):
            events.append("failure")

    class Context:
        async def __aenter__(self):
            return Repository()

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr("agentic_trader.cli.commands.alpha.alpha_repository", lambda: Context())

    def failed(*args, **kwargs):
        events.append("download")
        raise ValueError("injected provider failure")

    monkeypatch.setattr("agentic_trader.cli.commands.alpha.download_bars", failed)
    result = CliRunner().invoke(cli, ["alpha", "mine", "--symbol", "SPY", "--iterations", "0"])

    assert result.exit_code != 0
    assert events == ["reserve", "download", "failure"]


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


def test_research_resampling_explicitly_changes_its_clock_timeframe(monkeypatch, config):
    frame = pd.DataFrame(
        {"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 10.0},
        index=pd.date_range("2024-01-01", periods=8, freq="h", tz="UTC"),
    )
    frame.attrs.update(feed=f"alpaca:{config.market_data.alpaca_feed}", adjustment="raw", timeframe="1h")
    monkeypatch.setattr(
        "agentic_trader.cli.commands.alpha.AlpacaDataProvider",
        lambda **kw: SimpleNamespace(fetch_bars=lambda *a, **kw: frame),
    )
    sampled = download_bars("SPY", "1y", "4h", feed="alpaca", config=config)
    assert len(sampled) == 2
    assert sampled.attrs["timeframe"] == "4h"
    assert sampled.attrs["bar_layout"] == "fixed_duration_v1"
    assert list(sampled.Volume) == [40, 40]


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


def test_lifetime_plan_cli_freezes_protocol_without_runtime_services(tmp_path):
    output = tmp_path / "lifetime-protocol.json"
    result = CliRunner().invoke(cli, ["alpha", "lifetime-plan", "--seed", "441", "--output", str(output)])
    assert result.exit_code == 0, result.output
    protocol = LifetimeAttributionProtocol.from_document(json.loads(output.read_text()))
    assert protocol.seed == 441 and protocol.document()["contracts"]["synthetic_only"]


def test_lifetime_study_cli_runs_paired_artifacts_without_runtime_services(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("Lifetime study cannot access runtime services")

    for name in ("load_config", "SignalDatabase", "AlpacaDataProvider"):
        monkeypatch.setattr(f"agentic_trader.cli.commands.alpha.{name}", forbidden)
    protocol = LifetimeAttributionProtocol(
        seed=442,
        development_replicates=1,
        null_replicates=1,
        edge_replicates=1,
        generated_candidates=1,
        observations=600,
        search_timeout_seconds=5,
    )
    protocol_path = tmp_path / "protocol.json"
    protocol_path.write_text(json.dumps(protocol.document()))
    output = tmp_path / "run"
    result = CliRunner().invoke(cli, ["alpha", "lifetime-study", str(protocol_path), "--output", str(output)])
    assert result.exit_code == 0, result.output
    summary = json.loads((output / "completion.json").read_text())
    assert summary["status"] == "completed" and summary["recorded_jobs"] == summary["expected_jobs"]


def test_session_replay_cli_persists_failed_attempt_without_broker_or_notifier(monkeypatch, tmp_path):
    def fail_capture(*args):
        raise OSError("fixture capture failed")

    monkeypatch.setattr(
        "agentic_trader.cli.commands.alpha.session_source",
        lambda config, feed: nullcontext(SimpleNamespace(calendar=fail_capture)),
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


@pytest.mark.parametrize("missing", [False, True])
def test_forecast_benchmark_cli_retains_success_and_failure(tmp_path, missing):
    rng = np.random.default_rng(14)
    close = 100 * np.exp(rng.normal(0, 0.01, 500).cumsum())
    bars = pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": rng.integers(1000, 10000, 500),
        },
        index=pd.date_range("2020-01-01", periods=500, tz="UTC"),
    )
    bars.attrs.update(timeframe="1d", feed="synthetic", adjustment="raw")
    manifest = DatasetManifest.from_frame(
        bars, symbol="SPY", timeframe="1d", feed="synthetic", adjustment="raw", universe_version="fixture"
    ).to_dict()
    path = save_dataset(bars, tmp_path, manifest["content_hash"])
    manifest.update(artifact=str(path), holdout_start=str(bars.index[400]))

    async def setup():
        async with alpha_repository() as repo:
            await repo.record_run(
                "fixture",
                {"policy": asdict(ValidationPolicy()), "trial_count": 0, "trials": [], "holdout_start": 400},
                manifest,
            )

    asyncio.run(setup())
    if missing:
        path.unlink()
    output = tmp_path / "benchmark"
    result = CliRunner().invoke(
        cli,
        [
            "alpha",
            "benchmark",
            "fixture",
            "--method",
            "single",
            "--budget",
            "2",
            "--horizon",
            "1",
            "--label",
            "next_open_to_close",
            "--feature",
            "open_gap",
            "--feature",
            "roc(close,5)",
            "--cost-bps",
            "0",
            "--cost-bps",
            "5",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == int(missing), result.output
    document = json.loads((output / "result.json").read_text())
    assert document["status"] == ("failed" if missing else "completed")
    assert not document["authorizes_promotion"]
    status = CliRunner().invoke(cli, ["alpha", "status"])
    assert json.loads(status.output)["research_family"]["trial_count"] == 6


@pytest.mark.parametrize("options", [[], ["--days", "1", "--limit", "1"]])
def test_forward_evidence_cli_uses_isolated_storage(options):
    result = CliRunner().invoke(cli, ["alpha", "forward", *options])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["candidates"] == [] and not report["authorizes_promotion"]
    assert report["coverage_basis"] == "recorded_decisions" and not report["truncated"]


@pytest.mark.parametrize("option", ["--entry-lifetime-seconds", "--holding-lifetime-seconds"])
def test_replay_requires_both_lifetimes_before_provider_access(option):
    result = CliRunner().invoke(
        cli,
        [
            "alpha",
            "replay",
            "returns",
            "--symbol",
            "SPY",
            "--start",
            "2024-11-27",
            "--end",
            "2024-11-29",
            option,
            "300",
        ],
    )
    assert result.exit_code != 0 and "Both entry and holding lifetimes" in result.output


@pytest.mark.parametrize("kind", ["entry", "entry_cancel"])
def test_workflow_queue_cli_supports_read_only_cancellation_inspection(kind):
    result = CliRunner().invoke(cli, ["db", "queue", "--kind", kind])
    assert result.exit_code == 0, result.output
