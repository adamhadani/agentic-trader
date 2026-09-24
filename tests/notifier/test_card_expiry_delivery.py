"""CARD_EXPIRED delivery: striking a stale card's Telegram buttons, plus the late-SIGNAL race.

``TelegramNotifier.strike_expired_card`` is the delivery side of the session-close sweep
(``SignalDatabase.expire_stale_signals``): it edits the card's message to a single
Re-evaluate button, or removes the buttons entirely, and is wired into
``NotificationDispatcher._deliver`` for ``NotificationKind.CARD_EXPIRED``.
``send_signal_alert`` also guards against a retried SIGNAL delivery landing after the
sweep already expired the card (or after any other terminal status).
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import update
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

from agentic_trader.agent.evaluator import LLMTradeEvaluation
from agentic_trader.config import load_config
from agentic_trader.constants import AssetClass, SignalStatus
from agentic_trader.execution.durable import WorkKind, WorkStatus
from agentic_trader.notifier.outbox import NotificationDispatcher
from agentic_trader.notifier.telegram_bot import TelegramNotifier
from agentic_trader.storage.models import SignalRecord


PAST_NY_DATE = datetime(2026, 9, 23, 14, 0, tzinfo=UTC)  # 10:00 ET Sept 23
NEXT_DAY_NOW = datetime(2026, 9, 24, 15, 0, tzinfo=UTC)  # 11:00 ET Sept 24


def _signal_kwargs(**overrides):
    kwargs = {
        "contract": "SPY",
        "strategy": "s",
        "direction": "LONG",
        "entry_price": 100.0,
        "stop_loss": 98.0,
        "take_profit": 104.0,
        "risk_dollars": 2.0,
        "asset_class": "EQUITY",
        "quantity": 1,
    }
    kwargs.update(overrides)
    return kwargs


@pytest.fixture
def eval_res():
    return LLMTradeEvaluation(
        approved=True,
        contract="SPY",
        direction="LONG",
        entry_price=100.0,
        stop_loss=98.0,
        take_profit=104.0,
        stop_distance_points=2.0,
        target_distance_points=4.0,
        risk_reward_ratio=2.0,
        risk_dollars=20.0,
        reward_dollars=40.0,
        notional_value=1000.0,
        effective_leverage=0.1,
        macro_clearance=True,
        thesis_summary="thesis",
        quantity=10.0,
        asset_class=AssetClass.EQUITY,
    )


# --- strike_expired_card: unit behaviour ------------------------------------------------


@pytest.mark.asyncio
async def test_strike_expired_card_returns_false_when_never_configured(temp_db):
    # Never reachable through the dispatcher (`dispatch_one` returns early while
    # `is_configured()` is False), but the method itself must still fail closed here too.
    notifier = TelegramNotifier(bot_token=None, chat_id=None, db=temp_db)
    result = await notifier.strike_expired_card(1, "SPY", True)
    assert result is False


@pytest.mark.asyncio
async def test_strike_expired_card_returns_false_when_configured_but_app_unbuilt(temp_db):
    # The one branch of "not configured or no app" the dispatcher can actually reach: token
    # and chat id are present (`is_configured()` is True, so `dispatch_one` claims the item),
    # but `ApplicationBuilder` never produced an `app`. Every sibling sender
    # (`send_signal_alert`, `send_exit_alert`, `send_message`) returns a falsy value here so
    # the outbox retries and eventually dead-letters, instead of reporting a strike that
    # never happened as delivered.
    notifier = TelegramNotifier(bot_token="test_token", chat_id="123456", db=temp_db)
    notifier.app = None

    result = await notifier.strike_expired_card(1, "SPY", True)

    assert result is False


@pytest.mark.asyncio
async def test_dispatcher_retries_card_expired_when_telegram_app_is_unbuilt(temp_db):
    signal_id = await temp_db.record_signal(**_signal_kwargs())
    await temp_db.update_telegram_message_id(signal_id, 42)
    async with temp_db.session_factory() as session, session.begin():
        await session.execute(update(SignalRecord).where(SignalRecord.id == signal_id).values(timestamp=PAST_NY_DATE))
    await temp_db.expire_stale_signals(NEXT_DAY_NOW, configured_contracts=frozenset({"SPY"}))

    notifier = TelegramNotifier(bot_token="test_token", chat_id="123456", db=temp_db)
    notifier.app = None

    dispatched = await NotificationDispatcher(temp_db.workflows, notifier, load_config().execution).dispatch_one()

    assert dispatched is True
    notifications = await temp_db.workflows.list_work(WorkKind.NOTIFICATION)
    assert notifications[0].status == WorkStatus.QUEUED  # retried, not silently DELIVERED
    assert notifications[0].attempts == 1


@pytest.mark.asyncio
async def test_strike_expired_card_acknowledges_without_calling_telegram_when_no_message_id(temp_db):
    signal_id = await temp_db.record_signal(**_signal_kwargs())
    notifier = TelegramNotifier(bot_token="test_token", chat_id="123456", db=temp_db)
    notifier.app = SimpleNamespace(bot=SimpleNamespace(edit_message_reply_markup=AsyncMock()))

    result = await notifier.strike_expired_card(signal_id, "SPY", True)

    assert result
    notifier.app.bot.edit_message_reply_markup.assert_not_called()


@pytest.mark.asyncio
async def test_strike_expired_card_sets_reevaluate_button_when_reevaluable(temp_db):
    signal_id = await temp_db.record_signal(**_signal_kwargs())
    await temp_db.update_telegram_message_id(signal_id, 555)
    notifier = TelegramNotifier(bot_token="test_token", chat_id="123456", db=temp_db)
    edit = AsyncMock()
    notifier.app = SimpleNamespace(bot=SimpleNamespace(edit_message_reply_markup=edit))

    result = await notifier.strike_expired_card(signal_id, "SPY", True)

    assert result
    edit.assert_awaited_once_with(
        chat_id="123456",
        message_id=555,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔄 Re-evaluate", callback_data=f"reval_{signal_id}")]]
        ),
    )


@pytest.mark.asyncio
async def test_strike_expired_card_removes_buttons_when_not_reevaluable(temp_db):
    signal_id = await temp_db.record_signal(**_signal_kwargs())
    await temp_db.update_telegram_message_id(signal_id, 556)
    notifier = TelegramNotifier(bot_token="test_token", chat_id="123456", db=temp_db)
    edit = AsyncMock()
    notifier.app = SimpleNamespace(bot=SimpleNamespace(edit_message_reply_markup=edit))

    result = await notifier.strike_expired_card(signal_id, "DYNAMIC_XYZ", False)

    assert result
    edit.assert_awaited_once_with(chat_id="123456", message_id=556, reply_markup=None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        "Bad Request: message is not modified",
        "Bad Request: message to edit not found",
        "Bad Request: message can't be edited",
        "Bad Request: MESSAGE_ID_INVALID",
    ],
)
async def test_strike_expired_card_acknowledges_known_unmodifiable_badrequests(temp_db, message):
    signal_id = await temp_db.record_signal(**_signal_kwargs())
    await temp_db.update_telegram_message_id(signal_id, 557)
    notifier = TelegramNotifier(bot_token="test_token", chat_id="123456", db=temp_db)
    edit = AsyncMock(side_effect=BadRequest(message))
    notifier.app = SimpleNamespace(bot=SimpleNamespace(edit_message_reply_markup=edit))

    result = await notifier.strike_expired_card(signal_id, "SPY", True)

    assert result


@pytest.mark.asyncio
async def test_strike_expired_card_reraises_other_badrequests(temp_db):
    signal_id = await temp_db.record_signal(**_signal_kwargs())
    await temp_db.update_telegram_message_id(signal_id, 558)
    notifier = TelegramNotifier(bot_token="test_token", chat_id="123456", db=temp_db)
    edit = AsyncMock(side_effect=BadRequest("Bad Request: chat not found"))
    notifier.app = SimpleNamespace(bot=SimpleNamespace(edit_message_reply_markup=edit))

    with pytest.raises(BadRequest):
        await notifier.strike_expired_card(signal_id, "SPY", True)


@pytest.mark.asyncio
async def test_strike_expired_card_reraises_other_exceptions(temp_db):
    signal_id = await temp_db.record_signal(**_signal_kwargs())
    await temp_db.update_telegram_message_id(signal_id, 559)
    notifier = TelegramNotifier(bot_token="test_token", chat_id="123456", db=temp_db)
    edit = AsyncMock(side_effect=RuntimeError("boom"))
    notifier.app = SimpleNamespace(bot=SimpleNamespace(edit_message_reply_markup=edit))

    with pytest.raises(RuntimeError):
        await notifier.strike_expired_card(signal_id, "SPY", True)


# --- Outbox wiring: NotificationDispatcher delivers CARD_EXPIRED -----------------------


@pytest.mark.asyncio
async def test_dispatcher_delivers_card_expired_via_strike_expired_card(temp_db):
    signal_id = await temp_db.record_signal(**_signal_kwargs())
    await temp_db.update_telegram_message_id(signal_id, 42)
    async with temp_db.session_factory() as session, session.begin():
        await session.execute(update(SignalRecord).where(SignalRecord.id == signal_id).values(timestamp=PAST_NY_DATE))
    expired = await temp_db.expire_stale_signals(NEXT_DAY_NOW, configured_contracts=frozenset({"SPY"}))
    assert expired == [signal_id]

    notifier = TelegramNotifier(bot_token="test_token", chat_id="123456", db=temp_db)
    edit = AsyncMock()
    notifier.app = SimpleNamespace(bot=SimpleNamespace(edit_message_reply_markup=edit))

    dispatched = await NotificationDispatcher(temp_db.workflows, notifier, load_config().execution).dispatch_one()

    assert dispatched is True
    edit.assert_awaited_once_with(
        chat_id="123456",
        message_id=42,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔄 Re-evaluate", callback_data=f"reval_{signal_id}")]]
        ),
    )
    notifications = await temp_db.workflows.list_work(WorkKind.NOTIFICATION)
    assert notifications[0].status == WorkStatus.DELIVERED


# --- Late SIGNAL delivery race: send_signal_alert never re-offers Execute/Dismiss ------


@pytest.mark.asyncio
async def test_send_signal_alert_offers_only_reevaluate_when_card_already_expired(temp_db, eval_res):
    signal_id = await temp_db.record_signal(**_signal_kwargs())
    await temp_db.update_signal_status(signal_id, SignalStatus.EXPIRED)
    notifier = TelegramNotifier(bot_token="test_token", chat_id="123456", db=temp_db)
    send = AsyncMock(return_value=SimpleNamespace(message_id=99))
    notifier.app = SimpleNamespace(bot=SimpleNamespace(send_message=send))

    await notifier.send_signal_alert(eval_res, strategy="TREND_PULLBACK", signal_id=signal_id)

    markup = send.await_args.kwargs["reply_markup"]
    assert markup == InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔄 Re-evaluate", callback_data=f"reval_{signal_id}")]]
    )


@pytest.mark.asyncio
async def test_send_signal_alert_sends_no_buttons_for_a_non_expired_terminal_status(temp_db, eval_res):
    signal_id = await temp_db.record_signal(**_signal_kwargs())
    await temp_db.update_signal_status(signal_id, SignalStatus.DISMISSED)
    notifier = TelegramNotifier(bot_token="test_token", chat_id="123456", db=temp_db)
    send = AsyncMock(return_value=SimpleNamespace(message_id=100))
    notifier.app = SimpleNamespace(bot=SimpleNamespace(send_message=send))

    await notifier.send_signal_alert(eval_res, strategy="TREND_PULLBACK", signal_id=signal_id)

    assert send.await_args.kwargs["reply_markup"] is None


@pytest.mark.asyncio
async def test_send_signal_alert_still_offers_execute_dismiss_while_pending(temp_db, eval_res):
    signal_id = await temp_db.record_signal(**_signal_kwargs())
    notifier = TelegramNotifier(bot_token="test_token", chat_id="123456", db=temp_db)
    send = AsyncMock(return_value=SimpleNamespace(message_id=101))
    notifier.app = SimpleNamespace(bot=SimpleNamespace(send_message=send))

    await notifier.send_signal_alert(eval_res, strategy="TREND_PULLBACK", signal_id=signal_id)

    markup = send.await_args.kwargs["reply_markup"]
    callbacks = {b.callback_data for row in markup.inline_keyboard for b in row}
    assert callbacks == {f"exec_{signal_id}", f"dism_{signal_id}"}
