import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from agentic_trader.agent.calendar import ForexFactoryCalendar, MacroEvent
from agentic_trader.agent.evaluator import RiskEvaluator
from agentic_trader.agent.regime import RegimeDetector, RegimeSnapshot
from agentic_trader.constants import AssetClass, StrategyType, VolatilityRegime
from agentic_trader.market.session import MarketSessionInfo, MarketSessionType
from agentic_trader.screeners.base import ScreenerCandidate


@pytest.fixture
def evaluator_factory(config):
    """Deterministic risk tests explicitly supply calendar and market context."""

    def build(**overrides):
        detector = RegimeDetector(config.regime)
        detector.get_regime = AsyncMock(
            return_value=RegimeSnapshot(
                vix=18.0,
                vix_regime=VolatilityRegime.NORMAL,
                tnx=4.2,
                dxy=103.0,
                breakout_allowed=True,
                min_rr_threshold=2.0,
                timestamp=datetime.now(UTC),
                summary_text="Test context",
            )
        )
        calendar = AsyncMock(spec=ForexFactoryCalendar)
        calendar.is_in_lockout_window.return_value = (False, None)
        return RiskEvaluator(config, **{"regime_detector": detector, "calendar": calendar, **overrides})

    return build


def create_candidate(contract="/MES", direction="LONG", price=5800.0, atr=20.0, swing_low=5770.0, swing_high=5830.0):
    return ScreenerCandidate(
        contract=contract,
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
async def test_deterministic_long_evaluation(evaluator_factory):
    evaluator = evaluator_factory()
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
async def test_deterministic_short_evaluation(evaluator_factory):
    evaluator = evaluator_factory()
    candidate = create_candidate(direction="SHORT", price=5800.0, atr=20.0, swing_high=5840.0)
    eval_res = await evaluator.evaluate_candidate(candidate, current_open_notional=0.0, use_llm=False)

    assert eval_res.approved is True
    assert eval_res.direction == "SHORT"
    assert eval_res.stop_distance_points >= 30.0
    assert eval_res.stop_loss > eval_res.entry_price
    assert eval_res.take_profit < eval_res.entry_price
    assert eval_res.risk_reward_ratio >= 2.0


@pytest.mark.asyncio
async def test_notional_limit_rejection(evaluator_factory):
    evaluator = evaluator_factory()
    candidate = create_candidate(price=5800.0)  # /MES notional = 5800 * 5 = $29,000
    # If existing notional is $35,000, adding $29,000 = $64,000 > $60,000 limit
    eval_res = await evaluator.evaluate_candidate(candidate, current_open_notional=35000.0, use_llm=False)

    assert eval_res.approved is False
    assert "Exposure limit exceeded" in eval_res.rejection_reason


@pytest.mark.asyncio
async def test_macro_lockout_rejection(evaluator_factory):
    class MockCalendar(ForexFactoryCalendar):
        async def is_in_lockout_window(self, pre_minutes=60, post_minutes=30, now=None):
            return True, MacroEvent(
                title="FOMC Rate Decision",
                country="USD",
                impact="High",
                timestamp=datetime.now(UTC),
            )

    evaluator = evaluator_factory(calendar=MockCalendar())
    candidate = create_candidate(price=5800.0)
    eval_res = await evaluator.evaluate_candidate(candidate, current_open_notional=0.0, use_llm=False)

    assert eval_res.approved is False
    assert "Macro Event Lockout" in eval_res.rejection_reason
    assert eval_res.macro_clearance is False


@pytest.mark.asyncio
async def test_equity_dynamic_sizing(evaluator_factory):
    evaluator = evaluator_factory()
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


@pytest.mark.asyncio
async def test_squeeze_breakout_suppression_in_extreme_regime(evaluator_factory):
    # Setup mock regime detector returning EXTREME regime (VIX = 35.0, breakout_allowed = False)
    mock_regime = RegimeDetector()
    extreme_snapshot = RegimeSnapshot(
        vix=35.0,
        vix_regime=VolatilityRegime.EXTREME,
        tnx=4.8,
        dxy=105.0,
        breakout_allowed=False,
        min_rr_threshold=2.5,
        timestamp=datetime.now(UTC),
        summary_text="VIX: 35.00 (EXTREME) | Breakouts: Suppressed",
    )
    mock_regime.get_regime = lambda force_refresh=False: asyncio.sleep(0, result=extreme_snapshot)  # type: ignore

    evaluator = evaluator_factory(regime_detector=mock_regime)

    candidate = ScreenerCandidate(
        contract="/MES",
        timeframe="4h",
        strategy=StrategyType.SQUEEZE_BREAKOUT,
        direction="LONG",
        current_price=5800.0,
        ema_20=5780.0,
        ema_50=5750.0,
        ema_200=5700.0,
        rsi_14=55.0,
        atr_14=20.0,
        candle_timestamp="2026-09-12T16:00:00Z",
        recent_swing_low=5770.0,
        recent_swing_high=5830.0,
        trigger_detail="Squeeze fired long",
    )

    eval_res = await evaluator.evaluate_candidate(candidate, current_open_notional=0.0, use_llm=False)

    assert eval_res.approved is False
    assert "Volatility Regime Filter" in (eval_res.rejection_reason or "")
    assert "EXTREME" in (eval_res.rejection_reason or "")


@pytest.mark.asyncio
async def test_squeeze_breakout_allowed_in_normal_regime(evaluator_factory):
    mock_regime = RegimeDetector()
    normal_snapshot = RegimeSnapshot(
        vix=17.5,
        vix_regime=VolatilityRegime.NORMAL,
        tnx=4.2,
        dxy=102.0,
        breakout_allowed=True,
        min_rr_threshold=2.0,
        timestamp=datetime.now(UTC),
        summary_text="VIX: 17.50 (NORMAL) | Breakouts: Allowed",
    )
    mock_regime.get_regime = lambda force_refresh=False: asyncio.sleep(0, result=normal_snapshot)  # type: ignore

    evaluator = evaluator_factory(regime_detector=mock_regime)

    candidate = ScreenerCandidate(
        contract="/MES",
        timeframe="4h",
        strategy=StrategyType.SQUEEZE_BREAKOUT,
        direction="LONG",
        current_price=5800.0,
        ema_20=5780.0,
        ema_50=5750.0,
        ema_200=5700.0,
        rsi_14=55.0,
        atr_14=20.0,
        candle_timestamp="2026-09-12T16:00:00Z",
        recent_swing_low=5770.0,
        recent_swing_high=5830.0,
        trigger_detail="Squeeze fired long",
    )

    eval_res = await evaluator.evaluate_candidate(candidate, current_open_notional=0.0, use_llm=False)

    assert eval_res.approved is True


@pytest.mark.asyncio
async def test_evaluator_correlation_group_limit(evaluator_factory):
    """Test that a new candidate is rejected if its correlation group is at capacity."""
    evaluator = evaluator_factory()
    candidate = create_candidate(direction="LONG")
    # Active position already in US Equities group (e.g. SPY LONG)
    active_positions = [{"contract": "SPY", "direction": "LONG", "notional_value": 25000.0}]

    eval_res = await evaluator.evaluate_candidate(
        candidate,
        current_open_notional=25000.0,
        active_positions=active_positions,
        use_llm=False,
    )

    assert eval_res.approved is False
    assert "Correlation limit exceeded" in (eval_res.rejection_reason or "")
    assert "us_broad_market" in (eval_res.rejection_reason or "")


@pytest.mark.asyncio
async def test_evaluator_market_session_rejection(evaluator_factory):
    mock_session_provider = AsyncMock()
    mock_session_provider.get_session_info = AsyncMock(
        return_value=MarketSessionInfo(
            symbol="/MES",
            asset_class=AssetClass.FUTURES,
            is_open=False,
            is_rth=False,
            session_type=MarketSessionType.HOLIDAY_HALT,
            current_time=datetime.now(UTC),
            details="Christmas Day Holiday Closure",
        )
    )

    evaluator = evaluator_factory(session_provider=mock_session_provider)
    candidate = create_candidate(direction="LONG")

    eval_res = await evaluator.evaluate_candidate(candidate, current_open_notional=0.0, use_llm=False)

    assert eval_res.approved is False
    assert "Market Session Filter" in (eval_res.rejection_reason or "")


@pytest.mark.asyncio
async def test_macro_stress_risk_budget_scaling(evaluator_factory):
    # Setup mock regime with HIGH macro stress (risk multiplier 0.50)
    mock_regime_high = RegimeDetector()
    high_snapshot = RegimeSnapshot(
        vix=25.0,
        vix_regime=VolatilityRegime.ELEVATED,
        tnx=4.8,
        dxy=105.0,
        breakout_allowed=True,
        min_rr_threshold=2.2,
        timestamp=datetime.now(UTC),
        summary_text="VIX: 25.00 | Macro Stress: HIGH (0.50x)",
        risk_multiplier=0.50,
    )
    mock_regime_high.get_regime = lambda force_refresh=False: asyncio.sleep(0, result=high_snapshot)  # type: ignore

    evaluator_high = evaluator_factory(regime_detector=mock_regime_high)

    # Setup normal regime (risk multiplier 1.0)
    mock_regime_norm = RegimeDetector()
    norm_snapshot = RegimeSnapshot(
        vix=17.0,
        vix_regime=VolatilityRegime.NORMAL,
        tnx=4.2,
        dxy=102.0,
        breakout_allowed=True,
        min_rr_threshold=2.0,
        timestamp=datetime.now(UTC),
        summary_text="VIX: 17.00 | Macro Stress: LOW (1.00x)",
        risk_multiplier=1.00,
    )
    mock_regime_norm.get_regime = lambda force_refresh=False: asyncio.sleep(0, result=norm_snapshot)  # type: ignore

    evaluator_norm = evaluator_factory(regime_detector=mock_regime_norm)

    candidate = create_candidate(
        contract="SPY",
        direction="LONG",
        price=500.0,
        atr=5.0,
        swing_low=492.0,
        swing_high=508.0,
    )
    candidate.asset_class = AssetClass.EQUITY

    eval_norm = await evaluator_norm.evaluate_candidate(candidate, current_open_notional=0.0, use_llm=False)
    eval_high = await evaluator_high.evaluate_candidate(candidate, current_open_notional=0.0, use_llm=False)

    assert eval_norm.approved is True
    assert eval_high.approved is True
    assert eval_high.quantity < eval_norm.quantity
    assert eval_high.risk_dollars < eval_norm.risk_dollars
