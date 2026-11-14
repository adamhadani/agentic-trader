"""Operational incident aggregate: debounce, recovery hysteresis and reminders."""

from datetime import date, datetime
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


class WithheldReason(StrEnum):
    QUIET_HOURS = "quiet_hours"
    NOTIFICATIONS_DISABLED = "notifications_disabled"
    NOT_ANNOUNCED = "not_announced"


class WithheldNotice(BaseModel):
    kind: NoticeKind
    reason: WithheldReason


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


class ComponentTally(BaseModel):
    component: str
    opened: int
    recovered: int


class OpenIncident(BaseModel):
    component: str
    phase: IncidentPhase
    failed_since: datetime
    detail: str
    ready_now: bool


class GapPosition(BaseModel):
    symbol: str
    signal_id: str


class GapClose(GapPosition):
    outcome: str


class ReleaseExitGaps(BaseModel):
    """Silent release-exit gaps of one trading day, reported by the next morning's summary."""

    trading_day: date
    missing_pass: bool
    unknown: list[GapPosition]
    unnotified_closes: list[GapClose]
    unattempted_closes: list[GapPosition] = []

    @property
    def has_gaps(self) -> bool:
        return self.missing_pass or bool(self.unknown) or bool(self.unnotified_closes) or bool(self.unattempted_closes)


class QuietHoursSummary(BaseModel):
    period_start: datetime
    period_end: datetime
    observed_at: datetime
    failure_seconds: float
    tallies: list[ComponentTally]
    open_incidents: list[OpenIncident]
    release_exit: ReleaseExitGaps | None = None


def _opening_due(
    failed_since: datetime, observed_at: datetime, policy: OperationsConfig, alert_since: datetime | None
) -> bool:
    """An opening is due once the incident has failed for ``failure_seconds`` of alert-clock time."""
    since = max(failed_since, alert_since) if alert_since else failed_since
    return (observed_at - since).total_seconds() >= policy.failure_seconds


def advance(
    state: IncidentState,
    *,
    ready: bool,
    observed_at: datetime,
    policy: OperationsConfig,
    delivery_complete: bool,
    muted: bool = False,
    alert_since: datetime | None = None,
) -> tuple[IncidentState, NoticeKind | None, WithheldNotice | None]:
    state = state.model_copy(deep=True)
    notice = None
    if ready:
        if state.phase == IncidentPhase.PENDING:
            return IncidentState(), None, None
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
        # The phase opens on the debounce; the notice waits for the alert clock (announced later when not due yet).
        state.phase = IncidentPhase.OPEN
        if _opening_due(state.failed_since, observed_at, policy, alert_since):
            notice = NoticeKind.OPENED
    elif state.phase == IncidentPhase.OPEN and state.last_notified_at is None:
        assert state.failed_since is not None
        if _opening_due(state.failed_since, observed_at, policy, alert_since):
            notice = NoticeKind.OPENED
    elif (
        state.phase == IncidentPhase.OPEN
        and delivery_complete
        and state.last_notified_at is not None
        and (observed_at - state.last_notified_at).total_seconds() >= policy.reminder_seconds
    ):
        notice = NoticeKind.REMINDER
    withheld = None
    if notice:
        reason = (
            WithheldReason.QUIET_HOURS
            if muted
            else WithheldReason.NOTIFICATIONS_DISABLED
            if not policy.notifications_enabled
            else WithheldReason.NOT_ANNOUNCED
            if notice == NoticeKind.RECOVERED and state.last_notified_at is None
            else None
        )
        if reason:
            withheld, notice = WithheldNotice(kind=notice, reason=reason), None
    if notice:
        state.last_notified_at = observed_at
    return state, notice, withheld
