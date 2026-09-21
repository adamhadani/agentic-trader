"""Alpha aggregates on the shared journal, with transactional replayable projections.

This is not an execution queue. Registry changes become visible between scans via
an immutable snapshot. Historical versions and rejected research remain intact.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import delete, func, select

from agentic_trader.config import AlphaPipelineConfig
from agentic_trader.execution.durable import EventKind, NotificationKind
from agentic_trader.market.bars import ObservationStatus
from agentic_trader.research.alpha.models import AlphaDefinition, DecisionStatus, RegistrySnapshot
from agentic_trader.research.alpha.probe import ProbePolicy, assess_probe, is_paper_scope, policy_document
from agentic_trader.research.alpha.validation import ValidationPolicy
from agentic_trader.storage.models import AlphaProjectionRecord, DomainEventRecord
from agentic_trader.storage.probe_state import load_enrolment, load_forward_record, probe_block_reason
from agentic_trader.storage.workflow import WorkflowStore, encode


ALPHA_EVENT_KINDS = (EventKind.ALPHA_RESEARCH, EventKind.ALPHA_REGISTRY, EventKind.ALPHA_FORECAST)
REGISTRY_KEY = "registry"
SESSION_DECISION_KEY_LENGTH = len("session-decision/") + len(hashlib.sha256().hexdigest())


def _forward_payloads(decisions, cursors, truncated):
    return {
        "decisions": [json.loads(payload) for payload in decisions],
        "cursors": {key: json.loads(payload) for key, payload in cursors},
        "truncated": truncated,
    }


def _empty_registry():
    return {"generation": 0, "active": [], "shadow": [], "probe": []}


def _normalized(registry):
    return {**_empty_registry(), **registry} if registry else _empty_registry()


class AlphaRepository:
    def __init__(
        self,
        store: WorkflowStore,
        policy: AlphaPipelineConfig | None = None,
        *,
        validation_policy: ValidationPolicy | None = None,
        probe_policy: ProbePolicy | None = None,
    ):
        self.store = store
        self.policy = policy or AlphaPipelineConfig()
        self.validation_policy = validation_policy or ValidationPolicy()
        self.probe_policy = probe_policy or ProbePolicy()

    def _variance_family_key(self, timeframe):
        return f"family/{timeframe}/{self.validation_policy.return_timeline}"

    async def _get(self, session, key):
        row = await session.get(AlphaProjectionRecord, (self.store.scope, key))
        return json.loads(row.payload) if row else None

    async def _project(self, session, key, payload, event_id):
        row = await session.get(AlphaProjectionRecord, (self.store.scope, key))
        if row is None:
            session.add(
                AlphaProjectionRecord(scope=self.store.scope, key=key, payload=encode(payload), event_id=event_id)
            )
        else:
            row.payload, row.event_id = encode(payload), event_id
        await session.flush()

    async def _append(self, session, key, payload, kind, actor):
        event = await self.store.append(
            session, stream=f"alpha/{key}", kind=kind, payload={"key": key, "value": payload, "actor": actor}
        )
        await self._project(session, key, payload, event.id)

    async def get(self, key):
        async with self.store.db.session_factory() as session:
            return await self._get(session, key)

    async def forward_records(self, *, since, limit, cursor_keys):
        """Bounded current projections; aliases and late forensic results are excluded.

        A candle cannot be recorded before its close. Event time is an efficient
        lower bound; the report additionally filters exact candle timestamps.
        One SELECT keeps decision outcomes mutually consistent during completion.
        """
        async with self.store.db.session_factory() as session:
            rows = list(
                await session.scalars(
                    select(AlphaProjectionRecord)
                    .join(DomainEventRecord, DomainEventRecord.id == AlphaProjectionRecord.event_id)
                    .where(
                        AlphaProjectionRecord.scope == self.store.scope,
                        AlphaProjectionRecord.key.like("session-decision/%"),
                        func.length(AlphaProjectionRecord.key) == SESSION_DECISION_KEY_LENGTH,
                        DomainEventRecord.recorded_at >= since,
                    )
                    .order_by(AlphaProjectionRecord.event_id.desc())
                    .limit(limit + 1)
                )
            )
            cursors = await session.scalars(
                select(AlphaProjectionRecord).where(
                    AlphaProjectionRecord.scope == self.store.scope,
                    AlphaProjectionRecord.key.in_(cursor_keys),
                )
            )
            decisions = [row.payload for row in rows[:limit]]
            cursor_payloads = [(row.key, row.payload) for row in cursors]
        return await asyncio.to_thread(_forward_payloads, decisions, cursor_payloads, len(rows) > limit)

    async def register(self, definition: AlphaDefinition, *, actor: str):
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource="alpha")
            key = f"version/{definition.version_id}"
            existing = await self._get(session, key)
            if existing:
                return
            await self._append(session, key, {"definition": definition.to_dict()}, EventKind.ALPHA_RESEARCH, actor)

    async def record_run(self, run_id, run, manifest):
        if run.get("policy") != asdict(self.validation_policy):
            raise ValueError("Research run requires the current validation policy")
        payload = {"run": run, "manifest": manifest}
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource="alpha")
            existing = await self._get(session, f"run/{run_id}")
            if existing is not None:
                if existing != payload:
                    raise ValueError("Research run identity is immutable")
                return
            await self._append(session, f"run/{run_id}", payload, EventKind.ALPHA_RESEARCH, "research_worker")
            await self._append(
                session,
                "research/latest",
                {
                    "run_id": run_id,
                    "symbol": manifest["symbol"],
                    "timeframe": manifest["timeframe"],
                    "trial_count": run["trial_count"],
                    "status": run.get("status", "completed"),
                    "recorded_at": datetime.now(UTC).isoformat(),
                },
                EventKind.ALPHA_RESEARCH,
                "research_worker",
            )
            global_family = await self._get(session, "family/all") or {"trial_count": 0}
            reserved = await self._get(session, f"research/reservation/{run_id}")
            if reserved:
                if run["trial_count"] > reserved["trials"]:
                    raise ValueError("Research exceeded its reserved trial budget")
            else:
                global_family["trial_count"] += run["trial_count"]
            await self._append(session, "family/all", global_family, EventKind.ALPHA_RESEARCH, "research_worker")
            family_key = self._variance_family_key(manifest["timeframe"])
            family = await self._get(session, family_key) or {"trial_count": 0, "sharpes": []}
            family["trial_count"] += run["trial_count"]
            family["sharpes"].extend(
                (t.get("candidate", {}).get("metrics") or t.get("metrics"))["per_bar_sharpe"]
                for t in run["trials"]
                if t.get("candidate") or t.get("metrics")
            )
            await self._append(session, family_key, family, EventKind.ALPHA_RESEARCH, "research_worker")

    async def begin_holdout(self, run_id: str, version_id: str):
        """Persist consumption BEFORE evaluating; crashed trials cannot reread for free."""
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource="alpha")
            run = await self._get(session, f"run/{run_id}")
            if not run or not await self._get(session, f"version/{version_id}"):
                raise ValueError("Unknown run/version")
            if run["run"].get("policy") != asdict(self.validation_policy):
                raise ValueError("Research policy changed; fresh discovery required before consuming holdout")
            if version_id not in {
                AlphaDefinition.from_dict(t["definition"]).version_id
                for t in run["run"]["trials"]
                if t["status"] == "evaluated" and t.get("definition")
            }:
                raise ValueError("Finalist is outside the frozen research run")
            manifest = run["manifest"]
            # Consumption is an interval, not a run ID or terminal date. Moving
            # the end by one day must not recycle almost the same holdout.
            identity = {"symbol": manifest["symbol"]}
            key = "holdout/" + hashlib.sha256(encode(identity).encode()).hexdigest()
            if "holdout_start" not in manifest:
                raise ValueError("Frozen holdout timestamps required")
            start = pd.Timestamp(manifest["holdout_start"])
            end = pd.Timestamp(manifest["end"])
            if start > end:
                raise ValueError("Invalid holdout interval")
            consumed = await self._get(session, key) or {"intervals": []}
            if any(
                start <= pd.Timestamp(item["end"]) and end >= pd.Timestamp(item["start"])
                for item in consumed["intervals"]
            ):
                raise ValueError("Overlapping holdout already consumed; collect a new untouched period")
            consumed["intervals"].append(
                {"start": str(start), "end": str(end), "run_id": run_id, "version_id": version_id}
            )
            await self._append(session, key, consumed, EventKind.ALPHA_RESEARCH, "research_worker")
            family = await self._get(session, self._variance_family_key(manifest["timeframe"]))
            global_family = await self._get(session, "family/all")
            observations = len(family["sharpes"])
            variance = None
            if observations > 1:
                variance = float(np.var(family["sharpes"], ddof=1))
            elif global_family["trial_count"] == 1:
                variance = 0.0  # No multiple-selection variance is needed for a single attempt.
            consumption = {
                "version_id": version_id,
                "key": key,
                "trial_count": global_family["trial_count"],
                "trial_variance": variance,
                "variance_observations": observations,
                "return_timeline": self.validation_policy.return_timeline,
            }
            await self._append(
                session, f"consumption/{run_id}", consumption, EventKind.ALPHA_RESEARCH, "research_worker"
            )
            return consumption

    async def record_qualification(self, run_id: str, version_id: str, decision: dict):
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource="alpha")
            consumption = await self._get(session, f"consumption/{run_id}")
            if not consumption or consumption["version_id"] != version_id:
                raise ValueError("Holdout must be consumed for this frozen finalist")
            key = f"qualification-run/{run_id}/{version_id}"
            if await self._get(session, key):
                raise ValueError("Qualification decision is immutable")
            payload = {
                **decision,
                "run_id": run_id,
                "version_id": version_id,
                "qualified_at": datetime.now(UTC).isoformat(),
            }
            await self._append(session, key, payload, EventKind.ALPHA_RESEARCH, "qualification_service")
            await self._append(
                session, f"qualification/{version_id}", payload, EventKind.ALPHA_RESEARCH, "qualification_service"
            )

    async def _change(self, version_id, *, actor, expected_generation, mode, days=None, renew=False, now=None):
        now = now or datetime.now(UTC)
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource="alpha")
            version = await self._get(session, f"version/{version_id}")
            if not version:
                raise ValueError("Unknown alpha version")
            registry = _normalized(await self._get(session, REGISTRY_KEY))
            if registry["generation"] != expected_generation:
                raise ValueError("Registry changed; refresh generation before retrying")
            definition = AlphaDefinition.from_dict(version["definition"])
            if mode == "probe":
                await self._authorize_probe(session, registry, definition, days=days, renew=renew, actor=actor, now=now)
            if mode == "active":
                if definition.clock is not None:
                    raise ValueError("Session-clock activation requires live acquisition and execution evidence")
                if definition.data_feed not in ("alpaca:iex", "alpaca:sip"):
                    raise ValueError("Passing qualification requires an explicit deployment feed")
                decision = await self._get(session, f"qualification/{version_id}")
                if not decision or not decision.get("qualified") or decision.get("reasons"):
                    raise ValueError("Passing qualification evidence required")
                if decision.get("policy") != asdict(self.validation_policy):
                    raise ValueError("Qualification policy changed; fresh evidence required")
                age = datetime.now(UTC) - datetime.fromisoformat(decision["qualified_at"])
                if age.total_seconds() < 0 or age.days > self.policy.qualification_max_age_days:
                    raise ValueError("Qualification evidence expired")
                frozen_incumbents = {
                    AlphaDefinition.from_dict(d).version_id for d in decision["manifest"]["incumbents"]
                }
                if frozen_incumbents != set(registry["active"]) - {version_id}:
                    raise ValueError("Incumbents changed since qualification; fresh incremental evidence required")
                if not definition.eligible_symbols or set(definition.eligible_symbols) != set(
                    decision.get("eligible_symbols", [])
                ):
                    raise ValueError("Version universe differs from qualification evidence")
                shadow = await self._get(session, f"shadow/{version_id}") or {"sessions": {}}
                if any(
                    len(shadow["sessions"].get(symbol, [])) < self.policy.minimum_shadow_sessions
                    for symbol in definition.eligible_symbols
                ):
                    raise ValueError(
                        f"At least {self.policy.minimum_shadow_sessions} observed shadow sessions per qualified symbol required"
                    )
                if any(
                    shadow.get("decisions", {}).get(symbol, 0) < self.policy.minimum_shadow_decisions
                    for symbol in definition.eligible_symbols
                ):
                    raise ValueError("Insufficient observed shadow decisions")
                owners = (*registry["active"], *await self._live_probes(session, registry, now))
                if await self._symbol_owner_conflict(session, definition, owners):
                    raise ValueError(
                        "Instrument already has an alpha owner; evaluate a combined portfolio in shadow first"
                    )
            # One current version per logical alpha, while immutable history remains.
            for field in ("active", "shadow", "probe"):
                keep = []
                for existing in registry[field]:
                    old = await self._get(session, f"version/{existing}")
                    if old["definition"]["alpha_id"] != definition.alpha_id:
                        keep.append(existing)
                registry[field] = keep
            if mode in ("active", "shadow", "probe"):
                registry[mode].append(version_id)
                registry[mode].sort()
            registry["generation"] += 1
            await self._append(session, REGISTRY_KEY, registry, EventKind.ALPHA_REGISTRY, actor)
            return registry["generation"]

    async def promote(self, version_id: str, *, actor: str, expected_generation: int):
        return await self._change(version_id, actor=actor, expected_generation=expected_generation, mode="active")

    async def set_shadow(self, version_id: str, *, actor: str, expected_generation: int):
        return await self._change(version_id, actor=actor, expected_generation=expected_generation, mode="shadow")

    async def demote(self, version_id: str, *, actor: str, expected_generation: int):
        return await self._change(version_id, actor=actor, expected_generation=expected_generation, mode="inactive")

    async def _symbol_owner_conflict(self, session, definition, owner_ids) -> bool:
        """True if any of ``owner_ids`` is a different alpha_id sharing an eligible symbol."""
        for current_id in owner_ids:
            current = await self._get(session, f"version/{current_id}")
            incumbent = AlphaDefinition.from_dict(current["definition"])
            if incumbent.alpha_id != definition.alpha_id and set(incumbent.eligible_symbols or ()) & set(
                definition.eligible_symbols
            ):
                return True
        return False

    async def _live_probes(self, session, registry, now):
        reasons = [
            await probe_block_reason(
                session,
                scope=self.store.scope,
                db=self.store.db,
                version_id=version_id,
                now=now,
                policy=self.probe_policy,
            )
            for version_id in registry["probe"]
        ]
        return [version_id for version_id, reason in zip(registry["probe"], reasons, strict=True) if reason is None]

    async def _authorize_probe(self, session, registry, definition, *, days, renew, actor, now):
        version_id = definition.version_id
        if not is_paper_scope(self.store.scope):
            raise ValueError("Paper probes run only on the Alpaca paper account scope")
        days = self.policy.probe_term_days if days is None else days
        if type(days) is not int or not 1 <= days <= self.probe_policy.max_term_days:
            raise ValueError(f"Probe term must be 1..{self.probe_policy.max_term_days} days")
        if definition.clock is not None or definition.timeframe != "1d":
            raise ValueError("Paper probes require an unclocked native daily definition")
        if definition.data_feed not in ("alpaca:iex", "alpaca:sip"):
            raise ValueError("Paper probes require an explicit deployment feed")
        if not definition.eligible_symbols:
            raise ValueError("Paper probes require an explicit symbol universe")
        assessment = assess_probe(await self._get(session, f"qualification/{version_id}"), self.probe_policy)
        if not assessment.eligible:
            raise ValueError("Probe policy not met: " + ", ".join(assessment.reasons))
        previous = await load_enrolment(session, self.store.scope, version_id)
        first = datetime.fromisoformat(previous["first_enrolled_at"]) if previous else now
        record = await load_forward_record(session, self.store.db, version_id, first, self.probe_policy)
        if record["killed"]:
            raise ValueError(
                f"Probe kill rule reached ({record['cumulative_r']:.2f}R); this version cannot be enrolled again"
            )
        if renew and version_id not in registry["probe"]:
            raise ValueError("Version is not a current probe; enrol it instead of renewing")
        live = await self._live_probes(session, registry, now)
        others = [v for v in live if v != version_id]
        if len(others) >= self.policy.max_probes:
            raise ValueError(f"All {self.policy.max_probes} probe slots are in use")
        if await self._symbol_owner_conflict(session, definition, (*registry["active"], *others)):
            raise ValueError("Instrument already has an alpha owner; demote it or wait for its probe to end")
        payload = {
            "policy": policy_document(self.probe_policy),
            "assessment": assessment.to_dict(),
            "first_enrolled_at": first.isoformat(),
            "enrolled_at": now.isoformat(),
            "expires_at": (now + timedelta(days=days)).isoformat(),
            "term_days": days,
            "renewals": (previous["renewals"] + 1) if renew and previous else 0,
            "actor": actor,
        }
        await self._append(session, f"probe/{version_id}", payload, EventKind.ALPHA_REGISTRY, actor)

    async def enrol_probe(self, version_id, *, actor, expected_generation, days=None, renew=False, now=None):
        return await self._change(
            version_id,
            actor=actor,
            expected_generation=expected_generation,
            mode="probe",
            days=days,
            renew=renew,
            now=now,
        )

    async def sweep_probes(self, *, now=None) -> list[str]:
        """Make derived expiry/kill durable. Correctness never depends on this running."""
        now = now or datetime.now(UTC)
        retired: list[str] = []
        async with self.store.db.session_factory() as session, session.begin():
            # Lock order everywhere: trading admission, then alpha registry.
            await self.store.lock(session)
            await self.store.lock(session, resource="alpha")
            registry = _normalized(await self._get(session, REGISTRY_KEY))
            for version_id in list(registry["probe"]):
                reason = await probe_block_reason(
                    session,
                    scope=self.store.scope,
                    db=self.store.db,
                    version_id=version_id,
                    now=now,
                    policy=self.probe_policy,
                )
                if reason is None:
                    continue
                registry["probe"].remove(version_id)
                retired.append(version_id)
                enrolment = await load_enrolment(session, self.store.scope, version_id) or {}
                version = await self._get(session, f"version/{version_id}")
                await self.store.add_notification(
                    session,
                    f"alpha-probe/{version_id}/retired/{enrolment.get('enrolled_at', 'unknown')}",
                    NotificationKind.MESSAGE,
                    {
                        "text": f"🧪 Paper probe {version['definition']['alpha_id']} retired: {reason}. "
                        "Open positions keep their broker-held protection.",
                        "formatted": False,
                    },
                )
            if retired:
                registry["generation"] += 1
                await self._append(session, REGISTRY_KEY, registry, EventKind.ALPHA_REGISTRY, "probe_sweep")
        return retired

    async def probe_report(self, *, now=None) -> list[dict]:
        now = now or datetime.now(UTC)
        async with self.store.db.session_factory() as session:
            registry = _normalized(await self._get(session, REGISTRY_KEY))
            report = []
            for version_id in registry["probe"]:
                version = await self._get(session, f"version/{version_id}")
                enrolment = await load_enrolment(session, self.store.scope, version_id)
                if enrolment is None:
                    raise ValueError("Probe registry references missing enrolment evidence")
                reason = await probe_block_reason(
                    session,
                    scope=self.store.scope,
                    db=self.store.db,
                    version_id=version_id,
                    now=now,
                    policy=self.probe_policy,
                )
                forward = await load_forward_record(
                    session,
                    self.store.db,
                    version_id,
                    datetime.fromisoformat(enrolment["first_enrolled_at"]),
                    self.probe_policy,
                )
                report.append(
                    {
                        "version_id": version_id,
                        "alpha_id": version["definition"]["alpha_id"],
                        "symbols": version["definition"].get("eligible_symbols") or [],
                        "expires_at": enrolment["expires_at"],
                        "renewals": enrolment["renewals"],
                        "live": reason is None,
                        "blocked_reason": reason,
                        "forward": forward,
                    }
                )
            return report

    async def snapshot(self, *, now=None) -> RegistrySnapshot:
        now = now or datetime.now(UTC)
        async with self.store.db.session_factory() as session:
            registry = _normalized(await self._get(session, REGISTRY_KEY))
            members = {
                "active": registry["active"],
                "shadow": registry["shadow"],
                # Only probes that may take new risk are ever installed for scanning.
                "probe": await self._live_probes(session, registry, now),
            }
            groups = {}
            for field, version_ids in members.items():
                versions = []
                for version_id in version_ids:
                    row = await self._get(session, f"version/{version_id}")
                    if not row:
                        raise ValueError("Registry references missing immutable version")
                    versions.append(AlphaDefinition.from_dict(row["definition"]))
                groups[field] = tuple(versions)
            return RegistrySnapshot(registry["generation"], groups["active"], groups["shadow"], groups["probe"])

    async def versions(self):
        async with self.store.db.session_factory() as session:
            rows = await session.scalars(
                select(AlphaProjectionRecord)
                .where(AlphaProjectionRecord.scope == self.store.scope, AlphaProjectionRecord.key.like("version/%"))
                .order_by(AlphaProjectionRecord.key)
            )
            return [AlphaDefinition.from_dict(json.loads(row.payload)["definition"]) for row in rows]

    async def record_forecast(self, key: str, payload: dict):
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource="alpha")
            if await self._get(session, f"forecast/{key}"):
                return
            await self._append(session, f"forecast/{key}", payload, EventKind.ALPHA_FORECAST, "shadow_scan")
            if payload.get("valid"):
                version_id = payload["version_id"]
                key = f"shadow/{version_id}"
                summary = await self._get(session, key) or {"sessions": {}}
                sessions = summary["sessions"].setdefault(payload["symbol"], [])
                day = payload["completed_at"][:10]
                if payload.get("decision"):
                    decisions = summary.setdefault("decisions", {})
                    decisions[payload["symbol"]] = decisions.get(payload["symbol"], 0) + 1
                if day not in sessions:
                    sessions.append(day)
                    sessions.sort()
                await self._append(session, key, summary, EventKind.ALPHA_FORECAST, "shadow_scan")

    async def rebuild(self):
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource="alpha")
            events = await session.scalars(
                select(DomainEventRecord)
                .where(DomainEventRecord.scope == self.store.scope, DomainEventRecord.kind.in_(ALPHA_EVENT_KINDS))
                .order_by(DomainEventRecord.id)
            )
            await session.execute(delete(AlphaProjectionRecord).where(AlphaProjectionRecord.scope == self.store.scope))
            for event in events:
                if event.schema_version != 1:
                    raise ValueError("Unsupported alpha event schema")
                payload = json.loads(event.payload)
                await self._project(session, payload["key"], payload["value"], event.id)

    async def acknowledge(self, snapshot: RegistrySnapshot, *, run_id: str):
        payload = {
            "generation": snapshot.generation,
            "run_id": run_id,
            "active": [d.version_id for d in snapshot.active],
        }
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource="alpha")
            if await self._get(session, "runtime/registry") != payload:
                await self._append(session, "runtime/registry", payload, EventKind.ALPHA_REGISTRY, "daemon_scan")

    async def status(self, *, run_id: str | None = None):
        snapshot = await self.snapshot()
        installed = await self.get("runtime/registry")
        ready = bool(
            installed
            and (run_id is None or installed["run_id"] == run_id)
            and installed["generation"] == snapshot.generation
        )
        return {
            "ready": ready,
            "generation": snapshot.generation,
            "active": len(snapshot.active),
            "shadow": len(snapshot.shadow),
            "installed": installed,
            "latest_research": await self.get("research/latest"),
            "latest_observation": await self.get("observation/latest"),
            "latest_session_decision": await self.get("session-decision/latest"),
            "pending_session_decisions": await self.get("session-decision/pending") or {},
            "research_family": await self.get("family/all"),
        }

    async def _session_decision(self, session, payload):
        await self._append(
            session,
            f"session-decision/{payload['decision_id']}",
            payload,
            EventKind.ALPHA_FORECAST,
            "session_decisions",
        )
        await self._append(session, "session-decision/latest", payload, EventKind.ALPHA_FORECAST, "session_decisions")

    async def plan_session_decisions(self, key, previous, cursor, claims):
        """CAS the cursor and consume windows in the same journal transaction."""
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource="alpha")
            registry = await self._get(session, REGISTRY_KEY)
            if not registry or registry["generation"] != cursor["generation"]:
                raise ValueError("Registry changed during session planning")
            if await self._get(session, key) != previous:
                return []
            pending = await self._get(session, "session-decision/pending") or {}
            accepted = []
            for claim in claims:
                if await self._get(session, f"session-decision/{claim['decision_id']}") is not None:
                    continue
                await self._session_decision(session, claim)
                if claim["status"] == DecisionStatus.CLAIMED:
                    pending[claim["decision_id"]] = claim["expires_at"]
                accepted.append(claim)
            if accepted:
                await self._append(
                    session, "session-decision/pending", pending, EventKind.ALPHA_FORECAST, "session_decisions"
                )
            # Avoid a new event every idle poll: advance only enrollment/calendar/window boundaries.
            if (
                previous is None
                or claims
                or any(cursor.get(k) != previous.get(k) for k in ("generation", "calendar", "gap"))
            ):
                await self._append(session, key, cursor, EventKind.ALPHA_FORECAST, "session_decisions")
            return accepted

    async def expire_session_decisions(self, now):
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource="alpha")
            pending = await self._get(session, "session-decision/pending") or {}
            expired = [key for key, expiry in pending.items() if pd.Timestamp(expiry) <= now]
            results = []
            for identity in expired:
                claim = await self._get(session, f"session-decision/{identity}")
                result = {
                    **claim,
                    "status": DecisionStatus.INTERRUPTED,
                    "finished_at": now.isoformat(),
                    "reason": "claim_expired_without_completion",
                }
                await self._session_decision(session, result)
                del pending[identity]
                results.append(result)
            if expired:
                await self._append(
                    session, "session-decision/pending", pending, EventKind.ALPHA_FORECAST, "session_decisions"
                )
            return results

    async def finish_session_decision(self, claim, evidence, *, clock):
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource="alpha")
            previous = await self._get(session, f"session-decision/{claim['decision_id']}")
            if not previous or previous["claim_id"] != claim["claim_id"]:
                raise ValueError("Session decision requires its original claim")
            if evidence.get("status") not in (DecisionStatus.SCORED, DecisionStatus.UNAVAILABLE):
                raise ValueError("Invalid session decision outcome")
            if evidence.get("forecast", {}).get("valid"):
                raise ValueError("Session decisions cannot grant shadow credit")
            if previous["status"] != DecisionStatus.CLAIMED:
                # Late work is forensic evidence, never an overwrite of the original outcome.
                key = f"session-decision-late/{claim['decision_id']}"
                if await self._get(session, key) is None:
                    await self._append(session, key, evidence, EventKind.ALPHA_FORECAST, "session_decisions")
                return previous
            if any(k in evidence and evidence[k] != v for k, v in claim.items() if k != "status"):
                raise ValueError("Session claim identity is immutable")
            now = pd.Timestamp(clock())
            if now.tzinfo is None or pd.isna(now):
                raise ValueError("Aware commit clock required")
            result = {**claim, **evidence}
            registry = await self._get(session, REGISTRY_KEY)
            if registry["generation"] != claim["registry_generation"]:
                result.update(status=DecisionStatus.UNAVAILABLE, reason="registry_changed_during_capture")
            if not pd.Timestamp(claim["claimed_at"]) <= now < pd.Timestamp(claim["expires_at"]):
                result.update(status=DecisionStatus.UNAVAILABLE, reason="decision_expired_before_commit")
            result["committed_at"] = now.isoformat()
            await self._session_decision(session, result)
            pending = await self._get(session, "session-decision/pending") or {}
            pending.pop(claim["decision_id"], None)
            await self._append(
                session, "session-decision/pending", pending, EventKind.ALPHA_FORECAST, "session_decisions"
            )
            return result

    async def _observation_latest(self, session, payload):
        for key in ("observation/latest", f"observation/latest/{payload['symbol']}"):
            previous = await self._get(session, key)
            if previous is None or payload["started_at"] >= previous["started_at"]:
                await self._append(session, key, payload, EventKind.ALPHA_RESEARCH, "session_observer")

    async def begin_observation(self, identity: str, payload: dict):
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource="alpha")
            key = f"observation/{identity}"
            if await self._get(session, key) is not None:
                raise ValueError("Observation identity is immutable")
            await self._append(session, key, payload, EventKind.ALPHA_RESEARCH, "session_observer")
            await self._observation_latest(session, payload)

    async def finish_observation(self, identity: str, payload: dict):
        """Retain receipts/corrections without granting forecast or qualification credit."""
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource="alpha")
            key = f"observation/{identity}"
            previous = await self._get(session, key)
            if not previous or previous["status"] != ObservationStatus.CAPTURING:
                raise ValueError("Only a pending observation can be completed")
            if any(payload.get(k) != v for k, v in previous.items() if k != "status"):
                raise ValueError("Observation capture contract is immutable")
            if payload["status"] == ObservationStatus.COMPLETE:
                bar = await self._get(session, payload["bar_key"])
                received = payload["received_at"]
                if bar is None:
                    bar = {"first_observed_at": received, "last_observed_at": received, "versions": {}}
                versions = bar["versions"]
                content = payload["content_hash"]
                versions[content] = min(versions.get(content, received), received)
                bar["first_observed_at"] = min(bar["first_observed_at"], received)
                if received >= bar["last_observed_at"]:
                    bar.update(last_observed_at=received, content_hash=content, observation_id=identity)
                bar["revision_count"] = len(versions) - 1
                await self._append(session, payload["bar_key"], bar, EventKind.ALPHA_RESEARCH, "session_observer")
            await self._append(session, key, payload, EventKind.ALPHA_RESEARCH, "session_observer")
            await self._observation_latest(session, payload)

    async def record_failure(
        self, run_id: str, *, symbol: str, timeframe: str, error: str, evidence: dict | None = None
    ):
        payload: dict = {
            "run_id": run_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "status": "failed",
            "error": error,
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        if evidence is not None:
            payload["evidence"] = evidence
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource="alpha")
            await self._append(
                session, f"research/failure/{run_id}", payload, EventKind.ALPHA_RESEARCH, "research_worker"
            )
            await self._append(session, "research/latest", payload, EventKind.ALPHA_RESEARCH, "research_worker")

    async def record_diagnostic(self, run_id: str, result: dict):
        """Retain an immutable diagnostic result without adding variance or qualification evidence."""
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource="alpha")
            reservation = await self._get(session, f"research/reservation/{run_id}")
            if not reservation:
                raise ValueError("Diagnostic requires a reserved research attempt")
            key = f"diagnostic/{run_id}"
            if await self._get(session, key):
                raise ValueError("Diagnostic result is immutable")
            payload = {
                **result,
                "run_id": run_id,
                "symbol": reservation["symbol"],
                "timeframe": reservation["timeframe"],
                "trial_count": reservation["trials"],
                "recorded_at": datetime.now(UTC).isoformat(),
            }
            await self._append(session, key, payload, EventKind.ALPHA_RESEARCH, "research_worker")
            await self._append(session, "research/latest", payload, EventKind.ALPHA_RESEARCH, "research_worker")

    async def exclude_observed_interval(
        self, *, symbol: str, start: str, end: str, trials: int, reason: str, actor: str
    ):
        """Record external diagnostic exposure; it cannot become a fresh holdout."""
        start_at, end_at = pd.Timestamp(start), pd.Timestamp(end)
        if not symbol.strip() or not reason.strip() or type(trials) is not int or trials < 0:
            raise ValueError("Explicit symbol, reason and nonnegative trial count required")
        if start_at.tzinfo is None or end_at.tzinfo is None or start_at > end_at:
            raise ValueError("Ordered timezone-aware observation interval required")
        symbol = symbol.strip().upper()
        payload = {
            "symbol": symbol,
            "start": start_at.isoformat(),
            "end": end_at.isoformat(),
            "trials": trials,
            "reason": reason,
        }
        identity = hashlib.sha256(encode(payload).encode()).hexdigest()
        key = "holdout/" + hashlib.sha256(encode({"symbol": symbol}).encode()).hexdigest()
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource="alpha")
            if await self._get(session, f"external-observation/{identity}"):
                return
            await self._append(session, f"external-observation/{identity}", payload, EventKind.ALPHA_RESEARCH, actor)
            consumed = await self._get(session, key) or {"intervals": []}
            consumed["intervals"].append({**payload, "external_observation": identity})
            await self._append(session, key, consumed, EventKind.ALPHA_RESEARCH, actor)
            family = await self._get(session, "family/all") or {"trial_count": 0}
            family["trial_count"] += trials
            await self._append(session, "family/all", family, EventKind.ALPHA_RESEARCH, actor)

    async def reserve_run(self, run_id: str, *, symbol: str, timeframe: str, trials: int):
        """Charge the predeclared trial budget before CPU work; crashes get no refund."""
        if type(trials) is not int or trials < 1:
            raise ValueError("Positive integer research budget required")
        payload = {"symbol": symbol, "timeframe": timeframe, "trials": trials}
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource="alpha")
            key = f"research/reservation/{run_id}"
            existing = await self._get(session, key)
            if existing:
                if existing != payload:
                    raise ValueError("Reserved research budget is immutable")
                return
            await self._append(session, key, payload, EventKind.ALPHA_RESEARCH, "research_worker")
            await self._append(
                session,
                "research/latest",
                {**payload, "run_id": run_id, "status": "running", "recorded_at": datetime.now(UTC).isoformat()},
                EventKind.ALPHA_RESEARCH,
                "research_worker",
            )
            family = await self._get(session, "family/all") or {"trial_count": 0}
            family["trial_count"] += trials
            await self._append(session, "family/all", family, EventKind.ALPHA_RESEARCH, "research_worker")
