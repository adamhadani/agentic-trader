import html
import inspect
import logging
from collections.abc import Awaitable, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from agentic_trader.agent.evaluator import LLMTradeEvaluation
from agentic_trader.constants import (
    DEFAULT_PORTFOLIO_CASH,
    AssetClass,
    ExecutionMode,
    ExitReason,
    SignalStatus,
)
from agentic_trader.storage.db import SignalDatabase


logger = logging.getLogger(__name__)


def format_alert_card(
    eval_res: LLMTradeEvaluation,
    strategy: str,
    portfolio_cash: float = DEFAULT_PORTFOLIO_CASH,
    execution_mode: str = ExecutionMode.PAPER,
    regime_summary: str | None = None,
) -> str:
    """Format alert message matching Section 8 of the specification."""
    risk_pct = round((eval_res.risk_dollars / portfolio_cash) * 100.0, 2)
    macro_status = "Cleared" if eval_res.macro_clearance else "Event Alert Active"
    regime_line = f"• <b>Regime:</b> {html.escape(regime_summary)}\n" if regime_summary else ""

    qty = getattr(eval_res, "quantity", 1.0)
    asset_class = getattr(eval_res, "asset_class", AssetClass.FUTURES)
    if asset_class == AssetClass.EQUITY or not eval_res.contract.startswith("/"):
        qty_str = f"{qty:g} shares"
    else:
        qty_str = f"{qty:g}x"

    mode_lower = execution_mode.lower()
    if mode_lower == ExecutionMode.PAPER:
        exec_instr = (
            f"1. Click <b>[ 🚀 Execute (Paper) ]</b> to simulate entry for {qty_str} <code>{html.escape(eval_res.contract)}</code> at <code>{eval_res.entry_price:,.2f}</code>.\n"
            f"2. Fills against live market quote; synthetic bracket stop at <code>{eval_res.stop_loss:,.2f}</code>.\n"
        )
    elif mode_lower == ExecutionMode.TRADOVATE:
        exec_instr = (
            f"1. Click <b>[ 🚀 Approve & Execute ]</b> to submit {qty_str} <code>{html.escape(eval_res.contract)}</code> via Tradovate REST API.\n"
            f"2. Server-side OCO brackets placed at Stop: <code>{eval_res.stop_loss:,.2f}</code> / Target: <code>{eval_res.take_profit:,.2f}</code>.\n"
        )
    elif mode_lower == ExecutionMode.ALPACA:
        exec_instr = (
            f"1. Click <b>[ 🚀 Execute (Alpaca) ]</b> to submit {qty_str} <code>{html.escape(eval_res.contract)}</code> via Alpaca Trading API.\n"
            f"2. Server-side bracket order placed at Stop: <code>{eval_res.stop_loss:,.2f}</code> / Target: <code>{eval_res.take_profit:,.2f}</code>.\n"
        )
    else:
        exec_instr = (
            f"1. Buy/Sell {qty_str} <code>{html.escape(eval_res.contract)}</code> at Market/Limit <code>{eval_res.entry_price:,.2f}</code>.\n"
            f"2. Upon fill, immediately submit a resting <b>Stop Order</b> at <code>{eval_res.stop_loss:,.2f}</code> (GTC).\n"
        )

    sizing_section = ""
    tiers = getattr(eval_res, "sizing_tiers", None)
    if tiers and len(tiers) > 1:
        sizing_lines = ["\n📐 <b>Position Sizing Tiers:</b>"]
        for t in tiers:
            star = " ⭐" if t.get("is_default") else ""
            t_qty = t.get("quantity", 1.0)
            t_qty_str = (
                f"{t_qty:g} shares"
                if (asset_class == AssetClass.EQUITY or not eval_res.contract.startswith("/"))
                else f"{t_qty:g}x"
            )
            sizing_lines.append(
                f"• <b>{html.escape(str(t.get('label', '')))}:</b> {t_qty_str} | Risk: -${t.get('risk_dollars', 0.0):,.2f} | Notional: ${t.get('notional_dollars', 0.0):,.0f} ({t.get('effective_leverage', 0.0):.2f}x){star}"
            )
        gating = getattr(eval_res, "gating_reasons", None)
        if gating:
            sizing_lines.extend(f"  <i>🛡️ {html.escape(str(g))}</i>" for g in gating)
        sizing_section = "\n".join(sizing_lines) + "\n"

    # Using HTML formatting for rock-solid reliability with special characters
    text = (
        f"🚨 <b>TRADE SIGNAL: {qty_str} {html.escape(eval_res.contract)} ({html.escape(eval_res.direction)})</b>\n"
        f"<b>Strategy:</b> {html.escape(strategy)}\n\n"
        f"📊 <b>Levels</b>\n"
        f"• <b>Entry Price:</b> <code>{eval_res.entry_price:,.2f}</code>\n"
        f"• <b>Stop Loss:</b> <code>{eval_res.stop_loss:,.2f}</code> (-{eval_res.stop_distance_points:.2f} pts | -${eval_res.risk_dollars:,.2f})\n"
        f"• <b>Target ({eval_res.risk_reward_ratio:.1f}:1):</b> <code>{eval_res.take_profit:,.2f}</code> (+{eval_res.target_distance_points:.2f} pts | +${eval_res.reward_dollars:,.2f})\n\n"
        f"🛡️ <b>Risk &amp; Portfolio Context</b>\n"
        f"• <b>Capital Risk:</b> {risk_pct}% of ${portfolio_cash:,.0f}\n"
        f"• <b>Notional Exposure:</b> ~${eval_res.notional_value:,.2f} ({eval_res.effective_leverage:.2f}x leverage)\n"
        f"• <b>Macro Check:</b> {macro_status}\n"
        f"{regime_line}"
        f"{sizing_section}\n"
        f"📝 <b>Thesis:</b>\n"
        f"{html.escape(eval_res.thesis_summary)}\n\n"
        f"⚡ <b>Execution ({execution_mode.upper()}):</b>\n"
        f"{exec_instr}"
    )
    return text


def format_terminal_card(
    eval_res: LLMTradeEvaluation,
    strategy: str,
    portfolio_cash: float = DEFAULT_PORTFOLIO_CASH,
    execution_mode: str = ExecutionMode.PAPER,
    regime_summary: str | None = None,
) -> str:
    """ASCII/plain text formatted card for terminal display."""
    risk_pct = round((eval_res.risk_dollars / portfolio_cash) * 100.0, 2)
    macro_status = "Cleared" if eval_res.macro_clearance else "Event Alert Active"
    regime_line = f"• Volatility Regime:{regime_summary}\n" if regime_summary else ""

    qty = getattr(eval_res, "quantity", 1.0)
    asset_class = getattr(eval_res, "asset_class", AssetClass.FUTURES)
    if asset_class == AssetClass.EQUITY or not eval_res.contract.startswith("/"):
        qty_str = f"{qty:g} shares"
    else:
        qty_str = f"{qty:g}x"

    mode_lower = execution_mode.lower()
    if mode_lower == ExecutionMode.PAPER:
        exec_instr = (
            f"1. Run 'copilot execute <id>' or click [Execute (Paper)] in Telegram.\n"
            f"2. Simulates fill against live quote; bracket stop at {eval_res.stop_loss:,.2f}."
        )
    elif mode_lower == ExecutionMode.TRADOVATE:
        exec_instr = (
            f"1. Run 'copilot execute <id>' or click [Approve & Execute] in Telegram.\n"
            f"2. Sends API bracket order to Tradovate; OCO stop at {eval_res.stop_loss:,.2f}."
        )
    elif mode_lower == ExecutionMode.ALPACA:
        exec_instr = (
            f"1. Run 'copilot execute <id>' or click [Execute (Alpaca)] in Telegram.\n"
            f"2. Sends bracket order to Alpaca API; stop at {eval_res.stop_loss:,.2f}."
        )
    else:
        exec_instr = (
            f"1. Buy/Sell {qty_str} {eval_res.contract} at {eval_res.entry_price:,.2f}.\n"
            f"2. Place resting Stop Order at {eval_res.stop_loss:,.2f} (GTC)."
        )

    sizing_section = ""
    tiers = getattr(eval_res, "sizing_tiers", None)
    if tiers and len(tiers) > 1:
        sizing_lines = ["\n📐 Position Sizing Tiers:"]
        for t in tiers:
            star = " *" if t.get("is_default") else ""
            t_qty = t.get("quantity", 1.0)
            t_qty_str = (
                f"{t_qty:g} shares"
                if (asset_class == AssetClass.EQUITY or not eval_res.contract.startswith("/"))
                else f"{t_qty:g}x"
            )
            sizing_lines.append(
                f"• {t.get('label', '')}: {t_qty_str} | Risk: -${t.get('risk_dollars', 0.0):,.2f} | Notional: ${t.get('notional_dollars', 0.0):,.0f} ({t.get('effective_leverage', 0.0):.2f}x){star}"
            )
        gating = getattr(eval_res, "gating_reasons", None)
        if gating:
            sizing_lines.extend(f"  [Risk Gate] {g}" for g in gating)
        sizing_section = "\n".join(sizing_lines) + "\n"

    border = "=" * 65
    return f"""
{border}
🚨 TRADE SIGNAL: {qty_str} {eval_res.contract} ({eval_res.direction})
Strategy: {strategy}

📊 Levels
• Entry Price: {eval_res.entry_price:,.2f}
• Stop Loss:   {eval_res.stop_loss:,.2f} (-{eval_res.stop_distance_points:.2f} pts | -${eval_res.risk_dollars:,.2f})
• Target:      {eval_res.take_profit:,.2f} (+{eval_res.target_distance_points:.2f} pts | +${eval_res.reward_dollars:,.2f} [{eval_res.risk_reward_ratio:.1f}:1])

🛡️ Risk & Portfolio Context
• Asset Class:      {asset_class}
• Capital Risk:     {risk_pct}% of ${portfolio_cash:,.0f}
• Notional Value:   ${eval_res.notional_value:,.2f} ({eval_res.effective_leverage:.2f}x leverage)
• Macro Check:      {macro_status}
{regime_line}{sizing_section}
📝 Thesis:
{eval_res.thesis_summary}

⚡ Execution ({execution_mode.upper()}):
{exec_instr}
{border}
"""


def format_exit_card(
    contract: str,
    direction: str,
    exit_reason: str,
    entry_price: float,
    exit_price: float,
    realized_pnl: float,
    strategy: str,
) -> str:
    """Format exit notification card for Take Profit, Stop Loss, or Manual Close."""
    if ExitReason.TAKE_PROFIT in exit_reason.upper():
        icon = "🎯 TAKE PROFIT REACHED"
    elif ExitReason.STOP_LOSS in exit_reason.upper():
        icon = "🛑 STOP LOSS TRIGGERED"
    else:
        icon = "ℹ️ POSITION CLOSED"

    pnl_sign = "+" if realized_pnl >= 0 else "-"
    pnl_str = f"{pnl_sign}${abs(realized_pnl):,.2f}"

    text = (
        f"<b>{icon}: 1x {html.escape(contract)} ({html.escape(direction)})</b>\n"
        f"<b>Strategy:</b> {html.escape(strategy)}\n\n"
        f"📊 <b>Exit Execution Details</b>\n"
        f"• <b>Entry Price:</b> <code>{entry_price:,.2f}</code>\n"
        f"• <b>Exit Price:</b> <code>{exit_price:,.2f}</code>\n"
        f"• <b>Realized P&amp;L:</b> <b>{pnl_str}</b>\n"
        f"• <b>Reason:</b> {html.escape(exit_reason)}\n\n"
        f"🛡️ <i>Active open exposure has been released back to available notional capacity.</i>\n"
    )
    return text


class TelegramNotifier:
    def __init__(
        self,
        bot_token: str | None,
        chat_id: str | None,
        db: SignalDatabase,
        portfolio_cash: float = DEFAULT_PORTFOLIO_CASH,
        execution_mode: str = ExecutionMode.PAPER,
        status_provider: Callable[[], Awaitable[str]] | None = None,
        scan_runner: Callable[[], Awaitable[str]] | None = None,
        positions_provider: Callable[[], Awaitable[str]] | None = None,
        close_handler: Callable[[int, float | None], Awaitable[str]] | None = None,
        execute_handler: Callable[..., Awaitable[tuple[bool, str]]] | None = None,
        perf_provider: Callable[[], Awaitable[str]] | None = None,
        regime_provider: Callable[[], Awaitable[str]] | None = None,
        backtest_runner: Callable[[str, str], Awaitable[str]] | None = None,
        gex_provider: Callable[[str], Awaitable[str]] | None = None,
        pairs_provider: Callable[[], Awaitable[str]] | None = None,
    ):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.db = db
        self.portfolio_cash = portfolio_cash
        self.execution_mode = execution_mode
        self.status_provider = status_provider
        self.scan_runner = scan_runner
        self.positions_provider = positions_provider
        self.close_handler = close_handler
        self.execute_handler = execute_handler
        self.perf_provider = perf_provider
        self.regime_provider = regime_provider
        self.backtest_runner = backtest_runner
        self.gex_provider = gex_provider
        self.pairs_provider = pairs_provider
        self.app: Application | None = None

        if self.is_configured() and self.bot_token:
            try:
                self.app = ApplicationBuilder().token(self.bot_token).build()
                self._register_handlers()
            except Exception as e:
                logger.error(f"Failed to initialize Telegram application: {e}")
                self.app = None

    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id and "your_" not in self.bot_token and "your_" not in self.chat_id)

    def _is_authorized(self, update: Update) -> bool:
        if not update.effective_chat:
            return False
        return str(update.effective_chat.id) == str(self.chat_id)

    def _register_handlers(self):
        if self.app:
            self.app.add_handler(CallbackQueryHandler(self.handle_button_callback))
            self.app.add_handler(CommandHandler(["start", "help"], self.handle_help_command))
            self.app.add_handler(CommandHandler("status", self.handle_status_command))
            self.app.add_handler(CommandHandler("scan", self.handle_scan_command))
            self.app.add_handler(CommandHandler("positions", self.handle_positions_command))
            self.app.add_handler(CommandHandler("close", self.handle_close_command))
            self.app.add_handler(CommandHandler("perf", self.handle_perf_command))
            self.app.add_handler(CommandHandler("regime", self.handle_regime_command))
            self.app.add_handler(CommandHandler("backtest", self.handle_backtest_command))
            self.app.add_handler(CommandHandler("gex", self.handle_gex_command))
            self.app.add_handler(CommandHandler("pairs", self.handle_pairs_command))

    async def handle_help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update) or not update.message:
            return
        help_text = (
            "🤖 <b>Cash-Plus Trading Copilot</b>\n\n"
            "<b>Available Commands:</b>\n"
            "• /status - View portfolio exposure, cash base, and macro events\n"
            "• /positions - View active tracked trades and unrealized P&amp;L\n"
            "• /perf - View cumulative closed trade performance and win rate\n"
            "• /regime - View real-time VIX, 10Y yield, and Dollar Index regime\n"
            "• /pairs - View statistical arbitrage pairs, cointegration &amp; Z-scores\n"
            "• /gex [sym] - View market maker gamma exposure (GEX), walls, and gamma flip (e.g. <code>/gex SPY</code>)\n"
            "• /backtest [sym] [lookback] - Run an offline backtest (e.g. <code>/backtest SPY 1y</code>)\n"
            "• /close &lt;id&gt; [price] - Manually close a tracked trade and record fill\n"
            "• /scan - Trigger an on-demand quantitative scan across universe\n"
            "• /help - Display this command overview\n\n"
            "<b>Risk Invariants Enforced:</b>\n"
            "• Sizing: 1 micro contract (/MES, /MNQ, /MGC, /MCL) or fractional equity shares\n"
            "• Exposure Cap: $60,000 max total open notional\n"
            "• Stop Distance: ≥ 1.5x ATR\n"
            "• Reward-to-Risk: ≥ 2.0:1\n"
            "• Macro Lockout: 60m before / 30m after Tier-1 events"
        )
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🔍 Scan Now", callback_data="cmd_scan"),
                    InlineKeyboardButton("📈 Positions", callback_data="cmd_positions"),
                ],
                [
                    InlineKeyboardButton("📊 Performance", callback_data="cmd_perf"),
                    InlineKeyboardButton("🌐 Macro Regime", callback_data="cmd_regime"),
                ],
            ]
        )
        await update.message.reply_text(help_text, parse_mode="HTML", reply_markup=keyboard)

    async def handle_positions_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update) or not update.message:
            return
        if self.positions_provider:
            resp = await self.positions_provider()
            await update.message.reply_text(resp, parse_mode="HTML")
        else:
            await update.message.reply_text("Positions provider not attached.")

    async def handle_perf_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update) or not update.message:
            return
        if self.perf_provider:
            resp = await self.perf_provider()
            await update.message.reply_text(resp, parse_mode="HTML")
        else:
            await update.message.reply_text("Performance provider not attached.")

    async def handle_regime_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update) or not update.message:
            return
        if self.regime_provider:
            resp = await self.regime_provider()
            await update.message.reply_text(resp, parse_mode="HTML")
        else:
            await update.message.reply_text("Regime provider not attached.")

    async def handle_backtest_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update) or not update.message:
            return
        args = context.args or []
        symbol = args[0].upper() if len(args) > 0 else "SPY"
        lookback = args[1] if len(args) > 1 else "1y"

        await update.message.reply_text(
            f"⏳ Running backtest simulation for <b>{html.escape(symbol)}</b> ({html.escape(lookback)})...",
            parse_mode="HTML",
        )
        if self.backtest_runner:
            try:
                resp = await self.backtest_runner(symbol, lookback)
                await update.message.reply_text(resp, parse_mode="HTML")
            except Exception as e:
                await update.message.reply_text(f"❌ Backtest error: {e}")
        else:
            await update.message.reply_text("Backtest runner not attached.")

    async def handle_gex_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update) or not update.message:
            return
        args = context.args or []
        symbol = args[0].upper() if len(args) > 0 else "SPY"

        await update.message.reply_text(
            f"🧭 Analyzing options gamma exposure & dealer walls for <b>{html.escape(symbol)}</b>...",
            parse_mode="HTML",
        )
        if self.gex_provider:
            try:
                resp = await self.gex_provider(symbol)
                await update.message.reply_text(resp, parse_mode="HTML")
            except Exception as e:
                await update.message.reply_text(f"❌ GEX error: {e}")
        else:
            await update.message.reply_text("GEX provider not attached.")

    async def handle_pairs_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update) or not update.message:
            return
        await update.message.reply_text(
            "📐 Screening cross-asset pairs for cointegration & statistical arbitrage...",
            parse_mode="HTML",
        )
        if self.pairs_provider:
            try:
                resp = await self.pairs_provider()
                await update.message.reply_text(resp, parse_mode="HTML")
            except Exception as e:
                await update.message.reply_text(f"❌ Pairs screening error: {e}")
        else:
            await update.message.reply_text("Pairs provider not attached.")

    async def handle_close_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update) or not update.message:
            return
        args = context.args or []
        if not args:
            await update.message.reply_text(
                "Usage: <code>/close &lt;signal_id&gt; [exit_price]</code>\nExample: <code>/close 2 5845.00</code>",
                parse_mode="HTML",
            )
            return
        try:
            signal_id = int(args[0])
            exit_price = float(args[1]) if len(args) > 1 else None
        except ValueError:
            await update.message.reply_text("Invalid signal ID or price format. Use: /close <id> [price]")
            return

        if self.close_handler:
            resp = await self.close_handler(signal_id, exit_price)
            await update.message.reply_text(resp, parse_mode="HTML")
        else:
            await update.message.reply_text("Close handler not attached.")

    async def handle_status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update) or not update.message:
            return
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🔍 Run Scan", callback_data="cmd_scan"),
                    InlineKeyboardButton("📈 Positions", callback_data="cmd_positions"),
                ],
                [
                    InlineKeyboardButton("📊 Performance", callback_data="cmd_perf"),
                    InlineKeyboardButton("🌐 Macro Regime", callback_data="cmd_regime"),
                ],
            ]
        )
        if self.status_provider:
            status_text = await self.status_provider()
            await update.message.reply_text(status_text, parse_mode="HTML", reply_markup=keyboard)
        else:
            await update.message.reply_text("Status provider not attached.")

    async def handle_scan_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update) or not update.message:
            return
        await update.message.reply_text("🔍 Running quantitative scan across universe...")
        if self.scan_runner:
            result_text = await self.scan_runner()
            await update.message.reply_text(result_text, parse_mode="HTML")
        else:
            await update.message.reply_text("Scan runner not attached.")

    async def handle_button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if not query or not query.data:
            return
        data = query.data
        msg = query.message

        async def _safe_clear_markup() -> None:
            if hasattr(query, "edit_message_reply_markup"):
                try:
                    markup_res = query.edit_message_reply_markup(reply_markup=None)
                    if inspect.isawaitable(markup_res):
                        await markup_res
                except Exception:
                    pass

        if data == "cmd_scan":
            await query.answer("Running quantitative scan...")
            if self.scan_runner and msg and hasattr(msg, "reply_text"):
                res = await self.scan_runner()
                await msg.reply_text(res, parse_mode="HTML")
        elif data == "cmd_positions":
            await query.answer()
            if self.positions_provider and msg and hasattr(msg, "reply_text"):
                res = await self.positions_provider()
                await msg.reply_text(res, parse_mode="HTML")
        elif data == "cmd_perf":
            await query.answer()
            if self.perf_provider and msg and hasattr(msg, "reply_text"):
                res = await self.perf_provider()
                await msg.reply_text(res, parse_mode="HTML")
        elif data == "cmd_regime":
            await query.answer()
            if self.regime_provider and msg and hasattr(msg, "reply_text"):
                res = await self.regime_provider()
                await msg.reply_text(res, parse_mode="HTML")
        elif data.startswith("exec_"):
            parts = data.split("_")
            signal_id = int(parts[1])
            quantity = float(parts[2]) if len(parts) > 2 else None
            logger.info(
                "Execute button clicked for signal #%d (quantity: %s)",
                signal_id,
                quantity,
                extra={
                    "signal_id": signal_id,
                    "quantity": quantity,
                    "action": "execute",
                    "execution_mode": self.execution_mode,
                },
            )

            if self.execute_handler:
                qty_msg = f" for {quantity:g} units" if quantity is not None else ""
                await query.answer(f"Submitting order{qty_msg} to broker...")
                await _safe_clear_markup()
                _success, reply_text = await self.execute_handler(signal_id, quantity=quantity)
                if msg and hasattr(msg, "reply_text"):
                    await msg.reply_text(reply_text, parse_mode="HTML")
            else:
                await query.answer()
                await self.db.update_signal_status(signal_id, SignalStatus.EXECUTED)
                await _safe_clear_markup()
                if msg and hasattr(msg, "reply_text"):
                    await msg.reply_text(
                        f"✅ Signal #{signal_id} acknowledged: status set to {SignalStatus.EXECUTED}. Position is now active in risk tracking."
                    )

        elif data.startswith("dism_"):
            await query.answer()
            signal_id = int(data.split("_")[1])
            logger.info(
                "Dismiss button clicked for signal #%d",
                signal_id,
                extra={"signal_id": signal_id, "action": "dismiss"},
            )
            await self.db.update_signal_status(signal_id, SignalStatus.DISMISSED)
            await _safe_clear_markup()
            if msg and hasattr(msg, "reply_text"):
                await msg.reply_text(f"❌ Signal #{signal_id} {SignalStatus.DISMISSED}.")

    async def send_signal_alert(
        self,
        eval_res: LLMTradeEvaluation,
        strategy: str,
        signal_id: int,
        regime_summary: str | None = None,
    ) -> int | None:
        # Always output to terminal/logs
        print(
            format_terminal_card(
                eval_res,
                strategy,
                self.portfolio_cash,
                execution_mode=self.execution_mode,
                regime_summary=regime_summary,
            )
        )

        if not self.is_configured() or not self.app:
            logger.info(
                "Telegram not configured or token missing. Alert displayed on terminal.",
                extra={"signal_id": signal_id, "contract": eval_res.contract},
            )
            return None

        card_html = format_alert_card(
            eval_res,
            strategy,
            self.portfolio_cash,
            execution_mode=self.execution_mode,
            regime_summary=regime_summary,
        )

        mode_lower = self.execution_mode.lower()
        if mode_lower == ExecutionMode.PAPER:
            exec_btn_text = "🚀 Execute (Paper)"
        elif mode_lower == ExecutionMode.TRADOVATE:
            exec_btn_text = "🚀 Approve & Execute"
        elif mode_lower == ExecutionMode.ALPACA:
            exec_btn_text = "🚀 Execute (Alpaca)"
        else:
            exec_btn_text = "✅ Acknowledge & Tracking"

        tiers = getattr(eval_res, "sizing_tiers", None)
        asset_class = getattr(eval_res, "asset_class", AssetClass.FUTURES)
        if tiers and len(tiers) > 1:
            tier_buttons = []
            for t in tiers:
                t_qty = t.get("quantity", 1.0)
                t_risk = t.get("risk_dollars", 0.0)
                star = " ⭐" if t.get("is_default") else ""
                icon = "🔹" if t.get("tier_id") == "half" else ("⚡" if t.get("tier_id") == "max" else "🚀")
                if asset_class == AssetClass.EQUITY or not eval_res.contract.startswith("/"):
                    btn_label = f"{icon} {t_qty:g} sh (${t_risk:,.0f}){star}"
                else:
                    btn_label = f"{icon} {t_qty:g}x (${t_risk:,.0f}){star}"
                tier_buttons.append(InlineKeyboardButton(btn_label, callback_data=f"exec_{signal_id}_{t_qty:g}"))
            keyboard = [
                tier_buttons,
                [InlineKeyboardButton("❌ Dismiss Signal", callback_data=f"dism_{signal_id}")],
            ]
        else:
            keyboard = [
                [
                    InlineKeyboardButton(exec_btn_text, callback_data=f"exec_{signal_id}"),
                    InlineKeyboardButton("❌ Dismiss Signal", callback_data=f"dism_{signal_id}"),
                ]
            ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            bot = self.app.bot
            msg = await bot.send_message(
                chat_id=self.chat_id,
                text=card_html,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
            await self.db.update_telegram_message_id(signal_id, msg.message_id)
            logger.info(
                "Telegram signal alert dispatched for signal #%d",
                signal_id,
                extra={
                    "signal_id": signal_id,
                    "message_id": msg.message_id,
                    "contract": eval_res.contract,
                    "strategy": strategy,
                    "direction": eval_res.direction,
                    "entry_price": eval_res.entry_price,
                },
            )
            return msg.message_id
        except Exception as e:
            logger.error(
                "Failed to dispatch Telegram alert message: %s",
                e,
                extra={"signal_id": signal_id, "contract": eval_res.contract, "error": str(e)},
            )
            return None

    async def send_exit_alert(
        self,
        contract: str,
        direction: str,
        exit_reason: str,
        entry_price: float,
        exit_price: float,
        realized_pnl: float,
        strategy: str,
    ) -> int | None:
        """Send notification when a Take Profit, Stop Loss, or Manual Exit occurs."""
        card_html = format_exit_card(
            contract=contract,
            direction=direction,
            exit_reason=exit_reason,
            entry_price=entry_price,
            exit_price=exit_price,
            realized_pnl=realized_pnl,
            strategy=strategy,
        )
        logger.info(
            "Exit event: %s %s via %s @ %s (Realized PnL: $%.2f)",
            contract,
            direction,
            exit_reason,
            exit_price,
            realized_pnl,
            extra={
                "contract": contract,
                "direction": direction,
                "exit_reason": exit_reason,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "realized_pnl": realized_pnl,
                "strategy": strategy,
            },
        )

        if not self.is_configured() or not self.app:
            return None

        try:
            bot = self.app.bot
            msg = await bot.send_message(
                chat_id=self.chat_id,
                text=card_html,
                parse_mode="HTML",
            )
            return msg.message_id
        except Exception as e:
            logger.error(
                "Failed to dispatch Telegram exit alert: %s",
                e,
                extra={"contract": contract, "exit_reason": exit_reason, "error": str(e)},
            )
            return None

    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        if not self.is_configured() or not self.app:
            return False
        try:
            bot = self.app.bot
            await bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode=parse_mode,
            )
            return True
        except Exception as e:
            logger.error(
                "Failed to dispatch Telegram message: %s",
                e,
                extra={"error": str(e)},
            )
            return False
