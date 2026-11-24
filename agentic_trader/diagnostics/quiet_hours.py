"""Pure alert-window calendar for quiet hours: weekday, non-holiday New York wall-clock windows."""

from datetime import UTC, date, datetime, timedelta

from agentic_trader.config import QuietHoursConfig
from agentic_trader.market.session import ET_TZ, MarketHolidayCalendar


_MAX_LOOKBACK_DAYS = 14


def _require_aware(at: datetime) -> None:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("timezone-aware datetime required")


def is_alert_day(day: date) -> bool:
    return day.weekday() < 5 and not MarketHolidayCalendar.is_equity_holiday(day)[0]


def alert_since(at: datetime, policy: QuietHoursConfig) -> datetime | None:
    """UTC start of the alert window containing ``at``; None when disabled or in quiet time."""
    _require_aware(at)
    if not policy.enabled:
        return None
    local = at.astimezone(ET_TZ)
    if not is_alert_day(local.date()):
        return None
    start = datetime.combine(local.date(), policy.alert_start_et, tzinfo=ET_TZ)
    end = datetime.combine(local.date(), policy.alert_end_et, tzinfo=ET_TZ)
    if start <= local < end:
        return start.astimezone(UTC)
    return None


def is_muted(component: str, at: datetime, policy: QuietHoursConfig) -> bool:
    _require_aware(at)
    return policy.enabled and component not in policy.exempt_components and alert_since(at, policy) is None


def quiet_period_before(window_start: datetime, policy: QuietHoursConfig) -> tuple[datetime, datetime]:
    """(end of the nearest earlier alert window, ``window_start``), both UTC. Ignores ``policy.enabled``."""
    _require_aware(window_start)
    day = window_start.astimezone(ET_TZ).date()
    for back in range(1, _MAX_LOOKBACK_DAYS + 1):
        candidate = day - timedelta(days=back)
        if is_alert_day(candidate):
            end = datetime.combine(candidate, policy.alert_end_et, tzinfo=ET_TZ)
            return end.astimezone(UTC), window_start.astimezone(UTC)
    raise ValueError("no alert day within 14 days before the window")
