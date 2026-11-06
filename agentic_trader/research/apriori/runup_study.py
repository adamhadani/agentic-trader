"""Pre-earnings run-up labels, matched-control rows and net excess (entry ``runup``).

Spec section 5 (trade and labels), section 6's control labels and section 8's last-day excess
(``docs/superpowers/specs/2026-11-06-apriori-runup-design.md``). Every row is one LONG cell ``(symbol, D)``:
the decision price is the close of the last regular hourly bar ending by the decision clock on ``D``
(``pead_study.decision_price``), the stop ``stop_atr_multiple`` x ATR as of ``D - 1`` below it, and
``label_bracket`` walks the regular-session bars with ``RUNUP_ARM`` (no target, no trail) to the close of the
``lead_sessions``-th session, ``E - 1`` for a leg. One walk at the decisive cost gives every cost column (the
labeller's own ``r - 2 c / 1e4 * fill / risk`` per cost).

Exclusions are counted by reason, decision year and role: ``no_hourly_bars``, ``missing_session_bars`` (an
observed session of ``[D, D + lead - 1]`` without a regular hourly bar), ``no_decision_price``,
``degenerate_levels``, ``immature`` and, for rows with a report (legs and announcers),
``exit_on_or_after_report`` (the walk ends on or after ``E``, for example when ``D`` has no regular bar after
the decision and the fill moves to ``D + 1``).

Control cells are labelled the same way on their own bars, with ATR through ``D - 1`` from their adjusted
daily bars (``no_atr`` otherwise); each distinct ``(symbol, session)`` cell, event or control, is labelled
once and shared. A labelled leg or placebo's control is the mean ``r_cost`` and ``net_excess_r`` of its
``matched_controls`` cells; any failing cell leaves it unpaired, counted by role under the first failing
cell's reason in nearest-first order (``no_candidates`` when ``match_controls`` found too few names). Every
labelled cell gets ``net_excess_r_v1`` through ``pead_base_rate.add_net_excess`` (a missing input gives
``NaN``, never a fill). Research only: nothing here reads a database, a broker or the network.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.apriori.catalog import RunupEntry
from agentic_trader.research.apriori.exdiv_study import _regular_session_dates
from agentic_trader.research.apriori.pead_base_rate import add_net_excess
from agentic_trader.research.apriori.pead_events import atr_through, by_session, decision_at
from agentic_trader.research.apriori.pead_study import decision_price, utc_index
from agentic_trader.research.apriori.runup_events import MATCHED_ROLES, RUNUP_EVENT_COLUMNS, RunupInputs
from agentic_trader.research.pooled.cube import _Hourly
from agentic_trader.research.setups.labels import (
    BracketArm,
    BracketHit,
    SetupLevels,
    label_bracket,
    regular_session_bars,
)


__all__ = ["RUNUP_ARM", "RUNUP_CONTROL_COLUMNS", "cost_column", "label_runup", "runup_label_columns"]

RUNUP_ARM = BracketArm(name="runup_time_exit", target_r=None, trail=None)
RUNUP_CONTROL_COLUMNS = ("pair_id", "role", "session", "r_cost", "net_excess_r", "control_symbols")
_ROLES = ("leg", "announcer", "placebo")
_OUTCOME_COLUMNS = ("decision_price", "entry_price", "entry", "stop", "entry_time", "exit_time", "hit", "r", "r_cost")
_NET_EXCESS_COLUMNS = ("beta", "name_cost_bps", "market_r", "net_excess_r")
_NET_EXCESS_INPUTS = ("symbol", "session", "entry_price", "entry", "stop", "entry_time", "exit_time", "r")
# ``SetupLevels`` needs a target above the entry; ``RUNUP_ARM`` drops it, so this one is never walked.
_UNREACHABLE_TARGET_R = 1000.0

_Cell = dict[str, Any] | str  # the labelled outcome of a cell, or the reason it failed


def cost_column(cost: float) -> str:
    return f"r_cost_{cost:g}bps"


def runup_label_columns(entry: RunupEntry) -> tuple[str, ...]:
    return (
        *RUNUP_EVENT_COLUMNS,
        *_OUTCOME_COLUMNS,
        *(cost_column(cost) for cost in entry.costs_bps_per_side),
        "holding_sessions",
        *_NET_EXCESS_COLUMNS,
        "last_day_excess",
    )


class _Labeller:
    """Labels each ``(symbol, session)`` cell once; every symbol's bars are prepared once."""

    def __init__(self, inputs: RunupInputs, entry: RunupEntry):
        self._inputs = inputs
        self._days = tuple(inputs.trading_days)
        self._pos = {day: i for i, day in enumerate(self._days)}
        self._clock = inputs.decision_time
        self._lead = entry.event.lead_sessions
        self._stop_atr = entry.trade.stop_atr_multiple
        self._atr_window = entry.trade.atr_window
        self._decisive = entry.decision_cost_bps
        self._costs = entry.costs_bps_per_side
        self._hourly: dict[str, _Hourly | None] = {}
        self._dates: dict[str, frozenset[date]] = {}
        self._daily: dict[str, pd.DataFrame | None] = {}
        self.cells: dict[tuple[str, date], _Cell] = {}
        self._labelled: dict[tuple[str, date], dict[str, Any]] = {}

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

    def _bars(self, symbol: str) -> _Hourly | None:
        """The symbol's regular-session hourly bars, sorted and de-duplicated once (``None`` without bars)."""
        if symbol not in self._hourly:
            frame = self._inputs.hourly.get(symbol)
            if frame is None or frame.empty:
                self._hourly[symbol], self._dates[symbol] = None, frozenset()
            else:
                self._hourly[symbol] = _Hourly(regular_session_bars(frame.set_axis(utc_index(frame))))
                self._dates[symbol] = _regular_session_dates(frame)
        return self._hourly[symbol]

    def outcome(self, key: tuple[str, date]) -> dict[str, Any]:
        """A cell already labelled successfully (``KeyError`` otherwise)."""
        return self._labelled[key]

    def cell(self, symbol: str, session: date, atr: float | None = None) -> _Cell:
        """The cell's outcome, labelled on first request (``atr`` from the events frame, else computed)."""
        key = (symbol, session)
        if key not in self.cells:
            cell = self.cells[key] = self._label(symbol, session, atr)
            if not isinstance(cell, str):
                self._labelled[key] = cell
        return self.cells[key]

    def _label(self, symbol: str, session: date, atr: float | None) -> _Cell:
        d = self.position(session)
        bars = self._bars(symbol)
        if bars is None:
            return "no_hourly_bars"
        held = self._dates[symbol]
        if any(day not in held for day in self._days[d : d + self._lead]):
            return "missing_session_bars"
        if atr is None:
            daily = self.daily(symbol)
            atr = None if daily is None else atr_through(daily, self._days, d - 1, self._atr_window)
            if atr is None:
                return "no_atr"
        when = self.when(session)
        window = bars.around(when)
        price = decision_price(window, when)
        if price is None:
            return "no_decision_price"
        risk = self._stop_atr * atr
        if not (math.isfinite(risk) and risk > 0 and price - risk > 0):
            return "degenerate_levels"
        levels = SetupLevels("LONG", price, price - risk, price + _UNREACHABLE_TARGET_R * risk)
        outcome = label_bracket(
            levels, when, window, max_hold_sessions=self._lead, cost_bps_per_side=self._decisive, arm=RUNUP_ARM
        )
        if outcome.hit == BracketHit.IMMATURE:
            return "immature"
        r, fill = outcome.r, outcome.entry_price
        assert r is not None and fill is not None  # a resolved outcome has both
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


def _net_excess(
    keys: list[tuple[str, date]], labeller: _Labeller, inputs: RunupInputs
) -> dict[tuple[str, date], dict[str, float]]:
    """``beta``, ``name_cost_bps``, ``market_r`` and ``net_excess_r`` per labelled cell (``add_net_excess``)."""
    if not keys:
        return {}
    rows = []
    for symbol, session in keys:
        cell = labeller.outcome((symbol, session))
        rows.append({"symbol": symbol, "session": session, **{c: cell[c] for c in _NET_EXCESS_INPUTS[2:]}})
    market = inputs.market_hourly
    # market_r keeps only regular-session bars; filtering once up front is the same proxy, cheaper per row.
    regular = None if market is None or market.empty else regular_session_bars(market.set_axis(utc_index(market)))
    out = add_net_excess(
        pd.DataFrame(rows, columns=list(_NET_EXCESS_INPUTS)), inputs.adjusted, inputs.market_daily, regular
    )
    values = out[list(_NET_EXCESS_COLUMNS)].to_dict("records")
    return {key: {c: float(v) for c, v in value.items()} for key, value in zip(keys, values, strict=True)}


def _close(frame: pd.DataFrame | None, day: date) -> float:
    if frame is None or day not in frame.index:
        return math.nan
    value = float(frame.at[day, "Close"])
    return value if math.isfinite(value) and value > 0 else math.nan


def _last_day_excess(
    labeller: _Labeller, market: pd.DataFrame | None, event: Mapping[str, Any], cell: Mapping[str, Any], beta: float
) -> float:
    """``(C[E-1] / C[E-2] - 1 - beta * (SPY's same)) * decision_price / (entry - stop)``; ``NaN`` if any is missing."""
    e = labeller.position(event["report_date"])
    if e < 2:
        return math.nan
    days = labeller.days
    daily = labeller.daily(event["symbol"])
    stock = _close(daily, days[e - 1]) / _close(daily, days[e - 2]) - 1.0
    spy = _close(market, days[e - 1]) / _close(market, days[e - 2]) - 1.0
    value = (stock - beta * spy) * cell["decision_price"] / (cell["entry"] - cell["stop"])
    return value if math.isfinite(value) else math.nan


class _Tally:
    def __init__(self) -> None:
        self.reasons: Counter[str] = Counter()
        self.by_year: dict[str, Counter[int]] = defaultdict(Counter)
        self.by_role: dict[str, Counter[str]] = defaultdict(Counter)

    def __call__(self, reason: str, event: Mapping[str, Any]) -> None:
        self.reasons[reason] += 1
        self.by_year[reason][event["session"].year] += 1
        self.by_role[reason][event["role"]] += 1

    def counts(self) -> dict[str, Any]:
        return {
            "reasons": dict(sorted(self.reasons.items())),
            "reasons_by_year": {r: dict(sorted(y.items())) for r, y in sorted(self.by_year.items())},
            "reasons_by_role": {r: dict(sorted(y.items())) for r, y in sorted(self.by_role.items())},
        }


def _report_exit(event: Mapping[str, Any], cell: Mapping[str, Any]) -> bool:
    """Whether a row with a report (a leg or an announcer) exits on or after its report session."""
    report = event["report_date"]
    return isinstance(report, date) and pd.Timestamp(cell["exit_time"]).tz_convert(ET_TZ).date() >= report


def label_runup(
    events: pd.DataFrame, candidates: pd.DataFrame, inputs: RunupInputs, entry: RunupEntry
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """``(labels, controls, counts)``: one row per labelled event, one per paired leg or placebo, the funnel.

    ``labels`` has ``runup_label_columns(entry)``; ``controls`` has ``RUNUP_CONTROL_COLUMNS`` (the means over
    the ``matched_controls`` cells of a labelled row); ``counts`` holds ``labelled`` by role, the event
    exclusions by reason, year and role, ``control_cells`` (distinct control cells labelled and failed by
    reason), and ``paired``/``unpaired`` by role (unpaired by reason and by decision year). The funnel adds
    up per matched role: ``labelled == paired + sum(unpaired)``; an event that fails its own label is an
    exclusion, never an unpaired row. Raises ``ValueError`` when an event's ``decision_at`` is not the inputs'
    clock on its session, or a candidate row is on another session or lists other than ``matched_controls``
    names.
    """
    labeller = _Labeller(inputs, entry)
    skip = _Tally()
    kept: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for event in events.to_dict("records"):
        if labeller.when(event["session"]) != pd.Timestamp(event["decision_at"]):
            raise ValueError(f"{event['event_id']} decides at {event['decision_at']}, not at the inputs' clock")
        cell = labeller.cell(event["symbol"], event["session"], float(event["atr"]))
        if isinstance(cell, str):
            skip(cell, event)
        elif _report_exit(event, cell):
            skip("exit_on_or_after_report", event)
        else:
            kept.append((event, cell))

    k = entry.control.matched_controls
    by_key = {(row["pair_id"], row["role"]): row for row in candidates.to_dict("records")}
    paired: list[tuple[Mapping[str, Any], list[tuple[str, date]]]] = []
    unpaired: dict[str, Counter[str]] = {role: Counter() for role in MATCHED_ROLES}
    unpaired_by_year: dict[str, Counter[int]] = {role: Counter() for role in MATCHED_ROLES}
    control_keys: dict[tuple[str, date], None] = {}
    for event, _ in kept:
        role = event["role"]
        if role not in MATCHED_ROLES:
            continue
        match = by_key.get((event["pair_id"], role))
        reason = "no_candidates"
        if match is not None:
            if match["session"] != event["session"]:
                raise ValueError(f"{event['event_id']}: controls on {match['session']}, not on {event['session']}")
            keys = [(symbol, event["session"]) for symbol in str(match["control_symbols"]).split(",")]
            if len(keys) != k:
                raise ValueError(f"{event['event_id']} has {len(keys)} controls, not {k}")
            control_keys.update(dict.fromkeys(keys))
            failures = [cell for cell in (labeller.cell(*key) for key in keys) if isinstance(cell, str)]
            if not failures:
                paired.append((match, keys))
                continue
            reason = failures[0]  # nearest-first order: the first failing control names the reason
        unpaired[role][reason] += 1
        unpaired_by_year[role][event["session"].year] += 1

    needed = [(e["symbol"], e["session"]) for e, _ in kept] + [key for _, keys in paired for key in keys]
    net = _net_excess(list(dict.fromkeys(needed)), labeller, inputs)
    market = None if inputs.market_daily is None or inputs.market_daily.empty else by_session(inputs.market_daily)

    rows = []
    for event, cell in kept:
        extra = net[(event["symbol"], event["session"])]
        report = isinstance(event["report_date"], date)
        last_day = _last_day_excess(labeller, market, event, cell, extra["beta"]) if report else math.nan
        rows.append({**event, **cell, **extra, "last_day_excess": last_day})
    labels = pd.DataFrame(rows, columns=list(runup_label_columns(entry)))

    control_rows = [
        {
            "pair_id": match["pair_id"],
            "role": match["role"],
            "session": match["session"],
            "r_cost": float(np.mean([labeller.outcome(key)["r_cost"] for key in keys])),
            "net_excess_r": float(np.mean([net[key]["net_excess_r"] for key in keys])),
            "control_symbols": match["control_symbols"],
        }
        for match, keys in paired
    ]
    controls = pd.DataFrame(control_rows, columns=list(RUNUP_CONTROL_COLUMNS))

    failed = Counter(cell for key in control_keys if isinstance(cell := labeller.cells[key], str))
    counts = {
        "labelled": {role: sum(e["role"] == role for e, _ in kept) for role in _ROLES},
        **skip.counts(),
        "control_cells": {
            "labelled": len(control_keys) - sum(failed.values()),
            "reasons": dict(sorted(failed.items())),
        },
        "paired": {role: sum(match["role"] == role for match, _ in paired) for role in MATCHED_ROLES},
        "unpaired": {role: dict(sorted(reasons.items())) for role, reasons in unpaired.items()},
        "unpaired_by_year": {role: dict(sorted(years.items())) for role, years in unpaired_by_year.items()},
    }
    return labels, controls, counts
