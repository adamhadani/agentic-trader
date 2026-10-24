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
import threading
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest
from sqlalchemy import select, update

import agentic_trader.agent.copilot as copilot_module
import agentic_trader.agent.insider_watch as insider_watch_module
from agentic_trader.agent.catalog_sources import CatalogCardSource, PeadDriftSource
from agentic_trader.agent.evaluator import LLMTradeEvaluation
from agentic_trader.agent.insider_watch import (
    INSIDER_WATCH_POSITION_CAP_REACHED,
    InsiderWatchSource,
    LiveWatchHistory,
    PlausibilityCacheMissing,
    WatchArchiveState,
    WatchPreparation,
    archive_implausible,
    build_insider_watch_source,
    rank_events,
    read_study_raw_bars,
)
from agentic_trader.agent.macro import MacroInput
from agentic_trader.agent.regime import RegimeUnavailable
from agentic_trader.constants import AssetClass, ExecutionMode, SignalStatus
from agentic_trader.data.corporate_actions import CorporateAction, CorporateActionKind, CorporateActionsPage
from agentic_trader.data.edgar_form4 import IndexRow, select_form4
from agentic_trader.data.insider_capture import WatchHistory
from agentic_trader.execution.durable import EventKind, NotificationKind, WorkKind
from agentic_trader.execution.lifetime_policy import NATIVE_ENTRY_LIFETIME_KEY
from agentic_trader.market.session import ET_TZ, DeterministicCalendarProvider, MarketHolidayCalendar
from agentic_trader.notifier.telegram_bot import (
    WATCH_STUDY_LINE,
    TelegramNotifier,
    format_alert_card,
    format_terminal_card,
)
from agentic_trader.research.alpha.strategy import AprioriBracketPolicy, execution_policy_from_dict
from agentic_trader.research.apriori.watch_contract import (
    INSIDER_WATCH_STRATEGY_ID,
    WATCH_CALENDAR_SESSIONS,
    WATCH_CALENDAR_SHA256,
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
    "Study v1 (2016–2026): +0.087R/trade, not significant on its own; "
    "+0.070R vs matched names (90% CI excludes 0). Your call."
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


class FakeHistory:
    """The per-process archive state (the study's plausibility drops) and the per-date merged history."""

    def __init__(
        self, seed: WatchHistory, *, implausible=frozenset(), error: Exception | None = None, checked: bool = True
    ):
        self.seed, self.implausible, self.error, self.checked = seed, frozenset(implausible), error, checked
        self.loads: list[tuple[date, ...]] = []
        self.archive_calls: list[tuple[date, ...]] = []

    async def archive(self, prefix):
        self.archive_calls.append(tuple(prefix))
        if self.error is not None:
            raise self.error
        if not self.checked:
            return WatchArchiveState(implausible=frozenset(), provenance={"plausibility": "not_checked"})
        provenance = {"plausibility": "study_bars", "symbols_checked": 3, "implausible": len(self.implausible)}
        return WatchArchiveState(implausible=self.implausible, provenance=provenance)

    async def __call__(self, trading):
        self.loads.append(tuple(trading))
        return self.seed


def source_with(
    seed: WatchHistory, app_config, *, closes=None, actions=(), implausible=frozenset(), archive_error=None
):
    app_config.insider_watch.enabled = True
    fake = FakeHistory(seed, implausible=implausible, error=archive_error)
    source = InsiderWatchSource(
        app_config,
        CONTRACT,
        history=fake,
        calendar=AlpacaLikeCalendar(),
        bars=FakeBars(closes or {}),
        actions=FakeActions(actions),
    )
    return source, fake.loads


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
    assert prep.events == () and prep.counts["implausible_live"] == 1


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
    assert source.contract is None and source.service_error == "FileNotFoundError"
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
        "issuer_cik": 1334036,
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
        "issuer_cik": 1334036,
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


async def test_a_watch_card_never_carries_the_native_entry_lifetime_marker(
    watch_desk, temp_db, app_config, monkeypatch
):
    monkeypatch.setattr("agentic_trader.agent.copilot.requires_account_risk", lambda config: False)
    app_config.execution_mode = ExecutionMode.ALPACA
    app_config.execution.native_entry_lifetime = "session"

    await suggestion_scan(watch_desk)

    provenance = {s["contract"]: s["decision_provenance"] for s in await _signals(temp_db)}
    assert NATIVE_ENTRY_LIFETIME_KEY in provenance["DDD"]  # the native card in the same scan is bounded
    # The watch card's lifetime is its own alpha_policy (the cube bracket's session-count exit).
    assert NATIVE_ENTRY_LIFETIME_KEY not in provenance["WTCH"]


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
        "issuer_cik": 1334036,
        "filing_dates": ["2026-10-16", "2026-10-20"],
        "opp_dollars": 412_000.0,
        "holding_sessions": 20,
        "time_exit_at": "2026-11-18T20:45:00+00:00",
        "risk_cap": 100.0,
    }
    text = format_alert_card(_evaluation(), INSIDER_WATCH_STRATEGY_ID, card_facts=facts)
    assert text.startswith("👀 <b>INSIDER WATCH — unvalidated</b> — risk capped at $100;")
    assert (
        "3 officer/director buyers · $412,000 opportunistic open-market purchases · filed 2026-10-16, 2026-10-20"
        in text
    )
    assert '<a href="https://www.sec.gov/cgi-bin/own-disp?action=getissuer&amp;CIK=1334036">' in text
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
    assert source.service_error == "ValueError"
    prep = source.unbuilt(app_config, None, "10:35", session=D)
    assert prep.reason == f"service unavailable: {source.service_error}" and prep.policy == CONTRACT.policy
    assert desk._catalog_strategy_ids() == frozenset({PEAD_STRATEGY_ID, INSIDER_WATCH_STRATEGY_ID})


# --- fix round 1: the frozen calendar, the issuer link, notices, terminal card, actions evidence -----


def _frozen_sessions() -> list[date]:
    """The deterministic trading calendar from the cube spec's bars_from through D (the study's start)."""
    days = pd.bdate_range(CONTRACT.spec.bars_from, D).date
    return [day for day in days if not MarketHolidayCalendar.is_equity_holiday(day)[0] and day not in ONE_OFF_CLOSURES]


def _back(k: int) -> date:
    """The session ``k`` sessions before D (k=1 is D-1)."""
    return _frozen_sessions()[-1 - k]


def _pair(symbol: str, k: int, owners: tuple[int, int]) -> list[InsiderTransaction]:
    return [buy(symbol, _back(k), owners[0]), buy(symbol, _back(k), owners[1])]


async def test_i1_the_cooldown_chain_reads_the_frozen_calendar_so_todays_cluster_is_suppressed(app_config):
    # Clusters at -141, -91, -41 and -1 sessions. On the study's calendar -141 fires, -91 is suppressed,
    # -41 fires (100 after -141) and -1 is suppressed (40 after -41): no event today. A calendar that
    # starts after -141 would fire -91 and then -1 (90 after it): a card the frozen rule never sends.
    rows = [
        *_pair("AAA", 141, (1, 2)),
        *_pair("AAA", 91, (3, 4)),
        *_pair("AAA", 41, (5, 6)),
        *_pair("AAA", 1, (7, 8)),
    ]
    source, loads = source_with(history({"AAA": rows}), app_config, closes=CLOSES)
    prep = await source.prepare(now=NOW, snapshot=None, owned={})
    assert prep.status == "ok" and prep.events == () and prep.excluded_events == ()
    [trading] = loads
    assert trading[0] == date(2016, 1, 4) and trading[-1] == D  # the first session on or after bars_from
    assert prep.calendar == {"first_session": "2016-01-04", "sessions": len(trading)}


async def test_i1_a_trigger_whose_window_starts_before_a_short_calendar_still_suppresses(app_config):
    # The mirror: buyers at -140 and -124 make a -124 trigger on the study's calendar, which suppresses
    # the -62 cluster (62 after it), so today's -1 cluster (123 after -124) fires. A calendar starting
    # between -140 and -124 would lose the -124 trigger, fire -62 and suppress today's card.
    rows = [
        buy("AAA", _back(140), 1),
        buy("AAA", _back(124), 2),
        *_pair("AAA", 62, (3, 4)),
        *_pair("AAA", 1, (5, 6)),
    ]
    source, _ = source_with(history({"AAA": rows}), app_config, closes=CLOSES)
    prep = await source.prepare(now=NOW, snapshot=None, owned={})
    assert [(e["symbol"], e["buyers"]) for e in prep.events] == [("AAA", 2)]


async def test_i1_the_archive_is_loaded_once_per_process_and_a_cold_load_never_blocks_the_decision():
    loads: list[int] = []
    gate = threading.Event()

    def slow_load():
        loads.append(1)
        gate.wait(5)
        return "cohort", "archive"

    live = LiveWatchHistory(store=None, load_archive=slow_load)
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await live.archive()
    gate.set()  # the timed-out waiter did not cancel the load: it finishes in its thread
    assert (await live.archive()).archive == "archive"
    assert (await live.archive()).cohort == "cohort"
    assert loads == [1]


async def test_i1_a_failed_archive_load_is_retried_by_the_next_decision():
    attempts: list[int] = []

    def flaky():
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("archive unreadable")
        return "cohort", "archive"

    live = LiveWatchHistory(store=None, load_archive=flaky)
    with pytest.raises(OSError):
        await live.archive()
    assert (await live.archive()).archive == "archive" and len(attempts) == 2


async def test_i2_the_card_links_the_issuer_s_edgar_insider_page_and_never_lists_owner_ciks(app_config):
    seed = dataclasses.replace(history({"AAA": clusters()["AAA"]}), issuer_ciks={"AAA": 1334036})
    source, _ = source_with(seed, app_config, closes=CLOSES)
    prep = await source.prepare(now=NOW, snapshot=None, owned={})
    [event] = prep.events
    assert event["issuer_cik"] == 1334036 and event["owners"] == [1, 2]  # owners stay in provenance
    cand = watch_candidate("AAA").model_copy(update={"catalog_event": event})
    facts = source.card_facts(cand, prep)
    assert "owners" not in facts and facts["issuer_cik"] == 1334036
    text = format_alert_card(_evaluation(), INSIDER_WATCH_STRATEGY_ID, card_facts=facts)
    link = "https://www.sec.gov/cgi-bin/own-disp?action=getissuer&amp;CIK=1334036"
    assert f'<a href="{link}">' in text and "2 officer/director buyers ·" in text
    assert "owner CIK" not in text


async def test_m2_a_completed_decision_with_no_event_queues_no_notice(watch_desk, temp_db):
    watch_desk.insider_watch_source.prep = watch_prep()
    await suggestion_scan(watch_desk)
    [event] = await _watch_events(temp_db)
    assert (event["payload"]["status"], event["payload"]["events"]) == ("completed", [])
    assert not [
        n
        for n in await temp_db.workflows.list_work(WorkKind.NOTIFICATION)
        if n.payload["kind"] == NotificationKind.MESSAGE
    ]


def test_m3_the_terminal_card_renders_the_watch_block_too():
    facts = {
        "kind": "insider_watch",
        "buyers": 2,
        "issuer_cik": 1334036,
        "filing_dates": ["2026-10-16", "2026-10-20"],
        "opp_dollars": 250_000.0,
        "holding_sessions": 20,
        "time_exit_at": None,
        "risk_cap": 100.0,
    }
    text = format_terminal_card(_evaluation(), INSIDER_WATCH_STRATEGY_ID, card_facts=facts)
    assert "INSIDER WATCH — unvalidated" in text and STUDY_LINE in text
    assert "https://www.sec.gov/cgi-bin/own-disp?action=getissuer&CIK=1334036" in text
    assert format_terminal_card(_evaluation(), INSIDER_WATCH_STRATEGY_ID) == format_terminal_card(
        _evaluation(), INSIDER_WATCH_STRATEGY_ID, card_facts={"buyers": 2}
    )


async def test_m4_the_corporate_actions_read_is_journaled_with_its_digest_and_request_time(app_config):
    source, _ = source_with(history({"AAA": clusters()["AAA"]}), app_config, closes=CLOSES)
    prep = await source.prepare(now=NOW, snapshot=None, owned={})
    read = prep.counts["corporate_actions"]
    assert read["page_sha256"] == ["p"] and read["symbols"] == 1
    assert datetime.fromisoformat(read["requested_at"]).tzinfo is not None


async def test_i1_each_decision_merges_fresh_captures_onto_the_one_archive(app_config):
    loads: list[int] = []
    merges: list[tuple] = []
    store = SimpleNamespace(captures=AsyncMock(side_effect=[["day1"], ["day1", "day2"]]))

    def load():
        loads.append(1)
        return "cohort", "archive"

    def merge(archive, cohort, days, raw_bars, actions, captures):
        merges.append((archive, cohort, days, dict(raw_bars), tuple(actions), tuple(captures)))
        return "history"

    live = LiveWatchHistory(store, load_archive=load, merge=merge)
    app_config.insider_watch.enabled = True
    source = InsiderWatchSource(app_config, CONTRACT, history=live, calendar=object(), bars=object(), actions=object())
    source.warm()  # daemon start: the read begins before any decision
    assert await live((D - timedelta(days=1), D)) == "history"
    assert await live((D, D + timedelta(days=1))) == "history"
    assert loads == [1]
    assert [m[5] for m in merges] == [("day1",), ("day1", "day2")]
    assert merges[0][:5] == ("archive", "cohort", (D - timedelta(days=1), D), {}, ())


# --- fix round 2: the verified calendar, the study's plausibility cache, warm-up, previews, timings --

ONE_OFF_CLOSURES = (date(2018, 12, 5), date(2025, 1, 9))  # Bush and Carter national days of mourning


class AlpacaLikeCalendar:
    """The exchange calendar as Alpaca serves it: the deterministic rules plus the one-off closures."""

    def __init__(self, *, source: str = "alpaca_api", closures=ONE_OFF_CLOSURES):
        self.inner, self.source, self.closures, self.calls = DeterministicCalendarProvider(), source, closures, []

    async def get_calendar_range(self, start, end):
        self.calls.append((start, end))
        days = await self.inner.get_calendar_range(start, end)
        return [
            dataclasses.replace(
                day, source=self.source, is_trading_day=day.is_trading_day and day.date not in self.closures
            )
            for day in days
        ]


def test_r2_the_pinned_calendar_is_the_study_s_recorded_one():
    assert (WATCH_CALENDAR_SESSIONS, WATCH_CALENDAR_SHA256) == (
        2681,
        "e829346db48c18f155ba4d1cbedf5a55f553a165eadc65b99426c5991e3069dd",
    )


@pytest.mark.parametrize(
    ("calendar", "reason"),
    [
        (AlpacaLikeCalendar(closures=()), "calendar_mismatch"),  # a fallback that misses the one-off closures
        (AlpacaLikeCalendar(source="deterministic"), "calendar_source"),
    ],
    ids=["wrong-days", "fallback-source"],
)
async def test_r2_a_calendar_that_is_not_the_study_s_is_unavailable(app_config, calendar, reason):
    source, loads = source_with(history(clusters()), app_config, closes=CLOSES)
    source.calendar = calendar
    prep = await source.prepare(now=NOW, snapshot=None, owned={})
    assert (prep.status, prep.reason) == ("unavailable", reason)
    assert loads == [] and source.bars.calls == []


async def test_r2_a_fallback_day_after_the_study_window_is_unavailable(app_config):
    class TailFallback(AlpacaLikeCalendar):
        async def get_calendar_range(self, start, end):
            days = await super().get_calendar_range(start, end)
            return [
                dataclasses.replace(day, source="finnhub_api") if day.date > date(2026, 9, 1) else day for day in days
            ]

    source, _ = source_with(history(clusters()), app_config, closes=CLOSES)
    source.calendar = TailFallback()
    prep = await source.prepare(now=NOW, snapshot=None, owned={})
    assert (prep.status, prep.reason) == ("unavailable", "calendar_source")


async def test_r2_the_study_calendar_is_read_once_per_process_by_year_and_the_tail_once_per_date(app_config):
    source, _ = source_with(history(clusters()), app_config, closes=CLOSES)
    calendar = source.calendar
    await source.prepare(now=NOW, snapshot=None, owned={})
    prefix_calls = [call for call in calendar.calls if call[1] <= date(2026, 9, 1)]
    assert len(prefix_calls) == 11 and all(start.year == end.year for start, end in prefix_calls)

    def backward(calls):  # the time exit's forward read (from D) is not the trigger calendar
        return [(start, end) for start, end in calls if end <= D + timedelta(days=1) and start < D]

    calls = len(calendar.calls)
    await source.prepare(now=NOW + timedelta(minutes=1), snapshot=None, owned={})
    assert backward(calendar.calls[calls:]) == []  # nothing re-read the same New York date
    await source.prepare(now=NOW + timedelta(days=1), snapshot=None, owned={})
    assert backward(calendar.calls[calls:]) == [(date(2026, 9, 2), D + timedelta(days=1))]  # only the new tail


def test_r2_archive_plausibility_matches_the_study_on_the_study_calendar():
    prefix = _frozen_sessions()[: _frozen_sessions().index(date(2026, 9, 1)) + 1]
    big = buy("AAA", date(2026, 3, 2), 1, dollars=600_000_000.0)  # above 10 x $50M
    fine = buy("AAA", date(2026, 3, 3), 2)
    late = buy("AAA", date(2026, 9, 15), 3, dollars=600_000_000.0)  # after the study's bars: no DV, kept
    raw = {"AAA": daily(prefix, 50.0)}
    dropped = archive_implausible({"AAA": (big, fine, late)}, prefix, raw, 10.0)
    assert dropped == frozenset({big})


async def test_r2_the_archive_plausibility_drops_carry_into_the_decision(app_config):
    rows = [buy("AAA", _back(5), 1), buy("AAA", _back(1), 2)]
    seed = dataclasses.replace(history({"AAA": rows}), archive_through=D)  # every row is archive
    source, _ = source_with(seed, app_config, closes=CLOSES, implausible=frozenset({rows[1]}))
    prep = await source.prepare(now=NOW, snapshot=None, owned={})
    assert prep.events == ()  # the -1 buyer was implausible in the study's own bars: no cluster
    assert [call for call in source.bars.calls if call[3] == "raw"] == []  # no live buyer, no live bars


async def test_r2_live_plausibility_bars_are_fetched_only_for_live_buyers_and_extended_per_date(app_config):
    rows = clusters()
    rows["OLD"] = [buy("OLD", date(2026, 9, 2), 40), buy("OLD", date(2026, 9, 3), 41)]  # archive only
    source, _ = source_with(history(rows, latest=D), app_config, closes=CLOSES | {"OLD": 50.0})
    await source.prepare(now=NOW, snapshot=None, owned={})
    [raw] = [call for call in source.bars.calls if call[3] == "raw"]
    assert "OLD" not in raw[0] and "AAA" in raw[0]
    assert raw[2].date() == D - timedelta(days=1)
    source.bars.calls.clear()
    await source.prepare(now=NOW + timedelta(days=1), snapshot=None, owned={})
    [extension] = [call for call in source.bars.calls if call[3] == "raw"]
    assert extension[1].date() == D and extension[2].date() == D  # only the new session's bar


async def test_r2_a_missing_plausibility_cache_is_unavailable(app_config, tmp_path):
    state_error = PlausibilityCacheMissing(tmp_path / "cube.npz")
    source, _ = source_with(history(clusters()), app_config, closes=CLOSES, archive_error=state_error)
    prep = await source.prepare(now=NOW, snapshot=None, owned={})
    assert (prep.status, prep.reason) == ("unavailable", "plausibility_cache_missing")


async def test_r2_the_real_cache_reader_refuses_a_missing_cube(tmp_path):
    with pytest.raises(PlausibilityCacheMissing):
        await read_study_raw_bars(tmp_path, cohort_sha256="c" * 64, contract=CONTRACT, symbols=("AAA",))


async def test_r2_a_failed_warm_read_is_logged_by_type_and_retrieved(caplog):
    def broken():
        raise OSError("disk detail that must not be logged")

    live = LiveWatchHistory(store=None, load_archive=broken)
    live.warm()
    await asyncio.sleep(0.05)
    assert any(
        r.__dict__.get("event") == "insider_watch_archive_failed" and r.__dict__.get("error") == "OSError"
        for r in caplog.records
    )
    assert "disk detail" not in caplog.text


async def test_r2_the_decision_journals_each_phase_s_elapsed_time(app_config):
    source, _ = source_with(history(clusters()), app_config, closes=CLOSES)
    prep = await source.prepare(now=NOW, snapshot=None, owned={})
    phases = prep.counts["elapsed_seconds"]
    assert set(phases) == {"archive", "calendar", "bars", "actions", "rule"}
    assert all(isinstance(value, float) and value >= 0 for value in phases.values())


async def test_r2_a_watch_card_is_sent_without_a_link_preview():
    notifier = TelegramNotifier.__new__(TelegramNotifier)
    sent = AsyncMock(return_value=SimpleNamespace(message_id=7))
    notifier.app = SimpleNamespace(bot=SimpleNamespace(send_message=sent))
    notifier.db, notifier.chat_id, notifier.portfolio_cash = None, 1, 100_000.0
    notifier.execution_mode = "alpaca"
    notifier.is_configured = lambda: True
    notifier._label = lambda text: text
    facts = {
        "kind": "insider_watch",
        "buyers": 2,
        "issuer_cik": 1334036,
        "filing_dates": ["2026-10-20"],
        "opp_dollars": 1.0,
        "holding_sessions": 20,
        "time_exit_at": None,
        "risk_cap": 100.0,
    }
    await notifier.send_signal_alert(_evaluation(), INSIDER_WATCH_STRATEGY_ID, 1, card_facts=facts)
    assert sent.await_args.kwargs["link_preview_options"].is_disabled is True
    sent.reset_mock()
    await notifier.send_signal_alert(_evaluation(), "TREND_PULLBACK", 2)
    assert "link_preview_options" not in sent.await_args.kwargs


# --- fix round 3: plausibility evidence, timings on every decision, fast cache misses, one log line ---


async def test_r3_the_archive_plausibility_evidence_is_journaled_beside_the_live_count(app_config):
    rows = [buy("AAA", _back(5), 1), buy("AAA", _back(1), 2)]
    seed = dataclasses.replace(history({"AAA": rows}), archive_through=D)
    source, _ = source_with(seed, app_config, closes=CLOSES, implausible=frozenset({rows[1]}))
    prep = await source.prepare(now=NOW, snapshot=None, owned={})
    assert prep.counts["plausibility_archive"] == {"plausibility": "study_bars", "symbols_checked": 3, "implausible": 1}
    assert prep.counts["implausible_live"] == 0 and "implausible_size" not in prep.counts
    assert "implausible_size" not in prep.history  # the merge never measures it: not journaled as if it did


async def test_r3_an_unchecked_archive_is_unavailable_never_a_silent_pass(app_config):
    source, _ = source_with(history(clusters()), app_config, closes=CLOSES)
    source.history.checked = False
    prep = await source.prepare(now=NOW, snapshot=None, owned={})
    assert (prep.status, prep.reason) == ("unavailable", "plausibility_not_checked")


async def test_r3_an_unavailable_decision_journals_the_phases_that_ran(app_config):
    source, _ = source_with(history(clusters(), latest=D - timedelta(days=2)), app_config, closes=CLOSES)
    prep = await source.prepare(now=NOW, snapshot=None, owned={})
    assert prep.status == "unavailable"
    phases = prep.counts["elapsed_seconds"]
    assert set(phases) == {"archive", "calendar", "bars", "actions", "rule"} and phases["calendar"] >= 0


async def test_r3_the_corporate_actions_read_is_its_own_phase(app_config):
    class SlowActions(FakeActions):
        def fetch(self, symbols, start, end):
            threading.Event().wait(0.05)
            return super().fetch(symbols, start, end)

    source, _ = source_with(history({"AAA": clusters()["AAA"]}), app_config, closes=CLOSES)
    source.actions = SlowActions()
    prep = await source.prepare(now=NOW, snapshot=None, owned={})
    phases = prep.counts["elapsed_seconds"]
    assert phases["actions"] >= 0.05 > phases["bars"]


async def test_r3_a_missing_symbol_file_in_the_pooled_cache_fails_fast(tmp_path, monkeypatch):
    cube = SimpleNamespace(
        spec_identity=CONTRACT.spec.identity, cohort_sha256="c" * 64, coverage={}, sha256=CONTRACT.entry.cube_sha256
    )
    (tmp_path / f"cube-{'c' * 16}-{CONTRACT.spec.identity[:16]}.npz").write_bytes(b"")
    monkeypatch.setattr(insider_watch_module, "load_cube", lambda path: cube)
    monkeypatch.setattr(insider_watch_module, "_verify_bar_files", lambda coverage, bars, raw: "ok")

    async def never(*args, **kwargs):
        raise AssertionError("a cache miss must not reach the provider path")

    monkeypatch.setattr(insider_watch_module, "_fetch_cached", never)
    started = asyncio.get_running_loop().time()
    with pytest.raises(PlausibilityCacheMissing):
        await read_study_raw_bars(tmp_path, cohort_sha256="c" * 64, contract=CONTRACT, symbols=("AAA",))
    assert asyncio.get_running_loop().time() - started < 1.0


async def test_r3_a_failed_warm_read_logs_its_reason_token_once(app_config, caplog):
    def broken():
        raise PlausibilityCacheMissing("/private/path/that/must/not/be/logged")

    live = LiveWatchHistory(store=None, load_archive=broken)
    app_config.insider_watch.enabled = True
    source = InsiderWatchSource(
        app_config, CONTRACT, history=live, calendar=AlpacaLikeCalendar(), bars=object(), actions=object()
    )
    source.warm()
    await asyncio.sleep(0.2)
    failures = [r for r in caplog.records if r.__dict__.get("event") == "insider_watch_archive_failed"]
    assert len(failures) == 1
    assert (failures[0].__dict__["error"], failures[0].__dict__["reason"]) == (
        "PlausibilityCacheMissing",
        "plausibility_cache_missing",
    )
    assert "/private/path" not in caplog.text


# --- final review fix wave: share-class siblings, scope mismatch, live plausibility, same-symbol cards --


async def test_f1_share_class_siblings_card_only_the_class_with_the_higher_dollar_volume(app_config):
    rows = {
        "FOX": [buy("FOX", D - timedelta(days=2), 1), buy("FOX", D - timedelta(days=1), 2)],
        "FOXA": [buy("FOXA", D - timedelta(days=2), 1), buy("FOXA", D - timedelta(days=1), 2)],
    }
    seed = dataclasses.replace(history(rows), issuer_ciks={"FOX": 1754301, "FOXA": 1754301})
    source, _ = source_with(seed, app_config, closes={"FOX": 50.0, "FOXA": 60.0})  # FOXA: $60M dollar volume
    prep = await source.prepare(now=NOW, snapshot=None, owned={})
    assert [e["symbol"] for e in prep.events] == ["FOXA"]
    assert [(e["symbol"], e["reason"]) for e in prep.excluded_events] == [("FOX", "share_class_sibling")]


async def test_f1_an_ineligible_class_counts_as_minus_infinity_so_its_sibling_is_carded(app_config):
    rows = {
        "FOX": [buy("FOX", D - timedelta(days=2), 1), buy("FOX", D - timedelta(days=1), 2)],
        "FOXA": [buy("FOXA", D - timedelta(days=2), 1), buy("FOXA", D - timedelta(days=1), 2)],
    }
    seed = dataclasses.replace(history(rows), issuer_ciks={"FOX": 1754301, "FOXA": 1754301})
    # FOXA's raw dollar volume would be higher, but its $5 close fails the price floor.
    source, _ = source_with(seed, app_config, closes={"FOX": 50.0, "FOXA": 5.0})
    prep = await source.prepare(now=NOW, snapshot=None, owned={})
    assert [e["symbol"] for e in prep.events] == ["FOX"]
    assert {e["symbol"]: e["reason"] for e in prep.excluded_events} == {"FOXA": "share_class_sibling"}


async def test_f1_different_issuers_are_never_siblings(app_config):
    seed = dataclasses.replace(history(clusters()), issuer_ciks={"AAA": 1, "BBB": 2})
    source, _ = source_with(seed, app_config, closes=CLOSES)
    prep = await source.prepare(now=NOW, snapshot=None, owned={})
    # A symbol without an issuer CIK is its own issuer, as in the study.
    assert [e["symbol"] for e in prep.events] == ["BBB", "AAA", "DDD", "EEE"]
    assert "share_class_sibling" not in {e["reason"] for e in prep.excluded_events}


async def test_f4_a_capture_under_another_scope_is_named_in_the_unavailable_reason(app_config):
    seed = history(clusters(), latest=D - timedelta(days=3))
    seed = dataclasses.replace(seed, provenance={**seed.provenance, "scope_mismatch": "2026-10-19"})
    source, _ = source_with(seed, app_config, closes=CLOSES)
    prep = await source.prepare(now=NOW, snapshot=None, owned={})
    assert (prep.status, prep.reason) == ("unavailable", "scope_mismatch: 2026-10-19")


async def test_f4_a_live_buyer_without_bars_is_journaled_and_requested_again_next_date(app_config):
    rows = clusters() | {"NOB": [buy("NOB", D - timedelta(days=2), 30), buy("NOB", D - timedelta(days=1), 31)]}
    source, _ = source_with(history(rows, latest=D), app_config, closes=CLOSES)  # no bars for NOB
    prep = await source.prepare(now=NOW, snapshot=None, owned={})
    assert prep.counts["live_buyers_without_bars"] == ["NOB"]
    [first] = [call for call in source.bars.calls if call[3] == "raw" and "NOB" in call[0]][:1]
    source.bars.calls.clear()
    await source.prepare(now=NOW + timedelta(days=1), snapshot=None, owned={})
    [again] = [call for call in source.bars.calls if call[3] == "raw" and "NOB" in call[0]][:1]
    assert again[1] == first[1]  # never advanced past an empty read: the warm-up window is read again


async def test_f4_a_watch_event_on_a_symbol_already_carded_this_scan_is_skipped(watch_desk, temp_db):
    source = watch_desk.insider_watch_source
    source.prep = watch_prep("DDD", "WINR", "WTCH")  # DDD: the native card; WINR: the PEAD card
    source.candidates.update({"DDD": watch_candidate("DDD"), "WINR": watch_candidate("WINR")})

    await suggestion_scan(watch_desk)

    [event] = await _watch_events(temp_db)
    assert {e["symbol"]: e["outcome"] for e in event["payload"]["events"]} == {
        "DDD": "carded_this_scan",
        "WINR": "carded_this_scan",
        "WTCH": "sent",
    }
    assert sorted(s["contract"] for s in await _signals(temp_db)) == ["DDD", "WINR", "WTCH"]


def test_f4_an_unbuildable_source_names_the_exception_type_only(app_config, tmp_path, temp_db):
    app_config.insider_watch.enabled = True

    def clients():
        raise ValueError("secret detail /private/path")

    source = build_insider_watch_source(
        app_config, temp_db.workflows, repo_root=REPO_ROOT, calendar=None, clients=clients
    )
    assert source.service_error == "ValueError"
    prep = source.unbuilt(app_config, None, "10:35", session=D)
    assert prep.reason == "service unavailable: ValueError"


def test_f4_an_unscheduled_watch_decision_time_is_warned_at_construction(app_config, temp_db, mock_notifier, caplog):
    app_config.insider_watch.enabled = True
    app_config.copilot_chat_enabled = False
    app_config.scheduler.suggestion_scan_times_et = ["09:45", "14:35"]
    with caplog.at_level("WARNING", logger="copilot"):
        copilot_module.TradingCopilot(
            app_config,
            db=temp_db,
            broker=MagicMock(supports_activity_ledger=False, supports_trade_stream=False),
            notifier=mock_notifier,
            entry_service=AsyncMock(),
            alpha_repository=AsyncMock(),
        )
    [record] = [r for r in caplog.records if r.__dict__.get("event") == "insider_watch_decision_time_unscheduled"]
    assert "10:35" in record.getMessage() and record.__dict__["decision_time_et"] == "10:35"


def test_f4_a_scheduled_watch_decision_time_is_not_warned(app_config, temp_db, mock_notifier, caplog):
    app_config.insider_watch.enabled = True
    app_config.copilot_chat_enabled = False
    assert "10:35" in app_config.scheduler.suggestion_scan_times_et
    with caplog.at_level("WARNING", logger="copilot"):
        copilot_module.TradingCopilot(
            app_config,
            db=temp_db,
            broker=MagicMock(supports_activity_ledger=False, supports_trade_stream=False),
            notifier=mock_notifier,
            entry_service=AsyncMock(),
            alpha_repository=AsyncMock(),
        )
    assert not [r for r in caplog.records if r.__dict__.get("event") == "insider_watch_decision_time_unscheduled"]


async def test_f4_round2_the_same_scan_check_normalizes_the_symbol(watch_desk, temp_db):
    source = watch_desk.insider_watch_source
    source.prep = watch_prep("DDD", "WTCH")
    source.candidates["DDD"] = watch_candidate("DDD").model_copy(update={"contract": "/ddd"})  # same instrument

    await suggestion_scan(watch_desk)

    contracts = [s["contract"].strip("/").upper() for s in await _signals(temp_db)]
    assert sorted(contracts) == ["DDD", "WINR", "WTCH"]
