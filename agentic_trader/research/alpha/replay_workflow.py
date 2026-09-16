"""Read-only provider adapter and journal/artifact orchestration for session replay."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pandas as pd

from agentic_trader.data.sessions import SessionDataSource
from agentic_trader.market.bars import (
    SessionCoverageError,
    SessionSchedule,
    build_session_bars,
    utc_timestamp,
)
from agentic_trader.research.alpha.data import save_dataset, save_json_report
from agentic_trader.research.alpha.replay import (
    SESSION_REPLAY_KIND,
    ReplayPlan,
    ReplayStatus,
    simulate_session_strategy,
)
from agentic_trader.research.alpha.validation import frame_digest
from agentic_trader.storage.alpha import AlphaRepository


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _compute(plan: ReplayPlan, bars: pd.DataFrame, schedule: SessionSchedule, as_of, output: Path):
    digest = frame_digest(bars)
    dataset = save_dataset(bars, output, digest)
    calendar = save_json_report(schedule.document(), output / "calendar.json")
    save_json_report(
        {
            "dataset": dataset.name,
            "dataset_hash": _file_digest(dataset),
            "calendar_hash": _file_digest(calendar),
            "content_hash": digest,
            "attrs": bars.attrs,
            "captured_at": datetime.now(UTC).isoformat(),
        },
        output / "observations.json",
    )
    data = build_session_bars(bars, schedule, plan.definition.timeframe, as_of=as_of)
    result = simulate_session_strategy(plan.definition, data, policy=plan.policy)
    result["net_returns"] = [
        {"bar_start": t.isoformat(), "net_return": float(v)} for t, v in result["net_returns"].items()
    ]
    result["signal_bars"] = [
        {"bar_start": t.isoformat(), "closed_at": data.closed_at[i].isoformat(), **row.to_dict()}
        for i, (t, row) in enumerate(data.signals.iterrows())
    ]
    return result


class AlphaReplayService:
    def __init__(self, repository: AlphaRepository, source: SessionDataSource):
        self.repository = repository
        self.source = source

    async def run(self, plan: ReplayPlan, output: Path, *, environment: dict, as_of=None) -> dict:
        as_of = utc_timestamp(as_of if as_of is not None else datetime.now(UTC))
        if as_of <= plan.start_at:
            raise ValueError("Replay needs observed history before its as-of boundary")
        await asyncio.to_thread(output.mkdir, parents=True, mode=0o700)
        run_id = uuid4().hex
        manifest = {
            "run_id": run_id,
            "plan_id": plan.identity,
            "plan": plan.document(),
            "environment": environment,
            "as_of": as_of.isoformat(),
        }
        await asyncio.to_thread(save_json_report, manifest, output / "manifest.json")
        # Both budget and exposure commit before any provider/data evaluation.
        await self.repository.reserve_run(run_id, symbol=plan.symbol, timeframe=plan.definition.timeframe, trials=1)
        await self.repository.exclude_observed_interval(
            symbol=plan.symbol,
            start=plan.start_at.isoformat(),
            end=min(plan.end_at, as_of).isoformat(),
            trials=0,
            reason=f"Session replay {run_id}; frozen plan {plan.identity}",
            actor="session_replay",
        )
        try:
            sessions = await asyncio.to_thread(self.source.calendar, plan.start, plan.end)
            schedule = SessionSchedule(plan.start, plan.end, sessions, source="alpaca_calendar")
            bars = await asyncio.to_thread(
                self.source.minutes, plan.symbol, plan.start_at, min(plan.end_at, as_of), plan.definition.data_feed
            )
            detail = await asyncio.to_thread(_compute, plan, bars, schedule, as_of, output)
            result = {**detail, "status": ReplayStatus.COMPLETED}
        except Exception as exc:
            result = {"status": ReplayStatus.FAILED, "error_type": type(exc).__name__, "error": str(exc)}
            if isinstance(exc, SessionCoverageError):
                result["coverage"] = exc.coverage
        result.update(run_id=run_id, plan_id=plan.identity, authorizes_promotion=False)
        for name in ("manifest", "observations"):
            artifact = output / f"{name}.json"
            if await asyncio.to_thread(artifact.exists):
                result[f"{name}_hash"] = await asyncio.to_thread(_file_digest, artifact)
        path = await asyncio.to_thread(save_json_report, result, output / "result.json")
        digest = await asyncio.to_thread(_file_digest, path)
        await self.repository.record_diagnostic(
            run_id,
            {
                "kind": SESSION_REPLAY_KIND,
                "status": result["status"],
                "plan": plan.document(),
                "artifact": str(path),
                "artifact_hash": digest,
                "coverage": result.get("coverage"),
                "error_type": result.get("error_type"),
                "authorizes_promotion": False,
            },
        )
        return result
