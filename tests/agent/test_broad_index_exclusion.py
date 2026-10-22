"""Broad-index ETF exclusion: an unrestricted scan never sends a native card on a broad US equity index ETF.

A card-selection rule in ``run_scan`` (``scan.broad_index_etfs``), not an entry-risk rule. It is checked first in
the send loop, before every budget refusal, and the excluded names stay collected, ranked and journaled with the
fixed outcome ``broad_index_excluded``. They get no LLM evaluation, news fetch or score, book-sizing input or
card budget. A restricted scan, a policy-locked card and a journal-only scan are unaffected.
"""

from unittest.mock import AsyncMock

import pytest

from agentic_trader.agent.copilot import BROAD_INDEX_REASON
from agentic_trader.config import ScanBudget, UniverseConfig, UniverseEntry
from agentic_trader.execution.durable import RankedOutcome, ScanTrigger
from agentic_trader.screeners.dynamic_universe import ScreenerEntry
from tests.agent.test_dynamic_universe_scan import (  # noqa: F401  (dailies and dynamic_desk are fixtures)
    STATIC,
    FakeSource,
    asset,
    dailies,
    daily_frame,
    dynamic_desk,
    ranked_events,
)
from tests.agent.test_scan_budget import budget_desk, candidate, instrument  # noqa: F401  (a fixture)


QUALITIES = {"SPY": 0.95, "VOO": 0.9, "AAA": 0.8}


@pytest.fixture
def index_desk(dynamic_desk, app_config, dailies):  # noqa: F811
    """Static SPY and dynamic VOO outrank the native AAA, with the rule opted in (the fixture config ships it off)."""
    app_config.scan.broad_index_etfs = ("SPY", "VOO")
    app_config.universe = UniverseConfig(
        groups={"test": [UniverseEntry(symbol=s, sector="technology") for s in (*STATIC, "SPY")]}, max_symbols=10
    )
    app_config.universe.dynamic.enabled = True
    app_config.contracts = {**app_config.contracts, "SPY": instrument("SPY")}
    dailies["SPY"] = daily_frame()
    dailies["VOO"] = daily_frame(volume=2_000_000.0)
    dynamic_desk.strategy_engine.scan_contract.side_effect = lambda data, **kw: (
        [candidate(data.contract, QUALITIES[data.contract])] if data.contract in QUALITIES else []
    )
    dynamic_desk.dynamic_universe = FakeSource(
        [ScreenerEntry(symbol="VOO", source="most_actives", rank=0, price=None, percent_change=None)],
        {"VOO": asset("VOO")},
    )
    dynamic_desk.news_context = AsyncMock()
    dynamic_desk.news_context.for_symbols.return_value = {}
    dynamic_desk.book_sizer.prepare = AsyncMock(return_value=None)
    return dynamic_desk


async def universe_scan(desk, **kwargs):
    arguments = {
        "use_llm": True,
        "dry_run": False,
        "asset_class": "equity",
        "budget": ScanBudget.FULL,
        "shadow_evidence": True,
        "trigger": ScanTrigger.SUGGESTION_SCAN,
        **kwargs,
    }
    return await desk.run_scan(**arguments)


async def journaled_outcomes(db) -> dict[str, str]:
    [event] = await ranked_events(db)
    return {c["contract"]: c["outcome"] for c in event["payload"]["candidates"]}


def llm_contracts(desk) -> list[str]:
    return [c.args[0].contract for c in desk.evaluator.evaluate_candidate.await_args_list if c.kwargs["use_llm"]]


def runner_up_reasons(desk) -> dict[str, str]:
    return {r["contract"]: r["reason"] for r in desk.last_scan_summary["runners_up"]}


async def test_static_spy_and_dynamic_voo_ranked_first_are_excluded_and_the_next_native_is_sent(index_desk, temp_db):
    await universe_scan(index_desk)

    assert await journaled_outcomes(temp_db) == {
        "SPY": RankedOutcome.BROAD_INDEX_EXCLUDED,
        "VOO": RankedOutcome.BROAD_INDEX_EXCLUDED,
        "AAA": RankedOutcome.SENT,
    }
    assert RankedOutcome.BROAD_INDEX_EXCLUDED == "broad_index_excluded"
    assert [s["contract"] for s in await temp_db.get_recent_signals(limit=10)] == ["AAA"]
    assert llm_contracts(index_desk) == ["AAA"]  # no LLM evaluation is spent on the excluded two
    assert runner_up_reasons(index_desk) == {"SPY": BROAD_INDEX_REASON, "VOO": BROAD_INDEX_REASON}
    assert BROAD_INDEX_REASON == "broad index ETF: excluded from native cards (scan.broad_index_etfs)"
    # No news fetch or score, and no book-sizing input, on a name that cannot be carded.
    [news_call] = index_desk.news_context.for_symbols.await_args_list
    assert news_call.args[0] == ["AAA"]
    assert index_desk.book_sizer.prepare.await_args.kwargs["candidates"] == ["AAA"]
    # Excluded names are still collected, ranked and journaled.
    [event] = await ranked_events(temp_db)
    assert [(c["contract"], c["rank"]) for c in event["payload"]["candidates"]] == [("SPY", 1), ("VOO", 2), ("AAA", 3)]
    assert event["payload"]["funnel"]["admitted"] == 3


async def test_the_exclusion_comes_before_every_budget_refusal(index_desk, app_config, temp_db):
    app_config.scan.max_cards_per_scan = 0

    await universe_scan(index_desk)

    assert await journaled_outcomes(temp_db) == {
        "SPY": "broad_index_excluded",
        "VOO": "broad_index_excluded",
        "AAA": "per-scan budget spent",
    }
    assert await temp_db.get_recent_signals(limit=10) == []


@pytest.mark.parametrize(
    "scan",
    [
        {"budget": ScanBudget.FULL},
        {"budget": ScanBudget.SESSION},
        {"budget": ScanBudget.NONE},
        {"dry_run": True},
    ],
    ids=["full", "session", "none", "dry-run"],
)
async def test_every_unrestricted_budget_and_a_dry_run_exclude_a_listed_index(index_desk, temp_db, scan):
    index_desk.strategy_engine.scan_contract.side_effect = lambda data, **kw: (
        [candidate("SPY", QUALITIES["SPY"])] if data.contract == "SPY" else []
    )

    await universe_scan(index_desk, **scan)

    assert runner_up_reasons(index_desk) == {"SPY": BROAD_INDEX_REASON}
    assert llm_contracts(index_desk) == []
    assert await temp_db.get_recent_signals(limit=10) == []


async def test_a_restricted_scan_still_cards_a_listed_index(index_desk, temp_db):
    await index_desk.run_scan(use_llm=False, dry_run=False, symbols=["SPY"], budget=ScanBudget.SESSION)

    assert [s["contract"] for s in await temp_db.get_recent_signals(limit=10)] == ["SPY"]
    assert index_desk.last_scan_summary["runners_up"] == []


@pytest.mark.parametrize(
    "lock",
    [{"alpha_version": "alpha:x:v3"}, {"alpha_policy": {"id": "alpha_x"}}, {"probe": True}],
    ids=["alpha-version", "alpha-policy", "probe"],
)
async def test_a_policy_locked_alpha_or_probe_card_on_spy_is_unaffected(index_desk, temp_db, lock):
    native = index_desk.strategy_engine.scan_contract.side_effect
    index_desk.strategy_engine.scan_contract.side_effect = lambda data, **kw: [
        item.model_copy(update=lock) if item.contract == "SPY" else item for item in native(data, **kw)
    ]

    await universe_scan(index_desk)

    assert [s["contract"] for s in await temp_db.get_recent_signals(limit=10)] == ["SPY"]
    assert await journaled_outcomes(temp_db) == {
        "SPY": "sent",
        "VOO": "broad_index_excluded",  # a native name beside it is still excluded
        "AAA": "per-scan budget spent",
    }
    assert llm_contracts(index_desk) == ["SPY"]


async def test_an_empty_list_turns_the_rule_off(index_desk, app_config, temp_db):
    app_config.scan.broad_index_etfs = ()

    await universe_scan(index_desk)

    assert [s["contract"] for s in await temp_db.get_recent_signals(limit=10)] == ["SPY"]
    assert "broad_index_excluded" not in (await journaled_outcomes(temp_db)).values()
    assert BROAD_INDEX_REASON not in runner_up_reasons(index_desk).values()
    assert index_desk.news_context.for_symbols.await_args.args[0] == ["SPY", "VOO", "AAA"]


async def test_a_journal_only_scan_keeps_every_native_candidate_journal_only(index_desk, temp_db):
    """JOURNAL_ONLY wins: its send loop is skipped, so the exclusion is neither computed nor applied."""
    await index_desk.run_scan(
        use_llm=True, dry_run=False, budget=ScanBudget.JOURNAL_ONLY, trigger=ScanTrigger.SWING_SCAN
    )

    assert await journaled_outcomes(temp_db) == {"SPY": "journal_only", "AAA": "journal_only"}
    assert index_desk.last_scan_summary["runners_up"] == []
    assert await temp_db.get_recent_signals(limit=10) == []
