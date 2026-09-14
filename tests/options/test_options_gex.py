from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest
import yfinance as yf

from agentic_trader.notifier.telegram_bot import TelegramNotifier
from agentic_trader.options.fetcher import OptionsDataFetcher
from agentic_trader.options.gex import GEXCalculator, black_scholes_gamma
from agentic_trader.options.models import GammaExposureProfile, GammaRegime, StrikeGEX
from agentic_trader.options.reporting import format_gex_report, format_gex_telegram


def test_black_scholes_gamma_analytical():
    """Verify analytical Black-Scholes gamma calculation against closed-form values."""
    spot = 100.0
    strike = 100.0
    t = 30.0 / 365.0
    r = 0.045
    iv = 0.20

    gamma = black_scholes_gamma(spot, strike, t, r, iv)
    assert gamma > 0.0
    # Standard ATM 30-day gamma for 100-strike option is around 0.06 - 0.08
    assert 0.05 < gamma < 0.09

    # Far OTM option should have much lower gamma
    gamma_otm = black_scholes_gamma(spot, 130.0, t, r, iv)
    assert 0.0 <= gamma_otm < gamma

    # Edge cases
    assert black_scholes_gamma(0.0, 100.0, t, r, iv) == 0.0
    assert black_scholes_gamma(100.0, 0.0, t, r, iv) == 0.0
    assert black_scholes_gamma(100.0, 100.0, 0.0, r, iv) == 0.0
    assert black_scholes_gamma(100.0, 100.0, t, r, 0.0) == 0.0


def test_gex_calculation_synthetic_chain():
    """Verify GEX calculator identifies call wall, put wall, and aggregates net dealer gamma."""
    calculator = GEXCalculator(risk_free_rate=0.045)
    spot = 500.0

    # Synthetic Calls: Peak at 520 (Call Wall)
    calls_data = [
        {"strike": 480.0, "openInterest": 500, "volume": 100, "impliedVolatility": 0.22, "dte_years": 10 / 365},
        {"strike": 490.0, "openInterest": 1500, "volume": 400, "impliedVolatility": 0.20, "dte_years": 10 / 365},
        {"strike": 500.0, "openInterest": 4000, "volume": 1200, "impliedVolatility": 0.18, "dte_years": 10 / 365},
        {"strike": 510.0, "openInterest": 6000, "volume": 1500, "impliedVolatility": 0.17, "dte_years": 10 / 365},
        {"strike": 520.0, "openInterest": 10000, "volume": 2500, "impliedVolatility": 0.16, "dte_years": 10 / 365},
    ]

    # Synthetic Puts: Peak at 480 (Put Wall)
    puts_data = [
        {"strike": 480.0, "openInterest": 12000, "volume": 3000, "impliedVolatility": 0.25, "dte_years": 10 / 365},
        {"strike": 490.0, "openInterest": 5000, "volume": 1000, "impliedVolatility": 0.22, "dte_years": 10 / 365},
        {"strike": 500.0, "openInterest": 3000, "volume": 800, "impliedVolatility": 0.19, "dte_years": 10 / 365},
        {"strike": 510.0, "openInterest": 1000, "volume": 200, "impliedVolatility": 0.17, "dte_years": 10 / 365},
        {"strike": 520.0, "openInterest": 200, "volume": 50, "impliedVolatility": 0.16, "dte_years": 10 / 365},
    ]

    calls_df = pd.DataFrame(calls_data)
    puts_df = pd.DataFrame(puts_data)

    profile = calculator.calculate_gex(
        symbol="SPY",
        underlying_price=spot,
        calls_df=calls_df,
        puts_df=puts_df,
        expirations=["2026-09-20"],
    )

    assert profile.symbol == "SPY"
    assert profile.underlying_price == 500.0
    assert profile.call_wall_strike == 520.0
    assert profile.call_wall_oi == 10000
    assert profile.put_wall_strike == 480.0
    assert profile.put_wall_oi == 12000

    assert profile.total_call_gex > 0.0
    assert profile.total_put_gex < 0.0
    assert len(profile.strikes) == 5

    # Put/Call ratios
    total_put_oi = sum(p["openInterest"] for p in puts_data)
    total_call_oi = sum(c["openInterest"] for c in calls_data)
    expected_pcr_oi = round(total_put_oi / total_call_oi, 3)
    assert profile.pcr_open_interest == expected_pcr_oi


def test_gamma_flip_level_identification():
    """Verify linear interpolation detects gamma flip point where net GEX crosses zero."""
    calculator = GEXCalculator(risk_free_rate=0.045)
    spot = 500.0

    # Low strikes heavily put-dominated (negative net GEX), high strikes call-dominated (positive net GEX)
    calls_data = [
        {"strike": 480.0, "openInterest": 100, "impliedVolatility": 0.20, "dte_years": 14 / 365},
        {"strike": 490.0, "openInterest": 200, "impliedVolatility": 0.20, "dte_years": 14 / 365},
        {"strike": 500.0, "openInterest": 5000, "impliedVolatility": 0.20, "dte_years": 14 / 365},
        {"strike": 510.0, "openInterest": 8000, "impliedVolatility": 0.20, "dte_years": 14 / 365},
    ]
    puts_data = [
        {"strike": 480.0, "openInterest": 8000, "impliedVolatility": 0.20, "dte_years": 14 / 365},
        {"strike": 490.0, "openInterest": 5000, "impliedVolatility": 0.20, "dte_years": 14 / 365},
        {"strike": 500.0, "openInterest": 200, "impliedVolatility": 0.20, "dte_years": 14 / 365},
        {"strike": 510.0, "openInterest": 100, "impliedVolatility": 0.20, "dte_years": 14 / 365},
    ]

    profile = calculator.calculate_gex(
        symbol="SPY",
        underlying_price=spot,
        calls_df=pd.DataFrame(calls_data),
        puts_df=pd.DataFrame(puts_data),
    )

    assert profile.gamma_flip_strike is not None
    # Gamma flip should be between 490 and 500
    assert 489.0 <= profile.gamma_flip_strike <= 501.0


def test_options_data_fetcher_proxy_resolution():
    """Verify futures tickers correctly resolve to ETF option proxies."""
    assert OptionsDataFetcher.resolve_symbol("/MES") == "SPY"
    assert OptionsDataFetcher.resolve_symbol("MES=F") == "SPY"
    assert OptionsDataFetcher.resolve_symbol("/MNQ") == "QQQ"
    assert OptionsDataFetcher.resolve_symbol("NQ=F") == "QQQ"
    assert OptionsDataFetcher.resolve_symbol("/M2K") == "IWM"
    assert OptionsDataFetcher.resolve_symbol("/MGC") == "GLD"
    assert OptionsDataFetcher.resolve_symbol("/MCL") == "USO"
    assert OptionsDataFetcher.resolve_symbol("SPY") == "SPY"
    assert OptionsDataFetcher.resolve_symbol("AAPL") == "AAPL"


def test_gex_reporting_formatters():
    """Verify ASCII and Telegram GEX report generators."""
    profile = GammaExposureProfile(
        symbol="SPY",
        underlying_price=512.45,
        total_net_gex=142.50,
        total_call_gex=210.30,
        total_put_gex=-67.80,
        gamma_regime=GammaRegime.POSITIVE_GAMMA,
        gamma_flip_strike=504.20,
        call_wall_strike=520.0,
        put_wall_strike=500.0,
        call_wall_oi=45000,
        put_wall_oi=52000,
        pcr_open_interest=1.15,
        pcr_volume=0.92,
        strikes=[
            StrikeGEX(strike=500.0, call_gex=10.0, put_gex=-40.0, net_gex=-30.0, call_oi=5000, put_oi=52000),
            StrikeGEX(strike=510.0, call_gex=55.0, put_gex=-20.0, net_gex=35.0, call_oi=25000, put_oi=15000),
            StrikeGEX(strike=520.0, call_gex=110.0, put_gex=-5.0, net_gex=105.0, call_oi=45000, put_oi=2000),
        ],
        expirations_analyzed=["2026-09-18", "2026-09-25"],
        timestamp=datetime.now(UTC),
    )

    ascii_report = format_gex_report(profile)
    assert "OPTIONS GAMMA EXPOSURE (GEX) & VOLATILITY SURFACE: SPY" in ascii_report
    assert "$512.45" in ascii_report
    assert "POSITIVE GAMMA (+GEX" in ascii_report
    assert "$142.50M per 1% move" in ascii_report
    assert "$504.20" in ascii_report
    assert "$520.00" in ascii_report
    assert "CALL WALL" in ascii_report
    assert "PUT WALL" in ascii_report

    telegram_card = format_gex_telegram(profile)
    assert "GAMMA EXPOSURE: SPY" in telegram_card
    assert "POSITIVE GAMMA (+GEX)" in telegram_card
    assert "$512.45" in telegram_card
    assert "$142.50M / 1%" in telegram_card
    assert "$520.00" in telegram_card
    assert "$500.00" in telegram_card


def test_gex_empty_chain_handling():
    """Verify graceful handling when no contracts or invalid prices are passed."""
    calculator = GEXCalculator()
    with pytest.raises(ValueError, match="Underlying price must be positive"):
        calculator.calculate_gex("SPY", 0.0, pd.DataFrame(), pd.DataFrame())

    # Empty calls and puts
    res = calculator.calculate_gex("SPY", 500.0, pd.DataFrame(), pd.DataFrame())
    assert res.total_net_gex == 0.0
    assert res.gamma_regime == GammaRegime.NEUTRAL
    assert res.strikes == []


@pytest.mark.asyncio
async def test_telegram_gex_command():
    """Verify Telegram /gex command dispatches formatted output."""
    mock_db = MagicMock()
    mock_gex = AsyncMock(return_value="🧭 <b>GAMMA EXPOSURE: SPY</b>\n• Spot: $500.00")

    notifier = TelegramNotifier(
        bot_token="123456:ABC-DEF",
        chat_id="999888",
        db=mock_db,
        gex_provider=mock_gex,
    )

    mock_update = MagicMock()
    mock_update.effective_chat.id = 999888
    mock_update.message = MagicMock()
    mock_update.message.reply_text = AsyncMock()

    mock_context = MagicMock()
    mock_context.args = ["SPY"]

    await notifier.handle_gex_command(mock_update, mock_context)
    mock_gex.assert_called_once_with("SPY")
    assert mock_update.message.reply_text.call_count == 2
    mock_update.message.reply_text.assert_called_with(
        "🧭 <b>GAMMA EXPOSURE: SPY</b>\n• Spot: $500.00", parse_mode="HTML"
    )


def test_options_data_fetcher_mocked(monkeypatch):
    """Verify OptionsDataFetcher with mocked Yahoo Finance options chain."""
    fetcher = OptionsDataFetcher(cache_ttl_seconds=5)

    class MockFastInfo:
        last_price = 505.25

    class MockChain:
        calls = pd.DataFrame([{"strike": 510.0, "openInterest": 1500, "volume": 300, "impliedVolatility": 0.18}])
        puts = pd.DataFrame([{"strike": 500.0, "openInterest": 2500, "volume": 600, "impliedVolatility": 0.20}])

    class MockTicker:
        def __init__(self, sym):
            self.sym = sym
            self.fast_info = MockFastInfo()
            self.options = ["2026-09-25"]

        def option_chain(self, date):
            return MockChain()

    monkeypatch.setattr(yf, "Ticker", MockTicker)

    profile = fetcher.fetch_and_calculate_gex("SPY", max_expirations=1)
    assert profile.symbol == "SPY"
    assert profile.underlying_price == 505.25
    assert profile.call_wall_strike == 510.0
    assert profile.put_wall_strike == 500.0
    assert profile.pcr_open_interest > 0.0
