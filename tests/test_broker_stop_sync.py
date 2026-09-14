from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from alpaca.common.exceptions import APIError

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.broker.alpaca import AlpacaBroker
from agentic_trader.broker.base import BaseBroker, OrderRequest, OrderResult
from agentic_trader.broker.paper import PaperBroker
from agentic_trader.broker.tradovate import TradovateBroker
from agentic_trader.config import AppConfig


class DummyBroker(BaseBroker):
    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        pass

    async def submit_entry_order(self, request: OrderRequest) -> OrderResult:
        return OrderResult(success=True)

    async def close_position(self, *args, **kwargs) -> OrderResult:
        return OrderResult(success=True)

    async def get_positions(self) -> list:
        return []


@pytest.mark.asyncio
async def test_base_broker_modify_stop_default_unsupported():
    broker = DummyBroker()
    assert broker.supports_order_modification is False
    res = await broker.modify_order_stop(order_id="123", symbol="SPY", new_stop_price=500.0)
    assert res.success is False
    assert "does not support" in (res.error_message or "")


@pytest.mark.asyncio
async def test_paper_broker_modify_stop(config: AppConfig):
    broker = PaperBroker(config)
    assert broker.supports_order_modification is True
    res = await broker.modify_order_stop(order_id="P-123", symbol="SPY", new_stop_price=505.5)
    assert res.success is True
    assert res.order_id == "P-123"


@pytest.mark.asyncio
async def test_alpaca_broker_modify_stop_success(config: AppConfig):
    mock_client = MagicMock()
    mock_res = MagicMock()
    mock_res.id = "new-alpaca-order-id"
    mock_res.model_dump.return_value = {"id": "new-alpaca-order-id"}
    mock_client.replace_order_by_id.return_value = mock_res

    broker = AlpacaBroker(config, client=mock_client)
    broker._connected = True

    res = await broker.modify_order_stop(order_id="stop-order-1", symbol="SPY", new_stop_price=505.0)
    assert res.success is True
    assert res.order_id == "new-alpaca-order-id"
    mock_client.replace_order_by_id.assert_called_once()


@pytest.mark.asyncio
async def test_alpaca_broker_modify_stop_api_error(config: AppConfig):
    mock_client = MagicMock()
    mock_client.replace_order_by_id.side_effect = APIError("Order is already filled")

    broker = AlpacaBroker(config, client=mock_client)
    broker._connected = True

    res = await broker.modify_order_stop(order_id="stop-order-1", symbol="SPY", new_stop_price=505.0)
    assert res.success is False
    assert "Order is already filled" in (res.error_message or "")


@pytest.mark.asyncio
async def test_tradovate_broker_modify_stop_degraded_when_offline(config: AppConfig):
    broker = TradovateBroker(config)
    # No active connection
    res = await broker.modify_order_stop(order_id="12345", symbol="/MES", new_stop_price=5850.0)
    assert res.success is False
    assert "Not connected to Tradovate" in (res.error_message or "")


@pytest.mark.asyncio
async def test_copilot_syncs_broker_stop_on_ratchet(config: AppConfig, temp_db):
    copilot = TradingCopilot(config=config, db=temp_db)
    copilot.notifier = MagicMock()
    copilot.notifier.send_trailing_stop_alert = AsyncMock(return_value=True)

    # Mock broker that supports order modification
    mock_broker = MagicMock()
    mock_broker.supports_order_modification = True
    mock_broker.modify_order_stop = AsyncMock(return_value=OrderResult(success=True, order_id="MOCK-STOP-1"))
    copilot.broker = mock_broker

    # Mock market data fetcher with high market price to trigger trailing stop
    mock_fetcher = MagicMock()
    mock_fetcher.fetch_latest_price.return_value = 5950.0  # Big favorable move
    copilot.data_fetcher = mock_fetcher

    active_positions = [
        {
            "id": 1,
            "contract": "/MES",
            "direction": "LONG",
            "entry_price": 5800.0,
            "stop_loss": 5750.0,
            "take_profit": 5950.0,
            "risk_dollars": 250.0,
            "quantity": 1.0,
            "broker_order_id": "ORD-BROKER-99",
        }
    ]

    with patch.object(temp_db, "update_position_stop", new=AsyncMock()) as mock_update_db:
        updated = await copilot.manage_trailing_stops(active_positions)
        assert updated >= 1
        mock_update_db.assert_called()
        mock_broker.modify_order_stop.assert_called_once()
        call_kwargs = mock_broker.modify_order_stop.call_args[1]
        assert call_kwargs["order_id"] == "ORD-BROKER-99"
        assert call_kwargs["symbol"] == "/MES"
        assert call_kwargs["new_stop_price"] > 5750.0
        copilot.notifier.send_trailing_stop_alert.assert_called()
        alert_kwargs = copilot.notifier.send_trailing_stop_alert.call_args[1]
        assert alert_kwargs["broker_synced"] is True
