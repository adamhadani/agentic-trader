"""Feed-agnostic data-quality gate: hourly bars with volume, relative to a reference name."""

from __future__ import annotations

from typing import Any

import pandas as pd

from agentic_trader.market.session import ET_TZ


# Hourly buckets that overlap the 09:30-16:00 regular session, by New York start hour.
# The reference (SPY) also prints the 08:00 pre-market and 16:00 post-market buckets
# that most names never do; counting them skews every name's ratio by time of day.
REGULAR_SESSION_HOURS_NY = range(9, 16)


def active_hourly_bars(frame: pd.DataFrame, sessions: int) -> int:
    """Regular-session hourly bars with volume over the last ``sessions`` New York trading days."""
    if frame is None or frame.empty or "Volume" not in frame.columns:
        return 0
    index = pd.DatetimeIndex(frame.index)
    local = (index if index.tz is not None else index.tz_localize("UTC")).tz_convert(ET_TZ)
    regular = local.hour.isin(REGULAR_SESSION_HOURS_NY)
    days = local.normalize()
    recent = sorted(set(days[regular]))[-sessions:]
    mask = regular & days.isin(recent) & (frame["Volume"].to_numpy() > 0)
    return int(mask.sum())


def coverage_exclusions(
    datasets: dict[str, Any], *, reference: str, sessions: int, min_ratio: float, equities: set[str]
) -> tuple[set[str], str | None]:
    """Return equities whose active-bar count is below ``min_ratio`` of the reference's, or skip with a note."""
    ref = datasets.get(reference)
    if ref is None or isinstance(ref, BaseException) or getattr(ref, "hourly", None) is None:
        return set(), f"Coverage gate skipped: reference {reference} unavailable"
    ref_count = active_hourly_bars(ref.hourly, sessions)
    if ref_count == 0:
        return set(), f"Coverage gate skipped: reference {reference} has no active hourly bars"
    excluded = set()
    for symbol in equities:
        data = datasets.get(symbol)
        if data is None or isinstance(data, BaseException) or symbol == reference:
            continue
        if active_hourly_bars(getattr(data, "hourly", pd.DataFrame()), sessions) < min_ratio * ref_count:
            excluded.add(symbol)
    return excluded, None
