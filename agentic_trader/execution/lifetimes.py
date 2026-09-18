"""Shared deadline assessment and durable, exact-order lifetime orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from agentic_trader.constants import CloseRequestStatus
from agentic_trader.execution.durable import OrderObservation, WorkItem, WorkKind, WorkStatus
from agentic_trader.execution.lifetime_policy import TradeLifetimePolicy, aware_utc


if TYPE_CHECKING:
    from agentic_trader.broker.base import BaseBroker
    from agentic_trader.config import ExecutionConfig
    from agentic_trader.execution.closing import PositionCloseService
    from agentic_trader.storage.lifetimes import LifetimeRepository


class LifetimeAction(StrEnum):
    NONE = "none"
    CANCEL_ENTRY = "cancel_entry"
    CLOSE_POSITION = "close_position"
    REVIEW = "review"


BRACKET_EXIT_COUNT = 2
UNFILLED_TERMINAL = frozenset({"canceled", "expired", "rejected"})
WORKING_ENTRY = frozenset({"new", "accepted", "pending_new", "accepted_for_bidding", "done_for_day"})


@dataclass(frozen=True)
class EntryIdentity:
    order_id: str
    client_order_id: str
    symbol: str
    side: str
    quantity: str

    def matches(self, order: OrderObservation) -> bool:
        return (
            bool(self.client_order_id)
            and all(
                getattr(self, field) == getattr(order, field)
                for field in ("order_id", "client_order_id", "symbol", "side")
            )
            and Decimal(self.quantity) == Decimal(order.quantity)
            and not order.replaces
            and not order.replaced_by
        )


@dataclass(frozen=True)
class LifetimeDecision:
    action: LifetimeAction
    reason: str
    deadline: datetime | None = None


def assess_lifetime(
    policy: TradeLifetimePolicy, identity: EntryIdentity, order: OrderObservation, now: datetime
) -> LifetimeDecision:
    now = aware_utc(now)
    quantity, filled = Decimal(order.quantity), Decimal(order.filled_quantity)
    if (
        not identity.matches(order)
        or not quantity.is_finite()
        or not filled.is_finite()
        or not 0 <= filled <= quantity
        or quantity <= 0
    ):
        return LifetimeDecision(LifetimeAction.REVIEW, "entry_identity_or_quantity_changed")
    if filled == quantity and order.status == "filled":
        if order.filled_at is None or aware_utc(order.filled_at) > now:
            return LifetimeDecision(LifetimeAction.REVIEW, "missing_or_future_fill_time")
        deadline = policy.holding_deadline(order.filled_at)
        if deadline is None:
            return LifetimeDecision(LifetimeAction.NONE, "holding_lifetime_disabled")
        return LifetimeDecision(
            LifetimeAction.CLOSE_POSITION if now >= deadline else LifetimeAction.NONE, "holding_lifetime", deadline
        )
    if filled:
        return LifetimeDecision(LifetimeAction.REVIEW, "partial_entry_requires_protection_review")
    if order.status in UNFILLED_TERMINAL:
        return LifetimeDecision(LifetimeAction.NONE, "unfilled_terminal")
    if order.status not in WORKING_ENTRY:
        return LifetimeDecision(LifetimeAction.REVIEW, "unconfirmed_order_transition")
    if order.submitted_at is None or aware_utc(order.submitted_at) > now:
        return LifetimeDecision(LifetimeAction.REVIEW, "missing_or_future_submission_time")
    deadline = policy.entry_deadline(order.submitted_at)
    if deadline is None:
        return LifetimeDecision(LifetimeAction.NONE, "resting_lifetime_disabled")
    return LifetimeDecision(
        LifetimeAction.CANCEL_ENTRY if now >= deadline else LifetimeAction.NONE, "resting_lifetime", deadline
    )


class TradeLifetimeService:
    """Coordinate only explicitly timed positions; historical policies have no timer."""

    def __init__(
        self,
        repository: LifetimeRepository,
        broker: BaseBroker,
        close_service: PositionCloseService,
        *,
        config: ExecutionConfig,
        clock: Callable[[], datetime] | None = None,
    ):
        self.repository, self.broker, self.close_service = repository, broker, close_service
        self.store = repository.store
        self.config = config
        self.clock = clock or (lambda: datetime.now(UTC))

    async def _recover_cancel(
        self, item: WorkItem, *, owner_finished: bool = False, transport_error: str | None = None
    ) -> None:
        deadline = time.monotonic() + self.config.close_cancel_timeout_seconds
        while True:
            # Recovery never reissues DELETE, including a crash before submission.
            # Give an independent submitting owner its bounded transport/confirmation window.
            created = item.created_at.replace(tzinfo=UTC) if item.created_at.tzinfo is None else item.created_at
            in_flight = (self.clock() - created).total_seconds() < (
                self.config.broker_request_timeout_seconds + self.config.close_cancel_timeout_seconds
            )
            allow_pending = (owner_finished and time.monotonic() < deadline) or (
                not owner_finished and item.status == WorkStatus.SUBMITTING and in_flight
            )
            reason = None
            try:
                orders = await self.broker.read_entry_group(
                    item.payload["identity"]["order_id"], tuple(item.payload["order_ids"])
                )
                await self.store.observe_orders(orders)
            except Exception as exc:
                orders = []
                reason = f"lookup_failed:{type(exc).__name__}"
            resolved = await self.repository.resolve_cancel(
                item, orders, reason=reason, transport_error=transport_error, allow_pending=allow_pending
            )
            if resolved or not owner_finished or not allow_pending:
                return
            await asyncio.sleep(self.config.close_cancel_poll_seconds)

    async def recover(self) -> None:
        for item in await self.store.list_work(
            WorkKind.ENTRY_CANCEL, statuses=(WorkStatus.SUBMITTING, WorkStatus.UNKNOWN)
        ):
            await self._recover_cancel(item)  # Read-only recovery, including a crash before DELETE.

    async def reconcile(self) -> None:
        await self.recover()
        unresolved = await self.store.list_work(
            WorkKind.ENTRY_CANCEL, statuses=(WorkStatus.SUBMITTING, WorkStatus.UNKNOWN)
        )
        blocked = {item.payload["signal_id"] for item in unresolved}
        for signal in await self.store.db.get_active_positions():
            if not signal.get("alpha_policy"):
                continue
            if "lifetime" not in signal["alpha_policy"] or signal["id"] in blocked:
                continue
            try:
                lifetime = TradeLifetimePolicy(**signal["alpha_policy"]["lifetime"])
                await self._reconcile_signal(signal, lifetime)
            except Exception as exc:
                await self.repository.review(signal["id"], f"lifecycle_evidence_unavailable:{type(exc).__name__}", {})

    async def resume_blockers(self) -> list[int]:
        """Fresh read-only checks: /resume cannot waive unresolved timed-position evidence."""
        blocked = []
        for signal in await self.store.db.get_active_positions():
            if "lifetime" not in (signal.get("alpha_policy") or {}):
                continue
            try:
                lifetime = TradeLifetimePolicy(**signal["alpha_policy"]["lifetime"])
                identity = await self._entry_identity(signal)
                if identity is None:
                    blocked.append(signal["id"])
                    continue
                orders = await self.broker.read_entry_group(identity.order_id)
                parent = next(o for o in orders if o.order_id == identity.order_id)
                decision = assess_lifetime(lifetime, identity, parent, self.clock())
                close = await self.store.db.get_close_request(self._holding_request_id(signal["id"], identity.order_id))
                if (
                    decision.action == LifetimeAction.REVIEW
                    or (close and close["status"] in (CloseRequestStatus.FAILED, CloseRequestStatus.UNKNOWN))
                    or any(0 < Decimal(o.filled_quantity) < Decimal(o.quantity) for o in orders)
                ):
                    blocked.append(signal["id"])
            except Exception:
                blocked.append(signal["id"])
        return blocked

    def _holding_request_id(self, signal_id: int, order_id: str) -> str:
        key = hashlib.sha256(f"{self.store.scope}/{signal_id}/{order_id}".encode()).hexdigest()[:32]
        return f"hold-{key}"

    async def _entry_identity(self, signal: dict[str, Any]) -> EntryIdentity | None:
        entry = await self.repository.entry(signal["id"])
        if entry is None or entry.result.get("order_id") != signal["broker_order_id"]:
            return None
        request = entry.payload
        return EntryIdentity(
            signal["broker_order_id"],
            request["client_order_id"],
            request["symbol"],
            request["side"].lower(),
            str(request["quantity"]),
        )

    async def _reconcile_signal(self, signal: dict[str, Any], lifetime: TradeLifetimePolicy) -> None:
        identity = await self._entry_identity(signal)
        if identity is None:
            await self.repository.review(signal["id"], "missing_durable_entry_identity", {})
            return
        orders = await self.broker.read_entry_group(identity.order_id)
        await self.store.observe_orders(orders)
        parent = next(o for o in orders if o.order_id == identity.order_id)
        decision = assess_lifetime(lifetime, identity, parent, self.clock())
        if decision.action == LifetimeAction.REVIEW:
            await self.repository.review(signal["id"], decision.reason, parent.model_dump(mode="json"))
        elif decision.action == LifetimeAction.CANCEL_ENTRY:
            # Never cancel an incomplete or foreign group. Alpaca brackets contain two exits.
            children = [o for o in orders if o.order_id != parent.order_id]
            if (
                parent.order_class != "bracket"
                or len(children) != BRACKET_EXIT_COUNT
                or any(
                    o.symbol != identity.symbol
                    or o.side == identity.side
                    or Decimal(o.quantity) != Decimal(identity.quantity)
                    or Decimal(o.filled_quantity) != 0
                    or o.replaces
                    or o.replaced_by
                    for o in children
                )
            ):
                await self.repository.review(signal["id"], "incomplete_or_changed_bracket_group", {})
                return
            item = await self.repository.begin_cancel(
                signal["id"], identity, orders, asdict(lifetime), now=self.clock()
            )
            if item:
                transport_error = None
                try:
                    await self.broker.cancel_order(identity.order_id)
                except Exception as exc:
                    transport_error = type(exc).__name__
                await self._recover_cancel(item, owner_finished=True, transport_error=transport_error)
        elif decision.action == LifetimeAction.CLOSE_POSITION and await self.broker.regular_session_open():
            children = [o for o in orders if o.order_id != identity.order_id]
            if signal.get("broker_exit_order_id") or any(
                o.status == "filled" and Decimal(o.filled_quantity) == Decimal(identity.quantity) for o in children
            ):
                return  # Existing exact exit reconciliation owns this transition.
            if any(Decimal(o.filled_quantity) > 0 for o in children):
                await self.repository.review(signal["id"], "partial_exit_requires_protection_review", {})
                return
            request_id = self._holding_request_id(signal["id"], identity.order_id)
            assert decision.deadline is not None
            await self.close_service.close_signal(
                signal["id"],
                request_id=request_id,
                context={
                    "reason": decision.reason,
                    "deadline": decision.deadline.isoformat(),
                    "lifetime": asdict(lifetime),
                },
            )
            record = await self.store.db.get_close_request(request_id)
            if not record:
                # A concurrent manual close or protective exit may already own this position.
                refreshed = await self.store.db.get_signal_by_id(signal["id"])
                active = await self.store.db.active_close_requests()
                if refreshed and (
                    refreshed.get("broker_exit_order_id") or any(row["signal_id"] == signal["id"] for row in active)
                ):
                    return
            if not record or record["status"] in (CloseRequestStatus.FAILED, CloseRequestStatus.UNKNOWN):
                await self.repository.review(signal["id"], "holding_close_requires_review", {"request_id": request_id})
