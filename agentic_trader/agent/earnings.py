from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Protocol, runtime_checkable

import httpx

from agentic_trader.constants import NASDAQ_EARNINGS_CALENDAR_URL
from agentic_trader.market.session import ET_TZ, ensure_et


logger = logging.getLogger(__name__)


class EarningsTiming(StrEnum):
    """When on the reporting day a company announces earnings."""

    PRE_MARKET = "pre_market"
    AFTER_HOURS = "after_hours"
    UNSPECIFIED = "unspecified"


# Raw `time` values returned by the Nasdaq calendar endpoint. Anything else
# (including a missing/unknown value) is treated as not supplied.
_RAW_TIMING_MAP: dict[str, EarningsTiming] = {
    "time-pre-market": EarningsTiming.PRE_MARKET,
    "time-after-hours": EarningsTiming.AFTER_HOURS,
    "time-not-supplied": EarningsTiming.UNSPECIFIED,
}


def _parse_timing(raw: str | None) -> EarningsTiming:
    return _RAW_TIMING_MAP.get(raw or "", EarningsTiming.UNSPECIFIED)


def _timing_word(timing: EarningsTiming) -> str:
    if timing == EarningsTiming.PRE_MARKET:
        return "before open"
    if timing == EarningsTiming.AFTER_HOURS:
        return "after close"
    return "timing unspecified"


@dataclass(frozen=True)
class EarningsEvent:
    symbol: str
    date: date  # New York exchange date
    timing: EarningsTiming


NASDAQ_REQUEST_HEADERS: dict[str, str] = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


@dataclass(frozen=True)
class CalendarRow:
    """One Nasdaq calendar row: the report date plus the EPS fields the calendar serves today.

    Shared by the live earnings blackout (which reads only ``symbol``/``date``/``timing``)
    and the a priori PEAD study. The EPS values are as the endpoint serves them now, not
    guaranteed point-in-time.
    """

    symbol: str
    date: date
    timing: EarningsTiming
    eps: float | None
    eps_forecast: float | None
    surprise_pct_reported: float | None
    n_estimates: int | None
    fiscal_quarter: str | None


@dataclass(frozen=True)
class EarningsLookup:
    """Result of an earnings lookahead scan.

    `verified=False` means at least one date in the scanned window could not be
    fetched or parsed, so an absent `event` is not trustworthy evidence of "no
    report" — callers must fail open rather than treat it as clearance.
    """

    event: EarningsEvent | None
    verified: bool
    horizon_end: date


@runtime_checkable
class EarningsCalendarProtocol(Protocol):
    async def next_earnings(self, symbol: str, now: datetime, horizon_days: int) -> EarningsLookup: ...


def _number(raw: object) -> float | None:
    """``'$1.07'`` -> 1.07, ``'($0.95)'`` -> -0.95, ``'$1,234.50'`` -> 1234.5; blank, ``N/A`` or junk -> None."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.upper() == "N/A":
        return None
    negative = text.startswith("(") and text.endswith(")")
    cleaned = text.strip("()").replace("$", "").replace(",", "").strip()
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return -abs(value) if negative else value


def _count(raw: object) -> int | None:
    value = _number(raw)
    if value is None or value < 0 or not value.is_integer():
        return None
    return int(value)


def parse_calendar_payload(payload: object, day: date) -> list[CalendarRow]:
    """Rows of one Nasdaq calendar response for ``day``.

    A missing or null ``data``/``rows`` is an empty day (the endpoint serves
    ``data: null`` on some non-trading dates); rows without a symbol are skipped.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    raw_rows = data.get("rows") if isinstance(data, dict) else None
    rows: list[CalendarRow] = []
    for row in raw_rows if isinstance(raw_rows, list) else []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        quarter = str(row.get("fiscalQuarterEnding") or "").strip()
        rows.append(
            CalendarRow(
                symbol=symbol,
                date=day,
                timing=_parse_timing(row.get("time")),
                eps=_number(row.get("eps")),
                eps_forecast=_number(row.get("epsForecast")),
                surprise_pct_reported=_number(row.get("surprise")),
                n_estimates=_count(row.get("noOfEsts")),
                fiscal_quarter=quarter or None,
            )
        )
    return rows


class NasdaqEarningsCalendar:
    """Earnings calendar backed by the unofficial, keyless Nasdaq calendar endpoint.

    Follows `ForexFactoryCalendar`'s style: a short-timeout `httpx.AsyncClient`,
    per-date result caching with a TTL, and a logged warning (never a raised
    exception) on fetch/parse failure. Only issues GETs.
    """

    def __init__(
        self,
        cache_ttl_minutes: int = 360,
        transport: httpx.AsyncBaseTransport | None = None,
        failure_retry_minutes: int = 10,
    ):
        self.cache_ttl = timedelta(minutes=cache_ttl_minutes)
        self.failure_retry = timedelta(minutes=failure_retry_minutes)
        self._transport = transport
        # date -> (fetched_at, {SYMBOL: EarningsEvent}). A failed date is never
        # stored here; it is remembered in `_failed_at` instead.
        self._cache: dict[date, tuple[datetime, dict[str, EarningsEvent]]] = {}
        # date -> time of the last failed fetch. A down calendar must not cost every
        # candidate of a scan the full request timeout again, so a failed date is
        # retried only after `failure_retry`.
        self._failed_at: dict[date, datetime] = {}

    async def _fetch_date(self, day: date, now: datetime) -> dict[str, EarningsEvent] | None:
        cached = self._cache.get(day)
        if cached is not None:
            fetched_at, rows = cached
            if now - fetched_at < self.cache_ttl:
                return rows
        failed_at = self._failed_at.get(day)
        if failed_at is not None and now - failed_at < self.failure_retry:
            return None

        rows_map = await self._request_date(day)
        if rows_map is None:
            self._failed_at[day] = now
            return None
        self._failed_at.pop(day, None)
        self._cache[day] = (now, rows_map)
        return rows_map

    async def _request_date(self, day: date) -> dict[str, EarningsEvent] | None:
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=6.0) as client:
                resp = await client.get(
                    NASDAQ_EARNINGS_CALENDAR_URL,
                    params={"date": day.isoformat()},
                    headers=NASDAQ_REQUEST_HEADERS,
                )
                if resp.status_code != 200:
                    logger.warning(
                        "Nasdaq earnings calendar returned HTTP %s for %s", resp.status_code, day.isoformat()
                    )
                    return None
                payload = resp.json()
        except Exception as e:
            logger.warning("Failed to fetch Nasdaq earnings calendar", extra={"date": day.isoformat(), "error": str(e)})
            return None

        try:
            rows_map = {
                row.symbol: EarningsEvent(symbol=row.symbol, date=day, timing=row.timing)
                for row in parse_calendar_payload(payload, day)
            }
        except Exception as e:
            logger.warning(
                "Failed to parse Nasdaq earnings calendar payload", extra={"date": day.isoformat(), "error": str(e)}
            )
            return None
        return rows_map

    async def next_earnings(self, symbol: str, now: datetime, horizon_days: int) -> EarningsLookup:
        symbol_upper = symbol.upper()
        today = ensure_et(now, target_tz=ET_TZ).date()
        horizon_end = today + timedelta(days=horizon_days)

        verified = True
        found: EarningsEvent | None = None
        day = today
        while day <= horizon_end:
            rows_map = await self._fetch_date(day, now)
            if rows_map is None:
                verified = False
            elif found is None:
                event = rows_map.get(symbol_upper)
                if event is not None:
                    is_ahead = event.date > today or (event.date == today and event.timing != EarningsTiming.PRE_MARKET)
                    if is_ahead:
                        found = event
            day += timedelta(days=1)

        return EarningsLookup(event=found, verified=verified, horizon_end=horizon_end)


def earnings_blackout_reason(lookup: EarningsLookup, symbol: str, now: datetime, blackout_days: int) -> str | None:
    """Return a rejection reason when an ahead earnings event falls inside the blackout window.

    A report that was found always blocks, even if another date in the window
    could not be fetched. Only an *absence* is untrustworthy when the lookup is
    unverified, and then the gate fails open. `blackout_days=0` disables it.
    """
    if blackout_days <= 0 or lookup.event is None:
        return None

    today = ensure_et(now, target_tz=ET_TZ).date()
    days_out = (lookup.event.date - today).days
    if days_out < 0 or days_out > blackout_days:
        return None

    day_word = "day" if days_out == 1 else "days"
    return (
        f"{symbol} reports {lookup.event.date.isoformat()} {_timing_word(lookup.event.timing)} "
        f"(in {days_out} {day_word}) — within {blackout_days}-day blackout"
    )


def earnings_note(lookup: EarningsLookup, now: datetime, blackout_days: int) -> str:
    """Render the one-line earnings note shown on a suggestion card."""
    if lookup.event is None:
        if not lookup.verified:
            return "Unverified — earnings calendar unavailable"
        return f"No report within {blackout_days} days"

    today = ensure_et(now, target_tz=ET_TZ).date()
    days_out = (lookup.event.date - today).days
    inside = 0 <= days_out <= blackout_days if blackout_days > 0 else False
    qualifier = "within" if inside else "outside"
    day_word = "day" if days_out == 1 else "days"
    return (
        f"Reports {lookup.event.date.isoformat()} {_timing_word(lookup.event.timing)} "
        f"(in {days_out} {day_word}) — {qualifier} {blackout_days}-day blackout"
    )


__all__ = [
    "NASDAQ_REQUEST_HEADERS",
    "CalendarRow",
    "EarningsCalendarProtocol",
    "EarningsEvent",
    "EarningsLookup",
    "EarningsTiming",
    "NasdaqEarningsCalendar",
    "earnings_blackout_reason",
    "earnings_note",
    "parse_calendar_payload",
]
