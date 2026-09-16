from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats


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
    return round(ic_mean, 4), round(ic_std, 4), round(ic_ir, 4)


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
    Adjusts the estimated Sharpe ratio for:
    1. Multiple testing across `num_trials`
    2. Variance of tested trial Sharpe ratios
    3. Non-normality (skewness and excess kurtosis)
    4. Sample length in observations.

    Returns probability in [0.0, 1.0]. A value >= 0.95 indicates statistical significance at 95% confidence.
    """
    if sample_length <= 1 or num_trials <= 0:
        return 0.0

    if num_trials == 1 or variance_trials <= 0.0:
        # Single trial: test against zero benchmark
        std_sr = math.sqrt((1.0 - skewness * sharpe + ((kurtosis - 1.0) / 4.0) * (sharpe**2)) / sample_length)
        if std_sr <= 0:
            return 1.0 if sharpe > 0 else 0.0
        return float(stats.norm.cdf(sharpe / std_sr))

    # Euler-Mascheroni constant
    gamma_em = 0.57721566490153286

    # Expected maximum Sharpe ratio under the null hypothesis
    z_inv = stats.norm.ppf(1.0 - 1.0 / num_trials) if num_trials > 1 else 0.0
    z_inv_e = stats.norm.ppf(1.0 - 1.0 / (num_trials * math.e)) if num_trials > 1 else 0.0
    expected_max_sr = math.sqrt(variance_trials) * ((1.0 - gamma_em) * z_inv + gamma_em * z_inv_e)

    # Standard error of Sharpe ratio accounting for skewness & kurtosis
    denom = 1.0 - skewness * sharpe + ((kurtosis - 1.0) / 4.0) * (sharpe**2)
    std_sr = math.sqrt(max(1e-8, denom) / sample_length)

    z_stat = (sharpe - expected_max_sr) / std_sr
    dsr = float(stats.norm.cdf(z_stat))
    return round(min(1.0, max(0.0, dsr)), 4)


def simulate_alpha_performance(
    alpha_scores: pd.Series,
    prices: pd.Series,
    entry_threshold: float = 1.5,
    exit_threshold: float = 0.0,
    direction: str = "bi_directional",
    friction_pct: float = 0.0005,
    annual_factor: float = 252.0 * 6.5 / 4.0,  # default ~4h bars per year (~409)
) -> dict[str, Any]:
    """
    Simulate vectorized trade returns for an alpha signal.
    Normalizes alpha scores via rolling z-score, enters on threshold crossing,
    exits when signal decays back across exit_threshold.
    """
    clean_alpha = alpha_scores.fillna(0.0)
    close = prices.astype(float)

    # Normalize alpha to z-score
    rolling_mean = clean_alpha.rolling(50, min_periods=10).mean()
    rolling_std = clean_alpha.rolling(50, min_periods=10).std().replace(0.0, 1e-6)
    z_alpha = ((clean_alpha - rolling_mean) / rolling_std).fillna(0.0)

    pos = 0.0
    positions: list[float] = []

    for z in z_alpha.values:
        if pos == 0.0:
            if z >= entry_threshold and direction in ("long", "bi_directional"):
                pos = 1.0
            elif z <= -entry_threshold and direction in ("short", "bi_directional"):
                pos = -1.0
        elif (pos == 1.0 and z <= exit_threshold) or (pos == -1.0 and z >= -exit_threshold):
            pos = 0.0
        positions.append(pos)

    pos_series = pd.Series(positions, index=close.index)
    price_returns = close.pct_change().fillna(0.0)

    # Shift positions by 1 bar to prevent lookahead bias
    trade_returns = pos_series.shift(1).fillna(0.0) * price_returns

    # Deduct transaction friction on position changes
    pos_changes = pos_series.diff().abs().fillna(0.0)
    net_returns = trade_returns - (pos_changes * friction_pct)

    # Metrics
    cum_returns = (1.0 + net_returns).cumprod()
    total_return_pct = float((cum_returns.iloc[-1] - 1.0) * 100.0) if len(cum_returns) > 0 else 0.0

    ret_mean = float(net_returns.mean())
    ret_std = float(net_returns.std())
    sharpe = float((ret_mean / ret_std) * math.sqrt(annual_factor)) if ret_std > 0 else 0.0

    # Drawdown
    peak = cum_returns.cummax()
    dd_series = (cum_returns - peak) / peak.replace(0.0, 1.0)
    max_dd_pct = float(abs(dd_series.min()) * 100.0)

    # Trade counts & win rate
    trade_starts = (pos_series != 0) & (pos_series.shift(1) == 0)
    total_trades = int(trade_starts.sum())

    gains = net_returns[net_returns > 0].sum()
    losses = abs(net_returns[net_returns < 0].sum())
    profit_factor = float(gains / losses) if losses > 0 else (2.0 if gains > 0 else 0.0)
    win_rate = float((net_returns > 0).sum() / max(1, (net_returns != 0).sum()))

    skewness = float(stats.skew(net_returns.dropna())) if len(net_returns) > 3 else 0.0
    kurt = float(stats.kurtosis(net_returns.dropna(), fisher=False)) if len(net_returns) > 3 else 3.0

    return {
        "sharpe": round(sharpe, 2),
        "total_return_pct": round(total_return_pct, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "total_trades": total_trades,
        "profit_factor": round(profit_factor, 2),
        "win_rate": round(win_rate, 2),
        "skewness": round(skewness, 2),
        "kurtosis": round(kurt, 2),
        "sample_length": len(net_returns),
        "net_returns": net_returns,
    }


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
            corrs[name] = 0.0
    return corrs
