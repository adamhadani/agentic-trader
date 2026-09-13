from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from agentic_trader.agent.evaluator import RiskEvaluator
from agentic_trader.config import AppConfig, ContractConfig, PortfolioConfig
from agentic_trader.constants import AssetClass, Direction
from agentic_trader.data.market_data import MarketDataFetcher
from agentic_trader.screeners.strategies import ScreenerCandidate


@pytest.fixture
def risk_config():
    cfg = AppConfig(
        execution_mode="paper",
        portfolio=PortfolioConfig(
            cash=100000.0,
            max_notional_exposure=60000.0,
            max_futures_exposure=30000.0,
            max_equity_exposure=30000.0,
            max_crypto_exposure=15000.0,
            max_correlated_positions=1,
            max_correlation_threshold=0.85,
            enable_dynamic_correlation=False,
            correlation_groups={
                "us_broad_market": ["/MES", "/ES", "SPY", "VOO"],
                "us_tech": ["/MNQ", "/NQ", "QQQ"],
                "gold": ["/MGC", "/GC", "GLD"],
            },
        ),
        contracts={
            "/MES": ContractConfig(
                ticker="MES=F",
                name="Micro S&P",
                multiplier=5.0,
                tick_size=0.25,
                asset_class=AssetClass.FUTURES,
            ),
            "SPY": ContractConfig(
                ticker="SPY",
                name="SPDR S&P 500",
                multiplier=1.0,
                tick_size=0.01,
                asset_class=AssetClass.EQUITY,
                target_risk_dollars=250.0,
            ),
            "QQQ": ContractConfig(
                ticker="QQQ",
                name="Invesco QQQ",
                multiplier=1.0,
                tick_size=0.01,
                asset_class=AssetClass.EQUITY,
                target_risk_dollars=250.0,
            ),
            "/MGC": ContractConfig(
                ticker="MGC=F",
                name="Micro Gold",
                multiplier=10.0,
                tick_size=0.10,
                asset_class=AssetClass.FUTURES,
            ),
        },
    )
    return cfg


def create_candidate(
    contract: str,
    direction: Direction = Direction.LONG,
    price: float = 500.0,
    atr: float = 5.0,
    swing_low: float = 490.0,
    swing_high: float = 510.0,
    asset_class: AssetClass = AssetClass.FUTURES,
) -> ScreenerCandidate:
    return ScreenerCandidate(
        contract=contract,
        timeframe="4h",
        strategy="trend_pullback",
        direction=direction,
        current_price=price,
        ema_20=price,
        ema_50=price - (atr * 2),
        ema_200=price - (atr * 4),
        rsi_14=45.0,
        atr_14=atr,
        candle_timestamp="2026-09-12T16:00:00Z",
        recent_swing_low=swing_low,
        recent_swing_high=swing_high,
        trigger_detail="Test trigger",
        ema_trend=True,
        pullback_support=True,
        rsi_oversold=True,
        asset_class=asset_class,
    )


def create_mock_regime():
    mock_regime = MagicMock()
    mock_regime_obj = MagicMock(
        is_extreme_volatility=False,
        breakout_allowed=True,
        vix=15.5,
        vix_regime=MagicMock(value="NORMAL"),
        summary_text="Normal",
    )
    mock_regime.get_regime = AsyncMock(return_value=mock_regime_obj)
    mock_regime.detect_current_regime.return_value = mock_regime_obj
    mock_regime.get_prompt_context.return_value = "Normal Volatility"
    return mock_regime


@pytest.mark.asyncio
async def test_futures_allocation_cap_rejection(risk_config):
    mock_calendar = AsyncMock()
    mock_calendar.is_in_lockout_window.return_value = (False, None)
    mock_regime = create_mock_regime()

    evaluator = RiskEvaluator(
        risk_config,
        calendar=mock_calendar,
        regime_detector=mock_regime,
    )

    # Candidate: /MES at 5000.0 * 5.0 multiplier = $25,000 notional
    cand = create_candidate("/MES", price=5000.0, atr=30.0, swing_low=4950.0, asset_class=AssetClass.FUTURES)

    # Active positions already holding $20,000 in futures (e.g. /MGC)
    active_positions = [
        {
            "contract": "/MGC",
            "symbol": "/MGC",
            "direction": "LONG",
            "asset_class": "FUTURES",
            "notional_value": 20000.0,
        }
    ]

    # $20,000 existing + $25,000 new = $45,000 > $30,000 futures cap!
    res = await evaluator.evaluate_candidate(cand, use_llm=False, active_positions=active_positions)
    assert res.approved is False
    assert "Asset class limit exceeded" in (res.rejection_reason or "")
    assert "FUTURES" in (res.rejection_reason or "")


@pytest.mark.asyncio
async def test_different_asset_class_allowed_under_cap(risk_config):
    mock_calendar = AsyncMock()
    mock_calendar.is_in_lockout_window.return_value = (False, None)
    mock_regime = create_mock_regime()

    evaluator = RiskEvaluator(
        risk_config,
        calendar=mock_calendar,
        regime_detector=mock_regime,
    )

    # Active position has $25,000 in FUTURES
    active_positions = [
        {
            "contract": "/MES",
            "symbol": "/MES",
            "direction": "LONG",
            "asset_class": "FUTURES",
            "notional_value": 25000.0,
        }
    ]

    # Candidate is EQUITY (QQQ): notional is ~15,000 (well within $30k equity cap and $60k total cap)
    cand = create_candidate(
        "QQQ",
        price=450.0,
        atr=5.0,
        swing_low=440.0,
        asset_class=AssetClass.EQUITY,
    )

    res = await evaluator.evaluate_candidate(
        cand,
        current_open_notional=25000.0,
        use_llm=False,
        active_positions=active_positions,
    )
    assert res.approved is True


@pytest.mark.asyncio
async def test_correlation_group_filtering_same_direction(risk_config):
    mock_calendar = AsyncMock()
    mock_calendar.is_in_lockout_window.return_value = (False, None)
    mock_regime = create_mock_regime()

    evaluator = RiskEvaluator(
        risk_config,
        calendar=mock_calendar,
        regime_detector=mock_regime,
    )

    # Portfolio already holds /MES LONG (member of 'us_broad_market')
    active_positions = [
        {
            "contract": "/MES",
            "symbol": "/MES",
            "direction": "LONG",
            "asset_class": "FUTURES",
            "notional_value": 10000.0,
        }
    ]

    # Candidate SPY is also LONG (also member of 'us_broad_market')
    cand = create_candidate("SPY", direction=Direction.LONG, price=500.0, asset_class=AssetClass.EQUITY)

    res = await evaluator.evaluate_candidate(cand, use_llm=False, active_positions=active_positions)
    assert res.approved is False
    assert "Correlation limit exceeded" in (res.rejection_reason or "")
    assert "us_broad_market" in (res.rejection_reason or "")


@pytest.mark.asyncio
async def test_correlation_group_opposite_direction_allowed(risk_config):
    mock_calendar = AsyncMock()
    mock_calendar.is_in_lockout_window.return_value = (False, None)
    mock_regime = create_mock_regime()

    evaluator = RiskEvaluator(
        risk_config,
        calendar=mock_calendar,
        regime_detector=mock_regime,
    )

    # Portfolio holds /MES LONG
    active_positions = [
        {
            "contract": "/MES",
            "symbol": "/MES",
            "direction": "LONG",
            "asset_class": "FUTURES",
            "notional_value": 10000.0,
        }
    ]

    # Candidate SPY is SHORT (opposite direction / hedge)
    cand = create_candidate("SPY", direction=Direction.SHORT, price=500.0, asset_class=AssetClass.EQUITY)

    res = await evaluator.evaluate_candidate(cand, use_llm=False, active_positions=active_positions)
    # Opposite direction is not rejected by correlation group filter
    assert res.approved is True


@pytest.mark.asyncio
async def test_dynamic_statistical_correlation(risk_config):
    mock_calendar = AsyncMock()
    mock_calendar.is_in_lockout_window.return_value = (False, None)
    mock_regime = create_mock_regime()

    # Enable dynamic correlation in config and provide ample futures cap
    risk_config.portfolio.enable_dynamic_correlation = True
    risk_config.portfolio.max_correlation_threshold = 0.85
    risk_config.portfolio.max_futures_exposure = 60000.0

    mock_fetcher = MagicMock()
    # Mock high correlation 0.94
    mock_fetcher.calculate_correlation.return_value = 0.94

    evaluator = RiskEvaluator(
        risk_config,
        calendar=mock_calendar,
        regime_detector=mock_regime,
        data_fetcher=mock_fetcher,
    )

    # Active position in Gold (/MGC)
    active_positions = [
        {
            "contract": "/MGC",
            "symbol": "/MGC",
            "direction": "LONG",
            "asset_class": "FUTURES",
            "notional_value": 10000.0,
        }
    ]

    # Candidate in /MES (assume statistical test returns 0.94)
    cand = create_candidate("/MES", direction=Direction.LONG, price=5000.0, asset_class=AssetClass.FUTURES)

    res = await evaluator.evaluate_candidate(cand, use_llm=False, active_positions=active_positions)
    assert res.approved is False
    assert "Statistical correlation limit exceeded" in (res.rejection_reason or "")
    assert "0.94" in (res.rejection_reason or "")


def test_market_data_fetcher_calculate_correlation():
    fetcher = MarketDataFetcher()
    # Identical tickers return 1.0 immediately
    assert fetcher.calculate_correlation("SPY", "SPY") == 1.0

    # Test with mocked DataFrames
    dates = pd.date_range("2026-01-01", periods=30, freq="D")
    df_a = pd.DataFrame({"Close": [100 + i for i in range(30)]}, index=dates)
    df_b = pd.DataFrame({"Close": [200 + 2 * i for i in range(30)]}, index=dates)

    mock_data_a = MagicMock()
    mock_data_a.daily = df_a
    mock_data_b = MagicMock()
    mock_data_b.daily = df_b

    def mock_fetch(contract, ticker):
        if ticker == "AAA":
            return mock_data_a
        return mock_data_b

    fetcher.fetch_data = mock_fetch

    corr = fetcher.calculate_correlation("AAA", "BBB", lookback_days=30)
    assert corr is not None
    # Perfectly co-moving linear prices have high return correlation
    assert corr > 0.90
