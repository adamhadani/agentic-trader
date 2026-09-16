"""Read-only observed-calendar/raw-minute boundary, shared by replay and live evidence."""

from datetime import date
from typing import Protocol

import pandas as pd
from alpaca.trading.requests import GetCalendarRequest

from agentic_trader.market.bars import TradingSession
from agentic_trader.market.session import ET_TZ


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
        bars = self.bars_provider.fetch_bars(symbol, "1m", start=start.to_pydatetime(), end=end.to_pydatetime())
        if bars.attrs.get("feed") != feed or bars.attrs.get("adjustment") != "raw":
            raise ValueError("Minute observations do not match the frozen feed/adjustment")
        return bars
