"""COLLECT -> RANK -> SEND: the per-scan, per-session and per-group suggestion budget.

The desk screens a wide universe but may only send one or two Telegram cards per
session. These tests pin the ranking (by ``setup_quality``), the durable per-session
budget derived from recorded signals, the correlation-group cap, and the bounded
LLM re-evaluation of ranked winners.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from agentic_trader.agent.evaluator import LLMTradeEvaluation
from agentic_trader.config import ScanBudget
from agentic_trader.constants import AssetClass
from agentic_trader.screeners.base import ScreenerCandidate
from agentic_trader.storage.models import SignalRecord


def frame(rows=30):
    return pd.DataFrame({"Close": [100.0] * rows, "Volume": [1000] * rows})


def instrument(symbol):
    return SimpleNamespace(name=symbol, ticker=symbol, asset_class="EQUITY")


def candidate(symbol, quality, strategy="TREND_PULLBACK", direction="LONG"):
    return ScreenerCandidate(
        contract=symbol,
        symbol=symbol,
        asset_class=AssetClass.EQUITY,
        timeframe="4h",
        strategy=strategy,
        direction=direction,
        current_price=100.0,
        ema_20=1.0,
        ema_50=1.0,
        ema_200=1.0,
        rsi_14=50.0,
        atr_14=1.0,
        candle_timestamp="2026-09-22T00:00:00+00:00",
        recent_swing_low=95.0,
        recent_swing_high=105.0,
        trigger_detail="t",
        setup_quality=quality,
    )


def evaluation(candidate_, approved=True):
    return SimpleNamespace(
        approved=approved,
        rejection_reason=None if approved else "risk",
        contract=candidate_.contract,
        direction=candidate_.direction,
        entry_price=100.0,
        stop_loss=98.0,
        take_profit=104.0,
        risk_dollars=2.0,
        reward_dollars=4.0,
        notional_value=100.0,
        asset_class=AssetClass.EQUITY,
        quantity=1.0,
        model_dump_json=lambda: "{}",
        model_dump=lambda mode=None: {},
    )


@pytest.fixture
def budget_desk(scan_desk, app_config):
    app_config.contracts = {s: instrument(s) for s in ("AAA", "BBB", "CCC", "DDD")}
    app_config.portfolio.correlation_groups = {"sector_x": ["AAA", "BBB"], "sector_y": ["CCC"]}
    scan_desk.data_fetcher.fetch_data.side_effect = lambda contract, ticker, include_fifteen_min=True: SimpleNamespace(
        contract=contract, daily=frame(), four_hour=frame(), hourly=frame()
    )
    qualities = {"AAA": 0.9, "BBB": 0.8, "CCC": 0.7, "DDD": 0.6}
    scan_desk.strategy_engine.scan_contract.side_effect = lambda data, **kw: [
        candidate(data.contract, qualities[data.contract])
    ]

    async def evaluate(cand, **kwargs):
        return evaluation(cand)

    scan_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=evaluate)
    scan_desk.db.is_duplicate_recent = AsyncMock(return_value=False)
    return scan_desk


async def test_full_budget_sends_only_the_top_card_and_calls_the_llm_only_for_it(budget_desk, temp_db):
    await budget_desk.run_scan(use_llm=True, dry_run=False, budget=ScanBudget.FULL)
    sent = await temp_db.get_recent_signals(limit=10)
    assert [s["contract"] for s in sent] == ["AAA"]
    calls = budget_desk.evaluator.evaluate_candidate.call_args_list
    assert sum(1 for c in calls if c.kwargs.get("use_llm")) == 1  # deterministic pass for all, LLM for the winner only
    provenance = sent[0]["decision_provenance"]
    assert provenance["setup_quality"] == 0.9 and provenance["rank"] == 1 and provenance["candidates_considered"] == 4
    assert provenance["budget"] == "full"
    assert [r["contract"] for r in budget_desk.last_scan_summary["runners_up"]] == ["BBB", "CCC", "DDD"]


async def test_session_budget_is_derived_from_recorded_signals_and_survives_a_restart(budget_desk, temp_db, app_config):
    app_config.scan.max_cards_per_scan = 5
    await budget_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL)
    assert len(await temp_db.get_recent_signals(limit=10)) == 2  # max_cards_per_session
    budget_desk.last_scan_summary = {}  # "restart": nothing in memory
    await budget_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL)
    assert len(await temp_db.get_recent_signals(limit=10)) == 2


async def test_one_card_per_correlation_group_per_session(budget_desk, temp_db, app_config):
    app_config.scan.max_cards_per_scan = 5
    app_config.scan.max_cards_per_session = 5
    await budget_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL)
    sent = [s["contract"] for s in await temp_db.get_recent_signals(limit=10)]
    assert set(sent) == {"AAA", "CCC", "DDD"}  # BBB shares sector_x with AAA
    assert any(r["contract"] == "BBB" and "group" in r["reason"] for r in budget_desk.last_scan_summary["runners_up"])


async def test_llm_rejection_falls_through_to_the_next_ranked_candidate(budget_desk, temp_db):
    async def evaluate(cand, use_llm=False, **kwargs):
        return evaluation(cand, approved=not (use_llm and cand.contract == "AAA"))

    budget_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=evaluate)
    await budget_desk.run_scan(use_llm=True, dry_run=False, budget=ScanBudget.FULL)
    assert [s["contract"] for s in await temp_db.get_recent_signals(limit=10)] == ["BBB"]


async def test_llm_evaluation_budget_bounds_the_fallthrough(budget_desk, temp_db, app_config):
    app_config.scan.max_llm_evaluations_per_scan = 2

    async def evaluate(cand, use_llm=False, **kwargs):
        return evaluation(cand, approved=not use_llm)

    budget_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=evaluate)
    await budget_desk.run_scan(use_llm=True, dry_run=False, budget=ScanBudget.FULL)
    assert await temp_db.get_recent_signals(limit=10) == []
    assert sum(1 for c in budget_desk.evaluator.evaluate_candidate.call_args_list if c.kwargs.get("use_llm")) == 2


async def test_no_budget_records_every_approved_candidate(budget_desk, temp_db):
    await budget_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.NONE)
    assert len(await temp_db.get_recent_signals(limit=10)) == 4


async def test_session_budget_ignores_the_per_scan_cap(budget_desk, temp_db):
    await budget_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.SESSION)
    assert len(await temp_db.get_recent_signals(limit=10)) == 2


async def test_a_dry_run_never_spends_the_budget_and_records_nothing(budget_desk, temp_db):
    # A dry run prints a real terminal card, so it needs a real evaluation model.
    async def evaluate(cand, **kwargs):
        return LLMTradeEvaluation(
            approved=True,
            contract=cand.contract,
            direction="LONG",
            entry_price=100.0,
            stop_loss=98.0,
            take_profit=104.0,
            stop_distance_points=2.0,
            target_distance_points=4.0,
            risk_reward_ratio=2.0,
            risk_dollars=2.0,
            reward_dollars=4.0,
            notional_value=100.0,
            effective_leverage=1.0,
            macro_clearance=True,
            thesis_summary="dry",
            quantity=1.0,
            asset_class=AssetClass.EQUITY,
        )

    budget_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=evaluate)
    await budget_desk.run_scan(use_llm=False, dry_run=True, budget=ScanBudget.FULL)
    assert await temp_db.get_recent_signals(limit=10) == []
    assert budget_desk.last_scan_summary["approved"] == 4


async def test_signals_from_a_previous_session_do_not_spend_this_session_budget(budget_desk, temp_db):
    stale = budget_desk.session_start_et() - timedelta(days=1)
    async with temp_db.session_factory() as session, session.begin():
        session.add(
            SignalRecord(
                timestamp=stale,
                contract="AAA",
                strategy="TREND_PULLBACK",
                direction="LONG",
                entry_price=100.0,
                stop_loss=98.0,
                take_profit=104.0,
                risk_dollars=2.0,
                environment=temp_db.environment,
                execution_mode=temp_db.execution_mode,
            )
        )
    await budget_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.SESSION)
    # Yesterday's row is outside the session window, so this session still sends two cards.
    assert len(await temp_db.get_recent_signals(limit=10)) == 3


def test_session_start_is_new_york_midnight(scan_desk):
    now = datetime(2026, 9, 22, 18, 30, tzinfo=UTC)  # 14:30 New York
    start = scan_desk.session_start_et(now)
    assert start == datetime(2026, 9, 22, 0, 0, tzinfo=ZoneInfo("America/New_York"))


def test_correlation_groups_match_root_and_slashed_symbols(scan_desk, app_config):
    app_config.portfolio.correlation_groups = {"us_broad_market": ["/MES", "SPY"], "gold": ["GLD"]}
    assert scan_desk.correlation_groups_of("SPY") == {"us_broad_market"}
    assert scan_desk.correlation_groups_of("/MES") == {"us_broad_market"}
    assert scan_desk.correlation_groups_of("MES") == {"us_broad_market"}
    assert scan_desk.correlation_groups_of("AAPL") == set()


async def test_digest_summarises_the_session_and_is_published_once(budget_desk, app_config):
    budget_desk.outbox = AsyncMock()
    await budget_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL)
    text = await budget_desk.publish_scan_digest()
    assert "1 card" in text and "3 runners-up" in text and "BBB" in text
    budget_desk.outbox.publish_message.assert_awaited_once()
    assert budget_desk.outbox.publish_message.call_args.kwargs["key"].startswith("scan-digest/")


async def test_digest_without_scans_says_so(scan_desk):
    scan_desk.outbox = AsyncMock()
    text = await scan_desk.publish_scan_digest()
    assert "no suggestion scans" in text.lower()


async def test_session_scan_stats_keep_only_the_current_new_york_date(budget_desk):
    budget_desk._session_scan_stats["2020-01-01"] = [{"candidates": 99, "sent": 9, "runners_up": []}]
    await budget_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL)
    today = budget_desk.session_start_et().date().isoformat()
    assert list(budget_desk._session_scan_stats) == [today]
