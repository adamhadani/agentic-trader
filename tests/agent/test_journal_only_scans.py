"""Journal-only swing and intraday scans: collect, rank, journal, and nothing else.

``ScanBudget.JOURNAL_ONLY`` collects and ranks exactly as a budgeted scan does (dedup, account risk and
the deterministic ``evaluate_candidate`` with ``use_llm=False``), then journals every native candidate
with outcome ``journal_only``. It reads no shadow ranker, book, ``card_stats`` snapshot, card policy,
news or card budget; it calls no LLM and writes no ``signals`` row and no outbox item. The daemon's
swing job (including its run at start) and the intraday job register it, so neither can spend a slot.
``ScanBudget.NONE`` still means no limits and still sends (``tests/agent/test_scan_budget.py``).
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import yaml

import agentic_trader.agent.copilot as copilot_module
from agentic_trader.agent.calendar import Tier1Kind
from agentic_trader.cli.commands.service import register_intraday_scan, register_swing_scan
from agentic_trader.config import WORKSPACE_ROOT, ScanBudget
from agentic_trader.diagnostics.readiness import HealthComponent
from agentic_trader.execution.durable import EventKind, RankedOutcome, ScanTrigger, WorkKind
from tests.agent.test_dynamic_universe_scan import ranked_events
from tests.agent.test_scan_budget import budget_desk, candidate  # noqa: F401  (budget_desk is a fixture)
from tests.agent.test_scan_drift import drift_desk  # noqa: F401  (a fixture)
from tests.calendar_doubles import lockout_check, union_double
from tests.execution.card_stats_fixtures import FakeCardStats, key_stats, make_snapshot


RANK_ORDER = ["DDD", "CCC", "BBB", "AAA", "EEE"]  # budget_desk's setup_quality, best first


async def journal_only_scan(desk, **kwargs):
    arguments = {
        "use_llm": True,
        "dry_run": False,
        "budget": ScanBudget.JOURNAL_ONLY,
        "trigger": ScanTrigger.SWING_SCAN,
        **kwargs,
    }
    return await desk.run_scan(**arguments)


def llm_flags(desk) -> set[bool]:
    return {c.kwargs["use_llm"] for c in desk.evaluator.evaluate_candidate.await_args_list}


async def test_a_daemon_start_cannot_record_a_signal(budget_desk, temp_db, app_config):  # noqa: F811
    scheduler = MagicMock()
    register_swing_scan(scheduler, budget_desk, app_config, use_llm=True)
    job = scheduler.add_job.call_args
    budget_desk.news_context = AsyncMock()
    budget_desk.book_sizer.prepare = AsyncMock()
    await job.args[0](*job.kwargs["args"], **job.kwargs["kwargs"])
    assert await temp_db.get_recent_signals(limit=10) == []
    assert await temp_db.workflows.list_work(WorkKind.NOTIFICATION) == []
    assert {c.kwargs["use_llm"] for c in budget_desk.evaluator.evaluate_candidate.await_args_list} == {False}
    budget_desk.news_context.for_symbols.assert_not_awaited()
    budget_desk.book_sizer.prepare.assert_not_awaited()
    [event] = await ranked_events(temp_db)
    p = event["payload"]
    assert (p["budget"], p["trigger"]) == ("journal_only", "swing_scan")
    assert {c["outcome"] for c in p["candidates"]} == {"journal_only"} and len(p["candidates"]) == 5


async def test_the_journal_carries_every_ranked_native_candidate_with_no_card_evidence(budget_desk, temp_db):  # noqa: F811
    summary = await journal_only_scan(budget_desk)

    [event] = await ranked_events(temp_db)
    p = event["payload"]
    assert (p["status"], p["gate"], p["scope"]) == ("completed", None, "universe")
    assert [c["contract"] for c in p["candidates"]] == RANK_ORDER
    assert [c["rank"] for c in p["candidates"]] == [1, 2, 3, 4, 5]
    for entry in p["candidates"]:
        assert entry["outcome"] == RankedOutcome.JOURNAL_ONLY
        assert entry["signal_id"] is None
        assert (entry["llm"], entry["news"], entry["shadow"]) == (None, None, None)
        assert entry["book_sizing"]["status"] is None  # no decision: every field null but the configured mode
        assert entry["card_policy"] == {
            "measured_ev": None,
            "n_mature": None,
            "would_withhold": None,
            "n_fetch_failed": None,
        }
    assert p["funnel"]["raw_setups"] == p["funnel"]["admitted"] == 5
    assert (summary["approved"], summary["sent"], summary["runners_up"]) == (5, 0, [])
    assert (summary["budget"], summary["status"]) == ("journal_only", "completed")


async def test_a_journal_only_scan_reads_none_of_the_card_machinery(budget_desk, temp_db, app_config, monkeypatch):  # noqa: F811
    stats = FakeCardStats(make_snapshot(keys=[key_stats("TREND_PULLBACK", "LONG", n_mature=30, mean_r_cost=-0.4)]))
    budget_desk.card_stats = stats
    budget_desk._shadow_blocks = AsyncMock()
    budget_desk._news_blocks = AsyncMock()
    budget_desk.news_context = AsyncMock()
    budget_desk.book_sizer.prepare = AsyncMock()
    budget_desk.db.signals_since = AsyncMock(return_value=[])
    decide = MagicMock()
    monkeypatch.setattr(copilot_module, "decide", decide)
    app_config.universe.dynamic.enabled = False

    # shadow_evidence is set to prove the shadow block is skipped by budget, not only by caller convention.
    await journal_only_scan(budget_desk, shadow_evidence=True)

    assert stats.reads == 0
    decide.assert_not_called()
    budget_desk._shadow_blocks.assert_not_awaited()
    budget_desk._news_blocks.assert_not_awaited()
    budget_desk.news_context.for_symbols.assert_not_awaited()
    budget_desk.book_sizer.prepare.assert_not_awaited()
    budget_desk.db.signals_since.assert_not_awaited()
    assert llm_flags(budget_desk) == {False}
    assert await temp_db.get_recent_signals(limit=10) == []
    [event] = await ranked_events(temp_db)
    assert {c["shadow"] for c in event["payload"]["candidates"]} == {None}


async def test_a_journal_only_scan_never_runs_pead_drift(drift_desk, temp_db):  # noqa: F811
    await journal_only_scan(drift_desk, shadow_evidence=True, scheduled_time_et="10:35")

    assert drift_desk.earnings_drift.prepare_calls == 0
    assert [e for e in await temp_db.workflows.events() if e["kind"] == EventKind.PEAD_DECISION] == []
    assert await temp_db.get_recent_signals(limit=10) == []
    [event] = await ranked_events(temp_db)
    assert {c["contract"] for c in event["payload"]["candidates"]} == set(RANK_ORDER)


async def test_a_journal_only_scan_observes_scan_readiness_and_monitors_positions(budget_desk):  # noqa: F811
    budget_desk.monitor_positions = AsyncMock()

    await journal_only_scan(budget_desk)

    scan_observations = [c for c in budget_desk.readiness.observe.await_args_list if c.args[0] == HealthComponent.SCAN]
    assert [(c.args[1]) for c in scan_observations] == [True]
    budget_desk.monitor_positions.assert_awaited_once_with()


async def test_a_journal_only_scan_leaves_the_session_card_budget_to_the_suggestion_scan(budget_desk, temp_db):  # noqa: F811
    await journal_only_scan(budget_desk)
    assert await temp_db.get_recent_signals(limit=10) == []

    await budget_desk.run_scan(
        use_llm=False, dry_run=False, budget=ScanBudget.FULL, trigger=ScanTrigger.SUGGESTION_SCAN
    )

    assert [s["contract"] for s in await temp_db.get_recent_signals(limit=10)] == ["DDD"]


async def test_a_lockout_gated_journal_only_scan_journals_its_budget(budget_desk, temp_db):  # noqa: F811
    budget_desk.calendar = union_double(
        lockout_check(
            "FOMC Press Conference",
            datetime(2026, 10, 28, 18, 30, tzinfo=UTC),
            kind=Tier1Kind.FOMC_PRESS_CONFERENCE,
        )
    )

    assert await journal_only_scan(budget_desk) is None

    [event] = await ranked_events(temp_db)
    p = event["payload"]
    assert (p["status"], p["budget"], p["trigger"], p["candidates"]) == (
        "macro_lockout",
        "journal_only",
        "swing_scan",
        [],
    )


async def test_the_intraday_job_is_journal_only_restricted_and_never_journals(budget_desk, temp_db, app_config):  # noqa: F811
    symbols = sorted(app_config.contracts)
    app_config.explicit_contracts = tuple(symbols)
    budget_desk.strategy_engine.scan_contract.side_effect = lambda data, **kw: [
        candidate(data.contract, 0.9).model_copy(update={"timeframe": "15m"})
    ]
    budget_desk.run_scan = AsyncMock(wraps=budget_desk.run_scan)
    scheduler = MagicMock()
    register_intraday_scan(scheduler, budget_desk, app_config, use_llm=True)

    await scheduler.add_job.call_args.args[0]()

    kwargs = budget_desk.run_scan.await_args.kwargs
    assert kwargs["budget"] is ScanBudget.JOURNAL_ONLY
    assert (kwargs["timeframe"], kwargs["symbols"]) == ("15m", symbols)
    # The scan really ran and collected five candidates, yet it sent nothing and journaled nothing.
    assert (budget_desk.last_scan_summary["approved"], budget_desk.last_scan_summary["sent"]) == (5, 0)
    assert llm_flags(budget_desk) == {False}
    assert await temp_db.get_recent_signals(limit=10) == []
    assert await ranked_events(temp_db) == []


def test_shipped_config_disables_the_intraday_job():
    assert (
        yaml.safe_load((WORKSPACE_ROOT / "config/config.yaml").read_text())["scheduler"]["intraday_scan_enabled"]
        is False
    )
