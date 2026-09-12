from dataclasses import dataclass

import pandas as pd
import yfinance as yf

from agentic_trader.screeners.indicators import (
    calculate_atr,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_keltner_channels,
    calculate_rsi,
    consecutive_squeeze_count,
    detect_squeeze,
)


@dataclass
class ContractMarketData:
    contract: str
    ticker: str
    daily: pd.DataFrame
    four_hour: pd.DataFrame
    hourly: pd.DataFrame


class MarketDataFetcher:
    def __init__(self, cache_ttl_seconds: int = 300):
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cache: dict[str, ContractMarketData] = {}

    def _clean_yfinance_df(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
        return df

    def resample_to_4h(self, df_1h: pd.DataFrame) -> pd.DataFrame:
        """Resample 1-hour OHLCV candles to 4-hour candles."""
        if df_1h.empty:
            return pd.DataFrame()
        resampled = (
            df_1h.resample("4h")
            .agg(
                {
                    "Open": "first",
                    "High": "max",
                    "Low": "min",
                    "Close": "last",
                    "Volume": "sum",
                }
            )
            .dropna()
        )
        return resampled

    def compute_daily_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        res = df.copy()
        res["EMA_50"] = calculate_ema(res["Close"], span=50)
        res["EMA_200"] = calculate_ema(res["Close"], span=200)
        res["ATR_14"] = calculate_atr(res["High"], res["Low"], res["Close"], period=14)
        return res

    def compute_intraday_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty or len(df) < 5:
            return df
        res = df.copy()
        res["EMA_20"] = calculate_ema(res["Close"], span=20)
        res["EMA_50"] = calculate_ema(res["Close"], span=50)
        res["EMA_200"] = calculate_ema(res["Close"], span=200)
        res["RSI_14"] = calculate_rsi(res["Close"], period=14)
        res["ATR_14"] = calculate_atr(res["High"], res["Low"], res["Close"], period=14)

        bb_u, bb_m, bb_l = calculate_bollinger_bands(res["Close"], period=20, num_std=2.0)
        res["BB_Upper"] = bb_u
        res["BB_Middle"] = bb_m
        res["BB_Lower"] = bb_l

        kc_u, kc_m, kc_l = calculate_keltner_channels(
            res["High"], res["Low"], res["Close"], period=20, atr_multiplier=1.5
        )
        res["KC_Upper"] = kc_u
        res["KC_Middle"] = kc_m
        res["KC_Lower"] = kc_l

        res["Squeeze"] = detect_squeeze(bb_u, bb_l, kc_u, kc_l)
        res["Squeeze_Count"] = consecutive_squeeze_count(res["Squeeze"])
        res["Volume_SMA_20"] = res["Volume"].rolling(window=20).mean()

        return res

    def fetch_data(
        self,
        contract: str,
        ticker: str,
        daily_period: str = "1y",
        hourly_period: str = "60d",
    ) -> ContractMarketData:
        """Fetch real market data from Yahoo Finance and compute all indicators."""
        # 1. Daily
        raw_daily = yf.download(ticker, period=daily_period, interval="1d", progress=False)
        clean_daily = self._clean_yfinance_df(raw_daily)
        df_daily = self.compute_daily_indicators(clean_daily)

        # 2. Hourly
        raw_1h = yf.download(ticker, period=hourly_period, interval="1h", progress=False)
        clean_1h = self._clean_yfinance_df(raw_1h)
        df_1h = self.compute_intraday_indicators(clean_1h)

        # 3. 4-Hour (Resampled from 1h)
        clean_4h = self.resample_to_4h(clean_1h)
        df_4h = self.compute_intraday_indicators(clean_4h)

        market_data = ContractMarketData(
            contract=contract,
            ticker=ticker,
            daily=df_daily,
            four_hour=df_4h,
            hourly=df_1h,
        )
        return market_data

    def fetch_latest_price(self, ticker: str) -> float | None:
        """Fetch the current market quote for a ticker."""
        try:
            t = yf.Ticker(ticker)
            price = t.fast_info.get("lastPrice")
            if price is not None and not pd.isna(price):
                return float(price)
        except Exception:
            pass

        try:
            df = yf.download(ticker, period="1d", interval="5m", progress=False)
            clean = self._clean_yfinance_df(df)
            if not clean.empty:
                return float(clean["Close"].iloc[-1])
        except Exception:
            pass

        return None
