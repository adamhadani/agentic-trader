from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner

from agentic_trader.cli.main import cli
from agentic_trader.notifier.telegram_bot import TelegramNotifier


@pytest.mark.parametrize("command", sorted(cli.commands))
def test_cli_command_help_is_available(command):
    result = CliRunner().invoke(cli, [command, "--help"])
    assert result.exit_code == 0, result.output
    assert "Usage:" in result.output


@pytest.mark.parametrize(
    ("command", "provider"),
    [
        ("status", "status_provider"),
        ("positions", "positions_provider"),
        ("perf", "perf_provider"),
        ("macro", "macro_provider"),
        ("explain_macro", "explain_macro_provider"),
        ("alphas", "alphas_provider"),
        ("gex", "gex_provider"),
        ("pairs", "pairs_provider"),
        ("scan", "scan_runner"),
        ("backtest", "backtest_runner"),
    ],
)
@pytest.mark.asyncio
async def test_read_commands_dispatch_and_deliver(command, provider):
    callback = AsyncMock(return_value="<b>Report ready</b>")
    notifier = TelegramNotifier(None, "1", **{provider: callback})
    update = MagicMock()
    update.effective_chat.id = 1
    update.message.reply_text = AsyncMock()
    update.message.reply_chat_action = AsyncMock()
    context = MagicMock(args=[])
    await getattr(notifier, f"handle_{command}_command")(update, context)
    callback.assert_awaited_once()
    assert any("Report ready" in call.args[0] for call in update.message.reply_text.await_args_list)


@pytest.mark.asyncio
async def test_backtest_defaults_use_injected_configuration():
    callback = AsyncMock(return_value="Result")
    notifier = TelegramNotifier(None, "1", backtest_runner=callback, backtest_lookback="5y")
    update = MagicMock()
    update.effective_chat.id = 1
    update.message.reply_text = AsyncMock()
    await notifier.handle_backtest_command(update, MagicMock(args=[]))
    callback.assert_awaited_once_with("SPY", "5y")


@pytest.mark.asyncio
async def test_help_and_buttons_have_one_macro_entry():
    notifier = TelegramNotifier(None, "1")
    update = MagicMock()
    update.effective_chat.id = 1
    update.message.reply_text = AsyncMock()
    await notifier.handle_help_command(update, MagicMock())
    call = update.message.reply_text.await_args
    assert "/macro" in call.args[0]
    assert "/regime" not in call.args[0]
    callbacks = [button.callback_data for row in call.kwargs["reply_markup"].inline_keyboard for button in row]
    assert "cmd_macro" in callbacks
    assert "cmd_regime" not in callbacks
