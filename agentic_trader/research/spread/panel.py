"""Aligned daily close/open panels: cached SIP acquisition, the null shift and the synthetic power world."""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path

import numpy as np
import pandas as pd

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.setups.runner import BarSource, CalendarSource, _claim_cache_range, _fetch_cached
from agentic_trader.research.spread.protocol import PowerSpec, SpreadCohort


__all__ = ["PanelBuild", "SpreadPanel", "build_spread_panel", "shift_panel", "synthetic_panel"]

_TIMEFRAME = "1d"


@dataclass(frozen=True)
class SpreadPanel:
    """Closes and opens on one session index (naive dates); NaN where a symbol has no bar."""

    sessions: tuple[date, ...]
    closes: pd.DataFrame
    opens: pd.DataFrame

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(str(c) for c in self.closes.columns)


@dataclass(frozen=True)
class PanelBuild:
    panel: SpreadPanel
    bar_failures: dict[str, str]


def _session_index(sessions: Sequence[date]) -> pd.DatetimeIndex:
    return pd.DatetimeIndex([pd.Timestamp(d) for d in sessions])


def _by_session(frame: pd.DataFrame, column: str, index: pd.DatetimeIndex) -> pd.Series:
    stamps = pd.DatetimeIndex(frame.index)
    stamps = stamps.tz_localize(UTC) if stamps.tz is None else stamps
    dates = pd.DatetimeIndex(stamps.tz_convert(ET_TZ).normalize().tz_localize(None))
    series = pd.Series(frame[column].to_numpy(dtype=float), index=dates)
    series = series[~series.index.duplicated(keep="last")]
    return series.reindex(index)


async def build_spread_panel(
    symbols: Sequence[str],
    *,
    bars: BarSource,
    calendar: CalendarSource,
    cache_dir: Path,
    start: date,
    through: date,
    adjustment: str,
    pace: Callable[[], Awaitable[None]],
) -> PanelBuild:
    """One cached daily frame per symbol, reduced to session dates; a failed name is recorded, never raised."""
    days = await calendar.get_calendar_range(start, through)
    sessions = tuple(d.date for d in days if d.is_trading_day)
    index = _session_index(sessions)
    start_dt = datetime.combine(start, time.min, UTC)
    end_dt = datetime.combine(through, time.max, UTC)
    _claim_cache_range(cache_dir, start_dt, end_dt)
    closes: dict[str, pd.Series] = {}
    opens: dict[str, pd.Series] = {}
    failures: dict[str, str] = {}
    for symbol in sorted(set(symbols)):
        try:
            frame = await _fetch_cached(symbol, _TIMEFRAME, bars, cache_dir, start_dt, end_dt, adjustment, pace)
        except Exception as exc:
            failures[symbol] = type(exc).__name__
            frame = pd.DataFrame()
        if frame.empty:
            failures.setdefault(symbol, "empty")
            closes[symbol] = pd.Series(np.nan, index=index)
            opens[symbol] = pd.Series(np.nan, index=index)
            continue
        closes[symbol] = _by_session(frame, "Close", index)
        opens[symbol] = _by_session(frame, "Open", index)
    panel = SpreadPanel(
        sessions=sessions, closes=pd.DataFrame(closes, index=index), opens=pd.DataFrame(opens, index=index)
    )
    return PanelBuild(panel=panel, bar_failures=dict(sorted(failures.items())))


def shift_panel(panel: SpreadPanel, *, seed: int, block_sessions: int) -> SpreadPanel:
    """Null world: each symbol's close-to-close log returns and overnight gaps are circularly shifted by
    its own random multiple of ``block_sessions`` and the price paths rebuilt from the first close, so
    marginal dynamics survive while every contemporaneous relation is destroyed. Missing bars stay missing."""
    rng = np.random.default_rng(seed)
    closes = panel.closes.to_numpy(dtype=float).copy()
    opens = panel.opens.to_numpy(dtype=float).copy()
    for j in range(closes.shape[1]):
        c, o = closes[:, j], opens[:, j]
        valid = np.flatnonzero(np.isfinite(c) & np.isfinite(o) & (c > 0) & (o > 0))
        m = valid.size
        if m < 2 * block_sessions:
            continue
        cv, ov = c[valid], o[valid]
        returns = np.diff(np.log(cv))
        gaps = np.log(ov[1:]) - np.log(cv[:-1])
        blocks = (m - 1) // block_sessions
        shift = int(rng.integers(1, blocks)) * block_sessions if blocks > 1 else block_sessions
        returns, gaps = np.roll(returns, shift), np.roll(gaps, shift)
        new_c = np.empty(m)
        new_o = np.empty(m)
        new_c[0], new_o[0] = cv[0], ov[0]
        for i in range(1, m):
            new_c[i] = new_c[i - 1] * math.exp(returns[i - 1])
            new_o[i] = new_c[i - 1] * math.exp(gaps[i - 1])
        c[valid], o[valid] = new_c, new_o
    index = panel.closes.index
    return SpreadPanel(
        sessions=panel.sessions,
        closes=pd.DataFrame(closes, index=index, columns=panel.closes.columns),
        opens=pd.DataFrame(opens, index=index, columns=panel.opens.columns),
    )


def _ou(rng: np.random.Generator, n: int, half_life_sessions: float, innovation_std: float) -> np.ndarray:
    phi = 0.5 ** (1.0 / half_life_sessions)
    stationary_std = innovation_std / math.sqrt(1.0 - phi * phi)
    out = np.empty(n)
    out[0] = rng.normal(0.0, stationary_std)
    noise = rng.normal(0.0, innovation_std, n)
    for t in range(1, n):
        out[t] = phi * out[t - 1] + noise[t]
    return out


def synthetic_panel(
    cohort: SpreadCohort, sessions: Sequence[date], spec: PowerSpec, *, seed: int
) -> tuple[SpreadPanel, tuple[tuple[str, str], ...]]:
    """Power world: market + sector + idiosyncratic random walks for every cohort name and the market
    symbol, with ``spec.planted_pairs`` disjoint same-sector pairs replaced by cointegrated OU spreads."""
    rng = np.random.default_rng(seed)
    n = len(sessions)
    market = np.cumsum(rng.normal(0.0, spec.market_vol, n))
    logs: dict[str, np.ndarray] = {cohort.market: math.log(400.0) + market}
    for sector in sorted(cohort.sectors):
        factor = np.cumsum(rng.normal(0.0, spec.sector_vol, n))
        for symbol in sorted(cohort.sectors[sector]):
            logs[symbol] = math.log(100.0) + market + factor + np.cumsum(rng.normal(0.0, spec.idiosyncratic_vol, n))
    candidates = list(cohort.pairs())
    rng.shuffle(candidates)
    planted: list[tuple[str, str]] = []
    used: set[str] = set()
    for y, x, _ in candidates:
        if len(planted) == spec.planted_pairs:
            break
        if y in used or x in used:
            continue
        beta = float(rng.uniform(*spec.hedge_ratio))
        logs[y] = (
            math.log(100.0) * (1.0 - beta) + beta * logs[x] + _ou(rng, n, spec.half_life_sessions, spec.innovation_std)
        )
        planted.append((y, x))
        used.update((y, x))
    index = _session_index(sessions)
    closes = pd.DataFrame({s: np.exp(v) for s, v in sorted(logs.items())}, index=index)
    overnight = rng.normal(0.0, spec.overnight_vol, closes.shape)
    opens = closes.shift(1) * np.exp(overnight)
    opens.iloc[0] = closes.iloc[0]
    return SpreadPanel(sessions=tuple(sessions), closes=closes, opens=opens), tuple(planted)
