"""Causal series operators. Undefined observations remain NaN throughout the DSL."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd


MAX_LOOKBACK = 2520


def ts_rank(x: pd.Series, d: int) -> pd.Series:
    def last_rank(w):
        return 0.5 if d == 1 else float(((w < w[-1]).sum() + ((w == w[-1]).sum() - 1) / 2) / (d - 1))

    return x.rolling(d, min_periods=d).apply(last_rank, raw=True)


def ts_corr(x: pd.Series, y: pd.Series, d: int) -> pd.Series:
    return x.rolling(d, min_periods=d).corr(y).clip(-1, 1)


def ts_std(x: pd.Series, d: int) -> pd.Series:
    return x.rolling(d, min_periods=d).std()


def ts_mean(x: pd.Series, d: int) -> pd.Series:
    return x.rolling(d, min_periods=d).mean()


sma = ts_mean


def ema(x: pd.Series, d: int) -> pd.Series:
    return x.ewm(span=d, min_periods=d, adjust=False, ignore_na=False).mean().where(x.notna())


def ts_max(x: pd.Series, d: int) -> pd.Series:
    return x.rolling(d, min_periods=d).max()


def ts_min(x: pd.Series, d: int) -> pd.Series:
    return x.rolling(d, min_periods=d).min()


def ts_argmax(x: pd.Series, d: int) -> pd.Series:
    return x.rolling(d, min_periods=d).apply(lambda w: np.argmax(w) / max(1, d - 1), raw=True)


def ts_argmin(x: pd.Series, d: int) -> pd.Series:
    return x.rolling(d, min_periods=d).apply(lambda w: np.argmin(w) / max(1, d - 1), raw=True)


def delta(x: pd.Series, d: int = 1) -> pd.Series:
    return x.diff(d)


def delay(x: pd.Series, d: int = 1) -> pd.Series:
    return x.shift(d)


def decay_linear(x: pd.Series, d: int) -> pd.Series:
    weights = np.arange(1, d + 1, dtype=float)
    return x.rolling(d, min_periods=d).apply(lambda w: np.dot(w, weights) / weights.sum(), raw=True)


def zscore(x: pd.Series, d: int = 20) -> pd.Series:
    return safe_div(x - ts_mean(x, d), ts_std(x, d)).clip(-3, 3)


def sign(x):
    return np.sign(x)


def safe_div(x, y):
    if isinstance(y, pd.Series):
        return x / y.where(y != 0)
    return x / y if y != 0 else x * np.nan


def safe_log(x):
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.log(np.where(x > 0, x, np.nan))


def safe_sqrt(x):
    with np.errstate(invalid="ignore"):
        return np.sqrt(np.where(x >= 0, x, np.nan))


def cond(test, true_val, false_val):
    return np.where(pd.isna(test), np.nan, np.where(test > 0, true_val, false_val))


@dataclass(frozen=True)
class OperatorSpec:
    function: Callable
    minimum_args: int
    maximum_args: int
    window_argument: int | None = None
    # dimensional result: preserve first argument, dimensionless, or branch units
    result_units: str = "preserve"


OPERATOR_SPECS = {
    "ts_rank": OperatorSpec(ts_rank, 2, 2, 1, "dimensionless"),
    "ts_corr": OperatorSpec(ts_corr, 3, 3, 2, "dimensionless"),
    "ts_std": OperatorSpec(ts_std, 2, 2, 1),
    "ts_mean": OperatorSpec(ts_mean, 2, 2, 1),
    "sma": OperatorSpec(sma, 2, 2, 1),
    "ema": OperatorSpec(ema, 2, 2, 1),
    "ts_max": OperatorSpec(ts_max, 2, 2, 1),
    "ts_min": OperatorSpec(ts_min, 2, 2, 1),
    "ts_argmax": OperatorSpec(ts_argmax, 2, 2, 1, "dimensionless"),
    "ts_argmin": OperatorSpec(ts_argmin, 2, 2, 1, "dimensionless"),
    "delta": OperatorSpec(delta, 1, 2, 1),
    "delay": OperatorSpec(delay, 1, 2, 1),
    "decay_linear": OperatorSpec(decay_linear, 2, 2, 1),
    "zscore": OperatorSpec(zscore, 1, 2, 1, "dimensionless"),
    "sign": OperatorSpec(sign, 1, 1, result_units="dimensionless"),
    "abs": OperatorSpec(np.abs, 1, 1),
    "log": OperatorSpec(safe_log, 1, 1, result_units="dimensionless"),
    "sqrt": OperatorSpec(safe_sqrt, 1, 1, result_units="sqrt"),
    "cond": OperatorSpec(cond, 3, 3, result_units="branches"),
    "if_else": OperatorSpec(cond, 3, 3, result_units="branches"),
    "min": OperatorSpec(np.minimum, 2, 2, result_units="matching"),
    "max": OperatorSpec(np.maximum, 2, 2, result_units="matching"),
}


def roc(x: pd.Series, d: int) -> pd.Series:
    return safe_div(x, x.shift(d)) - 1


def ts_slope(x: pd.Series, d: int) -> pd.Series:
    t = np.arange(d, dtype=float)
    t -= t.mean()
    denominator = t @ t
    return x.rolling(d, min_periods=d).apply(
        lambda w: float(t @ w / denominator) if denominator > 0 else np.nan, raw=True
    )


def ts_residual(x: pd.Series, d: int) -> pd.Series:
    return x - ts_mean(x, d) - ts_slope(x, d) * (d - 1) / 2


def ts_mad(x: pd.Series, d: int) -> pd.Series:
    return x.rolling(d, min_periods=d).apply(lambda w: np.median(abs(w - np.median(w))), raw=True)


def ts_sum(x: pd.Series, d: int) -> pd.Series:
    return x.rolling(d, min_periods=d).sum()


OPERATOR_SPECS.update(
    {
        "roc": OperatorSpec(roc, 2, 2, 1, "dimensionless"),
        "realized_vol": OperatorSpec(ts_std, 2, 2, 1),
        "ts_slope": OperatorSpec(ts_slope, 2, 2, 1),
        "ts_residual": OperatorSpec(ts_residual, 2, 2, 1),
        "ts_mad": OperatorSpec(ts_mad, 2, 2, 1),
        "ts_sum": OperatorSpec(ts_sum, 2, 2, 1),
        "clip": OperatorSpec(np.clip, 3, 3, result_units="clip"),
    }
)
ALPHA_OPERATORS = {name: spec.function for name, spec in OPERATOR_SPECS.items()}
