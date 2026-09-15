from unittest.mock import AsyncMock, patch

import pytest

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.config import load_config


@pytest.mark.asyncio
async def test_update_position_stop_db(temp_db):
    sig_id = await temp_db.record_signal(
        contract="/MES",
        strategy="TREND_PULLBACK",
        direction="LONG",
        entry_price=5800.0,
        stop_loss=5750.0,
        take_profit=5900.0,
        risk_dollars=250.0,
        reward_dollars=500.0,
        notional_value=29000.0,
        status="EXECUTED",
    )

    # Move stop to breakeven
    updated = await temp_db.update_position_stop(sig_id, 5801.0, raw_response="BREAKEVEN")
    assert updated is True

    sig = await temp_db.get_signal_by_id(sig_id)
    assert sig is not None
    assert sig["stop_loss"] == 5801.0
    assert sig["raw_response"] == "BREAKEVEN"


@pytest.mark.asyncio
async def test_manage_trailing_stops_long_breakeven_and_trail(temp_db):
    config = load_config()
    config.db_path = temp_db.db_path
    config.trailing_stop.enabled = True
    config.trailing_stop.breakeven_trigger_r = 1.0
    config.trailing_stop.breakeven_buffer_dollars = 5.0
    config.trailing_stop.trail_trigger_r = 1.5
    config.trailing_stop.trail_atr_multiple = 1.5
    config.trailing_stop.trail_step_ticks = 2

    copilot = TradingCopilot(config)
    copilot.notifier.send_trailing_stop_alert = AsyncMock()

    # Create active LONG trade on /MES (multiplier = 5.0)
    # Entry: 5800.0, Stop: 5760.0 (Risk = 40 pts = $200)
    # Breakeven target buffer: 5.0 / 5.0 = 1.0 pt -> Target stop = 5801.0
    sig_id = await temp_db.record_signal(
        contract="/MES",
        strategy="TREND_PULLBACK",
        direction="LONG",
        entry_price=5800.0,
        stop_loss=5760.0,
        take_profit=5880.0,
        risk_dollars=200.0,
        reward_dollars=400.0,
        notional_value=29000.0,
        status="EXECUTED",
    )

    active_positions = await temp_db.get_active_positions()

    # 1. Price moves to 5845.0 (+45 pts / 40 pts risk = +1.125R >= 1.0R Breakeven Trigger)
    with patch.object(copilot.data_fetcher, "fetch_latest_price", return_value=5845.0):
        updates = await copilot.manage_trailing_stops(active_positions)
        assert updates == 1

        sig = await temp_db.get_signal_by_id(sig_id)
        assert sig["stop_loss"] == 5801.0
        assert sig["raw_response"] == "BREAKEVEN"
        copilot.notifier.send_trailing_stop_alert.assert_called_once()
        assert copilot.notifier.send_trailing_stop_alert.call_args[1]["reason"] == "BREAKEVEN"

    # 2. Price advances to 5880.0 (+80 pts = +2.0R >= 1.5R Trailing Trigger)
    # Trail distance = 1.5 * 40 pts = 60 pts. Proposed trail stop = 5880 - 60 = 5820.0 (> 5801.0)
    copilot.notifier.send_trailing_stop_alert.reset_mock()
    active_positions = await temp_db.get_active_positions()

    with patch.object(copilot.data_fetcher, "fetch_latest_price", return_value=5880.0):
        updates = await copilot.manage_trailing_stops(active_positions)
        assert updates == 1

        sig = await temp_db.get_signal_by_id(sig_id)
        assert sig["stop_loss"] == 5820.0
        assert sig["raw_response"] == "TRAILING_STOP"
        copilot.notifier.send_trailing_stop_alert.assert_called_once()
        assert copilot.notifier.send_trailing_stop_alert.call_args[1]["reason"] == "TRAILING_STOP"


@pytest.mark.asyncio
async def test_manage_trailing_stops_short_breakeven(temp_db):
    config = load_config()
    config.db_path = temp_db.db_path
    config.trailing_stop.enabled = True
    config.trailing_stop.breakeven_trigger_r = 1.0
    config.trailing_stop.breakeven_buffer_dollars = 5.0

    copilot = TradingCopilot(config)
    copilot.notifier.send_trailing_stop_alert = AsyncMock()

    # Create active SHORT trade on /MNQ (multiplier = 2.0)
    # Entry: 20000.0, Stop: 20050.0 (Risk = 50 pts = $100)
    # Breakeven target buffer: 5.0 / 2.0 = 2.5 pts -> Target stop = 20000 - 2.5 = 19997.5
    sig_id = await temp_db.record_signal(
        contract="/MNQ",
        strategy="SQUEEZE_BREAKOUT",
        direction="SHORT",
        entry_price=20000.0,
        stop_loss=20050.0,
        take_profit=19900.0,
        risk_dollars=100.0,
        reward_dollars=200.0,
        notional_value=40000.0,
        status="EXECUTED",
    )

    active_positions = await temp_db.get_active_positions()

    # Price drops to 19940.0 (+60 pts profit / 50 pts risk = +1.2R >= 1.0R Breakeven Trigger)
    with patch.object(copilot.data_fetcher, "fetch_latest_price", return_value=19940.0):
        updates = await copilot.manage_trailing_stops(active_positions)
        assert updates == 1

        sig = await temp_db.get_signal_by_id(sig_id)
        assert sig["stop_loss"] == 19997.5
        assert sig["raw_response"] == "BREAKEVEN"
        copilot.notifier.send_trailing_stop_alert.assert_called_once()


@pytest.mark.asyncio
async def test_manage_trailing_stops_chandelier_atr_no_breakeven(temp_db):
    """Verify that under chandelier_atr mode with breakeven_trigger_r=None,

    stops do NOT move to breakeven at 1.0-1.4R, but ratchet via ATR trailing at >= 1.5R.
    """
    config = load_config()
    config.db_path = temp_db.db_path
    config.trailing_stop.enabled = True
    config.trailing_stop.mode = "chandelier_atr"
    config.trailing_stop.breakeven_trigger_r = None
    config.trailing_stop.trail_trigger_r = 1.5
    config.trailing_stop.trail_atr_multiple = 1.5
    config.trailing_stop.trail_step_ticks = 2

    copilot = TradingCopilot(config)
    copilot.notifier.send_trailing_stop_alert = AsyncMock()

    # Entry: 5800.0, Stop: 5760.0 (Risk = 40 pts = $200)
    sig_id = await temp_db.record_signal(
        contract="/MES",
        strategy="TREND_PULLBACK",
        direction="LONG",
        entry_price=5800.0,
        stop_loss=5760.0,
        take_profit=5880.0,
        risk_dollars=200.0,
        reward_dollars=400.0,
        notional_value=29000.0,
        status="EXECUTED",
    )

    active_positions = await temp_db.get_active_positions()

    # 1. Price moves to 5848.0 (+48 pts / 40 pts risk = +1.2R)
    # Because breakeven_trigger_r is None, stop should NOT move to entry!
    with patch.object(copilot.data_fetcher, "fetch_latest_price", return_value=5848.0):
        updates = await copilot.manage_trailing_stops(active_positions)
        assert updates == 0

        sig = await temp_db.get_signal_by_id(sig_id)
        assert sig["stop_loss"] == 5760.0
        copilot.notifier.send_trailing_stop_alert.assert_not_called()

    # 2. Price advances to 5880.0 (+80 pts = +2.0R >= 1.5R Trailing Trigger)
    # Trail distance = 1.5 * 40 pts = 60 pts. Proposed trail stop = 5880 - 60 = 5820.0
    with patch.object(copilot.data_fetcher, "fetch_latest_price", return_value=5880.0):
        updates = await copilot.manage_trailing_stops(active_positions)
        assert updates == 1

        sig = await temp_db.get_signal_by_id(sig_id)
        assert sig["stop_loss"] == 5820.0
        assert sig["raw_response"] == "TRAILING_STOP"
        copilot.notifier.send_trailing_stop_alert.assert_called_once()
        assert copilot.notifier.send_trailing_stop_alert.call_args[1]["reason"] == "TRAILING_STOP"
