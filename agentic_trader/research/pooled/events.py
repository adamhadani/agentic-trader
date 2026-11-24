"""Event-conditioned score fields for the pooled lane (``events_fields_v1``), built offline from
the Nasdaq calendar page cache and the corporate-actions acquisition.

Every field is per symbol, indexed by NYSE trading day T and holds the state known at T's
regular close; ``evaluate_prepared`` then reads it at D-1 like any daily field. A report dated
on session D is complete when D+1 closes (its reaction is close(D-1) -> close(D+1), the PEAD
convention), so it is first visible at row D+1 with ``earnings_age`` 0.

Both sources key their rows by the ticker in use at the time, while the cohort's bars carry today's
symbol. A name change that moves a ticker onto or off a cohort symbol is an identity break: every
field is NaN before the latest such break, and earlier calendar and corporate-action rows are dropped.
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
from agentic_trader.data.corporate_actions import CorporateAction, CorporateActionKind
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
    "identity_breaks",
    "load_actions",
    "load_calendar_pages",
    "pages_sha256",
    "verify_event_sources",
]

EVENT_FIELDS = ("earnings_age", "earnings_surprise", "earnings_reaction", "dividend_yield")
_SURPRISE_DECIMALS = 9


@dataclass(frozen=True)
class LoadedCalendar:
    """Deduplicated calendar rows per symbol (sorted by date) with the page-set digest and counts."""

    rows_by_symbol: Mapping[str, tuple[CalendarRow, ...]]
    pages_sha256: str
    pages: int
    duplicates: int


@dataclass(frozen=True)
class EventCoverage:
    """What the builder could not fill: symbols without data and skipped rows, for the results doc.

    ``identity_breaks`` maps a symbol to the ISO date of its latest identity break (see the module).
    """

    symbols_without_reports: tuple[str, ...]
    symbols_without_actions: tuple[str, ...]
    non_session_reports: int
    unpriced_dividends: int
    unscaled_dividends: int
    symbols_without_raw: tuple[str, ...]
    identity_breaks: Mapping[str, str]
    first_finite: Mapping[str, dict[str, str | None]]


@dataclass(frozen=True)
class EventFrames:
    """Per-symbol event frames (naive-midnight DatetimeIndex over the trading days) and their coverage."""

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
        raise ValueError(f"{path}: actions.json must be a JSON list of corporate actions")  # noqa: TRY004
    digest = hashlib.sha256(encode(raw).encode()).hexdigest()
    return tuple(CorporateAction.model_validate(row) for row in raw), digest


def verify_event_sources(inputs: EventInputs, calendar: LoadedCalendar, actions_sha256: str) -> None:
    """Fail closed unless both source digests equal the ones the protocol pins."""
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
        vol_window = inputs.earnings.vol_window
        z = reaction_z(keyed, bench, days, i, vol_window) if keyed is not None and i - 1 - vol_window >= 0 else None
        visible.append((i + 1, _surprise(row, inputs), math.nan if z is None else z))
    for k, (start, s, z) in enumerate(visible):
        stop = visible[k + 1][0] if k + 1 < len(visible) else n
        age[start:stop] = np.arange(stop - start, dtype=float)
        surprise[start:stop] = s
        reaction[start:stop] = z
    return age, surprise, reaction, skipped


def identity_breaks(actions: Sequence[CorporateAction], symbols: Sequence[str]) -> dict[str, date]:
    """Per symbol, the latest name change whose old (``symbol``) or new (``related_symbol``) ticker it is.

    Before that date the ticker named another issuer or this issuer used another ticker, so rows keyed
    by it do not belong to today's bars. A rename that keeps the ticker moves nothing and is ignored.
    """
    wanted = set(symbols)
    breaks: dict[str, date] = {}
    for action in actions:
        if action.kind != CorporateActionKind.NAME_CHANGE or action.related_symbol == action.symbol:
            continue
        for symbol in (action.symbol, action.related_symbol):
            if symbol in wanted and (symbol not in breaks or action.effective_on > breaks[symbol]):
                breaks[symbol] = action.effective_on
    return breaks


def _dividend_yield(
    actions: Sequence[CorporateAction],
    close: np.ndarray,
    factor: np.ndarray,
    days: Sequence[date],
    inputs: EventInputs,
    start: int,
) -> tuple[np.ndarray, int, int]:
    """Trailing regular dividends in the adjusted-price scale, over the adjusted close at T.

    ``factor`` is adjusted/raw close, forward-filled. A dividend's declared ``rate`` is scaled by
    ``factor`` at its own ex-session, so the ratio to ``close[T]`` involves only actions in (k, T].
    Sessions before ``start`` (an identity break) count as missing, so the warm-up restarts there.
    """
    n = len(days)
    paid = np.zeros(n)
    paid[:start] = np.nan
    unpriced = unscaled = 0
    for action in actions:
        if action.kind != CorporateActionKind.CASH_DIVIDEND or action.special:
            continue
        if action.rate is None:
            unpriced += 1
            continue
        if action.effective_on < days[0]:
            continue
        k = bisect_left(days, action.effective_on)
        if k >= n:
            continue
        if not math.isfinite(factor[k]):
            unscaled += 1
            continue
        paid[k] += action.rate * factor[k]
    window = inputs.corporate_actions.trailing_sessions
    trailing = pd.Series(paid).rolling(window, min_periods=window).sum().to_numpy(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = trailing / close
    out[~np.isfinite(out)] = np.nan
    return out, unpriced, unscaled


def build_event_frames(
    inputs: EventInputs,
    calendar: LoadedCalendar,
    actions: Sequence[CorporateAction],
    adjusted: Mapping[str, pd.DataFrame],
    raw: Mapping[str, pd.DataFrame],
    trading_days: Sequence[date],
    symbols: Sequence[str],
) -> EventFrames:
    """One frame per symbol on the full trading-day calendar: the four ``events_fields_v1`` columns.

    ``raw`` is the unadjusted daily bars (same keys and ``Close`` column as ``adjusted``); it puts each
    dividend into the adjusted-price scale at its own ex-date.
    """
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
    breaks = identity_breaks(actions, symbols)
    frames: dict[str, pd.DataFrame] = {}
    first_finite: dict[str, dict[str, str | None]] = {}
    no_reports: list[str] = []
    no_actions: list[str] = []
    no_raw: list[str] = []
    skipped_total = unpriced_total = unscaled_total = 0
    for symbol in symbols:
        frame = adjusted.get(symbol)
        keyed = by_session(frame) if frame is not None and not frame.empty else None
        close = np.full(len(days), np.nan)
        if keyed is not None:
            close = keyed["Close"].reindex(list(days)).to_numpy(float)
        factor = np.full(len(days), np.nan)
        raw_frame = raw.get(symbol)
        if keyed is not None:
            if raw_frame is None or raw_frame.empty:
                no_raw.append(symbol)
            else:
                raw_close = by_session(raw_frame)["Close"].reindex(list(days)).to_numpy(float)
                with np.errstate(invalid="ignore", divide="ignore"):
                    ratio = close / raw_close
                ratio[~np.isfinite(ratio) | (ratio <= 0)] = np.nan
                factor = pd.Series(ratio).ffill().to_numpy(float)
        rows = calendar.rows_by_symbol.get(symbol, ())
        symbol_actions: Sequence[CorporateAction] = actions_by_symbol.get(symbol, ())
        break_on = breaks.get(symbol)
        start = 0
        if break_on is not None:
            # Rows keyed by this ticker before the break belong to another issuer or ticker.
            start = bisect_left(days, break_on)
            rows = tuple(row for row in rows if row.date >= break_on)
            symbol_actions = [action for action in symbol_actions if action.effective_on >= break_on]
        if not rows:
            no_reports.append(symbol)
        if not symbol_actions:
            no_actions.append(symbol)
        age, surprise, reaction, skipped = _earnings_columns(rows, keyed, bench, days, position, inputs)
        if keyed is not None and symbol in no_raw:
            dividend_yield, unpriced, unscaled = np.full(len(days), np.nan), 0, 0
        else:
            dividend_yield, unpriced, unscaled = _dividend_yield(symbol_actions, close, factor, days, inputs, start)
        for column in (age, surprise, reaction, dividend_yield):
            column[:start] = np.nan
        skipped_total += skipped
        unpriced_total += unpriced
        unscaled_total += unscaled
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
            unscaled_dividends=unscaled_total,
            symbols_without_raw=tuple(no_raw),
            identity_breaks={symbol: breaks[symbol].isoformat() for symbol in symbols if symbol in breaks},
            first_finite=first_finite,
        ),
    )
