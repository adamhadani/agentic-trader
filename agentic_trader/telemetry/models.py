from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class MetricType(str, Enum):
    GAUGE = "gauge"
    COUNTER = "counter"
    HISTOGRAM = "histogram"


class MetricSample(BaseModel):
    name: str
    metric_type: MetricType
    value: float
    labels: dict[str, str] = Field(default_factory=dict)
    help_text: str = ""


# Default histogram latency buckets (in seconds)
DEFAULT_LATENCY_BUCKETS: list[float] = [0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
