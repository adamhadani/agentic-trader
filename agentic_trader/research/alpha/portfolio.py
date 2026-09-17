"""Pure shadow portfolio construction: filled holdings and compatible forecast/risk contracts.

No broker mutations are available here. Portfolio execution is deliberately gated
until plans, partial-fill attribution and protection can use the existing FIFO.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from agentic_trader.research.alpha.forecasts import CombinedForecast, ForecastContract, validate_forecast
from agentic_trader.research.alpha.optimizer import ConvexAlphaPortfolioOptimizer


@dataclass(frozen=True)
class PortfolioPolicy:
    gross_limit: float = 0.6
    per_name_limit: float = 0.15
    turnover_limit: float = 0.2
    max_positions: int = 4
    risk_aversion: float = 5
    uncertainty_aversion: float = 1
    max_risk_age_seconds: float = 259200  # Three calendar days; explicit freshness policy
    cost_per_side: float = 0.0005
    covariance_shrinkage: float = 0.25
    min_observations: int = 60
    max_snapshot_age_seconds: float = 60
    dollar_neutral: bool = False
    max_factor_exposure: float | None = None

    def __post_init__(self):
        for name in ("gross_limit", "per_name_limit", "turnover_limit", "risk_aversion", "max_snapshot_age_seconds"):
            if not np.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f"Invalid portfolio policy {name}")
        if not np.isfinite(self.uncertainty_aversion) or self.uncertainty_aversion < 0:
            raise ValueError("Invalid uncertainty aversion")
        if not np.isfinite(self.max_risk_age_seconds) or self.max_risk_age_seconds <= 0:
            raise ValueError("Invalid risk-history freshness policy")
        if self.max_factor_exposure is not None and (
            not np.isfinite(self.max_factor_exposure) or self.max_factor_exposure < 0
        ):
            raise ValueError("Invalid factor-exposure bound")
        if not 0 <= self.cost_per_side < 0.1 or not 0 <= self.covariance_shrinkage <= 1:
            raise ValueError("Invalid covariance/cost policy")
        if (
            type(self.max_positions) is not int
            or self.max_positions < 1
            or type(self.min_observations) is not int
            or self.min_observations < 20
        ):
            raise ValueError("Invalid portfolio count/history policy")


@dataclass(frozen=True)
class PortfolioSnapshot:
    as_of: datetime
    account_version: str
    data_version: str
    config_version: str
    equity: float
    holdings: dict[str, float]  # signed market value, broker authoritative
    reservations: dict[str, float]  # signed worst-case pending entries
    locked_symbols: frozenset[str]
    shortable: frozenset[str]
    tradable: frozenset[str]
    liquidity_caps: dict[str, float]  # maximum notional at declared participation rate
    groups: dict[str, str | tuple[str, ...]]
    group_caps: dict[str, float]  # notional, e.g. asset-class or sector budgets
    factor_exposures: dict[str, dict[str, float]] | None = None


def build_shadow_portfolio(
    forecasts: tuple[CombinedForecast, ...],
    returns: pd.DataFrame,
    snapshot: PortfolioSnapshot,
    *,
    now: datetime,
    risk_contract: ForecastContract,
    policy: PortfolioPolicy | None = None,
):
    policy = policy or PortfolioPolicy()
    age = (now - snapshot.as_of).total_seconds()
    if (
        age < 0
        or age > policy.max_snapshot_age_seconds
        or not all((snapshot.account_version, snapshot.data_version, snapshot.config_version))
    ):
        raise ValueError("Stale/unversioned portfolio snapshot")
    if not np.isfinite(snapshot.equity) or snapshot.equity <= 0:
        raise ValueError("Invalid broker equity")
    if not forecasts or len({f.contract for f in forecasts}) != 1:
        raise ValueError("One calibrated forecast contract required")
    if forecasts[0].contract != risk_contract or risk_contract.currency != "USD":
        raise ValueError("Risk and forecast contracts must match the USD account")
    if len({f.symbol for f in forecasts}) != len(forecasts):
        raise ValueError("One combined forecast per instrument required")
    if len({f.observed_at for f in forecasts}) != 1:
        raise ValueError("Portfolio forecast observations must align")
    for forecast in forecasts:
        validate_forecast(forecast, now)
    if any(amount != 0 for amount in snapshot.reservations.values()):
        raise ValueError("Unfilled pending orders require a reachable-risk envelope; shadow allocation blocked")
    symbols = sorted(set(snapshot.holdings) | set(snapshot.reservations) | {f.symbol for f in forecasts})
    if set(returns.columns) != set(symbols) or not returns.index.is_unique or not returns.index.is_monotonic_increasing:
        raise ValueError("Covariance must cover every holding, reservation and forecast exactly")
    if (
        not isinstance(returns.index, pd.DatetimeIndex)
        or returns.index.tz is None
        or returns.empty
        or returns.index.hasnans
        or (returns.index > snapshot.as_of).any()
        or (snapshot.as_of - returns.index[-1]).total_seconds() > policy.max_risk_age_seconds
    ):
        raise ValueError("Stale, future or ambiguous risk-history availability")
    observed = returns.loc[:, symbols]
    if len(observed) < policy.min_observations or not np.isfinite(observed.to_numpy()).all():
        raise ValueError("Insufficient complete covariance history")
    current = np.array([snapshot.holdings.get(s, 0) / snapshot.equity for s in symbols])
    if not np.isfinite(current).all():
        raise ValueError("Invalid portfolio observations")
    expected = {f.symbol: f.expected_return for f in forecasts}
    alpha = np.array([expected.get(s, 0) for s in symbols])
    # Position count is discrete. Preserve existing owners, then preselect by
    # expected return magnitude with a deterministic tie break before convex solve.
    selected = {s for s, w in zip(symbols, current, strict=True) if w != 0}
    if len(selected) > policy.max_positions:
        raise ValueError("Existing/pending positions exceed position-count policy")
    for symbol in sorted(expected, key=lambda s: (-abs(expected[s]), s)):
        if len(selected) >= policy.max_positions:
            break
        selected.add(symbol)
    lower = []
    upper = []
    for symbol, weight in zip(symbols, current, strict=True):
        if symbol not in snapshot.liquidity_caps or symbol not in snapshot.groups:
            raise ValueError("Missing instrument liquidity/class observations")
        capacity = snapshot.liquidity_caps[symbol] / snapshot.equity
        if not np.isfinite(capacity) or capacity < 0:
            raise ValueError("Invalid liquidity capacity")
        if symbol in snapshot.locked_symbols or symbol not in snapshot.tradable:
            lower.append(weight)
            upper.append(weight)
        elif symbol not in selected:
            lower.append(0)
            upper.append(0)
        else:
            minimum = max(-policy.per_name_limit, weight - capacity)
            if symbol not in snapshot.shortable:
                minimum = max(minimum, min(0, weight))
            lower.append(minimum)
            upper.append(min(policy.per_name_limit, weight + capacity))
    memberships: dict[str, tuple[str, ...]] = {}
    for symbol in symbols:
        membership = snapshot.groups[symbol]
        memberships[symbol] = (membership,) if isinstance(membership, str) else tuple(membership)
    if any(not value for value in memberships.values()):
        raise ValueError("Missing group membership")
    groups = sorted({group for values in memberships.values() for group in values})
    if not set(groups) <= snapshot.group_caps.keys():
        raise ValueError("Missing group caps")
    matrix = np.array([[float(g in memberships[s]) for g in groups] for s in symbols])
    caps = np.array([snapshot.group_caps[g] / snapshot.equity for g in groups])
    covariance = observed.cov().to_numpy()
    covariance = (1 - policy.covariance_shrinkage) * covariance + policy.covariance_shrinkage * np.diag(
        np.diag(covariance)
    )
    factor_matrix = None
    if policy.max_factor_exposure is not None:
        exposures = snapshot.factor_exposures
        if exposures is None or set(exposures) != set(symbols):
            raise ValueError("Complete observed factors required for factor bound")
        factors = sorted(exposures[symbols[0]])
        if not factors or any(set(exposures[s]) != set(factors) for s in symbols):
            raise ValueError("Factor labels must align across instruments")
        factor_matrix = np.array([[exposures[s][factor] for factor in factors] for s in symbols])
    optimizer = ConvexAlphaPortfolioOptimizer(
        risk_aversion=policy.risk_aversion,
        uncertainty_aversion=policy.uncertainty_aversion,
        transaction_cost_rate=policy.cost_per_side,
        gross_leverage_limit=policy.gross_limit,
        max_position_weight=policy.per_name_limit,
        max_turnover=policy.turnover_limit,
        dollar_neutral=policy.dollar_neutral,
        max_factor_exposure=policy.max_factor_exposure,
    )
    result = optimizer.optimize(
        alpha,
        covariance,
        alpha_standard_error=[next((f.standard_error for f in forecasts if f.symbol == s), 0) for s in symbols],
        factor_matrix=factor_matrix,
        current_weights=current,
        lower_bounds=lower,
        upper_bounds=upper,
        group_matrix=matrix,
        group_caps=caps,
    )
    return {
        "mode": "shadow",
        "symbols": symbols,
        "account_version": snapshot.account_version,
        "data_version": snapshot.data_version,
        "config_version": snapshot.config_version,
        "as_of": snapshot.as_of.isoformat(),
        "selected": sorted(selected),
        "selection_method": "preserve_holdings_then_absolute_forecast_heuristic",
        "contract": asdict(risk_contract),
        "risk_observed_until": observed.index[-1].isoformat(),
        "policy": asdict(policy),
        "result": result.to_dict(),
    }
