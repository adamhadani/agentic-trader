"""Institutional reporting formatters for Pairs Trading and Cointegration analysis."""

from __future__ import annotations

import math

from agentic_trader.pairs.models import PairEvaluation, SignalType


def format_pairs_report(evaluations: list[PairEvaluation]) -> str:
    """Format pairs screening results into an institutional ASCII table."""
    if not evaluations:
        return "No pair candidates evaluated."

    lines = [
        "==========================================================================================================",
        "                               STATISTICAL PAIRS TRADING & COINTEGRATION SCREENER                         ",
        "==========================================================================================================",
        f"{'Pair':<12} | {'Hedge β':<8} | {'ADF p-val':<9} | {'Half-Life':<11} | {'Spread':<9} | {'Z-Score':<8} | {'Signal':<12} | {'Action':<15}",
        "-------------+----------+-----------+-------------+-----------+----------+--------------+-----------------",
    ]

    actionable_count = 0
    coint_count = 0

    for item in evaluations:
        coint = item.coint_result
        sig = item.signal

        if coint.is_cointegrated:
            coint_count += 1
        if item.is_actionable:
            actionable_count += 1

        hl_str = f"{coint.half_life_bars:.1f} bars" if not math.isinf(coint.half_life_bars) else "inf"
        p_val_str = f"{coint.p_value:.4f}" + ("*" if coint.is_cointegrated else " ")
        action_str = f"{sig.asset_y_action} {item.asset_y} / {sig.asset_x_action} {item.asset_x}"

        lines.append(
            f"{item.pair_name:<12} | "
            f"{coint.hedge_ratio_beta:>8.4f} | "
            f"{p_val_str:>9} | "
            f"{hl_str:>11} | "
            f"{sig.current_spread:>9.2f} | "
            f"{sig.z_score:>+8.2f} | "
            f"{sig.signal.value:<12} | "
            f"{action_str:<15}"
        )

    lines.append(
        "----------------------------------------------------------------------------------------------------------"
    )
    lines.append(f"Total Pairs Screened : {len(evaluations)}")
    lines.append(f"Cointegrated (p<0.05): {coint_count}")
    lines.append(f"Actionable Signals   : {actionable_count}")
    lines.append(
        "=========================================================================================================="
    )

    return "\n".join(lines)


def format_pairs_telegram(evaluations: list[PairEvaluation]) -> str:
    """Format pairs screening results into an HTML message for Telegram."""
    if not evaluations:
        return "📊 <b>Statistical Pairs Screener</b>\n\nNo pairs evaluated."

    actionable = [e for e in evaluations if e.is_actionable]
    display_list = actionable if actionable else evaluations[:5]

    lines = [
        "📊 <b>Statistical Pairs Arbitrage Screener</b>",
        f"<i>Screened {len(evaluations)} pairs | {len(actionable)} actionable signals</i>",
        "",
    ]

    for item in display_list:
        coint = item.coint_result
        sig = item.signal

        if sig.signal == SignalType.BUY_SPREAD:
            icon = "🟢"
        elif sig.signal == SignalType.SELL_SPREAD:
            icon = "🔴"
        elif sig.signal == SignalType.EXIT_SPREAD:
            icon = "🔄"
        else:
            icon = "⚪"

        coint_badge = "✅ Cointegrated" if coint.is_cointegrated else "⚠️ Weak Cointegration"
        hl_str = f"{coint.half_life_bars:.1f} bars" if not math.isinf(coint.half_life_bars) else "inf"

        lines.append(f"{icon} <b>{item.pair_name}</b> ({coint_badge})")
        lines.append(f"• <b>Hedge Ratio (β)</b>: <code>{coint.hedge_ratio_beta:.4f}</code>")
        lines.append(
            f"• <b>ADF p-value</b>: <code>{coint.p_value:.4f}</code> | <b>Half-Life</b>: <code>{hl_str}</code>"
        )
        lines.append(
            f"• <b>Spread</b>: <code>{sig.current_spread:.2f}</code> | <b>Z-Score</b>: <b>{sig.z_score:+.2f}</b>"
        )
        lines.append(f"• <b>Signal</b>: <b>{sig.signal.value}</b>")
        lines.append(f"• <b>Recommendation</b>: <i>{sig.summary}</i>")
        lines.append("")

    return "\n".join(lines)
