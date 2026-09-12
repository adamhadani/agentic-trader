from unittest.mock import MagicMock

import pytest

from agentic_trader.broker import (
    OrderRequest,
    OrderResult,
    PaperBroker,
    TradovateBroker,
    create_broker,
)
from agentic_trader.config import AppConfig
from agentic_trader.main import FuturesCopilot
from agentic_trader.storage.db import SignalDatabase


def test_order_request_and_result_models():
    req = OrderRequest(
        signal_id=1,
        contract="/MES",
        ticker="MES=F",
        direction="LONG",
        entry_price=5800.0,
        stop_loss=5750.0,
        take_profit=5900.0,
        quantity=1,
    )
    assert req.contract == "/MES"
    assert req.direction == "LONG"
    assert req.quantity == 1

    res = OrderResult(
        success=True,
        order_id="SIM-12345",
        fill_price=5801.25,
        bracket_orders={"stop_loss_id": "SIM-SL-1", "take_profit_id": "SIM-TP-1"},
    )
    assert res.success is True
    assert res.order_id == "SIM-12345"
    assert res.bracket_orders["stop_loss_id"] == "SIM-SL-1"


@pytest.mark.asyncio
async def test_paper_broker_flow():
    config = AppConfig(execution_mode="paper")
    mock_fetcher = MagicMock()
    mock_fetcher.fetch_latest_price.return_value = 5820.50

    broker = PaperBroker(config=config, data_fetcher=mock_fetcher)
    connected = await broker.connect()
    assert connected is True

    req = OrderRequest(
        signal_id=10,
        contract="/MES",
        ticker="MES=F",
        direction="LONG",
        entry_price=5815.00,
        stop_loss=5770.00,
        take_profit=5900.00,
        quantity=1,
    )

    result = await broker.submit_entry_order(req)
    assert result.success is True
    assert result.fill_price == 5820.50
    assert result.order_id is not None
    assert result.order_id.startswith("SIM-")
    assert "stop_loss_id" in result.bracket_orders
    assert "take_profit_id" in result.bracket_orders

    positions = await broker.get_positions()
    assert len(positions) == 1
    assert positions[0].contract == "/MES"
    assert positions[0].entry_price == 5820.50

    # Close position
    close_result = await broker.close_position(contract="/MES", exit_reason="TAKE_PROFIT", exit_price=5900.00)
    assert close_result.success is True
    assert close_result.fill_price == 5900.00
    assert close_result.order_id is not None
    assert "SIM-EXIT-" in close_result.order_id

    positions_after = await broker.get_positions()
    assert len(positions_after) == 0

    await broker.disconnect()


@pytest.mark.asyncio
async def test_create_broker_factory():
    config_paper = AppConfig(execution_mode="paper")
    broker_paper = create_broker(config_paper)
    assert isinstance(broker_paper, PaperBroker)

    config_tradovate = AppConfig(
        execution_mode="tradovate",
        tradovate_username="test_user",
        tradovate_password="test_pass",
        tradovate_api_key="key",
        tradovate_api_secret="secret",
    )
    broker_tradovate = create_broker(config_tradovate)
    assert isinstance(broker_tradovate, TradovateBroker)


def test_tradovate_missing_credentials():
    config = AppConfig(execution_mode="tradovate")
    broker = TradovateBroker(config)
    with pytest.raises(ValueError, match="Missing required Tradovate credentials"):
        broker._validate_credentials()


@pytest.mark.asyncio
async def test_copilot_execute_signal(tmp_path):
    db_file = tmp_path / "test_exec.db"
    db = SignalDatabase(str(db_file))
    await db.init_db()

    config = AppConfig(
        db_path=str(db_file),
        execution_mode="paper",
    )
    copilot = FuturesCopilot(config)
    copilot.data_fetcher = MagicMock()
    copilot.data_fetcher.fetch_latest_price.return_value = 5812.50

    sig_id = await db.record_signal(
        contract="/MES",
        strategy="TREND_PULLBACK",
        direction="LONG",
        entry_price=5812.50,
        stop_loss=5769.75,
        take_profit=5898.00,
        risk_dollars=213.75,
        reward_dollars=427.50,
        notional_value=29062.50,
        status="PENDING",
    )

    success, message = await copilot.execute_signal_by_id(sig_id)
    assert success is True
    assert "ORDER EXECUTED" in message
    assert "SIM-" in message

    # Verify SQLite status was updated to EXECUTED and broker_order_id was stored
    stored_sig = await db.get_signal_by_id(sig_id)
    assert stored_sig is not None
    assert stored_sig["status"] == "EXECUTED"
    assert stored_sig["broker_order_id"] is not None
    assert stored_sig["broker_order_id"].startswith("SIM-")

    # Second execution attempt should be rejected
    success2, message2 = await copilot.execute_signal_by_id(sig_id)
    assert success2 is False
    assert "only PENDING signals can be executed" in message2


@pytest.mark.asyncio
async def test_copilot_execute_signal_exposure_limit(tmp_path):
    db_file = tmp_path / "test_limit.db"
    db = SignalDatabase(str(db_file))
    await db.init_db()

    config = AppConfig(
        db_path=str(db_file),
        execution_mode="paper",
    )
    # Set maximum notional exposure lower than signal notional
    config.portfolio.max_notional_exposure = 20000.0

    copilot = FuturesCopilot(config)
    sig_id = await db.record_signal(
        contract="/MES",
        strategy="TREND_PULLBACK",
        direction="LONG",
        entry_price=5812.50,
        stop_loss=5769.75,
        take_profit=5898.00,
        risk_dollars=213.75,
        reward_dollars=427.50,
        notional_value=29062.50,
        status="PENDING",
    )

    success, message = await copilot.execute_signal_by_id(sig_id)
    assert success is False
    assert "Execution Rejected" in message
    assert "maximum portfolio notional ceiling" in message

    # Signal status must remain PENDING (not EXECUTED or SUBMITTING)
    stored = await db.get_signal_by_id(sig_id)
    assert stored is not None
    assert stored["status"] == "PENDING"
