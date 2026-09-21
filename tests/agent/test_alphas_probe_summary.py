"""Copilot glue for /alphas: pass the probe forward report through to the
dashboard formatter, and never fail the dashboard because probe detail failed.

Models its TradingCopilot construction on tests/agent/test_probe_scan_integration.py's
``scan_desk`` fixture (real temp db, mocked broker/notifier), but with a real
``AlphaRepository`` (rather than a full mock) scoped to the Alpaca paper account
so an actual probe can be enrolled and reported on.
"""

from unittest.mock import MagicMock

import pytest

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.config import AppConfig
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.db import SignalDatabase
from tests.research.probe_fixtures import make_definition, seed


@pytest.fixture
async def probe_repository(tmp_path):
    db = SignalDatabase(
        db_path=str(tmp_path / "probe.db"), config=AppConfig(execution_mode="alpaca", alpaca_paper=True)
    )
    await db.init_db()
    yield AlphaRepository(db.workflows)
    await db.engine.dispose()


@pytest.fixture
def alphas_copilot(app_config, temp_db, mock_notifier, probe_repository):
    return TradingCopilot(
        app_config,
        db=temp_db,
        broker=MagicMock(supports_activity_ledger=True),
        notifier=mock_notifier,
        alpha_repository=probe_repository,
    )


async def test_alphas_summary_passes_the_probe_forward_report_through(alphas_copilot, probe_repository):
    definition = make_definition("alpha_probe_summary", "AAPL")
    await seed(probe_repository, definition)
    await probe_repository.enrol_probe(definition.version_id, actor="op", expected_generation=0)

    html = await alphas_copilot.get_alphas_summary_html()

    assert "alpha_probe_summary" in html
    assert "d left" in html


async def test_alphas_summary_renders_without_probe_detail_when_the_report_fails(
    alphas_copilot, probe_repository, monkeypatch, caplog
):
    definition = make_definition("alpha_probe_summary", "AAPL")
    await seed(probe_repository, definition)
    await probe_repository.enrol_probe(definition.version_id, actor="op", expected_generation=0)

    async def boom(*args, **kwargs):
        raise RuntimeError("probe report boom")

    monkeypatch.setattr(probe_repository, "probe_report", boom)

    with caplog.at_level("ERROR"):
        html = await alphas_copilot.get_alphas_summary_html()

    assert "ALPHA RESEARCH" in html
    assert "1 paper probe" in html
    assert "d left" not in html
    assert any(r.__dict__.get("event") == "probe_report_failed" for r in caplog.records)
