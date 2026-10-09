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
    """Closes of completed sessions (strictly before ``as_of``) on a naive session-date index.

    Alpaca frames carry tz-aware timestamps and scan/yfinance frames naive ones; both reduce to
    the bar's calendar date, so mixed sources align. Duplicate dates keep the last bar.
    """
    session_dates = pd.DatetimeIndex(frame.index).date
    closes = frame.loc[session_dates < as_of, "Close"].astype(float)
    # A zero, negative or non-finite close is a bad print, not a price: it is dropped rather
    # than turned into an infinite return.
    closes = closes[np.isfinite(closes) & (closes > 0)]
    closes.index = pd.DatetimeIndex([pd.Timestamp(d) for d in pd.DatetimeIndex(closes.index).date])
    return closes[~closes.index.duplicated(keep="last")].sort_index()


def _aligned_returns(closes: Mapping[str, pd.Series], lookback_sessions: int) -> pd.DataFrame:
    """Inner-join the closes on session dates first, then one-session returns (no forward fill)."""
    joined = pd.concat(dict(closes), axis=1, join="inner").sort_index()
    returns = joined.pct_change(fill_method=None).dropna()
    return returns.iloc[-lookback_sessions:]


def daily_returns(
    frames: Mapping[str, pd.DataFrame],
    *,
    as_of: date,
    lookback_sessions: int,
    min_observations: int,
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    """Aligned simple close-to-close returns over the last ``lookback_sessions`` completed sessions.

    Closes are aligned on the intersection of session dates before differencing, so a symbol that
    misses a session never carries a two-session return beside its peers' one-session returns.
    While the aligned sample is shorter than ``min_observations`` the symbol with the shortest
    history is dropped and the rest re-aligned (one sparse name must not make the whole book
    unavailable); the dropped symbols are returned second, sorted.
    """
    if lookback_sessions <= 0 or min_observations <= 0:
        raise ValueError("lookback_sessions and min_observations must be positive")
    closes = {symbol: _closes_before(frame, as_of) for symbol, frame in frames.items()}
    dropped: set[str] = {s for s, c in closes.items() if len(c) <= min_observations}
    kept = {s: c for s, c in closes.items() if s not in dropped}
    while kept:
        aligned = _aligned_returns(kept, lookback_sessions)
        if len(aligned) >= min_observations:
            return aligned, tuple(sorted(dropped))
        shortest = min(kept, key=lambda s: (len(kept[s]), s))
        dropped.add(shortest)
        del kept[shortest]
    return pd.DataFrame(), tuple(sorted(dropped))


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
