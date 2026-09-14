from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import pandas as pd
import yfinance as yf
from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import (
    CryptoBarsRequest,
    CryptoLatestTradeRequest,
    StockBarsRequest,
    StockLatestBarRequest,
    StockLatestTradeRequest,
)
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from agentic_trader.resilience.fallback import (
    AllFallbacksExhaustedError,
    RetryPolicy,
    RunnableWithFallbacks,
)


logger = logging.getLogger(__name__)


class UnsupportedSymbolError(Exception):
    """Raised when a data provider cannot service the requested symbol or asset class."""


class MarketDataProvider(Protocol):
    """Standardized protocol for market data providers."""

    @property
    def name(self) -> str: ...

    def supports_symbol(self, symbol: str) -> bool: ...

    def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
        period: str | None = None,
    ) -> pd.DataFrame: ...

    def fetch_latest_price(self, symbol: str) -> float | None: ...


def parse_period_to_timedelta(period: str) -> timedelta:
    """Parse string period (e.g. '1y', '60d', '5d') into a timedelta."""
    p = period.strip().lower()
    if p.endswith("y"):
        return timedelta(days=int(p[:-1]) * 365)
    if p.endswith("mo"):
        return timedelta(days=int(p[:-2]) * 30)
    if p.endswith("d"):
        return timedelta(days=int(p[:-1]))
    if p.endswith("h"):
        return timedelta(hours=int(p[:-1]))
    return timedelta(days=365)


class AlpacaDataProvider:
    """Market data provider fetching US equities, ETFs, and crypto from Alpaca Data API."""

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        stock_client: StockHistoricalDataClient | None = None,
        crypto_client: CryptoHistoricalDataClient | None = None,
    ):
        self._name = "alpaca"
        self.api_key = api_key
        self.api_secret = api_secret

        if stock_client:
            self.stock_client: StockHistoricalDataClient | None = stock_client
        elif api_key and api_secret:
            self.stock_client = StockHistoricalDataClient(api_key=api_key, secret_key=api_secret)
        else:
            self.stock_client = None

        if crypto_client:
            self.crypto_client: CryptoHistoricalDataClient | None = crypto_client
        elif api_key and api_secret:
            self.crypto_client = CryptoHistoricalDataClient(api_key=api_key, secret_key=api_secret)
        else:
            self.crypto_client = None

    @property
    def name(self) -> str:
        return self._name

    def supports_symbol(self, symbol: str) -> bool:
        sym = symbol.strip().upper()
        # Alpaca does not offer CME futures continuous data (/MES, ES=F, etc.)
        if sym.startswith("/") or "=F" in sym:
            return False
        # Needs client instance or credentials
        if "/" in sym:
            return self.crypto_client is not None or bool(self.api_key and self.api_secret)
        return self.stock_client is not None or bool(self.api_key and self.api_secret)

    def _map_timeframe(self, timeframe: str) -> TimeFrame:
        tf = timeframe.strip().lower()
        if tf in ("1d", "daily", "d"):
            return TimeFrame.Day
        if tf in ("1h", "hourly", "h"):
            return TimeFrame.Hour
        if tf in ("4h", "4hour"):
            return TimeFrame(4, TimeFrameUnit.Hour)
        if tf in ("15m", "15min"):
            return TimeFrame(15, TimeFrameUnit.Minute)
        if tf in ("5m", "5min"):
            return TimeFrame(5, TimeFrameUnit.Minute)
        if tf in ("1m", "1min"):
            return TimeFrame.Minute
        return TimeFrame.Hour

    def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
        period: str | None = None,
    ) -> pd.DataFrame:
        if not self.supports_symbol(symbol):
            raise UnsupportedSymbolError(f"Alpaca does not support futures continuous contract '{symbol}'")

        is_crypto = "/" in symbol
        if is_crypto and not self.crypto_client:
            if self.api_key and self.api_secret:
                self.crypto_client = CryptoHistoricalDataClient(api_key=self.api_key, secret_key=self.api_secret)
            else:
                raise UnsupportedSymbolError("Alpaca crypto client not configured")

        if not is_crypto and not self.stock_client:
            if self.api_key and self.api_secret:
                self.stock_client = StockHistoricalDataClient(api_key=self.api_key, secret_key=self.api_secret)
            else:
                raise UnsupportedSymbolError("Alpaca stock client not configured")

        if start is None:
            delta = parse_period_to_timedelta(period or ("1y" if timeframe in ("1d", "daily") else "60d"))
            start = datetime.now(UTC) - delta

        tf = self._map_timeframe(timeframe)
        clean_sym = symbol.strip().upper()

        if is_crypto and self.crypto_client:
            crypto_req = CryptoBarsRequest(symbol_or_symbols=clean_sym, timeframe=tf, start=start, end=end)
            bars = self.crypto_client.get_crypto_bars(crypto_req)
        elif self.stock_client:
            stock_req = StockBarsRequest(symbol_or_symbols=clean_sym, timeframe=tf, start=start, end=end)
            bars = self.stock_client.get_stock_bars(stock_req)
        else:
            raise UnsupportedSymbolError("No Alpaca client available")

        df: pd.DataFrame = getattr(bars, "df", pd.DataFrame())
        if df.empty:
            return pd.DataFrame()

        # Handle MultiIndex ('symbol', 'timestamp')
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(clean_sym, level="symbol") if "symbol" in df.index.names else df.reset_index(level=0, drop=True)

        rename_map = {
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }
        df = df.rename(columns=rename_map)
        cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
        df = df[cols].dropna()
        return df

    def fetch_latest_price(self, symbol: str) -> float | None:
        if not self.supports_symbol(symbol):
            raise UnsupportedSymbolError(f"Alpaca does not support futures continuous contract '{symbol}'")

        clean_sym = symbol.strip().upper()
        is_crypto = "/" in clean_sym

        try:
            if is_crypto and self.crypto_client:
                req = CryptoLatestTradeRequest(symbol_or_symbols=clean_sym)
                trade = self.crypto_client.get_crypto_latest_trade(req)
                if trade.get(clean_sym):
                    return float(trade[clean_sym].price)
            elif self.stock_client:
                # Try trade first, fallback to latest bar
                try:
                    t_req = StockLatestTradeRequest(symbol_or_symbols=clean_sym)
                    trade = self.stock_client.get_stock_latest_trade(t_req)
                    if trade.get(clean_sym):
                        return float(trade[clean_sym].price)
                except Exception:
                    pass

                b_req = StockLatestBarRequest(symbol_or_symbols=clean_sym)
                bar = self.stock_client.get_stock_latest_bar(b_req)
                if bar.get(clean_sym):
                    return float(bar[clean_sym].close)
        except Exception:
            logger.debug("Alpaca fetch_latest_price failed for %s", symbol, exc_info=True)
            raise

        return None


class YFinanceDataProvider:
    """Market data provider fetching market data from Yahoo Finance."""

    def __init__(self):
        self._name = "yfinance"

    @property
    def name(self) -> str:
        return self._name

    def supports_symbol(self, symbol: str) -> bool:
        return True

    def _clean_df(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
        return df[cols].dropna()

    def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
        period: str | None = None,
    ) -> pd.DataFrame:
        clean_sym = symbol.strip().upper()
        if clean_sym.startswith("/"):
            clean_sym = f"{clean_sym.lstrip('/')}=F"

        interval = timeframe.strip().lower()
        if interval in ("1d", "daily", "d"):
            interval = "1d"
        elif interval in ("1h", "hourly", "h"):
            interval = "1h"
        elif interval in ("5m", "5min"):
            interval = "5m"
        elif interval in ("15m", "15min"):
            interval = "15m"

        if start is not None:
            raw = yf.download(clean_sym, start=start, end=end, interval=interval, progress=False)
        else:
            default_period = period or ("1y" if interval == "1d" else "60d")
            raw = yf.download(clean_sym, period=default_period, interval=interval, progress=False)

        clean = self._clean_df(raw)
        if clean.empty:
            raise ValueError(f"Yahoo Finance returned empty bars for '{clean_sym}'")
        return clean

    def fetch_latest_price(self, symbol: str) -> float | None:
        clean_sym = symbol.strip().upper()
        if clean_sym.startswith("/"):
            clean_sym = f"{clean_sym.lstrip('/')}=F"

        try:
            t = yf.Ticker(clean_sym)
            price = t.fast_info.get("lastPrice")
            if price is not None and not pd.isna(price) and float(price) > 0:
                return float(price)
        except Exception:
            pass

        try:
            df = yf.download(clean_sym, period="1d", interval="5m", progress=False)
            clean = self._clean_df(df)
            if not clean.empty:
                return float(clean["Close"].iloc[-1])
        except Exception:
            pass

        raise ValueError(f"Yahoo Finance unable to retrieve latest price for '{clean_sym}'")


class CompositeMarketDataProvider:
    """
    Composite provider orchestrating multiple MarketDataProviders using
    LangChain-inspired RunnableWithFallbacks for resilient cascading execution.
    """

    def __init__(
        self,
        providers: list[MarketDataProvider],
        retry_policy: RetryPolicy | None = None,
    ):
        if not providers:
            raise ValueError("CompositeMarketDataProvider requires at least one provider")
        self.providers = providers
        self.retry_policy = retry_policy or RetryPolicy()

    @property
    def name(self) -> str:
        return f"composite({', '.join(p.name for p in self.providers)})"

    def supports_symbol(self, symbol: str) -> bool:
        return any(p.supports_symbol(symbol) for p in self.providers)

    def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
        period: str | None = None,
    ) -> pd.DataFrame:
        candidates = [p for p in self.providers if p.supports_symbol(symbol)]
        if not candidates:
            raise UnsupportedSymbolError(f"No configured provider supports symbol '{symbol}'")

        primary = candidates[0]
        fallbacks = candidates[1:]

        def make_fetcher(prov: MarketDataProvider) -> Callable[..., pd.DataFrame]:
            def _call() -> pd.DataFrame:
                return prov.fetch_bars(symbol, timeframe, start=start, end=end, period=period)

            _call.__name__ = f"{prov.name}.fetch_bars"
            return _call

        runner = RunnableWithFallbacks[Any, pd.DataFrame](
            primary=make_fetcher(primary),
            fallbacks=[make_fetcher(f) for f in fallbacks],
            retry_policy=self.retry_policy,
            primary_name=primary.name,
            fallback_names=[f.name for f in fallbacks],
        )

        try:
            return runner.invoke()
        except AllFallbacksExhaustedError as e:
            logger.error(
                "All market data providers failed to fetch bars for %s (%s): %s",
                symbol,
                timeframe,
                e,
                extra={"event": "market_data_all_failed", "symbol": symbol, "timeframe": timeframe},
            )
            return pd.DataFrame()

    def fetch_latest_price(self, symbol: str) -> float | None:
        candidates = [p for p in self.providers if p.supports_symbol(symbol)]
        if not candidates:
            return None

        primary = candidates[0]
        fallbacks = candidates[1:]

        def make_pricer(prov: MarketDataProvider) -> Callable[..., float | None]:
            def _call() -> float | None:
                res = prov.fetch_latest_price(symbol)
                if res is None or res <= 0:
                    raise ValueError(f"{prov.name} returned invalid price: {res}")
                return res

            _call.__name__ = f"{prov.name}.fetch_latest_price"
            return _call

        runner = RunnableWithFallbacks[Any, float | None](
            primary=make_pricer(primary),
            fallbacks=[make_pricer(f) for f in fallbacks],
            retry_policy=self.retry_policy,
            primary_name=primary.name,
            fallback_names=[f.name for f in fallbacks],
        )

        try:
            return runner.invoke()
        except Exception as e:
            logger.warning("Failed to fetch latest price for %s: %s", symbol, e)
            return None
