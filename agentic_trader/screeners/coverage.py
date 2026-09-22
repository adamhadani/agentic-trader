"""Feed-agnostic data-quality gate: hourly bars with volume, relative to a reference name."""

from __future__ import annotations

from typing import Any

import pandas as pd


def active_hourly_bars(frame: pd.DataFrame, sessions: int) -> int:
    if frame is None or frame.empty or "Volume" not in frame.columns:
        return 0
    days = pd.DatetimeIndex(frame.index).normalize()
    recent = sorted(set(days))[-sessions:]
    mask = days.isin(recent) & (frame["Volume"].to_numpy() > 0)
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
