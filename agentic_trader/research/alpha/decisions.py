"""Durable prospective decisions over receipt-stamped session bars.

Scheduling is independent of scans and trading permissions. A claimed window is
never replayed, even after failure, process death or provider bar corrections.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pandas as pd

from agentic_trader.config import SessionDecisionConfig
from agentic_trader.data.market_data import ContractMarketData
from agentic_trader.data.sessions import SessionAcquisitionError, SessionDataSource
from agentic_trader.market.bars import (
    SessionCoverageError,
    SessionSchedule,
    SessionSnapshot,
    build_session_bars,
    session_bar_windows,
    utc_timestamp,
)
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.alpha.data import save_dataset
from agentic_trader.research.alpha.models import DecisionStatus
from agentic_trader.research.alpha.shadow import observe_definition
from agentic_trader.research.alpha.validation import frame_digest
from agentic_trader.storage.artifacts import save_json_report
from agentic_trader.storage.workflow import encode


SESSION_DECISION_VERSION = "forward_session_decision_v1"


def decision_plan(definition, symbol, generation, previous, sessions, start, now, limit, runtime):
    """Pure cursor transition; calendar changes cannot erase previously known windows."""
    calendar = {s.date.isoformat(): {"open": s.open.isoformat(), "close": s.close.isoformat()} for s in sessions}
    cursor = {"generation": generation, "checked_at": now.isoformat(), "calendar": calendar}
    if previous is None or previous["generation"] != generation:
        cursor["enrolled_at"] = now.isoformat()
        if previous:
            cursor["gap"] = {"start": previous["checked_at"], "end": now.isoformat(), "reason": "registry_changed"}
        return cursor, []
    cursor["enrolled_at"] = previous["enrolled_at"]
    since = utc_timestamp(previous["checked_at"])
    if now < since:
        raise ValueError("Session decision clock moved backwards")
    for day, contract in previous["calendar"].items():
        if day >= start.isoformat() and calendar.get(day) != contract:
            raise ValueError("Observed session calendar changed; cursor preserved")
    floor = pd.Timestamp(start, tz=ET_TZ).tz_convert("UTC")
    if since < floor:
        cursor["gap"] = {"start": since.isoformat(), "end": floor.isoformat(), "reason": "outside_calendar_horizon"}
        since = floor
    elif "gap" in previous:
        cursor["gap"] = previous["gap"]
    windows = [w for s in sessions for w in session_bar_windows(s, definition.timeframe)]
    available, expires = definition.clock.windows(pd.DatetimeIndex([w.closed_at for w in windows], tz="UTC"))
    due = [i for i, t in enumerate(available) if since < t <= now]
    claims = []
    for i in due[:limit]:
        window = windows[i]
        identity = {"version_id": definition.version_id, "symbol": symbol, "closed_at": window.closed_at.isoformat()}
        decision_id = hashlib.sha256(encode(identity).encode()).hexdigest()
        status = (
            DecisionStatus.MISSED
            if now >= expires[i]
            else DecisionStatus.SUPERSEDED
            if i != due[-1]
            else DecisionStatus.CLAIMED
        )
        claims.append(
            {
                **identity,
                "decision_id": decision_id,
                "claim_id": str(uuid4()),
                "status": status,
                "version": SESSION_DECISION_VERSION,
                "timeframe": definition.timeframe,
                "feed": definition.data_feed,
                "opened_at": window.opened_at.isoformat(),
                "available_at": available[i].isoformat(),
                "expires_at": expires[i].isoformat(),
                "claimed_at": now.isoformat(),
                "registry_generation": generation,
                "runtime": runtime,
                "authorizes_promotion": False,
            }
        )
    if len(due) > limit:
        cursor["checked_at"] = available[due[limit - 1]].isoformat()
    return cursor, claims


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SessionDecisionService:
    def __init__(
        self,
        repository,
        source: SessionDataSource,
        policy: SessionDecisionConfig,
        *,
        directory: Path,
        runtime: dict,
        clock=None,
    ):
        self.repository, self.source, self.policy = repository, source, policy.model_copy(deep=True)
        self.feed, self.directory, self.runtime = self.policy.feed, directory, runtime
        self.clock = clock or (lambda: datetime.now(UTC))
        self._lock = asyncio.Lock()

    async def run_once(self):
        async with self._lock:
            now = utc_timestamp(self.clock())
            results = await self.repository.expire_session_decisions(now)
            registry = await self.repository.snapshot()
            definitions = [
                d for d in (*registry.active, *registry.shadow) if d.clock is not None and d.data_feed == self.feed
            ]
            if len(definitions) > self.policy.max_candidates:
                raise ValueError("Session candidate budget exceeded; no candidates silently omitted")
            pairs = [(d, s) for d in definitions for s in self.policy.symbols if s in (d.eligible_symbols or ())]
            if not pairs:
                return results
            day = now.tz_convert(ET_TZ).date()
            start = day - timedelta(days=self.policy.history_days - 1)
            sessions = await asyncio.to_thread(self.source.calendar, start, day)
            if sessions:
                SessionSchedule(start, day, sessions, source="alpaca_calendar")  # Validate observed dates/order.
            remaining = self.policy.max_decisions_per_poll
            for definition, symbol in pairs:
                if remaining <= 0:
                    break
                key = f"session-cursor/{definition.version_id}/{symbol}"
                previous = await self.repository.get(key)
                cursor, claims = await asyncio.to_thread(
                    decision_plan,
                    definition,
                    symbol,
                    registry.generation,
                    previous,
                    sessions,
                    start,
                    now,
                    remaining,
                    self.runtime,
                )
                accepted = await self.repository.plan_session_decisions(key, previous, cursor, claims)
                remaining -= len(accepted)
                for claim in accepted:
                    if claim["status"] == DecisionStatus.CLAIMED:
                        schedule = SessionSchedule(start, day, sessions, source="alpaca_calendar")
                        result = await self._capture(definition, schedule, claim)
                    else:
                        result = claim
                    results.append(result)
            return results

    async def _capture(self, definition, schedule, claim):
        output = self.directory / claim["decision_id"] / claim["claim_id"]
        await asyncio.to_thread(
            save_json_report,
            {
                "claim": claim,
                "definition": definition.to_dict(),
                "schedule": schedule.document(),
                "policy": self.policy.model_dump(),
            },
            output / "manifest.json",
        )
        # Exposure is durable BEFORE price access, including failures and crashes.
        start = pd.Timestamp(schedule.start, tz=ET_TZ).tz_convert("UTC")
        end = pd.Timestamp(schedule.end + timedelta(days=1), tz=ET_TZ).tz_convert("UTC")
        await self.repository.exclude_observed_interval(
            symbol=claim["symbol"],
            start=start.isoformat(),
            end=end.isoformat(),
            trials=0,
            reason=SESSION_DECISION_VERSION,
            actor="session_decisions",
        )
        payload = await asyncio.to_thread(self._read_and_score, definition, schedule, claim, output)
        return await self.repository.finish_session_decision(claim, payload, clock=self.clock)

    def _read_and_score(self, definition, schedule, claim, output):
        requested = utc_timestamp(self.clock())
        payload = {"status": DecisionStatus.UNAVAILABLE, "requested_at": requested.isoformat()}
        try:
            if not utc_timestamp(claim["available_at"]) <= requested < utc_timestamp(claim["expires_at"]):
                raise ValueError("decision_expired_before_read")
            minutes = self.source.minutes(
                claim["symbol"], schedule.sessions[0].open, utc_timestamp(claim["closed_at"]), self.feed
            )
            received = utc_timestamp(self.clock())
            payload["received_at"] = received.isoformat()
            # Preserve returned evidence even when invalid or late.
            dataset = save_dataset(minutes, output, frame_digest(minutes))
            payload.update(dataset=str(dataset), dataset_hash=_hash(dataset), frame_hash=frame_digest(minutes))
            bars = build_session_bars(minutes, schedule, definition.timeframe, as_of=claim["closed_at"])
            snapshot = SessionSnapshot(bars, requested, received, claim["symbol"])
            data = ContractMarketData(symbol=claim["symbol"], session_bars={definition.timeframe: snapshot})
            forecast = observe_definition(definition, data, utc_timestamp(self.clock()))
            payload.update(coverage=bars.coverage, forecast=forecast)
            if forecast.get("completed_at") != claim["closed_at"]:
                raise ValueError("decision_candle_mismatch")
            if "score" not in forecast:
                raise ValueError(forecast["reason"])
            payload["status"] = DecisionStatus.SCORED
        except Exception as exc:
            payload.update(status=DecisionStatus.UNAVAILABLE, error_type=type(exc).__name__, reason=str(exc))
            if isinstance(exc, SessionAcquisitionError):
                payload["acquisition"] = exc.receipts
            if isinstance(exc, SessionCoverageError):
                payload["coverage"] = exc.coverage
        finished = utc_timestamp(self.clock())
        payload.setdefault("received_at", finished.isoformat())
        payload["finished_at"] = finished.isoformat()
        if not requested <= utc_timestamp(payload["received_at"]) <= finished < utc_timestamp(claim["expires_at"]):
            payload.update(status=DecisionStatus.UNAVAILABLE, reason="invalid_or_expired_receipt")
        payload["manifest_hash"] = _hash(output / "manifest.json")
        path = save_json_report({**claim, **payload}, output / "result.json")
        return {**payload, "artifact": str(path), "artifact_hash": _hash(path)}
