from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_trader.agent.evaluator import LLMTradeEvaluation
from agentic_trader.config import load_config
from agentic_trader.constants import AssetClass, Direction, ExitReason, SignalStatus
from agentic_trader.main import FuturesCopilot
from agentic_trader.notifier.telegram_bot import (
    TelegramNotifier,
    format_alert_card,
    format_exit_card,
    format_terminal_card,
)


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
    query_mock.edit_message_reply_markup = AsyncMock()
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

    # Button exec_42_2 (Execution with tiered quantity override)
    mock_exec = AsyncMock(return_value=(True, "<b>Order Executed: 2x /MES</b>"))
    notifier.execute_handler = mock_exec
    query_mock.message.reply_text.reset_mock()
    query_mock.data = "exec_42_2"
    await notifier.handle_button_callback(cb_update, mock_context)
    query_mock.answer.assert_called_with("Submitting order for 2 units to broker...")
    mock_exec.assert_called_once_with(42, quantity=2.0)
    query_mock.message.reply_text.assert_called_with("<b>Order Executed: 2x /MES</b>", parse_mode="HTML")


def test_format_alert_card_with_sizing_tiers():
    test_eval = LLMTradeEvaluation(
        approved=True,
        rejection_reason=None,
        contract="/MES",
        direction=Direction.LONG,
        entry_price=5812.50,
        stop_loss=5769.75,
        take_profit=5898.00,
        stop_distance_points=42.75,
        target_distance_points=85.50,
        risk_reward_ratio=2.0,
        risk_dollars=213.75,
        reward_dollars=427.50,
        notional_value=29062.50,
        effective_leverage=0.29,
        macro_clearance=True,
        thesis_summary="Bullish pullback",
        quantity=1.0,
        sizing_tiers=[
            {
                "tier_id": "half",
                "label": "Half (1x)",
                "quantity": 1.0,
                "risk_dollars": 213.75,
                "reward_dollars": 427.50,
                "notional_dollars": 29062.50,
                "effective_leverage": 0.29,
                "is_default": True,
            },
            {
                "tier_id": "base",
                "label": "Base (2x)",
                "quantity": 2.0,
                "risk_dollars": 427.50,
                "reward_dollars": 855.00,
                "notional_dollars": 58125.00,
                "effective_leverage": 0.58,
                "is_default": False,
            },
        ],
        gating_reasons=["Capped by $60,000 portfolio notional limit"],
    )

    card_html = format_alert_card(test_eval, strategy="TREND_PULLBACK")
    assert "Position Sizing Tiers:" in card_html
    assert "Half (1x)" in card_html
    assert "Base (2x)" in card_html
    assert "Risk: -$213.75" in card_html
    assert "Reward: +$427.50" in card_html
    assert "Risk: -$427.50" in card_html
    assert "Reward: +$855.00" in card_html
    assert "Capped by $60,000 portfolio notional limit" in card_html

    card_term = format_terminal_card(test_eval, strategy="TREND_PULLBACK")
    assert "Position Sizing Tiers:" in card_term
    assert "Half (1x)" in card_term
    assert "Base (2x)" in card_term
    assert "Risk: -$213.75" in card_term
    assert "Reward: +$427.50" in card_term
    assert "Risk: -$427.50" in card_term
    assert "Reward: +$855.00" in card_term


def test_format_exit_card_equity_and_futures():
    equity_exit = format_exit_card(
        contract="SPY",
        direction="LONG",
        exit_reason="TAKE_PROFIT",
        entry_price=761.77,
        exit_price=772.05,
        realized_pnl=400.92,
        strategy="TREND_PULLBACK",
        quantity=39.0,
        asset_class=AssetClass.EQUITY,
    )
    assert "39 shares SPY" in equity_exit
    assert "TARGET REACHED" in equity_exit or "TAKE PROFIT REACHED" in equity_exit
    assert "+$400.92" in equity_exit

    futures_exit = format_exit_card(
        contract="/MES",
        direction="SHORT",
        exit_reason="STOP_LOSS",
        entry_price=5800.0,
        exit_price=5830.0,
        realized_pnl=-150.0,
        strategy="SQUEEZE_BREAKOUT",
        quantity=2.0,
        asset_class=AssetClass.FUTURES,
    )
    assert "2x /MES" in futures_exit
    assert "STOP LOSS TRIGGERED" in futures_exit
    assert "-$150.00" in futures_exit
