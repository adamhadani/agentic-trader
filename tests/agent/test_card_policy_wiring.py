"""The default card policy reaches a real TradingCopilot and changes no scan result."""

from agentic_trader.config import CardPolicyConfig, ScanBudget
from tests.agent.test_scan_budget import budget_desk  # noqa: F401  (fixture)


async def test_the_default_policy_leaves_the_scan_unchanged(budget_desk, temp_db, app_config):  # noqa: F811
    """Integration: the fixture config loads the default block; a real TradingCopilot on temp_db still sends the top card."""
    assert app_config.card_policy == CardPolicyConfig()
    await budget_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL)
    assert [s["contract"] for s in await temp_db.get_recent_signals(limit=10)] == ["DDD"]
