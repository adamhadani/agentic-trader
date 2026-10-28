"""Measured net-excess outcomes of journaled suggestion-scan candidates, per ``(strategy, timeframe, direction)``.

Pure: a frame labelled by ``label_journaled`` in, one JSON-safe snapshot payload out. The daemon's
``card_stats`` worker persists it once per New York date; cards read it back through
``agentic_trader.execution.card_evidence``. Nothing here fetches bars, writes or ranks.

The live record is the matured, de-duplicated suggestion-scan cohort in the ``net_excess_r_v1`` unit,
shrunk toward the frozen replay prior (``card_prior``); see docs/card-evidence.md.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import pandas as pd
from pydantic import AwareDatetime, BaseModel

from agentic_trader.execution.durable import EventKind, ScanTrigger
from agentic_trader.market.session import ET_TZ, MarketHolidayCalendar
from agentic_trader.research.setups.card_prior import posterior
from agentic_trader.research.setups.labels import BracketHit, SetupLevels
from agentic_trader.research.setups.net_excess import COST_FLOOR_BPS, NET_EXCESS_UNIT
from agentic_trader.research.setups.outcomes import FETCH_FAILED_HIT
from agentic_trader.research.setups.ranker import finite_or_none


if TYPE_CHECKING:
    from collections.abc import Mapping

    from agentic_trader.research.setups.card_prior import CardStatsPrior


__all__ = [
    "AGGREGATE",
    "CARD_STATS_STREAM",
    "LABELLER_PROTOCOL",
    "CardKeyStats",
    "CardPopulation",
    "CardStatsSnapshot",
    "card_population",
    "card_stats_key",
    "complete_sessions_between",
    "compute_card_stats",
]

CARD_STATS_STREAM = "card_stats"
# The outcome labeller's frozen protocol (config/research/setup-outcomes-v1.json).
LABELLER_PROTOCOL = "setup-outcomes-v1"
# The all-candidates key: strategy, timeframe and direction are all "*".
AGGREGATE = "*"


def card_stats_key(et_date: date) -> str:
    """The idempotent journal key of one New York date's snapshot."""
    return f"card_stats/{NET_EXCESS_UNIT}/{et_date.isoformat()}"


def complete_sessions_between(start: date, end: date) -> int:
    """Exchange sessions strictly between two dates: weekdays that are not equity holidays."""
    count, day = 0, start + timedelta(days=1)
    while day < end:
        if day.weekday() < 5 and not MarketHolidayCalendar.is_equity_holiday(day)[0]:
            count += 1
        day += timedelta(days=1)
    return count


@dataclass(frozen=True)
class CardPopulation:
    """The suggestion-scan cohort handed to the labeller.

    ``events`` are copies of the journal's events with only valid, de-duplicated candidates;
    ``malformed`` counts dropped candidates per (strategy, timeframe, direction), a missing part as
    ``"?"``; ``considered`` is the number of suggestion-scan universe events read.
    """

    events: list[dict[str, Any]]
    malformed: dict[tuple[str, str, str], int]
    considered: int


def _decided_at(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except TypeError, ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except TypeError, ValueError:
        return None
    return number if math.isfinite(number) else None


def _plannable(candidate: Mapping[str, Any]) -> bool:
    """True when the labeller can judge the candidate: finite entry, stop and target forming a valid bracket."""
    levels = [_finite(candidate.get(name)) for name in ("entry", "stop", "target")]
    if None in levels:
        return False
    entry, stop, target = (float(level) for level in levels if level is not None)
    try:
        SetupLevels(direction=str(candidate.get("direction")), entry=entry, stop=stop, target=target)
    except ValueError:
        return False
    return True


def card_population(events: list[dict[str, Any]], *, excluded: frozenset[str]) -> CardPopulation:
    """Suggestion-scan universe candidates, malformed ones counted and dropped, one per setup per session.

    A setup is (contract, strategy, timeframe, direction, New York decision date); the earliest
    decision in the session is kept, so the 10:35 scan wins over the 14:35 one and the duplicate
    costs no bar requests. Contracts in ``excluded`` (broad index ETFs) are never carded.
    """
    scans: list[tuple[int, datetime, Mapping[str, Any], dict[str, Any]]] = []
    for index, event in enumerate(events):
        if event.get("kind") != EventKind.SCAN_CANDIDATES_RANKED:
            continue
        payload = event.get("payload") or {}
        if payload.get("scope") != "universe" or payload.get("trigger") != ScanTrigger.SUGGESTION_SCAN:
            continue
        decided_at = _decided_at(payload.get("decided_at"))
        if decided_at is None:
            continue
        scans.append((index, decided_at, payload, event))
    malformed: dict[tuple[str, str, str], int] = {}
    seen: set[tuple[str, str, str, str, date]] = set()
    kept: dict[int, list[dict[str, Any]]] = {}
    for index, decided_at, payload, _event in sorted(scans, key=lambda scan: scan[1]):
        session = decided_at.astimezone(ET_TZ).date()
        for candidate in payload.get("candidates") or []:
            contract = candidate.get("contract")
            if not contract or contract in excluded:
                continue
            strategy, timeframe, direction = (
                str(candidate.get(name) or "?") for name in ("strategy", "timeframe", "direction")
            )
            setup = (contract, strategy, timeframe, direction, session)
            if setup in seen:
                continue
            seen.add(setup)
            if not _plannable(candidate):
                key = (strategy, timeframe, direction)
                malformed[key] = malformed.get(key, 0) + 1
                continue
            kept.setdefault(index, []).append({**candidate})
    population = [
        {**event, "payload": {**payload, "candidates": kept[index]}}
        for index, _decided_at_, payload, event in scans
        if index in kept
    ]
    return CardPopulation(events=population, malformed=malformed, considered=len(scans))


class CardKeyStats(BaseModel, frozen=True, allow_inf_nan=False):
    """One ``(strategy, timeframe, direction)`` row. Rates are over mature labels; fetch failures are excluded.

    Every float is finite or None: a non-finite value (a JSON ``"NaN"`` string parses as one) fails
    validation, so the reader treats the whole snapshot as unreadable rather than rendering it.
    The v2 fields default, so a v1 payload still validates.
    """

    strategy: str
    direction: str
    n_mature: int
    n_immature: int
    n_fetch_failed: int
    target_rate: float | None
    stop_rate: float | None
    timeout_rate: float | None
    mean_r_cost: float | None
    mean_timeout_r: float | None
    median_holding_sessions: float | None
    first_decided_at: AwareDatetime | None
    last_decided_at: AwareDatetime | None
    timeframe: str = AGGREGATE
    n_young: int = 0
    n_unresolved: int = 0
    n_malformed: int = 0
    n_no_beta: int = 0
    live_mean: float | None = None
    live_se: float | None = None
    posterior_mean: float | None = None
    posterior_se: float | None = None
    prior_mean: float | None = None
    prior_se: float | None = None
    prior_weight: float | None = None
    mean_name_cost_bps: float | None = None
    prior_n_rows: int = 0
    prior_source: Literal["replay", "skeptical"] | None = None


class CardStatsSnapshot(BaseModel, frozen=True, allow_inf_nan=False):
    """The persisted ``card_stats_snapshot`` payload. Unknown keys are ignored, so a rollback still reads it."""

    computed_at: AwareDatetime
    window_start: date
    window_end: date
    feed: str
    cost_bps_per_side: float
    max_hold_sessions: int
    labeller_protocol: str
    code_revision: str
    events_considered: int
    rows_labelled: int
    keys: tuple[CardKeyStats, ...] = ()
    unit: str | None = None
    prior_sha256: str | None = None
    prior_window_start: str | None = None
    prior_window_end: str | None = None
    prior_feed: str | None = None
    cohort_end: date | None = None
    n_malformed: int = 0

    @property
    def snapshot_key(self) -> str:
        """The journal key this snapshot was written under: the v1 key when it carries no unit."""
        if self.unit is None:
            return f"card_stats/{self.window_end.isoformat()}"
        return card_stats_key(self.window_end)

    def stats(self, strategy: str, timeframe: str, direction: str) -> CardKeyStats | None:
        return next(
            (
                row
                for row in self.keys
                if (row.strategy, row.timeframe, row.direction) == (strategy, timeframe, direction)
            ),
            None,
        )


def _session_dates(frame: pd.DataFrame) -> pd.Series:
    """Each row's New York decision date."""
    decided = pd.to_datetime(frame["decided_at"], utc=True)
    return decided.dt.tz_convert(ET_TZ).dt.date


def _is_fetch_failure(rows: pd.DataFrame) -> pd.Series:
    failed = rows["hit"].astype(str) == FETCH_FAILED_HIT
    for column in ("beta_reason", "cost_reason"):
        failed |= rows[column].astype(str).str.startswith("fetch_failed")
    return failed


def _key_stats(
    rows: pd.DataFrame,
    eligible: pd.Series,
    key: tuple[str, str, str],
    *,
    malformed: int,
    prior: CardStatsPrior | None,
) -> dict[str, Any]:
    strategy, timeframe, direction = key
    hit = rows["hit"].astype(str)
    net = pd.to_numeric(rows["net_excess_r"], errors="coerce")
    net = net.where(np.isfinite(net))
    fetch_failed = eligible & _is_fetch_failure(rows)
    unresolved = eligible & (hit == BracketHit.IMMATURE.value) & ~fetch_failed
    resolved = eligible & ~unresolved & ~fetch_failed & (hit != BracketHit.IMMATURE.value)
    no_beta = resolved & net.isna() & ~fetch_failed
    mature_mask = eligible & net.notna()
    mature = rows.loc[mature_mask]
    mature_hit = hit[mature_mask]
    n_mature = len(mature)
    r_cost = pd.to_numeric(mature["r_cost"], errors="coerce")
    timeout_r = r_cost[mature_hit == BracketHit.TIMEOUT.value]
    holding = pd.to_numeric(mature["holding_sessions"], errors="coerce")
    costs = pd.to_numeric(mature["name_cost_bps"], errors="coerce")
    decided = pd.to_datetime(mature["decided_at"], utc=True).dropna()
    live = net[mature_mask]
    live_mean = finite_or_none(live.mean()) if n_mature else None
    live_se = finite_or_none(live.std(ddof=1) / math.sqrt(n_mature)) if n_mature >= 2 else None

    def rate(outcome: BracketHit) -> float | None:
        return finite_or_none((mature_hit == outcome.value).mean()) if n_mature else None

    row: dict[str, Any] = {
        "strategy": strategy,
        "timeframe": timeframe,
        "direction": direction,
        "n_mature": n_mature,
        "n_immature": int((~eligible).sum() + unresolved.sum()),
        "n_young": int((~eligible).sum()),
        "n_unresolved": int(unresolved.sum()),
        "n_fetch_failed": int(fetch_failed.sum()),
        "n_malformed": malformed,
        "n_no_beta": int(no_beta.sum()),
        "target_rate": rate(BracketHit.TARGET),
        "stop_rate": rate(BracketHit.STOP),
        "timeout_rate": rate(BracketHit.TIMEOUT),
        "mean_r_cost": finite_or_none(r_cost.mean()) if n_mature else None,
        "mean_timeout_r": finite_or_none(timeout_r.mean()) if len(timeout_r) else None,
        "median_holding_sessions": finite_or_none(holding.median()) if n_mature else None,
        "first_decided_at": decided.min().isoformat() if len(decided) else None,
        "last_decided_at": decided.max().isoformat() if len(decided) else None,
        "live_mean": live_mean,
        "live_se": live_se,
        "mean_name_cost_bps": finite_or_none(costs.mean()) if costs.notna().any() else None,
    }
    if prior is not None:
        entry = prior.entry(strategy, timeframe, direction)
        post_mean, post_se, k = posterior(n_mature, live_mean, entry)
        row |= {
            "posterior_mean": post_mean,
            "posterior_se": post_se,
            "prior_mean": entry.mean,
            "prior_se": math.sqrt(entry.tau**2 + entry.se_session**2),
            "prior_weight": k,
            "prior_n_rows": entry.n_rows,
            "prior_source": "replay" if prior.is_replay(strategy, timeframe, direction) else "skeptical",
        }
    return row


def compute_card_stats(
    frame: pd.DataFrame,
    *,
    prior: CardStatsPrior,
    prior_sha256: str,
    malformed: Mapping[tuple[str, str, str], int],
    window_start: date,
    window_end: date,
    feed: str,
    max_hold_sessions: int,
    code_revision: str,
    now: datetime,
    events_considered: int,
) -> dict[str, Any]:
    """One snapshot payload from a ``label_journaled`` frame of the matured, de-duplicated cohort.

    A row is eligible only when at least ``max_hold_sessions + 1`` complete exchange sessions separate
    its decision date from ``window_end``; younger rows count in ``n_young`` and nowhere else. Keys are
    the union of the frame's, the prior's and the malformed ones, plus the aggregate (no prior or
    posterior). The payload is validated as a ``CardStatsSnapshot`` before it is returned, so the
    writer never persists what the reader would refuse, and every number is finite or None (the
    journal encodes with ``allow_nan=False``).
    """
    frame = frame.reset_index(drop=True)
    if frame.empty:
        eligible = pd.Series([], dtype=bool)
        sessions = pd.Series([], dtype=object)
    else:
        sessions = _session_dates(frame)
        counts = {day: complete_sessions_between(day, window_end) for day in set(sessions)}
        eligible = sessions.map(counts) >= max_hold_sessions + 1
    keyed = frame.dropna(subset=["strategy", "timeframe", "direction"]) if not frame.empty else frame
    live_keys = {
        (str(s), str(t), str(d)) for s, t, d in keyed[["strategy", "timeframe", "direction"]].itertuples(index=False)
    }
    keys = sorted(live_keys | {(k.strategy, k.timeframe, k.direction) for k in prior.keys} | set(malformed))
    rows: list[dict[str, Any]] = []
    for key in keys:
        mask = (
            (frame["strategy"].astype(str) == key[0])
            & (frame["timeframe"].astype(str) == key[1])
            & (frame["direction"].astype(str) == key[2])
            & frame[["strategy", "timeframe", "direction"]].notna().all(axis=1)
            if not frame.empty
            else pd.Series([], dtype=bool)
        )
        rows.append(_key_stats(frame.loc[mask], eligible[mask], key, malformed=malformed.get(key, 0), prior=prior))
    aggregate = (AGGREGATE, AGGREGATE, AGGREGATE)
    aggregate_row = _key_stats(frame, eligible, aggregate, malformed=sum(malformed.values()), prior=None)
    rows.append(aggregate_row)
    cohort_end = max((day for day, ok in zip(sessions, eligible, strict=True) if ok), default=None)
    payload: dict[str, Any] = {
        "computed_at": now.isoformat(),
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "feed": feed,
        "cost_bps_per_side": float(COST_FLOOR_BPS),
        "max_hold_sessions": int(max_hold_sessions),
        "labeller_protocol": LABELLER_PROTOCOL,
        "code_revision": code_revision,
        "events_considered": int(events_considered),
        "rows_labelled": len(frame),
        "keys": rows,
        "unit": NET_EXCESS_UNIT,
        "prior_sha256": prior_sha256,
        "prior_window_start": prior.source.get("window_start"),
        "prior_window_end": prior.source.get("window_end"),
        "prior_feed": prior.source.get("feed"),
        "cohort_end": cohort_end.isoformat() if cohort_end is not None else None,
        "n_malformed": sum(malformed.values()),
    }
    CardStatsSnapshot.model_validate(payload)
    return payload
