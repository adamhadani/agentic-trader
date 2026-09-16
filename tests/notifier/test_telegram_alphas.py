from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_trader.notifier.telegram_bot import TelegramNotifier
from agentic_trader.presentation.formatters import TelegramHtmlFormatter
from agentic_trader.research.alpha.models import AlphaDefinition, RegistrySnapshot


@pytest.mark.parametrize("empty", [False, True])
def test_alpha_dashboard_shows_registry_and_safe_guidance(empty):
    definition = AlphaDefinition("alpha_wq_006", "WQ-006", "-ts_corr(open,volume,10)")
    snapshot = RegistrySnapshot(1, (), () if empty else (definition,))
    html = TelegramHtmlFormatter.format_alphas_dashboard_html(snapshot)
    assert "FORMULAIC ALPHA INTELLIGENCE" in html
    assert "Shadow observations do not place orders" in html
    assert "--auto-promote" not in html
    if not empty:
        assert definition.version_id[:12] in html


@pytest.mark.asyncio
async def test_telegram_handle_alphas_command_authorized():
    mock_alphas_provider = AsyncMock(return_value="<b>ALPHAS TEST DASHBOARD</b>")

    notifier = TelegramNotifier(
        bot_token="test_token",
        chat_id="12345",
        alphas_provider=mock_alphas_provider,
    )

    mock_update = MagicMock()
    mock_update.effective_chat.id = 12345
    mock_update.message = AsyncMock()

    mock_context = MagicMock()

    await notifier.handle_alphas_command(mock_update, mock_context)

    assert mock_alphas_provider.call_count == 1
    mock_update.message.reply_text.assert_called_once_with(
        "<b>ALPHAS TEST DASHBOARD</b>",
        parse_mode="HTML",
    )


@pytest.mark.asyncio
async def test_telegram_handle_alphas_command_unauthorized():
    mock_alphas_provider = AsyncMock(return_value="<b>ALPHAS TEST DASHBOARD</b>")

    notifier = TelegramNotifier(
        bot_token="test_token",
        chat_id="12345",
        alphas_provider=mock_alphas_provider,
    )

    mock_update = MagicMock()
    mock_update.effective_chat.id = 99999  # Unauthorized chat ID
    mock_update.message = AsyncMock()

    mock_context = MagicMock()

    await notifier.handle_alphas_command(mock_update, mock_context)

    assert mock_alphas_provider.call_count == 0
    assert mock_update.message.reply_text.call_count == 0
