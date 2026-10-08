"""next_session_close: recorded close, live-card guard, WAITING taps and dated rendering."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.config import ScanBudget, load_config
from agentic_trader.constants import SignalStatus
from agentic_trader.execution.freshness import ExecutionReply
from agentic_trader.market.session import ET_TZ, DeterministicCalendarProvider, next_regular_close_after
from agentic_trader.notifier.outbox import NotificationDispatcher
from agentic_trader.notifier.telegram_bot import TelegramNotifier
from agentic_trader.storage.models import SignalRecord
from tests.agent.test_card_freshness_tap import record_card, session_info, tap_desk, tap_events  # noqa: F401
from tests.agent.test_scan_budget import budget_desk, candidate  # noqa: F401
from tests.execution.card_stats_fixtures import real_evaluation


@pytest.fixture
def extended(budget_desk, app_config):  # noqa: F811
    app_config.card_policy.validity = "next_session_close"
    today_close = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=3)
    budget_desk.session_provider.get_session_info.return_value = session_info(next_close=today_close)
    budget_desk.session_provider.calendar = DeterministicCalendarProvider()
    budget_desk.today_close = today_close
    return budget_desk


async def _valid_untils(db):
    return [
        datetime.fromisoformat(s["decision_provenance"]["valid_until"]) for s in await db.get_recent_signals(limit=10)
    ]


async def test_native_equity_cards_record_the_next_trading_close(extended, temp_db):
    await extended.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.SESSION)
    expected = await next_regular_close_after(DeterministicCalendarProvider(), datetime.now(UTC))
    untils = await _valid_untils(temp_db)
    assert len(untils) == 2 and all(until == expected for until in untils)
    assert expected.astimezone(ET_TZ).date() > datetime.now(ET_TZ).date()


@pytest.mark.parametrize(
    "change",
    [
        lambda desk, config: setattr(config.card_policy, "validity", "session_close"),
        lambda desk, config: setattr(config.execution.card_freshness, "enabled", False),
        lambda desk, config: setattr(
            desk.session_provider,
            "calendar",
            SimpleNamespace(get_calendar_range=AsyncMock(side_effect=RuntimeError("down"))),
        ),
        lambda desk, config: setattr(
            desk.strategy_engine.scan_contract,
            "side_effect",
            lambda data, **kw: [candidate(data.contract, 0.5).model_copy(update={"probe": True})],
        ),
        lambda desk, config: setattr(
            desk.strategy_engine.scan_contract,
            "side_effect",
            lambda data, **kw: [candidate(data.contract, 0.5).model_copy(update={"alpha_version": "alpha:x:v3"})],
        ),
    ],
    ids=["default", "freshness-off", "calendar-down", "probe", "policy-locked"],
)
async def test_every_other_card_keeps_todays_close(extended, temp_db, app_config, change):
    change(extended, app_config)
    await extended.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.SESSION)
    untils = await _valid_untils(temp_db)
    assert untils and all(until == extended.today_close for until in untils)


async def _plant(db, contract, strategy, when):
    async with db.session_factory() as session, session.begin():
        session.add(
            SignalRecord(
                timestamp=when,
                contract=contract,
                strategy=strategy,
                direction="LONG",
                entry_price=100.0,
                stop_loss=98.0,
                take_profit=104.0,
                risk_dollars=2.0,
                environment=db.environment,
                execution_mode=db.execution_mode,
            )
        )


@pytest.mark.parametrize(
    ("validity", "strategy", "sent"),
    [
        ("next_session_close", "TREND_PULLBACK", ["CCC"]),  # DDD already has a live card for this setup
        ("next_session_close", "SQUEEZE_BREAKOUT", ["DDD"]),  # another strategy's card does not block
        ("session_close", "TREND_PULLBACK", ["DDD"]),  # the default path is unchanged
    ],
)
async def test_live_card_guard(extended, temp_db, app_config, validity, strategy, sent):
    app_config.card_policy.validity = validity
    await _plant(temp_db, "DDD", strategy, datetime.now(UTC) - timedelta(days=1))  # PENDING, outside dedup and today
    await extended.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL)
    recent = datetime.now(UTC) - timedelta(hours=1)  # signal rows carry naive UTC timestamps
    fresh = [
        s["contract"]
        for s in await temp_db.get_recent_signals(limit=10)
        if datetime.fromisoformat(s["timestamp"]).replace(tzinfo=UTC) > recent
    ]
    assert fresh == sent
    if sent == ["CCC"]:
        reasons = {r["contract"]: r["reason"] for r in extended.last_scan_summary["runners_up"]}
        assert reasons["DDD"] == "live card pending"


async def test_a_symbol_scan_skipped_for_a_live_card_says_so(extended, temp_db):
    """`/scan DDD` while DDD's setup already has a live card: the reply names the live card, not "no setup"."""
    await _plant(temp_db, "DDD", "TREND_PULLBACK", datetime.now(UTC) - timedelta(days=1))
    summary = await extended.run_scan(symbols=["DDD"], use_llm=False, dry_run=False, budget=ScanBudget.NONE)
    assert summary["sent"] == 0
    assert (
        TradingCopilot._scan_result_text("DDD", summary)
        == "No new card for DDD: a live card for this setup is pending."
    )


async def test_an_extended_card_renders_its_close_date_through_the_real_outbox(extended, temp_db):
    extended.evaluator.evaluate_candidate = AsyncMock(side_effect=lambda cand, **kwargs: real_evaluation(cand))
    await extended.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL)
    notifier = TelegramNotifier(bot_token="test_token", chat_id="123456", db=temp_db)
    send = AsyncMock(return_value=SimpleNamespace(message_id=5))
    notifier.app = SimpleNamespace(bot=SimpleNamespace(send_message=send))
    assert await NotificationDispatcher(temp_db.workflows, notifier, load_config().execution).dispatch_one()
    expected = await next_regular_close_after(DeterministicCalendarProvider(), datetime.now(UTC))
    assert f"• <b>Valid until:</b> {expected.astimezone(ET_TZ):%a %d %H:%M} NY\n" in send.await_args.kwargs["text"]


async def test_a_closed_market_tap_waits_and_leaves_the_card_live(tap_desk, temp_db, app_config):  # noqa: F811
    app_config.card_policy.validity = "next_session_close"
    valid_until = datetime.now(UTC) + timedelta(hours=20)
    sid = await record_card(temp_db, valid_until=valid_until)
    tap_desk.session_provider.get_session_info.return_value = session_info(is_open=False, is_rth=False)

    reply = await tap_desk.execute_signal_by_id(sid)

    until = valid_until.astimezone(ET_TZ)
    assert reply == ExecutionReply(
        False,
        f"⏳ Market closed; card valid until {until:%a %d %H:%M} NY. Next regular open 2026-09-24 13:30 UTC.",
        retryable=True,
    )
    assert (await temp_db.get_signal_by_id(sid))["status"] == SignalStatus.PENDING
    [event] = await tap_events(temp_db, sid)
    assert event["payload"]["outcome"] == "waiting" and event["payload"]["applied"] is False
    tap_desk.entry_service.authorize.assert_not_awaited()


async def test_a_policy_locked_card_keeps_todays_rule_when_closed(tap_desk, temp_db, app_config):  # noqa: F811
    app_config.card_policy.validity = "next_session_close"
    sid = await record_card(temp_db, valid_until=datetime.now(UTC) + timedelta(hours=20), alpha_version="alpha:x:v3")
    tap_desk.session_provider.get_session_info.return_value = session_info(is_open=False, is_rth=False)

    reply = await tap_desk.execute_signal_by_id(sid)

    assert reply.retryable is False and (await temp_db.get_signal_by_id(sid))["status"] == SignalStatus.EXPIRED


async def test_a_day_two_tap_reprices_and_the_replacement_keeps_the_close(tap_desk, temp_db, app_config):  # noqa: F811
    app_config.card_policy.validity = "next_session_close"
    sid = await record_card(temp_db, age_seconds=20 * 3600, valid_until=datetime.now(UTC) + timedelta(hours=3))
    tap_desk.data_fetcher.fetch_latest_price.return_value = 102.0

    await tap_desk.execute_signal_by_id(sid)

    old = await temp_db.get_signal_by_id(sid)
    [new] = [s for s in await temp_db.get_recent_signals(limit=10) if s["status"] == SignalStatus.PENDING]
    assert old["status"] == SignalStatus.EXPIRED
    assert new["decision_provenance"]["valid_until"] == old["decision_provenance"]["valid_until"]
