import numpy as np
import pandas as pd


def calculate_ema(series: pd.Series, span: int) -> pd.Series:
    """Calculate Exponential Moving Average."""
    return series.ewm(span=span, adjust=False).mean()


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Relative Strength Index (RSI) using Wilder's smoothing."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    # Wilder's smoothing: alpha = 1 / period
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    # Handle division by zero cleanly
    rs = np.where(avg_loss == 0, np.where(avg_gain == 0, 1.0, np.inf), avg_gain / avg_loss)
    rsi = np.where(np.isinf(rs), 100.0, 100.0 - (100.0 / (1.0 + rs)))
    rsi_series = pd.Series(rsi, index=series.index).fillna(50.0)
    return rsi_series


def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Average True Range (ATR) using Wilder's smoothing."""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    return atr


def calculate_bollinger_bands(
    series: pd.Series, period: int = 20, num_std: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Calculate Bollinger Bands (upper, middle, lower)."""
    middle = series.rolling(window=period).mean()
    std = series.rolling(window=period).std(ddof=0)
    upper = middle + (num_std * std)
    lower = middle - (num_std * std)
    return upper, middle, lower


def calculate_keltner_channels(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 20,
    atr_multiplier: float = 1.5,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Calculate Keltner Channels (upper, middle, lower)."""
    middle = calculate_ema(close, span=period)
    atr = calculate_atr(high, low, close, period=period)
    upper = middle + (atr_multiplier * atr)
    lower = middle - (atr_multiplier * atr)
    return upper, middle, lower


def detect_squeeze(
    bb_upper: pd.Series,
    bb_lower: pd.Series,
    kc_upper: pd.Series,
    kc_lower: pd.Series,
) -> pd.Series:
    """
    Returns boolean Series where True indicates Bollinger Bands are
    completely enclosed within Keltner Channels (volatility squeeze).
    """
    return (bb_upper < kc_upper) & (bb_lower > kc_lower)


def consecutive_squeeze_count(squeeze_series: pd.Series) -> pd.Series:
    """Calculate the running count of consecutive bars where squeeze is active."""
    counts = []
    current = 0
    for val in squeeze_series:
        if bool(val):
            current += 1
        else:
            current = 0
        counts.append(current)
    return pd.Series(counts, index=squeeze_series.index)
