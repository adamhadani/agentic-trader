"""Convex mean/variance/turnover portfolio targets via CVXPY + Clarabel.

Expected returns and covariance must share a horizon. Solver failures return no
weights. These targets are research/shadow artifacts and cannot submit orders.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import cvxpy as cp
import numpy as np
import pandas as pd


FEASIBILITY_TOLERANCE = 1e-7


@dataclass(frozen=True)
class PortfolioOptimizationResult:
    success: bool
    weights: np.ndarray | None
    alpha_capture: float = 0
    portfolio_variance: float = 0
    portfolio_volatility: float = 0
    turnover: float = 0
    net_exposure: float = 0
    gross_leverage: float = 0
    iterations: int = 0
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["weights"] = self.weights.tolist() if self.weights is not None else None
        return result


class ConvexAlphaPortfolioOptimizer:
    def __init__(
        self,
        risk_aversion: float = 1,
        transaction_cost_rate: float = 0.0005,
        gross_leverage_limit: float = 0.60,
        max_position_weight: float = 0.15,
        min_position_weight: float | None = None,
        dollar_neutral: bool = False,
        long_only: bool = False,
        max_factor_exposure: float | None = None,
        max_turnover: float | None = None,
        solver_seconds: float = 5,
    ):
        self.risk_aversion = risk_aversion
        self.transaction_cost_rate = transaction_cost_rate
        self.gross_leverage_limit = gross_leverage_limit
        self.max_position_weight = max_position_weight
        self.min_position_weight = (
            0 if long_only else (-max_position_weight if min_position_weight is None else min_position_weight)
        )
        self.dollar_neutral = dollar_neutral
        self.max_factor_exposure = max_factor_exposure
        self.max_turnover = max_turnover
        self.solver_seconds = solver_seconds
        positive = (risk_aversion, gross_leverage_limit, max_position_weight, solver_seconds)
        if (
            not all(np.isfinite(x) and x > 0 for x in positive)
            or not np.isfinite(self.min_position_weight)
            or self.min_position_weight > max_position_weight
        ):
            raise ValueError("Invalid optimizer policy")
        if any(
            value is not None and (not np.isfinite(value) or value < 0)
            for value in (transaction_cost_rate, max_factor_exposure, max_turnover)
        ):
            raise ValueError("Invalid cost/exposure constraint")

    def optimize(
        self,
        alpha,
        covariance=None,
        factor_matrix=None,
        current_weights=None,
        *,
        lower_bounds=None,
        upper_bounds=None,
        group_matrix=None,
        group_caps=None,
    ) -> PortfolioOptimizationResult:
        a = np.asarray(alpha, dtype=float)
        if a.ndim != 1 or not len(a) or not np.isfinite(a).all():
            raise ValueError("Expected returns must be a nonempty finite vector")
        n = len(a)
        if covariance is None:
            raise ValueError("Observed covariance required; no placeholder covariance")
        sigma = np.asarray(covariance, dtype=float)
        if (
            sigma.shape != (n, n)
            or not np.isfinite(sigma).all()
            or not np.allclose(sigma, sigma.T, atol=1e-12, rtol=1e-10)
        ):
            raise ValueError("Covariance must be finite, square and symmetric")
        if np.linalg.eigvalsh(sigma).min() < -1e-10:
            raise ValueError("Covariance must be positive semidefinite")
        if isinstance(alpha, pd.Series):
            if not alpha.index.is_unique:
                raise ValueError("Asset labels must be unique")
            if (
                not isinstance(covariance, pd.DataFrame)
                or not covariance.index.equals(alpha.index)
                or not covariance.columns.equals(alpha.index)
            ):
                raise ValueError("Covariance asset labels must align exactly")
            for data in (factor_matrix, current_weights, lower_bounds, upper_bounds, group_matrix):
                if data is not None and (
                    not isinstance(data, (pd.Series, pd.DataFrame)) or not data.index.equals(alpha.index)
                ):
                    raise ValueError("All labeled inputs must align exactly")

        def vector(value, default):
            v = np.full(n, default) if value is None else np.asarray(value, dtype=float)
            if v.shape != (n,) or not np.isfinite(v).all():
                raise ValueError("Portfolio vector shape/nonfinite mismatch")
            return v

        current = vector(current_weights, 0)
        lower = vector(lower_bounds, self.min_position_weight)
        upper = vector(upper_bounds, self.max_position_weight)
        if (lower > upper).any():
            raise ValueError("Inconsistent asset bounds")
        # Asset-specific liquidity/borrow bounds may tighten, never relax policy.
        lower = np.maximum(lower, self.min_position_weight)
        upper = np.minimum(upper, self.max_position_weight)
        factors = None if factor_matrix is None else np.asarray(factor_matrix, dtype=float)
        if factors is not None and (
            factors.ndim != 2 or factors.shape[0] != n or not factors.shape[1] or not np.isfinite(factors).all()
        ):
            raise ValueError("Factor matrix shape/nonfinite mismatch")
        if self.max_factor_exposure is not None and factors is None:
            raise ValueError("Configured factor constraint requires observations")
        groups = None if group_matrix is None else np.asarray(group_matrix, dtype=float)
        caps = None if group_caps is None else np.asarray(group_caps, dtype=float)
        if (groups is None) != (caps is None):
            raise ValueError("Group exposures and caps must be supplied together")
        if (
            groups is not None
            and caps is not None
            and (
                groups.ndim != 2
                or groups.shape[0] != n
                or caps.shape != (groups.shape[1],)
                or not np.isfinite(groups).all()
                or not np.isfinite(caps).all()
                or (groups < 0).any()
                or (caps < 0).any()
            )
        ):
            raise ValueError("Invalid group constraints")
        weights = cp.Variable(n)
        constraints = [weights >= lower, weights <= upper, cp.norm1(weights) <= self.gross_leverage_limit]
        if self.dollar_neutral:
            constraints.append(cp.sum(weights) == 0)
        if factors is not None and self.max_factor_exposure is not None:
            constraints.append(cp.abs(factors.T @ weights) <= self.max_factor_exposure)
        if self.max_turnover is not None:
            constraints.append(cp.norm1(weights - current) <= self.max_turnover)
        if groups is not None:
            constraints.append(groups.T @ cp.abs(weights) <= caps)
        objective = cp.Maximize(
            a @ weights
            - 0.5 * self.risk_aversion * cp.quad_form(weights, cp.psd_wrap(sigma))
            - self.transaction_cost_rate * cp.norm1(weights - current)
        )
        problem = cp.Problem(objective, constraints)
        try:
            problem.solve(
                solver=cp.CLARABEL,
                max_iter=300,
                time_limit=self.solver_seconds,
                tol_gap_abs=1e-10,
                tol_gap_rel=1e-10,
                tol_feas=1e-10,
            )
        except cp.error.SolverError as exc:
            return PortfolioOptimizationResult(False, None, message=f"solver_error:{exc}")
        if problem.status != cp.OPTIMAL or weights.value is None:
            return PortfolioOptimizationResult(False, None, message=f"solver_status:{problem.status}")
        target = np.asarray(weights.value)
        # Independent post-solve check; an optimizer status alone never authorizes risk.
        tol = FEASIBILITY_TOLERANCE
        feasible = (
            np.isfinite(target).all()
            and (target >= lower - tol).all()
            and (target <= upper + tol).all()
            and abs(target).sum() <= self.gross_leverage_limit + tol
        )
        if self.dollar_neutral:
            feasible = feasible and abs(target.sum()) <= tol
        if factors is not None and self.max_factor_exposure is not None:
            feasible = feasible and (abs(factors.T @ target) <= self.max_factor_exposure + tol).all()
        if self.max_turnover is not None:
            feasible = feasible and abs(target - current).sum() <= self.max_turnover + tol
        if groups is not None and caps is not None:
            feasible = feasible and (groups.T @ abs(target) <= caps + tol).all()
        if not feasible:
            return PortfolioOptimizationResult(False, None, message="post_solve_constraint_violation")
        variance = float(target @ sigma @ target)
        target.setflags(write=False)
        return PortfolioOptimizationResult(
            True,
            target,
            float(a @ target),
            variance,
            float(np.sqrt(max(0, variance))),
            float(abs(target - current).sum()),
            float(target.sum()),
            float(abs(target).sum()),
            int(problem.solver_stats.num_iters or 0),
            str(problem.status),
        )
