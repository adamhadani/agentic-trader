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
    """A typed bar reader that records each probe request; requests on a feed in ``refuse`` raise ``error``."""

    def __init__(self, error: Exception | None = None, refuse: frozenset[DataFeed] = frozenset(DataFeed)):
        self.requests: list = []
        self.error = error
        self.refuse = refuse

    def get_stock_bars(self, request):
        self.requests.append(request)
        if self.error is not None and request.feed in self.refuse:
            raise self.error
        return BarSet({})


PROBE_NOW = datetime(2026, 11, 9, 15, 35, tzinfo=UTC)


def sent(moment: datetime) -> datetime:
    """The SDK request models carry UTC as naive datetimes."""
    return moment.astimezone(UTC).replace(tzinfo=None)


def sip_delayed(app_config: AppConfig) -> AppConfig:
    """Scan bars on delayed SIP beside real-time IEX quotes, as shipped."""
    app_config.market_data.alpaca_feed = "iex"
    app_config.market_data.scan_bars = "sip_delayed"
    app_config.alpaca_data_feed = "iex"
    return app_config


async def test_under_sip_delayed_the_component_probes_delayed_sip_bars_and_real_time_quotes(app_config):
    reader = _RecordingBars()

    result = await check_market_data(sip_delayed(app_config), client=reader, now=PROBE_NOW)

    requests = {request.feed: request for request in reader.requests}
    assert len(reader.requests) == 2 and set(requests) == {DataFeed.SIP, DataFeed.IEX}
    delayed, real_time = requests[DataFeed.SIP], requests[DataFeed.IEX]
    assert (delayed.start, delayed.end) == (
        sent(PROBE_NOW - timedelta(minutes=16) - RECENT_ACCESS_WINDOW),
        sent(PROBE_NOW - timedelta(minutes=16)),
    )
    assert (real_time.start, real_time.end) == (sent(PROBE_NOW - RECENT_ACCESS_WINDOW), sent(PROBE_NOW))
    assert result.name == "market_data" and result.status == "OK"
    assert result.details == {
        "feed": "alpaca:sip",
        "end_delay_minutes": 16,
        "freshness_verified": False,
        "access": {
            "scan_bars": {"feed": "alpaca:sip", "delayed": True, "access": True, "observations": 0},
            "quotes": {"feed": "alpaca:iex", "delayed": False, "access": True, "observations": 0},
        },
    }
    assert result.message == (
        "Delayed alpaca:sip request (ending 16 min ago) permitted; recent alpaca:iex request permitted; "
        "price freshness requires separate observations"
    )


@pytest.mark.parametrize(
    ("refused", "failed_probe", "message"),
    [
        (
            DataFeed.SIP,
            "scan_bars",
            "Delayed alpaca:sip request (ending 16 min ago) failed; recent alpaca:iex request permitted",
        ),
        (
            DataFeed.IEX,
            "quotes",
            "Delayed alpaca:sip request (ending 16 min ago) permitted; recent alpaca:iex request failed",
        ),
    ],
)
async def test_under_sip_delayed_either_refused_probe_fails_the_component_and_is_named(
    app_config, refused, failed_probe, message
):
    reader = _RecordingBars(PermissionError("403"), refuse=frozenset({refused}))

    result = await check_market_data(sip_delayed(app_config), client=reader, now=PROBE_NOW)

    assert result.status == "ERROR"
    assert result.message == f"{message}; verify feed entitlement and transport. No fallback used"
    access = result.details["access"]
    assert access[failed_probe] == {
        "feed": access[failed_probe]["feed"],
        "delayed": failed_probe == "scan_bars",
        "access": False,
        "error_type": "PermissionError",
        "http_status": None,
    }
    [passed_probe] = set(access) - {failed_probe}
    assert access[passed_probe]["access"] is True
    assert "recent_access" not in result.details


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
