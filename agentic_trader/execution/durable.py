"""Execution workflow vocabulary and immutable persistence boundaries."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class WorkKind(StrEnum):
    ENTRY = "entry"
    ENTRY_CANCEL = "entry_cancel"
    NOTIFICATION = "notification"


class WorkStatus(StrEnum):
    QUEUED = "queued"
    CHECKING = "checking"
    SUBMITTING = "submitting"
    UNKNOWN = "unknown"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DELIVERED = "delivered"
    DEAD = "dead"


ENTRY_BLOCKING = (WorkStatus.QUEUED, WorkStatus.CHECKING, WorkStatus.SUBMITTING, WorkStatus.UNKNOWN)
LEDGER_LOCK = "ledger"


class EventKind(StrEnum):
    ENTRY_CANCEL_REQUESTED = "entry_cancel_requested"
    ENTRY_CANCEL_RESOLVED = "entry_cancel_resolved"
    LIFETIME_REVIEW = "lifetime_review"
    CLOSE_REQUESTED = "close_requested"
    CLOSE_RESOLVED = "close_resolved"
    ENTRY_QUEUED = "entry_queued"
    ENTRY_CHECKING = "entry_checking"
    ENTRY_SUBMITTING = "entry_submitting"
    ENTRY_RESOLVED = "entry_resolved"
    ORDER_OBSERVED = "order_observed"
    POSITION_CLOSED = "position_closed"
    NOTIFICATION_QUEUED = "notification_queued"
    NOTIFICATION_ATTEMPT = "notification_attempt"
    NOTIFICATION_RESULT = "notification_result"
    NOTIFICATION_REQUEUED = "notification_requeued"
    HEALTH_OBSERVED = "health_observed"
    ACCOUNT_BOUND = "account_bound"
    ACTIVITY_OBSERVED = "activity_observed"
    ACTIVITY_RETRACTED = "activity_retracted"
    LEDGER_CHECKPOINT = "ledger_checkpoint"
    INCIDENT_CHANGED = "incident_changed"
    HEALTH_COMPACTED = "health_compacted"
    ALPHA_RESEARCH = "alpha_research"
    ALPHA_REGISTRY = "alpha_registry"
    ALPHA_FORECAST = "alpha_forecast"
    SCAN_CANDIDATES_RANKED = "scan_candidates_ranked"
    CARD_TAP_ASSESSED = "card_tap_assessed"
    CARD_REEVALUATE_REQUESTED = "card_reevaluate_requested"


class NotificationKind(StrEnum):
    MESSAGE = "message"
    OPERATIONAL = "operational"
    EXIT = "exit"
    SIGNAL = "signal"
    STOP = "stop"


@dataclass(frozen=True)
class WorkItem:
    id: str
    kind: WorkKind
    status: WorkStatus
    payload: dict[str, Any]
    result: dict[str, Any]
    token: str | None
    attempts: int
    created_at: datetime


class OrderObservation(BaseModel):
    """Broker-reported cumulative state, not a synthetic individual execution.

    Decimal values are preserved as wire strings. REST snapshots can recover
    cumulative fills after a missed stream event without inventing execution IDs.
    """

    order_id: str
    client_order_id: str
    symbol: str
    side: str
    status: str
    quantity: str
    filled_quantity: str
    average_fill_price: str | None = None
    limit_price: str | None = None
    stop_price: str | None = None
    order_type: str
    order_class: str
    updated_at: datetime
    submitted_at: datetime | None = None
    filled_at: datetime | None = None
    replaces: str | None = None
    replaced_by: str | None = None
    parent_order_id: str | None = None
    source: str = "rest"
    schema_version: Literal[1] = Field(default=1, frozen=True)
