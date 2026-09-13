from unittest.mock import MagicMock

import httpx
import pytest

from agentic_trader.broker import (
    AlpacaBroker,
    OrderRequest,
    OrderResult,
    PaperBroker,
    TradovateBroker,
    create_broker,
)
from agentic_trader.config import AppConfig
from agentic_trader.constants import (
    AssetClass,
    Direction,
    ExecutionMode,
    OrderSide,
    OrderType,
)
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


def test_multi_asset_order_request():
    # Equity bracket request
    equity_req = OrderRequest(
        symbol="AAPL",
        asset_class=AssetClass.EQUITY,
        side=OrderSide.BUY,
        quantity=10.0,
        entry_price=150.0,
        stop_loss=145.0,
        take_profit=160.0,
    )
    assert equity_req.symbol == "AAPL"
    assert equity_req.asset_class == AssetClass.EQUITY
    assert equity_req.quantity == 10.0
    assert equity_req.direction == Direction.LONG
    assert equity_req.is_bracket is True

    # Fractional crypto simple request
    crypto_req = OrderRequest(
        symbol="BTC/USD",
        asset_class=AssetClass.CRYPTO,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=0.05,
    )
    assert crypto_req.symbol == "BTC/USD"
    assert crypto_req.asset_class == AssetClass.CRYPTO
    assert crypto_req.quantity == 0.05
    assert crypto_req.is_bracket is False

    # Backward compatibility with futures contract alias
    futures_req = OrderRequest(
        contract="/MNQ",
        direction=Direction.SHORT,
        quantity=1,
    )
    assert futures_req.symbol == "/MNQ"
    assert futures_req.contract == "/MNQ"
    assert futures_req.asset_class == AssetClass.FUTURES
    assert futures_req.side == OrderSide.SELL


@pytest.mark.asyncio
async def test_paper_broker_multi_asset():
    config = AppConfig(execution_mode=ExecutionMode.PAPER)
    mock_fetcher = MagicMock()
    mock_fetcher.fetch_latest_price.return_value = 500.00

    broker = PaperBroker(config=config, data_fetcher=mock_fetcher)
    await broker.connect()

    # Buy 5 shares of SPY (Equity - multiplier is 1.0)
    req = OrderRequest(
        symbol="SPY",
        asset_class=AssetClass.EQUITY,
        side=OrderSide.BUY,
        quantity=5.0,
        entry_price=500.00,
    )
    result = await broker.submit_entry_order(req)
    assert result.success is True
    assert result.fill_price == 500.00
    assert result.order_id.startswith("SIM-")

    positions = await broker.get_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "SPY"
    assert positions[0].quantity == 5.0
    assert positions[0].asset_class == AssetClass.EQUITY

    # Close SPY position at $510
    close_res = await broker.close_position(symbol="SPY", exit_reason="TAKE_PROFIT", exit_price=510.00)
    assert close_res.success is True

    # Check balance updated: 5 shares * $10 profit = +$50
    bal = await broker.get_account_balance()
    assert bal["realized_pnl"] == 50.0
    assert bal["simulated_cash"] == 100050.0

    await broker.disconnect()


@pytest.mark.asyncio
async def test_create_broker_alpaca():
    config = AppConfig(
        execution_mode=ExecutionMode.ALPACA,
        alpaca_api_key="test_key",
        alpaca_api_secret="test_secret",
        alpaca_paper=True,
    )
    broker = create_broker(config)
    assert isinstance(broker, AlpacaBroker)
    assert broker.is_paper is True


def test_alpaca_missing_credentials():
    config = AppConfig(execution_mode=ExecutionMode.ALPACA)
    broker = AlpacaBroker(config)
    with pytest.raises(ValueError, match="Missing required Alpaca credentials"):
        broker._validate_credentials()


@pytest.mark.asyncio
async def test_alpaca_broker_bracket_order_and_positions():
    config = AppConfig(
        execution_mode=ExecutionMode.ALPACA,
        alpaca_api_key="mock_key",
        alpaca_api_secret="mock_secret",
        alpaca_paper=True,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/account":
            return httpx.Response(
                200,
                json={
                    "id": "acct-test-1",
                    "account_number": "PA12345",
                    "status": "ACTIVE",
                    "cash": "100000.0",
                    "buying_power": "200000.0",
                    "portfolio_value": "100000.0",
                },
            )
        elif path == "/v2/orders" and request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "id": "alp-order-101",
                    "status": "new",
                    "filled_avg_price": "150.50",
                    "legs": [
                        {"id": "alp-sl-101", "type": "stop"},
                        {"id": "alp-tp-101", "type": "limit"},
                    ],
                },
            )
        elif path == "/v2/positions/AAPL" and request.method == "DELETE":
            return httpx.Response(200, json={"id": "alp-close-101", "symbol": "AAPL"})
        elif path == "/v2/positions" and request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "symbol": "AAPL",
                        "asset_class": "us_equity",
                        "side": "long",
                        "qty": "10",
                        "avg_entry_price": "150.50",
                        "current_price": "155.00",
                        "unrealized_pl": "45.00",
                    }
                ],
            )
        return httpx.Response(404)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://paper-api.alpaca.markets")
    broker = AlpacaBroker(config, client=mock_client)

    # Check connection
    connected = await broker.connect()
    assert connected is True

    # Check account balance
    bal = await broker.get_account_balance()
    assert bal["cash"] == 100000.0
    assert bal["buying_power"] == 200000.0

    # Submit bracket entry order
    req = OrderRequest(
        symbol="AAPL",
        asset_class=AssetClass.EQUITY,
        side=OrderSide.BUY,
        quantity=10.0,
        entry_price=150.50,
        stop_loss=145.00,
        take_profit=160.00,
    )
    result = await broker.submit_entry_order(req)
    assert result.success is True
    assert result.order_id == "alp-order-101"
    assert result.fill_price == 150.50
    assert result.bracket_orders.get("stop_loss_id") == "alp-sl-101"
    assert result.bracket_orders.get("take_profit_id") == "alp-tp-101"

    # Get positions
    positions = await broker.get_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "AAPL"
    assert positions[0].quantity == 10.0
    assert positions[0].unrealized_pnl == 45.00

    # Close position
    close_res = await broker.close_position(symbol="AAPL", exit_reason="TAKE_PROFIT", exit_price=160.00)
    assert close_res.success is True
    assert close_res.order_id == "alp-close-101"

    await broker.disconnect()
