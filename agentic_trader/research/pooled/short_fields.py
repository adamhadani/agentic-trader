"""FINRA short-sale score fields for the pooled lane (``short_fields_v1``), built offline from the
cached Reg SHO daily files and the corporate-actions acquisition.

Every field is per symbol, indexed by NYSE trading day T and holds the state known at T's close plus
the file's same-day publication, so ``evaluate_prepared`` reads it at D-1 like any daily field. The
trailing statistics (``short_ratio_z``, ``short_volume_rel``) use the ``z_window`` most recent finite
prior values (strictly before T), all within the last ``2 * z_window`` sessions, else ``NaN``. A missing
row is ``NaN`` that day (never carried forward); it, and a thin day for the ratio, is skipped by later
baselines, while a sparsely reported name stays ``NaN``.
Identity breaks are the events lane's rule: every field is ``NaN`` before a symbol's latest break and
rows keyed by it earlier are discarded.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from agentic_trader.data.corporate_actions import CorporateAction
from agentic_trader.research.apriori.pead_events import by_session
from agentic_trader.research.pooled.campaign import ShortInputs, ShortVolumeInputs
from agentic_trader.research.pooled.events import identity_breaks
from agentic_trader.research.short_volume import ShortVolumeRow, ShortVolumeStore, files_sha256, parse_file


__all__ = [
    "SHORT_FIELDS",
    "LoadedShortVolume",
    "ShortCoverage",
    "ShortFrames",
    "build_short_frames",
    "load_short_volume",
    "verify_short_sources",
]

SHORT_FIELDS = ("short_ratio", "short_ratio_z", "short_volume_rel", "offex_share")


@dataclass(frozen=True)
class LoadedShortVolume:
    """Every present file's rows keyed by (mapped) symbol then date, with the digest over the present files."""

    by_symbol: Mapping[str, Mapping[date, ShortVolumeRow]]
    files_sha256: str
    sessions: tuple[date, ...]
    absent: tuple[date, ...]
    rows: int


@dataclass(frozen=True)
class ShortCoverage:
    """What the builder could not fill, for the results doc.

    ``unmatched_rows`` counts file rows for symbols outside the cohort, summed over all files.
    ``identity_breaks`` maps a symbol to the ISO date of its latest identity break.
    """

    symbols_without_rows: tuple[str, ...]
    unmatched_rows: int
    thin_days: int
    offex_clipped: int
    identity_breaks: Mapping[str, str]
    first_finite: Mapping[str, dict[str, str | None]]


@dataclass(frozen=True)
class ShortFrames:
    """Per-symbol short frames (naive-midnight DatetimeIndex over the trading days) and their coverage."""

    frames: Mapping[str, pd.DataFrame]
    coverage: ShortCoverage


def load_short_volume(directory: Path, inputs: ShortVolumeInputs, sessions: Sequence[date]) -> LoadedShortVolume:
    """Read the cached files for ``sessions`` from disk only; a session without a usable file fails closed.

    An ``absent`` session (FINRA published nothing, accepted at acquisition) is skipped and listed.
    """
    del inputs  # the window is carried by ``sessions``; the digest check is ``verify_short_sources``
    store = ShortVolumeStore(directory)
    present: list[date] = []
    absent: list[date] = []
    by_symbol: dict[str, dict[date, ShortVolumeRow]] = defaultdict(dict)
    total = 0
    for day in sessions:
        meta = store.meta(day)
        if meta is None:
            raise ValueError(f"short-volume file for {day.isoformat()} is missing from {directory}")
        if meta["status"] == "absent":
            absent.append(day)
            continue
        if meta["status"] != "present":
            raise ValueError(f"short-volume file for {day.isoformat()} is {meta['status']}")
        body = store.load(day)
        if body is None:
            raise ValueError(f"short-volume file for {day.isoformat()} is missing from {directory}")
        present.append(day)
        for row in parse_file(body, day):
            by_symbol[row.symbol][day] = row
            total += 1
    return LoadedShortVolume(
        by_symbol=by_symbol,
        files_sha256=files_sha256(store, present),
        sessions=tuple(present),
        absent=tuple(absent),
        rows=total,
    )


def verify_short_sources(inputs: ShortInputs, loaded: LoadedShortVolume, actions_sha256: str) -> None:
    """Fail closed unless both source digests equal the ones the protocol pins."""
    if loaded.files_sha256 != inputs.short_volume.files_sha256:
        raise ValueError(
            "short-volume files digest does not match the protocol's short_inputs.short_volume.files_sha256"
        )
    if actions_sha256 != inputs.corporate_actions.content_sha256:
        raise ValueError(
            "corporate-actions digest does not match the protocol's short_inputs.corporate_actions.content_sha256"
        )


def _prior_stats(values: np.ndarray, window: int) -> tuple[np.ndarray, np.ndarray]:
    """Mean and sample std (ddof 1) of the baseline before each position, ``NaN`` where there is none.

    The baseline at position t is the ``window`` most recent finite values strictly before t, provided
    all of them lie within the last ``2 * window`` positions (t-2w ... t-1); otherwise ``NaN``. A missing
    or thin day is therefore skipped, while a sparsely reported series stays ``NaN``. The std is ``NaN``
    for a constant baseline (compared on min and max, not on a rounded variance).
    """
    n = len(values)
    finite = np.flatnonzero(np.isfinite(values))
    mean = np.full(n, np.nan)
    std = np.full(n, np.nan)
    if len(finite) < window:
        return mean, std
    t = np.arange(n)
    count = np.searchsorted(finite, t, side="left")  # finite values strictly before t
    ok = count >= window
    first = np.where(ok, count - window, 0)
    oldest = finite[first]
    ok &= oldest >= t - 2 * window
    if ok.any():
        picks = finite[first[ok][:, None] + np.arange(window)]
        block = values[picks]
        mean[ok] = block.mean(axis=1)
        spread = block.std(axis=1, ddof=1)
        spread[block.max(axis=1) == block.min(axis=1)] = np.nan
        std[ok] = spread
    return mean, std


def _first_finite(frame: pd.DataFrame, days: Sequence[date]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for name in SHORT_FIELDS:
        finite = np.isfinite(frame[name].to_numpy(float))
        out[name] = days[int(np.argmax(finite))].isoformat() if finite.any() else None
    return out


def build_short_frames(
    inputs: ShortInputs,
    loaded: LoadedShortVolume,
    actions: Sequence[CorporateAction],
    raw: Mapping[str, pd.DataFrame],
    trading_days: Sequence[date],
    symbols: Sequence[str],
) -> ShortFrames:
    """One frame per symbol on the full trading-day calendar: the four ``short_fields_v1`` columns.

    ``raw`` is the unadjusted daily bars (``Volume`` column); ``offex_share`` is the file's total volume
    over the bar volume.
    """
    params = inputs.short_volume
    days = tuple(trading_days)
    n = len(days)
    index = pd.DatetimeIndex([pd.Timestamp(day) for day in days])
    breaks = identity_breaks(actions, symbols)
    cohort = set(symbols)
    unmatched = sum(len(rows) for symbol, rows in loaded.by_symbol.items() if symbol not in cohort)
    frames: dict[str, pd.DataFrame] = {}
    first_finite: dict[str, dict[str, str | None]] = {}
    without_rows: list[str] = []
    thin_days = clipped = 0
    for symbol in symbols:
        short = np.full(n, np.nan)
        total = np.full(n, np.nan)
        break_on = breaks.get(symbol)
        start = 0 if break_on is None else bisect_left(days, break_on)
        used = 0
        for k, day in enumerate(days):
            row = loaded.by_symbol.get(symbol, {}).get(day)
            if row is None or k < start:
                continue
            short[k], total[k] = row.short_volume, row.total_volume
            used += 1
        if used == 0:
            without_rows.append(symbol)
        with np.errstate(invalid="ignore", divide="ignore"):
            ratio = short / total
            thin = np.isfinite(total) & (total < params.min_total_volume)
            ratio[thin | ~np.isfinite(ratio)] = np.nan
            thin_days += int(thin.sum())
            mean, std = _prior_stats(ratio, params.z_window)
            z = (ratio - mean) / std
            z[~np.isfinite(z)] = np.nan
            short_mean, _ = _prior_stats(short, params.z_window)
            rel = short / short_mean
            rel[~np.isfinite(rel)] = np.nan
            offex = np.full(n, np.nan)
            raw_frame = raw.get(symbol)
            if raw_frame is not None and not raw_frame.empty:
                bar_volume = by_session(raw_frame)["Volume"].reindex(list(days)).to_numpy(float)
                offex = total / bar_volume
                offex[~np.isfinite(offex)] = np.nan
            over = offex > params.max_offex_share
            clipped += int(over.sum())
            offex[over] = np.nan
        built = pd.DataFrame(
            {"short_ratio": ratio, "short_ratio_z": z, "short_volume_rel": rel, "offex_share": offex}, index=index
        )
        frames[symbol] = built
        first_finite[symbol] = _first_finite(built, days)
    return ShortFrames(
        frames=frames,
        coverage=ShortCoverage(
            symbols_without_rows=tuple(without_rows),
            unmatched_rows=unmatched,
            thin_days=thin_days,
            offex_clipped=clipped,
            identity_breaks={symbol: breaks[symbol].isoformat() for symbol in symbols if symbol in breaks},
            first_finite=first_finite,
        ),
    )
