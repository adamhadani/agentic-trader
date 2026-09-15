from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_trader.notifier.telegram_bot import TelegramNotifier
from agentic_trader.presentation.formatters import TelegramHtmlFormatter
from agentic_trader.research.alpha.models import (
    AlphaDefinition,
    AlphaEvaluationMetrics,
    AlphaOrigin,
    AlphaStatus,
    PromotedAlphaRecord,
)


def test_format_alphas_dashboard_html_with_records():
    defn = AlphaDefinition(
        alpha_id="alpha_wq_006",
        name="WQ-006 Open-Volume Absorption Divergence",
        expression="-1.0 * ts_corr(open, volume, 10)",
        description="Identifies institutional volume absorption",
        origin=AlphaOrigin.WORLDQUANT_101,
        direction="bi_directional",
        entry_threshold=0.6,
        exit_threshold=0.0,
        timeframe="4h",
    )
    metrics = AlphaEvaluationMetrics(
        rank_ic_mean=0.045,
        rank_ic_std=0.015,
        rank_ic_ir=3.0,
        sharpe_is=2.2,
        sharpe_oos=1.95,
        dsr=0.98,
        win_rate=0.58,
        profit_factor=2.1,
        max_drawdown_pct=5.5,
        annualized_return_pct=22.4,
        total_trades=54,
    )
    rec = PromotedAlphaRecord(
        alpha_id="alpha_wq_006",
        definition=defn,
        metrics=metrics,
        promoted_at="2026-09-15T08:00:00Z",
        promoted_by="lead_quant",
        allocation_weight=0.15,
        status=AlphaStatus.PROMOTED,
        notes="High DSR alpha",
    )

    html = TelegramHtmlFormatter.format_alphas_dashboard_html([rec])
    assert "FORMULAIC ALPHA INTELLIGENCE" in html
    assert "alpha_wq_006" in html
    assert "15%" in html
    assert "1.95" in html
    assert "0.98" in html
    assert "ts_corr" in html


def test_format_alphas_dashboard_html_empty():
    html = TelegramHtmlFormatter.format_alphas_dashboard_html([])
    assert "FORMULAIC ALPHA INTELLIGENCE" in html
    assert "copilot alpha mine --auto-promote" in html


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
