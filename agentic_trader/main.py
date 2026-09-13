import argparse
import asyncio
import contextlib
import logging
import os
import sys
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from agentic_trader.agent.calendar import EconomicCalendar
from agentic_trader.agent.evaluator import LLMTradeEvaluation, RiskEvaluator
from agentic_trader.broker import BaseBroker, OrderRequest, create_broker
from agentic_trader.config import WORKSPACE_ROOT, AppConfig, load_config
from agentic_trader.constants import (
    Direction,
    ExitReason,
    SignalStatus,
    StrategyType,
)
from agentic_trader.data.market_data import MarketDataFetcher
from agentic_trader.notifier.telegram_bot import TelegramNotifier, format_terminal_card
from agentic_trader.screeners.strategies import StrategyEngine
from agentic_trader.storage.db import SignalDatabase


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("copilot")


class FuturesCopilot:
    def __init__(self, config: AppConfig):
        self.config = config
        self.db = SignalDatabase(config.db_path)
        self.data_fetcher = MarketDataFetcher()
        self.broker: BaseBroker = create_broker(config=config, data_fetcher=self.data_fetcher)
        self.strategy_engine = StrategyEngine(config)
        self.calendar = EconomicCalendar(finnhub_api_key=config.finnhub_api_key)
        self.evaluator = RiskEvaluator(config, calendar=self.calendar)
        self.notifier = TelegramNotifier(
            bot_token=config.telegram_bot_token,
            chat_id=config.telegram_chat_id,
            db=self.db,
            portfolio_cash=config.portfolio.cash,
            execution_mode=config.execution_mode,
            status_provider=self.get_status_text_html,
            scan_runner=self.run_scan_summary_html,
            positions_provider=self.get_positions_summary_html,
            close_handler=self.close_position_manual,
            execute_handler=self.execute_signal_by_id,
        )

    async def run_scan(self, use_llm: bool = True, dry_run: bool = False):
        logger.info("=== Starting Quantitative Scan ===")
        current_exposure = await self.db.get_active_notional_exposure()
        active_count = await self.db.get_active_contract_count()
        logger.info(
            f"Portfolio Status: {active_count} active contracts | "
            f"Open Notional: ${current_exposure:,.2f} / ${self.config.portfolio.max_notional_exposure:,.2f} max"
        )

        in_lockout, lock_event = await self.calendar.is_in_lockout_window(
            pre_minutes=self.config.risk.lockout_pre_event_minutes,
            post_minutes=self.config.risk.lockout_post_event_minutes,
        )
        if in_lockout and lock_event:
            logger.warning(
                f"Macro Lockout Active: '{lock_event.title}' at {lock_event.timestamp.strftime('%H:%M UTC')}. "
                "No entry alerts will be emitted during this window."
            )
            return

        total_candidates = 0
        total_alerts = 0

        for contract, info in self.config.contracts.items():
            logger.info(f"Scanning contract {contract} ({info.name} - {info.ticker})...")
            try:
                data = self.data_fetcher.fetch_data(contract, info.ticker)
                if data.daily.empty or data.four_hour.empty:
                    logger.warning(f"Insufficient data for {contract}, skipping.")
                    continue

                candidates = self.strategy_engine.scan_contract(data)
                for candidate in candidates:
                    total_candidates += 1
                    logger.info(
                        "Found setup: %s %s via %s at %.2f",
                        candidate.contract,
                        candidate.direction,
                        candidate.strategy,
                        candidate.current_price,
                        extra={
                            "contract": candidate.contract,
                            "direction": candidate.direction,
                            "strategy": candidate.strategy,
                            "price": candidate.current_price,
                        },
                    )

                    # Deduplication check
                    is_dup = await self.db.is_duplicate_recent(
                        candidate.contract,
                        candidate.strategy,
                        hours=self.config.risk.deduplication_hours,
                    )
                    if is_dup:
                        logger.info(
                            "Skipping duplicate signal: %s %s already alerted within %d hours.",
                            candidate.contract,
                            candidate.strategy,
                            self.config.risk.deduplication_hours,
                            extra={"contract": candidate.contract, "strategy": candidate.strategy},
                        )
                        continue

                    # Risk evaluation
                    eval_res = await self.evaluator.evaluate_candidate(
                        candidate,
                        current_open_notional=current_exposure,
                        use_llm=use_llm,
                    )

                    if not eval_res.approved:
                        logger.info(
                            "Candidate rejected by risk engine: %s",
                            eval_res.rejection_reason,
                            extra={"contract": candidate.contract, "rejection_reason": eval_res.rejection_reason},
                        )
                        continue

                    if dry_run:
                        logger.info("[DRY RUN] Approved signal would be emitted:")
                        print(format_terminal_card(eval_res, candidate.strategy, self.config.portfolio.cash))
                        continue

                    # Record to database
                    sig_id = await self.db.record_signal(
                        contract=eval_res.contract,
                        strategy=candidate.strategy,
                        direction=eval_res.direction,
                        entry_price=eval_res.entry_price,
                        stop_loss=eval_res.stop_loss,
                        take_profit=eval_res.take_profit,
                        risk_dollars=eval_res.risk_dollars,
                        reward_dollars=eval_res.reward_dollars,
                        notional_value=eval_res.notional_value,
                        status=SignalStatus.PENDING,
                        raw_response=eval_res.model_dump_json(),
                    )

                    # Dispatch alert
                    await self.notifier.send_signal_alert(
                        eval_res=eval_res,
                        strategy=candidate.strategy,
                        signal_id=sig_id,
                    )
                    total_alerts += 1
                    # Update exposure in memory for subsequent checks in this run
                    current_exposure += eval_res.notional_value

            except Exception:
                logger.exception(f"Error scanning {contract}")

        logger.info(f"=== Scan Complete: {total_candidates} candidates evaluated, {total_alerts} alerts emitted ===")
        # Monitor any active positions for stop loss or take profit crossings
        await self.monitor_positions()

    async def monitor_positions(self) -> int:
        """
        Check real-time price against Stop Loss and Take Profit for all active (EXECUTED) positions.
        Returns the number of positions closed during this check.
        """
        active_positions = await self.db.get_active_positions()
        if not active_positions:
            logger.debug("No active positions to monitor.")
            return 0

        logger.info("Monitoring %d active position(s)...", len(active_positions))
        closed_count = 0

        for pos in active_positions:
            signal_id = pos["id"]
            contract = pos["contract"]
            direction = pos["direction"].upper()
            entry_price = float(pos["entry_price"])
            stop_loss = float(pos["stop_loss"])
            take_profit = float(pos["take_profit"])
            strategy = pos["strategy"]

            contract_info = self.config.contracts.get(contract)
            ticker = contract_info.ticker if contract_info else "MES=F"
            multiplier = contract_info.multiplier if contract_info else 5.0

            current_price = self.data_fetcher.fetch_latest_price(ticker)
            if current_price is None:
                logger.warning("Could not fetch latest quote for %s (%s). Skipping check.", contract, ticker)
                continue

            hit_tp = False
            hit_sl = False

            if direction == Direction.LONG:
                if current_price >= take_profit:
                    hit_tp = True
                elif current_price <= stop_loss:
                    hit_sl = True
            elif direction == Direction.SHORT:
                if current_price <= take_profit:
                    hit_tp = True
                elif current_price >= stop_loss:
                    hit_sl = True

            if hit_tp or hit_sl:
                exit_reason = ExitReason.TAKE_PROFIT if hit_tp else ExitReason.STOP_LOSS
                status = SignalStatus.CLOSED_WIN if hit_tp else SignalStatus.CLOSED_LOSS

                realized_pnl = (
                    (current_price - entry_price) * multiplier
                    if direction == Direction.LONG
                    else (entry_price - current_price) * multiplier
                )

                logger.info(
                    "Position %s %s hit %s at %.2f (Realized PnL: $%.2f)",
                    contract,
                    direction,
                    exit_reason,
                    current_price,
                    realized_pnl,
                    extra={
                        "signal_id": signal_id,
                        "contract": contract,
                        "direction": direction,
                        "exit_reason": exit_reason,
                        "exit_price": current_price,
                        "realized_pnl": realized_pnl,
                    },
                )

                # Close position at broker
                try:
                    await self.broker.close_position(
                        contract=contract,
                        exit_reason=exit_reason,
                        exit_price=current_price,
                    )
                except Exception as e:
                    logger.warning(
                        "Broker close_position exception for %s: %s",
                        contract,
                        e,
                        extra={"contract": contract, "error": str(e)},
                    )

                await self.db.close_position(
                    signal_id=signal_id,
                    exit_price=current_price,
                    exit_reason=exit_reason,
                    realized_pnl=realized_pnl,
                    status=status,
                )
                closed_count += 1

                await self.notifier.send_exit_alert(
                    contract=contract,
                    direction=direction,
                    exit_reason=exit_reason,
                    entry_price=entry_price,
                    exit_price=current_price,
                    realized_pnl=realized_pnl,
                    strategy=strategy,
                )

        return closed_count

    async def show_positions(self):
        """Display active positions in terminal."""
        positions = await self.db.get_active_positions()
        print("=" * 65)
        print("CASH-PLUS TRADING COPILOT: ACTIVE POSITIONS")
        print("=" * 65)
        if not positions:
            print("  (No active positions currently tracked)")
        else:
            total_unrealized = 0.0
            for pos in positions:
                contract = pos["contract"]
                direction = pos["direction"].upper()
                entry = float(pos["entry_price"])
                sl = float(pos["stop_loss"])
                tp = float(pos["take_profit"])
                contract_info = self.config.contracts.get(contract)
                ticker = contract_info.ticker if contract_info else "MES=F"
                multiplier = contract_info.multiplier if contract_info else 5.0

                current = self.data_fetcher.fetch_latest_price(ticker) or entry
                pnl = (current - entry) * multiplier if direction == Direction.LONG else (entry - current) * multiplier
                total_unrealized += pnl
                pnl_str = f"+${pnl:,.2f}" if pnl >= 0 else f"-${abs(pnl):,.2f}"
                print(
                    f"  #{pos['id']} {contract} {direction} | Entry: {entry:,.2f} | Current: {current:,.2f} | "
                    f"Stop: {sl:,.2f} | Target: {tp:,.2f} | PnL: {pnl_str}"
                )
            print("-" * 65)
            tot_str = f"+${total_unrealized:,.2f}" if total_unrealized >= 0 else f"-${abs(total_unrealized):,.2f}"
            print(f"Total Unrealized PnL: {tot_str}")
        print("=" * 65)

    async def get_positions_summary_html(self) -> str:
        """Format HTML message of tracked positions for Telegram /positions."""
        positions = await self.db.get_active_positions()
        if not positions:
            return (
                "📋 <b>ACTIVE POSITIONS (0)</b>\n\n"
                "<i>No active positions currently tracked.</i>\n"
                "When trade signals are acknowledged in Telegram, they appear here."
            )

        total_unrealized_pnl = 0.0
        lines = [f"📋 <b>ACTIVE POSITIONS ({len(positions)})</b>\n"]

        for pos in positions:
            contract = pos["contract"]
            direction = pos["direction"].upper()
            entry = float(pos["entry_price"])
            sl = float(pos["stop_loss"])
            tp = float(pos["take_profit"])
            contract_info = self.config.contracts.get(contract)
            ticker = contract_info.ticker if contract_info else "MES=F"
            multiplier = contract_info.multiplier if contract_info else 5.0

            current_price = self.data_fetcher.fetch_latest_price(ticker) or entry
            pnl = (
                (current_price - entry) * multiplier
                if direction == Direction.LONG
                else (entry - current_price) * multiplier
            )
            total_unrealized_pnl += pnl

            pnl_sign = "+" if pnl >= 0 else "-"
            pnl_str = f"{pnl_sign}${abs(pnl):,.2f}"

            lines.append(
                f"• <b>#{pos['id']} {contract} ({direction})</b>\n"
                f"  Entry: <code>{entry:,.2f}</code> | Current: <code>{current_price:,.2f}</code>\n"
                f"  Stop: <code>{sl:,.2f}</code> | Target: <code>{tp:,.2f}</code>\n"
                f"  Unrealized P&amp;L: <b>{pnl_str}</b>\n"
            )

        tot_sign = "+" if total_unrealized_pnl >= 0 else "-"
        lines.append(f"\n<b>Total Unrealized P&amp;L:</b> {tot_sign}${abs(total_unrealized_pnl):,.2f}")
        lines.append("\n💡 <i>To close a trade manually:</i> <code>/close &lt;id&gt; [exit_price]</code>")
        return "\n".join(lines)

    async def close_position_manual(self, signal_id: int, exit_price: float | None = None) -> str:
        """Manually close a position (via Telegram /close or CLI)."""
        pos = await self.db.get_signal_by_id(signal_id)
        if not pos:
            return f"❌ Signal #{signal_id} not found."
        if pos["status"] != SignalStatus.EXECUTED:
            return (
                f"❌ Signal #{signal_id} is in status <b>{pos['status']}</b> (only EXECUTED positions can be closed)."
            )

        contract = pos["contract"]
        direction = pos["direction"].upper()
        entry = float(pos["entry_price"])
        contract_info = self.config.contracts.get(contract)
        multiplier = contract_info.multiplier if contract_info else 5.0
        ticker = contract_info.ticker if contract_info else "MES=F"

        final_exit = exit_price or self.data_fetcher.fetch_latest_price(ticker) or entry
        realized_pnl = (
            (final_exit - entry) * multiplier if direction == Direction.LONG else (entry - final_exit) * multiplier
        )

        logger.info(
            "Manually closing position #%d for %s (Exit: %.2f, Realized PnL: $%.2f)",
            signal_id,
            contract,
            final_exit,
            realized_pnl,
            extra={
                "signal_id": signal_id,
                "contract": contract,
                "direction": direction,
                "exit_price": final_exit,
                "realized_pnl": realized_pnl,
            },
        )

        # Close position at broker
        try:
            await self.broker.close_position(
                contract=contract,
                exit_reason=ExitReason.MANUAL_CLOSE,
                exit_price=final_exit,
            )
        except Exception as e:
            logger.warning(
                "Broker close_position exception for %s: %s",
                contract,
                e,
                extra={"contract": contract, "error": str(e)},
            )

        status = SignalStatus.CLOSED_WIN if realized_pnl >= 0 else SignalStatus.CLOSED_LOSS
        await self.db.close_position(
            signal_id=signal_id,
            exit_price=final_exit,
            exit_reason=ExitReason.MANUAL_CLOSE,
            realized_pnl=realized_pnl,
            status=status,
        )

        await self.notifier.send_exit_alert(
            contract=contract,
            direction=direction,
            exit_reason=ExitReason.MANUAL_CLOSE,
            entry_price=entry,
            exit_price=final_exit,
            realized_pnl=realized_pnl,
            strategy=pos["strategy"],
        )

        pnl_sign = "+" if realized_pnl >= 0 else "-"
        return (
            f"✅ <b>Position #{signal_id} Closed ({contract} {direction})</b>\n"
            f"• Exit Price: <code>{final_exit:,.2f}</code>\n"
            f"• Realized P&amp;L: <b>{pnl_sign}${abs(realized_pnl):,.2f}</b>\n"
            f"• Notional capacity released."
        )

    async def execute_signal_by_id(self, signal_id: int) -> tuple[bool, str]:
        """
        Execute an approved signal via the configured broker (Paper, Tradovate, or Alpaca).
        Enforces risk invariants before submission and records the fill order ID in SQLite.
        """
        sig = await self.db.get_signal_by_id(signal_id)
        if not sig:
            return False, f"❌ Signal #{signal_id} not found."
        if sig["status"] != SignalStatus.PENDING:
            return False, (
                f"❌ Signal #{signal_id} is in status <b>{sig['status']}</b> (only PENDING signals can be executed)."
            )

        contract = sig["contract"]
        direction = sig["direction"].upper()
        contract_info = self.config.contracts.get(contract)
        ticker = contract_info.ticker if contract_info else f"{contract.strip('/').upper()}=F"

        # Re-verify portfolio risk limits prior to live execution
        active_count = await self.db.get_active_contract_count()
        if active_count >= self.config.portfolio.max_concurrent_contracts:
            return False, (
                f"⚠️ <b>Execution Rejected:</b> Maximum concurrent contracts ({self.config.portfolio.max_concurrent_contracts}) reached."
            )

        current_exposure = await self.db.get_active_notional_exposure()
        if current_exposure + sig["notional_value"] > self.config.portfolio.max_notional_exposure:
            return False, (
                f"⚠️ <b>Execution Rejected:</b> Order notional (${sig['notional_value']:,.2f}) would breach "
                f"${self.config.portfolio.max_notional_exposure:,.2f} maximum portfolio notional ceiling."
            )

        # Transition status to SUBMITTING to prevent duplicate / concurrent trigger
        await self.db.update_signal_status(signal_id, SignalStatus.SUBMITTING)

        req = OrderRequest(
            signal_id=signal_id,
            contract=contract,
            ticker=ticker,
            direction=direction,
            entry_price=float(sig["entry_price"]),
            stop_loss=float(sig["stop_loss"]),
            take_profit=float(sig["take_profit"]),
            quantity=1,
        )

        try:
            order_result = await self.broker.submit_entry_order(req)
        except Exception as e:
            logger.exception(
                "Error calling broker.submit_entry_order for signal #%d",
                signal_id,
                extra={"signal_id": signal_id, "error": str(e)},
            )
            await self.db.update_signal_status(signal_id, SignalStatus.FAILED)
            return False, f"❌ <b>Broker Submission Error:</b> {e}"

        if order_result.success:
            fill_price = order_result.fill_price or float(sig["entry_price"])
            await self.db.update_signal_execution(
                signal_id=signal_id,
                broker_order_id=order_result.order_id,
                fill_price=fill_price,
                status=SignalStatus.EXECUTED,
            )
            logger.info(
                "Signal #%d executed successfully via broker (%s)",
                signal_id,
                self.config.execution_mode,
                extra={
                    "signal_id": signal_id,
                    "order_id": order_result.order_id,
                    "fill_price": fill_price,
                    "execution_mode": self.config.execution_mode,
                },
            )
            msg = (
                f"🚀 <b>ORDER EXECUTED ({self.config.execution_mode.upper()})</b>\n"
                f"• <b>Contract:</b> 1x {contract} ({direction})\n"
                f"• <b>Fill Price:</b> <code>{fill_price:,.2f}</code>\n"
                f"• <b>Broker Order ID:</b> <code>{order_result.order_id}</code>\n"
                f"• <b>Stop Loss:</b> <code>{sig['stop_loss']:,.2f}</code> | <b>Target:</b> <code>{sig['take_profit']:,.2f}</code>\n"
                f"• <i>Position is now active in risk tracking.</i>"
            )
            return True, msg
        else:
            await self.db.update_signal_status(signal_id, SignalStatus.FAILED)
            err = order_result.error_message or "Unknown broker rejection"
            logger.warning(
                "Signal #%d execution rejected by broker (%s): %s",
                signal_id,
                self.config.execution_mode,
                err,
                extra={"signal_id": signal_id, "error": err, "execution_mode": self.config.execution_mode},
            )
            msg = (
                f"❌ <b>Execution Failed ({self.config.execution_mode.upper()}):</b>\n"
                f"<code>{err}</code>\n"
                f"Signal #{signal_id} marked as FAILED. No portfolio exposure was locked."
            )
            return False, msg

    async def show_status(self):
        current_exposure = await self.db.get_active_notional_exposure()
        active_count = await self.db.get_active_contract_count()
        eff_leverage = current_exposure / self.config.portfolio.cash

        print("=" * 65)
        print("CASH-PLUS TRADING COPILOT: PORTFOLIO & RISK STATUS")
        print("=" * 65)
        print(f"Cash Base:            ${self.config.portfolio.cash:,.2f}")
        print(f"Max Notional Ceiling: ${self.config.portfolio.max_notional_exposure:,.2f} (0.6x max leverage)")
        print(f"Active Exposure:      ${current_exposure:,.2f} ({eff_leverage:.2f}x effective leverage)")
        print(f"Active Position Count:{active_count} contracts")
        print(f"Telegram Configured:  {self.notifier.is_configured()}")
        print(f"LLM Model Configured: {self.config.llm_model}")
        print("-" * 65)

        macro_summary = await self.calendar.get_macro_summary_for_prompt()
        print("Macro Calendar Context:")
        print(macro_summary)
        print("-" * 65)

        signals = await self.db.get_recent_signals(limit=10)
        print(f"Recent Signals ({len(signals)}):")
        if not signals:
            print("  (No signals in database)")
        else:
            for s in signals:
                print(
                    f"  #{s['id']} [{s['status']}] {s['timestamp']} | {s['contract']} {s['direction']} "
                    f"via {s['strategy']} @ {s['entry_price']} (Risk: ${s['risk_dollars']:.2f})"
                )
        print("=" * 65)

    async def get_status_text_html(self) -> str:
        current_exposure = await self.db.get_active_notional_exposure()
        active_count = await self.db.get_active_contract_count()
        eff_leverage = current_exposure / self.config.portfolio.cash
        macro_summary = await self.calendar.get_macro_summary_for_prompt()
        signals = await self.db.get_recent_signals(limit=5)

        signals_text = ""
        if not signals:
            signals_text = "\n<i>(No recorded signals)</i>"
        else:
            for s in signals:
                signals_text += f"\n• #{s['id']} [{s['status']}] {s['contract']} {s['direction']} @ {s['entry_price']:,.2f} (Risk: ${s['risk_dollars']:.2f})"

        return (
            "📊 <b>CASH-PLUS COPILOT: STATUS</b>\n\n"
            f"• <b>Cash Base:</b> ${self.config.portfolio.cash:,.2f}\n"
            f"• <b>Active Exposure:</b> ${current_exposure:,.2f} ({eff_leverage:.2f}x leverage)\n"
            f"• <b>Notional Cap:</b> ${self.config.portfolio.max_notional_exposure:,.2f} (0.6x max)\n"
            f"• <b>Active Positions:</b> {active_count} contracts\n"
            f"• <b>LLM Model:</b> <code>{self.config.llm_model}</code>\n\n"
            f"🛡️ <b>Macro Context:</b>\n{macro_summary}\n\n"
            f"🕒 <b>Recent Signals:</b>{signals_text}"
        )

    async def run_scan_summary_html(self) -> str:
        count_before = len(await self.db.get_recent_signals(limit=100))
        await self.run_scan(use_llm=True, dry_run=False)
        count_after = len(await self.db.get_recent_signals(limit=100))
        new_alerts = count_after - count_before
        if new_alerts > 0:
            return (
                f"✅ <b>Scan Complete:</b> {new_alerts} new trade setup(s) identified and alert cards dispatched above."
            )
        return "✅ <b>Scan Complete:</b> Evaluated all micro contracts (/MES, /MNQ, /MGC, /MCL). No setups met quantitative triggers at current candle."

    async def send_test_alert(self):
        print("Sending synthetic test alert card...")
        test_eval = LLMTradeEvaluation(
            approved=True,
            rejection_reason=None,
            contract="/MES",
            direction=Direction.LONG,
            entry_price=5812.50,
            stop_loss=5769.75,
            take_profit=5898.00,
            stop_distance_points=42.75,
            target_distance_points=85.50,
            risk_reward_ratio=2.0,
            risk_dollars=213.75,
            reward_dollars=427.50,
            notional_value=29062.50,
            effective_leverage=0.29,
            macro_clearance=True,
            thesis_summary="Daily trend is bullish (Price > 50 > 200 EMA). 4h RSI dipped to 42 and bounced off 20 EMA.",
        )
        sig_id = await self.db.record_signal(
            contract=test_eval.contract,
            strategy="TREND_PULLBACK_TEST",
            direction=test_eval.direction,
            entry_price=test_eval.entry_price,
            stop_loss=test_eval.stop_loss,
            take_profit=test_eval.take_profit,
            risk_dollars=test_eval.risk_dollars,
            reward_dollars=test_eval.reward_dollars,
            notional_value=test_eval.notional_value,
            status=SignalStatus.PENDING,
        )
        await self.notifier.send_signal_alert(test_eval, StrategyType.TREND_PULLBACK, sig_id)


async def async_main():
    parser = argparse.ArgumentParser(description="Cash-Plus Trading Copilot")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    scan_parser = subparsers.add_parser("scan", help="Run an immediate quantitative scan")
    scan_parser.add_argument("--no-llm", action="store_true", help="Disable LLM evaluation (use deterministic rules)")
    scan_parser.add_argument("--dry-run", action="store_true", help="Scan without persisting or emitting alerts")

    subparsers.add_parser("status", help="Display current portfolio exposure and recent signals")
    subparsers.add_parser("positions", help="Display active tracked positions and unrealized P&L")
    close_parser = subparsers.add_parser("close", help="Manually close an active position")
    close_parser.add_argument("signal_id", type=int, help="Signal ID to close")
    close_parser.add_argument("price", type=float, nargs="?", default=None, help="Optional exit fill price")

    exec_parser = subparsers.add_parser("execute", help="Execute an approved signal via the configured broker")
    exec_parser.add_argument("signal_id", type=int, help="Signal ID to execute")

    subparsers.add_parser("test-alert", help="Send a synthetic test alert to verify Telegram and formatting")
    subparsers.add_parser("listen", help="Start Telegram Bot callback listener only")
    subparsers.add_parser("eval", help="Run Promptfoo benchmark evaluation against LLM risk prompts")

    daemon_parser = subparsers.add_parser("daemon", help="Start continuous scheduler and Telegram listener")
    daemon_parser.add_argument("--no-llm", action="store_true", help="Disable LLM evaluation")

    args = parser.parse_args()
    config = load_config()
    copilot = FuturesCopilot(config)
    await copilot.broker.connect()

    if args.command == "scan":
        await copilot.run_scan(use_llm=not args.no_llm, dry_run=args.dry_run)
    elif args.command == "status":
        await copilot.show_status()
    elif args.command == "positions":
        await copilot.show_positions()
    elif args.command == "close":
        res = await copilot.close_position_manual(args.signal_id, args.price)
        clean_text = res.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", "")
        print(clean_text)
    elif args.command == "execute":
        _success, res = await copilot.execute_signal_by_id(args.signal_id)
        clean_text = (
            res.replace("<b>", "")
            .replace("</b>", "")
            .replace("<code>", "")
            .replace("</code>", "")
            .replace("<i>", "")
            .replace("</i>", "")
            .replace("• ", "  * ")
        )
        print(clean_text)
    elif args.command == "test-alert":
        await copilot.send_test_alert()
    elif args.command == "eval":
        logger.info("Running Promptfoo evaluation benchmark on risk prompts...")
        config_file = WORKSPACE_ROOT / "evals" / "promptfooconfig.yaml"
        provider = config.llm_model.replace("/", ":") if "/" in config.llm_model else f"openai:{config.llm_model}"
        logger.info("Evaluating production model: %s (Promptfoo provider: %s)", config.llm_model, provider)
        proc = await asyncio.create_subprocess_exec(
            "npx",
            "-y",
            "promptfoo",
            "eval",
            "--providers",
            provider,
            "-c",
            str(config_file),
            "--no-cache",
            env=os.environ.copy(),
        )
        rc = await proc.wait()
        sys.exit(rc)

    elif args.command == "listen":
        if not copilot.notifier.is_configured() or not copilot.notifier.app or not copilot.notifier.app.updater:
            logger.error("Telegram is not configured in .envrc")
            return
        logger.info("Starting Telegram Bot listener... (press Ctrl+C to stop)")
        await copilot.notifier.app.initialize()
        await copilot.notifier.app.start()
        await copilot.notifier.app.updater.start_polling()
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt, SystemExit:
            logger.info("Stopping listener...")
            await copilot.notifier.app.updater.stop()
            await copilot.notifier.app.stop()
            await copilot.notifier.app.shutdown()
    elif args.command == "daemon":
        scheduler = AsyncIOScheduler()
        interval = config.scheduler.cron_hour_interval
        # Schedule regular scans
        scheduler.add_job(
            copilot.run_scan,
            "interval",
            hours=interval,
            args=[not args.no_llm, False],
            next_run_time=datetime.now(UTC),
        )
        # Schedule automated position monitoring every 15 minutes
        scheduler.add_job(
            copilot.monitor_positions,
            "interval",
            minutes=15,
            id="position_monitor",
            next_run_time=datetime.now(UTC),
        )
        scheduler.start()
        logger.info(f"Scheduler started: scanning every {interval}h, monitoring positions every 15m.")

        if copilot.notifier.is_configured() and copilot.notifier.app and copilot.notifier.app.updater:
            logger.info("Starting Telegram Bot listener for interactive callbacks...")
            await copilot.notifier.app.initialize()
            await copilot.notifier.app.start()
            await copilot.notifier.app.updater.start_polling()

        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt, SystemExit:
            logger.info("Shutting down daemon...")

            scheduler.shutdown()
            if copilot.notifier.app and copilot.notifier.app.updater:
                await copilot.notifier.app.updater.stop()
                await copilot.notifier.app.stop()
                await copilot.notifier.app.shutdown()
    else:
        parser.print_help()


def main():
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(async_main())


if __name__ == "__main__":
    main()
