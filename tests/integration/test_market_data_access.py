"""Actual SDK/TCP checks distinguish entitlement from bar availability."""

import asyncio
import threading
from datetime import UTC, datetime

import pytest

from agentic_trader.diagnostics.doctor import check_market_data


pytestmark = [pytest.mark.enable_socket, pytest.mark.allow_hosts(["127.0.0.1"])]


@pytest.mark.parametrize("feed", ["sip", "iex"])
@pytest.mark.parametrize("status", [200, 403, 422, 500])
async def test_recent_access_probe_keeps_requested_feed_and_redacts_errors(alpaca_http, app_config, feed, status):
    venue, broker = alpaca_http
    app_config.market_data.alpaca_feed = feed
    app_config.alpaca_api_key = "fake-key"
    app_config.alpaca_api_secret = "fake-secret"
    now = datetime(2026, 9, 17, 15, tzinfo=UTC)

    def response(method, path, query, body):
        if path == "/v2/stocks/bars":
            assert query["feed"] == [feed]
            assert query["timeframe"] == ["1Min"]
            return status, {"bars": {}, "next_page_token": None} if status == 200 else {
                "code": status * 10000,
                "message": "subscription does not permit querying recent SIP data; private-details",
            }

    venue.override = response
    broker.data_client._retry = 0
    result = await check_market_data(app_config, client=broker.data_client, now=now)
    assert result.status == ("OK" if status == 200 else "ERROR")
    assert result.details["feed"] == f"alpaca:{feed}"
    assert result.details["recent_access"] is (status == 200)
    assert "private-details" not in result.model_dump_json()
    assert len([p for _, p, *_ in venue.calls if p == "/v2/stocks/bars"]) == 1
    if status == 200:
        assert result.details["observations"] == 0
        assert result.details["freshness_verified"] is False


async def test_stalled_sdk_read_has_a_real_socket_deadline(alpaca_http, app_config):
    venue, broker = alpaca_http
    app_config.market_data.timeout_seconds = 0.05
    broker.data_client.request_timeout = 0.03
    broker.data_client._retry = 0
    release = threading.Event()
    entered = threading.Event()

    def stalled(method, path, query, body):
        if path == "/v2/stocks/bars":
            entered.set()
            release.wait(0.5)
            return 200, {"bars": {}, "next_page_token": None}

    venue.override = stalled
    task = asyncio.create_task(check_market_data(app_config, client=broker.data_client))
    try:
        assert await asyncio.to_thread(entered.wait, 0.3)
        result = await asyncio.wait_for(task, timeout=0.2)
        assert result.status == "ERROR"
        assert result.details["recent_access"] is False
    finally:
        release.set()
    assert len([p for _, p, *_ in venue.calls if p == "/v2/stocks/bars"]) == 1
