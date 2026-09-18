"""Application service for durable entry authorization, admission and recovery."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agentic_trader.accounting.risk import AccountRiskSnapshot
from agentic_trader.accounting.service import AccountLedgerService
from agentic_trader.broker.base import (
    BaseBroker,
    BrokerEntryContext,
    EntryContext,
    OrderRequest,
    OrderResult,
    SimulatedEntryContext,
)
from agentic_trader.config import AppConfig
from agentic_trader.constants import SignalStatus, SystemStateKey
from agentic_trader.execution.admission import authorization_expiry, reservation_rejection
from agentic_trader.execution.capacity import assess_entry_capacity
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
        created = datetime.fromisoformat(signal["timestamp"]).replace(tzinfo=UTC)
        return authorization_expiry(item.created_at, created, self.config, now=datetime.now(UTC))

    async def _preflight(
        self,
        item: WorkItem,
        request: OrderRequest,
        risk: AccountRiskSnapshot | None,
    ) -> tuple[str | None, EntryContext | None]:
        policy = self.config.execution
        assert request.signal_id is not None
        signal = await self.store.db.get_signal_by_id(request.signal_id)
        if signal is None or signal["status"] != SignalStatus.SUBMITTING:
            return "Signal was removed, quarantined or changed after approval.", None
        if reason := self._expiry_reason(item, signal):
            return reason, None
        if await self.store.db.get_state(SystemStateKey.TRADING_HALTED) in ("true", "1", "yes"):
            return "Emergency trading halt active.", None
        if await self.store.legacy_claims():
            return "An older entry claim lacks a durable request ID; operator reconciliation is required.", None
        reservations = await self.store.reservations(exclude_signal_id=request.signal_id)
        context = await self.broker.entry_market_context(
            request,
            entry_order_ids=tuple(sorted({p["broker_order_id"] for p in reservations if p.get("broker_order_id")})),
        )
        if isinstance(context, BrokerEntryContext):
            assert request.entry_price is not None
            if abs(float(context.price) / request.entry_price - 1) > policy.entry_max_price_drift_pct:
                return (
                    f"Market price changed to {context.price:g}; original approved limit {request.entry_price:g} retained.",
                    context,
                )
            if reason := await self.macro_check(request, signal):
                return reason, context
            try:
                assess_entry_capacity(
                    request, reservations, context, self.config, account_risk=risk, now=datetime.now(UTC)
                )
            except (ValueError, TypeError, ArithmeticError) as exc:
                return str(exc), context
        elif isinstance(context, SimulatedEntryContext) and not requires_account_risk(self.config):
            if reason := reservation_rejection(request, reservations, self.config):
                return reason, context
        else:
            return "Broker capacity evidence is unavailable or has an unsupported contract.", None
        return self._expiry_reason(item, signal), context

    async def dispatch_one(self) -> bool:
        item = await self.store.claim_entry(lease_seconds=self.config.execution.entry_preflight_lease_seconds)
        if item is None:
            return False
        request = OrderRequest.model_validate(item.payload)
        risk = None
        context = None
        try:
            if requires_account_risk(self.config):
                if self.ledger is None:
                    raise ValueError("Observed account risk service is unavailable")
                await self.ledger.refresh()
                risk = await self.ledger.current_risk()
            rejection, context = await self._preflight(item, request, risk)
        except Exception as exc:
            logger.exception("Entry preflight failed: %s", item.id)
            rejection = f"Admission evidence unavailable ({type(exc).__name__}: {exc}). No order submitted."
        if rejection:
            await self.store.resolve_entry(
                item,
                OrderResult(
                    success=False,
                    error_message=rejection,
                    raw_response={"broker_context": context.model_dump(mode="json") if context else None},
                ),
            )
            return True
        if reason := await self.store.begin_submission(
            item,
            self.config,
            risk_fingerprint=risk.fingerprint if risk else None,
            broker_context=context if isinstance(context, BrokerEntryContext) else None,
        ):
            await self.store.resolve_entry(
                item,
                OrderResult(
                    success=False,
                    error_message=reason,
                    raw_response={"broker_context": context.model_dump(mode="json") if context else None},
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
