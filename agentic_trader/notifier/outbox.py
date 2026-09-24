"""At-least-once notification delivery. No trade handler is ever replayed."""

from __future__ import annotations

import asyncio
import html
from typing import TYPE_CHECKING
from uuid import uuid4

from agentic_trader.agent.evaluator import LLMTradeEvaluation
from agentic_trader.constants import AuditEventType
from agentic_trader.diagnostics.incidents import OperationalNotice
from agentic_trader.execution.durable import NotificationKind
from agentic_trader.notifier.transport import notification_id
from agentic_trader.presentation.operations import format_operational_notice
from agentic_trader.storage.workflow import WorkflowStore


if TYPE_CHECKING:
    from agentic_trader.config import ExecutionConfig
    from agentic_trader.notifier.telegram_bot import TelegramNotifier


class NotificationDispatcher:
    def __init__(self, store: WorkflowStore, notifier: TelegramNotifier, policy: ExecutionConfig):
        self.store, self.notifier, self.policy = store, notifier, policy

    async def publish_message(self, text: str, *, key: str | None = None) -> None:
        await self.store.enqueue_notification(
            key or uuid4().hex, NotificationKind.MESSAGE, {"text": text, "formatted": True}
        )

    async def dispatch_one(self) -> bool:
        # An unconfigured local CLI cannot exhaust the live daemon's outbox.
        if not self.notifier.is_configured():
            return False
        item = await self.store.claim_notification(
            lease_seconds=self.policy.notification_lease_seconds, max_attempts=self.policy.notification_max_attempts
        )
        if not item:
            return False
        kind = item.payload["kind"]
        context_token = notification_id.set(item.id)
        try:
            async with asyncio.timeout(self.policy.notification_delivery_timeout_seconds):
                result = await self._deliver(NotificationKind(kind), item.payload["arguments"].copy())
            delivered, detail = bool(result), str(result) if result else "Delivery not acknowledged"
        except Exception as exc:
            delivered, detail = False, type(exc).__name__
        finally:
            notification_id.reset(context_token)
        delay = min(
            self.policy.notification_max_retry_seconds,
            self.policy.notification_retry_seconds * 2 ** min(item.attempts - 1, 30),
        )
        saved = await self.store.finish_notification(
            item,
            delivered=delivered,
            detail=detail,
            max_attempts=self.policy.notification_max_attempts,
            retry_seconds=delay,
        )
        if saved and kind == NotificationKind.EXIT:
            await self.store.db.record_audit(
                AuditEventType.EXIT_NOTIFICATION,
                {"outbox_id": item.id, "delivered": delivered, "attempt": item.attempts, "detail": detail},
            )
        return True

    async def _deliver(self, kind: NotificationKind, args: dict):
        if kind == NotificationKind.OPERATIONAL:
            return await self.notifier.send_message(format_operational_notice(OperationalNotice.model_validate(args)))
        if kind == NotificationKind.EXIT:
            return await self.notifier.send_exit_alert(**args)
        if kind == NotificationKind.STOP:
            return await self.notifier.send_trailing_stop_alert(**args)
        if kind == NotificationKind.SIGNAL:
            args["eval_res"] = LLMTradeEvaluation.model_validate(args["eval_res"])
            return await self.notifier.send_signal_alert(**args)
        if kind == NotificationKind.CARD_EXPIRED:
            return await self.notifier.strike_expired_card(**args)
        return await self.notifier.send_message(args["text"] if args.get("formatted") else html.escape(args["text"]))

    async def drain(self) -> int:
        count = 0
        for _ in range(self.policy.worker_batch_size):
            if not await self.dispatch_one():
                break
            count += 1
        return count
