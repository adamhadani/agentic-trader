from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_trader.broker.alpaca import AlpacaBroker
from agentic_trader.broker.base import ReconciliationEvent
from agentic_trader.config import AppConfig
from agentic_trader.constants import Direction, ExitReason, SignalStatus
from agentic_trader.main import FuturesCopilot


@pytest.fixture
def test_config(tmp_path):
    db_file = tmp_path / "test_stream.db"
    return AppConfig(
        db_path=str(db_file),
        execution_mode="alpaca",
        alpaca_api_key="TEST_KEY",
        alpaca_api_secret="TEST_SECRET",
        alpaca_base_url="https://paper-api.alpaca.markets/v2",
        telegram_bot_token=None,
        telegram_chat_id=None,
    )


@pytest.mark.asyncio
async def test_alpaca_broker_trade_stream_subscription(test_config):
    broker = AlpacaBroker(test_config)
    received_events: list[ReconciliationEvent] = []

    async def mock_callback(ev: ReconciliationEvent) -> None:
        received_events.append(ev)

    mock_stream = MagicMock()
    mock_stream._run_forever = AsyncMock(return_value=None)
    mock_stream.close = AsyncMock(return_value=None)
    mock_stream.stop_ws = AsyncMock(return_value=None)

    captured_handler = None

    def capture_subscribe(handler):
        nonlocal captured_handler
        captured_handler = handler

    mock_stream.subscribe_trade_updates.side_effect = capture_subscribe

    with patch("agentic_trader.broker.alpaca.TradingStream", return_value=mock_stream):
        await broker.start_trade_stream(mock_callback)

    assert broker._trade_stream is not None
    assert captured_handler is not None

    # Simulate a fill event from TradingStream
    mock_trade_update = MagicMock()
    mock_trade_update.event = "fill"
    mock_trade_update.price = 155.50
    mock_trade_update.order = MagicMock(
        symbol="AAPL",
        id="order-xyz-123",
        side="sell",
        order_type="limit",
        filled_avg_price=155.50,
    )

    await captured_handler(mock_trade_update)

    assert len(received_events) == 1
    ev = received_events[0]
    assert ev.symbol == "AAPL"
    assert ev.exit_price == 155.50
    assert ev.broker_order_id == "order-xyz-123"
    assert ev.exit_reason == ExitReason.TAKE_PROFIT


@pytest.mark.asyncio
async def test_alpaca_broker_trade_stream_stop(test_config):
    broker = AlpacaBroker(test_config)
    mock_stream = MagicMock()
    mock_stream.stop_ws = AsyncMock()
    mock_stream.close = AsyncMock()
    broker._trade_stream = mock_stream

    await broker.stop_trade_stream()

    mock_stream.stop_ws.assert_awaited_once()
    mock_stream.close.assert_awaited_once()
    assert broker._trade_stream is None


@pytest.mark.asyncio
async def test_copilot_on_stream_trade_update_matching(test_config):
    copilot = FuturesCopilot(test_config)
    copilot.notifier.send_exit_alert = AsyncMock()

    # Record a signal and mark as executed
    sig_id = await copilot.db.record_signal(
        contract="SPY",
        strategy="trend_pullback",
        direction="LONG",
        entry_price=500.0,
        stop_loss=495.0,
        take_profit=510.0,
        risk_dollars=500.0,
        reward_dollars=1000.0,
        notional_value=50000.0,
        status="PENDING",
        asset_class="EQUITY",
        quantity=10.0,
    )
    await copilot.db.update_signal_execution(
        signal_id=sig_id,
        broker_order_id="ALP-ORDER-999",
        fill_price=500.0,
        status=SignalStatus.EXECUTED,
    )

    # Verify active position exists
    active = await copilot.db.get_active_positions()
    assert len(active) == 1

    # Emit stream trade update matching broker_order_id
    ev = ReconciliationEvent(
        signal_id=0,
        symbol="SPY",
        contract="SPY",
        direction=Direction.LONG,
        exit_price=510.0,
        exit_reason=ExitReason.TAKE_PROFIT,
        exit_timestamp=datetime.now(UTC),
        broker_order_id="ALP-ORDER-999",
    )

    await copilot.on_stream_trade_update(ev)

    # Active positions should now be empty (position closed)
    remaining_active = await copilot.db.get_active_positions()
    assert len(remaining_active) == 0

    # Check closed position in DB
    stats = await copilot.db.get_closed_positions_stats()
    assert stats["total_trades"] == 1
    # 10 shares * ($510 - $500) = $100
    assert stats["total_pnl"] == 100.0
    assert stats["win_rate"] == 100.0

    copilot.notifier.send_exit_alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_copilot_process_reconciliation_event_dedup(test_config):
    copilot = FuturesCopilot(test_config)
    copilot.notifier.send_exit_alert = AsyncMock()

    sig_id = await copilot.db.record_signal(
        contract="QQQ",
        strategy="squeeze_breakout",
        direction="SHORT",
        entry_price=450.0,
        stop_loss=455.0,
        take_profit=440.0,
        risk_dollars=500.0,
        reward_dollars=1000.0,
        notional_value=45000.0,
        status=SignalStatus.EXECUTED,
        asset_class="EQUITY",
        quantity=5.0,
    )

    ev = ReconciliationEvent(
        signal_id=sig_id,
        symbol="QQQ",
        contract="QQQ",
        direction=Direction.SHORT,
        exit_price=440.0,
        exit_reason=ExitReason.TAKE_PROFIT,
        exit_timestamp=datetime.now(UTC),
        realized_pnl=50.0,
        broker_order_id="ORDER-QQQ-1",
    )

    first_res = await copilot.process_reconciliation_event(ev)
    assert first_res is True

    # Second invocation for the same closed event should safely deduplicate and return False
    second_res = await copilot.process_reconciliation_event(ev)
    assert second_res is False

    # Alert should only be sent once
    assert copilot.notifier.send_exit_alert.await_count == 1
