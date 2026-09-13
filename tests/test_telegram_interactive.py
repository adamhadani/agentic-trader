from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_trader.config import load_config
from agentic_trader.constants import Direction, ExitReason, SignalStatus
from agentic_trader.main import FuturesCopilot
from agentic_trader.notifier.telegram_bot import TelegramNotifier
from agentic_trader.storage.db import SignalDatabase


@pytest.fixture
def temp_db(tmp_path):
    db_file = str(tmp_path / "test_telegram.db")
    db = SignalDatabase(db_file)
    return db


@pytest.mark.asyncio
async def test_db_closed_positions_stats(temp_db):
    await temp_db.init_db()

    # Empty stats
    empty_stats = await temp_db.get_closed_positions_stats()
    assert empty_stats["total_trades"] == 0
    assert empty_stats["win_rate"] == 0.0
    assert empty_stats["total_pnl"] == 0.0
    assert empty_stats["profit_factor"] == 0.0

    # Insert 2 winning trades and 1 losing trade
    id1 = await temp_db.record_signal(
        contract="/MES",
        strategy="TREND_PULLBACK",
        direction=Direction.LONG,
        entry_price=5800.0,
        stop_loss=5770.0,
        take_profit=5860.0,
        risk_dollars=150.0,
        status=SignalStatus.EXECUTED,
    )
    await temp_db.close_position(
        signal_id=id1,
        exit_price=5860.0,
        exit_reason=ExitReason.TAKE_PROFIT,
        realized_pnl=300.0,
        status=SignalStatus.CLOSED_WIN,
    )

    id2 = await temp_db.record_signal(
        contract="/MNQ",
        strategy="SQUEEZE_BREAKOUT",
        direction=Direction.LONG,
        entry_price=20000.0,
        stop_loss=19900.0,
        take_profit=20200.0,
        risk_dollars=200.0,
        status=SignalStatus.EXECUTED,
    )
    await temp_db.close_position(
        signal_id=id2,
        exit_price=20150.0,
        exit_reason=ExitReason.TAKE_PROFIT,
        realized_pnl=300.0,
        status=SignalStatus.CLOSED_WIN,
    )

    id3 = await temp_db.record_signal(
        contract="SPY",
        strategy="TREND_PULLBACK",
        direction=Direction.SHORT,
        entry_price=580.0,
        stop_loss=585.0,
        take_profit=570.0,
        risk_dollars=250.0,
        status=SignalStatus.EXECUTED,
    )
    await temp_db.close_position(
        signal_id=id3,
        exit_price=585.0,
        exit_reason=ExitReason.STOP_LOSS,
        realized_pnl=-250.0,
        status=SignalStatus.CLOSED_LOSS,
    )

    stats = await temp_db.get_closed_positions_stats()
    assert stats["total_trades"] == 3
    assert stats["wins"] == 2
    assert stats["losses"] == 1
    assert stats["win_rate"] == 66.67
    assert stats["total_pnl"] == 350.0
    assert stats["gross_profit"] == 600.0
    assert stats["gross_loss"] == 250.0
    assert stats["profit_factor"] == 2.40
    assert len(stats["trades"]) == 3


@pytest.mark.asyncio
async def test_copilot_performance_and_regime_html(temp_db):
    config = load_config()
    config.db_path = temp_db.db_path
    copilot = FuturesCopilot(config)
    await copilot.db.init_db()

    # Insert a closed trade
    sig_id = await copilot.db.record_signal(
        contract="/MES",
        strategy="TREND_PULLBACK",
        direction=Direction.LONG,
        entry_price=5800.0,
        stop_loss=5770.0,
        take_profit=5860.0,
        risk_dollars=150.0,
        status=SignalStatus.EXECUTED,
    )
    await copilot.db.close_position(
        signal_id=sig_id,
        exit_price=5860.0,
        exit_reason=ExitReason.TAKE_PROFIT,
        realized_pnl=300.0,
        status=SignalStatus.CLOSED_WIN,
    )

    # Test get_performance_summary_html
    perf_html = await copilot.get_performance_summary_html()
    assert "CASH-PLUS COPILOT: PERFORMANCE ATTRIBUTION" in perf_html
    assert "+$300.00" in perf_html
    assert "100.0%" in perf_html
    assert "/MES" in perf_html

    # Test get_regime_summary_html
    regime_html = await copilot.get_regime_summary_html()
    assert "MARKET VOLATILITY & MACRO REGIME" in regime_html
    assert "VIX Level" in regime_html
    assert "Squeeze Breakouts" in regime_html


@pytest.mark.asyncio
async def test_telegram_commands_and_callbacks(temp_db):
    mock_perf = AsyncMock(return_value="<b>Performance: +$500.00</b>")
    mock_regime = AsyncMock(return_value="<b>Regime: Normal</b>")
    mock_backtest = AsyncMock(return_value="<b>Backtest: +12.5% Return</b>")
    mock_scan = AsyncMock(return_value="<b>Scan: 0 setups</b>")
    mock_positions = AsyncMock(return_value="<b>Positions: None</b>")

    notifier = TelegramNotifier(
        bot_token="test_token",
        chat_id="123456",
        db=temp_db,
        perf_provider=mock_perf,
        regime_provider=mock_regime,
        backtest_runner=mock_backtest,
        scan_runner=mock_scan,
        positions_provider=mock_positions,
    )

    # Mock Update object
    mock_update = MagicMock()
    mock_update.effective_chat.id = "123456"
    mock_update.message = MagicMock()
    mock_update.message.reply_text = AsyncMock()

    mock_context = MagicMock()

    # Test /help command
    await notifier.handle_help_command(mock_update, mock_context)
    mock_update.message.reply_text.assert_called()
    help_call_args = mock_update.message.reply_text.call_args[0][0]
    assert "/perf" in help_call_args
    assert "/regime" in help_call_args
    assert "/backtest" in help_call_args

    # Test /perf command
    mock_update.message.reply_text.reset_mock()
    await notifier.handle_perf_command(mock_update, mock_context)
    mock_perf.assert_called_once()
    mock_update.message.reply_text.assert_called_with("<b>Performance: +$500.00</b>", parse_mode="HTML")

    # Test /regime command
    mock_update.message.reply_text.reset_mock()
    await notifier.handle_regime_command(mock_update, mock_context)
    mock_regime.assert_called_once()
    mock_update.message.reply_text.assert_called_with("<b>Regime: Normal</b>", parse_mode="HTML")

    # Test /backtest command
    mock_update.message.reply_text.reset_mock()
    mock_context.args = ["AAPL", "2y"]
    await notifier.handle_backtest_command(mock_update, mock_context)
    mock_backtest.assert_called_once_with("AAPL", "2y")

    # Test inline button callbacks
    query_mock = MagicMock()
    query_mock.answer = AsyncMock()
    query_mock.message = MagicMock()
    query_mock.message.reply_text = AsyncMock()

    cb_update = MagicMock()
    cb_update.callback_query = query_mock

    # Button cmd_perf
    query_mock.data = "cmd_perf"
    await notifier.handle_button_callback(cb_update, mock_context)
    query_mock.answer.assert_called()
    query_mock.message.reply_text.assert_called_with("<b>Performance: +$500.00</b>", parse_mode="HTML")

    # Button cmd_regime
    query_mock.message.reply_text.reset_mock()
    query_mock.data = "cmd_regime"
    await notifier.handle_button_callback(cb_update, mock_context)
    query_mock.answer.assert_called()
    query_mock.message.reply_text.assert_called_with("<b>Regime: Normal</b>", parse_mode="HTML")

    # Button cmd_scan
    query_mock.message.reply_text.reset_mock()
    query_mock.data = "cmd_scan"
    await notifier.handle_button_callback(cb_update, mock_context)
    query_mock.answer.assert_called_with("Running quantitative scan...")
    query_mock.message.reply_text.assert_called_with("<b>Scan: 0 setups</b>", parse_mode="HTML")
