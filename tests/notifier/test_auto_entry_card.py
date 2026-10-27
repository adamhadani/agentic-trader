"""Paper auto-entry card rendering, the Dismiss -> Cancel entry... swap and ``cancelentry_ask``."""

import html
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

from agentic_trader.agent.evaluator import LLMTradeEvaluation
from agentic_trader.config import load_config
from agentic_trader.constants import AssetClass, SignalStatus
from agentic_trader.execution.durable import NotificationKind, WorkKind, WorkStatus
from agentic_trader.execution.lifetimes import RestingCancelResult
from agentic_trader.notifier.outbox import NotificationDispatcher
from agentic_trader.notifier.telegram_bot import (
    AUTO_ABANDONED_LINE,
    AUTO_ENTRY_LINE,
    AUTO_REFUSED_LINE,
    CANCEL_ENTRY_ASK_LABEL,
    TelegramNotifier,
    format_alert_card,
    format_terminal_card,
)


def _signal_kwargs():
    return {
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


def _buttons(markup):
    return [[(b.text, b.callback_data) for b in row] for row in markup.inline_keyboard]


async def _send(temp_db, eval_res, *, status=None, **kwargs):
    signal_id = await temp_db.record_signal(**_signal_kwargs())
    if status is not None:
        await temp_db.update_signal_status(signal_id, status)
    notifier = TelegramNotifier(bot_token="test_token", chat_id="123456", db=temp_db)
    send = AsyncMock(return_value=SimpleNamespace(message_id=99))
    notifier.app = SimpleNamespace(bot=SimpleNamespace(send_message=send))
    await notifier.send_signal_alert(eval_res, strategy="TREND_PULLBACK", signal_id=signal_id, **kwargs)
    return signal_id, send.await_args.kwargs


@pytest.mark.asyncio
async def test_placing_card_pending_shows_auto_line_and_only_dismiss(temp_db, eval_res):
    signal_id, sent = await _send(temp_db, eval_res, auto_entry="placing")
    assert AUTO_ENTRY_LINE in sent["text"]
    assert _buttons(sent["reply_markup"]) == [[("❌ Dismiss Signal", f"dism_{signal_id}")]]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [SignalStatus.SUBMITTING, SignalStatus.EXECUTED])
async def test_placing_card_delivered_after_entry_shows_cancel_entry(temp_db, eval_res, status):
    signal_id, sent = await _send(temp_db, eval_res, status=status, auto_entry="placing")
    assert AUTO_ENTRY_LINE in sent["text"]
    assert _buttons(sent["reply_markup"]) == [[(CANCEL_ENTRY_ASK_LABEL, f"cancelentry_ask:{signal_id}")]]


@pytest.mark.asyncio
async def test_refused_recard_shows_reason_and_normal_buttons(temp_db, eval_res):
    reason = "risk <cap> & more"
    _, normal = await _send(temp_db, eval_res)
    signal_id, sent = await _send(temp_db, eval_res, auto_entry="refused", auto_refusal=reason)
    assert AUTO_REFUSED_LINE.format(reason=html.escape(reason, quote=False)) in sent["text"]
    assert AUTO_ENTRY_LINE not in sent["text"]
    callbacks = [c for row in _buttons(sent["reply_markup"]) for _, c in row]
    assert callbacks == [f"exec_{signal_id}", f"dism_{signal_id}"]
    assert [t for row in _buttons(sent["reply_markup"]) for t, _ in row] == [
        t for row in _buttons(normal["reply_markup"]) for t, _ in row
    ]


@pytest.mark.asyncio
async def test_abandoned_recard_line(temp_db, eval_res):
    signal_id, sent = await _send(temp_db, eval_res, auto_entry="abandoned")
    assert AUTO_ABANDONED_LINE in sent["text"]
    assert [c for row in _buttons(sent["reply_markup"]) for _, c in row] == [f"exec_{signal_id}", f"dism_{signal_id}"]


@pytest.mark.asyncio
async def test_auto_card_failed_or_expired_renders_like_today(temp_db, eval_res):
    signal_id, failed = await _send(temp_db, eval_res, status=SignalStatus.FAILED, auto_entry="placing")
    assert failed["reply_markup"] is None
    signal_id, expired = await _send(temp_db, eval_res, status=SignalStatus.EXPIRED, auto_entry="placing")
    assert _buttons(expired["reply_markup"]) == [[("🔄 Re-evaluate", f"reval_{signal_id}")]]


def test_cards_without_auto_entry_are_unchanged(eval_res):
    assert "auto-entry" not in format_alert_card(eval_res, "S").lower()
    assert "auto-entry" not in format_terminal_card(eval_res, "S").lower()
    assert AUTO_ENTRY_LINE in format_terminal_card(eval_res, "S", auto_entry="placing")


@pytest.mark.asyncio
async def test_mark_auto_entered_swaps_keyboard(temp_db):
    signal_id = await temp_db.record_signal(**_signal_kwargs())
    await temp_db.update_telegram_message_id(signal_id, 555)
    notifier = TelegramNotifier(bot_token="test_token", chat_id="123456", db=temp_db)
    edit = AsyncMock()
    notifier.app = SimpleNamespace(bot=SimpleNamespace(edit_message_reply_markup=edit))
    assert await notifier.mark_auto_entered_card(signal_id, "SPY")
    edit.assert_awaited_once_with(
        chat_id="123456",
        message_id=555,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(CANCEL_ENTRY_ASK_LABEL, callback_data=f"cancelentry_ask:{signal_id}")]]
        ),
    )


@pytest.mark.asyncio
async def test_mark_auto_entered_without_message_id_is_acknowledged(temp_db):
    signal_id = await temp_db.record_signal(**_signal_kwargs())
    notifier = TelegramNotifier(bot_token="test_token", chat_id="123456", db=temp_db)
    edit = AsyncMock()
    notifier.app = SimpleNamespace(bot=SimpleNamespace(edit_message_reply_markup=edit))
    assert await notifier.mark_auto_entered_card(signal_id, "SPY")
    edit.assert_not_called()


@pytest.mark.asyncio
async def test_mark_auto_entered_unconfigured_returns_false(temp_db):
    notifier = TelegramNotifier(bot_token=None, chat_id=None, db=temp_db)
    assert await notifier.mark_auto_entered_card(1, "SPY") is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "acknowledged"),
    [
        (BadRequest("Bad Request: message is not modified"), True),
        (BadRequest("Bad Request: message to edit not found"), True),
        (BadRequest("Bad Request: chat not found"), False),
        (RuntimeError("boom"), False),
    ],
)
async def test_mark_auto_entered_error_handling(temp_db, error, acknowledged):
    signal_id = await temp_db.record_signal(**_signal_kwargs())
    await temp_db.update_telegram_message_id(signal_id, 556)
    notifier = TelegramNotifier(bot_token="test_token", chat_id="123456", db=temp_db)
    notifier.app = SimpleNamespace(bot=SimpleNamespace(edit_message_reply_markup=AsyncMock(side_effect=error)))
    if acknowledged:
        assert await notifier.mark_auto_entered_card(signal_id, "SPY")
    else:
        with pytest.raises(type(error)):
            await notifier.mark_auto_entered_card(signal_id, "SPY")


def _callback_update(data, chat_id=1):
    update = MagicMock()
    update.effective_chat.id = chat_id
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.message = AsyncMock()
    update.callback_query = query
    return update, query


@pytest.mark.asyncio
async def test_cancelentry_ask_runs_the_preview():
    preview = RestingCancelResult(status="preview", reason=None, signal_id=42, symbol="SPY", work_status=None)
    handler = AsyncMock(return_value=preview)
    notifier = TelegramNotifier(None, "1", cancel_handler=handler)
    update, query = _callback_update("cancelentry_ask:42")
    await notifier.handle_button_callback(update, MagicMock())
    handler.assert_awaited_once_with(42, False)
    call = query.message.reply_text.await_args
    assert _buttons(call.kwargs["reply_markup"]) == [
        [("Cancel entry", "cancelentry_confirm:42"), ("Keep", "cancelentry_keep")]
    ]


@pytest.mark.asyncio
async def test_cancelentry_ask_requires_authorization():
    handler = AsyncMock()
    notifier = TelegramNotifier(None, "1", cancel_handler=handler)
    update, query = _callback_update("cancelentry_ask:42", chat_id=2)
    await notifier.handle_button_callback(update, MagicMock())
    handler.assert_not_awaited()
    query.message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_outbox_delivers_card_auto_entered(temp_db):
    signal_id = await temp_db.record_signal(**_signal_kwargs())
    await temp_db.update_telegram_message_id(signal_id, 42)
    await temp_db.workflows.enqueue_notification(
        f"signal/{signal_id}/auto-entered",
        NotificationKind.CARD_AUTO_ENTERED,
        {"signal_id": signal_id, "contract": "SPY"},
    )
    notifier = TelegramNotifier(bot_token="test_token", chat_id="123456", db=temp_db)
    edit = AsyncMock()
    notifier.app = SimpleNamespace(bot=SimpleNamespace(edit_message_reply_markup=edit))
    assert await NotificationDispatcher(temp_db.workflows, notifier, load_config().execution).dispatch_one()
    edit.assert_awaited_once()
    assert (await temp_db.workflows.list_work(WorkKind.NOTIFICATION))[-1].status == WorkStatus.DELIVERED


def test_watch_card_still_says_tap_only(eval_res):
    facts = {
        "kind": "insider_watch",
        "risk_cap": 100.0,
        "buyers": 2,
        "opp_dollars": 1000.0,
        "filing_dates": ["2026-10-20"],
        "holding_sessions": 5,
        "issuer_cik": 1,
    }
    assert "tap-only, never auto-entered" in format_alert_card(eval_res, "S", card_facts=facts)
