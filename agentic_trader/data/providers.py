from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import numpy as np
import pandas as pd
import yfinance as yf
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import (
    CryptoBarsRequest,
    CryptoLatestTradeRequest,
    StockBarsRequest,
    StockLatestBarRequest,
    StockLatestTradeRequest,
)
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from agentic_trader.constants import (
    DEFAULT_DAILY_LOOKBACK_PERIOD,
    DEFAULT_DATA_TIMEOUT_SECONDS,
    DEFAULT_INTRADAY_LOOKBACK_PERIOD,
)
from agentic_trader.data.evidence import BarAcquisitionError, BarEvidenceStore
from agentic_trader.market.bars import OHLCV
from agentic_trader.resilience.fallback import (
    AllFallbacksExhaustedError,
    RetryPolicy,
    RunnableWithFallbacks,
)
from agentic_trader.transport.alpaca import BoundedCryptoDataClient, BoundedStockDataClient, BoundedTransport


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
        feed: str = "sip",
        request_timeout: float = DEFAULT_DATA_TIMEOUT_SECONDS,
        evidence: BarEvidenceStore | None = None,
    ):
        self._name = "alpaca"
        self.evidence = evidence
        self.request_timeout = request_timeout
        self.feed = DataFeed(feed)
        self.api_key = api_key
        self.api_secret = api_secret

        if stock_client:
            self.stock_client: StockHistoricalDataClient | None = stock_client
        elif api_key and api_secret:
            self.stock_client = BoundedStockDataClient(
                api_key=api_key, secret_key=api_secret, request_timeout=request_timeout
            )
        else:
            self.stock_client = None

        if crypto_client:
            self.crypto_client: CryptoHistoricalDataClient | None = crypto_client
        elif api_key and api_secret:
            self.crypto_client = BoundedCryptoDataClient(
                api_key=api_key, secret_key=api_secret, request_timeout=request_timeout
            )
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
        if start is None:
            delta = parse_period_to_timedelta(
                period
                or (DEFAULT_DAILY_LOOKBACK_PERIOD if timeframe in ("1d", "daily") else DEFAULT_INTRADAY_LOOKBACK_PERIOD)
            )
            start = datetime.now(UTC) - delta

        tf = self._map_timeframe(timeframe)
        clean_sym = symbol.strip().upper()

        client = self.crypto_client if is_crypto else self.stock_client
        if client is None:
            raise UnsupportedSymbolError("No Alpaca client available")
        if self.evidence is not None and not isinstance(client, BoundedTransport):
            raise TypeError("Evidence capture requires the observed SDK transport")
        capture = (
            self.evidence.begin(
                {
                    "symbol": clean_sym,
                    "timeframe": timeframe,
                    "sdk_timeframe": str(tf),
                    "start": start.isoformat(),
                    "end_inclusive": end.isoformat() if end is not None else None,
                    "feed": "alpaca:crypto" if is_crypto else f"alpaca:{self.feed.value}",
                    "adjustment": "raw",
                }
            )
            if self.evidence
            else None
        )
        try:
            if capture:
                capture.check_capacity()
            scope = (
                client.observe_responses(capture.observe)
                if capture and isinstance(client, BoundedTransport)
                else nullcontext()
            )
            with scope:
                if is_crypto and self.crypto_client is not None:
                    bars = self.crypto_client.get_crypto_bars(
                        CryptoBarsRequest(symbol_or_symbols=clean_sym, timeframe=tf, start=start, end=end)
                    )
                elif self.stock_client is not None:
                    bars = self.stock_client.get_stock_bars(
                        StockBarsRequest(
                            symbol_or_symbols=clean_sym,
                            timeframe=tf,
                            start=start,
                            end=end,
                            feed=self.feed,
                            adjustment=Adjustment.RAW,
                        )
                    )
            df, normalization = self._normalize_bars(bars, clean_sym)
            df.attrs.update(
                feed="alpaca:crypto" if is_crypto else f"alpaca:{self.feed.value}",
                adjustment="raw",
                timeframe=timeframe,
            )
            if capture:
                df.attrs["evidence"] = capture.finish(normalization=normalization)
        except Exception as exc:
            if capture:
                raise BarAcquisitionError(type(exc).__name__, capture.fail(exc)) from exc
            raise
        return df

    @staticmethod
    def _normalize_bars(bars, symbol: str) -> tuple[pd.DataFrame, dict]:
        df: pd.DataFrame = bars.df
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(symbol, level="symbol")
        rename_map = {name: name.title() for name in OHLCV}
        if df.empty:
            df = pd.DataFrame(columns=list(rename_map.values()), index=pd.DatetimeIndex([], tz="UTC", name="timestamp"))
        else:
            df = df.rename(columns=rename_map)[list(rename_map.values())]
        missing = df.isna()
        dropped = [
            {"position": int(i), "timestamp": df.index[i].isoformat(), "missing": list(df.columns[missing.iloc[i]])}
            for i in np.flatnonzero(missing.any(axis=1).to_numpy())
        ]
        cleaned = df.dropna()
        normalization = {
            "parsed_rows": len(df),
            "normalized_rows": len(cleaned),
            "dropped_rows": dropped,
            "columns": list(cleaned.columns),
            "frame_hash": hashlib.sha256(pd.util.hash_pandas_object(cleaned, index=True).values.tobytes()).hexdigest(),
        }
        return cleaned, normalization

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
            raw = yf.download(clean_sym, start=start, end=end, interval=interval, auto_adjust=True, progress=False)
        else:
            default_period = period or (
                DEFAULT_DAILY_LOOKBACK_PERIOD if interval == "1d" else DEFAULT_INTRADAY_LOOKBACK_PERIOD
            )
            raw = yf.download(clean_sym, period=default_period, interval=interval, auto_adjust=True, progress=False)

        clean = self._clean_df(raw)
        if clean.empty:
            raise ValueError(f"Yahoo Finance returned empty bars for '{clean_sym}'")
        clean.attrs.update(feed="yfinance", adjustment="yfinance_auto_adjust", timeframe=timeframe)
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
