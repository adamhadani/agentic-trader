from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from agentic_trader.constants import AssetClass
from agentic_trader.market.session import (
    AlpacaMarketSessionProvider,
    CMEFuturesSessionProvider,
    CompositeMarketSessionProvider,
    CryptoSessionProvider,
    MarketSessionType,
)


ET = ZoneInfo("America/New_York")


@pytest.mark.asyncio
async def test_crypto_session_always_open():
    provider = CryptoSessionProvider()
    assert await provider.is_market_open("BTC/USD") is True
    assert await provider.is_rth("BTC/USD") is True

    info = await provider.get_session_info("ETH/USD")
    assert info.is_open is True
    assert info.is_rth is True
    assert info.session_type == MarketSessionType.RTH
    assert info.asset_class == AssetClass.CRYPTO


@pytest.mark.asyncio
async def test_cme_futures_rth_session():
    provider = CMEFuturesSessionProvider()
    # Wednesday at 10:30 ET -> RTH
    wed_rth = datetime(2026, 9, 16, 10, 30, tzinfo=ET)
    assert await provider.is_market_open("/MES", wed_rth) is True
    assert await provider.is_rth("/MES", wed_rth) is True

    info = await provider.get_session_info("/MES", wed_rth)
    assert info.is_open is True
    assert info.is_rth is True
    assert info.session_type == MarketSessionType.RTH
    assert info.asset_class == AssetClass.FUTURES


@pytest.mark.asyncio
async def test_cme_futures_daily_halt():
    provider = CMEFuturesSessionProvider()
    # Wednesday at 17:30 ET -> Daily Maintenance Halt
    wed_halt = datetime(2026, 9, 16, 17, 30, tzinfo=ET)
    assert await provider.is_market_open("/MNQ", wed_halt) is False
    assert await provider.is_rth("/MNQ", wed_halt) is False

    info = await provider.get_session_info("/MNQ", wed_halt)
    assert info.is_open is False
    assert info.is_rth is False
    assert info.session_type == MarketSessionType.DAILY_HALT
    assert info.next_open is not None
    assert info.next_open.hour == 18


@pytest.mark.asyncio
async def test_cme_futures_weekend_halt():
    provider = CMEFuturesSessionProvider()
    # Saturday at 12:00 ET -> Weekend Halt
    sat_noon = datetime(2026, 9, 19, 12, 0, tzinfo=ET)
    assert await provider.is_market_open("/MGC", sat_noon) is False
    assert await provider.is_rth("/MGC", sat_noon) is False

    info = await provider.get_session_info("/MGC", sat_noon)
    assert info.is_open is False
    assert info.session_type == MarketSessionType.WEEKEND_HALT
    assert info.next_open is not None
    assert info.next_open.weekday() == 6  # Sunday
    assert info.next_open.hour == 18


@pytest.mark.asyncio
async def test_cme_futures_eth_overnight():
    provider = CMEFuturesSessionProvider()
    # Tuesday at 02:00 ET -> ETH (Overnight Globex)
    tue_night = datetime(2026, 9, 15, 2, 0, tzinfo=ET)
    assert await provider.is_market_open("/MCL", tue_night) is True
    assert await provider.is_rth("/MCL", tue_night) is False

    info = await provider.get_session_info("/MCL", tue_night)
    assert info.is_open is True
    assert info.is_rth is False
    assert info.session_type == MarketSessionType.ETH


@pytest.mark.asyncio
async def test_alpaca_market_session_mock_clock():
    mock_client = MagicMock()
    mock_clock = MagicMock()
    mock_clock.is_open = True
    mock_clock.next_open = datetime(2026, 9, 17, 9, 30, tzinfo=ET)
    mock_clock.next_close = datetime(2026, 9, 16, 16, 0, tzinfo=ET)
    mock_client.get_clock.return_value = mock_clock

    provider = AlpacaMarketSessionProvider(trading_client=mock_client)
    info = await provider.get_session_info("SPY")
    assert info.is_open is True
    assert info.is_rth is True
    assert info.session_type == MarketSessionType.RTH
    assert info.source == "alpaca_api"


@pytest.mark.asyncio
async def test_alpaca_market_session_static_fallback():
    # When trading client is None, falls back to static NYSE schedule
    provider = AlpacaMarketSessionProvider(trading_client=None)

    # Historical Wednesday at 11:00 ET -> RTH
    wed_rth = datetime(2026, 9, 16, 11, 0, tzinfo=ET)
    info = await provider.get_session_info("QQQ", timestamp=wed_rth)
    assert info.is_open is True
    assert info.is_rth is True
    assert info.session_type == MarketSessionType.RTH

    # Sunday at 14:00 ET -> Weekend Halt
    sun = datetime(2026, 9, 20, 14, 0, tzinfo=ET)
    sun_info = await provider.get_session_info("QQQ", timestamp=sun)
    assert sun_info.is_open is False
    assert sun_info.session_type == MarketSessionType.WEEKEND_HALT


@pytest.mark.asyncio
async def test_composite_market_session_routing():
    composite = CompositeMarketSessionProvider()

    wed_rth = datetime(2026, 9, 16, 10, 0, tzinfo=ET)

    # Futures routing
    fut_info = await composite.get_session_info("/MES", wed_rth)
    assert fut_info.asset_class == AssetClass.FUTURES
    assert fut_info.is_rth is True

    # Crypto routing
    crypto_info = await composite.get_session_info("BTC/USD", wed_rth)
    assert crypto_info.asset_class == AssetClass.CRYPTO
    assert crypto_info.is_open is True

    # Equity routing
    eq_info = await composite.get_session_info("SPY", wed_rth)
    assert eq_info.asset_class == AssetClass.EQUITY
    assert eq_info.is_rth is True
