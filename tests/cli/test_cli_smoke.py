from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from agentic_trader.cli.main import cli
from agentic_trader.diagnostics.doctor import ComponentHealth, DiagnosticReport
from agentic_trader.presentation.formatters import PanicReportView


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
        "perf",
        "scan",
        "doctor",
        "backtest",
        "stress",
        "gex",
        "pairs",
        "metrics",
        "panic",
        "resume",
        "db",
    ]:
        assert expected_cmd in result.output


@pytest.mark.parametrize(
    ("command_name", "expected_help_str"),
    [
        ("status", "--help"),
        ("positions", "--help"),
        ("perf", "--help"),
        ("scan", "--bypass-session-filter"),
        ("execute", "--qty"),
        ("close", "--price"),
        ("panic", "--confirm"),
        ("resume", "--help"),
        ("doctor", "daemon freshness"),
        ("test-alert", "--help"),
        ("gex", "--expirations"),
        ("pairs", "--z-entry"),
        ("metrics", "--port"),
        ("backtest", "--trailing-stop-mode"),
        ("stress", "--scenario"),
        ("db", "upgrade"),
        ("explain-macro", "tutorial"),
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


def test_cli_scan_multi_strategy_smoke(runner: CliRunner):
    """Verify copilot scan forwards --strategy and --strategy-mode flags."""
    with patch("agentic_trader.cli.commands.scan.get_copilot_and_config") as mock_get:
        mock_copilot = MagicMock()
        mock_copilot.broker = MagicMock()
        mock_copilot.broker.connect = AsyncMock()
        mock_copilot.run_scan = AsyncMock()
        mock_get.return_value = (mock_copilot, MagicMock())

        result = runner.invoke(
            cli,
            [
                "scan",
                "--dry-run",
                "--no-llm",
                "--strategy",
                "trend_pullback",
                "--strategy-mode",
                "single",
                "--symbols",
                "QQQ",
            ],
        )
        assert result.exit_code == 0
        mock_copilot.run_scan.assert_called_once_with(
            use_llm=False,
            dry_run=True,
            asset_class="all",
            symbols=["QQQ"],
            bypass_session_filter=False,
            strategy="trend_pullback",
            strategy_mode="single",
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
        assert "ACTIVE PROBES PASSED" in result.output


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


def test_cli_panic_smoke(runner: CliRunner):
    """Verify copilot panic --confirm invokes emergency_panic_halt."""
    with patch("agentic_trader.cli.commands.trade.get_copilot_and_config") as mock_get:
        mock_copilot = MagicMock()
        mock_copilot.broker = MagicMock()
        mock_copilot.broker.connect = AsyncMock()
        view = PanicReportView(
            cancelled_orders_count=1,
            liquidated_positions_count=1,
            total_realized_pnl=50.0,
            is_halted=True,
            halt_reason="CLI test",
        )
        mock_copilot.emergency_panic_halt = AsyncMock(return_value=view)
        mock_get.return_value = (mock_copilot, MagicMock())

        result = runner.invoke(cli, ["panic", "--confirm"])
        assert result.exit_code == 0
        mock_copilot.emergency_panic_halt.assert_called_once()
        assert "EMERGENCY KILL SWITCH" in result.output


def test_cli_resume_smoke(runner: CliRunner):
    """Verify copilot resume invokes resume_trading."""
    with patch("agentic_trader.cli.commands.trade.get_copilot_and_config") as mock_get:
        mock_copilot = MagicMock()
        mock_copilot.broker = MagicMock()
        mock_copilot.broker.connect = AsyncMock()
        mock_copilot.resume_trading = AsyncMock(return_value={"success": True, "message": "Resumed"})
        mock_get.return_value = (mock_copilot, MagicMock())

        result = runner.invoke(cli, ["resume"])
        assert result.exit_code == 0
        mock_copilot.resume_trading.assert_called_once()
        assert "Trading operations resumed successfully" in result.output


def test_cli_explain_macro_smoke(runner: CliRunner):
    """Verify copilot explain-macro invokes macro_engine.get_macro_report and formats explanation."""
    with patch("agentic_trader.cli.commands.trade.get_copilot_and_config") as mock_get:
        mock_copilot = MagicMock()
        mock_report = MagicMock()
        mock_copilot.regime_detector.macro_engine.get_macro_report = AsyncMock(return_value=mock_report)
        mock_get.return_value = (mock_copilot, MagicMock())

        with patch(
            "agentic_trader.agent.macro_explainer.MacroExplainer.explain", new_callable=AsyncMock
        ) as mock_explain:
            mock_explain.return_value = "Tutorial: Yield curve is NORMAL_STEEP."
            result = runner.invoke(cli, ["explain-macro"])
            assert result.exit_code == 0
            assert "Evaluating institutional macro indicators" in result.output
            assert "Tutorial: Yield curve is NORMAL_STEEP." in result.output
