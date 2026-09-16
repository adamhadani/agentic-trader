from __future__ import annotations

import math
from numbers import Integral, Real

import numpy as np
import pandas as pd
from scipy import stats


def observed_return_values(returns: pd.Series) -> np.ndarray:
    """Require the complete supplied execution clock; never compress missing bars."""
    if (
        returns.empty
        or returns.index.hasnans
        or not returns.index.is_unique
        or not returns.index.is_monotonic_increasing
    ):
        raise ValueError("Unique, ordered return observations required")
    values = returns.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("Finite return observations required; missing valuations cannot be dropped")
    return values


def calculate_rank_ic(
    alpha_scores: pd.Series,
    forward_returns: pd.Series,
    window: int = 20,
) -> tuple[float, float, float]:
    """
    Calculate Rolling Spearman Rank Information Coefficient (IC).
    Returns (ic_mean, ic_std, ic_ir).
    """
    valid = pd.DataFrame({"alpha": alpha_scores, "fwd": forward_returns}).dropna()
    if len(valid) < max(window, 10):
        return 0.0, 0.0, 0.0

    # Align rolling Spearman correlation
    def _spearman(df_sub: pd.DataFrame) -> float:
        a = df_sub["alpha"]
        b = df_sub["fwd"]
        if a.std() == 0 or b.std() == 0:
            return 0.0
        corr, _ = stats.spearmanr(a, b)
        return 0.0 if np.isnan(corr) else float(corr)

    # Compute expanding or rolling window correlation
    n = len(valid)
    step = max(5, window // 4)
    ics: list[float] = []
    for start in range(0, n - window + 1, step):
        chunk = valid.iloc[start : start + window]
        ic_val = _spearman(chunk)
        ics.append(ic_val)

    if not ics:
        # Full sample fallback
        corr, _ = stats.spearmanr(valid["alpha"], valid["fwd"])
        full_ic = 0.0 if np.isnan(corr) else float(corr)
        return full_ic, 0.0, full_ic

    ic_arr = np.array(ics)
    ic_mean = float(np.mean(ic_arr))
    ic_std = float(np.std(ic_arr))
    ic_ir = ic_mean / (ic_std + 1e-6) if ic_std > 0 else ic_mean
    return ic_mean, ic_std, ic_ir


def calculate_deflated_sharpe_ratio(
    sharpe: float,
    num_trials: int,
    variance_trials: float,
    sample_length: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """
    Compute Deflated Sharpe Ratio (DSR) based on Bailey & López de Prado (2014).
    Sharpe and trial variance MUST be in per-observation units, never annualized.
    Adjusts the estimated Sharpe ratio for:
    1. Multiple testing across `num_trials`
    2. Variance of tested trial Sharpe ratios
    3. Non-normality (skewness and excess kurtosis)
    4. Sample length in observations.

    Returns probability in [0.0, 1.0]. This model-dependent probability is a screening statistic, not a calibrated
    guarantee under serial dependence; block-bootstrap evidence is also required.
    """
    if (
        not all(
            isinstance(x, Real) and not isinstance(x, bool) and math.isfinite(x)
            for x in (sharpe, variance_trials, skewness, kurtosis)
        )
        or variance_trials < 0
        or kurtosis < 1
    ):
        raise ValueError("Invalid Sharpe sampling moments")
    if (
        not isinstance(num_trials, Integral)
        or isinstance(num_trials, bool)
        or num_trials < 1
        or not isinstance(sample_length, Integral)
        or isinstance(sample_length, bool)
        or sample_length < 0
    ):
        raise ValueError("Integer trial and sample counts required")
    if sample_length <= 1:
        return 0.0
    denom = 1.0 - skewness * sharpe + ((kurtosis - 1.0) / 4.0) * sharpe**2
    if not math.isfinite(denom) or denom <= 0:
        raise ValueError("Invalid Sharpe sampling variance")
    std_sr = math.sqrt(denom / (sample_length - 1))

    if num_trials == 1 or variance_trials <= 0.0:
        # Single trial: test against zero benchmark
        return float(stats.norm.cdf(sharpe / std_sr))

    # Euler-Mascheroni constant
    gamma_em = 0.57721566490153286

    # Expected maximum Sharpe ratio under the null hypothesis
    z_inv = stats.norm.ppf(1.0 - 1.0 / num_trials) if num_trials > 1 else 0.0
    z_inv_e = stats.norm.ppf(1.0 - 1.0 / (num_trials * math.e)) if num_trials > 1 else 0.0
    expected_max_sr = math.sqrt(variance_trials) * ((1.0 - gamma_em) * z_inv + gamma_em * z_inv_e)

    z_stat = (sharpe - expected_max_sr) / std_sr
    dsr = float(stats.norm.cdf(z_stat))
    return min(1.0, max(0.0, dsr))


def calculate_cross_strategy_correlations(
    candidate_returns: pd.Series,
    benchmark_returns: dict[str, pd.Series],
) -> dict[str, float]:
    """Calculate Pearson correlation of candidate alpha returns with active desk strategies."""
    corrs: dict[str, float] = {}
    for name, b_ret in benchmark_returns.items():
        aligned = pd.DataFrame({"cand": candidate_returns, "bench": b_ret}).dropna()
        if len(aligned) > 5 and aligned["cand"].std() > 0 and aligned["bench"].std() > 0:
            corr = float(aligned["cand"].corr(aligned["bench"]))
            corrs[name] = round(0.0 if np.isnan(corr) else corr, 3)
        else:
            raise ValueError(f"Insufficient aligned correlation evidence: {name}")
    return corrs
