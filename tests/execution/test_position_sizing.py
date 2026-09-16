from datetime import UTC, datetime

import pytest

from agentic_trader.agent.evaluator import RiskEvaluator
from agentic_trader.agent.position_sizing import (
    calculate_dynamic_sizing,
    compute_fractional_kelly_multiplier,
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


def test_fractional_kelly_multiplier_bounds():
    # Baseline: win_rate=0.50, R:R=2.0 -> multiplier should be 1.0
    mult_baseline = compute_fractional_kelly_multiplier(win_rate=0.50, payoff_ratio=2.0, fraction=0.5)
    assert mult_baseline == 1.0

    # High edge: win_rate=0.65, R:R=3.0 -> multiplier should scale up toward 2.0
    mult_high = compute_fractional_kelly_multiplier(win_rate=0.65, payoff_ratio=3.0, fraction=0.5)
    assert mult_high > 1.0
    assert mult_high <= 2.0

    # Negative expectancy: win_rate=0.30, R:R=1.0 -> should be clamped to min_multiplier 0.50
    mult_low = compute_fractional_kelly_multiplier(win_rate=0.30, payoff_ratio=1.0, fraction=0.5)
    assert mult_low == 0.50

    # Zero or negative payoff ratio
    mult_zero = compute_fractional_kelly_multiplier(win_rate=0.50, payoff_ratio=0.0)
    assert mult_zero == 1.0


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


def test_fractional_kelly_evaluator_integration():
    config = AppConfig(
        sizing=PositionSizingConfig(
            mode="fractional_kelly",
            target_futures_risk_dollars=300.0,
            max_contracts_per_trade=4,
            min_contracts=1,
            kelly_fraction=0.5,
            baseline_win_rate=0.50,
        ),
        contracts={
            "/MNQ": ContractConfig(
                ticker="MNQ=F", name="Micro NQ", multiplier=2.0, tick_size=0.25, asset_class=AssetClass.FUTURES
            )
        },
    )

    evaluator = RiskEvaluator(config=config)
    cand = _make_candidate(
        contract="/MNQ",
        price=18000.0,
        atr_14=50.0,
        direction=Direction.LONG,
        recent_swing_low=17925.0,  # 75 pt stop
    )

    levels = evaluator.calculate_levels_deterministic(cand)
    stop_loss = levels.stop_loss
    take_profit = levels.take_profit
    stop_dist = levels.stop_distance
    target_dist = levels.target_distance
    notional = levels.notional_value
    quantity = levels.quantity

    assert stop_loss < 18000.0
    assert take_profit > 18000.0
    assert target_dist >= stop_dist * 2.0
    assert 1.0 <= quantity <= 4.0
    assert notional == round(cand.current_price * 2.0 * quantity, 2)


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
