import asyncio
from http import HTTPStatus
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import Bot
from telegram.error import NetworkError
from telegram.request import HTTPXRequest

from agentic_trader.config import TelegramConfig
from agentic_trader.constants import AuditEventType
from agentic_trader.notifier.telegram_bot import TelegramNotifier
from agentic_trader.notifier.transport import ObservedPollingRequest, RetryingTelegramRequest, telegram_update_id
from agentic_trader.telemetry.collector import MetricsCollector


MESSAGE = (200, b'{"ok":true,"result":{"message_id":7,"date":1,"chat":{"id":1,"type":"private"},"text":"ok"}}')


@pytest.fixture
async def transport():
    request = RetryingTelegramRequest(TelegramConfig(), AsyncMock(), MetricsCollector())
    yield request
    await request.shutdown()


@pytest.mark.parametrize(
    "first",
    [NetworkError("read failed"), (503, b"unavailable"), (429, b'{"parameters":{"retry_after":2}}')],
    ids=["read-error", "server-error", "rate-limit"],
)
@pytest.mark.asyncio
async def test_shared_transport_retries_delivery_without_replaying_handler(transport, monkeypatch, first):
    send = AsyncMock(side_effect=[first, MESSAGE])
    pause = AsyncMock()
    monkeypatch.setattr(HTTPXRequest, "do_request", send)
    monkeypatch.setattr(asyncio, "sleep", pause)
    bot = Bot("123:FAKE", request=transport)

    async def reply():
        return await bot.send_message(1, "ok")

    handler = AsyncMock(side_effect=reply)
    # The handler is invoked once; only HTTP delivery is repeated.
    result = await handler()
    assert result.message_id == 7
    handler.assert_awaited_once()
    assert send.await_count == 2
    pause.assert_awaited_once()
    audit_payloads = [str(call.args[1]) for call in transport._audit.await_args_list]
    assert all("FAKE" not in payload and "chat" not in payload for payload in audit_payloads)


@pytest.mark.parametrize("response", [(400, b"bad"), (401, b"unauthorized"), (403, b"forbidden")])
@pytest.mark.asyncio
async def test_permanent_http_errors_are_not_retried(transport, monkeypatch, response):
    send = AsyncMock(return_value=response)
    monkeypatch.setattr(HTTPXRequest, "do_request", send)
    assert await transport.do_request("https://example.test/sendMessage", "POST") == response
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_network_retries_are_bounded(transport, monkeypatch):
    send = AsyncMock(side_effect=NetworkError("offline"))
    monkeypatch.setattr(HTTPXRequest, "do_request", send)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    with pytest.raises(NetworkError):
        await transport.do_request("https://example.test/sendMessage", "POST")
    assert send.await_count == 3


@pytest.mark.asyncio
async def test_poll_failure_then_recovery_updates_metrics_and_persistent_audit(monkeypatch, temp_db):
    metrics = MetricsCollector()
    notifier = TelegramNotifier(None, None, db=temp_db, metrics=metrics)
    request = ObservedPollingRequest(notifier._observe_poll)
    send = AsyncMock(side_effect=[NetworkError("offline"), (HTTPStatus.OK, b'{"ok":true,"result":[]}')])
    monkeypatch.setattr(HTTPXRequest, "do_request", send)
    try:
        with pytest.raises(NetworkError):
            await request.do_request("https://example.test/getUpdates", "POST")
        assert "trader_telegram_poll_healthy 0" in metrics.format_prometheus_exposition()
        await request.do_request("https://example.test/getUpdates", "POST")
        assert "trader_telegram_poll_healthy 1" in metrics.format_prometheus_exposition()
        events = await temp_db.get_audit_events()
        assert [(row["event_type"], row["payload"]["success"]) for row in events] == [
            (AuditEventType.TELEGRAM_POLL, True),
            (AuditEventType.TELEGRAM_POLL, False),
        ]
    finally:
        await request.shutdown()


@pytest.mark.asyncio
async def test_rate_limit_exceeding_budget_is_left_to_sdk(transport, monkeypatch):
    response = (429, b'{"parameters":{"retry_after":120}}')
    send = AsyncMock(return_value=response)
    pause = AsyncMock()
    monkeypatch.setattr(HTTPXRequest, "do_request", send)
    monkeypatch.setattr(asyncio, "sleep", pause)
    assert await transport.do_request("https://example.test/sendMessage", "POST") == response
    send.assert_awaited_once()
    pause.assert_not_awaited()


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.asyncio
async def test_handler_audit_correlates_delivery_and_resets_context(fails, temp_db):
    notifier = TelegramNotifier(None, "1", db=temp_db)
    update = SimpleNamespace(update_id=42, effective_chat=SimpleNamespace(id=1))

    async def callback(update, context):
        await notifier._audit(AuditEventType.TELEGRAM_REQUEST, {"operation": "sendMessage"})
        if fails:
            raise RuntimeError("handler failure")

    observed = notifier._observe_handler(callback)
    if fails:
        with pytest.raises(RuntimeError, match="handler failure"):
            await observed(update, None)
    else:
        await observed(update, None)
    rows = await temp_db.get_audit_events()
    assert all(row["payload"]["update_id"] == 42 for row in rows)
    assert rows[0]["payload"]["phase"] == ("failed" if fails else "completed")
    assert telegram_update_id.get() is None
