"""Shared pure risk policy; observation and storage belong to their own boundaries."""

import math

from agentic_trader.config import AppConfig, PositionSizingConfig
from agentic_trader.constants import ExecutionMode


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


def drawdown_risk_factor(drawdown_pct: float, policy: PositionSizingConfig) -> float:
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
