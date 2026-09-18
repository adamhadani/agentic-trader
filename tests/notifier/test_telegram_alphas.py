from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_trader.config import SessionDecisionConfig
from agentic_trader.market.bars import SessionClockPolicy
from agentic_trader.notifier.telegram_bot import TelegramNotifier
from agentic_trader.presentation.formatters import TelegramHtmlFormatter
from agentic_trader.research.alpha.evidence import build_forward_evidence
from agentic_trader.research.alpha.models import AlphaDefinition, RegistrySnapshot


@pytest.mark.parametrize("empty", [False, True])
def test_alpha_dashboard_shows_registry_and_safe_guidance(empty):
    definition = AlphaDefinition("alpha_wq_006", "WQ-006", "-ts_corr(open,volume,10)")
    snapshot = RegistrySnapshot(1, (), () if empty else (definition,))
    html = TelegramHtmlFormatter.format_alphas_dashboard_html(
        snapshot, evidence={"days": 7, "truncated": False, "candidates": []}
    )
    assert "ALPHA RESEARCH" in html
    assert "Shadow candidates cannot place orders" in html
    assert "--auto-promote" not in html
    if not empty:
        assert definition.version_id[:12] not in html
        assert "1 research candidates" in html


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


async def test_forward_dashboard_stays_compact_as_registry_grows():
    definitions = tuple(
        AlphaDefinition(
            f"control_{i}",
            f"Control {i}",
            "returns",
            timeframe="15m",
            eligible_symbols=("SPY",),
            semantics_version=3,
            data_feed="alpaca:sip",
            clock=SessionClockPolicy(),
        )
        for i in range(100)
    )
    snapshot = RegistrySnapshot(1, (), definitions)
    report = build_forward_evidence(
        snapshot,
        SessionDecisionConfig(enabled=True),
        {"decisions": [], "cursors": {}, "truncated": True},
        now=datetime.now(UTC),
        days=7,
    )
    card = TelegramHtmlFormatter.format_alphas_dashboard_html(snapshot, evidence=report)
    assert "lower bounds" in card and "No recorded evaluations" in card
    assert "100 research candidates" in card and "100 candidate/symbol pairs" in card
    assert len(card) < 1800
    assert "cannot place orders" in card and "copilot alpha forward" in card
    notifier = TelegramNotifier("test_token", "12345", alphas_provider=AsyncMock(return_value=card))
    update = MagicMock()
    update.effective_chat.id = 12345
    update.message = AsyncMock()
    await notifier.handle_alphas_command(update, MagicMock())
    chunks = [call.args[0] for call in update.message.reply_text.call_args_list]
    assert len(chunks) == 1
    for definition in definitions:
        assert definition.expression not in card
        assert definition.version_id[:12] not in card


def test_daily_panel_dashboard_is_compact_and_separate_from_intraday_and_qualification():
    report = {
        "days": 7,
        "truncated": False,
        "candidates": [],
        "daily_panel": {
            "worker_enabled": True,
            "truncated": True,
            "campaigns": [{"campaign_id": "private"}] * 100,
            "decision_sessions": 9,
            "outcome_sessions": 3,
            "decision_counts": {"scored": 7, "unavailable": 1, "missed": 1, "interrupted": 0, "claimed": 0},
            "outcome_counts": {"complete": 2, "unavailable": 1, "capturing": 0},
        },
    }
    card = TelegramHtmlFormatter.format_alphas_dashboard_html(RegistrySnapshot(1, (), ()), evidence=report)
    assert "Daily panel" in card and "7/9" in card and "2/3" in card
    assert "lower bounds" in card and "not qualification" in card
    assert "private" not in card and len(card) < 1800
