"""The next trading day's regular close: weekends, holidays and early closes from the calendar."""

from datetime import UTC, datetime, timedelta

from agentic_trader.market.session import DeterministicCalendarProvider, MarketCalendarDay, next_regular_close_after


async def test_the_next_close_skips_a_holiday_and_keeps_an_early_close():
    # Wednesday 2026-11-25 15:00 NY: Thanksgiving is closed and Friday closes at 13:00 NY.
    now = datetime(2026, 11, 25, 20, 0, tzinfo=UTC)
    assert await next_regular_close_after(DeterministicCalendarProvider(), now) == datetime(
        2026, 11, 27, 18, 0, tzinfo=UTC
    )


async def test_a_friday_card_is_valid_until_monday_close():
    now = datetime(2026, 10, 9, 18, 35, tzinfo=UTC)  # Friday 14:35 NY
    assert await next_regular_close_after(DeterministicCalendarProvider(), now) == datetime(
        2026, 10, 12, 20, 0, tzinfo=UTC
    )


async def test_a_failing_calendar_or_no_trading_day_gives_none():
    class Failing:
        async def get_calendar_range(self, start, end):
            raise RuntimeError("calendar down")

    class Closed:
        async def get_calendar_range(self, start, end):
            days = (end - start).days + 1
            return [
                MarketCalendarDay(date=start + timedelta(days=i), is_trading_day=False, is_early_close=False)
                for i in range(days)
            ]

    now = datetime(2026, 10, 9, 18, 35, tzinfo=UTC)
    assert await next_regular_close_after(Failing(), now) is None
    assert await next_regular_close_after(Closed(), now) is None
    assert await next_regular_close_after(DeterministicCalendarProvider(), now.replace(tzinfo=None)) is None
