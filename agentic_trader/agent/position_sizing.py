from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agentic_trader.constants import AssetClass


if TYPE_CHECKING:
    from agentic_trader.config import AppConfig
    from agentic_trader.scanner.models import ScreenerCandidate

logger = logging.getLogger(__name__)


def compute_fractional_kelly_multiplier(
    win_rate: float,
    payoff_ratio: float,
    fraction: float = 0.5,
    baseline_win_rate: float = 0.50,
    min_multiplier: float = 0.50,
    max_multiplier: float = 2.00,
) -> float:
    """Calculate Fractional Kelly sizing multiplier based on win rate and payoff ratio (R:R).

    Full Kelly fraction f* = (p * (b + 1) - 1) / b = p - (1 - p) / b
    where p is win probability, b is win/loss payoff ratio (R:R).
    """
    if payoff_ratio <= 0:
        return 1.0

    p = max(0.01, min(0.99, win_rate))
    b = max(0.1, payoff_ratio)

    full_kelly = p - ((1.0 - p) / b)
    if full_kelly <= 0:
        # Negative expectancy trade under Kelly formula
        return min_multiplier

    fractional_kelly = full_kelly * fraction

    # Compare against baseline half-Kelly with p=baseline_win_rate, b=2.0 (f* = 0.25, half = 0.125)
    baseline_full = baseline_win_rate - ((1.0 - baseline_win_rate) / 2.0)
    baseline_fractional = max(0.05, baseline_full * fraction)

    raw_multiplier = fractional_kelly / baseline_fractional
    return max(min_multiplier, min(max_multiplier, round(raw_multiplier, 2)))


def calculate_position_size(
    candidate: ScreenerCandidate,
    stop_distance: float,
    target_distance: float,
    multiplier: float,
    asset_class: AssetClass,
    config: AppConfig,
) -> tuple[float, float, float]:
    """Calculate position quantity, dollar risk, and dollar reward according to configured sizing mode.

    Returns:
        tuple of (quantity, risk_dollars, reward_dollars)
    """
    sizing_cfg = config.sizing
    mode = sizing_cfg.mode.lower().strip() if sizing_cfg else "static"

    contract_info = config.contracts.get(candidate.contract)
    cash = config.portfolio.cash

    # Default/Static Mode
    if mode == "static":
        if asset_class == AssetClass.EQUITY:
            target_risk = (
                contract_info.target_risk_dollars
                if contract_info and contract_info.target_risk_dollars
                else config.portfolio.default_equity_risk_dollars
            )
            per_share_risk = max(stop_distance * multiplier, 0.01)
            quantity = max(1.0, float(int(target_risk / per_share_risk)))
        else:
            quantity = 1.0

        risk_dollars = round(stop_distance * multiplier * quantity, 2)
        reward_dollars = round(target_distance * multiplier * quantity, 2)
        return quantity, risk_dollars, reward_dollars

    # Volatility-Targeted or Fractional Kelly Mode
    # 1. Establish base dollar risk budget
    if asset_class == AssetClass.EQUITY:
        # Use target_risk_dollars if explicitly specified on contract, otherwise cash * target_risk_pct
        if contract_info and contract_info.target_risk_dollars:
            base_risk_budget = contract_info.target_risk_dollars
        elif sizing_cfg.default_equity_risk_dollars:
            base_risk_budget = min(sizing_cfg.default_equity_risk_dollars, cash * sizing_cfg.target_risk_pct)
            if base_risk_budget <= 0:
                base_risk_budget = sizing_cfg.default_equity_risk_dollars
        else:
            base_risk_budget = cash * sizing_cfg.target_risk_pct
    else:
        # Futures: contract target_risk_dollars, or sizing_cfg.target_futures_risk_dollars
        if contract_info and contract_info.target_risk_dollars:
            base_risk_budget = contract_info.target_risk_dollars
        elif sizing_cfg.target_futures_risk_dollars:
            base_risk_budget = sizing_cfg.target_futures_risk_dollars
        else:
            base_risk_budget = cash * sizing_cfg.target_risk_pct

    # 2. Adjust budget if Fractional Kelly mode
    if mode == "fractional_kelly":
        rr_ratio = (target_distance / stop_distance) if stop_distance > 0 else 2.0
        win_rate = sizing_cfg.baseline_win_rate
        kelly_mult = compute_fractional_kelly_multiplier(
            win_rate=win_rate,
            payoff_ratio=rr_ratio,
            fraction=sizing_cfg.kelly_fraction,
            baseline_win_rate=sizing_cfg.baseline_win_rate,
        )
        effective_risk_budget = base_risk_budget * kelly_mult
    else:
        effective_risk_budget = base_risk_budget

    # 3. Compute continuous size from stop distance & multiplier
    per_unit_risk = max(stop_distance * multiplier, 0.01)
    raw_quantity = effective_risk_budget / per_unit_risk

    if asset_class == AssetClass.EQUITY:
        clamped_qty = max(
            sizing_cfg.min_shares,
            min(float(sizing_cfg.max_shares_per_trade), float(int(raw_quantity))),
        )
        quantity = max(1.0, clamped_qty)
    else:
        # Futures contracts are discrete integer units
        contracts = max(1, round(raw_quantity))
        clamped_contracts = max(
            sizing_cfg.min_contracts,
            min(sizing_cfg.max_contracts_per_trade, contracts),
        )
        quantity = float(clamped_contracts)

    risk_dollars = round(stop_distance * multiplier * quantity, 2)
    reward_dollars = round(target_distance * multiplier * quantity, 2)
    return quantity, risk_dollars, reward_dollars
