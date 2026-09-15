from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_trader.agent.macro import (
    CreditSpreads,
    CreditStressRegime,
    InflationExpectations,
    InflationRegime,
    MacroIntelligenceReport,
    MacroStressAssessment,
    MacroStressLevel,
    TreasuryYields,
    YieldCurveRegime,
    YieldCurveSpreads,
)
from agentic_trader.notifier.telegram_bot import TelegramNotifier
from agentic_trader.presentation.formatters import TelegramHtmlFormatter


def test_format_macro_dashboard_html():
    report = MacroIntelligenceReport(
        yields=TreasuryYields(
            yield_3m=3.93,
            yield_2y=4.38,
            yield_5y=4.79,
            yield_10y=4.96,
            yield_30y=5.33,
        ),
        spreads=YieldCurveSpreads(
            slope_10y_2y_pct=0.58,
            slope_10y_2y_bps=58.0,
            slope_10y_3m_bps=103.0,
            curvature_butterfly_bps=24.0,
            regime=YieldCurveRegime.NORMAL_STEEP,
        ),
        credit=CreditSpreads(
            high_yield_oas_pct=2.65,
            high_yield_oas_bps=265.0,
            regime=CreditStressRegime.BENIGN,
        ),
        inflation=InflationExpectations(
            breakeven_10y=2.37,
            breakeven_5y=2.40,
            regime=InflationRegime.ANCHORED,
        ),
        vix=17.20,
        dxy=103.40,
        stress=MacroStressAssessment(
            level=MacroStressLevel.LOW,
            risk_multiplier=1.00,
            squeeze_breakout_allowed=True,
            min_rr_threshold=2.0,
            key_drivers=["Benign macro conditions across all indicators"],
        ),
        timestamp=datetime.now(UTC),
        summary_text="Macro Stress: LOW (1.00x) | Curve: NORMAL_STEEP (+58 bps) | Credit OAS: 265 bps",
    )

    formatted = TelegramHtmlFormatter.format_macro_dashboard_html(report)

    assert "MACRO INTELLIGENCE &amp; YIELD CURVE" in formatted or "MACRO INTELLIGENCE & YIELD CURVE" in formatted
    assert "LOW STRESS" in formatted
    assert "1.00x" in formatted
    assert "NORMAL_STEEP" in formatted
    assert "3.93%" in formatted
    assert "4.38%" in formatted
    assert "4.96%" in formatted
    assert "+58.0 bps" in formatted
    assert "+103.0 bps" in formatted
    assert "265 bps" in formatted
    assert "BENIGN" in formatted
    assert "2.37%" in formatted
    assert "17.20" in formatted
    assert "103.40" in formatted
    assert "Allowed ✅" in formatted


@pytest.mark.asyncio
async def test_telegram_handle_macro_command_authorized():
    mock_macro_provider = AsyncMock(return_value="<b>MACRO DASHBOARD TEST</b>")

    notifier = TelegramNotifier(
        bot_token="test_token",
        chat_id="12345",
        macro_provider=mock_macro_provider,
    )

    mock_update = MagicMock()
    mock_update.effective_chat.id = 12345
    mock_update.message = AsyncMock()

    mock_context = MagicMock()

    await notifier.handle_macro_command(mock_update, mock_context)

    assert mock_macro_provider.call_count == 1
    mock_update.message.reply_text.assert_called_once_with(
        "<b>MACRO DASHBOARD TEST</b>",
        parse_mode="HTML",
    )


@pytest.mark.asyncio
async def test_telegram_handle_macro_command_unauthorized():
    mock_macro_provider = AsyncMock(return_value="<b>MACRO DASHBOARD TEST</b>")

    notifier = TelegramNotifier(
        bot_token="test_token",
        chat_id="12345",
        macro_provider=mock_macro_provider,
    )

    mock_update = MagicMock()
    mock_update.effective_chat.id = 99999  # Unauthorized chat ID
    mock_update.message = AsyncMock()

    mock_context = MagicMock()

    await notifier.handle_macro_command(mock_update, mock_context)

    assert mock_macro_provider.call_count == 0
    assert mock_update.message.reply_text.call_count == 0
