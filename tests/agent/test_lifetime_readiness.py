"""A failed lifetime evidence read degrades reconciliation readiness with type names only."""

from types import SimpleNamespace

import pytest

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.diagnostics.readiness import HealthComponent


@pytest.fixture
def observed(app_config, temp_db, mock_notifier, monkeypatch):
    copilot = TradingCopilot(app_config, db=temp_db, notifier=mock_notifier)
    rows: list[tuple[HealthComponent, bool, str]] = []

    async def observe(component, success, detail=""):
        rows.append((component, success, detail))

    async def no_recovery():
        return None

    monkeypatch.setattr(copilot.readiness, "observe", observe)
    monkeypatch.setattr(copilot.entry_service, "recover", no_recovery)
    return copilot, rows


async def test_monitor_records_lifetime_failures_in_readiness(observed):
    copilot, rows = observed

    async def failed():
        return ("ConnectionError",)

    copilot.lifetime_service = SimpleNamespace(reconcile=failed)
    await copilot.monitor_positions()
    component, ok, detail = rows[-1]
    assert component == HealthComponent.RECONCILIATION and not ok
    assert "trade_lifetimes:ConnectionError" in detail


async def test_monitor_clean_lifetime_sweep_adds_no_error(observed):
    copilot, rows = observed

    async def clean():
        return ()

    copilot.lifetime_service = SimpleNamespace(reconcile=clean)
    await copilot.monitor_positions()
    assert "trade_lifetimes:" not in rows[-1][2]
