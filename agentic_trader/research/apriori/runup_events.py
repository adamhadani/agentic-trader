"""Pre-earnings run-up events, their pseudo-event placebos and matched-control candidates (entry ``runup``).

Spec sections 4 and 6, the negative-control paragraph of section 7 and section 8's ``past_move``
(``docs/superpowers/specs/2026-11-06-apriori-runup-design.md``). For each deduplicated archive report of a
cohort symbol with report session ``E``, the decision session is ``D = E - lead_sessions``:

- placement: ``E`` inside the calendar (else ``outside_calendar``) and an observed session (else
  ``non_session_report_date``); ``D`` inside the calendar and the decision window (``outside_window``);
  another report of the symbol in the ``recent_previous_report_sessions`` sessions before ``E`` excludes it;
- the shared gates (``_Gates.measure``, also applied to every placebo): the volatility window inside the
  calendar, no identity break on or after its first session, no structural corporate action (anything but
  a cash dividend) within ``structural_exclusion_days`` calendar days of ``[D, E]``, cube eligibility at
  ``D``, ``rv`` from at least ``min_vol_returns`` finite log returns over the ``vol_window`` sessions
  ending ``D - 1``, ATR through ``D - 1`` and the 20-session return to ``D - 1``;
- the event is a ``leg`` when its ``rv`` tercile among the cube-eligible names at ``D`` is the top one,
  otherwise an ``announcer`` kept for descriptive statistics.

Each leg draws one placebo session ``D'`` (``default_rng(placebo.seed)``, legs in ``event_id`` order) among
the window's sessions within ``span_sessions`` of ``D`` with no report of the symbol in
``[D' - lead, D' + 2 * lead]``; ``D'`` passes the same gates, top tercile included. Every exclusion is
counted by reason and year, never silently dropped. Sessions and positions are the observed trading days,
never frame rows: a missing adjusted bar is ``NaN``. Research only: nothing here reads a database, a broker
or the network.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, time, timedelta
from typing import Any

import numpy as np
import pandas as pd

from agentic_trader.agent.earnings import CalendarRow, EarningsTiming
from agentic_trader.data.corporate_actions import CorporateAction, between
from agentic_trader.research.apriori.catalog import RunupEntry
from agentic_trader.research.apriori.earnings_history import dedupe_rows
from agentic_trader.research.apriori.insider_study import _closes, _trailing
from agentic_trader.research.apriori.pead_events import atr_through, by_session, decision_at
from agentic_trader.research.pooled.cube import LabelCube


__all__ = [
    "CONTROL_CANDIDATE_COLUMNS",
    "MATCHED_ROLES",
    "RUNUP_EVENT_COLUMNS",
    "RunupInputs",
    "build_runup_events",
    "match_controls",
]

RUNUP_EVENT_COLUMNS = (
    "event_id",
    "role",
    "pair_id",
    "symbol",
    "session",
    "report_date",
    "timing",
    "decision_at",
    "rv",
    "tercile",
    "atr",
    "ret20",
    "bucket",
    "dv",
    "past_move",
    "year",
)
CONTROL_CANDIDATE_COLUMNS = ("pair_id", "role", "session", "control_symbols")
MATCHED_ROLES = ("leg", "placebo")

_RV_TERCILES = 3
_TOP = _RV_TERCILES - 1
_PAST_REPORTS = 4  # past_move averages up to the last 4 reports before D ...
_MIN_PAST_REPORTS = 2  # ... and needs at least 2 finite reactions


@dataclass(frozen=True)
class RunupInputs:
    cube: LabelCube
    trading_days: tuple[date, ...]
    adjusted: Mapping[str, pd.DataFrame]  # the cohort's adjusted daily bars, keyed by symbol
    hourly: Mapping[str, pd.DataFrame]  # the cohort's hourly bars (labelling)
    reports: Mapping[str, tuple[CalendarRow, ...]]  # cohort symbols, deduplicated, date-sorted
    actions: tuple[CorporateAction, ...]
    identity_breaks: Mapping[str, date]
    market_daily: pd.DataFrame  # SPY adjusted daily bars
    market_hourly: pd.DataFrame  # SPY hourly bars (net excess)
    decision_time: time  # the cube spec's bracket clock
    provenance: Mapping[str, Any]


def _groups(values: np.ndarray, tiebreak: np.ndarray, mask: np.ndarray, groups: int) -> np.ndarray:
    """Group per symbol among the masked cells with a finite value (``-1`` elsewhere).

    The insider study's ``_buckets`` rule: the ``n`` cells are ranked ascending by value, ties by the cube's
    ``tiebreak``, and rank ``r`` falls in group ``floor(r * groups / n)``.
    """
    out = np.full(len(values), -1, dtype=int)
    cols = np.flatnonzero(mask & np.isfinite(values))
    order = cols[np.lexsort((tiebreak[cols], values[cols]))]
    out[order] = np.arange(len(order)) * groups // max(len(order), 1)
    return out


class _Panel:
    """The cohort on the observed trading days: closes, ``rv``, 20-session returns, ranks and report dates."""

    def __init__(self, inputs: RunupInputs, entry: RunupEntry):
        cube = inputs.cube
        self.view = cube.window(cube.sessions[0], cube.sessions[-1])
        self.days = tuple(inputs.trading_days)
        self.pos_of = {d: i for i, d in enumerate(self.days)}
        missing = [d for d in self.view.sessions if d not in self.pos_of]
        if missing:
            raise ValueError(f"{len(missing)} cube sessions are not trading days (first {missing[0].isoformat()})")
        self.row_of = {d: i for i, d in enumerate(self.view.sessions)}
        self.col_of = {s: j for j, s in enumerate(self.view.symbols)}
        self.closes = _closes(inputs.adjusted, self.view.symbols, self.days)
        self._log_returns = np.full_like(self.closes, np.nan)
        with np.errstate(invalid="ignore", divide="ignore"):
            self._log_returns[1:] = np.log(self.closes[1:] / self.closes[:-1])
        self.ret20 = _trailing(self.closes, entry.control.match_return_sessions)
        self.market = _closes({"market": inputs.market_daily}, ("market",), self.days)[:, 0]
        self._vol_window = entry.event.vol_window
        self._min_returns = entry.event.min_vol_returns
        self._liquidity_buckets = entry.control.liquidity_buckets
        self._rv: dict[int, np.ndarray] = {}
        self._tercile: dict[int, np.ndarray] = {}
        self._bucket: dict[int, np.ndarray] = {}
        self.reports: dict[str, tuple[CalendarRow, ...]] = {}
        self.duplicates = 0
        self._ordinals: dict[str, np.ndarray] = {}
        for symbol, rows in inputs.reports.items():
            kept, duplicates = dedupe_rows(rows)
            self.duplicates += duplicates
            self.reports[symbol] = tuple(sorted(kept, key=lambda row: row.date))
            self._ordinals[symbol] = np.array([row.date.toordinal() for row in self.reports[symbol]], dtype=np.int64)

    def rv(self, p: int) -> np.ndarray:
        """Per symbol, the sample standard deviation of the log returns of sessions ``p - vol_window .. p - 1``."""
        if p not in self._rv:
            out = np.full(len(self.view.symbols), np.nan)
            if p >= self._vol_window:
                window = self._log_returns[p - self._vol_window : p]
                ok = np.isfinite(window).sum(axis=0) >= self._min_returns
                if ok.any():
                    out[ok] = np.nanstd(window[:, ok], axis=0, ddof=1)
            self._rv[p] = out
        return self._rv[p]

    def tercile(self, p: int) -> np.ndarray:
        """The ``rv`` tercile among the cube-eligible names at session ``p`` with a finite ``rv``."""
        if p not in self._tercile:
            row = self.row_of[self.days[p]]
            self._tercile[p] = _groups(self.rv(p), self.view.tiebreak[row], self.view.eligible[row], _RV_TERCILES)
        return self._tercile[p]

    def bucket(self, p: int) -> np.ndarray:
        """The dollar-volume bucket among the cube-eligible names at session ``p``."""
        if p not in self._bucket:
            row = self.row_of[self.days[p]]
            view = self.view
            self._bucket[p] = _groups(
                view.dollar_volume[row], view.tiebreak[row], view.eligible[row], self._liquidity_buckets
            )
        return self._bucket[p]

    def reported(self, symbol: str, lo: date, hi: date) -> bool:
        """Whether the archive has a report date of ``symbol`` in ``[lo, hi]`` (every deduplicated row)."""
        ordinals = self._ordinals.get(symbol)
        if ordinals is None or not len(ordinals):
            return False
        i = int(np.searchsorted(ordinals, lo.toordinal(), side="left"))
        return i < len(ordinals) and int(ordinals[i]) <= hi.toordinal()

    def quiet(self, symbol: str, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
        """Per band ``[lo[i], hi[i]]`` (date ordinals), whether the symbol has no report date inside it."""
        ordinals = self._ordinals.get(symbol)
        if ordinals is None or not len(ordinals):
            return np.ones(len(lo), dtype=bool)
        i = np.searchsorted(ordinals, lo, side="left")
        inside = (i < len(ordinals)) & (ordinals[np.minimum(i, len(ordinals) - 1)] <= hi)
        return ~inside

    def past_move(self, symbol: str, col: int, d: int) -> float:
        """Mean absolute 2-session excess reaction to up to the last 4 reports before ``D`` (``NaN`` below 2).

        The reaction session ``R`` is ``E`` for a pre-market release and ``E + 1`` otherwise; the reaction is
        ``close[R + 1] / close[R - 1] - 1`` minus the market's over the same sessions, and counts only when
        ``R + 1`` is before ``D`` and every close is present.
        """
        prior = [row for row in self.reports.get(symbol, ()) if row.date < self.days[d]][-_PAST_REPORTS:]
        moves = []
        for row in prior:
            e = self.pos_of.get(row.date)
            if e is None:
                continue
            r = e if row.timing == EarningsTiming.PRE_MARKET else e + 1
            if r < 1 or r + 1 >= d:
                continue
            stock = self.closes[r + 1, col] / self.closes[r - 1, col] - 1.0
            market = self.market[r + 1] / self.market[r - 1] - 1.0
            move = abs(stock - market)
            if np.isfinite(move):
                moves.append(float(move))
        return float(np.mean(moves)) if len(moves) >= _MIN_PAST_REPORTS else float("nan")


class _Gates:
    """Spec section 4, steps 3-6: the gates every leg, announcer and placebo passes at a decision position."""

    def __init__(self, panel: _Panel, inputs: RunupInputs, entry: RunupEntry):
        self._panel = panel
        self._adjusted = inputs.adjusted
        self._breaks = inputs.identity_breaks
        self._vol_window = entry.event.vol_window
        self._atr_window = entry.trade.atr_window
        self._pad = timedelta(days=entry.event.structural_exclusion_days)
        self._structural: dict[str, list[CorporateAction]] = defaultdict(list)
        for action in inputs.actions:
            if action.is_structural:
                self._structural[action.symbol].append(action)
        self._keyed: dict[str, pd.DataFrame] = {}

    def _daily(self, symbol: str) -> pd.DataFrame:
        if symbol not in self._keyed:
            frame = self._adjusted.get(symbol)
            empty = frame is None or frame.empty
            self._keyed[symbol] = pd.DataFrame(columns=["High", "Low", "Close"]) if empty else by_session(frame)
        return self._keyed[symbol]

    def measure(self, symbol: str, d: int, last: int) -> dict[str, Any] | str:
        """The measurements at decision position ``d`` (hold through position ``last``) or the failing reason."""
        panel, days = self._panel, self._panel.days
        if d - self._vol_window - 1 < 0 or last >= len(days):
            return "outside_calendar"
        broke = self._breaks.get(symbol)
        # A break's first valid session at or after the volatility window's first session (D - vol_window)
        # means the window's first close (D - vol_window - 1) belongs to another issuer or ticker.
        if broke is not None and bisect_left(days, broke) >= d - self._vol_window:
            return "identity_break"
        if between(self._structural.get(symbol, ()), symbol, days[d] - self._pad, days[last] + self._pad):
            return "structural_action_nearby"
        row, col = panel.row_of.get(days[d]), panel.col_of.get(symbol)
        if row is None or col is None:
            return "not_in_cube"
        if not panel.view.eligible[row, col]:
            return "not_eligible"
        rv = float(panel.rv(d)[col])
        if not np.isfinite(rv):
            return "no_rv"
        atr = atr_through(self._daily(symbol), days, d - 1, self._atr_window)
        if atr is None:
            return "no_atr"
        ret20 = float(panel.ret20[d, col])
        if not np.isfinite(ret20):
            return "no_return"
        return {
            "rv": rv,
            "tercile": int(panel.tercile(d)[col]),
            "atr": atr,
            "ret20": ret20,
            "bucket": int(panel.bucket(d)[col]),
            "dv": float(panel.view.dollar_volume[row, col]),
            "past_move": panel.past_move(symbol, col, d),
        }


class _Tally:
    def __init__(self) -> None:
        self.reasons: Counter[str] = Counter()
        self.by_year: dict[str, Counter[int]] = defaultdict(Counter)

    def __call__(self, reason: str, year: int) -> None:
        self.reasons[reason] += 1
        self.by_year[reason][year] += 1

    def counts(self) -> tuple[dict[str, int], dict[str, dict[int, int]]]:
        return dict(sorted(self.reasons.items())), {r: dict(sorted(y.items())) for r, y in sorted(self.by_year.items())}


def _report_events(
    panel: _Panel, gates: _Gates, inputs: RunupInputs, entry: RunupEntry, skip: _Tally
) -> list[dict[str, Any]]:
    """Legs and announcers: every report's placement checks, then the shared gates (spec section 4)."""
    days = panel.days
    start, end = entry.window.decisions
    lead, recent = entry.event.lead_sessions, entry.event.recent_previous_report_sessions
    rows: list[dict[str, Any]] = []
    for symbol in sorted(panel.reports):
        for report in panel.reports[symbol]:
            when = report.date
            e = panel.pos_of.get(when)
            if not days[0] <= when <= days[-1] or (e is not None and e - lead < 0):
                skip("outside_calendar", when.year)
                continue
            if e is None:
                skip("non_session_report_date", when.year)
                continue
            d = e - lead
            if not start <= days[d] <= end:
                skip("outside_window", when.year)
                continue
            # A short calendar opens the look-back at the start of time, so it never hides a report.
            since = days[e - recent] if e >= recent else date.min
            if panel.reported(symbol, since, when - timedelta(days=1)):
                skip("recent_previous_report", when.year)
                continue
            measured = gates.measure(symbol, d, e)
            if isinstance(measured, str):
                skip(measured, when.year)
                continue
            event_id = f"{symbol}:{when.isoformat()}"
            rows.append(
                {
                    "event_id": event_id,
                    "role": "leg" if measured["tercile"] == _TOP else "announcer",
                    "pair_id": event_id,
                    "symbol": symbol,
                    "session": days[d],
                    "report_date": when,
                    "timing": report.timing.value,
                    "decision_at": decision_at(days[d], inputs.decision_time),
                    **measured,
                    "year": days[d].year,
                }
            )
    return rows


def _placebos(
    legs: Sequence[Mapping[str, Any]],
    panel: _Panel,
    gates: _Gates,
    inputs: RunupInputs,
    entry: RunupEntry,
    skip: _Tally,
) -> list[dict[str, Any]]:
    """One pseudo-event per leg (spec section 7): a seeded quiet session, then the legs' gates and tercile."""
    days = panel.days
    ordinals = np.array([day.toordinal() for day in days], dtype=np.int64)
    start, end = (day.toordinal() for day in entry.window.decisions)
    lead, span = entry.event.lead_sessions, entry.placebo.span_sessions
    rng = np.random.default_rng(entry.placebo.seed)
    rows: list[dict[str, Any]] = []
    for leg in sorted(legs, key=lambda row: row["event_id"]):
        symbol, d, year = leg["symbol"], panel.pos_of[leg["session"]], leg["session"].year
        q = np.arange(max(d - span, lead), min(d + span, len(days) - 1 - 2 * lead) + 1)
        in_window = (ordinals[q] >= start) & (ordinals[q] <= end)
        candidates = q[in_window & panel.quiet(symbol, ordinals[q - lead], ordinals[q + 2 * lead])]
        if not len(candidates):
            skip("placebo_no_session", year)
            continue
        drawn = int(candidates[int(rng.integers(len(candidates)))])
        measured = gates.measure(symbol, drawn, drawn + lead)
        if isinstance(measured, str):
            skip(measured, year)
            continue
        if measured["tercile"] != _TOP:
            skip("not_top_tercile", year)
            continue
        session = days[drawn]
        rows.append(
            {
                "event_id": f"{leg['event_id']}:placebo",
                "role": "placebo",
                "pair_id": leg["event_id"],
                "symbol": symbol,
                "session": session,
                "report_date": None,
                "timing": None,
                "decision_at": decision_at(session, inputs.decision_time),
                **measured,
                "year": session.year,
            }
        )
    return rows


def build_runup_events(inputs: RunupInputs, entry: RunupEntry) -> tuple[pd.DataFrame, dict]:
    """Legs, announcers and placebos (``RUNUP_EVENT_COLUMNS``) with every exclusion by reason and year.

    Event reasons are keyed by the report date's year; placebo reasons by the leg's decision year.
    """
    panel = _Panel(inputs, entry)
    gates = _Gates(panel, inputs, entry)
    skip, placebo_skip = _Tally(), _Tally()
    events = _report_events(panel, gates, inputs, entry, skip)
    legs = [row for row in events if row["role"] == "leg"]
    placebos = _placebos(legs, panel, gates, inputs, entry, placebo_skip)
    frame = pd.DataFrame([*events, *placebos], columns=list(RUNUP_EVENT_COLUMNS))
    frame = frame.sort_values(["session", "event_id"], kind="stable").reset_index(drop=True)
    for column in ("report_date", "timing"):
        frame[column] = frame[column].astype(object).where(frame[column].notna(), None)
    reasons, reasons_by_year = skip.counts()
    placebo_reasons, placebo_reasons_by_year = placebo_skip.counts()
    counts = {
        "reports": sum(len(rows) for rows in panel.reports.values()),
        "duplicates": panel.duplicates,
        "legs": len(legs),
        "announcers": len(events) - len(legs),
        "placebos": len(placebos),
        "events_by_tercile": dict(sorted(Counter(int(row["tercile"]) for row in events).items())),
        "reasons": reasons,
        "reasons_by_year": reasons_by_year,
        "placebo_reasons": placebo_reasons,
        "placebo_reasons_by_year": placebo_reasons_by_year,
    }
    return frame, counts


def match_controls(events: pd.DataFrame, inputs: RunupInputs, entry: RunupEntry) -> tuple[pd.DataFrame, dict]:
    """The ``matched_controls`` nearest cohort names for every leg and placebo row (spec section 6).

    A candidate is cube-eligible at ``D``, in the row's dollar-volume bucket and the top ``rv`` tercile,
    not another row of the same role on ``D`` (a placebo's controls bar the placebos on ``D'``, a leg's
    the legs on ``D``; legs on ``D'`` already report inside the hold), has no archive report date in
    ``[D, D + lead_sessions]`` and at least one in the ``coverage_sessions`` sessions before ``D``. The
    nearest by 20-session return to ``D - 1`` win, ties by the cube's ``tiebreak``; fewer candidates leave
    the row unpaired, counted by role and decision year.
    """
    panel = _Panel(inputs, entry)
    view, days = panel.view, panel.days
    k, lead, cover = entry.control.matched_controls, entry.event.lead_sessions, entry.control.coverage_sessions
    records = [record for record in events.to_dict("records") if record["role"] in MATCHED_ROLES]
    treated: dict[tuple[str, date], set[str]] = defaultdict(set)
    for record in records:
        treated[(record["role"], record["session"])].add(record["symbol"])
    paired = dict.fromkeys(MATCHED_ROLES, 0)
    unpaired: dict[str, Counter[int]] = {role: Counter() for role in MATCHED_ROLES}
    rows: list[dict[str, Any]] = []
    for record in records:
        role, session, symbol = record["role"], record["session"], record["symbol"]
        row, col, d = panel.row_of.get(session), panel.col_of.get(symbol), panel.pos_of.get(session)
        if row is None or col is None or d is None:
            raise ValueError(f"{record['event_id']} is not a cube cell")
        ret20 = panel.ret20[d]
        if not np.isfinite(ret20[col]):
            raise ValueError(f"{record['event_id']} has no {entry.control.match_return_sessions}-session return")
        bucket, tercile = panel.bucket(d), panel.tercile(d)
        mask = view.eligible[row] & (bucket == bucket[col]) & (tercile == _TOP) & np.isfinite(ret20)
        hold_end = days[d + lead] if d + lead < len(days) else date.max
        covered_from = days[d - cover] if d >= cover else date.min
        candidates = [
            c
            for c in np.flatnonzero(mask)
            if view.symbols[c] not in treated[(role, session)]
            and not panel.reported(view.symbols[c], session, hold_end)
            and panel.reported(view.symbols[c], covered_from, session - timedelta(days=1))
        ]
        if bucket[col] < 0 or len(candidates) < k:
            unpaired[role][session.year] += 1
            continue
        cand = np.array(candidates)
        distance = np.abs(ret20[cand] - ret20[col])
        chosen = cand[np.lexsort((view.tiebreak[row, cand], distance))[:k]]
        rows.append(
            {
                "pair_id": record["pair_id"],
                "role": role,
                "session": session,
                "control_symbols": ",".join(view.symbols[c] for c in chosen),
            }
        )
        paired[role] += 1
    counts = {
        "paired": paired,
        "unpaired": {role: sum(years.values()) for role, years in unpaired.items()},
        "unpaired_by_year": {role: dict(sorted(years.items())) for role, years in unpaired.items()},
    }
    return pd.DataFrame(rows, columns=list(CONTROL_CANDIDATE_COLUMNS)), counts
