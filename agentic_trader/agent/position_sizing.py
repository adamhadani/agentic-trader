from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from agentic_trader.constants import AssetClass, SizingMode
from agentic_trader.risk import RiskLimits, per_trade_risk_budget


if TYPE_CHECKING:
    from agentic_trader.agent.evaluator import LLMTradeEvaluation
    from agentic_trader.config import AppConfig
    from agentic_trader.screeners.base import ScreenerCandidate

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
    current_equity: float | None = None,
    risk_dollars_cap: float | None = None,
) -> PositionSizingResult:
    """Calculate stop-distance sizing with drawdown, macro and notional limits,
    producing tiered sizing choices (Half, Base, Max).

    The max tier is bounded by ``agentic_trader.risk.per_trade_risk_budget``, the one
    per-trade budget every layer shares (``min(cash, equity) x max_risk_pct_cap x
    drawdown_factor x macro_factor``), so every tier offered on a card fits the budget the
    tap-time regime gate and admission re-check with the same inputs. The base tier scales
    its own target budget by the same drawdown and macro factors and is clamped to the max
    tier; a paper-probe ``risk_dollars_cap`` may only lower the max.
    """
    sizing_cfg = config.sizing
    budget = per_trade_risk_budget(
        RiskLimits.from_config(config),
        equity=current_equity,
        drawdown_pct=current_drawdown_pct,
        macro_multiplier=macro_risk_multiplier,
    )
    portfolio_cash, drawdown_factor, macro_factor = budget.capital, budget.drawdown_factor, budget.macro_factor
    max_portfolio_notional = config.portfolio.max_notional_exposure
    gating_reasons: list[str] = []

    if risk_dollars_cap is not None and not risk_dollars_cap > 0:
        raise ValueError("Positive risk cap required")

    # 0. Macro Stress Risk Scaling
    if macro_factor < 1.0:
        gating_reasons.append(f"Macro stress risk scaling applied: {macro_factor * 100:.0f}% risk budget")

    # 1. Drawdown Haircut Gating
    if drawdown_factor == 0:
        gating_reasons.append(
            f"Drawdown halt active ({current_drawdown_pct * 100:.1f}% >= {sizing_cfg.max_drawdown_stop_pct * 100:.1f}%)"
        )
    elif drawdown_factor < 1:
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

    # 3. Maximum Permissible Quantity (Hard Risk & Notional Gates): the shared per-trade budget
    max_risk_dollars = budget.dollars
    if risk_dollars_cap is not None and risk_dollars_cap < max_risk_dollars:
        # A paper probe may only ever reduce risk. Base/half tiers clamp to max_qty below.
        max_risk_dollars = risk_dollars_cap
        gating_reasons.append(f"Paper probe risk cap applied: ${risk_dollars_cap:,.0f}")
    qty_by_risk = max_risk_dollars / per_unit_risk
    qty_by_notional = trade_notional_ceiling / unit_notional

    if asset_class == AssetClass.EQUITY:
        raw_max_qty = min(float(sizing_cfg.max_shares_per_trade), qty_by_risk, qty_by_notional)
        max_qty = max(0.0, float(int(raw_max_qty)))
    else:
        raw_max_qty = min(float(sizing_cfg.max_contracts_per_trade), qty_by_risk, qty_by_notional)
        max_qty = max(0.0, float(int(raw_max_qty)))

    minimum = sizing_cfg.min_shares if asset_class == AssetClass.EQUITY else sizing_cfg.min_contracts
    if drawdown_factor == 0 or max_qty < minimum:
        gating_reasons.append("No permissible quantity under hard risk/notional/drawdown gates")
        tier = SizingTier(
            tier_id="blocked",
            label="Unavailable",
            quantity=0,
            risk_dollars=0,
            reward_dollars=0,
            notional_dollars=0,
            effective_leverage=0,
            is_default=True,
        )
        return PositionSizingResult(
            default_tier=tier,
            max_tier=tier,
            tiers=[tier],
            drawdown_factor=drawdown_factor,
            gating_reasons=gating_reasons,
        )

    count_cap = (
        sizing_cfg.max_shares_per_trade if asset_class == AssetClass.EQUITY else sizing_cfg.max_contracts_per_trade
    )
    if raw_max_qty < count_cap:
        if qty_by_notional < qty_by_risk:
            gating_reasons.append(f"Max size capped by remaining ${trade_notional_ceiling:,.0f} notional limit")
        else:
            gating_reasons.append(f"Max size capped by {sizing_cfg.max_risk_pct_cap:.1%} risk ceiling")

    # 4. Standard Base Quantity
    contract_info = config.contracts.get(candidate.contract) if candidate else None
    mode = sizing_cfg.mode

    if mode == SizingMode.STATIC:
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


def scale_sizing(
    eval_res: LLMTradeEvaluation, factor: float, *, min_units: float, portfolio_cash: float
) -> LLMTradeEvaluation | None:
    """Every tier and the headline size scaled by ``factor`` in [0, 1] with whole units.

    Equities only. Per-unit risk/reward/notional come from the evaluation's own bracket
    (``stop_distance_points``, ``target_distance_points``, ``entry_price``), matching
    ``calculate_dynamic_sizing``. Tiers that round below ``min_units`` are dropped; when the
    default tier is dropped the card cannot be sized and None is returned. ``factor == 1``
    returns the evaluation unchanged.
    """
    if eval_res.asset_class != AssetClass.EQUITY:
        raise ValueError("scale_sizing supports equities only")
    if not 0.0 <= factor <= 1.0:
        raise ValueError("factor must be in [0, 1]")
    if factor == 1.0:
        return eval_res
    per_unit_risk = eval_res.stop_distance_points
    per_unit_reward = eval_res.target_distance_points
    unit_notional = eval_res.entry_price

    def scaled_tier(tier: dict[str, Any]) -> dict[str, Any] | None:
        qty = float(math.floor(float(tier["quantity"]) * factor))
        if qty < min_units:
            return None
        return {
            **tier,
            "quantity": qty,
            "risk_dollars": round(per_unit_risk * qty, 2),
            "reward_dollars": round(per_unit_reward * qty, 2),
            "notional_dollars": round(unit_notional * qty, 2),
            "effective_leverage": round(unit_notional * qty / portfolio_cash, 2),
        }

    tiers = [t for t in (scaled_tier(t) for t in eval_res.sizing_tiers or []) if t is not None]
    default = next((t for t in tiers if t.get("is_default")), None)
    if eval_res.sizing_tiers and default is None:
        return None
    qty = float(default["quantity"]) if default else float(math.floor(eval_res.quantity * factor))
    if qty < min_units:
        return None
    return eval_res.model_copy(
        update={
            "quantity": qty,
            "risk_dollars": round(per_unit_risk * qty, 2),
            "reward_dollars": round(per_unit_reward * qty, 2),
            "notional_value": round(unit_notional * qty, 2),
            "effective_leverage": round(unit_notional * qty / portfolio_cash, 2),
            "sizing_tiers": tiers or None,
        }
    )
