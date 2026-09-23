from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import replace as dataclass_replace
from datetime import UTC, datetime, time as dt_time
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

from agentic_trader.accounting.service import AccountLedgerService
from agentic_trader.agent.calendar import BaseEconomicCalendar, ForexFactoryCalendar
from agentic_trader.agent.copilot_graph import ask_copilot, create_copilot_graph
from agentic_trader.agent.earnings import (
    EarningsCalendarProtocol,
    EarningsLookup,
    NasdaqEarningsCalendar,
    earnings_blackout_reason,
)
from agentic_trader.agent.evaluator import LLMTradeEvaluation, RiskEvaluator
from agentic_trader.agent.macro_explainer import MacroExplainer
from agentic_trader.agent.regime import RegimeDetector
from agentic_trader.backtest import BacktestEngine, run_monte_carlo_simulation
from agentic_trader.broker import BaseBroker, OrderRequest, ReconciliationEvent, create_broker
from agentic_trader.config import AppConfig, ScanBudget
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
    executes_asset_class,
    normalize_asset_class,
)
from agentic_trader.data.market_data import MarketDataFetcher
from agentic_trader.diagnostics.readiness import HealthComponent, ReadinessService
from agentic_trader.execution.closing import PositionCloseService
from agentic_trader.execution.durable import EventKind, OrderObservation, WorkKind, WorkStatus
from agentic_trader.execution.engine import SlicedExecutionEngine
from agentic_trader.execution.entries import EntryExecutionService
from agentic_trader.execution.freshness import (
    REEVALUATE_SCAN_WAIT_SECONDS,
    TAP_CHECK_TIMEOUT_SECONDS,
    CardAssessment,
    CardOutcome,
    ExecutionReply,
    assess_card,
    reprice_quantity,
    round_to_tick,
)
from agentic_trader.execution.lifetimes import TradeLifetimeService
from agentic_trader.market.session import ET_TZ, CompositeMarketSessionProvider
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
from agentic_trader.research.alpha.probe import PAPER_PROBE_TAG
from agentic_trader.research.alpha.shadow import AlphaShadowService
from agentic_trader.research.alpha.strategy import execution_policy_from_dict, trailing_price
from agentic_trader.research.setups.ranker import (
    cached_ranker,
    finite_or_none,
    live_cross_section,
    setup_features,
    shadow_blocks,
)
from agentic_trader.resilience.reads import DEFAULT_READ_WORKERS, BoundedReadExecutor
from agentic_trader.risk import requires_account_risk
from agentic_trader.runtime import RUN_ID
from agentic_trader.screeners.coverage import coverage_exclusions
from agentic_trader.screeners.strategies import StrategyEngine
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.ledger import LedgerStore
from agentic_trader.storage.lifetimes import LifetimeRepository
from agentic_trader.telemetry import MetricsServer, global_metrics
from agentic_trader.transport.alpaca import BoundedTradingClient


logger = logging.getLogger("copilot")


class ScanBusyError(RuntimeError):
    """A bounded wait for the scan lock expired: another scan is still running."""


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
        if data_fetcher is not None:
            self.data_fetcher = data_fetcher
        else:
            # One dedicated, scan-sized read-capacity pool, owned by the copilot for its
            # lifetime; MarketDataFetcher never constructs its own (that would leak a
            # ThreadPoolExecutor per ad hoc fetcher construction elsewhere, e.g. backtests).
            # Headroom over scan_concurrency: the primary leg can hold every scan slot
            # for its full timeout while the fallback leg and the one-minute position
            # monitor still need slots of their own.
            self._scan_read_executor = BoundedReadExecutor(
                workers=2 * config.market_data.scan_concurrency + DEFAULT_READ_WORKERS
            )
            self.data_fetcher = MarketDataFetcher(config=config, read_executor=self._scan_read_executor)
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
        self.earnings_calendar: EarningsCalendarProtocol = NasdaqEarningsCalendar()
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
            earnings_calendar=self.earnings_calendar,
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
                reevaluate_handler=self.reevaluate_signal,
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
        self.ledger = (
            ledger
            if ledger is not None
            else (
                AccountLedgerService(self.broker, LedgerStore(self.db.workflows), config.accounting)
                if self.broker.supports_activity_ledger
                else None
            )
        )
        self.entry_service = (
            entry_service
            if entry_service is not None
            else EntryExecutionService(
                config,
                self.db.workflows,
                self.broker,
                self.execution_engine,
                self._entry_macro_check,
                ledger=self.ledger,
            )
        )
        self.outbox = (
            outbox if outbox is not None else NotificationDispatcher(self.db.workflows, self.notifier, config.execution)
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
        self.last_scan_summary: dict[str, Any] = {}
        self._session_scan_stats: dict[str, list[dict[str, Any]]] = {}
        self._shutdown_event = asyncio.Event()
        self._scan_lock = asyncio.Lock()
        # Strong references to background re-evaluations, so they are never garbage-collected.
        self.reevaluation_tasks: set[asyncio.Task[None]] = set()

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

    @contextlib.asynccontextmanager
    async def _hold_scan_lock(self, timeout: float | None) -> AsyncIterator[None]:
        """Hold the scan lock; with ``timeout``, give up waiting with ``ScanBusyError``."""
        if timeout is None:
            await self._scan_lock.acquire()
        else:
            try:
                async with asyncio.timeout(timeout):
                    await self._scan_lock.acquire()
            except TimeoutError:
                raise ScanBusyError(f"A scan is still running after {timeout:g}s") from None
        try:
            yield
        finally:
            self._scan_lock.release()

    def session_start_et(self, now: datetime | None = None) -> datetime:
        """New York midnight of the current New York date: the per-session card budget's window."""
        current = (now or datetime.now(UTC)).astimezone(ET_TZ)
        return datetime.combine(current.date(), dt_time(0, 0), ET_TZ)

    def correlation_groups_of(self, symbol: str) -> set[str]:
        """Configured correlation groups this symbol belongs to, matching root and slashed spellings."""
        keys = {symbol.upper(), symbol.strip("/").upper()}
        groups = set()
        for name, members in self.config.portfolio.correlation_groups.items():
            norm = {m.strip("/").upper() for m in members} | {m.upper() for m in members}
            if keys & norm:
                groups.add(name)
        return groups

    @staticmethod
    def _setup_quality(candidate: Any) -> float:
        """A candidate's ranking score; screeners that predate ``setup_quality`` rank last."""
        return float(getattr(candidate, "setup_quality", 0.0) or 0.0)

    @classmethod
    def _runner_up(cls, candidate: Any, reason: str) -> dict[str, Any]:
        return {
            "contract": candidate.contract,
            "strategy": candidate.strategy,
            "direction": candidate.direction,
            "setup_quality": cls._setup_quality(candidate),
            "reason": reason,
        }

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
        include_fifteen_min: bool | None = None,
        budget: ScanBudget = ScanBudget.SESSION,
        shadow_evidence: bool = False,
        dedup_exempt_setups: frozenset[tuple[str, str, str | None, str | None]] = frozenset(),
        scan_lock_timeout: float | None = None,
    ) -> dict[str, Any] | None:
        """Scan, rank and record cards; returns the scan summary, or None when the scan did not run.

        ``shadow_evidence`` is set only by the scheduled suggestion-scan job
        (``make_suggestion_scan``): it is the sole trigger for computing and journaling
        this scan's shadow ranker block. The daemon's swing scan and an operator/Telegram
        scan never set it, even though they may otherwise share this scan's shape (no
        symbols, no timeframe).

        ``dedup_exempt_setups`` skips the recent-duplicate rule for exactly those
        ``(contract, strategy, timeframe, alpha_version)`` setups: an operator's explicit
        re-evaluation of the one expired card, never its contract's other setups.
        ``scan_lock_timeout`` bounds the wait for a running scan; ``ScanBusyError`` is
        raised, before anything else happens, when it is exceeded.
        """
        async with self._hold_scan_lock(scan_lock_timeout):
            self.last_scan_summary = {}
            if dry_run:
                # A dry scan owns an empty simulated portfolio and sends nothing; it must
                # neither read nor spend the live session's card budget.
                budget = ScanBudget.NONE
            if not dry_run:
                # Durable visibility only; snapshot() already excludes non-live probes.
                try:
                    await self.alpha_repository.sweep_probes()
                except Exception:
                    logger.exception(
                        "Paper-probe sweep failed; retrying next scan", extra={"event": "probe_sweep_failed"}
                    )
            alpha_snapshot = await self.alpha_repository.snapshot()
            self.strategy_engine.registry.install_alphas(alpha_snapshot.active, alpha_snapshot.probe)
            if not dry_run:
                await self.alpha_repository.acknowledge(alpha_snapshot, run_id=RUN_ID)
            await self.check_halt_state()
            if self.is_halted:
                logger.warning(
                    "Trading scan halted: Emergency kill switch active (%s). Skipping universe scan.",
                    self.halt_reason,
                    extra={"event": "trading_halted_scan_blocked", "reason": self.halt_reason},
                )
                return None

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
                if not dry_run:
                    await self.readiness.observe(HealthComponent.SCAN, True, f"Session gate checked: {session_reason}")
                logger.info(
                    "Market session filter inactive (%s): %s. Skipping universe scan.",
                    asset_class,
                    session_reason,
                    extra={"event": "session_blocked", "asset_class": asset_class, "reason": session_reason},
                )
                return None

            in_lockout, lock_event = await self.calendar.is_in_lockout_window(
                pre_minutes=self.config.risk.lockout_pre_event_minutes,
                post_minutes=self.config.risk.lockout_post_event_minutes,
            )
            if in_lockout and lock_event:
                if not dry_run:
                    await self.readiness.observe(
                        HealthComponent.SCAN, True, f"Macro gate checked: {lock_event.title}; entry alerts paused"
                    )
                logger.warning(
                    f"Macro Lockout Active: '{lock_event.title}' at {lock_event.timestamp.strftime('%H:%M UTC')}. "
                    "No entry alerts will be emitted during this window.",
                    extra={
                        "event": "macro_lockout_active",
                        "lock_event": lock_event.title,
                        "event_time": str(lock_event.timestamp),
                    },
                )
                return None

            started = time.monotonic()
            if include_fifteen_min is None:
                include_fifteen_min = (timeframe or "").strip().lower() == "15m" or any(
                    getattr(d, "timeframe", None) == "15m"
                    for d in (*alpha_snapshot.active, *alpha_snapshot.shadow, *alpha_snapshot.probe)
                )
            summary: dict[str, Any] = {
                # What this run covered, so the digest can aggregate suggestion scans
                # only: the 15-minute intraday job carries a timeframe, and an operator
                # or Telegram scan of named symbols is restricted.
                "scope": {"asset_class": asset_class, "timeframe": timeframe, "restricted": bool(symbols)},
                "scanned": 0,
                "fetch_failed": [],
                "insufficient": [],
                "skipped_closed_session": [],
                "skipped_not_executable": [],
                "coverage_excluded": [],
                "candidates": 0,
                "approved": 0,
                "sent": 0,
                "runners_up": [],
            }
            equity_open = True
            if not dry_run and not bypass_session_filter:
                equity_open, _ = await self.session_provider.is_session_active(instrument_type="equity")

            total_candidates = 0
            total_alerts = 0
            # COLLECT: every deduplicated candidate that clears the deterministic risk
            # gate, as (candidate, evaluation, account_risk). RANK-AND-SEND follows the
            # sequential per-contract phase so the whole universe competes for the few
            # cards this session may still spend.
            approved_candidates: list[tuple[Any, Any, Any]] = []

            target_syms = [s.strip().upper() for s in symbols] if symbols else None

            selected: list[tuple[str, Any]] = []
            for contract, info in self.config.contracts.items():
                clean_contract = contract.strip("/").upper()
                if target_syms and (contract.upper() not in target_syms and clean_contract not in target_syms):
                    continue

                inst_class = getattr(info, "asset_class", AssetClass.FUTURES)
                inst_class_norm = normalize_asset_class(str(inst_class))
                req_class_norm = normalize_asset_class(asset_class)
                if asset_class and asset_class.lower() != "all" and inst_class_norm != req_class_norm:
                    continue
                if not executes_asset_class(self.config.execution_mode, inst_class_norm):
                    # A card the broker's entry admission would refuse cannot be accepted.
                    summary["skipped_not_executable"].append(contract)
                    continue
                if inst_class_norm == normalize_asset_class("equity") and not equity_open:
                    summary["skipped_closed_session"].append(contract)
                    continue
                selected.append((contract, info))

            datasets, receipts = await self._fetch_universe(selected, include_fifteen_min=include_fifteen_min)

            equities = {
                c
                for c, i in selected
                if normalize_asset_class(str(getattr(i, "asset_class", ""))) == normalize_asset_class("equity")
            }
            try:
                if not equities:
                    # The gate only ever excludes equities against an equity reference;
                    # a futures-only selection has nothing to measure and must not warn
                    # that the (unfetched) reference is unavailable.
                    excluded, coverage_note = set[str](), None
                else:
                    excluded, coverage_note = await asyncio.to_thread(
                        coverage_exclusions,
                        datasets,
                        reference=self.config.scan.coverage_reference_symbol,
                        sessions=self.config.scan.coverage_sessions,
                        min_ratio=self.config.scan.min_bar_coverage,
                        equities=equities,
                    )
            except Exception:
                logger.exception(
                    "Coverage gate raised; skipping the gate for this scan",
                    extra={"event": "coverage_gate_error"},
                )
                excluded, coverage_note = set(), "Coverage gate skipped: gate raised an exception"
            summary["coverage_excluded"] = sorted(excluded)
            summary["coverage_note"] = coverage_note
            if coverage_note:
                logger.warning(coverage_note, extra={"event": "coverage_gate_skipped"})

            for contract, info in selected:
                data = datasets.get(contract)
                if isinstance(data, BaseException) or data is None:
                    summary["fetch_failed"].append(contract)
                    logger.warning(
                        "Fetch failed for %s: %r",
                        contract,
                        data,
                        extra={"event": "scan_fetch_failed", "contract": contract},
                    )
                    continue

                inst_class = getattr(info, "asset_class", AssetClass.FUTURES)
                logger.info(f"Scanning contract {contract} ({info.name} - {info.ticker}) [{inst_class}]...")
                try:
                    if not dry_run:
                        await self.alpha_shadow.observe(alpha_snapshot, data, as_of=receipts.get(contract))

                    if data.daily.empty or data.four_hour.empty:
                        summary["insufficient"].append(contract)
                        logger.warning(f"Insufficient data for {contract}, skipping.")
                        continue

                    if contract in excluded:
                        logger.info(
                            "Coverage gate excluded %s from strategy scanning",
                            contract,
                            extra={"event": "coverage_excluded", "contract": contract},
                        )
                        continue

                    summary["scanned"] += 1

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

                        setup = (candidate.contract, candidate.strategy, candidate.timeframe, candidate.alpha_version)
                        is_dup = setup not in dedup_exempt_setups and await self.db.is_duplicate_recent(
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
                        account_risk = None
                        if not dry_run and requires_account_risk(self.config):
                            try:
                                if self.ledger is None:
                                    raise ValueError("Observed account risk service is unavailable")
                                account_risk = await self.ledger.current_risk()
                                if account_risk.equity <= 0:
                                    raise ValueError("Observed account equity is nonpositive; new entries blocked")
                            except ValueError as exc:
                                scan_errors += 1
                                logger.warning(
                                    "Candidate blocked: account risk unavailable: %s",
                                    exc,
                                    extra={"event": "candidate_risk_unavailable", "contract": candidate.contract},
                                )
                                continue
                        # Deterministic pass: it decides which candidates may compete for
                        # a card. The winners are re-evaluated below with the caller's
                        # use_llm, and that second result is the one recorded.
                        det_res = await self.evaluator.evaluate_candidate(
                            candidate,
                            current_open_notional=current_exposure,
                            use_llm=False,
                            active_positions=active_positions,
                            current_drawdown_pct=float(account_risk.drawdown_pct) if account_risk else 0.0,
                            current_equity=float(account_risk.equity) if account_risk else None,
                        )

                        if not det_res.approved:
                            logger.info(
                                "Candidate rejected by risk engine: %s",
                                det_res.rejection_reason,
                                extra={
                                    "event": "candidate_rejected",
                                    "phase": "collect",
                                    "contract": candidate.contract,
                                    "rejection_reason": det_res.rejection_reason,
                                },
                            )
                            continue

                        approved_candidates.append((candidate, det_res, account_risk))

                except Exception:
                    scan_errors += 1
                    logger.exception(f"Error scanning {contract}")

            # RANK AND SEND: best setup first, ties broken deterministically so a rerun
            # of the same universe spends the budget on the same names.
            ranked = sorted(
                approved_candidates,
                key=lambda item: (-self._setup_quality(item[0]), item[0].contract, item[0].strategy),
            )
            # Deterministic approvals eligible for ranking, not cards sent: the budget,
            # the send-phase evaluation and send failures all thin this number down.
            summary["approved"] = len(ranked)
            # SHADOW: evidence only. It reads `ranked` and never reorders, filters or
            # gates it; a failure leaves every block None and the scan unchanged.
            # `shadow_evidence` is an explicit keyword the caller must opt into -- only
            # `make_suggestion_scan`'s scheduled job passes True -- never inferred from
            # scan shape: the daemon's 4-hourly swing scan and an unrestricted operator
            # or Telegram scan share this scan's shape (no symbols, no timeframe) but
            # never request shadow evidence. The full-universe restriction below is a
            # second, defensive check: even a caller that mistakenly asks for shadow
            # evidence on a symbol- or timeframe-scoped scan gets neither scoring nor
            # journaling, since only the unrestricted population matches the setup
            # study's own.
            decided_at = datetime.now(UTC)
            scan_id = uuid4().hex
            summary["scan_id"] = scan_id
            compute_shadow_evidence = shadow_evidence and not symbols and timeframe is None
            shadow_by_rank: list[dict[str, Any] | None] = [None] * len(ranked)
            if not dry_run and ranked and compute_shadow_evidence:
                shadow_by_rank = await self._shadow_blocks(ranked, datasets, decided_at, summary)
            outcomes: list[str | None] = [None] * len(ranked)
            cfg = self.config.scan
            groups_used: dict[str, int] = {}
            if budget == ScanBudget.NONE:
                remaining_scan = remaining_session = len(ranked)
            else:
                # Durable, derived budget: a restart mid-session must not hand out a
                # fresh allowance, so today's spend is read back from recorded signals.
                today = await self.db.signals_since(self.session_start_et())
                remaining_session = max(0, cfg.max_cards_per_session - len(today))
                remaining_scan = cfg.max_cards_per_scan if budget == ScanBudget.FULL else len(ranked)
                for row in today:
                    for group in self.correlation_groups_of(row["contract"]):
                        groups_used[group] = groups_used.get(group, 0) + 1
            llm_budget = cfg.max_llm_evaluations_per_scan
            # One session-clock read per contract that actually records a card.
            session_closes: dict[str, str | None] = {}
            for rank, (candidate, _det_res, account_risk) in enumerate(ranked, 1):
                # One failed send must not abandon the rest of the ranking or the scan's
                # own bookkeeping, exactly as the per-contract guard protects COLLECT.
                try:
                    reason = None
                    if remaining_scan <= 0:
                        reason = "per-scan budget spent"
                    elif remaining_session <= 0:
                        reason = "per-session budget spent"
                    elif budget != ScanBudget.NONE and any(
                        groups_used.get(g, 0) >= cfg.max_cards_per_group_per_session
                        for g in self.correlation_groups_of(candidate.contract)
                    ):
                        reason = "correlation group already has a card this session"
                    elif use_llm and llm_budget <= 0:
                        # Only an LLM scan spends this budget; a deterministic scan is
                        # bounded by the card budget alone.
                        reason = "LLM evaluation budget spent"
                    if reason:
                        outcomes[rank - 1] = reason
                        summary["runners_up"].append(self._runner_up(candidate, reason))
                        continue

                    if use_llm:
                        llm_budget -= 1
                    eval_res = await self.evaluator.evaluate_candidate(
                        candidate,
                        current_open_notional=current_exposure,
                        use_llm=use_llm,
                        active_positions=active_positions,
                        current_drawdown_pct=float(account_risk.drawdown_pct) if account_risk else 0.0,
                        current_equity=float(account_risk.equity) if account_risk else None,
                    )

                    if not eval_res.approved:
                        outcomes[rank - 1] = f"rejected: {eval_res.rejection_reason}"
                        summary["runners_up"].append(
                            self._runner_up(candidate, f"rejected: {eval_res.rejection_reason}")
                        )
                        logger.info(
                            "Candidate rejected by risk engine: %s",
                            eval_res.rejection_reason,
                            extra={
                                "event": "candidate_rejected",
                                "phase": "send",
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

                    if candidate.contract not in session_closes:
                        session_closes[candidate.contract] = await self._card_valid_until(candidate.contract)
                    valid_until = session_closes[candidate.contract]
                    validity = {"valid_until": valid_until} if valid_until else {}

                    # Record to database
                    sig_id = await self.db.record_signal(
                        timeframe=candidate.timeframe,
                        alpha_version=candidate.alpha_version,
                        alpha_policy=candidate.alpha_policy,
                        decision_provenance={
                            "candle_timestamp": candidate.candle_timestamp,
                            "alpha_score": candidate.alpha_score,
                            "contributors": candidate.contributors,
                            "account_risk_fingerprint": account_risk.fingerprint if account_risk else None,
                            PAPER_PROBE_TAG: candidate.probe,
                            "setup_quality": self._setup_quality(candidate),
                            "rank": rank,
                            "candidates_considered": len(ranked),
                            "budget": str(budget),
                            "shadow_ranker": shadow_by_rank[rank - 1],
                            **validity,
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
                            "probe_risk_cap": self.config.alpha_pipeline.probe_risk_dollars
                            if candidate.probe
                            else None,
                            **validity,
                        },
                    )

                    # The card exists from here on, so charge the budget before any
                    # further bookkeeping: an exception later in this iteration must
                    # never leave a recorded card uncharged.
                    total_alerts += 1
                    outcomes[rank - 1] = "sent"
                    remaining_scan -= 1
                    remaining_session -= 1
                    for group in self.correlation_groups_of(candidate.contract):
                        groups_used[group] = groups_used.get(group, 0) + 1

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
                            "setup_quality": self._setup_quality(candidate),
                            "rank": rank,
                        },
                    )

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

                except Exception as exc:
                    scan_errors += 1
                    if outcomes[rank - 1] is None:
                        outcomes[rank - 1] = f"error: {type(exc).__name__}"
                    logger.exception(f"Error scanning {candidate.contract}")
                    continue

            if not dry_run and budget != ScanBudget.NONE and ranked and compute_shadow_evidence:
                await self._journal_scan_ranking(
                    scan_id=scan_id,
                    decided_at=decided_at,
                    budget=budget,
                    ranked=ranked,
                    outcomes=outcomes,
                    shadow_by_rank=shadow_by_rank,
                    summary=summary,
                )

            summary["candidates"] = total_candidates
            summary["sent"] = total_alerts
            summary["budget"] = str(budget)
            summary["duration_seconds"] = round(time.monotonic() - started, 3)
            self.last_scan_summary = summary
            # Only the current New York session is retained, so a long-running daemon
            # cannot accumulate one summary list per calendar day.
            et_today = self.session_start_et().date().isoformat()
            self._session_scan_stats = {et_today: [*self._session_scan_stats.get(et_today, []), summary]}
            self.metrics.observe_histogram(
                "trader_scan_duration_seconds",
                summary["duration_seconds"],
                help_text="Wall-clock duration of one universe scan",
            )
            logger.info(
                "=== Scan Complete: %d candidates evaluated, %d alerts emitted ===",
                summary["candidates"],
                summary["sent"],
                extra={
                    "event": "scan_completed",
                    **{k: (len(v) if isinstance(v, list) else v) for k, v in summary.items()},
                },
            )
            # Fetch failures are scan errors too: a scan where every name failed to fetch
            # must not report SCAN readiness as healthy. Insufficient data (an empty
            # frame that was fetched successfully) is not an error on its own -- a wide
            # universe legitimately contains thin names -- so it is surfaced in the
            # detail string only, not counted toward scan_errors.
            n_fetch_failed = len(summary["fetch_failed"])
            n_insufficient = len(summary["insufficient"])
            scan_errors += n_fetch_failed
            # A selection that reached strategy scanning for nothing at all is a silent
            # whole-scan failure even when every individual name looked merely thin.
            scanned_nothing = bool(selected) and summary["scanned"] == 0
            if not dry_run:
                if scanned_nothing:
                    detail = (
                        f"0 of {len(selected)} instruments scanned ({n_fetch_failed} fetch failed, "
                        f"{n_insufficient} insufficient, {len(summary['coverage_excluded'])} coverage excluded)"
                    )
                else:
                    detail = (
                        f"{scan_errors} instrument errors ({n_fetch_failed} fetch failed, "
                        f"{n_insufficient} insufficient)"
                    )
                await self.readiness.observe(HealthComponent.SCAN, scan_errors == 0 and not scanned_nothing, detail)
            # Monitor any active positions for stop loss or take profit crossings
            if not dry_run:
                await self.monitor_positions()
            return summary

    async def _shadow_blocks(
        self,
        ranked: list[tuple[Any, Any, Any]],
        datasets: dict[str, Any],
        decided_at: datetime,
        summary: dict[str, Any],
    ) -> list[dict[str, Any] | None]:
        """One shadow block per ranked candidate, or all None if anything fails."""
        started = time.monotonic()
        try:
            return await asyncio.to_thread(self._compute_shadow_blocks, ranked, datasets, decided_at)
        except ValueError as exc:
            # `live_cross_section` raises a plain ValueError when this scan's universe
            # has no completed daily session yet (every symbol's daily frame empty or
            # too short): an anticipated, recoverable gap, not a bug. Log it once at
            # WARNING with no traceback rather than `shadow_ranker_failed`'s ERROR/
            # traceback, which is reserved for a genuinely unexpected exception.
            summary["shadow_ranker_error"] = f"{type(exc).__name__}: {exc}"
            logger.warning("Shadow ranker skipped: %s", exc, extra={"event": "shadow_ranker_skipped"})
            return [None] * len(ranked)
        except Exception as exc:
            summary["shadow_ranker_error"] = f"{type(exc).__name__}: {exc}"
            logger.exception(
                "Shadow ranker failed; ranking and cards are unaffected", extra={"event": "shadow_ranker_failed"}
            )
            return [None] * len(ranked)
        finally:
            summary["shadow_ranker_seconds"] = round(time.monotonic() - started, 3)

    def _compute_shadow_blocks(
        self,
        ranked: list[tuple[Any, Any, Any]],
        datasets: dict[str, Any],
        decided_at: datetime,
    ) -> list[dict[str, Any] | None]:
        """Setup features over this scan's already-fetched daily frames; never refetches.

        The cross-section is every ``universe.groups`` symbol with daily bars in this
        scan (not explicit ``contracts:`` equities), as of the last session completed
        before the scan's New York date -- today's partial bar is excluded -- with
        sectors from the configured universe: the study's inputs (``live_cross_section``).
        """
        daily = {contract: getattr(data, "daily", None) for contract, data in datasets.items()}
        sectors = {entry.symbol: entry.sector for group in self.config.universe.groups.values() for entry in group}
        cs = live_cross_section(daily, sectors, self.session_start_et(decided_at).date())
        artifact = self.config.scan.shadow_ranker_artifact
        ranker = cached_ranker(artifact) if artifact is not None else None
        vectors = [
            setup_features(
                cs,
                symbol=candidate.contract,
                direction=candidate.direction,
                strategy=candidate.strategy,
                timeframe=candidate.timeframe,
                setup_quality=self._setup_quality(candidate),
                entry=det_res.entry_price,
                stop=det_res.stop_loss,
                target=det_res.take_profit,
                atr_14=candidate.atr_14,
            )
            for candidate, det_res, _ in ranked
        ]
        return list(shadow_blocks(vectors, ranker))

    async def _journal_scan_ranking(
        self,
        *,
        scan_id: str,
        decided_at: datetime,
        budget: ScanBudget,
        ranked: list[tuple[Any, Any, Any]],
        outcomes: list[str | None],
        shadow_by_rank: list[dict[str, Any] | None],
        summary: dict[str, Any],
    ) -> None:
        """Append one ``scan_candidates_ranked`` event in its own transaction; never raises."""
        try:
            candidates = [
                {
                    "contract": candidate.contract,
                    "strategy": candidate.strategy,
                    "timeframe": candidate.timeframe,
                    "direction": candidate.direction,
                    "entry": finite_or_none(det_res.entry_price),
                    "stop": finite_or_none(det_res.stop_loss),
                    "target": finite_or_none(det_res.take_profit),
                    "atr_14": finite_or_none(candidate.atr_14),
                    "setup_quality": finite_or_none(self._setup_quality(candidate)),
                    "rank": rank,
                    "outcome": outcomes[rank - 1],
                    "shadow": shadow_by_rank[rank - 1],
                }
                for rank, (candidate, det_res, _) in enumerate(ranked, 1)
            ]
            payload = {
                "scan_id": scan_id,
                "decided_at": decided_at.isoformat(),
                "scope": "universe",
                # This method is only ever called when `run_scan` computed shadow
                # evidence, i.e. the scheduled suggestion-scan job requested it.
                "trigger": "suggestion_scan",
                "budget": str(budget),
                "ranking_key": "setup_quality",
                "candidates": candidates,
            }
            et_date = self.session_start_et(decided_at).date().isoformat()
            workflows = self.db.workflows
            async with self.db.session_factory() as session, session.begin():
                await workflows.lock(session)
                await workflows.append(
                    session,
                    stream=f"scan/{et_date}",
                    kind=EventKind.SCAN_CANDIDATES_RANKED,
                    payload=payload,
                    key=f"scan_candidates_ranked/{scan_id}",
                )
        except Exception as exc:
            summary["scan_journal_error"] = f"{type(exc).__name__}: {exc}"
            logger.exception(
                "Scan ranking journal failed; cards and budget are unaffected", extra={"event": "scan_journal_failed"}
            )

    async def _fetch_universe(
        self, instruments: list[tuple[str, Any]], *, include_fifteen_min: bool
    ) -> tuple[dict[str, Any], dict[str, datetime]]:
        """Fetch every instrument's bars with bounded concurrency; failures are returned, not raised.

        Returns the fetched dataset per contract alongside each contract's own fetch
        *receipt* time (when its individual fetch resolved), so the sequential phase can
        stamp its shadow observation with that receipt instead of a scan-wide timestamp
        taken after the whole (potentially minutes-long) fetch phase completes.
        """
        semaphore = asyncio.Semaphore(self.config.market_data.scan_concurrency)
        receipts: dict[str, datetime] = {}

        async def one(contract: str, info: Any):
            async with semaphore:
                result = await asyncio.to_thread(
                    self.data_fetcher.fetch_data,
                    contract,
                    info.ticker,
                    include_fifteen_min=include_fifteen_min,
                )
                receipts[contract] = datetime.now(UTC)
                return result

        results = await asyncio.gather(*(one(c, i) for c, i in instruments), return_exceptions=True)
        datasets = {contract: result for (contract, _), result in zip(instruments, results, strict=True)}
        return datasets, receipts

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

    async def execute_signal_by_id(self, signal_id: int, quantity: float | None = None) -> ExecutionReply:
        """
        Execute an approved trade setup by signal ID.
        Submits bracket orders to the active broker and registers the position in the database.
        Optionally overrides the order quantity with operator-selected tier size.

        With card freshness enabled, the card is first re-assessed at tap time (current
        price, session and gates). Only an ``EXECUTE`` outcome continues into the unchanged
        entry authorization with the original bracket; the assessment never authorizes.
        """
        await self.check_halt_state()
        if self.is_halted:
            logger.warning(
                "Signal execution rejected: Emergency kill switch active (%s).",
                self.halt_reason,
                extra={"event": "trading_halted_execution_blocked", "signal_id": signal_id, "reason": self.halt_reason},
            )
            return ExecutionReply(
                False,
                f"🛑 <b>Execution Blocked:</b> Emergency trading halt active ({html.escape(str(self.halt_reason))}). "
                "Use /resume to unhalt.",
            )

        sig = await self.db.get_signal_by_id(signal_id)
        if not sig:
            return ExecutionReply(False, f"❌ Signal #{signal_id} not found in database.")

        if sig["status"] != SignalStatus.PENDING:
            return self._not_pending_reply(signal_id, sig["status"])

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
            return ExecutionReply(False, "Execution Rejected: quantity must be finite and positive.")
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
        if self.config.execution.card_freshness.enabled:
            tick_size = (
                contract_info.tick_size if contract_info else (0.01 if asset_class == AssetClass.EQUITY else 0.25)
            )
            refusal = await self._assess_card_tap(
                sig, req, ticker=ticker, asset_class=str(asset_class), multiplier=multiplier, tick_size=tick_size
            )
            if refusal is not None:
                return refusal
        item, reason = await self.entry_service.authorize(req)
        if item is None:
            return ExecutionReply(False, f"⚠️ <b>Execution Rejected:</b> {html.escape(reason)}")
        if item.status in (WorkStatus.QUEUED, WorkStatus.CHECKING):
            return ExecutionReply(
                False,
                f"⏳ Entry #{signal_id} queued as <code>{item.id}</code>. Fresh checks precede submission; final status will be notified.",
            )
        if item.status in (WorkStatus.SUBMITTING, WorkStatus.UNKNOWN):
            return ExecutionReply(
                False, f"⚠️ Entry #{signal_id} awaiting exact broker lookup ({item.id}). No automatic resubmission."
            )
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
        return ExecutionReply(item.status == WorkStatus.ACCEPTED, TelegramHtmlFormatter.format_execution_html(view))

    @staticmethod
    def _not_pending_reply(signal_id: int, status: str | None) -> ExecutionReply:
        return ExecutionReply(
            False, f"❌ Signal #{signal_id} is in status <b>{status}</b> (only PENDING signals can be executed)."
        )

    async def _current_status_reply(self, signal_id: int) -> ExecutionReply:
        """A conditional transition lost a race: report the card's current status instead."""
        current = await self.db.get_signal_by_id(signal_id)
        return self._not_pending_reply(signal_id, current["status"] if current else None)

    @staticmethod
    def _next_open_text(info: Any) -> str:
        """The next regular open in UTC and New York time, or an explicit unavailability."""
        next_open = getattr(info, "next_open", None)
        if isinstance(next_open, datetime) and next_open.utcoffset() is not None:
            return (
                f"Next regular open: {next_open.astimezone(UTC):%Y-%m-%d %H:%M} UTC "
                f"({next_open.astimezone(ET_TZ):%H:%M} NY)."
            )
        return "Next regular open: unavailable."

    async def _card_valid_until(self, contract: str) -> str | None:
        """ISO end of the regular session a card is issued in, or None when unknown.

        Recorded only while the contract is in its regular session: outside it a
        provider's ``next_close`` belongs to a later session, and a card without
        ``valid_until`` conservatively expires when the New York date changes.
        """
        try:
            info = await self.session_provider.get_session_info(contract)
        except Exception:
            logger.warning(
                "Session close unavailable for %s; card recorded without valid_until",
                contract,
                exc_info=True,
                extra={"event": "card_valid_until_unavailable", "contract": contract},
            )
            return None
        close = getattr(info, "next_close", None)
        if (
            getattr(info, "is_open", False) is True
            and getattr(info, "is_rth", False) is True
            and isinstance(close, datetime)
            and close.utcoffset() is not None
        ):
            return close.astimezone(UTC).isoformat()
        return None

    async def _tap_price(self, ticker: str, tick_size: float) -> float | None:
        """The latest trade, tick-aligned, or None when unavailable (off the event loop)."""
        try:
            raw = await asyncio.to_thread(self.data_fetcher.fetch_latest_price, ticker)
        except Exception:
            logger.warning(
                "Tap-time price fetch failed for %s", ticker, exc_info=True, extra={"event": "card_tap_price_failed"}
            )
            return None
        try:
            price = float(raw) if raw is not None else None
        except TypeError, ValueError:
            return None
        if price is None or not math.isfinite(price) or price <= 0:
            return None
        rounded = round_to_tick(price, tick_size)
        return rounded if rounded > 0 else None

    async def _earnings_blackout_reason(self, contract: str, now: datetime) -> str | None:
        """The evaluator's equity earnings gate, failing open on a calendar failure exactly as it does."""
        blackout_days = self.config.risk.earnings_blackout_days
        if blackout_days <= 0 or self.earnings_calendar is None:
            return None
        try:
            lookup = await self.earnings_calendar.next_earnings(contract, now=now, horizon_days=blackout_days)
        except Exception as e:
            logger.warning(
                "Earnings calendar lookup failed (%s); failing open (no blackout).",
                e,
                extra={"contract": contract, "error": str(e)},
            )
            lookup = EarningsLookup(event=None, verified=False, horizon_end=now.date())
        reason = earnings_blackout_reason(lookup, contract, now, blackout_days)
        return f"Earnings Blackout: {reason}" if reason else None

    async def _tap_gate_reason(
        self, request: OrderRequest, sig: dict[str, Any], regime: Any, *, asset_class: str, now: datetime
    ) -> str | None:
        """First failing tap-time gate: macro/regime/reward:risk, then the equity earnings blackout."""
        assert request.entry_price is not None and request.stop_loss is not None
        # Degenerate levels are MISSED by the assessment itself; the macro gate divides by the risk.
        if abs(request.entry_price - request.stop_loss) > 0:
            reason = await self._macro_gate(request, sig, regime)
            if reason:
                return reason
        if asset_class.upper() == AssetClass.EQUITY:
            return await self._earnings_blackout_reason(sig["contract"], now)
        return None

    async def _journal_card_tap(
        self,
        signal_id: int,
        assessment: CardAssessment,
        tapped_at: datetime,
        *,
        applied: bool,
        policy_locked: bool,
        new_signal_id: int | None = None,
    ) -> None:
        """Journal the assessment after its state transition, so the evidence matches the final state.

        ``applied`` is True when the outcome took effect: EXECUTE was handed to entry
        authorization (whose own outcome the entry workflow journals), REPRICE recorded its
        replacement, MISSED/EXPIRED expired the card. False means a conditional transition
        lost a race and the card's state is whatever the winner left.
        """
        payload = {
            "signal_id": signal_id,
            "outcome": str(assessment.outcome),
            "reason": assessment.reason,
            "age_seconds": round(assessment.age_seconds, 3),
            "price": assessment.price,
            "r_consumed": assessment.r_consumed,
            "tapped_at": tapped_at.isoformat(),
            "applied": applied,
            "new_signal_id": new_signal_id,
            "policy_locked": policy_locked,
        }
        logger.info(
            "Card #%d tap assessed: %s (%s)",
            signal_id,
            assessment.outcome,
            assessment.reason,
            extra={"event": EventKind.CARD_TAP_ASSESSED, **payload},
        )
        try:
            await self.db.workflows.record_card_tap(signal_id, payload)
        except Exception:
            # Evidence only: a journal failure must never block or alter the tap.
            logger.exception(
                "Card tap assessment journal failed", extra={"event": "card_tap_journal_failed", "signal_id": signal_id}
            )

    async def _replacement_card(
        self,
        sig: dict[str, Any],
        assessment: CardAssessment,
        *,
        price: float,
        base_quantity: float,
        asset_class: str,
        multiplier: float,
        issued_at: datetime,
        regime: Any,
    ) -> tuple[dict[str, Any] | None, str]:
        """``replace_signal`` arguments for a re-priced card, or None with the MISSED reason.

        Same stop, target and thesis; entry at the (tick-aligned) current price; size
        re-derived from ``base_quantity``'s risk dollars (the tapped tier, else the card's
        own size), capped by its notional. The notification mirrors the scan's card payload
        so the outbox delivers it exactly like a scan card.
        """
        signal_id = int(sig["id"])
        entry, stop, target = float(sig["entry_price"]), float(sig["stop_loss"]), float(sig["take_profit"])
        quantity = reprice_quantity(
            original_quantity=base_quantity,
            entry=entry,
            stop=stop,
            new_entry=price,
            multiplier=multiplier,
            # Fractional shares/contracts are never offered, except for crypto.
            whole_units=asset_class.upper() != AssetClass.CRYPTO,
        )
        if quantity <= 0:
            return None, "Missed: the re-priced size rounds to zero."
        quantity = self._capped_replacement_quantity(
            quantity, price=price, stop=stop, multiplier=multiplier, asset_class=asset_class, regime=regime
        )
        if quantity <= 0:
            return None, "Missed: the per-trade caps leave no size for a re-priced card."
        stop_distance, target_distance = abs(price - stop), abs(target - price)
        risk_dollars = round(stop_distance * multiplier * quantity, 2)
        reward_dollars = round(target_distance * multiplier * quantity, 2)
        notional_value = round(price * multiplier * quantity, 2)
        try:
            original = LLMTradeEvaluation.model_validate_json(sig.get("raw_response") or "")
            rebuilt = LLMTradeEvaluation.model_validate(
                {
                    **original.model_dump(),
                    "entry_price": price,
                    "quantity": quantity,
                    "stop_distance_points": stop_distance,
                    "target_distance_points": target_distance,
                    "risk_reward_ratio": target_distance / stop_distance,
                    "risk_dollars": risk_dollars,
                    "reward_dollars": reward_dollars,
                    "notional_value": notional_value,
                    "effective_leverage": round(notional_value / self.config.portfolio.cash, 2),
                    # Tiers were sized at the old entry; the replacement offers its own size only.
                    "sizing_tiers": None,
                }
            )
        except ValueError:
            logger.warning(
                "Card #%d cannot be re-priced: its recorded evaluation is unavailable or invalid",
                signal_id,
                exc_info=True,
                extra={"event": "card_reprice_unavailable", "signal_id": signal_id},
            )
            return None, "Missed: this card cannot be re-priced (its original evaluation is unavailable)."

        old_provenance = sig.get("decision_provenance")
        old_provenance = dict(old_provenance) if isinstance(old_provenance, dict) else {}
        first_issued_at = old_provenance.get("first_issued_at") or issued_at.isoformat()
        provenance = {
            **old_provenance,  # carries valid_until, rank, probe tag and the rest unchanged
            "reprices": signal_id,
            "first_issued_at": first_issued_at,
            "tap_latency_seconds": round(assessment.age_seconds, 3),
            "r_consumed": assessment.r_consumed,
        }
        valid_until = old_provenance.get("valid_until")
        notification = {
            "eval_res": rebuilt.model_dump(mode="json"),
            "strategy": sig["strategy"],
            "regime_summary": getattr(regime, "summary_text", None),
            "probe_risk_cap": self.config.alpha_pipeline.probe_risk_dollars
            if old_provenance.get(PAPER_PROBE_TAG)
            else None,
            **({"valid_until": valid_until} if valid_until else {}),
            "reprices": signal_id,
            "first_issued_at": first_issued_at,
        }
        fields = {
            "notification": notification,
            "decision_provenance": provenance,
            "contract": sig["contract"],
            "strategy": sig["strategy"],
            "timeframe": sig.get("timeframe"),
            "direction": sig["direction"],
            "entry_price": price,
            "stop_loss": stop,
            "take_profit": target,
            "risk_dollars": risk_dollars,
            "reward_dollars": reward_dollars,
            "notional_value": notional_value,
            "raw_response": rebuilt.model_dump_json(),
            "status": SignalStatus.PENDING,
            "asset_class": asset_class,
            "quantity": quantity,
            "alpha_version": sig.get("alpha_version"),
            "alpha_policy": sig.get("alpha_policy"),
        }
        return fields, ""

    def _capped_replacement_quantity(
        self, quantity: float, *, price: float, stop: float, multiplier: float, asset_class: str, regime: Any
    ) -> float:
        """Apply admission's per-trade caps, so a replacement never offers a size admission refuses.

        Quantity (shares/contracts), per-trade notional and per-trade risk (on configured
        cash, scaled down but never up by the regime) cap the size; whole units except crypto.
        Account-state limits (drawdown, aggregate exposure, positions) stay admission's job.
        """
        sizing = self.config.sizing
        is_futures = asset_class.upper() == AssetClass.FUTURES
        caps = [quantity, float(sizing.max_contracts_per_trade if is_futures else sizing.max_shares_per_trade)]
        unit_notional = price * multiplier
        if unit_notional > 0:
            caps.append(sizing.max_trade_notional_cap / unit_notional)
        unit_risk = abs(price - stop) * multiplier
        if unit_risk > 0:
            risk_scale = min(1.0, float(getattr(regime, "risk_multiplier", 1.0)))
            caps.append(self.config.portfolio.cash * sizing.max_risk_pct_cap * risk_scale / unit_risk)
        capped = max(min(caps), 0.0)
        if asset_class.upper() != AssetClass.CRYPTO:
            capped = float(math.floor(capped + 1e-9))
        return capped

    async def _assess_card_tap(
        self,
        sig: dict[str, Any],
        request: OrderRequest,
        *,
        ticker: str,
        asset_class: str,
        multiplier: float,
        tick_size: float,
    ) -> ExecutionReply | None:
        """Re-judge a PENDING card at tap time; None means ``EXECUTE`` (continue into authorization).

        It never authorizes: ``EXECUTE`` falls through to the unchanged entry path with the
        original bracket. ``REPRICE`` records a *new* card needing a fresh tap (the approved
        bracket is immutable); ``MISSED``/``EXPIRED`` expire the card. Every tap-time read
        shares one ``TAP_CHECK_TIMEOUT_SECONDS`` bound; a read failure or timeout refuses the
        tap retryably and leaves the card PENDING.

        A versioned alpha card (``alpha_version``/``alpha_policy``) is never re-priced: its
        immutable execution policy owns the entry limit and bracket, so a REPRICE outcome
        executes the original bracket instead and admission's age/drift/policy checks decide.
        """
        signal_id = int(sig["id"])
        contract = sig["contract"]
        tapped_at = datetime.now(UTC)
        issued_at = datetime.fromisoformat(sig["timestamp"]).replace(tzinfo=UTC)
        policy_locked = bool(sig.get("alpha_version") or sig.get("alpha_policy"))
        price: float | None = None

        async def refuse(reply_text: str, reason: str) -> ExecutionReply:
            # Journaled like any assessment, so tap-time read-failure rates are measurable.
            unavailable = CardAssessment(
                CardOutcome.UNAVAILABLE, reason, None, (tapped_at - issued_at).total_seconds(), price
            )
            await self._journal_card_tap(signal_id, unavailable, tapped_at, applied=False, policy_locked=policy_locked)
            return ExecutionReply(False, reply_text, retryable=True)

        checks_unavailable = "⚠️ Checks unavailable; try again shortly."
        try:
            async with asyncio.timeout(TAP_CHECK_TIMEOUT_SECONDS):
                price = await self._tap_price(ticker, tick_size)
                if price is None:
                    return await refuse("⚠️ Current price unavailable; try again shortly.", "Current price unavailable.")
                info = await self.session_provider.get_session_info(contract)
                # The cached regime: admission force-refreshes it before any submission.
                regime = await self.regime_detector.get_regime()
                gate_reason = await self._tap_gate_reason(request, sig, regime, asset_class=asset_class, now=tapped_at)
        except TimeoutError:
            logger.warning(
                "Tap-time checks for card #%d exceeded %.0fs; card left PENDING",
                signal_id,
                TAP_CHECK_TIMEOUT_SECONDS,
                extra={"event": "card_tap_checks_timeout", "signal_id": signal_id},
            )
            return await refuse(checks_unavailable, f"Tap-time checks timed out after {TAP_CHECK_TIMEOUT_SECONDS:g}s.")
        except Exception as exc:
            logger.warning(
                "Tap-time session or gate check failed for card #%d; card left PENDING",
                signal_id,
                exc_info=True,
                extra={"event": "card_tap_checks_failed", "signal_id": signal_id},
            )
            return await refuse(checks_unavailable, f"Tap-time checks failed: {type(exc).__name__}.")

        provenance = sig.get("decision_provenance")
        raw_valid_until = provenance.get("valid_until") if isinstance(provenance, dict) else None
        try:
            valid_until = datetime.fromisoformat(raw_valid_until) if raw_valid_until else None
        except TypeError, ValueError:
            valid_until = None
        if valid_until is not None and valid_until.utcoffset() is None:
            valid_until = None  # unreadable: fall back to the legacy New York date rule
        assert price is not None  # a missing price returned above
        assert request.entry_price is not None and request.stop_loss is not None and request.take_profit is not None
        assessment = assess_card(
            direction=request.direction,
            entry=request.entry_price,
            stop=request.stop_loss,
            target=request.take_profit,
            issued_at=issued_at,
            valid_until=valid_until,
            now=tapped_at,
            price=price,
            session_is_rth=bool(info.is_open and info.is_rth),
            gate_reason=gate_reason,
            min_reward_risk=max(self.config.risk.min_risk_reward_ratio, float(regime.min_rr_threshold)),
            policy=self.config.execution.card_freshness,
        )
        replacement: dict[str, Any] | None = None
        if assessment.outcome == CardOutcome.REPRICE and policy_locked:
            assessment = dataclass_replace(
                assessment,
                outcome=CardOutcome.EXECUTE,
                reason=f"Versioned alpha card keeps its original bracket; {assessment.reason}",
            )
        elif assessment.outcome == CardOutcome.REPRICE:
            replacement, missed_reason = await self._replacement_card(
                sig,
                assessment,
                price=price,
                base_quantity=request.quantity,
                asset_class=asset_class,
                multiplier=multiplier,
                issued_at=issued_at,
                regime=regime,
            )
            if replacement is None:
                assessment = dataclass_replace(assessment, outcome=CardOutcome.MISSED, reason=missed_reason)

        async def journal(applied: bool, new_signal_id: int | None = None) -> None:
            await self._journal_card_tap(
                signal_id,
                assessment,
                tapped_at,
                applied=applied,
                policy_locked=policy_locked,
                new_signal_id=new_signal_id,
            )

        if assessment.outcome == CardOutcome.EXECUTE:
            await journal(True)
            return None
        if replacement is not None:
            new_id = await self.db.replace_signal(signal_id, **replacement)
            await journal(new_id is not None, new_id)
            if new_id is None:
                return await self._current_status_reply(signal_id)
            minutes = round(assessment.age_seconds / 60)
            return ExecutionReply(
                False,
                f"🔄 Card #{signal_id} was {minutes} min old ({assessment.r_consumed or 0.0:+.2f}R since). "
                f"A re-priced card #{new_id} was sent; review it and tap again to trade.",
            )
        expired = await self.db.expire_signal(signal_id)
        await journal(expired)
        if not expired:
            return await self._current_status_reply(signal_id)
        text = f"⌛ {html.escape(assessment.reason)}"
        if assessment.outcome == CardOutcome.EXPIRED:
            text += f"\n{self._next_open_text(info)}"
        return ExecutionReply(False, text, offer_reevaluate=True)

    async def reevaluate_signal(self, signal_id: int) -> ExecutionReply:
        """Operator re-evaluation of an expired card: a fresh single-contract scan, never the old levels.

        Validates synchronously (the card is EXPIRED, no live card exists for its contract,
        the contract is in its regular session), then atomically claims the card's single
        re-evaluation, so a redelivered callback, double tap or CLI call schedules nothing
        more. The scan runs in the background so the serialized Telegram handler returns at
        once; it uses the NONE budget (an explicit operator request) and exempts only this
        card's exact setup from the recent-duplicate rule. A fresh card is its own result;
        otherwise the result is queued through the durable outbox.
        """
        sig = await self.db.get_signal_by_id(signal_id)
        if not sig:
            return ExecutionReply(False, f"❌ Signal #{signal_id} not found in database.")
        if sig["status"] != SignalStatus.EXPIRED:
            return ExecutionReply(False, f"Signal #{signal_id} is {sig['status']}; nothing to re-evaluate.")
        contract = sig["contract"]
        if (live := await self.db.live_signal_id(contract)) is not None:
            return ExecutionReply(False, f"A live card for {html.escape(contract)} already exists (#{live}).")
        try:
            async with asyncio.timeout(TAP_CHECK_TIMEOUT_SECONDS):
                info = await self.session_provider.get_session_info(contract)
        except Exception:
            logger.warning(
                "Session unavailable for re-evaluation of card #%d",
                signal_id,
                exc_info=True,
                extra={"event": "card_reevaluate_session_failed", "signal_id": signal_id},
            )
            return ExecutionReply(False, "⚠️ Market session unavailable; try again shortly.")
        if not (info.is_open and info.is_rth):
            return ExecutionReply(False, f"Market closed; {self._next_open_text(info)}")
        setup = (contract, sig["strategy"], sig.get("timeframe"), sig.get("alpha_version"))
        claimed = await self.db.workflows.claim_card_reevaluation(
            signal_id,
            {
                "signal_id": signal_id,
                "setup": list(setup),
                "requested_at": datetime.now(UTC).isoformat(),
            },
        )
        if not claimed:
            return ExecutionReply(False, f"Re-evaluation of #{signal_id} already requested.")
        task = asyncio.create_task(
            self._run_reevaluation(signal_id, contract, setup), name=f"reevaluate-card-{signal_id}"
        )
        self.reevaluation_tasks.add(task)
        task.add_done_callback(self.reevaluation_tasks.discard)
        return ExecutionReply(
            True, f"🔄 Re-evaluating {html.escape(contract)}… a fresh card or a result message will follow."
        )

    async def cancel_reevaluations(self) -> None:
        """Cancel and await every in-flight background re-evaluation (daemon shutdown)."""
        tasks = list(self.reevaluation_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_reevaluation(
        self, signal_id: int, contract: str, setup: tuple[str, str, str | None, str | None]
    ) -> None:
        """Background single-contract scan; any non-card result is delivered through the outbox.

        A cancellation (daemon shutdown) propagates and reports nothing.
        """
        name = html.escape(contract)
        try:
            summary = await self.run_scan(
                symbols=[contract],
                budget=ScanBudget.NONE,
                dedup_exempt_setups=frozenset({setup}),
                scan_lock_timeout=REEVALUATE_SCAN_WAIT_SECONDS,
            )
        except ScanBusyError:
            text = (
                f"Re-evaluation of #{signal_id} ({name}) did not run: a scan is running; "
                "try again in a minute with /scan."
            )
        except Exception:
            logger.exception(
                "Re-evaluation of card #%d failed",
                signal_id,
                extra={"event": "card_reevaluate_failed", "signal_id": signal_id, "contract": contract},
            )
            text = f"Re-evaluation of #{signal_id} ({name}) failed; see logs. Scheduled scans still cover {name}."
        else:
            if summary is not None and summary.get("sent", 0) > 0:
                return  # the fresh card, delivered by the outbox, is the result
            text = self._reevaluation_result_text(contract, summary)
        try:
            await self.outbox.publish_message(text, key=f"reevaluate/{signal_id}/{uuid4().hex}")
        except Exception:
            logger.exception(
                "Re-evaluation result for card #%d could not be queued",
                signal_id,
                extra={"event": "card_reevaluate_notice_failed", "signal_id": signal_id},
            )

    @staticmethod
    def _reevaluation_result_text(contract: str, summary: dict[str, Any] | None) -> str:
        name = html.escape(contract)
        if summary is None:
            return f"No fresh card for {name}: the scan did not run (trading halt, closed session or macro lockout)."
        text = f"No valid setup for {name} right now"
        runners_up = summary.get("runners_up") or []
        detail = None
        if runners_up:
            detail = runners_up[0].get("reason")
        else:
            for key, label in (
                ("fetch_failed", "market data unavailable"),
                ("insufficient", "insufficient market data"),
                ("coverage_excluded", "excluded by the bar-coverage gate"),
                ("skipped_closed_session", "session closed"),
                ("skipped_not_executable", "not executable by this broker"),
            ):
                if summary.get(key):
                    detail = label
                    break
        return f"{text}: {html.escape(str(detail))}." if detail else f"{text}."

    async def _entry_macro_check(self, request: OrderRequest, signal: dict[str, Any]) -> str | None:
        """Admission's macro check: force-refreshes the regime before any submission."""
        if reason := await self._macro_lockout_reason():
            return reason
        regime = await self.regime_detector.get_regime(force_refresh=True)
        return self._regime_gate(request, signal, regime)

    async def _macro_gate(self, request: OrderRequest, signal: dict[str, Any], regime: Any) -> str | None:
        """The same macro check against a regime the caller already holds (tap time: cached)."""
        return await self._macro_lockout_reason() or self._regime_gate(request, signal, regime)

    async def _macro_lockout_reason(self) -> str | None:
        in_lockout, event = await self.calendar.is_in_lockout_window(
            pre_minutes=self.config.risk.lockout_pre_event_minutes,
            post_minutes=self.config.risk.lockout_post_event_minutes,
        )
        if in_lockout:
            return f"Macro event lockout active: {event.title if event else 'scheduled release'}."
        return None

    def _regime_gate(self, request: OrderRequest, signal: dict[str, Any], regime: Any) -> str | None:
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
            if requires_account_risk(self.config):
                await self.ledger.current_risk()

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
        macro_summary = await self.calendar.get_macro_summary_for_prompt(
            pre_minutes=self.config.risk.lockout_pre_event_minutes,
            post_minutes=self.config.risk.lockout_post_event_minutes,
        )
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

    @staticmethod
    def _is_suggestion_scan(summary: dict[str, Any]) -> bool:
        """A universe scan: no timeframe filter and no symbol restriction."""
        scope = summary.get("scope") or {}
        return scope.get("timeframe") is None and not scope.get("restricted")

    async def publish_scan_digest(self, et_date: str | None = None) -> str:
        """Publish exactly one end-of-session digest of this New York date's suggestion scans.

        Only *universe* suggestion scans are aggregated: a summary with a timeframe (the
        15-minute intraday job) or a symbol restriction (an operator or Telegram scan of
        named contracts) is not a suggestion scan and is excluded. If only excluded scans
        ran, the digest says no suggestion scans ran.

        `publish_message` marks the text as formatted HTML. Every interpolated value is
        either one of our own literals or a contract symbol matching ``^[A-Z][A-Z.]{0,5}$``
        (plus an optional leading ``/`` for futures), so no escaping is needed here.
        """
        et_date = et_date or self.session_start_et().date().isoformat()
        scans = [s for s in self._session_scan_stats.get(et_date, []) if self._is_suggestion_scan(s)]
        if not scans:
            text = f"📋 Scan digest {et_date}: no suggestion scans ran this session."
        else:
            candidates = sum(s.get("candidates", 0) for s in scans)
            sent = sum(s.get("sent", 0) for s in scans)
            scanned = sum(s.get("scanned", 0) for s in scans)
            insufficient = sum(len(s.get("insufficient", [])) for s in scans)
            runners = [r for s in scans for r in s.get("runners_up", [])]
            failed = sorted({c for s in scans for c in s.get("fetch_failed", [])})
            excluded = sorted({c for s in scans for c in s.get("coverage_excluded", [])})
            durations = ", ".join(f"{s.get('duration_seconds', 0):.0f}s" for s in scans)
            top = ", ".join(f"{r['contract']} {r['direction']} ({r['setup_quality']:.2f})" for r in runners[:5])
            text = (
                f"📋 Scan digest {et_date}: {len(scans)} scan(s), {scanned} scanned, "
                f"{insufficient} insufficient, {candidates} candidates, {sent} card(s) sent, "
                f"{len(runners)} runners-up" + (f" — top: {top}" if top else "") + ". "
                f"Fetch failures: {len(failed)}" + (f" ({', '.join(failed[:8])})" if failed else "") + "; "
                f"coverage excluded: {len(excluded)}; scan durations: {durations}."
            )
        await self.outbox.publish_message(text, key=f"scan-digest/{et_date}")
        return text

    async def run_scan_summary_html(self) -> str:
        if self._scan_lock.locked():
            # A universe scan takes minutes; queueing /scan behind it would block the
            # Telegram handler and then report counts from the other run.
            return (
                "⏳ <b>Scan Already Running:</b> a universe scan is in flight; "
                "its cards will arrive here. Try /scan again shortly."
            )
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
        probes: list[dict] | None = None
        try:
            probes = await self.alpha_repository.probe_report()
        except Exception:
            logger.exception(
                "Paper-probe forward report failed; rendering dashboard without probe detail",
                extra={"event": "probe_report_failed"},
            )
        return TelegramHtmlFormatter.format_alphas_dashboard_html(snapshot, evidence=evidence, probes=probes)

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
