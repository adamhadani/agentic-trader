import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentic_trader.config import AppConfig, load_config
from agentic_trader.research.retuner import AutoRetuner
from tests.research.test_optimizer import make_synthetic_screener_data


@pytest.fixture
def config() -> AppConfig:
    return load_config()


def test_auto_retuner_initialization(config: AppConfig, tmp_path: Path):
    calib_file = tmp_path / "test_calibrations.json"
    retuner = AutoRetuner(config=config, calibrations_path=calib_file)
    assert retuner.calibrations_path == calib_file
    assert retuner.load_calibrations() == {}


def test_auto_retuner_run_retune_synthetic(config: AppConfig, tmp_path: Path):
    calib_file = tmp_path / "test_calibrations.json"
    retuner = AutoRetuner(config=config, calibrations_path=calib_file)

    md_spy = make_synthetic_screener_data(symbol="SPY", n_bars=70)
    data_map = {"SPY": md_spy}

    # Run retune with low threshold so synthetic data passes
    res = retuner.run_retune(
        symbols=["SPY"],
        strategies=["trend_pullback"],
        min_wfe=-999.0,
        min_sharpe=-999.0,
        lookback="1y",
        market_data_map=data_map,
    )

    assert res["total_tested"] == 1
    assert res["total_updated"] == 1
    assert "SPY:trend_pullback" in res["calibrations"]
    calib = res["calibrations"]["SPY:trend_pullback"]
    assert "parameters" in calib
    assert "wfe_ratio" in calib
    assert "oos_sharpe" in calib
    assert "WEEKEND STRATEGY AUTO-RETUNING REPORT" in res["summary_html"]
    assert "SPY (trend_pullback)" in res["summary_html"]

    # Verify persistence to disk
    assert calib_file.exists()
    loaded = retuner.load_calibrations()
    assert "SPY:trend_pullback" in loaded


def test_auto_retuner_filters_overfitted_candidates(config: AppConfig, tmp_path: Path):
    calib_file = tmp_path / "test_calibrations.json"
    retuner = AutoRetuner(config=config, calibrations_path=calib_file)

    md_spy = make_synthetic_screener_data(symbol="SPY", n_bars=70)
    data_map = {"SPY": md_spy}

    # Unattainably high WFE threshold ensures rejection of candidate
    res = retuner.run_retune(
        symbols=["SPY"],
        strategies=["trend_pullback"],
        min_wfe=999.0,
        min_sharpe=999.0,
        lookback="1y",
        market_data_map=data_map,
    )

    assert res["total_tested"] == 1
    assert res["total_updated"] == 0
    assert "No parameters met robustness thresholds" in res["summary_html"]


def test_auto_retuner_export_to_config(config: AppConfig, tmp_path: Path):
    calib_file = tmp_path / "calibrations.json"
    target_config = tmp_path / "config.yaml"

    calib_data = {
        "SPY:trend_pullback": {
            "symbol": "SPY",
            "strategy": "trend_pullback",
            "parameters": {"rsi_threshold": 38.0, "ema_span": 20},
            "wfe_ratio": 0.72,
            "oos_sharpe": 1.65,
            "oos_return_pct": 14.2,
        }
    }
    with open(calib_file, "w", encoding="utf-8") as f:
        json.dump(calib_data, f)

    retuner = AutoRetuner(config=config, calibrations_path=calib_file)
    count = retuner.export_all_to_config(str(target_config))
    assert count == 1
    assert target_config.exists()


def test_cli_retune_help():
    proc = subprocess.run(
        [sys.executable, "-m", "agentic_trader.main", "retune", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "Run quantitative parameter auto-recalibration" in proc.stdout
    assert "--symbols" in proc.stdout
    assert "--strategy" in proc.stdout
    assert "--min-wfe" in proc.stdout
    assert "--min-sharpe" in proc.stdout
    assert "--export-config" in proc.stdout
