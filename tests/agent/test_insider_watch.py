"""Insider watch cards: the frozen insider v1 cluster rule applied live at the 10:35 suggestion scan.

The source's own decision (``prepare``) runs on synthetic fakes of every provider: the watch history
(archive + captures), the calendar, SIP daily bars and corporate actions. The scan tests plug a source with
a fixed preparation into the PEAD drift desk, so the seam, the journal, both catalog budgets and the native
budget are exercised together. Review Focus items 1-5 of the plan are marked ``RF<n>``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import json
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest
from sqlalchemy import select, update

import agentic_trader.agent.copilot as copilot_module
from agentic_trader.agent.catalog_sources import CatalogCardSource, PeadDriftSource
from agentic_trader.agent.evaluator import LLMTradeEvaluation
from agentic_trader.agent.insider_watch import (
    INSIDER_WATCH_POSITION_CAP_REACHED,
    InsiderWatchSource,
    WatchPreparation,
    build_insider_watch_source,
    rank_events,
)
from agentic_trader.agent.macro import MacroInput
from agentic_trader.agent.regime import RegimeUnavailable
from agentic_trader.constants import AssetClass, SignalStatus
from agentic_trader.data.corporate_actions import CorporateAction, CorporateActionKind, CorporateActionsPage
from agentic_trader.data.edgar_form4 import IndexRow, select_form4
from agentic_trader.data.insider_capture import WatchHistory
from agentic_trader.execution.durable import EventKind, NotificationKind, WorkKind
from agentic_trader.market.session import ET_TZ, DeterministicCalendarProvider
from agentic_trader.notifier.telegram_bot import WATCH_STUDY_LINE, TelegramNotifier, format_alert_card
from agentic_trader.research.alpha.strategy import AprioriBracketPolicy, execution_policy_from_dict
from agentic_trader.research.apriori.watch_contract import (
    INSIDER_WATCH_STRATEGY_ID,
    WATCH_DECISION_TIME_ET,
    frozen_watch_contract,
    load_watch_contract,
)
from agentic_trader.research.insider_filings import InsiderTransaction
from agentic_trader.research.pooled.entry import REPO_ROOT
from agentic_trader.screeners.earnings_drift import PEAD_STRATEGY_ID
from agentic_trader.storage.models import DomainEventRecord, SignalRecord
from tests.agent.test_card_freshness_tap import record_card, tap_desk  # noqa: F401  (tap_desk is a fixture)
from tests.agent.test_evaluator import create_equity_candidate, evaluator_factory  # noqa: F401  (fixture)
from tests.agent.test_scan_budget import candidate
from tests.agent.test_scan_drift import (  # noqa: F401  (budget_desk, drift_desk are fixtures)
    _gate,
    _open_position,
    _signals,
    budget_desk,
    drift_desk,
    suggestion_scan,
    today_ny,
)


CONTRACT = load_watch_contract(REPO_ROOT)
D = date(2026, 10, 21)  # a Wednesday session; D-1 = 2026-10-20
NOW = datetime.combine(D, time(10, 35), tzinfo=ET_TZ)
STUDY_LINE = (
    "Study v1 (2016–2026): +0.087R/trade, not significant on its own; +0.070R vs matched names (significant). "
    "Your call."
)


# --- fakes for the source's own decision -------------------------------------------------------


def buy(symbol: str, filed: date, owner: int, dollars: float = 100_000.0) -> InsiderTransaction:
    return InsiderTransaction(
        symbol=symbol,
        filing_date=filed,
        trans_date=filed - timedelta(days=1),
        code="P",
        dollars=dollars,
        owner_cik=owner,
    )


def history(
    transactions: dict[str, list[InsiderTransaction]],
    *,
    latest: date = D - timedelta(days=1),
    incomplete: dict[str, tuple[date, ...]] | None = None,
) -> WatchHistory:
    return WatchHistory(
        transactions={symbol: tuple(rows) for symbol, rows in transactions.items()},
        identity_breaks={},
        latest_filing_date=latest,
        archive_through=date(2026, 9, 30),
        live_dates=(),
        incomplete=incomplete or {},
        provenance={"entry_sha256": CONTRACT.entry_sha256, "max_trade_rel": 10.0, "document_errors": 0},
    )


def daily(trading: list[date], close: float, volume: float = 1_000_000.0) -> pd.DataFrame:
    index = pd.DatetimeIndex([pd.Timestamp(day).tz_localize(ET_TZ).tz_convert(UTC) for day in trading])
    return pd.DataFrame(
        {"Open": close, "High": close + 1.0, "Low": close - 1.0, "Close": close, "Volume": volume}, index=index
    )


class FakeBars:
    def __init__(self, closes: dict[str, float]):
        self.closes, self.calls = closes, []

    def fetch_daily_many(self, symbols, start, end, *, adjustment):
        self.calls.append((tuple(symbols), start, end, adjustment))
        days = [d for d in pd.bdate_range(start.date(), end.date()).date]
        return {s: daily(days, self.closes[s]) for s in symbols if s in self.closes}


class FakeActions:
    def __init__(self, actions=()):
        self.actions, self.calls = tuple(actions), []

    def fetch(self, symbols, start, end):
        self.calls.append((tuple(symbols), start, end))
        now = datetime.now(UTC)
        return CorporateActionsPage(self.actions, now, now, len(symbols), (start, end), 1, ("p",))


def source_with(seed: WatchHistory, app_config, *, closes=None, actions=()):
    loads: list[tuple[date, ...]] = []

    async def load(trading):
        loads.append(tuple(trading))
        return seed

    app_config.insider_watch.enabled = True
    source = InsiderWatchSource(
        app_config,
        CONTRACT,
        history=load,
        calendar=DeterministicCalendarProvider(),
        bars=FakeBars(closes or {}),
        actions=FakeActions(actions),
    )
    return source, loads


def clusters() -> dict[str, list[InsiderTransaction]]:
    """AAA: 2 buyers; BBB: 3 buyers; CCC: 2 buyers at $5; DDD: 2 buyers with an unparsed filing;
    EEE: 2 buyers, owned; GGG: a cluster whose decision session is 2026-10-14 (outside D's window);
    HHH: one buyer (its second filing a 4/A amendment, which the capture never reads)."""
    d1, d3, d5 = D - timedelta(days=1), D - timedelta(days=5), D - timedelta(days=2)
    return {
        "AAA": [buy("AAA", d5, 1), buy("AAA", d1, 2)],
        "BBB": [buy("BBB", d5, 3), buy("BBB", d1, 4), buy("BBB", d1, 5)],
        "CCC": [buy("CCC", d3, 6), buy("CCC", d1, 7)],
        "DDD": [buy("DDD", d3, 8), buy("DDD", d1, 9)],
        "EEE": [buy("EEE", d3, 10), buy("EEE", d1, 11)],
        "GGG": [buy("GGG", date(2026, 10, 12), 12), buy("GGG", date(2026, 10, 13), 13)],
        "HHH": [buy("HHH", d1, 14)],
        "ZZZ": [],
    }


CLOSES = {s: 50.0 for s in ("AAA", "BBB", "DDD", "EEE", "GGG", "HHH")} | {"CCC": 5.0}


async def test_the_decision_narrows_the_frozen_rule_to_today_and_journals_every_reason(app_config):
    seed = history(clusters(), incomplete={"DDD": (D - timedelta(days=7),)})
    source, loads = source_with(seed, app_config, closes=CLOSES)

    prep = await source.prepare(now=NOW, snapshot=None, owned={"EEE": "open position"})

    assert prep.status == "ok" and prep.session == D
    # The calendar ends at D and reaches back past the cooldown plus the cluster window.
    [trading] = loads
    assert trading[-1] == D and len(trading) > 63 + 21
    # Ranking: buyers desc first (BBB's 3 before AAA's 2).
    assert [e["symbol"] for e in prep.events] == ["BBB", "AAA"]
    bbb = prep.events[0]
    assert (bbb["buyers"], bbb["owners"], bbb["filing_dates"]) == (3, [3, 4, 5], ["2026-10-19", "2026-10-20"])
    assert bbb["session"] == "2026-10-21" and bbb["filing_date"] == "2026-10-20"
    assert bbb["entry_sha256"] == CONTRACT.entry_sha256
    assert bbb["atr"] == pytest.approx(2.0) and bbb["dollar_volume"] == pytest.approx(50_000_000.0)
    assert bbb["opp_rel"] == pytest.approx(300_000.0 / 50_000_000.0)
    # Owned, ineligible and incomplete events are journaled with their reason, never carded.
    assert prep.skipped == {"EEE": "open position"} and [e["symbol"] for e in prep.skipped_events] == ["EEE"]
    assert {e["symbol"]: e["reason"] for e in prep.excluded_events} == {
        "CCC": "ineligible: illiquid_price",
        "DDD": "incomplete filings: 2026-10-14",
    }
    assert prep.incomplete == {"DDD": ["2026-10-14"]}
    # GGG's cluster decided 2026-10-14, not today: the narrowed window excludes it. HHH has one buyer.
    assert prep.counts["excluded"]["outside_window"] == {2026: 1}
    assert "HHH" not in {e["symbol"] for e in (*prep.events, *prep.excluded_events, *prep.skipped_events)}
    assert prep.policy == CONTRACT.policy and prep.latest_filing_date == "2026-10-20"
    assert prep.time_exit_at is not None


async def test_eligibility_reads_daily_bars_through_d_minus_one_only(app_config):
    source, _ = source_with(history(clusters()), app_config, closes=CLOSES)
    await source.prepare(now=NOW, snapshot=None, owned={})
    bars = source.bars
    assert {call[3] for call in bars.calls} == {"raw", "all"}
    assert all(call[2].date() == D - timedelta(days=1) for call in bars.calls)
    # Raw bars (plausibility) for every symbol with a purchase on the calendar; adjusted only for events.
    raw = next(call for call in bars.calls if call[3] == "raw")
    assert "ZZZ" not in raw[0] and "HHH" in raw[0]


async def test_a_missing_symbol_bar_history_is_an_eligibility_reason(app_config):
    source, _ = source_with(history({"AAA": clusters()["AAA"]}), app_config, closes={})
    prep = await source.prepare(now=NOW, snapshot=None, owned={})
    assert prep.events == ()
    assert [e["reason"] for e in prep.excluded_events] == ["ineligible: no_daily_bars"]


async def test_the_plausibility_filter_drops_an_implausible_purchase(app_config):
    rows = clusters()["AAA"]
    rows[1] = dataclasses.replace(rows[1], dollars=600_000_000.0)  # above 10 x $50M dollar volume
    source, _ = source_with(history({"AAA": rows}), app_config, closes=CLOSES)
    prep = await source.prepare(now=NOW, snapshot=None, owned={})
    assert prep.events == () and prep.counts["implausible_size"] == 1


async def test_a_name_change_after_the_filing_is_an_identity_break(app_config):
    rename = CorporateAction(
        id="nc", kind=CorporateActionKind.NAME_CHANGE, symbol="OLD", related_symbol="AAA", effective_on=D
    )
    source, _ = source_with(history({"AAA": clusters()["AAA"]}), app_config, closes=CLOSES, actions=[rename])
    prep = await source.prepare(now=NOW, snapshot=None, owned={})
    assert prep.events == ()
    assert prep.counts["identity_breaks"] == {"AAA": "2026-10-21"}
    assert prep.counts["excluded"]["identity_break"] == {2026: 1}
    # Read from the event's filing date: only a later name change can break it.
    assert source.actions.calls == [(("AAA",), D - timedelta(days=1), D)]


async def test_rf1_a_history_short_of_yesterday_is_unavailable_and_never_decides(app_config):
    source, _ = source_with(history(clusters(), latest=D - timedelta(days=2)), app_config, closes=CLOSES)
    prep = await source.prepare(now=NOW, snapshot=None, owned={})
    assert (prep.status, prep.reason) == ("unavailable", "capture_not_current: latest filing date 2026-10-19")
    assert prep.events == () and source.bars.calls == []


async def test_rf2_a_cluster_completed_only_by_an_amendment_does_not_fire(app_config):
    # The capture selects form "4" rows only: a 4/A never becomes a transaction.
    rows = [
        IndexRow("4", "HHH INC", 1, D - timedelta(days=1), "edgar/data/1/0000000001-26-000001.txt"),
        IndexRow("4/A", "HHH INC", 1, D - timedelta(days=1), "edgar/data/1/0000000001-26-000002.txt"),
    ]
    assert [row.form for row in select_form4(rows, {1})] == ["4"]
    source, _ = source_with(history({"HHH": clusters()["HHH"]}), app_config, closes=CLOSES)
    prep = await source.prepare(now=NOW, snapshot=None, owned={})
    assert prep.status == "ok" and prep.events == () and prep.excluded_events == ()


async def test_the_history_is_loaded_once_per_new_york_date(app_config):
    source, loads = source_with(history(clusters()), app_config, closes=CLOSES)
    await source.prepare(now=NOW, snapshot=None, owned={})
    await source.prepare(now=NOW + timedelta(minutes=5), snapshot=None, owned={})
    assert len(loads) == 1
    await source.prepare(now=NOW + timedelta(days=1), snapshot=None, owned={})
    assert len(loads) == 2


async def test_a_history_seeded_under_another_entry_fails_closed(app_config):
    seed = dataclasses.replace(history(clusters()), provenance={"entry_sha256": "0" * 64, "max_trade_rel": 10.0})
    source, _ = source_with(seed, app_config, closes=CLOSES)
    with pytest.raises(ValueError, match="another insider entry"):
        await source.prepare(now=NOW, snapshot=None, owned={})


def test_ranking_is_buyers_then_relative_dollars_then_symbol():
    events = [
        {"symbol": "B", "buyers": 2, "opp_rel": 0.01},
        {"symbol": "A", "buyers": 2, "opp_rel": 0.01},
        {"symbol": "C", "buyers": 2, "opp_rel": 0.05},
        {"symbol": "D", "buyers": 4, "opp_rel": 0.001},
    ]
    assert [e["symbol"] for e in rank_events(events)] == ["D", "C", "A", "B"]


# --- the contract --------------------------------------------------------------------------------


def test_the_policy_is_the_cube_bracket_through_the_session_lifetime():
    policy = execution_policy_from_dict(CONTRACT.policy)
    assert isinstance(policy, AprioriBracketPolicy)
    assert (policy.stop_atr, policy.atr_window, policy.reward_risk) == (2.0, 14, 3.0)
    assert (policy.lifetime.holding_sessions, policy.lifetime.close_time_et) == (20, "15:45")
    assert CONTRACT.decision_time_et == WATCH_DECISION_TIME_ET == "10:35"
    assert frozen_watch_contract().policy == CONTRACT.policy


def test_the_watch_source_is_a_watch_kind_catalog_source(app_config):
    source = InsiderWatchSource(app_config, CONTRACT)
    assert isinstance(source, CatalogCardSource)
    # Never auto-entered: item 1.4 must exclude every source whose kind is "watch".
    assert (source.kind, PeadDriftSource.kind) == ("watch", "probe")
    assert source.strategy_id == INSIDER_WATCH_STRATEGY_ID != PEAD_STRATEGY_ID
    assert (source.provenance_key, source.journal_kind) == ("insider_watch", EventKind.INSIDER_WATCH_DECISION)
    assert (source.notification_key, PeadDriftSource.notification_key) == ("card_facts", "drift")
    assert (source.news_context, PeadDriftSource.news_context) == (True, False)
    assert inspect.signature(TelegramNotifier.send_signal_alert).parameters.keys() >= {
        source.notification_key,
        PeadDriftSource.notification_key,
    }


def test_the_watch_source_reads_its_limits_from_the_insider_watch_config(app_config):
    source = InsiderWatchSource(app_config, CONTRACT)
    app_config.insider_watch.max_cards_per_session = 3
    app_config.insider_watch.max_open_positions = 5
    assert (source.max_cards_per_session(app_config), source.max_open_positions(app_config)) == (3, 5)


def test_a_source_built_with_its_clients_applies_at_the_decision_time(app_config, temp_db):
    app_config.insider_watch.enabled = True
    source = build_insider_watch_source(
        app_config, temp_db.workflows, repo_root=REPO_ROOT, calendar=object(), clients=lambda: (object(), object())
    )
    assert source.contract == CONTRACT and source.service_error is None
    assert source.applies("10:35") and not source.applies("14:35")
    app_config.insider_watch.enabled = False
    assert not source.applies("10:35")


def test_an_unloadable_contract_leaves_an_unbuilt_source(app_config, tmp_path, temp_db):
    app_config.insider_watch.enabled = True
    source = build_insider_watch_source(
        app_config, temp_db.workflows, repo_root=tmp_path, calendar=None, clients=lambda: (None, None)
    )
    assert source.contract is None and source.service_error.startswith("FileNotFoundError")
    assert not source.applies("10:35")
    prep = source.unbuilt(app_config, None, "10:35", session=D)
    assert prep is not None and prep.status == "unavailable" and prep.reason.startswith("service unavailable:")
    assert source.unbuilt(app_config, None, "14:35", session=D) is None


def test_the_copilot_builds_the_source_only_when_enabled(budget_desk, app_config):  # noqa: F811
    assert budget_desk.insider_watch_source is None
    assert [s.source_id for s in budget_desk.catalog_sources] == ["pead"]


# --- the scan ------------------------------------------------------------------------------------


def watch_event(symbol: str = "WTCH", buyers: int = 2) -> dict[str, Any]:
    return {
        "event_id": f"{symbol}:2026-10-20",
        "symbol": symbol,
        "filing_date": "2026-10-20",
        "session": today_ny().isoformat(),
        "decision_at": "2026-10-21T14:35:00+00:00",
        "buyers": buyers,
        "opp_dollars": 250_000.0,
        "year": 2026,
        "entry_sha256": CONTRACT.entry_sha256,
        "owners": [101, 102],
        "filing_dates": ["2026-10-16", "2026-10-20"],
        "atr": 1.0,
        "dollar_volume": 20_000_000.0,
        "opp_rel": 0.0125,
    }


def watch_candidate(symbol: str = "WTCH"):
    return candidate(symbol, 0.0, strategy=INSIDER_WATCH_STRATEGY_ID).model_copy(
        update={"timeframe": "1d", "catalog_event": watch_event(symbol), "alpha_policy": CONTRACT.policy}
    )


def watch_prep(*symbols: str, status: str = "ok", reason: str | None = None) -> WatchPreparation:
    return WatchPreparation(
        status,
        today_ny(),
        reason=reason,
        events=tuple(watch_event(s) for s in symbols),
        entry_sha256=CONTRACT.entry_sha256,
        policy=CONTRACT.policy,
        latest_filing_date="2026-10-20",
        counts={"triggers": len(symbols)},
        time_exit_at="2026-11-18T20:45:00+00:00",
    )


class FixedWatch(InsiderWatchSource):
    """The real source with its decision fixed: limits, candidates' bookkeeping, facts and journal are real."""

    def __init__(self, config, prep, candidates):
        super().__init__(config, CONTRACT, history=AsyncMock(), calendar=object(), bars=object(), actions=object())
        self.prep, self.candidates, self.prepare_calls, self.owned = prep, candidates, 0, None

    async def prepare(self, *, now, snapshot, owned):
        self.prepare_calls += 1
        self.owned = dict(owned)
        if isinstance(self.prep, BaseException):
            raise self.prep
        return self.prep

    def candidate(self, event, data, preparation):
        return self.candidates.get(event["symbol"], "no intraday price")


@pytest.fixture
def watch_desk(drift_desk, app_config):  # noqa: F811
    app_config.insider_watch.enabled = True
    source = FixedWatch(app_config, watch_prep("WTCH"), {"WTCH": watch_candidate("WTCH")})
    drift_desk.catalog_sources.append(source)
    drift_desk.insider_watch_source = source
    return drift_desk


async def _watch_events(db) -> list[dict]:
    return [e for e in await db.workflows.events() if e["kind"] == EventKind.INSIDER_WATCH_DECISION]


async def _cards(db, strategy) -> list[dict]:
    return [
        n.payload["arguments"]
        for n in await db.workflows.list_work(WorkKind.NOTIFICATION)
        if n.payload["kind"] == NotificationKind.SIGNAL and n.payload["arguments"]["strategy"] == strategy
    ]


async def _event_keys(db, kind) -> list[str | None]:
    async with db.session_factory() as session:
        rows = (await session.scalars(select(DomainEventRecord).where(DomainEventRecord.kind == kind))).all()
        return [row.event_key for row in rows]


async def test_rf4_a_watch_card_is_sent_beside_pead_and_native_with_independent_budgets(
    watch_desk, temp_db, app_config
):
    summary = await suggestion_scan(watch_desk)

    signals = await _signals(temp_db)
    assert sorted(s["contract"] for s in signals) == ["DDD", "WINR", "WTCH"]
    [wtch] = [s for s in signals if s["contract"] == "WTCH"]
    assert (wtch["strategy"], wtch["timeframe"], wtch["alpha_version"], wtch["alpha_policy"]) == (
        INSIDER_WATCH_STRATEGY_ID,
        "1d",
        None,
        CONTRACT.policy,
    )
    provenance = wtch["decision_provenance"]
    assert provenance["insider_watch"] == watch_event("WTCH")
    assert provenance["card_kind"] == "watch" and provenance["paper_probe"] is False
    assert (provenance["rank"], provenance["candidates_considered"]) == (1, 1)
    assert "dynamic" not in provenance and "card_evidence" not in provenance and provenance["book_sizing"] is None
    # PEAD's card carries no card_kind: its provenance is unchanged.
    [winr] = [s for s in signals if s["contract"] == "WINR"]
    assert "card_kind" not in winr["decision_provenance"]

    # The watch candidate got the LLM pass (commentary), outside the LLM budget, after PEAD's.
    llm = [c.args[0].contract for c in watch_desk.evaluator.evaluate_candidate.call_args_list if c.kwargs["use_llm"]]
    assert llm == ["DDD", "WINR", "WTCH"]
    assert summary["insider_watch"] == {"status": "ok", "reason": None, "events": 1, "sent": 1}
    assert summary["drift"]["sent"] == 1 and summary["sent"] == 3

    # Every payload key is a send_signal_alert keyword; the facts ride under card_facts.
    [card] = await _cards(temp_db, INSIDER_WATCH_STRATEGY_ID)
    inspect.signature(TelegramNotifier.send_signal_alert).bind(None, **card)
    assert card["card_facts"] == {
        "kind": "insider_watch",
        "buyers": 2,
        "owners": [101, 102],
        "filing_dates": ["2026-10-16", "2026-10-20"],
        "opp_dollars": 250_000.0,
        "holding_sessions": 20,
        "time_exit_at": "2026-11-18T20:45:00+00:00",
        "risk_cap": app_config.insider_watch.max_risk_per_trade_usd,
    }
    assert card["probe_risk_cap"] is None and "drift" not in card

    [event] = await _watch_events(temp_db)
    assert event["stream"] == f"scan/{today_ny().isoformat()}"
    payload = event["payload"]
    assert (payload["status"], payload["scan_id"], payload["open_watch_positions"]) == (
        "completed",
        summary["scan_id"],
        0,
    )
    assert [(e["symbol"], e["outcome"]) for e in payload["events"]] == [("WTCH", "sent")]
    assert await _event_keys(temp_db, EventKind.INSIDER_WATCH_DECISION) == [
        f"insider_watch_decision/{summary['scan_id']}"
    ]


async def test_the_pead_decision_keeps_its_journal_key(watch_desk, temp_db):
    summary = await suggestion_scan(watch_desk)
    assert await _event_keys(temp_db, EventKind.PEAD_DECISION) == [f"pead_decision/{summary['scan_id']}"]


async def test_the_watch_budget_is_spent_once_per_session_and_never_by_pead_or_native(watch_desk, temp_db, app_config):
    app_config.insider_watch.max_cards_per_session = 1
    watch_desk.insider_watch_source.prep = watch_prep("WTCH", "WTC2")
    watch_desk.insider_watch_source.candidates["WTC2"] = watch_candidate("WTC2")

    await suggestion_scan(watch_desk)

    [event] = await _watch_events(temp_db)
    assert {e["symbol"]: e["outcome"] for e in event["payload"]["events"]} == {
        "WTCH": "sent",
        "WTC2": "insider watch budget spent",
    }
    # PEAD's and the native card were still sent: neither budget is shared.
    assert sorted(s["contract"] for s in await _signals(temp_db)) == ["DDD", "WINR", "WTCH"]


async def test_a_watch_card_recorded_today_spends_only_the_watch_budget(watch_desk, temp_db, app_config):
    app_config.insider_watch.max_cards_per_session = 1
    await temp_db.record_signal(
        contract="OLDW", strategy=INSIDER_WATCH_STRATEGY_ID, direction="LONG", entry_price=10.0, stop_loss=9.0,
        take_profit=13.0, risk_dollars=10.0,
    )  # fmt: skip

    await suggestion_scan(watch_desk)

    [event] = await _watch_events(temp_db)
    assert [e["outcome"] for e in event["payload"]["events"]] == ["insider watch budget spent"]
    assert sorted(s["contract"] for s in await _signals(temp_db)) == ["DDD", "OLDW", "WINR"]


async def test_no_watch_card_while_the_open_watch_positions_are_at_the_cap(watch_desk, temp_db, app_config):
    app_config.insider_watch.max_open_positions = 1
    await _open_position(temp_db, "HELD", strategy=INSIDER_WATCH_STRATEGY_ID)
    watch_desk.monitor_positions = AsyncMock(return_value=0)  # no broker behind the open position

    await suggestion_scan(watch_desk)

    [event] = await _watch_events(temp_db)
    assert event["payload"]["open_watch_positions"] == 1
    assert [e["outcome"] for e in event["payload"]["events"]] == [INSIDER_WATCH_POSITION_CAP_REACHED]
    assert "WTCH" not in {s["contract"] for s in await _signals(temp_db)}


async def test_rf3_a_name_held_as_a_pead_position_is_passed_to_the_source_as_owned(watch_desk, temp_db):
    await _open_position(temp_db, "WTCH", strategy=PEAD_STRATEGY_ID)
    watch_desk.monitor_positions = AsyncMock(return_value=0)

    await suggestion_scan(watch_desk)

    assert watch_desk.insider_watch_source.owned["WTCH"] == "open position"


async def test_rf3_an_owned_event_is_journaled_with_its_reason(app_config, temp_db):
    seed = history({"AAA": clusters()["AAA"]})
    source, _ = source_with(seed, app_config, closes=CLOSES)
    prep = await source.prepare(now=NOW, snapshot=None, owned={"AAA": "open position"})
    decision = SimpleNamespace(scan_id="s1", preparation=prep, outcomes={}, open_positions=0)
    async with temp_db.session_factory() as session, session.begin():
        await temp_db.workflows.lock(session)
        await source.journal(temp_db.workflows, session, decision)
    [event] = await _watch_events(temp_db)
    assert [(e["symbol"], e["outcome"]) for e in event["payload"]["events"]] == [("AAA", "skipped: open position")]


async def test_a_watch_card_gets_the_news_block_and_pead_does_not(watch_desk, temp_db):
    asked: list[list[str]] = []

    async def for_symbols(symbols, *, score_first):
        asked.append(list(symbols))
        return {symbol: {"status": "no_news", "symbol": symbol} for symbol in symbols}

    watch_desk.news_context = SimpleNamespace(for_symbols=for_symbols)

    await suggestion_scan(watch_desk)

    assert "WTCH" in asked[0] and "WINR" not in asked[0]
    news = {
        c.args[0].contract: c.kwargs["news_context"]
        for c in watch_desk.evaluator.evaluate_candidate.call_args_list
        if c.kwargs["use_llm"]
    }
    assert news["WTCH"] == {"status": "no_news", "symbol": "WTCH"} and news["WINR"] is None


async def test_rf1_an_unavailable_session_journals_and_notifies_once_and_sends_no_watch_card(watch_desk, temp_db):
    reason = "capture_not_current: latest filing date 2026-10-19"
    watch_desk.insider_watch_source.prep = watch_prep(status="unavailable", reason=reason)

    summary = await suggestion_scan(watch_desk)

    [event] = await _watch_events(temp_db)
    assert (event["payload"]["status"], event["payload"]["reason"], event["payload"]["events"]) == (
        "unavailable",
        reason,
        [],
    )
    assert summary["insider_watch"]["status"] == "unavailable"
    assert sorted(s["contract"] for s in await _signals(temp_db)) == ["DDD", "WINR"]
    messages = [
        n.payload["arguments"]["text"]
        for n in await temp_db.workflows.list_work(WorkKind.NOTIFICATION)
        if n.payload["kind"] == NotificationKind.MESSAGE
    ]
    assert messages == [
        f"👀 Insider watch: no watch card this session — {reason}. Native and PEAD cards are unaffected."
    ]


async def test_a_failed_preparation_is_unavailable_with_a_type_name(watch_desk, temp_db):
    watch_desk.insider_watch_source.prep = RuntimeError("archive exploded")
    await suggestion_scan(watch_desk)
    [event] = await _watch_events(temp_db)
    assert (event["payload"]["status"], event["payload"]["reason"]) == ("unavailable", "error: RuntimeError")


async def test_both_preparations_share_one_deadline(watch_desk, temp_db, monkeypatch):
    monkeypatch.setattr(copilot_module, "DRIFT_PREPARE_TIMEOUT_SECONDS", 0.3)
    watch_desk.earnings_drift.delay = 0.2  # PEAD alone fits the bound
    original = watch_desk.insider_watch_source.prepare

    async def slow(**kwargs):
        await asyncio.sleep(0.2)  # alone it would fit too; after PEAD it does not
        return await original(**kwargs)

    watch_desk.insider_watch_source.prepare = slow

    await suggestion_scan(watch_desk)

    [watch] = await _watch_events(temp_db)
    assert (watch["payload"]["status"], watch["payload"]["reason"]) == ("unavailable", "timeout")
    assert "WINR" in {s["contract"] for s in await _signals(temp_db)}


@pytest.mark.parametrize("gate", ["halt", "lockout"])
async def test_a_gated_decision_scan_journals_a_skipped_watch_decision(watch_desk, temp_db, gate):
    reason = await _gate(watch_desk, temp_db, gate)

    assert await suggestion_scan(watch_desk) is None

    [event] = await _watch_events(temp_db)
    assert (event["payload"]["status"], event["payload"]["reason"], event["payload"]["events"]) == (
        "skipped",
        reason,
        [],
    )
    assert watch_desk.insider_watch_source.prepare_calls == 0


async def test_the_regime_notice_names_only_pead(watch_desk, temp_db):
    watch_desk.regime_detector.get_regime.side_effect = RegimeUnavailable(
        {"vix": MacroInput("vix", None, "yfinance:^VIX", None, True, "ReadTimeout")}
    )
    watch_desk.earnings_drift = None  # no PEAD decision would have been made

    await suggestion_scan(watch_desk)

    [event] = await _watch_events(temp_db)
    assert (event["payload"]["status"], event["payload"]["reason"]) == ("skipped", "regime unavailable")
    texts = [
        n.payload["arguments"]["text"]
        for n in await temp_db.workflows.list_work(WorkKind.NOTIFICATION)
        if n.payload["kind"] == NotificationKind.MESSAGE
    ]
    assert len(texts) == 1 and "no cards." in texts[0] and "PEAD" not in texts[0]


async def test_a_late_scan_journals_a_skipped_watch_decision(watch_desk, temp_db):
    await suggestion_scan(watch_desk, scheduled_at=datetime.now(UTC) - timedelta(minutes=30))
    [event] = await _watch_events(temp_db)
    assert event["payload"]["status"] == "skipped" and event["payload"]["reason"].startswith("late decision:")


async def test_the_watch_source_decides_only_at_its_decision_time(watch_desk, temp_db):
    await suggestion_scan(watch_desk, scheduled_time_et="14:35")
    assert watch_desk.insider_watch_source.prepare_calls == 0 and await _watch_events(temp_db) == []


# --- the risk cap ------------------------------------------------------------------------------


async def test_a_watch_card_is_sized_within_its_own_risk_cap(evaluator_factory, app_config):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.insider_watch.max_risk_per_trade_usd = 40.0
    watch = create_equity_candidate(price=100.0, atr=1.0, swing_low=99.0).model_copy(
        update={"strategy": INSIDER_WATCH_STRATEGY_ID, "timeframe": "1d", "alpha_policy": CONTRACT.policy}
    )
    native = watch.model_copy(update={"strategy": "TREND_PULLBACK", "alpha_policy": None})

    capped = await evaluator.evaluate_candidate(watch, use_llm=False)
    uncapped = await evaluator.evaluate_candidate(native, use_llm=False)

    assert capped.approved is True
    assert capped.risk_dollars <= 40.0 < uncapped.risk_dollars
    levels = evaluator.calculate_levels_deterministic(watch)
    assert max(tier["risk_dollars"] for tier in levels.sizing_tiers) <= 40.0
    assert "Card risk cap applied: $40" in levels.gating_reasons
    assert evaluator._probe_risk_cap(watch) == 40.0 and evaluator._probe_risk_cap(native) is None


# --- the card -----------------------------------------------------------------------------------


def _evaluation() -> LLMTradeEvaluation:
    return LLMTradeEvaluation(
        approved=True,
        rejection_reason=None,
        contract="WTCH",
        direction="LONG",
        entry_price=50.0,
        stop_loss=46.0,
        take_profit=62.0,
        stop_distance_points=4.0,
        target_distance_points=12.0,
        risk_reward_ratio=3.0,
        risk_dollars=100.0,
        reward_dollars=300.0,
        notional_value=1250.0,
        effective_leverage=0.01,
        macro_clearance=True,
        thesis_summary="LLM commentary (not a gate): t",
        quantity=25.0,
        asset_class=AssetClass.EQUITY,
    )


def test_the_card_carries_the_watch_header_the_cluster_and_the_study_line():
    facts = {
        "kind": "insider_watch",
        "buyers": 3,
        "owners": [101, 102, 103],
        "filing_dates": ["2026-10-16", "2026-10-20"],
        "opp_dollars": 412_000.0,
        "holding_sessions": 20,
        "time_exit_at": "2026-11-18T20:45:00+00:00",
        "risk_cap": 100.0,
    }
    text = format_alert_card(_evaluation(), INSIDER_WATCH_STRATEGY_ID, card_facts=facts)
    assert text.startswith("👀 <b>INSIDER WATCH — unvalidated</b> — risk capped at $100;")
    assert "3 officer/director buyers (owner CIKs 101, 102, 103)" in text
    assert "$412,000 opportunistic open-market purchases · filed 2026-10-16, 2026-10-20" in text
    assert "20-session hold · time exit 15:45 NY on 2026-11-18" in text
    assert STUDY_LINE == WATCH_STUDY_LINE and STUDY_LINE in text
    assert "📋 <b>SETUP:" in text and "PAPER PROBE" not in text  # then the standard levels and buttons


def test_facts_of_another_kind_render_no_watch_block():
    plain = format_alert_card(_evaluation(), INSIDER_WATCH_STRATEGY_ID)
    assert format_alert_card(_evaluation(), INSIDER_WATCH_STRATEGY_ID, card_facts={"buyers": 3}) == plain


async def test_a_real_candidate_is_locked_on_the_frozen_policy(app_config):
    source = InsiderWatchSource(app_config, CONTRACT)
    session = today_ny()
    hourly_at = pd.Timestamp(datetime.combine(session, time(10, 0), tzinfo=ET_TZ)).tz_convert(UTC)
    data = SimpleNamespace(
        hourly=pd.DataFrame({"Close": [51.0]}, index=pd.DatetimeIndex([hourly_at])),
        four_hour=None,
        daily=pd.DataFrame({"Close": [50.0] * 30, "High": [51.0] * 30, "Low": [49.0] * 30}),
    )
    prep = watch_prep("WTCH")
    cand = source.candidate(watch_event("WTCH"), data, prep)
    assert not isinstance(cand, str)
    assert (cand.strategy, cand.alpha_version, cand.alpha_policy, cand.probe) == (
        INSIDER_WATCH_STRATEGY_ID,
        None,
        CONTRACT.policy,
        False,
    )
    assert (cand.current_price, cand.atr_14, cand.timeframe) == (51.0, 1.0, "1d")
    assert cand.trigger_detail.startswith("Insider watch (unvalidated): 2 officer/director buyers")
    assert source.candidate(watch_event("WTCH"), None, prep) == "no intraday price"
    facts = source.card_facts(cand, prep)
    assert json.loads(json.dumps(facts)) == facts  # durable payload


# --- the tap ------------------------------------------------------------------------------------


async def test_rf5_a_tap_on_yesterdays_watch_card_is_refused_as_expired(tap_desk, temp_db):  # noqa: F811
    yesterday_close = datetime.now(UTC) - timedelta(hours=18)
    sid = await record_card(
        temp_db,
        age_seconds=24 * 3600,
        valid_until=yesterday_close,
        alpha_policy=CONTRACT.policy,
        provenance_extra={"insider_watch": watch_event("SPY"), "card_kind": "watch"},
    )
    async with temp_db.session_factory() as session, session.begin():
        await session.execute(
            update(SignalRecord)
            .where(SignalRecord.id == sid)
            .values(strategy=INSIDER_WATCH_STRATEGY_ID, timeframe="1d")
        )

    reply = await tap_desk.execute_signal_by_id(sid)

    assert reply.ok is False and "Next regular open" in reply.text
    assert (await temp_db.get_signal_by_id(sid))["status"] == SignalStatus.EXPIRED
    tap_desk.entry_service.authorize.assert_not_awaited()


def test_an_enabled_copilot_builds_and_appends_the_watch_source(app_config, temp_db, mock_notifier):
    app_config.insider_watch.enabled = True
    app_config.copilot_chat_enabled = False
    desk = copilot_module.TradingCopilot(
        app_config,
        db=temp_db,
        broker=MagicMock(supports_activity_ledger=False, supports_trade_stream=False),
        notifier=mock_notifier,
        entry_service=AsyncMock(),
        alpha_repository=AsyncMock(),
    )
    source = desk.insider_watch_source
    assert [s.source_id for s in desk.catalog_sources] == ["pead", "insider_watch"]
    assert desk.catalog_sources[-1] is source
    # The unit-test environment strips the Alpaca keys, so the clients cannot be built: the contract still
    # loads, and the 10:35 scan journals the decision unavailable rather than going silent.
    assert source.contract == CONTRACT and not source.applies("10:35")
    assert source.service_error == "ValueError: You must supply a method of authentication"
    prep = source.unbuilt(app_config, None, "10:35", session=D)
    assert prep.reason == f"service unavailable: {source.service_error}" and prep.policy == CONTRACT.policy
    assert desk._catalog_strategy_ids() == frozenset({PEAD_STRATEGY_ID, INSIDER_WATCH_STRATEGY_ID})
