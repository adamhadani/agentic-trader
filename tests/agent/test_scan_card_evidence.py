"""run_scan reads the card-statistics snapshot once and every native card carries its evidence."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from agentic_trader.config import ScanBudget, load_config
from agentic_trader.execution.durable import WorkKind
from agentic_trader.notifier.outbox import NotificationDispatcher
from agentic_trader.notifier.telegram_bot import TelegramNotifier
from tests.agent.test_scan_budget import budget_desk  # noqa: F401  (fixture)
from tests.execution.card_stats_fixtures import FakeCardStats, key_stats, make_snapshot, real_evaluation
from tests.execution.test_card_evidence import plant_snapshot_payload


def payload_key(payload):
    return f"card_stats/net_excess_r_v1/{payload['window_end']}"


async def test_a_sent_card_carries_its_evidence_through_the_real_outbox(budget_desk, temp_db, capsys):  # noqa: F811
    stats = FakeCardStats(make_snapshot())
    budget_desk.card_stats = stats
    budget_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=lambda cand, **kwargs: real_evaluation(cand))

    await budget_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL)

    assert stats.reads == 1  # once per scan, never per candidate
    [signal] = await temp_db.get_recent_signals(limit=10)
    evidence = signal["decision_provenance"]["card_evidence"]
    assert evidence["status"] == "measured" and evidence["n_mature"] == 23
    assert evidence["snapshot_key"] == stats.snapshot.snapshot_key
    [item] = await temp_db.workflows.list_work(WorkKind.NOTIFICATION)
    assert item.payload["arguments"]["card_evidence"] == evidence

    notifier = TelegramNotifier(bot_token="test_token", chat_id="123456", db=temp_db)
    send = AsyncMock(return_value=SimpleNamespace(message_id=9))
    notifier.app = SimpleNamespace(bot=SimpleNamespace(send_message=send))
    assert await NotificationDispatcher(temp_db.workflows, notifier, load_config().execution).dispatch_one()
    text = send.await_args.kwargs["text"]
    since = "2026-09-24"  # the key's first journaled decision (New York date), not the window start
    assert "📋 <b>SETUP: 1 shares DDD (LONG)</b>" in text
    assert "• <b>Expected excess</b> (TREND_PULLBACK 4h, LONG): -0.06R ± 0.10 vs beta-matched SPY after cost" in text
    assert f"over 23 mature since {since} (43% target / 52% stop / 4% timeout)" in text
    assert "• Expected excess (TREND_PULLBACK 4h, LONG): -0.06R ± 0.10" in capsys.readouterr().out


async def test_a_failed_statistics_read_never_blocks_a_card(budget_desk, temp_db):  # noqa: F811
    budget_desk.card_stats = FakeCardStats(error=RuntimeError("journal down"))
    await budget_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL)
    [signal] = await temp_db.get_recent_signals(limit=10)
    assert signal["contract"] == "DDD"
    assert signal["decision_provenance"]["card_evidence"]["status"] == "unavailable"
    assert budget_desk.last_scan_summary["card_stats_error"] == "RuntimeError: journal down"


async def test_a_non_finite_stored_snapshot_is_no_evidence_and_the_card_still_sends(budget_desk, temp_db):  # noqa: F811
    """A ``"NaN"`` string in the journal parses as a float NaN: the real repository refuses it and the card sends."""
    payload = make_snapshot().model_dump(mode="json")
    payload["keys"][0]["posterior_mean"] = "NaN"
    await plant_snapshot_payload(temp_db, payload, key=payload_key(payload))

    await budget_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL)

    [signal] = await temp_db.get_recent_signals(limit=10)
    assert signal["contract"] == "DDD"
    assert signal["decision_provenance"]["card_evidence"]["status"] == "unavailable"


async def test_dry_run_cards_state_no_statistics_in_this_scope(budget_desk, temp_db, capsys):  # noqa: F811
    # The real repository on the empty test database, as a dry scan's empty temporary database.
    budget_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=lambda cand, **kwargs: real_evaluation(cand))
    await budget_desk.run_scan(use_llm=False, dry_run=True, budget=ScanBudget.FULL)
    out = capsys.readouterr().out
    assert out.count("• Measured record: no statistics in this scope") == 5
    assert out.count("📋 SETUP:") == 5


async def test_scan_passes_timeframe_to_lookup(budget_desk, temp_db):  # noqa: F811
    """Two rows differing only in timeframe: the card states the row of its own candidate's timeframe."""
    budget_desk.card_stats = FakeCardStats(
        make_snapshot(
            keys=[key_stats(timeframe="4h", posterior_mean=-0.06), key_stats(timeframe="1h", posterior_mean=0.77)]
        )
    )
    budget_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=lambda cand, **kwargs: real_evaluation(cand))

    await budget_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL)

    [signal] = await temp_db.get_recent_signals(limit=10)
    evidence = signal["decision_provenance"]["card_evidence"]
    assert evidence["timeframe"] == "4h" and evidence["posterior_mean"] == -0.06
