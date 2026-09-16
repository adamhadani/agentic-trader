from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.broker.alpaca import AlpacaBroker
from agentic_trader.broker.base import BaseBroker
from agentic_trader.broker.paper import PaperBroker
from agentic_trader.broker.redundant import RedundantBroker
from agentic_trader.broker.tradovate import TradovateBroker
from agentic_trader.config import AppConfig
from agentic_trader.constants import ExitReason, SignalStatus
from agentic_trader.presentation.formatters import (
    PanicReportView,
    TelegramHtmlFormatter,
    TerminalFormatter,
)
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.telemetry.collector import MetricsCollector


@pytest.fixture
def test_db(tmp_path):
    db_path = str(tmp_path / "test_panic.db")
    db = SignalDatabase(db_path)
    yield db


@pytest.fixture
def copilot_fixture(test_db):
    config = AppConfig()
    config.portfolio.cash = 100_000.0
    config.portfolio.max_notional_exposure = 200_000.0
    config.execution_mode = "paper"
    metrics = MetricsCollector()
    copilot = TradingCopilot(config=config, db=test_db)
    copilot.metrics = metrics
    copilot.notifier.send_message = AsyncMock(return_value=True)  # type: ignore[method-assign]
    return copilot


@pytest.mark.asyncio
async def test_database_system_state(test_db):
    # Test initial none
    val = await test_db.get_state("non_existent_key")
    assert val is None

    # Test set and get
    await test_db.set_state("trading_halted", "true")
    val = await test_db.get_state("trading_halted")
    assert val == "true"

    # Test update existing
    await test_db.set_state("trading_halted", "false")
    val = await test_db.get_state("trading_halted")
    assert val == "false"


@pytest.mark.asyncio
async def test_broker_cancel_all_orders():
    # 1. BaseBroker default
    class DummyBroker(BaseBroker):
        async def connect(self):
            return True

        async def disconnect(self):
            pass

        async def submit_entry_order(self, request):
            return None  # type: ignore

        async def close_position(self, **kwargs):
            return None  # type: ignore

        async def get_positions(self):
            return []

    base = DummyBroker()
    assert await base.cancel_all_orders() == 0

    cfg = AppConfig()

    # 2. PaperBroker
    paper = PaperBroker(config=cfg)
    assert await paper.cancel_all_orders() == 0

    # 3. AlpacaBroker
    alpaca = AlpacaBroker(config=cfg)
    alpaca.client = MagicMock()
    cancel_mock = MagicMock()
    cancel_mock.status = 200
    alpaca.client.cancel_orders.return_value = [cancel_mock, cancel_mock]
    assert await alpaca.cancel_all_orders() == 2
    alpaca.client.cancel_orders.assert_called_once()

    # 4. TradovateBroker
    tradovate = TradovateBroker(config=cfg)
    tradovate._access_token = "fake-token"
    mock_client = MagicMock()
    list_resp = MagicMock()
    list_resp.status_code = 200
    list_resp.json.return_value = [
        {"id": 101, "ordStatus": "Working"},
        {"id": 102, "ordStatus": "Working"},
        {"id": 103, "ordStatus": "Filled"},
    ]
    cancel_resp = MagicMock()
    cancel_resp.status_code = 200
    mock_client.get = AsyncMock(return_value=list_resp)
    mock_client.post = AsyncMock(return_value=cancel_resp)
    tradovate.client = mock_client
    cancelled = await tradovate.cancel_all_orders()
    assert cancelled == 2

    # 5. RedundantBroker
    redundant = RedundantBroker(primary_broker=alpaca, fallback_broker=paper)
    alpaca.client.cancel_orders.return_value = [cancel_mock]
    total_cancelled = await redundant.cancel_all_orders()
    assert total_cancelled == 1


@pytest.mark.asyncio
async def test_emergency_panic_halt_and_resume_flow(copilot_fixture, test_db):
    copilot = copilot_fixture

    # 1. Seed two active executed positions in database
    sig1_id = await test_db.record_signal(
        contract="/MES",
        direction="LONG",
        strategy="Breakout",
        entry_price=5000.0,
        stop_loss=4950.0,
        take_profit=5100.0,
        risk_dollars=500.0,
        quantity=2.0,
        asset_class="FUTURES",
    )
    await test_db.update_signal_execution(
        signal_id=sig1_id,
        broker_order_id="ord-1",
        fill_price=5000.0,
        status=SignalStatus.EXECUTED,
        quantity=2.0,
        notional_value=50000.0,
        risk_dollars=500.0,
    )

    sig2_id = await test_db.record_signal(
        contract="SPY",
        direction="LONG",
        strategy="DipBuyer",
        entry_price=500.0,
        stop_loss=490.0,
        take_profit=520.0,
        risk_dollars=100.0,
        quantity=10.0,
        asset_class="EQUITY",
    )
    await test_db.update_signal_execution(
        signal_id=sig2_id,
        broker_order_id="ord-2",
        fill_price=500.0,
        status=SignalStatus.EXECUTED,
        quantity=10.0,
        notional_value=5000.0,
        risk_dollars=100.0,
    )

    active_before = await test_db.get_active_positions()
    assert len(active_before) == 2
    assert copilot.is_halted is False

    # 2. Trigger emergency panic halt
    with patch.object(
        copilot.data_fetcher, "fetch_latest_price", side_effect=lambda c: 5050.0 if "MES" in c else 510.0
    ):
        view = await copilot.emergency_panic_halt(reason="SEC circuit breaker triggered")

    assert isinstance(view, PanicReportView)
    assert view.is_halted is True
    assert view.liquidated_positions_count == 2
    assert view.halt_reason == "SEC circuit breaker triggered"
    assert copilot.is_halted is True
    assert copilot.halt_reason == "SEC circuit breaker triggered"

    # Verify positions in DB are closed with ExitReason.EMERGENCY_EXIT
    active_after = await test_db.get_active_positions()
    assert len(active_after) == 0
    exposure_after = await test_db.get_active_notional_exposure()
    assert exposure_after == 0.0

    sig1_row = await test_db.get_signal_by_id(sig1_id)
    assert sig1_row["status"] == SignalStatus.CLOSED_WIN
    assert sig1_row["exit_reason"] == ExitReason.EMERGENCY_EXIT

    sig2_row = await test_db.get_signal_by_id(sig2_id)
    assert sig2_row["status"] == SignalStatus.CLOSED_WIN
    assert sig2_row["exit_reason"] == ExitReason.EMERGENCY_EXIT

    # Verify state in database
    halted_state = await test_db.get_state("trading_halted")
    assert halted_state == "true"

    # Verify Prometheus metrics
    assert copilot.metrics._counters[("copilot_kill_switch_triggered_total", ())] == 1.0
    assert copilot.metrics._gauges[("copilot_trading_halted", ())] == 1.0

    # Verify Telegram notification was dispatched
    copilot.notifier.send_message.assert_called()

    # 3. Verify scan and execution are blocked while halted
    scan_res = await copilot.run_scan_summary_html()
    assert "Scan Blocked" in scan_res
    assert "Emergency trading halt active" in scan_res

    # Try executing a pending signal while halted
    sig3_id = await test_db.record_signal(
        contract="/MNQ",
        direction="SHORT",
        strategy="Breakout",
        entry_price=18000.0,
        stop_loss=18100.0,
        take_profit=17800.0,
        risk_dollars=200.0,
        quantity=1.0,
    )
    exec_success, exec_msg = await copilot.execute_signal_by_id(sig3_id)
    assert exec_success is False
    assert "Execution Blocked" in exec_msg

    # 4. Resume operations
    resume_res = await copilot.resume_trading()
    assert resume_res["success"] is True
    assert copilot.is_halted is False
    assert copilot.halt_reason is None

    # DB state reflects unhalted
    assert await test_db.get_state("trading_halted") == "false"
    assert copilot.metrics._gauges[("copilot_trading_halted", ())] == 0.0

    # Execution is now unblocked (will proceed to broker check)
    exec_success2, exec_msg2 = await copilot.execute_signal_by_id(sig3_id)
    assert exec_success2 is True  # Paper broker executes order successfully
    assert "Execution Blocked" not in exec_msg2


def test_panic_formatters():
    view = PanicReportView(
        cancelled_orders_count=3,
        liquidated_positions_count=2,
        total_realized_pnl=-150.50,
        is_halted=True,
        halt_reason="High volatility circuit breaker",
        closed_positions=[
            {"id": 1, "contract": "/MES", "direction": "LONG", "quantity": 1.0, "realized_pnl": 100.0},
            {"id": 2, "contract": "SPY", "direction": "SHORT", "quantity": 10.0, "realized_pnl": -250.50},
        ],
    )

    # Terminal report
    term = TerminalFormatter.format_panic_report(view)
    assert "EMERGENCY KILL SWITCH: LIQUIDATION & TRADING HALT REPORT" in term
    assert "Cancel Requests Accepted:     3" in term
    assert "Positions Liquidated: 2" in term
    assert "-$150.50" in term
    assert "/MES" in term

    # Telegram HTML report
    tg = TelegramHtmlFormatter.format_panic_html(view)
    assert "EMERGENCY KILL SWITCH ACTIVATED" in tg
    assert "TRADING HALT" in tg
    assert "High volatility circuit breaker" in tg
    assert "/resume" in tg


@pytest.mark.asyncio
async def test_telegram_panic_and_resume_handlers(copilot_fixture):
    notifier = copilot_fixture.notifier
    update = MagicMock()
    update.effective_chat.id = notifier.chat_id
    update.message = AsyncMock()

    # 1. /panic without confirm prompts warning card with inline keyboard
    context = MagicMock()
    context.args = []
    await notifier.handle_panic_command(update, context)
    update.message.reply_text.assert_called_once()
    args, kwargs = update.message.reply_text.call_args
    assert "EMERGENCY KILL SWITCH CONFIRMATION REQUIRED" in args[0]
    assert kwargs.get("reply_markup") is not None

    # 2. /panic confirm executes emergency panic directly
    update.message.reply_text.reset_mock()
    context.args = ["confirm"]
    await notifier.handle_panic_command(update, context)
    assert copilot_fixture.is_halted is True

    # 3. /resume executes unhalt
    update.message.reply_text.reset_mock()
    await notifier.handle_resume_command(update, context)
    assert copilot_fixture.is_halted is False

    # 4. Interactive button callbacks: panic_cancel and panic_confirm
    cb_update = MagicMock()
    cb_update.effective_chat.id = notifier.chat_id
    cb_query = MagicMock()
    cb_query.data = "panic_cancel"
    cb_query.message = AsyncMock()
    cb_query.answer = AsyncMock()
    cb_update.callback_query = cb_query

    await notifier.handle_button_callback(cb_update, context)
    cb_query.answer.assert_called_with("Panic cancelled")
    cb_query.message.reply_text.assert_called_once()
    assert "Emergency Kill Switch Cancelled" in cb_query.message.reply_text.call_args[0][0]

    # panic_confirm button
    cb_query.data = "panic_confirm"
    cb_query.message.reply_text.reset_mock()
    cb_query.answer.reset_mock()
    await notifier.handle_button_callback(cb_update, context)
    cb_query.answer.assert_called_with("Executing emergency kill switch...")
    assert copilot_fixture.is_halted is True


@pytest.mark.asyncio
async def test_telegram_bot_command_autocomplete(copilot_fixture):
    notifier = copilot_fixture.notifier
    notifier.app = MagicMock()
    notifier.app.bot = MagicMock()
    notifier.app.bot.set_my_commands = AsyncMock(return_value=True)

    with patch.object(notifier, "is_configured", return_value=True):
        res = await notifier.setup_bot_commands()
        assert res is True
        assert notifier.app.bot.set_my_commands.call_count >= 1
        commands_arg = notifier.app.bot.set_my_commands.call_args_list[0][0][0]
        cmd_names = [c.command for c in commands_arg]
        assert "panic" in cmd_names
        assert "resume" in cmd_names
        assert "status" in cmd_names
        assert "positions" in cmd_names
        assert "scan" in cmd_names
