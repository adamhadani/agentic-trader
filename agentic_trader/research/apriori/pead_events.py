"""PEAD event construction from daily bars: reaction, liquidity, legs and the control set.

For a report dated on session D (observed NYSE sessions only):

- reaction ``z = (r_i - r_SPY) / (sigma_i * sqrt(2))`` with ``r`` from close(D-1) to
  close(D+1) and ``sigma_i`` the sample standard deviation of the ``vol_window`` daily log
  returns ending D-1 -- never the report day itself;
- liquidity exactly as the live dynamic-universe gate computes it at the D+2 decision:
  ``median_dollar_volume(daily, as_of=D+1)`` against
  ``static_reference(static equities, percentile, as_of=D+1)`` and the ``as_of`` close
  against ``min_price`` (the live functions are reused, not reimplemented);
- ``ATR`` = the simple mean of the last ``atr_window`` true ranges through D+1.

Every liquid event with the needed bars is a control event; it is additionally a leg
event when its surprise and reaction agree. Anything else is counted by reason and year,
never silently dropped.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

import numpy as np
import pandas as pd

from agentic_trader.agent.earnings import CalendarRow
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.apriori.catalog import PeadEntry, PeadEvent
from agentic_trader.screeners.dynamic_universe import median_dollar_volume, static_reference


__all__ = ["EVENT_COLUMNS", "SUPPORTED_SYMBOL", "MarketData", "build_events", "decision_at", "surprise_pct"]

SUPPORTED_SYMBOL = re.compile(r"^[A-Z]{1,5}$")

EVENT_COLUMNS: tuple[str, ...] = (
    "symbol",
    "report_date",
    "session",
    "decision_at",
    "z",
    "surprise_pct",
    "surprise_pct_reported",
    "atr",
    "median_dollar_volume",
    "reference",
    "leg",
    "surprise_long",
    "surprise_short",
    "reaction_long",
    "reaction_short",
)


@dataclass(frozen=True)
class MarketData:
    trading_days: tuple[date, ...]  # sorted observed sessions
    daily: Mapping[str, pd.DataFrame]  # by symbol; tz-aware or UTC-naive index, OHLCV
    static_symbols: tuple[str, ...]  # static-universe equities for the liquidity reference


def surprise_pct(row: CalendarRow, event: PeadEvent) -> float | None:
    if row.eps is None or row.eps_forecast is None or row.n_estimates is None:
        return None
    if row.n_estimates < event.min_estimates or abs(row.eps_forecast) < event.min_abs_forecast:
        return None
    return 100.0 * (row.eps - row.eps_forecast) / abs(row.eps_forecast)


def decision_at(day: date, clock: time) -> datetime:
    return datetime.combine(day, clock, tzinfo=ET_TZ).astimezone(UTC)


def _by_session(frame: pd.DataFrame) -> pd.DataFrame:
    index = pd.DatetimeIndex(frame.index)
    if index.tz is None:
        index = index.tz_localize("UTC")
    keyed = frame.set_axis(index.tz_convert(ET_TZ).date)
    return keyed[~keyed.index.duplicated(keep="last")]


def _reaction_z(sym: pd.DataFrame, bench: pd.DataFrame, days: Sequence[date], i: int, vol_window: int) -> float | None:
    history = days[i - 1 - vol_window : i]  # vol_window + 1 closes ending D-1
    needed = [*history, days[i + 1]]
    if any(day not in sym.index for day in needed) or any(day not in bench.index for day in (days[i - 1], days[i + 1])):
        return None
    closes = sym.loc[list(history), "Close"].to_numpy(float)
    if np.any(~np.isfinite(closes)) or np.any(closes <= 0):
        return None
    sigma = float(np.std(np.diff(np.log(closes)), ddof=1))
    if not math.isfinite(sigma) or sigma <= 0:
        return None
    sym_prev, sym_next = float(sym.at[days[i - 1], "Close"]), float(sym.at[days[i + 1], "Close"])
    bench_prev, bench_next = float(bench.at[days[i - 1], "Close"]), float(bench.at[days[i + 1], "Close"])
    if not all(math.isfinite(v) and v > 0 for v in (sym_prev, sym_next, bench_prev, bench_next)):
        return None
    r_i = sym_next / sym_prev - 1.0
    r_m = bench_next / bench_prev - 1.0
    return (r_i - r_m) / (sigma * math.sqrt(2.0))


def _atr(sym: pd.DataFrame, days: Sequence[date], j: int, window: int) -> float | None:
    if j - window < 0:
        return None
    ranges = []
    for k in range(j - window + 1, j + 1):
        day, prev = days[k], days[k - 1]
        if day not in sym.index or prev not in sym.index:
            return None
        high, low = float(sym.at[day, "High"]), float(sym.at[day, "Low"])
        prev_close = float(sym.at[prev, "Close"])
        ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    value = float(np.mean(ranges))
    return value if math.isfinite(value) and value > 0 else None


def build_events(rows: Sequence[CalendarRow], market: MarketData, entry: PeadEntry) -> tuple[pd.DataFrame, dict]:
    days = market.trading_days
    position = {day: i for i, day in enumerate(days)}
    start, end = entry.window.decisions
    vol_window, atr_window = entry.event.vol_window, entry.trade.atr_window
    clock = entry.trade.decision_time()
    keyed: dict[str, pd.DataFrame] = {}
    references: dict[date, float | None] = {}
    static_daily = {s: market.daily[s] for s in market.static_symbols if s in market.daily}
    benchmark = entry.event.benchmark
    bench = _by_session(market.daily[benchmark])

    reasons: Counter[str] = Counter()
    by_year: dict[str, Counter[int]] = defaultdict(Counter)
    events: list[dict] = []

    def skip(reason: str, day: date) -> None:
        reasons[reason] += 1
        by_year[reason][day.year] += 1

    for row in rows:
        if not SUPPORTED_SYMBOL.fullmatch(row.symbol):
            skip("unsupported_symbol", row.date)
            continue
        raw = market.daily.get(row.symbol)
        if raw is None or raw.empty:
            skip("no_daily_bars", row.date)
            continue
        i = position.get(row.date)
        if i is None:
            skip("non_session_date", row.date)
            continue
        if i + 2 >= len(days) or i - 1 - vol_window < 0 or i + 1 - atr_window < 1:
            skip("outside_calendar", row.date)
            continue
        session, as_of = days[i + 2], days[i + 1]
        if not start <= session <= end:
            skip("outside_window", row.date)
            continue
        if as_of not in references:
            references[as_of] = static_reference(static_daily, entry.universe.static_percentile, as_of=as_of).value
        reference = references[as_of]
        if reference is None:
            skip("no_reference", row.date)
            continue
        dollar_volume = median_dollar_volume(raw, as_of)
        if dollar_volume is None:
            skip("short_history", row.date)
            continue
        sym = keyed.setdefault(row.symbol, _by_session(raw))
        if as_of not in sym.index:
            skip("no_reaction_bars", row.date)
            continue
        as_of_close = float(sym.at[as_of, "Close"])
        if not math.isfinite(as_of_close):
            skip("no_reaction_bars", row.date)
            continue
        if as_of_close < entry.universe.min_price:
            skip("illiquid_price", row.date)
            continue
        if dollar_volume < reference:
            skip("illiquid_volume", row.date)
            continue
        z = _reaction_z(sym, bench, days, i, vol_window)
        if z is None:
            skip("no_reaction_bars", row.date)
            continue
        atr = _atr(sym, days, i + 1, atr_window)
        if atr is None:
            skip("no_atr", row.date)
            continue
        surprise = surprise_pct(row, entry.event)
        threshold, sigma = entry.event.surprise_pct, entry.event.reaction_sigma
        surprise_long = surprise is not None and surprise >= threshold
        surprise_short = surprise is not None and surprise <= -threshold
        reaction_long, reaction_short = z >= sigma, z <= -sigma
        leg = "LONG" if surprise_long and reaction_long else "SHORT" if surprise_short and reaction_short else None
        events.append(
            {
                "symbol": row.symbol,
                "report_date": row.date,
                "session": session,
                "decision_at": decision_at(session, clock),
                "z": z,
                "surprise_pct": surprise,
                "surprise_pct_reported": row.surprise_pct_reported,
                "atr": atr,
                "median_dollar_volume": dollar_volume,
                "reference": reference,
                "leg": leg,
                "surprise_long": surprise_long,
                "surprise_short": surprise_short,
                "reaction_long": reaction_long,
                "reaction_short": reaction_short,
            }
        )

    frame = pd.DataFrame(events, columns=list(EVENT_COLUMNS))
    frame["leg"] = frame["leg"].astype(object).where(frame["leg"].notna(), None)
    counts = {
        "rows": len(rows),
        "events": len(frame),
        "reasons": dict(reasons),
        "reasons_by_year": {reason: dict(years) for reason, years in by_year.items()},
    }
    return frame, counts
