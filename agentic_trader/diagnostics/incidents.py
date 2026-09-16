"""Operational incident aggregate: debounce, recovery hysteresis and reminders."""

from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel

from agentic_trader.config import OperationsConfig


class IncidentPhase(StrEnum):
    HEALTHY = "healthy"
    PENDING = "pending"
    OPEN = "open"
    RECOVERING = "recovering"


class NoticeKind(StrEnum):
    OPENED = "opened"
    RECOVERED = "recovered"
    REMINDER = "reminder"


class IncidentState(BaseModel):
    phase: IncidentPhase = IncidentPhase.HEALTHY
    incident_id: str | None = None
    failed_since: datetime | None = None
    recovering_since: datetime | None = None
    last_notified_at: datetime | None = None
    notification_id: str | None = None


class OperationalNotice(BaseModel):
    component: str
    incident_id: str
    kind: NoticeKind
    observed_at: datetime
    failed_since: datetime
    detail: str


def advance(
    state: IncidentState, *, ready: bool, observed_at: datetime, policy: OperationsConfig, delivery_complete: bool
) -> tuple[IncidentState, NoticeKind | None]:
    state = state.model_copy(deep=True)
    notice = None
    if ready:
        if state.phase == IncidentPhase.PENDING:
            return IncidentState(), None
        if state.phase == IncidentPhase.OPEN:
            state.phase, state.recovering_since = IncidentPhase.RECOVERING, observed_at
        elif (
            state.phase == IncidentPhase.RECOVERING
            and state.recovering_since is not None
            and (observed_at - state.recovering_since).total_seconds() >= policy.recovery_seconds
        ):
            state.phase = IncidentPhase.HEALTHY
            notice = NoticeKind.RECOVERED
    elif state.phase == IncidentPhase.HEALTHY:
        state = IncidentState(phase=IncidentPhase.PENDING, incident_id=uuid4().hex, failed_since=observed_at)
    elif state.phase == IncidentPhase.RECOVERING:
        state.phase, state.recovering_since = IncidentPhase.OPEN, None
    elif (
        state.phase == IncidentPhase.PENDING
        and state.failed_since is not None
        and (observed_at - state.failed_since).total_seconds() >= policy.failure_seconds
    ):
        state.phase, notice = IncidentPhase.OPEN, NoticeKind.OPENED
    elif state.phase == IncidentPhase.OPEN and state.last_notified_at is None:
        notice = NoticeKind.OPENED
    elif (
        state.phase == IncidentPhase.OPEN
        and delivery_complete
        and state.last_notified_at is not None
        and (observed_at - state.last_notified_at).total_seconds() >= policy.reminder_seconds
    ):
        notice = NoticeKind.REMINDER
    if not policy.notifications_enabled:
        notice = None
    if notice:
        state.last_notified_at = observed_at
    return state, notice
