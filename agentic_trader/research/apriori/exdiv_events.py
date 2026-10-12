"""Pre-ex-dividend events and their mid-quarter placebo controls from corporate actions and daily bars.

For a non-special cash dividend of symbol ``s`` with ex-date ``E`` on the observed calendar:

- placement first: ``E`` inside the calendar span (else ``outside_calendar``), an observed session
  (else ``non_session_ex_date``) and a decision session ``D = E - entry_lead_sessions`` inside the
  decision window (else ``outside_window``), so a dividend outside the study is never counted under
  an eligibility reason;
- regularity: the previous non-special ex-date ``P`` of ``s`` (in the window or not) lies
  ``min_gap_days..max_gap_days`` calendar days earlier (the Hartzmark-Solomon "predicted dividend");
  a second regular dividend on the same ex-date is ``duplicate_ex_date``;
- the leg decides at 10:35 on ``D``; liquidity (raw bars, the live dynamic-universe functions) and
  ATR (adjusted bars) are measured as of ``D - 1``, and the raw close there is kept as ``raw_close``;
- no other ex-date of ``s`` (specials included) falls in ``[D - 1, E - 1]``, so the hold is
  cum-dividend by construction, and no other corporate action of ``s`` falls within
  ``structural_exclusion_days`` of ``[D, E]``;
- the control is the same name decided at ``D_c = D + control_offset_sessions``, same gates as of
  ``D_c - 1``, dropped when any ex-date of ``s`` (specials included) falls within one session of its
  span or a structural action is nearby. It carries the leg's ``session`` as the pairing key.

Every excluded dividend (or control) is counted by reason and ex-date year, never silently dropped.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from agentic_trader.data.corporate_actions import CorporateAction, CorporateActionKind, between
from agentic_trader.research.apriori.catalog import ExdivEntry
from agentic_trader.research.apriori.pead_events import (
    SUPPORTED_SYMBOL,
    MarketData,
    atr_through,
    by_session,
    decision_at,
)
from agentic_trader.screeners.dynamic_universe import StaticReference, median_dollar_volume, static_reference


__all__ = ["EXDIV_EVENT_COLUMNS", "build_exdiv_events"]

EXDIV_EVENT_COLUMNS: tuple[str, ...] = (
    "pair_id",
    "role",
    "symbol",
    "session",
    "control_session",
    "decision_at",
    "ex_date",
    "previous_ex_date",
    "gap_days",
    "rate",
    "foreign",
    "atr",
    "median_dollar_volume",
    "reference",
    "raw_close",
)


@dataclass(frozen=True)
class _Measured:
    """A passing gate as of the session before a decision."""

    atr: float
    median_dollar_volume: float
    reference: float
    raw_close: float  # unadjusted, the dividend-yield denominator


def build_exdiv_events(
    actions: Sequence[CorporateAction], market: MarketData, entry: ExdivEntry
) -> tuple[pd.DataFrame, dict]:
    days = market.trading_days
    position = {day: i for i, day in enumerate(days)}
    start, end = entry.window.decisions
    lead, offset = entry.trade.entry_lead_sessions, entry.trade.control_offset_sessions
    atr_window = entry.trade.atr_window
    clock = entry.trade.decision_time()
    pad = timedelta(days=entry.event.structural_exclusion_days)
    static_daily = {s: market.liquidity_daily[s] for s in market.static_symbols if s in market.liquidity_daily}
    references: dict[date, StaticReference] = {}
    keyed: dict[str, pd.DataFrame] = {}
    keyed_raw: dict[str, pd.DataFrame] = {}
    reasons: Counter[str] = Counter()
    by_year: dict[str, Counter[int]] = defaultdict(Counter)
    rows: list[dict] = []
    dividends: dict[str, list[CorporateAction]] = defaultdict(list)
    structural: dict[str, list[CorporateAction]] = defaultdict(list)
    for action in actions:
        (dividends if action.kind is CorporateActionKind.CASH_DIVIDEND else structural)[action.symbol].append(action)

    def skip(reason: str, ex: date) -> None:
        reasons[reason] += 1
        by_year[reason][ex.year] += 1

    def gate(symbol: str, d: int) -> _Measured | str:
        """Liquidity and ATR as of ``days[d - 1]``: the measurements, or the reason the gate failed."""
        as_of = days[d - 1]
        if as_of not in references:
            references[as_of] = static_reference(static_daily, entry.universe.static_percentile, as_of=as_of)
        reference = references[as_of].value
        if reference is None:
            return "no_reference"
        raw = market.liquidity_daily[symbol]
        dollar_volume = median_dollar_volume(raw, as_of)
        if dollar_volume is None:
            return "short_history"
        liquidity = keyed_raw.setdefault(symbol, by_session(raw))
        close = float(liquidity.at[as_of, "Close"]) if as_of in liquidity.index else math.nan
        if not math.isfinite(close):
            return "no_raw_close"
        if close < entry.universe.min_price:
            return "illiquid_price"
        if dollar_volume < reference:
            return "illiquid_volume"
        sym = keyed.setdefault(symbol, by_session(market.daily[symbol]))
        atr = atr_through(sym, days, d - 1, atr_window)
        if atr is None:
            return "no_atr"
        return _Measured(atr=atr, median_dollar_volume=dollar_volume, reference=reference, raw_close=close)

    def measured_columns(measured: _Measured) -> dict:
        return {
            "atr": measured.atr,
            "median_dollar_volume": measured.median_dollar_volume,
            "reference": measured.reference,
            "raw_close": measured.raw_close,
        }

    total = legs = controls = 0
    for symbol in sorted(dividends):
        series = sorted(dividends[symbol], key=lambda a: (a.effective_on, a.id))
        total += len(series)
        ex_dates = {a.effective_on for a in series}
        supported = SUPPORTED_SYMBOL.fullmatch(symbol) is not None
        predecessor: CorporateAction | None = None  # the previous non-special dividend, wherever it fell
        for action in series:
            ex = action.effective_on
            if action.special:
                skip("special_dividend", ex)
                continue
            previous, predecessor = predecessor, action
            if not supported:
                skip("unsupported_symbol", ex)
                continue
            # Placement before eligibility: a dividend outside the calendar or the decision window is
            # counted as such, never as no_predecessor / irregular_gap.
            if not days or not days[0] <= ex <= days[-1]:
                skip("outside_calendar", ex)
                continue
            e = position.get(ex)
            if e is None:
                skip("non_session_ex_date", ex)
                continue
            d = e - lead
            if d < 0:  # the decision session precedes the calendar
                skip("outside_calendar", ex)
                continue
            session = days[d]
            if not start <= session <= end:
                skip("outside_window", ex)
                continue
            if previous is None:
                skip("no_predecessor", ex)
                continue
            gap = (ex - previous.effective_on).days
            if gap == 0:
                skip("duplicate_ex_date", ex)
                continue
            if not entry.event.min_gap_days <= gap <= entry.event.max_gap_days:
                skip("irregular_gap", ex)
                continue
            if d - 1 - atr_window < 0:  # not enough calendar before D for the ATR window
                skip("outside_calendar", ex)
                continue
            adjusted = market.daily.get(symbol)
            raw = market.liquidity_daily.get(symbol)
            if adjusted is None or adjusted.empty or raw is None or raw.empty:
                skip("no_daily_bars", ex)
                continue
            # Cum-dividend by construction: no other ex-date of the name from D - 1 through E - 1.
            if any(days[d - 1] <= other <= days[e - 1] for other in ex_dates):
                skip("leg_overlaps_ex_date", ex)
                continue
            if between(structural[symbol], symbol, session - pad, ex + pad):
                skip("structural_action_nearby", ex)
                continue
            measured = gate(symbol, d)
            if isinstance(measured, str):
                skip(measured, ex)
                continue
            base = {
                "pair_id": action.id,
                "symbol": symbol,
                "session": session,
                "ex_date": ex,
                "previous_ex_date": previous.effective_on,
                "gap_days": gap,
                "rate": action.rate,
                "foreign": action.foreign,
            }
            rows.append(
                {
                    **base,
                    "role": "leg",
                    "control_session": None,
                    "decision_at": decision_at(session, clock),
                    **measured_columns(measured),
                }
            )
            legs += 1
            # Control reasons share the `reasons` dict (and `reasons_by_year`) under a `control_` prefix,
            # keyed by the leg's ex-date year; the leg row above is kept whatever happens to its control.
            dc = d + offset
            if dc >= len(days):
                skip("control_outside_calendar", ex)
                continue
            control_session = days[dc]
            last = days[min(dc + lead - 1, len(days) - 1)]
            span_start, span_end = days[dc - 1], days[min(dc + lead, len(days) - 1)]
            if any(span_start <= other <= span_end for other in ex_dates):
                skip("control_overlaps_ex_date", ex)
                continue
            if between(structural[symbol], symbol, control_session - pad, last + pad):
                skip("control_structural_action_nearby", ex)
                continue
            control = gate(symbol, dc)
            if isinstance(control, str):
                skip(f"control_{control}", ex)
                continue
            rows.append(
                {
                    **base,
                    "role": "control",
                    "control_session": control_session,
                    "decision_at": decision_at(control_session, clock),
                    **measured_columns(control),
                }
            )
            controls += 1

    frame = pd.DataFrame(rows, columns=list(EXDIV_EVENT_COLUMNS))
    frame["control_session"] = frame["control_session"].astype(object).where(frame["control_session"].notna(), None)
    counts = {
        "dividends": total,
        "payers": len(dividends),  # symbols with at least one cash dividend (specials included)
        "legs": legs,
        "controls": controls,
        "unpaired_legs": legs - controls,
        "reasons": dict(reasons),
        "reasons_by_year": {reason: dict(years) for reason, years in by_year.items()},
        "reference_names": {day.isoformat(): ref.names for day, ref in sorted(references.items())},
    }
    return frame, counts
