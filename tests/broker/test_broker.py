from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.broker import (
    AlpacaBroker,
    OrderRequest,
    OrderResult,
    PaperBroker,
    ReconciliationEvent,
    TradovateBroker,
    create_broker,
)
from agentic_trader.config import AppConfig, load_config
from agentic_trader.constants import (
    AssetClass,
    Direction,
    ExecutionMode,
    ExitReason,
    OrderSide,
    OrderType,
    SignalStatus,
)
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

    config = load_config().model_copy(update={"db_path": str(db_file), "execution_mode": "paper"})
    # Admission-path test of a legacy tap: tap-time card freshness is exercised in test_card_freshness_tap.py.
    config.execution.card_freshness.enabled = False
    copilot = TradingCopilot(config, db=db)
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

    reply = await copilot.execute_signal_by_id(sig_id)
    success, message = reply.ok, reply.text
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
    reply2 = await copilot.execute_signal_by_id(sig_id)
    success2, message2 = reply2.ok, reply2.text
    assert success2 is False
    assert "only PENDING signals can be executed" in message2


@pytest.mark.asyncio
async def test_copilot_execute_signal_exposure_limit(tmp_path):
    db_file = tmp_path / "test_limit.db"
    db = SignalDatabase(str(db_file))
    await db.init_db()

    config = load_config().model_copy(update={"db_path": str(db_file), "execution_mode": "paper"})
    # Admission-path test of a legacy tap: tap-time card freshness is exercised in test_card_freshness_tap.py.
    config.execution.card_freshness.enabled = False
    # Set maximum notional exposure lower than signal notional
    config.portfolio.max_notional_exposure = 20000.0

    copilot = TradingCopilot(config, db=db)
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

    reply = await copilot.execute_signal_by_id(sig_id)
    success, message = reply.ok, reply.text
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

    mock_client = MagicMock()

    # Mock account
    mock_account = MagicMock()
    mock_account.id = "acct-test-1"
    mock_account.account_number = "PA12345"
    mock_account.status = "ACTIVE"
    mock_account.cash = 100000.0
    mock_account.buying_power = 200000.0
    mock_account.portfolio_value = 100000.0
    mock_client.get_account.return_value = mock_account

    # Mock order submission
    mock_order = MagicMock()
    mock_order.id = "alp-order-101"
    mock_order.status = "new"
    mock_order.filled_avg_price = 150.50
    mock_leg_sl = SimpleNamespace(id="alp-sl-101", order_type=None, type="stop")
    mock_leg_tp = SimpleNamespace(id="alp-tp-101", order_type=None, type="limit")
    mock_order.legs = [mock_leg_sl, mock_leg_tp]
    mock_order.model_dump.return_value = {"id": "alp-order-101"}
    mock_client.submit_order.return_value = mock_order

    # Mock positions
    mock_pos = MagicMock()
    mock_pos.symbol = "AAPL"
    mock_pos.asset_class = "us_equity"
    mock_pos.side = "long"
    mock_pos.qty = 10.0
    mock_pos.avg_entry_price = 150.50
    mock_pos.current_price = 155.00
    mock_pos.unrealized_pl = 45.00
    mock_client.get_all_positions.return_value = [mock_pos]

    # Mock close position
    mock_close = MagicMock()
    mock_close.id = "alp-close-101"
    mock_close.model_dump.return_value = {"id": "alp-close-101"}
    mock_client.close_position.return_value = mock_close

    broker = AlpacaBroker(config, client=mock_client)

    # Check connection
    connected = await broker.connect()
    assert connected is True
    mock_client.get_account.assert_called_once()

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
    mock_client.submit_order.assert_called_once()

    # Get positions
    positions = await broker.get_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "AAPL"
    assert positions[0].quantity == 10.0
    assert positions[0].unrealized_pnl == 45.00

    # Close uses bracket-aware submission; simulation prices cannot become broker fills.
    mock_pos.qty_available = "10"
    mock_client.get_open_position.return_value = mock_pos
    mock_client.get_clock.return_value.is_open = True
    mock_client.get_orders.return_value = []
    mock_client.submit_order.return_value = {"id": "alp-close-101", "status": "accepted", "filled_qty": "0"}
    close_res = await broker.close_position(symbol="AAPL", exit_reason="TAKE_PROFIT", exit_price=160.00)
    assert close_res.success is True
    assert close_res.order_id == "alp-close-101"
    assert close_res.fill_price is None
    mock_client.close_position.assert_not_called()

    await broker.disconnect()


@pytest.mark.asyncio
async def test_paper_broker_reconcile_positions():
    config = load_config()
    config.execution_mode = "paper"
    mock_fetcher = MagicMock()
    # Price triggers take profit on /MES Long (target is 5900, price is 5910)
    mock_fetcher.fetch_latest_price.return_value = 5910.00

    broker = PaperBroker(config=config, data_fetcher=mock_fetcher)

    active_positions = [
        {
            "id": 1,
            "contract": "/MES",
            "direction": "LONG",
            "entry_price": 5800.0,
            "stop_loss": 5750.0,
            "take_profit": 5900.0,
            "strategy": "TREND_PULLBACK",
        },
        {
            "id": 2,
            "contract": "/MNQ",
            "direction": "LONG",
            "entry_price": 19000.0,
            "stop_loss": 18800.0,
            "take_profit": 19400.0,
            "strategy": "SQUEEZE_BREAKOUT",
        },
    ]

    # For /MNQ, set price between stop and target
    def price_side_effect(ticker):
        if "MES" in ticker:
            return 5910.0  # triggers TP
        return 19100.0  # within range, no exit

    mock_fetcher.fetch_latest_price.side_effect = price_side_effect

    events = await broker.reconcile_positions(active_positions)
    assert len(events) == 1
    assert events[0].signal_id == 1
    assert events[0].contract == "/MES"
    assert events[0].exit_reason == ExitReason.TAKE_PROFIT
    assert events[0].exit_price == 5910.0
    # Realized PnL: (5910 - 5800) * 5.0 = 550.0
    assert events[0].realized_pnl == 550.0


@pytest.mark.asyncio
async def test_alpaca_broker_reconcile_positions():
    config = AppConfig(
        execution_mode=ExecutionMode.ALPACA,
        alpaca_api_key="mock_key",
        alpaca_api_secret="mock_secret",
        alpaca_paper=True,
    )
    mock_client = MagicMock()

    # Open positions at Alpaca: only MSFT is still open, AAPL is closed!
    mock_pos_msft = MagicMock(symbol="MSFT")
    mock_client.get_all_positions.return_value = [mock_pos_msft]

    # Closed orders for AAPL: stop-loss order filled at 144.50
    mock_closed_order = SimpleNamespace()
    mock_closed_order.id = "alp-sl-filled-99"
    mock_closed_order.status = "filled"
    mock_closed_order.order_type = "stop"
    mock_closed_order.filled_avg_price = 144.50
    mock_closed_order.filled_at = datetime.now(UTC)
    mock_closed_order.filled_qty = "1"
    mock_closed_order.symbol = "AAPL"
    mock_closed_order.side = "sell"
    entry = {
        "id": "entry-aapl",
        "symbol": "AAPL",
        "side": "buy",
        "status": "filled",
        "filled_qty": "1",
        "filled_avg_price": "150",
        "filled_at": datetime(2026, 9, 14, tzinfo=UTC),
        "legs": [mock_closed_order],
    }
    mock_client.get_order_by_id.return_value = entry
    mock_client.get_orders.return_value = [mock_closed_order]

    broker = AlpacaBroker(config, client=mock_client)

    active_positions = [
        {
            "id": 10,
            "broker_order_id": "entry-aapl",
            "quantity": 1.0,
            "contract": "AAPL",
            "direction": "LONG",
            "entry_price": 150.0,
            "stop_loss": 145.0,
            "take_profit": 160.0,
            "strategy": "TREND_PULLBACK",
        },
        {
            "id": 11,
            "broker_order_id": "entry-msft",
            "quantity": 1.0,
            "contract": "MSFT",
            "direction": "LONG",
            "entry_price": 400.0,
            "stop_loss": 390.0,
            "take_profit": 420.0,
            "strategy": "SQUEEZE_BREAKOUT",
        },
    ]

    events = await broker.reconcile_positions(active_positions)
    # MSFT is still open, only AAPL should produce an exit event
    assert len(events) == 1
    assert events[0].signal_id == 10
    assert events[0].symbol == "AAPL"
    assert events[0].exit_reason == ExitReason.STOP_LOSS
    assert events[0].exit_price == 144.50
    assert events[0].broker_order_id == "alp-sl-filled-99"
    # Realized PnL: 144.50 - 150.0 = -5.50
    assert events[0].realized_pnl == pytest.approx(-5.50)


@pytest.mark.asyncio
async def test_copilot_monitor_positions_via_reconciliation(tmp_path):
    db_path = str(tmp_path / "test_reconcile.db")
    config = AppConfig(execution_mode="paper", db_path=db_path)
    copilot = TradingCopilot(config)
    await copilot.db.init_db()

    # Record active position
    sig_id = await copilot.db.record_signal(
        contract="/MES",
        strategy="TREND_PULLBACK",
        direction="LONG",
        entry_price=5800.0,
        stop_loss=5750.0,
        take_profit=5900.0,
        risk_dollars=250.0,
        status="EXECUTED",
    )

    # Mock broker.reconcile_positions returning an exit event
    mock_event = ReconciliationEvent(
        signal_id=sig_id,
        contract="/MES",
        direction="LONG",
        exit_price=5905.0,
        exit_reason=ExitReason.TAKE_PROFIT,
        realized_pnl=525.0,
        broker_order_id="SIM-EXIT-123",
    )
    copilot.broker.reconcile_positions = MagicMock(return_value=[mock_event])  # type: ignore[method-assign]

    # Make it awaitable
    async def mock_reconcile(active):
        return [mock_event]

    copilot.broker.reconcile_positions = mock_reconcile  # type: ignore[method-assign]

    closed_count = await copilot.monitor_positions()
    assert closed_count == 1

    # Verify signal status in database is now CLOSED_WIN
    sig = await copilot.db.get_signal_by_id(sig_id)
    assert sig is not None
    assert sig["status"] == "CLOSED_WIN"
    assert sig["exit_price"] == 5905.0
    assert sig["realized_pnl"] == 525.0


@pytest.mark.asyncio
async def test_copilot_execute_equity_signal_with_shares(tmp_path):
    db_path = str(tmp_path / "test_equity_exec.db")
    config = load_config()
    config.execution_mode = "paper"
    config.db_path = db_path
    # Admission-path test of a legacy tap: tap-time card freshness is exercised in test_card_freshness_tap.py.
    config.execution.card_freshness.enabled = False

    copilot = TradingCopilot(config)
    await copilot.db.init_db()

    # Record equity signal with 35 shares
    sig_id = await copilot.db.record_signal(
        contract="SPY",
        strategy="TREND_PULLBACK",
        direction="LONG",
        entry_price=500.0,
        stop_loss=493.0,
        take_profit=514.0,
        risk_dollars=245.0,
        reward_dollars=490.0,
        notional_value=17500.0,
        status=SignalStatus.PENDING,
        asset_class=AssetClass.EQUITY,
        quantity=35.0,
    )

    reply = await copilot.execute_signal_by_id(sig_id)
    success, msg = reply.ok, reply.text
    assert success is True
    assert "Executed" in msg or "Order ID" in msg

    # Verify signal in database is EXECUTED and quantity preserved
    sig = await copilot.db.get_signal_by_id(sig_id)
    assert sig is not None
    assert sig["status"] == SignalStatus.EXECUTED
    assert sig["quantity"] == 35.0
    assert sig["asset_class"] == AssetClass.EQUITY

    # Verify position exists in broker
    positions = await copilot.broker.get_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "SPY"
    assert positions[0].quantity == 35.0
