from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import math
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from tempfile import TemporaryDirectory
from typing import Any

from agentic_trader.accounting.service import AccountLedgerService
from agentic_trader.agent.calendar import BaseEconomicCalendar, ForexFactoryCalendar
from agentic_trader.agent.copilot_graph import ask_copilot, create_copilot_graph
from agentic_trader.agent.evaluator import RiskEvaluator
from agentic_trader.agent.macro_explainer import MacroExplainer
from agentic_trader.agent.regime import RegimeDetector
from agentic_trader.backtest import BacktestEngine, run_monte_carlo_simulation
from agentic_trader.broker import BaseBroker, OrderRequest, ReconciliationEvent, create_broker
from agentic_trader.config import AppConfig
from agentic_trader.constants import (
    BROKER_PRICE_TOLERANCE,
    BROKER_QUANTITY_TOLERANCE,
    DEFAULT_RESEARCH_SYMBOL,
    STREAM_RECONNECT_MULTIPLIER,
    AssetClass,
    AuditEventType,
    Direction,
    ExitReason,
    SignalStatus,
    StopAdjustmentReason,
    StrategyType,
    SystemStateKey,
    normalize_asset_class,
)
from agentic_trader.data.market_data import MarketDataFetcher
from agentic_trader.diagnostics.readiness import HealthComponent, ReadinessService
from agentic_trader.execution.closing import PositionCloseService
from agentic_trader.execution.durable import OrderObservation, WorkKind, WorkStatus
from agentic_trader.execution.engine import SlicedExecutionEngine
from agentic_trader.execution.entries import EntryExecutionService
from agentic_trader.execution.lifetimes import TradeLifetimeService
from agentic_trader.market.session import CompositeMarketSessionProvider
from agentic_trader.notifier.outbox import NotificationDispatcher
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
from agentic_trader.research.alpha.evidence import load_forward_evidence
from agentic_trader.research.alpha.shadow import AlphaShadowService
from agentic_trader.research.alpha.strategy import execution_policy_from_dict, trailing_price
from agentic_trader.runtime import RUN_ID
from agentic_trader.screeners.strategies import StrategyEngine
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.ledger import LedgerStore
from agentic_trader.storage.lifetimes import LifetimeRepository
from agentic_trader.telemetry import MetricsServer, global_metrics
from agentic_trader.transport.alpaca import BoundedTradingClient


logger = logging.getLogger("copilot")


class TradingCopilot:
    """Core autonomous trading copilot orchestrating universe scanning, risk evaluation,

    broker order execution, real-time trade monitoring, and metrics exposition.
    """

    def __init__(
        self,
        config: AppConfig,
        db: SignalDatabase | None = None,
        *,
        broker: BaseBroker | None = None,
        data_fetcher: MarketDataFetcher | None = None,
        notifier: TelegramNotifier | None = None,
        close_service: PositionCloseService | None = None,
        entry_service: EntryExecutionService | None = None,
        lifetime_service: TradeLifetimeService | None = None,
        outbox: NotificationDispatcher | None = None,
        ledger: AccountLedgerService | None = None,
        alpha_repository: AlphaRepository | None = None,
    ):
        self._dry_run_directory: TemporaryDirectory[str] | None = None
        self._reconciliation_lock = asyncio.Lock()
        self._reconciliation_errors: list[str] = []
        self.config = config
        self.db = db if db is not None else SignalDatabase(db_url=config.resolved_db_url, config=config)
        self.data_fetcher = data_fetcher if data_fetcher is not None else MarketDataFetcher(config=config)
        self.broker: BaseBroker = (
            broker if broker is not None else create_broker(config=config, data_fetcher=self.data_fetcher)
        )
        self.close_service = close_service if close_service is not None else PositionCloseService(self.broker, self.db)
        self.lifetime_service = (
            lifetime_service
            if lifetime_service is not None
            else TradeLifetimeService(
                LifetimeRepository(self.db.workflows), self.broker, self.close_service, config=config.execution
            )
        )
        self.alpha_repository = (
            alpha_repository
            if alpha_repository is not None
            else AlphaRepository(self.db.workflows, policy=self.config.alpha_pipeline)
        )
        self.alpha_shadow = AlphaShadowService(self.alpha_repository)
        self.strategy_engine = StrategyEngine(config)
        self.calendar: BaseEconomicCalendar = ForexFactoryCalendar()
        self.regime_detector = RegimeDetector(config=config.regime)
        alpaca_client = getattr(self.broker, "client", None)
        if (
            alpaca_client is None
            and config.alpaca_api_key
            and config.alpaca_api_secret
            and not config.alpaca_api_key.startswith("your_")
        ):
            try:
                alpaca_client = BoundedTradingClient(
                    request_timeout=config.market_data.timeout_seconds,
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

        self.notifier = (
            notifier
            if notifier is not None
            else TelegramNotifier(
                settings=config.telegram,
                environment=config.environment,
                bot_token=config.telegram_bot_token,
                chat_id=config.telegram_chat_id,
                db=self.db,
                portfolio_cash=config.portfolio.cash,
                execution_mode=config.execution_mode,
                status_provider=self.get_status_text_html,
                scan_runner=self.run_scan_summary_html,
                positions_provider=self.get_positions_summary_html,
                close_handler=self.close_position_manual,
                flatten_handler=self.flatten_positions,
                execute_handler=self.execute_signal_by_id,
                perf_provider=self.get_performance_summary_html,
                macro_provider=self.get_macro_summary_html,
                explain_macro_provider=self.get_explain_macro_html,
                alphas_provider=self.get_alphas_summary_html,
                backtest_runner=self.run_backtest_summary_html,
                backtest_lookback=self.config.backtest.lookback,
                gex_provider=self.run_gex_summary_html,
                pairs_provider=self.run_pairs_summary_html,
                panic_handler=self.emergency_panic_halt,
                resume_handler=self.resume_trading,
                chat_handler=self.ask_copilot,
            )
        )
        self.entry_service = (
            entry_service
            if entry_service is not None
            else EntryExecutionService(
                config, self.db.workflows, self.broker, self.execution_engine, self._entry_macro_check
            )
        )
        self.outbox = (
            outbox if outbox is not None else NotificationDispatcher(self.db.workflows, self.notifier, config.execution)
        )
        self.ledger = (
            ledger
            if ledger is not None
            else (
                AccountLedgerService(self.broker, LedgerStore(self.db.workflows), config.accounting)
                if self.broker.supports_activity_ledger
                else None
            )
        )
        self.metrics = global_metrics
        self.readiness = ReadinessService(
            self.db.workflows,
            config,
            self.metrics,
            accounting_enabled=self.ledger is not None,
            alpha_registry_report=lambda: self.alpha_repository.status(run_id=RUN_ID),
            stream_connected=(lambda: self.broker.trade_stream_connected)
            if self.broker.supports_trade_stream
            else None,
        )
        self.metrics_server = (
            MetricsServer(
                host=config.telemetry.metrics_host,
                port=config.telemetry.metrics_port,
                collector=self.metrics,
                readiness=self.readiness.report,
            )
            if config.telemetry.metrics_enabled
            else None
        )
        self.is_halted: bool = False
        self.halt_reason: str | None = None
        self._shutdown_event = asyncio.Event()
        self._scan_lock = asyncio.Lock()

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
        state_val = await self.db.get_state(SystemStateKey.TRADING_HALTED)
        if state_val and state_val.lower() in ("true", "1", "yes"):
            self.is_halted = True
            self.halt_reason = (
                await self.db.get_state(SystemStateKey.TRADING_HALT_REASON) or "Emergency Kill Switch Engaged"
            )
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
        timeframe: str | None = None,
    ):
        async with self._scan_lock:
            alpha_snapshot = await self.alpha_repository.snapshot()
            self.strategy_engine.registry.install_alphas(alpha_snapshot.active)
            if not dry_run:
                await self.alpha_repository.acknowledge(alpha_snapshot, run_id=RUN_ID)
            await self.check_halt_state()
            if self.is_halted:
                logger.warning(
                    "Trading scan halted: Emergency kill switch active (%s). Skipping universe scan.",
                    self.halt_reason,
                    extra={"event": "trading_halted_scan_blocked", "reason": self.halt_reason},
                )
                return

            scan_errors = 0
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
                await self.readiness.observe(HealthComponent.SCAN, True, f"Session gate checked: {session_reason}")
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
                inst_class_norm = normalize_asset_class(str(inst_class))
                req_class_norm = normalize_asset_class(asset_class)
                if asset_class and asset_class.lower() != "all" and inst_class_norm != req_class_norm:
                    continue

                logger.info(f"Scanning contract {contract} ({info.name} - {info.ticker}) [{inst_class}]...")
                try:
                    data = await asyncio.to_thread(self.data_fetcher.fetch_data, contract, info.ticker)
                    if not dry_run:
                        await self.alpha_shadow.observe(alpha_snapshot, data)
                    if data.daily.empty or data.four_hour.empty:
                        logger.warning(f"Insufficient data for {contract}, skipping.")
                        continue

                    candidates = await asyncio.to_thread(
                        self.strategy_engine.scan_contract,
                        data,
                        asset_class=inst_class,
                        override_strategy=strategy,
                        override_mode=strategy_mode,
                        timeframe=timeframe,
                    )
                    if timeframe:
                        tf_norm = timeframe.strip().lower()
                        candidates = [c for c in candidates if getattr(c, "timeframe", "").lower() == tf_norm]

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
                        dedup_hours = self.config.risk.deduplication_hours
                        candidate_tf = getattr(candidate, "timeframe", "4h").lower()
                        if candidate_tf in ("15m", "15min", "fifteen_minute"):
                            dedup_hours = min(dedup_hours, 2)
                        elif candidate_tf in ("1h", "hourly"):
                            dedup_hours = min(dedup_hours, 4)

                        is_dup = await self.db.is_duplicate_recent(
                            candidate.contract,
                            candidate.strategy,
                            hours=dedup_hours,
                            timeframe=candidate.timeframe,
                            alpha_version=candidate.alpha_version,
                        )
                        if is_dup:
                            logger.info(
                                "Skipping duplicate signal: %s %s already alerted within %d hours.",
                                candidate.contract,
                                candidate.strategy,
                                dedup_hours,
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
                            timeframe=candidate.timeframe,
                            alpha_version=candidate.alpha_version,
                            alpha_policy=candidate.alpha_policy,
                            decision_provenance={
                                "candle_timestamp": candidate.candle_timestamp,
                                "alpha_score": candidate.alpha_score,
                                "contributors": candidate.contributors,
                            },
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
                            notification={
                                "eval_res": eval_res.model_dump(mode="json"),
                                "strategy": candidate.strategy,
                                "regime_summary": regime.summary_text,
                            },
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
                    scan_errors += 1
                    logger.exception(f"Error scanning {contract}")

            logger.info(
                f"=== Scan Complete: {total_candidates} candidates evaluated, {total_alerts} alerts emitted ==="
            )
            if not dry_run:
                await self.readiness.observe(HealthComponent.SCAN, scan_errors == 0, f"{scan_errors} instrument errors")
            # Monitor any active positions for stop loss or take profit crossings
            if not dry_run:
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
            if "sell" in side_lower and pos_dir not in (Direction.LONG, str(Direction.LONG)):
                logger.warning(
                    "Rejecting exit reconciliation: SELL order %s cannot close %s position #%d",
                    ev.broker_order_id,
                    pos_dir,
                    ev.signal_id,
                )
                return False
            if "buy" in side_lower and pos_dir not in (Direction.SHORT, str(Direction.SHORT)):
                logger.warning(
                    "Rejecting exit reconciliation: BUY order %s cannot close %s position #%d",
                    ev.broker_order_id,
                    pos_dir,
                    ev.signal_id,
                )
                return False

        contract = ev.contract or ev.symbol
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

        notification = {
            "contract": contract,
            "direction": ev.direction,
            "exit_reason": str(ev.exit_reason),
            "entry_price": entry_price,
            "exit_price": ev.exit_price,
            "realized_pnl": ev.realized_pnl or 0.0,
            "strategy": strategy,
            "quantity": float(pos_dict.get("quantity") or 1.0),
            "asset_class": pos_dict.get("asset_class")
            or (AssetClass.FUTURES if contract.startswith("/") else AssetClass.EQUITY),
        }
        closed = await self.db.close_position(
            notification=notification,
            signal_id=ev.signal_id,
            exit_price=ev.exit_price,
            exit_reason=str(ev.exit_reason),
            realized_pnl=ev.realized_pnl or 0.0,
            status=status,
            broker_exit_order_id=ev.broker_order_id,
            exit_timestamp=ev.exit_timestamp,
        )
        if not closed:
            return False
        self.metrics.inc_counter(
            "trader_orders_filled_total",
            labels={"contract": contract, "direction": str(ev.direction), "exit_reason": str(ev.exit_reason)},
            help_text="Total filled orders count",
        )

        return True

    async def on_stream_trade_update(self, ev: ReconciliationEvent) -> None:
        """Record stream evidence and refresh exact entry/exit orders, including partial fills."""
        if ev.observation is not None:
            await self.db.workflows.observe_orders([OrderObservation.model_validate(ev.observation)])
        await self.db.record_audit(AuditEventType.BROKER_STREAM_UPDATE, ev.model_dump(mode="json"))
        await self.monitor_positions()

    async def sync_entry_executions(self, positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self.broker.authoritative_positions:
            return positions
        for pos in positions:
            try:
                result = await self.broker.get_entry_execution(pos)
                if result is None or result.fill_price is None or result.filled_quantity is None:
                    continue
                if (
                    math.isclose(float(pos["entry_price"]), result.fill_price, abs_tol=BROKER_PRICE_TOLERANCE)
                    and math.isclose(float(pos["quantity"]), result.filled_quantity, abs_tol=BROKER_PRICE_TOLERANCE)
                    and pos.get("executed_at")
                ):
                    continue
                instrument = self.config.contracts.get(pos["contract"])
                multiplier = instrument.multiplier if instrument else 1.0
                await self.db.update_signal_execution(
                    pos["id"],
                    result.order_id,
                    fill_price=result.fill_price,
                    quantity=result.filled_quantity,
                    executed_at=result.fill_timestamp,
                    notional_value=result.fill_price * result.filled_quantity * multiplier,
                    risk_dollars=abs(result.fill_price - float(pos["stop_loss"])) * result.filled_quantity * multiplier,
                )
                self.metrics.inc_counter("trader_entry_fill_sync_total", help_text="Confirmed entry fill corrections")
            except Exception as exc:
                logger.warning(
                    "Entry reconciliation failed signal=%s order=%s: %s", pos["id"], pos.get("broker_order_id"), exc
                )
                await self.db.record_audit(
                    AuditEventType.ENTRY_SYNC_FAILED,
                    {"broker_order_id": pos.get("broker_order_id"), "error_type": type(exc).__name__},
                    signal_id=pos["id"],
                )
        return await self.db.get_active_positions()

    async def start_trade_stream(self) -> None:
        """Continuously run broker real-time trade stream with exponential backoff auto-reconnect."""
        if not getattr(self.broker, "supports_trade_stream", False):
            logger.debug("Broker does not support real-time trade streaming; stream listener idle.")
            await self._shutdown_event.wait()
            return

        backoff = self.config.broker_stream.reconnect_initial_seconds
        max_backoff = self.config.broker_stream.reconnect_max_seconds
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
                backoff = self.config.broker_stream.reconnect_initial_seconds
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
                backoff = min(backoff * STREAM_RECONNECT_MULTIPLIER, max_backoff)

    async def monitor_positions(self) -> int:
        async with self._reconciliation_lock:
            self._reconciliation_errors = []
            try:
                result = await self._monitor_positions()
            except Exception as exc:
                await self.readiness.observe(HealthComponent.RECONCILIATION, False, type(exc).__name__)
                raise
            evidence = getattr(self.broker, "reconciliation_evidence", [])
            errors = self._reconciliation_errors + [row["error_type"] for row in evidence if "error_type" in row]
            await self.readiness.observe(HealthComponent.RECONCILIATION, not errors, ", ".join(errors))
            return result

    async def _monitor_positions(self) -> int:
        """Periodic position reconciliation loop.

        Detects server-side bracket order fills or simulated price threshold hits,
        records exits in database, and emits Telegram alerts.
        """
        await self.entry_service.recover()
        try:
            await self.lifetime_service.reconcile()
        except Exception as exc:
            self._reconciliation_errors.append(f"trade_lifetimes:{type(exc).__name__}")
            logger.exception("Trade lifetime reconciliation failed; continuing exact position reconciliation")
        if self.broker.supports_order_journal:
            try:
                tracked = await self.db.get_active_positions()
                views = await self.db.workflows.order_views()
                order_ids = [
                    str(p[key]) for p in tracked for key in ("broker_order_id", "broker_exit_order_id") if p.get(key)
                ]
                order_ids.extend(
                    o.order_id
                    for o in views
                    if o.status not in {"filled", "canceled", "expired", "rejected", "replaced"}
                )
                observations = await self.broker.observe_orders(order_ids)
                await self.db.workflows.observe_orders(observations)
                await self.broker.get_positions()
            except Exception as exc:
                self._reconciliation_errors.append(f"order_journal:{type(exc).__name__}")
                logger.exception("Order journal refresh failed; continuing exact position reconciliation")
                await self.db.record_audit(
                    AuditEventType.RECONCILIATION, {"phase": "order_journal_failed", "error_type": type(exc).__name__}
                )
        if self.broker.authoritative_positions:
            await self.close_service.recover()
        active_positions = await self.sync_entry_executions(await self.db.get_active_positions())
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
        await self.db.record_audit(
            AuditEventType.RECONCILIATION,
            {
                "active_signal_ids": [p["id"] for p in active_positions],
                "broker_evidence": getattr(self.broker, "reconciliation_evidence", []),
                "exits": [e.model_dump(mode="json") for e in reconciliation_events],
            },
        )
        for ev in reconciliation_events:
            if await self.process_reconciliation_event(ev, active_positions):
                closed_count += 1

        # Check remaining open positions for breakeven and trailing stop updates
        remaining_positions = await self.db.get_active_positions()
        if remaining_positions:
            await self.manage_trailing_stops(remaining_positions)

        return closed_count

    async def manage_trailing_stops(self, active_positions: list[dict[str, Any]]) -> int:
        """Ratchet from initial risk, saving only acknowledged broker stop prices."""
        policy = self.config.trailing_stop
        if not policy.enabled:
            return 0
        closing_symbols = {request["symbol"] for request in await self.db.active_close_requests()}
        updated = 0
        for pos in active_positions:
            contract = pos["contract"]
            if contract.strip("/").upper() in closing_symbols:
                continue
            if self.broker.authoritative_positions and not pos.get("executed_at"):
                continue
            try:
                info = self.config.contracts.get(contract)
                if not info:
                    logger.warning("No instrument policy for trailing stop #%s (%s)", pos["id"], contract)
                    continue
                current = await asyncio.to_thread(self.data_fetcher.fetch_latest_price, info.ticker)
                if current is None or not math.isfinite(current) or current <= 0:
                    continue
                entry, old_stop = float(pos["entry_price"]), float(pos["stop_loss"])
                sign = 1 if pos["direction"] == Direction.LONG else -1
                quantity = float(pos["quantity"])
                risk = float(pos.get("risk_dollars") or 0) / (info.multiplier * quantity)
                if risk <= 0:
                    continue
                multiple = sign * (current - entry) / risk
                target, reason = old_stop, StopAdjustmentReason.TRAILING_STOP
                if policy.breakeven_trigger_r is not None and multiple >= policy.breakeven_trigger_r:
                    breakeven = entry + sign * policy.breakeven_buffer_dollars / info.multiplier
                    if sign * (breakeven - target) > 0:
                        target, reason = breakeven, StopAdjustmentReason.BREAKEVEN
                if multiple >= policy.trail_trigger_r:
                    trail = current - sign * max(risk, risk * policy.trail_atr_multiple)
                    if sign * (trail - target) > info.tick_size * policy.trail_step_ticks:
                        target, reason = trail, StopAdjustmentReason.TRAILING_STOP
                if pos.get("alpha_policy"):
                    target = trailing_price(
                        entry, current, old_stop, risk, sign, execution_policy_from_dict(pos["alpha_policy"])
                    )
                    reason = StopAdjustmentReason.TRAILING_STOP
                if target == old_stop:
                    continue
                # One broker update per cycle; never advertise an unconfirmed local stop.
                await self.db.record_audit(
                    AuditEventType.STOP_REPLACEMENT,
                    {
                        "phase": "requested",
                        "entry_order_id": pos.get("broker_order_id"),
                        "old_stop": old_stop,
                        "requested_stop": target,
                        "reason": reason,
                    },
                    signal_id=pos["id"],
                )
                result = await self.broker.modify_order_stop(
                    order_id=pos.get("broker_order_id"),
                    symbol=contract,
                    new_stop_price=target,
                )
                confirmed = result.stop_price if result.stop_price is not None else target
                await self.db.record_audit(
                    AuditEventType.STOP_REPLACEMENT,
                    {
                        "phase": "result",
                        "success": result.success,
                        "order_id": result.order_id,
                        "requested_stop": target,
                        "confirmed_stop": confirmed if result.success else None,
                        "error": result.error_message,
                    },
                    signal_id=pos["id"],
                )
                if not result.success or (self.broker.authoritative_positions and result.stop_price is None):
                    continue
                notification = {
                    "signal_id": pos["id"],
                    "contract": contract,
                    "direction": pos["direction"],
                    "old_stop": old_stop,
                    "new_stop": confirmed,
                    "current_price": current,
                    "reason": reason,
                    "broker_synced": True,
                }
                if not await self.db.update_position_stop(
                    pos["id"], confirmed, reason=reason, notification=notification
                ):
                    continue
                updated += 1
            except Exception:
                logger.exception("Trailing stop update failed for #%s", pos.get("id"))
        return updated

    async def get_positions_report(self) -> PositionsReport:
        """Value the account using a single broker snapshot, preserving source and differences."""
        tracked = await self.db.get_active_positions()
        views: list[PositionView] = []
        notes: list[str] = []
        differences: list[dict[str, Any]] = []
        source = "Estimated from latest market trades"
        if self.broker.authoritative_positions:
            source = f"{type(self.broker).__name__} account valuation"
            try:
                broker_positions = await self.broker.get_positions()
            except Exception as exc:
                await self.db.record_audit(
                    AuditEventType.VALUATION_FAILED, {"source": source, "error_type": type(exc).__name__}
                )
                raise RuntimeError("Broker positions unavailable; retry /positions shortly.") from exc
            for bp in broker_positions:
                matches = [
                    p for p in tracked if p["contract"].strip("/") == bp.symbol and p["direction"] == str(bp.direction)
                ]
                pos = matches[0] if len(matches) == 1 else None
                if not pos:
                    notes.append(
                        f"{bp.symbol}: broker position has {len(matches)} matching tracked signals; shown once at account level."
                    )
                elif not math.isclose(float(pos["quantity"]), bp.quantity, abs_tol=BROKER_QUANTITY_TOLERANCE):
                    notes.append(f"{bp.symbol}: tracked quantity {pos['quantity']:g}; broker quantity {bp.quantity:g}.")
                differences.append(
                    {
                        "symbol": bp.symbol,
                        "signal_ids": [p["id"] for p in matches],
                        "tracked_entries": [p["entry_price"] for p in matches],
                        "broker": bp.model_dump(mode="json"),
                    }
                )
                views.append(
                    PositionView(
                        id=pos["id"] if pos else 0,
                        contract=bp.symbol,
                        direction=str(bp.direction),
                        quantity=bp.quantity,
                        entry_price=bp.entry_price,
                        current_price=bp.current_price,
                        unrealized_pnl=bp.unrealized_pnl,
                        stop_loss=float(pos["stop_loss"]) if pos else None,
                        take_profit=float(pos["take_profit"]) if pos else None,
                        strategy=pos["strategy"] if pos else "Broker account",
                        executed_at=pos.get("executed_at") if pos else None,
                    )
                )
            broker_symbols = {bp.symbol for bp in broker_positions}
            notes.extend(
                f"Signal #{pos['id']} {pos['contract']}: tracked order has no open broker position (awaiting fill or reconciliation)."
                for pos in tracked
                if pos["contract"].strip("/") not in broker_symbols
            )
        else:
            for pos in tracked:
                contract = pos["contract"]
                instrument = self.config.contracts.get(contract)
                multiplier = instrument.multiplier if instrument else 1.0
                entry, qty = float(pos["entry_price"]), float(pos.get("quantity") or 1.0)
                price = await asyncio.to_thread(
                    self.data_fetcher.fetch_latest_price, instrument.ticker if instrument else contract
                )
                pnl = (
                    None
                    if price is None
                    else (price - entry) * multiplier * qty * (1 if pos["direction"] == Direction.LONG else -1)
                )
                views.append(
                    PositionView(
                        id=pos["id"],
                        contract=contract,
                        direction=pos["direction"],
                        quantity=qty,
                        entry_price=entry,
                        current_price=price,
                        stop_loss=float(pos["stop_loss"]),
                        take_profit=float(pos["take_profit"]),
                        unrealized_pnl=pnl,
                        multiplier=multiplier,
                        strategy=pos.get("strategy", ""),
                        executed_at=pos.get("executed_at"),
                    )
                )
        as_of = datetime.now(UTC).isoformat(timespec="seconds")
        total = sum(v.unrealized_pnl for v in views if v.unrealized_pnl is not None)
        total_pnl = round(total, 2) if all(v.unrealized_pnl is not None for v in views) else None
        stats = await self.db.get_closed_positions_stats()
        await self.db.record_audit(
            AuditEventType.POSITIONS_VALUATION,
            {
                "source": source,
                "as_of": as_of,
                "positions": differences,
                "notes": notes,
                "total_unrealized_pnl": total_pnl,
            },
        )
        return PositionsReport(
            positions=views,
            total_unrealized_pnl=total_pnl,
            total_realized_pnl=float(stats.get("total_pnl", 0)),
            active_count=len(views),
            source=source,
            as_of=as_of,
            notes=" ".join(notes),
        )

    async def show_positions(self) -> None:
        """Display active positions in terminal."""
        report = await self.get_positions_report()
        print(TerminalFormatter.format_positions_table(report))

    async def get_positions_summary_html(self) -> str:
        """Format HTML message of tracked positions for Telegram /positions."""
        report = await self.get_positions_report()
        return TelegramHtmlFormatter.format_positions_html(report)

    async def flatten_positions(self, confirm: bool = False) -> str:
        if not confirm:
            return html.escape(await self.close_service.preview())
        async with self._reconciliation_lock:
            response = await self.close_service.flatten()
            positions = await self.sync_entry_executions(await self.db.get_active_positions())
            for event in await self.broker.reconcile_positions(positions):
                await self.process_reconciliation_event(event, positions)
            return html.escape(response)

    async def close_position_manual(
        self, signal_id: int, exit_price: float | None = None, *, allow_queued: bool = False
    ) -> str:
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

        if self.broker.authoritative_positions:
            async with self._reconciliation_lock:
                response = await self.close_service.close_signal(signal_id, allow_queued=allow_queued)
                positions = await self.sync_entry_executions(await self.db.get_active_positions())
                for event in await self.broker.reconcile_positions(positions):
                    await self.process_reconciliation_event(event, positions)
                updated = await self.db.get_signal_by_id(signal_id)
                if updated and updated["status"] in (SignalStatus.CLOSED_WIN, SignalStatus.CLOSED_LOSS):
                    return TelegramHtmlFormatter.format_manual_close_html(
                        ManualCloseResultView(
                            signal_id=signal_id,
                            contract=updated["contract"],
                            direction=updated["direction"],
                            exit_price=updated["exit_price"],
                            realized_pnl=updated["realized_pnl"],
                            success=True,
                        )
                    )
                return html.escape(response)

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
            live = await asyncio.to_thread(self.data_fetcher.fetch_latest_price, ticker)
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
        Submits bracket orders to the active broker and registers the position in the database.
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
        if quantity is not None and (not math.isfinite(quantity) or quantity <= 0):
            return False, "Execution Rejected: quantity must be finite and positive."
        target_qty = float(quantity) if quantity is not None else float(sig.get("quantity") or 1.0)
        entry_price = float(sig["entry_price"])
        stop_loss = float(sig["stop_loss"])
        take_profit = float(sig["take_profit"])
        stop_dist = abs(entry_price - stop_loss)
        notional_value = round(entry_price * multiplier * target_qty, 2)
        risk_dollars = round(stop_dist * multiplier * target_qty, 2)

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
        item, reason = await self.entry_service.authorize(req)
        if item is None:
            return False, f"⚠️ <b>Execution Rejected:</b> {html.escape(reason)}"
        if item.status in (WorkStatus.QUEUED, WorkStatus.CHECKING):
            return (
                False,
                f"⏳ Entry #{signal_id} queued as <code>{item.id}</code>. Fresh checks precede submission; final status will be notified.",
            )
        if item.status in (WorkStatus.SUBMITTING, WorkStatus.UNKNOWN):
            return False, f"⚠️ Entry #{signal_id} awaiting exact broker lookup ({item.id}). No automatic resubmission."
        result = item.result
        view = ExecutionResultView(
            signal_id=signal_id,
            contract=contract,
            direction=direction,
            quantity=target_qty,
            fill_price=result.get("fill_price"),
            order_id=result.get("order_id"),
            notional_value=notional_value,
            risk_dollars=risk_dollars,
            stop_loss=stop_loss,
            take_profit=take_profit,
            execution_mode=self.config.execution_mode.upper(),
            success=item.status == WorkStatus.ACCEPTED,
            error_message=result.get("error_message"),
        )
        return item.status == WorkStatus.ACCEPTED, TelegramHtmlFormatter.format_execution_html(view)

    async def _entry_macro_check(self, request: OrderRequest, signal: dict[str, Any]) -> str | None:
        in_lockout, event = await self.calendar.is_in_lockout_window(
            pre_minutes=self.config.risk.lockout_pre_event_minutes,
            post_minutes=self.config.risk.lockout_post_event_minutes,
        )
        if in_lockout:
            return f"Macro event lockout active: {event.title if event else 'scheduled release'}."
        regime = await self.regime_detector.get_regime(force_refresh=True)
        if signal["strategy"] == StrategyType.SQUEEZE_BREAKOUT and not regime.breakout_allowed:
            return "Current macro/volatility policy suppresses breakout entries."
        assert request.entry_price is not None and request.stop_loss is not None and request.take_profit is not None
        risk_distance = abs(request.entry_price - request.stop_loss)
        if abs(request.take_profit - request.entry_price) / risk_distance < max(
            self.config.risk.min_risk_reward_ratio, regime.min_rr_threshold
        ):
            return "Current macro/volatility policy requires a higher reward/risk ratio."
        info = self.config.contracts.get(request.symbol)
        risk = risk_distance * request.quantity * (info.multiplier if info else 1)
        if risk > self.config.portfolio.cash * self.config.sizing.max_risk_pct_cap * regime.risk_multiplier:
            return "Current macro risk scaling no longer permits the approved size."
        return None

    async def run_entries(self) -> None:
        await self.entry_service.recover()
        for _ in range(self.config.execution.worker_batch_size):
            if not await self.entry_service.dispatch_one():
                break

    async def workflow_worker(self) -> None:
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(self._run_worker(HealthComponent.WORKER, self.run_entries))
            tasks.create_task(self._run_worker(HealthComponent.DELIVERY, self.outbox.drain))
            if self.ledger:
                tasks.create_task(
                    self._run_worker(
                        HealthComponent.ACCOUNTING,
                        self.refresh_account_ledger,
                        interval=self.config.accounting.refresh_seconds,
                    )
                )

    async def refresh_account_ledger(self) -> None:
        if self.ledger:
            report = await self.ledger.refresh()
            if not report.ready:
                raise ValueError("; ".join(report.issues))

    async def _run_worker(
        self, component: HealthComponent, action: Callable[[], Awaitable[Any]], *, interval: float | None = None
    ) -> None:
        while not self._shutdown_event.is_set():
            try:
                await action()
                await self.readiness.observe(component, True)
            except Exception as exc:
                logger.exception("Durable workflow worker failed")
                with contextlib.suppress(Exception):
                    await self.readiness.observe(component, False, type(exc).__name__)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._shutdown_event.wait(), interval or self.config.execution.worker_interval_seconds
                )

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
        self.is_halted = True
        self.halt_reason = reason
        await self.db.set_state(SystemStateKey.TRADING_HALTED, "true")
        await self.db.set_state(SystemStateKey.TRADING_HALT_REASON, reason)
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
            if self.broker.authoritative_positions:
                await self.close_position_manual(pos["id"], allow_queued=True)
                confirmed = await self.db.get_signal_by_id(pos["id"])
                if confirmed and confirmed["status"] in (SignalStatus.CLOSED_WIN, SignalStatus.CLOSED_LOSS):
                    liquidated_count += 1
                    total_realized_pnl += float(confirmed["realized_pnl"] or 0)
                    closed_positions_details.append(confirmed)
                continue
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
                q = await asyncio.to_thread(self.data_fetcher.fetch_latest_price, ticker)
                if q and q > 0:
                    final_exit = q
            except Exception:
                final_exit = entry

            pnl_pts = (final_exit - entry) if direction in (Direction.LONG, "BUY") else (entry - final_exit)
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
        await self.db.set_state(SystemStateKey.TRADING_HALTED, "true")
        await self.db.set_state(SystemStateKey.TRADING_HALT_REASON, reason)

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
            await self.outbox.publish_message(TelegramHtmlFormatter.format_panic_html(view))
        except Exception as notify_err:
            logger.warning("Failed to send Telegram panic notification: %s", notify_err)

        return view

    async def resume_trading(self) -> dict[str, Any]:
        """Clear emergency halt state and resume normal autonomous operations."""
        await self.entry_service.recover()
        unresolved = await self.db.workflows.list_work(
            WorkKind.ENTRY, statuses=(WorkStatus.SUBMITTING, WorkStatus.UNKNOWN)
        )
        await self.lifetime_service.recover()
        cancellations = await self.db.workflows.list_work(
            WorkKind.ENTRY_CANCEL, statuses=(WorkStatus.SUBMITTING, WorkStatus.UNKNOWN)
        )
        if (
            unresolved
            or cancellations
            or await self.db.workflows.legacy_claims()
            or await self.lifetime_service.resume_blockers()
        ):
            return {
                "success": False,
                "is_halted": True,
                "message": "Unconfirmed broker entry or cancellation remains; inspect execution journal before resuming.",
            }
        logger.info("Resuming trading operations from emergency halt...")
        self.is_halted = False
        self.halt_reason = None
        await self.db.set_state(SystemStateKey.TRADING_HALTED, "false")
        await self.db.set_state(SystemStateKey.TRADING_HALT_REASON, "")
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
            await self.outbox.publish_message(msg)
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
            max_notional=self.config.portfolio.max_notional_exposure,
            current_exposure=current_exposure,
            effective_leverage=eff_leverage,
            active_contract_count=active_count,
            telegram_configured=self.notifier.is_configured(),
            llm_model=self.config.llm_model,
            execution_mode=self.config.execution_mode.upper(),
            macro_summary=macro_summary,
            vix_value=regime.vix,
            vix_regime=regime.vix_regime.value,
            tnx_value=regime.tnx,
            dxy_value=regime.dxy,
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
        source_note = "Simulated closed trades; before fees; all recorded history."
        unrealized_text = ""
        if self.broker.authoritative_positions:
            positions = await self.get_positions_report()
            active_count = positions.active_count
            active_exposure = sum(p.entry_price * p.quantity * p.multiplier for p in positions.positions)
            source_note = "Tracked full closes only; broker-confirmed fills, before fees. This subset is not added to account totals."
            if stats.get("unverified_closed_count"):
                source_note += f" {stats['unverified_closed_count']} unverified closes excluded."
            if positions.notes:
                source_note += " " + positions.notes
            unrealized_text = f"• <b>Broker open-position P&amp;L:</b> {positions.total_pnl_str} ({positions.as_of})"
        ledger_report, ledger_reason = await self.ledger.current() if self.ledger else (None, "")
        await self.db.record_audit(
            AuditEventType.PERFORMANCE_REPORT,
            {
                "source": source_note,
                "closed_signal_ids": [t["id"] for t in stats.get("trades", [])],
                "realized_pnl": stats.get("total_pnl"),
                "unrealized": unrealized_text,
                "account_ledger": ledger_report.model_dump(mode="json") if ledger_report else None,
                "account_ledger_unavailable": ledger_reason,
            },
        )
        report = PerformanceSummaryReport(
            account_ledger=ledger_report,
            account_ledger_unavailable=ledger_reason,
            source_note=source_note,
            unrealized_pnl_text=unrealized_text,
            total_pnl=float(stats.get("total_pnl", 0.0)),
            win_rate=float(stats.get("win_rate", 0.0)),
            wins=int(stats.get("wins", 0)),
            losses=int(stats.get("losses", 0)),
            profit_factor=float(stats.get("profit_factor", 0.0)),
            gross_profit=float(stats.get("gross_profit", 0.0)),
            gross_loss=float(stats.get("gross_loss", 0.0)),
            active_count=active_count,
            active_exposure=active_exposure,
            recent_closed_trades=stats.get("trades", []),
        )
        return TelegramHtmlFormatter.format_performance_html(report)

    async def get_macro_summary_html(self) -> str:
        """One macro dashboard using the same combined snapshot as trade evaluation."""
        regime = await self.regime_detector.get_regime()
        await self.db.record_audit(
            AuditEventType.MACRO_REPORT,
            payload={
                "fetched_at": regime.timestamp.isoformat(),
                "vix": regime.vix,
                "volatility_regime": regime.vix_regime.value,
                "breakout_allowed": regime.breakout_allowed,
                "minimum_rr": max(regime.min_rr_threshold, self.config.risk.min_risk_reward_ratio),
                "risk_multiplier": regime.risk_multiplier,
                "macro_unavailable_reason": regime.macro_unavailable_reason,
                "observation_dates": regime.macro_report.observation_dates if regime.macro_report else {},
            },
        )
        return TelegramHtmlFormatter.format_macro_dashboard_html(regime, self.config.risk.min_risk_reward_ratio)

    async def get_explain_macro_html(self) -> str:
        """Format educational macro tutorial and indicator breakdown for Telegram /explain_macro."""
        report = await self.regime_detector.macro_engine.get_macro_report()
        explainer = MacroExplainer(config=self.config)
        return await explainer.explain(report, format_mode="html")

    async def get_alphas_summary_html(self) -> str:
        """Format the compact registry and forward-evidence summary for Telegram /alphas."""

        snapshot, evidence = await load_forward_evidence(self.alpha_repository)
        return TelegramHtmlFormatter.format_alphas_dashboard_html(snapshot, evidence=evidence)

    async def broadcast_macro_briefing(self) -> None:
        """Broadcast morning macro intelligence card to Telegram."""
        if self.notifier.is_configured():
            try:
                card = await self.get_macro_summary_html()
                await self.outbox.publish_message(card)
                logger.info("Successfully broadcasted morning macro intelligence briefing to Telegram.")
            except Exception as e:
                logger.warning("Failed to broadcast morning macro briefing: %s", e)

    async def run_backtest_summary_html(
        self, symbol: str = DEFAULT_RESEARCH_SYMBOL, lookback: str | None = None
    ) -> str:
        """Run on-demand backtest and format result as Telegram HTML."""
        lookback = lookback or self.config.backtest.lookback
        engine = BacktestEngine(config=self.config)
        res = await asyncio.to_thread(
            engine.run,
            symbols=[symbol],
            strategy_filter="all",
            lookback=lookback,
        )
        if len(res.trades) >= 3:
            res.monte_carlo = await asyncio.to_thread(
                run_monte_carlo_simulation,
                res.trades,
                starting_cash=res.starting_cash,
                n_simulations=self.config.backtest.monte_carlo_simulations,
            )
        return TelegramHtmlFormatter.format_backtest_html(res, symbols=[symbol], lookback=lookback, strategy="all")

    async def run_gex_summary_html(self, symbol: str = DEFAULT_RESEARCH_SYMBOL) -> str:
        profile = await asyncio.to_thread(
            self.options_fetcher.fetch_and_calculate_gex,
            symbol,
            self.config.options.max_expirations,
        )
        return format_gex_telegram(profile)

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
        results = await self.scan_pairs()
        return format_pairs_telegram(results)

    async def send_test_alert(self):
        """Synthetic diagnostics never create actionable signal records."""
        await self.notifier.send_message("[TEST] Synthetic notification check. No signal or order was created.")
