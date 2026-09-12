import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    ContextTypes,
)

from agentic_trader.agent.evaluator import LLMTradeEvaluation
from agentic_trader.storage.db import SignalDatabase


logger = logging.getLogger(__name__)


def format_alert_card(
    eval_res: LLMTradeEvaluation,
    strategy: str,
    portfolio_cash: float = 100000.0,
) -> str:
    """Format alert message matching Section 8 of the specification."""
    risk_pct = round((eval_res.risk_dollars / portfolio_cash) * 100.0, 2)
    macro_status = "Cleared" if eval_res.macro_clearance else "Event Alert Active"

    # Using HTML formatting for rock-solid reliability with special characters
    text = (
        f"🚨 <b>TRADE SIGNAL: 1x {html.escape(eval_res.contract)} ({html.escape(eval_res.direction)})</b>\n"
        f"<b>Strategy:</b> {html.escape(strategy)}\n\n"
        f"📊 <b>Levels</b>\n"
        f"• <b>Entry Price:</b> <code>{eval_res.entry_price:,.2f}</code>\n"
        f"• <b>Stop Loss:</b> <code>{eval_res.stop_loss:,.2f}</code> (-{eval_res.stop_distance_points:.2f} pts | -${eval_res.risk_dollars:,.2f})\n"
        f"• <b>Target ({eval_res.risk_reward_ratio:.1f}:1):</b> <code>{eval_res.take_profit:,.2f}</code> (+{eval_res.target_distance_points:.2f} pts | +${eval_res.reward_dollars:,.2f})\n\n"
        f"🛡️ <b>Risk &amp; Portfolio Context</b>\n"
        f"• <b>Capital Risk:</b> {risk_pct}% of ${portfolio_cash:,.0f}\n"
        f"• <b>Notional Exposure:</b> ~${eval_res.notional_value:,.2f} ({eval_res.effective_leverage:.2f}x leverage)\n"
        f"• <b>Macro Check:</b> {macro_status}\n\n"
        f"📝 <b>Thesis:</b>\n"
        f"{html.escape(eval_res.thesis_summary)}\n\n"
        f"⚠️ <b>Execution Instruction (Robinhood):</b>\n"
        f"1. Buy/Sell 1 <code>{html.escape(eval_res.contract)}</code> at Market/Limit <code>{eval_res.entry_price:,.2f}</code>.\n"
        f"2. Upon fill, immediately submit a resting <b>Stop Order</b> at <code>{eval_res.stop_loss:,.2f}</code> (GTC).\n"
    )
    return text


def format_terminal_card(
    eval_res: LLMTradeEvaluation,
    strategy: str,
    portfolio_cash: float = 100000.0,
) -> str:
    """ASCII/plain text formatted card for terminal display."""
    risk_pct = round((eval_res.risk_dollars / portfolio_cash) * 100.0, 2)
    macro_status = "Cleared" if eval_res.macro_clearance else "Event Alert Active"

    border = "=" * 65
    return f"""
{border}
🚨 TRADE SIGNAL: 1x {eval_res.contract} ({eval_res.direction})
Strategy: {strategy}

📊 Levels
• Entry Price: {eval_res.entry_price:,.2f}
• Stop Loss:   {eval_res.stop_loss:,.2f} (-{eval_res.stop_distance_points:.2f} pts | -${eval_res.risk_dollars:,.2f})
• Target:      {eval_res.take_profit:,.2f} (+{eval_res.target_distance_points:.2f} pts | +${eval_res.reward_dollars:,.2f} [{eval_res.risk_reward_ratio:.1f}:1])

🛡️ Risk & Portfolio Context
• Capital Risk:     {risk_pct}% of ${portfolio_cash:,.0f}
• Notional Value:   ${eval_res.notional_value:,.2f} ({eval_res.effective_leverage:.2f}x leverage)
• Macro Check:      {macro_status}

📝 Thesis:
{eval_res.thesis_summary}

⚠️ Execution Instruction (Robinhood):
1. Buy/Sell 1 {eval_res.contract} at {eval_res.entry_price:,.2f}.
2. Place resting Stop Order at {eval_res.stop_loss:,.2f} (GTC).
{border}
"""


class TelegramNotifier:
    def __init__(
        self,
        bot_token: str | None,
        chat_id: str | None,
        db: SignalDatabase,
        portfolio_cash: float = 100000.0,
    ):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.db = db
        self.portfolio_cash = portfolio_cash
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

    def _register_handlers(self):
        if self.app:
            self.app.add_handler(CallbackQueryHandler(self.handle_button_callback))

    async def handle_button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if not query or not query.data:
            return
        await query.answer()
        data = query.data
        msg = query.message

        if data.startswith("exec_"):
            signal_id = int(data.split("_")[1])
            await self.db.update_signal_status(signal_id, "EXECUTED")
            await query.edit_message_reply_markup(reply_markup=None)
            if msg and hasattr(msg, "reply_text"):
                await msg.reply_text(
                    f"✅ Signal #{signal_id} acknowledged: status set to EXECUTED. Position is now active in risk tracking."
                )

        elif data.startswith("dism_"):
            signal_id = int(data.split("_")[1])
            await self.db.update_signal_status(signal_id, "DISMISSED")
            await query.edit_message_reply_markup(reply_markup=None)
            if msg and hasattr(msg, "reply_text"):
                await msg.reply_text(f"❌ Signal #{signal_id} DISMISSED.")

    async def send_signal_alert(
        self,
        eval_res: LLMTradeEvaluation,
        strategy: str,
        signal_id: int,
    ) -> int | None:
        # Always output to terminal/logs
        print(format_terminal_card(eval_res, strategy, self.portfolio_cash))

        if not self.is_configured() or not self.app:
            logger.info("Telegram not configured or token missing. Alert displayed on terminal.")
            return None

        card_html = format_alert_card(eval_res, strategy, self.portfolio_cash)
        keyboard = [
            [
                InlineKeyboardButton("✅ Acknowledge & Tracking", callback_data=f"exec_{signal_id}"),
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
            return msg.message_id
        except Exception as e:
            logger.error(f"Failed to dispatch Telegram alert message: {e}")
            return None
