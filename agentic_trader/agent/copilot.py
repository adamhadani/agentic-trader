from __future__ import annotations

import asyncio
import logging
from typing import Any

from agentic_trader.agent.calendar import BaseEconomicCalendar, EconomicCalendar
from agentic_trader.agent.evaluator import LLMTradeEvaluation, RiskEvaluator
from agentic_trader.agent.regime import RegimeDetector
from agentic_trader.backtest import BacktestEngine, run_monte_carlo_simulation
from agentic_trader.broker import BaseBroker, OrderRequest, ReconciliationEvent, create_broker
from agentic_trader.config import AppConfig
from agentic_trader.constants import (
    AssetClass,
    Direction,
    ExitReason,
    SignalStatus,
    StrategyType,
)
from agentic_trader.data.market_data import MarketDataFetcher
from agentic_trader.execution import SlicedExecutionEngine
from agentic_trader.notifier.telegram_bot import TelegramNotifier, format_terminal_card
from agentic_trader.options import OptionsDataFetcher, format_gex_telegram
from agentic_trader.research import AutoRetuner
from agentic_trader.screeners.strategies import StrategyEngine
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.telemetry import MetricsServer, global_metrics


logger = logging.getLogger("copilot")


class FuturesCopilot:
    """Core autonomous trading copilot orchestrating universe scanning, risk evaluation,

    broker order execution, real-time trade monitoring, and metrics exposition.
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self.db = SignalDatabase(config.db_path)
        self.data_fetcher = MarketDataFetcher()
        self.broker: BaseBroker = create_broker(config=config, data_fetcher=self.data_fetcher)
        self.strategy_engine = StrategyEngine(config)
        self.calendar: BaseEconomicCalendar = EconomicCalendar(finnhub_api_key=config.finnhub_api_key)
        self.regime_detector = RegimeDetector(config=config.regime)
        self.evaluator = RiskEvaluator(
            config,
            calendar=self.calendar,
            regime_detector=self.regime_detector,
            data_fetcher=self.data_fetcher,
        )
        self.execution_engine = SlicedExecutionEngine(config=self.config)
        self.options_fetcher = OptionsDataFetcher(
            risk_free_rate=config.options.risk_free_rate,
            cache_ttl_seconds=config.options.cache_ttl_seconds,
        )
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
            perf_provider=self.get_performance_summary_html,
            regime_provider=self.get_regime_summary_html,
            backtest_runner=self.run_backtest_summary_html,
            gex_provider=self.run_gex_summary_html,
        )
        self.metrics = global_metrics
        self.metrics_server = (
            MetricsServer(
                host=config.telemetry.metrics_host,
                port=config.telemetry.metrics_port,
                collector=self.metrics,
            )
            if config.telemetry.metrics_enabled
            else None
        )
        self._shutdown_event = asyncio.Event()

    async def run_scan(
        self,
        use_llm: bool = True,
        dry_run: bool = False,
        asset_class: str = "all",
        symbols: list[str] | None = None,
    ):
        logger.info("=== Starting Quantitative Scan ===")
        regime = await self.regime_detector.get_regime()
        logger.info("Current market volatility context: %s", regime.summary_text)
        current_exposure = await self.db.get_active_notional_exposure()
        active_count = await self.db.get_active_position_count()
        active_positions = await self.db.get_active_positions()
        max_positions = getattr(
            self.config.portfolio,
            "max_concurrent_positions",
            self.config.portfolio.max_concurrent_contracts,
        )
        logger.info(
            f"Portfolio Status: {active_count}/{max_positions} active positions | "
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

        target_syms = [s.strip().upper() for s in symbols] if symbols else None

        for contract, info in self.config.contracts.items():
            clean_contract = contract.strip("/").upper()
            if target_syms and (contract.upper() not in target_syms and clean_contract not in target_syms):
                continue

            inst_class = getattr(info, "asset_class", AssetClass.FUTURES)
            if asset_class and asset_class.lower() != "all" and str(inst_class).lower() != asset_class.lower():
                continue

            logger.info(f"Scanning contract {contract} ({info.name} - {info.ticker}) [{inst_class}]...")
            try:
                data = self.data_fetcher.fetch_data(contract, info.ticker)
                if data.daily.empty or data.four_hour.empty:
                    logger.warning(f"Insufficient data for {contract}, skipping.")
                    continue

                candidates = self.strategy_engine.scan_contract(data, asset_class=inst_class)
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
                        active_positions=active_positions,
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
                        print(
                            format_terminal_card(
                                eval_res,
                                candidate.strategy,
                                self.config.portfolio.cash,
                                regime_summary=regime.summary_text,
                            )
                        )
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
                        asset_class=str(eval_res.asset_class),
                        quantity=eval_res.quantity,
                    )

                    # Dispatch alert
                    await self.notifier.send_signal_alert(
                        eval_res=eval_res,
                        strategy=candidate.strategy,
                        signal_id=sig_id,
                        regime_summary=regime.summary_text,
                    )
                    total_alerts += 1
                    # Update exposure in memory for subsequent checks in this run
                    current_exposure += eval_res.notional_value
                    active_positions.append(
                        {
                            "contract": eval_res.contract,
                            "symbol": eval_res.contract,
                            "direction": eval_res.direction,
                            "asset_class": str(eval_res.asset_class),
                            "notional_value": eval_res.notional_value,
                        }
                    )

            except Exception:
                logger.exception(f"Error scanning {contract}")

        logger.info(f"=== Scan Complete: {total_candidates} candidates evaluated, {total_alerts} alerts emitted ===")
        # Monitor any active positions for stop loss or take profit crossings
        await self.monitor_positions()

    async def process_reconciliation_event(
        self, ev: ReconciliationEvent, active_positions: list[dict[str, Any]] | None = None
    ) -> bool:
        """Process a position exit reconciliation event, closing the position in DB and emitting alerts.

        Returns True if the position was successfully closed, False if already closed or not found.
        """
        if active_positions is None:
            active_positions = await self.db.get_active_positions()

        pos_dict = next((p for p in active_positions if p["id"] == ev.signal_id), None)
        if not pos_dict:
            # Position already closed or does not match
            return False

        contract = ev.contract or ev.symbol
        status = SignalStatus.CLOSED_WIN if ev.exit_reason == ExitReason.TAKE_PROFIT else SignalStatus.CLOSED_LOSS
        if ev.exit_reason == ExitReason.MANUAL_CLOSE:
            status = SignalStatus.CLOSED_WIN if (ev.realized_pnl or 0.0) >= 0 else SignalStatus.CLOSED_LOSS

        strategy = pos_dict.get("strategy", "UNKNOWN")
        entry_price = float(pos_dict.get("entry_price", ev.exit_price))

        logger.info(
            "Position %s %s exited via %s @ %.2f (Realized PnL: $%.2f)",
            contract,
            ev.direction,
            ev.exit_reason,
            ev.exit_price,
            ev.realized_pnl or 0.0,
            extra={
                "signal_id": ev.signal_id,
                "contract": contract,
                "direction": ev.direction,
                "exit_reason": str(ev.exit_reason),
                "exit_price": ev.exit_price,
                "realized_pnl": ev.realized_pnl,
                "broker_order_id": ev.broker_order_id,
            },
        )

        self.metrics.inc_counter(
            "trader_orders_filled_total",
            labels={
                "contract": contract,
                "direction": str(ev.direction),
                "exit_reason": str(ev.exit_reason),
            },
            help_text="Total filled orders count",
        )

        await self.db.close_position(
            signal_id=ev.signal_id,
            exit_price=ev.exit_price,
            exit_reason=str(ev.exit_reason),
            realized_pnl=ev.realized_pnl or 0.0,
            status=status,
        )

        await self.notifier.send_exit_alert(
            contract=contract,
            direction=ev.direction,
            exit_reason=str(ev.exit_reason),
            entry_price=entry_price,
            exit_price=ev.exit_price,
            realized_pnl=ev.realized_pnl or 0.0,
            strategy=strategy,
        )
        return True

    async def on_stream_trade_update(self, ev: ReconciliationEvent) -> None:
        """Callback triggered by broker real-time WebSocket trade stream on order fills."""
        active_positions = await self.db.get_active_positions()
        if not active_positions:
            return

        # 1. Match by broker_order_id if present
        matched_pos: dict[str, Any] | None = None
        if ev.broker_order_id:
            for pos in active_positions:
                if str(pos.get("broker_order_id") or "") == ev.broker_order_id:
                    matched_pos = pos
                    break

        # 2. Match by symbol/contract and opposite direction
        if not matched_pos:
            clean_ev_sym = (ev.contract or ev.symbol).strip("/").upper()
            for pos in active_positions:
                clean_pos_sym = (pos.get("contract") or pos.get("symbol", "")).strip("/").upper()
                if clean_pos_sym == clean_ev_sym:
                    matched_pos = pos
                    break

        if not matched_pos:
            logger.debug(
                "WebSocket trade update for %s (order %s) did not match any active position",
                ev.symbol,
                ev.broker_order_id,
            )
            return

        ev.signal_id = matched_pos["id"]
        ev.contract = matched_pos.get("contract") or ev.contract or ev.symbol
        direction = str(matched_pos["direction"]).upper()
        ev.direction = direction

        entry_price = float(matched_pos["entry_price"])
        qty = float(matched_pos.get("quantity") or 1.0)
        contract_info = self.config.contracts.get(ev.contract)
        multiplier = contract_info.multiplier if contract_info else 1.0

        if direction in ("LONG", str(Direction.LONG)):
            ev.realized_pnl = (ev.exit_price - entry_price) * multiplier * qty
            if ev.exit_reason not in (ExitReason.STOP_LOSS, ExitReason.TAKE_PROFIT):
                ev.exit_reason = (
                    ExitReason.TAKE_PROFIT
                    if ev.exit_price >= float(matched_pos["take_profit"])
                    else ExitReason.STOP_LOSS
                )
        else:
            ev.realized_pnl = (entry_price - ev.exit_price) * multiplier * qty
            if ev.exit_reason not in (ExitReason.STOP_LOSS, ExitReason.TAKE_PROFIT):
                ev.exit_reason = (
                    ExitReason.TAKE_PROFIT
                    if ev.exit_price <= float(matched_pos["take_profit"])
                    else ExitReason.STOP_LOSS
                )

        await self.process_reconciliation_event(ev, active_positions=[matched_pos])

    async def start_trade_stream(self) -> None:
        """Continuously run broker real-time trade stream with exponential backoff auto-reconnect."""
        backoff = 2.0
        max_backoff = 60.0
        while not self._shutdown_event.is_set():
            try:
                logger.info("Starting broker real-time trade stream listener...")
                await self.broker.start_trade_stream(self.on_stream_trade_update)
                backoff = 2.0
            except asyncio.CancelledError:
                logger.info("Broker trade stream task cancelled.")
                break
            except Exception as e:
                logger.warning(
                    "Broker trade stream error: %s. Reconnecting in %.1fs...",
                    e,
                    backoff,
                )
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    break
                backoff = min(backoff * 2, max_backoff)

    async def monitor_positions(self) -> int:
        """Periodic position reconciliation loop.

        Detects server-side bracket order fills or simulated price threshold hits,
        records exits in database, and emits Telegram alerts.
        """
        active_positions = await self.db.get_active_positions()
        # Update telemetry metrics
        self.metrics.set_gauge(
            "trader_account_cash_dollars",
            self.config.portfolio.cash,
            help_text="Liquid cash reserve in dollars",
        )
        self.metrics.set_gauge(
            "trader_active_positions_count",
            float(len(active_positions)),
            help_text="Number of active open positions",
        )

        if not active_positions:
            logger.debug("No active positions to monitor.")
            return 0

        logger.info("Monitoring/reconciling %d active position(s)...", len(active_positions))
        closed_count = 0

        reconciliation_events = await self.broker.reconcile_positions(active_positions)
        for ev in reconciliation_events:
            if await self.process_reconciliation_event(ev, active_positions):
                closed_count += 1

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
                qty = float(pos.get("quantity") or 1.0)
                contract_info = self.config.contracts.get(contract)
                ticker = (
                    contract_info.ticker
                    if contract_info
                    else (f"{contract.strip('/').upper()}=F" if contract.startswith("/") else contract)
                )
                multiplier = contract_info.multiplier if contract_info else (5.0 if contract.startswith("/") else 1.0)

                current = self.data_fetcher.fetch_latest_price(ticker) or entry
                pnl = (
                    (current - entry) * multiplier * qty
                    if direction == Direction.LONG
                    else (entry - current) * multiplier * qty
                )
                total_unrealized += pnl
                pnl_str = f"+${pnl:,.2f}" if pnl >= 0 else f"-${abs(pnl):,.2f}"
                qty_label = f"{qty:g}x" if contract.startswith("/") else f"{qty:g} shs"
                print(
                    f"  #{pos['id']} {qty_label} {contract} {direction} | Entry: {entry:,.2f} | Current: {current:,.2f} | "
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
            qty = float(pos.get("quantity") or 1.0)
            contract_info = self.config.contracts.get(contract)
            ticker = (
                contract_info.ticker
                if contract_info
                else (f"{contract.strip('/').upper()}=F" if contract.startswith("/") else contract)
            )
            multiplier = contract_info.multiplier if contract_info else (5.0 if contract.startswith("/") else 1.0)

            current_price = self.data_fetcher.fetch_latest_price(ticker) or entry
            pnl = (
                (current_price - entry) * multiplier * qty
                if direction == Direction.LONG
                else (entry - current_price) * multiplier * qty
            )
            total_unrealized_pnl += pnl

            pnl_sign = "+" if pnl >= 0 else "-"
            pnl_str = f"{pnl_sign}${abs(pnl):,.2f}"
            qty_label = f"{qty:g}x" if contract.startswith("/") else f"{qty:g} shs"

            lines.append(
                f"• <b>#{pos['id']} {qty_label} {contract} ({direction})</b>\n"
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
        qty = float(pos.get("quantity") or 1.0)
        contract_info = self.config.contracts.get(contract)
        multiplier = contract_info.multiplier if contract_info else (5.0 if contract.startswith("/") else 1.0)
        ticker = (
            contract_info.ticker
            if contract_info
            else (f"{contract.strip('/').upper()}=F" if contract.startswith("/") else contract)
        )

        if exit_price is not None:
            final_exit = exit_price
        else:
            live = self.data_fetcher.fetch_latest_price(ticker)
            final_exit = live if live is not None else entry

        if direction == Direction.LONG:
            realized_pnl = round((final_exit - entry) * multiplier * qty, 2)
        else:
            realized_pnl = round((entry - final_exit) * multiplier * qty, 2)

        logger.info(
            "Manual close requested for signal #%d (%s %s %g qty) at %.2f (PnL: $%.2f)",
            signal_id,
            contract,
            direction,
            qty,
            final_exit,
            realized_pnl,
            extra={
                "signal_id": signal_id,
                "contract": contract,
                "direction": direction,
                "quantity": qty,
                "entry_price": entry,
                "exit_price": final_exit,
                "realized_pnl": realized_pnl,
            },
        )

        # Close position at broker
        try:
            await self.broker.close_position(
                contract=contract,
                symbol=contract,
                exit_reason=ExitReason.MANUAL_CLOSE,
                exit_price=final_exit,
                quantity=qty,
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
        """Execute an approved signal via the configured broker (Paper, Tradovate, or Alpaca).

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
        asset_class = sig.get("asset_class") or (
            contract_info.asset_class
            if contract_info
            else (AssetClass.FUTURES if contract.startswith("/") else AssetClass.EQUITY)
        )
        ticker = (
            contract_info.ticker
            if contract_info
            else (f"{contract.strip('/').upper()}=F" if contract.startswith("/") else contract)
        )
        quantity = float(sig.get("quantity") or 1.0)

        # Re-verify portfolio risk limits prior to live execution
        active_count = await self.db.get_active_position_count()
        max_positions = getattr(
            self.config.portfolio,
            "max_concurrent_positions",
            self.config.portfolio.max_concurrent_contracts,
        )
        if active_count >= max_positions:
            return False, (f"⚠️ <b>Execution Rejected:</b> Maximum concurrent positions ({max_positions}) reached.")

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
            symbol=contract,
            ticker=ticker,
            asset_class=asset_class,
            direction=direction,
            entry_price=float(sig["entry_price"]),
            stop_loss=float(sig["stop_loss"]),
            take_profit=float(sig["take_profit"]),
            quantity=quantity,
        )

        try:
            order_result = await self.execution_engine.execute_order(req, self.broker)
        except Exception as e:
            logger.exception(
                "Error calling execution_engine.execute_order for signal #%d",
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
        regime = await self.regime_detector.get_regime()
        print("Macro Calendar Context:")
        print(macro_summary)
        print("-" * 65)
        print("Market Volatility & Macro Regime Context:")
        print(f"  • Volatility Regime: {regime.vix_regime.value} (VIX: {regime.vix:.2f})")
        print(f"  • 10Y Yield (^TNX):  {f'{regime.tnx:.2f}%' if regime.tnx is not None else 'N/A'}")
        print(f"  • Dollar Index (DXY):{f'{regime.dxy:.2f}' if regime.dxy is not None else 'N/A'}")
        print(f"  • Breakouts Status:  {'Allowed' if regime.breakout_allowed else 'Suppressed (Extreme Volatility)'}")
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
        regime = await self.regime_detector.get_regime()
        signals = await self.db.get_recent_signals(limit=5)

        signals_text = ""
        if not signals:
            signals_text = "\n<i>(No recorded signals)</i>"
        else:
            for s in signals:
                signals_text += (
                    f"\n• #{s['id']} [{s['status']}] {s['contract']} {s['direction']} "
                    f"@ {s['entry_price']:,.2f} (Risk: ${s['risk_dollars']:.2f})"
                )

        tnx_str = f"{regime.tnx:.2f}%" if regime.tnx is not None else "N/A"
        dxy_str = f"{regime.dxy:.2f}" if regime.dxy is not None else "N/A"
        breakout_str = "Allowed" if regime.breakout_allowed else "Suppressed (Extreme Volatility)"

        return (
            "📊 <b>CASH-PLUS COPILOT: STATUS</b>\n\n"
            f"• <b>Cash Base:</b> ${self.config.portfolio.cash:,.2f}\n"
            f"• <b>Active Exposure:</b> ${current_exposure:,.2f} ({eff_leverage:.2f}x leverage)\n"
            f"• <b>Notional Cap:</b> ${self.config.portfolio.max_notional_exposure:,.2f} (0.6x max)\n"
            f"• <b>Active Positions:</b> {active_count} contracts\n"
            f"• <b>LLM Model:</b> <code>{self.config.llm_model}</code>\n"
            f"• <b>Volatility Regime:</b> {regime.vix_regime.value} (VIX: {regime.vix:.2f})\n"
            f"• <b>Macro Indicators:</b> 10Y: {tnx_str} | DXY: {dxy_str} | Breakouts: {breakout_str}\n\n"
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
        return (
            "✅ <b>Scan Complete:</b> Evaluated all universe contracts. "
            "No setups met quantitative triggers at current candle."
        )

    async def get_performance_summary_html(self) -> str:
        stats = await self.db.get_closed_positions_stats()
        active_exposure = await self.db.get_active_notional_exposure()
        active_count = await self.db.get_active_position_count()

        pnl_sign = "+" if stats["total_pnl"] >= 0 else "-"
        abs_pnl = abs(stats["total_pnl"])
        color_pnl = "🟢" if stats["total_pnl"] >= 0 else "🔴"

        recent_trades_text = ""
        if not stats["trades"]:
            recent_trades_text = "\n<i>(No closed trades recorded yet)</i>"
        else:
            for t in stats["trades"][:5]:
                t_pnl = t.get("realized_pnl") or 0.0
                t_sign = "+" if t_pnl >= 0 else "-"
                recent_trades_text += (
                    f"\n• #{t['id']} <b>{t['contract']}</b> ({t['direction']}) "
                    f"via {t['strategy']}: {t_sign}${abs(t_pnl):,.2f} [{t['exit_reason'] or 'CLOSED'}]"
                )

        return (
            "📊 <b>CASH-PLUS COPILOT: PERFORMANCE ATTRIBUTION</b>\n\n"
            f"• <b>Realized Net Alpha:</b> {color_pnl} <code>{pnl_sign}${abs_pnl:,.2f}</code>\n"
            f"• <b>Win Rate:</b> <b>{stats['win_rate']:.1f}%</b> ({stats['wins']} wins / {stats['losses']} losses)\n"
            f"• <b>Profit Factor:</b> <code>{stats['profit_factor']:.2f}</code>\n"
            f"• <b>Gross Profits:</b> +${stats['gross_profit']:,.2f}\n"
            f"• <b>Gross Losses:</b> -${stats['gross_loss']:,.2f}\n"
            f"• <b>Active Open Risk:</b> {active_count} positions (${active_exposure:,.2f} notional)\n\n"
            f"🕒 <b>Recent Closed Trades:</b>{recent_trades_text}"
        )

    async def get_regime_summary_html(self) -> str:
        regime = await self.regime_detector.get_regime()
        tnx_str = f"{regime.tnx:.2f}%" if regime.tnx is not None else "N/A"
        dxy_str = f"{regime.dxy:.2f}" if regime.dxy is not None else "N/A"
        breakout_str = "Allowed ✅" if regime.breakout_allowed else "Suppressed ⚠️ (Extreme Volatility)"

        vix_color = "🟢" if regime.vix < 15.0 else ("🟡" if regime.vix < 22.0 else "🔴")

        return (
            "🌐 <b>MARKET VOLATILITY & MACRO REGIME</b>\n\n"
            f"• <b>VIX Level:</b> {vix_color} <code>{regime.vix:.2f}</code> ({regime.vix_regime.value.upper()})\n"
            f"• <b>10-Year Treasury Yield (^TNX):</b> <code>{tnx_str}</code>\n"
            f"• <b>US Dollar Index (DX-Y):</b> <code>{dxy_str}</code>\n"
            f"• <b>Squeeze Breakouts:</b> <b>{breakout_str}</b>\n\n"
            f"📝 <b>Quantitative Assessment:</b>\n"
            f"<i>{regime.summary_text}</i>"
        )

    async def run_backtest_summary_html(self, symbol: str = "SPY", lookback: str = "1y") -> str:
        engine = BacktestEngine(config=self.config)
        res = await asyncio.to_thread(
            engine.run,
            symbols=[symbol],
            strategy_filter="all",
            lookback=lookback,
        )
        mc_line = ""
        if len(res.trades) >= 3:
            mc = run_monte_carlo_simulation(res.trades, starting_cash=res.starting_cash, n_simulations=500)
            if mc:
                mc_line = f"\n• <b>95% Worst DD (Monte Carlo):</b> <code>{mc.ci_95th_drawdown_pct:.1f}%</code> (95% VaR: {mc.var_95_pct:.1f}%)"

        attr_line = ""
        if res.attribution and res.attribution.factors:
            top_f = max(res.attribution.factors, key=lambda f: f.pnl_dollars)
            if top_f.pnl_dollars != 0:
                attr_line = f"\n• <b>Top Driver:</b> {top_f.factor_name} (+${top_f.pnl_dollars:,.2f})"

        return (
            f"📈 <b>BACKTEST SIMULATION: {symbol.upper()} ({lookback})</b>\n\n"
            f"• <b>Total Net Return:</b> <code>{res.combined_return_pct:+.2f}%</code>\n"
            f"• <b>Annualized Return (CAGR):</b> <code>{res.annualized_return_pct:+.2f}%</code>\n"
            f"• <b>Sharpe Ratio:</b> <code>{res.sharpe_ratio:.2f}</code>\n"
            f"• <b>Max Drawdown:</b> <code>{res.max_drawdown_pct:.2f}%</code>\n"
            f"• <b>Win Rate:</b> <code>{res.win_rate:.1f}%</code> ({res.total_trades} trades)\n"
            f"• <b>Profit Factor:</b> <code>{res.profit_factor:.2f}</code>\n"
            f"• <b>Cash-Plus Yield Accrued:</b> +${res.cash_yield_pnl:,.2f}"
            f"{attr_line}"
            f"{mc_line}"
        )

    async def run_gex_summary_html(self, symbol: str = "SPY") -> str:
        try:
            profile = await asyncio.to_thread(
                self.options_fetcher.fetch_and_calculate_gex,
                symbol,
                self.config.options.max_expirations,
            )
            return format_gex_telegram(profile)
        except Exception as e:
            return f"❌ Failed to calculate GEX for {symbol}: {e}"

    async def run_auto_retune(
        self,
        symbols: list[str] | None = None,
        strategies: list[str] | None = None,
    ) -> dict[str, Any]:
        """Execute automated parameter recalibration and broadcast Telegram summary."""
        retuner = AutoRetuner(self.config)
        logger.info("Executing scheduled parameter retuning...")
        res = await asyncio.to_thread(
            retuner.run_retune,
            symbols=symbols,
            strategies=strategies,
        )
        if self.notifier.is_configured():
            await self.notifier.send_message(res["summary_html"])
        return res

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
