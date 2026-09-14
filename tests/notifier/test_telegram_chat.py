"""Tests for TelegramNotifier conversational chat message routing and formatting."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Chat, Message, Update, User
from telegram.constants import ChatAction

from agentic_trader.notifier.telegram_bot import TelegramNotifier


@pytest.fixture
def mock_notifier():
    notifier = TelegramNotifier(
        bot_token="TEST_BOT_TOKEN",
        chat_id="12345678",
        db=MagicMock(),
        status_provider=AsyncMock(return_value="Status OK"),
    )
    return notifier


def make_update(text: str, chat_id: int = 12345678) -> Update:
    update = MagicMock(spec=Update)
    chat = MagicMock(spec=Chat)
    chat.id = chat_id
    chat.send_action = AsyncMock()

    user = MagicMock(spec=User)
    user.id = chat_id
    user.first_name = "Trader"

    message = MagicMock(spec=Message)
    message.text = text
    message.chat = chat
    message.from_user = user
    message.reply_text = AsyncMock()

    update.effective_chat = chat
    update.effective_user = user
    update.message = message
    return update


@pytest.mark.asyncio
async def test_handle_chat_message_authorized(mock_notifier):
    chat_handler = AsyncMock(return_value="Current SPY position: +$195 unrealized PnL.")
    mock_notifier.chat_handler = chat_handler

    update = make_update("How is our SPY trade looking?")
    context = MagicMock()

    await mock_notifier.handle_chat_message(update, context)

    # Check typing indicator sent
    update.effective_chat.send_action.assert_called_with(ChatAction.TYPING)
    # Check chat handler invoked with query and chat id
    chat_handler.assert_called_once_with("How is our SPY trade looking?", "12345678")
    # Check reply was sent
    update.message.reply_text.assert_called_once()
    sent_text = update.message.reply_text.call_args[0][0]
    assert "Current SPY position" in sent_text


@pytest.mark.asyncio
async def test_handle_chat_message_unauthorized(mock_notifier):
    chat_handler = AsyncMock(return_value="Sensitive portfolio data.")
    mock_notifier.chat_handler = chat_handler

    # Sender has different chat id
    update = make_update("Tell me the portfolio balance", chat_id=99999999)
    context = MagicMock()

    await mock_notifier.handle_chat_message(update, context)

    # Chat handler should NOT be called
    chat_handler.assert_not_called()
    update.message.reply_text.assert_not_called()


@pytest.mark.asyncio
async def test_handle_chat_message_no_handler_configured(mock_notifier):
    mock_notifier.chat_handler = None

    update = make_update("Hello copilot")
    context = MagicMock()

    await mock_notifier.handle_chat_message(update, context)
    update.message.reply_text.assert_called_once()
    assert "Conversational copilot is not enabled" in update.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_handle_chat_message_long_reply_chunking(mock_notifier):
    # Generates response longer than 4000 characters
    long_response = "Line of analysis.\n" * 300  # ~5400 characters
    mock_notifier.chat_handler = AsyncMock(return_value=long_response)

    update = make_update("Deep market analysis please")
    context = MagicMock()

    await mock_notifier.handle_chat_message(update, context)

    # Should send more than 1 message chunk
    assert update.message.reply_text.call_count > 1
