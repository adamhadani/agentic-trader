"""Capital available to risk: the configured mandate, drawdown haircut and the per-trade budget.

Pure arithmetic; observation and storage belong to their own boundaries.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from agentic_trader.config import AppConfig, PositionSizingConfig
from agentic_trader.constants import ExecutionMode
from agentic_trader.risk.limits import DrawdownPolicy, RiskLimits


__all__ = [
    "MACRO_FACTOR_FLOOR",
    "RiskBudget",
    "drawdown_risk_factor",
    "macro_risk_factor",
    "per_trade_risk_budget",
    "requires_account_risk",
    "risk_capital",
]

MACRO_FACTOR_FLOOR = 0.10


def requires_account_risk(config: AppConfig) -> bool:
    """Alpaca paper/live use observed account evidence, unlike the local simulator."""
    return config.execution_mode == ExecutionMode.ALPACA


def risk_capital(mandate: float, current_equity: float | None = None) -> float:
    """Configured capital is a ceiling; observed capital cannot increase it."""
    if not math.isfinite(mandate) or mandate <= 0:
        raise ValueError("Configured risk capital must be finite and positive")
    if current_equity is None:
        return mandate
    if not math.isfinite(current_equity) or current_equity <= 0:
        raise ValueError("Observed equity must be finite and positive")
    return min(mandate, current_equity)


def drawdown_risk_factor(drawdown_pct: float, policy: PositionSizingConfig | DrawdownPolicy) -> float:
    """1 up to the haircut threshold, falling linearly to the minimum multiplier, 0 at the sizing halt; always 1
    when gating is disabled. ``policy`` is the sizing config or a ``RiskLimits.drawdown_policy`` snapshot."""
    if not math.isfinite(drawdown_pct) or drawdown_pct < 0:
        raise ValueError("Drawdown must be a finite nonnegative ratio")
    if not policy.drawdown_gating_enabled:
        return 1.0
    if drawdown_pct >= policy.max_drawdown_stop_pct:
        return 0.0
    if drawdown_pct <= policy.drawdown_haircut_threshold_pct:
        return 1.0
    span = policy.max_drawdown_stop_pct - policy.drawdown_haircut_threshold_pct
    excess = drawdown_pct - policy.drawdown_haircut_threshold_pct
    return max(policy.drawdown_min_risk_multiplier, 1.0 - excess / span)


def macro_risk_factor(multiplier: float) -> float:
    """Clamp a regime risk multiplier to ``[MACRO_FACTOR_FLOOR, 1.0]``: it scales risk down, never up."""
    value = float(multiplier)
    if not math.isfinite(value):
        raise ValueError("Macro risk multiplier must be finite")
    return max(MACRO_FACTOR_FLOOR, min(1.0, value))


@dataclass(frozen=True)
class RiskBudget:
    """The per-trade risk budget every layer shares."""

    capital: float
    drawdown_pct: float
    drawdown_factor: float
    macro_factor: float
    dollars: float


def per_trade_risk_budget(
    limits: RiskLimits, *, equity: float | None, drawdown_pct: float, macro_multiplier: float = 1.0
) -> RiskBudget:
    """``min(cash, equity) x max_risk_pct_cap x drawdown_factor x macro_factor``."""
    capital = risk_capital(limits.cash, equity)
    drawdown = drawdown_risk_factor(drawdown_pct, limits.drawdown_policy)
    macro = macro_risk_factor(macro_multiplier)
    return RiskBudget(
        capital=capital,
        drawdown_pct=float(drawdown_pct),
        drawdown_factor=drawdown,
        macro_factor=macro,
        dollars=capital * limits.max_risk_pct_cap * drawdown * macro,
    )
