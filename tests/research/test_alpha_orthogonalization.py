"""
Tests for Signal Orthogonalization and Multi-Factor Neutralization.
Verifies the exact numerical toy example from the reference document (Pages 15-16),
as well as Annihilator Matrix mathematical properties and residual IC testing.
"""

from __future__ import annotations

import numpy as np
import pytest

from agentic_trader.research.alpha.orthogonalization import (
    build_factor_annihilator,
    evaluate_residual_predictive_power,
    factor_neutralize,
    gram_schmidt_orthogonalize,
)


def test_gram_schmidt_toy_case_study():
    """
    Replicates the exact 4-asset numerical case study from the reference document.
    Universe: 4 assets [A, B, C, D]
    Realized Returns r: [-0.02, +0.01, -0.01, +0.02]
    Incumbent Alpha alpha_old: [-3, -1, +1, +3]
    Candidate Alpha alpha_new: [-2, -2, 0, +4]
    """
    r = np.array([-0.02, 0.01, -0.01, 0.02])
    alpha_old = np.array([-3.0, -1.0, 1.0, 3.0])
    alpha_new = np.array([-2.0, -2.0, 0.0, 4.0])

    # Standalone Information Coefficients (Pearson on demeaned)
    ic_old = np.dot(alpha_old, r) / (np.linalg.norm(alpha_old) * np.linalg.norm(r))
    ic_new = np.dot(alpha_new, r) / (np.linalg.norm(alpha_new) * np.linalg.norm(r))

    assert pytest.approx(ic_old, abs=1e-4) == 0.7071
    assert pytest.approx(ic_new, abs=1e-4) == 0.6455

    # Run Gram-Schmidt projection
    alpha_ortho, betas, _ = gram_schmidt_orthogonalize(alpha_new, alpha_old)

    # Beta should be exactly 1.000
    assert pytest.approx(betas[0], abs=1e-4) == 1.0000

    # Residual vector should be [+1, -1, -1, +1]
    expected_residual = np.array([1.0, -1.0, -1.0, 1.0])
    np.testing.assert_allclose(alpha_ortho, expected_residual, atol=1e-6)

    # Verify strict mathematical orthogonality: <alpha_ortho, alpha_old> == 0
    inner_prod = np.dot(alpha_ortho, alpha_old)
    assert pytest.approx(inner_prod, abs=1e-7) == 0.0

    # Test residual for true predictive power against forward returns r
    is_novel, res_ic, _ = evaluate_residual_predictive_power(alpha_ortho, r, min_residual_ic=0.015)

    # In the toy example, residual IC is exactly 0.000!
    assert pytest.approx(res_ic, abs=1e-6) == 0.0000
    # Candidate should be correctly rejected as a redundant noise mirage
    assert not is_novel


def test_gram_schmidt_genuine_alpha():
    """
    Tests that a truly independent, predictive candidate alpha passes orthogonalization.
    """
    r = np.array([0.05, -0.03, 0.04, -0.02, 0.01])
    # Incumbent captures part of the signal
    alpha_inc = np.array([1.0, -1.0, 0.5, -0.5, 0.0])

    # Candidate has strong independent variance that correlates with r
    alpha_cand = np.array([0.5, -0.2, 0.8, -0.6, 0.3])

    alpha_ortho, _, _ = gram_schmidt_orthogonalize(alpha_cand, alpha_inc)

    # Verify orthogonality
    assert pytest.approx(np.dot(alpha_ortho, alpha_inc), abs=1e-6) == 0.0

    # Verify residual maintains predictive power
    is_novel, res_ic, _ = evaluate_residual_predictive_power(alpha_ortho, r, min_residual_ic=0.015)
    assert not is_novel  # five observations cannot establish significance
    assert res_ic > 0.10


def test_factor_annihilator_properties():
    """
    Verifies the fundamental linear algebra properties of the Annihilator Matrix M_X:
    1. Symmetry: M_X^T == M_X
    2. Idempotency: M_X^2 == M_X
    3. Exact Factor Annihilation: X^T M_X == 0
    """
    np.random.seed(42)
    n_assets = 50
    k_factors = 5

    # Random factor exposure matrix X (N x K)
    X = np.random.randn(n_assets, k_factors)

    M_X = build_factor_annihilator(X)

    # 1. Symmetry
    np.testing.assert_allclose(M_X.T, M_X, atol=1e-8)

    # 2. Idempotency (M_X @ M_X == M_X)
    np.testing.assert_allclose(M_X @ M_X, M_X, atol=1e-8)

    # 3. Factor Annihilation (X^T @ M_X == 0)
    factor_loadings = X.T @ M_X
    np.testing.assert_allclose(factor_loadings, 0.0, atol=1e-8)

    # Test factor neutralization of a raw alpha vector
    raw_alpha = np.random.randn(n_assets)
    neutral_alpha = factor_neutralize(raw_alpha, X)

    # All factor exposures of neutral_alpha should collapse to zero: X^T neutral_alpha == 0
    net_exposures = X.T @ neutral_alpha
    np.testing.assert_allclose(net_exposures, 0.0, atol=1e-8)


def test_sector_partition_automatic_dollar_neutrality():
    """
    Verifies that neutralizing against a sector dummy matrix (partition of unity)
    automatically forces strict dollar neutrality (1^T alpha == 0).
    """
    n_assets = 60
    n_sectors = 6

    # Build mutually exclusive sector dummy matrix
    sectors = np.random.randint(0, n_sectors, size=n_assets)
    X_sectors = np.zeros((n_assets, n_sectors))
    for s in range(n_sectors):
        X_sectors[sectors == s, s] = 1.0

    raw_alpha = np.random.normal(loc=5.0, scale=2.0, size=n_assets)
    # Verify raw alpha is NOT dollar neutral
    assert abs(np.sum(raw_alpha)) > 1.0

    # Neutralize
    neutral_alpha = factor_neutralize(raw_alpha, X_sectors)

    # Sum of neutral alpha weights must be exactly 0 (dollar neutral)
    assert pytest.approx(np.sum(neutral_alpha), abs=1e-8) == 0.0
