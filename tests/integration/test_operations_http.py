"""Supervisor → passive HTTP → journal/outbox → real Telegram SDK HTTP delivery."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest
from telegram import Bot
from telegram.ext import Application

from agentic_trader.cli.commands import service
from agentic_trader.diagnostics.monitor import OperationsMonitor
from agentic_trader.diagnostics.probe import probe_readiness
from agentic_trader.execution.durable import WorkKind, WorkStatus
from agentic_trader.notifier.outbox import NotificationDispatcher
from agentic_trader.notifier.telegram_bot import TelegramNotifier
from agentic_trader.notifier.transport import RetryingTelegramRequest
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.operations import OperationsStore
from agentic_trader.telemetry.server import MetricsServer


pytestmark = [pytest.mark.enable_socket, pytest.mark.allow_hosts(["127.0.0.1", "localhost"])]


@pytest.fixture
async def telegram_http():
    state = {"fail": False, "messages": [], "methods": []}

    async def handle(reader, writer):
        try:
            method = (await reader.readline()).decode().split()[1].rsplit("/", 1)[-1]
            headers = {}
            while (line := await reader.readline()) not in (b"\r\n", b""):
                key, value = line.decode().split(":", 1)
                headers[key.lower()] = value.strip()
            body = await reader.readexactly(int(headers.get("content-length", "0")))
            state["methods"].append(method)
            status = "200 OK"
            result = {"id": 1, "is_bot": True, "first_name": "test", "username": "test_bot"}
            if method == "sendMessage":
                state["messages"].append(parse_qs(body.decode())["text"][0])
                result = {"message_id": 7, "date": 1, "chat": {"id": 1, "type": "private"}, "text": "ok"}
                if state["fail"]:
                    status = "403 Forbidden"
            payload = (
                {"ok": True, "result": result}
                if status == "200 OK"
                else {"ok": False, "error_code": 403, "description": "Forbidden: fixture failure"}
            )
            data = json.dumps(payload).encode()
            writer.write(
                f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode()
                + data
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield state, f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/bot"
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize("delivery_fails", [False, True])
async def test_supervisor_incident_delivery_and_replay(temp_db, app_config, telegram_http, delivery_fails):
    state, url = telegram_http
    state["fail"] = delivery_fails
    app_config.operations.failure_seconds = 0.001
    app_config.operations.startup_grace_seconds = 0
    app_config.execution.notification_max_attempts = 1

    async def report():
        now = datetime.now(UTC)
        return {
            "ready": False,
            "run_id": "integration",
            "checked_at": now.isoformat(),
            "started_at": (now - timedelta(hours=1)).isoformat(),
            "checks": {"outbox": {"ready": False, "dead_letters": 1, "pending": 0}},
        }

    server = MetricsServer(host="127.0.0.1", port=0, readiness=report)
    await server.start()
    app_config.telemetry.metrics_port = server.port
    store = OperationsStore(temp_db.workflows)
    monitor = OperationsMonitor(store, app_config.operations)
    try:
        async with httpx.AsyncClient() as client:
            assert (await client.get(f"http://127.0.0.1:{server.port}/healthcheck")).status_code == 404
            await monitor.observe(await probe_readiness(app_config, client))
            notices = await monitor.observe(await probe_readiness(app_config, client))
        assert len(notices) == 1
        notifier = TelegramNotifier(None, None, db=temp_db, environment=app_config.environment)
        notifier.bot_token, notifier.chat_id = "123:FAKE", "1"
        bot = Bot(
            notifier.bot_token,
            base_url=url,
            request=RetryingTelegramRequest(app_config.telegram, notifier._audit, notifier.metrics),
        )
        notifier.app = Application.builder().bot(bot).build()
        async with bot:
            assert await NotificationDispatcher(temp_db.workflows, notifier, app_config.execution).dispatch_one()
        (item,) = await temp_db.workflows.list_work(WorkKind.NOTIFICATION)
        assert item.status == (WorkStatus.DEAD if delivery_fails else WorkStatus.DELIVERED)
        assert state["methods"] == ["getMe", "sendMessage"]  # Never a second getUpdates poller.
        assert "[TEST]" in state["messages"][0] and "Operational alert" in state["messages"][0]
        before = await store.incidents()
        await store.rebuild()
        assert await store.incidents() == before
        assert len(await temp_db.workflows.list_work(WorkKind.NOTIFICATION)) == 1
    finally:
        await server.stop()


async def test_external_monitor_can_deliver_without_daemon_or_second_poller(app_config, telegram_http, monkeypatch):

    state, url = telegram_http
    app_config.operations.failure_seconds = 1
    app_config.telegram_bot_token, app_config.telegram_chat_id = "123:FAKE", "1"

    def notifier_factory(token, chat_id, **kwargs):
        notifier = TelegramNotifier(None, None, **kwargs)
        notifier.bot_token, notifier.chat_id = token, chat_id
        bot = Bot(
            token, base_url=url, request=RetryingTelegramRequest(app_config.telegram, notifier._audit, notifier.metrics)
        )
        notifier.app = Application.builder().bot(bot).build()
        return notifier

    monkeypatch.setattr(service, "TelegramNotifier", notifier_factory)
    now = datetime.now(UTC)
    for at in (now - timedelta(seconds=2), now):
        await service.monitor_once(
            app_config, {"ready": False, "checked_at": at.isoformat(), "checks": {"endpoint": {"ready": False}}}
        )
    db = SignalDatabase(config=app_config)
    try:
        notices = await db.workflows.list_work(WorkKind.NOTIFICATION)
        assert len(notices) == 1 and notices[0].status == WorkStatus.DELIVERED
        assert state["methods"] == ["getMe", "getMe", "sendMessage"]
        assert not await db.workflows.list_work(WorkKind.ENTRY)
    finally:
        await db.engine.dispose()
