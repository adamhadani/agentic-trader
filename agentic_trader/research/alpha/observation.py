"""Prospective data-clock evidence; no formula, strategy, broker or notifier access."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pandas as pd

from agentic_trader.config import SessionObservationConfig
from agentic_trader.data.sessions import SessionAcquisitionError, SessionDataSource
from agentic_trader.market.bars import (
    SESSION_BAR_LAYOUT,
    ObservationStatus,
    SessionCoverageError,
    SessionSchedule,
    TradingSession,
    build_session_bars,
    session_bar_windows,
    utc_timestamp,
)
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.alpha.data import save_dataset
from agentic_trader.research.alpha.validation import frame_digest
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.artifacts import save_json_report
from agentic_trader.storage.workflow import encode


OBSERVATION_VERSION = "forward_session_observation_v1"


def observation_target(sessions, timeframe, now, *, window_seconds):
    now = utc_timestamp(now)
    eligible = [
        window
        for session in sessions
        for window in session_bar_windows(session, timeframe)
        if pd.Timedelta(0) <= now - window.closed_at <= pd.Timedelta(seconds=window_seconds)
    ]
    return eligible[-1] if eligible else None


def _file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _retain_capture(bars, schedule, target, timeframe, feed, output):
    dataset = save_dataset(bars, output, frame_digest(bars))
    calendar = save_json_report(schedule.document(), output / "calendar.json")
    inputs = save_json_report(
        {"dataset": dataset.name, "dataset_hash": _file_hash(dataset)},
        output / "inputs.json",
    )
    evidence = {"inputs_hash": _file_hash(inputs), "calendar_hash": _file_hash(calendar)}
    try:
        if bars.attrs.get("feed") != feed:
            raise ValueError("Deployment feed mismatch")
        # Use the exact replay contract through the target close, never the forming bar.
        data = build_session_bars(bars, schedule, timeframe, as_of=target.closed_at)
        row = data.signals.loc[target.opened_at]
        source_minutes = data.execution.loc[data.execution.index >= target.opened_at]
        return {
            **evidence,
            "coverage": data.coverage,
            "bar": row.to_dict(),
            "content_hash": frame_digest(source_minutes),
            "status": ObservationStatus.COMPLETE,
        }
    except (TypeError, ValueError) as exc:
        evidence.update(status=ObservationStatus.UNAVAILABLE, error_type=type(exc).__name__, error=str(exc))
        if isinstance(exc, SessionCoverageError):
            evidence["coverage"] = exc.coverage
        return evidence


def _publish(payload, output):
    path = save_json_report(payload, output / "result.json")
    return {**payload, "artifact": str(path), "artifact_hash": hashlib.sha256(path.read_bytes()).hexdigest()}


class SessionObservationService:
    def __init__(
        self,
        repository: AlphaRepository,
        source: SessionDataSource,
        policy: SessionObservationConfig,
        *,
        directory: Path,
        clock=None,
        runtime: dict,
    ):
        self.repository, self.source, self.policy = repository, source, policy.model_copy(deep=True)
        self.feed, self.directory, self.runtime = self.policy.feed, directory, runtime
        self.clock = clock or (lambda: datetime.now(UTC))
        self._lock = asyncio.Lock()
        self._calendar: tuple[TradingSession, ...] = ()
        self._calendar_read_at: pd.Timestamp | None = None
        self._calendar_date = None
        self.policy_document = {
            "version": OBSERVATION_VERSION,
            "bar_layout": SESSION_BAR_LAYOUT,
            **policy.model_dump(exclude={"enabled"}),
        }
        self.policy_id = hashlib.sha256(encode(self.policy_document).encode()).hexdigest()

    async def run_once(self):
        async with self._lock:
            now = utc_timestamp(self.clock())
            day = now.tz_convert(ET_TZ).date()
            # A successful empty calendar is a holiday. Failed reads are not cached.
            if (
                self._calendar_date != day
                or self._calendar_read_at is None
                or (now - self._calendar_read_at).total_seconds() >= self.policy.calendar_refresh_seconds
            ):
                self._calendar = await asyncio.to_thread(self.source.calendar, day, day)
                self._calendar_date = day
                self._calendar_read_at = now
            target = observation_target(
                self._calendar, self.policy.timeframe, now, window_seconds=self.policy.window_seconds
            )
            if target is None:
                return []
            schedule = SessionSchedule(day, day, self._calendar, source="alpaca_calendar")
            results = []
            for symbol in self.policy.symbols:
                # Never begin a stale catch-up capture after slow earlier symbols.
                requested = utc_timestamp(self.clock())
                if requested - target.closed_at > pd.Timedelta(seconds=self.policy.window_seconds):
                    break
                results.append(await self._capture(symbol, schedule, target, requested))
            return results

    async def _capture(self, symbol, schedule, target, requested):
        observation_id = uuid4().hex
        output = self.directory / observation_id
        bar_identity = {
            "symbol": symbol,
            "feed": self.feed,
            "bar_layout": SESSION_BAR_LAYOUT,
            "timeframe": self.policy.timeframe,
            "closed_at": target.closed_at.isoformat(),
        }
        payload = {
            **bar_identity,
            "observation_id": observation_id,
            "policy_id": self.policy_id,
            "policy": self.policy_document,
            "runtime": self.runtime,
            "opened_at": target.opened_at.isoformat(),
            "started_at": requested.isoformat(),
            "bar_key": "observed-bar/" + hashlib.sha256(encode(bar_identity).encode()).hexdigest(),
            "authorizes_promotion": False,
        }
        await asyncio.to_thread(save_json_report, payload, output / "manifest.json")
        # Exclude the entire inspected session once per policy/day, before prices.
        # This is data observation, not a formula search: it spends zero trials.
        await self.repository.exclude_observed_interval(
            symbol=symbol,
            start=pd.Timestamp(schedule.start, tz="UTC").isoformat(),
            end=(pd.Timestamp(schedule.end, tz="UTC") + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)).isoformat(),
            trials=0,
            reason=f"Forward data observation {self.policy_id}",
            actor="session_observer",
        )
        await self.repository.begin_observation(observation_id, {**payload, "status": ObservationStatus.CAPTURING})
        try:
            bars, requested, received, error = await asyncio.to_thread(self._read_minutes, symbol, schedule, target)
            if received < requested or requested < pd.Timestamp(payload["started_at"]):
                raise ValueError("Receipt clock moved backward; availability evidence rejected")
            payload.update(requested_at=requested.isoformat(), received_at=received.isoformat())
            if error is not None:
                raise error
            detail = await asyncio.to_thread(
                _retain_capture, bars, schedule, target, self.policy.timeframe, self.feed, output
            )
            payload.update(detail)
            if detail["status"] == ObservationStatus.COMPLETE:
                payload["availability_upper_bound_seconds"] = (received - target.closed_at).total_seconds()
        except Exception as exc:
            payload.update(status=ObservationStatus.UNAVAILABLE, error_type=type(exc).__name__, error=str(exc))
            if isinstance(exc, SessionAcquisitionError):
                payload["acquisition"] = exc.receipts
            if isinstance(exc, SessionCoverageError):
                payload["coverage"] = exc.coverage
        payload["finished_at"] = utc_timestamp(self.clock()).isoformat()
        payload["manifest_hash"] = await asyncio.to_thread(_file_hash, output / "manifest.json")
        result = await asyncio.to_thread(_publish, payload, output)
        await self.repository.finish_observation(observation_id, result)
        return result

    def _read_minutes(self, symbol, schedule, target):
        requested = utc_timestamp(self.clock())
        lag = (requested - target.closed_at).total_seconds()
        try:
            if not 0 <= lag <= self.policy.window_seconds:
                raise ValueError("Capture missed the forward sampling window; no catch-up observation")
            bars = self.source.minutes(symbol, schedule.sessions[0].open, target.closed_at, self.feed)
            return bars, requested, utc_timestamp(self.clock()), None
        except Exception as exc:
            return None, requested, utc_timestamp(self.clock()), exc
