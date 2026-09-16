"""Alpha aggregates on the shared journal, with transactional replayable projections.

This is not an execution queue. Registry changes become visible between scans via
an immutable snapshot. Historical versions and rejected research remain intact.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import numpy as np
import pandas as pd
from sqlalchemy import delete, select

from agentic_trader.config import AlphaPipelineConfig
from agentic_trader.execution.durable import EventKind
from agentic_trader.research.alpha.models import AlphaDefinition, RegistrySnapshot
from agentic_trader.storage.models import AlphaProjectionRecord, DomainEventRecord
from agentic_trader.storage.workflow import WorkflowStore, encode


ALPHA_EVENT_KINDS = (EventKind.ALPHA_RESEARCH, EventKind.ALPHA_REGISTRY, EventKind.ALPHA_FORECAST)
REGISTRY_KEY = "registry"


class AlphaRepository:
    def __init__(self, store: WorkflowStore, policy: AlphaPipelineConfig | None = None):
        self.store = store
        self.policy = policy or AlphaPipelineConfig()

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

    async def register(self, definition: AlphaDefinition, *, actor: str):
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource="alpha")
            key = f"version/{definition.version_id}"
            existing = await self._get(session, key)
            if existing:
                return
            await self._append(session, key, {"definition": definition.to_dict()}, EventKind.ALPHA_RESEARCH, actor)

    async def record_run(self, run_id, run, manifest):
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
            family_key = f"family/{manifest['timeframe']}"
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
            family = await self._get(session, f"family/{manifest['timeframe']}")
            global_family = await self._get(session, "family/all")
            consumption = {
                "version_id": version_id,
                "key": key,
                "trial_count": global_family["trial_count"],
                "trial_variance": float(np.var(family["sharpes"], ddof=1)) if len(family["sharpes"]) > 1 else 0,
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

    async def _change(self, version_id, *, actor, expected_generation, mode):
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource="alpha")
            version = await self._get(session, f"version/{version_id}")
            if not version:
                raise ValueError("Unknown alpha version")
            registry = await self._get(session, REGISTRY_KEY) or {"generation": 0, "active": [], "shadow": []}
            if registry["generation"] != expected_generation:
                raise ValueError("Registry changed; refresh generation before retrying")
            definition = AlphaDefinition.from_dict(version["definition"])
            if mode == "active":
                if definition.data_feed not in ("alpaca:iex", "alpaca:sip"):
                    raise ValueError("Passing qualification requires an explicit deployment feed")
                decision = await self._get(session, f"qualification/{version_id}")
                if not decision or not decision.get("qualified") or decision.get("reasons"):
                    raise ValueError("Passing qualification evidence required")
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
                for current_id in registry["active"]:
                    current = await self._get(session, f"version/{current_id}")
                    incumbent = AlphaDefinition.from_dict(current["definition"])
                    if incumbent.alpha_id != definition.alpha_id and set(incumbent.eligible_symbols or ()) & set(
                        definition.eligible_symbols
                    ):
                        raise ValueError(
                            "Instrument already has an alpha owner; evaluate a combined portfolio in shadow first"
                        )
            # One current version per logical alpha, while immutable history remains.
            for field in ("active", "shadow"):
                keep = []
                for existing in registry[field]:
                    old = await self._get(session, f"version/{existing}")
                    if old["definition"]["alpha_id"] != definition.alpha_id:
                        keep.append(existing)
                registry[field] = keep
            if mode in ("active", "shadow"):
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

    async def snapshot(self) -> RegistrySnapshot:
        async with self.store.db.session_factory() as session:
            registry = await self._get(session, REGISTRY_KEY) or {"generation": 0, "active": [], "shadow": []}
            # The entire registry is one atomic value; immutable versions can be read
            # afterwards without requiring a long transaction/screening lock.
            groups = []
            for field in ("active", "shadow"):
                versions = []
                for version_id in registry[field]:
                    row = await self._get(session, f"version/{version_id}")
                    if not row:
                        raise ValueError("Registry references missing immutable version")
                    versions.append(AlphaDefinition.from_dict(row["definition"]))
                groups.append(tuple(versions))
            return RegistrySnapshot(registry["generation"], groups[0], groups[1])

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
            "research_family": await self.get("family/all"),
        }

    async def record_failure(self, run_id: str, *, symbol: str, timeframe: str, error: str):
        payload = {
            "run_id": run_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "status": "failed",
            "error": error,
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource="alpha")
            await self._append(
                session, f"research/failure/{run_id}", payload, EventKind.ALPHA_RESEARCH, "research_worker"
            )
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
