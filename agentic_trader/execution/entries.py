"""Application service for durable entry authorization, admission and recovery."""

from __future__ import annotations

import logging
import math
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agentic_trader.accounting.risk import AccountRiskSnapshot
from agentic_trader.accounting.service import AccountLedgerService
from agentic_trader.broker.base import BaseBroker, OrderRequest, OrderResult
from agentic_trader.config import AppConfig
from agentic_trader.constants import BROKER_CLOCK_SKEW_TOLERANCE_SECONDS, SignalStatus, SystemStateKey
from agentic_trader.execution.admission import reservation_rejection
from agentic_trader.execution.durable import WorkItem, WorkKind, WorkStatus
from agentic_trader.risk import requires_account_risk
from agentic_trader.storage.workflow import WorkflowStore


if TYPE_CHECKING:
    from agentic_trader.execution.engine import SlicedExecutionEngine

logger = logging.getLogger(__name__)


class EntryExecutionService:
    def __init__(
        self,
        config: AppConfig,
        store: WorkflowStore,
        broker: BaseBroker,
        executor: SlicedExecutionEngine,
        macro_check: Callable[[OrderRequest, dict[str, Any]], Awaitable[str | None]],
        *,
        ledger: AccountLedgerService | None = None,
    ):
        self.config, self.store, self.broker = config, store, broker
        self.executor, self.macro_check = executor, macro_check
        self.ledger = ledger

    async def authorize(self, request: OrderRequest) -> tuple[WorkItem | None, str]:
        item, reason = await self.store.enqueue_entry(request, self.config)
        if item and item.status == WorkStatus.QUEUED:
            # CLI/Telegram may help drain the same queue as the daemon. Durable
            # fencing, not a process-local lock, grants submission authority.
            await self.dispatch_one()
            item = await self.store.get_work(item.id)
        return item, reason

    def _expiry_reason(self, item: WorkItem, signal: dict[str, Any]) -> str | None:
        policy = self.config.execution
        now = datetime.now(UTC)
        age = (now - item.created_at).total_seconds()
        if age < -BROKER_CLOCK_SKEW_TOLERANCE_SECONDS or age > policy.entry_queue_max_age_seconds:
            return "Queued authorization expired; fresh operator approval is required."
        created = datetime.fromisoformat(signal["timestamp"]).replace(tzinfo=UTC)
        signal_age = (now - created).total_seconds()
        if signal_age < -BROKER_CLOCK_SKEW_TOLERANCE_SECONDS or signal_age > policy.signal_max_age_seconds:
            return "Signal is stale or clock-skewed; request a fresh scan."
        return None

    async def _preflight(self, item: WorkItem, request: OrderRequest, risk: AccountRiskSnapshot | None) -> str | None:
        policy = self.config.execution
        assert request.signal_id is not None
        signal = await self.store.db.get_signal_by_id(request.signal_id)
        if signal is None or signal["status"] != SignalStatus.SUBMITTING:
            return "Signal was removed, quarantined or changed after approval."
        if reason := self._expiry_reason(item, signal):
            return reason
        if await self.store.db.get_state(SystemStateKey.TRADING_HALTED) in ("true", "1", "yes"):
            return "Emergency trading halt active."
        if await self.store.legacy_claims():
            return "An older entry claim lacks a durable request ID; operator reconciliation is required."
        context = await self.broker.entry_market_context(request)
        if not context["simulated"]:
            quote_age = (datetime.now(UTC) - context["quote_timestamp"]).total_seconds()
            if quote_age < -BROKER_CLOCK_SKEW_TOLERANCE_SECONDS or quote_age > policy.entry_quote_max_age_seconds:
                return "Broker market-data observation is stale or clock-skewed."
            price = context["price"]
            if not math.isfinite(price) or price <= 0:
                return "Broker market-data price is invalid."
            assert request.entry_price is not None
            if abs(price / request.entry_price - 1) > policy.entry_max_price_drift_pct:
                return f"Market price changed to {price:g}; original approved limit {request.entry_price:g} retained."
            macro_reason = await self.macro_check(request, signal)
            if macro_reason:
                return macro_reason
        positions = await self.store.reservations(exclude_signal_id=request.signal_id)
        # Keep the larger of local reservations and broker exposure per symbol;
        # never omit broker-only holdings or outstanding external entry orders.
        exposure: dict[str, dict[str, Any]] = {p["contract"]: dict(p) for p in positions}
        for p in context["positions"]:
            symbol = p["symbol"]
            notional = abs(p["quantity"]) * max(p["entry_price"], p.get("current_price") or p["entry_price"])
            old = exposure.get(symbol, {})
            exposure[symbol] = {
                **p,
                "contract": symbol,
                "notional_value": max(notional, old.get("notional_value") or 0),
            }
        holdings = {p["symbol"]: p for p in context["positions"]}
        working_notional: dict[str, float] = {}
        for order in context["orders"]:
            symbol = order["symbol"]
            holding = holdings.get(symbol)
            closing_side = "sell" if holding and holding["direction"] == "LONG" else "buy"
            if holding and order["side"] == closing_side and float(order["quantity"]) <= abs(holding["quantity"]):
                continue
            price = order["limit_price"] or order["average_fill_price"]
            if not price or float(price) <= 0:
                return "Outstanding broker order has unbounded notional; reconcile it before new risk."
            remaining = max(0, float(order["quantity"]) - float(order["filled_quantity"]))
            working_notional[symbol] = working_notional.get(symbol, 0) + remaining * float(price)
        for symbol, notional in working_notional.items():
            holding = holdings.get(symbol)
            if holding:
                notional += abs(holding["quantity"]) * max(
                    holding["entry_price"], holding.get("current_price") or holding["entry_price"]
                )
            old = exposure.get(symbol, {})
            exposure[symbol] = {
                "contract": symbol,
                "asset_class": "EQUITY",
                "notional_value": max(notional, old.get("notional_value") or 0),
            }
        if reason := self._expiry_reason(item, signal):
            return reason
        if not context["simulated"]:
            if datetime.now(UTC) >= context["session_closes_at"]:
                return "Equity session ended during admission checks; request a new approval during market hours."
            final_age = (datetime.now(UTC) - context["quote_timestamp"]).total_seconds()
            if final_age < -BROKER_CLOCK_SKEW_TOLERANCE_SECONDS or final_age > policy.entry_quote_max_age_seconds:
                return "Market-data observation expired during admission checks; request a fresh approval."
        return reservation_rejection(
            request,
            list(exposure.values()),
            self.config,
            current_drawdown_pct=float(risk.drawdown_pct) if risk else 0.0,
        )

    async def dispatch_one(self) -> bool:
        item = await self.store.claim_entry(lease_seconds=self.config.execution.entry_preflight_lease_seconds)
        if item is None:
            return False
        request = OrderRequest.model_validate(item.payload)
        risk = None
        try:
            if requires_account_risk(self.config):
                if self.ledger is None:
                    raise ValueError("Observed account risk service is unavailable")
                await self.ledger.refresh()
                risk = await self.ledger.current_risk()
            rejection = await self._preflight(item, request, risk)
        except Exception as exc:
            logger.exception("Entry preflight failed: %s", item.id)
            rejection = f"Admission evidence unavailable ({type(exc).__name__}: {exc}). No order submitted."
        if rejection:
            await self.store.resolve_entry(item, OrderResult(success=False, error_message=rejection))
            return True
        if reason := await self.store.begin_submission(
            item, self.config, risk_fingerprint=risk.fingerprint if risk else None
        ):
            await self.store.resolve_entry(
                item,
                OrderResult(
                    success=False,
                    error_message=reason,
                ),
            )
            return True
        try:
            result = await self.executor.execute_order(request, self.broker)
        except Exception as exc:
            result = OrderResult(success=False, submission_uncertain=True, error_message=type(exc).__name__)
        # CancelledError intentionally leaves SUBMITTING. Recovery is lookup-only.
        if result.success and not result.order_id:
            result = OrderResult(
                success=False, submission_uncertain=True, error_message="Broker acknowledgement has no order ID"
            )
        await self.store.resolve_entry(item, result)
        return True

    async def recover(self) -> int:
        recovered = 0
        for item in await self.store.list_work(WorkKind.ENTRY, statuses=(WorkStatus.SUBMITTING, WorkStatus.UNKNOWN)):
            try:
                result = await self.broker.find_entry_order(OrderRequest.model_validate(item.payload))
                if result is not None:
                    recovered += await self.store.resolve_entry(item, result, recovery=True)
            except Exception:
                logger.exception("Exact entry recovery failed for %s", item.id)
        return recovered
