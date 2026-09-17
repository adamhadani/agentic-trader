"""Injected panel application service over the existing diagnostic event journal."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

import pandas as pd

from agentic_trader.data.evidence import BarAcquisitionError
from agentic_trader.market.bars import SessionSchedule, TradingSession, utc_timestamp
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.alpha.data import save_dataset
from agentic_trader.research.alpha.panel import PanelCoverageError, align_daily_panel
from agentic_trader.research.alpha.panel_study import (
    PANEL_JOURNAL_SYMBOL,
    PanelStudyPlan,
    PanelStudyStatus,
    compute_panel_study,
)
from agentic_trader.research.alpha.validation import frame_digest
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.artifacts import save_json_report


class DailyPanelSource(Protocol):
    def calendar(self, start: date, end: date) -> tuple[TradingSession, ...]: ...
    def daily(self, symbol: str, start: date, end: date, feed: str, adjustment: str = "raw") -> pd.DataFrame: ...


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


def _compute(frames, clock, plan, sessions):
    panel = align_daily_panel(frames, clock, feed=plan.feed)
    return compute_panel_study(panel, plan)


class AlphaPanelService:
    def __init__(self, repository: AlphaRepository, source: DailyPanelSource, *, compute: Callable | None = None):
        self.repository = repository
        self.source = source
        self.compute = compute

    async def run(self, plan: PanelStudyPlan, output: Path, *, environment: dict, as_of=None):
        observed_at = utc_timestamp(as_of if as_of is not None else datetime.now(UTC))
        start = pd.Timestamp(plan.start, tz=ET_TZ)
        end = pd.Timestamp(plan.end + timedelta(days=1), tz=ET_TZ)
        if observed_at < end:
            raise ValueError("Panel requires fully elapsed historical native daily windows")
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
            },
            output / "manifest.json",
        )
        await self.repository.reserve_run(run_id, symbol=PANEL_JOURNAL_SYMBOL, timeframe="1d", trials=plan.trial_count)
        symbols = (*plan.symbols, plan.benchmark)
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
        inputs: dict[str, Any] = {"receipts": [], "datasets": {}}

        async def acquire(method, *args):
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

        try:
            sessions = await acquire("calendar", plan.start, plan.end)
            schedule = SessionSchedule(plan.start, plan.end, sessions, source="alpaca_calendar")
            await asyncio.to_thread(save_json_report, schedule.document(), output / "calendar.json")
            clock = pd.DatetimeIndex([pd.Timestamp(s.date, tz=ET_TZ) for s in sessions])
            frames = {}
            for symbol in symbols:
                frame = await acquire("daily", symbol, plan.start, plan.end, plan.feed, plan.adjustment)
                inputs["datasets"][symbol] = await asyncio.to_thread(_save_observations, frame, output)
                frames[symbol] = frame
            result = await asyncio.to_thread(self.compute or _compute, frames, clock, plan, sessions)
            result.update(status=PanelStudyStatus.COMPLETED, completed_comparisons=plan.trial_count)
        except Exception as exc:
            result = {
                "status": PanelStudyStatus.FAILED,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "completed_comparisons": 0,
            }
            if isinstance(exc, PanelCoverageError):
                result["coverage"] = exc.coverage
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
