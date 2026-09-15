from __future__ import annotations

import html
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from agentic_trader.agent.macro import MacroIntelligenceReport
    from agentic_trader.agent.regime import RegimeSnapshot
    from agentic_trader.backtest.models import BacktestResult
    from agentic_trader.research.alpha.models import AlphaCandidate, PromotedAlphaRecord


@dataclass
class PositionView:
    """Read-only presentation projection of an active tracked position."""

    id: int
    contract: str
    direction: str
    quantity: float
    entry_price: float
    current_price: float
    stop_loss: float
    take_profit: float
    unrealized_pnl: float
    pnl_str: str = ""
    qty_label: str = ""
    multiplier: float = 1.0
    strategy: str = ""
    executed_at: str | None = None

    def __post_init__(self):
        if not self.pnl_str:
            sign = "+" if self.unrealized_pnl >= 0 else "-"
            self.pnl_str = f"{sign}${abs(self.unrealized_pnl):,.2f}"
        if not self.qty_label:
            self.qty_label = f"{self.quantity:g} shares" if not self.contract.startswith("/") else f"{self.quantity:g}x"


@dataclass
class PositionsReport:
    """Aggregated presentation report of all active tracked positions."""

    positions: list[PositionView]
    total_unrealized_pnl: float
    active_count: int
    total_realized_pnl: float = 0.0
    total_pnl_str: str = ""

    def __post_init__(self):
        if not self.total_pnl_str:
            pnl_sign = "+" if self.total_unrealized_pnl >= 0 else "-"
            self.total_pnl_str = f"{pnl_sign}${abs(self.total_unrealized_pnl):,.2f}"


@dataclass
class PortfolioStatusReport:
    """Structured operational snapshot of the portfolio, risk metrics, and market regime."""

    cash_base: float
    max_notional: float = 0.0
    current_exposure: float = 0.0
    effective_leverage: float = 0.0
    active_contract_count: int = 0
    telegram_configured: bool = False
    llm_model: str = ""
    macro_summary: str = ""
    vix_regime: str = ""
    vix_value: float = 0.0
    tnx_value: float | None = None
    dxy_value: float | None = None
    breakout_allowed: bool = True
    recent_signals: list[dict[str, Any]] = None  # type: ignore

    # Aliases and extra fields
    max_notional_exposure: float = 0.0
    active_exposure: float = 0.0
    active_position_count: int = 0
    macro_calendar_summary: str = ""
    vix: float = 0.0
    tnx: float | None = None
    dxy: float | None = None
    execution_mode: str = "PAPER"

    def __post_init__(self):
        if self.recent_signals is None:
            self.recent_signals = []
        if self.max_notional_exposure and not self.max_notional:
            self.max_notional = self.max_notional_exposure
        elif self.max_notional and not self.max_notional_exposure:
            self.max_notional_exposure = self.max_notional

        if self.active_exposure and not self.current_exposure:
            self.current_exposure = self.active_exposure
        elif self.current_exposure and not self.active_exposure:
            self.active_exposure = self.current_exposure

        if self.active_position_count and not self.active_contract_count:
            self.active_contract_count = self.active_position_count
        elif self.active_contract_count and not self.active_position_count:
            self.active_position_count = self.active_contract_count

        if self.macro_calendar_summary and not self.macro_summary:
            self.macro_summary = self.macro_calendar_summary
        elif self.macro_summary and not self.macro_calendar_summary:
            self.macro_calendar_summary = self.macro_summary

        if self.vix and not self.vix_value:
            self.vix_value = self.vix
        elif self.vix_value and not self.vix:
            self.vix = self.vix_value

        if self.tnx is not None and self.tnx_value is None:
            self.tnx_value = self.tnx
        elif self.tnx_value is not None and self.tnx is None:
            self.tnx = self.tnx_value

        if self.dxy is not None and self.dxy_value is None:
            self.dxy_value = self.dxy
        elif self.dxy_value is not None and self.dxy is None:
            self.dxy = self.dxy_value


@dataclass
class ExecutionResultView:
    """Presentation view model for an order execution result."""

    signal_id: int
    contract: str
    direction: str
    quantity: float = 1.0
    fill_price: float | None = None
    order_id: str | None = None
    broker_order_id: str | None = None
    notional_value: float = 0.0
    risk_dollars: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    execution_mode: str = "PAPER"
    success: bool = True
    error_message: str | None = None

    def __post_init__(self):
        if self.broker_order_id and not self.order_id:
            self.order_id = self.broker_order_id
        elif self.order_id and not self.broker_order_id:
            self.broker_order_id = self.order_id


@dataclass
class ManualCloseResultView:
    """Presentation view model for a manually closed position."""

    signal_id: int
    contract: str
    direction: str
    exit_price: float = 0.0
    realized_pnl: float = 0.0
    quantity: float = 1.0
    success: bool = True
    error_message: str | None = None


@dataclass
class PerformanceSummaryReport:
    """Structured performance metrics report across closed positions."""

    total_pnl: float
    win_rate: float
    wins: int
    losses: int
    profit_factor: float
    gross_profit: float
    gross_loss: float
    active_count: int = 0
    active_exposure: float = 0.0
    recent_closed_trades: list[dict[str, Any]] = None  # type: ignore

    # Aliases
    active_open_positions: int = 0
    active_notional_exposure: float = 0.0
    recent_trades: list[dict[str, Any]] = None  # type: ignore

    def __post_init__(self):
        if self.recent_closed_trades is None and self.recent_trades is not None:
            self.recent_closed_trades = self.recent_trades
        elif self.recent_closed_trades is None:
            self.recent_closed_trades = []

        if self.active_open_positions and not self.active_count:
            self.active_count = self.active_open_positions
        elif self.active_count and not self.active_open_positions:
            self.active_open_positions = self.active_count

        if self.active_notional_exposure and not self.active_exposure:
            self.active_exposure = self.active_notional_exposure
        elif self.active_exposure and not self.active_notional_exposure:
            self.active_notional_exposure = self.active_exposure


@dataclass
class PanicReportView:
    """Presentation view model for emergency kill switch liquidation and trading halt."""

    cancelled_orders_count: int
    liquidated_positions_count: int
    total_realized_pnl: float
    is_halted: bool
    halt_reason: str
    closed_positions: list[dict[str, Any]] = None  # type: ignore
    success: bool = True
    error_message: str | None = None

    def __post_init__(self):
        if self.closed_positions is None:
            self.closed_positions = []


class TerminalFormatter:
    """Renders formatted plain text / ASCII tables for CLI stdout output."""

    @staticmethod
    def format_positions_table(report: PositionsReport) -> str:
        sep = "=" * 65
        sub_sep = "-" * 65
        lines = [
            sep,
            "CASH-PLUS TRADING COPILOT: ACTIVE POSITIONS",
            sep,
        ]
        if not report.positions:
            lines.append("  (No active positions currently tracked)")
        else:
            lines.extend(
                f"  #{p.id} {p.qty_label} {p.contract} {p.direction} | "
                f"Entry: {p.entry_price:,.2f} | Current: {p.current_price:,.2f} | "
                f"Stop: {p.stop_loss:,.2f} | Target: {p.take_profit:,.2f} | PnL: {p.pnl_str}"
                for p in report.positions
            )
            lines.append(sub_sep)
            lines.append(f"Total Unrealized PnL: {report.total_pnl_str}")
        lines.append(sep)
        return "\n".join(lines)

    @staticmethod
    def format_status_dashboard(report: PortfolioStatusReport) -> str:
        sep = "=" * 65
        sub_sep = "-" * 65
        lines = [
            sep,
            "CASH-PLUS TRADING COPILOT: PORTFOLIO & RISK STATUS",
            sep,
            f"Cash Base:            ${report.cash_base:,.2f}",
            f"Max Notional Ceiling: ${report.max_notional:,.2f} (0.6x max leverage)",
            f"Active Exposure:      ${report.current_exposure:,.2f} ({report.effective_leverage:.2f}x effective leverage)",
            f"Active Position Count:{report.active_contract_count} contracts",
            f"Telegram Configured:  {report.telegram_configured}",
            f"LLM Model Configured: {report.llm_model}",
            sub_sep,
            "Macro Calendar Context:",
            report.macro_summary,
            sub_sep,
            "Market Volatility & Macro Regime Context:",
            f"  • Volatility Regime: {report.vix_regime} (VIX: {report.vix_value:.2f})",
            f"  • 10Y Yield (^TNX):  {f'{report.tnx_value:.2f}%' if report.tnx_value is not None else 'N/A'}",
            f"  • Dollar Index (DXY):{f'{report.dxy_value:.2f}' if report.dxy_value is not None else 'N/A'}",
            f"  • Breakouts Status:  {'Allowed' if report.breakout_allowed else 'Suppressed (Extreme Volatility)'}",
            sub_sep,
            f"Recent Signals ({len(report.recent_signals)}):",
        ]
        if not report.recent_signals:
            lines.append("  (No signals in database)")
        else:
            lines.extend(
                f"  #{s['id']} [{s['status']}] {s['timestamp']} | {s['contract']} {s['direction']} "
                f"via {s['strategy']} @ {s['entry_price']} (Risk: ${s['risk_dollars']:.2f})"
                for s in report.recent_signals
            )
        lines.append(sep)
        return "\n".join(lines)

    @staticmethod
    def format_panic_report(view: PanicReportView) -> str:
        sep = "=" * 65
        sub_sep = "-" * 65
        pnl_sign = "+" if view.total_realized_pnl >= 0 else "-"
        pnl_str = f"{pnl_sign}${abs(view.total_realized_pnl):,.2f}"
        lines = [
            sep,
            "EMERGENCY KILL SWITCH: LIQUIDATION & TRADING HALT REPORT",
            sep,
            f"Status:               {'HALTED (Trading Disabled)' if view.is_halted else 'ACTIVE'}",
            f"Reason:               {view.halt_reason}",
            f"Orders Cancelled:     {view.cancelled_orders_count}",
            f"Positions Liquidated: {view.liquidated_positions_count}",
            f"Total Realized P&L:   {pnl_str}",
            sub_sep,
        ]
        if view.closed_positions:
            lines.append("Liquidated Positions:")
            for p in view.closed_positions:
                c = p.get("contract", "")
                d = p.get("direction", "")
                q = float(p.get("quantity") or 1.0)
                pnl = float(p.get("realized_pnl") or 0.0)
                sign = "+" if pnl >= 0 else "-"
                lines.append(f"  • #{p.get('id')} {c} ({d} {q:g}x) -> {sign}${abs(pnl):,.2f}")
            lines.append(sub_sep)
        lines.append("Use 'copilot resume' or '/resume' to clear halt.")
        lines.append(sep)
        return "\n".join(lines)


class TelegramHtmlFormatter:
    """Renders formatted HTML payloads for Telegram bot notifications and commands."""

    @staticmethod
    def format_positions_html(report: PositionsReport) -> str:
        if not report.positions:
            return (
                "📋 <b>ACTIVE POSITIONS (0)</b>\n\n"
                "<i>No active positions currently tracked.</i>\n"
                "When trade signals are acknowledged in Telegram, they appear here."
            )

        lines = [f"📋 <b>ACTIVE POSITIONS ({report.active_count})</b>\n"]
        lines.extend(
            f"• <b>#{p.id} {p.qty_label} {p.contract} ({p.direction})</b>\n"
            f"  Entry: <code>{p.entry_price:,.2f}</code> | Current: <code>{p.current_price:,.2f}</code>\n"
            f"  Stop: <code>{p.stop_loss:,.2f}</code> | Target: <code>{p.take_profit:,.2f}</code>\n"
            f"  Unrealized P&amp;L: <b>{p.pnl_str}</b>\n"
            for p in report.positions
        )

        lines.append(f"\n<b>Total Unrealized P&amp;L:</b> {report.total_pnl_str}")
        lines.append("\n💡 <i>To close a trade manually:</i> <code>/close &lt;id&gt; [exit_price]</code>")
        return "\n".join(lines)

    @staticmethod
    def format_status_html(report: PortfolioStatusReport) -> str:
        lines = [
            "🛡️ <b>CASH-PLUS COPILOT STATUS</b>\n",
            f"• <b>Cash Base:</b> <code>${report.cash_base:,.2f}</code>",
            f"• <b>Max Notional:</b> <code>${report.max_notional:,.2f}</code>",
            (
                f"• <b>Active Exposure:</b> <code>${report.current_exposure:,.2f}</code> "
                f"({report.effective_leverage:.2f}x leverage)"
            ),
            f"• <b>Active Positions:</b> {report.active_contract_count} contracts\n",
            "<b>Market Context:</b>",
            f"• Volatility: <b>{report.vix_regime}</b> (VIX: {report.vix_value:.2f})",
        ]
        if report.tnx_value is not None:
            lines.append(f"• 10Y Yield: <code>{report.tnx_value:.2f}%</code>")
        if report.dxy_value is not None:
            lines.append(f"• US Dollar Index: <code>{report.dxy_value:.2f}</code>")

        lines.append("\n<b>Recent Signals:</b>")
        if not report.recent_signals:
            lines.append("<i>(No recorded signals)</i>")
        else:
            lines.extend(
                f"• #{s['id']} [{s['status']}] {s['contract']} {s['direction']} "
                f"@ {s['entry_price']:,.2f} (Risk: ${s['risk_dollars']:.2f})"
                for s in report.recent_signals
            )

        return "\n".join(lines)

    @staticmethod
    def format_execution_html(view: ExecutionResultView, execution_mode: str | None = None) -> str:
        mode_val = execution_mode or getattr(view, "execution_mode", "PAPER") or "PAPER"
        mode_upper = str(mode_val).upper()
        if view.success:
            fill_p = view.fill_price or 0.0
            qty_label = f"{view.quantity:g} shares" if not view.contract.startswith("/") else f"{view.quantity:g}x"
            return (
                f"🚀 <b>ORDER EXECUTED ({mode_upper})</b>\n"
                f"• <b>Contract:</b> {qty_label} {view.contract} ({view.direction})\n"
                f"• <b>Fill Price:</b> <code>{fill_p:,.2f}</code>\n"
                f"• <b>Broker Order ID:</b> <code>{view.order_id or 'N/A'}</code>\n"
                f"• <b>Notional:</b> <code>${view.notional_value:,.2f}</code> | "
                f"<b>Risk:</b> <code>${view.risk_dollars:,.2f}</code>\n"
                f"• <b>Stop Loss:</b> <code>{view.stop_loss:,.2f}</code> | "
                f"<b>Target:</b> <code>{view.take_profit:,.2f}</code>\n"
                f"• <i>Position is now active in risk tracking.</i>"
            )
        else:
            return (
                f"❌ <b>Execution Failed ({mode_upper}):</b>\n"
                f"• <b>Contract:</b> {view.contract} ({view.direction})\n"
                f"• <b>Error:</b> {view.error_message or 'Unknown error'}"
            )

    @staticmethod
    def format_manual_close_html(view: ManualCloseResultView) -> str:
        if not view.success:
            return f"❌ {view.error_message}"

        pnl_sign = "+" if view.realized_pnl >= 0 else "-"
        return (
            f"✅ <b>Position #{view.signal_id} Closed ({view.contract} {view.direction})</b>\n"
            f"• Exit Price: <code>{view.exit_price:,.2f}</code>\n"
            f"• Realized P&amp;L: <b>{pnl_sign}${abs(view.realized_pnl):,.2f}</b>\n"
            f"• Notional capacity released."
        )

    @staticmethod
    def format_regime_html(regime: RegimeSnapshot) -> str:
        vix_color = "🟢" if regime.vix < 15.0 else ("🟡" if regime.vix < 22.0 else "🔴")
        tnx_str = f"{regime.tnx:.2f}%" if regime.tnx is not None else "N/A"
        dxy_str = f"{regime.dxy:.2f}" if regime.dxy is not None else "N/A"
        breakout_str = "Allowed ✅" if regime.breakout_allowed else "Suppressed ⚠️ (Extreme Volatility)"

        return (
            "🌐 <b>MARKET VOLATILITY & MACRO REGIME</b>\n\n"
            f"• <b>VIX Level:</b> {vix_color} <code>{regime.vix:.2f}</code> ({regime.vix_regime.value.upper()})\n"
            f"• <b>10-Year Treasury Yield (^TNX):</b> <code>{tnx_str}</code>\n"
            f"• <b>US Dollar Index (DX-Y):</b> <code>{dxy_str}</code>\n"
            f"• <b>Squeeze Breakouts:</b> <b>{breakout_str}</b>\n\n"
            f"📝 <b>Quantitative Assessment:</b>\n"
            f"<i>{regime.summary_text}</i>"
        )

    @staticmethod
    def format_macro_dashboard_html(report: MacroIntelligenceReport) -> str:
        s = report.stress
        y = report.yields
        sp = report.spreads
        c = report.credit
        inf = report.inflation

        stress_badge = {
            "LOW": "🟢 LOW STRESS",
            "MODERATE": "🟡 MODERATE STRESS",
            "HIGH": "🟠 HIGH STRESS",
            "EXTREME": "🔴 EXTREME STRESS",
        }.get(s.level.value, s.level.value)

        curve_color = "🟢" if sp.regime == "NORMAL_STEEP" else ("🟡" if sp.regime in ("FLAT", "STEEP") else "🔴")
        credit_color = "🟢" if c.regime == "BENIGN" else ("🟡" if c.regime == "ELEVATED" else "🔴")
        breakout_str = "Allowed ✅" if s.squeeze_breakout_allowed else "Suppressed ⚠️"

        drivers_text = ", ".join(s.key_drivers) if s.key_drivers else "Benign conditions"

        return (
            "🏛️ <b>MACRO INTELLIGENCE & YIELD CURVE</b>\n\n"
            f"<b>Status:</b> {stress_badge} (Risk Multiplier: <code>{s.risk_multiplier:.2f}x</code>)\n\n"
            f"📈 <b>US Treasury Term Structure:</b> {curve_color} <code>{sp.regime.value}</code>\n"
            f"• <b>3M:</b> <code>{y.yield_3m:.2f}%</code> | <b>2Y:</b> <code>{y.yield_2y:.2f}%</code> | <b>5Y:</b> <code>{y.yield_5y:.2f}%</code>\n"
            f"• <b>10Y:</b> <code>{y.yield_10y:.2f}%</code> | <b>30Y:</b> <code>{y.yield_30y:.2f}%</code>\n"
            f"• <b>10Y-2Y Slope:</b> <code>{sp.slope_10y_2y_bps:+.1f} bps</code>\n"
            f"• <b>10Y-3M Slope:</b> <code>{sp.slope_10y_3m_bps:+.1f} bps</code>\n"
            f"• <b>Butterfly Curvature:</b> <code>{sp.curvature_butterfly_bps:+.1f} bps</code>\n\n"
            f"💳 <b>Credit Risk & Inflation Expectations:</b>\n"
            f"• <b>High Yield OAS:</b> {credit_color} <code>{c.high_yield_oas_bps:.0f} bps</code> ({c.high_yield_oas_pct:.2f}%) | <b>{c.regime.value}</b>\n"
            f"• <b>10Y Breakeven Inflation:</b> <code>{inf.breakeven_10y:.2f}%</code> ({inf.regime.value})\n"
            f"• <b>5Y Breakeven Inflation:</b> <code>{inf.breakeven_5y:.2f}%</code>\n\n"
            f"🌪️ <b>Volatility & Dollar:</b>\n"
            f"• <b>CBOE VIX:</b> <code>{report.vix:.2f}</code> | <b>DXY:</b> <code>{report.dxy:.2f}</code>\n\n"
            f"🛡️ <b>Strategy Policy & Risk Budget:</b>\n"
            f"• <b>Squeeze Breakouts:</b> <b>{breakout_str}</b>\n"
            f"• <b>Minimum Required R:R:</b> <code>{s.min_rr_threshold:.1f}:1</code>\n"
            f"• <b>Key Drivers:</b> <i>{html.escape(drivers_text)}</i>"
        )

    @staticmethod
    def format_performance_html(report: PerformanceSummaryReport) -> str:
        pnl_sign = "+" if report.total_pnl >= 0 else "-"
        abs_pnl = abs(report.total_pnl)
        color_pnl = "🟢" if report.total_pnl >= 0 else "🔴"
        pf_str = f"{report.profit_factor:.2f}" if report.profit_factor != float("inf") else "∞"

        recent_trades_text = ""
        if not report.recent_closed_trades:
            recent_trades_text = "\n<i>(No closed trades recorded yet)</i>"
        else:
            for t in report.recent_closed_trades[:5]:
                t_pnl = t.get("realized_pnl") or 0.0
                t_sign = "+" if t_pnl >= 0 else "-"
                recent_trades_text += (
                    f"\n• #{t['id']} <b>{t['contract']}</b> ({t['direction']}) "
                    f"via {t['strategy']}: {t_sign}${abs(t_pnl):,.2f} [{t.get('exit_reason') or 'CLOSED'}]"
                )

        return (
            "📊 <b>CASH-PLUS COPILOT: PERFORMANCE ATTRIBUTION</b>\n\n"
            f"• <b>Realized Net Alpha:</b> {color_pnl} <code>{pnl_sign}${abs_pnl:,.2f}</code>\n"
            f"• <b>Win Rate:</b> <b>{report.win_rate:.1f}%</b> ({report.wins} wins / {report.losses} losses)\n"
            f"• <b>Profit Factor:</b> <code>{pf_str}</code>\n"
            f"• <b>Gross Profits:</b> +${report.gross_profit:,.2f}\n"
            f"• <b>Gross Losses:</b> -${report.gross_loss:,.2f}\n"
            f"• <b>Active Open Risk:</b> {report.active_count} positions (${report.active_exposure:,.2f} notional)\n\n"
            f"🕒 <b>Recent Closed Trades:</b>{recent_trades_text}"
        )

    @staticmethod
    def format_backtest_html(
        res: BacktestResult,
        symbol: str = "SPY",
        lookback: str = "1y",
        symbols: list[str] | None = None,
        strategy: str = "all",
        mc_line: str = "",
        attr_line: str = "",
    ) -> str:
        sym = symbol if not symbols else ",".join(s.upper() for s in symbols)
        pf_str = f"{res.profit_factor:.2f}" if res.profit_factor != float("inf") else "∞"

        mc = getattr(res, "monte_carlo", None)
        if not mc_line and mc is not None:
            mc_line = f"\n• <b>95% Worst DD (Monte Carlo):</b> <code>{mc.ci_95th_drawdown_pct:.1f}%</code> (95% VaR: {mc.var_95_pct:.1f}%)"

        if not attr_line and res.attribution and res.attribution.factors:
            top_f = max(res.attribution.factors, key=lambda f: f.pnl_dollars)
            if top_f.pnl_dollars != 0:
                attr_line = f"\n• <b>Top Driver:</b> {top_f.factor_name} (+${top_f.pnl_dollars:,.2f})"

        return (
            f"📈 <b>BACKTEST SIMULATION: {sym} ({lookback})</b>\n\n"
            f"• <b>Total Net Return:</b> <code>{res.combined_return_pct:+.2f}%</code>\n"
            f"• <b>Annualized Return (CAGR):</b> <code>{res.annualized_return_pct:+.2f}%</code>\n"
            f"• <b>Sharpe Ratio:</b> <code>{res.sharpe_ratio:.2f}</code>\n"
            f"• <b>Max Drawdown:</b> <code>{res.max_drawdown_pct:.2f}%</code>\n"
            f"• <b>Win Rate:</b> <code>{res.win_rate:.1f}%</code> ({res.total_trades} trades)\n"
            f"• <b>Profit Factor:</b> <code>{pf_str}</code>\n"
            f"• <b>Cash-Plus Yield Accrued:</b> +${res.cash_yield_pnl:,.2f}"
            f"{attr_line}"
            f"{mc_line}"
        )

    @staticmethod
    def format_panic_html(view: PanicReportView) -> str:
        pnl_sign = "+" if view.total_realized_pnl >= 0 else "-"
        pnl_str = f"{pnl_sign}${abs(view.total_realized_pnl):,.2f}"
        pnl_color = "🟢" if view.total_realized_pnl >= 0 else "🔴"

        lines = [
            "🚨 <b>EMERGENCY KILL SWITCH ACTIVATED</b> 🚨",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "• <b>Status:</b> 🛑 <b>TRADING HALTED</b>",
            f"• <b>Reason:</b> <i>{html.escape(view.halt_reason)}</i>",
            f"• <b>Orders Cancelled:</b> <code>{view.cancelled_orders_count}</code>",
            f"• <b>Positions Liquidated:</b> <code>{view.liquidated_positions_count}</code>",
            f"• <b>Total Realized P&L:</b> {pnl_color} <code>{pnl_str}</code>",
        ]
        if view.closed_positions:
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("<b>Liquidated Positions:</b>")
            for p in view.closed_positions:
                c = html.escape(str(p.get("contract", "")))
                d = html.escape(str(p.get("direction", "")))
                q = float(p.get("quantity") or 1.0)
                pnl = float(p.get("realized_pnl") or 0.0)
                sign = "+" if pnl >= 0 else "-"
                lines.append(f"  • #{p.get('id')} <code>{c}</code> ({d} {q:g}x) ➔ <code>{sign}${abs(pnl):,.2f}</code>")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("⚠️ <i>All automated scans and order executions are strictly HALTED.</i>")
        lines.append("👉 Send <code>/resume</code> to clear the kill switch and restore normal operations.")
        return "\n".join(lines)

    @staticmethod
    def format_alphas_dashboard_html(
        promoted: list[PromotedAlphaRecord],
        catalog_count: int = 0,
    ) -> str:
        """Render active promoted formulaic alphas as an institutional Telegram HTML card."""
        lines = [
            "🧪 <b>FORMULAIC ALPHA INTELLIGENCE</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"• <b>Active Promoted Alphas:</b> <code>{len(promoted)}</code>",
            f"• <b>Catalog Library Size:</b> <code>{catalog_count}</code> institutional formulas",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]

        if not promoted:
            lines.append("<i>No formulaic alphas currently active in production desk.</i>")
            lines.append("👉 Run <code>copilot alpha mine --auto-promote</code> to discover candidates.")
        else:
            lines.append("<b>Active Production Alphas:</b>")
            for a in promoted:
                defn = a.definition
                m = a.metrics
                aid = html.escape(a.alpha_id)
                name = html.escape(defn.name)
                expr = html.escape(defn.expression)
                weight_pct = a.allocation_weight * 100.0

                sharpe_str = f"{m.sharpe_oos:.2f}" if m else "N/A"
                dsr_str = f"{m.dsr:.2f}" if m else "N/A"
                ic_str = f"{m.rank_ic_mean:+.3f}" if m else "N/A"

                lines.append(f"• <b><code>{aid}</code></b> ({name}) [Alloc: <code>{weight_pct:.0f}%</code>]")
                lines.append(f"  <i>Expr:</i> <code>{expr}</code>")
                lines.append(
                    f"  <i>OOS Sharpe:</i> <code>{sharpe_str}</code> | <i>DSR:</i> <code>{dsr_str}</code> | <i>IC:</i> <code>{ic_str}</code>"
                )
                lines.append("")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("💡 <i>Use /alphas for status or 'copilot alpha' in CLI.</i>")
        return "\n".join(lines)


def format_mined_alphas_table(candidates: list[AlphaCandidate]) -> str:
    """Format mined alpha candidates as a clean ASCII tabular tearsheet."""
    if not candidates:
        return "No alpha candidates met the minimum statistical gating filters."

    lines = [
        "=" * 105,
        f"{'ALPHA ID':<20} | {'NAME':<32} | {'OOS SR':<7} | {'DSR':<6} | {'RANK IC':<8} | {'WIN%':<6} | {'TRADES':<6}",
        "-" * 105,
    ]
    for c in candidates:
        m = c.metrics
        d = c.definition
        lines.append(
            f"{d.alpha_id:<20} | {d.name[:30]:<32} | {m.sharpe_oos:>7.2f} | {m.dsr:>6.2f} | {m.rank_ic_mean:>+8.3f} | {m.win_rate * 100:>5.1f}% | {m.total_trades:>6d}"
        )
    lines.append("=" * 105)
    return "\n".join(lines)


def format_alpha_inspection_report(candidate: AlphaCandidate) -> str:
    """Format a detailed quantitative tearsheet for an individual formulaic alpha candidate."""
    d = candidate.definition
    m = candidate.metrics

    lines = [
        "=" * 78,
        f"🔬 QUANTITATIVE TEARSHEET: {d.alpha_id.upper()} ({d.name})",
        "=" * 78,
        f"Expression:       {d.expression}",
        f"Description:      {d.description}",
        f"Origin:           {d.origin} | Direction: {d.direction} | Timeframe: {d.timeframe}",
        f"Thresholds:       Entry Z >= {d.entry_threshold:.2f} | Exit Z <= {d.exit_threshold:.2f}",
        "-" * 78,
        "STATISTICAL & OVERFITTING METRICS (Out-of-Sample):",
        f"• Out-of-Sample Sharpe Ratio:     {m.sharpe_oos:+.2f}",
        f"• In-Sample Sharpe Ratio:         {m.sharpe_is:+.2f}",
        f"• Deflated Sharpe Ratio (DSR):    {m.dsr:.2f}  ({'✅ PASS (>0.95)' if m.dsr >= 0.95 else '⚠️ CAUTION'})",
        f"• Mean Rank IC (Spearman):        {m.rank_ic_mean:+.4f}",
        f"• Rank IC Information Ratio (IR): {m.rank_ic_ir:+.2f}",
        f"• Annualized Strategy Return:     {m.annualized_return_pct:+.2f}%",
        f"• Maximum Drawdown:               {m.max_drawdown_pct:.2f}%",
        f"• Profit Factor:                  {m.profit_factor:.2f}",
        f"• Win Rate:                       {m.win_rate * 100:.1f}% ({m.total_trades} trades)",
    ]

    if candidate.correlations:
        lines.append("-" * 78)
        lines.append("CROSS-STRATEGY RETURN CORRELATIONS:")
        for strat_name, corr_val in candidate.correlations.items():
            lines.append(f"• vs {strat_name:<25}: {corr_val:+.3f}")

    lines.append("=" * 78)
    return "\n".join(lines)
