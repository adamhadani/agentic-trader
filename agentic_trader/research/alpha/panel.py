"""Explicit timestamp × symbol operators; never overload single-series rank."""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from agentic_trader.market.quality import SOURCE_QUALITY_ATTR, BarSourceQuality
from agentic_trader.market.session import ET_TZ


def validate_panel(panel: pd.DataFrame):
    if not panel.index.is_unique or not panel.index.is_monotonic_increasing or not panel.columns.is_unique:
        raise ValueError("Panel must have unique sorted timestamps and unique symbols")
    if np.isinf(panel.to_numpy(dtype=float)).any():
        raise ValueError("Infinite panel observations")


def cross_sectional_rank(panel: pd.DataFrame) -> pd.DataFrame:
    validate_panel(panel)
    return panel.rank(axis=1, pct=True, na_option="keep")


def cross_sectional_zscore(panel: pd.DataFrame) -> pd.DataFrame:
    validate_panel(panel)
    return panel.sub(panel.mean(axis=1), axis=0).div(panel.std(axis=1).replace(0, np.nan), axis=0)


def group_neutralize(panel: pd.DataFrame, groups: pd.Series) -> pd.DataFrame:
    validate_panel(panel)
    if not groups.index.equals(panel.columns) or groups.isna().any():
        raise ValueError("Group membership must align to every symbol")
    result = panel.astype(float).copy()
    for group in sorted(groups.unique()):
        columns = groups.index[groups == group]
        result[columns] = panel[columns].sub(panel[columns].mean(axis=1), axis=0)
    return result


class PanelCoverageError(ValueError):
    def __init__(self, coverage: dict[str, dict]):
        self.coverage = coverage
        super().__init__("Incomplete declared panel coverage; missing members/dates cannot be dropped")


@dataclass(frozen=True)
class DailyResearchPanel:
    frames: dict[str, pd.DataFrame]
    coverage: dict[str, dict]

    @property
    def complete(self):
        return all(not evidence["missing_dates"] for evidence in self.coverage.values())

    @property
    def close(self):
        return pd.DataFrame({symbol: frame.close for symbol, frame in self.frames.items()})

    @property
    def opening(self):
        return pd.DataFrame({symbol: frame.open for symbol, frame in self.frames.items()})


def align_daily_panel(
    frames: dict[str, pd.DataFrame], expected_index: pd.DatetimeIndex, *, feed: str, adjustment: str = "raw"
):
    """Observed native daily bars aligned to an explicit exchange-date calendar.

    Missing dates stay NaN; there is no intersection, resampling, or forward fill.
    Historical native daily prices are not exchange auction execution evidence.
    """
    if (
        adjustment not in ("raw", "all")
        or not frames
        or not isinstance(expected_index, pd.DatetimeIndex)
        or expected_index.tz is None
        or expected_index.empty
        or expected_index.hasnans
        or not expected_index.is_unique
        or not expected_index.is_monotonic_increasing
        or not expected_index.equals(expected_index.tz_convert(ET_TZ).normalize())
    ):
        raise ValueError("Explicit ordered New York exchange-date midnights required")
    aligned, coverage = {}, {}
    for symbol, frame in sorted(frames.items()):
        frame = frame.rename(columns=str.lower).copy()
        if SOURCE_QUALITY_ATTR in frame.attrs:
            BarSourceQuality.from_document(frame.attrs[SOURCE_QUALITY_ATTR]).require_lossless(frame_rows=len(frame))
        if (
            frame.attrs.get("timeframe") != "1d"
            or frame.attrs.get("feed") != feed
            or frame.attrs.get("adjustment") != adjustment
        ):
            raise ValueError("Panel source must match native daily/adjustment/feed semantics")
        if (
            not isinstance(frame.index, pd.DatetimeIndex)
            or frame.index.tz is None
            or not frame.index.is_unique
            or not frame.index.is_monotonic_increasing
            or frame.index.hasnans
        ):
            raise ValueError("Unique ordered aware daily observations required")
        index = frame.index.tz_convert(ET_TZ)
        if not index.equals(index.normalize()) or not index.isin(expected_index).all():
            raise ValueError("Daily bars must be native exchange-date midnights in the observed calendar")
        required = ["open", "high", "low", "close", "volume"]
        if not set(required).issubset(frame.columns):
            raise ValueError("Complete OHLCV source columns required")
        values = frame[required].to_numpy(dtype=float)
        if (
            not np.isfinite(values).all()
            or (values[:, :4] <= 0).any()
            or (values[:, 4] < 0).any()
            or (frame.high < frame[["open", "low", "close"]].max(axis=1)).any()
            or (frame.low > frame[["open", "high", "close"]].min(axis=1)).any()
        ):
            raise ValueError("Invalid observed daily OHLCV; do not drop malformed rows")
        # SDK empty frames can have object columns. Normalize validated OHLCV
        # before calendar reindexing so missing rows remain numeric NaNs.
        frame = frame.astype(dict.fromkeys(required, float))
        frame.index = index
        missing = expected_index.difference(index)
        aligned[symbol] = frame.reindex(expected_index)
        coverage[symbol] = {
            "expected": len(expected_index),
            "observed": len(frame),
            "missing_dates": [t.date().isoformat() for t in missing],
        }
    return DailyResearchPanel(aligned, coverage)


def beta_adjust_scores(
    scores: pd.DataFrame,
    returns: pd.DataFrame,
    market_score: pd.Series,
    market_returns: pd.Series,
    *,
    window: int,
):
    """Subtract trailing beta times the benchmark score using only observed history.

    This is a beta-adjusted score, not an assertion of a tradable market hedge.
    """
    validate_panel(scores)
    validate_panel(returns)
    if (
        type(window) is not int
        or window < 2
        or not scores.index.equals(returns.index)
        or not scores.columns.equals(returns.columns)
        or not scores.index.equals(market_score.index)
        or not scores.index.equals(market_returns.index)
        or np.isinf(market_score.to_numpy()).any()
        or np.isinf(market_returns.to_numpy()).any()
    ):
        raise ValueError("Explicit aligned causal beta inputs required")
    variance = market_returns.rolling(window).var().replace(0, np.nan)
    beta = returns.rolling(window).cov(market_returns).div(variance, axis=0)
    return scores - beta.mul(market_score, axis=0)
