from datetime import UTC, date, datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from agentic_trader.constants import AssetClass
from agentic_trader.market.session import (
    AlpacaMarketSessionProvider,
    CMEFuturesSessionProvider,
    CompositeMarketSessionProvider,
    CryptoSessionProvider,
    MarketHolidayCalendar,
    MarketSessionType,
    ensure_et,
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


def test_market_holiday_calendar_calculations():
    cal = MarketHolidayCalendar.get_year_calendar(2026)
    statutory = cal["statutory_holidays"]

    # 2026 statutory holidays
    assert date(2026, 1, 1) in statutory  # New Year's Day
    assert date(2026, 1, 19) in statutory  # MLK Day
    assert date(2026, 2, 16) in statutory  # Presidents' Day
    assert date(2026, 4, 3) in statutory  # Good Friday
    assert date(2026, 5, 25) in statutory  # Memorial Day
    assert date(2026, 6, 19) in statutory  # Juneteenth
    assert date(2026, 7, 3) in statutory  # July 4th observed (Fri)
    assert date(2026, 9, 7) in statutory  # Labor Day
    assert date(2026, 11, 26) in statutory  # Thanksgiving Day
    assert date(2026, 12, 25) in statutory  # Christmas Day

    # Early close
    assert date(2026, 11, 27) in cal["early_closes"]  # Black Friday
    assert date(2026, 12, 24) in cal["early_closes"]  # Christmas Eve (Thursday)

    # July 3 early close verification (2024 July 4 was Thursday, 2025 July 4 was Friday)
    cal_2024 = MarketHolidayCalendar.get_year_calendar(2024)
    assert date(2024, 7, 3) in cal_2024["early_closes"]  # Wednesday before Thursday July 4
    cal_2025 = MarketHolidayCalendar.get_year_calendar(2025)
    assert date(2025, 7, 3) in cal_2025["early_closes"]  # Thursday before Friday July 4
    # In 2026, July 4 is Saturday so July 3 is observed statutory holiday, not early close
    assert date(2026, 7, 3) not in cal["early_closes"]
    assert date(2026, 7, 3) in statutory


@pytest.mark.asyncio
async def test_cme_holiday_session_behavior():
    provider = CMEFuturesSessionProvider()

    # 1. Thanksgiving Day (2026-11-26)
    # Morning: Open in ETH (halts at 13:00 ET)
    tg_morning = datetime(2026, 11, 26, 10, 0, tzinfo=ET)
    info_tg_morn = await provider.get_session_info("/MES", tg_morning)
    assert info_tg_morn.is_open is True
    assert info_tg_morn.is_rth is False
    assert info_tg_morn.session_type == MarketSessionType.ETH

    # Afternoon: CME 13:00 - 18:00 ET Halt
    tg_halt = datetime(2026, 11, 26, 14, 30, tzinfo=ET)
    info_tg_halt = await provider.get_session_info("/MES", tg_halt)
    assert info_tg_halt.is_open is False
    assert info_tg_halt.session_type == MarketSessionType.HOLIDAY_HALT
    assert info_tg_halt.next_open == datetime(2026, 11, 26, 18, 0, tzinfo=ET)

    # Evening: Reopened for trade date Nov 27
    tg_eve = datetime(2026, 11, 26, 19, 0, tzinfo=ET)
    info_tg_eve = await provider.get_session_info("/MES", tg_eve)
    assert info_tg_eve.is_open is True
    assert info_tg_eve.session_type == MarketSessionType.ETH

    # 2. Christmas Day (2026-12-25) - Full Day Halt until 18:00 ET
    xmas_midday = datetime(2026, 12, 25, 12, 0, tzinfo=ET)
    info_xmas = await provider.get_session_info("/MES", xmas_midday)
    assert info_xmas.is_open is False
    assert info_xmas.session_type == MarketSessionType.HOLIDAY_HALT

    # 3. Good Friday (2026-04-03) - Halts at 09:15 ET
    gf_late = datetime(2026, 4, 3, 10, 0, tzinfo=ET)
    info_gf = await provider.get_session_info("/MES", gf_late)
    assert info_gf.is_open is False
    assert info_gf.session_type == MarketSessionType.HOLIDAY_HALT

    # 4. Black Friday (2026-11-27) - Early Close at 13:15 ET
    bf_rth = datetime(2026, 11, 27, 10, 30, tzinfo=ET)
    info_bf_rth = await provider.get_session_info("/MES", bf_rth)
    assert info_bf_rth.is_open is True
    assert info_bf_rth.is_rth is True
    assert info_bf_rth.session_type == MarketSessionType.RTH

    bf_closed = datetime(2026, 11, 27, 14, 0, tzinfo=ET)
    info_bf_closed = await provider.get_session_info("/MES", bf_closed)
    assert info_bf_closed.is_open is False
    assert info_bf_closed.session_type == MarketSessionType.HOLIDAY_HALT


@pytest.mark.asyncio
async def test_equity_holiday_session_behavior():
    provider = AlpacaMarketSessionProvider(trading_client=None)

    # Thanksgiving Day (2026-11-26) at 11:00 ET -> Closed all day
    tg = datetime(2026, 11, 26, 11, 0, tzinfo=ET)
    info_tg = await provider.get_session_info("SPY", timestamp=tg)
    assert info_tg.is_open is False
    assert info_tg.is_rth is False
    assert info_tg.session_type == MarketSessionType.HOLIDAY_HALT

    # Black Friday (2026-11-27) at 11:00 ET -> RTH
    bf_rth = datetime(2026, 11, 27, 11, 0, tzinfo=ET)
    info_bf_rth = await provider.get_session_info("SPY", timestamp=bf_rth)
    assert info_bf_rth.is_open is True
    assert info_bf_rth.is_rth is True
    assert info_bf_rth.session_type == MarketSessionType.RTH

    # Black Friday at 13:30 ET -> Post-market / ETH
    bf_post = datetime(2026, 11, 27, 13, 30, tzinfo=ET)
    info_bf_post = await provider.get_session_info("SPY", timestamp=bf_post)
    assert info_bf_post.is_open is False
    assert info_bf_post.session_type == MarketSessionType.ETH


def test_ensure_et_timezone_normalization():
    # Naive timestamp assumes UTC
    naive_dt = datetime(2026, 9, 16, 14, 0)  # noqa: DTZ001  # 14:00 UTC = 10:00 ET
    et_dt = ensure_et(naive_dt)
    assert et_dt.tzinfo == ET
    assert et_dt.hour == 10

    # Aware UTC timestamp
    utc_dt = datetime(2026, 9, 16, 14, 0, tzinfo=UTC)
    et_from_utc = ensure_et(utc_dt)
    assert et_from_utc.hour == 10

    # Other timezone (e.g. UTC+3)
    tz_plus_3 = ZoneInfo("Asia/Jerusalem")
    local_dt = datetime(2026, 9, 16, 17, 0, tzinfo=tz_plus_3)  # 17:00 IDT (UTC+3) = 10:00 EDT (UTC-4)
    et_from_local = ensure_et(local_dt)
    assert et_from_local.hour == 10


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("instrument_type", "dt", "expected_active"),
    [
        ("crypto", datetime(2026, 9, 16, 10, 0, tzinfo=ET), True),
        ("crypto", datetime(2026, 12, 25, 12, 0, tzinfo=ET), True),  # 24/7 even on Christmas
        ("futures", datetime(2026, 9, 16, 10, 0, tzinfo=ET), True),  # Wednesday 10:00 ET
        ("futures", datetime(2026, 9, 16, 17, 30, tzinfo=ET), False),  # CME daily halt 17:00-18:00
        ("futures", datetime(2026, 12, 25, 12, 0, tzinfo=ET), False),  # Christmas day halt
        ("equity", datetime(2026, 9, 16, 10, 0, tzinfo=ET), True),  # Wednesday RTH
        ("equity", datetime(2026, 9, 16, 18, 0, tzinfo=ET), False),  # Outside RTH
        ("all", datetime(2026, 9, 16, 10, 0, tzinfo=ET), True),  # Normal trading day
    ],
)
async def test_composite_is_session_active(instrument_type: str, dt: datetime, expected_active: bool):
    provider = CompositeMarketSessionProvider(config=None, alpaca_client=None)
    active, reason = await provider.is_session_active(instrument_type, timestamp=dt)
    assert active is expected_active
    assert isinstance(reason, str)
