from datetime import UTC, datetime

import pytest

from agentic_trader.config import QuietHoursConfig
from agentic_trader.diagnostics.quiet_hours import alert_since, is_muted, quiet_period_before


on = QuietHoursConfig(enabled=True)


def test_alert_window_edges_in_est():
    start = datetime(2026, 11, 3, 14, 15, tzinfo=UTC)
    assert alert_since(start, on) == start
    assert alert_since(datetime(2026, 11, 3, 14, 14, 59, tzinfo=UTC), on) is None
    assert alert_since(datetime(2026, 11, 3, 21, 29, 59, tzinfo=UTC), on) == start
    assert alert_since(datetime(2026, 11, 3, 21, 30, 0, tzinfo=UTC), on) is None


def test_alert_window_follows_new_york_wall_clock_across_dst():
    edt = datetime(2026, 10, 30, 13, 15, tzinfo=UTC)
    assert alert_since(edt, on) == edt
    assert alert_since(datetime(2026, 11, 2, 13, 15, tzinfo=UTC), on) is None  # 08:15 EST


def test_weekends_and_holidays_are_quiet():
    assert alert_since(datetime(2026, 11, 7, 15, 0, tzinfo=UTC), on) is None
    assert alert_since(datetime(2026, 11, 26, 15, 0, tzinfo=UTC), on) is None


def test_disabled_policy_has_no_alert_window_and_mutes_nothing():
    assert alert_since(datetime(2026, 11, 3, 15, tzinfo=UTC), QuietHoursConfig()) is None
    assert is_muted("scan", datetime(2026, 11, 3, 8, tzinfo=UTC), QuietHoursConfig()) is False


def test_is_muted_exempts_broker_work():
    night = datetime(2026, 11, 3, 8, 0, tzinfo=UTC)
    assert is_muted("scan", night, on) is True
    assert is_muted("entry_recovery", night, on) is False
    assert is_muted("scan", datetime(2026, 11, 3, 15, tzinfo=UTC), on) is False


def test_quiet_period_spans_the_weekend_and_dst():
    assert quiet_period_before(datetime(2026, 11, 2, 14, 15, tzinfo=UTC), on) == (
        datetime(2026, 10, 30, 20, 30, tzinfo=UTC),
        datetime(2026, 11, 2, 14, 15, tzinfo=UTC),
    )


def test_quiet_period_skips_a_holiday():
    assert quiet_period_before(datetime(2026, 11, 27, 14, 15, tzinfo=UTC), on)[0] == datetime(
        2026, 11, 25, 21, 30, tzinfo=UTC
    )


def test_naive_datetimes_are_rejected():
    naive = datetime(2026, 11, 3, 15, 0)  # noqa: DTZ001
    with pytest.raises(ValueError, match="timezone-aware"):
        alert_since(naive, on)
    with pytest.raises(ValueError, match="timezone-aware"):
        is_muted("scan", naive, on)
    with pytest.raises(ValueError, match="timezone-aware"):
        quiet_period_before(naive, on)
