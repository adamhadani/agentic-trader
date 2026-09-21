"""In-transaction probe liveness shared by the registry, the sweep and entry admission.

Admission (``WorkflowStore``) cannot import ``AlphaRepository`` without a cycle, so
the single source of truth for "may this probe take new risk" lives here.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentic_trader.constants import SignalStatus
from agentic_trader.research.alpha.probe import ProbePolicy, forward_record, is_paper_scope, policy_document
from agentic_trader.storage.models import AlphaProjectionRecord, SignalRecord


CLOSED_STATUSES = (SignalStatus.CLOSED_WIN, SignalStatus.CLOSED_LOSS, SignalStatus.CLOSED_MANUAL)


async def load_enrolment(session: AsyncSession, scope: str, version_id: str) -> dict | None:
    row = await session.get(AlphaProjectionRecord, (scope, f"probe/{version_id}"))
    return json.loads(row.payload) if row else None


async def load_forward_record(session: AsyncSession, db, version_id: str, since: datetime, policy: ProbePolicy) -> dict:
    rows = await session.execute(
        select(SignalRecord.realized_pnl, SignalRecord.risk_dollars).where(
            SignalRecord.alpha_version == version_id,
            SignalRecord.environment == db.environment,
            SignalRecord.execution_mode == db.execution_mode,
            SignalRecord.status.in_(CLOSED_STATUSES),
            SignalRecord.exit_timestamp >= since,
        )
    )
    return forward_record([(pnl, risk) for pnl, risk in rows], policy)


async def probe_block_reason(
    session: AsyncSession, *, scope: str, db, version_id: str, now: datetime, policy: ProbePolicy | None = None
) -> str | None:
    """Return why this probe may not take new risk, or ``None`` when it is live."""
    policy = policy or ProbePolicy()
    if not is_paper_scope(scope):
        return "paper probes run only on the Alpaca paper account"
    enrolment = await load_enrolment(session, scope, version_id)
    if enrolment is None:
        return "probe enrolment evidence is missing"
    if enrolment.get("policy") != policy_document(policy):
        return "probe policy changed; renew the probe under the current policy"
    if datetime.fromisoformat(enrolment["expires_at"]) <= now:
        return "probe term expired; renew it or let it retire"
    record = await load_forward_record(
        session, db, version_id, datetime.fromisoformat(enrolment["first_enrolled_at"]), policy
    )
    if record["killed"]:
        return f"probe kill rule reached ({record['cumulative_r']:.2f}R <= {policy.kill_r:.2f}R)"
    return None
