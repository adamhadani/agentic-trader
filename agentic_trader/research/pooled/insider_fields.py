"""SEC Form 4 insider-trading score fields for the pooled lane (``insider_fields_v1``), built offline from
the cached quarterly insider-transaction archives, the ticker map, the corporate-actions acquisition and
the cohort's raw daily bars.

Every field is per symbol, indexed by NYSE trading day T, and holds the state known by the end of T: a
filing dated F counts on every session T >= F (a filing dated on a non-session counts from the next
session; one dated after the last session is never visible). Windows are ``(T - w, T]`` in sessions,
inclusive of T. ``DV_T`` is the median raw ``Close x Volume`` over the last 20 finite sessions through
T (``NaN`` on a session without a bar, with fewer than 20, or when 0).

A symbol is covered from the first session with any qualifying transaction filed for its issuer; before
that ``insider_buy_rel``, ``insider_sell_rel``, ``insider_buyers`` and ``insider_opp_buy_rel`` are
``NaN``, and once covered they are ``0`` (exactly) without activity in the window. ``insider_net_ratio``
is ``NaN`` with a zero denominator and ``insider_buy_age`` before the first purchase on the calendar.

A filing dated before the calendar's first session has no session position: it covers the symbol from
the first session and counts in the routine set, but a window that reaches before the calendar
(``T < w - 1``) cannot be summed exactly when such a filing of that side exists, so the window's fields
are ``NaN`` there, and ``insider_buy_age`` starts at the first purchase on the calendar.

A transaction whose dollars exceed ``max_trade_rel`` times ``DV`` at its filing session is implausible
(a share-count or price entry error): it is dropped before any field is computed, including the routine
set and the coverage start, and counted under ``implausible_size``. Without a finite ``DV`` at that session
(fewer than 20 sessions of bars, a missing bar, a filing before the calendar) it is kept.

A purchase is routine when the same owner filed a purchase in the same issuer in the same calendar month
of the previous year (filing months on both sides); routine purchases leave ``insider_opp_buy_rel``.
Identity breaks are the events lane's rule: every field is ``NaN`` before a symbol's latest break, but no
transaction is discarded (the CIK survives a rename).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from agentic_trader.data.corporate_actions import CorporateAction
from agentic_trader.research.apriori.pead_events import by_session
from agentic_trader.research.insider_filings import (
    DROP_RULES,
    InsiderFilingStore,
    InsiderTransaction,
    files_sha256,
    parse_quarter,
    quarters_between,
    read_transactions,
    symbol_ciks,
)
from agentic_trader.research.pooled.campaign import InsiderFilingInputs, InsiderInputs
from agentic_trader.research.pooled.events import identity_breaks
from agentic_trader.research.pooled.short_fields import first_finite


__all__ = [
    "INSIDER_FIELDS",
    "InsiderCoverage",
    "InsiderFrames",
    "LoadedInsiderFilings",
    "build_insider_frames",
    "dollar_volume_series",
    "load_insider_filings",
    "plausible_transactions",
    "verify_insider_sources",
]

INSIDER_FIELDS = (
    "insider_net_ratio",
    "insider_buy_rel",
    "insider_sell_rel",
    "insider_buyers",
    "insider_buy_age",
    "insider_opp_buy_rel",
)
_DV_SESSIONS = 20
IMPLAUSIBLE_SIZE = "implausible_size"


@dataclass(frozen=True)
class LoadedInsiderFilings:
    """The qualifying transactions of the requested symbols over the protocol's quarters, with the files
    digest over every archive and the ticker map.

    ``rows`` counts the kept transactions (a row fanned out to two share classes counts twice);
    ``unscored_rows`` the qualifying archive rows whose issuer CIK maps to no requested symbol;
    ``drops`` the archive rows dropped by each reader rule (``DROP_RULES``), summed over the quarters.
    """

    by_symbol: Mapping[str, tuple[InsiderTransaction, ...]]
    files_sha256: str
    quarters: tuple[str, ...]
    rows: int
    unscored_rows: int
    drops: Mapping[str, int]
    symbols_without_cik: tuple[str, ...]


@dataclass(frozen=True)
class InsiderCoverage:
    """What the builder could not fill, for the results doc (over the symbols passed to the builder).

    ``symbols_without_rows`` lists the symbols never covered (``coverage_start`` ``None``): no loaded
    transaction, or every one filed after the last session or dropped as implausible. ``unscored_rows``
    is the loader's count plus the loaded transactions of names the builder does not score. ``drops``
    merges two scopes: the reader's per-rule counts are over every issuer in the archives, while
    ``implausible_size`` is over the scored cohort rows only (a row fanned out to two share classes
    counts once per class). ``coverage_start`` maps a symbol to the ISO date of its first covered session
    (``None`` without one); ``identity_breaks`` maps a symbol to the ISO date of its latest identity break.
    Share classes of one issuer each divide the issuer's dollars by their own class's ``DV``: the insider
    trades the issuer, the lane trades the class.
    """

    symbols_without_cik: tuple[str, ...]
    symbols_without_rows: tuple[str, ...]
    unscored_rows: int
    drops: Mapping[str, int]
    identity_breaks: Mapping[str, str]
    coverage_start: Mapping[str, str | None]
    first_finite: Mapping[str, dict[str, str | None]]


@dataclass(frozen=True)
class InsiderFrames:
    """Per-symbol insider frames (naive-midnight DatetimeIndex over the trading days) and their coverage."""

    frames: Mapping[str, pd.DataFrame]
    coverage: InsiderCoverage


def load_insider_filings(directory: Path, inputs: InsiderFilingInputs, symbols: Sequence[str]) -> LoadedInsiderFilings:
    """Read the cached archives of ``[quarters_from, quarters_through]`` and the ticker map from disk only,
    keeping the transactions of ``symbols``.

    A missing quarter or ticker map, or a body that differs from its meta file, fails closed.
    """
    store = InsiderFilingStore(directory)
    quarters = quarters_between(parse_quarter(inputs.quarters_from), parse_quarter(inputs.quarters_through))
    tickers, archives = store.load_window(quarters)
    cik_to_symbols, without_cik = symbol_ciks(tickers, symbols)
    by_symbol: dict[str, list[InsiderTransaction]] = {}
    drops = dict.fromkeys(DROP_RULES, 0)
    unscored = 0
    for archive in archives:
        transactions, dropped, outside = read_transactions(
            archive,
            cik_to_symbols=cik_to_symbols,
            owner_roles=inputs.owner_roles,
            exclude_10b5_1=inputs.exclude_10b5_1,
        )
        for transaction in transactions:
            by_symbol.setdefault(transaction.symbol, []).append(transaction)
        for rule, count in dropped.items():
            drops[rule] += count
        unscored += outside
    return LoadedInsiderFilings(
        by_symbol={symbol: tuple(found) for symbol, found in by_symbol.items()},
        files_sha256=files_sha256(store, quarters),
        quarters=tuple(str(quarter) for quarter in quarters),
        rows=sum(len(found) for found in by_symbol.values()),
        unscored_rows=unscored,
        drops=drops,
        symbols_without_cik=without_cik,
    )


def verify_insider_sources(inputs: InsiderInputs, loaded: LoadedInsiderFilings, actions_sha256: str) -> None:
    """Fail closed unless both source digests equal the ones the protocol pins."""
    if loaded.files_sha256 != inputs.insider_filings.files_sha256:
        raise ValueError(
            "insider-filings digest does not match the protocol's insider_inputs.insider_filings.files_sha256"
        )
    if actions_sha256 != inputs.corporate_actions.content_sha256:
        raise ValueError(
            "corporate-actions digest does not match the protocol's insider_inputs.corporate_actions.content_sha256"
        )


def _window_sum(per_session: np.ndarray, events: np.ndarray, window: int) -> np.ndarray:
    """Sums over ``(T - window, T]``; exactly ``0`` where the window holds no event (no cumsum residue)."""
    total = np.cumsum(per_session)
    count = np.cumsum(events)
    total[window:] -= total[:-window].copy()
    count[window:] -= count[:-window].copy()
    return np.where(count > 0, total, 0.0)


def _distinct_owners(positions: np.ndarray, owners: np.ndarray, n: int, window: int) -> np.ndarray:
    """Distinct owners with an event in ``(T - window, T]``: a sliding window over the sorted events,
    evaluated where the count can change (an event entering at k, leaving at k + window)."""
    out = np.zeros(n)
    if not len(positions):
        return out
    order = np.argsort(positions, kind="stable")
    at, who = positions[order], owners[order]
    points = np.unique(np.concatenate([at, at + window]))
    points = points[points < n]
    counts: dict[int, int] = {}
    entered = left = 0
    values = np.zeros(len(points))
    for j, t in enumerate(points):
        while entered < len(at) and at[entered] <= t:
            counts[int(who[entered])] = counts.get(int(who[entered]), 0) + 1
            entered += 1
        while left < entered and at[left] <= t - window:
            owner = int(who[left])
            counts[owner] -= 1
            if not counts[owner]:
                del counts[owner]
            left += 1
        values[j] = len(counts)
    step = np.searchsorted(points, np.arange(n), side="right") - 1
    return np.where(step >= 0, values[np.maximum(step, 0)], 0.0)


def dollar_volume_series(raw: pd.DataFrame | None, days: Sequence[date]) -> np.ndarray:
    """``DV_T``: the median raw ``Close x Volume`` over the last 20 finite sessions through T."""
    if raw is None or raw.empty:
        return np.full(len(days), np.nan)
    keyed = by_session(raw).reindex(list(days))
    dollars = keyed["Close"].astype(float) * keyed["Volume"].astype(float)
    finite = dollars[np.isfinite(dollars.to_numpy(float))]
    median = finite.rolling(_DV_SESSIONS, min_periods=_DV_SESSIONS).median().reindex(list(days))
    out = median.to_numpy(float)
    with np.errstate(invalid="ignore"):
        return np.where(out > 0, out, np.nan)


def _routine(transactions: Sequence[InsiderTransaction]) -> list[bool]:
    """Per purchase, whether its owner filed a purchase in the same issuer the same month a year earlier."""
    months = {(t.owner_cik, t.filing_date.year, t.filing_date.month) for t in transactions}
    return [(t.owner_cik, t.filing_date.year - 1, t.filing_date.month) in months for t in transactions]


def _per_session(position: np.ndarray, dollars: np.ndarray, keep: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Dollars and event counts per session of the kept transactions."""
    return (
        np.bincount(position[keep], weights=dollars[keep], minlength=n).astype(float),
        np.bincount(position[keep], minlength=n),
    )


def plausible_transactions(
    transactions: Sequence[InsiderTransaction],
    day_ordinals: np.ndarray,
    dollar_volume: np.ndarray,
    max_trade_rel: float,
) -> tuple[list[InsiderTransaction], int]:
    """The transactions visible on the calendar and not implausibly large, in input order, and the
    count dropped as implausible. ``day_ordinals`` must be non-empty."""
    # Filings after the last session are never visible, not even to the routine set.
    visible = [t for t in transactions if t.filing_date.toordinal() <= day_ordinals[-1]]
    filed = np.array([t.filing_date.toordinal() for t in visible], dtype=np.int64)
    position = np.searchsorted(day_ordinals, filed, side="left")  # a non-session rolls forward
    before = filed < day_ordinals[0]  # no session position on this calendar
    session_dv = np.where(before, np.nan, dollar_volume[position])
    # A missing DV compares False: the transaction is kept.
    plausible = ~(np.array([t.dollars for t in visible], dtype=float) > max_trade_rel * session_dv)
    seen = [t for t, keep in zip(visible, plausible, strict=True) if keep]
    return seen, int((~plausible).sum())


def _symbol_fields(
    transactions: Sequence[InsiderTransaction],
    day_ordinals: np.ndarray,
    dollar_volume: np.ndarray,
    buy_window: int,
    net_window: int,
    max_trade_rel: float,
) -> tuple[dict[str, np.ndarray], int | None, int]:
    """The six columns for one symbol before identity breaks, its coverage-start position, and the
    count of transactions dropped as implausible."""
    n = len(day_ordinals)
    nan = np.full(n, np.nan)
    if not n:
        return {name: nan.copy() for name in INSIDER_FIELDS}, None, 0
    seen, implausible = plausible_transactions(transactions, day_ordinals, dollar_volume, max_trade_rel)
    if not seen:
        return {name: nan.copy() for name in INSIDER_FIELDS}, None, implausible
    filed = np.array([t.filing_date.toordinal() for t in seen], dtype=np.int64)
    position = np.searchsorted(day_ordinals, filed, side="left")
    before = filed < day_ordinals[0]
    placed = ~before
    buy = np.array([t.code == "P" for t in seen])
    dollars = np.array([t.dollars for t in seen], dtype=float)
    owners = np.array([t.owner_cik for t in seen], dtype=np.int64)
    opportunistic = np.zeros(len(seen), dtype=bool)
    opportunistic[np.flatnonzero(buy)] = ~np.array(_routine([t for t in seen if t.code == "P"]), dtype=bool)
    purchases, purchase_events = _per_session(position, dollars, buy & placed, n)
    sales, sale_events = _per_session(position, dollars, ~buy & placed, n)
    opp, opp_events = _per_session(position, dollars, opportunistic & placed, n)
    buys_in = _window_sum(purchases, purchase_events, buy_window)
    sells_in = _window_sum(sales, sale_events, buy_window)
    opp_in = _window_sum(opp, opp_events, buy_window)
    buyers = _distinct_owners(position[buy & placed], owners[buy & placed], n, buy_window)
    buys_net = _window_sum(purchases, purchase_events, net_window)
    sells_net = _window_sum(sales, sale_events, net_window)
    with np.errstate(invalid="ignore", divide="ignore"):
        net = (buys_net - sells_net) / (buys_net + sells_net)
        buy_rel = buys_in / dollar_volume
        sell_rel = sells_in / dollar_volume
        opp_rel = opp_in / dollar_volume
    net[~((buys_net + sells_net) > 0)] = np.nan
    t = np.arange(n)
    # A window reaching before the calendar holds an unplaceable earlier filing of that side.
    if (before & buy).any():
        for column in (buy_rel, opp_rel, buyers):
            column[t < buy_window - 1] = np.nan
    if (before & ~buy).any():
        sell_rel[t < buy_window - 1] = np.nan
    if before.any():
        net[t < net_window - 1] = np.nan
    start = 0 if before.any() else int(position.min())
    for column in (buy_rel, sell_rel, buyers, opp_rel):
        column[t < start] = np.nan
    placed_buys = np.unique(position[buy & placed])
    last = np.searchsorted(placed_buys, t, side="right") - 1
    age = np.where(last >= 0, t - placed_buys[np.maximum(last, 0)], np.nan) if len(placed_buys) else nan.copy()
    columns = {
        "insider_net_ratio": net,
        "insider_buy_rel": buy_rel,
        "insider_sell_rel": sell_rel,
        "insider_buyers": buyers,
        "insider_buy_age": age.astype(float),
        "insider_opp_buy_rel": opp_rel,
    }
    return columns, start, implausible


def build_insider_frames(
    inputs: InsiderInputs,
    loaded: LoadedInsiderFilings,
    actions: Sequence[CorporateAction],
    raw: Mapping[str, pd.DataFrame],
    trading_days: Sequence[date],
    symbols: Sequence[str],
) -> InsiderFrames:
    """One frame per symbol on the full trading-day calendar: the six ``insider_fields_v1`` columns.

    ``raw`` is the unadjusted daily bars (``Close`` and ``Volume``) the dollar-volume denominator reads.
    """
    params = inputs.insider_filings
    days = tuple(trading_days)
    index = pd.DatetimeIndex([pd.Timestamp(day) for day in days])
    day_ordinals = np.array([day.toordinal() for day in days], dtype=np.int64)
    breaks = identity_breaks(actions, symbols)
    scored = set(symbols)
    unscored = loaded.unscored_rows + sum(
        len(found) for symbol, found in loaded.by_symbol.items() if symbol not in scored
    )
    frames: dict[str, pd.DataFrame] = {}
    finite_from: dict[str, dict[str, str | None]] = {}
    coverage_start: dict[str, str | None] = {}
    without_rows: list[str] = []
    implausible = 0
    for symbol in symbols:
        columns, start, dropped = _symbol_fields(
            loaded.by_symbol.get(symbol, ()),
            day_ordinals,
            dollar_volume_series(raw.get(symbol), days),
            params.buy_window,
            params.net_window,
            params.max_trade_rel,
        )
        implausible += dropped
        coverage_start[symbol] = days[start].isoformat() if start is not None else None
        if start is None:
            without_rows.append(symbol)
        built = pd.DataFrame(columns, index=index)[list(INSIDER_FIELDS)]
        break_on = breaks.get(symbol)
        if break_on is not None:
            built.loc[index < pd.Timestamp(break_on)] = np.nan
        frames[symbol] = built
        finite_from[symbol] = first_finite(built, days, INSIDER_FIELDS)
    return InsiderFrames(
        frames=frames,
        coverage=InsiderCoverage(
            symbols_without_cik=tuple(symbol for symbol in loaded.symbols_without_cik if symbol in scored),
            symbols_without_rows=tuple(without_rows),
            unscored_rows=unscored,
            drops={**loaded.drops, IMPLAUSIBLE_SIZE: implausible},
            identity_breaks={symbol: breaks[symbol].isoformat() for symbol in symbols if symbol in breaks},
            coverage_start=coverage_start,
            first_finite=finite_from,
        ),
    )
