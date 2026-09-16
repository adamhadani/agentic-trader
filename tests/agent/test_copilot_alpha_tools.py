from unittest.mock import MagicMock

import pytest

from agentic_trader.agent.copilot_tools import make_copilot_tools
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.storage.alpha import AlphaRepository


@pytest.fixture
async def alpha_tools(temp_db):
    await temp_db.init_db()
    copilot = MagicMock()
    copilot.alpha_repository = AlphaRepository(temp_db.workflows)
    definition = AlphaDefinition("alpha_tool", "Tool", "close")
    await copilot.alpha_repository.register(definition, actor="test")
    await copilot.alpha_repository.set_shadow(definition.version_id, actor="test", expected_generation=0)
    yield {tool.name: tool for tool in make_copilot_tools(copilot)}, definition
    await temp_db.engine.dispose()


async def test_chat_cannot_bypass_qualification(alpha_tools):
    tools, definition = alpha_tools
    result = await tools["promote_alpha"].ainvoke({"version_id": definition.version_id, "expected_generation": 1})
    assert "Promotion rejected" in result and "qualification" in result
    result = await tools["demote_alpha"].ainvoke({"version_id": definition.version_id, "expected_generation": 1})
    assert "Existing positions remain protected" in result


async def test_catalog_reports_actual_registry(alpha_tools):
    tools, _ = alpha_tools
    result = await tools["get_alpha_catalog"].ainvoke({})
    assert "Active: 0; shadow: 1" in result
    assert "alpha_wq_006" in result
