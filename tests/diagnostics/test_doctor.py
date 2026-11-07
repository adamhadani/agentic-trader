import asyncio
import urllib.request
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError

import pytest
from alpaca.data.enums import DataFeed
from alpaca.data.models import BarSet

from agentic_trader.config import AppConfig, PositionSizingConfig
from agentic_trader.diagnostics.doctor import (
    RECENT_ACCESS_WINDOW,
    ComponentHealth,
    DiagnosticReport,
    check_finnhub,
    check_market_data,
    format_doctor_cli_output,
    run_diagnostics,
)
from agentic_trader.telemetry.server import MetricsServer


@pytest.mark.asyncio
async def test_doctor_diagnostics_healthy(tmp_path):
    config = AppConfig(
        db_path=str(tmp_path / "doctor.db"),
        sizing=PositionSizingConfig(mode="static", target_risk_pct=0.005),
    )

    with (
        patch("agentic_trader.diagnostics.doctor.check_telegram", new_callable=AsyncMock) as mock_tg,
        patch("agentic_trader.diagnostics.doctor.check_alpaca", new_callable=AsyncMock) as mock_al,
        patch("agentic_trader.diagnostics.doctor.check_tradovate", new_callable=AsyncMock) as mock_tr,
        patch("agentic_trader.diagnostics.doctor.check_finnhub", new_callable=AsyncMock) as mock_fh,
        patch("agentic_trader.diagnostics.doctor.check_llm", new_callable=AsyncMock) as mock_llm,
    ):
        mock_tg.return_value = ComponentHealth(name="telegram", status="OK", message="Bot OK")
        mock_al.return_value = ComponentHealth(name="alpaca", status="OK", message="Alpaca OK")
        mock_tr.return_value = ComponentHealth(name="tradovate", status="DISABLED", message="Tradovate skipped")
        mock_fh.return_value = ComponentHealth(name="finnhub", status="OK", message="Finnhub OK")
        mock_llm.return_value = ComponentHealth(name="llm", status="OK", message="LLM OK")

        report = await run_diagnostics(config)
        assert isinstance(report, DiagnosticReport)
        assert report.is_healthy()
        assert "database" in report.components
        assert "risk_limits" in report.components
        assert "market_calendar" in report.components

        output = format_doctor_cli_output(report)
        assert "PRE-FLIGHT SYSTEM DOCTOR" in output
        assert "ACTIVE PROBES PASSED" in output


@pytest.mark.enable_socket
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_http_surface_cannot_run_active_diagnostics(tmp_path):
    server = MetricsServer(host="127.0.0.1", port=0)
    with patch("agentic_trader.diagnostics.doctor.run_diagnostics", new_callable=AsyncMock) as active:
        await server.start()
        try:
            with pytest.raises(HTTPError) as error:
                await asyncio.to_thread(urllib.request.urlopen, f"http://127.0.0.1:{server.port}/healthcheck")
            assert error.value.code == 404
            active.assert_not_awaited()
        finally:
            await server.stop()


async def test_unhandled_probe_errors_do_not_disclose_connection_details(app_config, monkeypatch):
    for name in ("database", "risk_limits", "market_calendar", "telegram", "alpaca", "tradovate", "finnhub", "llm"):
        monkeypatch.setattr(
            f"agentic_trader.diagnostics.doctor.check_{name}",
            AsyncMock(side_effect=RuntimeError("private-connection-details")),
        )
    report = await run_diagnostics(app_config)
    assert report.overall_status == "UNHEALTHY"
    assert "private-connection-details" not in report.model_dump_json()
    assert "RuntimeError" in report.model_dump_json()


async def test_finnhub_probe_uses_actual_provider_without_calendar_alias(app_config, monkeypatch):
    app_config.finnhub_api_key = "fake-key"
    provider = AsyncMock(return_value=[])
    monkeypatch.setattr("agentic_trader.diagnostics.doctor.FinnhubCalendarProvider.get_calendar_range", provider)
    result = await check_finnhub(app_config)
    assert result.status == "OK" and result.details["provider"] == "finnhub_api"
    start, end = provider.call_args.args
    assert (end - start).days == 7


class _RecordingBars:
    """A typed bar reader that records the probe request; it fails with ``error`` when given one."""

    def __init__(self, error: Exception | None = None):
        self.requests: list = []
        self.error = error

    def get_stock_bars(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return BarSet({})


PROBE_NOW = datetime(2026, 11, 9, 15, 35, tzinfo=UTC)


def sent(moment: datetime) -> datetime:
    """The SDK request models carry UTC as naive datetimes."""
    return moment.astimezone(UTC).replace(tzinfo=None)


async def test_under_sip_delayed_the_market_data_probe_asks_for_delayed_sip(app_config):
    app_config.market_data.alpaca_feed = "iex"
    app_config.market_data.scan_bars = "sip_delayed"
    reader = _RecordingBars()

    result = await check_market_data(app_config, client=reader, now=PROBE_NOW)

    [request] = reader.requests
    assert request.feed == DataFeed.SIP
    assert request.end == sent(PROBE_NOW - timedelta(minutes=16))
    assert request.start == request.end - RECENT_ACCESS_WINDOW
    assert result.status == "OK"
    assert result.details["feed"] == "alpaca:sip" and result.details["end_delay_minutes"] == 16
    assert "delayed alpaca:sip" in result.message.lower() and "16 min" in result.message


async def test_a_refused_delayed_sip_probe_names_delayed_sip(app_config):
    app_config.market_data.scan_bars = "sip_delayed"

    result = await check_market_data(app_config, client=_RecordingBars(PermissionError("403")), now=PROBE_NOW)

    assert result.status == "ERROR"
    assert "delayed alpaca:sip" in result.message.lower() and "No fallback used" in result.message


@pytest.mark.parametrize("feed", ["sip", "iex"])
async def test_under_configured_the_market_data_probe_is_unchanged(app_config, feed):
    app_config.market_data.alpaca_feed = feed
    reader = _RecordingBars()

    result = await check_market_data(app_config, client=reader, now=PROBE_NOW)

    [request] = reader.requests
    assert (request.feed, request.end, request.start) == (
        DataFeed(feed),
        sent(PROBE_NOW),
        sent(PROBE_NOW - RECENT_ACCESS_WINDOW),
    )
    assert result.status == "OK"
    assert result.details == {
        "feed": f"alpaca:{feed}",
        "recent_access": True,
        "freshness_verified": False,
        "observations": 0,
    }
    assert result.message == f"Recent alpaca:{feed} request permitted; price freshness requires separate observations"
