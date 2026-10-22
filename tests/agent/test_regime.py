from datetime import UTC, datetime

import pytest

from agentic_trader.agent.macro import MACRO_INPUT_SOURCES, MacroInput, MacroIntelligenceEngine
from agentic_trader.agent.regime import (
    RegimeDetector,
    RegimeSnapshot,
    RegimeUnavailable,
    classify_vix_level,
)
from agentic_trader.config import RegimeConfig
from agentic_trader.constants import VolatilityRegime


def _vix_only_detector(quote, config: RegimeConfig | None = None) -> RegimeDetector:
    """A VIX-only detector whose engine reads ``quote(ticker)`` (None: no observation) through ``yahoo_fetch``."""
    config = config or RegimeConfig(yield_curve_enabled=False)

    def yahoo_fetch(name: str, ticker: str, *, decision: bool) -> MacroInput:
        value = quote(ticker)
        return MacroInput(
            name=name,
            value=value,
            source=MACRO_INPUT_SOURCES[name],
            observed_on=None,
            decision=decision,
            error=None if value is not None else "no_observation",
        )

    engine = MacroIntelligenceEngine(config=config, yahoo_fetch=yahoo_fetch, vix_retry_delay_seconds=0)
    return RegimeDetector(config, macro_engine=engine)


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
    # VIX=17.5, TNX=4.25, DXY=103.5
    detector = _vix_only_detector({"^VIX": 17.5, "^TNX": 4.25, "DX-Y.NYB": 103.5}.get)

    snapshot = await detector.get_regime(force_refresh=True)

    assert snapshot.vix == 17.5
    assert snapshot.vix_regime == VolatilityRegime.NORMAL
    assert snapshot.breakout_allowed is True
    assert snapshot.min_rr_threshold == 2.0
    assert snapshot.tnx == 4.25
    assert snapshot.dxy == 103.5
    assert snapshot.risk_multiplier == 1.0 and snapshot.macro_stress is None
    assert "NORMAL" in snapshot.summary_text
    assert "Allowed" in snapshot.summary_text


@pytest.mark.asyncio
async def test_regime_detector_extreme():
    detector = _vix_only_detector({"^VIX": 34.8, "^TNX": 5.10, "DX-Y.NYB": 106.2}.get)

    snapshot = await detector.get_regime(force_refresh=True)

    assert snapshot.vix == 34.8
    assert snapshot.vix_regime == VolatilityRegime.EXTREME
    assert snapshot.breakout_allowed is False
    assert snapshot.min_rr_threshold == 2.5
    assert "EXTREME" in snapshot.summary_text
    assert "Suppressed" in snapshot.summary_text


@pytest.mark.asyncio
async def test_regime_detector_caching():
    calls: list[str] = []

    def quote(ticker):
        calls.append(ticker)
        return 18.0

    detector = _vix_only_detector(quote, RegimeConfig(cache_ttl_seconds=300, yield_curve_enabled=False))
    first = await detector.get_regime(force_refresh=True)
    assert len(calls) == 3  # VIX, TNX, DXY

    # Second call within TTL should return cached object
    second = await detector.get_regime(force_refresh=False)
    assert len(calls) == 3
    assert first is second

    # Force refresh should bypass cache
    await detector.get_regime(force_refresh=True)
    assert len(calls) == 6


@pytest.mark.asyncio
@pytest.mark.parametrize("vix", [None, 0.0, -1.0])
async def test_regime_detector_fallback_on_error(vix):
    detector = _vix_only_detector({"^VIX": vix, "^TNX": 4.2, "DX-Y.NYB": 103.0}.get)

    with pytest.raises(RegimeUnavailable, match="^Market regime unavailable: missing vix$") as caught:
        await detector.get_regime(force_refresh=True)
    assert caught.value.inputs["vix"].error == ("no_observation" if vix is None else "unexpected_payload")


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
        risk_multiplier=1.0,
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
    detector = _vix_only_detector(lambda ticker: 26.0, config)
    snapshot = await detector.get_regime()
    assert snapshot.vix_regime == VolatilityRegime.EXTREME
    assert not snapshot.breakout_allowed
    assert snapshot.min_rr_threshold == 2.9
