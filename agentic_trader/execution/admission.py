"""Pure entry admission policy shared by transactional reservations and dispatch."""

import math
from datetime import UTC, datetime
from typing import Any

from agentic_trader.broker.base import OrderRequest
from agentic_trader.config import AppConfig
from agentic_trader.constants import BROKER_CLOCK_SKEW_TOLERANCE_SECONDS, AssetClass, Direction, OrderSide
from agentic_trader.risk import Book, EntryIntent, RiskLimits, admission_gates, per_trade_risk_budget


_INVALID_REQUEST_NUMBERS = "Quantity and bracket prices must be finite and positive."


def authorization_expiry(
    approved_at: datetime, signal_at: datetime, config: AppConfig, *, now: datetime | None = None
) -> str | None:
    """One deadline policy for remote preflight and the final locked transaction."""
    now = now or datetime.now(UTC)
    age = (now - approved_at).total_seconds()
    if not -BROKER_CLOCK_SKEW_TOLERANCE_SECONDS <= age <= config.execution.entry_queue_max_age_seconds:
        return "Queued authorization expired; fresh operator approval is required."
    signal_age = (now - signal_at).total_seconds()
    if not -BROKER_CLOCK_SKEW_TOLERANCE_SECONDS <= signal_age <= config.execution.signal_max_age_seconds:
        return "Signal is stale or clock-skewed; request a fresh scan."
    return None


def reservation_rejection(
    request: OrderRequest,
    positions: list[dict[str, Any]],
    config: AppConfig,
    *,
    current_drawdown_pct: float = 0.0,
    current_equity: float | None = None,
    current_price: float | None = None,
) -> str | None:
    """Admission's verdict on one entry request: the first rejection's reason, or ``None``.

    Before the shared rules, the broker side must match the direction, a futures symbol
    must have a configured multiplier, and a configured multiplier must be finite and
    positive (a configuration fault, reported as such). ``EntryIntent`` validates the
    request's quantity, bracket prices and ``current_price`` (finite and positive), and
    ``per_trade_risk_budget`` derives the per-trade budget from ``current_equity`` and
    ``current_drawdown_pct``; either failure is a rejection. Every remaining rule is
    ``agentic_trader.risk.admission_gates`` over the book of ``positions`` (``signals`` rows;
    ``SUBMITTING`` rows count as reservations), in its fixed order: ``exposure_known``,
    ``drawdown_halt``, ``reward_risk`` at the configured minimum (the tap gate and the
    preflight macro check apply the regime threshold), ``per_trade_risk``,
    ``per_trade_notional``, ``quantity_cap``, ``aggregate_stop_risk``,
    ``concurrent_positions``, ``same_symbol``, ``portfolio_notional``,
    ``asset_class_notional``, ``correlation_group``. Notional is priced at
    ``max(entry, current_price)``, or at the entry when no current price is known.
    """
    if request.direction not in (Direction.LONG, Direction.SHORT) or str(request.side).upper() != (
        OrderSide.BUY if request.direction == Direction.LONG else OrderSide.SELL
    ):
        return "Order direction and broker side disagree."
    info = config.contracts.get(request.symbol)
    if info is None and request.asset_class == AssetClass.FUTURES:
        return "Futures instrument multiplier is not configured."
    multiplier = info.multiplier if info else 1.0
    if multiplier is None or not math.isfinite(multiplier) or multiplier <= 0:
        return "Configured instrument multiplier must be finite and positive."
    entry, stop, target = request.entry_price, request.stop_loss, request.take_profit
    if entry is None or stop is None or target is None:
        return _INVALID_REQUEST_NUMBERS
    try:
        intent = EntryIntent(
            symbol=request.symbol,
            direction=str(request.direction),
            asset_class=str(request.asset_class),
            quantity=request.quantity,
            entry=entry,
            stop=stop,
            target=target,
            multiplier=multiplier,
            current_price=current_price,
        )
    except ValueError:
        if current_price is not None and (not math.isfinite(current_price) or current_price <= 0):
            return "Current exposure price must be finite and positive."
        return _INVALID_REQUEST_NUMBERS
    limits = RiskLimits.from_config(config)
    try:
        budget = per_trade_risk_budget(limits, equity=current_equity, drawdown_pct=current_drawdown_pct)
    except ValueError as exc:
        return f"Account risk inputs invalid: {exc}."
    rejection = admission_gates(intent, Book.from_signal_rows(positions, reservations=True), budget, limits)
    return str(rejection) if rejection else None
