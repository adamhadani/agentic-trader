"""Forward and residual return arrays for the IC lane (research only).

Alignment rules, all pinned by tests:

* A score for decision session ``D`` reads completed bars at ``D-1`` (the pooled ``evaluate_prepared`` read).
* ``r_h[D] = open[D+delay+h] / open[D+delay] - 1`` with ``h`` counted in sessions of the trading calendar, not
  a symbol's own bars. A missing open, or an exit session after ``window_end``, is NaN: nothing is carried to
  the next available bar and no reserve price is read.
* Every bar (and SPY bar) after ``window_end`` is dropped before anything is computed.
* The residual subtracts ``beta_60`` as known at ``D-1`` times SPY's open-to-open return over the same
  sessions, then demeans within each sector over eligible, finite cells only; a cell that is not eligible is NaN.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.alpha.data import load_dataset
from agentic_trader.research.apriori.pead_events import by_session
from agentic_trader.research.ic.protocol import WINDOW_END_LIMIT
from agentic_trader.research.ic.sectors import UNKNOWN
from agentic_trader.research.pooled.runner import CubeBuild
from agentic_trader.research.pooled.scoring import ScoreBook


__all__ = [
    "BENCHMARK",
    "BETA_EXPRESSION",
    "HORIZONS",
    "ICArrays",
    "build_ic_arrays",
    "load_spy",
    "market_frames",
    "truncate_bars",
]

BENCHMARK = "SPY"
BETA_EXPRESSION = "ts_beta(returns, mkt_ret, 60)"
HORIZONS = (1, 5, 20, 60)


@dataclass(frozen=True, eq=False)
class ICArrays:
    sessions: tuple[date, ...]
    symbols: tuple[str, ...]
    offset: int  # index of the first session in the cube's calendar
    eligible: np.ndarray  # bool [S, N]: the cube's eligible and a known sector
    groups: np.ndarray  # str [N]
    log_adv: np.ndarray  # float [S, N]
    beta_60: np.ndarray  # float [S, N], read at D-1
    returns: dict[int, np.ndarray]  # horizon -> float [S, N]
    residuals: dict[int, np.ndarray]  # horizon -> float [S, N], sector-demeaned
    spy_returns: dict[int, np.ndarray]  # horizon -> float [S]
    book: ScoreBook  # truncated bars with the mkt_ret field


def _session_dates(frame: pd.DataFrame) -> np.ndarray:
    index = pd.DatetimeIndex(frame.index)
    if index.tz is None:
        index = index.tz_localize("UTC")
    return np.asarray(index.tz_convert(ET_TZ).date)


def truncate_bars(frames: Mapping[str, pd.DataFrame], window_end: date) -> dict[str, pd.DataFrame]:
    """Bars whose New York session date is on or before ``window_end``."""
    out: dict[str, pd.DataFrame] = {}
    for symbol, frame in frames.items():
        if frame is None or frame.empty:
            out[symbol] = frame
            continue
        out[symbol] = frame[_session_dates(frame) <= window_end]
    return out


def _spy_close(spy: pd.DataFrame) -> pd.Series:
    keyed = by_session(spy)
    return pd.Series(keyed["Close"].to_numpy(float), index=pd.DatetimeIndex(keyed.index))


def market_frames(adjusted: Mapping[str, pd.DataFrame], spy: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Per symbol, ``mkt_ret``: SPY's return between the symbol's own consecutive bars.

    SPY's close is read at the symbol's bar dates, so a gap in the symbol compounds SPY over the gap.
    """
    close = _spy_close(spy)
    frames: dict[str, pd.DataFrame] = {}
    for symbol, frame in adjusted.items():
        if frame is None or frame.empty:
            continue
        keyed = by_session(frame)
        index = pd.DatetimeIndex(keyed.index)
        frames[symbol] = pd.DataFrame(
            {"mkt_ret": close.reindex(index).pct_change(fill_method=None).to_numpy(float)}, index=index
        )
    return frames


def _opens(frames: Mapping[str, pd.DataFrame], symbols: Sequence[str], trading_days: Sequence[date]) -> np.ndarray:
    """[T, N] opens on the trading calendar; a session without a bar (or a non-positive open) is NaN."""
    calendar = pd.DatetimeIndex(trading_days)
    out = np.full((len(calendar), len(symbols)), np.nan)
    for col, symbol in enumerate(symbols):
        frame = frames.get(symbol)
        if frame is None or frame.empty:
            continue
        keyed = by_session(frame)
        series = pd.Series(keyed["Open"].to_numpy(float), index=pd.DatetimeIndex(keyed.index))
        out[:, col] = series.reindex(calendar).to_numpy(float)
    out[~(out > 0)] = np.nan
    return out


def _forward(
    opens: np.ndarray,
    positions: np.ndarray,
    trading_days: Sequence[date],
    window_end: date,
    horizon: int,
    delay: int,
) -> np.ndarray:
    """``open[p+delay+h] / open[p+delay] - 1`` for each decision position ``p``; NaN past the window end."""
    total = opens.shape[0]
    entry = positions + delay
    exit_ = entry + horizon
    in_range = exit_ < total
    exit_day_ok = np.zeros(len(positions), bool)
    exit_day_ok[in_range] = np.array([trading_days[i] <= window_end for i in exit_[in_range]], dtype=bool)
    out = np.full((len(positions), opens.shape[1]), np.nan)
    rows = np.flatnonzero(exit_day_ok)
    if rows.size:
        out[rows] = opens[exit_[rows]] / opens[entry[rows]] - 1.0
    out[~np.isfinite(out)] = np.nan
    return out


def _demean_by_group(values: np.ndarray, groups: np.ndarray, eligible: np.ndarray) -> np.ndarray:
    """Subtract each session's group mean over the group's eligible, finite cells; every other cell is NaN."""
    out = np.full(values.shape, np.nan)
    for group in np.unique(groups):
        cols = np.flatnonzero(groups == group)
        block = values[:, cols]
        finite = np.isfinite(block) & eligible[:, cols]
        count = finite.sum(axis=1)
        mean = np.where(count > 0, np.where(finite, block, 0.0).sum(axis=1) / np.maximum(count, 1), np.nan)
        out[:, cols] = np.where(finite, block - mean[:, None], np.nan)
    return out


def build_ic_arrays(
    built: CubeBuild,
    spy: pd.DataFrame,
    groups: np.ndarray,
    window: tuple[date, date],
    *,
    horizons: Sequence[int] = HORIZONS,
    entry_delay: int = 0,
) -> ICArrays:
    cube = built.cube
    start, window_end = window
    if window_end > WINDOW_END_LIMIT:
        raise ValueError(f"window ends {window_end.isoformat()}, after the {WINDOW_END_LIMIT.isoformat()} limit")
    view = cube.window(start, window_end)
    symbols = tuple(cube.symbols)
    groups = np.asarray(groups, dtype=str)
    if groups.shape != (len(symbols),):
        raise ValueError("groups must hold one entry per cube symbol")
    if entry_delay < 0:
        raise ValueError("entry_delay cannot be negative")
    trading_days = tuple(built.trading_days)
    position = {day: i for i, day in enumerate(trading_days)}
    missing = [day for day in view.sessions if day not in position]
    if missing:
        raise ValueError(f"cube session {missing[0].isoformat()} is not a trading day of the build")
    positions = np.array([position[day] for day in view.sessions], dtype=int)

    bars = truncate_bars(built.adjusted, window_end)
    spy_bars = truncate_bars({BENCHMARK: spy}, window_end)[BENCHMARK]
    frames = market_frames(bars, spy_bars)
    book = ScoreBook.with_fields(bars, frames, ["mkt_ret"], trading_days, cube.sessions, symbols)
    stop = view.offset + len(view.sessions)
    beta_60 = np.array(book.panel(BETA_EXPRESSION)[view.offset : stop])

    opens = _opens(bars, symbols, trading_days)
    spy_opens = _opens({BENCHMARK: spy_bars}, (BENCHMARK,), trading_days)
    eligible = np.asarray(view.eligible, dtype=bool) & (groups != UNKNOWN)[None, :]
    returns: dict[int, np.ndarray] = {}
    residuals: dict[int, np.ndarray] = {}
    spy_returns: dict[int, np.ndarray] = {}
    for horizon in horizons:
        r = _forward(opens, positions, trading_days, window_end, horizon, entry_delay)
        spy_r = _forward(spy_opens, positions, trading_days, window_end, horizon, entry_delay)[:, 0]
        returns[horizon] = r
        spy_returns[horizon] = spy_r
        residuals[horizon] = _demean_by_group(r - beta_60 * spy_r[:, None], groups, eligible)

    dollar_volume = np.asarray(view.dollar_volume, dtype=float)
    log_adv = np.full(dollar_volume.shape, np.nan)
    positive = np.isfinite(dollar_volume) & (dollar_volume > 0)
    log_adv[positive] = np.log(dollar_volume[positive])
    return ICArrays(
        sessions=tuple(view.sessions),
        symbols=symbols,
        offset=view.offset,
        eligible=eligible,
        groups=groups,
        log_adv=log_adv,
        beta_60=beta_60,
        returns=returns,
        residuals=residuals,
        spy_returns=spy_returns,
        book=book,
    )


def load_spy(built: CubeBuild, cache_dir: Path) -> pd.DataFrame:
    """SPY's cached daily bars, only if they are exactly the file the cube was built from. Never a provider.

    The cube's ``bar_file_list`` names the ``SPY|1d|all|<file>`` entry; ``cache_dir`` is the pooled cache
    root and its ``bars/SPY_1d`` folder must hold that file first by name (as ``_verify_bar_files`` checks).
    """
    listed = [
        entry for entry in built.cube.coverage.get("bar_file_list") or () if entry.startswith(f"{BENCHMARK}|1d|all|")
    ]
    if not listed:
        raise ValueError(
            f"{BENCHMARK} is not among the bar files the cube was built from; build the cube with it as a static "
            "reference name"
        )
    if len(listed) > 1:
        raise ValueError(f"the cube lists {len(listed)} {BENCHMARK} bar files")
    name = listed[0].split("|", 3)[3]
    folder = cache_dir / "bars" / f"{BENCHMARK}_1d"
    current = min((path.name for path in folder.glob("*.npz")), default=None)
    if current != name:
        raise ValueError(f"the cached {BENCHMARK} bars are {current!r}, not the {name!r} the cube was built from")
    frame = load_dataset(folder / name)
    if frame.empty:
        raise ValueError(f"{BENCHMARK} has no cached bars")
    return frame
