from __future__ import annotations

import numpy as np
import pandas as pd


def _to_series(val: float | pd.Series | np.ndarray, index: pd.Index | None = None) -> pd.Series:
    """Ensure value is a pandas Series with appropriate index."""
    if isinstance(val, pd.Series):
        return val
    if isinstance(val, np.ndarray):
        return pd.Series(val, index=index)
    if index is not None:
        return pd.Series(float(val), index=index)
    return pd.Series([float(val)])


def ts_rank(x: pd.Series, d: int) -> pd.Series:
    """
    Rolling percentile rank of x over window d, normalized to [0.0, 1.0].
    +1.0 means current value is the highest in the window, 0.0 means lowest.
    """
    if d <= 1:
        return pd.Series(0.5, index=x.index)

    def _rank_last(window: np.ndarray) -> float:
        actual_d = len(window)
        if actual_d <= 1:
            return 0.5
        val = window[-1]
        count_less = (window < val).sum()
        count_equal = (window == val).sum() - 1
        pct = (count_less + 0.5 * count_equal) / (actual_d - 1)
        return float(pct)

    res = x.rolling(d, min_periods=1).apply(_rank_last, raw=True)
    return res.fillna(0.5)


def ts_corr(x: pd.Series, y: pd.Series, d: int) -> pd.Series:
    """Rolling Pearson correlation between x and y over window d."""
    if d <= 2:
        return pd.Series(0.0, index=x.index)
    res = x.rolling(d, min_periods=max(3, d // 2)).corr(y)
    return res.replace([np.inf, -np.inf], 0.0).fillna(0.0)


def ts_std(x: pd.Series, d: int) -> pd.Series:
    """Rolling standard deviation of x over window d."""
    if d <= 1:
        return pd.Series(0.0, index=x.index)
    res = x.rolling(d, min_periods=max(2, d // 2)).std()
    return res.fillna(0.0)


def ts_mean(x: pd.Series, d: int) -> pd.Series:
    """Rolling simple moving average of x over window d."""
    if d <= 1:
        return x
    return x.rolling(d, min_periods=1).mean()


def sma(x: pd.Series, d: int) -> pd.Series:
    """Alias for ts_mean."""
    return ts_mean(x, d)


def ema(x: pd.Series, d: int) -> pd.Series:
    """Exponential moving average of x with span d."""
    if d <= 1:
        return x
    return x.ewm(span=d, adjust=False).mean()


def ts_max(x: pd.Series, d: int) -> pd.Series:
    """Rolling maximum of x over window d."""
    return x.rolling(d, min_periods=1).max()


def ts_min(x: pd.Series, d: int) -> pd.Series:
    """Rolling minimum of x over window d."""
    return x.rolling(d, min_periods=1).min()


def ts_argmax(x: pd.Series, d: int) -> pd.Series:
    """
    Relative position of the maximum value in window d, normalized to [0.0, 1.0].
    1.0 means the maximum occurred on the most recent bar; 0.0 means at the start.
    """
    if d <= 1:
        return pd.Series(1.0, index=x.index)

    def _argmax(w: np.ndarray) -> float:
        return float(np.argmax(w)) / float(len(w) - 1 if len(w) > 1 else 1)

    res = x.rolling(d, min_periods=max(2, d // 2)).apply(_argmax, raw=True)
    return res.fillna(0.5)


def ts_argmin(x: pd.Series, d: int) -> pd.Series:
    """
    Relative position of the minimum value in window d, normalized to [0.0, 1.0].
    1.0 means the minimum occurred on the most recent bar; 0.0 means at the start.
    """
    if d <= 1:
        return pd.Series(1.0, index=x.index)

    def _argmin(w: np.ndarray) -> float:
        return float(np.argmin(w)) / float(len(w) - 1 if len(w) > 1 else 1)

    res = x.rolling(d, min_periods=max(2, d // 2)).apply(_argmin, raw=True)
    return res.fillna(0.5)


def delta(x: pd.Series, d: int = 1) -> pd.Series:
    """Difference: x_t - x_{t-d}."""
    return x.diff(d).fillna(0.0)


def delay(x: pd.Series, d: int = 1) -> pd.Series:
    """Lagged series: x_{t-d}."""
    return x.shift(d).bfill().fillna(0.0)


def decay_linear(x: pd.Series, d: int) -> pd.Series:
    """
    Linearly weighted moving average over past d periods with weights d, d-1, ..., 1.
    """
    if d <= 1:
        return x

    weights = np.arange(1, d + 1, dtype=float)
    weights_sum = weights.sum()

    def _weighted_avg(w: np.ndarray) -> float:
        actual_len = len(w)
        if actual_len == d:
            return float(np.dot(w, weights) / weights_sum)
        sub_weights = weights[-actual_len:]
        return float(np.dot(w, sub_weights) / sub_weights.sum())

    res = x.rolling(d, min_periods=1).apply(_weighted_avg, raw=True)
    return res.fillna(x)


def rank(x: pd.Series) -> pd.Series:
    """Percentile rank normalized to [0.0, 1.0]."""
    if len(x) <= 1:
        return pd.Series(0.5, index=x.index)
    return x.rank(pct=True).fillna(0.5)


def zscore(x: pd.Series, d: int = 20) -> pd.Series:
    """Rolling z-score (standardized score) clipped to [-3.0, 3.0]."""
    mean = x.rolling(d, min_periods=max(2, d // 2)).mean()
    std = x.rolling(d, min_periods=max(2, d // 2)).std().replace(0.0, 1e-6)
    res = (x - mean) / std
    return res.fillna(0.0).clip(-3.0, 3.0)


def scale(x: pd.Series, a: float = 1.0) -> pd.Series:
    """Scale series so sum of absolute values equals a."""
    denom = np.abs(x).sum()
    if denom == 0 or np.isnan(denom):
        return x
    return x * (a / denom)


def sign(x: pd.Series | float) -> pd.Series:
    """Sign of elements: +1.0, 0.0, or -1.0."""
    if isinstance(x, (float, int)):
        return pd.Series(float(np.sign(x)))
    return np.sign(x).fillna(0.0)


def safe_div(x: pd.Series, y: pd.Series | float) -> pd.Series:
    """Element-wise division handling zero denominators safely by returning 0.0."""
    if isinstance(y, (float, int)):
        if y == 0:
            return pd.Series(0.0, index=x.index)
        return x / y
    res = x / y.replace(0.0, np.nan)
    return res.fillna(0.0).replace([np.inf, -np.inf], 0.0)


def safe_log(x: pd.Series) -> pd.Series:
    """Element-wise natural logarithm safe for non-positive values."""
    return np.log(np.maximum(x, 1e-8)).fillna(0.0)


def safe_sqrt(x: pd.Series) -> pd.Series:
    """Element-wise square root safe for non-positive values."""
    return np.sqrt(np.maximum(x, 0.0)).fillna(0.0)


def cond(test: pd.Series, true_val: pd.Series | float, false_val: pd.Series | float) -> pd.Series:
    """Conditional selection: where test > 0 return true_val else false_val."""
    t_ser = _to_series(true_val, index=test.index)
    f_ser = _to_series(false_val, index=test.index)
    return pd.Series(np.where(test > 0, t_ser, f_ser), index=test.index)


# Registry of known functions callable within alpha DSL expressions
ALPHA_OPERATORS = {
    "ts_rank": ts_rank,
    "ts_corr": ts_corr,
    "ts_std": ts_std,
    "ts_mean": ts_mean,
    "sma": sma,
    "ema": ema,
    "ts_max": ts_max,
    "ts_min": ts_min,
    "ts_argmax": ts_argmax,
    "ts_argmin": ts_argmin,
    "delta": delta,
    "delay": delay,
    "decay_linear": decay_linear,
    "rank": rank,
    "zscore": zscore,
    "scale": scale,
    "sign": sign,
    "abs": np.abs,
    "log": safe_log,
    "sqrt": safe_sqrt,
    "cond": cond,
    "if_else": cond,
    "min": lambda a, b: np.minimum(a, b),
    "max": lambda a, b: np.maximum(a, b),
}
