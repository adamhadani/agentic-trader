"""Insider-cluster labels from the cached cube, matched controls, the frozen P1-P4 rule and the executor (entry ``insider``).

Spec sections 5 (steps 4-7), 6 and 8. One direction (LONG): every accepted event takes its cube cell
``(D, s)``; its control is the mean of the ``matched_controls`` cube cells on the same session ``D``,
in the same dollar-volume bucket, nearest in 20-session return. Sessions and positions are the
observed trading days, never frame rows: a missing adjusted bar gives ``NaN``.

``execute_insider_study`` writes ``protocol.json`` and ``manifest.json`` before ``build`` (the only
provider access), refuses a cube whose content hash differs from ``cube_sha256``, and records any
failure as ``status: failed``. Nothing here authorizes promotion.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from agentic_trader.research.apriori.catalog import InsiderEntry, LoadedInsiderEntry
from agentic_trader.research.apriori.exdiv_study import _mean_n, _terciles
from agentic_trader.research.apriori.insider_events import INSIDER_EVENT_COLUMNS, build_insider_events, purchase_dates
from agentic_trader.research.apriori.pead_events import by_session
from agentic_trader.research.apriori.pead_study import save_frame
from agentic_trader.research.apriori.statistics import mean_test, paired_test, trimmed_mean
from agentic_trader.research.insider_filings import InsiderTransaction
from agentic_trader.research.pooled.cube import HIT_CODES, CubeView, LabelCube
from agentic_trader.research.setups.study import _finite_json
from agentic_trader.storage.artifacts import save_json_report


__all__ = [
    "CONTROL_COLUMNS",
    "LEG_COLUMNS",
    "InsiderLegInputs",
    "evaluate_insider",
    "execute_insider_study",
    "label_insider_events",
]

HIT_NAMES = {code: str(hit) for hit, code in HIT_CODES.items()}
LEG_COLUMNS = (
    *INSIDER_EVENT_COLUMNS,
    *("r_cost", "r_gross", "hit", "holding", "ret20", "bucket", "dv", "opp_rel", "hold60", "session_mean_r_cost"),
)
CONTROL_COLUMNS = ("pair_id", "session", "r_cost", "r_gross", "hold60", "control_symbols")


@dataclass(frozen=True)
class InsiderLegInputs:
    cube: LabelCube
    trading_days: tuple[date, ...]
    adjusted: Mapping[str, pd.DataFrame]  # the cohort's adjusted daily bars, keyed by symbol
    transactions: Mapping[str, tuple[InsiderTransaction, ...]]  # already plausible-filtered by the runner
    identity_breaks: Mapping[str, date]
    issuer_of: Mapping[str, int]  # symbol -> issuer CIK
    decision_time: time  # the cube spec's bracket clock (ruling R1)
    provenance: Mapping[str, Any]


def _closes(adjusted: Mapping[str, pd.DataFrame], symbols: Sequence[str], days: Sequence[date]) -> np.ndarray:
    """Adjusted closes ``[T, N]`` on the trading days; a missing or non-positive close is ``NaN``."""
    out = np.full((len(days), len(symbols)), np.nan)
    for col, symbol in enumerate(symbols):
        frame = adjusted.get(symbol)
        if frame is not None and not frame.empty:
            out[:, col] = by_session(frame)["Close"].reindex(list(days)).to_numpy(float)
    out[~(out > 0)] = np.nan
    return out


def _trailing(closes: np.ndarray, sessions: int) -> np.ndarray:
    """``ret[p] = close[p - 1] / close[p - 1 - sessions] - 1``: the return to ``D - 1``, known before ``D``."""
    out = np.full_like(closes, np.nan)
    if len(closes) > sessions + 1:
        with np.errstate(invalid="ignore", divide="ignore"):
            out[sessions + 1 :] = closes[sessions:-1] / closes[: -sessions - 1] - 1.0
    return out


def _forward(closes: np.ndarray, sessions: int) -> np.ndarray:
    """``fwd[p] = close[p + sessions] / close[p] - 1``; ``NaN`` where ``p + sessions`` is past the bars."""
    out = np.full_like(closes, np.nan)
    if len(closes) > sessions:
        with np.errstate(invalid="ignore", divide="ignore"):
            out[:-sessions] = closes[sessions:] / closes[:-sessions] - 1.0
    return out


def _buckets(view: CubeView, row: int, buckets: int) -> np.ndarray:
    """Dollar-volume bucket per symbol among the labelled cells at ``row`` (``-1`` where unlabelled).

    Deterministic, quantile-free: the ``n`` labelled cells are ranked by ``dollar_volume`` (ties by the
    cube's ``tiebreak``) and rank ``r`` falls in bucket ``floor(r * buckets / n)``.
    """
    out = np.full(len(view.symbols), -1, dtype=int)
    cols = np.flatnonzero(view.labelled[row])
    order = cols[np.lexsort((view.tiebreak[row, cols], view.dollar_volume[row, cols]))]
    out[order] = np.arange(len(order)) * buckets // max(len(order), 1)
    return out


class _Purchases:
    """Whether a symbol has a qualifying purchase (routine or not) filed in ``[days[p - n], days[p])``.

    When ``p < n`` the window opens at the start of time, so a short calendar never admits a buyer.
    """

    def __init__(self, inputs: InsiderLegInputs, sessions: int):
        self._days = inputs.trading_days
        self._sessions = sessions
        self._dates = purchase_dates(inputs.transactions, last_session=inputs.trading_days[-1])

    def recent(self, symbol: str, p: int) -> bool:
        ordinals = self._dates.get(symbol)
        if ordinals is None:
            return False
        lo = self._days[p - self._sessions].toordinal() if p >= self._sessions else np.iinfo(np.int64).min
        i = int(np.searchsorted(ordinals, lo, side="left"))
        return i < len(ordinals) and ordinals[i] < self._days[p].toordinal()


def label_insider_events(
    events: pd.DataFrame, inputs: InsiderLegInputs, entry: InsiderEntry
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """``(legs, controls, counts)``: the events' cube cells, their matched controls and the exclusions by year."""
    cube = inputs.cube
    view = cube.window(cube.sessions[0], cube.sessions[-1])
    row_of = {d: i for i, d in enumerate(view.sessions)}
    col_of = {s: j for j, s in enumerate(view.symbols)}
    days = tuple(inputs.trading_days)
    pos_of = {d: i for i, d in enumerate(days)}
    missing = [d for d in view.sessions if d not in pos_of]
    if missing:
        raise ValueError(f"{len(missing)} cube sessions are not trading days (first {missing[0].isoformat()})")
    closes = _closes(inputs.adjusted, view.symbols, days)
    ret20 = _trailing(closes, entry.control.match_return_sessions)
    hold = _forward(closes, entry.descriptive_hold_sessions)
    purchases = _Purchases(inputs, entry.control.exclusion_sessions)
    k = entry.control.matched_controls
    excluded: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    unpaired: dict[int, int] = defaultdict(int)
    bucket_cache: dict[int, np.ndarray] = {}
    records = events.to_dict("records")
    # Every triggered symbol on D (share-class siblings included) is barred from D's controls.
    triggered: dict[date, set[str]] = defaultdict(set)
    for record in records:
        triggered[record["session"]].add(record["symbol"])

    def dv_at(record: Mapping[str, Any]) -> float:
        row, col = row_of.get(record["session"]), col_of.get(record["symbol"])
        value = np.nan if row is None or col is None else float(view.dollar_volume[row, col])
        return value if np.isfinite(value) else -np.inf

    groups: dict[tuple[date, object], list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        groups[(record["session"], inputs.issuer_of.get(record["symbol"], record["symbol"]))].append(record)
    winners = {id(max(group, key=dv_at)) for group in groups.values()}  # max keeps the first on a tie

    legs: list[dict[str, Any]] = []
    controls: list[dict[str, Any]] = []
    for record in records:
        session = record["session"]
        year = session.year
        if id(record) not in winners:
            excluded["share_class_sibling"][year] += 1
            continue
        row, col = row_of.get(session), col_of.get(record["symbol"])
        if row is None or col is None:
            excluded["not_in_cube"][year] += 1
            continue
        p = pos_of[session]
        reason = None
        if not view.eligible[row, col]:
            reason = "not_eligible"
        elif not view.labelled[row, col]:
            reason = "unlabelled"
        elif not np.isfinite(ret20[p, col]):
            reason = "no_return"
        if reason is not None:
            excluded[reason][year] += 1
            continue
        bucket = bucket_cache.setdefault(row, _buckets(view, row, entry.control.liquidity_buckets))
        dv = float(view.dollar_volume[row, col])
        legs.append(
            {
                **record,
                "r_cost": float(view.r_cost[row, col]),
                "r_gross": float(view.r_gross[row, col]),
                "hit": HIT_NAMES[int(view.hit[row, col])],
                "holding": int(view.holding[row, col]),
                "ret20": float(ret20[p, col]),
                "bucket": int(bucket[col]),
                "dv": dv,
                "opp_rel": float(record["opp_dollars"]) / dv,
                "hold60": float(hold[p, col]),
                "session_mean_r_cost": float(np.nanmean(view.r_cost[row][view.labelled[row]])),
            }
        )
        barred = {col_of[s] for s in triggered[session] if s in col_of}
        candidates = [
            c
            for c in np.flatnonzero(view.labelled[row] & (bucket == bucket[col]) & np.isfinite(ret20[p]))
            if c not in barred and not purchases.recent(view.symbols[c], p)
        ]
        if len(candidates) < k:
            unpaired[year] += 1
            continue
        cand = np.array(candidates)
        distance = np.abs(ret20[p, cand] - ret20[p, col])
        chosen = cand[np.lexsort((view.tiebreak[row, cand], distance))[:k]]
        held = hold[p, chosen]
        controls.append(
            {
                "pair_id": record["event_id"],
                "session": session,
                "r_cost": float(view.r_cost[row, chosen].mean()),
                "r_gross": float(view.r_gross[row, chosen].mean()),
                "hold60": float(held[np.isfinite(held)].mean()) if np.isfinite(held).any() else float("nan"),
                "control_symbols": ",".join(view.symbols[c] for c in chosen),
            }
        )
    counts = {
        "excluded": {reason: dict(sorted(years.items())) for reason, years in sorted(excluded.items())},
        "legs": len(legs),
        "paired": len(controls),
        "unpaired": sum(unpaired.values()),
        "unpaired_by_year": dict(sorted(unpaired.items())),
    }
    return pd.DataFrame(legs, columns=list(LEG_COLUMNS)), pd.DataFrame(controls, columns=list(CONTROL_COLUMNS)), counts


def _difference(leg: np.ndarray, other: np.ndarray) -> dict:
    """Means over the rows where both values are finite, and their difference."""
    both = np.isfinite(leg) & np.isfinite(other)
    if not both.any():
        return {"n": 0, "leg_mean": float("nan"), "other_mean": float("nan"), "diff": float("nan")}
    leg_mean, other_mean = float(leg[both].mean()), float(other[both].mean())
    return {"n": int(both.sum()), "leg_mean": leg_mean, "other_mean": other_mean, "diff": leg_mean - other_mean}


def _diagnostics(legs: pd.DataFrame, controls: pd.DataFrame, paired: pd.DataFrame) -> dict:
    """Spec section 6's descriptive splits; none of them decides anything."""
    per_session = legs.groupby("session").size()
    matched = controls.set_index("pair_id").reindex(paired["event_id"])
    return {
        "by_year": {int(year): _mean_n(group) for year, group in legs.groupby("year")},
        "hits": {str(name): int(n) for name, n in legs["hit"].value_counts().items()},
        "by_buyers": {"2": _mean_n(legs[legs["buyers"] == 2]), "3+": _mean_n(legs[legs["buyers"] >= 3])},
        "opp_rel_terciles": _terciles(legs, legs["opp_rel"]),
        "ret20_terciles": _terciles(legs, legs["ret20"]),
        "events_per_session": {
            "median": float(per_session.median()) if len(per_session) else 0.0,
            "p90": float(per_session.quantile(0.9)) if len(per_session) else 0.0,
            "max": int(per_session.max()) if len(per_session) else 0,
        },
        "excluding_2020": _mean_n(legs[legs["year"] != 2020]),
        "vs_session_mean": _difference(legs["r_cost"].to_numpy(float), legs["session_mean_r_cost"].to_numpy(float)),
        "hold60": {
            **_difference(paired["hold60"].to_numpy(float), matched["hold60"].to_numpy(float)),
            "immature_legs": int((~np.isfinite(legs["hold60"].to_numpy(float))).sum()),
        },
        "r_gross": {"leg": _mean_n(legs, "r_gross"), "control": _mean_n(controls, "r_gross")},
        "control": _mean_n(controls),
        "unpaired": len(legs) - len(paired),
    }


def evaluate_insider(legs: pd.DataFrame, controls: pd.DataFrame, entry: InsiderEntry) -> dict:
    """The frozen P1-P4 rule (spec section 6); P2 uses the paired legs only, keyed by ``D``."""
    paired = legs[legs["event_id"].isin(set(controls["pair_id"]))]
    recent = legs[legs["session"] >= entry.window.recent_from]
    trimmed = trimmed_mean(legs["r_cost"].to_numpy(float), entry.pass_rule.trim_fraction)
    recent_mean = float(recent["r_cost"].mean()) if len(recent) else float("nan")
    criteria = {
        "P1": mean_test(legs, entry.bootstrap) if len(legs) else {"n": 0, "holds": False},
        "P2": paired_test(paired, controls, entry.bootstrap) if len(paired) else {"n_leg": 0, "holds": False},
        "P3": {
            "trimmed_mean": trimmed,
            "recent_mean": recent_mean,
            "holds": bool(np.isfinite(trimmed) and trimmed > 0 and np.isfinite(recent_mean) and recent_mean > 0),
        },
        "P4": {
            "n": len(legs),
            "n_recent": len(recent),
            "holds": len(legs) >= entry.pass_rule.min_events and len(recent) >= entry.pass_rule.min_recent_events,
        },
    }
    passes = all(criterion["holds"] for criterion in criteria.values())
    return {
        "decision": "eligible_for_probe" if passes else "failed",
        "criteria": criteria,
        "diagnostics": _diagnostics(legs, controls, paired),
    }


async def execute_insider_study(
    loaded: LoadedInsiderEntry,
    directory: Path,
    *,
    build: Callable[[], Awaitable[InsiderLegInputs]],
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
            "pooled_protocol": entry.pooled_protocol,
            "pooled_protocol_sha256": entry.pooled_protocol_sha256,
            "cube_sha256": entry.cube_sha256,
            "environment": environment,
            "started_at": datetime.now(UTC).isoformat(),
            "authorizes_promotion": False,
        },
        directory / "manifest.json",
    )
    try:
        inputs = await build()
        digest = await asyncio.to_thread(lambda: inputs.cube.sha256)
        if digest != entry.cube_sha256:
            raise ValueError(
                f"the loaded cube has SHA-256 {digest}, but the entry pins cube_sha256 {entry.cube_sha256}"
            )
        events, event_counts = await asyncio.to_thread(
            build_insider_events,
            inputs.transactions,
            inputs.trading_days,
            inputs.identity_breaks,
            entry,
            decision_time=inputs.decision_time,
        )
        legs, controls, label_counts = await asyncio.to_thread(label_insider_events, events, inputs, entry)
        for name, frame in (("events", events), ("legs", legs), ("controls", controls)):
            await asyncio.to_thread(save_frame, frame, directory / f"{name}.csv.gz")
        evaluation = await asyncio.to_thread(evaluate_insider, legs, controls, entry)
        result = {
            "status": "completed",
            **evaluation,
            "events": event_counts,
            "labels": label_counts,
            "inputs": dict(inputs.provenance),
            "authorizes_promotion": False,
        }
    except Exception as exc:
        result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "authorizes_promotion": False}
    save_json_report(_finite_json(result), directory / "result.json")
    return result
