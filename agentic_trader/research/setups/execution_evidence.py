"""Fill slippage and tap age per sent card, read from the entry workflow's own journal.

Read-only: signal rows, the broker's ``order_observed`` events on the card's entry order
(else an ``entry_resolved`` acknowledgement that itself carried a fill), and the applied
``execute`` tap assessment. A re-priced card is followed to its replacement, and the
planned levels are the card as sent (its ``raw_response``). Never infers a fill from a
request and never substitutes a zero for missing evidence.
"""

from __future__ import annotations

import json
import math
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd

from agentic_trader.constants import SignalStatus
from agentic_trader.execution.auto_entry import AUTO_ENTRY_KEY
from agentic_trader.execution.durable import EntryTrigger, EventKind, WorkStatus
from agentic_trader.execution.freshness import CardOutcome
from agentic_trader.storage.db import SignalDatabase


__all__ = [
    "ENTERED_STATUSES",
    "collect_execution",
    "entry_fill",
    "execute_tap",
    "fill_slip_r",
    "observed_fill",
    "operator_action",
    "reprice_target",
    "summarize_execution",
]

# A card whose entry the broker accepted: still open or resting (EXECUTED is set at acceptance,
# not at the fill) or since closed (any CLOSED_*). Whether it filled is a separate question.
ENTERED_STATUSES = frozenset(
    {SignalStatus.EXECUTED, SignalStatus.CLOSED_WIN, SignalStatus.CLOSED_LOSS, SignalStatus.CLOSED_MANUAL}
)

# Where a fill was read: the broker's own order observations (exact, the Alpaca path) or an
# entry acknowledgement that itself carried a fill (the paper simulator's immediate fills).
FILL_SOURCES = ("order_observed", "entry_resolved")

# A REPRICE tap expires a card and records its replacement; follow at most this many.
MAX_REPRICE_HOPS = 5

COLUMNS = (
    "signal_id",
    "final_signal_id",
    "repriced",
    "contract",
    "direction",
    "status",
    "planned_entry",
    "stop",
    "planned_source",
    "fill_price",
    "filled_quantity",
    "fill_source",
    "fill_slip_r",
    "tap_age_seconds",
    "tap_r_consumed",
    "auto_marked",
    "entry_trigger",
    "operator_action",
)

# Who entered a card: the auto-entry sweep, an operator tap, or nobody (no entry item).
NO_ENTRY = "none"


def fill_slip_r(
    direction: str, planned_entry: float | None, stop: float | None, fill_price: float | None
) -> float | None:
    """Adverse-positive slippage of the fill against the planned entry, in the card's R units."""
    if direction not in ("LONG", "SHORT") or planned_entry is None or stop is None or fill_price is None:
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


def _decimal(value: Any) -> Decimal | None:
    """A finite Decimal from a wire string/number, else None."""
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def observed_fill(events: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    """``(average_fill_price, filled_quantity)`` from the latest ``order_observed`` fill, else ``(None, None)``.

    Observations carry the broker's cumulative state and REST snapshots can arrive after
    stream events, so "latest" is the largest cumulative ``filled_quantity`` (journal
    order breaks ties), as the order projection itself never lets that quantity decrease.
    An observation without an average fill price is no price evidence and is never inferred.
    """
    best: tuple[Decimal, int, Decimal | None] | None = None
    for position, event in enumerate(events):
        if event.get("kind") != EventKind.ORDER_OBSERVED:
            continue
        payload = event.get("payload") or {}
        quantity = _decimal(payload.get("filled_quantity"))
        if quantity is None or quantity <= 0:
            continue
        if best is None or (quantity, position) > best[:2]:
            best = (quantity, position, _decimal(payload.get("average_fill_price")))
    if best is None or best[2] is None:
        return None, None
    return float(best[2]), float(best[0])


def execute_tap(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The applied ``execute`` tap assessment's age and consumed R, else None.

    The auto-entry sweep writes the same assessment with ``entry_trigger: auto``; that is no operator
    tap and is skipped. An assessment that predates the key is a tap.
    """
    for event in reversed(events):
        payload = event.get("payload") or {}
        if (
            event.get("kind") == EventKind.CARD_TAP_ASSESSED
            and payload.get("outcome") == CardOutcome.EXECUTE
            and payload.get("applied")
            and payload.get("entry_trigger", EntryTrigger.TAP) == EntryTrigger.TAP
        ):
            return {"age_seconds": payload.get("age_seconds"), "r_consumed": payload.get("r_consumed")}
    return None


def reprice_target(events: list[dict[str, Any]]) -> int | None:
    """The replacement signal id of the latest applied reprice tap, else None."""
    for event in reversed(events):
        payload = event.get("payload") or {}
        if (
            event.get("kind") == EventKind.CARD_TAP_ASSESSED
            and payload.get("outcome") == CardOutcome.REPRICE
            and payload.get("applied")
            and payload.get("new_signal_id") is not None
        ):
            try:
                return int(payload["new_signal_id"])
            except TypeError, ValueError:
                return None
    return None


async def _follow_reprices(db: SignalDatabase, signal_id: int) -> tuple[int, list[dict[str, Any]]]:
    """The final card of a reprice chain and its own card events; bounded, and a cycle stops it."""
    current, seen = signal_id, {signal_id}
    events = await db.workflows.events(stream=f"card/{current}")
    for _ in range(MAX_REPRICE_HOPS):
        target = reprice_target(events)
        if target is None or target in seen:
            break
        current = target
        seen.add(current)
        events = await db.workflows.events(stream=f"card/{current}")
    return current, events


def _value(value: Any) -> Any:
    """Missing frame cells arrive as NaN; treat them as absent."""
    return None if pd.isna(value) else value


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except TypeError, ValueError:
        return None
    return number if math.isfinite(number) else None


def _planned_levels(raw_response: Any, record: dict[str, Any]) -> tuple[float | None, float | None, str]:
    """``(planned_entry, stop, source)`` of the bracket that was actually sent.

    The signal's ``raw_response`` is the evaluation as sent (the LLM's stop, a re-priced
    card's new entry), recorded before any fill overwrite or stop ratchet. Only when it is
    missing or unparseable fall back to the journaled candidate: its entry, and its applied
    LLM stop when recorded, else its deterministic stop.
    """
    try:
        sent = json.loads(raw_response) if isinstance(raw_response, str) else None
    except ValueError:
        sent = None
    if isinstance(sent, dict):
        entry, stop = _finite(sent.get("entry_price")), _finite(sent.get("stop_loss"))
        if entry is not None and stop is not None:
            return entry, stop, "raw_response"
    stop = _value(record.get("llm_stop_loss"))
    if stop is None:
        stop = _value(record.get("stop"))
    return _value(record.get("entry")), stop, "journal"


def operator_action(
    *,
    marked: bool,
    entry_trigger: str,
    status: str | None,
    work_status: str | None,
    filled: bool,
    operator_cancelled: bool,
    attempt: str | None,
) -> str:
    """What the operator (or the auto-entry sweep) did with one sent card; the first matching rule wins.

    A card recorded with the ``auto_entry`` marker (``marked``):

    - ``cancelled``: an operator-cause ``entry_cancel`` exists (a deadline cancel is not an operator act);
    - ``auto_entered``: the entry item's trigger is ``auto``, unless that entry was later rejected
      without a fill, which is ``auto_refused``;
    - ``tapped``: a tap-trigger entry exists (an operator tap that beat the sweep, or that followed its
      refusal or abandonment; such a card is on neither side of kept-vs-vetoed);
    - ``dismissed``: the card was dismissed (before any placement);
    - ``auto_refused``: the sweep's attempt outcome was ``refused``;
    - ``abandoned``: the attempt outcome was ``abandoned`` (the sweep never got to it);
    - ``expired``: the card expired with no attempt journaled;
    - otherwise the unmarked rules below.

    An unmarked (tap-era or ineligible) card: ``cancelled`` when its entry was operator-cancelled,
    ``tapped`` when a tap-trigger entry exists, ``dismissed``, ``expired``, else ``none``.
    """
    entered = entry_trigger != NO_ENTRY
    if operator_cancelled and entered:
        return "cancelled"
    if marked:
        if entry_trigger == EntryTrigger.AUTO:
            rejected = work_status == WorkStatus.REJECTED and not filled
            return "auto_refused" if rejected else "auto_entered"
        if entry_trigger == EntryTrigger.TAP:
            return "tapped"
        if status == SignalStatus.DISMISSED:
            return "dismissed"
        if attempt == "refused":
            return "auto_refused"
        if attempt == "abandoned":
            return "abandoned"
        if status == SignalStatus.EXPIRED and attempt is None and not entered:
            return "expired"
    if entry_trigger == EntryTrigger.TAP:
        return "tapped"
    if status == SignalStatus.DISMISSED:
        return "dismissed"
    if status == SignalStatus.EXPIRED:
        return "expired"
    return "none"


def _attempt_outcome(events: list[dict[str, Any]]) -> str | None:
    """The latest ``auto_entry_attempted`` outcome on a card, else None."""
    for event in reversed(events):
        if event.get("kind") == EventKind.AUTO_ENTRY_ATTEMPTED:
            return (event.get("payload") or {}).get("outcome")
    return None


async def collect_execution(db: SignalDatabase, frame: pd.DataFrame) -> pd.DataFrame:
    """One row per sent card with a ``signal_id`` in ``frame`` (the outcome report's rows).

    A re-priced card is followed to its replacement: status, broker order, fill and the
    applied ``execute`` tap are the final card's, since that is the one that traded.
    """
    sent = frame.loc[frame["signal_id"].notna()] if "signal_id" in frame else frame.iloc[0:0]
    if sent.empty:
        return pd.DataFrame(columns=list(COLUMNS))
    records = sent.to_dict("records")
    chains = [await _follow_reprices(db, int(record["signal_id"])) for record in records]
    final_ids = [final_id for final_id, _ in chains]
    item_ids = await db.workflows.entry_item_ids(final_ids)
    work_states = await db.workflows.entry_work_states(final_ids)
    cancel_causes = await db.workflows.entry_cancel_causes(final_ids)
    rows: list[dict[str, Any]] = []
    for record, (final_id, card_events) in zip(records, chains, strict=True):
        signal_id = int(record["signal_id"])
        direction = str(record.get("direction") or "")
        # A row that is gone keeps its card in the denominator, with no status (counted missing).
        signal = await db.get_signal_by_id(final_id) or {}
        fill_price, filled_quantity, fill_source = None, None, None
        if broker_order_id := signal.get("broker_order_id"):
            fill_price, filled_quantity = observed_fill(await db.workflows.events(stream=f"order/{broker_order_id}"))
            fill_source = "order_observed" if fill_price is not None else None
        if fill_price is None and (item_id := item_ids.get(final_id)) is not None:
            # The acknowledgement counts only when it carries a fill (the simulator's market fills).
            price, quantity = entry_fill(await db.workflows.events(stream=f"entry/{item_id}"))
            if price is not None:
                fill_price, filled_quantity, fill_source = price, quantity, "entry_resolved"
        tap = execute_tap(card_events)
        # The marker and the sweep's attempt belong to the card as sent; the entry itself to the final card.
        sent_card = signal if final_id == signal_id else (await db.get_signal_by_id(signal_id) or {})
        sent_events = card_events if final_id == signal_id else await db.workflows.events(stream=f"card/{signal_id}")
        marked = bool((sent_card.get("decision_provenance") or {}).get(AUTO_ENTRY_KEY))
        work_status, trigger = work_states.get(final_id, (None, None))
        entry_trigger = str(trigger) if trigger is not None else NO_ENTRY
        planned_entry, stop, planned_source = _planned_levels(signal.get("raw_response"), record)
        rows.append(
            {
                "signal_id": signal_id,
                "final_signal_id": final_id,
                "repriced": final_id != signal_id,
                "contract": record.get("contract"),
                "direction": direction,
                "status": signal.get("status"),
                "planned_entry": planned_entry,
                "stop": stop,
                "planned_source": planned_source,
                "fill_price": fill_price,
                "filled_quantity": filled_quantity,
                "fill_source": fill_source,
                "fill_slip_r": fill_slip_r(direction, planned_entry, stop, fill_price),
                "tap_age_seconds": tap["age_seconds"] if tap else None,
                "tap_r_consumed": tap["r_consumed"] if tap else None,
                "auto_marked": marked,
                "entry_trigger": entry_trigger,
                "operator_action": operator_action(
                    marked=marked,
                    entry_trigger=entry_trigger,
                    status=signal.get("status"),
                    work_status=work_status,
                    filled=fill_price is not None,
                    operator_cancelled="operator" in cancel_causes.get(final_id, set()),
                    attempt=_attempt_outcome(sent_events),
                ),
            }
        )
    return pd.DataFrame(rows, columns=list(COLUMNS))


def _mean(values: pd.Series) -> float | None:
    """Mean of the present values; None (never NaN) when there are none."""
    present = pd.to_numeric(values, errors="coerce").dropna()
    return float(present.mean()) if len(present) else None


def summarize_execution(frame: pd.DataFrame) -> dict[str, Any]:
    """Counts over the sent cards: entered, filled, missing evidence, re-priced and missing rows."""
    entered = frame.loc[frame["status"].isin(ENTERED_STATUSES)] if not frame.empty else frame
    with_fill = entered.dropna(subset=["fill_price"]) if not entered.empty else entered
    return {
        "cards": len(frame),
        "entered": len(entered),
        "filled": len(with_fill),
        "missing_fill_evidence": int(len(entered) - len(with_fill)),
        "repriced": int(frame["repriced"].astype(bool).sum()) if not frame.empty else 0,
        "missing_signal_rows": int(frame["status"].isna().sum()) if not frame.empty else 0,
        "fill_source": {
            source: int((with_fill["fill_source"] == source).sum()) if len(with_fill) else 0 for source in FILL_SOURCES
        },
        "mean_fill_slip_r": _mean(with_fill["fill_slip_r"]),
        # An auto entry carries no tap age, but the counts are explicit so they never blur.
        "mean_tap_age_seconds": _mean(frame["tap_age_seconds"]),
        "taps": int((frame["entry_trigger"] == EntryTrigger.TAP).sum()) if not frame.empty else 0,
        "auto_entries": int((frame["entry_trigger"] == EntryTrigger.AUTO).sum()) if not frame.empty else 0,
    }
