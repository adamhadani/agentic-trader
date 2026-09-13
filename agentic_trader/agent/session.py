"""Alias re-export for backward compatibility."""

from agentic_trader.market.session import (
    AlpacaMarketSessionProvider,
    CMEFuturesSessionProvider,
    CompositeMarketSessionProvider,
    CryptoSessionProvider,
    MarketHolidayCalendar,
    MarketSessionInfo,
    MarketSessionProtocol,
    MarketSessionType,
    ensure_et,
)


__all__ = [
    "AlpacaMarketSessionProvider",
    "CMEFuturesSessionProvider",
    "CompositeMarketSessionProvider",
    "CryptoSessionProvider",
    "MarketHolidayCalendar",
    "MarketSessionInfo",
    "MarketSessionProtocol",
    "MarketSessionType",
    "ensure_et",
]
