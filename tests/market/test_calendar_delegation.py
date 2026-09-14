from __future__ import annotations

from datetime import date, datetime, time
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import httpx
import pytest

from agentic_trader.constants import AssetClass
from agentic_trader.market.session import (
    AlpacaCalendarProvider,
    CMEFuturesSessionProvider,
    CompositeMarketCalendar,
    CompositeMarketSessionProvider,
    DeterministicCalendarProvider,
    FinnhubCalendarProvider,
    MarketCalendarDay,
    MarketSessionType,
)


ET = ZoneInfo("America/New_York")


@pytest.mark.asyncio
async def test_deterministic_calendar_provider():
    provider = DeterministicCalendarProvider()

    # Normal Wednesday
    wed = date(2026, 9, 16)
    day = await provider.get_trading_day(wed)
    assert day.is_trading_day is True
    assert day.is_early_close is False
    assert day.open_time == time(9, 30)
    assert day.close_time == time(16, 0)
    assert day.source == "deterministic"

    # Weekend (Saturday)
    sat = date(2026, 9, 19)
    day_sat = await provider.get_trading_day(sat)
    assert day_sat.is_trading_day is False
    assert day_sat.holiday_name == "Weekend"

    # Full holiday (Thanksgiving 2026-11-26)
    tg = date(2026, 11, 26)
    day_tg = await provider.get_trading_day(tg)
    assert day_tg.is_trading_day is False
    assert day_tg.holiday_name == "Thanksgiving Day"

    # Early close (Black Friday 2026-11-27)
    bf = date(2026, 11, 27)
    day_bf = await provider.get_trading_day(bf)
    assert day_bf.is_trading_day is True
    assert day_bf.is_early_close is True
    assert day_bf.close_time == time(13, 0)

    # Range
    rng = await provider.get_calendar_range(date(2026, 11, 26), date(2026, 11, 28))
    assert len(rng) == 3
    assert rng[0].is_trading_day is False  # Thanksgiving
    assert rng[1].is_trading_day is True  # Black Friday
    assert rng[2].is_trading_day is False  # Saturday


@pytest.mark.asyncio
async def test_alpaca_calendar_provider_unconfigured():
    provider = AlpacaCalendarProvider(trading_client=None)
    with pytest.raises(RuntimeError, match="Alpaca TradingClient is not configured"):
        await provider.get_trading_day(date(2026, 9, 16))


@pytest.mark.asyncio
async def test_alpaca_calendar_provider_with_mock_client():
    mock_client = MagicMock()

    # Create mock items for July 3, 2024 (early close) and normal day July 2, 2024
    item_jul2 = MagicMock()
    item_jul2.date = date(2024, 7, 2)
    item_jul2.open = datetime(2024, 7, 2, 9, 30, tzinfo=ET)
    item_jul2.close = datetime(2024, 7, 2, 16, 0, tzinfo=ET)

    item_jul3 = MagicMock()
    item_jul3.date = date(2024, 7, 3)
    item_jul3.open = datetime(2024, 7, 3, 9, 30, tzinfo=ET)
    item_jul3.close = datetime(2024, 7, 3, 13, 0, tzinfo=ET)

    # July 4 is omitted from Alpaca's calendar
    mock_client.get_calendar.return_value = [item_jul2, item_jul3]

    provider = AlpacaCalendarProvider(trading_client=mock_client)

    # July 2: Normal trading day
    day_jul2 = await provider.get_trading_day(date(2024, 7, 2))
    assert day_jul2.is_trading_day is True
    assert day_jul2.is_early_close is False
    assert day_jul2.source == "alpaca_api"

    # July 3: Early close
    day_jul3 = await provider.get_trading_day(date(2024, 7, 3))
    assert day_jul3.is_trading_day is True
    assert day_jul3.is_early_close is True
    assert day_jul3.close_time == time(13, 0)

    # July 4: Statutory holiday (omitted from returned list)
    day_jul4 = await provider.get_trading_day(date(2024, 7, 4))
    assert day_jul4.is_trading_day is False
    assert day_jul4.holiday_name == "Independence Day"

    # Verify annual cache: get_calendar was called only once for year 2024
    await provider.get_trading_day(date(2024, 7, 2))
    assert mock_client.get_calendar.call_count == 1


@pytest.mark.asyncio
async def test_finnhub_calendar_provider_unconfigured():
    provider = FinnhubCalendarProvider(api_key=None)
    with pytest.raises(RuntimeError, match="Finnhub API key is not configured"):
        await provider.get_trading_day(date(2026, 9, 16))


@pytest.mark.asyncio
async def test_finnhub_calendar_provider_with_mock_api():
    mock_response = {
        "data": [
            {
                "atDate": "2024-07-03",
                "eventName": "Independence Day",
                "tradingHour": "09:30-13:00",
            },
            {
                "atDate": "2024-07-04",
                "eventName": "Independence Day",
                "tradingHour": "",
            },
        ]
    }

    mock_resp_obj = MagicMock()
    mock_resp_obj.status_code = 200
    mock_resp_obj.json.return_value = mock_response

    provider = FinnhubCalendarProvider(api_key="valid_token_123")

    with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp_obj

        # July 3: Early close
        day_jul3 = await provider.get_trading_day(date(2024, 7, 3))
        assert day_jul3.is_trading_day is True
        assert day_jul3.is_early_close is True
        assert day_jul3.close_time == time(13, 0)
        assert day_jul3.holiday_name == "Independence Day"
        assert day_jul3.source == "finnhub_api"

        # July 4: Full closure
        day_jul4 = await provider.get_trading_day(date(2024, 7, 4))
        assert day_jul4.is_trading_day is False
        assert day_jul4.holiday_name == "Independence Day"

        # Normal Wednesday not in holiday list
        day_normal = await provider.get_trading_day(date(2024, 7, 10))
        assert day_normal.is_trading_day is True
        assert day_normal.is_early_close is False
        assert day_normal.open_time == time(9, 30)
        assert day_normal.close_time == time(16, 0)


@pytest.mark.asyncio
async def test_composite_market_calendar_fallback_cascade():
    # Primary: Alpaca fails
    mock_alpaca = MagicMock()
    mock_alpaca.get_calendar.side_effect = ConnectionError("Alpaca API unavailable")

    # Secondary: Finnhub fails
    mock_resp_obj = MagicMock()
    mock_resp_obj.status_code = 503

    cal = CompositeMarketCalendar(
        alpaca_client=mock_alpaca,
        finnhub_api_key="test_token",
        primary_provider="alpaca",
        fallback_providers=["finnhub", "deterministic"],
        max_retries=0,
    )

    with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp_obj

        # Cascades to deterministic fallback without throwing!
        day = await cal.get_trading_day(date(2026, 11, 26))
        assert day.source == "deterministic"
        assert day.is_trading_day is False
        assert day.holiday_name == "Thanksgiving Day"


@pytest.mark.asyncio
async def test_cme_futures_dynamic_early_close_sync():
    # Construct a custom mock calendar provider that declares a date as early close
    custom_cal = MagicMock()
    early_day = MarketCalendarDay(
        date=date(2024, 7, 3),
        is_trading_day=True,
        is_early_close=True,
        open_time=time(9, 30),
        close_time=time(13, 0),
        holiday_name="Day Before Independence Day",
        source="mock_third_party",
    )
    custom_cal.get_trading_day = AsyncMock(return_value=early_day)

    futures_provider = CMEFuturesSessionProvider(calendar_provider=custom_cal)

    # 1. During RTH on early close day (10:30 ET)
    dt_rth = datetime(2024, 7, 3, 10, 30, tzinfo=ET)
    info_rth = await futures_provider.get_session_info("/MES", dt_rth)
    assert info_rth.is_open is True
    assert info_rth.is_rth is True
    assert info_rth.session_type == MarketSessionType.RTH
    assert info_rth.next_close == datetime(2024, 7, 3, 13, 15, tzinfo=ET)

    # 2. After CME 13:15 ET early close (13:30 ET) -> HOLIDAY_HALT
    dt_halt = datetime(2024, 7, 3, 13, 30, tzinfo=ET)
    info_halt = await futures_provider.get_session_info("/MES", dt_halt)
    assert info_halt.is_open is False
    assert info_halt.session_type == MarketSessionType.HOLIDAY_HALT


@pytest.mark.asyncio
async def test_composite_session_provider_with_calendar():
    provider = CompositeMarketSessionProvider(
        config=None,
        alpaca_client=None,
    )

    # Futures info
    fut_info = await provider.get_session_info("/MES", datetime(2026, 9, 16, 10, 0, tzinfo=ET))
    assert fut_info.asset_class == AssetClass.FUTURES
    assert fut_info.is_rth is True

    # Equities info
    eq_info = await provider.get_session_info("SPY", datetime(2026, 9, 16, 10, 0, tzinfo=ET))
    assert eq_info.asset_class == AssetClass.EQUITY
    assert eq_info.is_rth is True
