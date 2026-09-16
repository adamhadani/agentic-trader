import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_trader.diagnostics.readiness import HealthComponent, ReadinessService
from agentic_trader.execution.durable import OrderObservation
from agentic_trader.telemetry.collector import MetricsCollector
from agentic_trader.telemetry.server import MetricsServer


@pytest.fixture
def observation():
    return OrderObservation(
        order_id="broker-entry",
        client_order_id="persisted-client-id",
        symbol="SPY",
        side="buy",
        status="partially_filled",
        quantity="10",
        filled_quantity="3",
        average_fill_price="100.01",
        order_type="limit",
        order_class="bracket",
        updated_at=datetime.now(UTC),
    )


@pytest.mark.parametrize("sequence", [(0, 1, 1, 0), (1, 0), (0, 0, 1)])
async def test_order_projection_replays_partial_fills_and_out_of_order_events(store, observation, sequence):
    full = observation.model_copy(
        update={
            "status": "filled",
            "filled_quantity": "10",
            "average_fill_price": "101.25",
            "updated_at": observation.updated_at + timedelta(seconds=1),
        }
    )
    for index in sequence:
        await store.observe_orders([[observation, full][index]])
    expected = await store.order_views()
    assert expected == [full]
    assert len(await store.events("order/broker-entry")) == 2
    await store.rebuild_order_views()
    assert await store.order_views() == expected


async def test_replacement_identity_and_broker_only_orders_survive_replay(store, observation):
    old = observation.model_copy(update={"status": "replaced", "replaced_by": "new-id"})
    new = observation.model_copy(
        update={"order_id": "new-id", "replaces": old.order_id, "parent_order_id": "outside-system-parent"}
    )
    await store.observe_orders([old, new])
    before = await store.order_views()
    await store.rebuild_order_views()
    assert await store.order_views() == before
    assert len(before) == 2
    assert not await store.db.get_active_positions()  # evidence cannot invent a tracked trade


@pytest.mark.parametrize("mode", ["fresh", "stale", "failure", "previous-run", "no-start"])
async def test_readiness_requires_current_run_successful_fresh_observations(store, app_config, mode):
    collector = MetricsCollector()
    collector.set_gauge("trader_event_loop_lag_seconds", 0)
    readiness = ReadinessService(store, app_config, collector, run_id="current")
    readiness.started = mode != "no-start"
    for component in (HealthComponent.RECONCILIATION, HealthComponent.WORKER, HealthComponent.SCAN):
        await store.record_health(component, mode != "failure", run_id="old" if mode == "previous-run" else "current")
    now = datetime.now(UTC) + (timedelta(days=1) if mode == "stale" else timedelta())
    report = await readiness.report(now=now)
    assert report["ready"] == (mode == "fresh")


async def test_dead_letters_degrade_readiness_even_with_fresh_worker(store, app_config):
    collector = MetricsCollector()
    collector.set_gauge("trader_event_loop_lag_seconds", 0)
    readiness = ReadinessService(store, app_config, collector)
    readiness.started = True
    for component in (HealthComponent.RECONCILIATION, HealthComponent.WORKER, HealthComponent.SCAN):
        await readiness.observe(component, True)
    await store.enqueue_notification("dead", "message", {"text": "example"})
    item = await store.claim_notification(max_attempts=8, lease_seconds=1)
    await store.finish_notification(item, delivered=False, max_attempts=1, retry_seconds=1)
    report = await readiness.report()
    assert not report["ready"] and report["checks"]["outbox"]["dead_letters"] == 1
    assert await store.requeue_notification(item.id)
    assert (await readiness.report())["checks"]["outbox"]["dead_letters"] == 0


async def test_repeated_equal_timestamp_old_order_does_not_regress_terminal_state(store, observation):
    empty = observation.model_copy(update={"status": "accepted", "filled_quantity": "0", "average_fill_price": None})
    canceled = empty.model_copy(update={"status": "canceled"})
    await store.observe_orders([empty, canceled, empty])
    assert (await store.order_views())[0].status == "canceled"
    await store.rebuild_order_views()
    assert (await store.order_views())[0].status == "canceled"


@pytest.mark.parametrize(
    "status,quantity,released",
    [
        ("canceled", "0", True),
        ("rejected", "0", True),
        ("expired", "0", True),
        ("canceled", "3", False),
        ("pending_cancel", "0", False),
    ],
)
async def test_only_confirmed_zero_fill_terminal_entries_release_reservations(
    store, entry, observation, status, quantity, released
):
    req = await entry()
    await store.db.update_signal_execution(req.signal_id, observation.order_id)
    await store.observe_orders([observation.model_copy(update={"status": status, "filled_quantity": quantity})])
    assert ((await store.db.get_signal_by_id(req.signal_id))["status"] == "FAILED") == released


async def test_stale_cancel_cannot_release_a_more_recent_filled_entry(store, entry, observation):
    req = await entry()
    await store.db.update_signal_execution(req.signal_id, observation.order_id)
    await store.observe_orders([observation])
    stale_cancel = observation.model_copy(
        update={
            "updated_at": observation.updated_at - timedelta(seconds=5),
            "status": "canceled",
            "filled_quantity": "0",
        }
    )
    await store.observe_orders([stale_cancel])
    assert (await store.db.get_signal_by_id(req.signal_id))["status"] == "EXECUTED"


@pytest.mark.parametrize("ready", [True, False])
async def test_passive_readiness_http_status_and_body(store, app_config, ready):

    metrics = MetricsCollector()
    metrics.set_gauge("trader_event_loop_lag_seconds", 0)
    service = ReadinessService(store, app_config, metrics)
    service.started = True
    for component in (HealthComponent.RECONCILIATION, HealthComponent.WORKER, HealthComponent.SCAN):
        await service.observe(component, ready)
    server = MetricsServer(readiness=service.report)
    reader = asyncio.StreamReader()
    reader.feed_data(b"GET /readyz HTTP/1.1\r\nHost: localhost\r\n\r\n")
    reader.feed_eof()
    writer = MagicMock(drain=AsyncMock(), wait_closed=AsyncMock())
    await server._handle_client(reader, writer)
    response = writer.write.call_args.args[0]
    assert response.startswith(b"HTTP/1.1 200" if ready else b"HTTP/1.1 503")
    assert json.loads(response.split(b"\r\n\r\n", 1)[1])["ready"] == ready


async def test_cancel_seen_before_entry_ack_is_applied_on_next_observation(store, entry, observation):
    request = await entry()
    canceled = observation.model_copy(update={"status": "canceled", "filled_quantity": "0"})
    await store.observe_orders([canceled])
    await store.db.update_signal_execution(request.signal_id, observation.order_id)
    await store.observe_orders([canceled])
    assert (await store.db.get_signal_by_id(request.signal_id))["status"] == "FAILED"


@pytest.mark.parametrize("observation", [None, False, True])
async def test_accounting_readiness_requires_current_run_evidence(store, app_config, observation):
    metrics = MetricsCollector()
    metrics.set_gauge("trader_event_loop_lag_seconds", 0)
    service = ReadinessService(store, app_config, metrics, accounting_enabled=True)
    service.started = True
    for component in (HealthComponent.RECONCILIATION, HealthComponent.WORKER, HealthComponent.SCAN):
        await service.observe(component, True)
    if observation is not None:
        await service.observe(HealthComponent.ACCOUNTING, observation)
    assert (await service.report())["ready"] is (observation is True)


@pytest.mark.parametrize("mode", ["disabled", "missing", "fresh", "previous-run", "stale", "failure"])
async def test_forward_observer_readiness_uses_current_run_progress(store, app_config, mode):
    app_config.alpha_pipeline.observations.enabled = mode != "disabled"
    readiness = ReadinessService(store, app_config, MetricsCollector(), run_id="current")
    if mode not in ("disabled", "missing"):
        await store.record_health(
            HealthComponent.ALPHA_OBSERVER, mode != "failure", run_id="old" if mode == "previous-run" else "current"
        )
    now = datetime.now(UTC) + (timedelta(hours=1) if mode == "stale" else timedelta())
    checks = (await readiness.report(now=now))["checks"]
    if mode == "disabled":
        assert HealthComponent.ALPHA_OBSERVER not in checks
    else:
        assert checks[HealthComponent.ALPHA_OBSERVER]["ready"] == (mode == "fresh")
