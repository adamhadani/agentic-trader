from __future__ import annotations

import asyncio
import html
import logging
from typing import Any

from alpaca.trading.client import TradingClient

from agentic_trader.agent.calendar import BaseEconomicCalendar, EconomicCalendar
from agentic_trader.agent.copilot_graph import ask_copilot, create_copilot_graph
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
from agentic_trader.market.session import CompositeMarketSessionProvider
from agentic_trader.notifier.telegram_bot import TelegramNotifier, format_terminal_card
from agentic_trader.options import OptionsDataFetcher, format_gex_telegram
from agentic_trader.pairs import PairEvaluation, PairsScreener, format_pairs_telegram
from agentic_trader.presentation.formatters import (
    ExecutionResultView,
    ManualCloseResultView,
    PanicReportView,
    PerformanceSummaryReport,
    PortfolioStatusReport,
    PositionsReport,
    PositionView,
    TelegramHtmlFormatter,
    TerminalFormatter,
)
from agentic_trader.research import AutoRetuner
from agentic_trader.research.alpha import AlphaCatalog, AlphaPromotionManager
from agentic_trader.screeners.strategies import StrategyEngine
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.telemetry import MetricsServer, global_metrics


logger = logging.getLogger("copilot")


class TradingCopilot:
    """Core autonomous trading copilot orchestrating universe scanning, risk evaluation,

    broker order execution, real-time trade monitoring, and metrics exposition.
    """

    def __init__(self, config: AppConfig, db: SignalDatabase | None = None):
        self.config = config
        self.db = db or SignalDatabase(config.db_path)
        self.data_fetcher = MarketDataFetcher()
        self.broker: BaseBroker = create_broker(config=config, data_fetcher=self.data_fetcher)
        self.strategy_engine = StrategyEngine(config)
        self.calendar: BaseEconomicCalendar = EconomicCalendar(finnhub_api_key=config.finnhub_api_key)
        self.regime_detector = RegimeDetector(config=config.regime)
        alpaca_client = getattr(self.broker, "client", None)
        if (
            alpaca_client is None
            and config.alpaca_api_key
            and config.alpaca_api_secret
            and not config.alpaca_api_key.startswith("your_")
        ):
            try:
                alpaca_client = TradingClient(
                    api_key=config.alpaca_api_key,
                    secret_key=config.alpaca_api_secret,
                    paper=config.alpaca_paper,
                )
            except Exception as e:
                logger.debug("Could not initialize read-only Alpaca client for calendar: %s", e)

        self.session_provider = CompositeMarketSessionProvider(
            config=config,
            alpaca_client=alpaca_client,
        )
        self.evaluator = RiskEvaluator(
            config,
            calendar=self.calendar,
            regime_detector=self.regime_detector,
            data_fetcher=self.data_fetcher,
            session_provider=self.session_provider,
        )
        self.execution_engine = SlicedExecutionEngine(config=self.config)
        self.options_fetcher = OptionsDataFetcher(
            risk_free_rate=config.options.risk_free_rate,
            cache_ttl_seconds=config.options.cache_ttl_seconds,
        )
        self.pairs_screener = PairsScreener(
            data_fetcher=self.data_fetcher,
            config=self.config.pairs,
        )
        self.copilot_graph = None
        if getattr(config, "copilot_chat_enabled", True):
            try:
                self.copilot_graph = create_copilot_graph(self)
            except Exception as e:
                logger.warning(f"Could not pre-compile LangGraph conversational copilot: {e}")

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
            macro_provider=self.get_macro_summary_html,
            alphas_provider=self.get_alphas_summary_html,
            backtest_runner=self.run_backtest_summary_html,
            gex_provider=self.run_gex_summary_html,
            pairs_provider=self.run_pairs_summary_html,
            panic_handler=self.emergency_panic_halt,
            resume_handler=self.resume_trading,
            chat_handler=self.ask_copilot,
        )
        self.metrics = global_metrics
        self.metrics_server = (
            MetricsServer(
                host=config.telemetry.metrics_host,
                port=config.telemetry.metrics_port,
                collector=self.metrics,
                config=config,
            )
            if config.telemetry.metrics_enabled
            else None
        )
        self.is_halted: bool = False
        self.halt_reason: str | None = None
        self._shutdown_event = asyncio.Event()

    async def ask_copilot(self, query: str, chat_id: str | int = "default") -> str:
        """Handle a natural language conversational turn through the LangGraph copilot."""
        if not self.copilot_graph:
            try:
                self.copilot_graph = create_copilot_graph(self)
            except Exception as e:
                logger.error(f"Failed to create copilot graph on demand: {e}")
                return f"❌ Conversational copilot unavailable: {e}"
        return await ask_copilot(
            self.copilot_graph,
            query=query,
            chat_id=chat_id,
            execution_mode=self.config.execution_mode,
        )

    async def check_halt_state(self) -> bool:
        """Check persistent database state for emergency trading halt."""
        state_val = await self.db.get_state("trading_halted")
        if state_val and state_val.lower() in ("true", "1", "yes"):
            self.is_halted = True
            self.halt_reason = await self.db.get_state("trading_halt_reason") or "Emergency Kill Switch Engaged"
        else:
            self.is_halted = False
            self.halt_reason = None
        if self.metrics:
            self.metrics.set_gauge(
                "copilot_trading_halted",
                1.0 if self.is_halted else 0.0,
                help_text="1 if trading is halted by emergency kill switch, 0 otherwise",
            )
        return self.is_halted

    async def run_scan(
        self,
        use_llm: bool = True,
        dry_run: bool = False,
        asset_class: str = "all",
        symbols: list[str] | None = None,
        bypass_session_filter: bool = False,
        strategy: str | None = None,
        strategy_mode: str | None = None,
    ):
        await self.check_halt_state()
        if self.is_halted:
            logger.warning(
                "Trading scan halted: Emergency kill switch active (%s). Skipping universe scan.",
                self.halt_reason,
                extra={"event": "trading_halted_scan_blocked", "reason": self.halt_reason},
            )
            return

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
        session_allowed, session_reason = await self.session_provider.is_session_active(
            instrument_type=asset_class or "all"
        )
        if not session_allowed and not bypass_session_filter:
            logger.info(
                "Market session filter inactive (%s): %s. Skipping universe scan.",
                asset_class,
                session_reason,
                extra={"event": "session_blocked", "asset_class": asset_class, "reason": session_reason},
            )
            return

        in_lockout, lock_event = await self.calendar.is_in_lockout_window(
            pre_minutes=self.config.risk.lockout_pre_event_minutes,
            post_minutes=self.config.risk.lockout_post_event_minutes,
        )
        if in_lockout and lock_event:
            logger.warning(
                f"Macro Lockout Active: '{lock_event.title}' at {lock_event.timestamp.strftime('%H:%M UTC')}. "
                "No entry alerts will be emitted during this window.",
                extra={
                    "event": "macro_lockout_active",
                    "lock_event": lock_event.title,
                    "event_time": str(lock_event.timestamp),
                },
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

                candidates = self.strategy_engine.scan_contract(
                    data,
                    asset_class=inst_class,
                    override_strategy=strategy,
                    override_mode=strategy_mode,
                )
                for candidate in candidates:
                    total_candidates += 1
                    logger.info(
                        "Found setup: %s %s via %s at %.2f",
                        candidate.contract,
                        candidate.direction,
                        candidate.strategy,
                        candidate.current_price,
                        extra={
                            "event": "candidate_found",
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
                            extra={
                                "event": "duplicate_signal_skipped",
                                "contract": candidate.contract,
                                "strategy": candidate.strategy,
                            },
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
                            extra={
                                "event": "candidate_rejected",
                                "contract": candidate.contract,
                                "rejection_reason": eval_res.rejection_reason,
                            },
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

                    logger.info(
                        "Signal #%d approved and recorded: %s %s via %s (risk: $%.2f, notional: $%.2f)",
                        sig_id,
                        eval_res.contract,
                        eval_res.direction,
                        candidate.strategy,
                        eval_res.risk_dollars,
                        eval_res.notional_value,
                        extra={
                            "event": "signal_approved",
                            "signal_id": sig_id,
                            "contract": eval_res.contract,
                            "direction": eval_res.direction,
                            "strategy": candidate.strategy,
                            "risk_dollars": eval_res.risk_dollars,
                            "notional_value": eval_res.notional_value,
                            "quantity": eval_res.quantity,
                            "entry_price": eval_res.entry_price,
                        },
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

        # Invariant 1: Do not reconcile entry order fills as exit events
        entry_order_id = str(pos_dict.get("broker_order_id") or "")
        if ev.broker_order_id and entry_order_id and ev.broker_order_id == entry_order_id:
            logger.info(
                "Ignoring exit reconciliation for order %s on position #%d: matches entry order ID (entry confirmation, not exit)",
                ev.broker_order_id,
                ev.signal_id,
            )
            return False

        # Invariant 2: Directional Opposing Side Rule
        # An exit order MUST strictly oppose the position direction (Sell closes LONG, Buy closes SHORT)
        pos_dir = str(pos_dict.get("direction", "")).upper()
        if ev.order_side:
            side_lower = ev.order_side.lower()
            if "sell" in side_lower and pos_dir not in ("LONG", str(Direction.LONG)):
                logger.warning(
                    "Rejecting exit reconciliation: SELL order %s cannot close %s position #%d",
                    ev.broker_order_id,
                    pos_dir,
                    ev.signal_id,
                )
                return False
            if "buy" in side_lower and pos_dir not in ("SHORT", str(Direction.SHORT)):
                logger.warning(
                    "Rejecting exit reconciliation: BUY order %s cannot close %s position #%d",
                    ev.broker_order_id,
                    pos_dir,
                    ev.signal_id,
                )
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

        pos_qty = float(pos_dict.get("quantity") or 1.0)
        pos_asset_class = pos_dict.get("asset_class") or (
            AssetClass.EQUITY if not contract.startswith("/") else AssetClass.FUTURES
        )

        await self.notifier.send_exit_alert(
            contract=contract,
            direction=ev.direction,
            exit_reason=str(ev.exit_reason),
            entry_price=entry_price,
            exit_price=ev.exit_price,
            realized_pnl=ev.realized_pnl or 0.0,
            strategy=strategy,
            quantity=pos_qty,
            asset_class=pos_asset_class,
        )
        return True

    async def on_stream_trade_update(self, ev: ReconciliationEvent) -> None:
        """Callback triggered by broker real-time WebSocket trade stream on order fills."""
        active_positions = await self.db.get_active_positions()
        if not active_positions:
            return

        # Invariant 1: Check if this stream fill is an ENTRY order fill confirmation
        if ev.broker_order_id:
            for pos in active_positions:
                entry_ord_id = str(pos.get("broker_order_id") or "")
                if entry_ord_id and entry_ord_id == ev.broker_order_id:
                    logger.info(
                        "WebSocket stream trade update confirms entry fill for order %s (signal #%d, %s) @ %.2f - position active",
                        ev.broker_order_id,
                        pos["id"],
                        pos.get("contract") or pos.get("symbol"),
                        ev.exit_price,
                    )
                    return

        # Invariant 2: Match active position for an EXIT fill.
        # Requirements:
        #  - Symbol/contract must match
        #  - Order must NOT be the entry order ID
        #  - Order side must strictly oppose position direction (SELL closes LONG, BUY closes SHORT)
        clean_ev_sym = (ev.contract or ev.symbol).strip("/").upper()
        matched_pos: dict[str, Any] | None = None

        for pos in active_positions:
            clean_pos_sym = (pos.get("contract") or pos.get("symbol", "")).strip("/").upper()
            if clean_pos_sym != clean_ev_sym:
                continue

            entry_ord_id = str(pos.get("broker_order_id") or "")
            if ev.broker_order_id and entry_ord_id and ev.broker_order_id == entry_ord_id:
                continue

            pos_dir = str(pos.get("direction", "")).upper()
            if ev.order_side:
                side_lower = ev.order_side.lower()
                # SELL orders can only close LONG positions
                if "sell" in side_lower and pos_dir not in ("LONG", str(Direction.LONG)):
                    continue
                # BUY orders can only close SHORT positions
                if "buy" in side_lower and pos_dir not in ("SHORT", str(Direction.SHORT)):
                    continue
            elif ev.direction:
                # If order_side not provided, fallback to ev.direction
                ev_dir = str(ev.direction).upper()
                if pos_dir not in (ev_dir, f"DIRECTION.{ev_dir}"):
                    continue

            matched_pos = pos
            break

        if not matched_pos:
            logger.debug(
                "WebSocket trade update for %s (order %s, side %s) did not match any eligible active position to exit",
                ev.symbol,
                ev.broker_order_id,
                ev.order_side,
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
        if not getattr(self.broker, "supports_trade_stream", False):
            logger.debug("Broker does not support real-time trade streaming; stream listener idle.")
            await self._shutdown_event.wait()
            return

        backoff = 2.0
        max_backoff = 60.0
        while not self._shutdown_event.is_set():
            try:
                logger.info("Starting broker real-time trade stream listener...")
                await self.broker.start_trade_stream(self.on_stream_trade_update)
                if not self._shutdown_event.is_set():
                    logger.info("Broker trade stream disconnected cleanly. Reconnecting in %.1fs...", backoff)
                    try:
                        await asyncio.wait_for(self._shutdown_event.wait(), timeout=backoff)
                        break
                    except TimeoutError:
                        pass
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
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=backoff)
                    break
                except TimeoutError:
                    pass
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

        # Check remaining open positions for breakeven and trailing stop updates
        remaining_positions = await self.db.get_active_positions()
        if remaining_positions:
            await self.manage_trailing_stops(remaining_positions)

        return closed_count

    async def _sync_broker_stop(
        self,
        signal_id: int,
        contract: str,
        new_stop: float,
        broker_order_id: str | None,
    ) -> bool:
        """Attempt to amend resting stop order on the broker exchange with graceful degradation."""
        if not getattr(self.broker, "supports_order_modification", False):
            return False

        try:
            mod_res = await self.broker.modify_order_stop(
                order_id=broker_order_id,
                symbol=contract,
                new_stop_price=new_stop,
            )
            if mod_res.success:
                logger.info(
                    "Broker resting stop modified for #%d (%s) -> %.2f (order: %s)",
                    signal_id,
                    contract,
                    new_stop,
                    mod_res.order_id,
                    extra={
                        "event": "broker_stop_synced",
                        "signal_id": signal_id,
                        "contract": contract,
                        "new_stop": new_stop,
                        "broker_order_id": mod_res.order_id,
                    },
                )
                return True
            else:
                logger.warning(
                    "Broker stop modification degraded for #%d (%s): %s",
                    signal_id,
                    contract,
                    mod_res.error_message,
                    extra={
                        "event": "broker_stop_sync_degraded",
                        "signal_id": signal_id,
                        "contract": contract,
                        "error": mod_res.error_message,
                    },
                )
                return False
        except Exception as broker_err:
            logger.warning(
                "Exception during broker stop sync for #%d (%s): %s",
                signal_id,
                contract,
                broker_err,
                extra={
                    "event": "broker_stop_sync_exception",
                    "signal_id": signal_id,
                    "error": str(broker_err),
                },
            )
            return False

    async def manage_trailing_stops(self, active_positions: list[dict[str, Any]]) -> int:
        """Evaluate active open positions for Breakeven and Dynamic Trailing Stop ratchets.

        Configured via config.trailing_stop:
        - breakeven_trigger_r: moves stop to entry + buffer once price advances by +1.0R
        - trail_trigger_r: moves stop to trail behind price by trail_atr_multiple * ATR
        """
        ts_config = getattr(self.config, "trailing_stop", None)
        if not ts_config or not ts_config.enabled:
            return 0

        updates_count = 0
        for pos in active_positions:
            try:
                sig_id = pos["id"]
                contract = pos["contract"]
                direction = pos["direction"].upper()
                entry = float(pos["entry_price"])
                current_stop = float(pos["stop_loss"])

                # Determine quote ticker
                contract_info = self.config.contracts.get(contract)
                ticker = (
                    contract_info.ticker
                    if contract_info
                    else (f"{contract.strip('/').upper()}=F" if contract.startswith("/") else contract)
                )

                current_price = self.data_fetcher.fetch_latest_price(ticker)
                if current_price is None or current_price <= 0:
                    continue

                mult = contract_info.multiplier if contract_info else (5.0 if contract.startswith("/") else 1.0)
                qty = float(pos.get("quantity") or 1.0)
                denom = mult * qty
                risk_dollars = float(pos.get("risk_dollars") or 0.0)
                initial_risk = risk_dollars / denom if risk_dollars > 0 and denom > 0 else abs(entry - current_stop)

                if initial_risk <= 0:
                    continue

                # Buffer in price units
                tick_size = contract_info.tick_size if contract_info else 0.25
                min_step = tick_size * ts_config.trail_step_ticks
                buffer_pts = (ts_config.breakeven_buffer_dollars / mult) if mult > 0 else 0.5

                if direction == Direction.LONG:
                    favorable_dist = current_price - entry
                    r_multiple = favorable_dist / initial_risk

                    # 1. Breakeven Trigger (opt-in if breakeven_trigger_r is configured)
                    if ts_config.breakeven_trigger_r is not None and r_multiple >= ts_config.breakeven_trigger_r:
                        target_be_stop = entry + buffer_pts
                        if current_stop < target_be_stop:
                            logger.info(
                                "Position #%d (%s LONG) hit Breakeven (+%.2fR): Moving stop from %.2f to %.2f",
                                sig_id,
                                contract,
                                r_multiple,
                                current_stop,
                                target_be_stop,
                                extra={
                                    "event": "breakeven_ratchet",
                                    "contract": contract,
                                    "direction": direction,
                                    "signal_id": sig_id,
                                    "r_multiple": round(r_multiple, 2),
                                    "old_stop": current_stop,
                                    "new_stop": target_be_stop,
                                    "current_price": current_price,
                                },
                            )
                            await self.db.update_position_stop(sig_id, target_be_stop, raw_response="BREAKEVEN")
                            synced = await self._sync_broker_stop(
                                sig_id, contract, target_be_stop, pos.get("broker_order_id")
                            )
                            await self.notifier.send_trailing_stop_alert(
                                signal_id=sig_id,
                                contract=contract,
                                direction=direction,
                                old_stop=current_stop,
                                new_stop=target_be_stop,
                                current_price=current_price,
                                reason="BREAKEVEN",
                                broker_synced=synced,
                            )
                            current_stop = target_be_stop
                            updates_count += 1

                    # 2. Dynamic Trailing Stop Trigger (Chandelier ATR / ATR distance)
                    if r_multiple >= ts_config.trail_trigger_r:
                        trail_dist = max(initial_risk, initial_risk * ts_config.trail_atr_multiple)
                        proposed_trail_stop = current_price - trail_dist
                        if proposed_trail_stop > current_stop + min_step:
                            logger.info(
                                "Position #%d (%s LONG) hit Trailing Stop trigger (+%.2fR): Ratcheting stop from %.2f to %.2f",
                                sig_id,
                                contract,
                                r_multiple,
                                current_stop,
                                proposed_trail_stop,
                                extra={
                                    "event": "trailing_stop_ratchet",
                                    "contract": contract,
                                    "direction": direction,
                                    "signal_id": sig_id,
                                    "r_multiple": round(r_multiple, 2),
                                    "old_stop": current_stop,
                                    "new_stop": proposed_trail_stop,
                                    "current_price": current_price,
                                },
                            )
                            await self.db.update_position_stop(
                                sig_id, proposed_trail_stop, raw_response="TRAILING_STOP"
                            )
                            synced = await self._sync_broker_stop(
                                sig_id, contract, proposed_trail_stop, pos.get("broker_order_id")
                            )
                            await self.notifier.send_trailing_stop_alert(
                                signal_id=sig_id,
                                contract=contract,
                                direction=direction,
                                old_stop=current_stop,
                                new_stop=proposed_trail_stop,
                                current_price=current_price,
                                reason="TRAILING_STOP",
                                broker_synced=synced,
                            )
                            updates_count += 1

                elif direction in (Direction.SHORT, "SELL"):
                    favorable_dist = entry - current_price
                    r_multiple = favorable_dist / initial_risk

                    # 1. Breakeven Trigger (opt-in if breakeven_trigger_r is configured)
                    if ts_config.breakeven_trigger_r is not None and r_multiple >= ts_config.breakeven_trigger_r:
                        target_be_stop = entry - buffer_pts
                        if current_stop > target_be_stop:
                            logger.info(
                                "Position #%d (%s SHORT) hit Breakeven (+%.2fR): Moving stop from %.2f to %.2f",
                                sig_id,
                                contract,
                                r_multiple,
                                current_stop,
                                target_be_stop,
                                extra={
                                    "event": "breakeven_ratchet",
                                    "contract": contract,
                                    "direction": direction,
                                    "signal_id": sig_id,
                                    "r_multiple": round(r_multiple, 2),
                                    "old_stop": current_stop,
                                    "new_stop": target_be_stop,
                                    "current_price": current_price,
                                },
                            )
                            await self.db.update_position_stop(sig_id, target_be_stop, raw_response="BREAKEVEN")
                            synced = await self._sync_broker_stop(
                                sig_id, contract, target_be_stop, pos.get("broker_order_id")
                            )
                            await self.notifier.send_trailing_stop_alert(
                                signal_id=sig_id,
                                contract=contract,
                                direction=direction,
                                old_stop=current_stop,
                                new_stop=target_be_stop,
                                current_price=current_price,
                                reason="BREAKEVEN",
                                broker_synced=synced,
                            )
                            current_stop = target_be_stop
                            updates_count += 1

                    # 2. Dynamic Trailing Stop Trigger (Chandelier ATR / ATR distance)
                    if r_multiple >= ts_config.trail_trigger_r:
                        trail_dist = max(initial_risk, initial_risk * ts_config.trail_atr_multiple)
                        proposed_trail_stop = current_price + trail_dist
                        if proposed_trail_stop < current_stop - min_step:
                            logger.info(
                                "Position #%d (%s SHORT) hit Trailing Stop trigger (+%.2fR): Ratcheting stop from %.2f to %.2f",
                                sig_id,
                                contract,
                                r_multiple,
                                current_stop,
                                proposed_trail_stop,
                                extra={
                                    "event": "trailing_stop_ratchet",
                                    "contract": contract,
                                    "direction": direction,
                                    "signal_id": sig_id,
                                    "r_multiple": round(r_multiple, 2),
                                    "old_stop": current_stop,
                                    "new_stop": proposed_trail_stop,
                                    "current_price": current_price,
                                },
                            )
                            await self.db.update_position_stop(
                                sig_id, proposed_trail_stop, raw_response="TRAILING_STOP"
                            )
                            synced = await self._sync_broker_stop(
                                sig_id, contract, proposed_trail_stop, pos.get("broker_order_id")
                            )
                            await self.notifier.send_trailing_stop_alert(
                                signal_id=sig_id,
                                contract=contract,
                                direction=direction,
                                old_stop=current_stop,
                                new_stop=proposed_trail_stop,
                                current_price=current_price,
                                reason="TRAILING_STOP",
                                broker_synced=synced,
                            )
                            updates_count += 1
            except Exception as e:
                logger.warning(
                    "Error evaluating trailing stop for position #%s: %s",
                    pos.get("id"),
                    e,
                    extra={"signal_id": pos.get("id"), "error": str(e)},
                )

        return updates_count

    async def get_positions_report(self) -> PositionsReport:
        """Construct a decoupled PositionsReport DTO containing all active tracked positions."""
        positions = await self.db.get_active_positions()
        pos_views: list[PositionView] = []
        total_unrealized_pnl = 0.0

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

            pos_views.append(
                PositionView(
                    id=int(pos["id"]),
                    contract=contract,
                    direction=direction,
                    quantity=qty,
                    entry_price=entry,
                    current_price=current_price,
                    stop_loss=sl,
                    take_profit=tp,
                    unrealized_pnl=pnl,
                    multiplier=multiplier,
                    strategy=pos.get("strategy", ""),
                    executed_at=pos.get("executed_at"),
                )
            )

        perf_stats = await self.db.get_closed_positions_stats()
        realized_pnl = float(perf_stats.get("total_pnl", 0.0))

        return PositionsReport(
            positions=pos_views,
            total_unrealized_pnl=round(total_unrealized_pnl, 2),
            total_realized_pnl=round(realized_pnl, 2),
            active_count=len(pos_views),
        )

    async def show_positions(self) -> None:
        """Display active positions in terminal."""
        report = await self.get_positions_report()
        print(TerminalFormatter.format_positions_table(report))

    async def get_positions_summary_html(self) -> str:
        """Format HTML message of tracked positions for Telegram /positions."""
        report = await self.get_positions_report()
        return TelegramHtmlFormatter.format_positions_html(report)

    async def close_position_manual(self, signal_id: int, exit_price: float | None = None) -> str:
        """Manually close a position (via Telegram /close or CLI)."""
        pos = await self.db.get_signal_by_id(signal_id)
        if not pos:
            return TelegramHtmlFormatter.format_manual_close_html(
                ManualCloseResultView(
                    signal_id=signal_id,
                    contract="",
                    direction="",
                    exit_price=0.0,
                    realized_pnl=0.0,
                    success=False,
                    error_message=f"Signal #{signal_id} not found.",
                )
            )
        if pos["status"] != SignalStatus.EXECUTED:
            return TelegramHtmlFormatter.format_manual_close_html(
                ManualCloseResultView(
                    signal_id=signal_id,
                    contract=pos.get("contract", ""),
                    direction=pos.get("direction", ""),
                    exit_price=0.0,
                    realized_pnl=0.0,
                    success=False,
                    error_message=f"Signal #{signal_id} is in status {pos['status']} (only EXECUTED positions can be closed).",
                )
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
                "event": "manual_close_request",
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

        pos_asset_class = pos.get("asset_class") or (
            AssetClass.EQUITY if not contract.startswith("/") else AssetClass.FUTURES
        )
        await self.notifier.send_exit_alert(
            contract=contract,
            direction=direction,
            exit_reason=ExitReason.MANUAL_CLOSE,
            entry_price=entry,
            exit_price=final_exit,
            realized_pnl=realized_pnl,
            strategy=pos["strategy"],
            quantity=qty,
            asset_class=pos_asset_class,
        )

        view = ManualCloseResultView(
            signal_id=signal_id,
            contract=contract,
            direction=direction,
            exit_price=final_exit,
            realized_pnl=realized_pnl,
            quantity=qty,
            success=True,
        )
        return TelegramHtmlFormatter.format_manual_close_html(view)

    async def execute_signal_by_id(self, signal_id: int, quantity: float | None = None) -> tuple[bool, str]:
        """
        Execute an approved trade setup by signal ID.
        Submits bracket orders to the active broker and registers the position in SQLite.
        Optionally overrides the order quantity with operator-selected tier size.
        """
        await self.check_halt_state()
        if self.is_halted:
            logger.warning(
                "Signal execution rejected: Emergency kill switch active (%s).",
                self.halt_reason,
                extra={"event": "trading_halted_execution_blocked", "signal_id": signal_id, "reason": self.halt_reason},
            )
            return False, (
                f"🛑 <b>Execution Blocked:</b> Emergency trading halt active ({html.escape(str(self.halt_reason))}). "
                "Use /resume to unhalt."
            )

        sig = await self.db.get_signal_by_id(signal_id)
        if not sig:
            return False, f"❌ Signal #{signal_id} not found in database."

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

        multiplier = contract_info.multiplier if contract_info else (1.0 if asset_class == AssetClass.EQUITY else 5.0)
        target_qty = float(quantity) if quantity is not None and quantity > 0 else float(sig.get("quantity") or 1.0)
        entry_price = float(sig["entry_price"])
        stop_loss = float(sig["stop_loss"])
        take_profit = float(sig["take_profit"])
        stop_dist = abs(entry_price - stop_loss)
        notional_value = round(entry_price * multiplier * target_qty, 2)
        risk_dollars = round(stop_dist * multiplier * target_qty, 2)

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
        if current_exposure + notional_value > self.config.portfolio.max_notional_exposure:
            return False, (
                f"⚠️ <b>Execution Rejected:</b> Order notional (${notional_value:,.2f}) would breach "
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
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            quantity=target_qty,
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
            fill_price = order_result.fill_price or entry_price
            await self.db.update_signal_execution(
                signal_id=signal_id,
                broker_order_id=order_result.order_id,
                fill_price=fill_price,
                status=SignalStatus.EXECUTED,
                quantity=target_qty,
                notional_value=notional_value,
                risk_dollars=risk_dollars,
            )
            logger.info(
                "Signal #%d executed successfully via broker (%s) with qty %g",
                signal_id,
                self.config.execution_mode,
                target_qty,
                extra={
                    "signal_id": signal_id,
                    "order_id": order_result.order_id,
                    "fill_price": fill_price,
                    "quantity": target_qty,
                    "execution_mode": self.config.execution_mode,
                },
            )
            view = ExecutionResultView(
                signal_id=signal_id,
                contract=contract,
                direction=direction,
                quantity=target_qty,
                fill_price=fill_price,
                broker_order_id=order_result.order_id,
                notional_value=notional_value,
                risk_dollars=risk_dollars,
                stop_loss=float(sig["stop_loss"]),
                take_profit=float(sig["take_profit"]),
                execution_mode=self.config.execution_mode.upper(),
                success=True,
            )
            return True, TelegramHtmlFormatter.format_execution_html(view)
        else:
            await self.db.update_signal_status(signal_id, SignalStatus.FAILED)
            err = order_result.error_message or "Unknown broker rejection"
            logger.warning(
                "Signal #%d execution rejected by broker (%s): %s",
                signal_id,
                self.config.execution_mode,
                err,
                extra={
                    "event": "signal_execution_rejected",
                    "signal_id": signal_id,
                    "error": err,
                    "execution_mode": self.config.execution_mode,
                },
            )
            view = ExecutionResultView(
                signal_id=signal_id,
                contract=contract,
                direction=direction,
                quantity=target_qty,
                fill_price=0.0,
                broker_order_id=None,
                notional_value=notional_value,
                risk_dollars=risk_dollars,
                stop_loss=float(sig["stop_loss"]),
                take_profit=float(sig["take_profit"]),
                execution_mode=self.config.execution_mode.upper(),
                success=False,
                error_message=err,
            )
            return False, TelegramHtmlFormatter.format_execution_html(view)

    async def emergency_panic_halt(self, reason: str = "Manual emergency panic trigger") -> PanicReportView:
        """Institutional emergency kill switch:

        1. Ingress cancellation: cancel all working/resting orders at broker exchange.
        2. Egress liquidation: flatten all active open positions at market.
        3. Persistent circuit breaker: engage database and in-memory trading halt.
        4. Dispatch high-visibility alert cards and record metrics.
        """
        logger.warning(
            "EMERGENCY PANIC TRIGGERED: Cancelling all orders, liquidating positions, engaging halt. Reason: %s",
            reason,
            extra={"event": "emergency_panic_triggered", "reason": reason},
        )
        # 1. Ingress cancellation
        cancelled_orders_count = 0
        try:
            cancelled_orders_count = await self.broker.cancel_all_orders()
        except Exception:
            logger.exception("Error cancelling orders during emergency panic")

        # 2. Egress liquidation
        active_positions = await self.db.get_active_positions()
        liquidated_count = 0
        total_realized_pnl = 0.0
        closed_positions_details: list[dict[str, Any]] = []

        for pos in active_positions:
            sig_id = pos["id"]
            contract = pos["contract"]
            direction = pos["direction"].upper()
            qty = float(pos.get("quantity") or 1.0)
            entry = float(pos["entry_price"])

            contract_info = self.config.contracts.get(contract)
            ticker = (
                contract_info.ticker
                if contract_info
                else (f"{contract.strip('/').upper()}=F" if contract.startswith("/") else contract)
            )
            multiplier = contract_info.multiplier if contract_info else (1.0 if not contract.startswith("/") else 5.0)

            final_exit = entry
            try:
                q = self.data_fetcher.fetch_latest_price(ticker)
                if q and q > 0:
                    final_exit = q
            except Exception:
                final_exit = entry

            pnl_pts = (final_exit - entry) if direction in ("LONG", "BUY") else (entry - final_exit)
            realized_pnl = round(pnl_pts * multiplier * qty, 2)
            total_realized_pnl += realized_pnl

            try:
                await self.broker.close_position(
                    contract=contract,
                    symbol=contract,
                    exit_reason=ExitReason.EMERGENCY_EXIT,
                    exit_price=final_exit,
                    quantity=qty,
                )
            except Exception as broker_err:
                logger.warning("Broker close_position exception during panic for %s: %s", contract, broker_err)

            status = SignalStatus.CLOSED_WIN if realized_pnl >= 0 else SignalStatus.CLOSED_LOSS
            await self.db.close_position(
                signal_id=sig_id,
                exit_price=final_exit,
                exit_reason=ExitReason.EMERGENCY_EXIT,
                realized_pnl=realized_pnl,
                status=status,
            )
            liquidated_count += 1
            closed_positions_details.append(
                {
                    "id": sig_id,
                    "contract": contract,
                    "direction": direction,
                    "quantity": qty,
                    "entry_price": entry,
                    "exit_price": final_exit,
                    "realized_pnl": realized_pnl,
                }
            )

        # 3. Persistent circuit breaker
        self.is_halted = True
        self.halt_reason = reason
        await self.db.set_state("trading_halted", "true")
        await self.db.set_state("trading_halt_reason", reason)

        # 4. Metrics & telemetry
        if self.metrics:
            self.metrics.inc_counter(
                "copilot_kill_switch_triggered_total",
                help_text="Total number of emergency panic kill switch activations",
            )
            self.metrics.set_gauge(
                "copilot_trading_halted",
                1.0,
                help_text="1 if trading is halted by emergency kill switch, 0 otherwise",
            )

        view = PanicReportView(
            cancelled_orders_count=cancelled_orders_count,
            liquidated_positions_count=liquidated_count,
            total_realized_pnl=round(total_realized_pnl, 2),
            is_halted=True,
            halt_reason=reason,
            closed_positions=closed_positions_details,
            success=True,
        )

        # 5. Telegram notification
        try:
            await self.notifier.send_message(TelegramHtmlFormatter.format_panic_html(view))
        except Exception as notify_err:
            logger.warning("Failed to send Telegram panic notification: %s", notify_err)

        return view

    async def resume_trading(self) -> dict[str, Any]:
        """Clear emergency halt state and resume normal autonomous operations."""
        logger.info("Resuming trading operations from emergency halt...")
        self.is_halted = False
        self.halt_reason = None
        await self.db.set_state("trading_halted", "false")
        await self.db.set_state("trading_halt_reason", "")
        if self.metrics:
            self.metrics.set_gauge(
                "copilot_trading_halted",
                0.0,
                help_text="1 if trading is halted by emergency kill switch, 0 otherwise",
            )
        msg = (
            "🟢 <b>TRADING HALT CLEARED / OPERATIONS RESUMED</b>\n\n"
            "• <b>Status:</b> Active 🟢\n"
            "• <b>Action:</b> Universe scanning and order execution unblocked.\n"
            "• <b>Safety:</b> Standard portfolio risk limits, macro lockouts &amp; session filters remain in effect."
        )
        try:
            await self.notifier.send_message(msg)
        except Exception as notify_err:
            logger.warning("Failed to send Telegram resume notification: %s", notify_err)

        return {
            "success": True,
            "is_halted": False,
            "message": "Trading operations successfully resumed. Market scans and signal executions are unblocked.",
        }

    async def get_status_report(self) -> PortfolioStatusReport:
        """Construct a decoupled PortfolioStatusReport DTO."""
        current_exposure = await self.db.get_active_notional_exposure()
        active_count = await self.db.get_active_contract_count()
        eff_leverage = current_exposure / self.config.portfolio.cash if self.config.portfolio.cash > 0 else 0.0
        macro_summary = await self.calendar.get_macro_summary_for_prompt()
        regime = await self.regime_detector.get_regime()
        signals = await self.db.get_recent_signals(limit=10)

        return PortfolioStatusReport(
            cash_base=self.config.portfolio.cash,
            max_notional_exposure=self.config.portfolio.max_notional_exposure,
            active_exposure=current_exposure,
            effective_leverage=eff_leverage,
            active_position_count=active_count,
            telegram_configured=self.notifier.is_configured(),
            llm_model=self.config.llm_model,
            execution_mode=self.config.execution_mode.upper(),
            macro_calendar_summary=macro_summary,
            vix=regime.vix,
            vix_regime=regime.vix_regime.value,
            tnx=regime.tnx,
            dxy=regime.dxy,
            breakout_allowed=regime.breakout_allowed,
            recent_signals=signals,
        )

    async def show_status(self) -> None:
        """Display portfolio and risk status in terminal."""
        report = await self.get_status_report()
        print(TerminalFormatter.format_status_dashboard(report))

    async def get_status_text_html(self) -> str:
        """Format HTML status message for Telegram /status."""
        report = await self.get_status_report()
        return TelegramHtmlFormatter.format_status_html(report)

    async def run_scan_summary_html(self) -> str:
        await self.check_halt_state()
        if self.is_halted:
            return (
                f"🛑 <b>Scan Blocked:</b> Emergency trading halt active ({html.escape(str(self.halt_reason))}). "
                "Use /resume to clear."
            )
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
        """Format HTML performance attribution for Telegram /performance."""
        stats = await self.db.get_closed_positions_stats()
        active_exposure = await self.db.get_active_notional_exposure()
        active_count = await self.db.get_active_position_count()
        report = PerformanceSummaryReport(
            total_pnl=float(stats.get("total_pnl", 0.0)),
            win_rate=float(stats.get("win_rate", 0.0)),
            wins=int(stats.get("wins", 0)),
            losses=int(stats.get("losses", 0)),
            profit_factor=float(stats.get("profit_factor", 0.0)),
            gross_profit=float(stats.get("gross_profit", 0.0)),
            gross_loss=float(stats.get("gross_loss", 0.0)),
            active_open_positions=active_count,
            active_notional_exposure=active_exposure,
            recent_trades=stats.get("trades", []),
        )
        return TelegramHtmlFormatter.format_performance_html(report)

    async def get_regime_summary_html(self) -> str:
        """Format HTML volatility and macro regime for Telegram /regime."""
        regime = await self.regime_detector.get_regime()
        return TelegramHtmlFormatter.format_regime_html(regime)

    async def get_macro_summary_html(self) -> str:
        """Format HTML multi-asset macro intelligence & yield curve dashboard for Telegram /macro."""
        report = await self.regime_detector.macro_engine.get_macro_report()
        return TelegramHtmlFormatter.format_macro_dashboard_html(report)

    async def get_alphas_summary_html(self) -> str:
        """Format HTML formulaic alpha intelligence dashboard for Telegram /alphas."""
        try:
            mgr = AlphaPromotionManager()
            catalog = AlphaCatalog()
            promoted = mgr.list_active_alphas()
            return TelegramHtmlFormatter.format_alphas_dashboard_html(
                promoted, catalog_count=len(catalog.list_alphas())
            )
        except Exception as e:
            return f"❌ Failed retrieving formulaic alpha status: {e}"

    async def broadcast_macro_briefing(self) -> None:
        """Broadcast morning macro intelligence card to Telegram."""
        if self.notifier.is_configured():
            try:
                card = await self.get_macro_summary_html()
                await self.notifier.send_message(card)
                logger.info("Successfully broadcasted morning macro intelligence briefing to Telegram.")
            except Exception as e:
                logger.warning("Failed to broadcast morning macro briefing: %s", e)

    async def run_backtest_summary_html(self, symbol: str = "SPY", lookback: str = "1y") -> str:
        """Run on-demand backtest and format result as Telegram HTML."""
        engine = BacktestEngine(config=self.config)
        res = await asyncio.to_thread(
            engine.run,
            symbols=[symbol],
            strategy_filter="all",
            lookback=lookback,
        )
        if len(res.trades) >= 3:
            res.monte_carlo = run_monte_carlo_simulation(res.trades, starting_cash=res.starting_cash, n_simulations=500)
        return TelegramHtmlFormatter.format_backtest_html(res, symbols=[symbol], lookback=lookback, strategy="all")

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

    async def scan_pairs(
        self,
        pairs: list[tuple[str, str]] | None = None,
        lookback_days: int | None = None,
    ) -> list[PairEvaluation]:
        """Scan cross-asset pairs for cointegration and statistical arbitrage opportunities."""
        return await asyncio.to_thread(
            self.pairs_screener.scan_pairs,
            pairs=pairs,
            lookback_days=lookback_days,
        )

    async def run_pairs_summary_html(self) -> str:
        """Run statistical pairs screener and format as Telegram HTML."""
        try:
            results = await self.scan_pairs()
            return format_pairs_telegram(results)
        except Exception as e:
            return f"❌ Failed to evaluate pairs: {e}"

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
            quantity=1.0,
            sizing_tiers=[
                {
                    "tier_id": "half",
                    "label": "Half (1x)",
                    "quantity": 1.0,
                    "risk_dollars": 213.75,
                    "reward_dollars": 427.50,
                    "notional_dollars": 29062.50,
                    "effective_leverage": 0.29,
                    "is_default": True,
                },
                {
                    "tier_id": "base",
                    "label": "Base (2x)",
                    "quantity": 2.0,
                    "risk_dollars": 427.50,
                    "reward_dollars": 855.00,
                    "notional_dollars": 58125.00,
                    "effective_leverage": 0.58,
                    "is_default": False,
                },
            ],
            gating_reasons=["Max size capped by remaining $58,125 notional limit"],
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


# Backward-compatibility alias
FuturesCopilot = TradingCopilot

__all__ = ["FuturesCopilot", "TradingCopilot"]
