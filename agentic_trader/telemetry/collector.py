from __future__ import annotations

from threading import Lock
from typing import Any

from agentic_trader.telemetry.models import DEFAULT_LATENCY_BUCKETS, MetricType


def _format_labels(labels: dict[str, str] | None) -> str:
    if not labels:
        return ""
    # Sort keys for deterministic output
    items = [f'{k}="{v}"' for k, v in sorted(labels.items())]
    return "{" + ",".join(items) + "}"


class MetricsCollector:
    """Thread-safe Prometheus metrics collector formatting metrics according to

    the Prometheus exposition format 0.0.4.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        # name -> help_text
        self._help: dict[str, str] = {}
        # name -> MetricType
        self._types: dict[str, MetricType] = {}
        # (name, tuple_sorted_labels) -> float
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        # (name, tuple_sorted_labels) -> float
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        # (name, tuple_sorted_labels) -> dict with 'buckets', 'counts', 'sum', 'count'
        self._histograms: dict[tuple[str, tuple[tuple[str, str], ...]], dict[str, Any]] = {}

    def reset(self) -> None:
        """Clears all stored metrics (useful for isolated unit tests)."""
        with self._lock:
            self._help.clear()
            self._types.clear()
            self._gauges.clear()
            self._counters.clear()
            self._histograms.clear()

    def set_gauge(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
        help_text: str = "",
    ) -> None:
        """Sets a gauge metric to an instantaneous value."""
        lbl_tuple = tuple(sorted((labels or {}).items()))
        with self._lock:
            self._types[name] = MetricType.GAUGE
            if help_text and name not in self._help:
                self._help[name] = help_text
            self._gauges[(name, lbl_tuple)] = float(value)

    def get_gauge(self, name: str, labels: dict[str, str] | None = None) -> float | None:
        with self._lock:
            return self._gauges.get((name, tuple(sorted((labels or {}).items()))))

    def inc_counter(
        self,
        name: str,
        value: float = 1.0,
        labels: dict[str, str] | None = None,
        help_text: str = "",
    ) -> None:
        """Increments a monotonically increasing counter metric."""
        if value < 0:
            raise ValueError(f"Counter increment must be non-negative, got {value}")
        lbl_tuple = tuple(sorted((labels or {}).items()))
        with self._lock:
            self._types[name] = MetricType.COUNTER
            if help_text and name not in self._help:
                self._help[name] = help_text
            key = (name, lbl_tuple)
            self._counters[key] = self._counters.get(key, 0.0) + float(value)

    def observe_histogram(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
        buckets: list[float] | None = None,
        help_text: str = "",
    ) -> None:
        """Records an observation into a histogram distribution."""
        lbl_tuple = tuple(sorted((labels or {}).items()))
        b_list = sorted(buckets or DEFAULT_LATENCY_BUCKETS)
        with self._lock:
            self._types[name] = MetricType.HISTOGRAM
            if help_text and name not in self._help:
                self._help[name] = help_text

            key = (name, lbl_tuple)
            if key not in self._histograms:
                self._histograms[key] = {
                    "buckets": b_list,
                    "counts": [0] * len(b_list),
                    "sum": 0.0,
                    "count": 0,
                }

            h = self._histograms[key]
            h["count"] += 1
            h["sum"] += float(value)
            for idx, bound in enumerate(h["buckets"]):
                if value <= bound:
                    h["counts"][idx] += 1

    def format_prometheus_exposition(self) -> str:
        """Renders all registered metrics into Prometheus 0.0.4 plain-text format."""
        with self._lock:
            lines: list[str] = []
            all_metric_names = sorted(self._types.keys())

            for name in all_metric_names:
                m_type = self._types[name]
                help_str = self._help.get(name, f"{name} metric")
                lines.append(f"# HELP {name} {help_str}")
                lines.append(f"# TYPE {name} {m_type.value}")

                if m_type == MetricType.GAUGE:
                    for (m_name, lbl_tuple), val in sorted(self._gauges.items()):
                        if m_name == name:
                            lbl_dict = dict(lbl_tuple)
                            lines.append(f"{name}{_format_labels(lbl_dict)} {val:.17g}")

                elif m_type == MetricType.COUNTER:
                    for (m_name, lbl_tuple), val in sorted(self._counters.items()):
                        if m_name == name:
                            lbl_dict = dict(lbl_tuple)
                            lines.append(f"{name}{_format_labels(lbl_dict)} {val:.17g}")

                elif m_type == MetricType.HISTOGRAM:
                    for (m_name, lbl_tuple), h in sorted(self._histograms.items()):
                        if m_name == name:
                            base_labels = dict(lbl_tuple)
                            for bound, count in zip(h["buckets"], h["counts"], strict=False):
                                b_lbls = {**base_labels, "le": f"{bound:g}"}
                                lines.append(f"{name}_bucket{_format_labels(b_lbls)} {count}")

                            # +Inf bucket
                            inf_lbls = {**base_labels, "le": "+Inf"}
                            lines.append(f"{name}_bucket{_format_labels(inf_lbls)} {h['count']}")

                            # _sum and _count
                            lines.append(f"{name}_sum{_format_labels(base_labels)} {h['sum']:.17g}")
                            lines.append(f"{name}_count{_format_labels(base_labels)} {h['count']}")

            return "\n".join(lines) + ("\n" if lines else "")

    def format_prometheus(self) -> str:
        """Alias for format_prometheus_exposition."""
        return self.format_prometheus_exposition()


# Global singleton instance for operational telemetry
global_metrics = MetricsCollector()
