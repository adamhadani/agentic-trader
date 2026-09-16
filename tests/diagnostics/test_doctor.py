import asyncio
import urllib.request
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError

import pytest

from agentic_trader.config import AppConfig, PositionSizingConfig
from agentic_trader.diagnostics.doctor import (
    ComponentHealth,
    DiagnosticReport,
    check_finnhub,
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
