"""Event-conditioned score fields for the pooled lane (``events_fields_v1``), built offline from
the Nasdaq calendar page cache and the corporate-actions acquisition.

Every field is per symbol, indexed by NYSE trading day T and holds the state known at T's
regular close; ``evaluate_prepared`` then reads it at D-1 like any daily field. A report dated
on session D is complete when D+1 closes (its reaction is close(D-1) -> close(D+1), the PEAD
convention), so it is first visible at row D+1 with ``earnings_age`` 0.
"""

from __future__ import annotations

import hashlib
import json
import math
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from agentic_trader.agent.earnings import CalendarRow, parse_calendar_payload
from agentic_trader.data.corporate_actions import CorporateAction
from agentic_trader.research.apriori.earnings_history import CalendarPageStore, dedupe_rows, weekdays
from agentic_trader.research.apriori.pead_events import by_session, reaction_z
from agentic_trader.research.pooled.campaign import EventInputs
from agentic_trader.storage.workflow import encode


__all__ = [
    "EVENT_FIELDS",
    "EventCoverage",
    "EventFrames",
    "LoadedCalendar",
    "build_event_frames",
    "load_actions",
    "load_calendar_pages",
    "pages_sha256",
    "verify_event_sources",
]

EVENT_FIELDS = ("earnings_age", "earnings_surprise", "earnings_reaction", "dividend_yield")
_SURPRISE_DECIMALS = 9


@dataclass(frozen=True)
class LoadedCalendar:
    rows_by_symbol: Mapping[str, tuple[CalendarRow, ...]]
    pages_sha256: str
    pages: int
    duplicates: int


@dataclass(frozen=True)
class EventCoverage:
    symbols_without_reports: tuple[str, ...]
    symbols_without_actions: tuple[str, ...]
    non_session_reports: int
    unpriced_dividends: int
    first_finite: Mapping[str, dict[str, str | None]]


@dataclass(frozen=True)
class EventFrames:
    frames: Mapping[str, pd.DataFrame]
    coverage: EventCoverage


def _page_lines(directory: Path, calendar_from: date, calendar_through: date) -> list[str]:
    lines = []
    for day in weekdays(calendar_from, calendar_through):
        meta = directory / f"{day.isoformat()}.meta.json"
        if not meta.exists():
            raise ValueError(f"earnings calendar page for {day.isoformat()} is missing from {directory}")
        lines.append(f"{day.isoformat()}:{json.loads(meta.read_text())['sha256']}")
    return sorted(lines)


def pages_sha256(directory: Path, calendar_from: date, calendar_through: date) -> str:
    """SHA-256 of the sorted ``<date>:<page sha256>`` lines of every weekday page in the window."""
    return hashlib.sha256("\n".join(_page_lines(directory, calendar_from, calendar_through)).encode()).hexdigest()


def load_calendar_pages(directory: Path, calendar_from: date, calendar_through: date) -> LoadedCalendar:
    """Every weekday page in the window, read from disk only (never fetched); a missing page fails closed."""
    digest = pages_sha256(directory, calendar_from, calendar_through)
    store = CalendarPageStore(directory)
    rows: list[CalendarRow] = []
    days = weekdays(calendar_from, calendar_through)
    for day in days:
        body = store.load(day)
        if body is None:
            raise ValueError(f"earnings calendar page for {day.isoformat()} is missing from {directory}")
        rows.extend(parse_calendar_payload(json.loads(body), day))
    kept, duplicates = dedupe_rows(rows)
    by_symbol: dict[str, list[CalendarRow]] = defaultdict(list)
    for row in kept:
        by_symbol[row.symbol].append(row)
    return LoadedCalendar(
        rows_by_symbol={s: tuple(sorted(v, key=lambda r: r.date)) for s, v in by_symbol.items()},
        pages_sha256=digest,
        pages=len(days),
        duplicates=duplicates,
    )


def load_actions(path: Path) -> tuple[tuple[CorporateAction, ...], str]:
    """The acquisition's ``actions.json`` rows and their content digest (the manifest's ``content_sha256``)."""
    raw = json.loads(path.read_text())
    if not isinstance(raw, list):
        raise ValueError(f"{path}: actions.json is a JSON list of corporate actions")  # noqa: TRY004
    digest = hashlib.sha256(encode(raw).encode()).hexdigest()
    return tuple(CorporateAction.model_validate(row) for row in raw), digest


def verify_event_sources(inputs: EventInputs, calendar: LoadedCalendar, actions_sha256: str) -> None:
    if calendar.pages_sha256 != inputs.earnings.pages_sha256:
        raise ValueError("earnings pages digest does not match the protocol's event_inputs.earnings.pages_sha256")
    if actions_sha256 != inputs.corporate_actions.content_sha256:
        raise ValueError(
            "corporate-actions digest does not match the protocol's event_inputs.corporate_actions.content_sha256"
        )


def _surprise(row: CalendarRow, inputs: EventInputs) -> float:
    earnings = inputs.earnings
    if row.eps is None or row.eps_forecast is None or row.n_estimates is None:
        return math.nan
    if row.n_estimates < earnings.min_estimates or abs(row.eps_forecast) < earnings.min_abs_forecast:
        return math.nan
    return round(100.0 * (row.eps - row.eps_forecast) / abs(row.eps_forecast), _SURPRISE_DECIMALS)


def _earnings_columns(
    rows: Sequence[CalendarRow],
    keyed: pd.DataFrame | None,
    bench: pd.DataFrame,
    days: Sequence[date],
    position: Mapping[date, int],
    inputs: EventInputs,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    n = len(days)
    age = np.full(n, np.nan)
    surprise = np.full(n, np.nan)
    reaction = np.full(n, np.nan)
    skipped = 0
    visible: list[tuple[int, float, float]] = []
    for row in rows:
        i = position.get(row.date)
        if i is None:
            skipped += 1
            continue
        if i + 1 >= n:
            continue  # completes after the calendar ends: never visible here
        z = reaction_z(keyed, bench, days, i, inputs.earnings.vol_window) if keyed is not None else None
        visible.append((i + 1, _surprise(row, inputs), math.nan if z is None else z))
    for k, (start, s, z) in enumerate(visible):
        stop = visible[k + 1][0] if k + 1 < len(visible) else n
        if stop <= start:
            continue
        age[start:stop] = np.arange(stop - start, dtype=float)
        surprise[start:stop] = s
        reaction[start:stop] = z
    return age, surprise, reaction, skipped


def _dividend_yield(
    actions: Sequence[CorporateAction],
    close: np.ndarray,
    days: Sequence[date],
    inputs: EventInputs,
) -> tuple[np.ndarray, int]:
    n = len(days)
    paid = np.zeros(n)
    unpriced = 0
    for action in actions:
        if action.kind != "cash_dividend" or action.special:
            continue
        if action.rate is None:
            unpriced += 1
            continue
        k = bisect_left(days, action.effective_on)
        if k < n:
            paid[k] += action.rate
    window = inputs.corporate_actions.trailing_sessions
    trailing = pd.Series(paid).rolling(window, min_periods=window).sum().to_numpy(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = trailing / close
    out[~np.isfinite(out)] = np.nan
    return out, unpriced


def build_event_frames(
    inputs: EventInputs,
    calendar: LoadedCalendar,
    actions: Sequence[CorporateAction],
    adjusted: Mapping[str, pd.DataFrame],
    trading_days: Sequence[date],
    symbols: Sequence[str],
) -> EventFrames:
    """One frame per symbol on the full trading-day calendar: the four ``events_fields_v1`` columns."""
    days = tuple(trading_days)
    position = {day: i for i, day in enumerate(days)}
    benchmark = adjusted.get(inputs.earnings.benchmark)
    if benchmark is None or benchmark.empty:
        raise ValueError(f"benchmark {inputs.earnings.benchmark} has no adjusted daily bars for the event fields")
    bench = by_session(benchmark)
    index = pd.DatetimeIndex([pd.Timestamp(day) for day in days])
    actions_by_symbol: dict[str, list[CorporateAction]] = defaultdict(list)
    for action in actions:
        actions_by_symbol[action.symbol].append(action)
    frames: dict[str, pd.DataFrame] = {}
    first_finite: dict[str, dict[str, str | None]] = {}
    no_reports: list[str] = []
    no_actions: list[str] = []
    skipped_total = unpriced_total = 0
    for symbol in symbols:
        frame = adjusted.get(symbol)
        keyed = by_session(frame) if frame is not None and not frame.empty else None
        close = np.full(len(days), np.nan)
        if keyed is not None:
            close = keyed["Close"].reindex(list(days)).to_numpy(float)
        rows = calendar.rows_by_symbol.get(symbol, ())
        if not rows:
            no_reports.append(symbol)
        if symbol not in actions_by_symbol:
            no_actions.append(symbol)
        age, surprise, reaction, skipped = _earnings_columns(rows, keyed, bench, days, position, inputs)
        dividend_yield, unpriced = _dividend_yield(actions_by_symbol.get(symbol, ()), close, days, inputs)
        skipped_total += skipped
        unpriced_total += unpriced
        built = pd.DataFrame(
            {
                "earnings_age": age,
                "earnings_surprise": surprise,
                "earnings_reaction": reaction,
                "dividend_yield": dividend_yield,
            },
            index=index,
        )
        frames[symbol] = built
        first_finite[symbol] = {
            name: (
                days[int(np.argmax(np.isfinite(built[name].to_numpy())))].isoformat()
                if np.isfinite(built[name].to_numpy()).any()
                else None
            )
            for name in EVENT_FIELDS
        }
    return EventFrames(
        frames=frames,
        coverage=EventCoverage(
            symbols_without_reports=tuple(no_reports),
            symbols_without_actions=tuple(no_actions),
            non_session_reports=skipped_total,
            unpriced_dividends=unpriced_total,
            first_finite=first_finite,
        ),
    )
