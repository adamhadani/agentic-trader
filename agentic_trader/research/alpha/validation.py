"""Predeclared temporal splits and reproducible research data identities."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from itertools import pairwise

import numpy as np
import pandas as pd

from agentic_trader.market.bars import BAR_DURATIONS, FIXED_BAR_LAYOUT, fixed_bar_closes


RETURN_TIMELINE = "complete_observed_bars_v1"


@dataclass(frozen=True)
class ValidationPolicy:
    return_timeline: str = RETURN_TIMELINE
    holdout_fraction: float = 0.2
    folds: int = 3
    initial_train_fraction: float = 0.5
    label_horizon: int = 1
    embargo_bars: int = 1
    minimum_validation_bars: int = 20
    minimum_trades: int = 10
    min_sharpe: float = 1.0
    min_dsr: float = 0.95
    min_ic: float = 0.01
    max_drawdown_pct: float = 20.0

    def __post_init__(self):
        if self.return_timeline != RETURN_TIMELINE:
            raise ValueError("Unsupported research return timeline")
        if not 0 < self.holdout_fraction < 0.5 or not 0.2 <= self.initial_train_fraction <= 0.8:
            raise ValueError("Invalid temporal split")
        if (
            self.folds < 2
            or self.label_horizon < 1
            or self.embargo_bars < 0
            or self.minimum_validation_bars < 10
            or self.minimum_trades < 1
        ):
            raise ValueError("Invalid validation budget")
        if (
            not all(np.isfinite(v) for v in (self.min_sharpe, self.min_dsr, self.min_ic, self.max_drawdown_pct))
            or not 0 < self.min_dsr < 1
        ):
            raise ValueError("Invalid qualification thresholds")


@dataclass(frozen=True)
class TemporalFold:
    train_end: int
    validation_start: int
    validation_end: int


def purged_folds(length: int, policy: ValidationPolicy) -> tuple[TemporalFold, ...]:
    discovery_end = int(length * (1 - policy.holdout_fraction))
    first_validation = int(discovery_end * policy.initial_train_fraction)
    edges = np.linspace(first_validation, discovery_end, policy.folds + 1, dtype=int)
    folds = tuple(
        TemporalFold(int(a - policy.label_horizon - policy.embargo_bars), int(a), int(b)) for a, b in pairwise(edges)
    )
    if any(f.train_end <= 0 or f.validation_end - f.validation_start < policy.minimum_validation_bars for f in folds):
        raise ValueError("Insufficient data for predeclared purged validation folds")
    return folds


def frame_digest(frame: pd.DataFrame) -> str:
    normalized = frame.rename(columns=str.lower).sort_index(axis=1).astype(float)
    digest = hashlib.sha256(pd.util.hash_pandas_object(normalized, index=True).values.tobytes())
    digest.update(json.dumps(list(normalized.columns)).encode())
    return digest.hexdigest()


@dataclass(frozen=True)
class DatasetManifest:
    symbol: str
    timeframe: str
    feed: str
    adjustment: str
    universe_version: str
    observations: int
    start: str
    end: str
    content_hash: str
    bar_layout: str

    @classmethod
    def from_frame(
        cls, frame: pd.DataFrame, *, symbol: str, timeframe: str, feed: str, adjustment: str, universe_version: str
    ):
        validate_sampling(frame, timeframe)
        if frame.index.tz is None:
            raise ValueError("Research manifests require timezone-aware timestamps")
        if not symbol or not feed or not adjustment or not universe_version:
            raise ValueError("Dataset provenance is required")
        return cls(
            symbol.upper(),
            timeframe,
            feed,
            adjustment,
            universe_version,
            len(frame),
            str(frame.index[0]),
            str(frame.index[-1]),
            frame_digest(frame),
            FIXED_BAR_LAYOUT,
        )

    def to_dict(self):
        return asdict(self)


def validate_sampling(frame: pd.DataFrame, timeframe: str) -> None:
    fixed_bar_closes(frame, timeframe)
    if frame.attrs.get("timeframe") != timeframe:
        raise ValueError("Dataset timeframe must be declared and match the strategy version")
    if len(frame) < 2:
        raise ValueError("Insufficient observations")
    duration = BAR_DURATIONS[timeframe]
    differences = frame.index.to_series().diff().dropna()
    if (differences < duration).any():
        raise ValueError("Observations are more frequent than the declared timeframe")
    if timeframe != "1d" and differences.median() >= pd.Timedelta(days=1):
        raise ValueError("Daily observations cannot validate an intraday timeframe")
