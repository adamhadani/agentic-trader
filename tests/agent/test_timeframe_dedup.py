from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.config import AppConfig
from agentic_trader.constants import AssetClass
from agentic_trader.research.alpha.models import RegistrySnapshot
from agentic_trader.screeners.base import ScreenerCandidate


@pytest.fixture
def mock_copilot():
    cfg = AppConfig()

    repository = AsyncMock()
    repository.snapshot.return_value = RegistrySnapshot(0, (), ())
    copilot = TradingCopilot(config=cfg, db=AsyncMock(), alpha_repository=repository)
    copilot.db = AsyncMock()
    copilot.db.get_state.return_value = None
    copilot.db.get_active_notional_exposure.return_value = 0.0
    copilot.db.get_active_position_count.return_value = 0
    copilot.db.get_active_positions.return_value = []
    copilot.evaluator = AsyncMock()
    copilot.notifier = MagicMock()
    copilot.data_fetcher = MagicMock()
    copilot.data_fetcher.fetch_data.return_value = MagicMock(
        daily=pd.DataFrame({"Close": [100.0]}),
        four_hour=pd.DataFrame({"Close": [100.0]}),
        hourly=pd.DataFrame({"Close": [100.0]}),
        fifteen_minute=pd.DataFrame({"Close": [100.0]}),
    )
    copilot.strategy_engine = MagicMock()
    copilot.session_provider = AsyncMock()
    copilot.session_provider.is_session_active.return_value = (True, "Session Active")
    copilot.calendar = AsyncMock()
    copilot.calendar.is_in_lockout_window.return_value = (False, None)
    copilot.regime_detector = AsyncMock()
    mock_regime = MagicMock()
    mock_regime.summary_text = "NORMAL"
    copilot.regime_detector.get_regime.return_value = mock_regime
    return copilot


@pytest.mark.asyncio
async def test_timeframe_aware_deduplication(mock_copilot):
    """Verify 15m setups deduplicate with 2h window while 4h setups use configured risk window (12h)."""
    cand_15m = ScreenerCandidate(
        contract="SPY",
        symbol="SPY",
        asset_class=AssetClass.EQUITY,
        timeframe="15m",
        strategy="alpha_test_15m",
        direction="LONG",
        current_price=580.0,
        ema_20=579.0,
        ema_50=578.0,
        ema_200=575.0,
        rsi_14=60.0,
        atr_14=1.5,
        candle_timestamp="2026-09-15T10:00:00Z",
        recent_swing_low=577.0,
        recent_swing_high=582.0,
        trigger_detail="Test 15m trigger",
    )

    cand_4h = ScreenerCandidate(
        contract="SPY",
        symbol="SPY",
        asset_class=AssetClass.EQUITY,
        timeframe="4h",
        strategy="trend_pullback",
        direction="LONG",
        current_price=580.0,
        ema_20=579.0,
        ema_50=578.0,
        ema_200=575.0,
        rsi_14=60.0,
        atr_14=6.5,
        candle_timestamp="2026-09-15T12:00:00Z",
        recent_swing_low=570.0,
        recent_swing_high=590.0,
        trigger_detail="Test 4h trigger",
    )

    mock_copilot.strategy_engine.scan_contract.return_value = [cand_15m, cand_4h]
    mock_copilot.db.is_duplicate_recent.return_value = False
    mock_copilot.evaluator.evaluate_candidate.return_value = MagicMock(approved=False, rejection_reason="test")

    # Run scan across SPY
    with patch.object(
        mock_copilot.config, "contracts", {"SPY": MagicMock(ticker="SPY", name="SPY", asset_class=AssetClass.EQUITY)}
    ):
        await mock_copilot.run_scan(dry_run=True, symbols=["SPY"], bypass_session_filter=True)

    # Verify calls to is_duplicate_recent
    calls = mock_copilot.db.is_duplicate_recent.call_args_list
    assert len(calls) == 2

    # 15m candidate should use hours=2
    assert calls[0].args == ("SPY", "alpha_test_15m")
    assert calls[0].kwargs["hours"] == 2

    # 4h candidate should use hours=12 (config default)
    assert calls[1].args == ("SPY", "trend_pullback")
    assert calls[1].kwargs["hours"] == 12


@pytest.mark.asyncio
async def test_run_scan_timeframe_filter(mock_copilot):
    """Verify run_scan with timeframe='15m' filters out other timeframe setups."""
    cand_15m = ScreenerCandidate(
        contract="SPY",
        symbol="SPY",
        asset_class=AssetClass.EQUITY,
        timeframe="15m",
        strategy="alpha_test_15m",
        direction="LONG",
        current_price=580.0,
        ema_20=579.0,
        ema_50=578.0,
        ema_200=575.0,
        rsi_14=60.0,
        atr_14=1.5,
        candle_timestamp="2026-09-15T10:00:00Z",
        recent_swing_low=577.0,
        recent_swing_high=582.0,
        trigger_detail="Test 15m trigger",
    )

    cand_4h = ScreenerCandidate(
        contract="SPY",
        symbol="SPY",
        asset_class=AssetClass.EQUITY,
        timeframe="4h",
        strategy="trend_pullback",
        direction="LONG",
        current_price=580.0,
        ema_20=579.0,
        ema_50=578.0,
        ema_200=575.0,
        rsi_14=60.0,
        atr_14=6.5,
        candle_timestamp="2026-09-15T12:00:00Z",
        recent_swing_low=570.0,
        recent_swing_high=590.0,
        trigger_detail="Test 4h trigger",
    )

    mock_copilot.strategy_engine.scan_contract.return_value = [cand_15m, cand_4h]
    mock_copilot.db.is_duplicate_recent.return_value = False
    mock_copilot.evaluator.evaluate_candidate.return_value = MagicMock(approved=False, rejection_reason="test")

    with patch.object(
        mock_copilot.config, "contracts", {"SPY": MagicMock(ticker="SPY", name="SPY", asset_class=AssetClass.EQUITY)}
    ):
        await mock_copilot.run_scan(dry_run=True, symbols=["SPY"], bypass_session_filter=True, timeframe="15m")

    # Only the 15m candidate should have been processed
    calls = mock_copilot.db.is_duplicate_recent.call_args_list
    assert len(calls) == 1
    assert calls[0].args == ("SPY", "alpha_test_15m")
