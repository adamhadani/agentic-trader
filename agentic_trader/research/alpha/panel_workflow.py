"""Injected panel application service over the existing diagnostic event journal."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

import pandas as pd

from agentic_trader.config import DailyAcquisitionConfig
from agentic_trader.data.evidence import BarAcquisitionError
from agentic_trader.market.bars import SessionSchedule, TradingSession, utc_timestamp
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.alpha.daily_inputs import DailyStudyInputs
from agentic_trader.research.alpha.data import save_dataset
from agentic_trader.research.alpha.panel import PanelCoverageError, align_daily_panel
from agentic_trader.research.alpha.panel_study import (
    PANEL_JOURNAL_SYMBOL,
    PanelStudyStatus,
    compute_panel_study,
)
from agentic_trader.research.alpha.validation import frame_digest
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.artifacts import save_json_report


class DailyPanelSource(Protocol):
    def calendar(self, start: date, end: date) -> tuple[TradingSession, ...]: ...
    def daily(self, symbol: str, start: date, end: date, feed: str, adjustment: str = "raw") -> pd.DataFrame: ...


class DailyStudyPlan(Protocol):
    """Acquisition/accounting contract shared by pure daily study computations."""

    @property
    def start(self) -> date: ...
    @property
    def end(self) -> date: ...
    @property
    def feed(self) -> str: ...
    @property
    def adjustment(self) -> str: ...
    @property
    def acquisition_symbols(self) -> tuple[str, ...]: ...
    @property
    def trial_count(self) -> int: ...
    @property
    def identity(self) -> str: ...
    def document(self) -> dict: ...
    def validate_as_of(self, now) -> None: ...


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _save_observations(frame: pd.DataFrame, output: Path):
    digest = frame_digest(frame)
    path = save_dataset(frame, output, digest)
    return {
        "artifact": path.name,
        "content_hash": digest,
        "artifact_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
        "rows": len(frame),
        "attrs": frame.attrs,
    }


def _compute(batch, clock, plan, sessions):
    frames = batch.require_complete(plan.acquisition_symbols)
    panel = align_daily_panel(frames, clock, feed=plan.feed)
    return compute_panel_study(panel, plan)


async def _drain_on_cancel(operation):
    """Finish the current bounded read/checkpoint before its source context can close."""
    task = asyncio.create_task(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        task.result()
        raise


class AcquisitionBudgetExceeded(RuntimeError):
    """The next read was not attempted because the frozen elapsed budget expired."""


class AlphaPanelService:
    def __init__(
        self,
        repository: AlphaRepository,
        source: DailyPanelSource,
        *,
        acquisition: DailyAcquisitionConfig,
        compute: Callable | None = None,
    ):
        self.repository = repository
        self.source = source
        self.compute = compute
        self.acquisition = acquisition.model_copy(deep=True)

    async def run(self, plan: DailyStudyPlan, output: Path, *, environment: dict, as_of=None):
        observed_at = utc_timestamp(as_of if as_of is not None else datetime.now(UTC))
        start = pd.Timestamp(plan.start, tz=ET_TZ)
        end = pd.Timestamp(plan.end + timedelta(days=1), tz=ET_TZ)
        plan.validate_as_of(observed_at)
        symbols = plan.acquisition_symbols
        if (
            not symbols
            or len(set(symbols)) != len(symbols)
            or len(symbols) > self.acquisition.max_symbols
            or not 0 <= (plan.end - plan.start).days < self.acquisition.max_days
        ):
            raise ValueError("Study exceeds acquisition bounds")
        await asyncio.to_thread(output.mkdir, parents=True, mode=0o700)
        run_id = uuid4().hex
        await asyncio.to_thread(
            save_json_report,
            {
                "run_id": run_id,
                "plan_id": plan.identity,
                "plan": plan.document(),
                "as_of": observed_at.isoformat(),
                "environment": environment,
                "acquisition": self.acquisition.model_dump(mode="json"),
            },
            output / "manifest.json",
        )
        await self.repository.reserve_run(run_id, symbol=PANEL_JOURNAL_SYMBOL, timeframe="1d", trials=plan.trial_count)
        # Every member, including warmup/benchmark observations, is excluded before any provider call.
        for symbol in symbols:
            await self.repository.exclude_observed_interval(
                symbol=symbol,
                start=start.isoformat(),
                end=end.isoformat(),
                trials=0,
                reason=f"Panel diagnostics {run_id}; frozen plan {plan.identity}",
                actor="panel_research",
            )
        inputs: dict[str, Any] = {"receipts": [], "datasets": {}, "failures": {}, "members": []}
        started = time.monotonic()
        last_request: float | None = None

        async def acquire(method, *args):
            nonlocal last_request
            if time.monotonic() - started >= self.acquisition.max_elapsed_seconds:
                raise AcquisitionBudgetExceeded()
            if last_request is not None:
                delay = self.acquisition.min_request_interval_seconds - (time.monotonic() - last_request)
                if delay > 0:
                    await asyncio.sleep(delay)
            if time.monotonic() - started >= self.acquisition.max_elapsed_seconds:
                raise AcquisitionBudgetExceeded()
            last_request = time.monotonic()
            receipt = {
                "method": method,
                "arguments": [str(a) for a in args],
                "requested_at": datetime.now(UTC).isoformat(),
            }
            try:
                return await asyncio.to_thread(getattr(self.source, method), *args)
            except Exception as exc:
                receipt["error_type"] = type(exc).__name__
                if isinstance(exc, BarAcquisitionError):
                    receipt["evidence"] = exc.evidence
                raise
            finally:
                receipt["received_at"] = datetime.now(UTC).isoformat()
                inputs["receipts"].append(receipt)

        async def acquire_calendar():
            sessions = await acquire("calendar", plan.start, plan.end)
            schedule = SessionSchedule(plan.start, plan.end, sessions, source="alpaca_calendar")
            await asyncio.to_thread(save_json_report, schedule.document(), output / "calendar.json")
            return sessions

        frames: dict[str, pd.DataFrame] = {}

        async def acquire_member(index, symbol):
            checkpoint = {"symbol": symbol}
            if time.monotonic() - started >= self.acquisition.max_elapsed_seconds:
                failure: dict[str, Any] = {"error_type": "AcquisitionBudgetExceeded", "status": "not_attempted"}
                inputs["failures"][symbol] = failure
                checkpoint.update(failure)
            else:
                receipts_before = len(inputs["receipts"])
                try:
                    frame = await acquire("daily", symbol, plan.start, plan.end, plan.feed, plan.adjustment)
                    if "evidence" in frame.attrs:
                        inputs["receipts"][-1]["evidence"] = frame.attrs["evidence"]
                    dataset = await asyncio.to_thread(_save_observations, frame, output)
                    inputs["datasets"][symbol] = dataset
                    frames[symbol] = frame
                    checkpoint.update(status="observed", dataset=dataset)
                except Exception as exc:
                    failure = {
                        "error_type": type(exc).__name__,
                        "status": "not_attempted" if isinstance(exc, AcquisitionBudgetExceeded) else "unavailable",
                    }
                    if isinstance(exc, BarAcquisitionError):
                        failure["evidence"] = exc.evidence
                    inputs["failures"][symbol] = failure
                    checkpoint.update(failure)
                if len(inputs["receipts"]) > receipts_before:
                    checkpoint["receipt"] = inputs["receipts"][-1]
            path = output / "members" / f"{index:04d}.json"
            await asyncio.to_thread(save_json_report, checkpoint, path)
            inputs["members"].append(
                {"artifact": str(path.relative_to(output)), "sha256": await asyncio.to_thread(_file_hash, path)}
            )

        try:
            sessions = await _drain_on_cancel(acquire_calendar())
            clock = pd.DatetimeIndex([pd.Timestamp(s.date, tz=ET_TZ) for s in sessions])
            for index, symbol in enumerate(symbols):
                await _drain_on_cancel(acquire_member(index, symbol))
            batch = DailyStudyInputs(frames, inputs["failures"])
            result = await asyncio.to_thread(self.compute or _compute, batch, clock, plan, sessions)
            result.setdefault("status", PanelStudyStatus.COMPLETED)
            result["completed_comparisons"] = plan.trial_count if result["status"] == PanelStudyStatus.COMPLETED else 0
        except Exception as exc:
            result = {
                "status": PanelStudyStatus.FAILED,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "completed_comparisons": 0,
            }
            if isinstance(exc, PanelCoverageError):
                result["coverage"] = exc.coverage
            # A terminal calendar failure still accounts for every declared member.
            for index, symbol in enumerate(symbols[len(inputs["members"]) :], start=len(inputs["members"])):
                failure = {
                    "status": "not_attempted",
                    "error_type": "PrerequisiteUnavailable",
                    "cause": type(exc).__name__,
                }
                inputs["failures"][symbol] = failure
                path = output / "members" / f"{index:04d}.json"
                await asyncio.to_thread(save_json_report, {"symbol": symbol, **failure}, path)
                inputs["members"].append(
                    {"artifact": str(path.relative_to(output)), "sha256": await asyncio.to_thread(_file_hash, path)}
                )
        result.update(run_id=run_id, plan_id=plan.identity, charged_trials=plan.trial_count, authorizes_promotion=False)
        await asyncio.to_thread(save_json_report, inputs, output / "inputs.json")
        for name in ("manifest", "inputs", "calendar"):
            path = output / f"{name}.json"
            if await asyncio.to_thread(path.exists):
                result[f"{name}_hash"] = await asyncio.to_thread(_file_hash, path)
        await asyncio.to_thread(save_json_report, result, output / "result.json")
        digest = await asyncio.to_thread(lambda: hashlib.sha256((output / "result.json").read_bytes()).hexdigest())
        await self.repository.record_diagnostic(
            run_id,
            {
                "kind": plan.document()["version"],
                "status": result["status"],
                "plan": plan.document(),
                "artifact": str(output / "result.json"),
                "artifact_hash": digest,
                "error_type": result.get("error_type"),
                "completed_comparisons": result["completed_comparisons"],
                "authorizes_promotion": False,
            },
        )
        return result
