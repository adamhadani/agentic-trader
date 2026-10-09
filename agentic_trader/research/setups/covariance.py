"""Daily-return covariance for book-aware sizing (docs/card-evidence.md).

Completed sessions only (a bar dated on or after ``as_of`` is not completed), raw Alpaca prices only,
Ledoit-Wolf shrinkage. Research-grade estimation code: the sizing rule itself is
``agentic_trader.risk.book_vol``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf


__all__ = ["RAW_ADJUSTMENTS", "daily_returns", "is_raw_frame", "shrunk_covariance"]

RAW_ADJUSTMENTS: frozenset[str | None] = frozenset({None, "raw"})


def is_raw_frame(frame: pd.DataFrame) -> bool:
    """True when the frame carries no adjustment tag or the raw one (never yfinance auto-adjust)."""
    return frame.attrs.get("adjustment") in RAW_ADJUSTMENTS


def _closes_before(frame: pd.DataFrame, as_of: date) -> pd.Series:
    session_dates = pd.DatetimeIndex(frame.index).date
    closes = frame.loc[session_dates < as_of, "Close"].astype(float)
    closes.index = pd.DatetimeIndex(closes.index).normalize()
    return closes[~closes.index.duplicated(keep="last")]


def daily_returns(
    frames: Mapping[str, pd.DataFrame],
    *,
    as_of: date,
    lookback_sessions: int,
    min_observations: int,
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    """Aligned simple close-to-close returns over the last ``lookback_sessions`` completed sessions.

    Symbols with fewer than ``min_observations`` aligned returns are dropped and returned second.
    """
    series = {symbol: _closes_before(frame, as_of).pct_change().dropna() for symbol, frame in frames.items()}
    kept = {s: r for s, r in series.items() if len(r) >= min_observations}
    dropped = tuple(sorted(s for s in series if s not in kept))
    if not kept:
        return pd.DataFrame(), dropped
    aligned = pd.concat(kept, axis=1, join="inner").sort_index()
    aligned = aligned.iloc[-lookback_sessions:]
    aligned.columns = list(kept)
    short = tuple(sorted(c for c in aligned.columns if aligned[c].notna().sum() < min_observations))
    if short:
        aligned = aligned.drop(columns=list(short))
        dropped = tuple(sorted({*dropped, *short}))
    return aligned.dropna(), dropped


def shrunk_covariance(returns: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    """Ledoit-Wolf covariance (daily, per unit notional) and the fitted shrinkage."""
    if returns.empty or returns.shape[1] == 0:
        raise ValueError("no returns to estimate a covariance from")
    if returns.shape[1] == 1:
        variance = float(np.var(returns.iloc[:, 0].to_numpy(dtype=float)))
        return pd.DataFrame([[variance]], index=returns.columns, columns=returns.columns), 0.0
    model = LedoitWolf().fit(returns.to_numpy(dtype=float))
    covariance = pd.DataFrame(model.covariance_, index=returns.columns, columns=returns.columns)
    return covariance, float(model.shrinkage_)
