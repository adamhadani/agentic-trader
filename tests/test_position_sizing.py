from datetime import UTC, datetime

from agentic_trader.agent.evaluator import RiskEvaluator
from agentic_trader.agent.position_sizing import (
    calculate_position_size,
    compute_fractional_kelly_multiplier,
)
from agentic_trader.config import AppConfig, ContractConfig, PositionSizingConfig
from agentic_trader.constants import AssetClass, Direction, StrategyType
from agentic_trader.screeners.strategies import ScreenerCandidate


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


def test_static_sizing_backward_compatibility():
    config = AppConfig(
        sizing=PositionSizingConfig(mode="static"),
        contracts={
            "/MES": ContractConfig(
                ticker="MES=F", name="Micro ES", multiplier=5.0, tick_size=0.25, asset_class=AssetClass.FUTURES
            ),
            "AAPL": ContractConfig(
                ticker="AAPL",
                name="Apple",
                multiplier=1.0,
                tick_size=0.01,
                asset_class=AssetClass.EQUITY,
                target_risk_dollars=250.0,
            ),
        },
    )

    # Futures static sizing: always 1.0 contract
    cand_mes = _make_candidate(contract="/MES", price=5000.0, atr_14=20.0)
    qty_mes, risk_mes, reward_mes = calculate_position_size(
        candidate=cand_mes,
        stop_distance=30.0,
        target_distance=60.0,
        multiplier=5.0,
        asset_class=AssetClass.FUTURES,
        config=config,
    )
    assert qty_mes == 1.0
    assert risk_mes == 150.0  # 30 pts * $5 * 1
    assert reward_mes == 300.0

    # Equity static sizing: target $250 / stop distance $4.00 = 62 shares
    cand_aapl = _make_candidate(contract="AAPL", price=200.0, atr_14=3.0)
    qty_aapl, risk_aapl, reward_aapl = calculate_position_size(
        candidate=cand_aapl,
        stop_distance=4.0,
        target_distance=8.0,
        multiplier=1.0,
        asset_class=AssetClass.EQUITY,
        config=config,
    )
    assert qty_aapl == 62.0
    assert risk_aapl == 248.0
    assert reward_aapl == 496.0


def test_volatility_targeted_futures_sizing():
    config = AppConfig(
        sizing=PositionSizingConfig(
            mode="volatility_targeted",
            target_futures_risk_dollars=300.0,
            max_contracts_per_trade=4,
            min_contracts=1,
        ),
        contracts={
            "/MES": ContractConfig(
                ticker="MES=F", name="Micro ES", multiplier=5.0, tick_size=0.25, asset_class=AssetClass.FUTURES
            )
        },
    )

    # Case A: Low/Compressed Volatility (Tight stop: 15.0 pts -> $75/contract risk)
    # Budget $300 / $75 = 4.0 contracts (capped at 4)
    cand_low = _make_candidate(contract="/MES", price=5000.0, atr_14=10.0)
    qty_low, risk_low, _ = calculate_position_size(
        candidate=cand_low,
        stop_distance=15.0,
        target_distance=30.0,
        multiplier=5.0,
        asset_class=AssetClass.FUTURES,
        config=config,
    )
    assert qty_low == 4.0
    assert risk_low == 300.0

    # Case B: High Volatility (Wide stop: 60.0 pts -> $300/contract risk)
    # Budget $300 / $300 = 1.0 contract
    cand_high = _make_candidate(contract="/MES", price=5000.0, atr_14=40.0)
    qty_high, risk_high, _ = calculate_position_size(
        candidate=cand_high,
        stop_distance=60.0,
        target_distance=120.0,
        multiplier=5.0,
        asset_class=AssetClass.FUTURES,
        config=config,
    )
    assert qty_high == 1.0
    assert risk_high == 300.0

    # Case C: Extreme Volatility (Extreme stop: 100.0 pts -> $500/contract risk)
    # Budget $300 / $500 = 0.6 contract -> clamped to min 1.0
    cand_ext = _make_candidate(contract="/MES", price=5000.0, atr_14=70.0)
    qty_ext, risk_ext, _ = calculate_position_size(
        candidate=cand_ext,
        stop_distance=100.0,
        target_distance=200.0,
        multiplier=5.0,
        asset_class=AssetClass.FUTURES,
        config=config,
    )
    assert qty_ext == 1.0
    assert risk_ext == 500.0


def test_volatility_targeted_equity_sizing():
    config = AppConfig(
        sizing=PositionSizingConfig(
            mode="volatility_targeted",
            default_equity_risk_dollars=500.0,
            max_shares_per_trade=300,
            min_shares=1.0,
        ),
        contracts={
            "SPY": ContractConfig(
                ticker="SPY",
                name="SPY ETF",
                multiplier=1.0,
                tick_size=0.01,
                asset_class=AssetClass.EQUITY,
                target_risk_dollars=500.0,
            )
        },
    )

    # Tight stop: $2.00 per share -> 500 / 2 = 250 shares
    cand_spy = _make_candidate(contract="SPY", price=500.0, atr_14=1.5)
    qty_spy, risk_spy, reward_spy = calculate_position_size(
        candidate=cand_spy,
        stop_distance=2.0,
        target_distance=4.0,
        multiplier=1.0,
        asset_class=AssetClass.EQUITY,
        config=config,
    )
    assert qty_spy == 250.0
    assert risk_spy == 500.0
    assert reward_spy == 1000.0


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

    (
        stop_loss,
        take_profit,
        stop_dist,
        target_dist,
        risk_dollars,
        reward_dollars,
        notional,
        quantity,
    ) = evaluator.calculate_levels_deterministic(cand)

    assert stop_loss < 18000.0
    assert take_profit > 18000.0
    assert target_dist >= stop_dist * 2.0
    assert 1.0 <= quantity <= 4.0
    assert risk_dollars > 0
    assert reward_dollars > 0
    assert notional == round(cand.current_price * 2.0 * quantity, 2)
