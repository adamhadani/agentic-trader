import contextlib
import html
import inspect
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine
from datetime import datetime
from functools import wraps
from typing import Any

from telegram import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonCommands,
    Update,
)
from telegram.constants import ChatAction
from telegram.error import BadRequest, NetworkError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from agentic_trader.agent.evaluator import LLMTradeEvaluation
from agentic_trader.config import TelegramConfig
from agentic_trader.constants import (
    APP_DISPLAY_NAME,
    DEFAULT_BACKTEST_LOOKBACK,
    DEFAULT_PORTFOLIO_CASH,
    DEFAULT_RESEARCH_SYMBOL,
    TELEGRAM_MESSAGE_CHUNK_LENGTH,
    AssetClass,
    AuditEventType,
    ExecutionMode,
    ExitReason,
    RuntimeEnvironment,
    SignalStatus,
)
from agentic_trader.execution.freshness import ExecutionReply
from agentic_trader.market.session import ET_TZ
from agentic_trader.notifier.transport import (
    ObservedPollingRequest,
    RetryingTelegramRequest,
    notification_id,
    telegram_update_id,
)
from agentic_trader.presentation.formatters import TelegramHtmlFormatter
from agentic_trader.runtime import RUN_ID
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.telemetry.collector import MetricsCollector, global_metrics


logger = logging.getLogger(__name__)


def _ny_hhmm(value: str | None) -> str | None:
    """Convert an aware ISO-8601 timestamp string to an ``HH:MM`` New York clock string.

    Returns None for a missing, unparseable or naive value, so a card renders without
    the affected line rather than raising.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except TypeError, ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(ET_TZ).strftime("%H:%M")


def _exec_button_label(execution_mode: str) -> str:
    mode_lower = execution_mode.lower()
    if mode_lower == ExecutionMode.PAPER:
        return "🚀 Execute (Paper)"
    if mode_lower == ExecutionMode.TRADOVATE:
        return "🚀 Approve & Execute"
    if mode_lower == ExecutionMode.ALPACA:
        return "🚀 Execute (Alpaca)"
    return "✅ Acknowledge & Tracking"


def format_alert_card(
    eval_res: LLMTradeEvaluation,
    strategy: str,
    portfolio_cash: float = DEFAULT_PORTFOLIO_CASH,
    execution_mode: str = ExecutionMode.PAPER,
    regime_summary: str | None = None,
    probe_risk_cap: float | None = None,
    valid_until: str | None = None,
    reprices: int | None = None,
    first_issued_at: str | None = None,
) -> str:
    """Format alert message matching Section 8 of the specification."""
    risk_pct = round((eval_res.risk_dollars / portfolio_cash) * 100.0, 2)
    macro_status = "Cleared" if eval_res.macro_clearance else "Event Alert Active"
    earnings_line = f"• <b>Earnings:</b> {html.escape(eval_res.earnings_note)}\n" if eval_res.earnings_note else ""
    regime_line = f"• <b>Regime:</b> {html.escape(regime_summary)}\n" if regime_summary else ""
    valid_until_hhmm = _ny_hhmm(valid_until)
    valid_until_line = f"• <b>Valid until:</b> {valid_until_hhmm} NY\n" if valid_until_hhmm else ""
    updated_card_prefix = ""
    if reprices is not None:
        first_issued_hhmm = _ny_hhmm(first_issued_at)
        issued_part = f", first issued {first_issued_hhmm} NY" if first_issued_hhmm else ""
        updated_card_prefix = f"🔄 <b>UPDATED CARD (re-priced from #{reprices}{issued_part})</b>\n"

    qty = eval_res.quantity
    asset_class = eval_res.asset_class
    if asset_class == AssetClass.EQUITY or not eval_res.contract.startswith("/"):
        qty_str = f"{qty:g} shares"
    else:
        qty_str = f"{qty:g}x"

    tiers = eval_res.sizing_tiers
    has_tiers = bool(tiers and len(tiers) > 1)

    mode_lower = execution_mode.lower()
    if has_tiers:
        if mode_lower == ExecutionMode.PAPER:
            exec_instr = (
                "1. Tap an execution tier button below to simulate entry with that size (or tap <b>[ ❌ Dismiss ]</b> to decline).\n"
                f"2. Fills against live quote; synthetic bracket stop at <code>{eval_res.stop_loss:,.2f}</code>, target at <code>{eval_res.take_profit:,.2f}</code>.\n"
            )
        elif mode_lower == ExecutionMode.TRADOVATE:
            exec_instr = (
                "1. Tap an execution tier button below to submit order via Tradovate (or tap <b>[ ❌ Dismiss ]</b> to decline).\n"
                f"2. Server-side OCO brackets placed at Stop: <code>{eval_res.stop_loss:,.2f}</code> / Target: <code>{eval_res.take_profit:,.2f}</code>.\n"
            )
        elif mode_lower == ExecutionMode.ALPACA:
            exec_instr = (
                "1. Tap an execution tier button below to submit order via Alpaca (or tap <b>[ ❌ Dismiss ]</b> to decline).\n"
                f"2. Server-side bracket order placed at Stop: <code>{eval_res.stop_loss:,.2f}</code> / Target: <code>{eval_res.take_profit:,.2f}</code>.\n"
            )
        else:
            exec_instr = (
                "1. Tap an execution tier button below to authorize entry (or tap <b>[ ❌ Dismiss ]</b> to decline).\n"
                f"2. Upon fill, resting stop placed at <code>{eval_res.stop_loss:,.2f}</code> (GTC).\n"
            )
    else:
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
    if has_tiers and tiers:
        sizing_lines = ["\n📐 <b>Position Sizing Tiers:</b>"]
        for t in tiers:
            star = " ⭐" if t.get("is_default") else ""
            t_qty = t.get("quantity", 1.0)
            t_qty_str = (
                f"{t_qty:g} shares"
                if (asset_class == AssetClass.EQUITY or not eval_res.contract.startswith("/"))
                else f"{t_qty:g}x"
            )
            risk_val = t.get("risk_dollars", 0.0)
            reward_val = t.get("reward_dollars", 0.0)
            notional_val = t.get("notional_dollars", 0.0)
            lev_val = t.get("effective_leverage", 0.0)
            sizing_lines.append(
                f"• <b>{html.escape(str(t.get('label', '')))}:</b> {t_qty_str} | "
                f"Risk: -${risk_val:,.2f} | "
                f"Reward: +${reward_val:,.2f} | "
                f"Notional: ${notional_val:,.0f} ({lev_val:.2f}x){star}"
            )
        gating = eval_res.gating_reasons
        if gating:
            sizing_lines.extend(f"  <i>🛡️ {html.escape(str(g))}</i>" for g in gating)
        sizing_section = "\n".join(sizing_lines) + "\n"

    # Using HTML formatting for rock-solid reliability with special characters
    text = (
        f"{updated_card_prefix}"
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
        f"{earnings_line}"
        f"{valid_until_line}"
        f"{regime_line}"
        f"{sizing_section}\n"
        f"📝 <b>Thesis:</b>\n"
        f"{html.escape(eval_res.thesis_summary)}\n\n"
        f"⚡ <b>Execution ({execution_mode.upper()}):</b>\n"
        f"{exec_instr}"
    )
    if probe_risk_cap is None:
        return text
    return (
        f"🧪 <b>PAPER PROBE</b> — risk capped at ${probe_risk_cap:,.0f}; paper account only; "
        "earns no promotion credit\n\n" + text
    )


def format_terminal_card(
    eval_res: LLMTradeEvaluation,
    strategy: str,
    portfolio_cash: float = DEFAULT_PORTFOLIO_CASH,
    execution_mode: str = ExecutionMode.PAPER,
    regime_summary: str | None = None,
    valid_until: str | None = None,
    reprices: int | None = None,
    first_issued_at: str | None = None,
) -> str:
    """ASCII/plain text formatted card for terminal display."""
    risk_pct = round((eval_res.risk_dollars / portfolio_cash) * 100.0, 2)
    macro_status = "Cleared" if eval_res.macro_clearance else "Event Alert Active"
    earnings_line = f"• Earnings:         {eval_res.earnings_note}\n" if eval_res.earnings_note else ""
    regime_line = f"• Volatility Regime:{regime_summary}\n" if regime_summary else ""
    valid_until_hhmm = _ny_hhmm(valid_until)
    valid_until_line = f"• Valid until:      {valid_until_hhmm} NY\n" if valid_until_hhmm else ""
    updated_card_prefix = ""
    if reprices is not None:
        first_issued_hhmm = _ny_hhmm(first_issued_at)
        issued_part = f", first issued {first_issued_hhmm} NY" if first_issued_hhmm else ""
        updated_card_prefix = f"🔄 UPDATED CARD (re-priced from #{reprices}{issued_part})\n"

    qty = eval_res.quantity
    asset_class = eval_res.asset_class
    if asset_class == AssetClass.EQUITY or not eval_res.contract.startswith("/"):
        qty_str = f"{qty:g} shares"
    else:
        qty_str = f"{qty:g}x"

    tiers = eval_res.sizing_tiers
    has_tiers = bool(tiers and len(tiers) > 1)

    mode_lower = execution_mode.lower()
    if has_tiers:
        if mode_lower == ExecutionMode.PAPER:
            exec_instr = (
                "1. Select execution tier in Telegram or pass '--quantity N' to 'copilot execute <id>'.\n"
                f"2. Simulates fill against live quote; bracket stop at {eval_res.stop_loss:,.2f}, target at {eval_res.take_profit:,.2f}."
            )
        elif mode_lower == ExecutionMode.TRADOVATE:
            exec_instr = (
                "1. Select execution tier in Telegram or pass '--quantity N' to 'copilot execute <id>'.\n"
                f"2. Sends API bracket order to Tradovate; OCO stop at {eval_res.stop_loss:,.2f}, target at {eval_res.take_profit:,.2f}."
            )
        elif mode_lower == ExecutionMode.ALPACA:
            exec_instr = (
                "1. Select execution tier in Telegram or pass '--quantity N' to 'copilot execute <id>'.\n"
                f"2. Sends bracket order to Alpaca API; stop at {eval_res.stop_loss:,.2f}, target at {eval_res.take_profit:,.2f}."
            )
        else:
            exec_instr = (
                f"1. Authorize entry with selected tier at {eval_res.entry_price:,.2f}.\n"
                f"2. Place resting Stop Order at {eval_res.stop_loss:,.2f} (GTC)."
            )
    else:
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
    if has_tiers and tiers:
        sizing_lines = ["\n📐 Position Sizing Tiers:"]
        for t in tiers:
            star = " *" if t.get("is_default") else ""
            t_qty = t.get("quantity", 1.0)
            t_qty_str = (
                f"{t_qty:g} shares"
                if (asset_class == AssetClass.EQUITY or not eval_res.contract.startswith("/"))
                else f"{t_qty:g}x"
            )
            risk_val = t.get("risk_dollars", 0.0)
            reward_val = t.get("reward_dollars", 0.0)
            notional_val = t.get("notional_dollars", 0.0)
            lev_val = t.get("effective_leverage", 0.0)
            sizing_lines.append(
                f"• {t.get('label', '')}: {t_qty_str} | Risk: -${risk_val:,.2f} | Reward: +${reward_val:,.2f} | Notional: ${notional_val:,.0f} ({lev_val:.2f}x){star}"
            )
        gating = eval_res.gating_reasons
        if gating:
            sizing_lines.extend(f"  [Risk Gate] {g}" for g in gating)
        sizing_section = "\n".join(sizing_lines) + "\n"

    border = "=" * 65
    return f"""
{border}
{updated_card_prefix}🚨 TRADE SIGNAL: {qty_str} {eval_res.contract} ({eval_res.direction})
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
{earnings_line}{valid_until_line}{regime_line}{sizing_section}
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
    quantity: float = 1.0,
    asset_class: AssetClass = AssetClass.FUTURES,
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

    qty_str = (
        f"{quantity:g} shares"
        if (asset_class == AssetClass.EQUITY or not contract.startswith("/"))
        else f"{quantity:g}x"
    )

    text = (
        f"<b>{icon}: {qty_str} {html.escape(contract)} ({html.escape(direction)})</b>\n"
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
        db: SignalDatabase | None = None,
        portfolio_cash: float = DEFAULT_PORTFOLIO_CASH,
        execution_mode: str = ExecutionMode.PAPER,
        status_provider: Callable[[], Awaitable[str]] | None = None,
        scan_runner: Callable[[], Awaitable[str]] | None = None,
        positions_provider: Callable[[], Awaitable[str]] | None = None,
        close_handler: Callable[[int, float | None], Awaitable[str]] | None = None,
        flatten_handler: Callable[[bool], Awaitable[str]] | None = None,
        execute_handler: Callable[..., Awaitable[ExecutionReply]] | None = None,
        reevaluate_handler: Callable[[int], Awaitable[ExecutionReply]] | None = None,
        perf_provider: Callable[[], Awaitable[str]] | None = None,
        macro_provider: Callable[[], Awaitable[str]] | None = None,
        explain_macro_provider: Callable[[], Awaitable[str]] | None = None,
        backtest_runner: Callable[[str, str], Awaitable[str]] | None = None,
        gex_provider: Callable[[str], Awaitable[str]] | None = None,
        pairs_provider: Callable[[], Awaitable[str]] | None = None,
        panic_handler: Callable[[str], Awaitable[Any]] | None = None,
        resume_handler: Callable[[], Awaitable[Any]] | None = None,
        chat_handler: Callable[[str, str | int], Awaitable[str]] | None = None,
        alphas_provider: Callable[[], Awaitable[str]] | None = None,
        environment: str = RuntimeEnvironment.DEVELOPMENT,
        settings: TelegramConfig | None = None,
        metrics: MetricsCollector | None = None,
        application: Application | None = None,
        backtest_lookback: str = DEFAULT_BACKTEST_LOOKBACK,
    ):
        self.backtest_lookback = backtest_lookback
        self.settings = settings if settings is not None else TelegramConfig()
        self.metrics = metrics if metrics is not None else global_metrics
        self._poll_healthy = False
        self._last_poll_audit = 0.0
        self.environment = environment
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.db = db
        self.portfolio_cash = portfolio_cash
        self.execution_mode = execution_mode
        self.status_provider = status_provider
        self.scan_runner = scan_runner
        self.positions_provider = positions_provider
        self.close_handler = close_handler
        self.flatten_handler = flatten_handler
        self.execute_handler = execute_handler
        self.reevaluate_handler = reevaluate_handler
        self.perf_provider = perf_provider
        self.macro_provider = macro_provider
        self.explain_macro_provider = explain_macro_provider
        self.backtest_runner = backtest_runner
        self.gex_provider = gex_provider
        self.pairs_provider = pairs_provider
        self.panic_handler = panic_handler
        self.resume_handler = resume_handler
        self.chat_handler = chat_handler
        self.alphas_provider = alphas_provider
        self.app: Application | None = application

        if self.app is not None:
            self._register_handlers()
        elif self.is_configured() and self.bot_token:
            try:
                request_options = {
                    "read_timeout": self.settings.read_timeout_seconds,
                    "connect_timeout": self.settings.connect_timeout_seconds,
                }
                self.app = (
                    ApplicationBuilder()
                    .token(self.bot_token)
                    .request(RetryingTelegramRequest(self.settings, self._audit, self.metrics))
                    .get_updates_request(ObservedPollingRequest(self._observe_poll, **request_options))
                    .build()
                )
                self._register_handlers()
            except Exception as e:
                logger.error(f"Failed to initialize Telegram application: {e}")
                self.app = None

    def _label(self, message: str) -> str:
        return (
            message if self.environment == RuntimeEnvironment.PRODUCTION else f"[{self.environment.upper()}] {message}"
        )

    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id and "your_" not in self.bot_token and "your_" not in self.chat_id)

    def _is_authorized(self, update: Update) -> bool:
        if not update.effective_chat:
            return False
        return str(update.effective_chat.id) == str(self.chat_id)

    async def _audit(self, event: AuditEventType, payload: dict[str, Any]) -> None:
        if self.db is not None:
            try:
                if (delivery_id := notification_id.get()) is not None:
                    payload = {**payload, "outbox_id": delivery_id}
                if (update_id := telegram_update_id.get()) is not None:
                    payload = {**payload, "update_id": update_id}
                await self.db.record_audit(event, payload)
            except Exception:
                logger.exception("Failed to persist Telegram telemetry: %s", event)

    async def _observe_poll(self, success: bool, error_type: str | None) -> None:
        now = time.time()
        previous = self._poll_healthy
        self._poll_healthy = success
        self.metrics.set_gauge("trader_telegram_poll_healthy", float(success))
        if success:
            self.metrics.set_gauge("trader_telegram_last_poll_success_timestamp_seconds", now)
        else:
            self.metrics.inc_counter("trader_telegram_poll_errors_total", labels={"error": error_type or "unknown"})
        if not success or not previous or now - self._last_poll_audit >= self.settings.poll_audit_interval_seconds:
            await self._audit(AuditEventType.TELEGRAM_POLL, {"success": success, "error_type": error_type})
            if self.db:
                try:
                    await self.db.workflows.record_health("telegram", success, error_type or "", run_id=RUN_ID)
                except Exception:
                    logger.exception("Could not persist poll readiness; poll transport continues")
            self._last_poll_audit = now
        if success and not previous:
            logger.info("Telegram polling healthy; successful getUpdates response received")

    def _observe_handler(self, callback: Callable[..., Awaitable[Any]]) -> Callable[..., Coroutine[Any, Any, Any]]:
        @wraps(callback)
        async def observed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Any:
            if not self._is_authorized(update):
                return None
            started = time.monotonic()
            context_token = telegram_update_id.set(update.update_id)
            payload = {"handler": callback.__name__, "update_id": update.update_id}
            await self._audit(AuditEventType.TELEGRAM_COMMAND, {**payload, "phase": "started"})
            try:
                result = await callback(update, context)
            except Exception:
                await self._audit(AuditEventType.TELEGRAM_COMMAND, {**payload, "phase": "failed"})
                raise
            else:
                await self._audit(AuditEventType.TELEGRAM_COMMAND, {**payload, "phase": "completed"})
                return result
            finally:
                telegram_update_id.reset(context_token)
                self.metrics.observe_histogram(
                    "trader_telegram_command_duration_seconds",
                    time.monotonic() - started,
                    labels={"handler": callback.__name__},
                )

        return observed

    async def _handle_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        error = context.error
        payload = {
            "error_type": type(error).__name__,
            "update_id": update.update_id if isinstance(update, Update) else None,
        }
        self.metrics.inc_counter("trader_telegram_errors_total", labels={"error": type(error).__name__})
        await self._audit(AuditEventType.TELEGRAM_ERROR, payload)
        if update is None and isinstance(error, NetworkError):
            logger.warning("Telegram polling interrupted (%s); SDK will retry", type(error).__name__)
            return
        logger.error("Telegram handler failed: %s", payload, exc_info=error)
        if isinstance(update, Update) and self._is_authorized(update) and update.effective_message:
            # Report failure, but never replay a command or a trade action.
            try:
                await self.safe_reply_text(
                    update.effective_message,
                    "⚠️ I could not complete the response. Check /positions before repeating any trade action.",
                    parse_mode="",
                )
            except Exception:
                logger.exception("Could not deliver Telegram command failure notice")

    async def setup_bot_commands(self) -> bool:
        """Register slash commands with Telegram so client-side autocomplete works across all scopes."""
        if not self.is_configured() or not self.app:
            return False
        try:
            commands = [
                BotCommand("status", "Portfolio exposure, cash base, and macro events"),
                BotCommand("positions", "Broker positions, cost basis and unrealized P&L"),
                BotCommand("perf", "Account performance and tracked trade statistics"),
                BotCommand("macro", "VIX, yield curve, credit, inflation & trading filters"),
                BotCommand("explain_macro", "Tutorial & breakdown of live macro indicators"),
                BotCommand("alphas", "Formulaic alpha intelligence & active strategies"),
                BotCommand("pairs", "Statistical arbitrage pairs, cointegration & Z-scores"),
                BotCommand("gex", "Option-chain gamma estimates and concentration levels"),
                BotCommand("backtest", "Offline backtest simulation"),
                BotCommand("scan", "Trigger on-demand quantitative universe scan"),
                BotCommand("close", "Close one position without halting trading"),
                BotCommand("flatten", "Preview or close all positions without a trading halt"),
                BotCommand("panic", "EMERGENCY: cancel all orders, liquidate positions & halt"),
                BotCommand("resume", "Resume trading operations after panic halt"),
                BotCommand("help", "Command overview and risk invariants"),
            ]
            # Register across default, all private chats, and specific operator chat scopes
            await self.app.bot.set_my_commands(commands, scope=BotCommandScopeDefault())
            with contextlib.suppress(Exception):
                await self.app.bot.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats())
            if self.chat_id:
                with contextlib.suppress(Exception):
                    await self.app.bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=int(self.chat_id)))

            # Configure interactive menu button for instant palette / autocomplete access
            with contextlib.suppress(Exception):
                await self.app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
            if self.chat_id:
                with contextlib.suppress(Exception):
                    await self.app.bot.set_chat_menu_button(chat_id=int(self.chat_id), menu_button=MenuButtonCommands())

            logger.info(
                "Successfully registered Telegram slash command palette (set_my_commands and menu button across all scopes)"
            )
            return True
        except Exception as e:
            logger.warning(f"Failed to register Telegram bot commands: {e}")
            return False

    async def start_polling(self) -> None:
        """Initialize, register slash commands, start application, and start updater polling."""
        if not self.is_configured() or not self.app or not self.app.updater:
            logger.warning("TelegramNotifier not configured or app unavailable; cannot start polling.")
            return
        await self.app.initialize()
        await self.setup_bot_commands()
        await self.app.start()
        await self.app.updater.start_polling(timeout=self.settings.poll_timeout_seconds, drop_pending_updates=False)

    async def stop_polling(self) -> None:
        """Cleanly stop updater polling and shutdown the Telegram application."""
        if self.app:
            if self.app.updater and self.app.updater.running:
                await self.app.updater.stop()
            if self.app.running:
                await self.app.stop()
            await self.app.shutdown()
            self.metrics.set_gauge("trader_telegram_poll_healthy", 0)

    def _register_handlers(self):
        if self.app:
            self.app.add_error_handler(self._handle_error)
            self.app.add_handler(CallbackQueryHandler(self._observe_handler(self.handle_button_callback)))
            self.app.add_handler(CommandHandler(["start", "help"], self._observe_handler(self.handle_help_command)))
            self.app.add_handler(CommandHandler("status", self._observe_handler(self.handle_status_command)))
            self.app.add_handler(CommandHandler("scan", self._observe_handler(self.handle_scan_command)))
            self.app.add_handler(CommandHandler("positions", self._observe_handler(self.handle_positions_command)))
            self.app.add_handler(CommandHandler("close", self._observe_handler(self.handle_close_command)))
            self.app.add_handler(CommandHandler("flatten", self._observe_handler(self.handle_flatten_command)))
            self.app.add_handler(CommandHandler("perf", self._observe_handler(self.handle_perf_command)))
            self.app.add_handler(CommandHandler("macro", self._observe_handler(self.handle_macro_command)))
            self.app.add_handler(
                CommandHandler("explain_macro", self._observe_handler(self.handle_explain_macro_command))
            )
            self.app.add_handler(CommandHandler("alphas", self._observe_handler(self.handle_alphas_command)))
            self.app.add_handler(CommandHandler("backtest", self._observe_handler(self.handle_backtest_command)))
            self.app.add_handler(CommandHandler("gex", self._observe_handler(self.handle_gex_command)))
            self.app.add_handler(CommandHandler("pairs", self._observe_handler(self.handle_pairs_command)))
            self.app.add_handler(CommandHandler("panic", self._observe_handler(self.handle_panic_command)))
            self.app.add_handler(CommandHandler("resume", self._observe_handler(self.handle_resume_command)))
            self.app.add_handler(
                MessageHandler(filters.TEXT & ~filters.COMMAND, self._observe_handler(self.handle_chat_message))
            )

    async def safe_reply_text(
        self,
        message: Any,
        text: str,
        parse_mode: str = "HTML",
        max_chunk_len: int = TELEGRAM_MESSAGE_CHUNK_LENGTH,
    ) -> list[Any]:
        """Safely send or reply to a Telegram message with HTML sanitization,
        chunking for messages exceeding Telegram's 4096-character limit,
        and automatic plain-text fallback if Telegram's entity parser rejects HTML.
        """
        if not text or not message:
            return []

        sanitized = TelegramHtmlFormatter.sanitize_telegram_html(text) if parse_mode == "HTML" else text
        chunks = TelegramHtmlFormatter.split_telegram_message(sanitized, max_chunk_len=max_chunk_len)

        sent_messages: list[Any] = []
        for chunk in chunks:
            try:
                sent = await message.reply_text(chunk, parse_mode=parse_mode or None)
            except BadRequest as exc:
                if parse_mode != "HTML" or "parse entities" not in str(exc).lower():
                    raise
                logger.warning("Telegram rejected HTML entities; retrying this reply as plain text")
                sent = await message.reply_text(TelegramHtmlFormatter.strip_html(chunk), parse_mode=None)
            sent_messages.append(sent)
        return sent_messages

    async def handle_chat_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle natural language conversational queries from the operator."""
        if not update.effective_chat or not update.message or not update.message.text:
            return
        if not self._is_authorized(update):
            logger.warning(f"Unauthorized chat message received from {update.effective_chat.id}")
            return

        user_text = update.message.text.strip()
        if not user_text:
            return

        with contextlib.suppress(Exception):
            await update.effective_chat.send_action(ChatAction.TYPING)

        if not self.chat_handler:
            await update.message.reply_text(
                "🤖 Conversational copilot is not enabled or configured.",
                parse_mode="HTML",
            )
            return

        chat_id = str(update.effective_chat.id)
        try:
            response = await self.chat_handler(user_text, chat_id)
        except Exception as e:
            logger.exception("Error invoking copilot chat handler")
            response = f"❌ Error processing copilot request: {e}"

        await self.safe_reply_text(update.message, response, parse_mode="HTML")

    async def handle_help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update) or not update.message:
            return
        help_text = (
            f"🤖 <b>{APP_DISPLAY_NAME}</b>\n\n"
            "<b>Available Commands:</b>\n"
            "• /status - View portfolio exposure, cash base, and macro events\n"
            "• /positions - Broker positions, cost basis and unrealized P&amp;L\n"
            "• /perf - Account performance and tracked trade statistics\n"
            "• /macro - VIX, yields, credit, inflation and combined trading filters\n"
            "• /explain_macro - Tutorial &amp; educational indicator breakdown with LLM context\n"
            "• /alphas - View formulaic alpha intelligence, catalog, and active strategies\n"
            "• /pairs - View statistical arbitrage pairs, cointegration &amp; Z-scores\n"
            "• /gex [sym] - View market maker gamma exposure (GEX), walls, and gamma flip (e.g. <code>/gex SPY</code>)\n"
            "• /backtest [sym] [lookback] - Run an offline backtest (e.g. <code>/backtest SPY 1y</code>)\n"
            "• /close &lt;id&gt; - Request broker closure; accounting waits for fills\n"
            "• /scan - Trigger an on-demand quantitative scan across universe\n"
            "• /flatten [confirm] - Preview/close all broker positions; halt state unchanged\n"
            "• /panic [confirm] - 🔴 Emergency kill switch: cancel orders, liquidate &amp; halt\n"
            "• /resume - 🟢 Clear emergency halt and restore normal operations\n"
            "• /help - Display this command overview\n\n"
            "<b>Trading workflow:</b>\n"
            "Scans stage suggestions for operator approval. Risk and sizing use the loaded configuration.\n"
            "Use /status for configured capital, exposure and macro context.\n"
            "Broker fills determine trade accounting. GEX, pairs and backtests are research estimates."
        )
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🔍 Scan Now", callback_data="cmd_scan"),
                    InlineKeyboardButton("📈 Positions", callback_data="cmd_positions"),
                ],
                [
                    InlineKeyboardButton("📊 Performance", callback_data="cmd_perf"),
                    InlineKeyboardButton("🌐 Macro & Filters", callback_data="cmd_macro"),
                ],
            ]
        )
        await update.message.reply_text(help_text, parse_mode="HTML", reply_markup=keyboard)

    async def handle_positions_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update) or not update.message:
            return
        if self.positions_provider:
            resp = await self.positions_provider()
            await self.safe_reply_text(update.message, resp)
        else:
            await update.message.reply_text("Positions provider not attached.")

    async def handle_perf_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update) or not update.message:
            return
        if self.perf_provider:
            resp = await self.perf_provider()
            await self.safe_reply_text(update.message, resp)
        else:
            await update.message.reply_text("Performance provider not attached.")

    async def handle_macro_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update) or not update.message:
            return
        if self.macro_provider:
            resp = await self.macro_provider()
            await self.safe_reply_text(update.message, resp)
        else:
            await update.message.reply_text("Macro intelligence provider not attached.")

    async def handle_explain_macro_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update) or not update.message:
            return
        if self.explain_macro_provider:
            resp = await self.explain_macro_provider()
            await self.safe_reply_text(update.message, resp)
        else:
            await update.message.reply_text("Macro explanation provider not attached.")

    async def handle_alphas_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update) or not update.message:
            return
        if self.alphas_provider:
            resp = await self.alphas_provider()
            await self.safe_reply_text(update.message, resp)
        else:
            await update.message.reply_text("Alpha provider not attached.")

    async def handle_backtest_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update) or not update.message:
            return
        args = context.args or []
        symbol = args[0].upper() if len(args) > 0 else DEFAULT_RESEARCH_SYMBOL
        lookback = args[1] if len(args) > 1 else self.backtest_lookback

        await update.message.reply_text(
            f"⏳ Running backtest simulation for <b>{html.escape(symbol)}</b> ({html.escape(lookback)})...",
            parse_mode="HTML",
        )
        if self.backtest_runner:
            resp = await self.backtest_runner(symbol, lookback)
            await self.safe_reply_text(update.message, resp)
        else:
            await update.message.reply_text("Backtest runner not attached.")

    async def handle_gex_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update) or not update.message:
            return
        args = context.args or []
        symbol = args[0].upper() if len(args) > 0 else DEFAULT_RESEARCH_SYMBOL

        await update.message.reply_text(
            f"🧭 Analyzing options gamma exposure & dealer walls for <b>{html.escape(symbol)}</b>...",
            parse_mode="HTML",
        )
        if self.gex_provider:
            resp = await self.gex_provider(symbol)
            await self.safe_reply_text(update.message, resp)
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
            resp = await self.pairs_provider()
            await self.safe_reply_text(update.message, resp)
        else:
            await update.message.reply_text("Pairs provider not attached.")

    async def handle_close_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update) or not update.message:
            return
        args = context.args or []
        if not args:
            await update.message.reply_text(
                "Usage: <code>/close &lt;signal_id&gt; [exit_price]</code>\nAlpaca waits for the broker fill; exit_price is for simulation/manual adapters.",
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
            await self.safe_reply_text(update.message, resp)
        else:
            await update.message.reply_text("Close handler not attached.")

    async def handle_flatten_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update) or not update.message:
            return
        args = context.args or []
        if args not in ([], ["confirm"], ["dry-run"]):
            await update.message.reply_text("Usage: /flatten [confirm|dry-run]")
            return
        if self.flatten_handler is None:
            await update.message.reply_text("Flatten handler is unavailable.")
            return
        confirm = args == ["confirm"]
        response = await self.flatten_handler(confirm)
        if not confirm:
            response += "\nTo submit closes for the current positions, send <code>/flatten confirm</code>."
        await self.safe_reply_text(update.message, response)

    async def handle_panic_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update) or not update.message:
            return
        args = context.args or []
        is_confirmed = bool(args and args[0].lower() in ("confirm", "force", "yes", "now"))

        if is_confirmed:
            await update.message.reply_text(
                "🚨 <b>EMERGENCY KILL SWITCH ENGAGED... LIQUIDATING NOW</b>", parse_mode="HTML"
            )
            if self.panic_handler:
                try:
                    res = await self.panic_handler("Manual /panic confirm triggered via Telegram")
                    if isinstance(res, str):
                        await update.message.reply_text(res, parse_mode="HTML")
                except Exception as e:
                    await update.message.reply_text(f"❌ Error during emergency panic: {e}")
            else:
                await update.message.reply_text("❌ Panic handler not attached to copilot.")
            return

        # Two-step confirmation prompt with warning card and interactive inline button
        active_count = await self.db.get_active_position_count() if self.db else 0
        open_notional = await self.db.get_active_notional_exposure() if self.db else 0.0
        warning_card = (
            "⚠️ <b>EMERGENCY KILL SWITCH CONFIRMATION REQUIRED</b> ⚠️\n\n"
            f"• <b>Active Open Positions:</b> <code>{active_count}</code>\n"
            f"• <b>Total Open Notional:</b> <code>${open_notional:,.2f}</code>\n\n"
            "<b>Institutional Kill Switch Waterfall:</b>\n"
            "1. <b>Ingress Cancellation:</b> Immediate withdrawal of all working/resting broker orders.\n"
            "2. <b>Egress Liquidation:</b> Market orders to flatten all active positions.\n"
            "3. <b>Trading Halt:</b> Persistent circuit breaker blocks all scans &amp; executions.\n\n"
            "Are you sure you want to trigger emergency liquidation?"
        )
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔴 CONFIRM EMERGENCY LIQUIDATE & HALT",
                        callback_data="panic_confirm",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data="panic_cancel",
                    )
                ],
            ]
        )
        await update.message.reply_text(warning_card, parse_mode="HTML", reply_markup=keyboard)

    async def handle_resume_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update) or not update.message:
            return
        if self.resume_handler:
            try:
                res = await self.resume_handler()
                if isinstance(res, str):
                    await update.message.reply_text(res, parse_mode="HTML")
                elif isinstance(res, dict) and not res.get("success", False):
                    await update.message.reply_text(f"❌ Failed to resume: {res.get('message', 'Unknown error')}")
            except Exception as e:
                await update.message.reply_text(f"❌ Error resuming trading: {e}")
        else:
            await update.message.reply_text("❌ Resume handler not attached to copilot.")

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
                    InlineKeyboardButton("🌐 Macro & Filters", callback_data="cmd_macro"),
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
        if not self._is_authorized(update):
            return
        query = update.callback_query
        if not query or not query.data:
            return
        data = query.data
        msg = query.message

        async def _safe_set_markup(markup: InlineKeyboardMarkup | None) -> None:
            if hasattr(query, "edit_message_reply_markup"):
                try:
                    markup_res = query.edit_message_reply_markup(reply_markup=markup)
                    if inspect.isawaitable(markup_res):
                        await markup_res
                except Exception:
                    pass

        async def _safe_clear_markup() -> None:
            await _safe_set_markup(None)

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
        elif data == "cmd_macro":
            await query.answer()
            if self.macro_provider and msg and hasattr(msg, "reply_text"):
                res = await self.macro_provider()
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
                reply = await self.execute_handler(signal_id, quantity=quantity)
                if getattr(reply, "retryable", False):
                    # The card is still PENDING (e.g. price/checks unavailable); restore the
                    # tapped button plus dismiss so the operator can retry. Other tiers on a
                    # multi-tier card are not reconstructable here, so only the button that was
                    # actually tapped is restored.
                    restore_markup = InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(_exec_button_label(self.execution_mode), callback_data=data),
                                InlineKeyboardButton("❌ Dismiss Signal", callback_data=f"dism_{signal_id}"),
                            ]
                        ]
                    )
                    await _safe_set_markup(restore_markup)
                if msg and hasattr(msg, "reply_text"):
                    if reply.offer_reevaluate:
                        reeval_markup = InlineKeyboardMarkup(
                            [[InlineKeyboardButton("🔄 Re-evaluate", callback_data=f"reval_{signal_id}")]]
                        )
                        await msg.reply_text(reply.text, parse_mode="HTML", reply_markup=reeval_markup)
                    else:
                        await msg.reply_text(reply.text, parse_mode="HTML")
            else:
                await query.answer("Execution handler unavailable; no order submitted.", show_alert=True)

        elif data.startswith("dism_"):
            await query.answer()
            signal_id = int(data.split("_")[1])
            logger.info(
                "Dismiss button clicked for signal #%d",
                signal_id,
                extra={"signal_id": signal_id, "action": "dismiss"},
            )
            dismissed = await self.db.dismiss_signal(signal_id) if self.db else False
            await _safe_clear_markup()
            if msg and hasattr(msg, "reply_text"):
                await msg.reply_text(
                    f"❌ Signal #{signal_id} {SignalStatus.DISMISSED}."
                    if dismissed
                    else f"Signal #{signal_id} is no longer pending; no trade state changed."
                )
        elif data.startswith("reval_"):
            signal_id = int(data.split("_")[1])
            logger.info(
                "Re-evaluate button clicked for signal #%d",
                signal_id,
                extra={"signal_id": signal_id, "action": "reevaluate"},
            )
            if self.reevaluate_handler:
                await query.answer("Re-evaluating…")
                await _safe_clear_markup()
                reply = await self.reevaluate_handler(signal_id)
                if msg and hasattr(msg, "reply_text"):
                    await msg.reply_text(reply.text, parse_mode="HTML")
            else:
                await query.answer("Re-evaluate handler unavailable.", show_alert=True)
        elif data == "panic_confirm":
            await query.answer("Executing emergency kill switch...")
            await _safe_clear_markup()
            if self.panic_handler:
                try:
                    res = await self.panic_handler("Manual panic confirmation button tapped in Telegram")
                    if isinstance(res, str) and msg and hasattr(msg, "reply_text"):
                        await msg.reply_text(res, parse_mode="HTML")
                except Exception as e:
                    if msg and hasattr(msg, "reply_text"):
                        await msg.reply_text(f"❌ Error during emergency panic: {e}")
            else:
                if msg and hasattr(msg, "reply_text"):
                    await msg.reply_text("❌ Panic handler not attached to copilot.")
        elif data == "panic_cancel":
            await query.answer("Panic cancelled")
            await _safe_clear_markup()
            if msg and hasattr(msg, "reply_text"):
                await msg.reply_text(
                    "🛡️ <b>Emergency Kill Switch Cancelled.</b> Active positions and trading operations remain untouched.",
                    parse_mode="HTML",
                )

    async def send_signal_alert(
        self,
        eval_res: LLMTradeEvaluation,
        strategy: str,
        signal_id: int,
        regime_summary: str | None = None,
        probe_risk_cap: float | None = None,
        valid_until: str | None = None,
        reprices: int | None = None,
        first_issued_at: str | None = None,
    ) -> int | None:
        # ``valid_until``/``reprices``/``first_issued_at`` arrive in card payloads recorded
        # by the scan and by tap-time re-pricing; rendered on both cards below.
        # Always output to terminal/logs
        print(
            format_terminal_card(
                eval_res,
                strategy,
                self.portfolio_cash,
                execution_mode=self.execution_mode,
                regime_summary=regime_summary,
                valid_until=valid_until,
                reprices=reprices,
                first_issued_at=first_issued_at,
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
            probe_risk_cap=probe_risk_cap,
            valid_until=valid_until,
            reprices=reprices,
            first_issued_at=first_issued_at,
        )

        exec_btn_text = _exec_button_label(self.execution_mode)

        tiers = eval_res.sizing_tiers
        asset_class = eval_res.asset_class
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
                text=self._label(card_html),
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
            if self.db:
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
        quantity: float = 1.0,
        asset_class: AssetClass = AssetClass.FUTURES,
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
            quantity=quantity,
            asset_class=asset_class,
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
                text=self._label(card_html),
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

    async def send_trailing_stop_alert(
        self,
        signal_id: int,
        contract: str,
        direction: str,
        old_stop: float,
        new_stop: float,
        current_price: float,
        reason: str = "BREAKEVEN",
        broker_synced: bool = False,
    ) -> bool:
        """Send formatted alert when position stop is moved to breakeven or trailed upward."""
        icon = "🛡️" if reason == "BREAKEVEN" else "📈"
        title = "STOP TO BREAKEVEN" if reason == "BREAKEVEN" else "TRAILING STOP RATCHETED"
        broker_status = "Synced on Exchange ⚡" if broker_synced else "Local Copilot Tracking 🛡️"
        text = (
            f"{icon} <b>{title}</b>\n\n"
            f"• <b>Position:</b> #{signal_id} <code>{contract}</code> ({direction.upper()})\n"
            f"• <b>Market Price:</b> <code>{current_price:,.2f}</code>\n"
            f"• <b>Old Stop:</b> <code>{old_stop:,.2f}</code>\n"
            f"• <b>New Protective Stop:</b> <code>{new_stop:,.2f}</code>\n"
            f"• <b>Broker Sync:</b> {broker_status}\n"
            f"• <b>Action:</b> Capital preserved / unrealized gains locked in"
        )
        return await self.send_message(text)

    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        if not self.is_configured() or not self.app:
            return False
        try:
            bot = self.app.bot
            await bot.send_message(
                chat_id=self.chat_id,
                text=self._label(text),
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
