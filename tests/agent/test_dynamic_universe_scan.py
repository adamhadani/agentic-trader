"""WS3: dynamic suggestion-universe names inside the scheduled suggestion scan.

Only ``run_scan(shadow_evidence=True)`` on the full universe (the scheduled
suggestion-scan job) adds screener names; every other scan is unchanged. Dynamic names
get a synthetic per-scan contract (``config.contracts`` is never mutated), a liquidity
gate on the scan's own daily bars, one shared ``dynamic`` correlation group, flagged
provenance/journal entries and one ``dynamic_universe_built`` audit event per scan.
"""

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pandas as pd
import pytest

import agentic_trader.agent.copilot as copilot_module
from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.agent.earnings import EarningsLookup
from agentic_trader.agent.evaluator import LLMTradeEvaluation, RiskEvaluator
from agentic_trader.broker.base import (
    BrokerEntryContext,
    EntryAccountEvidence,
    EntryAssetEvidence,
    EntryQuoteEvidence,
    OrderResult,
)
from agentic_trader.config import ScanBudget, UniverseConfig, UniverseEntry
from agentic_trader.constants import AssetClass, SignalStatus
from agentic_trader.execution.durable import EventKind, WorkKind, WorkStatus
from agentic_trader.execution.entries import EntryExecutionService
from agentic_trader.market.session import ET_TZ, MarketSessionInfo, MarketSessionType
from agentic_trader.screeners.dynamic_universe import AssetInfo, ScreenerEntry
from tests.agent.test_scan_budget import (  # noqa: F401  (budget_desk is a fixture)
    budget_desk,
    candidate,
    evaluation,
    frame,
)


STATIC = ("AAA", "BBB", "CCC", "DDD", "EEE")
QUALITIES = {"AAA": 0.6, "BBB": 0.7, "CCC": 0.8, "DDD": 0.9, "EEE": 0.5, "NEWA": 0.97, "NEWB": 0.96}


def daily_frame(sessions: int = 300, *, volume: float = 1_000_000.0, close: float = 100.0, today: bool = False):
    """Daily bars stamped at New York midnight, ending with the previous session (plus today's partial bar)."""
    end = pd.Timestamp(datetime.now(ET_TZ).date()) - pd.offsets.BDay(1)
    dates = list(pd.bdate_range(end=end, periods=sessions))
    if today:
        dates.append(pd.Timestamp(datetime.now(ET_TZ).date()))
    index = pd.DatetimeIndex(dates).tz_localize(ET_TZ)
    rng = np.random.default_rng(len(dates))
    closes = close * np.exp(np.cumsum(rng.normal(0.0, 0.001, len(dates))))
    return pd.DataFrame({"Close": closes, "High": closes * 1.01, "Volume": volume}, index=index)


def asset(symbol: str) -> AssetInfo:
    return AssetInfo(
        symbol=symbol,
        name=f"{symbol} Holdings Inc. Common Stock",
        asset_class="us_equity",
        exchange="NASDAQ",
        status="active",
        tradable=True,
    )


class FakeSource:
    """The ``DynamicUniverseSource`` boundary: canned screener entries and asset metadata."""

    def __init__(self, entries, assets, *, fail: str | None = None):
        self._entries = list(entries)
        self._assets = dict(assets)
        self.fail = fail
        self.entries_calls = 0
        self.assets_calls = 0

    async def entries(self):
        self.entries_calls += 1
        if self.fail == "entries":
            raise RuntimeError("screener down")
        return list(self._entries)

    async def assets(self):
        self.assets_calls += 1
        if self.fail == "assets":
            raise RuntimeError("asset list down")
        return dict(self._assets)


def default_source(**kwargs) -> FakeSource:
    return FakeSource(
        [
            ScreenerEntry(symbol="NEWA", source="most_actives", rank=0, price=None, percent_change=None),
            ScreenerEntry(symbol="NEWB", source="movers", rank=0, price=25.0, percent_change=12.5),
            ScreenerEntry(symbol="AAA", source="movers", rank=1, price=25.0, percent_change=3.0),  # static
        ],
        {s: asset(s) for s in ("NEWA", "NEWB", "AAA")},
        **kwargs,
    )


@pytest.fixture
def dailies():
    return {symbol: daily_frame() for symbol in (*STATIC, "NEWA", "NEWB")}


@pytest.fixture
def dynamic_desk(budget_desk, app_config, dailies):  # noqa: F811
    app_config.universe = UniverseConfig(
        groups={"test": [UniverseEntry(symbol=s, sector="technology") for s in STATIC]}, max_symbols=10
    )
    app_config.universe.dynamic.enabled = True
    budget_desk.data_fetcher.fetch_data.side_effect = lambda contract, ticker, include_fifteen_min=True: (
        SimpleNamespace(contract=contract, daily=dailies[contract], four_hour=frame(), hourly=frame())
    )
    budget_desk.strategy_engine.scan_contract.side_effect = lambda data, **kw: [
        candidate(data.contract, QUALITIES[data.contract])
    ]
    budget_desk.dynamic_universe = default_source()
    return budget_desk


def fetched(desk) -> list[str]:
    return [c.args[0] for c in desk.data_fetcher.fetch_data.call_args_list]


def strategy_scanned(desk) -> list[str]:
    return [c.args[0].contract for c in desk.strategy_engine.scan_contract.call_args_list]


async def dynamic_events(db):
    return [e for e in await db.workflows.events() if e["kind"] == EventKind.DYNAMIC_UNIVERSE_BUILT]


async def ranked_events(db):
    return [e for e in await db.workflows.events() if e["kind"] == EventKind.SCAN_CANDIDATES_RANKED]


async def suggestion_scan(desk, **kwargs):
    return await desk.run_scan(
        use_llm=False, dry_run=False, asset_class="equity", budget=ScanBudget.FULL, shadow_evidence=True, **kwargs
    )


async def test_suggestion_scan_adds_the_dynamic_names_without_touching_config_contracts(
    dynamic_desk, temp_db, app_config
):
    contracts_before = dict(app_config.contracts)

    await suggestion_scan(dynamic_desk)

    assert {"NEWA", "NEWB"} <= set(fetched(dynamic_desk))
    assert {"NEWA", "NEWB"} <= set(strategy_scanned(dynamic_desk))
    # The contract passed to the fetcher is the synthetic one: ticker is the symbol.
    newa_call = next(c for c in dynamic_desk.data_fetcher.fetch_data.call_args_list if c.args[0] == "NEWA")
    assert newa_call.args[1] == "NEWA"
    summary = dynamic_desk.last_scan_summary
    assert summary["dynamic"]["available"] is True
    assert summary["dynamic"]["members"] == ["NEWA", "NEWB"]
    assert summary["dynamic"]["excluded"] == {}
    assert summary["dynamic"]["reasons"] == {"static": 1}
    assert summary["dynamic"]["raw_counts"] == {"most_actives": 1, "movers": 2}
    assert summary["scanned"] == 7
    # The highest-quality setup is dynamic, so it takes the scan's single card.
    [card] = await temp_db.get_recent_signals(limit=10)
    assert card["contract"] == "NEWA"
    assert app_config.contracts == contracts_before
    assert "NEWA" not in app_config.contracts and "NEWB" not in app_config.contracts


@pytest.mark.parametrize(
    "scan",
    [
        pytest.param({"shadow_evidence": False}, id="manual"),
        pytest.param({"shadow_evidence": True, "timeframe": "15m"}, id="intraday"),
        pytest.param({"shadow_evidence": True, "symbols": ["AAA", "NEWA"]}, id="restricted"),
        pytest.param({"shadow_evidence": True, "disabled": True}, id="disabled"),
    ],
)
async def test_other_scans_never_see_dynamic_names(dynamic_desk, temp_db, app_config, scan):
    scan = dict(scan)
    if scan.pop("disabled", False):
        app_config.universe.dynamic.enabled = False

    await dynamic_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL, **scan)

    source = dynamic_desk.dynamic_universe
    assert source.entries_calls == 0 and source.assets_calls == 0
    assert not {"NEWA", "NEWB"} & set(fetched(dynamic_desk))
    assert "dynamic" not in dynamic_desk.last_scan_summary
    assert await dynamic_events(temp_db) == []


@pytest.mark.parametrize("fail", ["entries", "assets"])
async def test_a_screener_failure_is_a_static_only_scan_with_an_unavailable_event(dynamic_desk, temp_db, fail, caplog):
    dynamic_desk.dynamic_universe = default_source(fail=fail)

    with caplog.at_level(logging.WARNING, logger="copilot"):
        await suggestion_scan(dynamic_desk)

    assert set(fetched(dynamic_desk)) == set(STATIC)
    [card] = await temp_db.get_recent_signals(limit=10)
    assert card["contract"] == "DDD"
    unavailable = [r for r in caplog.records if getattr(r, "event", None) == "dynamic_universe_unavailable"]
    assert len(unavailable) == 1
    summary = dynamic_desk.last_scan_summary["dynamic"]
    assert summary["available"] is False and "down" in summary["error"]
    [event] = await dynamic_events(temp_db)
    assert event["payload"]["available"] is False
    assert "RuntimeError" in event["payload"]["error"] and "down" in event["payload"]["error"]
    assert event["payload"]["scan_id"] == dynamic_desk.last_scan_summary["scan_id"]


async def test_a_copilot_without_screener_credentials_scans_static_only(app_config, temp_db, mock_notifier):
    """The daemon constructs the real source; missing credentials must not break construction or a scan."""
    app_config.universe.dynamic.enabled = True
    copilot = TradingCopilot(
        app_config,
        db=temp_db,
        broker=MagicMock(supports_activity_ledger=False, supports_trade_stream=False),
        notifier=mock_notifier,
        alpha_repository=AsyncMock(),
    )
    assert copilot.dynamic_universe is None
    injected = default_source()
    assert (
        TradingCopilot(
            app_config,
            db=temp_db,
            broker=MagicMock(supports_activity_ledger=False, supports_trade_stream=False),
            notifier=mock_notifier,
            alpha_repository=AsyncMock(),
            dynamic_universe=injected,
        ).dynamic_universe
        is injected
    )


async def test_scan_without_a_source_journals_unavailable(dynamic_desk, temp_db):
    dynamic_desk.dynamic_universe = None
    await suggestion_scan(dynamic_desk)
    assert set(fetched(dynamic_desk)) == set(STATIC)
    [event] = await dynamic_events(temp_db)
    assert event["payload"]["available"] is False and event["payload"]["error"]


async def test_the_liquidity_gate_excludes_thin_names_from_strategy_scanning(dynamic_desk, temp_db, dailies):
    dailies["NEWB"] = daily_frame(volume=1_000.0)  # ~$100k/day, far below $50M

    await suggestion_scan(dynamic_desk)

    assert "NEWB" in fetched(dynamic_desk)
    assert "NEWB" not in strategy_scanned(dynamic_desk)
    assert "NEWA" in strategy_scanned(dynamic_desk)
    summary = dynamic_desk.last_scan_summary
    assert summary["dynamic"]["members"] == ["NEWA"]
    assert summary["dynamic"]["excluded"] == {"NEWB": "dollar_volume"}
    assert "NEWB" not in summary["fetch_failed"] and "NEWB" not in summary["insufficient"]
    [event] = await dynamic_events(temp_db)
    payload = event["payload"]
    assert payload["available"] is True
    assert payload["excluded"] == {"NEWB": "dollar_volume"}
    assert payload["scanned"] == ["NEWA"]
    assert payload["members"] == [
        {"symbol": "NEWA", "source": "most_actives", "rank": 0, "price": None, "percent_change": None},
        {"symbol": "NEWB", "source": "movers", "rank": 0, "price": 25.0, "percent_change": 12.5},
    ]
    assert payload["reasons"] == {"static": 1}
    assert payload["raw_counts"] == {"most_actives": 1, "movers": 2}
    assert event["stream"] == f"scan/{dynamic_desk.session_start_et().date().isoformat()}"


async def test_the_liquidity_gate_ignores_todays_partial_bar(dynamic_desk, dailies):
    """19 completed sessions plus today's in-progress bar is too little history."""
    dailies["NEWA"] = daily_frame(sessions=19, today=True)

    await suggestion_scan(dynamic_desk)

    assert dynamic_desk.last_scan_summary["dynamic"]["excluded"] == {"NEWA": "insufficient_bars"}
    assert "NEWA" not in strategy_scanned(dynamic_desk)


async def test_a_failed_dynamic_fetch_is_an_exclusion_not_a_scan_error(dynamic_desk, dailies):
    original = dynamic_desk.data_fetcher.fetch_data.side_effect

    def fetch(contract, ticker, include_fifteen_min=True):
        if contract == "NEWB":
            raise TimeoutError("slow provider")
        return original(contract, ticker, include_fifteen_min=include_fifteen_min)

    dynamic_desk.data_fetcher.fetch_data.side_effect = fetch
    await suggestion_scan(dynamic_desk)

    summary = dynamic_desk.last_scan_summary
    assert summary["dynamic"]["excluded"] == {"NEWB": "fetch_failed"}
    assert summary["fetch_failed"] == []


async def test_coverage_excluded_dynamic_names_do_not_consume_the_cap(dynamic_desk, app_config, monkeypatch):
    app_config.universe.dynamic.max_symbols = 1
    monkeypatch.setattr(copilot_module, "coverage_exclusions", lambda datasets, **kwargs: ({"NEWA"}, None))

    await suggestion_scan(dynamic_desk)

    dynamic = dynamic_desk.last_scan_summary["dynamic"]
    assert dynamic["members"] == ["NEWB"]
    assert dynamic["excluded"] == {"NEWA": "coverage"}
    assert "NEWA" in dynamic_desk.last_scan_summary["coverage_excluded"]


async def test_dynamic_names_share_one_correlation_group(dynamic_desk, temp_db, app_config):
    app_config.scan.max_cards_per_scan = 5
    app_config.scan.max_cards_per_session = 5

    await suggestion_scan(dynamic_desk)

    sent = {s["contract"] for s in await temp_db.get_recent_signals(limit=10)}
    # NEWB shares the dynamic group with the better-ranked NEWA; AAA shares sector_x with BBB.
    assert sent == {"NEWA", "DDD", "CCC", "BBB", "EEE"}
    runners = {r["contract"]: r["reason"] for r in dynamic_desk.last_scan_summary["runners_up"]}
    assert "group" in runners["NEWB"] and "group" in runners["AAA"]


async def test_todays_dynamic_card_blocks_further_dynamic_cards(dynamic_desk, temp_db, app_config):
    """A card recorded earlier today on a (different) dynamic name counts toward group ``dynamic``."""
    app_config.scan.max_cards_per_scan = 5
    app_config.scan.max_cards_per_session = 5
    await temp_db.record_signal(
        "OLDX", "TREND_PULLBACK", "LONG", 100, 98, 104, 2, decision_provenance={"dynamic": True, "rank": 1}
    )
    # A static (non-dynamic) card on a name outside every group never counts toward it.
    await temp_db.record_signal("ZZZ", "TREND_PULLBACK", "LONG", 100, 98, 104, 2, decision_provenance={"rank": 1})

    await suggestion_scan(dynamic_desk)

    sent = {s["contract"] for s in await temp_db.get_recent_signals(limit=10)} - {"OLDX", "ZZZ"}
    assert sent == {"DDD", "CCC", "BBB"}  # session budget 5 minus the two planted cards
    runners = {r["contract"]: r["reason"] for r in dynamic_desk.last_scan_summary["runners_up"]}
    assert "group" in runners["NEWA"] and "group" in runners["NEWB"]


async def test_provenance_and_the_ranked_journal_carry_the_dynamic_flags(dynamic_desk, temp_db, app_config):
    app_config.scan.max_cards_per_scan = 5
    app_config.scan.max_cards_per_session = 5

    await suggestion_scan(dynamic_desk)

    cards = {s["contract"]: s["decision_provenance"] for s in await temp_db.get_recent_signals(limit=10)}
    assert cards["NEWA"]["dynamic"] is True and cards["NEWA"]["dynamic_source"] == "most_actives"
    assert "dynamic" not in cards["DDD"] and "dynamic_source" not in cards["DDD"]
    [event] = await ranked_events(temp_db)
    candidates = {c["contract"]: c for c in event["payload"]["candidates"]}
    assert candidates["NEWA"]["dynamic"] is True and candidates["NEWA"]["dynamic_source"] == "most_actives"
    assert candidates["NEWB"]["dynamic"] is True and candidates["NEWB"]["dynamic_source"] == "movers"
    assert all("dynamic" not in candidates[s] and "dynamic_source" not in candidates[s] for s in STATIC)
    # The cross-section stays universe.groups only: a dynamic name's cross-sectional features are null.
    assert candidates["NEWA"]["shadow"]["features"]["mom_60"] is None
    assert candidates["DDD"]["shadow"]["features"]["mom_60"] is not None


async def test_a_dry_run_selects_dynamic_names_but_journals_nothing(dynamic_desk, temp_db):
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

    dynamic_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=evaluate)
    await dynamic_desk.run_scan(use_llm=False, dry_run=True, budget=ScanBudget.FULL, shadow_evidence=True)

    assert dynamic_desk.last_scan_summary["dynamic"]["members"] == ["NEWA", "NEWB"]
    assert await dynamic_events(temp_db) == []


async def test_the_digest_counts_dynamic_names(dynamic_desk, dailies):
    dailies["NEWB"] = daily_frame(volume=1_000.0)
    dynamic_desk.outbox = AsyncMock()

    await suggestion_scan(dynamic_desk)
    text = await dynamic_desk.publish_scan_digest()

    assert "1 dynamic names scanned (1 excluded)" in text


async def test_the_digest_omits_the_dynamic_line_when_no_scan_attempted_it(dynamic_desk, app_config):
    app_config.universe.dynamic.enabled = False
    dynamic_desk.outbox = AsyncMock()

    await suggestion_scan(dynamic_desk)
    text = await dynamic_desk.publish_scan_digest()

    assert "dynamic" not in text


async def test_the_digest_reports_an_unavailable_dynamic_universe(dynamic_desk):
    dynamic_desk.dynamic_universe = default_source(fail="entries")
    dynamic_desk.outbox = AsyncMock()

    await suggestion_scan(dynamic_desk)
    text = await dynamic_desk.publish_scan_digest()

    assert "0 dynamic names scanned (0 excluded)" in text and "unavailable in 1 scan" in text


def entry_context(symbol: str, price: str) -> BrokerEntryContext:
    now = datetime.now(UTC)
    account = EntryAccountEvidence(
        account_id="fixture-account",
        status="ACTIVE",
        currency="USD",
        cash="100000",
        equity="100000",
        buying_power="200000",
        regt_buying_power="200000",
        non_marginable_buying_power="100000",
        multiplier="2",
        trading_blocked=False,
        account_blocked=False,
        trade_suspended_by_user=False,
        shorting_enabled=True,
    )
    return BrokerEntryContext(
        account_before=account,
        account=account,
        asset=EntryAssetEvidence(
            asset_id="00000000-0000-0000-0000-000000000002",
            symbol=symbol,
            asset_class="us_equity",
            status="active",
            tradable=True,
            marginable=True,
            shortable=True,
            fractionable=True,
            borrow_status="easy_to_borrow",
        ),
        quote=EntryQuoteEvidence(symbol=symbol, bid_price="99.9", ask_price="100.1", timestamp=now, feed="iex"),
        price=price,
        trade_timestamp=now,
        requested_at=now,
        observed_at=now,
        session_closes_at=now + timedelta(hours=6),
        orders=(),
        positions=(),
    )


async def test_a_dynamic_card_tapped_through_freshness_queues_a_multiplier_one_bracket(
    dynamic_desk, temp_db, app_config
):
    """Scan -> card -> tap -> real EntryExecutionService: an unconfigured equity sizes at multiplier 1."""
    real_evaluator = RiskEvaluator(app_config, calendar=AsyncMock(), regime_detector=AsyncMock())
    newa = candidate("NEWA", QUALITIES["NEWA"])
    levels = real_evaluator.calculate_levels_deterministic(newa)
    assert levels.quantity > 0
    assert levels.notional_value == pytest.approx(newa.current_price * levels.quantity)  # multiplier 1

    async def evaluate(cand, **kwargs):
        if cand.contract != "NEWA":
            return evaluation(cand, approved=False)
        return LLMTradeEvaluation(
            approved=True,
            contract="NEWA",
            direction="LONG",
            entry_price=cand.current_price,
            stop_loss=levels.stop_loss,
            take_profit=levels.take_profit,
            stop_distance_points=levels.stop_distance,
            target_distance_points=levels.target_distance,
            risk_reward_ratio=levels.target_distance / levels.stop_distance,
            risk_dollars=levels.risk_dollars,
            reward_dollars=levels.reward_dollars,
            notional_value=levels.notional_value,
            effective_leverage=0.01,
            macro_clearance=True,
            thesis_summary="dynamic setup",
            quantity=levels.quantity,
            asset_class=AssetClass.EQUITY,
        )

    now = datetime.now(UTC)
    info = MarketSessionInfo(
        symbol="NEWA",
        asset_class=AssetClass.EQUITY,
        is_open=True,
        is_rth=True,
        session_type=MarketSessionType.RTH,
        current_time=now,
        next_open=now + timedelta(days=1),
        next_close=now + timedelta(hours=2),
    )
    dynamic_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=evaluate)
    dynamic_desk.session_provider.get_session_info.return_value = info
    dynamic_desk.regime_detector.get_regime.return_value = SimpleNamespace(
        summary_text="calm", breakout_allowed=True, min_rr_threshold=2.0, risk_multiplier=1.0
    )
    dynamic_desk.earnings_calendar = AsyncMock()
    dynamic_desk.earnings_calendar.next_earnings.return_value = EarningsLookup(
        event=None, verified=True, horizon_end=now.date()
    )
    dynamic_desk.data_fetcher.fetch_latest_price.return_value = newa.current_price
    broker = AsyncMock()
    broker.entry_market_context.return_value = entry_context("NEWA", str(newa.current_price))
    broker.find_entry_order.return_value = None
    executor = AsyncMock()
    executor.execute_order.return_value = OrderResult(success=True, order_id="exact-entry-id")
    dynamic_desk.entry_service = EntryExecutionService(
        app_config, temp_db.workflows, broker, executor, dynamic_desk._entry_macro_check
    )
    assert app_config.execution.card_freshness.enabled

    await suggestion_scan(dynamic_desk)
    [card] = await temp_db.get_recent_signals(limit=10)
    assert card["contract"] == "NEWA" and card["decision_provenance"]["dynamic"] is True
    assert card["decision_provenance"]["valid_until"]

    reply = await dynamic_desk.execute_signal_by_id(card["id"])

    assert reply.ok is True, reply.text
    [tap] = [e for e in await temp_db.workflows.events(stream=f"card/{card['id']}") if e["kind"] == "card_tap_assessed"]
    assert tap["payload"]["outcome"] == "execute"
    executor.execute_order.assert_awaited_once()
    request = executor.execute_order.await_args.args[0]
    assert (request.symbol, request.ticker, request.quantity) == ("NEWA", "NEWA", levels.quantity)
    assert (request.entry_price, request.stop_loss, request.take_profit) == (
        newa.current_price,
        levels.stop_loss,
        levels.take_profit,
    )
    [work] = await temp_db.workflows.list_work(WorkKind.ENTRY)
    assert work.status == WorkStatus.ACCEPTED
    signal = await temp_db.get_signal_by_id(card["id"])
    assert signal["status"] != SignalStatus.PENDING
    assert signal["notional_value"] == pytest.approx(newa.current_price * levels.quantity)
    assert signal["risk_dollars"] == pytest.approx(abs(newa.current_price - levels.stop_loss) * levels.quantity)


async def test_trailing_stops_manage_an_unconfigured_equity_at_multiplier_one(config, temp_db, mock_notifier):
    """A dynamic position has no contracts: entry; the equity default policy must still trail it."""
    config.trailing_stop.enabled = True
    config.trailing_stop.breakeven_trigger_r = 1.0
    config.trailing_stop.breakeven_buffer_dollars = 5.0
    config.trailing_stop.trail_trigger_r = 1.5
    copilot = TradingCopilot(config, db=temp_db, notifier=mock_notifier)
    copilot.data_fetcher = MagicMock()
    copilot.data_fetcher.fetch_latest_price.return_value = 106.0  # +1.2R on a $5 risk per share
    sid = await temp_db.record_signal(
        contract="NEWA",
        strategy="test",
        direction="LONG",
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        risk_dollars=50.0,
        quantity=10,
        status=SignalStatus.EXECUTED,
        asset_class="EQUITY",
    )
    assert "NEWA" not in config.contracts

    assert await copilot.manage_trailing_stops(await copilot.db.get_active_positions()) == 1

    copilot.data_fetcher.fetch_latest_price.assert_called_with("NEWA")
    # Breakeven at entry + $5 buffer / multiplier 1 (a multiplier-5 futures default would give 101).
    assert (await temp_db.get_signal_by_id(sid))["stop_loss"] == pytest.approx(105.0)


async def test_a_missed_dynamic_card_offers_no_reevaluation_and_refuses_one(dynamic_desk, temp_db, app_config):
    """Re-evaluate runs a restricted scan over configured contracts, which never adds a dynamic name."""
    now = datetime.now(UTC)
    dynamic_desk.session_provider.get_session_info.return_value = MarketSessionInfo(
        symbol="NEWA",
        asset_class=AssetClass.EQUITY,
        is_open=True,
        is_rth=True,
        session_type=MarketSessionType.RTH,
        current_time=now,
        next_open=now + timedelta(days=1),
        next_close=now + timedelta(hours=2),
    )
    dynamic_desk.regime_detector.get_regime.return_value = SimpleNamespace(
        summary_text="calm", breakout_allowed=True, min_rr_threshold=2.0, risk_multiplier=1.0
    )
    dynamic_desk.earnings_calendar = AsyncMock()
    dynamic_desk.earnings_calendar.next_earnings.return_value = EarningsLookup(
        event=None, verified=True, horizon_end=now.date()
    )
    dynamic_desk.data_fetcher.fetch_latest_price.return_value = 90.0  # through the 98 stop
    dynamic_desk.entry_service = AsyncMock()
    sid = await temp_db.record_signal(
        "NEWA",
        "TREND_PULLBACK",
        "LONG",
        100.0,
        98.0,
        104.0,
        2.0,
        asset_class="EQUITY",
        quantity=1.0,
        decision_provenance={"dynamic": True, "dynamic_source": "movers"},
    )

    reply = await dynamic_desk.execute_signal_by_id(sid)

    assert reply.ok is False and reply.offer_reevaluate is False
    assert (await temp_db.get_signal_by_id(sid))["status"] == SignalStatus.EXPIRED
    dynamic_desk.entry_service.authorize.assert_not_awaited()
    refusal = await dynamic_desk.reevaluate_signal(sid)
    assert refusal.ok is False and "not a configured contract" in refusal.text
    assert dynamic_desk.reevaluation_tasks == set()
    assert [e for e in await temp_db.workflows.events() if e["kind"] == EventKind.CARD_REEVALUATE_REQUESTED] == []
