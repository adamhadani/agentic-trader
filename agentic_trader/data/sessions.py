"""Read-only observed-calendar/raw-minute boundary, shared by replay and live evidence."""

from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

import pandas as pd
from alpaca.trading.requests import GetCalendarRequest

from agentic_trader.data.evidence import BarAcquisitionError
from agentic_trader.market.bars import TradingSession, utc_timestamp
from agentic_trader.market.session import ET_TZ


SESSION_REQUEST_DAYS = 31
MAX_SESSION_REQUESTS = 12  # Bounds acquisition to roughly one leap year, including timezone offsets.


class SessionAcquisitionError(ValueError):
    def __init__(self, receipts: list[dict]):
        self.receipts = receipts
        super().__init__(
            f"Session acquisition failed: {receipts[-1]['error_type']}; partial chunks are not usable evidence"
        )


class SessionDataSource(Protocol):
    def calendar(self, start: date, end: date) -> tuple[TradingSession, ...]: ...
    def minutes(self, symbol: str, start: pd.Timestamp, end: pd.Timestamp, feed: str) -> pd.DataFrame: ...


class AlpacaSessionSource:
    """Injected clients; no fallback feed/calendar or order methods."""

    def __init__(self, bars_provider, calendar_client):
        self.bars_provider = bars_provider
        self.calendar_client = calendar_client

    def calendar(self, start: date, end: date) -> tuple[TradingSession, ...]:
        items = self.calendar_client.get_calendar(GetCalendarRequest(start=start, end=end))
        if not isinstance(items, list):
            raise TypeError("Observed Alpaca calendar response required")
        sessions = []
        for item in items:
            opened, closed = pd.Timestamp(item.open), pd.Timestamp(item.close)
            opened = opened.tz_localize(ET_TZ) if opened.tzinfo is None else opened
            closed = closed.tz_localize(ET_TZ) if closed.tzinfo is None else closed
            sessions.append(TradingSession(item.date, opened, closed))
        dates = [s.date for s in sessions]
        if dates != sorted(set(dates)) or any(not start <= d <= end for d in dates):
            raise ValueError("Ordered sessions within the requested calendar range required")
        return tuple(sessions)

    def minutes(self, symbol: str, start: pd.Timestamp, end: pd.Timestamp, feed: str) -> pd.DataFrame:
        """Acquire disjoint half-open chunks, then return one uninterrupted price clock.

        Alpaca endpoints are inclusive; one microsecond maps our exclusive upper
        bound to the SDK's datetime precision. Never deduplicate or fill missing prices.
        Pagination within a chunk remains the SDK/provider's responsibility.
        """
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("Explicit aware acquisition bounds required")
        cursor, end = utc_timestamp(start), utc_timestamp(end)
        if not cursor < end <= cursor + pd.Timedelta(days=SESSION_REQUEST_DAYS * MAX_SESSION_REQUESTS):
            raise ValueError("Ordered bounded session acquisition required")
        chunks, receipts = [], []
        while cursor < end:
            boundary = min(cursor + pd.Timedelta(days=SESSION_REQUEST_DAYS), end)
            receipt: dict[str, Any] = {
                "start": cursor.isoformat(),
                "end_exclusive": boundary.isoformat(),
                "requested_at": datetime.now(UTC).isoformat(),
            }
            try:
                bars = self.bars_provider.fetch_bars(
                    symbol,
                    "1m",
                    start=cursor.to_pydatetime(),
                    end=boundary.to_pydatetime() - timedelta(microseconds=1),
                )
                if "evidence" in bars.attrs:
                    receipt["evidence"] = bars.attrs["evidence"]
                if (
                    bars.attrs.get("feed") != feed
                    or bars.attrs.get("adjustment") != "raw"
                    or bars.attrs.get("timeframe") != "1m"
                ):
                    raise ValueError("Minute observations do not match the frozen feed/adjustment/timeframe")
                if (
                    not isinstance(bars.index, pd.DatetimeIndex)
                    or bars.index.tz is None
                    or bars.index.hasnans
                    or not bars.index.is_unique
                    or not bars.index.is_monotonic_increasing
                    or not ((bars.index >= cursor) & (bars.index < boundary)).all()
                ):
                    raise ValueError("Unique ordered chunk observations within exact requested bounds required")
                receipt["rows"] = len(bars)
                chunks.append(bars)
            except Exception as exc:
                if isinstance(exc, BarAcquisitionError):
                    receipt["evidence"] = exc.evidence
                receipt.update(error_type=type(exc).__name__, error=str(exc), received_at=datetime.now(UTC).isoformat())
                receipts.append(receipt)
                raise SessionAcquisitionError(receipts) from exc
            receipt["received_at"] = datetime.now(UTC).isoformat()
            receipts.append(receipt)
            cursor = boundary
        combined = pd.concat(chunks)
        combined.attrs = {k: v for k, v in chunks[0].attrs.items() if k != "evidence"}
        combined.attrs["acquisition"] = receipts
        return combined

    def daily(self, symbol: str, start: date, end: date, feed: str, adjustment: str = "raw") -> pd.DataFrame:
        """Read native daily history for panel diagnostics; no auction-fill claim.

        The application bounds the historical plan. Calendar alignment/coverage
        remain pure research validation; provider rows are never imputed here.
        """
        if start > end:
            raise ValueError("Ordered daily acquisition bounds required")
        opened = pd.Timestamp(start, tz=ET_TZ)
        closed = pd.Timestamp(end + timedelta(days=1), tz=ET_TZ)
        bars = self.bars_provider.fetch_bars(
            symbol,
            "1d",
            start=opened.to_pydatetime(),
            end=closed.to_pydatetime() - timedelta(microseconds=1),
            adjustment=adjustment,
        )
        if (
            bars.attrs.get("feed") != feed
            or bars.attrs.get("adjustment") != adjustment
            or bars.attrs.get("timeframe") != "1d"
        ):
            raise ValueError("Daily observations do not match the frozen feed/adjustment/timeframe")
        return bars
