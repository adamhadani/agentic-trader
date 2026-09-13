from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from agentic_trader.cli.main import cli
from agentic_trader.diagnostics.doctor import ComponentHealth, DiagnosticReport


@pytest.fixture
def runner() -> CliRunner:
    """Provide Click CliRunner instance."""
    return CliRunner()


def test_cli_root_help(runner: CliRunner):
    """Verify root CLI displays all commands and exits with 0."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Agentic Trader" in result.output
    for expected_cmd in [
        "status",
        "positions",
        "scan",
        "doctor",
        "backtest",
        "optimize",
        "retune",
        "stress",
        "gex",
        "pairs",
        "metrics",
        "db",
    ]:
        assert expected_cmd in result.output


@pytest.mark.parametrize(
    ("command_name", "expected_help_str"),
    [
        ("status", "--help"),
        ("positions", "--help"),
        ("scan", "--bypass-session-filter"),
        ("execute", "--qty"),
        ("close", "--price"),
        ("doctor", "pre-flight"),
        ("test-alert", "--help"),
        ("gex", "--expirations"),
        ("pairs", "--z-entry"),
        ("metrics", "--port"),
        ("backtest", "--trailing-stop-mode"),
        ("optimize", "--walk-forward"),
        ("retune", "--min-wfe"),
        ("stress", "--scenario"),
        ("db", "upgrade"),
    ],
)
def test_cli_subcommands_help(runner: CliRunner, command_name: str, expected_help_str: str):
    """Parametrized smoke test verifying all CLI subcommands have operational help documentation."""
    result = runner.invoke(cli, [command_name, "--help"])
    assert result.exit_code == 0, f"Command {command_name} --help failed: {result.output}"
    assert expected_help_str in result.output


def test_cli_scan_smoke(runner: CliRunner):
    """Verify copilot scan runs cleanly with mocked copilot and passes bypass_session_filter."""
    with patch("agentic_trader.cli.commands.scan.get_copilot_and_config") as mock_get:
        mock_copilot = MagicMock()
        mock_copilot.broker = MagicMock()
        mock_copilot.broker.connect = AsyncMock()
        mock_copilot.run_scan = AsyncMock()
        mock_get.return_value = (mock_copilot, MagicMock())

        result = runner.invoke(cli, ["scan", "--dry-run", "--no-llm", "--bypass-session-filter", "--symbols", "SPY"])
        assert result.exit_code == 0
        mock_copilot.run_scan.assert_called_once_with(
            use_llm=False,
            dry_run=True,
            asset_class="all",
            symbols=["SPY"],
            bypass_session_filter=True,
        )


def test_cli_doctor_smoke(runner: CliRunner):
    """Verify copilot doctor runs cleanly with mocked healthy probes."""
    mock_report = DiagnosticReport(
        overall_status="HEALTHY",
        timestamp=datetime.now(UTC).isoformat(),
        components={
            "database": ComponentHealth(name="database", status="OK", message="Database connection verified"),
            "risk_limits": ComponentHealth(name="risk_limits", status="OK", message="Cash: $100,000 | Sizing: static"),
            "telegram": ComponentHealth(name="telegram", status="OK", message="Telegram bot verified"),
            "alpaca": ComponentHealth(name="alpaca", status="OK", message="Alpaca Paper API connected"),
            "tradovate": ComponentHealth(
                name="tradovate", status="DISABLED", message="Tradovate not configured (optional)"
            ),
            "finnhub": ComponentHealth(name="finnhub", status="OK", message="Finnhub API verified"),
            "llm": ComponentHealth(name="llm", status="OK", message="Model verified"),
        },
    )

    with patch("agentic_trader.cli.commands.service.run_diagnostics", new=AsyncMock(return_value=mock_report)):
        result = runner.invoke(cli, ["doctor"])
        assert result.exit_code == 0
        assert "PRE-FLIGHT SYSTEM DOCTOR" in result.output
        assert "HEALTHY" in result.output
        assert "ALL CRITICAL SYSTEMS OPERATIONAL" in result.output


def test_cli_status_smoke(runner: CliRunner):
    """Verify copilot status runs cleanly and displays formatted dashboard."""
    with patch("agentic_trader.cli.commands.trade.get_copilot_and_config") as mock_get:
        mock_copilot = MagicMock()
        mock_copilot.broker = MagicMock()
        mock_copilot.broker.connect = AsyncMock()
        mock_copilot.show_status = AsyncMock()
        mock_get.return_value = (mock_copilot, MagicMock())

        result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        mock_copilot.show_status.assert_called_once()


def test_cli_positions_smoke(runner: CliRunner):
    """Verify copilot positions runs cleanly and displays positions report."""
    with patch("agentic_trader.cli.commands.trade.get_copilot_and_config") as mock_get:
        mock_copilot = MagicMock()
        mock_copilot.broker = MagicMock()
        mock_copilot.broker.connect = AsyncMock()
        mock_copilot.show_positions = AsyncMock()
        mock_get.return_value = (mock_copilot, MagicMock())

        result = runner.invoke(cli, ["positions"])
        assert result.exit_code == 0
        mock_copilot.show_positions.assert_called_once()


def test_cli_metrics_smoke(runner: CliRunner):
    """Verify copilot metrics dumps prometheus exposition format."""
    result = runner.invoke(cli, ["metrics"])
    assert result.exit_code == 0
    assert "# HELP trader_" in result.output


def test_cli_stress_smoke(runner: CliRunner):
    """Verify copilot stress executes shock scenario dry-run."""
    result = runner.invoke(cli, ["stress", "--scenario", "shock"])
    assert result.exit_code == 0
    assert "PORTFOLIO STRESS TEST" in result.output


def test_cli_db_current_smoke(runner: CliRunner):
    """Verify copilot db current executes without exception."""
    result = runner.invoke(cli, ["db", "current"])
    assert result.exit_code == 0
