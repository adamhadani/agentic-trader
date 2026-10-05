"""Fill slippage and tap age per sent card, read from the entry workflow's own journal.

Read-only: signal rows, ``entry_resolved`` events on the card's entry work item and the
applied ``execute`` tap assessment. Never infers a fill from a request and never
substitutes a zero for missing evidence.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from agentic_trader.execution.durable import EventKind
from agentic_trader.execution.freshness import CardOutcome
from agentic_trader.storage.db import SignalDatabase


__all__ = ["collect_execution", "entry_fill", "execute_tap", "fill_slip_r", "summarize_execution"]

COLUMNS = (
    "signal_id",
    "contract",
    "direction",
    "status",
    "planned_entry",
    "stop",
    "fill_price",
    "filled_quantity",
    "fill_slip_r",
    "tap_age_seconds",
    "tap_r_consumed",
)


def fill_slip_r(
    direction: str, planned_entry: float | None, stop: float | None, fill_price: float | None
) -> float | None:
    """Adverse-positive slippage of the fill against the planned entry, in the card's R units."""
    if planned_entry is None or stop is None or fill_price is None:
        return None
    risk_unit = abs(float(planned_entry) - float(stop))
    if not risk_unit:
        return None
    adverse = float(fill_price) - float(planned_entry)
    if direction != "LONG":
        adverse = -adverse
    return adverse / risk_unit


def entry_fill(events: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    """``(fill_price, filled_quantity)`` from the last ``entry_resolved`` event, else ``(None, None)``."""
    resolved = [e for e in events if e.get("kind") == EventKind.ENTRY_RESOLVED]
    if not resolved:
        return None, None
    result = (resolved[-1].get("payload") or {}).get("result") or {}
    return result.get("fill_price"), result.get("filled_quantity")


def execute_tap(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The applied ``execute`` tap assessment's age and consumed R, else None."""
    for event in reversed(events):
        payload = event.get("payload") or {}
        if (
            event.get("kind") == EventKind.CARD_TAP_ASSESSED
            and payload.get("outcome") == CardOutcome.EXECUTE
            and payload.get("applied")
        ):
            return {"age_seconds": payload.get("age_seconds"), "r_consumed": payload.get("r_consumed")}
    return None


def _value(value: Any) -> Any:
    """Missing frame cells arrive as NaN; treat them as absent."""
    return None if pd.isna(value) else value


async def collect_execution(db: SignalDatabase, frame: pd.DataFrame) -> pd.DataFrame:
    """One row per sent card with a ``signal_id`` in ``frame`` (the outcome report's rows)."""
    sent = frame.loc[frame["signal_id"].notna()] if "signal_id" in frame else frame.iloc[0:0]
    if sent.empty:
        return pd.DataFrame(columns=list(COLUMNS))
    signal_ids = [int(value) for value in sent["signal_id"]]
    item_ids = await db.workflows.entry_item_ids(signal_ids)
    rows: list[dict[str, Any]] = []
    for record in sent.to_dict("records"):
        signal_id = int(record["signal_id"])
        signal = await db.get_signal_by_id(signal_id) or {}
        fill_price, filled_quantity = (None, None)
        if (item_id := item_ids.get(signal_id)) is not None:
            fill_price, filled_quantity = entry_fill(await db.workflows.events(stream=f"entry/{item_id}"))
        tap = execute_tap(await db.workflows.events(stream=f"card/{signal_id}"))
        planned_entry = _value(record.get("entry"))
        stop = _value(record.get("stop"))
        rows.append(
            {
                "signal_id": signal_id,
                "contract": record.get("contract"),
                "direction": record.get("direction"),
                "status": signal.get("status"),
                "planned_entry": planned_entry,
                "stop": stop,
                "fill_price": fill_price,
                "filled_quantity": filled_quantity,
                "fill_slip_r": fill_slip_r(str(record.get("direction")), planned_entry, stop, fill_price),
                "tap_age_seconds": tap["age_seconds"] if tap else None,
                "tap_r_consumed": tap["r_consumed"] if tap else None,
            }
        )
    return pd.DataFrame(rows, columns=list(COLUMNS))


def summarize_execution(frame: pd.DataFrame) -> dict[str, Any]:
    executed = frame.loc[frame["status"] == "EXECUTED"] if not frame.empty else frame
    with_fill = executed.dropna(subset=["fill_price"]) if not executed.empty else executed
    taps = frame.dropna(subset=["tap_age_seconds"]) if not frame.empty else frame
    return {
        "cards": len(frame),
        "executed": len(executed),
        "with_fill_evidence": len(with_fill),
        "missing_fill_evidence": int(len(executed) - len(with_fill)),
        "mean_fill_slip_r": float(with_fill["fill_slip_r"].mean()) if len(with_fill) else None,
        "mean_tap_age_seconds": float(taps["tap_age_seconds"].mean()) if len(taps) else None,
    }
