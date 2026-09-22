import pytest

from agentic_trader.screeners.base import ScreenerCandidate, clamp01
from agentic_trader.screeners.formulaic import alpha_setup_quality
from agentic_trader.screeners.strategies import squeeze_setup_quality, trend_pullback_setup_quality


@pytest.mark.parametrize(
    ("value", "expected"), [(-1.0, 0.0), (0.0, 0.0), (0.4, 0.4), (1.0, 1.0), (7.0, 1.0), (float("nan"), 0.0)]
)
def test_clamp01(value, expected):
    assert clamp01(value) == expected


def test_candidate_defaults_to_zero_quality_and_rejects_out_of_range():
    kwargs = {
        "contract": "X",
        "timeframe": "4h",
        "strategy": "s",
        "direction": "LONG",
        "current_price": 1.0,
        "ema_20": 1.0,
        "ema_50": 1.0,
        "ema_200": 1.0,
        "rsi_14": 50.0,
        "atr_14": 1.0,
        "candle_timestamp": "t",
        "recent_swing_low": 0.9,
        "recent_swing_high": 1.1,
        "trigger_detail": "d",
    }
    assert ScreenerCandidate(**kwargs).setup_quality == 0.0
    with pytest.raises(ValueError):
        ScreenerCandidate(**kwargs, setup_quality=1.5)


def test_trend_pullback_quality_is_monotone_in_each_component():
    base = {
        "daily_fast": 105.0,
        "daily_slow": 100.0,
        "dist_to_trigger": 0.5,
        "tolerance": 1.0,
        "rsi_now": 45.0,
        "rsi_extreme": 35.0,
        "recovery_span": 15.0,
    }
    q = trend_pullback_setup_quality(**base)
    assert 0.0 < q < 1.0
    assert trend_pullback_setup_quality(**{**base, "daily_fast": 110.0}) > q  # stronger trend
    assert trend_pullback_setup_quality(**{**base, "dist_to_trigger": 0.1}) > q  # closer to the trigger EMA
    assert trend_pullback_setup_quality(**{**base, "rsi_now": 50.0}) > q  # bigger RSI recovery
    assert (
        trend_pullback_setup_quality(
            daily_fast=100.0,
            daily_slow=100.0,
            dist_to_trigger=1.0,
            tolerance=1.0,
            rsi_now=35.0,
            rsi_extreme=35.0,
            recovery_span=15.0,
        )
        == 0.0
    )
    assert (
        trend_pullback_setup_quality(
            daily_fast=200.0,
            daily_slow=100.0,
            dist_to_trigger=0.0,
            tolerance=1.0,
            rsi_now=80.0,
            rsi_extreme=35.0,
            recovery_span=15.0,
        )
        == 1.0
    )


def test_squeeze_quality_is_monotone_in_squeeze_length_and_volume():
    q = squeeze_setup_quality(squeeze_bars=8, volume_ratio=1.8, volume_factor=1.3)
    assert 0.0 < q < 1.0
    assert squeeze_setup_quality(squeeze_bars=16, volume_ratio=1.8, volume_factor=1.3) > q
    assert squeeze_setup_quality(squeeze_bars=8, volume_ratio=2.5, volume_factor=1.3) > q
    assert squeeze_setup_quality(squeeze_bars=40, volume_ratio=5.0, volume_factor=1.3) == 1.0


def test_formulaic_candidates_map_z_to_quality():
    assert alpha_setup_quality(0.0) == 0.0
    assert alpha_setup_quality(1.5) == 0.5
    assert alpha_setup_quality(-3.0) == 1.0
    assert alpha_setup_quality(9.0) == 1.0
