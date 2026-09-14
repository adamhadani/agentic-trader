from __future__ import annotations

import pytest

from agentic_trader.config import AppConfig, load_config
from agentic_trader.constants import AssetClass, ConflictResolutionMode, Direction, StrategyMode, StrategyType
from agentic_trader.data.market_data import ContractMarketData
from agentic_trader.screeners.base import BaseStrategy, ScreenerCandidate, StrategyProtocol
from agentic_trader.screeners.registry import ConflictResolver, StrategyRegistry
from agentic_trader.screeners.strategies import (
    SqueezeBreakoutStrategy,
    StrategyEngine,
    TrendPullbackStrategy,
)
from tests.screeners.test_screeners import create_mock_market_data


class DummyCustomStrategy(BaseStrategy):
    strategy_id: str = "custom_alpha"
    display_name: str = "Custom Alpha Model"
    supported_asset_classes: tuple[AssetClass, ...] = (AssetClass.EQUITY,)
    default_timeframe: str = "1h"

    def __init__(self, should_trigger: bool = True):
        super().__init__()
        self.should_trigger = should_trigger

    def evaluate(
        self,
        data: ContractMarketData,
        asset_class: AssetClass = AssetClass.FUTURES,
    ) -> list[ScreenerCandidate]:
        if not self.should_trigger:
            return []
        return [
            ScreenerCandidate(
                contract=data.contract,
                symbol=data.contract,
                asset_class=asset_class,
                timeframe="1h",
                strategy="CUSTOM_ALPHA",
                direction=Direction.LONG,
                current_price=500.0,
                ema_20=495.0,
                ema_50=490.0,
                ema_200=480.0,
                rsi_14=45.0,
                atr_14=5.0,
                candle_timestamp="2026-09-14T12:00:00Z",
                recent_swing_low=490.0,
                recent_swing_high=510.0,
                trigger_detail="Custom alpha signal trigger",
            )
        ]


@pytest.fixture
def base_config() -> AppConfig:
    return load_config()


def test_strategy_protocol_compliance():
    """Verify built-in strategies conform to StrategyProtocol runtime check."""
    trend_strat = TrendPullbackStrategy()
    squeeze_strat = SqueezeBreakoutStrategy()
    custom_strat = DummyCustomStrategy()

    assert isinstance(trend_strat, StrategyProtocol)
    assert isinstance(squeeze_strat, StrategyProtocol)
    assert isinstance(custom_strat, StrategyProtocol)

    assert trend_strat.strategy_id == "trend_pullback"
    assert squeeze_strat.strategy_id == "squeeze_breakout"
    assert custom_strat.strategy_id == "custom_alpha"


def test_strategy_registry_registration_and_lookup():
    """Verify strategy registration, case-insensitivity, and list queries."""
    registry = StrategyRegistry()
    trend = TrendPullbackStrategy()
    custom = DummyCustomStrategy()

    registry.register(trend)
    registry.register(custom)

    assert registry.get("trend_pullback") is trend
    assert registry.get("TREND_PULLBACK") is trend
    assert registry.get("custom_alpha") is custom
    assert registry.get("non_existent") is None
    assert registry.list_strategies() == ["custom_alpha", "trend_pullback"]


def test_registry_active_strategies_single_mode(base_config: AppConfig):
    """Verify single strategy mode resolution."""
    registry = StrategyRegistry()
    trend = TrendPullbackStrategy(base_config)
    squeeze = SqueezeBreakoutStrategy(base_config)
    registry.register(trend)
    registry.register(squeeze)

    # Configure single mode selecting squeeze_breakout
    cfg = base_config.model_copy(deep=True)
    cfg.strategies.mode = StrategyMode.SINGLE
    cfg.strategies.active_strategy = "squeeze_breakout"

    active = registry.get_active_strategies(cfg)
    assert len(active) == 1
    assert active[0].strategy_id == "squeeze_breakout"


def test_registry_active_strategies_parallel_mode(base_config: AppConfig):
    """Verify parallel mode resolution returning multiple enabled strategies."""
    registry = StrategyRegistry()
    trend = TrendPullbackStrategy(base_config)
    squeeze = SqueezeBreakoutStrategy(base_config)
    custom = DummyCustomStrategy()
    registry.register(trend)
    registry.register(squeeze)
    registry.register(custom)

    cfg = base_config.model_copy(deep=True)
    cfg.strategies.mode = StrategyMode.PARALLEL
    cfg.strategies.active_strategies = ["trend_pullback", "squeeze_breakout"]

    active = registry.get_active_strategies(cfg)
    active_ids = [s.strategy_id for s in active]
    assert "trend_pullback" in active_ids
    assert "squeeze_breakout" in active_ids
    assert "custom_alpha" not in active_ids


def test_registry_active_strategies_runtime_overrides(base_config: AppConfig):
    """Verify runtime override flags take precedence over configuration."""
    registry = StrategyRegistry()
    trend = TrendPullbackStrategy(base_config)
    squeeze = SqueezeBreakoutStrategy(base_config)
    registry.register(trend)
    registry.register(squeeze)

    # Config says parallel, but runtime override specifies single strategy trend_pullback
    active = registry.get_active_strategies(base_config, override_strategy="trend_pullback")
    assert len(active) == 1
    assert active[0].strategy_id == "trend_pullback"


def test_conflict_resolver_netting_cancels_opposing_signals():
    """Verify that opposing LONG and SHORT signals on the same symbol cancel out under netting."""
    cand_long = ScreenerCandidate(
        contract="SPY",
        symbol="SPY",
        asset_class=AssetClass.EQUITY,
        timeframe="4h",
        strategy=StrategyType.TREND_PULLBACK,
        direction=Direction.LONG,
        current_price=560.0,
        ema_20=555.0,
        ema_50=550.0,
        ema_200=530.0,
        rsi_14=42.0,
        atr_14=5.0,
        candle_timestamp="2026-09-14T12:00:00Z",
        recent_swing_low=550.0,
        recent_swing_high=570.0,
        trigger_detail="Trend pullback long",
    )
    cand_short = ScreenerCandidate(
        contract="SPY",
        symbol="SPY",
        asset_class=AssetClass.EQUITY,
        timeframe="1h",
        strategy=StrategyType.SQUEEZE_BREAKOUT,
        direction=Direction.SHORT,
        current_price=560.0,
        ema_20=555.0,
        ema_50=550.0,
        ema_200=530.0,
        rsi_14=58.0,
        atr_14=5.0,
        candle_timestamp="2026-09-14T12:00:00Z",
        recent_swing_low=550.0,
        recent_swing_high=570.0,
        trigger_detail="Squeeze breakdown short",
    )
    unconflicted_qqq = ScreenerCandidate(
        contract="QQQ",
        symbol="QQQ",
        asset_class=AssetClass.EQUITY,
        timeframe="4h",
        strategy=StrategyType.TREND_PULLBACK,
        direction=Direction.LONG,
        current_price=480.0,
        ema_20=475.0,
        ema_50=470.0,
        ema_200=450.0,
        rsi_14=41.0,
        atr_14=6.0,
        candle_timestamp="2026-09-14T12:00:00Z",
        recent_swing_low=470.0,
        recent_swing_high=490.0,
        trigger_detail="QQQ trend pullback long",
    )

    # Under netting, SPY LONG and SPY SHORT cancel each other out, QQQ is preserved
    resolved = ConflictResolver.resolve(
        [cand_long, cand_short, unconflicted_qqq],
        mode=ConflictResolutionMode.NETTING,
    )
    assert len(resolved) == 1
    assert resolved[0].symbol == "QQQ"


def test_conflict_resolver_highest_conviction_mode():
    """Verify that highest_conviction picks the signal with greater price range."""
    cand_narrow = ScreenerCandidate(
        contract="SPY",
        symbol="SPY",
        asset_class=AssetClass.EQUITY,
        timeframe="1h",
        strategy=StrategyType.SQUEEZE_BREAKOUT,
        direction=Direction.SHORT,
        current_price=560.0,
        ema_20=555.0,
        ema_50=550.0,
        ema_200=530.0,
        rsi_14=58.0,
        atr_14=5.0,
        candle_timestamp="2026-09-14T12:00:00Z",
        recent_swing_low=558.0,
        recent_swing_high=562.0,  # Range = 4.0
        trigger_detail="Narrow range squeeze",
    )
    cand_wide = ScreenerCandidate(
        contract="SPY",
        symbol="SPY",
        asset_class=AssetClass.EQUITY,
        timeframe="4h",
        strategy=StrategyType.TREND_PULLBACK,
        direction=Direction.LONG,
        current_price=560.0,
        ema_20=555.0,
        ema_50=550.0,
        ema_200=530.0,
        rsi_14=42.0,
        atr_14=5.0,
        candle_timestamp="2026-09-14T12:00:00Z",
        recent_swing_low=540.0,
        recent_swing_high=570.0,  # Range = 30.0
        trigger_detail="Wide range pullback",
    )

    resolved = ConflictResolver.resolve(
        [cand_narrow, cand_wide],
        mode=ConflictResolutionMode.HIGHEST_CONVICTION,
    )
    assert len(resolved) == 1
    assert resolved[0].direction == Direction.LONG
    assert resolved[0].strategy == StrategyType.TREND_PULLBACK


def test_conflict_resolver_deduplication():
    """Verify that duplicate identical signals from the same strategy are deduplicated."""
    c1 = ScreenerCandidate(
        contract="GLD",
        symbol="GLD",
        asset_class=AssetClass.EQUITY,
        timeframe="4h",
        strategy=StrategyType.TREND_PULLBACK,
        direction=Direction.LONG,
        current_price=230.0,
        ema_20=228.0,
        ema_50=225.0,
        ema_200=210.0,
        rsi_14=42.0,
        atr_14=2.0,
        candle_timestamp="2026-09-14T12:00:00Z",
        recent_swing_low=225.0,
        recent_swing_high=235.0,
        trigger_detail="Trigger 1",
    )
    c2 = c1.model_copy()

    resolved = ConflictResolver.resolve([c1, c2])
    assert len(resolved) == 1
    assert resolved[0].symbol == "GLD"


def test_strategy_engine_multi_strategy_scan(base_config: AppConfig):
    """Verify StrategyEngine executes active strategies and applies conflict resolution."""
    engine = StrategyEngine(base_config)
    # Market data with trend pullback setup
    data = create_mock_market_data(
        daily_close=5800.0,
        daily_ema50=5700.0,
        daily_ema200=5500.0,
        four_h_close=5800.0,
        four_h_ema20=5800.0,
        four_h_rsi_seq=[43.0, 42.0, 41.0, 44.0],
        four_h_atr=20.0,
    )

    # In parallel mode, scan should evaluate and return trend pullback candidate
    candidates = engine.scan_contract(data, asset_class=AssetClass.FUTURES)
    assert len(candidates) >= 1
    assert candidates[0].strategy == StrategyType.TREND_PULLBACK
    assert candidates[0].direction == Direction.LONG

    # In single mode selecting squeeze_breakout (which has no trigger on this data), should return empty
    single_candidates = engine.scan_contract(
        data,
        asset_class=AssetClass.FUTURES,
        override_strategy="squeeze_breakout",
    )
    assert len(single_candidates) == 0
