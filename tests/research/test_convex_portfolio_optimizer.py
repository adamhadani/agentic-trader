"""
Tests for Convex Portfolio Alpha Optimizer (Mean-Variance-Friction QP/SLSQP).
Verifies that the portfolio optimizer correctly solves target weights w* balancing
alpha capture, covariance risk penalty, and transaction cost drag,
while honoring dollar neutrality, gross leverage limits, and factor bounds.
"""

from __future__ import annotations

import numpy as np
import pytest

from agentic_trader.research.alpha.optimizer import (
    ConvexAlphaPortfolioOptimizer,
    PortfolioOptimizationResult,
)


def test_convex_optimizer_toy_setup():
    """
    Tests convex optimization on a synthetic 10-asset universe with structural factor risk
    and linear transaction friction.
    """
    np.random.seed(42)
    n_assets = 10
    k_factors = 3

    # Generate synthetic alpha signals (demeaned, standardized)
    raw_alpha = np.array([1.5, 0.8, -0.4, 1.2, -1.1, -0.6, 0.9, -1.3, 0.2, -1.2])
    alpha = raw_alpha - np.mean(raw_alpha)

    # Factor exposures and factor covariance
    X = np.random.randn(n_assets, k_factors)
    Sigma_F = np.diag([0.04, 0.05, 0.03])
    Delta = np.diag(np.full(n_assets, 0.08))
    Sigma = X @ Sigma_F @ X.T + Delta

    optimizer = ConvexAlphaPortfolioOptimizer(
        risk_aversion=1e-3,
        transaction_cost_rate=0.0005,  # 5 bps
        gross_leverage_limit=0.60,  # 0.6x gross leverage limit (Cash-Plus $60k cap)
        max_position_weight=0.15,  # 15% max per asset
        dollar_neutral=True,
    )

    result: PortfolioOptimizationResult = optimizer.optimize(
        alpha=alpha,
        covariance=Sigma,
        factor_matrix=X,
    )

    assert result.success
    w_star = result.weights

    # 1. Verify weights shape and bounds
    assert len(w_star) == n_assets
    assert np.all(w_star <= 0.15 + 1e-5)
    assert np.all(w_star >= -0.15 - 1e-5)

    # 2. Verify Dollar Neutrality: sum(w) == 0
    assert pytest.approx(float(np.sum(w_star)), abs=1e-4) == 0.0

    # 3. Verify Gross Leverage Cap: sum(|w|) <= 0.60
    gross_lev = float(np.sum(np.abs(w_star)))
    assert gross_lev <= 0.60 + 1e-4
    assert gross_lev > 0.10  # Must deploy meaningful capital

    # 4. Assets with positive alpha should receive positive or higher weights than negative alpha assets
    assert np.dot(alpha, w_star) > 0.0  # Positive expected alpha capture


def test_transaction_cost_turnover_damping():
    """
    Verifies that increasing transaction costs TC(w - w_0) damps trading turnover.
    """
    np.random.seed(42)
    n = 6
    # Realistic daily expected alpha return magnitude
    alpha = np.array([0.02, -0.015, 0.01, -0.008, 0.025, -0.02])
    Sigma = np.eye(n) * 0.04

    # Starting portfolio w_0
    w_0 = np.array([0.08, 0.0, 0.08, 0.0, 0.08, 0.0])

    # Low transaction cost optimizer (0.1 bps)
    opt_low_tc = ConvexAlphaPortfolioOptimizer(
        risk_aversion=1.0,
        transaction_cost_rate=0.00001,
        gross_leverage_limit=0.50,
        max_position_weight=0.25,
    )
    res_low = opt_low_tc.optimize(alpha=alpha, covariance=Sigma, current_weights=w_0)

    # High transaction cost optimizer (500 bps - severe penalty for moving away from w_0)
    opt_high_tc = ConvexAlphaPortfolioOptimizer(
        risk_aversion=1.0,
        transaction_cost_rate=0.0500,
        gross_leverage_limit=0.50,
        max_position_weight=0.25,
    )
    res_high = opt_high_tc.optimize(alpha=alpha, covariance=Sigma, current_weights=w_0)

    turnover_low = float(np.sum(np.abs(res_low.weights - w_0)))
    turnover_high = float(np.sum(np.abs(res_high.weights - w_0)))

    # Higher transaction cost must damp trading turnover toward w_0
    assert turnover_high < turnover_low


def test_long_only_portfolio_mode():
    """
    Verifies that the optimizer can operate in long-only mode (w_min >= 0).
    """
    n = 6
    # Demeaned alpha with clear gradation
    alpha = np.array([0.05, 0.03, 0.01, -0.01, -0.03, -0.05])
    Sigma = np.eye(n) * 0.02

    optimizer = ConvexAlphaPortfolioOptimizer(
        risk_aversion=2.0,
        transaction_cost_rate=0.0001,
        gross_leverage_limit=0.50,
        max_position_weight=0.35,  # Generous ceiling to allow risk-return differentiation
        dollar_neutral=False,
        long_only=True,
    )
    res = optimizer.optimize(alpha=alpha, covariance=Sigma)

    assert res.success
    # All weights must be non-negative
    assert np.all(res.weights >= -1e-6)
    # Highest alpha asset receives strictly higher weight than lower positive alpha asset
    assert res.weights[0] > res.weights[2]
    # Negative alphas receive zero weight in long-only mode
    assert pytest.approx(float(res.weights[4]), abs=1e-3) == 0.0
    assert pytest.approx(float(res.weights[5]), abs=1e-3) == 0.0
