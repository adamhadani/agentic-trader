"""Tap-time card freshness: the copilot re-assesses a card before entry authorization.

The freshness decision never authorizes an order by itself: ``EXECUTE`` continues into
the unchanged ``EntryExecutionService.authorize`` with the original bracket, while every
other outcome refuses the tap (a re-priced card is a *new* signal needing a fresh tap).
"""

import inspect
import math
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner
from sqlalchemy import update

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

    assert [e["payload"]["outcome"] for e in await tap_events(temp_db, sid)] == ["reprice"]

    # A second tap on the re-priced original is refused: it is no longer PENDING.
    again = await tap_desk.execute_signal_by_id(sid)
    assert again.ok is False and "only PENDING signals can be executed" in again.text
    tap_desk.entry_service.authorize.assert_not_awaited()
    assert len(await temp_db.workflows.list_work(WorkKind.NOTIFICATION)) == 1


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

    assert reply == ExecutionReply(False, "⚠️ Current price unavailable; try again shortly.")
    assert (await temp_db.get_signal_by_id(sid))["status"] == SignalStatus.PENDING
    tap_desk.entry_service.authorize.assert_not_awaited()


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


async def test_dedup_exemption_applies_to_the_named_contract_only(budget_desk, temp_db):  # noqa: F811
    budget_desk.db.is_duplicate_recent = AsyncMock(return_value=True)

    await budget_desk.run_scan(
        use_llm=False, dry_run=False, budget=ScanBudget.NONE, dedup_exempt_contracts=frozenset({"CCC"})
    )

    assert [s["contract"] for s in await temp_db.get_recent_signals(limit=10)] == ["CCC"]
    checked = {c.args[0] for c in budget_desk.db.is_duplicate_recent.await_args_list}
    assert checked == {"AAA", "BBB", "DDD", "EEE"}


async def test_reevaluate_runs_a_single_contract_scan_without_budget_or_dedup(tap_desk, temp_db):
    sid = await record_card(temp_db)
    await temp_db.expire_signal(sid)
    tap_desk.run_scan = AsyncMock(return_value={"sent": 1, "runners_up": []})

    reply = await tap_desk.reevaluate_signal(sid)

    tap_desk.run_scan.assert_awaited_once_with(
        symbols=["SPY"], budget=ScanBudget.NONE, dedup_exempt_contracts=frozenset({"SPY"})
    )
    assert reply.ok is True and "Fresh card sent" in reply.text


async def test_reevaluate_without_a_setup_names_the_first_reason(tap_desk, temp_db):
    sid = await record_card(temp_db)
    tap_desk.run_scan = AsyncMock(
        return_value={"sent": 0, "runners_up": [{"contract": "SPY", "reason": "rejected: <weak>"}]}
    )

    reply = await tap_desk.reevaluate_signal(sid)

    assert reply.ok is False and "No valid setup for SPY right now" in reply.text
    assert "rejected: &lt;weak&gt;" in reply.text


async def test_reevaluate_is_refused_outside_rth(tap_desk, temp_db):
    sid = await record_card(temp_db)
    tap_desk.session_provider.get_session_info.return_value = session_info(is_open=False, is_rth=False)
    tap_desk.run_scan = AsyncMock()

    reply = await tap_desk.reevaluate_signal(sid)

    tap_desk.run_scan.assert_not_awaited()
    assert reply.ok is False and reply.text.startswith("Market closed") and "2026-09-24 13:30 UTC" in reply.text


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
