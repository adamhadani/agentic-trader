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
    _require_alignment(candidate, incumbents)
    cand = np.asarray(candidate, dtype=float).flatten()
    if not np.isfinite(cand).all():
        raise ValueError("Nonfinite candidate observations")
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

    if A.ndim != 2 or A.shape[0] != n or not np.isfinite(A).all():
        raise ValueError("Invalid or unaligned incumbent observations")
    if A.size == 0:
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
        - is_novel: True if residual IC >= min_residual_ic and p_value <= 0.10 with at least 30 observations.
        - residual_ic: Pearson or Spearman correlation between orthogonal residual and returns.
        - p_value: Two-tailed p-value for the correlation test.
    """
    _require_alignment(alpha_ortho, forward_returns)
    ortho = np.asarray(alpha_ortho, dtype=float).flatten()
    fwd = np.asarray(forward_returns, dtype=float).flatten()

    if ortho.shape != fwd.shape or method not in ("pearson", "spearman"):
        raise ValueError("Invalid residual comparison")
    mask = ~(np.isnan(ortho) | np.isnan(fwd) | np.isinf(ortho) | np.isinf(fwd))
    ortho_clean = ortho[mask]
    fwd_clean = fwd[mask]

    if len(ortho_clean) < 4 or np.std(ortho_clean) <= 1e-10 or np.all(fwd_clean == fwd_clean[0]):
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

    # Tiny or numerically degenerate residuals are not independent evidence.
    is_stat_sig = len(ortho_clean) >= 30 and p_val <= 0.10
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
    - Idempotency: M_X^2 = M_X
    - Weighted annihilation: X^T W M_X = 0
    - Symmetry/unweighted annihilation only when W is the identity.
    """
    X = np.asarray(factor_matrix, dtype=float)
    if X.ndim == 1:
        X = X.reshape(-1, 1)

    if X.ndim != 2 or not np.isfinite(X).all():
        raise ValueError("Invalid factor observations")
    n, _ = X.shape
    I_N = np.eye(n)

    if weights is not None:
        w_diag = np.asarray(weights, dtype=float).flatten()
        if w_diag.shape != (n,) or not np.isfinite(w_diag).all() or (w_diag <= 0).any():
            raise ValueError("WLS weights must be aligned, finite and positive")
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

    Guarantees X^T W * alpha_neutral == 0; WLS residuals are not portfolio weights.
    """
    _require_alignment(alpha_vector, factor_matrix)
    a = np.asarray(alpha_vector, dtype=float).flatten()
    if not np.isfinite(a).all():
        raise ValueError("Nonfinite alpha vector")
    M_X = build_factor_annihilator(factor_matrix, weights=weights)
    return M_X @ a


def _require_alignment(left, right):
    if (
        isinstance(left, (pd.Series, pd.DataFrame))
        and isinstance(right, (pd.Series, pd.DataFrame))
        and not left.index.equals(right.index)
    ):
        raise ValueError("Observation labels must align exactly")


def residual_validation(
    candidate: pd.Series, incumbents: pd.DataFrame, forward_returns: pd.Series, *, train_end: int, validation_start: int
) -> dict:
    """Fit redundancy loadings on training observations, test only subsequent labels."""
    _require_alignment(candidate, incumbents)
    _require_alignment(candidate, forward_returns)
    if not 0 < train_end <= validation_start < len(candidate):
        raise ValueError("Invalid residual validation boundary")
    joined = pd.concat([candidate.rename("candidate"), incumbents, forward_returns.rename("forward_return")], axis=1)
    train = joined.iloc[:train_end].dropna()
    validation = joined.iloc[validation_start:].dropna()
    if len(train) < max(30, 10 * (len(incumbents.columns) + 1)) or len(validation) < 30:
        raise ValueError("Insufficient aligned incremental evidence")
    columns = list(incumbents.columns)
    design = np.column_stack([np.ones(len(train)), train[columns].to_numpy()])
    beta = np.linalg.lstsq(design, train.candidate.to_numpy(), rcond=None)[0]
    residual = (
        validation.candidate.to_numpy()
        - np.column_stack([np.ones(len(validation)), validation[columns].to_numpy()]) @ beta
    )
    novel, ic, p_value = evaluate_residual_predictive_power(residual, validation.forward_return.to_numpy())
    return {
        "novel": novel,
        "residual_ic": ic,
        "p_value": p_value,
        "training_observations": len(train),
        "validation_observations": len(validation),
        "coefficients": beta.tolist(),
    }
