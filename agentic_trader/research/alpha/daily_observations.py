"""One-shot prospective daily diagnostics over the shared journal and read-only source."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from agentic_trader.data.evidence import BarAcquisitionError
from agentic_trader.market.bars import ObservationStatus, SessionSchedule, utc_timestamp
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.alpha.acquisition import AcquisitionBudgetExceeded, drain_on_cancel, save_observations
from agentic_trader.research.alpha.daily_forecasts import compute_daily_forecasts, evaluate_daily_outcomes
from agentic_trader.research.alpha.daily_inputs import DailyStudyInputs, validate_daily_window
from agentic_trader.research.alpha.daily_plan import CALENDAR_FORWARD_DAYS
from agentic_trader.research.alpha.equity_universe import document_hash
from agentic_trader.research.alpha.models import DecisionStatus
from agentic_trader.storage.artifacts import save_json_report


DAILY_RECORD_BATCH = 100


def _save(payload, path):
    save_json_report(payload, path)
    return {"artifact": str(path), "artifact_hash": hashlib.sha256(path.read_bytes()).hexdigest()}


def _load(ref, directory):
    path = Path(ref["artifact"]).resolve()
    if not path.is_relative_to(directory.resolve()):
        raise ValueError("Campaign artifact outside configured private directory")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != ref["artifact_hash"]:
        raise ValueError("Campaign artifact hash mismatch")
    return json.loads(raw)


class DailyPanelService:
    def __init__(
        self, repository, source, plan, policy, *, acquisition, directory, runtime, clock=None, on_progress=None
    ):
        self.repository, self.source, self.plan, self.policy = repository, source, plan, policy
        self.acquisition = acquisition.model_copy(deep=True)
        self.directory, self.runtime = Path(directory), dict(runtime)
        self.clock = clock or (lambda: datetime.now(UTC))
        self.on_progress = on_progress
        self._lock = asyncio.Lock()
        self._calendar = None
        self._calendar_at = None
        self._checked_at = None
        self._enrolled = False
        if (
            len(plan.acquisition_symbols) > acquisition.max_symbols
            or (plan.end_date - plan.history_start).days >= acquisition.max_days
        ):
            raise ValueError("Daily campaign exceeds acquisition bounds")

    async def _records(self, kind):
        records, after = [], None
        while True:
            page = await self.repository.records(
                self.plan.campaign_id, kind=kind, limit=DAILY_RECORD_BATCH, after=after
            )
            records.extend(page["records"])
            if not page["truncated"]:
                return records
            if not page["next_after"] or page["next_after"] == after or len(records) > 732:
                raise ValueError("Daily campaign record pagination exceeds protocol bounds")
            after = page["next_after"]

    async def _sessions(self, now):
        if (
            self._calendar_at is not None
            and (now - self._calendar_at).total_seconds() < self.policy.calendar_refresh_seconds
        ):
            return self._calendar
        end = min(self.plan.end_date, max(self.plan.start_date, now.tz_convert(ET_TZ).date())) + timedelta(
            days=CALENDAR_FORWARD_DAYS
        )
        requested = utc_timestamp(self.clock())
        sessions = await drain_on_cancel(asyncio.to_thread(self.source.calendar, self.plan.history_start, end))
        received = utc_timestamp(self.clock())
        if received < requested:
            raise ValueError("Daily calendar receipt clock moved backwards")
        # This validates ordering/range independently of a provider adapter.
        schedule = SessionSchedule(self.plan.history_start, end, tuple(sessions), "alpaca_calendar")
        self._calendar, self._calendar_at = schedule.sessions, received
        self._calendar_receipt = {"requested_at": requested.isoformat(), "received_at": received.isoformat()}
        return self._calendar

    async def run_once(self):
        async with self._lock:
            now = utc_timestamp(self.clock())
            if self._checked_at is not None and now < self._checked_at:
                raise ValueError("Daily worker clock moved backwards")
            self._checked_at = now
            enrolled = not self._enrolled
            if enrolled:
                await self.repository.enroll(self.plan.campaign_id, self.plan.document(), self.plan.identity)
                self._enrolled = True
            await self.repository.expire(self.plan.campaign_id)
            sessions = await self._sessions(now)
            decisions = await self._records("decision")
            outcomes = {r["decision_id"] for r in await self._records("outcome")}
            known = {r["session_date"]: r for r in decisions}
            due = []
            calendar = [
                {"date": s.date.isoformat(), "open": s.open.isoformat(), "close": s.close.isoformat()} for s in sessions
            ]
            for session in sessions:
                if not self.plan.start_date <= session.date <= min(self.plan.end_date, now.tz_convert(ET_TZ).date()):
                    continue
                window = self.plan.decision_window(session.date, sessions)
                old = known.get(session.date.isoformat())
                if old:
                    if old["context"]["decision_window"] != window.document():
                        raise ValueError("Observed calendar changed frozen daily endpoints")
                    if (
                        old["status"] == DecisionStatus.SCORED
                        and old["decision_id"] not in outcomes
                        and now >= window.outcome_available_at
                    ):
                        due.append((window.outcome_expires_at, "outcome", old, window))
                elif now >= window.available_at:
                    due.append((window.expires_at, "decision", None, window))
            result = {
                "status": "enrolled" if enrolled else "idle",
                "decisions": 0,
                "outcomes": 0,
                "campaign_id": self.plan.campaign_id,
                "terminal_counts": {},
            }
            for _, kind, old, window in sorted(due, key=lambda row: (row[0], row[1])):
                if kind == "decision":
                    context = {
                        "decision_window": window.document(),
                        "calendar_hash": document_hash(
                            {"sessions": [row for row in calendar if row["date"] <= window.exit_date.isoformat()]}
                        ),
                    }
                    claim = await self.repository.claim_decision(
                        self.plan.campaign_id,
                        window.decision_date,
                        native_close=window.native_closed_at,
                        available_at=window.available_at,
                        expires_at=window.expires_at,
                        context=context,
                    )
                    if claim is None:
                        continue
                    terminal = await self._decision(claim, window, sessions, calendar)
                    result["decisions"] += 1
                else:
                    claim = await self.repository.claim_outcome(
                        old["decision_id"],
                        available_at=window.outcome_available_at,
                        expires_at=window.outcome_expires_at,
                    )
                    if claim is None:
                        continue
                    terminal = await self._outcome(claim, old, window, sessions, calendar)
                    result["outcomes"] += 1
                key = f"{kind}:{terminal['status']}"
                result["terminal_counts"][key] = result["terminal_counts"].get(key, 0) + 1
                if terminal["status"] not in (DecisionStatus.SCORED, ObservationStatus.COMPLETE):
                    result["status"] = "unavailable"
                elif result["status"] != "unavailable":
                    result["status"] = "recorded"
            return result

    async def _capture(self, end, directory, sessions, calendar, expires):
        plan = self.plan
        validate_daily_window(end, self.clock())
        # Reserve every inspected interval before touching prices. Calendar reads have no labels.
        for symbol in plan.acquisition_symbols:
            await self.repository.exclude_observed_interval(
                symbol=symbol,
                start=pd.Timestamp(plan.history_start, tz=ET_TZ).isoformat(),
                end=pd.Timestamp(end + timedelta(days=1), tz=ET_TZ).isoformat(),
                trials=0,
                reason=f"Prospective daily diagnostics {plan.identity}",
                actor="daily_panel",
            )
        await asyncio.to_thread(
            _save, {"sessions": calendar, "receipt": self._calendar_receipt}, directory / "calendar.json"
        )
        frames, receipts, datasets = {}, {}, {}
        failures: dict[str, dict] = {}
        started, previous = time.monotonic(), None

        async def acquire_member(index, symbol):
            nonlocal previous
            receipt: dict = {"status": "not_attempted"}
            try:
                if previous is not None:
                    await asyncio.sleep(
                        max(0, self.acquisition.min_request_interval_seconds - (time.monotonic() - previous))
                    )
                if (
                    time.monotonic() - started >= self.acquisition.max_elapsed_seconds
                    or utc_timestamp(self.clock()) >= expires
                ):
                    raise AcquisitionBudgetExceeded()
                previous = time.monotonic()
                receipt = {"status": "requested", "requested_at": utc_timestamp(self.clock()).isoformat()}
                try:
                    frame = await asyncio.to_thread(
                        self.source.daily, symbol, plan.history_start, end, plan.feed, plan.adjustment
                    )
                finally:
                    receipt["received_at"] = utc_timestamp(self.clock()).isoformat()
                receipt["status"] = "received"
                if "evidence" in frame.attrs:
                    receipt["evidence"] = frame.attrs["evidence"]
                if utc_timestamp(receipt["received_at"]) < utc_timestamp(receipt["requested_at"]):
                    raise ValueError("Daily source receipt clock moved backwards")
                datasets[symbol] = await asyncio.to_thread(save_observations, frame, directory / "datasets")
                frames[symbol] = frame
            except Exception as exc:
                failures[symbol] = {
                    "error_type": type(exc).__name__,
                    "status": "not_attempted" if isinstance(exc, AcquisitionBudgetExceeded) else "unavailable",
                }
                if isinstance(exc, BarAcquisitionError):
                    failures[symbol]["evidence"] = exc.evidence
                    receipt["evidence"] = exc.evidence
            receipts[symbol] = receipt
            await asyncio.to_thread(
                _save,
                {
                    "symbol": symbol,
                    "receipt": receipt,
                    "dataset": datasets.get(symbol),
                    "failure": failures.get(symbol),
                },
                directory / "members" / f"{index:04d}.json",
            )

        for index, symbol in enumerate(plan.acquisition_symbols):
            # A cancellation may stop the next read, but never discard this read's receipt.
            await drain_on_cancel(acquire_member(index, symbol))
        evidence = await asyncio.to_thread(
            _save, {"receipts": receipts, "datasets": datasets, "failures": failures}, directory / "inputs.json"
        )
        return DailyStudyInputs(frames, failures), receipts, evidence

    async def _decision(self, claim, window, sessions, calendar):
        directory = self.directory / self.plan.identity / claim["decision_id"] / claim["claim_id"]
        evidence: dict = {"authorizes_promotion": False}
        state_ref, status = None, DecisionStatus.UNAVAILABLE
        try:
            await asyncio.to_thread(
                _save,
                {"claim": claim, "protocol": self.plan.document(), "runtime": self.runtime},
                directory / "manifest.json",
            )
            batch, receipts, evidence["inputs"] = await self._capture(
                window.decision_date, directory, sessions, calendar, window.expires_at
            )
            previous = (
                await asyncio.to_thread(_load, claim["parent_state"], self.directory)
                if claim.get("parent_state")
                else None
            )
            forecast = await drain_on_cancel(
                asyncio.to_thread(
                    compute_daily_forecasts,
                    batch,
                    sessions,
                    self.plan,
                    decision_date=window.decision_date,
                    fit_cutoff=utc_timestamp(self.clock()),
                    receipts=receipts,
                    previous_residual_state=previous,
                )
            )
            evidence["forecast"] = await asyncio.to_thread(_save, forecast, directory / "forecast.json")
            if forecast.get("residual_state") is not None:
                state_ref = await asyncio.to_thread(
                    _save, forecast["residual_state"], directory / "residual-state.json"
                )
            status = (
                DecisionStatus.SCORED if forecast.get("status") == DecisionStatus.SCORED else DecisionStatus.UNAVAILABLE
            )
            evidence["models"] = [{"model": arm["model"], "status": arm["status"]} for arm in forecast["arms"]]
        except Exception as exc:
            evidence["error_type"] = type(exc).__name__
            evidence["failure"] = await asyncio.to_thread(
                _save, {"error_type": type(exc).__name__, "error": str(exc)}, directory / "failure.json"
            )
        terminal = await self.repository.finish_decision(claim, status=status, evidence=evidence, state_ref=state_ref)
        if self.on_progress is not None:
            await self.on_progress()
        return terminal

    async def _outcome(self, claim, decision, window, sessions, calendar):
        directory = self.directory / self.plan.identity / claim["decision_id"] / f"outcome-{claim['claim_id']}"
        evidence: dict = {"authorizes_promotion": False}
        status = ObservationStatus.UNAVAILABLE
        try:
            await asyncio.to_thread(
                _save,
                {"claim": claim, "decision_id": decision["decision_id"], "runtime": self.runtime},
                directory / "manifest.json",
            )
            forecast = await asyncio.to_thread(_load, decision["evidence"]["forecast"], self.directory)
            batch, receipts, evidence["inputs"] = await self._capture(
                window.exit_date, directory, sessions, calendar, window.outcome_expires_at
            )
            outcome = await drain_on_cancel(
                asyncio.to_thread(
                    evaluate_daily_outcomes,
                    forecast,
                    batch,
                    sessions,
                    self.plan,
                    receipts=receipts,
                    observed_at=utc_timestamp(self.clock()),
                )
            )
            evidence["outcome"] = await asyncio.to_thread(_save, outcome, directory / "outcome.json")
            status = (
                ObservationStatus.COMPLETE
                if outcome.get("status") == ObservationStatus.COMPLETE
                else ObservationStatus.UNAVAILABLE
            )
        except Exception as exc:
            evidence["error_type"] = type(exc).__name__
            evidence["failure"] = await asyncio.to_thread(
                _save, {"error_type": type(exc).__name__, "error": str(exc)}, directory / "failure.json"
            )
        terminal = await self.repository.finish_outcome(claim, status=status, evidence=evidence)
        if self.on_progress is not None:
            await self.on_progress()
        return terminal
