"""Cointegration analysis, Ornstein-Uhlenbeck half-life estimation, and spread signals."""

from __future__ import annotations

import math
from datetime import datetime

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.tsa.stattools import adfuller

from agentic_trader.pairs.models import CointegrationResult, SignalType, SpreadSignal


def compute_half_life(residuals: pd.Series) -> float:
    """Compute the Ornstein-Uhlenbeck mean-reversion half-life of a residual spread.

    Fits the continuous-time Ornstein-Uhlenbeck / discrete AR(1) process:
        Δε_t = θ * ε_{t-1} + c + η_t

    If θ < 0 (mean reverting):
        Half-life = -ln(2) / θ
    If θ >= 0 (explosive or pure random walk):
        Half-life = infinity
    """
    cleaned = residuals.dropna()
    if len(cleaned) < 10:
        return float("inf")

    delta_res = cleaned.diff().dropna()
    lagged_res = cleaned.shift(1).dropna()

    aligned = pd.concat([delta_res, lagged_res], axis=1).dropna()
    if len(aligned) < 5:
        return float("inf")

    y = aligned.iloc[:, 0]
    x = sm.add_constant(aligned.iloc[:, 1])

    ar_model = sm.OLS(y, x).fit()
    # param index 1 is lagged_res slope θ
    theta = float(ar_model.params.iloc[1])

    if theta < -1e-6:
        half_life = -math.log(2.0) / theta
        return float(half_life)
    return float("inf")


def run_engle_granger_test(
    series_y: pd.Series,
    series_x: pd.Series,
    asset_y_name: str | None = None,
    asset_x_name: str | None = None,
    p_value_threshold: float = 0.05,
) -> CointegrationResult:
    """Execute two-step Engle-Granger cointegration test.

    Step 1: OLS regression of Y on X -> Y_t = β * X_t + α + ε_t
    Step 2: Augmented Dickey-Fuller test on residuals ε_t
    Step 3: Ornstein-Uhlenbeck half-life of mean-reversion estimation
    """
    y_name = asset_y_name or str(series_y.name or "Asset_Y")
    x_name = asset_x_name or str(series_x.name or "Asset_X")

    aligned = pd.concat([series_y, series_x], axis=1).dropna()
    if len(aligned) < 20:
        return CointegrationResult(
            asset_y=y_name,
            asset_x=x_name,
            hedge_ratio_beta=1.0,
            intercept_alpha=0.0,
            adf_statistic=0.0,
            p_value=1.0,
            critical_values={},
            half_life_bars=float("inf"),
            is_cointegrated=False,
        )

    clean_y = aligned.iloc[:, 0]
    clean_x = aligned.iloc[:, 1]

    # Step 1: OLS regression Y = β*X + α
    x_with_const = sm.add_constant(clean_x)
    ols_res = sm.OLS(clean_y, x_with_const).fit()

    alpha = float(ols_res.params.iloc[0])
    beta = float(ols_res.params.iloc[1])

    residuals = clean_y - (beta * clean_x + alpha)

    # Step 2: ADF test on residuals (constant only, autolag='AIC')
    adf_res = adfuller(residuals, autolag="AIC", result_object=False)
    adf_stat = float(adf_res[0])
    p_val = float(adf_res[1])
    crit_vals = {str(k): float(v) for k, v in adf_res[4].items()}

    # Step 3: Mean reversion half-life
    half_life = compute_half_life(residuals)

    is_coint = p_val < p_value_threshold

    return CointegrationResult(
        asset_y=y_name,
        asset_x=x_name,
        hedge_ratio_beta=beta,
        intercept_alpha=alpha,
        adf_statistic=adf_stat,
        p_value=p_val,
        critical_values=crit_vals,
        half_life_bars=half_life,
        is_cointegrated=is_coint,
    )


# Backward-compatible alias
test_engle_granger = run_engle_granger_test


def calculate_rolling_spread_zscore(
    series_y: pd.Series,
    series_x: pd.Series,
    beta: float,
    alpha: float,
    lookback: int = 30,
) -> pd.DataFrame:
    """Compute instantaneous spread S_t = Y_t - (β * X_t + α) and rolling Z-score."""
    aligned = pd.concat([series_y, series_x], axis=1).dropna()
    clean_y = aligned.iloc[:, 0]
    clean_x = aligned.iloc[:, 1]

    spread = clean_y - (beta * clean_x + alpha)
    min_periods = max(5, lookback // 2)

    rolling_mean = spread.rolling(window=lookback, min_periods=min_periods).mean()
    rolling_std = spread.rolling(window=lookback, min_periods=min_periods).std()

    # Guard against zero variance
    safe_std = rolling_std.replace(0.0, np.nan)
    z_score = (spread - rolling_mean) / safe_std

    df = pd.DataFrame(
        {
            "spread": spread,
            "spread_mean": rolling_mean,
            "spread_std": rolling_std,
            "z_score": z_score,
        },
        index=aligned.index,
    )
    return df


def generate_spread_signal(
    pair_name: str,
    timestamp: datetime,
    current_spread: float,
    spread_mean: float,
    spread_std: float,
    z_score: float,
    z_entry: float = 2.0,
    z_exit: float = 0.5,
) -> SpreadSignal:
    """Evaluate current Z-score against statistical arbitrage entry and exit bands."""
    if math.isnan(z_score) or math.isnan(spread_std) or spread_std <= 0:
        return SpreadSignal(
            pair_name=pair_name,
            timestamp=timestamp,
            current_spread=current_spread,
            spread_mean=spread_mean,
            spread_std=spread_std,
            z_score=0.0,
            signal=SignalType.NEUTRAL,
            asset_y_action="HOLD",
            asset_x_action="HOLD",
            summary="Insufficient variance or history for Z-score evaluation",
        )

    if z_score <= -z_entry:
        return SpreadSignal(
            pair_name=pair_name,
            timestamp=timestamp,
            current_spread=current_spread,
            spread_mean=spread_mean,
            spread_std=spread_std,
            z_score=z_score,
            signal=SignalType.BUY_SPREAD,
            asset_y_action="BUY",
            asset_x_action="SELL",
            summary=f"Undervalued spread (Z={z_score:.2f} <= -{z_entry:.1f}): Long {pair_name.split('/', maxsplit=1)[0]} / Short {pair_name.split('/')[1]}",
        )
    elif z_score >= z_entry:
        return SpreadSignal(
            pair_name=pair_name,
            timestamp=timestamp,
            current_spread=current_spread,
            spread_mean=spread_mean,
            spread_std=spread_std,
            z_score=z_score,
            signal=SignalType.SELL_SPREAD,
            asset_y_action="SELL",
            asset_x_action="BUY",
            summary=f"Overvalued spread (Z={z_score:.2f} >= +{z_entry:.1f}): Short {pair_name.split('/', maxsplit=1)[0]} / Long {pair_name.split('/')[1]}",
        )
    elif abs(z_score) <= z_exit:
        return SpreadSignal(
            pair_name=pair_name,
            timestamp=timestamp,
            current_spread=current_spread,
            spread_mean=spread_mean,
            spread_std=spread_std,
            z_score=z_score,
            signal=SignalType.EXIT_SPREAD,
            asset_y_action="EXIT",
            asset_x_action="EXIT",
            summary=f"Mean-reverted spread (|Z|={abs(z_score):.2f} <= {z_exit:.1f}): Exit open position",
        )
    else:
        return SpreadSignal(
            pair_name=pair_name,
            timestamp=timestamp,
            current_spread=current_spread,
            spread_mean=spread_mean,
            spread_std=spread_std,
            z_score=z_score,
            signal=SignalType.NEUTRAL,
            asset_y_action="HOLD",
            asset_x_action="HOLD",
            summary=f"Neutral holding band (Z={z_score:.2f})",
        )
