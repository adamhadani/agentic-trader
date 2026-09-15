"""
Macro Explainer & Quantitative Tutorial Engine.
Synthesizes live institutional macroeconomic indicators (Yield Curve, Credit OAS, VIX, Breakevens)
into an educational, accessible executive tutorial using LLM with deterministic rule-based fallback.
"""

from __future__ import annotations

import html
import logging
from typing import TYPE_CHECKING

import litellm

from agentic_trader.agent.macro import (
    CreditStressRegime,
    MacroIntelligenceReport,
    YieldCurveRegime,
)
from agentic_trader.presentation.formatters import TelegramHtmlFormatter


if TYPE_CHECKING:
    from agentic_trader.config import AppConfig

logger = logging.getLogger(__name__)


class MacroExplainer:
    """
    Translates institutional macroeconomic telemetry into intuitive, educational briefings.
    Combines quantitative metric explanation with tactical trading guidance.
    """

    def __init__(self, config: AppConfig | None = None) -> None:
        self.config = config

    async def explain(
        self,
        report: MacroIntelligenceReport,
        format_mode: str = "text",  # "text" or "html"
    ) -> str:
        """
        Generate an educational macro briefing for the given report.
        Tries LLM synthesis first; falls back to deterministic expert template if unavailable.
        """
        # Try LLM synthesis if configured
        if self.config and (self.config.openai_api_key or self.config.anthropic_api_key or self.config.gemini_api_key):
            try:
                llm_explanation = await self._explain_with_llm(report, format_mode)
                if llm_explanation:
                    return llm_explanation
            except Exception as e:
                logger.warning("LLM macro explanation failed, falling back to deterministic template: %s", e)

        return self.explain_deterministic(report, format_mode)

    async def _explain_with_llm(self, report: MacroIntelligenceReport, format_mode: str) -> str:
        prompt_context = f"""
Current Quantitative Macro Telemetry:
- Yields: 3M={report.yields.yield_3m:.2f}%, 2Y={report.yields.yield_2y:.2f}%, 5Y={report.yields.yield_5y:.2f}%, 10Y={report.yields.yield_10y:.2f}%, 30Y={report.yields.yield_30y:.2f}%
- Term Structure: 10Y-2Y Slope = {report.spreads.slope_10y_2y_bps:+.1f} bps ({report.spreads.regime.value}), 10Y-3M = {report.spreads.slope_10y_3m_bps:+.1f} bps, Butterfly Curvature = {report.spreads.curvature_butterfly_bps:+.1f} bps
- Credit Spreads: High Yield OAS = {report.credit.high_yield_oas_bps:.0f} bps ({report.credit.regime.value})
- Inflation Expectations: 10Y Breakeven = {report.inflation.breakeven_10y:.2f}%, 5Y = {report.inflation.breakeven_5y:.2f}% ({report.inflation.regime.value})
- Volatility & FX: VIX = {report.vix:.2f}, US Dollar Index (DXY) = {report.dxy:.2f}
- Copilot Stress Assessment: Level = {report.stress.level.value}, Risk Multiplier = {report.stress.risk_multiplier:.2f}x, Squeeze Breakouts Allowed = {report.stress.squeeze_breakout_allowed}, Min R:R = {report.stress.min_rr_threshold:.1f}:1
- Key Drivers: {", ".join(report.stress.key_drivers)}
"""

        system_prompt = (
            "You are a Senior Quantitative Macro Strategist at a systematic multi-strategy fund. "
            "Your objective is to give the human trader an accessible, educational tutorial briefing "
            "explaining the live quantitative macro numbers. "
            "Structure your briefing clearly with 4 distinct sections:\n"
            "1. 🌐 EXECUTIVE SUMMARY & MACRO REGIME (Plain English market tone)\n"
            "2. 📈 YIELD CURVE & TERM STRUCTURE TUTORIAL (Explain 10Y-2Y slope in bps, what regime means, why equities care)\n"
            "3. 💳 CREDIT SPREADS & LIQUIDITY (Explain HY OAS in bps, corporate credit health, and risk-on vs risk-off)\n"
            "4. 🛡️ COPILOT RISK ALLOCATION (Why the copilot chose this risk multiplier and allowed/blocked breakout strategies)\n"
            "Keep the tone professional, educational, punchy, and actionable. Avoid generic fluff."
        )

        if format_mode == "html":
            system_prompt += (
                " Target a concise briefing around 2,500 characters so it fits cleanly in a mobile Telegram card. "
                "Use ONLY valid Telegram HTML formatting (<b>bold</b>, <i>italic</i>, <code>code</code>). "
                "CRITICAL: Never use raw '<' or '>' mathematical symbols (write 'under 350 bps', 'over 2:1', or 'below 4%'). "
                "Do NOT use unsupported tags like <p>, <br>, <h1> or markdown headers."
            )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Please explain our live macro state:\n{prompt_context}"},
        ]

        model_name = self.config.llm_model if self.config else "openai/gpt-4o"
        resp = await litellm.acompletion(
            api_key=self.config.llm_api_key if self.config else None, model=model_name, messages=messages, timeout=35
        )
        content = resp.choices[0].message.content or ""
        if format_mode == "html":
            return TelegramHtmlFormatter.sanitize_telegram_html(content.strip())
        return content.strip()

    def explain_deterministic(self, report: MacroIntelligenceReport, format_mode: str = "text") -> str:
        """
        Exhaustive, rule-based quantitative educational breakdown of the live macro report.
        """
        spread_bps = report.spreads.slope_10y_2y_bps
        oas_bps = report.credit.high_yield_oas_bps
        vix = report.vix
        mult = report.stress.risk_multiplier
        level = report.stress.level.value
        curve_regime = report.spreads.regime.value
        credit_regime = report.credit.regime.value

        # 1. Curve interpretation
        if curve_regime == YieldCurveRegime.INVERTED:
            curve_desc = (
                f"The 10Y-2Y yield curve is INVERTED at {spread_bps:+.1f} bps (2Y yields higher than 10Y). "
                "Historically, inversion reflects tight central bank policy and signals recessionary headwinds. "
                "Equities experience multiple compression, and late-cycle volatility spikes are common."
            )
        elif curve_regime == YieldCurveRegime.FLAT:
            curve_desc = (
                f"The curve is FLAT at {spread_bps:+.1f} bps (0-25 bps). "
                "Banks face compressed net interest margins, and markets anticipate monetary transition."
            )
        elif curve_regime == YieldCurveRegime.NORMAL_STEEP:
            curve_desc = (
                f"The curve exhibits NORMAL upward slope at {spread_bps:+.1f} bps (25-150 bps). "
                "Term premiums are healthy, economic expansion is sustained, and financial intermediation operates smoothly."
            )
        else:
            curve_desc = (
                f"The curve is AGGRESSIVELY STEEP at {spread_bps:+.1f} bps (>150 bps). "
                "Often coincides with early-cycle recovery or fiscal term premium repricing."
            )

        # 2. Credit OAS interpretation
        if credit_regime == CreditStressRegime.BENIGN:
            credit_desc = (
                f"High Yield OAS is BENIGN at {oas_bps:.0f} bps (<350 bps). "
                "Corporate borrowers can refinance debt cheaply. Default probabilities remain subdued, "
                "signaling strong risk-on appetite and ample institutional liquidity."
            )
        elif credit_regime == CreditStressRegime.ELEVATED:
            credit_desc = (
                f"High Yield OAS is ELEVATED at {oas_bps:.0f} bps (350-500 bps). "
                "Credit spreads are widening as lenders demand higher risk premiums. "
                "High-beta equities and speculative assets face liquidity drag."
            )
        else:
            credit_desc = (
                f"High Yield OAS is in CRITICAL stress at {oas_bps:.0f} bps (>500 bps). "
                "Severe credit contraction. Liquidity dries up, corporate refinancing stalls, "
                "and systemic equity drawdowns are elevated."
            )

        # 3. Volatility interpretation
        if vix < 18.0:
            vix_desc = f"VIX at {vix:.2f} reflects a CALM market environment with ordered price discovery."
        elif vix <= 25.0:
            vix_desc = f"VIX at {vix:.2f} reflects ELEVATED uncertainty with active macro repricing."
        else:
            vix_desc = f"VIX at {vix:.2f} reflects PANIC/HIGH VOLATILITY with risk of sharp intraday whipsaws."

        # 4. Copilot Risk Engine Impact
        risk_desc = (
            f"The Copilot assessed overall Macro Stress as {level}.\n"
            f"• Position Sizing Risk Multiplier: {mult:.2f}x (Scales dollar risk per trade).\n"
            f"• Squeeze Breakouts: {'ALLOWED ✅' if report.stress.squeeze_breakout_allowed else 'BLOCKED 🛑 (Avoid breakout traps)'}.\n"
            f"• Minimum Reward-to-Risk Requirement: ≥ {report.stress.min_rr_threshold:.1f}:1."
        )

        if format_mode == "html":
            return (
                "🧭 <b>MACRO INTELLIGENCE TUTORIAL &amp; EXPLANATION</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "1️⃣ <b>Executive Summary &amp; Market Tone:</b>\n"
                f"Overall Macro Stress is <code>{level}</code> with risk budget at <code>{mult * 100:.0f}%</code>.\n\n"
                "2️⃣ <b>Yield Curve Term Structure (10Y - 2Y):</b>\n"
                f"• <i>Current Spread:</i> <code>{spread_bps:+.1f} bps</code> (Regime: <code>{curve_regime}</code>)\n"
                f"• <i>Explanation:</i> {html.escape(curve_desc)}\n\n"
                "3️⃣ <b>Credit Markets &amp; High Yield OAS:</b>\n"
                f"• <i>Current Spread:</i> <code>{oas_bps:.0f} bps</code> (Regime: <code>{credit_regime}</code>)\n"
                f"• <i>Explanation:</i> {html.escape(credit_desc)}\n\n"
                "4️⃣ <b>Volatility &amp; Currency Context:</b>\n"
                f"• <i>VIX:</i> <code>{vix:.2f}</code> | <i>DXY:</i> <code>{report.dxy:.2f}</code>\n"
                f"• <i>Context:</i> {html.escape(vix_desc)}\n\n"
                "5️⃣ <b>Copilot Risk Engine Actions:</b>\n"
                f"{html.escape(risk_desc)}\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "💡 <i>Tip: Run /macro for quick telemetry or /status for portfolio exposure.</i>"
            )

        return (
            "=" * 78 + "\n"
            "🧭 QUANTITATIVE MACRO INTELLIGENCE TUTORIAL & EXPLANATION\n"
            "=" * 78 + "\n\n"
            f"1. EXECUTIVE SUMMARY & MARKET TONE:\n"
            f"   Overall Macro Stress: {level} | Position Risk Multiplier: {mult:.2f}x\n\n"
            f"2. YIELD CURVE & TERM STRUCTURE (10Y - 2Y):\n"
            f"   Spread: {spread_bps:+.1f} bps (Regime: {curve_regime})\n"
            f"   Tutorial: {curve_desc}\n\n"
            f"3. CREDIT MARKETS & HIGH YIELD OAS:\n"
            f"   Spread: {oas_bps:.0f} bps (Regime: {credit_regime})\n"
            f"   Tutorial: {credit_desc}\n\n"
            f"4. VOLATILITY & CURRENCY (VIX & DXY):\n"
            f"   VIX: {vix:.2f} | DXY: {report.dxy:.2f}\n"
            f"   Context: {vix_desc}\n\n"
            f"5. COPILOT RISK ENGINE ACTIONS:\n"
            f"   {risk_desc}\n"
            "=" * 78
        )
