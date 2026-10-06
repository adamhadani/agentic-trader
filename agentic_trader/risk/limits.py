"""A frozen snapshot of every limit the risk rules read.

Rules never read ``AppConfig``: they read this object, so a rule's inputs are visible in
its signature and a test can build limits without a whole config.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from agentic_trader.config import AppConfig
from agentic_trader.constants import AssetClass


__all__ = ["DrawdownPolicy", "RiskLimits", "normalize_symbol"]


def normalize_symbol(symbol: str) -> str:
    """``/MES``, ``mes`` and ``MES`` are one instrument for every risk rule."""
    return str(symbol).strip().lstrip("/").upper()


def _cap(name: str, value: float) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"Risk limit {name} must be finite and non-negative")
    return number


def _ratio(name: str, value: float) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"Risk limit {name} must be finite")
    return number


@dataclass(frozen=True)
class DrawdownPolicy:
    """The four sizing fields ``drawdown_risk_factor`` reads, as one frozen value."""

    drawdown_gating_enabled: bool
    drawdown_haircut_threshold_pct: float
    max_drawdown_stop_pct: float
    drawdown_min_risk_multiplier: float


@dataclass(frozen=True)
class RiskLimits:
    cash: float
    drawdown_gating_enabled: bool
    drawdown_haircut_threshold_pct: float
    max_drawdown_stop_pct: float
    drawdown_min_risk_multiplier: float
    max_risk_pct_cap: float
    max_trade_notional_cap: float
    max_shares_per_trade: int
    max_contracts_per_trade: int
    max_stop_risk_pct: float
    max_notional_exposure: float
    asset_class_caps: Mapping[str, float]  # a class absent here is uncapped (FX, or a cap configured as None)
    max_concurrent_positions: int
    max_correlated_positions: int
    correlation_groups: Mapping[str, frozenset[str]]  # normalised symbols; empty mapping disables the rule
    min_risk_reward_ratio: float
    lockout_pre_minutes: int
    lockout_post_minutes: int
    earnings_blackout_days: int
    enforce_rth: bool

    @property
    def drawdown_policy(self) -> DrawdownPolicy:
        """The snapshotted drawdown haircut, in the shape ``drawdown_risk_factor`` reads."""
        return DrawdownPolicy(
            drawdown_gating_enabled=self.drawdown_gating_enabled,
            drawdown_haircut_threshold_pct=self.drawdown_haircut_threshold_pct,
            max_drawdown_stop_pct=self.max_drawdown_stop_pct,
            drawdown_min_risk_multiplier=self.drawdown_min_risk_multiplier,
        )

    @classmethod
    def from_config(cls, config: AppConfig) -> RiskLimits:
        """Snapshot ``config``; ``ValueError`` (naming the config field) when a cap is not finite and
        non-negative or a ratio is not finite."""
        portfolio, sizing, risk = config.portfolio, config.sizing, config.risk
        raw_caps = {
            str(AssetClass.EQUITY): ("max_equity_exposure", portfolio.max_equity_exposure),
            str(AssetClass.FUTURES): ("max_futures_exposure", portfolio.max_futures_exposure),
            str(AssetClass.CRYPTO): ("max_crypto_exposure", portfolio.max_crypto_exposure),
        }
        groups = {
            name: frozenset(normalize_symbol(member) for member in members)
            for name, members in (portfolio.correlation_groups or {}).items()
        }
        return cls(
            cash=_cap("cash", portfolio.cash),
            drawdown_gating_enabled=bool(sizing.drawdown_gating_enabled),
            drawdown_haircut_threshold_pct=_ratio(
                "drawdown_haircut_threshold_pct", sizing.drawdown_haircut_threshold_pct
            ),
            max_drawdown_stop_pct=_ratio("max_drawdown_stop_pct", sizing.max_drawdown_stop_pct),
            drawdown_min_risk_multiplier=_ratio("drawdown_min_risk_multiplier", sizing.drawdown_min_risk_multiplier),
            max_risk_pct_cap=_cap("max_risk_pct_cap", sizing.max_risk_pct_cap),
            max_trade_notional_cap=_cap("max_trade_notional_cap", sizing.max_trade_notional_cap),
            max_shares_per_trade=int(_cap("max_shares_per_trade", sizing.max_shares_per_trade)),
            max_contracts_per_trade=int(_cap("max_contracts_per_trade", sizing.max_contracts_per_trade)),
            max_stop_risk_pct=_cap("max_stop_risk_pct", portfolio.max_stop_risk_pct),
            max_notional_exposure=_cap("max_notional_exposure", portfolio.max_notional_exposure),
            asset_class_caps=MappingProxyType({k: _cap(name, v) for k, (name, v) in raw_caps.items() if v is not None}),
            max_concurrent_positions=int(_cap("max_concurrent_positions", portfolio.max_concurrent_positions)),
            max_correlated_positions=int(_cap("max_correlated_positions", portfolio.max_correlated_positions)),
            correlation_groups=MappingProxyType(groups),
            min_risk_reward_ratio=_ratio("min_risk_reward_ratio", risk.min_risk_reward_ratio),
            lockout_pre_minutes=int(risk.lockout_pre_event_minutes),
            lockout_post_minutes=int(risk.lockout_post_event_minutes),
            earnings_blackout_days=int(risk.earnings_blackout_days),
            enforce_rth=bool(config.session.enforce_rth),
        )
