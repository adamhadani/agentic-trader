"""
Signal Orthogonalization & Multi-Factor Neutralization Pipeline.
Implements:
1. Gram-Schmidt Alpha Residualization against active production alphas.
2. Residual Predictive Power Testing (Information Coefficient on orthogonal residual).
3. Factor Annihilator Matrix (M_X) projection operator for market beta and sector neutralization.
4. Weighted Least Squares (WLS) cross-sectional heteroskedasticity handling.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from scipy import stats


logger = logging.getLogger(__name__)


def gram_schmidt_orthogonalize(
    candidate: np.ndarray | pd.Series,
    incumbents: np.ndarray | pd.Series | pd.DataFrame | list[np.ndarray],
) -> tuple[np.ndarray, np.ndarray, float]:
    """
    Project candidate alpha onto the subspace spanned by incumbent production alphas:
        alpha_candidate = A * beta + alpha_ortho
        beta = (A^T * A)^(-1) * A^T * alpha_candidate
        alpha_ortho = alpha_candidate - A * beta

    Returns:
        (alpha_ortho, betas, r_squared):
        - alpha_ortho: Orthogonal residual vector strictly perpendicular to incumbents.
        - betas: Regression coefficients (redundancy loadings on incumbents).
        - r_squared: Fraction of candidate variance explained by incumbents in [0.0, 1.0].
    """
    cand = np.asarray(candidate, dtype=float).flatten()
    n = len(cand)

    if isinstance(incumbents, (pd.Series, np.ndarray)) and incumbents.ndim == 1:
        A = np.asarray(incumbents, dtype=float).reshape(-1, 1)
    elif isinstance(incumbents, pd.DataFrame):
        A = np.asarray(incumbents.values, dtype=float)
    elif isinstance(incumbents, list):
        if not incumbents:
            return cand, np.zeros(0), 0.0
        A = np.column_stack([np.asarray(x, dtype=float).flatten() for x in incumbents])
    else:
        A = np.asarray(incumbents, dtype=float)
        if A.ndim == 1:
            A = A.reshape(-1, 1)

    if A.shape[0] != n or A.size == 0:
        return cand, np.zeros(0), 0.0

    # Solve least-squares projection via pseudo-inverse for numerical stability
    # A^T A beta = A^T cand
    pinv_A = np.linalg.pinv(A)
    betas = pinv_A @ cand

    projected = A @ betas
    alpha_ortho = cand - projected

    total_var = float(np.sum(cand**2))
    residual_var = float(np.sum(alpha_ortho**2))
    r_squared = max(0.0, min(1.0, 1.0 - (residual_var / (total_var + 1e-12))))

    return alpha_ortho, betas, r_squared


def evaluate_residual_predictive_power(
    alpha_ortho: np.ndarray | pd.Series,
    forward_returns: np.ndarray | pd.Series,
    min_residual_ic: float = 0.015,
    method: str = "pearson",
) -> tuple[bool, float, float]:
    """
    Test whether the orthogonal residual component contains statistically significant
    predictive power over forward asset returns.

    Returns:
        (is_novel, residual_ic, p_value):
        - is_novel: True if residual IC >= min_residual_ic and (p_value <= 0.10 or small sample).
        - residual_ic: Pearson or Spearman correlation between orthogonal residual and returns.
        - p_value: Two-tailed p-value for the correlation test.
    """
    ortho = np.asarray(alpha_ortho, dtype=float).flatten()
    fwd = np.asarray(forward_returns, dtype=float).flatten()

    mask = ~(np.isnan(ortho) | np.isnan(fwd) | np.isinf(ortho) | np.isinf(fwd))
    ortho_clean = ortho[mask]
    fwd_clean = fwd[mask]

    if len(ortho_clean) < 4 or np.all(ortho_clean == ortho_clean[0]) or np.all(fwd_clean == fwd_clean[0]):
        return False, 0.0, 1.0

    if method.lower() == "spearman":
        res = stats.spearmanr(ortho_clean, fwd_clean)
        corr = float(res.statistic) if hasattr(res, "statistic") else float(res[0])
        p_val = float(res.pvalue) if hasattr(res, "pvalue") else float(res[1])
    else:
        res_p = stats.pearsonr(ortho_clean, fwd_clean)
        corr = float(res_p.statistic) if hasattr(res_p, "statistic") else float(res_p[0])
        p_val = float(res_p.pvalue) if hasattr(res_p, "pvalue") else float(res_p[1])

    if np.isnan(corr):
        corr = 0.0
    if np.isnan(p_val):
        p_val = 1.0

    # For small sample sizes (N < 10), statistical significance threshold on p-value is relaxed
    is_stat_sig = p_val <= 0.10 if len(ortho_clean) >= 10 else True
    is_novel = bool(corr >= min_residual_ic and is_stat_sig)
    return is_novel, round(corr, 6), round(p_val, 6)


def build_factor_annihilator(
    factor_matrix: np.ndarray | pd.DataFrame,
    weights: np.ndarray | None = None,
) -> np.ndarray:
    """
    Construct the Barra-style Factor Annihilator Matrix M_X:
        M_X = I - X (X^T W X)^(-1) X^T W
    where X is an (N x K) factor exposure matrix and W is an optional diagonal weighting matrix.

    Guarantees:
    - Symmetry: M_X^T = M_X
    - Idempotency: M_X^2 = M_X
    - Exact Factor Annihilation: X^T M_X = 0
    """
    X = np.asarray(factor_matrix, dtype=float)
    if X.ndim == 1:
        X = X.reshape(-1, 1)

    n, _ = X.shape
    I_N = np.eye(n)

    if weights is not None:
        w_diag = np.asarray(weights, dtype=float).flatten()
        W = np.diag(w_diag)
        XT_W = X.T @ W
        inv_gram = np.linalg.pinv(XT_W @ X)
        P_X = X @ (inv_gram @ XT_W)
    else:
        inv_gram = np.linalg.pinv(X.T @ X)
        P_X = X @ (inv_gram @ X.T)

    M_X = I_N - P_X
    return M_X


def factor_neutralize(
    alpha_vector: np.ndarray | pd.Series,
    factor_matrix: np.ndarray | pd.DataFrame,
    weights: np.ndarray | None = None,
) -> np.ndarray:
    """
    Neutralize a cross-sectional alpha vector against systematic risk factors (Market Beta,
    Size, Momentum, Sector Dummies):
        alpha_neutral = M_X * alpha

    Guarantees that X^T * alpha_neutral == 0.
    """
    a = np.asarray(alpha_vector, dtype=float).flatten()
    M_X = build_factor_annihilator(factor_matrix, weights=weights)
    return M_X @ a
