"""Exercise drawdown through scan orchestration, not only the sizing helper."""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.constants import ExecutionMode
from agentic_trader.diagnostics.readiness import HealthComponent, ReadinessService
from agentic_trader.research.alpha.models import RegistrySnapshot
from agentic_trader.telemetry.collector import MetricsCollector


@pytest.fixture
def risk_desk(app_config, temp_db, mock_notifier):
    app_config.execution_mode = ExecutionMode.ALPACA
    app_config.copilot_chat_enabled = False
    repository = AsyncMock()
    repository.snapshot.return_value = RegistrySnapshot(0, (), ())
    ledger = AsyncMock()
    ledger.current_risk.return_value = SimpleNamespace(
        drawdown_pct=Decimal("0.045"), equity=Decimal(10000), fingerprint="observed-risk"
    )
    copilot = TradingCopilot(
        app_config,
        db=temp_db,
        broker=MagicMock(supports_activity_ledger=True),
        notifier=mock_notifier,
        alpha_repository=repository,
        ledger=ledger,
    )
    copilot.evaluator = AsyncMock()
    copilot.evaluator.evaluate_candidate.return_value = SimpleNamespace(approved=False, rejection_reason="test")
    copilot.alpha_shadow = AsyncMock()
    copilot.readiness = AsyncMock()
    copilot.monitor_positions = AsyncMock()
    copilot.data_fetcher = MagicMock()
    copilot.data_fetcher.fetch_data.return_value = SimpleNamespace(
        daily=pd.DataFrame({"Close": [100.0]}),
        four_hour=pd.DataFrame({"Close": [100.0]}),
    )
    candidate = SimpleNamespace(
        contract="SPY", direction="LONG", strategy="test", current_price=100, timeframe="4h", alpha_version=None
    )
    copilot.strategy_engine = MagicMock()
    copilot.strategy_engine.scan_contract.return_value = [candidate]
    copilot.session_provider = AsyncMock()
    copilot.session_provider.is_session_active.return_value = (True, "Open")
    copilot.calendar = AsyncMock()
    copilot.calendar.is_in_lockout_window.return_value = (False, None)
    copilot.regime_detector = AsyncMock()
    copilot.regime_detector.get_regime.return_value = SimpleNamespace(summary_text="test")
    app_config.contracts = {"SPY": SimpleNamespace(name="SPY", ticker="SPY", asset_class="EQUITY")}
    return copilot


async def test_production_scan_passes_observed_drawdown_to_evaluator(risk_desk):
    await risk_desk.run_scan(use_llm=False, symbols=["SPY"])
    assert risk_desk.evaluator.evaluate_candidate.await_args.kwargs["current_drawdown_pct"] == 0.045
    risk_desk.ledger.current_risk.assert_awaited()


@pytest.mark.parametrize("reason", ["missing observation", "stale observation", "failed reconciliation"])
async def test_missing_account_risk_cannot_become_zero_drawdown(risk_desk, reason):
    risk_desk.ledger.current_risk.side_effect = ValueError(reason)
    await risk_desk.run_scan(use_llm=False, symbols=["SPY"])
    risk_desk.evaluator.evaluate_candidate.assert_not_awaited()
    # Independent monitoring remains available; an entry gate is not an emergency halt.
    assert not await risk_desk.check_halt_state()


async def test_empty_dry_scan_does_not_read_broker_risk(risk_desk):
    risk_desk.ledger.current_risk.side_effect = AssertionError("Dry scan read account risk")
    await risk_desk.run_scan(use_llm=False, dry_run=True, symbols=["SPY"])
    assert risk_desk.evaluator.evaluate_candidate.await_args.kwargs["current_drawdown_pct"] == 0
    risk_desk.ledger.current_risk.assert_not_awaited()


@pytest.mark.parametrize(
    ("session_open", "macro_lockout", "bypass_session", "detail"),
    [
        (False, False, False, "Session gate checked: Closed"),
        (True, True, False, "Macro gate checked: Scheduled event; entry alerts paused"),
        (False, True, True, "Macro gate checked: Scheduled event; entry alerts paused"),
    ],
)
@pytest.mark.parametrize("dry_run", [False, True])
async def test_entry_policy_gates_record_scan_progress_without_scoring(
    risk_desk, session_open, macro_lockout, bypass_session, detail, dry_run
):
    risk_desk.readiness = ReadinessService(risk_desk.db.workflows, risk_desk.config, MetricsCollector())
    risk_desk.session_provider.is_session_active.return_value = (session_open, "Closed")
    risk_desk.calendar.is_in_lockout_window.return_value = (
        macro_lockout,
        SimpleNamespace(title="Scheduled event", timestamp=datetime(2026, 9, 18, 13, 30, tzinfo=UTC)),
    )

    await risk_desk.run_scan(use_llm=False, symbols=["SPY"], bypass_session_filter=bypass_session, dry_run=dry_run)

    checks = (await risk_desk.readiness.report())["checks"]
    assert checks[HealthComponent.SCAN]["ready"] is not dry_run
    assert checks[HealthComponent.SCAN]["detail"] == ("No observation in this run" if dry_run else detail)
    assert not checks[HealthComponent.RECONCILIATION]["ready"]  # Other observations stay independent.
    risk_desk.data_fetcher.fetch_data.assert_not_called()
    risk_desk.strategy_engine.scan_contract.assert_not_called()
    risk_desk.evaluator.evaluate_candidate.assert_not_awaited()
    risk_desk.alpha_shadow.observe.assert_not_awaited()


async def test_failed_macro_check_does_not_record_healthy_scan(risk_desk):
    risk_desk.calendar.is_in_lockout_window.side_effect = RuntimeError("Calendar unavailable")
    with pytest.raises(RuntimeError, match="Calendar unavailable"):
        await risk_desk.run_scan(use_llm=False, symbols=["SPY"])
    risk_desk.readiness.observe.assert_not_awaited()
    risk_desk.evaluator.evaluate_candidate.assert_not_awaited()
