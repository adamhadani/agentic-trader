"""The scan journal's one writer: a ``scan_candidates_ranked`` event and its notice intent in one transaction.

A scan that loses its regime read journals ``status: regime_unavailable`` and queues the New York
date's one ``regime-unavailable/{et_date}`` notice beside it, so the event and the notice commit
or roll back together. Errors here carry exception type names only, never provider text.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from agentic_trader.agent.macro import DECISION_INPUTS, missing_input_names
from agentic_trader.agent.regime import RegimeUnavailable
from agentic_trader.execution.durable import EventKind, NotificationKind, ScanStatus, ScanTrigger
from agentic_trader.market.session import ET_TZ
from agentic_trader.storage.db import SignalDatabase


@dataclass(frozen=True)
class ScanNotice:
    """One outbox message intent: ``key`` is its dedup key, ``text`` plain (unformatted) text."""

    key: str
    text: str


async def append_scan_journal(
    db: SignalDatabase,
    *,
    et_date: str,
    scan_id: str,
    payload: Mapping[str, Any] | None,
    notice: ScanNotice | None,
) -> None:
    """Append the scan's event (stream ``scan/{et_date}``) and add its notice in one locked transaction.

    Either part may be absent. Raises on failure, leaving neither row; the caller records the type name.
    The notice is idempotent by its key, so a second scan the same New York date adds none.
    """
    workflows = db.workflows
    async with db.session_factory() as session, session.begin():
        await workflows.lock(session)
        if payload is not None:
            await workflows.append(
                session,
                stream=f"scan/{et_date}",
                kind=EventKind.SCAN_CANDIDATES_RANKED,
                payload=dict(payload),
                key=f"scan_candidates_ranked/{scan_id}",
            )
        if notice is not None:
            await workflows.add_notification(
                session, notice.key, NotificationKind.MESSAGE, {"text": notice.text, "formatted": False}
            )


def regime_unavailable_gate(exc: BaseException) -> dict[str, Any]:
    """The journal ``gate`` of a scan whose regime read raised.

    A ``RegimeUnavailable`` names its missing decision inputs and their error types; context inputs
    never gate, so they appear only in the regime block. Any other exception is ``regime``: its type.
    """
    if isinstance(exc, RegimeUnavailable):
        missing = list(missing_input_names(exc.inputs, decision_only=True))
        errors = {name: exc.inputs[name].error or "no_observation" for name in missing}
    else:
        missing, errors = [], {"regime": type(exc).__name__}
    return {"rule": str(ScanStatus.REGIME_UNAVAILABLE), "missing": missing, "errors": errors}


def regime_errors_text(errors: Mapping[str, str]) -> str:
    """``"vix: ReadTimeout"``: the ``name: Type`` pairs in ``DECISION_INPUTS`` order, other names after."""
    rank = {name: index for index, name in enumerate(DECISION_INPUTS)}
    ordered = sorted(errors.items(), key=lambda item: rank.get(item[0], len(DECISION_INPUTS)))
    return ", ".join(f"{name}: {error}" for name, error in ordered)


def regime_unavailable_notice(
    errors: Mapping[str, str], *, at: datetime, trigger: ScanTrigger, pead_skipped: bool
) -> ScanNotice:
    """The New York date's one notice that a scan lost its regime read at ``at``."""
    local = at.astimezone(ET_TZ)
    pead = ", no PEAD decision" if pead_skipped else ""
    return ScanNotice(
        f"regime-unavailable/{local.date().isoformat()}",
        f"⚠️ Market regime unavailable ({regime_errors_text(errors)}) at {local:%H:%M} NY: the "
        f"{trigger.removesuffix('_scan')} scan was skipped — no cards{pead}. Later scans retry; this notice is "
        "sent once per New York date.",
    )
