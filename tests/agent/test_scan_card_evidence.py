"""run_scan reads the card-statistics snapshot once and every native card carries its evidence."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from agentic_trader.config import ScanBudget, load_config
from agentic_trader.execution.durable import WorkKind
from agentic_trader.notifier.outbox import NotificationDispatcher
from agentic_trader.notifier.telegram_bot import TelegramNotifier
from tests.agent.test_scan_budget import budget_desk  # noqa: F401  (fixture)
from tests.execution.card_stats_fixtures import FakeCardStats, make_snapshot, real_evaluation


async def test_a_sent_card_carries_its_evidence_through_the_real_outbox(budget_desk, temp_db, capsys):  # noqa: F811
    stats = FakeCardStats(make_snapshot())
    budget_desk.card_stats = stats
    budget_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=lambda cand, **kwargs: real_evaluation(cand))

    await budget_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL)

    assert stats.reads == 1  # once per scan, never per candidate
    [signal] = await temp_db.get_recent_signals(limit=10)
    evidence = signal["decision_provenance"]["card_evidence"]
    assert evidence["status"] == "measured" and evidence["n_mature"] == 30
    assert evidence["snapshot_key"] == stats.snapshot.snapshot_key
    [item] = await temp_db.workflows.list_work(WorkKind.NOTIFICATION)
    assert item.payload["arguments"]["card_evidence"] == evidence

    notifier = TelegramNotifier(bot_token="test_token", chat_id="123456", db=temp_db)
    send = AsyncMock(return_value=SimpleNamespace(message_id=9))
    notifier.app = SimpleNamespace(bot=SimpleNamespace(send_message=send))
    assert await NotificationDispatcher(temp_db.workflows, notifier, load_config().execution).dispatch_one()
    text = send.await_args.kwargs["text"]
    since = stats.snapshot.window_start.isoformat()
    assert "📋 <b>SETUP: 1 shares DDD (LONG)</b>" in text
    assert f"• <b>Measured record</b> (TREND_PULLBACK, LONG): 30 mature cards since {since}:" in text
    assert "• <b>Implied EV at 2.0:1:</b> -0.25R" in text
    assert f"• Measured record (TREND_PULLBACK, LONG): 30 mature cards since {since}:" in capsys.readouterr().out


async def test_a_failed_statistics_read_never_blocks_a_card(budget_desk, temp_db):  # noqa: F811
    budget_desk.card_stats = FakeCardStats(error=RuntimeError("journal down"))
    await budget_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL)
    [signal] = await temp_db.get_recent_signals(limit=10)
    assert signal["contract"] == "DDD"
    assert signal["decision_provenance"]["card_evidence"]["status"] == "unavailable"
    assert budget_desk.last_scan_summary["card_stats_error"] == "RuntimeError: journal down"


async def test_dry_run_cards_state_no_statistics_in_this_scope(budget_desk, temp_db, capsys):  # noqa: F811
    # The real repository on the empty test database, as a dry scan's empty temporary database.
    budget_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=lambda cand, **kwargs: real_evaluation(cand))
    await budget_desk.run_scan(use_llm=False, dry_run=True, budget=ScanBudget.FULL)
    out = capsys.readouterr().out
    assert out.count("• Measured record: no statistics in this scope") == 5
    assert out.count("📋 SETUP:") == 5
