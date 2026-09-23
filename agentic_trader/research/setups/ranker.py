"""Frozen setup-ranker artifact loading and the live shadow feature block.

``study.py`` writes ``ranker.json`` (plus ``ranker.pkl`` for an HGB winner) and records
the sha256 of ``ranker.json`` in ``selection.json``. The live suggestion scan scores
each ranked candidate with that artifact *in shadow only*: the score is evidence and
never changes ranking, the card budget or any card.

Integrity is checked before anything is trusted: ``features_version`` must equal
``FEATURES_VERSION``, ``ranker.json`` must match ``selection.json`` when present, and
the HGB pickle's sha256 must match ``ranker.json`` *before* it is unpickled. Any
mismatch or unreadable artifact logs a warning and yields ``None`` -- the scan then
records features only. A ``setup_features_v1`` artifact is always rejected: v1's
252-session lookbacks never fit the live one-year daily fetch. ``cached_ranker`` keeps
the verified result per artifact file state, so a scan neither re-reads nor re-warns.

``live_cross_section`` is the live half of the study/live feature contract: the study
(``runner.py``) slices its cached history to the same one-year window and builds the
same cross-section for a decision instant.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import pickle
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from agentic_trader.research.setups.features import FEATURES_VERSION, CrossSection, cross_section, setup_vector


__all__ = [
    "LoadedRanker",
    "cached_ranker",
    "finite_or_none",
    "last_completed_session",
    "live_cross_section",
    "load_ranker",
    "setup_features",
    "shadow_blocks",
]

logger = logging.getLogger(__name__)

_LINEAR_SCORERS = frozenset({"logistic", "ridge"})
_ONE_HOT_PREFIXES = ("strategy=", "timeframe=")


@dataclass(frozen=True)
class LoadedRanker:
    """A verified ranker artifact; ``score`` reproduces ``study.Fitted.predict`` for one row."""

    scorer: str
    features: tuple[str, ...]
    sha256: str
    coefficients: np.ndarray | None = None
    intercept: float = 0.0
    imputer_medians: np.ndarray | None = None
    scaler_mean: np.ndarray | None = None
    scaler_scale: np.ndarray | None = None
    model: Any = None

    def _row(self, vector: Mapping[str, float]) -> list[float]:
        # study._reindex_features: an absent/NaN one-hot means "not this setup" (0.0);
        # every other absent/NaN feature stays NaN for the imputer / HGB to handle.
        values = []
        for feature in self.features:
            raw = vector.get(feature)
            value = float("nan") if raw is None else float(raw)
            if feature.startswith(_ONE_HOT_PREFIXES) and math.isnan(value):
                value = 0.0
            values.append(value)
        return values

    def score_many(self, vectors: Sequence[Mapping[str, float]]) -> np.ndarray:
        """One score per vector, computed as one batch (one HGB ``predict`` call)."""
        if not vectors:
            return np.empty(0, dtype=float)
        if self.scorer == "setup_quality":
            return np.asarray([float(vector["setup_quality"]) for vector in vectors], dtype=float)
        rows = np.asarray([self._row(vector) for vector in vectors], dtype=float)
        if self.scorer == "hgb":
            frame = pd.DataFrame(rows, columns=list(self.features))
            return np.asarray(self.model.predict(frame), dtype=float)
        assert self.coefficients is not None and self.imputer_medians is not None
        assert self.scaler_mean is not None and self.scaler_scale is not None
        # The study's imputers use keep_empty_features=True, so every median is finite
        # (0.0 for an all-missing training column) and nothing is dropped. This branch
        # is defensive: it mirrors SimpleImputer's default of dropping NaN-median features.
        kept = ~np.isnan(self.imputer_medians)
        imputed = np.where(np.isnan(rows), self.imputer_medians, rows)[:, kept]
        scaled = (imputed - self.scaler_mean) / self.scaler_scale
        linear = scaled @ self.coefficients + self.intercept
        if self.scorer == "logistic":
            return 1.0 / (1.0 + np.exp(-linear))
        return linear

    def score(self, vector: Mapping[str, float]) -> float:
        return float(self.score_many([vector])[0])


def _reject(path: Path, reason: str) -> None:
    logger.warning(
        "Shadow ranker artifact %s rejected (%s); recording features only",
        path,
        reason,
        extra={"event": "shadow_ranker_artifact_rejected", "path": str(path), "reason": reason},
    )


def load_ranker(path: str | Path) -> LoadedRanker | None:
    """Load and verify a ``ranker.json`` (or its directory); ``None`` on any mismatch."""
    path = Path(path)
    if path.is_dir():
        path = path / "ranker.json"
    try:
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        doc = json.loads(raw)
        if doc.get("features_version") != FEATURES_VERSION:
            _reject(path, f"features_version {doc.get('features_version')!r} != {FEATURES_VERSION!r}")
            return None
        selection = path.parent / "selection.json"
        if selection.exists():
            expected = json.loads(selection.read_text()).get("ranker_sha256")
            if expected != sha:
                _reject(path, "ranker.json sha256 does not match selection.json")
                return None
        scorer = doc["scorer"]
        features = tuple(str(feature) for feature in doc["features"])

        if scorer == "setup_quality":
            return LoadedRanker(scorer, features, sha)

        if scorer == "hgb":
            pickle_name = str(doc["pickle_path"])
            if Path(pickle_name).name != pickle_name:
                _reject(path, f"pickle_path {pickle_name!r} is not a sibling file")
                return None
            blob = (path.parent / pickle_name).read_bytes()
            if hashlib.sha256(blob).hexdigest() != doc["pickle_sha256"]:
                _reject(path, "ranker.pkl sha256 does not match ranker.json")
                return None
            return LoadedRanker(scorer, features, sha, model=pickle.loads(blob))  # sha256-verified above

        if scorer in _LINEAR_SCORERS:
            medians = np.asarray(doc["imputer_medians"], dtype=float)
            coefficients = np.asarray(doc["coefficients"], dtype=float)
            mean = np.asarray(doc["scaler_mean"], dtype=float)
            scale = np.asarray(doc["scaler_scale"], dtype=float)
            kept = int((~np.isnan(medians)).sum())
            if len(medians) != len(features) or not (len(coefficients) == len(mean) == len(scale) == kept):
                _reject(path, "linear payload dimensions do not match its feature list")
                return None
            return LoadedRanker(
                scorer,
                features,
                sha,
                coefficients=coefficients,
                intercept=float(doc["intercept"]),
                imputer_medians=medians,
                scaler_mean=mean,
                scaler_scale=scale,
            )

        _reject(path, f"unknown scorer {scorer!r}")
        return None
    except Exception as exc:  # an unreadable artifact degrades to features-only, never fails a scan
        _reject(path, f"{type(exc).__name__}: {exc}")
        return None


_ARTIFACT_FILES = ("ranker.json", "selection.json", "ranker.pkl")
_CACHE_LOCK = threading.Lock()
_CACHE: dict[Path, tuple[tuple[tuple[int, int] | None, ...], LoadedRanker | None]] = {}


def _artifact_state(path: Path) -> tuple[tuple[int, int] | None, ...]:
    """(mtime_ns, size) of the artifact and its sibling files; ``None`` for an absent one."""
    state: list[tuple[int, int] | None] = []
    for candidate in (path, *(path.parent / name for name in _ARTIFACT_FILES if name != path.name)):
        try:
            stat = candidate.stat()
        except OSError:
            state.append(None)
        else:
            state.append((stat.st_mtime_ns, stat.st_size))
    return tuple(state)


def cached_ranker(path: str | Path) -> LoadedRanker | None:
    """``load_ranker``, memoized by the artifact files' (path, mtime, size).

    Each scan otherwise re-read, re-hashed and re-unpickled the artifact, and re-logged the
    same rejection. A changed ``ranker.json``, ``selection.json`` or ``ranker.pkl`` reloads.
    """
    path = Path(path)
    if path.is_dir():
        path = path / "ranker.json"
    path = path.resolve()
    state = _artifact_state(path)
    with _CACHE_LOCK:
        cached = _CACHE.get(path)
        if cached is not None and cached[0] == state:
            return cached[1]
        ranker = load_ranker(path)
        _CACHE[path] = (state, ranker)
        return ranker


def last_completed_session(daily: Mapping[str, pd.DataFrame], before: date) -> date | None:
    """The latest daily session date strictly before ``before`` across the fetched frames.

    Daily bars are stamped at their session's New York midnight (04:00Z/05:00Z in UTC),
    so the index's own ``.date()`` is the session date for a UTC or New York index alike.
    """
    latest: date | None = None
    for frame in daily.values():
        dates = [d for d in pd.DatetimeIndex(frame.index).date if d < before]
        if dates:
            candidate = max(dates)
            latest = candidate if latest is None or candidate > latest else latest
    return latest


def live_cross_section(
    daily: Mapping[str, pd.DataFrame], universe_sectors: Mapping[str, str], session_date: date
) -> CrossSection:
    """The live scan's cross-section from its ``fetch_data(daily_period="1y")`` frames.

    The population is every ``universe.groups`` symbol (the keys of ``universe_sectors``)
    with daily bars in this scan -- not explicit ``contracts:`` equities outside the
    universe -- as of the last session completed before ``session_date``, so today's
    in-progress bar is excluded. The study reproduces this per decision date.
    """
    population = {
        symbol: frame
        for symbol, frame in daily.items()
        if symbol in universe_sectors and frame is not None and not frame.empty
    }
    as_of = last_completed_session(population, session_date)
    if as_of is None:
        raise ValueError(f"No completed daily session before {session_date} in this scan's data")
    return cross_section(population, universe_sectors, as_of)


def finite_or_none(value: Any) -> float | None:
    """A finite float, or ``None`` for missing/NaN/inf (JSON here is encoded with allow_nan=False)."""
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def setup_features(
    cs: CrossSection,
    *,
    symbol: str,
    direction: str,
    strategy: str,
    timeframe: str,
    setup_quality: float,
    entry: float,
    stop: float,
    target: float,
    atr_14: float,
) -> dict[str, float]:
    """``setup_vector`` with the study runner's exact setup geometry.

    ``stop_atr = |entry - stop| / atr`` and ``reward_risk = |target - entry| / |entry - stop|``.
    """
    risk_unit = abs(entry - stop)
    stop_atr = risk_unit / atr_14 if atr_14 else float("nan")
    reward_risk = abs(target - entry) / risk_unit if risk_unit else float("nan")
    return setup_vector(
        cs,
        symbol=symbol,
        direction=direction,
        strategy=strategy,
        timeframe=timeframe,
        setup_quality=setup_quality,
        stop_atr=stop_atr,
        reward_risk=reward_risk,
    )


def shadow_blocks(vectors: Sequence[Mapping[str, float]], ranker: LoadedRanker | None) -> list[dict[str, Any]]:
    """One ``{features_version, features, score, ranker_sha}`` per vector; NaN recorded as ``None``.

    Scores are ``None`` unless a verified ranker is given. Journals and signal provenance
    are JSON-encoded with ``allow_nan=False``, so every non-finite value becomes ``None``.
    """
    scores = ranker.score_many(vectors) if ranker is not None else [None] * len(vectors)
    return [
        {
            "features_version": FEATURES_VERSION,
            "features": {name: finite_or_none(value) for name, value in vector.items()},
            "score": finite_or_none(score),
            "ranker_sha": ranker.sha256 if ranker is not None else None,
        }
        for vector, score in zip(vectors, scores, strict=True)
    ]
