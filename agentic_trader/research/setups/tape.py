"""The index tape at a decision: SPY's 20-session return and 200-session mean from completed sessions.

Pure and I/O-free. The outcomes report (``research/setups/outcomes.py``) fetches SPY daily bars
once and calls ``tape_state`` per decision session so every journaled candidate, including the
history before the regime block existed, gets the same past-only measures. Bucket names are
threshold-free (the sign of the return, the side of the mean), so nothing here can be fitted
to the sample. Descriptive only: never a gate, rule or ranking input.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from agentic_trader.market.session import ET_TZ


__all__ = [
    "MEAN_SESSIONS",
    "RET_SESSIONS",
    "TAPE_BUCKETS",
    "TAPE_HISTORY_DAYS",
    "TapeState",
    "tape_buckets",
    "tape_state",
]

# Calendar days of daily bars fetched before the earliest decision: about 275 sessions, enough
# for the 200-session mean with margin.
TAPE_HISTORY_DAYS = 400
RET_SESSIONS = 20
MEAN_SESSIONS = 200
TAPE_BUCKETS = ("ret20_up", "ret20_down", "above_200", "below_200", "unknown")


@dataclass(frozen=True)
class TapeState:
    """SPY measures over the completed sessions strictly before a decision's session."""

    ret_20: float | None  # close[-1] / close[-21] - 1
    above_200: bool | None  # close[-1] > mean(close[-200:])
    sessions: int  # completed sessions with a finite close available before the session


def _closes_before(daily: pd.DataFrame, session: date) -> np.ndarray:
    index = pd.DatetimeIndex(daily.index)
    if index.tz is None:
        index = index.tz_localize("UTC")
    frame = daily.set_axis(index).sort_index()
    ny_dates = np.array(frame.index.tz_convert(ET_TZ).date, dtype=object)
    closes = pd.to_numeric(frame.loc[ny_dates < session, "Close"], errors="coerce")
    values = closes.to_numpy(dtype=float)
    return values[np.isfinite(values)]


def tape_state(daily: pd.DataFrame | None, session: date) -> TapeState:
    """The tape as of ``session``'s decision: only sessions whose New York date precedes it count.

    Today's bar, partial or complete, is never used (the decision is intraday). Rows without a
    finite close are dropped before counting. Fewer than 21 closes give no return; fewer than
    200 give no mean comparison. A missing, empty or close-less frame is ``(None, None, 0)``.
    """
    if daily is None or daily.empty or "Close" not in daily.columns:
        return TapeState(ret_20=None, above_200=None, sessions=0)
    values = _closes_before(daily, session)
    n = len(values)
    ret_20: float | None = None
    if n >= RET_SESSIONS + 1 and values[-RET_SESSIONS - 1]:
        candidate = float(values[-1] / values[-RET_SESSIONS - 1] - 1.0)
        ret_20 = candidate if math.isfinite(candidate) else None
    above_200 = bool(values[-1] > float(np.mean(values[-MEAN_SESSIONS:]))) if n >= MEAN_SESSIONS else None
    return TapeState(ret_20=ret_20, above_200=above_200, sessions=n)


def tape_buckets(state: TapeState) -> tuple[str, ...]:
    """The bucket names a state belongs to; ``("unknown",)`` only when both measures are absent."""
    names: list[str] = []
    if state.ret_20 is not None:
        names.append("ret20_up" if state.ret_20 > 0 else "ret20_down")
    if state.above_200 is not None:
        names.append("above_200" if state.above_200 else "below_200")
    return tuple(names) if names else ("unknown",)
