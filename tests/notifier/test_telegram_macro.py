import dataclasses
import functools
import math
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.agent.macro import (
    MACRO_INPUT_SOURCES,
    CreditSpreads,
    CreditStressRegime,
    InflationExpectations,
    InflationRegime,
    MacroEvaluation,
    MacroInput,
    MacroIntelligenceReport,
    MacroStressAssessment,
    MacroStressLevel,
    TreasuryYields,
    YieldCurveRegime,
    YieldCurveSpreads,
    calculate_yield_curve_spreads,
    classify_credit_stress,
    compute_compound_macro_stress,
    unknown_macro_stress,
)
from agentic_trader.agent.macro_explainer import MacroExplainer
from agentic_trader.agent.regime import RegimeDetector, RegimeSnapshot, RegimeUnavailable, regime_journal_block
from agentic_trader.config import RegimeConfig
from agentic_trader.constants import VolatilityRegime
from agentic_trader.notifier.telegram_bot import TelegramNotifier
from agentic_trader.presentation.formatters import TelegramHtmlFormatter


@pytest.mark.parametrize(("regime_min_rr", "shown"), [(math.nan, 2.5), (2.7, 2.7), (2.0, 2.5)])
async def test_macro_dashboard_and_audit_show_the_shared_required_ratio(app_config, regime_min_rr, shown):
    """``/macro`` prints ``agentic_trader.risk.required_reward_risk``: a non-finite regime threshold is the
    configured minimum, never ``nan``."""
    app_config.risk.min_risk_reward_ratio = 2.5
    regime = RegimeSnapshot(
        vix=17.0,
        vix_regime=VolatilityRegime.NORMAL,
        tnx=4.2,
        dxy=103.0,
        breakout_allowed=True,
        min_rr_threshold=2.0,
        timestamp=datetime.now(UTC),
        summary_text="Normal",
        risk_multiplier=1.0,
    )
    desk = SimpleNamespace(
        config=app_config,
        regime_detector=SimpleNamespace(
            get_regime=AsyncMock(return_value=dataclasses.replace(regime, min_rr_threshold=regime_min_rr))
        ),
        db=SimpleNamespace(record_audit=AsyncMock()),
    )
    html = await TradingCopilot.get_macro_summary_html(desk)
    assert f"Minimum Required R:R:</b> <code>{shown:.1f}:1</code>" in html
    payload = desk.db.record_audit.await_args.kwargs["payload"]
    assert payload["minimum_rr"] == shown
    assert payload["regime"] == regime_journal_block(desk.regime_detector.get_regime.return_value)


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

    regime = RegimeSnapshot(
        vix=report.vix,
        vix_regime=VolatilityRegime.ELEVATED,
        tnx=report.yields.yield_10y,
        dxy=report.dxy,
        breakout_allowed=False,
        min_rr_threshold=2.7,
        risk_multiplier=0.5,
        timestamp=report.timestamp,
        summary_text="Combined policy",
        macro_report=report,
    )
    # The caller passes required_reward_risk: max(configured 3.0, regime 2.7).
    formatted = TelegramHtmlFormatter.format_macro_dashboard_html(regime, required_rr=3.0)
    assert "3.0:1" in formatted
    assert "0.50x" in formatted

    assert "MACRO INTELLIGENCE &amp; YIELD CURVE" in formatted or "MACRO INTELLIGENCE & YIELD CURVE" in formatted
    assert "LOW STRESS" in formatted
    assert "1.00x" not in formatted
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
    assert "Suppressed ⚠️" in formatted
    assert "Allowed ✅" not in formatted


def _unknown_snapshot(missing=("2y", "hy_oas")) -> RegimeSnapshot:
    inputs = {
        name: MacroInput(
            name=name,
            value=None if name in missing else 4.0,
            source=source,
            observed_on=None,
            decision=name in {"vix", "3m", "2y", "10y", "hy_oas"},
            error="no_observation" if name in missing else None,
        )
        for name, source in MACRO_INPUT_SOURCES.items()
    }
    return RegimeSnapshot(
        vix=17.0,
        vix_regime=VolatilityRegime.NORMAL,
        tnx=4.0,
        dxy=4.0,
        breakout_allowed=False,
        min_rr_threshold=2.0,
        timestamp=datetime(2026, 10, 21, 15, 5, tzinfo=UTC),
        summary_text="VIX: 17.00 (NORMAL) | Macro: UNKNOWN (missing 2y, hy_oas; ×0.50, breakouts suppressed)",
        risk_multiplier=0.5,
        macro_unavailable_reason="Missing decision inputs: 2y, hy_oas",
        macro_stress=unknown_macro_stress(RegimeConfig(), list(missing), vix=17.0, hy_oas=inputs["hy_oas"].value),
        macro_inputs=inputs,
    )


def test_macro_dashboard_names_the_unknown_policy():
    formatted = TelegramHtmlFormatter.format_macro_dashboard_html(_unknown_snapshot(), required_rr=2.0)
    assert "UNKNOWN" in formatted
    assert "missing 2y, hy_oas" in formatted
    assert "×0.50" in formatted
    assert "uses volatility only" not in formatted
    assert "Suppressed ⚠️" in formatted


def test_macro_dashboard_with_the_yield_curve_disabled_still_says_volatility_only():
    regime = RegimeSnapshot(
        vix=17.0,
        vix_regime=VolatilityRegime.NORMAL,
        tnx=4.2,
        dxy=103.0,
        breakout_allowed=True,
        min_rr_threshold=2.0,
        timestamp=datetime.now(UTC),
        summary_text="VIX only",
        risk_multiplier=1.0,
        macro_unavailable_reason="Yield-curve enrichment is disabled in configuration",
    )
    formatted = TelegramHtmlFormatter.format_macro_dashboard_html(regime, required_rr=2.0)
    assert "Yield-curve enrichment is disabled in configuration" in formatted
    assert "uses volatility only" in formatted and "UNKNOWN" not in formatted


def _report_without_context() -> MacroIntelligenceReport:
    yields = TreasuryYields(yield_3m=3.95, yield_2y=4.35, yield_5y=None, yield_10y=4.95, yield_30y=None)
    spreads = calculate_yield_curve_spreads(yields)
    credit = classify_credit_stress(2.65)
    return MacroIntelligenceReport(
        yields=yields,
        spreads=spreads,
        credit=credit,
        inflation=None,
        vix=17.2,
        dxy=None,
        stress=compute_compound_macro_stress(17.2, spreads, credit),
        timestamp=datetime(2026, 10, 21, 15, 5, tzinfo=UTC),
        summary_text="test",
    )


def test_indicators_and_explainer_render_missing_context_as_na():
    report = _report_without_context()
    assert report.spreads.curvature_butterfly_bps is None
    indicators = TelegramHtmlFormatter._format_macro_indicators_html(report)
    assert "<b>5Y:</b> <code>N/A</code>" in indicators
    assert "<b>30Y:</b> <code>N/A</code>" in indicators
    assert "<b>Butterfly Curvature:</b> <code>N/A</code>" in indicators
    assert "<b>10Y Breakeven Inflation:</b> <code>N/A</code>" in indicators
    assert "<b>5Y Breakeven Inflation:</b> <code>N/A</code>" in indicators

    text = MacroExplainer(config=None).explain_deterministic(report, format_mode="text")
    assert "VIX: 17.20 | DXY: N/A" in text
    html_text = MacroExplainer(config=None).explain_deterministic(report, format_mode="html")
    assert "<i>DXY:</i> <code>N/A</code>" in html_text


def test_prompt_context_names_the_unknown_stress():
    context = RegimeDetector(RegimeConfig()).get_prompt_context(_unknown_snapshot())
    assert "Compound Macro Stress: UNKNOWN (missing 2y, hy_oas; Risk Multiplier: 0.50x)" in context
    assert "US Treasury Yield Curve" not in context


def _strategy_line(context: str) -> str:
    (line,) = [line for line in context.splitlines() if line.startswith("• Strategy Adaptation:")]
    return line


def test_prompt_breakout_reason_names_unknown_macro_stress_at_a_normal_vix():
    context = RegimeDetector(RegimeConfig()).get_prompt_context(_unknown_snapshot())
    assert _strategy_line(context) == (
        "• Strategy Adaptation: Squeeze Breakouts SUPPRESSED (Macro Stress UNKNOWN: missing 2y, hy_oas); "
        "Minimum required Reward-to-Risk: 2.0:1."
    )
    assert "Extreme Volatility Chop" not in context


def test_prompt_breakout_reason_names_measured_macro_stress_at_a_normal_vix():
    high = MacroStressAssessment(
        level=MacroStressLevel.HIGH,
        risk_multiplier=0.5,
        squeeze_breakout_allowed=False,
        min_rr_threshold=2.2,
        key_drivers=["Yield Curve Inverted (10Y-2Y: -35 bps)"],
    )
    report = dataclasses.replace(_report_without_context(), stress=high)
    regime = RegimeSnapshot(
        vix=17.2,
        vix_regime=VolatilityRegime.NORMAL,
        tnx=4.95,
        dxy=None,
        breakout_allowed=False,
        min_rr_threshold=2.2,
        timestamp=report.timestamp,
        summary_text="test",
        risk_multiplier=0.5,
        macro_report=report,
        macro_stress=high,
    )
    context = RegimeDetector(RegimeConfig()).get_prompt_context(regime)
    assert _strategy_line(context) == (
        "• Strategy Adaptation: Squeeze Breakouts SUPPRESSED (Macro Stress HIGH); "
        "Minimum required Reward-to-Risk: 2.2:1."
    )


@pytest.mark.parametrize("stress", [None, "unknown"])
def test_prompt_breakout_reason_keeps_volatility_chop_only_for_an_extreme_vix(stress):
    regime = dataclasses.replace(
        _unknown_snapshot(),
        vix=34.8,
        vix_regime=VolatilityRegime.EXTREME,
        min_rr_threshold=2.5,
        macro_stress=_unknown_snapshot().macro_stress if stress else None,
    )
    context = RegimeDetector(RegimeConfig()).get_prompt_context(regime)
    assert _strategy_line(context) == (
        "• Strategy Adaptation: Squeeze Breakouts SUPPRESSED (Extreme Volatility Chop / High False Breakout Risk); "
        "Minimum required Reward-to-Risk: 2.5:1."
    )


def test_prompt_breakouts_allowed_is_unchanged():
    regime = dataclasses.replace(_unknown_snapshot(), breakout_allowed=True, macro_stress=None, macro_inputs={})
    context = RegimeDetector(RegimeConfig()).get_prompt_context(regime)
    assert _strategy_line(context) == (
        "• Strategy Adaptation: Squeeze Breakouts ALLOWED; Minimum required Reward-to-Risk: 2.0:1."
    )


def test_prompt_context_renders_a_missing_breakeven_as_na():
    report = _report_without_context()
    regime = RegimeSnapshot(
        vix=17.2,
        vix_regime=VolatilityRegime.NORMAL,
        tnx=4.95,
        dxy=None,
        breakout_allowed=True,
        min_rr_threshold=2.0,
        timestamp=report.timestamp,
        summary_text="test",
        risk_multiplier=1.0,
        macro_report=report,
        macro_stress=report.stress,
    )
    context = RegimeDetector(RegimeConfig()).get_prompt_context(regime)
    assert "• 10Y Breakeven Inflation: N/A" in context
    assert "• US Dollar Index (DXY): N/A" in context
    assert "• Compound Macro Stress: LOW (Risk Multiplier: 1.00x)" in context


REGIME_DOWN_TEXT = "Market regime unavailable (vix: ReadTimeout); scans skip until VIX is readable."


def _vix_down_desk(app_config) -> SimpleNamespace:
    vix = MacroInput("vix", None, MACRO_INPUT_SOURCES["vix"], None, True, "ReadTimeout")
    dxy = MacroInput("dxy", None, MACRO_INPUT_SOURCES["dxy"], None, False, "ConnectError")
    return SimpleNamespace(
        config=app_config,
        regime_detector=SimpleNamespace(get_regime=AsyncMock(side_effect=RegimeUnavailable({"vix": vix, "dxy": dxy}))),
        db=SimpleNamespace(record_audit=AsyncMock()),
    )


async def test_macro_summary_names_an_unavailable_regime_by_type_name(app_config):
    desk = _vix_down_desk(app_config)

    assert await TradingCopilot.get_macro_summary_html(desk) == REGIME_DOWN_TEXT

    desk.db.record_audit.assert_not_awaited()  # no regime was read, so no MACRO_REPORT


@pytest.mark.parametrize("path", ["/macro", "macro button"])
async def test_macro_command_and_button_reply_that_the_regime_is_unavailable(app_config, path):
    desk = _vix_down_desk(app_config)
    notifier = TelegramNotifier(
        bot_token="test_token",
        chat_id="12345",
        macro_provider=functools.partial(TradingCopilot.get_macro_summary_html, desk),
    )
    update = MagicMock()
    update.effective_chat.id = 12345
    if path == "/macro":
        update.message = AsyncMock()
        await notifier.handle_macro_command(update, MagicMock())
        reply = update.message.reply_text
    else:
        update.callback_query.data = "cmd_macro"
        update.callback_query.answer = AsyncMock()
        update.callback_query.message.reply_text = AsyncMock()
        await notifier.handle_button_callback(update, MagicMock())
        reply = update.callback_query.message.reply_text

    reply.assert_awaited_once_with(REGIME_DOWN_TEXT, parse_mode="HTML")


async def test_explain_macro_without_a_report_names_the_missing_decision_inputs(app_config):
    evaluation = MacroEvaluation(
        inputs={},
        missing=("2y", "hy_oas", "dxy"),
        missing_decision=("2y", "hy_oas"),
        report=None,
        stress=unknown_macro_stress(RegimeConfig(), ["2y", "hy_oas"], vix=17.2, hy_oas=None),
    )
    desk = SimpleNamespace(
        config=app_config,
        regime_detector=SimpleNamespace(macro_engine=SimpleNamespace(evaluate=AsyncMock(return_value=evaluation))),
    )
    text = await TradingCopilot.get_explain_macro_html(desk)
    assert text == "Macro report unavailable: missing decision inputs: 2y, hy_oas"


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


@pytest.mark.asyncio
async def test_telegram_handle_explain_macro_command_authorized():
    mock_explain_provider = AsyncMock(return_value="<b>EXPLAIN MACRO TEST</b>")

    notifier = TelegramNotifier(
        bot_token="test_token",
        chat_id="12345",
        explain_macro_provider=mock_explain_provider,
    )

    mock_update = MagicMock()
    mock_update.effective_chat.id = 12345
    mock_update.message = AsyncMock()
    mock_context = MagicMock()

    await notifier.handle_explain_macro_command(mock_update, mock_context)

    assert mock_explain_provider.call_count == 1
    # Check that reply_text was called with the briefing
    mock_update.message.reply_text.assert_any_call(
        "<b>EXPLAIN MACRO TEST</b>",
        parse_mode="HTML",
    )


@pytest.mark.asyncio
async def test_telegram_handle_explain_macro_command_entity_error_fallback():
    # Simulate Telegram BadRequest when HTML has unexpected end tag or bad entity
    mock_explain_provider = AsyncMock(return_value="<b>MACRO BRIEFING</b>\nUnexpected </i> and </b>\nYield < 5%")

    notifier = TelegramNotifier(
        bot_token="test_token",
        chat_id="12345",
        explain_macro_provider=mock_explain_provider,
    )

    mock_update = MagicMock()
    mock_update.effective_chat.id = 12345
    mock_message = AsyncMock()

    # Make HTML parse fail with Telegram BadRequest exception on the briefing message
    async def mock_reply_text(*args, **kwargs):
        if kwargs.get("parse_mode") == "HTML" and args and "MACRO BRIEFING" in args[0]:
            raise BadRequest("Can't parse entities: unexpected end tag at byte offset 5321")
        return MagicMock()

    mock_message.reply_text = AsyncMock(side_effect=mock_reply_text)
    mock_update.message = mock_message
    mock_context = MagicMock()

    await notifier.handle_explain_macro_command(mock_update, mock_context)

    # Verify that the provider was called
    assert mock_explain_provider.call_count == 1
    # Verify that it fell back to plain text (parse_mode=None) instead of crashing!
    plain_calls = [call for call in mock_message.reply_text.call_args_list if call.kwargs.get("parse_mode") is None]
    assert len(plain_calls) >= 1
    # Plain text should contain the content
    fallback_text = plain_calls[0].args[0]
    assert "MACRO BRIEFING" in fallback_text


@pytest.mark.asyncio
async def test_telegram_safe_reply_text_chunking():
    notifier = TelegramNotifier(bot_token="test_token", chat_id="12345")
    mock_message = AsyncMock()

    # Message longer than max_chunk_len
    p1 = "Section 1: " + ("A" * 150)
    p2 = "Section 2: " + ("B" * 150)
    long_msg = f"{p1}\n\n{p2}"

    sent = await notifier.safe_reply_text(mock_message, long_msg, parse_mode="HTML", max_chunk_len=200)
    assert len(sent) == 2
    assert mock_message.reply_text.call_count == 2
