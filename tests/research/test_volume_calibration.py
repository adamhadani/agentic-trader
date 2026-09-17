from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from agentic_trader.market.bars import FIXED_BAR_LAYOUT
from agentic_trader.research.alpha.volume import VolumeCalibration, VolumeContract, VolumePolicy, relative_volume


@pytest.fixture
def volume_case():
    index = pd.date_range("2021-01-04", periods=100, freq="B", tz="America/New_York")
    volume = pd.Series(np.random.default_rng(42).lognormal(8, 0.5, len(index)), index=index)
    available = pd.Series(index + pd.DateOffset(days=1), index=index)
    contract = VolumeContract("alpaca:iex", "1d", "all", FIXED_BAR_LAYOUT)
    policy = VolumePolicy(lookback=5, min_observations=30, quantile=0.9)
    return volume, available, contract, policy


def fit(case):
    volume, available, contract, policy = case
    return VolumeCalibration.fit(
        volume.iloc[:60],
        available.iloc[:60],
        symbol="SPY",
        contract=contract,
        policy=policy,
        training_start=volume.index[5],
        trained_until=available.iloc[59],
    )


def test_training_only_profile_roundtrip_and_independent_rank_reference(volume_case):
    volume, available, contract, _ = volume_case
    profile = fit(volume_case)
    ratios = np.array([volume.iloc[i] / np.median(volume.iloc[i - 5 : i]) for i in range(5, 60)])
    assert profile.threshold == pytest.approx(np.quantile(ratios, 0.9))
    restored = VolumeCalibration.from_document(profile.document())
    assert restored == profile and restored.calibration_id == profile.calibration_id
    applied = profile.apply(volume, available, symbol="SPY", contract=contract, as_of=available.iloc[-1])
    assert applied.index.equals(volume.index[60:])
    for stamp, row in applied.iterrows():
        offset = volume.index.get_loc(stamp)
        rvol = volume.loc[stamp] / np.median(volume.iloc[offset - 5 : offset])
        assert row.relative_volume == pytest.approx(rvol)
        assert row.percentile == pytest.approx(((ratios < rvol).sum() + 0.5 * (ratios == rvol).sum()) / len(ratios))
        assert row.surge == (rvol > profile.threshold)


def test_scale_invariance_and_future_perturbation(volume_case):
    volume, available, contract, policy = volume_case
    profile = fit(volume_case)
    scaled = fit((volume * 0.02, available, contract, policy))
    assert scaled.threshold == pytest.approx(profile.threshold)
    assert scaled.calibration_id != profile.calibration_id
    pd.testing.assert_series_equal(relative_volume(volume, policy), relative_volume(volume * 0.02, policy))
    prefix = profile.apply(
        volume.iloc[:80], available.iloc[:80], symbol="SPY", contract=contract, as_of=available.iloc[79]
    )
    changed = volume.copy()
    changed.iloc[80:] *= 100
    full = profile.apply(changed, available, symbol="SPY", contract=contract, as_of=available.iloc[-1])
    pd.testing.assert_frame_equal(prefix, full.loc[prefix.index])
    assert fit((changed, available, contract, policy)) == profile


@pytest.mark.parametrize("field,value", [("feed", "alpaca:sip"), ("adjustment", "raw")])
def test_profile_rejects_different_source(volume_case, field, value):
    volume, available, contract, _ = volume_case
    with pytest.raises(ValueError, match="contract"):
        fit(volume_case).apply(
            volume, available, symbol="SPY", contract=replace(contract, **{field: value}), as_of=available.iloc[-1]
        )


@pytest.mark.parametrize(
    "fault", ["future", "missing", "negative", "duplicate", "zero_baseline", "insufficient", "availability"]
)
def test_invalid_training_fails_closed(volume_case, fault):
    volume, available, contract, policy = volume_case
    volume, available = volume.iloc[:60].copy(), available.iloc[:60].copy()
    cutoff = available.iloc[-1]
    if fault == "future":
        cutoff = available.iloc[-2]
    elif fault == "missing":
        volume.iloc[20] = np.nan
    elif fault == "negative":
        volume.iloc[20] = -1
    elif fault == "duplicate":
        volume.index = volume.index.where(volume.index != volume.index[20], volume.index[19])
    elif fault == "zero_baseline":
        volume.iloc[10:20] = 0
    elif fault == "insufficient":
        policy = replace(policy, min_observations=100)
    else:
        available.iloc[20] = volume.index[20]
    with pytest.raises(ValueError):
        VolumeCalibration.fit(
            volume,
            available,
            symbol="SPY",
            contract=contract,
            policy=policy,
            training_start=volume.index[5],
            trained_until=cutoff,
        )


@pytest.mark.parametrize("fault", ["symbol", "future", "zero_baseline", "warmup"])
def test_application_refuses_unknown_or_incomplete_evidence(volume_case, fault):
    volume, available, contract, _ = volume_case
    profile = fit(volume_case)
    symbol, as_of = "SPY", available.iloc[-1]
    if fault == "symbol":
        symbol = "IWM"
    elif fault == "future":
        as_of = available.iloc[-2]
    elif fault == "zero_baseline":
        volume.iloc[70:80] = 0
    else:
        volume, available = volume.iloc[60:], available.iloc[60:]
    with pytest.raises(ValueError):
        profile.apply(volume, available, symbol=symbol, contract=contract, as_of=as_of)


@pytest.mark.parametrize("timeframe,layout", [("15m", FIXED_BAR_LAYOUT), ("1d", "rth_open_v1")])
def test_intraday_and_session_profiles_need_a_separate_seasonality_contract(timeframe, layout):
    with pytest.raises(ValueError):
        VolumeContract("alpaca:iex", timeframe, "all", layout)


def test_equivalent_utc_timestamps_keep_profile_identity(volume_case):
    volume, available, contract, policy = volume_case
    utc_volume = volume.tz_convert("UTC")
    utc_available = available.tz_convert("UTC").dt.tz_convert("UTC")
    assert fit((utc_volume, utc_available, contract, policy)) == fit(volume_case)
