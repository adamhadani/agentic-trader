from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agentic_trader.agent.copilot_tools import make_copilot_tools
from agentic_trader.constants import ExecutionMode
from agentic_trader.research.alpha.promotion import AlphaPromotionManager
from agentic_trader.screeners.registry import StrategyRegistry
from agentic_trader.screeners.strategies import StrategyEngine


@pytest.fixture
def mock_copilot():
    copilot = MagicMock()
    copilot.config.execution_mode = ExecutionMode.PAPER
    copilot.config.portfolio.cash = 100_000.0
    copilot.is_halted = False
    copilot.halt_reason = None

    # Setup StrategyEngine and StrategyRegistry on copilot
    registry = StrategyRegistry(auto_load_promoted=False)
    copilot.strategy_engine = StrategyEngine(registry=registry)
    return copilot


@pytest.mark.asyncio
async def test_alpha_copilot_tools_lifecycle(mock_copilot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    temp_yaml = tmp_path / "promoted_alphas.yaml"
    monkeypatch.setattr(
        "agentic_trader.agent.copilot_tools.AlphaPromotionManager",
        lambda *args, **kwargs: AlphaPromotionManager(config_path=temp_yaml),
    )
    tools = {t.name: t for t in make_copilot_tools(mock_copilot)}

    assert "get_alpha_catalog" in tools
    assert "promote_alpha" in tools
    assert "demote_alpha" in tools

    # 1. Inspect catalog
    catalog_res = await tools["get_alpha_catalog"].ainvoke({})
    assert "Formulaic Alpha Intelligence" in catalog_res
    assert "Catalog Library" in catalog_res
    assert "alpha_wq_006" in catalog_res

    # 2. Promote an institutional alpha (alpha_wq_006)
    promote_res = await tools["promote_alpha"].ainvoke(
        {"alpha_id": "alpha_wq_006", "allocation_weight": 0.15, "notes": "Promoted via Copilot tool test"}
    )
    assert "Successfully promoted 'alpha_wq_006'" in promote_res
    assert "15%" in promote_res

    # Check registry dynamically registered the alpha strategy
    assert "alpha_wq_006" in mock_copilot.strategy_engine.registry.list_strategies()

    # 3. Demote the alpha
    demote_res = await tools["demote_alpha"].ainvoke({"alpha_id": "alpha_wq_006", "notes": "Demoted in test"})
    assert "Successfully demoted 'alpha_wq_006'" in demote_res

    # Check unregister from registry
    assert "alpha_wq_006" not in mock_copilot.strategy_engine.registry.list_strategies()
