"""Operator /cancel: a preview with confirm/keep buttons; only the confirm button cancels."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from agentic_trader.cli.main import cli
from agentic_trader.execution.durable import WorkStatus
from agentic_trader.execution.lifetimes import RestingCancelResult
from agentic_trader.notifier.telegram_bot import TelegramNotifier


PREVIEW = RestingCancelResult(status="preview", reason=None, signal_id=42, symbol="SPY", work_status=None)
DONE = RestingCancelResult(status="submitted", reason=None, signal_id=42, symbol="SPY", work_status=WorkStatus.ACCEPTED)


def _command_update(chat_id=1):
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.message.reply_text = AsyncMock()
    return update


def _callback_update(data, chat_id=1):
    update = MagicMock()
    update.effective_chat.id = chat_id
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.message = AsyncMock()
    update.callback_query = query
    return update, query


@pytest.mark.asyncio
async def test_telegram_cancel_preview_and_buttons():
    handler = AsyncMock(return_value=PREVIEW)
    notifier = TelegramNotifier(None, "1", cancel_handler=handler)
    update = _command_update()
    await notifier.handle_cancel_command(update, MagicMock(args=["42"]))
    handler.assert_awaited_once_with(42, False)
    call = update.message.reply_text.await_args
    assert "SPY" in call.args[0] and "#42" in call.args[0]
    buttons = [button for row in call.kwargs["reply_markup"].inline_keyboard for button in row]
    assert [(b.text, b.callback_data) for b in buttons] == [
        ("Cancel entry", "cancelentry_confirm:42"),
        ("Keep", "cancelentry_keep"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [[], ["abc"], ["1", "2"]])
async def test_telegram_cancel_usage_never_calls_the_handler(args):
    handler = AsyncMock()
    notifier = TelegramNotifier(None, "1", cancel_handler=handler)
    update = _command_update()
    await notifier.handle_cancel_command(update, MagicMock(args=args))
    handler.assert_not_awaited()
    assert "/cancel" in update.message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_telegram_cancel_refusal_is_a_direct_reply_without_buttons():
    refused = RestingCancelResult(status="refused", reason="not_resting", signal_id=42, symbol="SPY", work_status=None)
    notifier = TelegramNotifier(None, "1", cancel_handler=AsyncMock(return_value=refused))
    update = _command_update()
    await notifier.handle_cancel_command(update, MagicMock(args=["42"]))
    call = update.message.reply_text.await_args
    assert "not_resting" in call.args[0]
    assert call.kwargs.get("reply_markup") is None


@pytest.mark.asyncio
async def test_telegram_cancel_requires_authorization():
    handler = AsyncMock(return_value=DONE)
    notifier = TelegramNotifier(None, "1", cancel_handler=handler)
    await notifier.handle_cancel_command(_command_update(chat_id=2), MagicMock(args=["42"]))
    update, query = _callback_update("cancelentry_confirm:42", chat_id=2)
    await notifier.handle_button_callback(update, MagicMock())
    handler.assert_not_awaited()
    query.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_telegram_confirm_callback_runs_cancel():
    handler = AsyncMock(return_value=DONE)
    notifier = TelegramNotifier(None, "1", cancel_handler=handler)
    update, query = _callback_update("cancelentry_confirm:42")
    await notifier.handle_button_callback(update, MagicMock())
    handler.assert_awaited_once_with(42, True)
    query.edit_message_reply_markup.assert_awaited_with(reply_markup=None)
    text = query.message.reply_text.await_args.args[0]
    assert "SPY" in text and "#42" in text


@pytest.mark.asyncio
async def test_telegram_keep_callback_cancels_nothing():
    handler = AsyncMock()
    notifier = TelegramNotifier(None, "1", cancel_handler=handler)
    update, query = _callback_update("cancelentry_keep")
    await notifier.handle_button_callback(update, MagicMock())
    handler.assert_not_awaited()
    query.edit_message_reply_markup.assert_awaited_with(reply_markup=None)
    assert "kept" in query.message.reply_text.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_cancel_is_in_the_command_palette_and_help():
    notifier = TelegramNotifier(None, "1")
    notifier.app = MagicMock()
    notifier.app.bot.set_my_commands = AsyncMock(return_value=True)
    notifier.app.bot.set_chat_menu_button = AsyncMock(return_value=True)
    with patch.object(notifier, "is_configured", return_value=True):
        assert await notifier.setup_bot_commands()
    names = [c.command for c in notifier.app.bot.set_my_commands.await_args_list[0].args[0]]
    assert "cancel" in names
    update = _command_update()
    await notifier.handle_help_command(update, MagicMock())
    assert "/cancel" in update.message.reply_text.await_args.args[0]


@pytest.mark.parametrize(("flags", "confirm"), [([], False), (["--confirm"], True)])
def test_cli_cancel_previews_without_confirm(flags, confirm):
    result_value = PREVIEW if not confirm else DONE
    with patch("agentic_trader.cli.commands.trade.get_copilot_and_config") as mock_get:
        copilot = MagicMock()
        copilot.broker.connect = AsyncMock()
        copilot.cancel_resting_entry = AsyncMock(return_value=result_value)
        mock_get.return_value = (copilot, MagicMock())
        result = CliRunner().invoke(cli, ["cancel", "42", *flags])
    assert result.exit_code == 0, result.output
    copilot.cancel_resting_entry.assert_awaited_once_with(42, confirm=confirm)
    assert "SPY" in result.output and "<b>" not in result.output
    assert ("copilot cancel 42 --confirm" in result.output) is (not confirm)
