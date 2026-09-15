from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from agentic_trader.agent.regime import (
    RegimeDetector,
    RegimeSnapshot,
    classify_vix_level,
)
from agentic_trader.config import RegimeConfig
from agentic_trader.constants import VolatilityRegime


def test_classify_vix_level():
    config = RegimeConfig(
        vix_compressed_threshold=15.0,
        vix_elevated_threshold=22.0,
        vix_extreme_threshold=30.0,
    )

    # Compressed: < 15.0
    assert classify_vix_level(12.5, config) == VolatilityRegime.COMPRESSED
    assert classify_vix_level(14.99, config) == VolatilityRegime.COMPRESSED

    # Normal: 15.0 <= VIX <= 22.0
    assert classify_vix_level(15.0, config) == VolatilityRegime.NORMAL
    assert classify_vix_level(18.5, config) == VolatilityRegime.NORMAL
    assert classify_vix_level(22.0, config) == VolatilityRegime.NORMAL

    # Elevated: 22.0 < VIX <= 30.0
    assert classify_vix_level(22.01, config) == VolatilityRegime.ELEVATED
    assert classify_vix_level(26.4, config) == VolatilityRegime.ELEVATED
    assert classify_vix_level(30.0, config) == VolatilityRegime.ELEVATED

    # Extreme: > 30.0
    assert classify_vix_level(30.01, config) == VolatilityRegime.EXTREME
    assert classify_vix_level(45.0, config) == VolatilityRegime.EXTREME


@pytest.mark.asyncio
async def test_regime_detector_normal():
    detector = RegimeDetector(RegimeConfig(yield_curve_enabled=False))

    with patch("agentic_trader.agent.regime._fetch_ticker_sync") as mock_fetch:
        # Mock VIX=17.5, TNX=4.25, DXY=103.5
        mock_fetch.side_effect = lambda ticker: {
            "^VIX": 17.5,
            "^TNX": 4.25,
            "DX-Y.NYB": 103.5,
        }.get(ticker, 0.0)

        snapshot = await detector.get_regime(force_refresh=True)

        assert snapshot.vix == 17.5
        assert snapshot.vix_regime == VolatilityRegime.NORMAL
        assert snapshot.breakout_allowed is True
        assert snapshot.min_rr_threshold == 2.0
        assert snapshot.tnx == 4.25
        assert snapshot.dxy == 103.5
        assert "NORMAL" in snapshot.summary_text
        assert "Allowed" in snapshot.summary_text


@pytest.mark.asyncio
async def test_regime_detector_extreme():
    detector = RegimeDetector(RegimeConfig(yield_curve_enabled=False))

    with patch("agentic_trader.agent.regime._fetch_ticker_sync") as mock_fetch:
        mock_fetch.side_effect = lambda ticker: {
            "^VIX": 34.8,
            "^TNX": 5.10,
            "DX-Y.NYB": 106.2,
        }.get(ticker, 0.0)

        snapshot = await detector.get_regime(force_refresh=True)

        assert snapshot.vix == 34.8
        assert snapshot.vix_regime == VolatilityRegime.EXTREME
        assert snapshot.breakout_allowed is False
        assert snapshot.min_rr_threshold == 2.5
        assert "EXTREME" in snapshot.summary_text
        assert "Suppressed" in snapshot.summary_text


@pytest.mark.asyncio
async def test_regime_detector_caching():
    detector = RegimeDetector(RegimeConfig(cache_ttl_seconds=300, yield_curve_enabled=False))

    call_count = 0

    def mock_fetch(ticker):
        nonlocal call_count
        call_count += 1
        return 18.0

    with patch("agentic_trader.agent.regime._fetch_ticker_sync", side_effect=mock_fetch):
        first = await detector.get_regime(force_refresh=True)
        assert call_count == 3  # VIX, TNX, DXY

        # Second call within TTL should return cached object
        second = await detector.get_regime(force_refresh=False)
        assert call_count == 3
        assert first is second

        # Force refresh should bypass cache
        await detector.get_regime(force_refresh=True)
        assert call_count == 6


@pytest.mark.asyncio
async def test_regime_detector_fallback_on_error():
    detector = RegimeDetector(RegimeConfig(yield_curve_enabled=False))

    with (
        patch("agentic_trader.agent.regime._fetch_ticker_sync", return_value=None),
        pytest.raises(ValueError, match="VIX data unavailable"),
    ):
        await detector.get_regime(force_refresh=True)


def test_prompt_context_formatting():
    detector = RegimeDetector(RegimeConfig(yield_curve_enabled=False))
    snapshot = RegimeSnapshot(
        vix=16.5,
        vix_regime=VolatilityRegime.NORMAL,
        tnx=4.30,
        dxy=102.8,
        breakout_allowed=True,
        min_rr_threshold=2.0,
        timestamp=datetime.now(UTC),
        summary_text="VIX: 16.50 (NORMAL)",
    )

    ctx = detector.get_prompt_context(snapshot)
    assert "NORMAL" in ctx
    assert "16.50" in ctx
    assert "4.30%" in ctx
    assert "102.80" in ctx
    assert "ALLOWED" in ctx


@pytest.mark.asyncio
async def test_configured_volatility_thresholds_and_rr():
    config = RegimeConfig(yield_curve_enabled=False, vix_extreme_threshold=25, extreme_min_rr=2.9)
    detector = RegimeDetector(config)
    with patch("agentic_trader.agent.regime._fetch_ticker_sync", return_value=26.0):
        snapshot = await detector.get_regime()
    assert snapshot.vix_regime == VolatilityRegime.EXTREME
    assert not snapshot.breakout_allowed
    assert snapshot.min_rr_threshold == 2.9
