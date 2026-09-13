from datetime import UTC, datetime

import pytest

from agentic_trader.agent.calendar import EconomicCalendar, MacroEvent
from agentic_trader.agent.evaluator import RiskEvaluator
from agentic_trader.config import load_config
from agentic_trader.constants import AssetClass
from agentic_trader.screeners.strategies import ScreenerCandidate


@pytest.fixture
def config():
    return load_config()


def create_candidate(direction="LONG", price=5800.0, atr=20.0, swing_low=5770.0, swing_high=5830.0):
    return ScreenerCandidate(
        contract="/MES",
        timeframe="4h",
        strategy="TREND_PULLBACK",
        direction=direction,
        current_price=price,
        ema_20=price,
        ema_50=price - 30,
        ema_200=price - 60,
        rsi_14=42.0,
        atr_14=atr,
        candle_timestamp="2026-09-12T16:00:00Z",
        recent_swing_low=swing_low,
        recent_swing_high=swing_high,
        trigger_detail="Test trigger",
    )


@pytest.mark.asyncio
async def test_deterministic_long_evaluation(config):
    evaluator = RiskEvaluator(config)
    candidate = create_candidate(direction="LONG", price=5800.0, atr=20.0, swing_low=5760.0)
    eval_res = await evaluator.evaluate_candidate(candidate, current_open_notional=0.0, use_llm=False)

    assert eval_res.approved is True
    assert eval_res.contract == "/MES"
    assert eval_res.direction == "LONG"
    # Stop distance must be >= 1.5 * 20 = 30 points
    assert eval_res.stop_distance_points >= 30.0
    # Stop loss must be below entry
    assert eval_res.stop_loss < eval_res.entry_price
    # Target must be above entry
    assert eval_res.take_profit > eval_res.entry_price
    # R:R must be >= 2.0
    assert eval_res.risk_reward_ratio >= 2.0
    # Dollar risk and reward for /MES ($5/pt)
    assert abs(eval_res.risk_dollars - (eval_res.stop_distance_points * 5.0)) < 0.01
    assert abs(eval_res.reward_dollars - (eval_res.target_distance_points * 5.0)) < 0.01


@pytest.mark.asyncio
async def test_deterministic_short_evaluation(config):
    evaluator = RiskEvaluator(config)
    candidate = create_candidate(direction="SHORT", price=5800.0, atr=20.0, swing_high=5840.0)
    eval_res = await evaluator.evaluate_candidate(candidate, current_open_notional=0.0, use_llm=False)

    assert eval_res.approved is True
    assert eval_res.direction == "SHORT"
    assert eval_res.stop_distance_points >= 30.0
    assert eval_res.stop_loss > eval_res.entry_price
    assert eval_res.take_profit < eval_res.entry_price
    assert eval_res.risk_reward_ratio >= 2.0


@pytest.mark.asyncio
async def test_notional_limit_rejection(config):
    evaluator = RiskEvaluator(config)
    candidate = create_candidate(price=5800.0)  # /MES notional = 5800 * 5 = $29,000
    # If existing notional is $35,000, adding $29,000 = $64,000 > $60,000 limit
    eval_res = await evaluator.evaluate_candidate(candidate, current_open_notional=35000.0, use_llm=False)

    assert eval_res.approved is False
    assert "Exposure limit exceeded" in eval_res.rejection_reason


@pytest.mark.asyncio
async def test_macro_lockout_rejection(config):
    class MockCalendar(EconomicCalendar):
        async def is_in_lockout_window(self, pre_minutes=60, post_minutes=30, now=None):
            return True, MacroEvent(
                title="FOMC Rate Decision",
                country="USD",
                impact="High",
                timestamp=datetime.now(UTC),
            )

    evaluator = RiskEvaluator(config, calendar=MockCalendar())
    candidate = create_candidate(price=5800.0)
    eval_res = await evaluator.evaluate_candidate(candidate, current_open_notional=0.0, use_llm=False)

    assert eval_res.approved is False
    assert "Macro Event Lockout" in eval_res.rejection_reason
    assert eval_res.macro_clearance is False


@pytest.mark.asyncio
async def test_equity_dynamic_sizing(config):
    evaluator = RiskEvaluator(config)
    # Equity AAPL at 150.00 with ATR=2.0 and swing low=146.00
    candidate = ScreenerCandidate(
        contract="AAPL",
        symbol="AAPL",
        asset_class=AssetClass.EQUITY,
        timeframe="4h",
        strategy="TREND_PULLBACK",
        direction="LONG",
        current_price=150.0,
        ema_20=150.0,
        ema_50=145.0,
        ema_200=140.0,
        rsi_14=42.0,
        atr_14=2.0,
        candle_timestamp="2026-09-12T16:00:00Z",
        recent_swing_low=146.0,
        recent_swing_high=155.0,
        trigger_detail="Equity AAPL test pullback",
    )

    eval_res = await evaluator.evaluate_candidate(candidate, current_open_notional=0.0, use_llm=False)

    assert eval_res.approved is True
    assert eval_res.contract == "AAPL"
    assert eval_res.asset_class == AssetClass.EQUITY
    # Stop distance is 150 - (146 - 0.02) = 4.02 (or >= 1.5 * 2 = 3.0)
    assert eval_res.stop_distance_points == pytest.approx(4.02, abs=0.05)
    # Target distance must be >= 2x stop distance
    assert eval_res.target_distance_points >= eval_res.stop_distance_points * 2.0
    # Dynamic share sizing: target risk $250 / $4.02 = 62 shares
    assert eval_res.quantity == 62.0
    # Total risk dollars: 62 * 4.02 ~= $249.24 (<= $250)
    assert eval_res.risk_dollars <= 250.0
    assert eval_res.risk_dollars > 200.0
    # Notional value: 62 * $150 = $9,300
    assert eval_res.notional_value == pytest.approx(9300.0, abs=50.0)
