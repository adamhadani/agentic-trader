"""Pure capacity assessment from fresh broker evidence and durable reservations.

This bounds planned stop risk and snapshot exposure, not gap loss or portfolio CVaR.
Broker capacity is already net of its working entries. The durable FIFO gives the
head funding priority; later queued approvals retain local risk reservations but
must acquire their own fresh funding evidence before submission.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from alpaca.trading.enums import OrderStatus
from pydantic import BaseModel, ConfigDict

from agentic_trader.accounting.ledger import decimal
from agentic_trader.constants import (
    BROKER_CLOCK_SKEW_TOLERANCE_SECONDS,
    BROKER_PRICE_TOLERANCE,
    AssetClass,
    Direction,
    OrderClass,
    OrderType,
    SignalStatus,
    TimeInForce,
)
from agentic_trader.execution.admission import reservation_rejection
from agentic_trader.risk import risk_capital


if TYPE_CHECKING:
    from agentic_trader.accounting.risk import AccountRiskSnapshot
    from agentic_trader.broker.base import BrokerEntryContext, OrderRequest
    from agentic_trader.config import AppConfig
    from agentic_trader.execution.durable import OrderObservation


# External broker contract, not operator-adjustable strategy parameters.
SHORT_BUYING_POWER_MULTIPLIER: Final = Decimal("1.03")
MINIMUM_MARGIN_EQUITY: Final = Decimal(2000)
WORKING_STATUSES: Final = frozenset((OrderStatus.NEW, OrderStatus.ACCEPTED, OrderStatus.HELD, OrderStatus.PENDING_NEW))
TERMINAL_STATUSES: Final = frozenset(
    (OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.EXPIRED, OrderStatus.REJECTED, OrderStatus.REPLACED)
)


class CapacityAssessment(BaseModel):
    model_config = ConfigDict(frozen=True)

    required_buying_power: Decimal
    available_buying_power: Decimal
    effective_risk_capital: Decimal
    aggregate_stop_risk: Decimal
    aggregate_notional: Decimal
    position_count: int


def _fresh(at: datetime, now: datetime, max_age: float, name: str) -> None:
    age = (now - at).total_seconds()
    if not -BROKER_CLOCK_SKEW_TOLERANCE_SECONDS <= age <= max_age:
        raise ValueError(f"{name} is stale, expired or clock-skewed; request fresh approval")


def _group(root: str, orders: dict[str, OrderObservation]) -> set[str]:
    """Exact parent/replacement lineage, never symbol-based exit inference."""
    found = {root}
    while True:
        expanded = found | {o.order_id for o in orders.values() if o.parent_order_id in found or o.replaces in found}
        if expanded == found:
            return found
        found = expanded


def _exposure(reservations: list[dict[str, Any]], context: BrokerEntryContext) -> list[dict[str, Any]]:
    orders = {o.order_id: o for o in context.orders}
    holdings = {p.symbol: p for p in context.positions}
    if len(orders) != len(context.orders) or len(holdings) != len(context.positions):
        raise ValueError("Duplicate broker exposure identities; reconciliation required")
    covered_orders: set[str] = set()
    covered_holdings: set[str] = set()
    exposure = []
    for reservation in reservations:
        row = dict(reservation)
        symbol = row["contract"]
        root_id = row.get("broker_order_id")
        if not root_id:
            if row.get("status") != SignalStatus.SUBMITTING:
                raise ValueError("Tracked exposure lacks exact broker entry identity")
            exposure.append(row)
            continue
        root = orders.get(root_id)
        side = "buy" if row["direction"] == Direction.LONG else "sell"
        if root is None or root.symbol != symbol or root.side != side:
            raise ValueError("Tracked exposure differs from exact broker entry identity")
        quantity, filled = decimal(root.quantity), decimal(root.filled_quantity)
        if quantity <= 0 or quantity != decimal(row["quantity"]) or not 0 <= filled <= quantity:
            raise ValueError("Broker exposure quantity differs from its full reserved commitment")
        if root.status == OrderStatus.PARTIALLY_FILLED or 0 < filled < quantity:
            raise ValueError("Partial entry exposure requires protection and ownership reconciliation")
        family = _group(root_id, orders)
        if family & covered_orders:
            raise ValueError("Multiple reservations claim the same broker exposure")
        covered_orders |= family
        if any(o.symbol != symbol for identity, o in orders.items() if identity in family):
            raise ValueError("Broker protection lineage has inconsistent symbol identity")
        if any(decimal(orders[identity].filled_quantity) > 0 for identity in family - {root_id}):
            raise ValueError("Historical or partial exit execution requires ownership reconciliation")
        if root.status in WORKING_STATUSES and filled == 0:
            if symbol in holdings:
                raise ValueError("Pending entry overlaps existing broker exposure")
            if any(
                orders[identity].status not in (OrderStatus.HELD, OrderStatus.PENDING_NEW)
                for identity in family - {root_id}
            ):
                raise ValueError("Pending entry has unexpected active protection; reconcile exposure")
            if not root.limit_price or decimal(root.limit_price) <= 0:
                raise ValueError("Pending broker exposure has no bounded approved limit")
            legs = [orders[identity] for identity in family - {root_id}]
            stops = [leg for leg in legs if leg.order_type == OrderType.STOP.lower()]
            profits = [leg for leg in legs if leg.order_type == OrderType.LIMIT.lower()]
            if (
                root.order_class != OrderClass.BRACKET.lower()
                or root.order_type != OrderType.LIMIT.lower()
                or decimal(root.limit_price) != decimal(row["entry_price"])
                or len(legs) != 2
                or len(stops) != 1
                or len(profits) != 1
                or any(leg.side == side or decimal(leg.quantity) != quantity for leg in legs)
                or not stops[0].stop_price
                or not profits[0].limit_price
                or decimal(stops[0].stop_price) != decimal(row["stop_loss"])
                or decimal(profits[0].limit_price) != decimal(row["take_profit"])
            ):
                raise ValueError("Pending entry no longer matches its exact approved bracket/protection")
            row["notional_value"] = max(decimal(row["notional_value"]), quantity * decimal(root.limit_price))
        elif root.status == OrderStatus.FILLED and filled == quantity:
            holding = holdings.get(symbol)
            if (
                holding is None
                or symbol in covered_holdings
                or holding.direction != row["direction"]
                or abs(decimal(holding.quantity)) != quantity
                or holding.asset_class != AssetClass.EQUITY
            ):
                raise ValueError("Filled entry and broker exposure disagree; reconcile ownership")
            if (
                not root.average_fill_price
                or decimal(root.average_fill_price) <= 0
                or abs(decimal(root.average_fill_price) - decimal(holding.entry_price))
                > decimal(BROKER_PRICE_TOLERANCE)
            ):
                raise ValueError("Broker inventory basis differs from confirmed entry execution; reconcile ownership")
            covered_holdings.add(symbol)
            mark = decimal(holding.current_price)
            if mark <= 0:
                raise ValueError("Broker exposure has no valid market mark")
            stops = [
                orders[identity]
                for identity in family - {root_id}
                if orders[identity].order_type in (OrderType.STOP.lower(), OrderType.STOP_LIMIT.lower())
                and orders[identity].status == OrderStatus.NEW
            ]
            if len(stops) != 1:
                raise ValueError("Exact active stop protection is unavailable for existing exposure")
            stop = stops[0]
            if (
                stop.side == side
                or decimal(stop.quantity) != quantity
                or decimal(stop.filled_quantity) != 0
                or not stop.stop_price
                or decimal(stop.stop_price) <= 0
            ):
                raise ValueError("Exact protection differs from the broker exposure")
            distance = (mark - decimal(stop.stop_price)) * (1 if side == "buy" else -1)
            if distance <= 0:
                raise ValueError("Broker mark has reached stop protection; reconcile before new exposure")
            for identity in family - {root_id}:
                leg = orders[identity]
                if leg.status not in TERMINAL_STATUSES and (
                    leg.status not in WORKING_STATUSES
                    or leg.side == side
                    or decimal(leg.quantity) != quantity
                    or decimal(leg.filled_quantity) != 0
                ):
                    raise ValueError("Unresolved or partial protection requires exposure reconciliation")
            row["notional_value"] = max(
                decimal(row["notional_value"]), quantity * max(mark, decimal(holding.entry_price))
            )
            row["risk_dollars"] = max(decimal(row["risk_dollars"]), quantity * distance)
        else:
            raise ValueError("Unresolved broker entry exposure requires reconciliation")
        exposure.append(row)
    if set(holdings) - covered_holdings:
        raise ValueError("Untracked broker exposure requires ownership and protection review")
    if any(o.status not in TERMINAL_STATUSES for identity, o in orders.items() if identity not in covered_orders):
        raise ValueError("Unrelated broker order exposure requires reconciliation; closing intent cannot be inferred")
    return exposure


def assess_entry_capacity(
    request: OrderRequest,
    reservations: list[dict[str, Any]],
    context: BrokerEntryContext,
    config: AppConfig,
    *,
    account_risk: AccountRiskSnapshot | None = None,
    now: datetime | None = None,
) -> CapacityAssessment:
    now = now or datetime.now(UTC)
    policy = config.execution
    _fresh(context.requested_at, now, policy.entry_evidence_max_age_seconds, "Broker account/book evidence")
    _fresh(context.observed_at, now, policy.entry_evidence_max_age_seconds, "Broker receipt")
    _fresh(context.trade_timestamp, now, policy.entry_quote_max_age_seconds, "Market trade")
    _fresh(context.quote.timestamp, now, policy.entry_quote_max_age_seconds, "Market quote")
    if now >= context.session_closes_at:
        raise ValueError("Equity session ended; request new approval during market hours")
    if (
        request.asset_class != AssetClass.EQUITY
        or request.order_type != OrderType.LIMIT
        or request.order_class != OrderClass.BRACKET
        or request.time_in_force not in (TimeInForce.DAY, TimeInForce.GTC)
        or not float(request.quantity).is_integer()
    ):
        raise ValueError("Broker admission supports whole-share equity limit brackets with DAY/GTC only")
    asset = context.asset
    accounts = (context.account_before, context.account)
    if asset.symbol != request.symbol or context.quote.symbol != request.symbol:
        raise ValueError("Broker asset/quote identity differs from approved symbol")
    if asset.asset_class != "us_equity":
        raise ValueError("Broker asset is not a supported equity")
    if asset.status != "active" or not asset.tradable:
        raise ValueError("Broker asset must be active and tradable")
    for account in accounts:
        if account.account_id != context.account.account_id or (
            account_risk and account.account_id != account_risk.account_id
        ):
            raise ValueError("Broker account identity differs from reconciled risk")
        if account.status != "ACTIVE":
            raise ValueError("Broker account is not active")
        if account.currency != "USD":
            raise ValueError("Broker funding requires USD account evidence")
        if account.trading_blocked or account.account_blocked or account.trade_suspended_by_user:
            raise ValueError("Broker account trading is blocked or suspended")
    equity = min(*(a.equity for a in accounts), account_risk.equity if account_risk else context.account.equity)
    capital = risk_capital(config.portfolio.cash, float(equity))
    available = min(*(a.buying_power for a in accounts), *(a.regt_buying_power for a in accounts))
    ask = context.quote.ask_price
    assert request.entry_price is not None
    price = decimal(request.entry_price)
    if request.direction == Direction.SHORT:
        if (
            any(not a.shorting_enabled or a.multiplier <= 1 or a.equity < MINIMUM_MARGIN_EQUITY for a in accounts)
            or not asset.marginable
            or not asset.shortable
            or asset.borrow_status != "easy_to_borrow"
        ):
            raise ValueError(
                "Short entry requires enabled margin and current easy-to-borrow evidence; locates unsupported"
            )
        price = max(price, ask * SHORT_BUYING_POWER_MULTIPLIER)
    else:
        price = max(price, ask)
        if not asset.marginable:
            available = min(available, *(a.non_marginable_buying_power for a in accounts))
    required = price * decimal(request.quantity)
    if required > available:
        raise ValueError(
            f"Insufficient observed buying power: need ${required:.2f}, available ${available:.2f}; request smaller sizing"
        )
    exposure = _exposure(reservations, context)
    drawdown = float(account_risk.drawdown_pct) if account_risk else 0.0
    exposure_price = max(decimal(request.entry_price), context.quote.ask_price, context.price)
    if reason := reservation_rejection(
        request,
        exposure,
        config,
        current_drawdown_pct=drawdown,
        current_equity=float(equity),
        current_price=float(exposure_price),
    ):
        raise ValueError(reason)
    candidate_risk = abs(decimal(request.entry_price) - decimal(request.stop_loss)) * decimal(request.quantity)
    return CapacityAssessment(
        required_buying_power=required,
        available_buying_power=available,
        effective_risk_capital=decimal(capital),
        aggregate_stop_risk=sum((decimal(p["risk_dollars"]) for p in exposure), candidate_risk),
        aggregate_notional=sum(
            (decimal(p["notional_value"]) for p in exposure), exposure_price * decimal(request.quantity)
        ),
        position_count=len(exposure) + 1,
    )
