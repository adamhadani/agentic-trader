"""Read-only measurement of the live PEAD catalog probe over journaled ``pead_decision`` events.

Mirrors ``research/setups/outcomes.py`` for the catalog probe: it never sends orders or
notifications and never writes to the database. Every scheduled 10:35 scan appends one
``pead_decision`` event per session (``agent/copilot.py:_journal_drift``), whose payload
carries the study's own JSON-safe events (``pead_live.event_document``) each tagged with
that scan's card outcome. ``copilot cards outcomes`` combines this journal with the
study's own labeller (``pead_study.label_events``) to report a counterfactual R alongside
the probe's actually realized R -- entirely from durable evidence.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

import pandas as pd
from sqlalchemy import select

from agentic_trader.storage.models import SignalRecord
from agentic_trader.storage.probe_state import CLOSED_STATUSES


if TYPE_CHECKING:
    from agentic_trader.storage.db import SignalDatabase


__all__ = ["decision_frame", "probe_signals", "summarize_probe"]

# Realized and counterfactual R are both noisy over a handful of trades; this is a
# reporting caveat only, never a gate -- the probe's own kill rule lives elsewhere
# (storage/probe_state.py).
_MIN_CLOSED_FOR_COMPARISON = 10

PEAD_LONG_STRATEGY = "pead_long"

# One row per journaled event: the study's own EVENT_COLUMNS plus the scan-time outcome
# and the journaling scan's ID. Declared explicitly so an empty result still has every
# column `label_events` and `summarize_probe` expect.
_DECISION_COLUMNS = (
    "scan_id",
    "symbol",
    "report_date",
    "session",
    "decision_at",
    "z",
    "surprise_pct",
    "surprise_pct_reported",
    "atr",
    "median_dollar_volume",
    "reference",
    "leg",
    "surprise_long",
    "surprise_short",
    "reaction_long",
    "reaction_short",
    "outcome",
)


def decision_frame(payloads: list[dict[str, Any]]) -> pd.DataFrame:
    """One row per journaled LONG PEAD event, deduped by the journaling scan's ID.

    ``payloads`` is one ``pead_decision`` event's payload per journaled scan. A repeated
    ``scan_id`` -- a reader re-walking the same stream, not a second decision -- is kept
    once. ``session``/``report_date`` round-trip to ``date`` and ``decision_at`` to an
    aware ``datetime``, undoing the JSON-safe ISO strings ``pead_live.event_document``
    wrote before journaling.
    """
    seen_scans: set[Any] = set()
    rows: list[dict[str, Any]] = []
    for payload in payloads:
        scan_id = payload.get("scan_id")
        if scan_id in seen_scans:
            continue
        seen_scans.add(scan_id)
        for event in payload.get("events") or []:
            if event.get("leg") != "LONG":
                continue
            row = dict(event)
            row["scan_id"] = scan_id
            row["session"] = date.fromisoformat(row["session"])
            row["report_date"] = date.fromisoformat(row["report_date"]) if row.get("report_date") else None
            row["decision_at"] = datetime.fromisoformat(row["decision_at"])
            rows.append(row)
    return pd.DataFrame(rows, columns=list(_DECISION_COLUMNS))


def summarize_probe(
    decisions: pd.DataFrame,
    labels: pd.DataFrame,
    signals: list[dict[str, Any]],
    forward: dict[str, Any] | None,
    study_mean_r: float | None,
) -> dict[str, Any]:
    """Journaled counts, realized R over closed probe signals, and the study's counterfactual label.

    ``labels`` is the study's own ``label_events`` output, already narrowed to the LONG
    leg (``direction == "LONG"`` and ``is_leg``); by construction it holds only mature
    outcomes (an immature event never becomes a row there), so ``len(labels)`` is exactly
    the "mature" count. ``signals`` is one row per ``pead_long`` ``SignalRecord`` (as
    ``SignalRecord.to_dict()`` returns it): ``executed`` counts a non-null ``executed_at``,
    and ``closed`` counts the durable closed statuses (``storage/probe_state.CLOSED_STATUSES``).
    """
    carded = int((decisions["outcome"] == "sent").sum()) if not decisions.empty else 0
    executed_signals = [signal for signal in signals if signal.get("executed_at") is not None]
    closed_signals = [signal for signal in signals if signal.get("status") in CLOSED_STATUSES]
    ratios = [
        signal["realized_pnl"] / signal["risk_dollars"]
        for signal in closed_signals
        if signal.get("realized_pnl") is not None and signal.get("risk_dollars")
    ]
    realized_mean_r = float(sum(ratios) / len(ratios)) if ratios else None
    counterfactual = (
        {"mature": len(labels), "mean_r_cost": float(labels["r_cost"].mean())}
        if labels is not None and not labels.empty
        else None
    )
    return {
        "events": len(decisions),
        "carded": carded,
        "executed": len(executed_signals),
        "closed": len(closed_signals),
        "realized_mean_r": realized_mean_r,
        "counterfactual": counterfactual,
        "forward": forward,
        "study_mean_r": study_mean_r,
        "note": (
            "Realized and counterfactual results are not comparable until the probe has "
            f"at least {_MIN_CLOSED_FOR_COMPARISON} closed trades."
        ),
    }


async def probe_signals(db: SignalDatabase, since: datetime) -> list[dict[str, Any]]:
    """``pead_long`` signals in ``db``'s own scope, recorded since ``since``."""
    async with db.session_factory() as session:
        stmt = select(SignalRecord).where(
            *db._scope(), SignalRecord.strategy == PEAD_LONG_STRATEGY, SignalRecord.timestamp >= since
        )
        result = await session.execute(stmt)
        return [record.to_dict() for record in result.scalars().all()]
