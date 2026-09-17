from datetime import UTC, datetime

import pytest

from agentic_trader.agent.position_sizing import (
    calculate_dynamic_sizing,
)
from agentic_trader.config import AppConfig, ContractConfig, PositionSizingConfig
from agentic_trader.constants import AssetClass, Direction, StrategyType
from agentic_trader.screeners.base import ScreenerCandidate


def _make_candidate(
    contract: str = "/MES",
    price: float = 5000.0,
    atr_14: float = 20.0,
    direction: Direction = Direction.LONG,
    recent_swing_low: float = 4970.0,
    recent_swing_high: float = 5030.0,
) -> ScreenerCandidate:
    return ScreenerCandidate(
        contract=contract,
        timeframe="4h",
        strategy=StrategyType.TREND_PULLBACK,
        direction=direction,
        current_price=price,
        ema_20=price - 5.0,
        ema_50=price - 10.0,
        ema_200=price - 50.0,
        rsi_14=42.0,
        atr_14=atr_14,
        candle_timestamp="2026-09-12T16:00:00Z",
        recent_swing_low=recent_swing_low,
        recent_swing_high=recent_swing_high,
        trigger_detail="Test pullback setup",
        timestamp=datetime.now(UTC),
    )


@pytest.mark.parametrize("mode", ["fractional_kelly", "unknown", "STATIC"])
def test_unvalidated_sizing_modes_are_rejected(mode):
    with pytest.raises(ValueError):
        PositionSizingConfig(mode=mode)


@pytest.mark.parametrize(
    "mode,symbol,price,stop,multiplier,asset,risk_budget,expected_qty,expected_risk",
    [
        ("static", "/MES", 5000, 30, 5, AssetClass.FUTURES, 300, 1, 150),
        ("static", "AAPL", 200, 4, 1, AssetClass.EQUITY, 250, 62, 248),
        ("volatility_targeted", "/MES", 5000, 15, 5, AssetClass.FUTURES, 300, 1, 75),
        ("volatility_targeted", "/MES", 5000, 60, 5, AssetClass.FUTURES, 300, 1, 300),
        ("volatility_targeted", "/MES", 5000, 100, 5, AssetClass.FUTURES, 300, 1, 500),
        ("volatility_targeted", "SPY", 500, 2, 1, AssetClass.EQUITY, 500, 60, 120),
    ],
)
def test_sizing_uses_actual_price_and_notional_cap(
    mode, symbol, price, stop, multiplier, asset, risk_budget, expected_qty, expected_risk
):
    config = AppConfig(
        sizing=PositionSizingConfig(mode=mode, target_futures_risk_dollars=risk_budget, max_contracts_per_trade=4),
        contracts={
            symbol: ContractConfig(
                ticker=symbol,
                name=symbol,
                multiplier=multiplier,
                tick_size=0.01,
                asset_class=asset,
                target_risk_dollars=risk_budget,
            )
        },
    )
    candidate = _make_candidate(contract=symbol, price=price)
    result = calculate_dynamic_sizing(
        entry=candidate.current_price,
        candidate=candidate,
        stop_distance=stop,
        target_distance=2 * stop,
        multiplier=multiplier,
        asset_class=asset,
        config=config,
    )
    tier = result.default_tier
    assert tier.quantity == expected_qty
    assert tier.risk_dollars == expected_risk
    assert tier.reward_dollars == 2 * expected_risk
    assert tier.notional_dollars <= config.sizing.max_trade_notional_cap


def test_dynamic_sizing_drawdown_gating_and_tiers():
    config = AppConfig(
        sizing=PositionSizingConfig(
            mode="static",
            target_futures_risk_dollars=300.0,
            max_contracts_per_trade=4,
            drawdown_gating_enabled=True,
            drawdown_haircut_threshold_pct=0.03,
            max_drawdown_stop_pct=0.06,
        )
    )

    # 1. Normal drawdown: factor should be 1.0
    res_normal = calculate_dynamic_sizing(
        entry=5000.0,
        stop_distance=20.0,
        target_distance=40.0,
        multiplier=5.0,
        asset_class=AssetClass.FUTURES,
        config=config,
        current_drawdown_pct=0.01,
    )
    assert res_normal.drawdown_factor == 1.0
    assert len(res_normal.tiers) >= 1
    assert res_normal.default_tier.quantity >= 1.0

    # 2. Moderate drawdown (4.5%): midway between 3% and 6% -> factor approx 0.50
    res_haircut = calculate_dynamic_sizing(
        entry=5000.0,
        stop_distance=20.0,
        target_distance=40.0,
        multiplier=5.0,
        asset_class=AssetClass.FUTURES,
        config=config,
        current_drawdown_pct=0.045,
    )
    assert 0.40 <= res_haircut.drawdown_factor <= 0.60
    assert any("Drawdown haircut applied" in g for g in res_haircut.gating_reasons)

    # 3. Severe drawdown (6.5% >= 6.0%): drawdown factor 0.0 (halt)
    res_halt = calculate_dynamic_sizing(
        entry=5000.0,
        stop_distance=20.0,
        target_distance=40.0,
        multiplier=5.0,
        asset_class=AssetClass.FUTURES,
        config=config,
        current_drawdown_pct=0.065,
    )
    assert res_halt.drawdown_factor == 0.0
    assert any("Drawdown halt active" in g for g in res_halt.gating_reasons)


def test_dynamic_sizing_notional_cap_and_equity_tiers():
    config = AppConfig(
        sizing=PositionSizingConfig(
            mode="static",
            default_equity_risk_dollars=250.0,
            max_shares_per_trade=500,
            max_trade_notional_cap=15000.0,  # $15k cap
        )
    )

    # SPY at $500/share -> max shares by notional = 15000 / 500 = 30 shares
    res = calculate_dynamic_sizing(
        entry=500.0,
        stop_distance=5.0,
        target_distance=10.0,
        multiplier=1.0,
        asset_class=AssetClass.EQUITY,
        config=config,
    )
    assert res.max_tier.quantity <= 30.0
    assert res.max_tier.notional_dollars <= 15000.0
    # Verify tiers generated (e.g. Conservative / Base / Max)
    assert len(res.tiers) >= 1
    assert all(t.risk_dollars <= 1000.0 for t in res.tiers)  # 1% risk cap on $100k


@pytest.mark.parametrize("mode", ["static", "volatility_targeted"])
@pytest.mark.parametrize("asset,multiplier", [(AssetClass.EQUITY, 1), (AssetClass.FUTURES, 5)])
@pytest.mark.parametrize("constraint", ["exhausted", "drawdown", "unit_too_large", "minimum_above_cap"])
def test_minimum_size_never_overrides_a_hard_gate(mode, asset, multiplier, constraint):
    config = AppConfig(sizing=PositionSizingConfig(mode=mode))
    opened = config.portfolio.max_notional_exposure if constraint == "exhausted" else 0
    drawdown = 0.07 if constraint == "drawdown" else 0
    entry = 100000 if constraint == "unit_too_large" else 100
    if constraint == "minimum_above_cap":
        config.sizing.min_shares = 1000
        config.sizing.min_contracts = 1000
        config.sizing.max_trade_notional_cap = entry * multiplier * 2
    result = calculate_dynamic_sizing(
        entry, 10, 20, multiplier, asset, config, current_open_notional=opened, current_drawdown_pct=drawdown
    )
    for tier in result.tiers:
        assert tier.quantity == 0
        assert tier.risk_dollars == tier.notional_dollars == 0
