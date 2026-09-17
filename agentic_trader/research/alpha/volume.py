"""Source-bound relative-volume calibration; no consolidated-liquidity inference.

Native daily bars only. Intraday bars require explicit session/seasonality buckets
before this contract can be extended. Availability is supplied, never backdated.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from agentic_trader.market.bars import FIXED_BAR_LAYOUT, utc_timestamp
from agentic_trader.market.session import ET_TZ


VOLUME_CALIBRATION_VERSION = "daily_relative_volume_v1"
MIN_VOLUME_OBSERVATIONS = 30
MAX_VOLUME_OBSERVATIONS = 10000
MAX_VOLUME_LOOKBACK = 252


@dataclass(frozen=True)
class VolumeContract:
    feed: str
    timeframe: str
    adjustment: str
    bar_layout: str

    def __post_init__(self):
        if (
            not self.feed
            or not isinstance(self.feed, str)
            or self.timeframe != "1d"
            or self.adjustment not in ("raw", "all")
            or self.bar_layout != FIXED_BAR_LAYOUT
        ):
            raise ValueError("Explicit source and native daily volume contract required")


@dataclass(frozen=True)
class VolumePolicy:
    lookback: int = 20
    min_observations: int = 200
    quantile: float = 0.9

    def __post_init__(self):
        if (
            type(self.lookback) is not int
            or not 2 <= self.lookback <= MAX_VOLUME_LOOKBACK
            or type(self.min_observations) is not int
            or not MIN_VOLUME_OBSERVATIONS <= self.min_observations <= MAX_VOLUME_OBSERVATIONS
            or isinstance(self.quantile, bool)
            or not np.isfinite(self.quantile)
            or not 0 < self.quantile < 1
        ):
            raise ValueError("Bounded relative-volume window, sample size and quantile required")


def _volume(volume):
    if (
        not isinstance(volume.index, pd.DatetimeIndex)
        or volume.index.tz is None
        or volume.empty
        or volume.index.hasnans
        or not volume.index.is_unique
        or not volume.index.is_monotonic_increasing
        or not volume.index.tz_convert(ET_TZ).equals(volume.index.tz_convert(ET_TZ).normalize())
        or not np.isfinite(volume.to_numpy(dtype=float)).all()
        or (volume < 0).any()
    ):
        raise ValueError("Complete finite nonnegative native daily volume observations required")


def _observations(volume, available_at, as_of):
    _volume(volume)
    cutoff = utc_timestamp(as_of)
    if not volume.index.equals(available_at.index) or not isinstance(available_at.dtype, pd.DatetimeTZDtype):
        raise ValueError("Aware availability must align to volume observations")
    if (
        available_at.isna().any()
        or not available_at.is_monotonic_increasing
        or (available_at < volume.index.tz_convert(ET_TZ) + pd.DateOffset(days=1)).any()
        or (available_at > cutoff).any()
    ):
        raise ValueError("Daily bars must be complete and available by the observation cutoff")
    return cutoff


def relative_volume(volume: pd.Series, policy: VolumePolicy) -> pd.Series:
    """Current observed volume / median of the preceding N bars (current excluded).

    Zero volume is observed zero; a zero denominator is unavailable, not a surge.
    Missing observations are errors, not interpolated or silently dropped.
    """
    _volume(volume)
    baseline = volume.shift(1).rolling(policy.lookback, min_periods=policy.lookback).median()
    return volume / baseline.replace(0, np.nan)


@dataclass(frozen=True)
class VolumeCalibration:
    symbol: str
    contract: VolumeContract
    policy: VolumePolicy
    training_start: str
    trained_until: str
    training_data_hash: str
    reference: tuple[float, ...]

    def __post_init__(self):
        if (
            not isinstance(self.contract, VolumeContract)
            or not isinstance(self.policy, VolumePolicy)
            or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", self.symbol)
            or utc_timestamp(self.training_start) >= utc_timestamp(self.trained_until)
            or not re.fullmatch(r"[a-f0-9]{64}", self.training_data_hash)
            or not isinstance(self.reference, tuple)
            or not self.policy.min_observations <= len(self.reference) <= MAX_VOLUME_OBSERVATIONS
            or not np.isfinite(self.reference).all()
            or min(self.reference) < 0
            or tuple(sorted(self.reference)) != self.reference
        ):
            raise ValueError("Invalid source-specific volume calibration evidence")

    @classmethod
    def fit(cls, volume, available_at, *, symbol, contract, policy, training_start, trained_until):
        cutoff = _observations(volume, available_at, trained_until)
        start = utc_timestamp(training_start)
        ratios = relative_volume(volume, policy).loc[volume.index >= start]
        if ratios.empty or not np.isfinite(ratios.to_numpy()).all():
            raise ValueError("Complete warmup and positive volume baseline required for training")
        digest = hashlib.sha256(pd.util.hash_pandas_object(volume, index=True).to_numpy().tobytes())
        digest.update(pd.util.hash_pandas_object(available_at, index=True).to_numpy().tobytes())
        return cls(
            symbol,
            contract,
            policy,
            start.isoformat(),
            cutoff.isoformat(),
            digest.hexdigest(),
            tuple(sorted(float(v) for v in ratios)),
        )

    @property
    def threshold(self):
        return float(np.quantile(self.reference, self.policy.quantile, method="linear"))

    def apply(self, volume, available_at, *, symbol, contract, as_of):
        if symbol != self.symbol or contract != self.contract:
            raise ValueError("Volume calibration symbol/source contract mismatch")
        _observations(volume, available_at, as_of)
        cutoff = utc_timestamp(self.trained_until)
        ratios = relative_volume(volume, self.policy).loc[available_at > cutoff]
        if ratios.empty or (ratios.index < cutoff).any() or not np.isfinite(ratios.to_numpy()).all():
            raise ValueError("Complete post-training observations and positive warmup baseline required")
        reference = np.asarray(self.reference)
        # Midrank ties: constant samples do not masquerade as extreme observations.
        ranks = np.searchsorted(reference, ratios, side="left") + np.searchsorted(reference, ratios, side="right")
        return pd.DataFrame(
            {"relative_volume": ratios, "percentile": ranks / (2 * len(reference)), "surge": ratios > self.threshold},
            index=ratios.index,
        )

    def document(self):
        return {"version": VOLUME_CALIBRATION_VERSION, **asdict(self), "reference": list(self.reference)}

    @classmethod
    def from_document(cls, document):
        values = {k: v for k, v in document.items() if k != "version"}
        values["contract"] = VolumeContract(**values["contract"])
        values["policy"] = VolumePolicy(**values["policy"])
        values["reference"] = tuple(values["reference"])
        result = cls(**values)
        if document != result.document():
            raise ValueError("Exact versioned volume calibration required")
        return result

    @property
    def calibration_id(self):
        return hashlib.sha256(json.dumps(self.document(), sort_keys=True, allow_nan=False).encode()).hexdigest()
