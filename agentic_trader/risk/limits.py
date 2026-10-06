"""A frozen snapshot of every limit the risk rules read.

Rules never read ``AppConfig``: they read this object, so a rule's inputs are visible in
its signature and a test can build limits without a whole config.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from agentic_trader.config import AppConfig, PositionSizingConfig
from agentic_trader.constants import AssetClass


__all__ = ["RiskLimits", "normalize_symbol"]


def normalize_symbol(symbol: str) -> str:
    """``/MES``, ``mes`` and ``MES`` are one instrument for every risk rule."""
    return str(symbol).strip().lstrip("/").upper()


@dataclass(frozen=True)
class RiskLimits:
    cash: float
    sizing: PositionSizingConfig
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

    @classmethod
    def from_config(cls, config: AppConfig) -> RiskLimits:
        portfolio, sizing, risk = config.portfolio, config.sizing, config.risk
        raw_caps = {
            str(AssetClass.EQUITY): getattr(portfolio, "max_equity_exposure", None),
            str(AssetClass.FUTURES): getattr(portfolio, "max_futures_exposure", None),
            str(AssetClass.CRYPTO): getattr(portfolio, "max_crypto_exposure", None),
        }
        groups = {
            name: frozenset(normalize_symbol(member) for member in members)
            for name, members in (portfolio.correlation_groups or {}).items()
        }
        return cls(
            cash=float(portfolio.cash),
            sizing=sizing,
            max_risk_pct_cap=float(sizing.max_risk_pct_cap),
            max_trade_notional_cap=float(sizing.max_trade_notional_cap),
            max_shares_per_trade=int(sizing.max_shares_per_trade),
            max_contracts_per_trade=int(sizing.max_contracts_per_trade),
            max_stop_risk_pct=float(portfolio.max_stop_risk_pct),
            max_notional_exposure=float(portfolio.max_notional_exposure),
            asset_class_caps=MappingProxyType({k: float(v) for k, v in raw_caps.items() if v is not None}),
            max_concurrent_positions=int(portfolio.max_concurrent_positions),
            max_correlated_positions=int(portfolio.max_correlated_positions),
            correlation_groups=MappingProxyType(groups),
            min_risk_reward_ratio=float(risk.min_risk_reward_ratio),
            lockout_pre_minutes=int(risk.lockout_pre_event_minutes),
            lockout_post_minutes=int(risk.lockout_post_event_minutes),
            earnings_blackout_days=int(risk.earnings_blackout_days),
            enforce_rth=bool(getattr(config.session, "enforce_rth", True)),
        )
