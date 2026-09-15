import logging
from dataclasses import dataclass, field

import pandas as pd

from agentic_trader.config import AppConfig, load_config
from agentic_trader.constants import (
    DEFAULT_DATA_MAX_RETRIES,
    DEFAULT_DATA_RETRY_BACKOFF_FACTOR,
    DEFAULT_DATA_TIMEOUT_SECONDS,
    DEFAULT_MARKET_DATA_CACHE_TTL_SECONDS,
)
from agentic_trader.data.providers import (
    AlpacaDataProvider,
    CompositeMarketDataProvider,
    MarketDataProvider,
    YFinanceDataProvider,
)
from agentic_trader.resilience.fallback import RetryPolicy
from agentic_trader.screeners.indicators import (
    calculate_atr,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_keltner_channels,
    calculate_rsi,
    consecutive_squeeze_count,
    detect_squeeze,
)


logger = logging.getLogger(__name__)


@dataclass
class ContractMarketData:
    contract: str = ""
    ticker: str = ""
    daily: pd.DataFrame = field(default_factory=pd.DataFrame)
    four_hour: pd.DataFrame = field(default_factory=pd.DataFrame)
    hourly: pd.DataFrame = field(default_factory=pd.DataFrame)
    fifteen_minute: pd.DataFrame = field(default_factory=pd.DataFrame)
    symbol: str = ""
    one_hour: pd.DataFrame = field(default_factory=pd.DataFrame)

    def __post_init__(self):
        if not self.contract and self.symbol:
            self.contract = self.symbol
        if not self.ticker and self.contract:
            self.ticker = self.contract
        if self.hourly.empty and not self.one_hour.empty:
            self.hourly = self.one_hour
        elif not self.hourly.empty and self.one_hour.empty:
            self.one_hour = self.hourly


class MarketDataFetcher:
    def __init__(
        self,
        cache_ttl_seconds: int = DEFAULT_MARKET_DATA_CACHE_TTL_SECONDS,
        config: AppConfig | None = None,
        provider: MarketDataProvider | None = None,
    ):
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cache: dict[str, ContractMarketData] = {}

        if provider:
            self.provider = provider
        else:
            cfg = config or load_config()
            alpaca_prov = AlpacaDataProvider(api_key=cfg.alpaca_api_key, api_secret=cfg.alpaca_api_secret)
            yf_prov = YFinanceDataProvider()

            md_cfg = getattr(cfg, "market_data", None)
            if md_cfg and md_cfg.primary_equities_provider == "yfinance":
                providers: list[MarketDataProvider] = [yf_prov, alpaca_prov]
            else:
                providers = [alpaca_prov, yf_prov]

            retry_policy = RetryPolicy(
                max_retries=md_cfg.max_retries if md_cfg else DEFAULT_DATA_MAX_RETRIES,
                backoff_factor=md_cfg.retry_backoff_factor if md_cfg else DEFAULT_DATA_RETRY_BACKOFF_FACTOR,
                timeout_seconds=md_cfg.timeout_seconds if md_cfg else DEFAULT_DATA_TIMEOUT_SECONDS,
            )
            self.provider = CompositeMarketDataProvider(providers, retry_policy=retry_policy)

    def _clean_yfinance_df(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
        return df[cols].dropna()

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
        if df.empty or len(df) < 5:
            return df
        res = df.copy()
        res["EMA_20"] = calculate_ema(res["Close"], span=20)
        res["EMA_50"] = calculate_ema(res["Close"], span=50)
        res["EMA_200"] = calculate_ema(res["Close"], span=200)
        res["ATR_14"] = calculate_atr(res["High"], res["Low"], res["Close"], period=14)
        res["RSI_14"] = calculate_rsi(res["Close"], period=14)

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
        if "Volume" in res:
            res["Volume_SMA_20"] = res["Volume"].rolling(window=20).mean()

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
        fifteen_min_period: str = "10d",
        include_fifteen_min: bool = True,
    ) -> ContractMarketData:
        """Fetch market data via resilient providers and compute all indicators."""
        # 1. Daily
        clean_daily = self.provider.fetch_bars(ticker, "1d", period=daily_period)
        df_daily = self.compute_daily_indicators(clean_daily)

        # 2. Hourly
        clean_1h = self.provider.fetch_bars(ticker, "1h", period=hourly_period)
        df_1h = self.compute_intraday_indicators(clean_1h)

        # 3. 4-Hour (Resampled from 1h)
        clean_4h = self.resample_to_4h(clean_1h)
        df_4h = self.compute_intraday_indicators(clean_4h)

        # 4. 15-Minute
        df_15m = pd.DataFrame()
        if include_fifteen_min:
            try:
                clean_15m = self.provider.fetch_bars(ticker, "15m", period=fifteen_min_period)
                df_15m = self.compute_intraday_indicators(clean_15m)
            except Exception as e:
                logger.debug("Could not fetch 15m bars for %s: %s", ticker, e)

        market_data = ContractMarketData(
            contract=contract,
            ticker=ticker,
            daily=df_daily,
            four_hour=df_4h,
            hourly=df_1h,
            fifteen_minute=df_15m,
        )
        return market_data

    def fetch_latest_price(self, ticker: str) -> float | None:
        """Fetch the current market quote for a ticker via resilient providers."""
        return self.provider.fetch_latest_price(ticker)

    def calculate_correlation(self, ticker_a: str, ticker_b: str, lookback_days: int = 60) -> float | None:
        """Compute Pearson return correlation between two tickers over lookback period."""
        if ticker_a.strip().upper() == ticker_b.strip().upper():
            return 1.0
        try:
            data_a = self.fetch_data(ticker_a, ticker_a)
            data_b = self.fetch_data(ticker_b, ticker_b)
            if data_a.daily.empty or data_b.daily.empty:
                return None
            close_a = data_a.daily["Close"].tail(lookback_days).pct_change().dropna()
            close_b = data_b.daily["Close"].tail(lookback_days).pct_change().dropna()
            combined = pd.concat([close_a, close_b], axis=1, join="inner")
            if len(combined) < 15:
                return None
            corr = float(combined.iloc[:, 0].corr(combined.iloc[:, 1]))
            if pd.isna(corr):
                return None
            return round(corr, 4)
        except Exception:
            return None
