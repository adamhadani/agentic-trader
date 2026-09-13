from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import yfinance as yf

from agentic_trader.options.gex import GEXCalculator
from agentic_trader.options.models import GammaExposureProfile


logger = logging.getLogger(__name__)

# Futures contracts to liquid ETF option proxies
FUTURES_TO_ETF_PROXY: dict[str, str] = {
    "/MES": "SPY",
    "MES=F": "SPY",
    "ES=F": "SPY",
    "/MNQ": "QQQ",
    "MNQ=F": "QQQ",
    "NQ=F": "QQQ",
    "/M2K": "IWM",
    "RTY=F": "IWM",
    "/MGC": "GLD",
    "GC=F": "GLD",
    "/MCL": "USO",
    "CL=F": "USO",
}


class OptionsDataFetcher:
    """Fetches option chains via yfinance and analyzes market maker gamma exposure."""

    def __init__(self, risk_free_rate: float = 0.045, cache_ttl_seconds: int = 60):
        self.calculator = GEXCalculator(risk_free_rate=risk_free_rate)
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cache: dict[str, tuple[float, GammaExposureProfile]] = {}

    @classmethod
    def resolve_symbol(cls, symbol: str) -> str:
        """Resolves futures or alternative tickers to liquid ETF option proxy tickers."""
        cleaned = symbol.strip().upper()
        return FUTURES_TO_ETF_PROXY.get(cleaned, cleaned)

    def fetch_and_calculate_gex(
        self,
        symbol: str,
        max_expirations: int = 3,
        force_refresh: bool = False,
    ) -> GammaExposureProfile:
        """Fetches options chain for symbol (or ETF proxy) and computes gamma exposure profile."""
        target_symbol = self.resolve_symbol(symbol)
        now_ts = datetime.now(UTC).timestamp()

        if not force_refresh and target_symbol in self._cache:
            cache_time, cached_profile = self._cache[target_symbol]
            if (now_ts - cache_time) < self.cache_ttl_seconds:
                return cached_profile

        ticker = yf.Ticker(target_symbol)
        underlying_price = self._get_underlying_price(ticker, target_symbol)

        try:
            available_expirations = list(ticker.options or [])
        except Exception as e:
            logger.warning(f"Could not retrieve option expirations for {target_symbol}: {e}")
            available_expirations = []

        if not available_expirations:
            raise ValueError(f"No option expirations available for {target_symbol}")

        target_expirations = available_expirations[:max_expirations]
        all_calls: list[pd.DataFrame] = []
        all_puts: list[pd.DataFrame] = []
        now = datetime.now(UTC)

        for exp_date_str in target_expirations:
            try:
                chain = ticker.option_chain(exp_date_str)
                exp_dt = datetime.strptime(exp_date_str, "%Y-%m-%d").replace(tzinfo=UTC)
                dte_days = max(0.25, (exp_dt - now).total_seconds() / 86400.0)
                dte_years = dte_days / 365.0

                if chain.calls is not None and not chain.calls.empty:
                    c_df = chain.calls.copy()
                    c_df["dte_years"] = dte_years
                    all_calls.append(c_df)

                if chain.puts is not None and not chain.puts.empty:
                    p_df = chain.puts.copy()
                    p_df["dte_years"] = dte_years
                    all_puts.append(p_df)
            except Exception as e:
                logger.warning(f"Failed to fetch option chain for {target_symbol} on {exp_date_str}: {e}")

        combined_calls = pd.concat(all_calls, ignore_index=True) if all_calls else pd.DataFrame()
        combined_puts = pd.concat(all_puts, ignore_index=True) if all_puts else pd.DataFrame()

        profile = self.calculator.calculate_gex(
            symbol=target_symbol,
            underlying_price=underlying_price,
            calls_df=combined_calls,
            puts_df=combined_puts,
            expirations=target_expirations,
        )

        self._cache[target_symbol] = (now_ts, profile)
        return profile

    def _get_underlying_price(self, ticker: Any, symbol: str) -> float:
        """Extracts current market price for underlying ticker."""
        try:
            fast_info = getattr(ticker, "fast_info", None)
            if fast_info is not None:
                last_price = getattr(fast_info, "last_price", None)
                if last_price and float(last_price) > 0:
                    return float(last_price)
        except Exception:
            pass

        try:
            hist = ticker.history(period="1d")
            if not hist.empty and "Close" in hist.columns:
                val = float(hist["Close"].iloc[-1])
                if val > 0:
                    return val
        except Exception:
            pass

        # Fallback default estimate for liquid ETFs if offline/mocked
        default_prices = {"SPY": 500.0, "QQQ": 440.0, "IWM": 200.0, "GLD": 215.0, "USO": 75.0}
        return default_prices.get(symbol, 100.0)
