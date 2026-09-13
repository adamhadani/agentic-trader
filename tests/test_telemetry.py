from __future__ import annotations

import asyncio
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from agentic_trader.cli.commands.telemetry import metrics
from agentic_trader.config import load_config
from agentic_trader.main import FuturesCopilot
from agentic_trader.telemetry import (
    MetricsCollector,
    MetricsServer,
    global_metrics,
)


def test_metrics_collector_gauges() -> None:
    collector = MetricsCollector()
    collector.set_gauge("test_cash", 50000.0, help_text="Cash balance")
    collector.set_gauge("test_pos", 3.0, labels={"symbol": "ES"}, help_text="Open positions")

    exposition = collector.format_prometheus_exposition()
    assert "# HELP test_cash Cash balance" in exposition
    assert "# TYPE test_cash gauge" in exposition
    assert "test_cash 50000" in exposition
    assert 'test_pos{symbol="ES"} 3' in exposition

    # Overwrite gauge
    collector.set_gauge("test_cash", 45000.0)
    exposition2 = collector.format_prometheus()
    assert "test_cash 45000" in exposition2


def test_metrics_collector_counters() -> None:
    collector = MetricsCollector()
    collector.inc_counter("test_orders_total", 1.0, help_text="Total orders")
    collector.inc_counter("test_orders_total", 2.0)
    collector.inc_counter("test_fills_total", 1.0, labels={"symbol": "NQ", "side": "BUY"})

    exposition = collector.format_prometheus_exposition()
    assert "# HELP test_orders_total Total orders" in exposition
    assert "# TYPE test_orders_total counter" in exposition
    assert "test_orders_total 3" in exposition
    assert 'test_fills_total{side="BUY",symbol="NQ"} 1' in exposition

    # Invalid negative counter
    with pytest.raises(ValueError, match="must be non-negative"):
        collector.inc_counter("test_orders_total", -1.0)


def test_metrics_collector_histograms() -> None:
    collector = MetricsCollector()
    buckets = [0.05, 0.1, 0.25, 0.5, 1.0]
    collector.observe_histogram(
        "request_duration_seconds",
        0.08,
        buckets=buckets,
        labels={"route": "/scan"},
        help_text="Execution latency",
    )
    collector.observe_histogram(
        "request_duration_seconds",
        0.30,
        buckets=buckets,
        labels={"route": "/scan"},
    )

    exposition = collector.format_prometheus_exposition()
    assert "# HELP request_duration_seconds Execution latency" in exposition
    assert "# TYPE request_duration_seconds histogram" in exposition
    assert 'request_duration_seconds_bucket{le="0.05",route="/scan"} 0' in exposition
    assert 'request_duration_seconds_bucket{le="0.1",route="/scan"} 1' in exposition
    assert 'request_duration_seconds_bucket{le="0.25",route="/scan"} 1' in exposition
    assert 'request_duration_seconds_bucket{le="0.5",route="/scan"} 2' in exposition
    assert 'request_duration_seconds_bucket{le="+Inf",route="/scan"} 2' in exposition
    assert 'request_duration_seconds_count{route="/scan"} 2' in exposition
    assert 'request_duration_seconds_sum{route="/scan"} 0.38' in exposition


def test_metrics_collector_reset() -> None:
    collector = MetricsCollector()
    collector.set_gauge("temp_gauge", 100.0)
    assert "temp_gauge 100" in collector.format_prometheus()
    collector.reset()
    assert collector.format_prometheus() == ""


@pytest.mark.asyncio
async def test_metrics_server_endpoints() -> None:
    collector = MetricsCollector()
    collector.set_gauge("test_server_metric", 42.0)
    server = MetricsServer(host="127.0.0.1", port=0, collector=collector)
    await server.start()
    assert server._server is not None
    port = server.port

    def fetch_url(url: str) -> Any:
        return urllib.request.urlopen(url, timeout=5)

    # Test /metrics
    resp_metrics = await asyncio.to_thread(fetch_url, f"http://127.0.0.1:{port}/metrics")
    assert resp_metrics.status == 200
    metrics_body = resp_metrics.read().decode("utf-8")
    assert "test_server_metric 42" in metrics_body

    # Test /healthz
    resp_health = await asyncio.to_thread(fetch_url, f"http://127.0.0.1:{port}/healthz")
    assert resp_health.status == 200
    health_text = resp_health.read().decode("utf-8").strip()
    assert health_text == "OK"

    # Test 404 on unknown route
    try:
        await asyncio.to_thread(fetch_url, f"http://127.0.0.1:{port}/unknown")
    except urllib.error.HTTPError as err:
        assert err.code == 404

    await server.stop()


def test_cli_metrics_help() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "agentic_trader.main", "metrics", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "Export or print Prometheus telemetry metrics" in proc.stdout
    assert "--serve" in proc.stdout
    assert "--port" in proc.stdout


def test_cli_metrics_output() -> None:
    global_metrics.set_gauge("test_cli_gauge", 99.0)
    runner = CliRunner()
    result = runner.invoke(metrics, [])
    assert result.exit_code == 0
    assert "test_cli_gauge 99" in result.output

    # Test standalone subprocess execution
    proc = subprocess.run(
        [sys.executable, "-m", "agentic_trader.main", "metrics"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "trader_up 1" in proc.stdout


@pytest.mark.asyncio
async def test_copilot_telemetry_gauges() -> None:
    config = load_config()
    copilot = FuturesCopilot(config)
    assert copilot.metrics is not None

    with patch.object(copilot.db, "get_active_positions", AsyncMock(return_value=[])):
        monitored = await copilot.monitor_positions()
        assert monitored == 0

    exposition = copilot.metrics.format_prometheus()
    assert "trader_account_cash_dollars" in exposition
    assert "trader_active_positions_count 0" in exposition
