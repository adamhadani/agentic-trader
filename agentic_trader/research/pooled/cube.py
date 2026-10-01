# agentic_trader/research/pooled/cube.py
"""The label cube: one long bracket outcome per eligible (session, cohort symbol).

Built once per cohort and spec, it is shared by every formula and every run: a formula
only *selects* cells. Labels use the setup study's ``label_bracket`` (conservative
same-bar tie, gap fills at the open) from ``decision_price`` at 10:35 New York, with
stop = price - 2*ATR14 (through D-1) and target = price + 3R. Labels are readable only
through ``LabelCube.window`` so a campaign can open its stage windows in order; the
cube's own coverage report holds counts, never a return statistic.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, time, timedelta
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, field_validator

from agentic_trader.research.apriori.pead_events import decision_at
from agentic_trader.research.apriori.pead_study import decision_price
from agentic_trader.research.pooled.cohort import Eligibility, UniverseSpec
from agentic_trader.research.setups.labels import BracketHit, SetupLevels, label_bracket


__all__ = [
    "HIT_CODES",
    "BracketSpec",
    "CoverageSpec",
    "CubeSpec",
    "CubeView",
    "LabelCube",
    "build_cube",
    "check_coverage",
    "load_cube",
    "save_cube",
    "tiebreak_key",
]

HIT_CODES = {BracketHit.IMMATURE: 0, BracketHit.TARGET: 1, BracketHit.STOP: 2, BracketHit.TIMEOUT: 3}
# 20 sessions span at most ~30 calendar days; the slice handed to the labeller is trimmed to this.
_LABEL_SPAN = timedelta(days=45)
_ARRAYS = ("eligible", "labelled", "r_gross", "r_cost", "holding", "hit", "tiebreak", "dollar_volume")
_PROGRESS_SESSIONS = 100


class BracketSpec(BaseModel, frozen=True, extra="forbid"):
    decision_time_et: str
    stop_atr_multiple: float = Field(gt=0)
    atr_window: int = Field(ge=2)
    target_r: float = Field(gt=0)
    max_hold_sessions: int = Field(ge=1)

    @field_validator("decision_time_et")
    @classmethod
    def _clock(cls, value: str) -> str:
        time.fromisoformat(value)
        return value

    def decision_clock(self) -> time:
        return time.fromisoformat(self.decision_time_et)


class CoverageSpec(BaseModel, frozen=True, extra="forbid"):
    max_no_reference_fraction: float = Field(ge=0, lt=1)
    max_unlabelled_fraction_per_year: float = Field(ge=0, lt=1)


class CubeSpec(BaseModel, frozen=True, extra="forbid"):
    feed: Literal["alpaca:sip"]
    bars_from: date
    decisions: tuple[date, date]
    bars_through: date
    bracket: BracketSpec
    universe: UniverseSpec
    decision_cost_bps: float = Field(ge=0)

    @property
    def identity(self) -> str:
        encoded = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()


def tiebreak_key(symbol: str, day: date) -> int:
    """Deterministic, non-alphabetical tie-break: the first 8 bytes of SHA-256(symbol|date)."""
    return int.from_bytes(hashlib.sha256(f"{symbol}|{day.isoformat()}".encode()).digest()[:8], "big")


@dataclass(frozen=True)
class CubeView:
    sessions: tuple[date, ...]
    symbols: tuple[str, ...]
    offset: int  # index of the first session in the parent cube
    eligible: np.ndarray  # bool [S, N]
    labelled: np.ndarray  # bool [S, N]: eligible and resolved
    r_gross: np.ndarray  # float [S, N]: R at 0 bps, NaN where unlabelled
    r_cost: np.ndarray  # float [S, N]: R at the decisive cost, NaN where unlabelled
    holding: np.ndarray  # int [S, N]: sessions held (max hold where unlabelled)
    hit: np.ndarray  # int8 [S, N]: HIT_CODES
    tiebreak: np.ndarray  # uint64 [S, N]
    dollar_volume: np.ndarray  # float [S, N]

    def sub(self, lo: int, hi: int) -> CubeView:
        """Sessions ``lo:hi`` of this view (relative indices)."""
        return CubeView(
            sessions=self.sessions[lo:hi],
            symbols=self.symbols,
            offset=self.offset + lo,
            **{name: getattr(self, name)[lo:hi] for name in _ARRAYS},
        )


def _read_only(array: np.ndarray) -> np.ndarray:
    view = np.asarray(array).view()
    view.flags.writeable = False
    return view


class LabelCube:
    """Holds the labels privately; ``window`` is the only way to read them."""

    def __init__(
        self,
        *,
        spec_identity: str,
        cohort_sha256: str,
        sessions: tuple[date, ...],
        symbols: tuple[str, ...],
        arrays: Mapping[str, np.ndarray],
        coverage: Mapping,
    ):
        missing = set(_ARRAYS) - set(arrays)
        if missing:
            raise ValueError(f"missing cube arrays: {sorted(missing)}")
        self._spec_identity = spec_identity
        self._cohort_sha256 = cohort_sha256
        self._sessions = tuple(sessions)
        self._symbols = tuple(symbols)
        # Read-only views: windows are slices of these, so in-place edits cannot corrupt the cube.
        self._arrays = {name: _read_only(arrays[name]) for name in _ARRAYS}
        self._coverage = dict(coverage)
        self._sha256: str | None = None

    @property
    def sessions(self) -> tuple[date, ...]:
        return self._sessions

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._symbols

    @property
    def spec_identity(self) -> str:
        return self._spec_identity

    @property
    def cohort_sha256(self) -> str:
        return self._cohort_sha256

    @property
    def coverage(self) -> dict:
        return dict(self._coverage)

    def _meta(self) -> dict:
        return {
            "spec_identity": self._spec_identity,
            "cohort_sha256": self._cohort_sha256,
            "sessions": [d.isoformat() for d in self._sessions],
            "symbols": list(self._symbols),
        }

    @property
    def sha256(self) -> str:
        if self._sha256 is None:
            digest = hashlib.sha256(json.dumps(self._meta(), sort_keys=True, separators=(",", ":")).encode())
            for name in _ARRAYS:
                array = np.ascontiguousarray(self._arrays[name])
                digest.update(name.encode())
                digest.update(str(array.dtype).encode())
                digest.update(str(array.shape).encode())
                digest.update(array.tobytes())
            self._sha256 = digest.hexdigest()
        return self._sha256

    def window(self, start: date, end: date) -> CubeView:
        lo, hi = bisect_left(self._sessions, start), bisect_right(self._sessions, end)
        if start > end or lo >= hi:
            raise ValueError(f"no cube session in {start.isoformat()}..{end.isoformat()}")
        return CubeView(
            sessions=self._sessions[lo:hi],
            symbols=self._symbols,
            offset=lo,
            **{name: self._arrays[name][lo:hi] for name in _ARRAYS},
        )

    def with_shift(self, session_idx: np.ndarray, symbol_idx: np.ndarray, delta: float) -> LabelCube:
        """A copy whose labelled cells at the given absolute indices gain ``delta`` R (power checks)."""
        arrays = {name: array.copy() for name, array in self._arrays.items()}
        rows, cols = np.asarray(session_idx, dtype=int), np.asarray(symbol_idx, dtype=int)
        keep = arrays["labelled"][rows, cols]
        for name in ("r_gross", "r_cost"):
            arrays[name][rows[keep], cols[keep]] += delta
        return LabelCube(
            spec_identity=self._spec_identity,
            cohort_sha256=self._cohort_sha256,
            sessions=self._sessions,
            symbols=self._symbols,
            arrays=arrays,
            coverage=self._coverage,
        )


class _Hourly:
    def __init__(self, frame: pd.DataFrame):
        index = pd.DatetimeIndex(frame.index)
        index = index if index.tz is not None else index.tz_localize("UTC")
        ordered = frame.set_axis(index.tz_convert("UTC")).sort_index()
        self._frame = ordered[~ordered.index.duplicated(keep="first")]
        self._index = self._frame.index

    def around(self, when) -> pd.DataFrame:
        lo = self._index.searchsorted(when - timedelta(days=1), side="left")
        hi = self._index.searchsorted(when + _LABEL_SPAN, side="right")
        return self._frame.iloc[lo:hi]


def build_cube(
    eligibility: Eligibility,
    hourly: Mapping[str, pd.DataFrame],
    spec: CubeSpec,
    *,
    cohort_sha256: str,
    provenance: Mapping[str, object] | None = None,
    progress: Callable[[str], None] | None = None,
) -> LabelCube:
    """Label every eligible cell.

    ``provenance`` (what the build read: bar failures, the static reference set, bar-file
    digests) is stored in the coverage report, which is outside the cube hash: the same
    labels are the same cube whatever is recorded about how they were acquired.
    ``progress`` is told the session count about every ``_PROGRESS_SESSIONS`` sessions.
    """
    shape = eligibility.eligible.shape
    labelled = np.zeros(shape, dtype=bool)
    r_gross = np.full(shape, np.nan)
    r_cost = np.full(shape, np.nan)
    holding = np.full(shape, spec.bracket.max_hold_sessions, dtype=np.int16)
    hit = np.zeros(shape, dtype=np.int8)
    tiebreak = np.zeros(shape, dtype=np.uint64)
    unlabelled: dict[str, Counter[str]] = defaultdict(Counter)
    eligible_by_year: Counter[str] = Counter()
    labelled_by_year: Counter[str] = Counter()
    prepared = {s: _Hourly(f) for s, f in hourly.items() if f is not None and not f.empty}
    clock = spec.bracket.decision_clock()
    for row, day in enumerate(eligibility.sessions):
        if progress is not None and row and row % _PROGRESS_SESSIONS == 0:
            progress(f"cube build {row}/{len(eligibility.sessions)} sessions")
        year = str(day.year)
        when = decision_at(day, clock)
        for col, symbol in enumerate(eligibility.symbols):
            tiebreak[row, col] = tiebreak_key(symbol, day)
            if not eligibility.eligible[row, col]:
                continue
            eligible_by_year[year] += 1
            bars = prepared.get(symbol)
            if bars is None:
                unlabelled["no_hourly_bars"][year] += 1
                continue
            window = bars.around(when)
            price = decision_price(window, when)
            if price is None:
                unlabelled["no_decision_price"][year] += 1
                continue
            risk = spec.bracket.stop_atr_multiple * float(eligibility.atr[row, col])
            if not (math.isfinite(risk) and risk > 0 and price - risk > 0):
                unlabelled["degenerate_levels"][year] += 1
                continue
            levels = SetupLevels("LONG", price, price - risk, price + spec.bracket.target_r * risk)
            outcome = label_bracket(
                levels,
                when,
                window,
                max_hold_sessions=spec.bracket.max_hold_sessions,
                cost_bps_per_side=spec.decision_cost_bps,
            )
            if outcome.hit == BracketHit.IMMATURE:
                unlabelled["immature"][year] += 1
                continue
            labelled[row, col] = True
            labelled_by_year[year] += 1
            r_gross[row, col] = outcome.r
            r_cost[row, col] = outcome.r_cost
            holding[row, col] = outcome.holding_sessions
            hit[row, col] = HIT_CODES[outcome.hit]
    coverage = {
        **(provenance or {}),
        "sessions": len(eligibility.sessions),
        "skipped_sessions": dict(eligibility.skipped_sessions),
        "eligibility_reasons": dict(eligibility.reasons),
        "eligible_by_year": dict(eligible_by_year),
        "labelled_by_year": dict(labelled_by_year),
        "unlabelled_by_year": {reason: dict(years) for reason, years in unlabelled.items()},
        "reference_names": {
            "min": min(eligibility.reference_names, default=0),
            "max": max(eligibility.reference_names, default=0),
        },
    }
    return LabelCube(
        spec_identity=spec.identity,
        cohort_sha256=cohort_sha256,
        sessions=eligibility.sessions,
        symbols=eligibility.symbols,
        arrays={
            "eligible": eligibility.eligible.copy(),
            "labelled": labelled,
            "r_gross": r_gross,
            "r_cost": r_cost,
            "holding": holding,
            "hit": hit,
            "tiebreak": tiebreak,
            "dollar_volume": eligibility.dollar_volume.copy(),
        },
        coverage=coverage,
    )


def check_coverage(cube: LabelCube, coverage: CoverageSpec) -> None:
    """Fail closed on a gappy sample (the study never tests on it)."""
    report = cube.coverage
    sessions = max(report["sessions"], 1)
    no_reference = report["skipped_sessions"].get("no_reference", 0)
    if no_reference / sessions > coverage.max_no_reference_fraction:
        raise ValueError(
            f"static reference unavailable on {no_reference}/{sessions} sessions, above the frozen "
            f"{coverage.max_no_reference_fraction:.0%} limit"
        )
    gaps: Counter[str] = Counter()
    for reason in ("no_hourly_bars", "no_decision_price"):
        gaps.update(report["unlabelled_by_year"].get(reason, {}))
    for year, eligible in report["eligible_by_year"].items():
        if eligible and gaps[year] / eligible > coverage.max_unlabelled_fraction_per_year:
            raise ValueError(
                f"{gaps[year]}/{eligible} eligible cells in {year} have no hourly decision data, above the frozen "
                f"{coverage.max_unlabelled_fraction_per_year:.0%} limit"
            )
    # An empty sample is a gap too (no threshold: nothing at all can be tested on it).
    eligible_cells, labelled_cells = (int(cube._arrays[name].sum()) for name in ("eligible", "labelled"))
    if eligible_cells == 0:
        raise ValueError("the cube has no eligible cell; nothing can be tested on it")
    if labelled_cells == 0:
        raise ValueError(f"none of the {eligible_cells} eligible cells is labelled; nothing can be tested on the cube")


def save_cube(cube: LabelCube, path: Path) -> None:
    meta = {**cube._meta(), "coverage": cube.coverage, "sha256": cube.sha256}
    tmp = path.with_suffix(".tmp.npz")
    payload: dict[str, np.ndarray] = {"meta": np.array(json.dumps(meta, sort_keys=True)), **cube._arrays}
    np.savez_compressed(tmp, **payload)  # type: ignore[arg-type]
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def load_cube(path: Path) -> LabelCube:
    with np.load(path, allow_pickle=False) as data:
        meta = json.loads(str(data["meta"]))
        arrays = {name: data[name] for name in _ARRAYS}
    cube = LabelCube(
        spec_identity=meta["spec_identity"],
        cohort_sha256=meta["cohort_sha256"],
        sessions=tuple(date.fromisoformat(d) for d in meta["sessions"]),
        symbols=tuple(meta["symbols"]),
        arrays=arrays,
        coverage=meta["coverage"],
    )
    if cube.sha256 != meta["sha256"]:
        raise ValueError(f"cube hash mismatch for {path}: stored {meta['sha256']}, computed {cube.sha256}")
    return cube
