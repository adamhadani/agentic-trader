from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.broker.alpaca import AlpacaBroker
from agentic_trader.broker.base import ReconciliationEvent
from agentic_trader.config import AppConfig
from agentic_trader.constants import Direction, ExitReason, SignalStatus


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
    copilot = TradingCopilot(test_config)
    copilot.notifier.send_exit_alert = AsyncMock(return_value=123)
    copilot.manage_trailing_stops = AsyncMock()
    client = MagicMock()
    entry = {
        "id": "ALP-ENTRY-999",
        "symbol": "SPY",
        "side": "buy",
        "status": "filled",
        "filled_qty": "10",
        "filled_avg_price": "500",
        "filled_at": datetime.now(UTC),
        "legs": [],
    }
    client.get_order_by_id.return_value = entry
    copilot.broker.client = client

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
        broker_order_id="ALP-ENTRY-999",
        fill_price=500.0,
        status=SignalStatus.EXECUTED,
    )

    # Verify active position exists
    active = await copilot.db.get_active_positions()
    assert len(active) == 1

    # Invariant 1: Stream fill matching entry order ID confirms entry and MUST NOT close position
    ev_entry = ReconciliationEvent(
        signal_id=0,
        symbol="SPY",
        contract="SPY",
        direction=Direction.LONG,
        exit_price=500.0,
        exit_reason=ExitReason.MANUAL_CLOSE,
        exit_timestamp=datetime.now(UTC),
        broker_order_id="ALP-ENTRY-999",
        order_side="buy",
    )
    await copilot.on_stream_trade_update(ev_entry)

    # Position MUST remain active!
    active_after_entry_fill = await copilot.db.get_active_positions()
    assert len(active_after_entry_fill) == 1
    copilot.notifier.send_exit_alert.assert_not_called()

    # Invariant 2: Stream fill with non-opposing side (BUY fill on a LONG position) MUST NOT close position
    ev_wrong_side = ReconciliationEvent(
        signal_id=0,
        symbol="SPY",
        contract="SPY",
        direction=Direction.LONG,
        exit_price=505.0,
        exit_reason=ExitReason.MANUAL_CLOSE,
        exit_timestamp=datetime.now(UTC),
        broker_order_id="ALP-OTHER-BUY-1002",
        order_side="buy",
    )
    await copilot.on_stream_trade_update(ev_wrong_side)

    active_after_wrong_side = await copilot.db.get_active_positions()
    assert len(active_after_wrong_side) == 1
    copilot.notifier.send_exit_alert.assert_not_called()

    # Invariant 3: Legitimate exit fill with opposing side (SELL on LONG position) and distinct exit order ID DOES close position
    ev_exit = ReconciliationEvent(
        signal_id=0,
        symbol="SPY",
        contract="SPY",
        direction=Direction.LONG,
        exit_price=510.0,
        exit_reason=ExitReason.TAKE_PROFIT,
        exit_timestamp=datetime.now(UTC),
        broker_order_id="ALP-EXIT-TP-1001",
        order_side="sell",
    )
    # Only the exact bracket leg, verified by REST, authorizes closure.
    entry["legs"] = [
        {
            "id": "ALP-EXIT-TP-1001",
            "symbol": "SPY",
            "side": "sell",
            "status": "filled",
            "filled_qty": "10",
            "filled_avg_price": "510",
            "order_type": "limit",
            "filled_at": datetime.now(UTC),
        }
    ]
    await copilot.on_stream_trade_update(ev_exit)

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
    copilot = TradingCopilot(test_config)
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


@pytest.mark.asyncio
async def test_alpaca_reconcile_skips_entry_order_and_requires_exit(test_config):
    broker = AlpacaBroker(test_config)
    broker.client = MagicMock()

    # Case 1: Symbol not in open positions, but closed orders only contains the entry BUY order
    broker.client.get_all_positions.return_value = []

    entry_order = SimpleNamespace(
        id="ENTRY-ORDER-123",
        symbol="SPY",
        filled_qty="39",
        legs=[],
        side="buy",
        status="filled",
        filled_avg_price="761.50",
        order_type="limit",
        filled_at=datetime.now(UTC),
    )
    broker.client.get_orders.return_value = [entry_order]
    broker.client.get_order_by_id.return_value = entry_order

    active_positions = [
        {
            "id": 1,
            "contract": "SPY",
            "symbol": "SPY",
            "direction": "LONG",
            "entry_price": 761.50,
            "take_profit": 772.0,
            "quantity": 39.0,
            "broker_order_id": "ENTRY-ORDER-123",
        }
    ]

    # Reconcile should NOT treat the entry order as an exit and should return empty list
    events = await broker.reconcile_positions(active_positions)
    assert events == []

    # Case 2: An actual exit SELL order has filled
    exit_order = SimpleNamespace(
        id="EXIT-ORDER-456",
        symbol="SPY",
        filled_qty="39",
        side="sell",
        status="filled",
        filled_avg_price="772.10",
        order_type="limit",
        filled_at=datetime.now(UTC),
    )
    broker.client.get_orders.return_value = [exit_order, entry_order]
    entry_order.legs = [exit_order]

    events_exit = await broker.reconcile_positions(active_positions)
    assert len(events_exit) == 1
    assert events_exit[0].exit_reason == ExitReason.TAKE_PROFIT
    assert events_exit[0].exit_price == 772.10
    assert events_exit[0].broker_order_id == "EXIT-ORDER-456"
    assert events_exit[0].order_side == "sell"


@pytest.mark.asyncio
async def test_copilot_process_reconciliation_event_safeguards(test_config):
    copilot = TradingCopilot(test_config)
    copilot.notifier.send_exit_alert = AsyncMock()

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
        broker_order_id="ENTRY-123",
        fill_price=500.0,
        status=SignalStatus.EXECUTED,
    )

    # Safeguard 1: Event matching entry order ID is rejected
    ev_entry = ReconciliationEvent(
        signal_id=sig_id,
        symbol="SPY",
        contract="SPY",
        direction=Direction.LONG,
        exit_price=500.0,
        exit_reason=ExitReason.MANUAL_CLOSE,
        exit_timestamp=datetime.now(UTC),
        broker_order_id="ENTRY-123",
        order_side="buy",
    )
    res_entry = await copilot.process_reconciliation_event(ev_entry)
    assert res_entry is False
    assert len(await copilot.db.get_active_positions()) == 1

    # Safeguard 2: Event with non-opposing side (BUY for LONG) is rejected
    ev_same_side = ReconciliationEvent(
        signal_id=sig_id,
        symbol="SPY",
        contract="SPY",
        direction=Direction.LONG,
        exit_price=505.0,
        exit_reason=ExitReason.MANUAL_CLOSE,
        exit_timestamp=datetime.now(UTC),
        broker_order_id="BUY-999",
        order_side="buy",
    )
    res_same_side = await copilot.process_reconciliation_event(ev_same_side)
    assert res_same_side is False
    assert len(await copilot.db.get_active_positions()) == 1

    # Safeguard 3: Legitimate opposing exit order (SELL for LONG) succeeds
    ev_valid_exit = ReconciliationEvent(
        signal_id=sig_id,
        symbol="SPY",
        contract="SPY",
        direction=Direction.LONG,
        exit_price=510.0,
        exit_reason=ExitReason.TAKE_PROFIT,
        exit_timestamp=datetime.now(UTC),
        broker_order_id="EXIT-SELL-888",
        order_side="sell",
    )
    res_valid = await copilot.process_reconciliation_event(ev_valid_exit)
    assert res_valid is True
    assert len(await copilot.db.get_active_positions()) == 0
    copilot.notifier.send_exit_alert.assert_awaited_once()
