from __future__ import annotations

import html

from agentic_trader.options.models import GammaExposureProfile, GammaRegime


def format_gex_report(profile: GammaExposureProfile) -> str:
    """Renders institutional ASCII table of Gamma Exposure, Pinning Walls, and Regime."""
    lines: list[str] = []
    lines.append("=" * 80)
    lines.append(f"  Estimate source: {profile.source} | As of: {profile.timestamp.isoformat()}")
    if profile.data_quality_notes:
        lines.append("  Data quality: " + "; ".join(profile.data_quality_notes))
    lines.append(f"  OPTIONS GAMMA EXPOSURE (GEX) & VOLATILITY SURFACE: {profile.symbol.upper()}")
    lines.append("=" * 80)

    # Regime formatting
    if profile.gamma_regime == GammaRegime.POSITIVE_GAMMA:
        regime_desc = "POSITIVE GAMMA (+GEX: Volatility Suppressed, Mean-Reversion Favored)"
    elif profile.gamma_regime == GammaRegime.NEGATIVE_GAMMA:
        regime_desc = "NEGATIVE GAMMA (-GEX: Volatility Amplified, Trend / Breakouts Favored)"
    else:
        regime_desc = "NEUTRAL GAMMA (Balanced Dealer Inventory)"

    flip_str = f"${profile.gamma_flip_strike:,.2f}" if profile.gamma_flip_strike else "N/A (Uniform Skew)"

    lines.append(f"  Underlying Spot Price : ${profile.underlying_price:,.2f}")
    lines.append(f"  Gamma Regime          : {regime_desc}")
    sign = "+" if profile.total_net_gex >= 0 else ""
    lines.append(f"  Total Net GEX         : {sign}${profile.total_net_gex:,.2f}M per 1% move")
    lines.append(f"  Total Call / Put GEX  : +${profile.total_call_gex:,.2f}M / ${profile.total_put_gex:,.2f}M")
    lines.append(f"  Gamma Flip Level      : {flip_str}")
    lines.append("-" * 80)
    lines.append("  KEY STRUCTURAL PINNING WALLS")
    lines.append(f"  • Call Wall (Resistance / Pin) : ${profile.call_wall_strike:,.2f}  (OI: {profile.call_wall_oi:,})")
    lines.append(f"  • Put Wall (Support / Floor)   : ${profile.put_wall_strike:,.2f}  (OI: {profile.put_wall_oi:,})")
    lines.append("-" * 80)
    lines.append("  SENTIMENT & VOLUME FLOWS")
    lines.append(f"  • Put / Call OI Ratio          : {profile.pcr_open_interest:.3f}")
    lines.append(f"  • Put / Call Volume Ratio      : {profile.pcr_volume:.3f}")
    if profile.expirations_analyzed:
        lines.append(f"  • Expirations Analyzed         : {', '.join(profile.expirations_analyzed)}")
    lines.append("-" * 80)

    # Top Gamma Concentration Strikes near spot
    if profile.strikes:
        lines.append("  NEAR-MONEY GAMMA CONCENTRATION BY STRIKE ($ Millions / 1% move)")
        lines.append(
            f"  {'Strike':<10} {'Call GEX':<12} {'Put GEX':<12} {'Net GEX':<12} {'Call OI':<10} {'Put OI':<10} {'Pin Target':<10}"
        )
        lines.append("  " + "-" * 76)

        # Filter strikes within +/- 8% of spot
        spot = profile.underlying_price
        near_strikes = [s for s in profile.strikes if 0.92 * spot <= s.strike <= 1.08 * spot]
        if not near_strikes:
            near_strikes = sorted(profile.strikes, key=lambda s: abs(s.strike - spot))[:10]

        for s in sorted(near_strikes, key=lambda x: x.strike):
            marker = ""
            if abs(s.strike - profile.call_wall_strike) < 0.01:
                marker = "CALL WALL"
            elif abs(s.strike - profile.put_wall_strike) < 0.01:
                marker = "PUT WALL"
            elif profile.gamma_flip_strike and abs(s.strike - profile.gamma_flip_strike) < 2.0:
                marker = "FLIP"
            elif abs(s.strike - spot) < (spot * 0.005):
                marker = "<-- SPOT"

            c_str = f"+${s.call_gex:,.2f}M"
            p_str = f"-${abs(s.put_gex):,.2f}M"
            net_str = f"{'+' if s.net_gex >= 0 else ''}${s.net_gex:,.2f}M"

            lines.append(
                f"  ${s.strike:<9.2f} {c_str:<12} {p_str:<12} {net_str:<12} {s.call_oi:<10,} {s.put_oi:<10,} {marker:<10}"
            )

    lines.append("=" * 80)
    return "\n".join(lines)


def format_gex_telegram(profile: GammaExposureProfile) -> str:
    """Renders HTML-formatted card for mobile Telegram alerts and command responses."""
    if profile.gamma_regime == GammaRegime.POSITIVE_GAMMA:
        regime_badge = "🟢 <b>POSITIVE GAMMA (+GEX)</b>"
        regime_tip = "<i>Volatility suppressed; mean-reversion & dip-buying favored.</i>"
    elif profile.gamma_regime == GammaRegime.NEGATIVE_GAMMA:
        regime_badge = "🔴 <b>NEGATIVE GAMMA (-GEX)</b>"
        regime_tip = "<i>Volatility amplified; dealer momentum selling / breakout acceleration.</i>"
    else:
        regime_badge = "⚪ <b>NEUTRAL GAMMA</b>"
        regime_tip = "<i>Balanced dealer gamma distribution.</i>"

    sign = "+" if profile.total_net_gex >= 0 else ""
    flip_val = f"<code>${profile.gamma_flip_strike:,.2f}</code>" if profile.gamma_flip_strike else "<i>None</i>"

    text = (
        f"🧭 <b>GAMMA EXPOSURE: {html.escape(profile.symbol.upper())}</b>\n"
        f"• <b>Spot Price:</b> <code>${profile.underlying_price:,.2f}</code>\n"
        f"• <b>Regime:</b> {regime_badge}\n"
        f"• <b>Net GEX:</b> <code>{sign}${profile.total_net_gex:,.2f}M / 1%</code>\n"
        f"• <b>Gamma Flip:</b> {flip_val}\n\n"
        f"🧱 <b>Key Institutional Walls</b>\n"
        f"• <b>Call Wall:</b> <code>${profile.call_wall_strike:,.2f}</code> (OI: {profile.call_wall_oi:,})\n"
        f"• <b>Put Wall:</b> <code>${profile.put_wall_strike:,.2f}</code> (OI: {profile.put_wall_oi:,})\n"
        f"• <b>Put/Call OI:</b> <code>{profile.pcr_open_interest:.2f}</code> | <b>Vol:</b> <code>{profile.pcr_volume:.2f}</code>\n\n"
        f"{regime_tip}\n"
        f"<i>Estimate: {html.escape(profile.source)}. {profile.timestamp:%Y-%m-%d %H:%M UTC}</i>\n"
    )
    if profile.data_quality_notes:
        text += "⚠️ <b>Incomplete chain:</b> " + html.escape("; ".join(profile.data_quality_notes))
    return text
