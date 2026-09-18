from __future__ import annotations

import math
import warnings
from statistics import NormalDist

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.alpha.metrics import (
    calculate_cross_strategy_correlations,
    calculate_deflated_sharpe_ratio,
    calculate_rank_ic,
)


def test_rank_ic_perfect_and_inverse():
    n = 100
    dates = pd.date_range("2025-01-01", periods=n, freq="D")
    alpha = pd.Series(np.linspace(1.0, 100.0, n), index=dates)
    fwd_perf = pd.Series(np.linspace(0.01, 0.10, n), index=dates)

    ic_mean, _, _ = calculate_rank_ic(alpha, fwd_perf, window=20)
    assert ic_mean > 0.90, f"Expected high positive Rank IC, got {ic_mean}"

    # Inverse correlation
    fwd_inv = -fwd_perf
    ic_mean_inv, _, _ = calculate_rank_ic(alpha, fwd_inv, window=20)
    assert ic_mean_inv < -0.90, f"Expected high negative Rank IC, got {ic_mean_inv}"


@pytest.mark.parametrize(
    "alpha,forward",
    [
        (np.ones(100), np.linspace(0.01, 0.10, 100)),
        (np.linspace(1.0, 100.0, 100), np.ones(100)),
    ],
)
def test_rank_ic_constant_input_returns_zero_without_scipy_warning(alpha, forward):
    dates = pd.date_range("2025-01-01", periods=100, freq="D")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = calculate_rank_ic(pd.Series(alpha, index=dates), pd.Series(forward, index=dates), window=20)

    assert result == (0.0, 0.0, 0.0)
    assert not any("ConstantInputWarning" in str(item.message) for item in caught)


def test_deflated_sharpe_ratio_behavior():
    # 1. Strong strategy discovered with minimal multiple testing
    dsr_strong = calculate_deflated_sharpe_ratio(
        sharpe=2.5,
        num_trials=5,
        variance_trials=0.10,
        sample_length=500,
    )
    assert dsr_strong >= 0.95, f"Expected strong DSR >= 0.95, got {dsr_strong}"

    # 2. Modest strategy (Sharpe 1.2) heavily mined over 5,000 trials with high variance
    dsr_mined = calculate_deflated_sharpe_ratio(
        sharpe=1.2,
        num_trials=5000,
        variance_trials=0.80,
        sample_length=252,
    )
    assert dsr_mined < 0.50, f"Expected overfitted mined strategy DSR < 0.50, got {dsr_mined}"

    # 3. Effect of negative skewness and fat-tailed kurtosis
    dsr_normal = calculate_deflated_sharpe_ratio(
        sharpe=0.85,
        num_trials=10,
        variance_trials=0.2,
        sample_length=252,
        skewness=0.0,
        kurtosis=3.0,
    )
    dsr_fat_tails = calculate_deflated_sharpe_ratio(
        sharpe=0.85,
        num_trials=10,
        variance_trials=0.2,
        sample_length=252,
        skewness=-1.5,
        kurtosis=8.0,
    )
    assert dsr_fat_tails < dsr_normal, (
        f"Negative skew & fat tails should deflate Sharpe confidence ({dsr_fat_tails} vs {dsr_normal})"
    )


def test_calculate_cross_strategy_correlations():
    n = 100
    dates = pd.date_range("2025-01-01", periods=n, freq="D")
    alpha_rets = pd.Series(np.random.randn(n) * 0.01, index=dates)

    existing_strats = {
        "trend_pullback": alpha_rets * 0.8 + np.random.randn(n) * 0.002,
        "squeeze_breakout": np.random.randn(n) * 0.01,
    }

    corrs = calculate_cross_strategy_correlations(alpha_rets, existing_strats)
    assert "trend_pullback" in corrs
    assert "squeeze_breakout" in corrs
    assert corrs["trend_pullback"] > 0.60
    assert abs(corrs["squeeze_breakout"]) < 0.50


@pytest.mark.parametrize(
    "sr,count,variance,length,skew,kurt",
    [
        (0.12, 100, 0.003, 500, -0.2, 4),
        (0.25, 7049, 0.002876, 1000, 0.3, 5),
        (0.05, 1, 0, 252, 0, 3),
    ],
)
def test_dsr_matches_independent_reference_with_multiple_trials(sr, count, variance, length, skew, kurt):
    normal = NormalDist()
    benchmark = (
        math.sqrt(variance)
        * (
            (1 - 0.5772156649015329) * normal.inv_cdf(1 - 1 / count)
            + 0.5772156649015329 * normal.inv_cdf(1 - 1 / (count * math.e))
        )
        if count > 1 and variance > 0
        else 0
    )
    error = math.sqrt((1 - skew * sr + (kurt - 1) * sr**2 / 4) / (length - 1))
    assert calculate_deflated_sharpe_ratio(sr, count, variance, length, skew, kurt) == pytest.approx(
        normal.cdf((sr - benchmark) / error), abs=1e-12
    )


@pytest.mark.parametrize(
    "count,length,skew,kurt",
    [
        (True, 100, 0, 3),
        (1.5, 100, 0, 3),
        (10, 100.5, 0, 3),
        (10, 100, 10, 1),
        (1, 100, 10, 1),
    ],
)
def test_dsr_rejects_invalid_sampling_variance_and_fractional_budgets(count, length, skew, kurt):
    with pytest.raises(ValueError):
        calculate_deflated_sharpe_ratio(0.2, count, 0.003, length, skew, kurt)
