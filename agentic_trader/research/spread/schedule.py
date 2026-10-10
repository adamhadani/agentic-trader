"""Non-overlapping formation/trading tiles over a session calendar (Gatev-style)."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date


__all__ = ["Window", "stage_windows"]


@dataclass(frozen=True)
class Window:
    """Half-open position ranges into the session sequence: formation ends where trading starts."""

    index: int
    formation: tuple[int, int]
    trading: tuple[int, int]


def stage_windows(
    sessions: Sequence[date], stage: tuple[date, date], *, formation_sessions: int, trading_sessions: int
) -> tuple[Window, ...]:
    """Trading tiles of ``trading_sessions`` inside ``stage`` (inclusive dates), each preceded by
    ``formation_sessions`` sessions. The first tile starts at the first stage session that has enough
    history; whole tiles only, so a tile that would cross the stage end is dropped."""
    start_pos = max(bisect_left(sessions, stage[0]), formation_sessions)
    end_pos = bisect_right(sessions, stage[1])
    windows: list[Window] = []
    pos = start_pos
    while pos + trading_sessions <= end_pos:
        windows.append(Window(len(windows), (pos - formation_sessions, pos), (pos, pos + trading_sessions)))
        pos += trading_sessions
    return tuple(windows)
