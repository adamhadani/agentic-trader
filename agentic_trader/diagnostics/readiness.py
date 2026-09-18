"""Passive readiness of this daemon run, separate from active doctor probes."""

import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from agentic_trader.config import AppConfig
from agentic_trader.constants import CloseRequestStatus, SystemStateKey
from agentic_trader.execution.durable import WorkKind, WorkStatus
from agentic_trader.runtime import RUN_ID
from agentic_trader.storage.workflow import WorkflowStore
from agentic_trader.telemetry.collector import MetricsCollector


class HealthComponent(StrEnum):
    RECONCILIATION = "reconciliation"
    WORKER = "worker"
    SCAN = "scan"
    TELEGRAM = "telegram"
    DELIVERY = "delivery"
    ACCOUNTING = "accounting"
    ALPHA_OBSERVER = "alpha_observer"
    ALPHA_DECISIONS = "alpha_decisions"
    ALPHA_DAILY_PANEL = "alpha_daily_panel"


class ReadinessService:
    def __init__(
        self,
        store: WorkflowStore,
        config: AppConfig,
        metrics: MetricsCollector,
        *,
        run_id: str = RUN_ID,
        stream_connected: Callable[[], bool] | None = None,
        accounting_enabled: bool = False,
        alpha_registry_report: Callable[[], Awaitable[dict[str, Any]]] | None = None,
    ):
        self.store, self.config, self.metrics, self.run_id = store, config, metrics, run_id
        self.started = False
        self.started_at = datetime.now(UTC)
        self.accounting_enabled = accounting_enabled
        self.alpha_registry_report = alpha_registry_report
        self.stream_connected = stream_connected
        self._last_observation: dict[str, tuple[float, bool, str]] = {}

    async def observe(self, component: HealthComponent, success: bool, detail: str = "") -> None:
        now = time.monotonic()
        previous = self._last_observation.get(component)
        if (
            previous
            and previous[1:] == (success, detail)
            and now - previous[0] < self.config.telemetry.health_observation_interval_seconds
        ):
            return
        await self.store.record_health(component, success, detail, run_id=self.run_id)
        self._last_observation[component] = (now, success, detail)
        self.metrics.set_gauge("trader_component_healthy", float(success), labels={"component": component})
        if success:
            self.metrics.set_gauge(
                "trader_component_last_success_timestamp_seconds",
                datetime.now(UTC).timestamp(),
                labels={"component": component},
            )

    async def report(self, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        limits = {
            HealthComponent.RECONCILIATION: self.config.telemetry.reconciliation_max_age_seconds,
            HealthComponent.WORKER: self.config.telemetry.worker_max_age_seconds,
            HealthComponent.SCAN: self.config.telemetry.scan_max_age_seconds,
        }
        if self.config.telegram_bot_token and self.config.telegram_chat_id:
            limits[HealthComponent.TELEGRAM] = self.config.telemetry.telegram_max_age_seconds
            limits[HealthComponent.DELIVERY] = self.config.telemetry.worker_max_age_seconds
        if self.config.alpha_pipeline.observations.enabled:
            limits[HealthComponent.ALPHA_OBSERVER] = self.config.alpha_pipeline.observations.max_age_seconds
        if self.config.alpha_pipeline.decisions.enabled:
            limits[HealthComponent.ALPHA_DECISIONS] = self.config.alpha_pipeline.decisions.max_age_seconds
        if self.config.alpha_pipeline.daily_panel.enabled:
            limits[HealthComponent.ALPHA_DAILY_PANEL] = self.config.alpha_pipeline.daily_panel.max_age_seconds
        if self.accounting_enabled:
            limits[HealthComponent.ACCOUNTING] = self.config.accounting.max_age_seconds
        checks: dict[str, Any] = {"daemon_started": {"ready": self.started}}
        try:
            if self.alpha_registry_report is not None:
                checks["alpha_registry"] = await self.alpha_registry_report()
            for component, age_limit in limits.items():
                rows = await self.store.events(f"health/{self.run_id}/{component}", limit=1)
                observation = rows[0] if rows else None
                age = (
                    (now - datetime.fromisoformat(observation["recorded_at"])).total_seconds() if observation else None
                )
                success = bool(observation and observation["payload"]["success"])
                checks[component] = {
                    "ready": bool(success and age is not None and 0 <= age <= age_limit),
                    "age_seconds": age,
                    "max_age_seconds": age_limit,
                    "detail": observation["payload"].get("detail", "") if observation else "No observation in this run",
                }
            entries = await self.store.work_summary(WorkKind.ENTRY)
            notifications = await self.store.work_summary(WorkKind.NOTIFICATION)
            pending = sum(
                entries.get(status, {}).get("count", 0) for status in (WorkStatus.SUBMITTING, WorkStatus.UNKNOWN)
            )
            dead = notifications.get(WorkStatus.DEAD, {}).get("count", 0)
            backlog = sum(
                notifications.get(status, {}).get("count", 0) for status in (WorkStatus.QUEUED, WorkStatus.CHECKING)
            )
            oldest = min(
                (
                    notifications[status]["oldest"]
                    for status in (WorkStatus.QUEUED, WorkStatus.CHECKING)
                    if status in notifications
                ),
                default=None,
            )
            age = (now - oldest).total_seconds() if oldest else 0
            legacy = await self.store.legacy_claims()
            checks["entry_recovery"] = {
                "ready": not pending and not legacy,
                "unresolved": pending,
                "unmapped_legacy_signal_ids": legacy,
            }
            cancellations = await self.store.work_summary(WorkKind.ENTRY_CANCEL)
            pending_cancels = sum(
                cancellations.get(status, {}).get("count", 0) for status in (WorkStatus.SUBMITTING, WorkStatus.UNKNOWN)
            )
            checks["entry_cancellation"] = {"ready": not pending_cancels, "unresolved": pending_cancels}
            self.metrics.set_gauge("trader_entry_cancellations_unresolved", pending_cancels)
            checks["outbox"] = {
                "ready": not dead and age <= self.config.telemetry.notification_max_age_seconds,
                "dead_letters": dead,
                "pending": backlog,
                "oldest_age_seconds": age,
            }
            closes = await self.store.db.active_close_requests()
            unresolved_closes = [
                r for r in closes if r["status"] in (CloseRequestStatus.CLAIMED, CloseRequestStatus.UNKNOWN)
            ]
            checks["close_recovery"] = {
                "ready": not unresolved_closes,
                "unresolved": len(unresolved_closes),
                "active": len(closes),
            }
            checks["trading_halt"] = {
                "ready": await self.store.db.get_state(SystemStateKey.TRADING_HALTED) not in ("true", "1", "yes")
            }
            if self.stream_connected is not None:
                checks["broker_stream"] = {
                    "ready": self.stream_connected(),
                    "detail": "SDK authenticated WebSocket connection; absence of fills alone is not staleness",
                }
            self.metrics.set_gauge("trader_outbox_dead_letters", dead)
            self.metrics.set_gauge("trader_outbox_pending", backlog)
            self.metrics.set_gauge("trader_entries_unresolved", pending)
        except Exception as exc:
            checks["storage"] = {"ready": False, "detail": type(exc).__name__}
        lag = self.metrics.get_gauge("trader_event_loop_lag_seconds")
        checks["event_loop"] = {
            "ready": lag is not None and lag < self.config.telemetry.event_loop_warning_seconds,
            "lag_seconds": lag,
        }
        ready = self.started and all(check["ready"] for check in checks.values())
        self.metrics.set_gauge("trader_ready", float(ready))
        return {
            "ready": ready,
            "run_id": self.run_id,
            "checked_at": now.isoformat(),
            "started_at": self.started_at.isoformat(),
            "checks": checks,
            "note": "Freshness is operational readiness; session/price/risk are revalidated for every entry.",
        }
