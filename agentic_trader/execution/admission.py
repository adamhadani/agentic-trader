"""Pure entry admission policy shared by transactional reservations and dispatch."""

import math
from datetime import UTC, datetime
from typing import Any

from agentic_trader.broker.base import OrderRequest
from agentic_trader.config import AppConfig
from agentic_trader.constants import BROKER_CLOCK_SKEW_TOLERANCE_SECONDS, AssetClass, Direction, OrderSide
from agentic_trader.risk import drawdown_risk_factor, risk_capital


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
    if request.direction not in (Direction.LONG, Direction.SHORT) or str(request.side).upper() != (
        OrderSide.BUY if request.direction == Direction.LONG else OrderSide.SELL
    ):
        return "Order direction and broker side disagree."
    numbers = (request.quantity, request.entry_price, request.stop_loss, request.take_profit)
    if any(n is None or not math.isfinite(n) or n <= 0 for n in numbers):
        return "Quantity and bracket prices must be finite and positive."
    assert request.entry_price is not None and request.stop_loss is not None and request.take_profit is not None
    sign = 1 if request.direction == Direction.LONG else -1
    risk = (request.entry_price - request.stop_loss) * sign
    reward = (request.take_profit - request.entry_price) * sign
    if risk <= 0 or reward / risk < config.risk.min_risk_reward_ratio:
        return "Bracket direction or configured risk/reward requirement is no longer valid."
    info = config.contracts.get(request.symbol)
    if info is None and request.asset_class == AssetClass.FUTURES:
        return "Futures instrument multiplier is not configured."
    multiplier = info.multiplier if info else 1
    if current_price is not None and (not math.isfinite(current_price) or current_price <= 0):
        return "Current exposure price must be finite and positive."
    notional = max(request.entry_price, current_price or request.entry_price) * request.quantity * multiplier
    policy, sizing = config.portfolio, config.sizing
    capital = risk_capital(policy.cash, current_equity)
    factor = drawdown_risk_factor(current_drawdown_pct, sizing)
    if factor == 0:
        return f"Account drawdown {current_drawdown_pct:.1%} reaches the configured sizing halt; new entries blocked."
    if risk * request.quantity * multiplier > capital * sizing.max_risk_pct_cap * factor:
        if factor < 1:
            return "Order exceeds the drawdown-adjusted per-trade risk cap; request a fresh scan for smaller sizing."
        return "Order exceeds the configured per-trade risk cap."
    if notional > sizing.max_trade_notional_cap:
        return "Order exceeds the configured per-trade notional cap."
    max_quantity = (
        sizing.max_contracts_per_trade if request.asset_class == AssetClass.FUTURES else sizing.max_shares_per_trade
    )
    if request.quantity > max_quantity:
        return "Order exceeds the configured quantity cap."
    for position in positions:
        for field in ("notional_value", "risk_dollars"):
            value = position.get(field)
            if value is None or not math.isfinite(float(value)) or float(value) < 0:
                return "Existing exposure is unknown or invalid; reconcile it before new risk."
    if sum(float(p["risk_dollars"]) for p in positions) + risk * request.quantity * multiplier > (
        capital * policy.max_stop_risk_pct * factor
    ):
        return "Order would breach the aggregate planned stop-risk budget, including reservations."
    if len(positions) >= policy.max_concurrent_positions:
        return f"Maximum concurrent positions ({policy.max_concurrent_positions}) reached, including reservations."
    if any(p.get("contract", p.get("symbol")) == request.symbol for p in positions):
        return "Symbol already has a position or entry reservation; adding/netting requires a separate reviewed plan."
    if sum(float(p.get("notional_value") or 0) for p in positions) + notional > policy.max_notional_exposure:
        return "Order would breach the maximum portfolio notional ceiling, including reservations."
    class_cap = {
        AssetClass.EQUITY: policy.max_equity_exposure,
        AssetClass.FUTURES: policy.max_futures_exposure,
        AssetClass.CRYPTO: policy.max_crypto_exposure,
    }[request.asset_class]
    class_notional = sum(
        float(p.get("notional_value") or 0)
        for p in positions
        if str(p.get("asset_class", "")).upper() == request.asset_class
    )
    if class_notional + notional > class_cap:
        return "Order would breach the configured asset-class notional ceiling."
    for symbols in policy.correlation_groups.values():
        if (
            request.symbol in symbols
            and sum(p.get("contract", p.get("symbol")) in symbols for p in positions) >= policy.max_correlated_positions
        ):
            return "Configured correlated-position limit reached."
    return None
