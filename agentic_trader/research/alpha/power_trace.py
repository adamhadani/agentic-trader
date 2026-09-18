"""Observation-only diagnosis of the shared bracket simulator.

An opportunity belongs to a completed decision bar and is eligible on the next
execution bar. A pulse's timely entry means its own order filled on that bar;
it does not establish capture of the whole planted return or a broker fill.
Pending/holding durations count execution bars ending in that state. The last
observation has no following execution bar and is reported separately. Folds
start flat, retain causal feature history, and never inspect rows beyond ``end``.
"""

from __future__ import annotations

from collections import Counter, defaultdict

import numpy as np
import pandas as pd

from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.simulation import (
    BracketIntent,
    SimulationEventKind,
    entry_intents,
    simulate_strategy,
)
from agentic_trader.research.alpha.strategy import alpha_scores, entry_directions


def execution_diagnostics(
    definition: AlphaDefinition,
    bars: pd.DataFrame,
    *,
    start: int = 0,
    end: int | None = None,
    pulse_mask: pd.Series | None = None,
) -> dict:
    """Run the existing strategy once and retain JSON-safe execution evidence.

    ``pulse_mask`` is an externally known boolean predicate at *decision* bars.
    Missing pulse provenance remains unavailable. Scores/brackets use the shared
    helpers; lifecycle reconstruction consumes actual events, never hypothesized
    fills. Session versions still require their existing minute-replay adapter.
    """
    end = len(bars) if end is None else end
    if not 0 <= start < end <= len(bars):
        raise ValueError("Invalid execution diagnosis interval")
    frame = bars.iloc[:end].rename(columns=str.lower)
    if (
        not isinstance(frame.index, pd.DatetimeIndex)
        or not frame.index.is_unique
        or not frame.index.is_monotonic_increasing
        or frame.index.hasnans
    ):
        raise ValueError("Execution diagnosis requires unique ordered observation timestamps")
    pulses = None
    if pulse_mask is not None:
        pulses = pulse_mask.iloc[:end]
        if not pulses.index.equals(frame.index) or not all(isinstance(x, (bool, np.bool_)) for x in pulses):
            raise ValueError("Pulse observations require exactly aligned nonmissing boolean values")
    scores = alpha_scores(definition, frame)
    simulation = simulate_strategy(definition, frame, start=start, scores=scores, trace=True)
    observations = entry_intents(definition, frame, scores)
    intents = {i: proposal for i in range(max(1, start), end) if (proposal := observations[i - 1]) is not None}
    directions = entry_directions(scores, definition)
    report = _summarize_trace(frame, intents, simulation, start=start, pulse_mask=pulses)
    report["definition_id"] = definition.version_id
    report["feature_coverage"] = simulation["feature_coverage"]
    report["counts"]["threshold_opportunities"] = int(directions.iloc[max(0, start - 1) : end - 1].ne(0).sum())
    report["economics"]["friction_per_side"] = definition.execution.friction_per_side
    report["execution_scope"] = simulation["execution_scope"]
    return report


def _summarize_trace(
    frame: pd.DataFrame,
    intents: dict[int, BracketIntent],
    simulation: dict,
    *,
    start: int,
    pulse_mask: pd.Series | None,
) -> dict:
    """Project observed engine events; no execution or cost rules live here."""
    end = len(frame)
    events = simulation["events"]
    grouped = defaultdict(list)
    for event in events:
        grouped[event["bar_start"]].append(event)
    kinds = Counter(event["kind"] for event in events)
    orders: dict[int, dict] = {}
    pending = holding = None
    opportunities: list[dict] = []
    occupancy: Counter = Counter()
    suppression: Counter = Counter()
    pulse_rows: list[dict] = []

    for i in range(start, end):
        bar_events = grouped[str(frame.index[i])]
        # Expiration precedes admission. Exits/fills occur after admission, so a
        # position closing on this bar still suppresses this bar's new intent.
        for event in bar_events:
            if event["kind"] == SimulationEventKind.ENTRY_EXPIRED:
                order = orders[event["order_number"]]
                order.update(pending_outcome="expired", pending_resolution_bar=i)
                pending = None
        state = "pending" if pending is not None else "holding" if holding is not None else "flat"
        eligible = i in intents
        pulse = bool(pulse_mask.iloc[i - 1]) if pulse_mask is not None and i else None
        admission = (f"suppressed_{state}" if state != "flat" else "created") if eligible else "no_intent"
        if eligible and state != "flat":
            suppression[state] += 1
        timely_entry = False
        any_entry = False
        for event in bar_events:
            number = event["order_number"]
            match event["kind"]:
                case SimulationEventKind.ORDER_CREATED:
                    pending = number
                    orders[number] = {
                        "order_number": number,
                        "signal_timestamp": event["signal_timestamp"],
                        "created_bar": i,
                        "created_bar_start": event["bar_start"],
                        "direction": event["direction"],
                        "limit": event["limit"],
                        "stop": event["stop"],
                        "target": event["target"],
                        "pulse": pulse,
                        "entry_bar": None,
                        "exit_bar": None,
                        "pending_bars": 0,
                        "holding_bars": 0,
                        "pending_outcome": "censored",
                        "holding_outcome": None,
                        "pending_resolution_bar": None,
                        "holding_resolution_bar": None,
                    }
                case SimulationEventKind.ENTRY_FILLED:
                    pending, holding = None, number
                    order = orders[number]
                    order.update(
                        entry_bar=i, pending_outcome="filled", pending_resolution_bar=i, holding_outcome="censored"
                    )
                    any_entry = True
                    timely_entry = order["created_bar"] == i
                case SimulationEventKind.EXIT_FILLED:
                    orders[number].update(exit_bar=i, holding_outcome="closed", holding_resolution_bar=i)
                    holding = None
        if pending is not None:
            orders[pending]["pending_bars"] += 1
            occupancy["pending"] += 1
        elif holding is not None:
            orders[holding]["holding_bars"] += 1
            occupancy["holding"] += 1
        else:
            occupancy["flat"] += 1
        if eligible or pulse:
            row = {
                "signal_timestamp": str(frame.index[i - 1]),
                "execution_bar": i,
                "execution_bar_start": str(frame.index[i]),
                "eligible_intent": eligible,
                "pulse": pulse,
                "state_at_admission": state,
                "admission": admission,
                "timely_entry": timely_entry,
                "any_entry": any_entry,
                "holding_at_close": holding is not None,
            }
            opportunities.append(row)
            if pulse:
                pulse_rows.append(row)

    pulses = None
    if pulse_mask is not None:
        pulses = {
            "opportunities": len(pulse_rows),
            "eligible_intents": sum(row["eligible_intent"] for row in pulse_rows),
            "no_valid_intent": sum(not row["eligible_intent"] for row in pulse_rows),
            "orders_created": sum(row["admission"] == "created" for row in pulse_rows),
            "suppressed_pending": sum(row["admission"] == "suppressed_pending" for row in pulse_rows),
            "suppressed_holding": sum(row["admission"] == "suppressed_holding" for row in pulse_rows),
            "timely_entries": sum(row["timely_entry"] for row in pulse_rows),
            "missed_timely_entries": sum(not row["timely_entry"] for row in pulse_rows),
            "any_entry_on_pulse_bar": sum(row["any_entry"] for row in pulse_rows),
            "held_at_close": sum(row["holding_at_close"] for row in pulse_rows),
            "delayed_entries": sum(
                order["pulse"] and order["entry_bar"] is not None and order["entry_bar"] > order["created_bar"]
                for order in orders.values()
            ),
            "outside_execution_boundary": int(pulse_mask.iloc[-1]),
        }
    entry_events = [event for event in events if event["kind"] == SimulationEventKind.ENTRY_FILLED]
    exit_events = [event for event in events if event["kind"] == SimulationEventKind.EXIT_FILLED]
    entry_fees = sum(event["fee"] for event in entry_events)
    exit_fees = sum(event["fee"] for event in exit_events)
    return {
        "interval": {"start": start, "end_exclusive": end},
        "counts": {
            "execution_bars": end - start,
            "eligible_intents": len(intents),
            "orders_created": kinds[SimulationEventKind.ORDER_CREATED],
            "entries": len(entry_events),
            "closes": len(exit_events),
            "entry_expirations": kinds[SimulationEventKind.ENTRY_EXPIRED],
            "holding_expirations": kinds[SimulationEventKind.HOLDING_EXPIRED],
            "suppressed_pending": suppression["pending"],
            "suppressed_holding": suppression["holding"],
            "pending_at_close_bars": occupancy["pending"],
            "holding_at_close_bars": occupancy["holding"],
            "flat_at_close_bars": occupancy["flat"],
        },
        "pulses": pulses,
        "orders": list(orders.values()),
        "opportunities": opportunities,
        "boundary": {
            "pending_entry": simulation["pending_entry"],
            "open_position": simulation["open_position"],
            "pending_order": pending,
            "open_order": holding,
            "pending_bars": orders[pending]["pending_bars"] if pending is not None else None,
            "holding_bars": orders[holding]["holding_bars"] if holding is not None else None,
            "last_observed_bar_start": str(frame.index[-1]),
        },
        "economics": {
            "capital_basis": "simulated_starting_capital_1",
            "entry_fees": entry_fees,
            "exit_fees": exit_fees,
            "total_fees": entry_fees + exit_fees,
            "closed_gross_pnl": sum(event["gross_pnl"] for event in exit_events),
            "closed_net_pnl": sum(event["net_pnl"] for event in exit_events),
            "total_return_pct": simulation["total_return_pct"],
            "max_drawdown_pct": simulation["max_drawdown_pct"],
            "per_bar_sharpe": simulation["per_bar_sharpe"],
            "sharpe": simulation["sharpe"],
        },
        "returns": [
            {"bar_start": str(timestamp), "net_return": float(value)}
            for timestamp, value in simulation["net_returns"].items()
        ],
        "trades": simulation["trades"],
        "events": events,
        "limitations": [
            "OHLC event times identify bar starts, not exchange fill timestamps.",
            "Pending/holding bars count end-of-bar state; unresolved lifecycles are boundary-censored.",
            "Timely pulse entry is a same-opportunity fill, not proof of capturing the full planted return.",
            "Fees and closed P&L use units of simulated starting capital 1; return fields are separately labelled.",
            "Open exposure is marked to market; no hypothetical liquidation or exit fee is added.",
        ],
    }
