from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
from click.testing import CliRunner

import agentic_trader.cli.commands.cards as cards_module
from agentic_trader.cli.main import cli
from agentic_trader.config import ScanBudget, load_config
from agentic_trader.diagnostics.doctor import ComponentHealth, DiagnosticReport
from agentic_trader.execution.durable import EventKind
from agentic_trader.presentation.formatters import PanicReportView
from agentic_trader.research.setups import sources
from agentic_trader.storage.db import SignalDatabase


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
        "cards",
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
        ("cards", "outcomes"),
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
            budget=ScanBudget.SESSION,
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
            budget=ScanBudget.SESSION,
            strategy="trend_pullback",
            strategy_mode="single",
        )


def test_cli_scan_no_budget_flag_disables_the_suggestion_budget(runner: CliRunner):
    """--no-budget records every approved candidate; a plain scan keeps the session budget."""
    for args, expected in (
        (["scan", "--no-budget", "--dry-run"], ScanBudget.NONE),
        (["scan", "--dry-run"], ScanBudget.SESSION),
    ):
        with patch("agentic_trader.cli.commands.scan.get_copilot_and_config") as mock_get:
            mock_copilot = MagicMock()
            mock_copilot.broker = MagicMock()
            mock_copilot.broker.connect = AsyncMock()
            mock_copilot.run_scan = AsyncMock()
            mock_get.return_value = (mock_copilot, MagicMock())

            result = runner.invoke(cli, args)
            assert result.exit_code == 0, result.output
            assert mock_copilot.run_scan.await_args.kwargs["budget"] is expected


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
    """Verify copilot explain-macro invokes macro_engine.evaluate and formats explanation."""
    with patch("agentic_trader.cli.commands.trade.get_copilot_and_config") as mock_get:
        mock_copilot = MagicMock()
        mock_report = MagicMock()
        mock_copilot.regime_detector.macro_engine.evaluate = AsyncMock(
            return_value=SimpleNamespace(report=mock_report, missing_decision=())
        )
        mock_get.return_value = (mock_copilot, MagicMock())

        with patch(
            "agentic_trader.agent.macro_explainer.MacroExplainer.explain", new_callable=AsyncMock
        ) as mock_explain:
            mock_explain.return_value = "Tutorial: Yield curve is NORMAL_STEEP."
            result = runner.invoke(cli, ["explain-macro"])
            assert result.exit_code == 0
            assert "Evaluating institutional macro indicators" in result.output
            assert "Tutorial: Yield curve is NORMAL_STEEP." in result.output
            assert mock_explain.await_args.args[0] is mock_report


def test_cli_explain_macro_without_a_report_names_the_missing_decision_inputs(runner: CliRunner):
    with patch("agentic_trader.cli.commands.trade.get_copilot_and_config") as mock_get:
        mock_copilot = MagicMock()
        mock_copilot.regime_detector.macro_engine.evaluate = AsyncMock(
            return_value=SimpleNamespace(report=None, missing_decision=("2y", "hy_oas"))
        )
        mock_get.return_value = (mock_copilot, MagicMock())

        with patch(
            "agentic_trader.agent.macro_explainer.MacroExplainer.explain", new_callable=AsyncMock
        ) as mock_explain:
            result = runner.invoke(cli, ["explain-macro"])
            assert result.exit_code == 0
            assert "Macro report unavailable: missing decision inputs: 2y, hy_oas" in result.output
            mock_explain.assert_not_awaited()


class _FakeBarSource:
    """A fake ``BarSource``: no network, one canned hourly frame for every symbol."""

    def __init__(self, frame: pd.DataFrame):
        self.frame = frame
        self.calls: list[tuple] = []

    def fetch_bars(self, symbol, timeframe, start, end, *, adjustment):
        self.calls.append((symbol, timeframe, start, end, adjustment))
        return self.frame


def test_cli_outputs_table_with_fake_sources(runner: CliRunner):
    """`copilot cards outcomes` reads journaled candidates, labels them, and prints a table.

    No orders, no Telegram, no broker/network I/O: the bar source is a fake, and the
    only DB access is the read-only journal reader plus the direct seed write below.
    """
    fixed_now = datetime(2026, 3, 3, 20, 0, tzinfo=UTC)  # 15:00 ET, after the regular session
    decided_at = datetime(2026, 3, 3, 15, 0, tzinfo=UTC)  # 10:00 ET, inside the regular session

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is not None else fixed_now.replace(tzinfo=None)

    hourly = pd.DataFrame(
        {
            "Open": [100.5, 100.6],
            "High": [100.8, 102.5],
            "Low": [100.2, 100.3],
            "Close": [100.6, 102.0],
        },
        index=pd.DatetimeIndex(
            [pd.Timestamp("2026-03-03T15:00:00+00:00"), pd.Timestamp("2026-03-03T16:00:00+00:00")], tz="UTC"
        ),
    )
    fake_bars = _FakeBarSource(hourly)

    async def seed() -> None:
        db = SignalDatabase(config=load_config())
        try:
            payload = {
                "scan_id": "cli-smoke-scan",
                "decided_at": decided_at.isoformat(),
                "scope": "universe",
                "budget": "full",
                "ranking_key": "setup_quality",
                "candidates": [
                    {
                        "contract": "AAPL",
                        "strategy": "BREAKOUT",
                        "timeframe": "1h",
                        "direction": "LONG",
                        "entry": 100.0,
                        "stop": 99.0,
                        "target": 102.0,
                        "atr_14": 1.0,
                        "setup_quality": 0.8,
                        "rank": 1,
                        "outcome": "sent",
                        "shadow": {"score": 0.7},
                        "llm": {
                            "approved": True,
                            "rejection_reason": None,
                            "stop_loss": 99.0,
                            "take_profit": 102.0,
                            "applied": True,
                        },
                        "signal_id": None,
                    }
                ],
            }
            async with db.session_factory() as session, session.begin():
                await db.workflows.lock(session)
                await db.workflows.append(
                    session,
                    stream="scan/2026-03-03",
                    kind=EventKind.SCAN_CANDIDATES_RANKED,
                    payload=payload,
                    key="scan_candidates_ranked/cli-smoke-scan",
                )
        finally:
            await db.engine.dispose()

    asyncio.run(seed())

    with (
        patch.object(cards_module, "datetime", _FrozenDateTime),
        patch.object(cards_module, "build_bar_source", return_value=fake_bars),
    ):
        result = runner.invoke(cli, ["cards", "outcomes", "--days", "1"])

    assert result.exit_code == 0, result.output
    assert fake_bars.calls, "expected the fake bar source to be queried for AAPL"
    assert fake_bars.calls[0][0] == "AAPL"
    assert fake_bars.calls[0][4] == "raw"
    assert {call[0] for call in fake_bars.calls} == {"AAPL", "SPY"}
    assert '"llm_gate"' in result.output and '"market_exposure"' in result.output and '"execution"' in result.output
    assert '"ran": 1' in result.output
    assert '"total": 1' in result.output
    assert "AAPL" in result.output
    assert "target" in result.output
    # No orders, no Telegram: only the journal-evidence report is printed. The execution
    # block's fill-source key names the journal event it reads, not an order action.
    assert '"order_observed": 0' in result.output
    assert "order" not in result.output.lower().replace('"order_observed"', "")
    assert "telegram" not in result.output.lower()


def test_cards_outcomes_prints_supply_and_the_empty_line(runner: CliRunner):
    """A window whose only scan ranked nothing still reports it: `supply` counts scans, and the empty line says so."""
    decided_at = datetime(2026, 3, 3, 15, 0, tzinfo=UTC)
    event = {
        "id": 1,
        "stream": "scan/2026-03-03",
        "kind": EventKind.SCAN_CANDIDATES_RANKED,
        "payload": {
            "scan_id": "empty-scan",
            "decided_at": decided_at.isoformat(),
            "scope": "universe",
            "trigger": "suggestion_scan",
            "status": "completed",
            "candidates": [],
            "funnel": {"selected": 100, "scanned": 100, "raw_setups": 0, "admitted": 0, "errors": {}},
        },
    }
    fake_bars = _FakeBarSource(pd.DataFrame())

    with (
        patch.object(cards_module, "scan_ranked_events", AsyncMock(return_value=[event])),
        patch.object(cards_module, "build_bar_source", return_value=fake_bars),
    ):
        result = runner.invoke(cli, ["cards", "outcomes"])

    assert result.exit_code == 0, result.output
    assert not fake_bars.calls
    assert '"supply"' in result.output
    assert '"suggestion_scan"' in result.output and '"zero_candidate": 1' in result.output
    assert "No journaled candidates in 30 day(s) (1 scan events)." in result.output


def test_cards_outcomes_prints_operator_block(runner: CliRunner):
    """The summary carries the descriptive `operator` block (an empty window is `insufficient`, never a gate)."""
    with (
        patch.object(cards_module, "scan_ranked_events", AsyncMock(return_value=[])),
        patch.object(cards_module, "build_bar_source", return_value=_FakeBarSource(pd.DataFrame())),
    ):
        result = runner.invoke(cli, ["cards", "outcomes"])

    assert result.exit_code == 0, result.output
    for key in ('"operator"', '"by_slot"', '"tap_latency"', '"kept_vs_vetoed"', '"tap_era"'):
        assert key in result.output
    assert '"status": "insufficient"' in result.output


def test_cards_outcomes_daily_source_reads_sip_through_its_delayed_bound(runner: CliRunner):
    """The beta and per-name cost reads are SIP: each ends at least 15 minutes before now (an end of now is a 403)."""
    fixed_now = datetime(2026, 3, 3, 20, 0, tzinfo=UTC)
    decided_at = datetime(2026, 3, 3, 15, 0, tzinfo=UTC)
    candidate = {
        "contract": "AAPL",
        "strategy": "BREAKOUT",
        "timeframe": "1h",
        "direction": "LONG",
        "entry": 100.0,
        "stop": 99.0,
        "target": 102.0,
        "rank": 1,
        "outcome": "sent",
    }
    event = {
        "id": 1,
        "stream": "scan/2026-03-03",
        "kind": EventKind.SCAN_CANDIDATES_RANKED,
        "payload": {
            "scan_id": "daily-bound",
            "decided_at": decided_at.isoformat(),
            "scope": "universe",
            "trigger": "suggestion_scan",
            "candidates": [candidate],
        },
    }
    hourly = pd.DataFrame(
        {"Open": [100.5], "High": [100.8], "Low": [100.2], "Close": [100.6]},
        index=pd.DatetimeIndex([pd.Timestamp("2026-03-03T15:00:00+00:00")], tz="UTC"),
    )
    daily, feeds = _FakeBarSource(pd.DataFrame()), []

    def build(config, *, feed=None):
        feeds.append(feed)
        return daily

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is not None else fixed_now.replace(tzinfo=None)

    with (
        patch.object(cards_module, "datetime", _FrozenDateTime),
        patch.object(cards_module, "scan_ranked_events", AsyncMock(return_value=[event])),
        patch.object(cards_module, "build_bar_source", return_value=_FakeBarSource(hourly)),
        patch.object(sources, "build_bar_source", build),
    ):
        result = runner.invoke(cli, ["cards", "outcomes", "--days", "1"])

    assert result.exit_code == 0, result.output
    daily_reads = [call for call in daily.calls if call[1] == "1d"]
    assert feeds == ["sip"] and {call[0] for call in daily_reads} == {"AAPL", "SPY"}
    assert all(call[3] <= fixed_now - timedelta(minutes=15) for call in daily_reads)


def test_cards_outcomes_days_reach_the_card_statistics_window(runner: CliRunner):
    """`--days` accepts up to 180 days, the shipped `card_policy.stats_window_days`, and no more."""
    with (
        patch.object(cards_module, "scan_ranked_events", AsyncMock(return_value=[])) as events,
        patch.object(cards_module, "build_bar_source", return_value=_FakeBarSource(pd.DataFrame())),
    ):
        assert runner.invoke(cli, ["cards", "outcomes", "--days", "180"]).exit_code == 0
        assert events.await_args.args[1] == 180
        assert runner.invoke(cli, ["cards", "outcomes", "--days", "181"]).exit_code != 0


def test_db_queue_default_kind_is_a_valid_choice(runner: CliRunner):
    result = runner.invoke(cli, ["db", "queue"])
    assert "is not one of" not in result.output, result.output


def test_cards_outcomes_reports_the_insider_watch_block(runner: CliRunner):
    """Journaled `insider_watch_decision` events are labelled with the frozen bracket; read-only and descriptive."""
    fixed_now = datetime(2026, 10, 12, 20, 0, tzinfo=UTC)
    session = "2026-10-05"
    decision = datetime(2026, 10, 5, 14, 35, tzinfo=UTC)
    hours = [datetime(2026, 10, 5, h, tzinfo=UTC) for h in (13, 14, 15, 16, 17)]  # 09:00..13:00 ET (EDT)
    prices = [100.0, 100.0, 100.0, 112.0, 112.0]
    hourly = pd.DataFrame(
        {"Open": prices, "High": prices, "Low": prices, "Close": prices, "Volume": 1.0},
        index=pd.DatetimeIndex(hours),
    )
    event = {
        "event_id": "AAA-1",
        "symbol": "AAA",
        "session": session,
        "decision_at": decision.isoformat(),
        "atr": 2.0,
        "buyers": 3,
        "opp_dollars": 900_000.0,
        "outcome": "sent",
    }
    payload = {"scan_id": "watch-scan", "session": session, "status": "completed", "events": [event]}

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is not None else fixed_now.replace(tzinfo=None)

    async def decision_events(db, days, *, now, kind):
        return [payload] if kind == EventKind.INSIDER_WATCH_DECISION else []

    configured = _FakeBarSource(pd.DataFrame())  # the configured (IEX) feed: never read by the watch block
    sip = _FakeBarSource(hourly)
    providers: list[dict] = []

    def provider(**kwargs):
        providers.append(kwargs)
        return sip

    with (
        patch.object(cards_module, "datetime", _FrozenDateTime),
        patch.object(cards_module, "scan_ranked_events", AsyncMock(return_value=[])),
        patch.object(cards_module, "build_bar_source", return_value=configured),
        patch.object(cards_module, "AlpacaDataProvider", provider),
        patch.object(cards_module, "watch_signals", AsyncMock(return_value=[])),
        patch.object(cards_module, "_decision_events", decision_events),
    ):
        result = runner.invoke(cli, ["cards", "outcomes", "--days", "10"])

    assert result.exit_code == 0, result.output
    assert "Insider watch:" in result.output and '"insider_watch"' in result.output
    assert '"sent_events"' in result.output and '"taps": 0' in result.output and '"n": 1' in result.output
    # The study, its +0.087R line and the PEAD record are all SIP: the watch block labels on SIP too.
    assert [p["feed"] for p in providers] == ["sip"]
    assert {call[0] for call in sip.calls} >= {"AAA", "SPY"} and configured.calls == []
    assert all(call[4] == "all" for call in sip.calls if call[0] == "AAA")


def test_cards_outcomes_reads_sip_only_through_its_delayed_bound(runner: CliRunner):
    """SIP history ending later than 15 minutes ago is a 403 for this account: every SIP read of the PEAD and
    watch blocks ends at least 15 minutes before now, and the bars after that bound never mature a label."""
    fixed_now = datetime(2026, 10, 5, 17, 30, tzinfo=UTC)  # 13:30 New York, mid-session
    bound = fixed_now - pd.Timedelta(minutes=16)
    session = "2026-10-05"
    decision = datetime(2026, 10, 5, 14, 35, tzinfo=UTC)
    hours = [datetime(2026, 10, 5, h, tzinfo=UTC) for h in (13, 14, 15, 16, 17)]  # 17:00 UTC is still forming
    prices = [100.0, 100.0, 100.0, 100.0, 112.0]
    hourly = pd.DataFrame(
        {"Open": prices, "High": prices, "Low": prices, "Close": prices, "Volume": 1.0},
        index=pd.DatetimeIndex(hours),
    )
    watch_event = {
        "event_id": "AAA-1",
        "symbol": "AAA",
        "session": session,
        "decision_at": decision.isoformat(),
        "atr": 2.0,
        "buyers": 3,
        "opp_dollars": 900_000.0,
        "outcome": "sent",
    }
    pead_event = {
        "symbol": "PPP",
        "report_date": "2026-10-02",
        "session": session,
        "decision_at": decision.isoformat(),
        "atr": 2.0,
        "leg": "LONG",
        "outcome": "sent",
    }
    payloads = {
        EventKind.INSIDER_WATCH_DECISION: [
            {"scan_id": "w", "session": session, "status": "completed", "events": [watch_event]}
        ],
        EventKind.PEAD_DECISION: [{"scan_id": "p", "session": session, "status": "ok", "events": [pead_event]}],
    }

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is not None else fixed_now.replace(tzinfo=None)

    async def decision_events(db, days, *, now, kind):
        return payloads.get(kind, [])

    sip = _FakeBarSource(hourly)
    pead_hourly: list[dict] = []

    def label_events(events, frames, entry):
        pead_hourly.append(dict(frames))
        return pd.DataFrame(), {}

    with (
        patch.object(cards_module, "datetime", _FrozenDateTime),
        patch.object(cards_module, "scan_ranked_events", AsyncMock(return_value=[])),
        patch.object(cards_module, "build_bar_source", return_value=_FakeBarSource(pd.DataFrame())),
        patch.object(cards_module, "AlpacaDataProvider", lambda **kwargs: sip),
        patch.object(cards_module, "label_events", label_events),
        patch.object(cards_module, "probe_signals", AsyncMock(return_value=[])),
        patch.object(cards_module, "watch_signals", AsyncMock(return_value=[])),
        patch.object(cards_module, "_decision_events", decision_events),
    ):
        result = runner.invoke(cli, ["cards", "outcomes", "--days", "1"])

    assert result.exit_code == 0, result.output
    assert {call[0] for call in sip.calls} >= {"AAA", "PPP", "SPY"}
    assert all(call[3] <= fixed_now - pd.Timedelta(minutes=15) for call in sip.calls)
    # The bar still forming at the bound (17:00 UTC, the +12 jump) is not part of either label.
    [frames] = pead_hourly
    assert frames["PPP"].index.max() < bound - pd.Timedelta(hours=1) + pd.Timedelta(seconds=1)
    assert '"immature": 1' in result.output and '"sent_events"' in result.output
