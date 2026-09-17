from __future__ import annotations

from datetime import UTC
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from agentic_trader.data.market_data import MarketDataFetcher
from agentic_trader.data.providers import (
    AlpacaDataProvider,
    CompositeMarketDataProvider,
    UnsupportedSymbolError,
    YFinanceDataProvider,
)
from agentic_trader.resilience.fallback import RetryPolicy


@pytest.fixture
def mock_alpaca_bars_df() -> pd.DataFrame:
    """Synthetic Alpaca MultiIndex DataFrame."""
    dates = pd.date_range("2026-09-01", periods=5, freq="D", tz=UTC)
    idx = pd.MultiIndex.from_product([["SPY"], dates], names=["symbol", "timestamp"])
    return pd.DataFrame(
        {
            "open": [500.0, 501.0, 502.0, 503.0, 504.0],
            "high": [505.0, 506.0, 507.0, 508.0, 509.0],
            "low": [495.0, 496.0, 497.0, 498.0, 499.0],
            "close": [502.0, 503.0, 504.0, 505.0, 506.0],
            "volume": [10000, 12000, 11000, 13000, 14000],
            "trade_count": [100, 120, 110, 130, 140],
            "vwap": [502.5, 503.5, 504.5, 505.5, 506.5],
        },
        index=idx,
    )


def test_alpaca_provider_supports_symbol():
    provider = AlpacaDataProvider(api_key="test", api_secret="test")
    assert provider.supports_symbol("SPY") is True
    assert provider.supports_symbol("QQQ") is True
    assert provider.supports_symbol("BTC/USD") is True
    assert provider.supports_symbol("/MES") is False
    assert provider.supports_symbol("ES=F") is False


@pytest.mark.parametrize("attribute", ["stock_client", "crypto_client"])
def test_default_alpaca_readers_have_transport_deadlines(attribute):
    provider = AlpacaDataProvider(api_key="fake-key", api_secret="fake-secret", request_timeout=0.25)
    client = getattr(provider, attribute)
    assert client.request_timeout == 0.25


def test_alpaca_provider_unsupported_symbol_raises():
    provider = AlpacaDataProvider(api_key="test", api_secret="test")
    with pytest.raises(UnsupportedSymbolError):
        provider.fetch_bars("/MES", "1d")


def test_alpaca_provider_fetch_bars_standardizes_df(mock_alpaca_bars_df):
    mock_client = MagicMock()
    mock_bar_set = MagicMock()
    mock_bar_set.df = mock_alpaca_bars_df
    mock_client.get_stock_bars.return_value = mock_bar_set

    provider = AlpacaDataProvider(stock_client=mock_client)
    df = provider.fetch_bars("SPY", "1d", period="5d")

    assert not df.empty
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert len(df) == 5
    assert df["Close"].iloc[-1] == 506.0
    assert not isinstance(df.index, pd.MultiIndex)


def test_alpaca_provider_fetch_latest_price():
    mock_client = MagicMock()
    mock_trade = MagicMock()
    mock_trade.price = 505.25
    mock_client.get_stock_latest_trade.return_value = {"SPY": mock_trade}

    provider = AlpacaDataProvider(stock_client=mock_client)
    price = provider.fetch_latest_price("SPY")
    assert price == 505.25


def test_yfinance_provider_fetch_bars():
    provider = YFinanceDataProvider()
    dates = pd.date_range("2026-09-01", periods=5, freq="D")
    sample_df = pd.DataFrame(
        {
            "Open": [500.0, 501.0, 502.0, 503.0, 504.0],
            "High": [505.0, 506.0, 507.0, 508.0, 509.0],
            "Low": [495.0, 496.0, 497.0, 498.0, 499.0],
            "Close": [502.0, 503.0, 504.0, 505.0, 506.0],
            "Volume": [10000, 12000, 11000, 13000, 14000],
        },
        index=dates,
    )

    with patch("agentic_trader.data.providers.yf.download", return_value=sample_df):
        df = provider.fetch_bars("/MES", "1d")
        assert not df.empty
        assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
        assert df["Close"].iloc[-1] == 506.0


def test_composite_provider_falls_back_when_alpaca_fails(mock_alpaca_bars_df):
    mock_alpaca = MagicMock()
    mock_alpaca.name = "alpaca"
    mock_alpaca.supports_symbol.return_value = True
    mock_alpaca.fetch_bars.side_effect = TimeoutError("Alpaca rate limited")

    dates = pd.date_range("2026-09-01", periods=5, freq="D")
    yf_df = pd.DataFrame(
        {
            "Open": [500.0, 501.0, 502.0, 503.0, 504.0],
            "High": [505.0, 506.0, 507.0, 508.0, 509.0],
            "Low": [495.0, 496.0, 497.0, 498.0, 499.0],
            "Close": [502.0, 503.0, 504.0, 505.0, 506.0],
            "Volume": [10000, 12000, 11000, 13000, 14000],
        },
        index=dates,
    )
    mock_yf = MagicMock()
    mock_yf.name = "yfinance"
    mock_yf.supports_symbol.return_value = True
    mock_yf.fetch_bars.return_value = yf_df

    composite = CompositeMarketDataProvider(
        providers=[mock_alpaca, mock_yf],
        retry_policy=RetryPolicy(max_retries=1, backoff_factor=0.01),
    )

    result_df = composite.fetch_bars("SPY", "1d")
    assert not result_df.empty
    assert mock_alpaca.fetch_bars.call_count == 2  # 1 initial + 1 retry
    mock_yf.fetch_bars.assert_called_once_with("SPY", "1d", start=None, end=None, period=None)


def test_composite_provider_routes_futures_directly_to_yfinance():
    mock_alpaca = MagicMock()
    mock_alpaca.name = "alpaca"
    mock_alpaca.supports_symbol.return_value = False

    mock_yf = MagicMock()
    mock_yf.name = "yfinance"
    mock_yf.supports_symbol.return_value = True
    mock_yf.fetch_bars.return_value = pd.DataFrame({"Close": [5800.0]})

    composite = CompositeMarketDataProvider(providers=[mock_alpaca, mock_yf])
    res = composite.fetch_bars("/MES", "1d")
    assert not res.empty
    mock_alpaca.fetch_bars.assert_not_called()
    mock_yf.fetch_bars.assert_called_once()


def test_market_data_fetcher_integration():
    mock_provider = MagicMock()
    dates = pd.date_range("2026-08-01", periods=60, freq="D")
    base_df = pd.DataFrame(
        {
            "Open": [500.0 + i for i in range(60)],
            "High": [505.0 + i for i in range(60)],
            "Low": [495.0 + i for i in range(60)],
            "Close": [502.0 + i for i in range(60)],
            "Volume": [10000 + i * 100 for i in range(60)],
        },
        index=dates,
    )
    mock_provider.fetch_bars.return_value = base_df
    mock_provider.fetch_latest_price.return_value = 561.0

    fetcher = MarketDataFetcher(provider=mock_provider)
    data = fetcher.fetch_data("SPY", "SPY")
    assert data.contract == "SPY"
    assert "EMA_20" in data.daily.columns
    assert "ATR_14" in data.daily.columns
    assert "RSI_14" in data.daily.columns
    assert fetcher.fetch_latest_price("SPY") == 561.0
