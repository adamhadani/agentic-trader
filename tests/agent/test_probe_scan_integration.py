"""End-to-end coverage for the copilot-level paper-probe scan integration: sweep
ordering/best-effort semantics, install_alphas wiring and probe signal provenance.

Models its TradingCopilot construction on tests/agent/test_account_risk.py's
``risk_desk`` fixture (real temp SQLite db, mocked evaluator/session/regime) so the
persisted signal row and its queued outbox notification can be read back for real
rather than asserted against a mock of ``record_signal``.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentic_trader.agent.evaluator import LLMTradeEvaluation
from agentic_trader.constants import AssetClass
from agentic_trader.research.alpha.models import RegistrySnapshot
from agentic_trader.research.alpha.probe import PAPER_PROBE_TAG


def _candidate(*, probe: bool) -> SimpleNamespace:
    """A minimal stand-in for a FormulaicAlphaStrategy-produced ScreenerCandidate,
    exposing only the attributes run_scan's candidate-processing loop reads."""
    return SimpleNamespace(
        contract="SPY",
        direction="LONG",
        strategy="alpha_probe_test",
        current_price=100.0,
        timeframe="4h",
        alpha_version=None,
        alpha_score=None,
        alpha_policy=None,
        contributors=(),
        candle_timestamp="2026-09-21T00:00:00+00:00",
        probe=probe,
    )


def _approved_eval() -> LLMTradeEvaluation:
    return LLMTradeEvaluation(
        approved=True,
        contract="SPY",
        direction="LONG",
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        stop_distance_points=5.0,
        target_distance_points=10.0,
        risk_reward_ratio=2.0,
        risk_dollars=500.0,
        reward_dollars=1000.0,
        notional_value=10_000.0,
        effective_leverage=1.0,
        macro_clearance=True,
        thesis_summary="test",
        quantity=100.0,
        asset_class=AssetClass.EQUITY,
    )


async def test_non_dry_run_sweeps_probes_before_snapshot_and_install(scan_desk):
    """Item 1: sweep_probes() is awaited strictly before snapshot() and install_alphas()."""
    manager = Mock()
    manager.attach_mock(scan_desk.alpha_repository.sweep_probes, "sweep_probes")
    manager.attach_mock(scan_desk.alpha_repository.snapshot, "snapshot")
    manager.attach_mock(scan_desk.strategy_engine.registry.install_alphas, "install_alphas")

    await scan_desk.run_scan(use_llm=False, dry_run=False, symbols=["SPY"])

    called_order = [c[0] for c in manager.mock_calls if c[0] in ("sweep_probes", "snapshot", "install_alphas")]
    assert called_order == ["sweep_probes", "snapshot", "install_alphas"]


async def test_dry_run_scan_never_sweeps_but_still_installs_the_snapshot(scan_desk):
    """Item 2: dry_run=True skips sweep_probes(), while snapshot/install still run."""
    await scan_desk.run_scan(use_llm=False, dry_run=True, symbols=["SPY"])

    scan_desk.alpha_repository.sweep_probes.assert_not_awaited()
    scan_desk.alpha_repository.snapshot.assert_awaited_once()
    scan_desk.strategy_engine.registry.install_alphas.assert_called_once()


async def test_sweep_failure_is_logged_and_swallowed_so_the_scan_still_proceeds(scan_desk, caplog):
    """Item 3 (first half): a sweep_probes() failure is best-effort — logged, not fatal."""
    scan_desk.alpha_repository.sweep_probes.side_effect = RuntimeError("sweep boom")

    with caplog.at_level("ERROR"):
        await scan_desk.run_scan(use_llm=False, dry_run=False, symbols=["SPY"])

    assert any(r.__dict__.get("event") == "probe_sweep_failed" for r in caplog.records)
    scan_desk.alpha_repository.snapshot.assert_awaited_once()
    scan_desk.strategy_engine.registry.install_alphas.assert_called_once()


async def test_a_failure_from_something_else_is_not_swallowed_by_the_sweep_guard(scan_desk):
    """Item 3 (second half): only the sweep_probes() call is guarded; snapshot() raising
    must propagate, and install_alphas must never be reached."""
    scan_desk.alpha_repository.snapshot.side_effect = RuntimeError("snapshot boom")

    with pytest.raises(RuntimeError, match="snapshot boom"):
        await scan_desk.run_scan(use_llm=False, dry_run=False, symbols=["SPY"])

    scan_desk.alpha_repository.sweep_probes.assert_awaited_once()
    scan_desk.strategy_engine.registry.install_alphas.assert_not_called()


async def test_install_alphas_receives_the_active_and_probe_tuples_from_the_snapshot(scan_desk):
    """Item 4: install_alphas(snapshot.active, snapshot.probe), not just snapshot.active."""
    scan_desk.alpha_repository.snapshot.return_value = RegistrySnapshot(1, ("ACTIVE_DEF",), (), ("PROBE_DEF",))

    await scan_desk.run_scan(use_llm=False, dry_run=False, symbols=["SPY"])

    scan_desk.strategy_engine.registry.install_alphas.assert_called_once_with(("ACTIVE_DEF",), ("PROBE_DEF",))


@pytest.mark.parametrize("probe", [True, False])
async def test_probe_signal_provenance_and_notification_risk_cap(scan_desk, app_config, probe):
    """Item 5: an approved candidate's persisted decision_provenance carries
    paper_probe=<candidate.probe>, and its queued notification's arguments carry
    probe_risk_cap=config.alpha_pipeline.probe_risk_dollars only when probe is True.

    Reads both the signal row and the queued notification back from the real
    temp SQLite database rather than asserting on a mock of record_signal.
    """
    scan_desk.strategy_engine.scan_contract.return_value = [_candidate(probe=probe)]
    scan_desk.evaluator.evaluate_candidate.return_value = _approved_eval()

    # Spy on the real record_signal to capture the persisted row's id without
    # assuming anything about autoincrement numbering.
    original_record_signal = scan_desk.db.record_signal
    recorded_ids: list[int] = []

    async def _spy_record_signal(*args, **kwargs):
        sig_id = await original_record_signal(*args, **kwargs)
        recorded_ids.append(sig_id)
        return sig_id

    scan_desk.db.record_signal = _spy_record_signal

    await scan_desk.run_scan(use_llm=False, dry_run=False, symbols=["SPY"])

    assert len(recorded_ids) == 1
    stored = await scan_desk.db.get_signal_by_id(recorded_ids[0])
    assert stored is not None
    assert stored["decision_provenance"][PAPER_PROBE_TAG] is probe

    item = await scan_desk.db.workflows.claim_notification(lease_seconds=30.0, max_attempts=5)
    assert item is not None
    expected_cap = app_config.alpha_pipeline.probe_risk_dollars if probe else None
    assert item.payload["arguments"]["probe_risk_cap"] == expected_cap
