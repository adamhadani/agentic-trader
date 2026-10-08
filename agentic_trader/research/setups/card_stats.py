"""Measured outcomes of journaled suggestion-scan candidates, per ``(strategy, direction)``.

Pure: a frame labelled by ``label_journaled`` in, one JSON-safe snapshot payload out. The
daemon's ``card_stats`` worker persists it once per New York date; cards read it back through
``agentic_trader.execution.card_evidence``. Nothing here fetches bars, writes or ranks.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd
from pydantic import AwareDatetime, BaseModel

from agentic_trader.research.setups.labels import BracketHit
from agentic_trader.research.setups.outcomes import FETCH_FAILED_HIT
from agentic_trader.research.setups.ranker import finite_or_none


__all__ = [
    "AGGREGATE",
    "CARD_STATS_STREAM",
    "LABELLER_PROTOCOL",
    "CardKeyStats",
    "CardStatsSnapshot",
    "card_stats_key",
    "compute_card_stats",
]

CARD_STATS_STREAM = "card_stats"
# The outcome labeller's frozen protocol (config/research/setup-outcomes-v1.json).
LABELLER_PROTOCOL = "setup-outcomes-v1"
# The all-candidates key: strategy and direction are both "*".
AGGREGATE = "*"


def card_stats_key(et_date: date) -> str:
    """The idempotent journal key of one New York date's snapshot."""
    return f"card_stats/{et_date.isoformat()}"


class CardKeyStats(BaseModel, frozen=True, allow_inf_nan=False):
    """One ``(strategy, direction)`` row. Rates are over mature labels; fetch failures are excluded.

    Every float is finite or None: a non-finite value (a JSON ``"NaN"`` string parses as one) fails
    validation, so the reader treats the whole snapshot as unreadable rather than rendering it.
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


class CardStatsSnapshot(BaseModel, frozen=True):
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

    @property
    def snapshot_key(self) -> str:
        return card_stats_key(self.window_end)

    def stats(self, strategy: str, direction: str) -> CardKeyStats | None:
        return next((row for row in self.keys if row.strategy == strategy and row.direction == direction), None)


def _key_stats(rows: pd.DataFrame, strategy: str, direction: str) -> dict[str, Any]:
    hit = rows["hit"].astype(str)
    fetch_failed = hit == FETCH_FAILED_HIT
    immature = hit == BracketHit.IMMATURE.value
    mature = rows.loc[~(fetch_failed | immature)]
    mature_hit = mature["hit"].astype(str)
    n_mature = len(mature)
    r_cost = pd.to_numeric(mature["r_cost"], errors="coerce")
    timeout_r = r_cost[mature_hit == BracketHit.TIMEOUT.value]
    holding = pd.to_numeric(mature["holding_sessions"], errors="coerce")
    decided = pd.to_datetime(rows["decided_at"], utc=True).dropna()

    def rate(outcome: BracketHit) -> float | None:
        return finite_or_none((mature_hit == outcome.value).mean()) if n_mature else None

    return {
        "strategy": strategy,
        "direction": direction,
        "n_mature": n_mature,
        "n_immature": int(immature.sum()),
        "n_fetch_failed": int(fetch_failed.sum()),
        "target_rate": rate(BracketHit.TARGET),
        "stop_rate": rate(BracketHit.STOP),
        "timeout_rate": rate(BracketHit.TIMEOUT),
        "mean_r_cost": finite_or_none(r_cost.mean()) if n_mature else None,
        "mean_timeout_r": finite_or_none(timeout_r.mean()) if len(timeout_r) else None,
        "median_holding_sessions": finite_or_none(holding.median()) if n_mature else None,
        "first_decided_at": decided.min().isoformat() if len(decided) else None,
        "last_decided_at": decided.max().isoformat() if len(decided) else None,
    }


def compute_card_stats(
    frame: pd.DataFrame,
    *,
    window_start: date,
    window_end: date,
    feed: str,
    cost_bps: float,
    max_hold_sessions: int,
    code_revision: str,
    now: datetime,
    events_considered: int,
) -> dict[str, Any]:
    """One snapshot payload from a ``label_journaled`` frame: every ``(strategy, direction)`` plus ``("*", "*")``.

    Rows without a strategy or direction count only in the aggregate; an empty frame gives no
    keys. The payload is validated as a ``CardStatsSnapshot`` before it is returned, so the writer
    never persists what the reader would refuse, and every number is finite or None (the journal
    encodes with ``allow_nan=False``).
    """
    keys = [
        _key_stats(rows, str(strategy), str(direction))
        for (strategy, direction), rows in frame.groupby(["strategy", "direction"], sort=True)
    ]
    if not frame.empty:
        keys.append(_key_stats(frame, AGGREGATE, AGGREGATE))
    payload: dict[str, Any] = {
        "computed_at": now.isoformat(),
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "feed": feed,
        "cost_bps_per_side": float(cost_bps),
        "max_hold_sessions": int(max_hold_sessions),
        "labeller_protocol": LABELLER_PROTOCOL,
        "code_revision": code_revision,
        "events_considered": int(events_considered),
        "rows_labelled": len(frame),
        "keys": keys,
    }
    CardStatsSnapshot.model_validate(payload)
    return payload
