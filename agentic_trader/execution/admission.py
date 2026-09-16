"""Pure entry admission policy shared by transactional reservations and dispatch."""

import math
from typing import Any

from agentic_trader.broker.base import OrderRequest
from agentic_trader.config import AppConfig
from agentic_trader.constants import AssetClass, Direction


def reservation_rejection(request: OrderRequest, positions: list[dict[str, Any]], config: AppConfig) -> str | None:
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
    notional = request.entry_price * request.quantity * multiplier
    policy, sizing = config.portfolio, config.sizing
    if risk * request.quantity * multiplier > policy.cash * sizing.max_risk_pct_cap:
        return "Order exceeds the configured per-trade risk cap."
    if notional > sizing.max_trade_notional_cap:
        return "Order exceeds the configured per-trade notional cap."
    max_quantity = (
        sizing.max_contracts_per_trade if request.asset_class == AssetClass.FUTURES else sizing.max_shares_per_trade
    )
    if request.quantity > max_quantity:
        return "Order exceeds the configured quantity cap."
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
