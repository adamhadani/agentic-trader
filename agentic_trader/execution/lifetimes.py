"""Shared deadline assessment and durable, exact-order lifetime orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal
from zoneinfo import ZoneInfo

from agentic_trader.constants import CloseRequestStatus, OrderType, SignalStatus
from agentic_trader.execution.capacity import protective_stop
from agentic_trader.execution.durable import OrderObservation, WorkItem, WorkKind, WorkStatus
from agentic_trader.execution.lifetime_policy import (
    LifetimePolicy,
    SessionEvidenceError,
    SessionLifetimePolicy,
    aware_utc,
    entry_lifetime_of,
)


if TYPE_CHECKING:
    from agentic_trader.broker.base import BaseBroker
    from agentic_trader.config import ExecutionConfig
    from agentic_trader.execution.closing import PositionCloseService
    from agentic_trader.market.session import MarketCalendarDay, MarketCalendarProtocol
    from agentic_trader.storage.lifetimes import LifetimeRepository

NEW_YORK = ZoneInfo("America/New_York")


class LifetimeAction(StrEnum):
    NONE = "none"
    CANCEL_ENTRY = "cancel_entry"
    CLOSE_POSITION = "close_position"
    REVIEW = "review"


BRACKET_EXIT_COUNT = 2
UNFILLED_TERMINAL = frozenset({"canceled", "expired", "rejected"})
WORKING_ENTRY = frozenset({"new", "accepted", "pending_new", "accepted_for_bidding", "done_for_day"})
# The only statuses a filled bracket's live exits show: Alpaca holds the stop while the take-profit works.
PROTECTED_EXIT = frozenset({"new", "held"})
# signals.id is a 32-bit Integer column: a larger id can never exist and would fail the lookup.
MAX_SIGNAL_ID = 2_147_483_647


# Why an entry_cancel work item exists: the resting deadline passed, or the operator asked.
CancelCause = Literal["deadline", "operator"]
INCOMPLETE_OR_CHANGED_GROUP = "incomplete_or_changed_bracket_group"
# A cancel the broker refused because the entry already filled with protection (WorkStatus.REJECTED).
FILLED_BEFORE_CANCEL = "filled_before_cancel"


@dataclass(frozen=True)
class RestingCancelResult:
    """An operator cancel's outcome; ``reason`` is a stable refusal token, never an exception message."""

    status: Literal["preview", "submitted", "refused"]
    reason: str | None
    signal_id: int
    symbol: str | None
    work_status: str | None


def parse_signal_id(text: str) -> int | None:
    """An operator-typed signal id: ASCII decimal digits (``int`` also takes "４２" or "٤٢") within the
    ``signals.id`` column's range."""
    if not (text.isascii() and text.isdecimal()) or len(text) > len(str(MAX_SIGNAL_ID)):
        return None
    value = int(text)
    return value if value <= MAX_SIGNAL_ID else None


def holding_calendar_span_days(holding_sessions: int) -> int:
    """Calendar days from the fill date that always cover ``holding_sessions`` regular sessions.

    Five sessions per seven days, plus two weeks of margin for holidays and closures.
    """
    return math.ceil(holding_sessions * 7 / 5) + 14


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
    policy: LifetimePolicy,
    identity: EntryIdentity,
    order: OrderObservation,
    now: datetime,
    sessions: Sequence[MarketCalendarDay] | None = None,
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
        deadline: datetime | None
        if isinstance(policy, SessionLifetimePolicy):
            if sessions is None:
                return LifetimeDecision(LifetimeAction.REVIEW, "session_calendar_unavailable")
            try:
                deadline = policy.holding_deadline(order.filled_at, sessions)
            except SessionEvidenceError as exc:
                return LifetimeDecision(LifetimeAction.REVIEW, str(exc))
        else:
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


def resting_entry_refusal(identity: EntryIdentity, parent: OrderObservation) -> str | None:
    """An operator cancel needs the exact, working, wholly unfilled parent.

    The deadline path gets the same guarantee from ``assess_lifetime`` returning CANCEL_ENTRY.
    """
    if not identity.matches(parent):
        return INCOMPLETE_OR_CHANGED_GROUP
    if Decimal(parent.filled_quantity) != 0 or parent.status not in WORKING_ENTRY:
        return "not_resting"
    return None


def bracket_group_refusal(identity: EntryIdentity, orders: Sequence[OrderObservation]) -> str | None:
    """Never cancel an incomplete or foreign group. Alpaca brackets contain two exits."""
    parent = next((o for o in orders if o.order_id == identity.order_id), None)
    children = [o for o in orders if o.order_id != identity.order_id]
    if (
        parent is None
        or parent.order_class != "bracket"
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
        return INCOMPLETE_OR_CHANGED_GROUP
    return None


def filled_before_cancel(identity: EntryIdentity, orders: Sequence[OrderObservation], order_ids: Sequence[str]) -> bool:
    """The DELETE lost a clean fill race: the exact parent fully filled and its exits protect it.

    Protection is the capacity rule (`protective_stop`) plus exactly one limit take-profit. Anything
    less (a partial fill; a missing, changed, filled, terminal or merely pending exit) is not this outcome.
    """
    by_id = {o.order_id: o for o in orders}
    parent = by_id.get(identity.order_id)
    exits = [o for o in orders if o.order_id != identity.order_id]
    return (
        parent is not None
        and identity.matches(parent)
        and parent.status == "filled"
        and Decimal(parent.filled_quantity) == Decimal(parent.quantity)
        and set(order_ids) <= by_id.keys()
        and bracket_group_refusal(identity, orders) is None
        and all(o.status in PROTECTED_EXIT for o in exits)
        and sum(o.order_type == OrderType.LIMIT.lower() for o in exits) == 1
        and protective_stop(exits, side=identity.side, quantity=Decimal(identity.quantity)) is not None
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
        calendar: MarketCalendarProtocol | None = None,
    ):
        self.repository, self.broker, self.close_service = repository, broker, close_service
        self.store = repository.store
        self.config = config
        self.clock = clock or (lambda: datetime.now(UTC))
        self.calendar = calendar

    async def _sessions(self, lifetime: LifetimePolicy, parent: OrderObservation) -> list | None:
        """Broker sessions from the fill date; ``None`` when this policy counts no sessions."""
        if not isinstance(lifetime, SessionLifetimePolicy) or parent.filled_at is None:
            return None
        if self.calendar is None:
            raise RuntimeError("Session-counted lifetime requires a market calendar")
        start = aware_utc(parent.filled_at).astimezone(NEW_YORK).date()
        span = timedelta(days=holding_calendar_span_days(lifetime.holding_sessions))
        days = await self.calendar.get_calendar_range(start, start + span)
        return [day for day in days if day.is_trading_day]

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
            if signal["id"] in blocked:
                continue
            try:
                lifetime = entry_lifetime_of(signal)
                if lifetime is None:
                    continue
                await self._reconcile_signal(signal, lifetime)
            except Exception as exc:
                await self.repository.review(signal["id"], f"lifecycle_evidence_unavailable:{type(exc).__name__}", {})

    async def resume_blockers(self) -> list[int]:
        """Fresh read-only checks: /resume cannot waive unresolved timed-position evidence."""
        blocked = []
        for signal in await self.store.db.get_active_positions():
            try:
                lifetime = entry_lifetime_of(signal)
                if lifetime is None:
                    continue
                identity = await self._entry_identity(signal)
                if identity is None:
                    blocked.append(signal["id"])
                    continue
                orders = await self.broker.read_entry_group(identity.order_id)
                parent = next(o for o in orders if o.order_id == identity.order_id)
                sessions = await self._sessions(lifetime, parent)
                decision = assess_lifetime(lifetime, identity, parent, self.clock(), sessions)
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

    async def _submit_cancel(self, item: WorkItem, identity: EntryIdentity) -> None:
        """The one DELETE for a claimed entry_cancel, never retried; exact lookup resolves it."""
        transport_error = None
        try:
            await self.broker.cancel_order(identity.order_id)
        except Exception as exc:
            transport_error = type(exc).__name__
        await self._recover_cancel(item, owner_finished=True, transport_error=transport_error)

    async def cancel_resting_entry(self, signal_id: int, *, confirm: bool) -> RestingCancelResult:
        """Operator cancel of one resting desk entry, timed or not: a preview, or one DELETE.

        No deadline or lifetime policy is consulted, so an untimed historical entry or a malformed
        marker can still be cancelled. Every other fence holds, and the claim rechecks them under
        the scope lock. Refusals never halt; an unconfirmed outcome halts through ``resolve_cancel``.
        """
        signal = await self.store.db.get_signal_by_id(signal_id)
        if signal is None:
            return RestingCancelResult("refused", "unknown_signal", signal_id, None, None)
        symbol = signal.get("contract")

        def refused(reason: str, work_status: str | None = None) -> RestingCancelResult:
            return RestingCancelResult("refused", reason, signal_id, symbol, work_status)

        if (existing := await self.repository.cancel_item(signal_id)) is not None:
            if existing.status == WorkStatus.REJECTED:
                return refused(FILLED_BEFORE_CANCEL, existing.status)
            return refused("cancel_in_progress", existing.status)
        if (
            signal["status"] != SignalStatus.EXECUTED
            or signal.get("executed_at") is not None
            or not signal.get("broker_order_id")
        ):
            return refused("not_resting")
        if not self.broker.supports_entry_cancellation:
            return refused("broker_cannot_cancel")
        identity = await self._entry_identity(signal)
        if identity is None:
            return refused("no_durable_entry")
        if any(row["symbol"] == identity.symbol for row in await self.store.db.active_close_requests()):
            return refused("close_active")
        try:
            orders = await self.broker.read_entry_group(identity.order_id)
        except Exception as exc:
            return refused(f"entry_group_unavailable:{type(exc).__name__}")
        if confirm:
            # A preview writes nothing; the confirming call re-reads and journals the evidence.
            await self.store.observe_orders(orders)
        parent = next((o for o in orders if o.order_id == identity.order_id), None)
        if parent is None:
            return refused(INCOMPLETE_OR_CHANGED_GROUP)
        if refusal := resting_entry_refusal(identity, parent) or bracket_group_refusal(identity, orders):
            return refused(refusal)
        if not confirm:
            return RestingCancelResult("preview", None, signal_id, identity.symbol, None)
        claim = await self.repository.begin_cancel(
            signal_id, identity, orders, None, now=self.clock(), reason="operator"
        )
        if not isinstance(claim, WorkItem):
            return refused(claim)
        await self._submit_cancel(claim, identity)
        resolved = await self.repository.cancel_item(signal_id)
        return RestingCancelResult("submitted", None, signal_id, identity.symbol, resolved.status if resolved else None)

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

    async def _reconcile_signal(self, signal: dict[str, Any], lifetime: LifetimePolicy) -> None:
        identity = await self._entry_identity(signal)
        if identity is None:
            await self.repository.review(signal["id"], "missing_durable_entry_identity", {})
            return
        orders = await self.broker.read_entry_group(identity.order_id)
        await self.store.observe_orders(orders)
        parent = next(o for o in orders if o.order_id == identity.order_id)
        decision = assess_lifetime(lifetime, identity, parent, self.clock(), await self._sessions(lifetime, parent))
        if decision.action == LifetimeAction.REVIEW:
            await self.repository.review(signal["id"], decision.reason, parent.model_dump(mode="json"))
        elif decision.action == LifetimeAction.CANCEL_ENTRY:
            if refusal := bracket_group_refusal(identity, orders):
                await self.repository.review(signal["id"], refusal, {})
                return
            claim = await self.repository.begin_cancel(
                signal["id"], identity, orders, asdict(lifetime), now=self.clock()
            )
            if isinstance(claim, WorkItem):
                await self._submit_cancel(claim, identity)
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
