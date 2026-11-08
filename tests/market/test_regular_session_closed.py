from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from agentic_trader.market.session import regular_session_certainly_closed


NY = ZoneInfo("America/New_York")
CASES = [
    (datetime(2026, 11, 7, 12, 0, tzinfo=NY), True),  # Saturday
    (datetime(2026, 11, 8, 12, 0, tzinfo=NY), True),  # Sunday
    (datetime(2026, 11, 9, 9, 29, 59, tzinfo=NY), True),  # Monday before the open
    (datetime(2026, 11, 9, 9, 30, tzinfo=NY), False),  # the open
    (datetime(2026, 11, 9, 15, 59, 59, tzinfo=NY), False),
    (datetime(2026, 11, 9, 16, 0, tzinfo=NY), True),  # the close
    (datetime(2026, 11, 26, 12, 0, tzinfo=NY), True),  # Thanksgiving
    (datetime(2026, 11, 27, 14, 0, tzinfo=NY), False),  # Black Friday early close: ignored, reads open
    (datetime(2026, 10, 12, 12, 0, tzinfo=NY), False),  # Columbus Day: NYSE open
    (datetime(2026, 11, 9, 14, 30, tzinfo=UTC), False),  # 09:30 EST from UTC
    (datetime(2026, 11, 9, 14, 29, tzinfo=UTC), True),  # 09:29 EST from UTC
    (datetime(2026, 7, 6, 13, 30, tzinfo=UTC), False),  # 09:30 EDT from UTC
    (datetime(2026, 11, 9, 12, 0), False),  # noqa: DTZ001 - naive: never "certainly closed"
]


@pytest.mark.parametrize(("when", "expected"), CASES)
def test_regular_session_certainly_closed(when, expected):
    assert regular_session_certainly_closed(when) is expected
