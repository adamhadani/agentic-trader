"""The scan journal's one writer: a ``scan_candidates_ranked`` event and its notice intent in one transaction.

A scan that loses its regime read journals ``status: regime_unavailable`` and queues the New York
date's one ``regime-unavailable/{et_date}`` notice beside it, so the event and the notice commit
or roll back together. Errors here carry exception type names only, never provider text.

A completed scan's event carries its collect ``funnel`` (``ScanFunnel.journal_block``): what the
scan selected and scanned, and where each native setup went. Admission refusals are typed
(``AdmissionRefusal``), so the funnel counts them by rule id, never by parsing reason text.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from agentic_trader.agent.macro import DECISION_INPUTS, missing_input_names
from agentic_trader.agent.regime import RegimeUnavailable
from agentic_trader.execution.durable import EventKind, NotificationKind, ScanStatus, ScanTrigger
from agentic_trader.market.session import ET_TZ
from agentic_trader.storage.db import SignalDatabase


class RefusalKind(StrEnum):
    """Why the collect phase's admission refused a setup."""

    DUPLICATE = "duplicate"
    ACCOUNT_RISK_UNAVAILABLE = "account_risk_unavailable"
    REJECTED = "rejected"


@dataclass(frozen=True)
class AdmissionRefusal:
    """A setup the collect phase's admission refused.

    ``rule`` is the deterministic evaluation's ``rejection_rule`` (a ``RiskRule`` value), or None: a
    duplicate, unavailable account risk, or a rejection no shared rule made. ``reason`` is the
    operator text (``"duplicate"``, ``"account risk unavailable: …"`` or ``"rejected: …"``), which a
    PEAD decision journals as its outcome; the funnel never reads it.
    """

    kind: RefusalKind
    rule: str | None
    reason: str

    def __str__(self) -> str:
        return self.reason


@dataclass
class ScanFunnel:
    """A completed scan's collect funnel: contracts by gate, then native setups by where they went.

    Each ``selected`` contract is dynamic-excluded, fetch-failed, insufficient, coverage-excluded or
    ``scanned``, unless its guard fires first; ``errors`` counts the guard's exception type names, so a
    contract that raises after scanning counts in both. Every native setup iterated (``raw_setups``) is
    a duplicate, lacks account risk, is rejected (by rule, ``unclassified`` without one), is admitted
    (``admitted`` equals the ranked native count), or is lost when its contract's guard fires before it
    is classified (``setups_lost_to_errors``). PEAD drift candidates are not counted here.
    """

    selected: int = 0
    scanned: int = 0
    fetch_failed: int = 0
    insufficient: int = 0
    coverage_excluded: int = 0
    dynamic_excluded: int = 0
    raw_setups: int = 0
    duplicates: int = 0
    account_risk_unavailable: int = 0
    setups_lost_to_errors: int = 0
    admitted: int = 0
    raw_setups_by_strategy: Counter[str] = field(default_factory=Counter)
    rejected_by_rule: Counter[str] = field(default_factory=Counter)
    errors: Counter[str] = field(default_factory=Counter)

    def record_refusal(self, refusal: AdmissionRefusal) -> None:
        """Count one refused native setup under its kind; a rejection under its rule id."""
        match refusal.kind:
            case RefusalKind.DUPLICATE:
                self.duplicates += 1
            case RefusalKind.ACCOUNT_RISK_UNAVAILABLE:
                self.account_risk_unavailable += 1
            case RefusalKind.REJECTED:
                self.rejected_by_rule[refusal.rule or "unclassified"] += 1

    def journal_block(self) -> dict[str, Any]:
        """The journal's ``funnel``: integers and plain ``{name: n}`` dicts with sorted keys."""

        def counts(counter: Counter[str]) -> dict[str, int]:
            return {name: counter[name] for name in sorted(counter)}

        return {
            "selected": self.selected,
            "scanned": self.scanned,
            "fetch_failed": self.fetch_failed,
            "insufficient": self.insufficient,
            "coverage_excluded": self.coverage_excluded,
            "dynamic_excluded": self.dynamic_excluded,
            "raw_setups": self.raw_setups,
            "raw_setups_by_strategy": counts(self.raw_setups_by_strategy),
            "duplicates": self.duplicates,
            "account_risk_unavailable": self.account_risk_unavailable,
            "rejected_by_rule": counts(self.rejected_by_rule),
            "errors": counts(self.errors),
            "setups_lost_to_errors": self.setups_lost_to_errors,
            "admitted": self.admitted,
        }


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
