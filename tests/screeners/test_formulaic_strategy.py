from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from agentic_trader.constants import AssetClass, Direction
from agentic_trader.data.market_data import ContractMarketData
from agentic_trader.research.alpha.models import (
    AlphaCandidate,
    AlphaDefinition,
    AlphaEvaluationMetrics,
    AlphaOrigin,
)
from agentic_trader.research.alpha.promotion import AlphaPromotionManager
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
        exit_threshold=0.0,
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


def test_registry_promoted_alphas_lifecycle():
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as tf:
        cfg_path = Path(tf.name)

    try:
        promo_mgr = AlphaPromotionManager(config_path=cfg_path)
        registry = StrategyRegistry(auto_load_promoted=False)

        # Baseline: no formulaic alphas registered yet
        assert len(registry.list_strategies()) == 0

        # Promote an alpha
        defn = AlphaDefinition(
            alpha_id="alpha_custom_trend",
            name="Custom Trend Alpha",
            expression="delta(close, 3)",
            direction="long",
            entry_threshold=0.1,
            timeframe="4h",
        )
        metrics = AlphaEvaluationMetrics(
            rank_ic_mean=0.05,
            rank_ic_std=0.01,
            rank_ic_ir=5.0,
            sharpe_is=2.0,
            sharpe_oos=1.8,
            dsr=0.95,
        )
        promo_mgr.promote(AlphaCandidate(definition=defn, metrics=metrics), promoted_by="unit_test")

        # Load into registry
        loaded = registry.load_promoted_alphas(config_path=cfg_path)
        assert loaded == 1
        assert "alpha_custom_trend" in registry.list_registered_strategies()

        # Evaluate market data through retrieved strategy
        strat = registry.get("alpha_custom_trend")
        assert strat is not None
        md = create_mock_market_data(n_candles=60, trend=2.0)
        results = strat.evaluate(md, asset_class=AssetClass.CRYPTO)
        assert len(results) >= 1
        assert results[0].strategy == "alpha_custom_trend"

        # Demote and re-sync
        promo_mgr.demote("alpha_custom_trend", reason="Retiring test alpha")
        loaded_after = registry.load_promoted_alphas(config_path=cfg_path)
        assert loaded_after == 0
        assert "alpha_custom_trend" not in registry.list_registered_strategies()
    finally:
        if cfg_path.exists():
            cfg_path.unlink()
