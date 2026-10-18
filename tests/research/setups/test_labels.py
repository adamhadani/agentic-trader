from datetime import UTC, datetime

import pandas as pd
import pytest

from agentic_trader.research.setups.labels import (
    LIVE_ARM,
    REGULAR_SESSION_HOURS_NY,
    BracketArm,
    BracketHit,
    SetupLevels,
    TrailSpec,
    label_bracket,
)


def bars(rows: list[tuple[str, float, float, float, float]]) -> pd.DataFrame:
    """Build a UTC-indexed OHLC frame from (iso_start, open, high, low, close) rows."""
    index = pd.DatetimeIndex([pd.Timestamp(row[0]) for row in rows], tz="UTC")
    return pd.DataFrame(
        {
            "Open": [row[1] for row in rows],
            "High": [row[2] for row in rows],
            "Low": [row[3] for row in rows],
            "Close": [row[4] for row in rows],
        },
        index=index,
    )


LONG = SetupLevels(direction="LONG", entry=100.0, stop=99.0, target=102.0)
SHORT = SetupLevels(direction="SHORT", entry=100.0, stop=101.0, target=98.0)

BEFORE_OPEN = datetime(2026, 3, 2, 13, 45, tzinfo=UTC)


def test_regular_session_hours_reexported():
    assert range(9, 16) == REGULAR_SESSION_HOURS_NY


def test_long_target_first():
    frame = bars(
        [
            ("2026-03-02T13:00:00+00:00", 100.0, 100.0, 100.0, 100.0),  # pre-market, ignored
            ("2026-03-02T14:00:00+00:00", 100.5, 100.8, 100.2, 100.6),  # entry bar, no hit
            ("2026-03-02T15:00:00+00:00", 100.6, 102.5, 100.3, 102.0),  # target hit only
        ]
    )
    outcome = label_bracket(LONG, BEFORE_OPEN, frame, max_hold_sessions=5)

    assert outcome.hit == BracketHit.TARGET
    assert outcome.entry_time == pd.Timestamp("2026-03-02T14:00:00+00:00")
    assert outcome.entry_price == pytest.approx(100.5)
    assert outcome.exit_time == pd.Timestamp("2026-03-02T15:00:00+00:00")
    assert outcome.exit_price == pytest.approx(102.0)
    assert outcome.r == pytest.approx((102.0 - 100.5) / 1.0)
    assert outcome.holding_sessions == 1


def test_long_stop_first():
    frame = bars(
        [
            ("2026-03-02T14:00:00+00:00", 100.5, 100.8, 100.2, 100.6),  # entry bar, no hit
            ("2026-03-02T15:00:00+00:00", 100.6, 100.9, 98.5, 99.0),  # stop hit only
        ]
    )
    outcome = label_bracket(LONG, BEFORE_OPEN, frame, max_hold_sessions=5)

    assert outcome.hit == BracketHit.STOP
    assert outcome.exit_price == pytest.approx(99.0)
    assert outcome.r == pytest.approx(-(100.5 - 99.0) / 1.0)


def test_same_bar_stop_wins():
    frame = bars(
        [
            ("2026-03-02T14:00:00+00:00", 100.5, 103.0, 98.0, 100.0),  # entry bar hits both
        ]
    )
    outcome = label_bracket(LONG, BEFORE_OPEN, frame, max_hold_sessions=5)

    assert outcome.hit == BracketHit.STOP
    assert outcome.exit_time == pd.Timestamp("2026-03-02T14:00:00+00:00")
    assert outcome.exit_price == pytest.approx(99.0)
    assert outcome.r == pytest.approx((99.0 - 100.5) / 1.0)


def test_gap_through_stop_fills_at_open_r_below_minus_one():
    frame = bars(
        [
            ("2026-03-02T14:00:00+00:00", 100.5, 100.8, 100.2, 100.4),  # entry bar, no hit
            ("2026-03-02T15:00:00+00:00", 97.0, 97.5, 96.0, 96.5),  # gaps below stop
        ]
    )
    outcome = label_bracket(LONG, BEFORE_OPEN, frame, max_hold_sessions=5)

    assert outcome.hit == BracketHit.STOP
    assert outcome.exit_price == pytest.approx(97.0)
    assert outcome.r == pytest.approx((97.0 - 100.5) / 1.0)
    assert outcome.r < -1.0


def test_gap_at_entry_beyond_target_is_target_at_open():
    frame = bars(
        [
            ("2026-03-02T14:00:00+00:00", 103.0, 103.5, 102.8, 103.2),  # entry bar gaps past target
        ]
    )
    outcome = label_bracket(LONG, BEFORE_OPEN, frame, max_hold_sessions=5)

    assert outcome.hit == BracketHit.TARGET
    assert outcome.entry_price == pytest.approx(103.0)
    assert outcome.exit_time == pd.Timestamp("2026-03-02T14:00:00+00:00")
    assert outcome.exit_price == pytest.approx(103.0)
    assert outcome.r == pytest.approx(0.0)


def test_short_mirror_target():
    frame = bars(
        [
            ("2026-03-02T14:00:00+00:00", 100.2, 100.6, 99.9, 100.1),  # entry bar, no hit
            ("2026-03-02T15:00:00+00:00", 99.5, 99.8, 97.5, 98.0),  # target hit only
        ]
    )
    outcome = label_bracket(SHORT, BEFORE_OPEN, frame, max_hold_sessions=5)

    assert outcome.hit == BracketHit.TARGET
    assert outcome.entry_price == pytest.approx(100.2)
    assert outcome.exit_price == pytest.approx(98.0)
    assert outcome.r == pytest.approx(-1.0 * (98.0 - 100.2) / 1.0)


def test_timeout_exits_last_close_of_nth_session():
    wide = SetupLevels(direction="LONG", entry=100.0, stop=90.0, target=110.0)
    rows = []
    for day, closes in (
        ("2026-03-02", [100.0, 100.1, 99.9, 100.2, 99.8, 100.3, 100.4]),
        ("2026-03-03", [100.2, 100.0, 99.9, 100.1, 100.0, 99.9, 100.5]),
    ):
        for hour, close in zip(range(14, 21), closes, strict=True):
            rows.append((f"{day}T{hour:02d}:00:00+00:00", close - 0.1, close + 0.3, close - 0.3, close))
    # First bar of the third distinct session date; never evaluated for hits, only triggers timeout.
    rows.append(("2026-03-04T14:00:00+00:00", 100.0, 100.4, 99.7, 100.1))

    frame = bars(rows)
    outcome = label_bracket(wide, BEFORE_OPEN, frame, max_hold_sessions=2)

    assert outcome.hit == BracketHit.TIMEOUT
    assert outcome.holding_sessions == 2
    assert outcome.exit_time == pd.Timestamp("2026-03-03T20:00:00+00:00")
    assert outcome.exit_price == pytest.approx(100.5)
    assert outcome.r == pytest.approx((100.5 - (100.0 - 0.1)) / 10.0)


def test_immature_when_bars_run_out():
    wide = SetupLevels(direction="LONG", entry=100.0, stop=90.0, target=110.0)
    rows = []
    rows.extend(
        (f"{day}T{hour:02d}:00:00+00:00", 100.0, 100.4, 99.7, 100.1)
        for day in ("2026-03-02", "2026-03-03")
        for hour in range(14, 21)
    )

    frame = bars(rows)
    outcome = label_bracket(wide, BEFORE_OPEN, frame, max_hold_sessions=5)

    assert outcome.hit == BracketHit.IMMATURE
    assert outcome.entry_time == pd.Timestamp("2026-03-02T14:00:00+00:00")
    assert outcome.entry_price == pytest.approx(100.0)
    assert outcome.exit_time is None
    assert outcome.exit_price is None
    assert outcome.r is None
    assert outcome.r_cost is None
    assert outcome.holding_sessions == 2


def test_immature_when_no_entry_bar_available():
    frame = bars(
        [
            ("2026-03-02T14:00:00+00:00", 100.5, 100.8, 100.2, 100.6),
        ]
    )
    after_all_bars = datetime(2026, 3, 2, 21, 0, tzinfo=UTC)
    outcome = label_bracket(LONG, after_all_bars, frame, max_hold_sessions=5)

    assert outcome.hit == BracketHit.IMMATURE
    assert outcome.entry_time is None
    assert outcome.entry_price is None
    assert outcome.exit_time is None
    assert outcome.exit_price is None
    assert outcome.r is None
    assert outcome.r_cost is None
    assert outcome.holding_sessions == 0


def test_extended_hours_bars_are_ignored():
    frame = bars(
        [
            ("2026-03-02T14:00:00+00:00", 100.5, 100.8, 100.2, 100.6),  # entry bar, no hit
            # After-hours bar (16:00 ET) would gap through both levels, but must be ignored.
            ("2026-03-02T21:00:00+00:00", 100.6, 500.0, 1.0, 100.6),
            ("2026-03-03T14:00:00+00:00", 100.5, 100.8, 100.2, 100.6),  # next regular bar, no hit
        ]
    )
    outcome = label_bracket(LONG, BEFORE_OPEN, frame, max_hold_sessions=5)

    assert outcome.hit == BracketHit.IMMATURE
    assert outcome.r is None


def test_cost_reduces_r():
    frame = bars(
        [
            ("2026-03-02T14:00:00+00:00", 100.5, 100.8, 100.2, 100.6),
            ("2026-03-02T15:00:00+00:00", 100.6, 102.5, 100.3, 102.0),
        ]
    )
    outcome = label_bracket(LONG, BEFORE_OPEN, frame, max_hold_sessions=5, cost_bps_per_side=10.0)

    expected_r = (102.0 - 100.5) / 1.0
    expected_r_cost = expected_r - 2 * 10.0 / 1e4 * 100.5 / 1.0
    assert outcome.r == pytest.approx(expected_r)
    assert outcome.r_cost == pytest.approx(expected_r_cost)
    assert outcome.r_cost < outcome.r


@pytest.mark.parametrize(
    "kwargs",
    [
        {"direction": "LONG", "entry": 100.0, "stop": 101.0, "target": 102.0},  # stop above entry
        {"direction": "LONG", "entry": 100.0, "stop": 99.0, "target": 99.5},  # target below entry
        {"direction": "SHORT", "entry": 100.0, "stop": 99.0, "target": 98.0},  # stop below entry
        {"direction": "SHORT", "entry": 100.0, "stop": 101.0, "target": 100.5},  # target above entry
        {"direction": "long", "entry": 100.0, "stop": 99.0, "target": 102.0},  # lowercase direction
        {"direction": "UP", "entry": 100.0, "stop": 99.0, "target": 102.0},  # invalid direction
    ],
)
def test_invalid_levels_raise(kwargs):
    with pytest.raises(ValueError):
        SetupLevels(**kwargs)


A1 = BracketArm(name="A1_3r", target_r=3.0, trail=None)
TRAIL = TrailSpec(trigger_r=1.5, distance_r=1.5)
A2 = BracketArm(name="A2_2r_trail", target_r=2.0, trail=TRAIL)
A3 = BracketArm(name="A3_3r_trail", target_r=3.0, trail=TRAIL)
A4 = BracketArm(name="A4_trail_only", target_r=None, trail=TRAIL)


def test_arm_none_is_byte_identical_to_live_arm():
    frame = bars(
        [
            ("2026-03-02T14:00:00+00:00", 100.5, 100.8, 100.2, 100.6),
            ("2026-03-02T15:00:00+00:00", 100.6, 102.5, 100.3, 102.0),
        ]
    )
    base = label_bracket(LONG, BEFORE_OPEN, frame, max_hold_sessions=5)
    assert base == label_bracket(LONG, BEFORE_OPEN, frame, max_hold_sessions=5, arm=LIVE_ARM)
    assert base.ratchet_moves == 0
    assert base.final_stop == pytest.approx(LONG.stop)


def test_3r_target_arm_hits_where_live_times_out():
    frame = bars(
        [
            ("2026-03-02T14:00:00+00:00", 100.5, 100.8, 100.2, 100.6),
            ("2026-03-02T15:00:00+00:00", 100.6, 101.9, 100.3, 101.8),
            ("2026-03-02T16:00:00+00:00", 101.8, 103.6, 101.5, 103.2),
        ]
    )
    out = label_bracket(LONG, BEFORE_OPEN, frame, max_hold_sessions=5, arm=A1)
    assert out.hit == BracketHit.TARGET
    assert out.exit_price == pytest.approx(103.0)
    assert out.r == pytest.approx((103.0 - 100.5) / 1.0)
    live = label_bracket(LONG, BEFORE_OPEN, frame, max_hold_sessions=5)
    assert live.exit_price == pytest.approx(102.0)


def test_ratchet_triggers_on_close_and_is_effective_next_bar():
    frame = bars(
        [
            ("2026-03-02T14:00:00+00:00", 100.0, 100.5, 99.9, 100.2),
            ("2026-03-02T15:00:00+00:00", 100.3, 101.6, 99.95, 101.5),  # low would hit 100.0 stop if early
            ("2026-03-02T16:00:00+00:00", 101.4, 101.5, 99.9, 100.0),
        ]
    )
    out = label_bracket(LONG, BEFORE_OPEN, frame, max_hold_sessions=5, arm=A4)
    assert out.hit == BracketHit.STOP
    assert out.exit_time == pd.Timestamp("2026-03-02T16:00:00+00:00")
    assert out.exit_price == pytest.approx(100.0)
    assert out.r == pytest.approx(0.0)
    assert out.ratchet_moves == 1
    assert out.final_stop == pytest.approx(100.0)


def test_ratchet_never_moves_down():
    frame = bars(
        [
            ("2026-03-02T14:00:00+00:00", 100.0, 100.5, 99.9, 100.2),
            ("2026-03-02T15:00:00+00:00", 100.2, 102.1, 100.1, 102.0),
            ("2026-03-02T16:00:00+00:00", 102.0, 102.0, 100.9, 101.0),
            ("2026-03-02T17:00:00+00:00", 101.0, 101.2, 100.4, 100.6),
        ]
    )
    out = label_bracket(LONG, BEFORE_OPEN, frame, max_hold_sessions=5, arm=A4)
    assert out.hit == BracketHit.STOP
    assert out.exit_price == pytest.approx(100.5)
    assert out.ratchet_moves == 1
    assert out.final_stop == pytest.approx(100.5)


def test_trail_only_arm_never_returns_target():
    frame = bars(
        [
            ("2026-03-02T14:00:00+00:00", 100.0, 100.5, 99.9, 100.2),
            ("2026-03-03T14:00:00+00:00", 100.2, 110.0, 100.1, 110.0),
            ("2026-03-04T14:00:00+00:00", 110.0, 112.0, 109.0, 112.0),
            ("2026-03-05T14:00:00+00:00", 112.0, 112.2, 111.0, 112.0),
            ("2026-03-06T14:00:00+00:00", 112.0, 112.2, 111.0, 112.0),
            ("2026-03-09T14:00:00+00:00", 112.0, 112.2, 111.0, 112.0),
        ]
    )
    out = label_bracket(LONG, BEFORE_OPEN, frame, max_hold_sessions=5, arm=A4)
    assert out.hit == BracketHit.TIMEOUT
    assert out.r is not None
    assert out.r > 0
    assert out.ratchet_moves >= 2
    assert out.final_stop == pytest.approx(110.5)


def test_gap_through_ratcheted_stop_fills_at_open():
    frame = bars(
        [
            ("2026-03-02T14:00:00+00:00", 100.0, 100.5, 99.9, 100.2),
            ("2026-03-02T15:00:00+00:00", 100.2, 102.1, 100.1, 102.0),
            ("2026-03-02T16:00:00+00:00", 99.0, 99.5, 98.5, 99.2),
        ]
    )
    out = label_bracket(LONG, BEFORE_OPEN, frame, max_hold_sessions=5, arm=A4)
    assert out.hit == BracketHit.STOP
    assert out.exit_price == pytest.approx(99.0)
    assert out.final_stop == pytest.approx(100.5)


def test_both_touched_bar_counts_as_stop_with_ratcheted_level():
    frame = bars(
        [
            ("2026-03-02T14:00:00+00:00", 100.0, 100.5, 99.9, 100.2),
            ("2026-03-02T15:00:00+00:00", 100.2, 102.1, 100.1, 102.0),
            ("2026-03-02T16:00:00+00:00", 102.0, 103.5, 100.4, 103.0),
        ]
    )
    for arm in (A4, A3):
        out = label_bracket(LONG, BEFORE_OPEN, frame, max_hold_sessions=5, arm=arm)
        assert out.hit == BracketHit.STOP
        assert out.exit_price == pytest.approx(100.5)


def test_short_ratchet_mirrors_long():
    frame = bars(
        [
            ("2026-03-02T14:00:00+00:00", 100.0, 100.2, 99.8, 99.9),
            ("2026-03-02T15:00:00+00:00", 99.9, 100.0, 98.4, 98.5),
            ("2026-03-02T16:00:00+00:00", 98.6, 100.1, 98.5, 99.0),
        ]
    )
    out = label_bracket(SHORT, BEFORE_OPEN, frame, max_hold_sessions=5, arm=A4)
    assert out.hit == BracketHit.STOP
    assert out.exit_price == pytest.approx(100.0)
    assert out.r == pytest.approx(0.0)
    assert out.ratchet_moves == 1
    assert out.final_stop == pytest.approx(100.0)


def test_arm_outcome_defaults_without_trail():
    frame = bars(
        [
            ("2026-03-02T14:00:00+00:00", 100.5, 100.8, 100.2, 100.6),
            ("2026-03-02T15:00:00+00:00", 100.6, 101.9, 100.3, 101.8),
        ]
    )
    out = label_bracket(LONG, BEFORE_OPEN, frame, max_hold_sessions=5, arm=A1)
    assert out.hit == BracketHit.IMMATURE
    assert out.ratchet_moves == 0
    assert out.final_stop == pytest.approx(LONG.stop)
