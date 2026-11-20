"""Investor-day labels, controls, net excess, the frozen rule and its executor (entry ``idday``).

Spec sections 8-11 (``docs/superpowers/specs/2026-11-19-apriori-idday-design.md``). Every row is one LONG cell
``(symbol, D, H)``: the decision price is the close of the last regular hourly bar ending by the decision clock on
``D`` (``pead_study.decision_price``), the stop ``stop_atr_multiple`` x ATR as of ``D - 1`` below it, and
``label_bracket`` walks the regular-session bars with ``IDDAY_ARM`` (no target, no trail) to the close of the row's
own ``H``-th session, ``X`` for a leg. One walk at the decisive cost gives every cost column, as in ``runup``.

The hold is per row, so the labeller never uses the cube's 45-calendar-day label span (holds beyond about 30
sessions would be ``immature``): it slices the symbol's hourly bars from the decision minus a day through the
close of the ``H``-th session plus 3 calendar days, extended to the next trading day when that is later (a Friday
``X`` before a Monday holiday), so the walk always reaches the bar that resolves the time exit. Cells are labelled
once per ``(symbol, D, H)``: a name that controls two legs with different holds is two cells.

Exclusions are counted by reason, decision year and role, as in ``runup``: ``no_hourly_bars``,
``missing_session_bars`` (a session of ``[D, D + H - 1]`` without a regular bar), ``no_atr`` (controls only),
``no_decision_price``, ``degenerate_levels``, ``immature`` and ``hold_outside_window`` (the fill is not on ``D``,
the exit is after the close of the ``H``-th session, which is ``X`` for a leg or placebo, validated on entry, or a
time exit falls on any other day than that session).
Controls are labelled with their row's hold; the control row is the mean of its ``matched_controls`` cells, and any
failing cell leaves the row unpaired under the first failing cell's reason (``no_candidates`` when
``match_controls`` found too few names), counted by role, year and class. Every labelled cell gets
``net_excess_r_v1`` through ``pead_base_rate.add_net_excess`` over its own hold; every labelled event row also
gets ``no_stop_excess_r`` (section 10's buy-and-hold excess, descriptive).

Evaluation (spec section 9) reuses the shared session-block statistics under the column name ``r_cost``, each
test recording its ``unit``: P1 ``mean_test`` on the legs' finite ``net_excess_r``; P2 ``paired_test`` of paired
legs' ``r_cost`` against their control rows; P3 the 1%-trimmed mean of ``net_excess_r``, its mean from
``recent_from`` and ``cluster_paired_test`` of P2's pairs with the legs' ``cik`` as clusters; P4 the finite
counts; P5 ``difference_in_paired_test``, legs minus placebos from one joint bootstrap. The placebo runs P1 and
P2 on its rows and its P2 holding fails the read. The planted check adds ``planted_return x decision_price /
|entry - stop|`` R to every paired placebo's ``r_cost`` and ``net_excess_r`` and needs P1 and P2 to hold, or the
read is ``inconclusive_underpowered``; it is computed and saved before any leg statistic. Diagnostics decide
nothing.

``execute_idday_study`` writes ``protocol.json`` and ``manifest.json`` before ``build`` (the only provider access),
refuses inputs whose runner-recorded part digests (``cube_sha256_v3``, ``cube_sha256_xu``) differ from the entry's
pinned cohorts or whose merged cube does not hash to the recorded ``merged_cube_sha256``, then (spec section 6, in
depth behind the runner) an event table whose own ``events_sha256`` is not the entry's or a recorded parser check
(``provenance["parser_check"]``, ``validate_parser_check``'s result) that is not passing for the entry's sample
size; it then saves the frames, then ``planted.json``, then ``result.json``; any failure is ``status: failed``. Research only: nothing here reads a
database, a broker or the network, and nothing authorizes promotion.
"""

from __future__ import annotations

import asyncio
import math
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.apriori.catalog import IddayEntry, LoadedIddayEntry
from agentic_trader.research.apriori.exdiv_study import _regular_session_dates
from agentic_trader.research.apriori.idday_events import (
    IDDAY_EVENT_COLUMNS,
    MATCHED_ROLES,
    STANDALONE,
    WITH_EARNINGS,
    IddayInputs,
    build_idday_events,
    match_controls,
)
from agentic_trader.research.apriori.pead_base_rate import add_net_excess
from agentic_trader.research.apriori.pead_events import atr_through, by_session, decision_at
from agentic_trader.research.apriori.pead_study import decision_price, save_frame, utc_index
from agentic_trader.research.apriori.runup_study import (
    _as_r_cost,
    _dispersion,
    _failure,
    _mde,
    _numbers,
    _paired,
    _summary,
    _Tally,
    cost_column,
)
from agentic_trader.research.apriori.statistics import (
    cluster_paired_test,
    difference_in_paired_test,
    mean_test,
    paired_test,
    trimmed_mean,
)
from agentic_trader.research.setups.labels import (
    BracketArm,
    BracketHit,
    SetupLevels,
    label_bracket,
    regular_session_bars,
)
from agentic_trader.research.setups.study import _finite_json
from agentic_trader.storage.artifacts import save_json_report


__all__ = [
    "IDDAY_ARM",
    "IDDAY_CONTROL_CELL_COLUMNS",
    "IDDAY_CONTROL_COLUMNS",
    "IddayDecision",
    "evaluate_idday",
    "execute_idday_study",
    "idday_label_columns",
    "label_idday",
    "planted_check",
]

IDDAY_ARM = BracketArm(name="idday_time_exit", target_r=None, trail=None)
IDDAY_CONTROL_COLUMNS = (
    "pair_id",
    "role",
    "session",
    "hold_sessions",
    "r_cost",
    "net_excess_r",
    "beta",
    "control_symbols",
)
IDDAY_CONTROL_CELL_COLUMNS = (
    "pair_id",
    "role",
    "session",
    "hold_sessions",
    "symbol",
    "hit",
    "exit_time",
    "r_cost",
    "net_excess_r",
    "beta",
)
_OUTCOME_COLUMNS = ("decision_price", "entry_price", "entry", "stop", "entry_time", "exit_time", "hit", "r", "r_cost")
_NET_EXCESS_COLUMNS = ("beta", "name_cost_bps", "market_r", "net_excess_r")
_NET_EXCESS_INPUTS = ("symbol", "session", "entry_price", "entry", "stop", "entry_time", "exit_time", "r")
# ``SetupLevels`` needs a target above the entry; ``IDDAY_ARM`` drops it, so this one is never walked.
_UNREACHABLE_TARGET_R = 1000.0
_SLICE_MARGIN = timedelta(days=3)  # past the close of the H-th session (controller ruling)
_SESSION_CLOSE = time(16)
_TOP_SYMBOLS = 5  # section 10's concentration check drops the five symbols with the most legs
_CLASSES = (WITH_EARNINGS, STANDALONE)
_NO_STOP_DEFINITION = (
    "(adjusted close of X - decision price) / (entry - stop) minus beta x SPY's return from its daily open on D "
    "to its close on X, in the same R; descriptive"
)

_Key = tuple[str, date, int]  # (symbol, D, H)
_Cell = dict[str, Any] | str  # the labelled outcome of a cell, or the reason it failed


def idday_label_columns(entry: IddayEntry) -> tuple[str, ...]:
    return (
        *IDDAY_EVENT_COLUMNS,
        *_OUTCOME_COLUMNS,
        *(cost_column(cost) for cost in entry.costs_bps_per_side),
        "holding_sessions",
        *_NET_EXCESS_COLUMNS,
        "no_stop_excess_r",
    )


def _ny_date(stamp: Any) -> date:
    return pd.Timestamp(stamp).tz_convert(ET_TZ).date()


class _Bars:
    """A symbol's regular-session hourly bars, sorted and de-duplicated once, sliced by time."""

    def __init__(self, frame: pd.DataFrame):
        ordered = frame.set_axis(utc_index(frame).tz_convert("UTC")).sort_index()
        self._frame = regular_session_bars(ordered[~ordered.index.duplicated(keep="first")])
        self._index = self._frame.index

    def between(self, lo: datetime, hi: datetime) -> pd.DataFrame:
        start = self._index.searchsorted(pd.Timestamp(lo).tz_convert("UTC"), side="left")
        stop = self._index.searchsorted(pd.Timestamp(hi).tz_convert("UTC"), side="right")
        return self._frame.iloc[start:stop]


class _Labeller:
    """Labels each ``(symbol, D, H)`` cell once; every symbol's bars are prepared once."""

    def __init__(self, inputs: IddayInputs, entry: IddayEntry):
        self._inputs = inputs
        self._days = tuple(inputs.trading_days)
        self._pos = {day: i for i, day in enumerate(self._days)}
        self._clock = inputs.decision_time
        self._stop_atr = entry.trade.stop_atr_multiple
        self._atr_window = entry.trade.atr_window
        self._decisive = entry.decision_cost_bps
        self._costs = entry.costs_bps_per_side
        self._hourly: dict[str, _Bars | None] = {}
        self._dates: dict[str, frozenset[date]] = {}
        self._daily: dict[str, pd.DataFrame | None] = {}
        self.cells: dict[_Key, _Cell] = {}
        self._labelled: dict[_Key, dict[str, Any]] = {}

    @property
    def days(self) -> tuple[date, ...]:
        return self._days

    def position(self, session: date) -> int:
        p = self._pos.get(session)
        if p is None:
            raise ValueError(f"{session.isoformat()} is not a trading day")
        return p

    def when(self, session: date) -> pd.Timestamp:
        return pd.Timestamp(decision_at(session, self._clock))

    def daily(self, symbol: str) -> pd.DataFrame | None:
        """Adjusted daily bars keyed by New York session date (``None`` without bars)."""
        if symbol not in self._daily:
            frame = self._inputs.adjusted.get(symbol)
            self._daily[symbol] = None if frame is None or frame.empty else by_session(frame)
        return self._daily[symbol]

    def _bars(self, symbol: str) -> _Bars | None:
        if symbol not in self._hourly:
            frame = self._inputs.hourly.get(symbol)
            if frame is None or frame.empty:
                self._hourly[symbol], self._dates[symbol] = None, frozenset()
            else:
                self._hourly[symbol] = _Bars(frame)
                self._dates[symbol] = _regular_session_dates(frame)
        return self._hourly[symbol]

    def outcome(self, key: _Key) -> dict[str, Any]:
        """A cell already labelled successfully (``KeyError`` otherwise)."""
        return self._labelled[key]

    def cell(self, key: _Key, atr: float | None = None) -> _Cell:
        """The cell's outcome, labelled on first request (``atr`` from the events frame, else computed)."""
        if key not in self.cells:
            cell = self.cells[key] = self._label(*key, atr)
            if not isinstance(cell, str):
                self._labelled[key] = cell
        return self.cells[key]

    def _slice_end(self, last: int) -> datetime:
        """The close of session ``last`` plus the margin, reaching at least the next trading day's close."""
        day = self._days[last] + _SLICE_MARGIN
        if last + 1 < len(self._days):
            day = max(day, self._days[last + 1])
        return datetime.combine(day, _SESSION_CLOSE, tzinfo=ET_TZ)

    def _label(self, symbol: str, session: date, hold: int, atr: float | None) -> _Cell:
        d = self.position(session)
        last = d + hold - 1
        bars = self._bars(symbol)
        if bars is None:
            return "no_hourly_bars"
        held = self._dates[symbol]
        if any(day not in held for day in self._days[d : last + 1]):
            return "missing_session_bars"
        if atr is None:
            daily = self.daily(symbol)
            atr = None if daily is None else atr_through(daily, self._days, d - 1, self._atr_window)
            if atr is None:
                return "no_atr"
        when = self.when(session)
        window = bars.between(when - timedelta(days=1), self._slice_end(last))
        price = decision_price(window, when)
        if price is None:
            return "no_decision_price"
        risk = self._stop_atr * atr
        if not (math.isfinite(risk) and risk > 0 and price - risk > 0):
            return "degenerate_levels"
        levels = SetupLevels("LONG", price, price - risk, price + _UNREACHABLE_TARGET_R * risk)
        outcome = label_bracket(
            levels, when, window, max_hold_sessions=hold, cost_bps_per_side=self._decisive, arm=IDDAY_ARM
        )
        # A fill after D is outside the window before it is immature: the slice ends near X, so a walk that
        # entered late cannot reach its own time exit.
        if outcome.entry_time is not None and _ny_date(outcome.entry_time) != session:
            return "hold_outside_window"
        if outcome.hit == BracketHit.IMMATURE:
            return "immature"
        r, fill = outcome.r, outcome.entry_price
        assert r is not None and fill is not None  # a resolved outcome has both
        # An exit after the close of X is outside the window, and so is a time exit on any day but X: the walk
        # counts sessions by bar dates, so a stray bar on a non-trading date would end the hold early.
        exit_day = _ny_date(outcome.exit_time)
        if exit_day > self._days[last] or (outcome.hit == BracketHit.TIMEOUT and exit_day != self._days[last]):
            return "hold_outside_window"
        risk_unit = abs(levels.entry - levels.stop)
        return {
            "decision_price": price,
            "entry_price": fill,
            "entry": levels.entry,
            "stop": levels.stop,
            "entry_time": outcome.entry_time,
            "exit_time": outcome.exit_time,
            "hit": str(outcome.hit),
            "r": r,
            "r_cost": outcome.r_cost,
            **{
                cost_column(cost): outcome.r_cost if cost == self._decisive else r - 2 * cost / 1e4 * fill / risk_unit
                for cost in self._costs
            },
            "holding_sessions": outcome.holding_sessions,
        }


def _net_excess(keys: list[_Key], labeller: _Labeller, inputs: IddayInputs) -> dict[_Key, dict[str, float]]:
    """``beta``, ``name_cost_bps``, ``market_r`` and ``net_excess_r`` per labelled cell, over its own hold."""
    if not keys:
        return {}
    rows = []
    for key in keys:
        cell = labeller.outcome(key)
        rows.append({"symbol": key[0], "session": key[1], **{c: cell[c] for c in _NET_EXCESS_INPUTS[2:]}})
    market = inputs.market_hourly
    regular = None if market is None or market.empty else regular_session_bars(market.set_axis(utc_index(market)))
    out = add_net_excess(
        pd.DataFrame(rows, columns=list(_NET_EXCESS_INPUTS)), inputs.adjusted, inputs.market_daily, regular
    )
    values = out[list(_NET_EXCESS_COLUMNS)].to_dict("records")
    return {key: {c: float(v) for c, v in value.items()} for key, value in zip(keys, values, strict=True)}


def _price(frame: pd.DataFrame | None, day: date, column: str) -> float:
    if frame is None or day not in frame.index:
        return math.nan
    value = float(frame.at[day, column])
    return value if math.isfinite(value) and value > 0 else math.nan


def _no_stop_excess(
    labeller: _Labeller, market: pd.DataFrame | None, event: Mapping[str, Any], cell: Mapping[str, Any], beta: float
) -> float:
    """``((C[X] - P) - beta x (SPY C[X] / SPY O[D] - 1) x P) / (entry - stop)``; ``NaN`` if any is missing.

    Section 10's buy-and-hold excess without the stop, in the row's R: ``C`` is the symbol's adjusted daily
    close, ``P`` the decision price (adjusted hourly bars, the same basis) and ``beta`` the row's trailing beta;
    SPY's span starts at its daily open on ``D``, its last daily price at or before the decision (a controller
    ruling, 2026-11-19; the close of ``D - 1`` would add the overnight gap that ``P`` has already absorbed). The
    return is converted to R as ``runup``'s last-day excess is, ``x P / (entry - stop)``. Descriptive only.
    """
    price, risk = float(cell["decision_price"]), float(cell["entry"]) - float(cell["stop"])
    stock = _price(labeller.daily(event["symbol"]), event["X"], "Close")
    spy = _price(market, event["X"], "Close") / _price(market, event["session"], "Open") - 1.0
    value = ((stock - price) - beta * spy * price) / risk
    return value if math.isfinite(value) else math.nan


def _check_event(labeller: _Labeller, event: Mapping[str, Any]) -> _Key:
    """The event's cell key; refuses a decision off the inputs' clock or an ``X`` that is not the ``H``-th session."""
    session, hold = event["session"], int(event["hold_sessions"])
    if labeller.when(session) != pd.Timestamp(event["decision_at"]):
        raise ValueError(f"{event['event_id']} decides at {event['decision_at']}, not at the inputs' clock")
    last = labeller.position(session) + hold - 1
    if hold < 1 or last >= len(labeller.days) or labeller.days[last] != event["X"]:
        raise ValueError(f"{event['event_id']}: X {event['X']} does not end the {hold}-session hold from {session}")
    return event["symbol"], session, hold


def _control_keys(match: Mapping[str, Any], event: Mapping[str, Any], k: int) -> list[_Key]:
    """The row's control cells, on its session and with its hold (``ValueError`` otherwise)."""
    session, hold = event["session"], int(event["hold_sessions"])
    if match["session"] != session:
        raise ValueError(f"{event['event_id']}: controls on {match['session']}, not on {session}")
    if int(match["hold_sessions"]) != hold:
        raise ValueError(f"{event['event_id']}: controls hold {match['hold_sessions']} sessions, not {hold}")
    keys = [(symbol, session, hold) for symbol in str(match["control_symbols"]).split(",")]
    if len(keys) != k:
        raise ValueError(f"{event['event_id']} has {len(keys)} controls, not {k}")
    return keys


class _Pairing:
    """Each labelled row's control cells: paired rows with their keys, unpaired rows by role, year and class."""

    def __init__(self) -> None:
        self.paired: list[tuple[Mapping[str, Any], list[_Key]]] = []
        self.keys: dict[_Key, None] = {}
        self.unpaired = {role: Counter[str]() for role in MATCHED_ROLES}
        self.by_year = {role: Counter[int]() for role in MATCHED_ROLES}
        self.by_class = {role: Counter[str]() for role in MATCHED_ROLES}

    def run(self, kept: list[tuple[dict, dict]], candidates: pd.DataFrame, labeller: _Labeller, k: int) -> None:
        by_key = {(row["pair_id"], row["role"]): row for row in candidates.to_dict("records")}
        for event, _ in kept:
            role = event["role"]
            if role not in MATCHED_ROLES:
                continue
            match = by_key.get((event["pair_id"], role))
            reason = "no_candidates"
            if match is not None:
                keys = _control_keys(match, event, k)
                self.keys.update(dict.fromkeys(keys))
                failures = [cell for cell in (labeller.cell(key) for key in keys) if isinstance(cell, str)]
                if not failures:
                    self.paired.append((match, keys))
                    continue
                reason = failures[0]  # nearest-first order: the first failing control names the reason
            self.unpaired[role][reason] += 1
            self.by_year[role][event["session"].year] += 1
            self.by_class[role][event["klass"]] += 1

    def counts(self, labeller: _Labeller) -> dict:
        failed = Counter(cell for key in self.keys if isinstance(cell := labeller.cells[key], str))
        return {
            "control_cells": {
                "labelled": len(self.keys) - sum(failed.values()),
                "reasons": dict(sorted(failed.items())),
            },
            "paired": {role: sum(match["role"] == role for match, _ in self.paired) for role in MATCHED_ROLES},
            "unpaired": {role: dict(sorted(c.items())) for role, c in self.unpaired.items()},
            "unpaired_by_year": {role: dict(sorted(c.items())) for role, c in self.by_year.items()},
            "unpaired_by_class": {role: dict(sorted(c.items())) for role, c in self.by_class.items()},
        }


def label_idday(
    events: pd.DataFrame, candidates: pd.DataFrame, inputs: IddayInputs, entry: IddayEntry
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    """``(labels, controls, control_cells, counts)``: labelled events, paired rows' controls, their cells, the funnel.

    ``labels`` has ``idday_label_columns(entry)``; ``controls`` has ``IDDAY_CONTROL_COLUMNS`` (the means over the
    ``matched_controls`` cells of a labelled row, labelled with its hold); ``control_cells`` has
    ``IDDAY_CONTROL_CELL_COLUMNS``, one row per cell of each paired row, nearest first. ``counts`` holds
    ``labelled`` by role, the event exclusions by reason, year and role, ``control_cells``, and
    ``paired``/``unpaired`` by role (unpaired by reason, decision year and class); per role ``labelled ==
    paired + sum(unpaired)``. Raises ``ValueError`` when an event decides off the inputs' clock, its ``X`` is not
    the ``hold_sessions``-th session from ``D``, or a candidate row has another session, hold or control count.
    """
    labeller = _Labeller(inputs, entry)
    skip = _Tally()
    kept: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for event in events.to_dict("records"):
        cell = labeller.cell(_check_event(labeller, event), float(event["atr"]))
        if isinstance(cell, str):
            skip(cell, event)
        else:
            kept.append((event, cell))
    pairing = _Pairing()
    pairing.run(kept, candidates, labeller, entry.control.matched_controls)

    event_keys = [(e["symbol"], e["session"], int(e["hold_sessions"])) for e, _ in kept]
    needed = event_keys + [key for _, keys in pairing.paired for key in keys]
    net = _net_excess(list(dict.fromkeys(needed)), labeller, inputs)
    market = None if inputs.market_daily is None or inputs.market_daily.empty else by_session(inputs.market_daily)
    rows = []
    for (event, cell), key in zip(kept, event_keys, strict=True):
        no_stop = _no_stop_excess(labeller, market, event, cell, net[key]["beta"])
        rows.append({**event, **cell, **net[key], "no_stop_excess_r": no_stop})
    labels = pd.DataFrame(rows, columns=list(idday_label_columns(entry)))

    cell_rows, control_rows = [], []
    for match, keys in pairing.paired:
        common = {"pair_id": match["pair_id"], "role": match["role"], "session": match["session"]}
        common["hold_sessions"] = int(match["hold_sessions"])
        outcomes = [labeller.outcome(key) for key in keys]
        for key, outcome in zip(keys, outcomes, strict=True):
            cell_rows.append(
                {
                    **common,
                    "symbol": key[0],
                    "hit": outcome["hit"],
                    "exit_time": outcome["exit_time"],
                    "r_cost": outcome["r_cost"],
                    "net_excess_r": net[key]["net_excess_r"],
                    "beta": net[key]["beta"],
                }
            )
        control_rows.append(
            {
                **common,
                "r_cost": float(np.mean([outcome["r_cost"] for outcome in outcomes])),
                "net_excess_r": float(np.mean([net[key]["net_excess_r"] for key in keys])),
                "beta": float(np.mean([net[key]["beta"] for key in keys])),
                "control_symbols": match["control_symbols"],
            }
        )
    counts = {
        "labelled": {role: sum(e["role"] == role for e, _ in kept) for role in MATCHED_ROLES},
        **skip.counts(),
        **pairing.counts(labeller),
    }
    return (
        labels,
        pd.DataFrame(control_rows, columns=list(IDDAY_CONTROL_COLUMNS)),
        pd.DataFrame(cell_rows, columns=list(IDDAY_CONTROL_CELL_COLUMNS)),
        counts,
    )


# --- evaluation (spec section 9) -------------------------------------------------------------------


class IddayDecision(StrEnum):
    """The only outcomes of the development read (spec section 1); none authorizes a probe or a card."""

    SURVIVES_DEVELOPMENT = "survives_development"
    FAILED = "failed"
    INCONCLUSIVE_UNDERPOWERED = "inconclusive_underpowered"


def _p1(rows: pd.DataFrame, entry: IddayEntry) -> dict:
    """``mean_test`` on the rows' finite ``net_excess_r``."""
    tested = _as_r_cost(rows, "net_excess_r")
    if tested.empty:
        return {"unit": "net_excess_r", "n": 0, "mean_r_cost": math.nan, "holds": False}
    return {"unit": "net_excess_r", **mean_test(tested, entry.bootstrap)}


def _p2(rows: pd.DataFrame, controls: pd.DataFrame, role: str, entry: IddayEntry) -> dict:
    """``paired_test`` of the rows' ``r_cost`` against their ``role`` control rows (pairs by ``pair_id``)."""
    leg, control = _paired(rows, controls, role, "r_cost")
    if leg.empty:
        return {"unit": "r_cost", "n_leg": 0, "n_control": 0, "mean_diff": math.nan, "holds": False}
    return {"unit": "r_cost", **paired_test(leg, control, entry.bootstrap)}


def _p5(labels: pd.DataFrame, controls: pd.DataFrame, entry: IddayEntry) -> dict:
    """P5: the legs' paired ``r_cost`` difference minus the placebos', from one joint session-block bootstrap."""
    legs = _paired(labels[labels["role"] == "leg"], controls, "leg", "r_cost")
    placebo = _paired(labels[labels["role"] == "placebo"], controls, "placebo", "r_cost")
    return {"unit": "r_cost", **difference_in_paired_test(*legs, *placebo, entry.bootstrap)}


def _issuer_cluster(legs: pd.DataFrame, controls: pd.DataFrame, entry: IddayEntry) -> dict:
    """P3's issuer test: each paired leg's ``r_cost`` against its control row's, issuers (``cik``) resampled whole."""
    own = controls.loc[controls["role"] == "leg", ["pair_id", "r_cost"]]
    pairs = legs[["pair_id", "cik", "r_cost"]].merge(
        own, on="pair_id", how="inner", suffixes=("", "_control"), validate="one_to_one"
    )
    result = cluster_paired_test(
        pd.DataFrame({"r_cost": _numbers(pairs, "r_cost")}),
        pd.DataFrame({"r_cost": _numbers(pairs, "r_cost_control")}),
        pairs["cik"],
        entry.cluster_bootstrap,
    )
    return {"unit": "r_cost", "clusters": "cik", **result}


def planted_check(labels: pd.DataFrame, controls: pd.DataFrame, entry: IddayEntry) -> dict:
    """Spec section 9: whether P1 and P2 detect half the published effect planted in the placebos.

    Every paired placebo row (one with a ``placebo`` control row) gains ``planted_return x decision_price /
    |entry - stop|`` R in both ``r_cost`` and ``net_excess_r``; its controls are unchanged. ``detected`` needs
    P1 and P2 to hold on the shifted rows. ``mde`` is the (unshifted) placebo P2's minimum detectable effect,
    ``(1.645 + 0.84) x`` the standard deviation of its bootstrap draws.
    """
    placebo = labels[labels["role"] == "placebo"]
    paired = placebo[placebo["pair_id"].isin(set(controls.loc[controls["role"] == "placebo", "pair_id"]))]
    risk = (pd.to_numeric(paired["entry"]) - pd.to_numeric(paired["stop"])).abs()
    shift = entry.placebo.planted_return * pd.to_numeric(paired["decision_price"]) / risk
    planted = paired.assign(
        r_cost=pd.to_numeric(paired["r_cost"]) + shift,
        net_excess_r=pd.to_numeric(paired["net_excess_r"]) + shift,
    )
    p1 = _p1(planted, entry)
    p2 = _p2(planted, controls, "placebo", entry)
    mde = _mde(_p2(placebo, controls, "placebo", entry))
    shift_values = shift.to_numpy(float)
    return {
        "planted_return": entry.placebo.planted_return,
        "n_paired": len(paired),
        "shift_r": {
            **_summary(shift_values),
            "min": float(shift_values.min()) if shift_values.size else math.nan,
            "max": float(shift_values.max()) if shift_values.size else math.nan,
        },
        "P1": p1,
        "P2": p2,
        "detected": bool(p1["holds"] and p2["holds"]),
        "mde": mde,
    }


def _criteria(legs: pd.DataFrame, controls: pd.DataFrame, entry: IddayEntry) -> dict:
    """P1-P4 on the legs (spec section 9); P5 needs the placebos too (``_p5``)."""
    finite = _as_r_cost(legs, "net_excess_r")
    recent_from = entry.window.recent_from
    recent = finite[np.array([session >= recent_from for session in finite["session"]], dtype=bool)]
    trimmed = trimmed_mean(finite["r_cost"].to_numpy(float), entry.pass_rule.trim_fraction)
    recent_mean = float(recent["r_cost"].mean()) if len(recent) else math.nan
    cluster = _issuer_cluster(legs, controls, entry)
    rule = entry.pass_rule
    return {
        "P1": _p1(legs, entry),
        "P2": _p2(legs, controls, "leg", entry),
        "P3": {
            "unit": "net_excess_r",
            "trim_fraction": rule.trim_fraction,
            "trimmed_mean": trimmed,
            "recent_from": recent_from.isoformat(),
            "recent_mean": recent_mean,
            "issuer_cluster": cluster,
            "holds": bool(
                math.isfinite(trimmed)
                and trimmed > 0
                and math.isfinite(recent_mean)
                and recent_mean > 0
                and cluster["holds"]
            ),
        },
        "P4": {
            "n": len(finite),
            "n_recent": len(recent),
            "min_events": rule.min_events,
            "min_recent_events": rule.min_recent_events,
            "holds": len(finite) >= rule.min_events and len(recent) >= rule.min_recent_events,
        },
    }


def _decision(planted: Mapping[str, Any], criteria: Mapping[str, Any], placebo: Mapping[str, Any]) -> IddayDecision:
    if not planted["detected"]:
        return IddayDecision.INCONCLUSIVE_UNDERPOWERED
    if not all(criterion["holds"] for criterion in criteria.values()) or placebo["P2"]["holds"]:
        return IddayDecision.FAILED
    return IddayDecision.SURVIVES_DEVELOPMENT


# --- descriptive diagnostics (spec section 10) -------------------------------------------------------


def _split(frame: pd.DataFrame) -> dict:
    return {
        "net_excess_r": _summary(frame["net_excess_r"]),
        "r_cost": _summary(frame["r_cost"]),
        "paired_diff_r_cost": _summary(frame["paired_diff_r_cost"]),
    }


def _by(frame: pd.DataFrame, keys: list) -> dict:
    """``_split`` per distinct key, in key order (no rows, no keys)."""
    values = np.array(keys, dtype=object)
    return {key: _split(frame[values == key]) for key in sorted(set(keys))}


def _keyword(value: Any) -> str:
    return " ".join(value.lower().split()) if isinstance(value, str) else "none"


def _hold_terciles(legs: pd.DataFrame) -> dict:
    """Legs by tercile of ``hold_sessions`` (ascending, rank ``r`` of ``n`` in ``floor(3r / n)``, stable)."""
    values = _numbers(legs, "hold_sessions")
    finite = np.flatnonzero(np.isfinite(values))
    terciles: dict[str, dict] = {}
    if finite.size >= 3:
        order = finite[np.argsort(values[finite], kind="stable")]
        group = np.arange(order.size) * 3 // order.size
        for k, name in enumerate(("low", "mid", "high")):
            members = order[group == k]
            span = [int(values[members].min()), int(values[members].max())]
            terciles[name] = {**_split(legs.iloc[members]), "hold_range": span}
    return {"n_missing": len(legs) - int(finite.size), "terciles": terciles}


def _concentration(legs: pd.DataFrame) -> dict:
    """The legs without the five symbols with the most legs (ties by symbol), the first leg per symbol, issuers."""
    by_symbol = Counter(legs["symbol"])
    top = [symbol for symbol, _ in sorted(by_symbol.items(), key=lambda item: (-item[1], item[0]))[:_TOP_SYMBOLS]]
    first = legs.sort_values(["session", "event_id"], kind="stable").drop_duplicates("symbol")
    by_issuer = Counter(legs["cik"])
    return {
        "excluding_top_symbols": {"symbols": top, **_split(legs[~legs["symbol"].isin(top)])},
        "first_leg_per_symbol": _split(first),
        "issuers": {"n": len(by_issuer), "max_legs": max(by_issuer.values(), default=0)},
    }


def _diagnostics(labels: pd.DataFrame, controls: pd.DataFrame, entry: IddayEntry) -> dict:
    """Spec section 10; nothing here decides anything (the placebo's P1 is reported under ``placebo``).

    Every split reports the legs' ``net_excess_r``, ``r_cost`` and ``paired_diff_r_cost`` (a paired leg's
    ``r_cost`` minus its control row's, ``NaN`` for an unpaired leg). The pipeline counts need the events and
    the inputs; the executor adds them as ``pipeline``.
    """
    own = controls[controls["role"] == "leg"]
    control_r = dict(zip(own["pair_id"], _numbers(own, "r_cost"), strict=True))
    legs = labels[labels["role"] == "leg"]
    matched = np.array([control_r.get(pair, math.nan) for pair in legs["pair_id"]], dtype=float)
    legs = legs.assign(paired_diff_r_cost=_numbers(legs, "r_cost") - matched)
    placebo = labels[labels["role"] == "placebo"]
    return {
        "legs": _split(legs),
        "unpaired_legs": int((~legs["pair_id"].isin(set(control_r))).sum()),
        "by_class": {klass: _split(legs[legs["klass"] == klass]) for klass in _CLASSES},
        "by_year": _by(legs, [session.year for session in legs["session"]]),
        "by_band": _by(legs, [str(band) for band in legs["band"]]),
        "by_keyword": _by(legs, [_keyword(keyword) for keyword in legs["keyword"]]),
        "by_hold_tercile": _hold_terciles(legs),
        "by_bucket": _by(legs, [int(bucket) for bucket in legs["bucket"]]),
        "concentration": _concentration(legs),
        "no_stop_excess": {
            "definition": _NO_STOP_DEFINITION,
            "legs": _summary(legs["no_stop_excess_r"]),
            "placebo": _summary(placebo["no_stop_excess_r"]),
            "by_class": {k: _summary(legs.loc[legs["klass"] == k, "no_stop_excess_r"]) for k in _CLASSES},
        },
        "exits": {
            "hits": {str(hit): int(n) for hit, n in legs["hit"].value_counts().sort_index().items()},
            "by_hit": {str(hit): _split(group) for hit, group in legs.groupby("hit")},
            "mean_holding_sessions": _summary(legs["holding_sessions"])["mean"],
            "mean_hold_sessions": _summary(legs["hold_sessions"])["mean"],
        },
        "costs": {cost_column(cost): _summary(legs[cost_column(cost)]) for cost in entry.costs_bps_per_side},
        "dispersion": _dispersion(legs),
    }


def evaluate_idday(
    labels: pd.DataFrame, controls: pd.DataFrame, entry: IddayEntry, *, planted: dict | None = None
) -> dict:
    """The planted check, the placebo, the frozen P1-P5 rule, the decision and the diagnostics (spec 9-10).

    ``planted`` is the executor's already saved ``planted_check`` result; without it the check is computed
    here, still before the legs' statistics.
    """
    if planted is None:
        planted = planted_check(labels, controls, entry)
    placebo_rows = labels[labels["role"] == "placebo"]
    placebo_p2 = _p2(placebo_rows, controls, "placebo", entry)
    placebo = {
        "P1": _p1(placebo_rows, entry),
        "P2": placebo_p2,
        "negative_control_fails": bool(placebo_p2["holds"]),
    }
    criteria = {**_criteria(labels[labels["role"] == "leg"], controls, entry), "P5": _p5(labels, controls, entry)}
    return {
        "decision": _decision(planted, criteria, placebo).value,
        "criteria": criteria,
        "placebo": placebo,
        "planted": planted,
        "diagnostics": _diagnostics(labels, controls, entry),
    }


# --- the executor (spec section 11) -----------------------------------------------------------------


def _verified_cube(inputs: IddayInputs, entry: IddayEntry) -> str:
    """The merged cube's own SHA-256, after the runner's part digests are checked against the pinned cohorts."""
    provenance = inputs.provenance
    for cohort in entry.cohorts:
        key = f"cube_sha256_{cohort.name}"
        if provenance.get(key) != cohort.cube_sha256:
            raise ValueError(
                f"the runner's {key} is {provenance.get(key)}, but the entry pins cohort {cohort.name}'s "
                f"cube_sha256 {cohort.cube_sha256}"
            )
    digest = inputs.cube.sha256
    recorded = provenance.get("merged_cube_sha256")
    if digest != recorded:
        raise ValueError(f"the merged cube has SHA-256 {digest}, but the runner recorded merged_cube_sha256 {recorded}")
    return digest


def _verified_events(inputs: IddayInputs, entry: IddayEntry) -> None:
    """Spec section 6 (ruling R11): the event table's own digest is the pinned one and its parser check passed.

    ``provenance["parser_check"]`` is the runner's ``validate_parser_check`` result: a mapping with an integer
    ``errors`` (non-negative, at most ``max_errors``) and the entry's ``sample_size``.
    """
    events_sha256 = inputs.events.events_sha256
    if events_sha256 != entry.investor_days.events_sha256:
        raise ValueError(
            f"the event table has events_sha256 {events_sha256}, but the entry pins {entry.investor_days.events_sha256}"
        )
    check, rules = inputs.provenance.get("parser_check"), entry.parser_check
    if not isinstance(check, Mapping):
        raise ValueError("the runner recorded no parser_check result")  # noqa: TRY004 -- every refusal is a ValueError
    errors = check.get("errors")
    if type(errors) is not int or errors < 0:
        raise ValueError(f"the recorded parser_check has no integer error count ({errors!r})")
    if check.get("sample_size") != rules.sample_size:
        raise ValueError(
            f"the recorded parser_check sampled {check.get('sample_size')!r}, not the entry's {rules.sample_size}"
        )
    if errors > rules.max_errors:
        raise ValueError(f"the recorded parser_check has {errors} errors, above the frozen limit of {rules.max_errors}")


def _pipeline_counts(
    event_counts: Mapping[str, Any],
    label_counts: Mapping[str, Any],
    events: pd.DataFrame,
    labels: pd.DataFrame,
    inputs: IddayInputs,
) -> dict:
    """Section 10's pipeline counts: events, exclusions by stage, symbols with a leg, Item 2.02, acquisition."""
    candidates = event_counts.get("candidates", {})
    legs = events[events["role"] == "leg"]
    flags = [bool(value) for value in legs["item_2_02"] if isinstance(value, bool | np.bool_)]
    records = list(inputs.issuers.values())
    failures: Counter[str] = Counter()
    for record in records:
        failures.update(record.doc_failures)
    mapped = set(inputs.issuer_of)
    return {
        "events": candidates.get("events"),
        "event_only": candidates.get("event_only"),
        "exclusions": {
            "price_free": candidates.get("reasons", {}),
            "price_gates": event_counts.get("reasons", {}),
            "placebo_draws": event_counts.get("placebo_reasons", {}),
            "labels": label_counts.get("reasons", {}),
        },
        "symbols_with_leg": int(legs["symbol"].nunique()),
        "symbols_with_labelled_leg": int(labels.loc[labels["role"] == "leg", "symbol"].nunique()),
        "item_2_02": {
            "n": len(flags),
            "n_true": sum(flags),
            "share": sum(flags) / len(flags) if flags else math.nan,
        },
        "acquisition": {
            "issuers": len(records),
            "visible": sum(record.visible for record in records),
            "capped": sum(record.capped for record in records),
            "capped_ciks": sorted(record.cik for record in records if record.capped),
            "with_document_failures": sum(bool(record.doc_failures) for record in records),
            "document_failures": dict(sorted(failures.items())),
            "unmapped_symbols": sum(symbol not in mapped for symbol in inputs.cube.symbols),
        },
    }


async def execute_idday_study(
    loaded: LoadedIddayEntry,
    directory: Path,
    *,
    build: Callable[[], Awaitable[IddayInputs]],
    environment: dict,
) -> dict:
    entry = loaded.entry
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    save_json_report({**entry.model_dump(mode="json"), "sha256": loaded.sha256}, directory / "protocol.json")
    save_json_report(
        {
            "entry_id": entry.id,
            "version": entry.version,
            "sha256": loaded.sha256,
            "path": str(loaded.path),
            "pinned": {
                "cohorts": [cohort.model_dump(mode="json") for cohort in entry.cohorts],
                "investor_days": entry.investor_days.model_dump(mode="json"),
                "parser_check": entry.parser_check.model_dump(mode="json"),
            },
            "environment": environment,
            "started_at": datetime.now(UTC).isoformat(),
            "authorizes_promotion": False,
        },
        directory / "manifest.json",
    )
    try:
        inputs = await build()
        digest = await asyncio.to_thread(_verified_cube, inputs, entry)
        await asyncio.to_thread(_verified_events, inputs, entry)
        events, event_counts = await asyncio.to_thread(build_idday_events, inputs, entry)
        candidates, matching_counts = await asyncio.to_thread(match_controls, events, inputs, entry)
        labelled = await asyncio.to_thread(label_idday, events, candidates, inputs, entry)
        labels, controls, control_cells, label_counts = labelled
        frames = {
            "events": events,
            "labels": labels,
            "controls": controls,
            "control_cells": control_cells,
            "placebo": labels[labels["role"] == "placebo"],
        }
        for name, frame in frames.items():
            await asyncio.to_thread(save_frame, frame, directory / f"{name}.csv.gz")
        # Spec section 9: the planted check (with the placebo P2's minimum detectable effect) is computed and
        # saved before any leg statistic.
        planted = await asyncio.to_thread(planted_check, labels, controls, entry)
        save_json_report(_finite_json(planted), directory / "planted.json")
        evaluation = await asyncio.to_thread(evaluate_idday, labels, controls, entry, planted=planted)
        evaluation["diagnostics"]["pipeline"] = _pipeline_counts(event_counts, label_counts, events, labels, inputs)
        result = {
            "status": "completed",
            **evaluation,
            "events": event_counts,
            "matching": matching_counts,
            "labels": label_counts,
            "inputs": {**inputs.provenance, "cube_sha256": digest},
            "authorizes_promotion": False,
        }
    except Exception as exc:
        result = _failure(exc)
    try:
        save_json_report(_finite_json(result), directory / "result.json")
    except (TypeError, ValueError) as exc:  # a result that cannot be encoded is still recorded, as a failure
        result = _failure(exc)
        save_json_report(result, directory / "result.json")
    return result
