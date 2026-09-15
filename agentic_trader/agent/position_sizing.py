from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from agentic_trader.constants import AssetClass


if TYPE_CHECKING:
    from agentic_trader.config import AppConfig
    from agentic_trader.scanner.models import ScreenerCandidate

logger = logging.getLogger(__name__)


class SizingTier(BaseModel):
    tier_id: str  # "half", "base", "max", "conservative"
    label: str  # "Half Size (0.5x)", "Standard (1.0x)", "Max Sizing"
    quantity: float
    risk_dollars: float
    reward_dollars: float
    notional_dollars: float
    effective_leverage: float
    is_default: bool = False


class PositionSizingResult(BaseModel):
    default_tier: SizingTier
    max_tier: SizingTier
    tiers: list[SizingTier] = Field(default_factory=list)
    drawdown_factor: float = 1.0
    gating_reasons: list[str] = Field(default_factory=list)


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


def calculate_dynamic_sizing(
    entry: float,
    stop_distance: float,
    target_distance: float,
    multiplier: float,
    asset_class: AssetClass,
    config: AppConfig,
    candidate: ScreenerCandidate | None = None,
    current_open_notional: float = 0.0,
    current_drawdown_pct: float = 0.0,
    macro_risk_multiplier: float = 1.0,
) -> PositionSizingResult:
    """Calculate dynamic position sizing with Drawdown, VaR, Macro, and Notional gating,
    producing tiered sizing choices (Half, Base, Max).
    """
    sizing_cfg = config.sizing
    portfolio_cash = max(config.portfolio.cash, 1000.0)
    max_portfolio_notional = config.portfolio.max_notional_exposure
    gating_reasons: list[str] = []

    # 0. Macro Stress Risk Scaling
    macro_factor = max(0.10, min(1.0, float(macro_risk_multiplier)))
    if macro_factor < 1.0:
        gating_reasons.append(f"Macro stress risk scaling applied: {macro_factor * 100:.0f}% risk budget")

    # 1. Drawdown Haircut Gating
    drawdown_factor = 1.0
    if sizing_cfg.drawdown_gating_enabled and current_drawdown_pct > 0:
        if current_drawdown_pct >= sizing_cfg.max_drawdown_stop_pct:
            drawdown_factor = 0.0
            gating_reasons.append(
                f"Drawdown halt active ({current_drawdown_pct * 100:.1f}% >= {sizing_cfg.max_drawdown_stop_pct * 100:.1f}%)"
            )
        elif current_drawdown_pct > sizing_cfg.drawdown_haircut_threshold_pct:
            span = max(0.001, sizing_cfg.max_drawdown_stop_pct - sizing_cfg.drawdown_haircut_threshold_pct)
            excess = current_drawdown_pct - sizing_cfg.drawdown_haircut_threshold_pct
            drawdown_factor = max(0.10, 1.0 - (excess / span))
            gating_reasons.append(
                f"Drawdown haircut applied: {drawdown_factor * 100:.0f}% sizing (DD: {current_drawdown_pct * 100:.1f}%)"
            )

    effective_adjustment = drawdown_factor * macro_factor

    # 2. Portfolio Notional Ceiling & Budget
    remaining_notional = max(0.0, max_portfolio_notional - current_open_notional)
    trade_notional_ceiling = min(sizing_cfg.max_trade_notional_cap, remaining_notional)
    if remaining_notional < max_portfolio_notional:
        gating_reasons.append(f"Notional budget remaining: ${remaining_notional:,.0f}")

    per_unit_risk = max(stop_distance * multiplier, 0.01)
    unit_notional = max(entry * multiplier, 0.01)

    # 3. Maximum Permissible Quantity (Hard Risk & Notional Gates)
    max_risk_dollars = portfolio_cash * sizing_cfg.max_risk_pct_cap  # e.g., 1.0% = $1,000
    qty_by_risk = max_risk_dollars / per_unit_risk
    qty_by_notional = trade_notional_ceiling / unit_notional

    if asset_class == AssetClass.EQUITY:
        raw_max_qty = min(float(sizing_cfg.max_shares_per_trade), qty_by_risk, qty_by_notional)
        max_qty = max(1.0, float(int(raw_max_qty)))
    else:
        raw_max_qty = min(float(sizing_cfg.max_contracts_per_trade), qty_by_risk, qty_by_notional)
        max_qty = max(1.0, float(int(raw_max_qty)))

    if (
        raw_max_qty < sizing_cfg.max_contracts_per_trade
        if asset_class != AssetClass.EQUITY
        else sizing_cfg.max_shares_per_trade
    ):
        if qty_by_notional < qty_by_risk:
            gating_reasons.append(f"Max size capped by remaining ${trade_notional_ceiling:,.0f} notional limit")
        else:
            gating_reasons.append(f"Max size capped by {sizing_cfg.max_risk_pct_cap * 100:.1f}% risk ceiling")

    # 4. Standard Base Quantity
    contract_info = config.contracts.get(candidate.contract) if candidate else None
    mode = sizing_cfg.mode.lower().strip() if sizing_cfg else "static"

    if mode == "static":
        if asset_class == AssetClass.EQUITY:
            if contract_info and contract_info.target_risk_dollars:
                base_risk_budget = contract_info.target_risk_dollars
            elif sizing_cfg and sizing_cfg.default_equity_risk_dollars:
                base_risk_budget = sizing_cfg.default_equity_risk_dollars
            else:
                base_risk_budget = 500.0
            raw_base_qty = (base_risk_budget * effective_adjustment) / per_unit_risk
            base_qty = max(
                sizing_cfg.min_shares,
                min(max_qty, float(int(raw_base_qty))),
            )
            base_qty = max(1.0, base_qty)
        else:
            base_qty = 1.0
    else:
        if asset_class == AssetClass.EQUITY:
            if contract_info and contract_info.target_risk_dollars:
                base_risk_budget = contract_info.target_risk_dollars
            elif sizing_cfg.default_equity_risk_dollars:
                base_risk_budget = min(
                    sizing_cfg.default_equity_risk_dollars, portfolio_cash * sizing_cfg.target_risk_pct
                )
                if base_risk_budget <= 0:
                    base_risk_budget = sizing_cfg.default_equity_risk_dollars
            else:
                base_risk_budget = portfolio_cash * sizing_cfg.target_risk_pct
        else:
            if contract_info and contract_info.target_risk_dollars:
                base_risk_budget = contract_info.target_risk_dollars
            elif sizing_cfg.target_futures_risk_dollars:
                base_risk_budget = sizing_cfg.target_futures_risk_dollars
            else:
                base_risk_budget = portfolio_cash * sizing_cfg.target_risk_pct

        # Adjust base budget if Fractional Kelly
        if mode == "fractional_kelly":
            rr_ratio = (target_distance / stop_distance) if stop_distance > 0 else 2.0
            win_rate = sizing_cfg.baseline_win_rate
            kelly_mult = compute_fractional_kelly_multiplier(
                win_rate=win_rate,
                payoff_ratio=rr_ratio,
                fraction=sizing_cfg.kelly_fraction,
                baseline_win_rate=sizing_cfg.baseline_win_rate,
            )
            effective_base_budget = base_risk_budget * kelly_mult * effective_adjustment
        else:
            effective_base_budget = base_risk_budget * effective_adjustment

        raw_base_qty = effective_base_budget / per_unit_risk

        if asset_class == AssetClass.EQUITY:
            base_qty = max(
                sizing_cfg.min_shares,
                min(max_qty, float(int(raw_base_qty))),
            )
            base_qty = max(1.0, base_qty)
        else:
            contracts = max(1, round(raw_base_qty))
            clamped_contracts = max(
                sizing_cfg.min_contracts,
                min(int(max_qty), contracts),
            )
            base_qty = float(clamped_contracts)

    # 5. Build Sizing Tiers
    tier_quantities: list[tuple[str, str, float, bool]] = []

    if asset_class == AssetClass.EQUITY:
        half_qty = max(1.0, float(int(base_qty * 0.5)))
        if max_qty > base_qty > half_qty:
            tier_quantities.append(("half", "Conservative (0.5x)", half_qty, False))
            tier_quantities.append(("base", "Standard (1.0x)", base_qty, True))
            tier_quantities.append(("max", "Max Permissible", max_qty, False))
        elif max_qty > base_qty:
            tier_quantities.append(("base", "Standard (1.0x)", base_qty, True))
            tier_quantities.append(("max", "Max Permissible", max_qty, False))
        elif base_qty > half_qty:
            tier_quantities.append(("half", "Conservative (0.5x)", half_qty, False))
            tier_quantities.append(("base", "Standard / Max", base_qty, True))
        else:
            tier_quantities.append(("base", "Standard (1.0x)", base_qty, True))
    else:
        # Futures (discrete micro contracts)
        int_max = int(max_qty)
        int_base = int(base_qty)

        if int_max >= 3 and int_base >= 2:
            tier_quantities.append(("half", "Half (1x)", 1.0, False))
            tier_quantities.append(("base", f"Base ({int_base}x)", float(int_base), True))
            tier_quantities.append(("max", f"Max ({int_max}x)", float(int_max), False))
        elif int_max >= 3 and int_base == 1:
            tier_quantities.append(("base", "Base (1x)", 1.0, True))
            tier_quantities.append(("med", "Medium (2x)", 2.0, False))
            tier_quantities.append(("max", f"Max ({int_max}x)", float(int_max), False))
        elif int_max == 2:
            tier_quantities.append(("half", "Half (1x)", 1.0, int_base == 1))
            tier_quantities.append(("base", "Base / Max (2x)", 2.0, int_base == 2))
        else:
            tier_quantities.append(("base", "Base (1x)", 1.0, True))

    built_tiers: list[SizingTier] = []
    default_tier: SizingTier | None = None
    max_tier: SizingTier | None = None

    for t_id, label, qty, is_def in tier_quantities:
        risk = round(per_unit_risk * qty, 2)
        reward = round(target_distance * multiplier * qty, 2)
        notional = round(unit_notional * qty, 2)
        lev = round(notional / portfolio_cash, 2)
        tier = SizingTier(
            tier_id=t_id,
            label=label,
            quantity=qty,
            risk_dollars=risk,
            reward_dollars=reward,
            notional_dollars=notional,
            effective_leverage=lev,
            is_default=is_def,
        )
        built_tiers.append(tier)
        if is_def or default_tier is None:
            default_tier = tier
        if t_id == "max" or max_tier is None or qty >= max_tier.quantity:
            max_tier = tier

    assert default_tier is not None
    assert max_tier is not None

    return PositionSizingResult(
        default_tier=default_tier,
        max_tier=max_tier,
        tiers=built_tiers,
        drawdown_factor=drawdown_factor,
        gating_reasons=gating_reasons,
    )


def calculate_position_size(
    candidate: ScreenerCandidate,
    stop_distance: float,
    target_distance: float,
    multiplier: float,
    asset_class: AssetClass,
    config: AppConfig,
    current_open_notional: float = 0.0,
    current_drawdown_pct: float = 0.0,
) -> tuple[float, float, float]:
    """Calculate position quantity, dollar risk, and dollar reward.
    Maintains full backwards compatibility with legacy callers while leveraging dynamic sizing.

    Returns:
        tuple of (quantity, risk_dollars, reward_dollars)
    """
    entry = candidate.entry_price if hasattr(candidate, "entry_price") else 0.0
    if entry <= 0:
        entry = 100.0  # Fallback baseline

    result = calculate_dynamic_sizing(
        entry=entry,
        stop_distance=stop_distance,
        target_distance=target_distance,
        multiplier=multiplier,
        asset_class=asset_class,
        config=config,
        candidate=candidate,
        current_open_notional=current_open_notional,
        current_drawdown_pct=current_drawdown_pct,
    )
    return result.default_tier.quantity, result.default_tier.risk_dollars, result.default_tier.reward_dollars
