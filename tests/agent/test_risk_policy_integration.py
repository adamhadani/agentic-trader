"""End to end: the same risk rules at scan, tap and admission (spec §Testing, integration).

Each scenario drives the simulator desk (``EXECUTION_MODE=paper``) on the temporary database
through the real ``run_scan`` with the real ``RiskEvaluator``, the real tap
(``execute_signal_by_id``) and the real entry admission (``WorkflowStore.enqueue_entry``, then
``EntryExecutionService`` preflight with the copilot's own macro check), and names the rule it
pins by its reason text or ``rejection_rule``. Only I/O is stubbed: market data and the
strategy engine's setups, the macro context (``calm_macro``), the session provider, the alpha
registry, readiness, position monitoring and the order executor.
"""

import dataclasses
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest
from sqlalchemy import update

from agentic_trader.agent.calendar import MacroEvent
from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.agent.evaluator import LLMTradeEvaluation, RiskEvaluator
from agentic_trader.broker.base import OrderRequest, OrderResult
from agentic_trader.broker.paper import PaperBroker
from agentic_trader.config import ScanBudget
from agentic_trader.constants import AssetClass, ExecutionMode, SignalStatus, VolatilityRegime
from agentic_trader.execution.durable import WorkKind, WorkStatus
from agentic_trader.market.session import MarketSessionInfo, MarketSessionType
from agentic_trader.research.alpha.models import RegistrySnapshot
from agentic_trader.risk import RiskRule
from agentic_trader.screeners.base import ScreenerCandidate
from agentic_trader.storage.models import SignalRecord


def session(*, is_rth: bool = True) -> MarketSessionInfo:
    """An open SPY session, regular or extended hours, closing in two hours and opening again in a day."""
    now = datetime.now(UTC)
    return MarketSessionInfo(
        symbol="SPY",
        asset_class=AssetClass.EQUITY,
        is_open=True,
        is_rth=is_rth,
        session_type=MarketSessionType.RTH if is_rth else MarketSessionType.ETH,
        current_time=now,
        next_open=now + timedelta(days=1),
        next_close=now + timedelta(hours=2),
    )


def bars() -> pd.DataFrame:
    return pd.DataFrame({"Close": [100.0] * 30, "Volume": [1000] * 30})


def hourly_bars() -> pd.DataFrame:
    """One regular session of hourly bars with volume: every name clears the coverage gate."""
    index = pd.date_range("2026-10-05 13:30", periods=7, freq="h", tz="UTC")
    return pd.DataFrame({"Close": [100.0] * 7, "Volume": [1000] * 7}, index=index)


def setup(symbol: str, quality: float = 0.9) -> ScreenerCandidate:
    """A LONG pullback at 100 whose structural stop sits 2.02 below the entry (97.98)."""
    return ScreenerCandidate(
        contract=symbol,
        symbol=symbol,
        asset_class=AssetClass.EQUITY,
        timeframe="4h",
        strategy="TREND_PULLBACK",
        direction="LONG",
        current_price=100.0,
        ema_20=100.0,
        ema_50=99.0,
        ema_200=97.0,
        rsi_14=45.0,
        atr_14=1.0,
        candle_timestamp="2026-10-06T14:00:00+00:00",
        recent_swing_low=98.0,
        recent_swing_high=103.0,
        trigger_detail="fixture pullback",
        setup_quality=quality,
    )


@pytest.fixture
def desk(app_config, temp_db, mock_notifier, calm_macro):
    """The simulator desk as the daemon wires it, on the temp database.

    The copilot builds its own ``PaperBroker`` and ``EntryExecutionService`` (macro check:
    ``_entry_macro_check``); its evaluator is rebuilt exactly as the constructor wires it, over
    the stubbed calendar, regime detector and session provider, so the scan, the tap and
    admission read one macro context. The executor stands in for order submission.
    """
    app_config.copilot_chat_enabled = False
    app_config.contracts = {symbol: app_config.contracts[symbol] for symbol in ("SPY", "QQQ")}
    repository = AsyncMock()
    repository.snapshot.return_value = RegistrySnapshot(0, (), ())
    copilot = TradingCopilot(app_config, db=temp_db, notifier=mock_notifier, alpha_repository=repository)
    calm_macro(copilot)
    copilot.session_provider = AsyncMock()
    copilot.session_provider.is_session_active.return_value = (True, "Open")
    copilot.session_provider.get_session_info.return_value = session()
    copilot.earnings_calendar = None
    copilot.data_fetcher = MagicMock()
    copilot.data_fetcher.fetch_data.side_effect = lambda contract, ticker, include_fifteen_min=True: SimpleNamespace(
        contract=contract, daily=bars(), four_hour=bars(), hourly=hourly_bars()
    )
    copilot.data_fetcher.fetch_latest_price.return_value = 100.0
    copilot.strategy_engine = MagicMock()
    copilot.strategy_engine.scan_contract.return_value = []
    copilot.evaluator = RiskEvaluator(
        app_config,
        calendar=copilot.calendar,
        regime_detector=copilot.regime_detector,
        data_fetcher=copilot.data_fetcher,
        session_provider=copilot.session_provider,
        earnings_calendar=copilot.earnings_calendar,
    )
    copilot.alpha_shadow = AsyncMock()
    copilot.readiness = AsyncMock()
    copilot.earnings_drift = None
    # Reconciling held positions against a broker is not under test.
    copilot.monitor_positions = AsyncMock(return_value=0)
    copilot.entry_service.executor = AsyncMock()
    copilot.entry_service.executor.execute_order.return_value = OrderResult(success=True, order_id="simulated-entry")
    return copilot


def offer(desk, *setups: ScreenerCandidate) -> None:
    """The strategy engine finds exactly these setups, one per contract."""
    by_contract = {s.contract: s for s in setups}
    desk.strategy_engine.scan_contract.side_effect = lambda data, **kwargs: (
        [by_contract[data.contract]] if data.contract in by_contract else []
    )


def evaluations(desk) -> list[LLMTradeEvaluation]:
    """Every evaluation the real evaluator returns, in order (the scan records no collect-phase refusal)."""
    results: list[LLMTradeEvaluation] = []
    evaluate = desk.evaluator.evaluate_candidate

    async def recording(*args, **kwargs):
        result = await evaluate(*args, **kwargs)
        results.append(result)
        return result

    desk.evaluator.evaluate_candidate = recording
    return results


async def hold(db, symbol: str, *, asset_class: str = "EQUITY", notional: float = 1_000.0, risk: float = 50.0) -> int:
    """An EXECUTED LONG opened three days ago: in the book, outside today's card budget."""
    signal_id = await db.record_signal(
        contract=symbol,
        strategy="TREND_PULLBACK",
        direction="LONG",
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        risk_dollars=risk,
        notional_value=notional,
        status=SignalStatus.EXECUTED,
        asset_class=asset_class,
        quantity=10.0,
    )
    async with db.session_factory() as session_, session_.begin():
        await session_.execute(
            update(SignalRecord)
            .where(SignalRecord.id == signal_id)
            .values(timestamp=datetime.now(UTC) - timedelta(days=3))
        )
    return signal_id


async def pending_cards(db) -> list[dict]:
    return [s for s in await db.get_recent_signals(limit=20) if s["status"] == SignalStatus.PENDING]


def order_for(card: dict) -> OrderRequest:
    return OrderRequest(
        signal_id=card["id"],
        symbol=card["contract"],
        contract=card["contract"],
        asset_class=card["asset_class"],
        direction=card["direction"],
        quantity=card["quantity"],
        entry_price=card["entry_price"],
        stop_loss=card["stop_loss"],
        take_profit=card["take_profit"],
    )


async def tap_outcomes(db, signal_id: int) -> list[dict]:
    events = await db.workflows.events(stream=f"card/{signal_id}")
    return [e["payload"] for e in events if e["kind"] == "card_tap_assessed"]


async def test_a_card_built_under_an_elevated_regime_passes_its_own_tap_gate(desk, temp_db, calm_regime):
    elevated = dataclasses.replace(
        calm_regime, vix=24.0, vix_regime=VolatilityRegime.ELEVATED, min_rr_threshold=2.2, summary_text="elevated"
    )
    desk.regime_detector.get_regime.return_value = elevated
    offer(desk, setup("SPY"))

    await desk.run_scan(use_llm=False, budget=ScanBudget.FULL)

    [card] = await pending_cards(temp_db)
    entry, stop, target = card["entry_price"], card["stop_loss"], card["take_profit"]
    assert round((target - entry) / (entry - stop), 2) >= 2.2  # built at max(configured 2.0, regime 2.2)
    assert desk._regime_gate(order_for(card), card, elevated) is None
    # The real tap re-checks it under the cached regime, admission under the force-refreshed one.
    reply = await desk.execute_signal_by_id(card["id"])
    assert [outcome["outcome"] for outcome in await tap_outcomes(temp_db, card["id"])] == ["execute"]
    [work] = await temp_db.workflows.list_work(WorkKind.ENTRY)
    assert work.status == WorkStatus.ACCEPTED, work.result
    assert reply.ok is True
    desk.entry_service.executor.execute_order.assert_awaited_once()


def rejected_log(caplog, phase: str) -> list[tuple[str, str]]:
    """``candidate_rejected`` log records of one scan phase: (contract, rejection_rule)."""
    return [
        (record.contract, record.rejection_rule)
        for record in caplog.records
        if getattr(record, "event", None) == "candidate_rejected" and record.phase == phase
    ]


async def test_a_full_book_stops_the_card_at_scan_time(desk, temp_db, app_config, caplog):
    caplog.set_level(logging.INFO, logger="copilot")
    limit = app_config.portfolio.max_concurrent_positions
    for index in range(limit):
        await hold(temp_db, f"HELD{index}")
    results = evaluations(desk)
    offer(desk, setup("SPY"))

    await desk.run_scan(use_llm=False, budget=ScanBudget.FULL)

    assert await pending_cards(temp_db) == []
    [refused] = results  # the collect pass: the candidate never competes for a card
    assert refused.rejection_rule == RiskRule.CONCURRENT_POSITIONS
    assert refused.rejection_reason == f"Maximum concurrent positions ({limit}) reached."
    assert desk.last_scan_summary["approved"] == 0 and desk.last_scan_summary["sent"] == 0
    assert rejected_log(caplog, "collect") == [("SPY", RiskRule.CONCURRENT_POSITIONS)]


async def test_a_book_filled_by_this_scan_stops_the_next_card(desk, temp_db, app_config, caplog):
    caplog.set_level(logging.INFO, logger="copilot")
    app_config.scan.max_cards_per_scan = 2
    limit = app_config.portfolio.max_concurrent_positions
    for index in range(limit - 1):
        await hold(temp_db, f"HELD{index}")
    offer(desk, setup("SPY", quality=0.9), setup("QQQ", quality=0.8))

    await desk.run_scan(use_llm=False, budget=ScanBudget.FULL)

    assert [card["contract"] for card in await pending_cards(temp_db)] == ["SPY"]
    assert [(r["contract"], r["reason"]) for r in desk.last_scan_summary["runners_up"]] == [
        ("QQQ", f"rejected: Maximum concurrent positions ({limit}) reached.")
    ]
    assert rejected_log(caplog, "send") == [("QQQ", RiskRule.CONCURRENT_POSITIONS)]


async def test_empty_correlation_groups_disable_the_rule_at_both_layers(desk, temp_db, app_config):
    # /MES and SPY share us_broad_market in DEFAULT_CORRELATION_GROUPS; none applies when empty.
    app_config.portfolio.correlation_groups = {}
    await hold(temp_db, "/MES", asset_class="FUTURES", notional=29_000.0, risk=250.0)
    offer(desk, setup("SPY"))

    await desk.run_scan(use_llm=False, budget=ScanBudget.FULL)

    [card] = await pending_cards(temp_db)
    assert card["contract"] == "SPY"
    item, reason = await temp_db.workflows.enqueue_entry(order_for(card), app_config)
    assert item is not None and item.status == WorkStatus.QUEUED, reason


async def test_a_slashed_group_member_matches_the_held_root_symbol_at_both_layers(desk, temp_db, app_config):
    app_config.portfolio.correlation_groups = {"us_broad_market": ["/MES", "SPY"]}
    await hold(temp_db, "MES", asset_class="FUTURES", notional=29_000.0, risk=250.0)
    results = evaluations(desk)
    offer(desk, setup("SPY"))
    reason = "Correlation group 'us_broad_market' already has 1 LONG position(s) (MES); max 1."

    await desk.run_scan(use_llm=False, budget=ScanBudget.FULL)

    assert await pending_cards(temp_db) == []
    [refused] = results
    assert (refused.rejection_rule, refused.rejection_reason) == (RiskRule.CORRELATION_GROUP, reason)
    # Admission refuses a SPY card issued before the position opened, for the same group.
    signal_id = await temp_db.record_signal(
        contract="SPY",
        strategy="TREND_PULLBACK",
        direction="LONG",
        entry_price=100.0,
        stop_loss=97.98,
        take_profit=104.04,
        risk_dollars=202.0,
        notional_value=10_000.0,
        asset_class="EQUITY",
        quantity=100.0,
    )
    item, detail = await temp_db.workflows.enqueue_entry(
        order_for(await temp_db.get_signal_by_id(signal_id)), app_config
    )
    assert item is None and detail == reason


async def test_simulated_admission_rejects_during_a_macro_lockout(desk, temp_db, app_config):
    assert app_config.execution_mode == ExecutionMode.PAPER and isinstance(desk.broker, PaperBroker)
    offer(desk, setup("SPY"))
    await desk.run_scan(use_llm=False, budget=ScanBudget.FULL)
    [card] = await pending_cards(temp_db)
    item, reason = await temp_db.workflows.enqueue_entry(order_for(card), app_config)
    assert item is not None and item.status == WorkStatus.QUEUED, reason
    # A tier-1 release's lockout window opens while the card waits in the queue.
    event_at = datetime.now(UTC).replace(second=0, microsecond=0)
    desk.calendar.is_in_lockout_window.return_value = (
        True,
        MacroEvent(title="CPI", country="USD", impact="High", timestamp=event_at),
    )

    assert await desk.entry_service.dispatch_one()

    work = await temp_db.workflows.get_work(item.id)
    assert work.status == WorkStatus.REJECTED
    assert work.result["error_message"] == f"Macro event lockout: CPI at {event_at:%H:%M} UTC."
    assert (await temp_db.get_signal_by_id(card["id"]))["status"] == SignalStatus.FAILED
    desk.entry_service.executor.execute_order.assert_not_awaited()


@pytest.mark.parametrize("enforce_rth", [False, True])
async def test_tap_outside_rth_is_allowed_when_rth_is_not_enforced(desk, temp_db, app_config, enforce_rth):
    app_config.session.enforce_rth = enforce_rth
    offer(desk, setup("SPY"))
    await desk.run_scan(use_llm=False, budget=ScanBudget.FULL)  # issued in regular hours
    [card] = await pending_cards(temp_db)
    desk.session_provider.get_session_info.return_value = session(is_rth=False)  # tapped in extended hours

    reply = await desk.execute_signal_by_id(card["id"])

    [assessed] = await tap_outcomes(temp_db, card["id"])
    if enforce_rth:
        assert (assessed["outcome"], assessed["reason"]) == ("expired", "Card expired: its session has ended.")
        assert (await temp_db.get_signal_by_id(card["id"]))["status"] == SignalStatus.EXPIRED
        assert await temp_db.workflows.list_work(WorkKind.ENTRY) == []
    else:
        assert assessed["outcome"] == "execute"
        [work] = await temp_db.workflows.list_work(WorkKind.ENTRY)
        assert work.status == WorkStatus.ACCEPTED, work.result
        assert reply.ok is True
