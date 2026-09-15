"""
Convex Portfolio Alpha Optimizer.
Solves the institutional Mean-Variance-Friction Quadratic Program (QP/SLSQP):
    max_w  alpha^T * w - (lambda / 2) * w^T * Sigma * w - TC * ||w - w0||_1
subject to:
    - Dollar Neutrality (optional): 1^T * w == 0
    - Gross Leverage Cap: ||w||_1 <= L_gross
    - Single-Stock Box Bounds: w_min <= w <= w_max
    - Factor Exposure Bounds: b_lower <= X^T * w <= b_upper
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from scipy import optimize as opt


logger = logging.getLogger(__name__)


@dataclass
class PortfolioOptimizationResult:
    """Quantitative summary of the optimal portfolio allocation solve."""

    success: bool
    weights: np.ndarray
    alpha_capture: float
    portfolio_variance: float
    portfolio_volatility: float
    turnover: float
    net_exposure: float
    gross_leverage: float
    iterations: int
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "weights": self.weights.tolist(),
            "alpha_capture": self.alpha_capture,
            "portfolio_variance": self.portfolio_variance,
            "portfolio_volatility": self.portfolio_volatility,
            "turnover": self.turnover,
            "net_exposure": self.net_exposure,
            "gross_leverage": self.gross_leverage,
            "iterations": self.iterations,
            "message": self.message,
        }


class ConvexAlphaPortfolioOptimizer:
    """
    Constrained convex portfolio optimizer translating raw or orthogonalized alpha vectors
    into optimal investable target portfolio weights under risk and transaction friction.
    """

    def __init__(
        self,
        risk_aversion: float = 1e-3,
        transaction_cost_rate: float = 0.0005,  # 5 bps
        gross_leverage_limit: float = 0.60,  # 0.6x for Cash-Plus portfolio
        max_position_weight: float = 0.15,  # 15% single-asset limit
        min_position_weight: float | None = None,
        dollar_neutral: bool = False,
        long_only: bool = False,
        max_factor_exposure: float | None = None,
    ):
        self.risk_aversion = float(risk_aversion)
        self.transaction_cost_rate = float(transaction_cost_rate)
        self.gross_leverage_limit = float(gross_leverage_limit)
        self.max_position_weight = float(max_position_weight)
        self.long_only = bool(long_only)
        self.min_position_weight = (
            0.0
            if self.long_only
            else (float(min_position_weight) if min_position_weight is not None else -self.max_position_weight)
        )
        self.dollar_neutral = bool(dollar_neutral)
        self.max_factor_exposure = float(max_factor_exposure) if max_factor_exposure is not None else None

    def optimize(
        self,
        alpha: np.ndarray | pd.Series | list[float],
        covariance: np.ndarray | pd.DataFrame | None = None,
        factor_matrix: np.ndarray | pd.DataFrame | None = None,
        current_weights: np.ndarray | pd.Series | None = None,
    ) -> PortfolioOptimizationResult:
        """
        Solve optimal target weights w* balancing alpha capture, covariance risk, and turnover friction.
        """
        a = np.asarray(alpha, dtype=float).flatten()
        n = len(a)

        if n == 0:
            raise ValueError("Alpha vector cannot be empty.")

        # Covariance matrix setup
        if covariance is not None:
            Sigma = np.asarray(covariance, dtype=float)
            if Sigma.shape != (n, n):
                raise ValueError(f"Covariance matrix shape {Sigma.shape} does not match alpha length {n}.")
        else:
            # Default to diagonal variance placeholder
            Sigma = np.eye(n) * 0.10

        # Current portfolio weights w_0
        if current_weights is not None:
            w_0 = np.asarray(current_weights, dtype=float).flatten()
            if len(w_0) != n:
                w_0 = np.zeros(n)
        else:
            w_0 = np.zeros(n)

        # Factor exposure matrix X
        X = np.asarray(factor_matrix, dtype=float) if factor_matrix is not None else None
        if X is not None and X.shape[0] != n:
            X = None

        # Objective Function (Formulated as Minimization)
        # Minimize: -alpha^T w + 0.5 * lambda * w^T Sigma w + TC * sum(sqrt((w - w_0)^2 + eps^2))
        eps = 1e-6

        def objective(w: np.ndarray) -> float:
            alpha_term = -float(np.dot(a, w))
            risk_term = 0.5 * self.risk_aversion * float(np.dot(w, Sigma @ w))
            diff = w - w_0
            tc_term = self.transaction_cost_rate * float(np.sum(np.sqrt(diff**2 + eps**2)))
            return alpha_term + risk_term + tc_term

        # Constraints
        constraints: list[dict[str, Any]] = []

        # 1. Dollar Neutrality: sum(w) == 0
        if self.dollar_neutral:
            constraints.append({"type": "eq", "fun": lambda w: float(np.sum(w))})

        # 2. Gross Leverage Bound: L_gross - sum(|w|) >= 0
        constraints.append({"type": "ineq", "fun": lambda w: float(self.gross_leverage_limit - np.sum(np.abs(w)))})

        # 3. Factor Exposure Bounds (if specified)
        if X is not None and self.max_factor_exposure is not None:
            constraints.append(
                {
                    "type": "ineq",
                    "fun": lambda w: float(self.max_factor_exposure - np.max(np.abs(X.T @ w))),
                }
            )

        # Single asset bounds
        bounds = [(self.min_position_weight, self.max_position_weight) for _ in range(n)]

        # Initial guess: start from w_0 or small proportional allocation
        x0 = np.copy(w_0)
        if np.all(x0 == 0.0):
            if self.long_only:
                x0 = np.full(n, min(self.max_position_weight, self.gross_leverage_limit / n))
            elif self.dollar_neutral:
                pos_mask = a > 0
                neg_mask = a < 0
                if np.any(pos_mask) and np.any(neg_mask):
                    x0[pos_mask] = 0.05
                    x0[neg_mask] = -0.05

        try:
            res = opt.minimize(
                objective,
                x0=x0,
                method="SLSQP",
                bounds=bounds,
                constraints=constraints,
                options={"maxiter": 200, "ftol": 1e-7},
            )
            w_star = res.x
            success = bool(res.success)
            message = str(res.message)
            nit = int(res.nit)
        except Exception as e:
            logger.error("Convex portfolio optimization failed: %s", e)
            w_star = w_0
            success = False
            message = str(e)
            nit = 0

        alpha_cap = float(np.dot(a, w_star))
        port_var = float(np.dot(w_star, Sigma @ w_star))
        port_vol = float(np.sqrt(max(0.0, port_var)))
        turnover = float(np.sum(np.abs(w_star - w_0)))
        net_exp = float(np.sum(w_star))
        gross_lev = float(np.sum(np.abs(w_star)))

        return PortfolioOptimizationResult(
            success=success,
            weights=w_star,
            alpha_capture=round(alpha_cap, 6),
            portfolio_variance=round(port_var, 6),
            portfolio_volatility=round(port_vol, 6),
            turnover=round(turnover, 6),
            net_exposure=round(net_exp, 6),
            gross_leverage=round(gross_lev, 6),
            iterations=nit,
            message=message,
        )
