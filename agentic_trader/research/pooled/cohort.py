"""The pooled lane's cohort: a frozen symbol list under ``config/research/pooled/``.

The cohort is today's membership, not a point-in-time universe: the configured
scan-universe equity groups plus a prospective equity snapshot. Its identity is the
SHA-256 of the file's bytes, recorded in every manifest; a changed file is a new version.
Point-in-time eligibility per session is computed separately from bars
(``point_in_time_eligibility``).
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, field_validator, model_validator

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.apriori.pead_events import _atr, _by_session
from agentic_trader.screeners.dynamic_universe import median_dollar_volume, static_reference


__all__ = [
    "SUPPORTED_SYMBOL",
    "Cohort",
    "CohortSource",
    "Eligibility",
    "LoadedCohort",
    "UniverseSpec",
    "load_cohort",
    "point_in_time_eligibility",
]

SUPPORTED_SYMBOL = re.compile(r"^[A-Z]{1,5}$")
_PROGRESS_SESSIONS = 100


class CohortSource(BaseModel, frozen=True, extra="forbid"):
    kind: Literal["config_groups", "equity_snapshot"]
    description: str
    # config groups: SHA-256 of the newline-joined sorted symbols; snapshot: its snapshot_id.
    identity: str = Field(min_length=1)
    symbols: tuple[str, ...]


class Cohort(BaseModel, frozen=True, extra="forbid"):
    id: Literal["pooled-cohort"]
    version: int = Field(ge=1)
    survivorship: str
    sources: tuple[CohortSource, ...] = Field(min_length=1)
    excluded: dict[str, str]  # symbol -> reason (e.g. unsupported symbol form)
    symbols: tuple[str, ...] = Field(min_length=1)

    @field_validator("symbols")
    @classmethod
    def _sorted_unique_supported(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if list(value) != sorted(set(value)):
            raise ValueError("symbols must be sorted and unique")
        unsupported = [s for s in value if not SUPPORTED_SYMBOL.fullmatch(s)]
        if unsupported:
            raise ValueError(f"unsupported symbols: {unsupported[:5]}")
        return value

    @model_validator(mode="after")
    def _union_of_sources(self) -> Cohort:
        union = sorted({s for source in self.sources for s in source.symbols if SUPPORTED_SYMBOL.fullmatch(s)})
        if list(self.symbols) != union:
            raise ValueError("symbols must be the sorted union of the sources' supported symbols")
        return self

    def source_of(self, symbol: str) -> tuple[str, ...]:
        return tuple(source.kind for source in self.sources if symbol in source.symbols)


@dataclass(frozen=True)
class LoadedCohort:
    cohort: Cohort
    sha256: str
    path: Path


def load_cohort(path: Path) -> LoadedCohort:
    raw = path.read_bytes()
    return LoadedCohort(cohort=Cohort.model_validate_json(raw), sha256=hashlib.sha256(raw).hexdigest(), path=path)


class UniverseSpec(BaseModel, frozen=True, extra="forbid"):
    min_price: float = Field(gt=0)
    static_percentile: float = Field(gt=0, lt=1)
    min_eligible_names: int = Field(ge=1)


class _DailySlicer:
    """Fast point-in-time access to one symbol's raw daily bars by New York session date.

    ``median_dollar_volume`` keeps the finite rows through ``as_of`` and takes the last 20.
    Handing it only the last ``_TAIL`` finite rows through ``as_of`` returns the identical
    value without scanning the whole history on every call (the live function is reused,
    not reimplemented).
    """

    _TAIL = 40

    def __init__(self, frame: pd.DataFrame):
        keep = np.isfinite(frame["Close"].to_numpy(float)) & np.isfinite(frame["Volume"].to_numpy(float))
        finite = frame[keep]
        index = pd.DatetimeIndex(finite.index)
        index = index if index.tz is not None else index.tz_localize("UTC")
        self._frame = finite
        self._days = index.tz_convert(ET_TZ).tz_localize(None).normalize().values.astype("datetime64[D]")
        self._closes = finite["Close"].to_numpy(float)

    def upto(self, as_of: date) -> pd.DataFrame:
        pos = int(np.searchsorted(self._days, np.datetime64(as_of), side="right"))
        return self._frame.iloc[max(0, pos - self._TAIL) : pos]

    def median_dollar_volume(self, as_of: date) -> float | None:
        return median_dollar_volume(self.upto(as_of), as_of)

    def close_on(self, as_of: date) -> float | None:
        pos = int(np.searchsorted(self._days, np.datetime64(as_of), side="left"))
        if pos < len(self._days) and self._days[pos] == np.datetime64(as_of):
            return float(self._closes[pos])
        return None


@dataclass(frozen=True)
class Eligibility:
    sessions: tuple[date, ...]  # decision sessions in the window
    symbols: tuple[str, ...]
    eligible: np.ndarray  # bool [S, N]
    atr: np.ndarray  # float [S, N], ATR through D-1 on adjusted bars; NaN where ineligible
    dollar_volume: np.ndarray  # float [S, N], raw 20-session median at D-1; NaN where ineligible
    reference: tuple[float | None, ...]  # static reference per session
    reference_names: tuple[int, ...]
    skipped_sessions: dict[str, int]
    reasons: dict[str, int]  # per-cell ineligibility reasons


def point_in_time_eligibility(
    trading_days: Sequence[date],
    decisions: tuple[date, date],
    symbols: Sequence[str],
    adjusted: Mapping[str, pd.DataFrame],
    raw: Mapping[str, pd.DataFrame],
    static_raw: Mapping[str, pd.DataFrame],
    *,
    universe: UniverseSpec,
    atr_window: int,
    progress: Callable[[str], None] | None = None,
) -> Eligibility:
    """Eligibility at each decision session D exactly as the live dynamic-universe gate sees it.

    ``as_of`` is D-1, the last completed session. The $10 floor and the dollar-volume screen
    read **raw** bars (adjusted history depends on later corporate actions); ATR reads the
    adjusted bars. Eligibility never depends on a formula, so every formula shares one control.
    ``progress`` is told the session count about every ``_PROGRESS_SESSIONS`` sessions.
    """
    days = tuple(trading_days)
    start, end = decisions
    rows = [i for i, day in enumerate(days) if start <= day <= end and i >= 1]
    sessions = tuple(days[i] for i in rows)
    names = tuple(symbols)
    shape = (len(sessions), len(names))
    eligible = np.zeros(shape, dtype=bool)
    atr = np.full(shape, np.nan)
    dollar_volume = np.full(shape, np.nan)
    reference: list[float | None] = []
    reference_names: list[int] = []
    skipped: Counter[str] = Counter()
    reasons: Counter[str] = Counter()

    raw_slicers = {s: _DailySlicer(f) for s, f in raw.items() if f is not None and not f.empty}
    static_slicers = {s: _DailySlicer(f) for s, f in static_raw.items() if f is not None and not f.empty}
    keyed = {s: _by_session(f) for s, f in adjusted.items() if f is not None and not f.empty}

    for row, i in enumerate(rows):
        if progress is not None and row and row % _PROGRESS_SESSIONS == 0:
            progress(f"eligibility {row}/{len(rows)} sessions")
        as_of = days[i - 1]
        ref = static_reference(
            {s: sl.upto(as_of) for s, sl in static_slicers.items()}, universe.static_percentile, as_of=as_of
        )
        reference.append(ref.value)
        reference_names.append(ref.names)
        if ref.value is None:
            skipped["no_reference"] += 1
            continue
        for col, symbol in enumerate(names):
            slicer, adj = raw_slicers.get(symbol), keyed.get(symbol)
            if slicer is None or adj is None:
                reasons["no_daily_bars"] += 1
                continue
            volume = slicer.median_dollar_volume(as_of)
            if volume is None:
                reasons["short_history"] += 1
                continue
            close = slicer.close_on(as_of)
            if close is None:
                reasons["no_raw_close"] += 1
                continue
            if close < universe.min_price:
                reasons["illiquid_price"] += 1
                continue
            if volume < ref.value:
                reasons["illiquid_volume"] += 1
                continue
            value = _atr(adj, days, i - 1, atr_window)
            if value is None:
                reasons["no_atr"] += 1
                continue
            eligible[row, col] = True
            atr[row, col] = value
            dollar_volume[row, col] = volume
        if eligible[row].sum() < universe.min_eligible_names:
            skipped["too_few_eligible"] += 1
            eligible[row] = False
            atr[row] = np.nan
            dollar_volume[row] = np.nan
    return Eligibility(
        sessions=sessions,
        symbols=names,
        eligible=eligible,
        atr=atr,
        dollar_volume=dollar_volume,
        reference=tuple(reference),
        reference_names=tuple(reference_names),
        skipped_sessions=dict(skipped),
        reasons=dict(reasons),
    )
