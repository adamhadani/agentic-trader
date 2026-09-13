from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field

from agentic_trader.broker.base import OrderRequest


class SliceStatus(str, Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FAILED = "FAILED"
    SKIPPED_COLLAR = "SKIPPED_COLLAR"


class ExecutionPlanStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FAILED = "FAILED"
    ABORTED_COLLAR = "ABORTED_COLLAR"


class ChildSlice(BaseModel):
    slice_id: str
    slice_index: int
    quantity: float
    target_price: float
    limit_price: float
    status: SliceStatus = SliceStatus.PENDING
    broker_order_id: str | None = None
    fill_price: float | None = None
    fill_quantity: float = 0.0
    fill_timestamp: datetime | None = None
    error_message: str | None = None


class ExecutionPlan(BaseModel):
    plan_id: str
    request: OrderRequest
    algorithm: str  # "immediate", "twap", "vwap"
    total_quantity: float
    filled_quantity: float = 0.0
    weighted_avg_fill_price: float | None = None
    status: ExecutionPlanStatus = ExecutionPlanStatus.PENDING
    slices: list[ChildSlice] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    bracket_orders: dict[str, str] = Field(default_factory=dict)
