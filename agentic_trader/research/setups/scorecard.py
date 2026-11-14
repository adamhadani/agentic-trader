"""The lane scorecard: is anything working? Native cards, the PEAD paper probe and insider watch cards.

Read-only. For each lane it reports last week's activity (cards sent, entries split into auto and tap, fills,
closes, realized R), the realized record over a trailing window (closed trades, mean realized R, open positions)
and the counterfactual record over the same window (matured labels, mean ``r_cost`` and, where the lane has it,
the beta-matched SPY excess), plus the account's adjusted equity at both ends of the week. Nothing here sends,
writes or gates: a counterfactual label assumes study-price fills and no lane has validated edge.

The producers also feed ``copilot cards outcomes`` (``decision_events``, ``native_outcomes``,
``pead_probe_summary``, ``insider_watch_summary``), which prints their output unchanged; bar sources are injected
so tests and the daemon pick their own.

SPY comparison: native uses the *beta-matched* excess (``market_exposure.sent.mean_beta_excess_r``, a past-only
120-session beta against SPY), the insider watch lane uses the same yardstick (``all.mean_beta_excess_r``), and the
PEAD record has no SPY control, so its excess is ``None``. Realized R is ``realized_pnl / risk_dollars`` over closed
rows, the formula of ``research/alpha/probe.forward_record``; a closed row with unknown pnl or risk is not counted in
``record_closed`` or the mean.
"""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from agentic_trader.data.pacing import RequestPacer
from agentic_trader.data.providers import AlpacaDataProvider
from agentic_trader.execution.durable import EntryTrigger, EventKind
from agentic_trader.execution.scope import is_non_native
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.apriori.catalog import load_pead_entry
from agentic_trader.research.apriori.pead_events import EVENT_COLUMNS
from agentic_trader.research.apriori.pead_study import label_events
from agentic_trader.research.apriori.probe_outcomes import (
    PEAD_LONG_STRATEGY,
    decision_frame,
    probe_signals,
    summarize_probe,
)
from agentic_trader.research.apriori.watch_contract import INSIDER_WATCH_STRATEGY_ID, frozen_watch_contract
from agentic_trader.research.apriori.watch_outcomes import (
    completed_bars,
    decision_frame as decision_frame_watch,
    is_tap,
    label_watch_events,
    summarize_watch,
    watch_bars,
    watch_signals,
)
from agentic_trader.research.setups.execution_evidence import collect_execution
from agentic_trader.research.setups.outcomes import (
    DEFAULT_COST_BPS_PER_SIDE,
    DEFAULT_MAX_HOLD_SESSIONS,
    attach_execution,
    label_journaled,
    summarize,
)
from agentic_trader.research.setups.sources import SIP_HISTORY_DELAY, DelayedBars, build_bar_source, scan_ranked_events
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.ledger import LedgerStore
from agentic_trader.storage.models import SignalRecord
from agentic_trader.storage.probe_state import CLOSED_STATUSES


if TYPE_CHECKING:
    from agentic_trader.config import AppConfig
    from agentic_trader.research.setups.runner import BarSource
    from agentic_trader.storage.db import SignalDatabase

__all__ = [
    "AccountRow",
    "LaneRow",
    "LaneScorecard",
    "NativeOutcomes",
    "build_scorecard",
    "decision_events",
    "insider_watch_summary",
    "native_outcomes",
    "pead_probe_summary",
    "sip_reader",
]

LaneName = Literal["native", "pead", "insider_watch"]
LANES: tuple[LaneName, ...] = ("native", "pead", "insider_watch")
_FETCH_FAILED = "fetch_failed"


class LaneRow(BaseModel):
    """One lane's week and trailing record. ``cf_*`` are ``None`` when the labels could not be read (see ``note``)."""

    model_config = ConfigDict(frozen=True)

    lane: LaneName
    sent: int
    entered_auto: int
    entered_tap: int
    filled: int
    closed: int
    realized_r_sum: float
    record_closed: int
    record_mean_r: float | None
    open_positions: int
    cf_mature: int | None
    cf_mean_r: float | None
    cf_mean_excess_r: float | None
    note: str | None = None


class AccountRow(BaseModel):
    """Cash-flow-adjusted account equity at the window's start and end, from ledger checkpoints."""

    model_config = ConfigDict(frozen=True)

    start_equity: float | None
    end_equity: float | None


class LaneScorecard(BaseModel):
    model_config = ConfigDict(frozen=True)

    window_start: datetime
    window_end: datetime
    record_days: int = 90
    lanes: tuple[LaneRow, ...]
    account: AccountRow | None


# ---------------------------------------------------------------------------------------------------------------
# Producers shared with ``copilot cards outcomes`` (moved from the CLI; the CLI prints their output unchanged).


async def decision_events(db: SignalDatabase, days: int, *, now: datetime, kind: EventKind) -> list[dict[str, Any]]:
    """Every ``kind`` decision event's payload journaled on each ET calendar date in the window.

    Mirrors ``scan_ranked_events``' per-day stream walk, but returns the journaled
    payload itself (``decision_frame`` consumes payloads, not the wrapped event).
    """
    et_today = now.astimezone(ET_TZ).date()
    payloads: list[dict[str, Any]] = []
    for offset in range(days):
        day = et_today - timedelta(days=offset)
        day_events = await db.workflows.events(stream=f"scan/{day.isoformat()}")
        payloads.extend(event.get("payload", event) for event in day_events if event.get("kind") == kind)
    return payloads


def sip_reader(config: AppConfig, through: datetime) -> Callable[[], DelayedBars]:
    """A lazy, single SIP reader ending at ``through``, built on first use.

    The PEAD and insider watch records are labelled on SIP, the feed their studies (and the watch card's +0.087R
    line) were measured on, never the configured feed. Every read ends at ``through`` (delayed SIP), which is also
    both blocks' maturity cutoff.
    """
    reader: DelayedBars | None = None

    def sip() -> DelayedBars:
        nonlocal reader
        if reader is None:
            provider = AlpacaDataProvider(
                api_key=config.alpaca_api_key,
                api_secret=config.alpaca_api_secret,
                feed="sip",
                request_timeout=config.market_data.timeout_seconds,
            )
            reader = DelayedBars(provider, through)
        return reader

    return sip


@dataclass(frozen=True)
class NativeOutcomes:
    """The labelled native frame, its execution evidence and ``summarize``'s descriptive summary."""

    frame: pd.DataFrame
    execution: pd.DataFrame
    summary: dict[str, Any]


async def native_outcomes(
    db: SignalDatabase,
    config: AppConfig,
    events: list[dict[str, Any]],
    bars: BarSource,
    daily_bars: BarSource,
    *,
    now: datetime,
) -> NativeOutcomes:
    """Label the journaled ranked candidates, attach execution evidence and summarize."""
    frame = await asyncio.to_thread(
        label_journaled,
        events,
        bars,
        daily_bars=daily_bars,
        max_hold_sessions=DEFAULT_MAX_HOLD_SESSIONS,
        cost_bps=DEFAULT_COST_BPS_PER_SIDE,
        now=now,
        max_requests_per_minute=config.market_data.max_requests_per_minute,
    )
    execution = await collect_execution(db, frame)
    summary = summarize(
        attach_execution(frame, execution),
        asset_classes=config.universe.outcome_asset_classes(),
        slots=config.scheduler.suggestion_scan_times_et,
    )
    return NativeOutcomes(frame, execution, summary)


async def pead_probe_summary(
    db: SignalDatabase,
    config: AppConfig,
    payloads: list[dict[str, Any]],
    *,
    sip: Callable[[], BarSource],
    now: datetime,
    signals_since: datetime,
) -> dict[str, Any] | None:
    """The PEAD probe record over journaled ``pead_decision`` payloads; ``None`` when there are none.

    ``signals_since`` bounds the ``pead_long`` signals counted by ``summarize_probe``. Per-symbol bar failures
    are kept in ``bar_failures`` (``"Type: message"``), never raised.
    """
    if not payloads:
        return None
    sip_through = now - SIP_HISTORY_DELAY
    entry = load_pead_entry(Path(config.apriori.pead_entry_path)).entry
    decisions = decision_frame(payloads)
    bar_failures: dict[str, str] = {}
    hourly: dict[str, pd.DataFrame] = {}
    if not decisions.empty:
        pead_source = sip()
        pacer = RequestPacer(max(1, config.market_data.max_requests_per_minute))
        for symbol, group in decisions.groupby("symbol"):
            start = datetime.combine(min(group["session"]) - timedelta(days=7), time.min, tzinfo=UTC)
            await asyncio.to_thread(pacer.acquire)
            try:
                read = await asyncio.to_thread(
                    pead_source.fetch_bars, symbol, "1h", start, sip_through, adjustment="all"
                )
                hourly[symbol] = completed_bars(read, sip_through)
            except Exception as exc:
                bar_failures[symbol] = f"{type(exc).__name__}: {exc}"
    labels, _ = await asyncio.to_thread(label_events, decisions[list(EVENT_COLUMNS)], hourly, entry)
    labels = labels[(labels["direction"] == "LONG") & labels["is_leg"]] if not labels.empty else labels
    report = [r for r in await AlphaRepository(db.workflows).probe_report(now=now) if r.get("kind") == "apriori"]
    summary = summarize_probe(
        decisions,
        labels,
        await probe_signals(db, signals_since),
        report[0]["forward"] if report else None,
        report[0]["study_mean_r"] if report else None,
    )
    summary["bar_failures"] = bar_failures
    return summary


async def insider_watch_summary(
    db: SignalDatabase,
    config: AppConfig,
    payloads: list[dict[str, Any]],
    *,
    sip: Callable[[], BarSource],
    now: datetime,
    signals_since: datetime,
) -> dict[str, Any] | None:
    """The insider watch record over journaled ``insider_watch_decision`` payloads; ``None`` when there are none.

    ``signals_since`` bounds the signals behind ``taps``, ``auto_entries`` and ``fills``. Bar failures are kept
    in ``bar_failures`` (``"Type: message"`` per symbol), never raised.
    """
    if not payloads:
        return None
    sip_through = now - SIP_HISTORY_DELAY
    watch = decision_frame_watch(payloads)
    contract = frozen_watch_contract()
    pacer = RequestPacer(max(1, config.market_data.max_requests_per_minute))
    watch_source = sip()

    def fetch(symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
        pacer.acquire()
        return watch_source.fetch_bars(symbol, timeframe, start, end, adjustment="all")

    watch_data = await asyncio.to_thread(watch_bars, fetch, watch, sip_through)
    watch_labels = label_watch_events(
        watch,
        watch_data.hourly,
        watch_data.daily,
        watch_data.market_hourly,
        watch_data.market_daily,
        contract.spec.bracket,
        contract.spec.decision_cost_bps,
        as_of=sip_through,
    )
    summary = summarize_watch(watch, watch_labels, await watch_signals(db, signals_since))
    summary["bar_failures"] = watch_data.failures
    return summary


# ---------------------------------------------------------------------------------------------------------------
# The scorecard.


@dataclass(frozen=True)
class _Signal:
    lane: LaneName
    timestamp: datetime
    status: str
    executed_at: datetime | None
    exit_time: datetime
    realized_r: float | None
    row: dict[str, Any]  # ``SignalRecord.to_dict()`` plus ``entry_trigger`` when the signal has an entry item

    @property
    def trigger(self) -> str | None:
        return self.row.get("entry_trigger")

    @property
    def closed(self) -> bool:
        return self.status in CLOSED_STATUSES


@dataclass(frozen=True)
class _Counterfactual:
    mature: int | None = 0
    mean_r: float | None = None
    mean_excess_r: float | None = None
    note: str | None = None
    split: tuple[int, int] | None = None  # (auto, tap) when a lane summary already split the entries


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _realized_r(record: SignalRecord) -> float | None:
    pnl, risk = _finite(record.realized_pnl), _finite(record.risk_dollars)
    if pnl is None or risk is None or risk <= 0:
        return None
    return pnl / risk


def _lane_of(record: SignalRecord, provenance: dict[str, Any] | None) -> LaneName | None:
    if record.strategy == PEAD_LONG_STRATEGY:
        return "pead"
    if record.strategy == INSIDER_WATCH_STRATEGY_ID:
        return "insider_watch"
    if is_non_native(
        strategy=record.strategy,
        alpha_version=record.alpha_version,
        alpha_policy=record.alpha_policy,
        provenance=provenance,
    ):
        return None
    return "native"


async def _lane_signals(db: SignalDatabase, since: datetime) -> list[_Signal]:
    """Every lane's signals recorded since ``since`` in ``db``'s scope, with each one's entry trigger."""
    async with db.session_factory() as session:
        records = list(
            (await session.execute(select(SignalRecord).where(*db._scope(), SignalRecord.timestamp >= since)))
            .scalars()
            .all()
        )
    classified: list[tuple[SignalRecord, LaneName]] = []
    for record in records:
        provenance = json.loads(record.decision_provenance) if record.decision_provenance else None
        if (lane := _lane_of(record, provenance)) is not None:
            classified.append((record, lane))
    triggers = await db.workflows.entry_work_states([record.id for record, _ in classified])
    signals: list[_Signal] = []
    for record, lane in classified:
        row = record.to_dict()
        if record.id in triggers:
            row["entry_trigger"] = triggers[record.id][1].value
        signals.append(
            _Signal(
                lane=lane,
                timestamp=record.timestamp,
                status=str(record.status),
                executed_at=record.executed_at,
                exit_time=record.exit_timestamp or record.timestamp,
                realized_r=_realized_r(record),
                row=row,
            )
        )
    return signals


def _et_midnight(day: Any) -> datetime:
    return datetime.combine(day, time.min, tzinfo=ET_TZ).astimezone(UTC)


def _failure_note(types: set[str]) -> str:
    return "bar fetch failed: " + ", ".join(sorted(types))


def _error_types(failures: dict[str, str]) -> set[str]:
    return {message.split(":", 1)[0] for message in failures.values()}


async def _native_counterfactual(
    db: SignalDatabase,
    config: AppConfig,
    *,
    record_days: int,
    now: datetime,
    bars: BarSource,
    sip: Callable[[], BarSource],
) -> _Counterfactual:
    events = await scan_ranked_events(db, record_days, now=now)
    outcomes = await native_outcomes(db, config, events, bars, sip(), now=now)
    frame = outcomes.frame
    if not frame.empty:
        failed = frame[frame["sent"].astype(bool) & (frame["hit"] == _FETCH_FAILED)]
        if not failed.empty:
            return _Counterfactual(
                None, None, None, _failure_note({str(reason).split(":", 1)[0] for reason in failed["reason"]})
            )
    summary = outcomes.summary
    sent_rate = summary["base_rates"]["sent"]
    exposure = summary["market_exposure"]["sent"]
    return _Counterfactual(
        mature=sent_rate["n"] if sent_rate else 0,
        mean_r=sent_rate["mean_r_cost"] if sent_rate else None,
        mean_excess_r=exposure["mean_beta_excess_r"] if exposure else None,
    )


async def _pead_counterfactual(
    db: SignalDatabase,
    config: AppConfig,
    *,
    record_days: int,
    now: datetime,
    sip: Callable[[], BarSource],
    window_start: datetime,
) -> _Counterfactual:
    payloads = await decision_events(db, record_days, now=now, kind=EventKind.PEAD_DECISION)
    summary = await pead_probe_summary(db, config, payloads, sip=sip, now=now, signals_since=window_start)
    if summary is None:
        return _Counterfactual()
    if summary["bar_failures"]:
        return _Counterfactual(None, None, None, _failure_note(_error_types(summary["bar_failures"])))
    labelled = summary["counterfactual"]
    return _Counterfactual(
        mature=labelled["mature"] if labelled else 0, mean_r=labelled["mean_r_cost"] if labelled else None
    )


async def _watch_counterfactual(
    db: SignalDatabase,
    config: AppConfig,
    *,
    record_days: int,
    now: datetime,
    sip: Callable[[], BarSource],
    window_start: datetime,
) -> _Counterfactual:
    payloads = await decision_events(db, record_days, now=now, kind=EventKind.INSIDER_WATCH_DECISION)
    summary = await insider_watch_summary(db, config, payloads, sip=sip, now=now, signals_since=window_start)
    if summary is None:
        return _Counterfactual()
    split = (int(summary["auto_entries"]), int(summary["taps"]))
    if summary["bar_failures"]:
        return _Counterfactual(None, None, None, _failure_note(_error_types(summary["bar_failures"])), split)
    block = summary["all"]
    return _Counterfactual(block["n"], block["mean_r_cost"], block["mean_beta_excess_r"], None, split)


async def _contained(coro: Any) -> _Counterfactual:
    """A lane's counterfactual, or an unavailable one naming only the exception type."""
    try:
        return await coro
    except Exception as exc:
        return _Counterfactual(None, None, None, f"counterfactual unavailable: {type(exc).__name__}")


def _lane_row(
    lane: LaneName, signals: list[_Signal], *, window_start: datetime, record_start: datetime, cf: _Counterfactual
) -> LaneRow:
    week = [s for s in signals if s.timestamp >= window_start]
    auto = sum(1 for s in week if s.trigger == EntryTrigger.AUTO)
    tap = sum(1 for s in week if is_tap(s.row))
    if cf.split is not None:
        auto, tap = cf.split
    closed_week = [s for s in signals if s.closed and s.exit_time >= window_start]
    closed_record = [s for s in signals if s.closed and s.exit_time >= record_start]
    record_r = [s.realized_r for s in closed_record if s.realized_r is not None]
    return LaneRow(
        lane=lane,
        sent=len(week),
        entered_auto=auto,
        entered_tap=tap,
        filled=sum(1 for s in week if s.executed_at is not None),
        closed=len(closed_week),
        realized_r_sum=float(sum(s.realized_r for s in closed_week if s.realized_r is not None)),
        record_closed=len(record_r),
        record_mean_r=float(sum(record_r) / len(record_r)) if record_r else None,
        open_positions=sum(1 for s in signals if s.status == "EXECUTED"),
        cf_mature=cf.mature,
        cf_mean_r=cf.mean_r,
        cf_mean_excess_r=cf.mean_excess_r,
        note=cf.note,
    )


async def _account(db: SignalDatabase, *, start: datetime, end: datetime) -> AccountRow | None:
    try:
        ledger = LedgerStore(db.workflows)
        first, last = await ledger.adjusted_equity_at(start), await ledger.adjusted_equity_at(end)
    except Exception:
        return None
    return None if first is None and last is None else AccountRow(start_equity=first, end_equity=last)


async def build_scorecard(
    db: SignalDatabase,
    config: AppConfig,
    *,
    now: datetime,
    activity_days: int = 7,
    record_days: int = 90,
    bars: BarSource | None = None,
    sip: Callable[[], BarSource] | None = None,
) -> LaneScorecard:
    """Build the scorecard at ``now``: the last ``activity_days`` New York days, the trailing ``record_days``.

    ``bars`` is the scan-bar source for native labels (default ``build_bar_source``); ``sip`` returns the delayed
    SIP reader used for the PEAD and watch labels and for every daily (beta, cost) read (default ``sip_reader``).
    A lane whose bars cannot be read keeps its activity and realized record with ``cf_*`` ``None`` and a note
    naming only the exception types.
    """
    et_today = now.astimezone(ET_TZ).date()
    window_start = _et_midnight(et_today - timedelta(days=activity_days - 1))
    record_start = _et_midnight(et_today - timedelta(days=record_days - 1))
    bars = bars or build_bar_source(config)
    sip = sip or sip_reader(config, now - SIP_HISTORY_DELAY)

    signals = await _lane_signals(db, record_start)
    counterfactuals = {
        "native": await _contained(
            _native_counterfactual(db, config, record_days=record_days, now=now, bars=bars, sip=sip)
        ),
        "pead": await _contained(
            _pead_counterfactual(db, config, record_days=record_days, now=now, sip=sip, window_start=window_start)
        ),
        "insider_watch": await _contained(
            _watch_counterfactual(db, config, record_days=record_days, now=now, sip=sip, window_start=window_start)
        ),
    }
    lanes = tuple(
        _lane_row(
            lane,
            [s for s in signals if s.lane == lane],
            window_start=window_start,
            record_start=record_start,
            cf=counterfactuals[lane],
        )
        for lane in LANES
    )
    return LaneScorecard(
        window_start=window_start,
        window_end=now,
        record_days=record_days,
        lanes=lanes,
        account=await _account(db, start=window_start, end=now),
    )
