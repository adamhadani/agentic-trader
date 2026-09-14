import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_trader.broker import (
    BaseBroker,
    CircuitState,
    OrderRequest,
    OrderResult,
    PaperBroker,
    ReconciliationEvent,
    RedundantBroker,
    TradovateBroker,
    create_broker,
)
from agentic_trader.config import AppConfig, RedundancyConfig
from agentic_trader.constants import AssetClass, ExecutionMode, ExitReason, OrderSide


@pytest.mark.asyncio
async def test_tradovate_get_account_balance():
    config = AppConfig(
        execution_mode=ExecutionMode.TRADOVATE,
        tradovate_username="trader1",
        tradovate_password="pw",
        tradovate_api_key="key",
        tradovate_api_secret="sec",
        tradovate_account_id="12345",
    )
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "cashBalance": 75000.0,
        "realizedPnL": 1250.0,
        "netLiquidity": 76250.0,
    }
    mock_client.get.return_value = mock_resp

    broker = TradovateBroker(config, client=mock_client)
    broker._access_token = "token_abc"

    bal = await broker.get_account_balance()
    assert bal["cash"] == 75000.0
    assert bal["realized_pnl"] == 1250.0
    assert bal["net_liquidity"] == 76250.0


@pytest.mark.asyncio
async def test_tradovate_reconcile_positions_exit_detection():
    config = AppConfig(
        execution_mode=ExecutionMode.TRADOVATE,
        tradovate_username="trader1",
        tradovate_password="pw",
        tradovate_api_key="key",
        tradovate_api_secret="sec",
        tradovate_account_id="12345",
    )
    mock_client = AsyncMock()

    # /position/list: /MNQ is still open (netPos=1), /MES is closed (missing from list)
    resp_pos = MagicMock(status_code=200)
    resp_pos.json.return_value = [
        {"contract": "MNQM4", "netPos": 1, "netPrice": 19000.0},
    ]

    # /order/list: bracket limit order filled for /MES at 5850.0
    resp_orders = MagicMock(status_code=200)
    resp_orders.json.return_value = [
        {
            "id": 991,
            "contract": "MESM4",
            "symbol": "MESM4",
            "action": "Sell",
            "ordStatus": "Filled",
            "orderType": "Limit",
            "avgPx": 5850.0,
        }
    ]

    # /fill/list
    resp_fills = MagicMock(status_code=200)
    resp_fills.json.return_value = [
        {
            "id": 1001,
            "orderId": 991,
            "contract": "MESM4",
            "action": "Sell",
            "price": 5850.0,
            "qty": 1,
        }
    ]

    def mock_get(url, *args, **kwargs):
        if "position/list" in url:
            return resp_pos
        elif "order/list" in url:
            return resp_orders
        elif "fill/list" in url:
            return resp_fills
        raise ValueError(f"Unexpected GET {url}")

    mock_client.get.side_effect = mock_get

    broker = TradovateBroker(config, client=mock_client)
    broker._access_token = "token_abc"

    active_positions = [
        {
            "id": 42,
            "contract": "/MES",
            "direction": "LONG",
            "entry_price": 5800.0,
            "stop_loss": 5750.0,
            "take_profit": 5850.0,
            "quantity": 1.0,
            "strategy": "TREND_PULLBACK",
        },
        {
            "id": 43,
            "contract": "/MNQ",
            "direction": "LONG",
            "entry_price": 19000.0,
            "stop_loss": 18800.0,
            "take_profit": 19400.0,
            "quantity": 1.0,
            "strategy": "SQUEEZE_BREAKOUT",
        },
    ]

    events = await broker.reconcile_positions(active_positions)
    # /MNQ is still open in /position/list, only /MES should be reconciled as an exit
    assert len(events) == 1
    ev = events[0]
    assert ev.signal_id == 42
    assert ev.contract == "/MES"
    assert ev.exit_price == 5850.0
    assert ev.exit_reason == ExitReason.TAKE_PROFIT
    assert ev.broker_order_id == "991"
    # Realized PnL: (5850.0 - 5800.0) * 5.0 (/MES multiplier) * 1 = +$250.0
    assert ev.realized_pnl == pytest.approx(250.0)


@pytest.mark.asyncio
async def test_tradovate_websocket_streaming_flow():
    config = AppConfig(
        execution_mode=ExecutionMode.TRADOVATE,
        tradovate_username="trader1",
        tradovate_password="pw",
        tradovate_api_key="key",
        tradovate_api_secret="sec",
    )

    # Mock WebSocket connection sequence:
    # 1. 'o' -> open frame
    # 2. 'h' -> heartbeat
    # 3. 'a[...]' -> order stop-loss fill event
    events_received: list[ReconciliationEvent] = []

    class MockWS:
        def __init__(self):
            self.sent_messages: list[str] = []
            self.step = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

        async def send(self, msg: str):
            self.sent_messages.append(msg)

        async def recv(self):
            if self.step == 0:
                self.step += 1
                return "o"
            elif self.step == 1:
                self.step += 1
                return "h"
            elif self.step == 2:
                self.step += 1
                payload = [
                    json.dumps(
                        {
                            "e": "props",
                            "d": {
                                "entityType": "order",
                                "entity": {
                                    "contract": "MESM4",
                                    "symbol": "MESM4",
                                    "avgPx": 5740.0,
                                    "action": "Sell",
                                    "ordStatus": "Filled",
                                    "orderType": "Stop",
                                    "id": 7788,
                                },
                            },
                        }
                    )
                ]
                return f"a{json.dumps(payload)}"
            else:
                # Cancel or wait
                await asyncio.sleep(0.01)
                raise asyncio.CancelledError()

        async def close(self):
            pass

    mock_ws = MockWS()

    def mock_ws_connect(url):
        return mock_ws

    broker = TradovateBroker(config, ws_connect=mock_ws_connect)
    broker._access_token = "valid_jwt_token"

    async def fill_callback(ev: ReconciliationEvent):
        events_received.append(ev)

    with pytest.raises(asyncio.CancelledError):
        await broker.start_trade_stream(fill_callback)

    # Verify authorization frame was sent upon 'o'
    assert any("authorize\n1\n\nvalid_jwt_token" in m for m in mock_ws.sent_messages)
    # Verify heartbeat response was sent upon 'h'
    assert "[]" in mock_ws.sent_messages
    # Verify fill callback received the parsed stop-loss event
    assert len(events_received) == 1
    ev = events_received[0]
    assert ev.symbol == "MESM4"
    assert ev.exit_price == 5740.0
    assert ev.exit_reason == ExitReason.STOP_LOSS
    assert ev.broker_order_id == "7788"


@pytest.mark.asyncio
async def test_redundant_broker_circuit_breaker_and_failover():
    primary = MagicMock(spec=BaseBroker)
    primary.connect = AsyncMock(return_value=True)
    primary.disconnect = AsyncMock()
    primary.get_positions = AsyncMock(return_value=[])
    primary.get_account_balance = AsyncMock(return_value={"cash": 100000.0})
    primary.reconcile_positions = AsyncMock(return_value=[])
    primary.start_trade_stream = AsyncMock()
    primary.stop_trade_stream = AsyncMock()

    fallback = MagicMock(spec=BaseBroker)
    fallback.connect = AsyncMock(return_value=True)
    fallback.disconnect = AsyncMock()
    fallback.get_positions = AsyncMock(return_value=[])
    fallback.get_account_balance = AsyncMock(return_value={"cash": 50000.0})
    fallback.reconcile_positions = AsyncMock(return_value=[])
    fallback.start_trade_stream = AsyncMock()
    fallback.stop_trade_stream = AsyncMock()

    # Redundancy config with max 2 failures
    cfg = RedundancyConfig(
        enabled=True,
        fallback_mode="paper",
        max_consecutive_failures=2,
        recovery_probe_interval_seconds=0.1,
    )
    redundant = RedundantBroker(primary_broker=primary, fallback_broker=fallback, config=cfg)

    # Initially CLOSED
    assert redundant.state == CircuitState.CLOSED
    connected = await redundant.connect()
    assert connected is True
    assert primary.connect.called

    # Primary order succeeds
    req = OrderRequest(
        symbol="SPY",
        asset_class=AssetClass.EQUITY,
        side=OrderSide.BUY,
        quantity=10.0,
        entry_price=500.0,
    )
    primary.submit_entry_order = AsyncMock(return_value=OrderResult(success=True, order_id="P-1"))
    fallback.submit_entry_order = AsyncMock(return_value=OrderResult(success=True, order_id="FB-1"))

    res = await redundant.submit_entry_order(req)
    assert res.order_id == "P-1"
    assert redundant.consecutive_failures == 0
    assert redundant.state == CircuitState.CLOSED

    # Failure #1 on primary (e.g. broker timeout)
    primary.submit_entry_order = AsyncMock(
        return_value=OrderResult(success=False, error_message="Connection timeout to broker API")
    )
    res_fail1 = await redundant.submit_entry_order(req)
    assert res_fail1.success is False
    assert redundant.consecutive_failures == 1
    assert redundant.state == CircuitState.CLOSED  # Not tripped yet (limit is 2)

    # Failure #2 on primary -> trips circuit to OPEN and routes to fallback
    res_fail2 = await redundant.submit_entry_order(req)
    assert res_fail2.success is True  # executed via fallback!
    assert res_fail2.order_id == "FB-1"
    assert redundant.consecutive_failures == 2
    assert redundant.state == CircuitState.OPEN

    # While OPEN, subsequent calls bypass primary completely and route to fallback
    primary.submit_entry_order.reset_mock()
    fallback.submit_entry_order.reset_mock()
    res_open = await redundant.submit_entry_order(req)
    assert res_open.order_id == "FB-1"
    primary.submit_entry_order.assert_not_called()
    fallback.submit_entry_order.assert_called_once()


@pytest.mark.asyncio
async def test_redundant_broker_recovery_half_open():
    primary = MagicMock(spec=BaseBroker)
    fallback = MagicMock(spec=BaseBroker)

    primary.submit_entry_order = AsyncMock(return_value=OrderResult(success=True, order_id="P-REC"))
    fallback.submit_entry_order = AsyncMock(return_value=OrderResult(success=True, order_id="FB-REC"))

    cfg = RedundancyConfig(
        enabled=True,
        max_consecutive_failures=1,
        recovery_probe_interval_seconds=0.05,
        auto_failback=True,
    )
    redundant = RedundantBroker(primary_broker=primary, fallback_broker=fallback, config=cfg)

    # Manually trip circuit
    redundant._record_failure("Forced error")
    assert redundant.state == CircuitState.OPEN

    # Sleep past recovery cooldown
    await asyncio.sleep(0.06)

    # Next call should transition to HALF_OPEN and probe primary
    req = OrderRequest(symbol="/MES", entry_price=5800.0)
    res = await redundant.submit_entry_order(req)
    assert res.order_id == "P-REC"
    # Primary succeeded, circuit must reset to CLOSED
    assert redundant.state == CircuitState.CLOSED
    assert redundant.consecutive_failures == 0


@pytest.mark.asyncio
async def test_create_broker_with_redundancy_config():
    config = AppConfig(
        execution_mode=ExecutionMode.TRADOVATE,
        tradovate_username="user1",
        tradovate_password="pw",
        tradovate_api_key="k",
        tradovate_api_secret="s",
        redundancy=RedundancyConfig(
            enabled=True,
            fallback_mode="paper",
            max_consecutive_failures=2,
        ),
    )
    broker = create_broker(config)
    assert isinstance(broker, RedundantBroker)
    assert isinstance(broker.primary, TradovateBroker)
    assert isinstance(broker.fallback, PaperBroker)
    assert broker.config.max_consecutive_failures == 2
