import argparse
import tempfile
from pathlib import Path

import pandas as pd
import yaml

from agentic_trader.config import (
    AppConfig,
    SqueezeBreakoutConfig,
    StrategyConfig,
    TrendPullbackConfig,
    load_config,
)
from agentic_trader.constants import AssetClass, Direction
from agentic_trader.data.market_data import ContractMarketData
from agentic_trader.research.models import ParameterCandidate
from agentic_trader.research.reporting import (
    candidate_to_strategy_dict,
    export_candidate_to_config,
    format_candidate_as_yaml,
)
from agentic_trader.screeners.strategies import StrategyEngine


def create_mock_market_data(
    daily_close=5800.0,
    daily_ema50=5700.0,
    daily_ema200=5500.0,
    four_h_close=5800.0,
    four_h_ema20=5800.0,
    four_h_rsi_seq=None,
    four_h_atr=20.0,
    squeeze_bars=0,
    breakout=False,
    volume=5000.0,
    volume_sma=5000.0,
):
    if four_h_rsi_seq is None:
        four_h_rsi_seq = [43.0, 42.0, 41.0, 44.0]

    dates_daily = pd.date_range("2026-01-01", periods=20, freq="1D")
    df_daily = pd.DataFrame(
        {
            "Open": [daily_close] * 20,
            "High": [daily_close + 10] * 20,
            "Low": [daily_close - 10] * 20,
            "Close": [daily_close] * 20,
            "Volume": [10000.0] * 20,
            "EMA_50": [daily_ema50] * 20,
            "EMA_200": [daily_ema200] * 20,
            "ATR_14": [25.0] * 20,
        },
        index=dates_daily,
    )

    dates_4h = pd.date_range("2026-01-01", periods=30, freq="4h")
    closes = [four_h_close] * 30
    if breakout:
        closes[-1] = four_h_close + 25.0  # breakout above BB Upper

    bb_upper = [four_h_close + 10] * 30
    squeeze_counts = [squeeze_bars] * 30

    volumes = [volume_sma] * 30
    volumes[-1] = volume

    df_4h = pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 5 for c in closes],
            "Low": [c - 5 for c in closes],
            "Close": closes,
            "Volume": volumes,
            "EMA_20": [four_h_ema20] * 30,
            "EMA_50": [daily_ema50] * 30,
            "EMA_200": [daily_ema200] * 30,
            "RSI_14": [50.0] * 26 + four_h_rsi_seq,
            "ATR_14": [four_h_atr] * 30,
            "BB_Upper": bb_upper,
            "BB_Middle": [four_h_close] * 30,
            "BB_Lower": [four_h_close - 10] * 30,
            "KC_Upper": [four_h_close + 15] * 30,
            "KC_Middle": [four_h_close] * 30,
            "KC_Lower": [four_h_close - 15] * 30,
            "Squeeze": [squeeze_bars > 0] * 30,
            "Squeeze_Count": squeeze_counts,
            "Volume_SMA_20": [volume_sma] * 30,
        },
        index=dates_4h,
    )

    return ContractMarketData(
        contract="/MES",
        ticker="MES=F",
        daily=df_daily,
        four_hour=df_4h,
        hourly=pd.DataFrame(),
    )


def test_trend_pullback_config_defaults_and_customization():
    cfg = TrendPullbackConfig()
    assert cfg.enabled is True
    assert cfg.daily_ema_fast == 50
    assert cfg.daily_ema_slow == 200
    assert cfg.trigger_ema_span == 20
    assert cfg.rsi_oversold == 40.0
    assert cfg.rsi_oversold_dip == 45.0
    assert cfg.rsi_overbought == 60.0
    assert cfg.rsi_overbought_surge == 55.0
    assert cfg.atr_multiplier == 1.5

    custom = TrendPullbackConfig(
        daily_ema_fast=20,
        daily_ema_slow=100,
        trigger_ema_span=10,
        rsi_oversold=30.0,
        rsi_oversold_dip=35.0,
        atr_multiplier=2.0,
    )
    assert custom.daily_ema_fast == 20
    assert custom.daily_ema_slow == 100
    assert custom.trigger_ema_span == 10
    assert custom.rsi_oversold == 30.0
    assert custom.rsi_oversold_dip == 35.0
    assert custom.atr_multiplier == 2.0


def test_strategy_engine_respects_custom_rsi_thresholds():
    # RSI sequence: dipped to 32 and recovered to 35
    data = create_mock_market_data(four_h_rsi_seq=[36.0, 32.0, 33.0, 35.0])

    # Default config: requires rsi_current >= 40.0 -> Should NOT trigger
    engine_default = StrategyEngine(load_config())
    cand_default = engine_default.check_trend_pullback(data, asset_class=AssetClass.FUTURES)
    assert cand_default is None

    # Custom config: rsi_oversold=30.0, rsi_oversold_dip=35.0 -> min_rsi (32) < 35 and current (35) >= 30 -> Should trigger
    app_cfg = AppConfig(
        strategies=StrategyConfig(
            trend_pullback=TrendPullbackConfig(
                rsi_oversold=30.0,
                rsi_oversold_dip=35.0,
            )
        )
    )
    engine_custom = StrategyEngine(app_cfg)
    cand_custom = engine_custom.check_trend_pullback(data, asset_class=AssetClass.FUTURES)
    assert cand_custom is not None
    assert cand_custom.direction == Direction.LONG
    assert "32.0" in cand_custom.trigger_detail
    assert "35.0" in cand_custom.trigger_detail


def test_strategy_engine_dynamic_daily_ema_calculation():
    # Provide upward trending daily prices so Close > EMA_5 > EMA_10
    trending_closes = [5000.0 + i * 50.0 for i in range(20)]
    last_close = trending_closes[-1]
    data = create_mock_market_data(
        daily_close=last_close,
        four_h_close=last_close,
        four_h_ema20=last_close,
    )
    data.daily["Close"] = trending_closes

    app_cfg = AppConfig(
        strategies=StrategyConfig(
            trend_pullback=TrendPullbackConfig(
                daily_ema_fast=5,
                daily_ema_slow=10,
                trigger_ema_span=15,
            )
        )
    )
    engine = StrategyEngine(app_cfg)
    cand = engine.check_trend_pullback(data, asset_class=AssetClass.FUTURES)
    assert cand is not None
    assert "near 15 EMA" in cand.trigger_detail


def test_strategy_engine_custom_squeeze_config():
    # 3 squeeze bars prior, volume surge 1.25x
    data = create_mock_market_data(
        squeeze_bars=3,
        breakout=True,
        volume=6250.0,  # 1.25x of 5000.0
        volume_sma=5000.0,
    )

    # Default config requires min_squeeze_bars=5, volume_factor=1.3 -> should not trigger
    engine_default = StrategyEngine(load_config())
    cand_default = engine_default.check_squeeze_breakout(data, timeframe="4h")
    assert cand_default is None

    # Custom config: min_squeeze_bars=3, volume_factor=1.2 -> should trigger
    app_cfg = AppConfig(
        strategies=StrategyConfig(
            squeeze_breakout=SqueezeBreakoutConfig(
                min_squeeze_bars=3,
                volume_factor=1.2,
            )
        )
    )
    engine_custom = StrategyEngine(app_cfg)
    cand_custom = engine_custom.check_squeeze_breakout(data, timeframe="4h")
    assert cand_custom is not None
    assert cand_custom.direction == Direction.LONG


def test_candidate_to_strategy_dict_and_yaml_formatting():
    cand = ParameterCandidate(
        parameters={"ema_span": 10, "rsi_threshold": 45},
        total_return_pct=15.5,
        total_trades=12,
        win_rate=75.0,
        sharpe_ratio=2.85,
        max_drawdown_pct=2.1,
        profit_factor=3.1,
    )

    strat_dict = candidate_to_strategy_dict(cand, "trend_pullback")
    assert strat_dict["enabled"] is True
    assert strat_dict["trigger_ema_span"] == 10
    assert strat_dict["rsi_oversold"] == 45.0
    assert strat_dict["rsi_oversold_dip"] == 50.0
    assert strat_dict["rsi_overbought"] == 55.0
    assert strat_dict["rsi_overbought_surge"] == 50.0

    yaml_str = format_candidate_as_yaml(cand, "trend_pullback")
    parsed = yaml.safe_load(yaml_str)
    assert parsed["strategies"]["trend_pullback"]["trigger_ema_span"] == 10
    assert parsed["strategies"]["trend_pullback"]["rsi_oversold"] == 45.0


def test_export_candidate_to_config_roundtrip():
    cand = ParameterCandidate(
        parameters={"volume_factor": 1.15, "min_squeeze_bars": 3},
        total_return_pct=12.0,
        total_trades=8,
        win_rate=62.5,
        sharpe_ratio=1.95,
        max_drawdown_pct=3.0,
        profit_factor=2.4,
    )

    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
        temp_path = f.name

    try:
        # Write initial skeleton config
        init_cfg = {"portfolio": {"cash": 50000.0}}
        with open(temp_path, "w", encoding="utf-8") as f:
            yaml.dump(init_cfg, f)

        # Export candidate
        success = export_candidate_to_config(cand, "squeeze_breakout", temp_path)
        assert success is True

        # Load with load_config
        loaded = load_config(temp_path)
        assert loaded.strategies.squeeze_breakout.volume_factor == 1.15
        assert loaded.strategies.squeeze_breakout.min_squeeze_bars == 3
    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_cli_optimize_export_config_parser():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    opt_parser = subparsers.add_parser("optimize")
    opt_parser.add_argument("--symbol", default="SPY")
    opt_parser.add_argument(
        "--export-config",
        nargs="?",
        const="stdout",
        default=None,
        help="Export configuration",
    )

    # Test default (no flag passed)
    args_default = parser.parse_args(["optimize"])
    assert args_default.export_config is None

    # Test flag without argument -> defaults to const "stdout"
    args_stdout = parser.parse_args(["optimize", "--export-config"])
    assert args_stdout.export_config == "stdout"

    # Test flag with explicit filepath
    args_file = parser.parse_args(["optimize", "--export-config", "config/custom.yaml"])
    assert args_file.export_config == "config/custom.yaml"
