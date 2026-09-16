import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.alpha.optimizer import ConvexAlphaPortfolioOptimizer


@pytest.mark.parametrize("invalid", ["nan", "indefinite", "factors", "weights", "missing_covariance", "labels"])
def test_invalid_portfolio_contract_rejected(invalid):
    args = {"alpha": np.array([0.03, -0.02, 0.01]), "covariance": np.eye(3)}
    if invalid == "nan":
        args["alpha"][0] = np.nan
    if invalid == "indefinite":
        args["covariance"] = -np.eye(3)
    if invalid == "factors":
        args["factor_matrix"] = np.ones((2, 1))
    if invalid == "weights":
        args["current_weights"] = np.ones(2)
    if invalid == "missing_covariance":
        args["covariance"] = None
    if invalid == "labels":
        args["alpha"] = pd.Series([0.03, -0.02, 0.01], index=["A", "B", "C"])
        args["covariance"] = pd.DataFrame(np.eye(3), index=["C", "B", "A"], columns=["C", "B", "A"])
    with pytest.raises(ValueError):
        ConvexAlphaPortfolioOptimizer().optimize(**args)


@pytest.mark.parametrize("assets", [3, 10, 50, 100])
def test_large_portfolios_remain_feasible_and_order_invariant(assets):
    rng = np.random.default_rng(assets)
    factors = rng.normal(size=(assets, 3))
    covariance = factors @ factors.T * 0.01 + np.eye(assets) * 0.02
    alpha = rng.normal(0, 0.02, assets)
    optimizer = ConvexAlphaPortfolioOptimizer(risk_aversion=1, dollar_neutral=True, max_factor_exposure=0.05)
    result = optimizer.optimize(alpha, covariance, factors)
    assert result.success, result.message
    assert abs(result.weights.sum()) < 1e-7
    assert abs(result.weights).sum() <= 0.6 + 1e-7
    assert abs(factors.T @ result.weights).max() <= 0.05 + 1e-7
    permutation = rng.permutation(assets)
    other = optimizer.optimize(alpha[permutation], covariance[np.ix_(permutation, permutation)], factors[permutation])
    np.testing.assert_allclose(other.weights, result.weights[permutation], atol=1e-5)


def test_failed_solve_has_no_actionable_target():
    optimizer = ConvexAlphaPortfolioOptimizer(
        min_position_weight=0.2, max_position_weight=0.3, gross_leverage_limit=0.1
    )
    result = optimizer.optimize(np.ones(3) * 0.01, np.eye(3))
    assert not result.success
    assert result.weights is None
