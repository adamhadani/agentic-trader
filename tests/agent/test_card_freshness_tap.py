"""Tap-time card freshness: the copilot re-assesses a card before entry authorization.

The freshness decision never authorizes an order by itself: ``EXECUTE`` continues into
the unchanged ``EntryExecutionService.authorize`` with the original bracket, while every
other outcome refuses the tap (a re-priced card is a *new* signal needing a fresh tap).
"""

import asyncio
import inspect
import math
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner
from sqlalchemy import update

from agentic_trader.agent import copilot as copilot_module
from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.agent.earnings import EarningsEvent, EarningsLookup, EarningsTiming
from agentic_trader.agent.evaluator import LLMTradeEvaluation
from agentic_trader.cli.main import cli
from agentic_trader.config import ScanBudget
from agentic_trader.constants import AssetClass, SignalStatus
from agentic_trader.execution.durable import EventKind, WorkKind
from agentic_trader.execution.freshness import ExecutionReply
from agentic_trader.market.session import ET_TZ, MarketSessionInfo, MarketSessionType
from agentic_trader.notifier.telegram_bot import TelegramNotifier
from agentic_trader.storage.models import SignalRecord
from tests.agent.test_scan_budget import budget_desk  # noqa: F401  (pytest fixture)


NEXT_OPEN = datetime(2026, 9, 24, 13, 30, tzinfo=UTC)


def session_info(*, is_open=True, is_rth=True, next_close=None, next_open=NEXT_OPEN):
    now = datetime.now(UTC)
    return MarketSessionInfo(
        symbol="SPY",
        asset_class=AssetClass.EQUITY,
        is_open=is_open,
        is_rth=is_rth,
        session_type=MarketSessionType.RTH if is_rth else MarketSessionType.CLOSED,
        current_time=now,
        next_open=next_open,
        next_close=next_close,
    )


def original_evaluation(*, entry=100.0, stop=95.0, target=120.0, quantity=10.0) -> LLMTradeEvaluation:
    return LLMTradeEvaluation(
        approved=True,
        contract="SPY",
        direction="LONG",
        entry_price=entry,
        stop_loss=stop,
        take_profit=target,
        stop_distance_points=abs(entry - stop),
        target_distance_points=abs(target - entry),
        risk_reward_ratio=abs(target - entry) / abs(entry - stop),
        risk_dollars=abs(entry - stop) * quantity,
        reward_dollars=abs(target - entry) * quantity,
        notional_value=entry * quantity,
        effective_leverage=0.01,
        macro_clearance=True,
        thesis_summary="Original thesis",
        quantity=quantity,
        asset_class=AssetClass.EQUITY,
        sizing_tiers=[{"name": "full", "quantity": quantity}],
        earnings_note="No report within 7 days",
    )


@pytest.fixture
def tap_desk(app_config, temp_db, mock_notifier):
    app_config.copilot_chat_enabled = False
    copilot = TradingCopilot(
        app_config,
        db=temp_db,
        broker=MagicMock(supports_activity_ledger=False, supports_trade_stream=False),
        notifier=mock_notifier,
        entry_service=AsyncMock(),
        alpha_repository=AsyncMock(),
    )
    copilot.entry_service.authorize.return_value = (None, "stubbed admission")
    copilot.data_fetcher = MagicMock()
    copilot.data_fetcher.fetch_latest_price.return_value = 100.5
    copilot.session_provider = AsyncMock()
    copilot.session_provider.get_session_info.return_value = session_info(
        next_close=datetime.now(UTC) + timedelta(hours=2)
    )
    copilot.calendar = AsyncMock()
    copilot.calendar.is_in_lockout_window.return_value = (False, None)
    copilot.regime_detector = AsyncMock()
    copilot.regime_detector.get_regime.return_value = SimpleNamespace(
        summary_text="calm", breakout_allowed=True, min_rr_threshold=2.0, risk_multiplier=1.0
    )
    copilot.earnings_calendar = AsyncMock()
    copilot.earnings_calendar.next_earnings.return_value = EarningsLookup(
        event=None, verified=True, horizon_end=datetime.now(UTC).date()
    )
    return copilot


async def record_card(
    db,
    *,
    age_seconds: float = 60,
    valid_until: datetime | None | str = "default",
    entry=100.0,
    stop=95.0,
    target=120.0,
    quantity=10.0,
    provenance_extra: dict | None = None,
    alpha_version: str | None = None,
    alpha_policy: dict | None = None,
) -> int:
    evaluation = original_evaluation(entry=entry, stop=stop, target=target, quantity=quantity)
    provenance = {"setup_quality": 0.9, "rank": 1, **(provenance_extra or {})}
    if valid_until == "default":
        valid_until = datetime.now(UTC) + timedelta(hours=2)
    if valid_until is not None:
        provenance["valid_until"] = valid_until.isoformat()
    signal_id = await db.record_signal(
        contract="SPY",
        strategy="TREND_PULLBACK",
        timeframe="4h",
        direction="LONG",
        entry_price=entry,
        stop_loss=stop,
        take_profit=target,
        risk_dollars=evaluation.risk_dollars,
        reward_dollars=evaluation.reward_dollars,
        notional_value=evaluation.notional_value,
        raw_response=evaluation.model_dump_json(),
        asset_class=AssetClass.EQUITY,
        quantity=quantity,
        decision_provenance=provenance,
        alpha_version=alpha_version,
        alpha_policy=alpha_policy,
    )
    async with db.session_factory() as session, session.begin():
        await session.execute(
            update(SignalRecord)
            .where(SignalRecord.id == signal_id)
            .values(timestamp=datetime.now(UTC) - timedelta(seconds=age_seconds))
        )
    return signal_id


async def tap_events(db, signal_id):
    return [e for e in await db.workflows.events(stream=f"card/{signal_id}") if e["kind"] == "card_tap_assessed"]


async def test_fresh_card_executes_the_original_bracket_unchanged(tap_desk, temp_db):
    sid = await record_card(temp_db)

    reply = await tap_desk.execute_signal_by_id(sid)

    assert isinstance(reply, ExecutionReply)
    assert reply.ok is False and "stubbed admission" in reply.text
    tap_desk.entry_service.authorize.assert_awaited_once()
    request = tap_desk.entry_service.authorize.await_args.args[0]
    assert (request.signal_id, request.entry_price, request.stop_loss, request.take_profit, request.quantity) == (
        sid,
        100.0,
        95.0,
        120.0,
        10.0,
    )
    tap_desk.data_fetcher.fetch_latest_price.assert_called_once_with("SPY")
    events = await tap_events(temp_db, sid)
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["outcome"] == "execute" and payload["signal_id"] == sid and payload["price"] == 100.5
    assert set(payload) >= {"signal_id", "outcome", "reason", "age_seconds", "price", "r_consumed", "tapped_at"}
    assert payload["applied"] is True and payload["new_signal_id"] is None and payload["policy_locked"] is False
    assert EventKind.CARD_TAP_ASSESSED == "card_tap_assessed"


async def test_stale_card_is_repriced_into_one_new_pending_card_without_authorizing(tap_desk, temp_db):
    sid = await record_card(temp_db, age_seconds=3600)
    tap_desk.data_fetcher.fetch_latest_price.return_value = 102.0  # +0.4R; R:R at 102 is 18/7

    reply = await tap_desk.execute_signal_by_id(sid)

    tap_desk.entry_service.authorize.assert_not_awaited()
    assert reply.ok is False and reply.offer_reevaluate is False
    assert (await temp_db.get_signal_by_id(sid))["status"] == SignalStatus.EXPIRED
    pending = [s for s in await temp_db.get_recent_signals(limit=10) if s["status"] == SignalStatus.PENDING]
    assert len(pending) == 1
    new = pending[0]
    assert new["id"] != sid and f"#{new['id']}" in reply.text and f"#{sid}" in reply.text and "+0.40R" in reply.text
    assert (new["entry_price"], new["stop_loss"], new["take_profit"]) == (102.0, 95.0, 120.0)
    assert new["quantity"] == 7.0  # 50 risk dollars / 7 per share, whole shares
    assert new["risk_dollars"] == pytest.approx(49.0) and new["notional_value"] == pytest.approx(714.0)
    assert (new["strategy"], new["timeframe"], new["direction"], new["asset_class"]) == (
        "TREND_PULLBACK",
        "4h",
        "LONG",
        "EQUITY",
    )
    old = await temp_db.get_signal_by_id(sid)
    provenance = new["decision_provenance"]
    assert provenance["reprices"] == sid
    assert provenance["valid_until"] == old["decision_provenance"]["valid_until"]
    assert provenance["setup_quality"] == 0.9
    assert provenance["tap_latency_seconds"] == pytest.approx(3600, abs=30)
    assert provenance["r_consumed"] == pytest.approx(0.4)
    assert datetime.fromisoformat(provenance["first_issued_at"]).tzinfo is not None

    notifications = await temp_db.workflows.list_work(WorkKind.NOTIFICATION)
    assert len(notifications) == 1
    arguments = notifications[0].payload["arguments"]
    assert arguments["signal_id"] == new["id"] and arguments["reprices"] == sid
    assert arguments["first_issued_at"] == provenance["first_issued_at"]
    assert arguments["valid_until"] == provenance["valid_until"]
    assert arguments["strategy"] == "TREND_PULLBACK" and arguments["regime_summary"] == "calm"
    rebuilt = LLMTradeEvaluation.model_validate(arguments["eval_res"])
    assert (rebuilt.entry_price, rebuilt.quantity, rebuilt.stop_loss, rebuilt.take_profit) == (102.0, 7.0, 95.0, 120.0)
    assert rebuilt.risk_reward_ratio == pytest.approx(18 / 7)
    assert rebuilt.thesis_summary == "Original thesis"
    # The durable payload must be deliverable by the real Telegram notifier signature.
    inspect.signature(TelegramNotifier.send_signal_alert).bind(None, **{**arguments, "eval_res": rebuilt})

    [event] = await tap_events(temp_db, sid)
    assert event["payload"]["outcome"] == "reprice"
    assert event["payload"]["applied"] is True and event["payload"]["new_signal_id"] == new["id"]

    # A second tap on the re-priced original is refused: it is EXPIRED (not PENDING), and
    # -- like any EXPIRED card -- still offers a path to a fresh one instead of a dead end.
    again = await tap_desk.execute_signal_by_id(sid)
    assert again.ok is False and again.offer_reevaluate is True
    assert "Card expired: its session has ended." in again.text
    tap_desk.entry_service.authorize.assert_not_awaited()
    assert len(await temp_db.workflows.list_work(WorkKind.NOTIFICATION)) == 1


async def test_tap_on_a_card_already_expired_by_the_sweep_offers_reevaluate_not_a_dead_end(tap_desk, temp_db):
    # The session-close sweep (or a duplicate SIGNAL delivery already struck) can expire a
    # card before the operator's tap ever reaches it. Before this fix that hit the generic
    # "only PENDING signals can be executed" refusal with no Re-evaluate button -- a
    # dead end, since Telegram had already cleared the keyboard on tap.
    sid = await record_card(temp_db)
    await temp_db.update_signal_status(sid, SignalStatus.EXPIRED)

    reply = await tap_desk.execute_signal_by_id(sid)

    assert reply.ok is False and reply.offer_reevaluate is True
    assert "Card expired: its session has ended." in reply.text
    tap_desk.entry_service.authorize.assert_not_awaited()


async def test_reprice_race_lost_to_a_concurrent_sweep_offers_reevaluate(tap_desk, temp_db):
    # Simulates the sweep committing between the tap-time read and the atomic replace:
    # `replace_signal`'s conditional PENDING->EXPIRED update finds the row already EXPIRED
    # and returns None, so `_current_status_reply` must not dead-end on the generic message.
    sid = await record_card(temp_db, age_seconds=3600)
    tap_desk.data_fetcher.fetch_latest_price.return_value = 102.0  # +0.4R -> REPRICE, not MISSED

    async def racing_replace_signal(*args, **kwargs):
        await temp_db.update_signal_status(sid, SignalStatus.EXPIRED)

    tap_desk.db.replace_signal = racing_replace_signal

    reply = await tap_desk.execute_signal_by_id(sid)

    assert reply.ok is False and reply.offer_reevaluate is True
    assert "Card expired: its session has ended." in reply.text


async def test_reprice_that_rounds_to_zero_shares_is_missed(tap_desk, temp_db):
    sid = await record_card(temp_db, age_seconds=3600, quantity=1.0)
    tap_desk.data_fetcher.fetch_latest_price.return_value = 102.0  # 5 / 7 of a share

    reply = await tap_desk.execute_signal_by_id(sid)

    assert reply.offer_reevaluate is True and "rounds to zero" in reply.text
    assert (await temp_db.get_signal_by_id(sid))["status"] == SignalStatus.EXPIRED
    assert await temp_db.workflows.list_work(WorkKind.NOTIFICATION) == []
    assert [e["payload"]["outcome"] for e in await tap_events(temp_db, sid)] == ["missed"]


async def test_card_through_its_stop_is_missed_and_offers_reevaluation(tap_desk, temp_db):
    sid = await record_card(temp_db)
    tap_desk.data_fetcher.fetch_latest_price.return_value = 94.0

    reply = await tap_desk.execute_signal_by_id(sid)

    assert reply == ExecutionReply(False, reply.text, offer_reevaluate=True)
    assert reply.text.startswith("⌛") and "stop" in reply.text
    assert (await temp_db.get_signal_by_id(sid))["status"] == SignalStatus.EXPIRED
    tap_desk.entry_service.authorize.assert_not_awaited()


async def test_low_reward_risk_reason_is_html_escaped(tap_desk, temp_db):
    sid = await record_card(temp_db, age_seconds=3600)
    tap_desk.data_fetcher.fetch_latest_price.return_value = 110.0  # R:R 10/15 < 2

    reply = await tap_desk.execute_signal_by_id(sid)

    assert reply.offer_reevaluate is True and "&lt;" in reply.text and "<" not in reply.text.replace("<b>", "")


async def test_card_past_its_session_expires_with_the_next_regular_open(tap_desk, temp_db):
    sid = await record_card(temp_db, valid_until=datetime.now(UTC) - timedelta(minutes=1))

    reply = await tap_desk.execute_signal_by_id(sid)

    assert reply.ok is False and reply.offer_reevaluate is True
    assert "2026-09-24 13:30 UTC" in reply.text and "09:30 NY" in reply.text
    assert (await temp_db.get_signal_by_id(sid))["status"] == SignalStatus.EXPIRED
    assert [e["payload"]["outcome"] for e in await tap_events(temp_db, sid)] == ["expired"]
    tap_desk.entry_service.authorize.assert_not_awaited()


async def test_card_tapped_outside_rth_expires(tap_desk, temp_db):
    sid = await record_card(temp_db)
    tap_desk.session_provider.get_session_info.return_value = session_info(is_open=False, is_rth=False)

    reply = await tap_desk.execute_signal_by_id(sid)

    assert reply.offer_reevaluate is True and "Next regular open" in reply.text
    assert (await temp_db.get_signal_by_id(sid))["status"] == SignalStatus.EXPIRED


@pytest.mark.parametrize("failure", ["none", "nan", "raises"])
async def test_price_failure_leaves_the_card_pending(tap_desk, temp_db, failure):
    sid = await record_card(temp_db)
    if failure == "raises":
        tap_desk.data_fetcher.fetch_latest_price.side_effect = TimeoutError("feed down")
    else:
        tap_desk.data_fetcher.fetch_latest_price.return_value = None if failure == "none" else math.nan

    reply = await tap_desk.execute_signal_by_id(sid)

    assert reply == ExecutionReply(False, "⚠️ Current price unavailable; try again shortly.", retryable=True)
    assert (await temp_db.get_signal_by_id(sid))["status"] == SignalStatus.PENDING
    tap_desk.entry_service.authorize.assert_not_awaited()
    [event] = await tap_events(temp_db, sid)
    assert event["payload"]["outcome"] == "unavailable" and event["payload"]["applied"] is False
    assert "price unavailable" in event["payload"]["reason"].lower()


async def test_disabled_freshness_is_the_legacy_path(tap_desk, temp_db, app_config):
    app_config.execution.card_freshness.enabled = False
    sid = await record_card(temp_db, age_seconds=3600, valid_until=datetime.now(UTC) - timedelta(hours=1))

    reply = await tap_desk.execute_signal_by_id(sid)

    assert reply.ok is False and "stubbed admission" in reply.text
    tap_desk.entry_service.authorize.assert_awaited_once()
    tap_desk.data_fetcher.fetch_latest_price.assert_not_called()
    tap_desk.session_provider.get_session_info.assert_not_awaited()
    assert await tap_events(temp_db, sid) == []
    assert (await temp_db.get_signal_by_id(sid))["status"] == SignalStatus.PENDING


async def test_earnings_blackout_turns_a_fresh_card_into_missed(tap_desk, temp_db):
    sid = await record_card(temp_db)
    today = datetime.now(UTC).astimezone(ET_TZ).date()
    tap_desk.earnings_calendar.next_earnings.return_value = EarningsLookup(
        event=EarningsEvent("SPY", today + timedelta(days=2), EarningsTiming.AFTER_HOURS),
        verified=True,
        horizon_end=today + timedelta(days=7),
    )

    reply = await tap_desk.execute_signal_by_id(sid)

    assert reply.offer_reevaluate is True and "Earnings Blackout" in reply.text
    assert (await temp_db.get_signal_by_id(sid))["status"] == SignalStatus.EXPIRED
    tap_desk.entry_service.authorize.assert_not_awaited()


async def test_earnings_calendar_failure_fails_open_like_the_evaluator(tap_desk, temp_db):
    sid = await record_card(temp_db)
    tap_desk.earnings_calendar.next_earnings.side_effect = RuntimeError("calendar down")

    await tap_desk.execute_signal_by_id(sid)

    tap_desk.entry_service.authorize.assert_awaited_once()


async def test_macro_gate_turns_a_fresh_card_into_missed(tap_desk, temp_db):
    sid = await record_card(temp_db)
    tap_desk.calendar.is_in_lockout_window.return_value = (True, SimpleNamespace(title="CPI"))

    reply = await tap_desk.execute_signal_by_id(sid)

    assert reply.offer_reevaluate is True and "CPI" in reply.text
    tap_desk.entry_service.authorize.assert_not_awaited()


async def test_assessment_journal_failure_never_blocks_the_tap(tap_desk, temp_db):
    sid = await record_card(temp_db)
    tap_desk.db.workflows.record_card_tap = AsyncMock(side_effect=RuntimeError("journal down"))

    await tap_desk.execute_signal_by_id(sid)

    tap_desk.entry_service.authorize.assert_awaited_once()


async def test_scan_records_valid_until_once_per_recorded_card(budget_desk, temp_db, app_config):  # noqa: F811
    close = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=3)
    budget_desk.session_provider.get_session_info.return_value = session_info(next_close=close)

    await budget_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.SESSION)

    sent = await temp_db.get_recent_signals(limit=10)
    assert len(sent) == 2  # max_cards_per_session
    for signal in sent:
        assert datetime.fromisoformat(signal["decision_provenance"]["valid_until"]) == close
    assert sorted(c.args[0] for c in budget_desk.session_provider.get_session_info.await_args_list) == sorted(
        s["contract"] for s in sent
    )
    payloads = [n.payload["arguments"] for n in await temp_db.workflows.list_work(WorkKind.NOTIFICATION)]
    assert len(payloads) == 2 and all(datetime.fromisoformat(p["valid_until"]) == close for p in payloads)


async def test_scan_omits_valid_until_when_the_session_close_is_unavailable(budget_desk, temp_db):  # noqa: F811
    budget_desk.session_provider.get_session_info.side_effect = RuntimeError("clock down")

    await budget_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.SESSION)

    sent = await temp_db.get_recent_signals(limit=10)
    assert len(sent) == 2 and all("valid_until" not in s["decision_provenance"] for s in sent)
    payloads = [n.payload["arguments"] for n in await temp_db.workflows.list_work(WorkKind.NOTIFICATION)]
    assert all("valid_until" not in p for p in payloads)


async def test_dedup_exemption_applies_to_the_exact_setup_only(budget_desk, temp_db):  # noqa: F811
    budget_desk.db.is_duplicate_recent = AsyncMock(return_value=True)

    await budget_desk.run_scan(
        use_llm=False,
        dry_run=False,
        budget=ScanBudget.NONE,
        dedup_exempt_setups=frozenset({("CCC", "TREND_PULLBACK", "4h", None)}),
    )

    assert [s["contract"] for s in await temp_db.get_recent_signals(limit=10)] == ["CCC"]
    checked = {c.args[0] for c in budget_desk.db.is_duplicate_recent.await_args_list}
    assert checked == {"AAA", "BBB", "DDD", "EEE"}


@pytest.mark.parametrize(
    "setup",
    [
        ("CCC", "SQUEEZE_BREAKOUT", "4h", None),  # another strategy on the same contract
        ("CCC", "TREND_PULLBACK", "1h", None),  # another timeframe
        ("CCC", "TREND_PULLBACK", "4h", "alpha-v9"),  # another alpha version
    ],
)
async def test_dedup_exemption_never_covers_a_different_setup(budget_desk, temp_db, setup):  # noqa: F811
    budget_desk.db.is_duplicate_recent = AsyncMock(return_value=True)

    await budget_desk.run_scan(
        use_llm=False, dry_run=False, budget=ScanBudget.NONE, dedup_exempt_setups=frozenset({setup})
    )

    assert await temp_db.get_recent_signals(limit=10) == []


async def message_texts(db):
    return [
        n.payload["arguments"]["text"]
        for n in await db.workflows.list_work(WorkKind.NOTIFICATION)
        if n.payload["kind"] == "message"
    ]


async def finish_reevaluations(copilot):
    await asyncio.gather(*list(copilot.reevaluation_tasks))


async def test_reevaluate_schedules_a_single_contract_scan_without_budget_or_dedup(tap_desk, temp_db):
    sid = await record_card(temp_db)
    await temp_db.expire_signal(sid)
    tap_desk.run_scan = AsyncMock(return_value={"sent": 1, "runners_up": []})

    reply = await tap_desk.reevaluate_signal(sid)

    assert reply.ok is True and reply.text == "🔄 Re-evaluating SPY… a fresh card or a result message will follow."
    await finish_reevaluations(tap_desk)
    tap_desk.run_scan.assert_awaited_once_with(
        symbols=["SPY"],
        budget=ScanBudget.NONE,
        dedup_exempt_setups=frozenset({("SPY", "TREND_PULLBACK", "4h", None)}),
        scan_lock_timeout=copilot_module.REEVALUATE_SCAN_WAIT_SECONDS,
    )
    assert await message_texts(temp_db) == []  # the fresh card itself is the result


async def test_reevaluate_does_not_run_the_scan_inline(tap_desk, temp_db):
    sid = await record_card(temp_db)
    await temp_db.expire_signal(sid)
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_scan(**kwargs):
        started.set()
        await release.wait()
        return {"sent": 1, "runners_up": []}

    tap_desk.run_scan = AsyncMock(side_effect=slow_scan)

    reply = await asyncio.wait_for(tap_desk.reevaluate_signal(sid), timeout=1)

    assert "Re-evaluating" in reply.text and not release.is_set()
    await asyncio.wait_for(started.wait(), timeout=1)
    release.set()
    await finish_reevaluations(tap_desk)


async def test_reevaluate_without_a_setup_reports_through_the_outbox(tap_desk, temp_db):
    sid = await record_card(temp_db)
    await temp_db.expire_signal(sid)
    tap_desk.run_scan = AsyncMock(
        return_value={"sent": 0, "runners_up": [{"contract": "SPY", "reason": "rejected: <weak>"}]}
    )

    await tap_desk.reevaluate_signal(sid)
    await finish_reevaluations(tap_desk)

    [text] = await message_texts(temp_db)
    assert "No valid setup for SPY right now" in text and "rejected: &lt;weak&gt;" in text
    tap_desk.notifier.send_message.assert_not_called()  # never a bare bot send


async def test_reevaluate_reports_a_busy_scan_lock_through_the_outbox(tap_desk, temp_db, monkeypatch):
    sid = await record_card(temp_db)
    await temp_db.expire_signal(sid)
    monkeypatch.setattr(copilot_module, "REEVALUATE_SCAN_WAIT_SECONDS", 0.05)
    await tap_desk._scan_lock.acquire()
    try:
        await tap_desk.reevaluate_signal(sid)
        await finish_reevaluations(tap_desk)
    finally:
        tap_desk._scan_lock.release()

    [text] = await message_texts(temp_db)
    assert "another scan held the scanner" in text.lower()


async def test_reevaluate_failure_is_logged_and_reported(tap_desk, temp_db):
    sid = await record_card(temp_db)
    await temp_db.expire_signal(sid)
    tap_desk.run_scan = AsyncMock(side_effect=RuntimeError("provider down"))

    await tap_desk.reevaluate_signal(sid)
    await finish_reevaluations(tap_desk)

    [text] = await message_texts(temp_db)
    assert "SPY" in text and "failed" in text


@pytest.mark.parametrize("status", [SignalStatus.PENDING, SignalStatus.EXECUTED])
async def test_reevaluate_accepts_only_expired_cards(tap_desk, temp_db, status):
    sid = await record_card(temp_db)
    if status != SignalStatus.PENDING:
        await temp_db.update_signal_status(sid, status)
    tap_desk.run_scan = AsyncMock()

    reply = await tap_desk.reevaluate_signal(sid)

    assert reply == ExecutionReply(False, f"Signal #{sid} is {status}; nothing to re-evaluate.")
    assert tap_desk.reevaluation_tasks == set()
    tap_desk.run_scan.assert_not_awaited()


async def test_reevaluate_is_refused_outside_rth(tap_desk, temp_db):
    sid = await record_card(temp_db)
    await temp_db.expire_signal(sid)
    tap_desk.session_provider.get_session_info.return_value = session_info(is_open=False, is_rth=False)
    tap_desk.run_scan = AsyncMock()

    reply = await tap_desk.reevaluate_signal(sid)

    assert tap_desk.reevaluation_tasks == set()
    tap_desk.run_scan.assert_not_awaited()
    assert reply.ok is False and reply.text.startswith("Market closed") and "2026-09-24 13:30 UTC" in reply.text


async def test_versioned_alpha_card_is_never_repriced_but_executes_its_original_bracket(tap_desk, temp_db):
    sid = await record_card(
        temp_db, age_seconds=3600, alpha_version="alpha-v1", alpha_policy={"entry": {"kind": "limit"}}
    )
    tap_desk.data_fetcher.fetch_latest_price.return_value = 102.0  # would re-price an unversioned card

    await tap_desk.execute_signal_by_id(sid)

    tap_desk.entry_service.authorize.assert_awaited_once()
    request = tap_desk.entry_service.authorize.await_args.args[0]
    assert (request.entry_price, request.stop_loss, request.take_profit, request.quantity) == (100.0, 95.0, 120.0, 10)
    assert [s["id"] for s in await temp_db.get_recent_signals(limit=10)] == [sid]
    [event] = await tap_events(temp_db, sid)
    assert event["payload"]["outcome"] == "execute" and event["payload"]["policy_locked"] is True


async def test_versioned_alpha_card_through_its_stop_is_still_missed(tap_desk, temp_db):
    sid = await record_card(temp_db, alpha_version="alpha-v1")
    tap_desk.data_fetcher.fetch_latest_price.return_value = 94.0

    reply = await tap_desk.execute_signal_by_id(sid)

    assert reply.offer_reevaluate is True
    tap_desk.entry_service.authorize.assert_not_awaited()
    assert (await temp_db.get_signal_by_id(sid))["status"] == SignalStatus.EXPIRED


async def test_regime_reward_risk_threshold_above_config_governs_and_uses_the_cached_regime(tap_desk, temp_db):
    sid = await record_card(temp_db, age_seconds=3600)
    tap_desk.data_fetcher.fetch_latest_price.return_value = 102.0  # R:R 18/7 = 2.57
    tap_desk.regime_detector.get_regime.return_value = SimpleNamespace(
        summary_text="stressed", breakout_allowed=True, min_rr_threshold=3.0, risk_multiplier=1.0
    )

    reply = await tap_desk.execute_signal_by_id(sid)

    assert reply.offer_reevaluate is True and "3.0" in reply.text
    assert (await temp_db.get_signal_by_id(sid))["status"] == SignalStatus.EXPIRED
    for call in tap_desk.regime_detector.get_regime.await_args_list:
        assert not call.kwargs.get("force_refresh")


async def test_slow_tap_time_reads_are_bounded_and_leave_the_card_pending(tap_desk, temp_db, monkeypatch):
    sid = await record_card(temp_db)
    monkeypatch.setattr(copilot_module, "TAP_CHECK_TIMEOUT_SECONDS", 0.05)

    async def slow_session(*args, **kwargs):
        await asyncio.sleep(5)

    tap_desk.session_provider.get_session_info.side_effect = slow_session

    reply = await asyncio.wait_for(tap_desk.execute_signal_by_id(sid), timeout=2)

    assert reply == ExecutionReply(False, "⚠️ Checks unavailable; try again shortly.", retryable=True)
    assert (await temp_db.get_signal_by_id(sid))["status"] == SignalStatus.PENDING
    tap_desk.entry_service.authorize.assert_not_awaited()
    [event] = await tap_events(temp_db, sid)
    assert event["payload"]["outcome"] == "unavailable" and "timed out" in event["payload"]["reason"]


async def test_a_failing_gate_read_is_a_retryable_refusal(tap_desk, temp_db):
    sid = await record_card(temp_db)
    tap_desk.regime_detector.get_regime.side_effect = ValueError("VIX data unavailable")

    reply = await tap_desk.execute_signal_by_id(sid)

    assert reply == ExecutionReply(False, "⚠️ Checks unavailable; try again shortly.", retryable=True)
    assert (await temp_db.get_signal_by_id(sid))["status"] == SignalStatus.PENDING
    [event] = await tap_events(temp_db, sid)
    assert event["payload"]["outcome"] == "unavailable" and "ValueError" in event["payload"]["reason"]


async def test_a_failing_session_read_is_journaled_as_unavailable(tap_desk, temp_db):
    sid = await record_card(temp_db)
    tap_desk.session_provider.get_session_info.side_effect = RuntimeError("clock down")

    reply = await tap_desk.execute_signal_by_id(sid)

    assert reply.retryable is True
    [event] = await tap_events(temp_db, sid)
    assert event["payload"]["outcome"] == "unavailable" and event["payload"]["price"] == 100.5


async def test_terminal_refusals_are_not_retryable(tap_desk, temp_db):
    sid = await record_card(temp_db)
    tap_desk.data_fetcher.fetch_latest_price.return_value = 94.0

    assert (await tap_desk.execute_signal_by_id(sid)).retryable is False
    assert (await tap_desk.execute_signal_by_id(sid)).retryable is False  # now not PENDING


async def test_replacement_entry_is_rounded_to_the_tick(tap_desk, temp_db):
    sid = await record_card(temp_db, age_seconds=3600)
    tap_desk.data_fetcher.fetch_latest_price.return_value = 102.0049

    await tap_desk.execute_signal_by_id(sid)

    [new] = [s for s in await temp_db.get_recent_signals(limit=10) if s["id"] != sid]
    assert new["entry_price"] == 102.0


async def test_replacement_is_sized_from_the_tapped_tier(tap_desk, temp_db):
    sid = await record_card(temp_db, age_seconds=3600)
    tap_desk.data_fetcher.fetch_latest_price.return_value = 102.0

    await tap_desk.execute_signal_by_id(sid, quantity=5)

    [new] = [s for s in await temp_db.get_recent_signals(limit=10) if s["id"] != sid]
    assert new["quantity"] == 3.0  # 5 shares x 5 risk / 7 per share, whole shares


async def test_lost_race_is_journaled_as_not_applied(tap_desk, temp_db):
    sid = await record_card(temp_db, age_seconds=3600)
    tap_desk.data_fetcher.fetch_latest_price.return_value = 102.0
    tap_desk.db.replace_signal = AsyncMock(return_value=None)

    reply = await tap_desk.execute_signal_by_id(sid)

    assert reply.ok is False and "status" in reply.text
    [event] = await tap_events(temp_db, sid)
    assert event["payload"]["outcome"] == "reprice"
    assert event["payload"]["applied"] is False and event["payload"]["new_signal_id"] is None


async def test_expiry_lost_race_is_journaled_as_not_applied(tap_desk, temp_db):
    sid = await record_card(temp_db)
    tap_desk.data_fetcher.fetch_latest_price.return_value = 94.0
    tap_desk.db.expire_signal = AsyncMock(return_value=False)

    reply = await tap_desk.execute_signal_by_id(sid)

    assert reply.offer_reevaluate is False
    [event] = await tap_events(temp_db, sid)
    assert event["payload"]["outcome"] == "missed" and event["payload"]["applied"] is False


async def test_concurrent_reevaluations_schedule_exactly_one_scan(tap_desk, temp_db):
    sid = await record_card(temp_db)
    await temp_db.expire_signal(sid)
    tap_desk.run_scan = AsyncMock(return_value={"sent": 1, "runners_up": []})

    replies = await asyncio.gather(tap_desk.reevaluate_signal(sid), tap_desk.reevaluate_signal(sid))
    await finish_reevaluations(tap_desk)
    later = await tap_desk.reevaluate_signal(sid)  # e.g. Telegram redelivering the callback

    texts = sorted(r.text for r in replies)
    assert texts[0].startswith("Re-evaluation of #") and "already requested" in texts[0]
    assert texts[1].startswith("🔄 Re-evaluating SPY")
    assert later == ExecutionReply(False, f"Re-evaluation of #{sid} already requested.")
    tap_desk.run_scan.assert_awaited_once()


@pytest.mark.parametrize("live_status", [SignalStatus.PENDING, SignalStatus.SUBMITTING])
async def test_reevaluate_is_refused_while_a_live_card_exists_for_the_contract(tap_desk, temp_db, live_status):
    sid = await record_card(temp_db)
    await temp_db.expire_signal(sid)
    live = await record_card(temp_db)
    if live_status != SignalStatus.PENDING:
        await temp_db.update_signal_status(live, live_status)
    tap_desk.run_scan = AsyncMock()

    reply = await tap_desk.reevaluate_signal(sid)

    assert reply == ExecutionReply(False, f"A live card for SPY already exists (#{live}).")
    assert tap_desk.reevaluation_tasks == set()
    tap_desk.run_scan.assert_not_awaited()


async def test_shutdown_cancels_and_awaits_in_flight_reevaluations(tap_desk, temp_db):
    sid = await record_card(temp_db)
    await temp_db.expire_signal(sid)
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def hanging_scan(**kwargs):
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    tap_desk.run_scan = AsyncMock(side_effect=hanging_scan)
    await tap_desk.reevaluate_signal(sid)
    await asyncio.wait_for(started.wait(), timeout=1)

    await asyncio.wait_for(tap_desk.cancel_reevaluations(), timeout=1)

    assert cancelled.is_set() and tap_desk.reevaluation_tasks == set()
    assert await message_texts(temp_db) == []  # a cancelled re-evaluation reports nothing


@pytest.mark.parametrize(
    "policy,setting,value,expected",
    [
        ("sizing", "max_shares_per_trade", 4, 4.0),
        ("sizing", "max_trade_notional_cap", 300.0, 2.0),  # floor(300 / 102)
    ],
)
async def test_replacement_size_respects_the_per_trade_caps(
    tap_desk, temp_db, app_config, policy, setting, value, expected
):
    app_config.portfolio.cash = 100_000.0
    setattr(getattr(app_config, policy), setting, value)
    sid = await record_card(temp_db, age_seconds=3600)
    tap_desk.data_fetcher.fetch_latest_price.return_value = 102.0

    await tap_desk.execute_signal_by_id(sid)

    [new] = [s for s in await temp_db.get_recent_signals(limit=10) if s["id"] != sid]
    assert new["quantity"] == expected


async def test_replacement_capped_to_zero_is_missed(tap_desk, temp_db, app_config):
    app_config.sizing.max_trade_notional_cap = 50.0
    sid = await record_card(temp_db, age_seconds=3600)
    tap_desk.data_fetcher.fetch_latest_price.return_value = 102.0

    reply = await tap_desk.execute_signal_by_id(sid)

    assert reply.offer_reevaluate is True and "cap" in reply.text
    assert [s["id"] for s in await temp_db.get_recent_signals(limit=10)] == [sid]


def test_cli_execute_prints_the_reply_text():
    with patch("agentic_trader.cli.commands.trade.get_copilot_and_config") as mock_get:
        copilot = MagicMock()
        copilot.broker.connect = AsyncMock()
        copilot.execute_signal_by_id = AsyncMock(
            return_value=ExecutionReply(False, "⚠️ <b>Execution Rejected:</b> stale", offer_reevaluate=True)
        )
        mock_get.return_value = (copilot, MagicMock())

        result = CliRunner().invoke(cli, ["execute", "7", "--qty", "2"])

    assert result.exit_code == 0, result.output
    copilot.execute_signal_by_id.assert_awaited_once_with(7, quantity=2.0)
    assert "Execution Rejected: stale" in result.output
