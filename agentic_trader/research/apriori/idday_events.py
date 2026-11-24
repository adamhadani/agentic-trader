"""Investor-day legs, same-firm placebos, earnings-matched control candidates and the parser check (entry ``idday``).

Spec sections 6-8 and section 9's negative-control draws (``docs/superpowers/specs/2026-11-19-apriori-idday-design.md``).
Every span counted in sessions is counted on the observed trading days; ``F``'s session is the last trading day
on or before ``F``. The price-free gates run on ONE calendar, the merged cube's sessions (``idday_candidates``), for
the parser-check sample and the study alike; placebos, controls and labels use the trading days. An issuer's archive rows are the union of its cohort symbols' report dates; its event dates
are the filing dates of all its raw full-text search hits plus the filing and event dates of all its parsed
events (announced and ``event_only``).

- ``candidate_legs`` (steps 1-4, price-free, shared by the parser-check sampler and the study): ``D`` is the
  ``entry_offset_sessions``-th session strictly after ``F``, ``X`` the last session strictly before ``E`` and
  ``H`` the sessions in ``[D, X]`` (``lead_too_short``, ``lead_too_long``); ``D`` inside the decisions and
  ``X <= last_exit`` (``outside_window``), and so is anything needing a session outside the calendar (``F``'s
  session, the earnings band, ``D`` or ``X``). An archive report in ``[F + 1, X]`` is ``earnings_in_hold``, and
  the class is ``with_earnings`` when a report falls in the band from the session ``earnings_band_sessions - 1``
  before ``F``'s session through ``F`` (a non-session ``F`` keeps its own date). Overlap runs last, over the
  events that passed steps 1, 2 and 4, in ``D`` order: a later event of an issuer whose ``D`` is within
  ``overlap_sessions`` of an earlier kept one is ``overlapping_event``. A candidate that later fails a price gate
  (steps 5-7) still blocks, since that cannot be known price-free.
- ``build_idday_events``: the issuer's eligible cohort symbol with the most dollar volume at ``D`` (ties to the
  smaller cube ``tiebreak``), then steps 5-7 (``_Gates``) and one placebo per leg (legs in ``(session, symbol)``
  order, one ``default_rng(placebo.seed)``), which passes the same gates; a second draw of a ``(symbol, D')``
  is ``duplicate_session``. A placebo has no ``E``; its structural span ends at the session after ``X'``.
- ``match_controls``: section 8's control candidates for every leg and placebo row.

Every exclusion is counted by reason and year. Research only: nothing here reads a database, a broker or the
network.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, time, timedelta
from typing import Any

import numpy as np
import pandas as pd

from agentic_trader.agent.earnings import CalendarRow
from agentic_trader.data.corporate_actions import CorporateAction, between
from agentic_trader.research.apriori.catalog import IddayEntry
from agentic_trader.research.apriori.idday_parser import EventTable
from agentic_trader.research.apriori.insider_study import _closes, _trailing
from agentic_trader.research.apriori.pead_events import atr_through, by_session, decision_at
from agentic_trader.research.investor_days import IssuerRecord
from agentic_trader.research.pooled.cohort import Cohort
from agentic_trader.research.pooled.cube import LabelCube


__all__ = [
    "CANDIDATE_COLUMNS",
    "CONTROL_CANDIDATE_COLUMNS",
    "IDDAY_EVENT_COLUMNS",
    "MATCHED_ROLES",
    "IddayInputs",
    "build_idday_events",
    "candidate_legs",
    "cohort_bands",
    "idday_candidates",
    "issuer_reports",
    "match_controls",
    "sample_parser_check",
    "validate_parser_check",
]

CANDIDATE_COLUMNS = ("event_id", "cik", "F", "E", "D", "X", "H", "klass")
IDDAY_EVENT_COLUMNS = (
    "event_id",
    "pair_id",
    "role",
    "symbol",
    "cik",
    "klass",
    "band",
    "session",
    "decision_at",
    "F",
    "E",
    "X",
    "hold_sessions",
    "atr",
    "ret_20",
    "bucket",
    "keyword",
    "item_2_02",
)
CONTROL_CANDIDATE_COLUMNS = ("pair_id", "role", "session", "control_symbols", "hold_sessions")
MATCHED_ROLES = ("leg", "placebo")
WITH_EARNINGS, STANDALONE = "with_earnings", "standalone"

_EMPTY = np.zeros(0, dtype=np.int64)
_MIN_ORDINAL, _MAX_ORDINAL = date.min.toordinal(), date.max.toordinal()
_DAY = timedelta(days=1)


@dataclass(frozen=True)
class IddayInputs:
    cube: LabelCube  # the merged cohort v3 + xu cube
    trading_days: tuple[date, ...]
    adjusted: Mapping[str, pd.DataFrame]  # adjusted daily bars, keyed by symbol
    hourly: Mapping[str, pd.DataFrame]  # hourly bars (labelling)
    reports: Mapping[str, tuple[CalendarRow, ...]]  # cohort symbols, deduplicated, date-sorted
    actions: tuple[CorporateAction, ...]
    identity_breaks: Mapping[str, date]
    market_daily: pd.DataFrame  # SPY adjusted daily bars
    market_hourly: pd.DataFrame  # SPY hourly bars (net excess)
    decision_time: time
    provenance: Mapping[str, Any]
    events: EventTable
    issuers: Mapping[int, IssuerRecord]
    issuer_of: Mapping[str, int]  # cohort symbol -> CIK; an unmapped symbol is absent
    band_of: Mapping[str, str]  # "v3", "xu_large", "xu_small" or "xu"


def issuer_reports(reports: Mapping[str, Sequence[CalendarRow]], symbols: Sequence[str]) -> tuple[date, ...]:
    """The union of the symbols' archive report dates, deduplicated and sorted."""
    return tuple(sorted({row.date for symbol in symbols for row in reports.get(symbol, ())}))


def cohort_bands(v3: Cohort, xu: Cohort) -> dict[str, str]:
    """``v3`` for cohort v3; ``xu_large``/``xu_small`` by the xu screens' first rank, else ``xu``."""
    bands = dict.fromkeys(xu.symbols, "xu")
    screens = [source for source in xu.sources if source.screen is not None]
    starts = {source.screen.rank_from for source in screens if source.screen is not None}
    if len(screens) == 2 and len(starts) == 2:
        first = min(starts)
        for source in screens:
            band = "xu_large" if source.screen is not None and source.screen.rank_from == first else "xu_small"
            bands.update((symbol, band) for symbol in source.symbols if symbol in bands)
    bands.update(dict.fromkeys(v3.symbols, "v3"))
    return bands


def _ordinals(dates: Iterable[date]) -> np.ndarray:
    return np.array(sorted({d.toordinal() for d in dates}), dtype=np.int64)


def _any_inside(ordinals: np.ndarray, lo: date, hi: date) -> bool:
    if not len(ordinals) or lo > hi:
        return False
    i = int(np.searchsorted(ordinals, lo.toordinal(), side="left"))
    return i < len(ordinals) and int(ordinals[i]) <= hi.toordinal()


def _none_inside(ordinals: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """Per band ``[lo[i], hi[i]]`` (ordinals), whether no ordinal falls inside it."""
    if not len(ordinals):
        return np.ones(len(lo), dtype=bool)
    i = np.searchsorted(ordinals, lo, side="left")
    return ~((i < len(ordinals)) & (ordinals[np.minimum(i, len(ordinals) - 1)] <= hi))


def _day(days: Sequence[date], p: int) -> date:
    """The session at position ``p``; before the calendar is the start of time, after it the end."""
    if p < 0:
        return date.min
    return days[p] if p < len(days) else date.max


def _band(days: Sequence[date], filed: date, sessions: int) -> tuple[date, date]:
    """The earnings band: from ``sessions - 1`` sessions before ``filed``'s session through ``filed``."""
    return _day(days, bisect_right(days, filed) - sessions), filed


def _groups(values: np.ndarray, tiebreak: np.ndarray, mask: np.ndarray, groups: int) -> np.ndarray:
    """Group per symbol among the masked finite cells, ranked by value then ``tiebreak`` (``-1`` elsewhere)."""
    out = np.full(len(values), -1, dtype=int)
    cols = np.flatnonzero(mask & np.isfinite(values))
    order = cols[np.lexsort((tiebreak[cols], values[cols]))]
    out[order] = np.arange(len(order)) * groups // max(len(order), 1)
    return out


class _Tally:
    def __init__(self) -> None:
        self.reasons: Counter[str] = Counter()
        self.by_year: dict[str, Counter[int]] = defaultdict(Counter)

    def __call__(self, reason: str, year: int) -> None:
        self.reasons[reason] += 1
        self.by_year[reason][year] += 1

    def counts(self) -> tuple[dict[str, int], dict[str, dict[int, int]]]:
        return dict(sorted(self.reasons.items())), {r: dict(sorted(y.items())) for r, y in sorted(self.by_year.items())}


def _timing(filed: date, event_date: date, days: Sequence[date], entry: IddayEntry) -> tuple[int, int] | str:
    """Steps 1-2: the positions of ``D`` and ``X`` or the failing reason."""
    rules, (start, end) = entry.event, entry.window.decisions
    after = bisect_right(days, filed)  # F's session is after - 1; the band starts at after - band_sessions
    d = after + rules.entry_offset_sessions - 1
    if after < max(rules.earnings_band_sessions, 1) or d >= len(days):
        return "outside_window"
    if event_date > days[-1]:  # X is at or after the calendar's last session
        return "lead_too_long" if len(days) - d > rules.max_hold_sessions else "outside_window"
    x = bisect_left(days, event_date) - 1
    hold = x - d + 1
    if hold < rules.min_hold_sessions:
        return "lead_too_short"
    if hold > rules.max_hold_sessions:
        return "lead_too_long"
    if not start <= days[d] <= end or days[x] > entry.window.last_exit:
        return "outside_window"
    return d, x


def candidate_legs(
    table: EventTable,
    trading_days: Sequence[date],
    reports_by_issuer: Mapping[int, tuple[date, ...]],
    entry: IddayEntry,
) -> tuple[pd.DataFrame, dict]:
    """Spec section 7 steps 1-4 for every announced event (``CANDIDATE_COLUMNS``), price-free.

    ``trading_days`` is the gating calendar (the merged cube's sessions, see ``idday_candidates``). Overlap is
    applied last, over the events that passed timing, window and earnings. Reasons are keyed by ``F``'s year;
    ``event_only`` events are counted apart and never become legs.
    """
    days = tuple(trading_days)
    rules = entry.event
    skip = _Tally()
    reports = {cik: _ordinals(dates) for cik, dates in reports_by_issuer.items()}
    passed: list[dict[str, Any]] = []
    event_only = 0
    for event in table.events:
        filed = event.filed
        if event.status != "announced" or filed is None:
            event_only += 1
            continue
        placed = _timing(filed, event.event_date, days, entry)
        if isinstance(placed, str):
            skip(placed, filed.year)
            continue
        d, x = placed
        own = reports.get(event.cik, _EMPTY)
        if _any_inside(own, filed + _DAY, days[x]):
            skip("earnings_in_hold", filed.year)
            continue
        band = _band(days, filed, rules.earnings_band_sessions)
        passed.append(
            {
                "event_id": event.event_id,
                "cik": event.cik,
                "F": filed,
                "E": event.event_date,
                "D": days[d],
                "X": days[x],
                "H": x - d + 1,
                "klass": WITH_EARNINGS if _any_inside(own, *band) else STANDALONE,
                "_d": d,
            }
        )
    last_kept: dict[int, int] = {}
    rows: list[dict[str, Any]] = []
    for row in sorted(passed, key=lambda item: (item["_d"], item["event_id"])):
        d = row.pop("_d")
        prior = last_kept.get(row["cik"])
        if prior is not None and d - prior <= rules.overlap_sessions:
            skip("overlapping_event", row["F"].year)
            continue
        last_kept[row["cik"]] = d
        rows.append(row)
    reasons, by_year = skip.counts()
    counts = {
        "events": len(table.events),
        "event_only": event_only,
        "candidates": len(rows),
        "by_class": dict(sorted(Counter(row["klass"] for row in rows).items())),
        "reasons": reasons,
        "reasons_by_year": by_year,
    }
    return pd.DataFrame(rows, columns=list(CANDIDATE_COLUMNS)), counts


def _reports_by_issuer(inputs: IddayInputs) -> dict[int, tuple[date, ...]]:
    return {cik: issuer_reports(inputs.reports, record.symbols) for cik, record in inputs.issuers.items()}


def idday_candidates(inputs: IddayInputs, entry: IddayEntry) -> tuple[pd.DataFrame, dict]:
    """``candidate_legs`` on the one gating calendar, the merged cube's sessions (the parser-check sample's too)."""
    return candidate_legs(inputs.events, inputs.cube.sessions, _reports_by_issuer(inputs), entry)


def sample_parser_check(candidates: pd.DataFrame, table: EventTable, entry: IddayEntry) -> dict:
    """The seeded parser-check sample (spec section 6), drawn without replacement in ``event_id`` order.

    Candidates whose issuer has a symbol in ``excluded_symbols`` are left out before the draw; fewer
    remaining candidates than ``sample_size`` refuse.
    """
    check = entry.parser_check
    by_id = {event.event_id: event for event in table.events}
    ids = list(candidates["event_id"])
    unknown = [event_id for event_id in ids if event_id not in by_id]
    if unknown or len(set(ids)) != len(ids):
        raise ValueError(f"candidate event_ids must be unique rows of the event table ({len(unknown)} unknown)")
    excluded = set(check.excluded_symbols)
    population = sorted(event_id for event_id in ids if excluded.isdisjoint(by_id[event_id].symbols))
    if len(population) < check.sample_size:
        raise ValueError(
            f"only {len(population)} candidate legs outside the excluded issuers; the check samples {check.sample_size}"
        )
    drawn = np.random.default_rng(check.seed).choice(len(population), size=check.sample_size, replace=False)
    sample = []
    for event_id in sorted(population[int(i)] for i in drawn):
        event = by_id[event_id]
        sample.append(
            {
                "event_id": event_id,
                "symbols": list(event.symbols),
                "filed": event.filed.isoformat() if event.filed else None,
                "event_date": event.event_date.isoformat(),
                "keyword": event.keyword,
                "sentence": event.sentence,
                "url": event.url,
            }
        )
    return {
        "events_sha256": table.events_sha256,
        "seed": check.seed,
        "sample_size": check.sample_size,
        "population": len(population),
        "sample": sample,
    }


def validate_parser_check(document: Mapping, sample: Mapping, entry: IddayEntry) -> dict:
    """Refuse a parser check that does not label exactly the sample or has more than ``max_errors`` errors.

    ``document`` is ``parser_check.json``: ``events_sha256``, ``seed``, ``sample_size`` and ``labels``, one
    ``{"event_id", "correct": bool, "reason": non-empty str}`` per sampled event.
    """
    check = entry.parser_check
    if document.get("events_sha256") != sample["events_sha256"]:
        raise ValueError("parser check events_sha256 does not match the sample's")
    if (sample["seed"], sample["sample_size"]) != (check.seed, check.sample_size):
        raise ValueError("the sample's seed and size are not the entry's parser_check")
    if (document.get("seed"), document.get("sample_size")) != (sample["seed"], sample["sample_size"]):
        raise ValueError("parser check seed or sample_size does not match the sample's")
    labels = document.get("labels")
    if not isinstance(labels, list) or not all(isinstance(label, Mapping) for label in labels):
        raise ValueError("parser check labels must be a list of objects")
    ids = [label.get("event_id") for label in labels]
    if len(set(ids)) != len(ids) or set(ids) != {row["event_id"] for row in sample["sample"]}:
        raise ValueError("parser check labels must name each sampled event_id exactly once")
    for label in labels:
        if not isinstance(label.get("correct"), bool):
            raise ValueError(f"label {label.get('event_id')} needs a boolean 'correct'")  # noqa: TRY004 -- every refusal is a ValueError
        reason = label.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"label {label.get('event_id')} needs a non-empty 'reason'")
    errors = sum(1 for label in labels if label["correct"] is False)
    if errors > check.max_errors:
        raise ValueError(f"parser check has {errors} errors, above the frozen limit of {check.max_errors}")
    return {"errors": errors, "sample_size": len(labels), "precision": (len(labels) - errors) / len(labels)}


class _Panel:
    """The merged cohort on the trading days: closes, 20-session returns, dollar-volume buckets and history."""

    def __init__(self, inputs: IddayInputs, entry: IddayEntry):
        cube = inputs.cube
        self.view = cube.window(cube.sessions[0], cube.sessions[-1])
        self.days = tuple(inputs.trading_days)
        self.pos_of = {d: i for i, d in enumerate(self.days)}
        missing = [d for d in self.view.sessions if d not in self.pos_of]
        if missing:
            raise ValueError(f"{len(missing)} cube sessions are not trading days (first {missing[0].isoformat()})")
        self.row_of = {d: i for i, d in enumerate(self.view.sessions)}
        self.col_of = {s: j for j, s in enumerate(self.view.symbols)}
        self.ordinals = np.array([d.toordinal() for d in self.days], dtype=np.int64)
        self.ret20 = _trailing(
            _closes(inputs.adjusted, self.view.symbols, self.days), entry.control.match_return_sessions
        )
        self._buckets = entry.control.liquidity_buckets
        self._bucket: dict[int, np.ndarray] = {}
        unknown = sorted({event.cik for event in inputs.events.events} - set(inputs.issuers))
        if unknown:
            raise ValueError(f"{len(unknown)} event-table issuers are not in the acquisition (first {unknown[0]})")
        self.reports_by_issuer = _reports_by_issuer(inputs)
        self.reports = {cik: _ordinals(dates) for cik, dates in self.reports_by_issuer.items()}
        event_days: dict[int, set[date]] = defaultdict(set)
        for cik, record in inputs.issuers.items():
            event_days[cik].update(hit.filed for hit in record.hits)
        for event in inputs.events.events:
            event_days[event.cik].update(d for d in (event.filed, event.event_date) if d is not None)
        self.events = {cik: _ordinals(dates) for cik, dates in event_days.items()}

    def bucket(self, p: int) -> np.ndarray:
        """The dollar-volume bucket among the cube-eligible names at session ``p``."""
        if p not in self._bucket:
            row, view = self.row_of[self.days[p]], self.view
            self._bucket[p] = _groups(view.dollar_volume[row], view.tiebreak[row], view.eligible[row], self._buckets)
        return self._bucket[p]

    def ordinal_at(self, positions: np.ndarray) -> np.ndarray:
        """``_day`` over an array of positions, as ordinals."""
        inside = self.ordinals[np.clip(positions, 0, len(self.days) - 1)]
        return np.where(positions < 0, _MIN_ORDINAL, np.where(positions >= len(self.days), _MAX_ORDINAL, inside))


class _Gates:
    """Spec section 7 steps 5-7 at a decision position, for legs and placebos alike."""

    def __init__(self, panel: _Panel, inputs: IddayInputs, entry: IddayEntry):
        self._panel = panel
        self._adjusted = inputs.adjusted
        self._breaks = inputs.identity_breaks
        self._lookback = entry.event.identity_lookback_sessions
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

    def choose(self, symbols: Sequence[str], d: int) -> str:
        """The eligible symbol with the most dollar volume at ``D`` (ties to the smaller tiebreak), or a reason."""
        panel = self._panel
        row = panel.row_of.get(panel.days[d])
        if row is None:
            return "not_in_cube"
        view = panel.view
        keyed = [
            ((-float(view.dollar_volume[row, col]), int(view.tiebreak[row, col])), symbol)
            for symbol in symbols
            if (col := panel.col_of.get(symbol)) is not None and view.eligible[row, col]
        ]
        return min(keyed)[1] if keyed else "not_eligible"

    def measure(self, symbol: str, d: int, through: date) -> dict[str, Any] | str:
        """The inputs at decision position ``d`` (structural span ``[D, through]``) or the failing reason."""
        panel, days = self._panel, self._panel.days
        broke = self._breaks.get(symbol)
        if broke is not None and broke >= _day(days, d - self._lookback):
            return "identity_break"
        if between(self._structural.get(symbol, ()), symbol, days[d] - self._pad, through + self._pad):
            return "structural_action_nearby"
        row, col = panel.row_of.get(days[d]), panel.col_of.get(symbol)
        if row is None or col is None:
            return "not_in_cube"
        if not panel.view.eligible[row, col]:
            return "not_eligible"
        atr = atr_through(self._daily(symbol), days, d - 1, self._atr_window)
        if atr is None:
            return "no_atr"
        ret20 = float(panel.ret20[d, col])
        if not np.isfinite(ret20):
            return "no_return"
        return {"atr": atr, "ret_20": ret20, "bucket": int(panel.bucket(d)[col])}


def _report_draws(leg: Mapping[str, Any], panel: _Panel, entry: IddayEntry) -> list[tuple[int, date | None]]:
    """A ``with_earnings`` leg's placebo options: ``(D', r')`` per clean other report within the span of ``F``."""
    days, cik, hold = panel.days, leg["cik"], leg["hold_sessions"]
    start, end = entry.window.decisions
    f = bisect_right(days, leg["F"]) - 1
    band_lo, band_hi = _band(days, leg["F"], entry.event.earnings_band_sessions)
    reports, events = panel.reports.get(cik, _EMPTY), panel.events.get(cik, _EMPTY)
    options: list[tuple[int, date | None]] = []
    for report in panel.reports_by_issuer.get(cik, ()):
        r = bisect_right(days, report) - 1
        if band_lo <= report <= band_hi or r < 0 or abs(r - f) > entry.placebo.earnings_span_sessions:
            continue
        d = r + entry.event.entry_offset_sessions
        x = d + hold - 1
        if x >= len(days) or not start <= days[d] <= end or days[x] > entry.window.last_exit:
            continue
        free_lo = _day(days, r - entry.placebo.report_event_free_before_sessions)
        if _any_inside(events, free_lo, _day(days, x + entry.control.event_free_after_sessions)):
            continue
        if not _any_inside(reports, report + _DAY, days[x]):
            options.append((d, report))
    return options


def _standalone_draws(leg: Mapping[str, Any], panel: _Panel, entry: IddayEntry) -> list[tuple[int, date | None]]:
    """A ``standalone`` leg's placebo options: sessions within the span of ``D`` with a clean hold."""
    days, cik, hold, control = panel.days, leg["cik"], leg["hold_sessions"], entry.control
    d = panel.pos_of[leg["session"]]
    span = entry.placebo.standalone_span_sessions
    q = np.arange(max(d - span, 0), min(d + span, len(days) - hold) + 1)
    x = q + hold - 1
    start, end = (day.toordinal() for day in entry.window.decisions)
    ok = (
        (panel.ordinals[q] >= start)
        & (panel.ordinals[q] <= end)
        & (panel.ordinals[x] <= entry.window.last_exit.toordinal())
    )
    ok &= _none_inside(
        panel.reports.get(cik, _EMPTY), panel.ordinal_at(q - control.quiet_before_sessions), panel.ordinals[x]
    )
    lo, hi = (
        panel.ordinal_at(q - control.event_free_before_sessions),
        panel.ordinal_at(x + control.event_free_after_sessions),
    )
    ok &= _none_inside(panel.events.get(cik, _EMPTY), lo, hi)
    return [(int(p), None) for p in q[ok]]


def _placebos(
    legs: Sequence[Mapping[str, Any]],
    panel: _Panel,
    gates: _Gates,
    inputs: IddayInputs,
    entry: IddayEntry,
    skip: _Tally,
) -> list[dict[str, Any]]:
    """One pseudo-event per leg (spec section 9), same symbol, class and hold; reasons by the leg's year."""
    days = panel.days
    rng = np.random.default_rng(entry.placebo.seed)
    drawn_cells: set[tuple[str, int]] = set()
    rows: list[dict[str, Any]] = []
    for leg in sorted(legs, key=lambda row: (row["session"], row["symbol"], row["event_id"])):
        symbol, year = leg["symbol"], leg["session"].year
        draws = _report_draws if leg["klass"] == WITH_EARNINGS else _standalone_draws
        options = draws(leg, panel, entry)
        if not options:
            skip("placebo_no_session", year)
            continue
        d, report = options[int(rng.integers(len(options)))]
        if (symbol, d) in drawn_cells:
            skip("duplicate_session", year)
            continue
        drawn_cells.add((symbol, d))
        x = d + leg["hold_sessions"] - 1
        measured = gates.measure(symbol, d, days[min(x + 1, len(days) - 1)])
        if isinstance(measured, str):
            skip(measured, year)
            continue
        rows.append(
            {
                **leg,
                "event_id": f"{leg['event_id']}:placebo",
                "role": "placebo",
                "session": days[d],
                "decision_at": decision_at(days[d], inputs.decision_time),
                "F": report,
                "E": None,
                "X": days[x],
                **measured,
                "keyword": None,
                "item_2_02": None,
            }
        )
    return rows


def build_idday_events(inputs: IddayInputs, entry: IddayEntry) -> tuple[pd.DataFrame, dict]:
    """Legs and their placebos (``IDDAY_EVENT_COLUMNS``) with every exclusion by reason and year.

    ``counts["candidates"]`` holds ``idday_candidates``' counts (steps 1-4, by ``F``'s year); ``reasons`` the
    symbol choice and steps 5-7 (by ``D``'s year); placebo reasons are keyed by the leg's decision year.
    """
    missing = [symbol for symbol in inputs.cube.symbols if symbol not in inputs.band_of]
    if missing:
        raise ValueError(f"band_of lacks {len(missing)} cube symbols (first {missing[0]})")
    panel = _Panel(inputs, entry)
    gates = _Gates(panel, inputs, entry)
    candidates, candidate_counts = idday_candidates(inputs, entry)
    by_id = {event.event_id: event for event in inputs.events.events}
    skip, placebo_skip = _Tally(), _Tally()
    legs: list[dict[str, Any]] = []
    for candidate in candidates.to_dict("records"):
        event, d = by_id[candidate["event_id"]], panel.pos_of[candidate["D"]]
        symbol = gates.choose(inputs.issuers[event.cik].symbols, d)
        measured = gates.measure(symbol, d, candidate["E"]) if symbol in panel.col_of else symbol
        if isinstance(measured, str):
            skip(measured, candidate["D"].year)
            continue
        legs.append(
            {
                "event_id": event.event_id,
                "pair_id": event.event_id,
                "role": "leg",
                "symbol": symbol,
                "cik": event.cik,
                "klass": candidate["klass"],
                "band": inputs.band_of[symbol],
                "session": candidate["D"],
                "decision_at": decision_at(candidate["D"], inputs.decision_time),
                "F": candidate["F"],
                "E": candidate["E"],
                "X": candidate["X"],
                "hold_sessions": int(candidate["H"]),
                **measured,
                "keyword": event.keyword,
                "item_2_02": event.item_2_02,
            }
        )
    placebos = _placebos(legs, panel, gates, inputs, entry, placebo_skip)
    frame = pd.DataFrame([*legs, *placebos], columns=list(IDDAY_EVENT_COLUMNS))
    frame = frame.sort_values(["session", "event_id"], kind="stable").reset_index(drop=True)
    for column in ("F", "E", "keyword", "item_2_02"):
        frame[column] = frame[column].astype(object).where(frame[column].notna(), None)
    reasons, reasons_by_year = skip.counts()
    placebo_reasons, placebo_reasons_by_year = placebo_skip.counts()
    counts = {
        "candidates": candidate_counts,
        "legs": len(legs),
        "legs_by_class": dict(sorted(Counter(row["klass"] for row in legs).items())),
        "placebos": len(placebos),
        "reasons": reasons,
        "reasons_by_year": reasons_by_year,
        "placebo_reasons": placebo_reasons,
        "placebo_reasons_by_year": placebo_reasons_by_year,
    }
    return frame, counts


def match_controls(events: pd.DataFrame, inputs: IddayInputs, entry: IddayEntry) -> tuple[pd.DataFrame, dict]:
    """The ``matched_controls`` nearest merged-universe names for every leg and placebo row (spec section 8).

    A candidate is cube-eligible at ``D`` in the row's dollar-volume bucket with a finite 20-session return,
    not another row of the same role on ``D`` and not of the row's issuer; its issuer is mapped, ``visible``,
    uncapped and fetched without failure, with no event date in ``[D - 63, X + 10]`` sessions. A
    ``standalone`` row's candidate has no report in ``[D - 30 sessions, X]`` and one in the 130 sessions
    before ``D``; a ``with_earnings`` row's has one in the row's band and none in ``[F + 1, X]``. The nearest
    by 20-session return to ``D - 1`` win, ties by the cube's ``tiebreak``; fewer candidates leave the row
    unpaired, counted by role, decision year and class.
    """
    panel = _Panel(inputs, entry)
    view, days, control, k = panel.view, panel.days, entry.control, entry.control.matched_controls
    issuer = np.array([inputs.issuer_of.get(symbol, -1) for symbol in view.symbols])
    records_of = [inputs.issuers.get(inputs.issuer_of.get(symbol, -1)) for symbol in view.symbols]
    # An unmapped name has unknown visibility; a 6-K filer, a capped search or a failed document disqualifies.
    admissible = np.array([record is not None and record.visible and record.clean for record in records_of])
    records = [record for record in events.to_dict("records") if record["role"] in MATCHED_ROLES]
    treated: dict[tuple[str, date], set[str]] = defaultdict(set)
    for record in records:
        treated[(record["role"], record["session"])].add(record["symbol"])
    paired = dict.fromkeys(MATCHED_ROLES, 0)
    by_year: dict[str, Counter[int]] = {role: Counter() for role in MATCHED_ROLES}
    by_class: dict[str, Counter[str]] = {role: Counter() for role in MATCHED_ROLES}
    rows: list[dict[str, Any]] = []
    for record in records:
        role, session = record["role"], record["session"]
        row, col = panel.row_of.get(session), panel.col_of.get(record["symbol"])
        d, x = panel.pos_of.get(session), panel.pos_of.get(record["X"])
        if row is None or col is None or d is None or x is None:
            raise ValueError(f"{record['event_id']} is not a cube cell with a session X")
        ret20 = panel.ret20[d]
        if not np.isfinite(ret20[col]):
            raise ValueError(f"{record['event_id']} has no {control.match_return_sessions}-session return")
        bucket = panel.bucket(d)
        mask = view.eligible[row] & (bucket == bucket[col]) & np.isfinite(ret20) & admissible
        mask &= issuer != record["cik"]
        free = (_day(days, d - control.event_free_before_sessions), _day(days, x + control.event_free_after_sessions))
        if record["klass"] == STANDALONE:
            barred = (_day(days, d - control.quiet_before_sessions), days[x])
            needed = (_day(days, d - control.coverage_sessions), session - _DAY)
        elif record["F"] is not None:
            barred = (record["F"] + _DAY, days[x])
            needed = _band(days, record["F"], entry.event.earnings_band_sessions)
        else:
            raise ValueError(f"{record['event_id']} is with_earnings but has no F")
        candidates = []
        for c in np.flatnonzero(mask):
            history = panel.reports.get(int(issuer[c]), _EMPTY)
            if (
                view.symbols[c] not in treated[(role, session)]
                and not _any_inside(panel.events.get(int(issuer[c]), _EMPTY), *free)
                and not _any_inside(history, *barred)
                and _any_inside(history, *needed)
            ):
                candidates.append(c)
        if bucket[col] < 0 or len(candidates) < k:
            by_year[role][session.year] += 1
            by_class[role][record["klass"]] += 1
            continue
        cand = np.array(candidates)
        chosen = cand[np.lexsort((view.tiebreak[row, cand], np.abs(ret20[cand] - ret20[col])))[:k]]
        rows.append(
            {
                "pair_id": record["pair_id"],
                "role": role,
                "session": session,
                "control_symbols": ",".join(view.symbols[c] for c in chosen),
                "hold_sessions": int(record["hold_sessions"]),
            }
        )
        paired[role] += 1
    counts = {
        "paired": paired,
        "unpaired": {role: sum(years.values()) for role, years in by_year.items()},
        "unpaired_by_year": {role: dict(sorted(years.items())) for role, years in by_year.items()},
        "unpaired_by_class": {role: dict(sorted(classes.items())) for role, classes in by_class.items()},
    }
    return pd.DataFrame(rows, columns=list(CONTROL_CANDIDATE_COLUMNS)), counts
