import asyncio
import json
import urllib.request
from unittest.mock import AsyncMock, patch

import pytest

from agentic_trader.config import AppConfig, PositionSizingConfig
from agentic_trader.diagnostics.doctor import (
    ComponentHealth,
    DiagnosticReport,
    format_doctor_cli_output,
    run_diagnostics,
)
from agentic_trader.telemetry.server import MetricsServer


@pytest.mark.asyncio
async def test_doctor_diagnostics_healthy():
    config = AppConfig(
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

        output = format_doctor_cli_output(report)
        assert "PRE-FLIGHT SYSTEM DOCTOR" in output
        assert "ALL CRITICAL SYSTEMS OPERATIONAL" in output


@pytest.mark.asyncio
async def test_telemetry_healthcheck_endpoint():
    config = AppConfig()
    server = MetricsServer(host="127.0.0.1", port=0, config=config)

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

        await server.start()
        port = server.port
        url = f"http://127.0.0.1:{port}/healthcheck"

        raw_bytes = await asyncio.to_thread(lambda: urllib.request.urlopen(url).read())
        await server.stop()

        data = json.loads(raw_bytes.decode("utf-8"))
        assert "overall_status" in data
        assert "components" in data
        assert data["components"]["telegram"]["status"] == "OK"
