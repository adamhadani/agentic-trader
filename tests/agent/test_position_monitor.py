from unittest.mock import AsyncMock, patch

import pytest

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.config import load_config


@pytest.mark.asyncio
async def test_db_active_positions_and_close(temp_db):
    # Record a signal and mark it EXECUTED
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

    positions = await temp_db.get_active_positions()
    assert len(positions) == 1
    assert positions[0]["id"] == sig_id
    assert positions[0]["contract"] == "/MES"

    # Close the position
    closed = await temp_db.close_position(
        signal_id=sig_id,
        exit_price=5910.0,
        exit_reason="TAKE_PROFIT",
        realized_pnl=550.0,
        status="CLOSED_WIN",
    )
    assert closed is True

    # Check active positions is now empty
    positions_after = await temp_db.get_active_positions()
    assert len(positions_after) == 0

    # Verify signal state
    sig = await temp_db.get_signal_by_id(sig_id)
    assert sig is not None
    assert sig["status"] == "CLOSED_WIN"
    assert sig["exit_price"] == 5910.0
    assert sig["realized_pnl"] == 550.0
    assert sig["exit_reason"] == "TAKE_PROFIT"
    assert sig["exit_timestamp"] is not None


@pytest.mark.asyncio
async def test_monitor_positions_take_profit_and_stop_loss(temp_db, mock_notifier):
    config = load_config()
    config.db_path = temp_db.db_path
    config.execution_mode = "paper"
    copilot = TradingCopilot(config, notifier=mock_notifier)
    copilot.notifier.send_exit_alert = AsyncMock()

    # 1. Long position that hits Take Profit
    sig_long_tp = await temp_db.record_signal(
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

    # 2. Short position that hits Stop Loss
    sig_short_sl = await temp_db.record_signal(
        contract="/MNQ",
        strategy="SQUEEZE_BREAKOUT",
        direction="SHORT",
        entry_price=20000.0,
        stop_loss=20100.0,
        take_profit=19800.0,
        risk_dollars=200.0,
        reward_dollars=400.0,
        notional_value=40000.0,
        status="EXECUTED",
    )

    # Mock market quotes:
    # /MES (MES=F) trades at 5885.0 (> 5880 TP)
    # /MNQ (MNQ=F) trades at 20110.0 (> 20100 SL for SHORT)
    def mock_latest_price(ticker):
        if ticker == "MES=F":
            return 5885.0
        if ticker == "MNQ=F":
            return 20110.0
        return 1000.0

    with patch.object(copilot.data_fetcher, "fetch_latest_price", side_effect=mock_latest_price):
        closed_count = await copilot.monitor_positions()

    assert closed_count == 2
    active_remaining = await temp_db.get_active_positions()
    assert len(active_remaining) == 0

    # Verify LONG TP
    mes_sig = await temp_db.get_signal_by_id(sig_long_tp)
    assert mes_sig["status"] == "CLOSED_WIN"
    assert mes_sig["exit_reason"] == "TAKE_PROFIT"
    # PnL = (5885 - 5800) * 5.0 = 85 * 5 = 425.0
    assert mes_sig["realized_pnl"] == 425.0

    # Verify SHORT SL
    mnq_sig = await temp_db.get_signal_by_id(sig_short_sl)
    assert mnq_sig["status"] == "CLOSED_LOSS"
    assert mnq_sig["exit_reason"] == "STOP_LOSS"
    # PnL = (20000 - 20110) * 2.0 = -110 * 2 = -220.0
    assert mnq_sig["realized_pnl"] == -220.0

    await copilot.outbox.drain()
    assert copilot.notifier.send_exit_alert.call_count == 2


@pytest.mark.asyncio
async def test_close_position_manual(temp_db, mock_notifier):
    config = load_config()
    config.db_path = temp_db.db_path
    config.execution_mode = "paper"
    copilot = TradingCopilot(config, notifier=mock_notifier)
    copilot.notifier.send_exit_alert = AsyncMock()

    sig_id = await temp_db.record_signal(
        contract="/MGC",
        strategy="TREND_PULLBACK",
        direction="LONG",
        entry_price=2600.0,
        stop_loss=2580.0,
        take_profit=2650.0,
        risk_dollars=200.0,
        reward_dollars=500.0,
        notional_value=26000.0,
        status="EXECUTED",
    )

    # Close with explicit exit price
    res = await copilot.close_position_manual(sig_id, exit_price=2625.0)
    assert "Closed" in res
    assert "/MGC" in res

    # PnL = (2625 - 2600) * 10 = +$250.00
    sig = await temp_db.get_signal_by_id(sig_id)
    assert sig["status"] == "CLOSED_WIN"
    assert sig["exit_price"] == 2625.0
    assert sig["realized_pnl"] == 250.0
    assert sig["exit_reason"] == "MANUAL_CLOSE"
