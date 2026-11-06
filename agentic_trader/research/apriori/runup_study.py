"""Pre-earnings run-up labels, controls, net excess, the frozen rule and its executor (entry ``runup``).

Spec section 5 (trade and labels), section 6's control labels and section 8's last-day excess
(``docs/superpowers/specs/2026-11-06-apriori-runup-design.md``). Every row is one LONG cell ``(symbol, D)``:
the decision price is the close of the last regular hourly bar ending by the decision clock on ``D``
(``pead_study.decision_price``), the stop ``stop_atr_multiple`` x ATR as of ``D - 1`` below it, and
``label_bracket`` walks the regular-session bars with ``RUNUP_ARM`` (no target, no trail) to the close of the
``lead_sessions``-th session, ``E - 1`` for a leg. One walk at the decisive cost gives every cost column (the
labeller's own ``r - 2 c / 1e4 * fill / risk`` per cost).

Exclusions are counted by reason, decision year and role: ``no_hourly_bars``, ``missing_session_bars`` (an
observed session of ``[D, D + lead - 1]`` without a regular hourly bar), ``no_decision_price``,
``degenerate_levels``, ``immature`` and ``hold_outside_window`` (the fill is not on ``D`` or the exit is after
the close of ``D + lead - 1``, for example when ``D`` has no regular bar after the decision and the fill moves
to ``D + 1``). The last applies to every cell, leg, announcer, placebo or control, so no labelled leg holds
through its report and no control or placebo holds outside its report-free band.

Control cells are labelled the same way on their own bars, with ATR through ``D - 1`` from their adjusted
daily bars (``no_atr`` otherwise); each distinct ``(symbol, session)`` cell, event or control, is labelled
once and shared. A labelled leg or placebo's control is the mean ``r_cost``, ``net_excess_r`` and ``beta`` of
its ``matched_controls`` cells, whose own outcomes are returned too (``RUNUP_CONTROL_CELL_COLUMNS``); any
failing cell leaves it unpaired, counted by role under the first failing cell's reason in nearest-first order
(``no_candidates`` when ``match_controls`` found too few names). Every labelled cell gets ``net_excess_r_v1``
through ``pead_base_rate.add_net_excess`` (a missing input gives ``NaN``, never a fill); every labelled event
row gets ``hold_max_excess_rv``, its largest absolute daily excess return over the hold in units of ``rv``.

Evaluation (spec section 7) reuses the shared session-block statistics, which read a column named
``r_cost``: each test passes its unit under that name and records it as ``unit``. P1 is ``mean_test`` on the
legs' finite ``net_excess_r``; P2 is ``paired_test`` on the paired legs' ``r_cost`` against their control rows,
keyed by session; P3 is the trimmed mean of the legs' finite ``net_excess_r`` and its plain mean from
``recent_from``; P4 counts the legs with a finite ``net_excess_r`` overall and from ``recent_from``; P5 is
``difference_in_paired_test``, the legs' P2 difference minus the placebos', from one joint bootstrap over the
union of their sessions. The placebo runs the same P1 and P2 on ``role == "placebo"`` rows and their controls;
its P2 holding fails the read. The planted check adds ``planted_return x decision_price / |entry - stop|`` to
every paired placebo's ``r_cost`` and ``net_excess_r`` (controls unchanged) and needs both P1 and P2 to hold,
or the read is ``inconclusive_underpowered``; it also states the placebo P2's minimum detectable effect and
is computed before the legs' statistics. Diagnostics (spec section 8) decide nothing.

``execute_runup_study`` writes ``protocol.json`` and ``manifest.json`` before ``build`` (the only provider
access), hashes the cube it was given and refuses one whose SHA-256 differs from the entry's ``cube_sha256``
(recording that digest in the result's ``inputs``), saves the frames (``control_cells`` included), then
``planted.json`` before any leg statistic, then ``result.json``; any failure is ``status: failed``. Research
only: nothing here reads a database, a broker or the network, and nothing authorizes promotion.
"""

from __future__ import annotations

import asyncio
import math
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from agentic_trader.agent.earnings import EarningsTiming
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.apriori.catalog import LoadedRunupEntry, RunupEntry
from agentic_trader.research.apriori.exdiv_study import _regular_session_dates
from agentic_trader.research.apriori.pead_base_rate import add_net_excess
from agentic_trader.research.apriori.pead_events import atr_through, by_session, decision_at
from agentic_trader.research.apriori.pead_study import decision_price, save_frame, utc_index
from agentic_trader.research.apriori.runup_events import (
    MATCHED_ROLES,
    RUNUP_EVENT_COLUMNS,
    RunupInputs,
    build_runup_events,
    match_controls,
)
from agentic_trader.research.apriori.statistics import (
    difference_in_paired_test,
    mean_test,
    paired_test,
    trimmed_mean,
)
from agentic_trader.research.pooled.cube import _Hourly
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
    "RUNUP_ARM",
    "RUNUP_CONTROL_CELL_COLUMNS",
    "RUNUP_CONTROL_COLUMNS",
    "RunupDecision",
    "cost_column",
    "evaluate_runup",
    "execute_runup_study",
    "label_runup",
    "planted_check",
    "runup_label_columns",
]

RUNUP_ARM = BracketArm(name="runup_time_exit", target_r=None, trail=None)
RUNUP_CONTROL_COLUMNS = ("pair_id", "role", "session", "r_cost", "net_excess_r", "beta", "control_symbols")
RUNUP_CONTROL_CELL_COLUMNS = ("pair_id", "role", "session", "symbol", "r_cost", "net_excess_r", "beta")
_ROLES = ("leg", "announcer", "placebo")
_OUTCOME_COLUMNS = ("decision_price", "entry_price", "entry", "stop", "entry_time", "exit_time", "hit", "r", "r_cost")
_NET_EXCESS_COLUMNS = ("beta", "name_cost_bps", "market_r", "net_excess_r")
_NET_EXCESS_INPUTS = ("symbol", "session", "entry_price", "entry", "stop", "entry_time", "exit_time", "r")
# ``SetupLevels`` needs a target above the entry; ``RUNUP_ARM`` drops it, so this one is never walked.
_UNREACHABLE_TARGET_R = 1000.0
_Z_ALPHA, _Z_POWER = 1.645, 0.84  # one-sided 5% and 80% power: the placebo P2's minimum detectable effect
_JUMP_RV = 3.0  # a hold's daily excess move above this many rv flags a possibly misdated release

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
        "hold_max_excess_rv",
    )


def _ny_date(stamp: Any) -> date:
    return pd.Timestamp(stamp).tz_convert(ET_TZ).date()


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
        # Every role fills on D and exits by the close of D + lead - 1; anything else leaves the report-free band.
        last = d + self._lead - 1
        if (
            last >= len(self._days)
            or _ny_date(outcome.entry_time) != session
            or _ny_date(outcome.exit_time) > self._days[last]
        ):
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


def _hold_max_excess_rv(
    labeller: _Labeller, market: pd.DataFrame | None, event: Mapping[str, Any], cell: Mapping[str, Any], beta: float
) -> float:
    """The largest ``|adjusted daily return - beta x SPY's|`` over the hold's sessions, divided by the row's ``rv``.

    The hold's sessions run from ``D`` (its return is close ``D - 1`` to close ``D``) through the exit session;
    a session with a missing close is skipped. ``NaN`` without ``rv``, beta or any usable session. Spec section
    8's check for releases dated wrongly; it decides nothing.
    """
    rv = float(event["rv"])
    if not (math.isfinite(rv) and rv > 0 and math.isfinite(beta)):
        return math.nan
    days, daily = labeller.days, labeller.daily(event["symbol"])
    first = labeller.position(event["session"])
    last = labeller.position(_ny_date(cell["exit_time"]))
    moves = []
    for p in range(max(first, 1), last + 1):
        stock = _close(daily, days[p]) / _close(daily, days[p - 1]) - 1.0
        spy = _close(market, days[p]) / _close(market, days[p - 1]) - 1.0
        move = abs(stock - beta * spy)
        if math.isfinite(move):
            moves.append(move)
    return max(moves) / rv if moves else math.nan


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


def label_runup(
    events: pd.DataFrame, candidates: pd.DataFrame, inputs: RunupInputs, entry: RunupEntry
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    """``(labels, controls, control_cells, counts)``: labelled events, paired rows' controls, their cells, the funnel.

    ``labels`` has ``runup_label_columns(entry)``; ``controls`` has ``RUNUP_CONTROL_COLUMNS`` (the means over
    the ``matched_controls`` cells of a labelled row); ``control_cells`` has ``RUNUP_CONTROL_CELL_COLUMNS``, one
    row per cell of each paired row, nearest first; ``counts`` holds ``labelled`` by role, the event
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
        jump = _hold_max_excess_rv(labeller, market, event, cell, extra["beta"])
        rows.append({**event, **cell, **extra, "last_day_excess": last_day, "hold_max_excess_rv": jump})
    labels = pd.DataFrame(rows, columns=list(runup_label_columns(entry)))

    cell_rows = [
        {
            "pair_id": match["pair_id"],
            "role": match["role"],
            "session": match["session"],
            "symbol": key[0],
            "r_cost": labeller.outcome(key)["r_cost"],
            "net_excess_r": net[key]["net_excess_r"],
            "beta": net[key]["beta"],
        }
        for match, keys in paired
        for key in keys
    ]
    control_cells = pd.DataFrame(cell_rows, columns=list(RUNUP_CONTROL_CELL_COLUMNS))
    control_rows = [
        {
            "pair_id": match["pair_id"],
            "role": match["role"],
            "session": match["session"],
            "r_cost": float(np.mean([labeller.outcome(key)["r_cost"] for key in keys])),
            "net_excess_r": float(np.mean([net[key]["net_excess_r"] for key in keys])),
            "beta": float(np.mean([net[key]["beta"] for key in keys])),
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
    return labels, controls, control_cells, counts


# --- evaluation (spec section 7) -------------------------------------------------------------------


class RunupDecision(StrEnum):
    """The only outcomes of the development read (spec section 1); none authorizes a probe or a card."""

    SURVIVES_DEVELOPMENT = "survives_development"
    FAILED = "failed"
    INCONCLUSIVE_UNDERPOWERED = "inconclusive_underpowered"


def _numbers(frame: pd.DataFrame, column: str) -> np.ndarray:
    return pd.to_numeric(frame[column], errors="coerce").to_numpy(float)


def _as_r_cost(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    """The rows with a finite ``column``, as the ``session`` and ``r_cost`` frame the shared statistics read."""
    values = _numbers(frame, column)
    keep = np.isfinite(values)
    return pd.DataFrame({"session": frame["session"].to_numpy(dtype=object)[keep], "r_cost": values[keep]})


def _p1(rows: pd.DataFrame, entry: RunupEntry) -> dict:
    """``mean_test`` on the rows' finite ``net_excess_r``."""
    tested = _as_r_cost(rows, "net_excess_r")
    if tested.empty:
        return {"unit": "net_excess_r", "n": 0, "mean_r_cost": math.nan, "holds": False}
    return {"unit": "net_excess_r", **mean_test(tested, entry.bootstrap)}


def _paired(rows: pd.DataFrame, controls: pd.DataFrame, role: str, column: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The rows and their ``role`` control rows with a finite ``column`` on both sides of a pair (``pair_id``)."""
    own = controls[controls["role"] == role]
    own = own[np.isfinite(_numbers(own, column))]
    rows = rows[np.isfinite(_numbers(rows, column))]
    paired = set(rows["pair_id"]) & set(own["pair_id"])
    return _as_r_cost(rows[rows["pair_id"].isin(paired)], column), _as_r_cost(own[own["pair_id"].isin(paired)], column)


def _p2(rows: pd.DataFrame, controls: pd.DataFrame, role: str, entry: RunupEntry, column: str = "r_cost") -> dict:
    """``paired_test`` of the rows' ``column`` against their ``role`` control rows (pairs by ``pair_id``)."""
    leg, control = _paired(rows, controls, role, column)
    if leg.empty:
        return {"unit": column, "n_leg": 0, "n_control": 0, "mean_diff": math.nan, "holds": False}
    return {"unit": column, **paired_test(leg, control, entry.bootstrap)}


def _p5(labels: pd.DataFrame, controls: pd.DataFrame, entry: RunupEntry) -> dict:
    """P5: the legs' paired ``r_cost`` difference minus the placebos', from one joint session-block bootstrap."""
    legs = _paired(labels[labels["role"] == "leg"], controls, "leg", "r_cost")
    placebo = _paired(labels[labels["role"] == "placebo"], controls, "placebo", "r_cost")
    return {"unit": "r_cost", **difference_in_paired_test(*legs, *placebo, entry.bootstrap)}


def _mde(p2: Mapping[str, Any]) -> dict:
    """``(z_alpha + z_power) x`` the P2's bootstrap standard deviation (``NaN`` without one)."""
    sd = float(p2.get("boot_sd", math.nan))
    return {
        "unit": p2["unit"],
        "z_alpha": _Z_ALPHA,
        "z_power": _Z_POWER,
        "boot_sd": sd,
        "mde_r": (_Z_ALPHA + _Z_POWER) * sd,
    }


def _summary(values: pd.Series | np.ndarray) -> dict:
    numbers = pd.to_numeric(pd.Series(values), errors="coerce").to_numpy(float)
    finite = numbers[np.isfinite(numbers)]
    return {"n": int(finite.size), "mean": float(finite.mean()) if finite.size else math.nan}


def planted_check(labels: pd.DataFrame, controls: pd.DataFrame, entry: RunupEntry) -> dict:
    """Spec section 7 (R3): whether P1 and P2 detect half the published effect planted in the placebos.

    Every paired placebo row (one with a ``placebo`` control row) gains ``planted_return x decision_price /
    |entry - stop|`` R in both ``r_cost`` and ``net_excess_r``; its controls are unchanged. P1 runs on the
    shifted rows with a finite ``net_excess_r``, P2 on the shifted ``r_cost`` against their controls;
    ``detected`` needs both to hold. ``mde`` is the minimum detectable effect of the (unshifted) placebo P2,
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


def _criteria(legs: pd.DataFrame, controls: pd.DataFrame, entry: RunupEntry) -> dict:
    """P1-P4 on the legs (spec section 7); P5 needs the placebos too (``_p5``)."""
    finite = _as_r_cost(legs, "net_excess_r")
    recent_from = entry.window.recent_from
    recent = finite[np.array([session >= recent_from for session in finite["session"]], dtype=bool)]
    trimmed = trimmed_mean(finite["r_cost"].to_numpy(float), entry.pass_rule.trim_fraction)
    recent_mean = float(recent["r_cost"].mean()) if len(recent) else math.nan
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
            "holds": bool(math.isfinite(trimmed) and trimmed > 0 and math.isfinite(recent_mean) and recent_mean > 0),
        },
        "P4": {
            "n": len(finite),
            "n_recent": len(recent),
            "min_events": rule.min_events,
            "min_recent_events": rule.min_recent_events,
            "holds": len(finite) >= rule.min_events and len(recent) >= rule.min_recent_events,
        },
    }


def _decision(planted: Mapping[str, Any], criteria: Mapping[str, Any], placebo: Mapping[str, Any]) -> RunupDecision:
    if not planted["detected"]:
        return RunupDecision.INCONCLUSIVE_UNDERPOWERED
    if not all(criterion["holds"] for criterion in criteria.values()) or placebo["P2"]["holds"]:
        return RunupDecision.FAILED
    return RunupDecision.SURVIVES_DEVELOPMENT


# --- descriptive diagnostics (spec section 8) --------------------------------------------------------


def _both(frame: pd.DataFrame) -> dict:
    return {"net_excess_r": _summary(frame["net_excess_r"]), "r_cost": _summary(frame["r_cost"])}


def _dispersion(legs: pd.DataFrame) -> dict:
    """The per-leg SD of ``net_excess_r`` and the session design effect the prospective power plan needs.

    ``design_effect = var(session means) x mean(legs per session) / var(per-leg values)`` over the legs with a
    finite value (sample variances, ``ddof=1``): about 1 when legs on a session are independent, up to the
    legs per session when they move together. ``NaN`` without two sessions or with no per-leg variance.
    """
    finite = _as_r_cost(legs, "net_excess_r")
    grouped = finite.groupby("session")["r_cost"]
    means, sizes = grouped.mean(), grouped.size()
    per_leg = float(finite["r_cost"].var(ddof=1)) if len(finite) > 1 else math.nan
    between = float(means.var(ddof=1)) if len(means) > 1 else math.nan
    per_session = float(sizes.mean()) if len(sizes) else math.nan
    effect = between * per_session / per_leg if per_leg > 0 else math.nan
    return {
        "n": len(finite),
        "n_sessions": len(means),
        "legs_per_session": per_session,
        "sd_per_leg": math.sqrt(per_leg) if per_leg >= 0 else math.nan,
        "var_session_means": between,
        "design_effect": effect,
    }


def _past_move_terciles(legs: pd.DataFrame) -> dict:
    """Legs by tercile of ``past_move`` (ascending, rank ``r`` of ``n`` in ``floor(3r / n)``, stable on ties)."""
    values = _numbers(legs, "past_move")
    finite = np.flatnonzero(np.isfinite(values))
    terciles: dict[str, dict] = {}
    if finite.size >= 3:
        order = finite[np.argsort(values[finite], kind="stable")]
        group = np.arange(order.size) * 3 // order.size
        for k, name in enumerate(("low", "mid", "high")):
            terciles[name] = _both(legs.iloc[order[group == k]])
    return {"n_missing": len(legs) - int(finite.size), "terciles": terciles}


def _hold_jumps(rows: pd.DataFrame) -> dict:
    """How many rows with a finite ``hold_max_excess_rv`` moved more than ``_JUMP_RV`` rv on a day of the hold."""
    values = _numbers(rows, "hold_max_excess_rv")
    finite = values[np.isfinite(values)]
    above = int((finite > _JUMP_RV).sum())
    return {"n": int(finite.size), "n_above": above, "share": above / finite.size if finite.size else math.nan}


def _diagnostics(labels: pd.DataFrame, controls: pd.DataFrame, entry: RunupEntry) -> dict:
    """Spec section 8; nothing here decides anything (the placebo's P1 is reported under ``placebo``).

    Beyond the splits: P2 on ``net_excess_r`` and the mean beta of the paired legs against their control rows,
    the legs by ``archive_covered``, and the share of legs (placebos as a baseline) whose hold had a daily excess
    move above ``_JUMP_RV`` rv, a check for releases dated wrongly.
    """
    legs = labels[labels["role"] == "leg"]
    reports = labels[labels["role"].isin(("leg", "announcer"))]
    paired = set(controls.loc[controls["role"] == "leg", "pair_id"])
    years = pd.to_numeric(legs["year"])
    return {
        "announcers": {
            "all": _both(reports),
            "bottom_tercile": _both(reports[pd.to_numeric(reports["tercile"]) == 0]),
        },
        "legs": _both(legs),
        "unpaired_legs": int((~legs["pair_id"].isin(paired)).sum()),
        "by_year": {int(year): _both(group) for year, group in legs.groupby(years)},
        "by_timing": {timing.value: _both(legs[legs["timing"] == timing.value]) for timing in EarningsTiming},
        "by_bucket": {int(bucket): _both(group) for bucket, group in legs.groupby(pd.to_numeric(legs["bucket"]))},
        "excluding_2020": _both(legs[years != 2020]),
        "exits": {
            "hits": {str(hit): int(n) for hit, n in legs["hit"].value_counts().sort_index().items()},
            "by_hit": {str(hit): _both(group) for hit, group in legs.groupby("hit")},
            "mean_holding_sessions": _summary(legs["holding_sessions"])["mean"],
        },
        "costs": {cost_column(cost): _summary(legs[cost_column(cost)]) for cost in entry.costs_bps_per_side},
        "dispersion": _dispersion(legs),
        "last_day_excess": _summary(legs["last_day_excess"]),
        "past_move_terciles": _past_move_terciles(legs),
        "net_excess_vs_controls": {
            "P2": _p2(legs, controls, "leg", entry, column="net_excess_r"),
            "beta": {
                "legs": _summary(legs.loc[legs["pair_id"].isin(paired), "beta"]),
                "controls": _summary(controls.loc[controls["role"] == "leg", "beta"]),
            },
        },
        "by_archive_coverage": {
            "covered": _both(legs[legs["archive_covered"].eq(True)]),
            "uncovered": _both(legs[legs["archive_covered"].eq(False)]),
        },
        "hold_jumps": {
            "threshold_rv": _JUMP_RV,
            "legs": _hold_jumps(legs),
            "placebo": _hold_jumps(labels[labels["role"] == "placebo"]),
        },
    }


def evaluate_runup(
    labels: pd.DataFrame, controls: pd.DataFrame, entry: RunupEntry, *, planted: dict | None = None
) -> dict:
    """The planted check, the placebo, the frozen P1-P5 rule, the decision and the diagnostics (spec 7-8).

    ``planted`` is the executor's already saved ``planted_check`` result; without it the check is computed
    here, still before the legs' statistics (its minimum detectable effect needs the placebo P2).
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


# --- the executor (spec section 9) --------------------------------------------------------------------


def _failure(exc: BaseException) -> dict:
    return {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "authorizes_promotion": False}


async def execute_runup_study(
    loaded: LoadedRunupEntry,
    directory: Path,
    *,
    build: Callable[[], Awaitable[RunupInputs]],
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
                "pooled_protocol": entry.pooled_protocol,
                "pooled_protocol_sha256": entry.pooled_protocol_sha256,
                "cube_sha256": entry.cube_sha256,
            },
            "environment": environment,
            "started_at": datetime.now(UTC).isoformat(),
            "authorizes_promotion": False,
        },
        directory / "manifest.json",
    )
    try:
        inputs = await build()
        # The cube actually used is hashed (cached on the cube); a provenance label is never trusted for it.
        digest = await asyncio.to_thread(lambda: inputs.cube.sha256)
        if digest != entry.cube_sha256:
            raise ValueError(
                f"the loaded cube has SHA-256 {digest}, but the entry pins cube_sha256 {entry.cube_sha256}"
            )
        events, event_counts = await asyncio.to_thread(build_runup_events, inputs, entry)
        candidates, matching_counts = await asyncio.to_thread(match_controls, events, inputs, entry)
        labelled = await asyncio.to_thread(label_runup, events, candidates, inputs, entry)
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
        # Spec section 7: the planted check (with the placebo P2's minimum detectable effect) is computed and
        # saved before any leg statistic.
        planted = await asyncio.to_thread(planted_check, labels, controls, entry)
        save_json_report(_finite_json(planted), directory / "planted.json")
        evaluation = await asyncio.to_thread(evaluate_runup, labels, controls, entry, planted=planted)
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
