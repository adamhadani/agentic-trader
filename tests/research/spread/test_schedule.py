from datetime import date, timedelta
from itertools import pairwise

from agentic_trader.research.spread.schedule import Window, stage_windows


def weekdays(start: date, end: date) -> tuple[date, ...]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return tuple(out)


SESSIONS = weekdays(date(2016, 1, 4), date(2026, 8, 31))


def test_discovery_tiles_are_whole_contiguous_and_inside_the_stage():
    stage = (date(2017, 1, 3), date(2023, 12, 29))
    windows = stage_windows(SESSIONS, stage, formation_sessions=252, trading_sessions=126)
    assert windows and all(isinstance(w, Window) for w in windows)
    first = windows[0]
    assert SESSIONS[first.trading[0]] >= stage[0]
    assert first.formation == (first.trading[0] - 252, first.trading[0])
    for a, b in pairwise(windows):
        assert a.trading[1] == b.trading[0] and b.index == a.index + 1
        assert b.formation[1] == b.trading[0] and b.formation[1] - b.formation[0] == 252
    last = windows[-1]
    assert last.trading[1] - last.trading[0] == 126
    assert SESSIONS[last.trading[1] - 1] <= stage[1]
    # the partial tail is dropped, so one more window would cross the stage end
    assert last.trading[1] + 126 > sum(1 for s in SESSIONS if s <= stage[1])
    assert 12 <= len(windows) <= 15


def test_confirmation_tiles_start_at_the_stage_start_and_reach_back_for_formation():
    stage = (date(2024, 1, 2), date(2026, 7, 31))
    windows = stage_windows(SESSIONS, stage, formation_sessions=252, trading_sessions=126)
    assert SESSIONS[windows[0].trading[0]] == date(2024, 1, 2)
    assert SESSIONS[windows[0].formation[0]] < date(2024, 1, 2)
    assert 4 <= len(windows) <= 6


def test_insufficient_history_starts_at_the_first_session_with_enough_formation():
    windows = stage_windows(
        SESSIONS, (date(2016, 1, 4), date(2017, 12, 29)), formation_sessions=252, trading_sessions=126
    )
    assert windows[0].trading[0] == 252 and windows[0].formation == (0, 252)


def test_a_stage_too_short_for_one_window_has_no_windows():
    assert (
        stage_windows(SESSIONS, (date(2026, 7, 1), date(2026, 8, 31)), formation_sessions=252, trading_sessions=126)
        == ()
    )
