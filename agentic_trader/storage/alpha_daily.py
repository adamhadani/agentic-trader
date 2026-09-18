"""Prospective daily research aggregates on the existing alpha event journal.

Claims are single-use observations, not retryable work. A campaign has one residual
state lineage; the state pointer and forecast commit under the same parent CAS.
Timestamps are database wall-clock samples taken after acquiring the journal lock,
not client request times or PostgreSQL transaction-start timestamps.
"""

from __future__ import annotations

import json
import re
from datetime import date
from uuid import uuid4

from sqlalchemy import JSON, and_, cast, or_, select, text, type_coerce

from agentic_trader.execution.durable import EventKind
from agentic_trader.market.bars import ObservationStatus, utc_timestamp
from agentic_trader.research.alpha.equity_universe import document_hash
from agentic_trader.research.alpha.models import DecisionStatus
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.models import AlphaProjectionRecord, DomainEventRecord


DAILY_HYPOTHESIS_COUNT = 4
MAX_DAILY_RECORDS = 1000
MAX_DAILY_PAYLOAD_BYTES = 65536
DAILY_ACTOR = "daily_panel_observer"
DAILY_PREFIXES = {"campaign": "daily-campaign/", "decision": "daily-decision/", "outcome": "daily-outcome/"}


def _document(value):
    if not isinstance(value, dict):
        raise TypeError("Small JSON document required")
    encoded = json.dumps(value, sort_keys=True, allow_nan=False)
    if len(encoded.encode()) > MAX_DAILY_PAYLOAD_BYTES:
        raise ValueError("Daily projections contain summaries and artifact references, not full matrices")
    return json.loads(encoded)


def _reference(value):
    if value is None:
        return None
    value = _document(value)
    if (
        set(value) != {"artifact", "artifact_hash"}
        or not isinstance(value["artifact"], str)
        or not value["artifact"].strip()
        or not isinstance(value["artifact_hash"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", value["artifact_hash"])
    ):
        raise ValueError("Immutable artifact path and SHA256 reference required")
    return value


def _evidence(value, *, required=None):
    value = _document(value)
    if required is not None and required not in value:
        raise ValueError(f"Completed daily evidence requires its {required} artifact reference")
    for key in ("inputs", "forecast", "outcome", "failure"):
        if key in value and _reference(value[key]) is None:
            raise ValueError("Daily evidence artifact reference cannot be null")
    return value


def _campaign_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("Bounded explicit campaign identity required")
    return value


def _limit(value):
    if type(value) is not int or not 1 <= value <= MAX_DAILY_RECORDS:
        raise ValueError("Bounded daily record limit required")
    return value


def _key(kind, identity):
    return DAILY_PREFIXES[kind] + identity


def _claim_matches(previous, claim):
    if not isinstance(claim, dict) or previous is None:
        raise ValueError("Original daily claim required")
    identity = {k: v for k, v in claim.items() if k != "claim_hash"}
    if previous.get("claim_hash") != claim.get("claim_hash") or document_hash(identity) != claim.get("claim_hash"):
        raise ValueError("Original immutable daily claim required")


class DailyCampaignRepository(AlphaRepository):
    """Specialized alpha aggregate; shares locking, event format and replay exactly."""

    async def _now(self, session):
        dialect = session.bind.dialect.name
        if dialect == "postgresql":
            value = await session.scalar(text("SELECT clock_timestamp()"))
        elif dialect == "sqlite":
            value = await session.scalar(text("SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"))
        else:
            raise ValueError(f"Unsupported daily journal database: {dialect}")
        return utc_timestamp(value)

    async def _write(self, session, kind, identity, payload):
        await self._append(session, _key(kind, identity), payload, EventKind.ALPHA_FORECAST, DAILY_ACTOR)

    async def campaign(self, campaign_id):
        return await self.get(_key("campaign", _campaign_id(campaign_id)))

    async def decision(self, decision_id):
        return await self.get(_key("decision", decision_id))

    async def outcome(self, decision_id):
        return await self.get(_key("outcome", decision_id))

    async def enroll(self, campaign_id, protocol: dict, protocol_hash: str, initial_state: dict | None = None):
        campaign_id = _campaign_id(campaign_id)
        protocol, initial_state = _document(protocol), _reference(initial_state)
        if document_hash(protocol) != protocol_hash:
            raise ValueError("Frozen protocol hash does not match its document")
        identity = {
            "campaign_id": campaign_id,
            "protocol": protocol,
            "protocol_hash": protocol_hash,
            "initial_state": initial_state,
        }
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource="alpha")
            previous = await self._get(session, _key("campaign", campaign_id))
            if previous is not None:
                if any(previous[k] != value for k, value in identity.items()):
                    raise ValueError("Daily campaign enrollment is immutable")
                return previous
            now = await self._now(session)
            campaign = {
                **identity,
                "enrolled_at": now.isoformat(),
                "state_ref": initial_state,
                "state_generation": 0,
                "inflight_decision": None,
                "last_session": None,
                "trial_count": DAILY_HYPOTHESIS_COUNT,
                "authorizes_promotion": False,
            }
            reservation = {"symbol": f"panel:{campaign_id}", "timeframe": "1d", "trials": DAILY_HYPOTHESIS_COUNT}
            reservation_key = f"research/reservation/daily/{campaign_id}"
            if await self._get(session, reservation_key) is not None:
                raise ValueError("Daily campaign reservation exists without enrollment")
            family = await self._get(session, "family/all") or {"trial_count": 0}
            family["trial_count"] += DAILY_HYPOTHESIS_COUNT
            await self._append(session, reservation_key, reservation, EventKind.ALPHA_RESEARCH, DAILY_ACTOR)
            await self._append(session, "family/all", family, EventKind.ALPHA_RESEARCH, DAILY_ACTOR)
            await self._write(session, "campaign", campaign_id, campaign)
            return campaign

    async def _expire_decision(self, session, previous, campaign, now):
        result = {
            **previous,
            "status": DecisionStatus.INTERRUPTED,
            "reason": "decision_expired",
            "recorded_at": now.isoformat(),
        }
        await self._write(session, "decision", previous["decision_id"], result)
        if campaign["inflight_decision"] == previous["decision_id"]:
            campaign["inflight_decision"] = None
            await self._write(session, "campaign", campaign["campaign_id"], campaign)
        return result

    async def claim_decision(self, campaign_id, session_date, *, native_close, available_at, expires_at, context: dict):
        campaign_id = _campaign_id(campaign_id)
        day = date.fromisoformat(str(session_date)).isoformat()
        close, available, expires = map(utc_timestamp, (native_close, available_at, expires_at))
        if not close <= available < expires:
            raise ValueError("Ordered daily decision window required")
        context = _document(context)
        outcome_window = context.get("decision_window", {})
        outcome_available, outcome_expires = (
            utc_timestamp(outcome_window[key]) for key in ("outcome_available_at", "outcome_expires_at")
        )
        if not expires < outcome_available < outcome_expires:
            raise ValueError("Frozen future outcome window required")
        identity = {"campaign_id": campaign_id, "session_date": day}
        decision_id = document_hash(identity)
        contract = {
            **identity,
            "native_close": close.isoformat(),
            "available_at": available.isoformat(),
            "expires_at": expires.isoformat(),
            "context": context,
        }
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource="alpha")
            campaign = await self._get(session, _key("campaign", campaign_id))
            if campaign is None:
                raise ValueError("Daily campaign must enroll before acquisition")
            existing = await self._get(session, _key("decision", decision_id))
            if existing is not None:
                if any(existing[k] != value for k, value in contract.items()):
                    raise ValueError("Daily decision calendar and context are immutable")
                return None
            now = await self._now(session)
            if now < utc_timestamp(campaign["enrolled_at"]):
                raise ValueError("Daily database clock moved before enrollment")
            if now < available:
                return None
            if campaign["inflight_decision"] is not None:
                inflight = await self._get(session, _key("decision", campaign["inflight_decision"]))
                if now < utc_timestamp(inflight["expires_at"]):
                    return None
                await self._expire_decision(session, inflight, campaign, now)
            reason = None
            if utc_timestamp(campaign["enrolled_at"]) >= close:
                reason = "not_enrolled_before_native_close"
            elif now >= expires:
                reason = "decision_window_missed"
            elif campaign["last_session"] is not None and day <= campaign["last_session"]:
                reason = "decision_precedes_campaign_cursor"
            claim = {
                **contract,
                "decision_id": decision_id,
                "claim_id": uuid4().hex,
                "status": DecisionStatus.MISSED if reason else DecisionStatus.CLAIMED,
                "claimed_at": now.isoformat(),
                "parent_generation": campaign["state_generation"],
                "parent_state": campaign["state_ref"],
                "protocol_hash": campaign["protocol_hash"],
                "authorizes_promotion": False,
            }
            if reason:
                claim.update(reason=reason, recorded_at=now.isoformat())
            claim["claim_hash"] = document_hash(claim)
            await self._write(session, "decision", decision_id, claim)
            campaign["last_session"] = max(day, campaign["last_session"] or day)
            if reason is None:
                campaign["inflight_decision"] = decision_id
            await self._write(session, "campaign", campaign_id, campaign)
            return None if reason else claim

    async def _late(self, session, kind, claim, completion, now):
        identity = document_hash(completion)
        key = f"daily-late/{kind}/{claim['decision_id']}/{identity}"
        if await self._get(session, key) is None:
            await self._append(
                session,
                key,
                {
                    **completion,
                    "decision_id": claim["decision_id"],
                    "recorded_at": now.isoformat(),
                    "authorizes_promotion": False,
                },
                EventKind.ALPHA_FORECAST,
                DAILY_ACTOR,
            )

    async def finish_decision(self, claim: dict, *, status, evidence: dict, state_ref: dict | None = None):
        if status not in (DecisionStatus.SCORED, DecisionStatus.UNAVAILABLE):
            raise ValueError("Daily completion must be scored or explicitly unavailable")
        evidence = _evidence(evidence, required="forecast" if status == DecisionStatus.SCORED else None)
        state_ref = _reference(state_ref)
        if status == DecisionStatus.SCORED and state_ref is None:
            raise ValueError("Scored daily forecast requires its resulting immutable state")
        completion = {"status": status, "evidence": evidence, "state_ref": state_ref}
        completion_hash = document_hash(completion)
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource="alpha")
            previous = await self._get(session, _key("decision", claim["decision_id"]))
            _claim_matches(previous, claim)
            now = await self._now(session)
            if previous["status"] != DecisionStatus.CLAIMED:
                if previous.get("completion_hash") == completion_hash:
                    return previous
                if previous["status"] == DecisionStatus.INTERRUPTED:
                    await self._late(session, "decision", claim, completion, now)
                    return previous
                raise ValueError("Daily forecast completion is immutable")
            campaign = await self._get(session, _key("campaign", claim["campaign_id"]))
            if not utc_timestamp(claim["claimed_at"]) <= now < utc_timestamp(claim["expires_at"]):
                result = await self._expire_decision(session, previous, campaign, now)
                await self._late(session, "decision", claim, completion, now)
                return result
            if (
                campaign["state_generation"] != claim["parent_generation"]
                or campaign["state_ref"] != claim["parent_state"]
                or campaign["inflight_decision"] != claim["decision_id"]
            ):
                raise ValueError("Daily claim parent state changed")
            result = {**previous, **completion, "completion_hash": completion_hash, "recorded_at": now.isoformat()}
            if state_ref is not None:
                campaign["state_ref"] = state_ref
                campaign["state_generation"] += 1
            campaign["inflight_decision"] = None
            await self._write(session, "decision", claim["decision_id"], result)
            await self._write(session, "campaign", claim["campaign_id"], campaign)
            return result

    async def claim_outcome(self, decision_id, *, available_at, expires_at):
        available, expires = map(utc_timestamp, (available_at, expires_at))
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource="alpha")
            decision = await self._get(session, _key("decision", decision_id))
            if decision is None or decision["status"] not in (DecisionStatus.SCORED, DecisionStatus.UNAVAILABLE):
                raise ValueError("Outcome requires a committed daily decision")
            window = decision["context"]["decision_window"]
            if available != utc_timestamp(window["outcome_available_at"]) or expires != utc_timestamp(
                window["outcome_expires_at"]
            ):
                raise ValueError("Outcome must use the frozen decision window")
            if await self._get(session, _key("outcome", decision_id)) is not None:
                return None
            now = await self._now(session)
            if now < available:
                return None
            missed = now >= expires
            claim = {
                "decision_id": decision_id,
                "campaign_id": decision["campaign_id"],
                "session_date": decision["session_date"],
                "claim_id": uuid4().hex,
                "status": ObservationStatus.UNAVAILABLE if missed else ObservationStatus.CAPTURING,
                "available_at": available.isoformat(),
                "expires_at": expires.isoformat(),
                "claimed_at": now.isoformat(),
                "decision_completion_hash": decision["completion_hash"],
                "authorizes_promotion": False,
            }
            if missed:
                claim.update(reason="outcome_window_missed", recorded_at=now.isoformat())
            claim["claim_hash"] = document_hash(claim)
            await self._write(session, "outcome", decision_id, claim)
            return None if missed else claim

    async def finish_outcome(self, claim: dict, *, status, evidence: dict):
        if status not in (ObservationStatus.COMPLETE, ObservationStatus.UNAVAILABLE):
            raise ValueError("Daily outcome must be complete or explicitly unavailable")
        completion = {
            "status": status,
            "evidence": _evidence(evidence, required="outcome" if status == ObservationStatus.COMPLETE else None),
        }
        completion_hash = document_hash(completion)
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource="alpha")
            previous = await self._get(session, _key("outcome", claim["decision_id"]))
            _claim_matches(previous, claim)
            now = await self._now(session)
            if previous["status"] != ObservationStatus.CAPTURING:
                if previous.get("completion_hash") == completion_hash:
                    return previous
                if previous.get("reason") == "outcome_expired":
                    await self._late(session, "outcome", claim, completion, now)
                    return previous
                raise ValueError("Daily primary outcome is immutable")
            if not utc_timestamp(claim["claimed_at"]) <= now < utc_timestamp(claim["expires_at"]):
                result = {
                    **previous,
                    "status": ObservationStatus.UNAVAILABLE,
                    "reason": "outcome_expired",
                    "recorded_at": now.isoformat(),
                }
                await self._late(session, "outcome", claim, completion, now)
            else:
                result = {**previous, **completion, "completion_hash": completion_hash, "recorded_at": now.isoformat()}
            await self._write(session, "outcome", claim["decision_id"], result)
            return result

    def _records_query(self, campaign_id, kind, *, status=None, after=None):
        if kind not in ("decision", "outcome"):
            raise ValueError("Daily record kind must be decision or outcome")
        # PostgreSQL's text column requires an explicit JSON cast; SQLite's
        # JSON_EXTRACT reads the text directly (CAST AS JSON coerces it to 0).
        payload = (
            cast(AlphaProjectionRecord.payload, JSON)
            if self.store.db.engine.dialect.name == "postgresql"
            else type_coerce(AlphaProjectionRecord.payload, JSON)
        )
        query = select(AlphaProjectionRecord).where(
            AlphaProjectionRecord.scope == self.store.scope,
            AlphaProjectionRecord.key.startswith(DAILY_PREFIXES[kind]),
            payload["campaign_id"].as_string() == _campaign_id(campaign_id),
        )
        if status is not None:
            query = query.where(payload["status"].as_string() == str(status))
        if after is not None:
            query = query.where(payload["session_date"].as_string() > date.fromisoformat(after).isoformat())
        return query.order_by(payload["session_date"].as_string())

    async def records(self, campaign_id, *, kind="decision", status=None, limit=100, after=None):
        limit = _limit(limit)
        async with self.store.db.session_factory() as session:
            rows = list(
                await session.scalars(
                    self._records_query(campaign_id, kind, status=status, after=after).limit(limit + 1)
                )
            )
            documents = [json.loads(row.payload) for row in rows[:limit]]
        truncated = len(rows) > limit
        return {
            "records": documents,
            "truncated": truncated,
            "next_after": documents[-1]["session_date"] if truncated else None,
        }

    async def expire(self, campaign_id, *, limit=100):
        limit = _limit(limit)
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource="alpha")
            campaign = await self._get(session, _key("campaign", _campaign_id(campaign_id)))
            if campaign is None:
                raise ValueError("Unknown daily campaign")
            now = await self._now(session)
            expired = []
            if campaign["inflight_decision"] is not None:
                previous = await self._get(session, _key("decision", campaign["inflight_decision"]))
                if now >= utc_timestamp(previous["expires_at"]):
                    expired.append(await self._expire_decision(session, previous, campaign, now))
            remaining = limit - len(expired)
            if remaining:
                query = self._records_query(campaign_id, "outcome", status=ObservationStatus.CAPTURING).limit(remaining)
                rows = list(await session.scalars(query))
                for row in rows:
                    previous = json.loads(row.payload)
                    if now >= utc_timestamp(previous["expires_at"]):
                        result = {
                            **previous,
                            "status": ObservationStatus.UNAVAILABLE,
                            "reason": "outcome_expired",
                            "recorded_at": now.isoformat(),
                        }
                        await self._write(session, "outcome", previous["decision_id"], result)
                        expired.append(result)
            return expired

    async def report(self, *, since, now, limit=1000):
        since, now, limit = utc_timestamp(since), utc_timestamp(now), _limit(limit)
        if since > now:
            raise ValueError("Ordered daily report interval required")
        kinds = or_(
            AlphaProjectionRecord.key.startswith(DAILY_PREFIXES["campaign"]),
            and_(
                DomainEventRecord.recorded_at >= since.to_pydatetime(),
                or_(*(AlphaProjectionRecord.key.startswith(DAILY_PREFIXES[kind]) for kind in ("decision", "outcome"))),
            ),
        )
        async with self.store.db.session_factory() as session:
            rows = list(
                await session.execute(
                    select(AlphaProjectionRecord, DomainEventRecord.recorded_at)
                    .join(DomainEventRecord, DomainEventRecord.id == AlphaProjectionRecord.event_id)
                    .where(
                        AlphaProjectionRecord.scope == self.store.scope,
                        DomainEventRecord.recorded_at <= now.to_pydatetime(),
                        kinds,
                    )
                    .order_by(AlphaProjectionRecord.event_id.desc())
                    .limit(limit + 1)
                )
            )
            result = {
                kind + "s": [
                    {**json.loads(row.payload), "projection_recorded_at": recorded_at.isoformat()}
                    for row, recorded_at in rows[:limit]
                    if row.key.startswith(prefix)
                ]
                for kind, prefix in DAILY_PREFIXES.items()
            }
        return {
            **result,
            "truncated": len(rows) > limit,
            "since": since.isoformat(),
            "as_of": now.isoformat(),
            "authorizes_promotion": False,
        }
