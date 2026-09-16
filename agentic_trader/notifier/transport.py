"""Shared Telegram delivery policy and observation of the SDK's polling loop."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from http import HTTPStatus
from typing import Any

from telegram.error import NetworkError
from telegram.request import HTTPXRequest

from agentic_trader.config import TelegramConfig
from agentic_trader.constants import AuditEventType
from agentic_trader.telemetry.collector import MetricsCollector


notification_id: ContextVar[str | None] = ContextVar("notification_id", default=None)


telegram_update_id: ContextVar[int | None] = ContextVar("telegram_update_id", default=None)


class RetryingTelegramRequest(HTTPXRequest):
    """Retry Telegram API delivery, never the application handler producing it.

    A lost response can cause duplicate Telegram messages on retry. The durable
    outbox provides at-least-once delivery, not exactly-once Telegram delivery.
    getUpdates uses the separate ObservedPollingRequest and the SDK retry loop.
    """

    def __init__(
        self,
        settings: TelegramConfig,
        audit: Callable[[AuditEventType, dict[str, Any]], Awaitable[None]],
        metrics: MetricsCollector,
    ) -> None:
        super().__init__(
            read_timeout=settings.read_timeout_seconds,
            connect_timeout=settings.connect_timeout_seconds,
        )
        self._settings = settings
        self._audit = audit
        self._metrics = metrics

    async def do_request(self, url: str, *args: Any, **kwargs: Any) -> tuple[int, bytes]:
        operation = url.rsplit("/", 1)[-1]
        for attempt in range(1, self._settings.request_attempts + 1):
            failure: NetworkError | None = None
            response: tuple[int, bytes] | None = None
            delay = self._settings.request_retry_delay_seconds * attempt
            data: dict[str, Any] = {}
            try:
                response = await super().do_request(url, *args, **kwargs)
            except NetworkError as exc:
                failure = exc
                error_type = type(exc).__name__
            else:
                status, body = response
                try:
                    decoded = json.loads(body)
                    data = decoded if isinstance(decoded, dict) else {}
                except ValueError, UnicodeError:
                    pass  # Preserve the original response for the SDK's error parser.
                error_type = f"HTTP_{status}"
                if status == HTTPStatus.TOO_MANY_REQUESTS:
                    retry_after = data.get("parameters", {}).get("retry_after")
                    if isinstance(retry_after, (float, int)):
                        delay = max(delay, float(retry_after))
                elif status < HTTPStatus.INTERNAL_SERVER_ERROR:
                    result = data.get("result")
                    await self._audit(
                        AuditEventType.TELEGRAM_REQUEST,
                        {
                            "operation": operation,
                            "success": status == HTTPStatus.OK,
                            "attempt": attempt,
                            "status": status,
                            "message_id": result.get("message_id") if isinstance(result, dict) else None,
                        },
                    )
                    return response
            await self._audit(
                AuditEventType.TELEGRAM_REQUEST,
                {"operation": operation, "success": False, "attempt": attempt, "error_type": error_type},
            )
            if attempt == self._settings.request_attempts or delay > self._settings.max_retry_after_seconds:
                if failure is not None:
                    raise failure
                assert response is not None
                return response
            self._metrics.inc_counter("trader_telegram_request_retries_total", labels={"operation": operation})
            await asyncio.sleep(delay)
        raise RuntimeError("Telegram request attempts exhausted")


class ObservedPollingRequest(HTTPXRequest):
    def __init__(self, observer: Callable[[bool, str | None], Awaitable[None]], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._observer = observer

    async def do_request(self, *args: Any, **kwargs: Any) -> tuple[int, bytes]:
        try:
            result = await super().do_request(*args, **kwargs)
        except NetworkError as exc:
            await self._observer(False, type(exc).__name__)
            raise
        success = result[0] == HTTPStatus.OK
        await self._observer(success, None if success else f"HTTP_{result[0]}")
        return result
