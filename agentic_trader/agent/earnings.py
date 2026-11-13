from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

import httpx

from agentic_trader.constants import NASDAQ_EARNINGS_CALENDAR_URL
from agentic_trader.market.session import ET_TZ, ensure_et
from agentic_trader.risk import earnings_days_out, earnings_unverified_refuses


if TYPE_CHECKING:
    from agentic_trader.data.earnings_capture import CapturedReports

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


def timing_word(timing: EarningsTiming) -> str:
    """How a report's timing reads on a card or notice: "before open", "after close" or "timing unspecified"."""
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
    report" — callers apply ``risk.earnings_calendar_unknown`` (``open`` labels it, ``closed`` refuses) rather
    than treat it as clearance.
    """

    event: EarningsEvent | None
    verified: bool
    horizon_end: date
    # "capture": the verdict came from the journaled dated calendar capture, not the live lookup.
    source: Literal["live", "capture"] = "live"
    capture_date: date | None = None


@runtime_checkable
class EarningsCalendarProtocol(Protocol):
    async def next_earnings(self, symbol: str, now: datetime, horizon_days: int) -> EarningsLookup: ...


class EarningsCaptureSource(Protocol):
    """The journaled dated earnings capture (``EarningsCaptureStore``), read-only."""

    async def reports_between(self, start: date, end: date, *, today: date) -> CapturedReports | None: ...


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


def calendar_page_rejection(response: httpx.Response) -> tuple[dict[str, Any] | None, str | None]:
    """``(payload, None)`` for a verified calendar page, else ``(None, reason)``.

    A page is verified only with HTTP 200, a JSON object body, ``status.rCode == 200`` and a
    JSON-object ``data``; a soft-error page (``data: null``) is never an empty day.
    """
    if response.status_code != 200:
        return None, f"HTTP {response.status_code}"
    try:
        payload = response.json()
    except ValueError:
        return None, "body is not JSON"
    if not isinstance(payload, dict):
        return None, "body is not a JSON object"
    status = payload.get("status")
    code = status.get("rCode") if isinstance(status, dict) else None
    if code != 200:
        return None, f"status.rCode {code!r}"
    if not isinstance(payload.get("data"), dict):
        return None, "data is not an object"
    return payload, None


def parse_calendar_payload(payload: object, day: date) -> list[CalendarRow]:
    """Rows of one Nasdaq calendar response for ``day``.

    A missing or null ``data``/``rows`` parses as an empty day (research tolerance; the live
    paths reject a page without a ``data`` object first, see ``calendar_page_rejection``);
    rows without a symbol are skipped.
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


@dataclass(frozen=True)
class ForwardRow:
    """One row of a forward (not yet reported) Nasdaq calendar page, as served."""

    symbol: str
    timing: EarningsTiming
    fiscal_quarter: str | None
    eps_forecast: float | None
    n_estimates: int | None
    last_year_report_date: date | None
    last_year_eps: float | None
    market_cap: float | None


def _report_date(raw: object) -> date | None:
    """``'11/06/2025'`` -> date(2025, 11, 6); ``N/A``, blank or unparseable -> None."""
    try:
        return date.strptime(str(raw or "").strip(), "%m/%d/%Y")
    except ValueError:
        return None


def parse_forward_rows(payload: object, day: date) -> tuple[list[ForwardRow], int]:
    """Forward rows of one Nasdaq calendar response for ``day`` and the count skipped.

    A missing or null ``data``/``rows`` parses as an empty day; rows without a symbol are
    skipped and counted. Duplicate symbols are kept as served.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    raw_rows = data.get("rows") if isinstance(data, dict) else None
    rows: list[ForwardRow] = []
    skipped = 0
    for row in raw_rows if isinstance(raw_rows, list) else []:
        symbol = str(row.get("symbol") or "").strip().upper() if isinstance(row, dict) else ""
        if not symbol:
            skipped += 1
            continue
        quarter = str(row.get("fiscalQuarterEnding") or "").strip()
        rows.append(
            ForwardRow(
                symbol=symbol,
                timing=_parse_timing(row.get("time")),
                fiscal_quarter=quarter or None,
                eps_forecast=_number(row.get("epsForecast")),
                n_estimates=_count(row.get("noOfEsts")),
                last_year_report_date=_report_date(row.get("lastYearRptDt")),
                last_year_eps=_number(row.get("lastYearEPS")),
                market_cap=_number(row.get("marketCap")),
            )
        )
    return rows, skipped


@dataclass(frozen=True)
class ReportedPage:
    """Every row Nasdaq listed for ``day``, or the reason it could not be trusted.

    ``rows is None`` means unavailable: no page was ever guessed. ``body_sha256`` is
    the accepted or rejected response body's hash, so a caller can journal exactly
    what was seen without keeping the raw bytes.
    """

    day: date
    rows: tuple[CalendarRow, ...] | None
    http_status: int | None
    detail: str | None
    body_sha256: str | None


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
                payload, rejection = calendar_page_rejection(resp)
                if rejection is not None:
                    logger.warning("Nasdaq earnings calendar page rejected for %s: %s", day.isoformat(), rejection)
                    return None
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

    async def reported_rows(self, day: date) -> ReportedPage:
        """Every row Nasdaq lists for ``day``; unusable pages are ``rows=None``, never guessed.

        Acceptance is ``calendar_page_rejection``: HTTP 200, a JSON object, ``status.rCode == 200``
        and a ``data`` object. Uncached: a live PEAD decision reads the page once.
        """
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=6.0) as client:
                resp = await client.get(
                    NASDAQ_EARNINGS_CALENDAR_URL, params={"date": day.isoformat()}, headers=NASDAQ_REQUEST_HEADERS
                )
        except Exception as exc:
            return ReportedPage(day, None, None, f"request failed: {type(exc).__name__}", None)
        digest = hashlib.sha256(resp.content).hexdigest()
        payload, rejection = calendar_page_rejection(resp)
        if rejection is not None:
            return ReportedPage(day, None, resp.status_code, rejection, digest)
        return ReportedPage(day, tuple(parse_calendar_payload(payload, day)), 200, None, digest)

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


def _is_ahead(report_date: date, timing: EarningsTiming, today: date) -> bool:
    return report_date > today or (report_date == today and timing != EarningsTiming.PRE_MARKET)


def _log_resolved(symbol: str, lookup: EarningsLookup) -> None:
    """One INFO line for a verdict that did not come from a verified live lookup (capture or unverified)."""
    logger.info(
        "earnings_lookup_resolved",
        extra={
            "event": "earnings_lookup_resolved",
            "contract": symbol,
            "source": lookup.source,
            "capture_date": lookup.capture_date.isoformat() if lookup.capture_date else None,
            "verified": lookup.verified,
        },
    )


async def resolve_earnings(
    symbol: str,
    now: datetime,
    horizon_days: int,
    *,
    live: EarningsCalendarProtocol | None,
    captures: EarningsCaptureSource | None,
) -> EarningsLookup:
    """One earnings verdict: the live lookup, else the journaled dated capture, else unverified.

    A verified live result, or any live result that found a report, is returned as is. When the live
    lookup raised or is unverified without a report, the capture (journal read only) vouches: it is
    scanned with the live "ahead" rule and a verified ``source="capture"`` lookup returned. With
    neither source the lookup is unverified. Failures are logged by exception type name only.
    """
    today = ensure_et(now, target_tz=ET_TZ).date()
    horizon_end = today + timedelta(days=horizon_days)

    if live is not None:
        try:
            lookup = await live.next_earnings(symbol, now, horizon_days)
        except Exception as exc:
            logger.warning(
                "earnings_lookup_failed", extra={"event": "earnings_lookup_failed", "error": type(exc).__name__}
            )
        else:
            if lookup.verified or lookup.event is not None:
                return lookup

    if captures is not None:
        captured = None
        try:
            captured = await captures.reports_between(today, horizon_end, today=today)
        except Exception as exc:
            logger.warning(
                "earnings_capture_read_failed",
                extra={"event": "earnings_capture_read_failed", "error": type(exc).__name__},
            )
        if captured is not None:
            symbol_upper = symbol.upper()
            found: EarningsEvent | None = None
            for day in sorted(captured.rows):
                if day < today or day > horizon_end:
                    continue
                for row_symbol, timing in captured.rows[day]:
                    if row_symbol.upper() == symbol_upper and _is_ahead(day, timing, today):
                        found = EarningsEvent(symbol=symbol_upper, date=day, timing=timing)
                        break
                if found is not None:
                    break
            result = EarningsLookup(
                event=found,
                verified=True,
                horizon_end=horizon_end,
                source="capture",
                capture_date=captured.capture_date,
            )
            _log_resolved(symbol, result)
            return result

    result = EarningsLookup(event=None, verified=False, horizon_end=horizon_end)
    _log_resolved(symbol, result)
    return result


def earnings_blackout_reason(
    lookup: EarningsLookup,
    symbol: str,
    now: datetime,
    blackout_days: int,
    *,
    unknown: Literal["open", "closed"] = "open",
) -> str | None:
    """Return a rejection reason when an ahead earnings event falls inside the blackout window.

    The window is ``agentic_trader.risk.earnings_days_out``: the report date lies within
    ``[0, blackout_days]`` days of today in New York. A report that was found always blocks,
    even if another date in the window could not be fetched. Only an *absence* is
    untrustworthy when the lookup is unverified: the gate then fails open (``unknown="open"``) or
    refuses (``"closed"``, ``risk.earnings_calendar_unknown``). `blackout_days=0` disables it.
    """
    if lookup.event is None:
        if earnings_unverified_refuses(
            verified=lookup.verified, has_event=False, blackout_days=blackout_days, unknown=unknown
        ):
            return (
                f"{symbol}: earnings calendar unavailable and no fresh capture covers "
                f"the next {blackout_days} days (policy closed)"
            )
        return None
    today = ensure_et(now, target_tz=ET_TZ).date()
    days_out = earnings_days_out(lookup.event.date, today, blackout_days)
    if days_out is None:
        return None

    day_word = "day" if days_out == 1 else "days"
    suffix = (
        f" (calendar capture {lookup.capture_date.isoformat()})"
        if lookup.source == "capture" and lookup.capture_date
        else ""
    )
    return (
        f"{symbol} reports {lookup.event.date.isoformat()} {timing_word(lookup.event.timing)} "
        f"(in {days_out} {day_word}) — within {blackout_days}-day blackout{suffix}"
    )


def earnings_note(lookup: EarningsLookup, now: datetime, blackout_days: int) -> str:
    """Render the one-line earnings note shown on a suggestion card."""
    suffix = (
        f" (calendar capture {lookup.capture_date.isoformat()})"
        if lookup.source == "capture" and lookup.capture_date
        else ""
    )
    if lookup.event is None:
        if not lookup.verified:
            return "Unverified — earnings calendar unavailable"
        return f"No report within {blackout_days} days{suffix}"

    today = ensure_et(now, target_tz=ET_TZ).date()
    days_out = (lookup.event.date - today).days
    inside = 0 <= days_out <= blackout_days if blackout_days > 0 else False
    qualifier = "within" if inside else "outside"
    day_word = "day" if days_out == 1 else "days"
    return (
        f"Reports {lookup.event.date.isoformat()} {timing_word(lookup.event.timing)} "
        f"(in {days_out} {day_word}) — {qualifier} {blackout_days}-day blackout{suffix}"
    )


__all__ = [
    "NASDAQ_REQUEST_HEADERS",
    "CalendarRow",
    "EarningsCalendarProtocol",
    "EarningsCaptureSource",
    "EarningsEvent",
    "EarningsLookup",
    "EarningsTiming",
    "NasdaqEarningsCalendar",
    "ReportedPage",
    "earnings_blackout_reason",
    "earnings_note",
    "parse_calendar_payload",
    "resolve_earnings",
    "timing_word",
]
