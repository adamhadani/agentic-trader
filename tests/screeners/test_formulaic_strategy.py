from __future__ import annotations

import numpy as np
import pandas as pd

from agentic_trader.constants import AssetClass, Direction
from agentic_trader.data.market_data import ContractMarketData
from agentic_trader.research.alpha.models import (
    AlphaDefinition,
    AlphaOrigin,
)
from agentic_trader.screeners.formulaic import FormulaicAlphaStrategy
from agentic_trader.screeners.registry import StrategyRegistry


def create_mock_market_data(n_candles: int = 60, trend: float = 1.0) -> ContractMarketData:
    np.random.seed(42)
    dates = pd.date_range("2026-09-01", periods=n_candles, freq="4h")
    ret = np.random.normal(0.005 * trend, 0.01, size=n_candles)
    close = 5000.0 * np.exp(np.cumsum(ret))
    high = close * 1.01
    low = close * 0.99
    open_p = (high + low) / 2.0
    volume = np.random.uniform(5000, 15000, size=n_candles)

    df = pd.DataFrame(
        {
            "Open": open_p,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume,
        },
        index=dates,
    )

    return ContractMarketData(
        contract="BTC-USDT",
        ticker="BTC-USDT",
        daily=df,
        four_hour=df,
        hourly=df,
    )


def test_formulaic_strategy_evaluation():
    # Momentum alpha: delta(close, 5)
    defn = AlphaDefinition(
        alpha_id="alpha_wq_006",
        name="WorldQuant #6",
        expression="-1.0 * ts_corr(open, volume, 10)",
        description="Correlation between open and volume",
        origin=AlphaOrigin.WORLDQUANT_101,
        direction="bi_directional",
        entry_threshold=0.1,
        timeframe="4h",
    )

    strat = FormulaicAlphaStrategy(definition=defn)
    assert strat.strategy_id == "alpha_wq_006"
    assert strat.display_name == "WorldQuant #6"

    md = create_mock_market_data(n_candles=60)
    candidates = strat.evaluate(md, asset_class=AssetClass.CRYPTO)

    # Strategy should evaluate and emit candidate stamped with strategy_id
    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.strategy == "alpha_wq_006"
    assert cand.contract == "BTC-USDT"
    assert cand.direction in (Direction.LONG, Direction.SHORT)
    assert cand.ema_20 > 0
    assert cand.rsi_14 > 0
    assert cand.atr_14 > 0
    assert "Alpha alpha_wq_006 triggered" in cand.trigger_detail


def test_registry_snapshot_replacement():
    definition = AlphaDefinition(
        "alpha_custom_trend", "Custom", "delta(close,3)", entry_threshold=0.1, direction="long"
    )
    registry = StrategyRegistry()
    assert registry.install_alphas((definition,)) == 1
    strategy = registry.get(definition.alpha_id)
    assert strategy.definition.version_id == definition.version_id
    assert registry.install_alphas(()) == 0
    assert registry.list_strategies() == []


def test_formulaic_strategy_eligible_symbols_filtering():
    """Verify that eligible_symbols enforces strict multi-asset universe filtering."""
    defn = AlphaDefinition(
        alpha_id="alpha_chip_trend",
        name="Semiconductor Trend Alpha",
        expression="delta(close, 2)",
        direction="long",
        entry_threshold=0.1,
        timeframe="4h",
        eligible_symbols=["NVDA", "AMD"],
    )

    strat = FormulaicAlphaStrategy(definition=defn)

    # 1. Matching symbol NVDA
    md_nvda = ContractMarketData(
        contract="NVDA",
        ticker="NVDA",
        daily=create_mock_market_data(n_candles=60, trend=2.0).daily,
        four_hour=create_mock_market_data(n_candles=60, trend=2.0).four_hour,
        hourly=create_mock_market_data(n_candles=60, trend=2.0).hourly,
    )
    res_nvda = strat.evaluate(md_nvda, asset_class=AssetClass.EQUITY)
    assert len(res_nvda) == 1
    assert res_nvda[0].contract == "NVDA"

    # 2. Non-matching symbol SPY
    md_spy = ContractMarketData(
        contract="SPY",
        ticker="SPY",
        daily=create_mock_market_data(n_candles=60, trend=2.0).daily,
        four_hour=create_mock_market_data(n_candles=60, trend=2.0).four_hour,
        hourly=create_mock_market_data(n_candles=60, trend=2.0).hourly,
    )
    res_spy = strat.evaluate(md_spy, asset_class=AssetClass.EQUITY)
    assert len(res_spy) == 0

    # 3. Strategy with eligible_symbols=None allows all symbols
    defn_all = AlphaDefinition(
        alpha_id="alpha_broad_trend",
        name="Broad Trend Alpha",
        expression="delta(close, 2)",
        direction="long",
        entry_threshold=0.1,
        timeframe="4h",
        eligible_symbols=None,
    )
    strat_all = FormulaicAlphaStrategy(definition=defn_all)
    res_all = strat_all.evaluate(md_spy, asset_class=AssetClass.EQUITY)
    assert len(res_all) == 1
