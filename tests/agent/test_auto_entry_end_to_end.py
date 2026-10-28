"""One paper auto-entry day end to end on SQLite, with only the broker stubbed.

Two marked cards of one suggestion scan: the first is recorded, delivered as a placing card, entered by the
sweep (its Dismiss swapped for Cancel entry…), accepted and filled (one fill notice); the second is entered,
accepted and cancelled unfilled at its deadline (both routine notices folded). The digest then reports both,
and ``cards outcomes`` reads both as auto entries. The broker's part is its acknowledgements
(``OrderResult``) and order observations; storage, the outbox, the renderer and the sweep are real.
"""

from datetime import UTC, datetime, time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd

from agentic_trader.broker.base import OrderResult
from agentic_trader.constants import SignalStatus
from agentic_trader.execution.auto_entry import AUTO_ENTRY_KEY, AUTO_ENTRY_MARKER
from agentic_trader.execution.durable import EntryTrigger, NotificationKind, WorkKind, WorkStatus
from agentic_trader.market.session import ET_TZ, MarketCalendarDay
from agentic_trader.notifier.outbox import NotificationDispatcher
from agentic_trader.notifier.telegram_bot import (
    AUTO_ENTRY_LINE,
    CANCEL_ENTRY_ASK_LABEL,
    ENTRY_SESSION_BOUND_LINE,
    TelegramNotifier,
)
from agentic_trader.research.setups.execution_evidence import collect_execution
from tests.agent.test_auto_entry_digest import FOOTER, FakeCalendar
from tests.agent.test_auto_entry_sweep import (  # noqa: F401  (fixtures)
    auto_desk,
    enqueue_for_real,
    notifications,
    record_auto_card,
    temp_db,
)
from tests.storage.test_auto_entry_notices import begin_cancel, notice_keys, observation, resolve_cancel


def _buttons(markup):
    return [[(b.text, b.callback_data) for b in row] for row in markup.inline_keyboard]


async def _drain(dispatcher: NotificationDispatcher) -> None:
    while await dispatcher.dispatch_one():
        pass


async def _accept(db, signal_id: int, order_id: str) -> str:
    """The broker acknowledges the FIFO head (the entry service's submission, broker stubbed)."""
    claimed = await db.workflows.claim_entry(lease_seconds=60)
    assert claimed is not None and claimed.payload["signal_id"] == signal_id
    assert await db.workflows.resolve_entry(claimed, OrderResult(success=True, order_id=order_id))
    return claimed.id


async def test_one_auto_entry_day_end_to_end(auto_desk, temp_db, app_config, monkeypatch):  # noqa: F811
    enqueue_for_real(auto_desk, temp_db, app_config, monkeypatch)
    today = datetime.now(UTC).astimezone(ET_TZ).date()

    # 1. Record: the marker in provenance and the placing payload on the queued card.
    filled = await record_auto_card(temp_db, rank=1)
    unfilled = await record_auto_card(temp_db, rank=2, contract="QQQ")
    for sid in (filled, unfilled):
        assert (await temp_db.get_signal_by_id(sid))["decision_provenance"][AUTO_ENTRY_KEY] == AUTO_ENTRY_MARKER
        assert (await notifications(temp_db))[f"signal/{sid}/created"]["arguments"]["auto_entry"] == "placing"

    # 2. Outbox delivery renders the placing card: the auto line and Dismiss only.
    notifier = TelegramNotifier(bot_token="test_token", chat_id="123456", db=temp_db)
    send = AsyncMock(side_effect=[SimpleNamespace(message_id=n) for n in range(500, 520)])
    edit = AsyncMock()
    notifier.app = SimpleNamespace(bot=SimpleNamespace(send_message=send, edit_message_reply_markup=edit))
    dispatcher = NotificationDispatcher(temp_db.workflows, notifier, app_config.execution)
    await _drain(dispatcher)
    [first_card, second_card] = [call.kwargs for call in send.await_args_list]
    assert AUTO_ENTRY_LINE in first_card["text"]
    assert f"• {ENTRY_SESSION_BOUND_LINE}" not in first_card["text"]  # ruling R11: the auto line says it
    assert _buttons(first_card["reply_markup"]) == [[("❌ Dismiss Signal", f"dism_{filled}")]]
    assert _buttons(second_card["reply_markup"]) == [[("❌ Dismiss Signal", f"dism_{unfilled}")]]
    card_message = (await temp_db.get_signal_by_id(filled))["telegram_message_id"]

    # 3. The sweep enters both, in rank order, and queues each card's button swap.
    attempts = await auto_desk.auto_enter_pending()
    assert [(a.signal_id, a.outcome) for a in attempts] == [(filled, "entered"), (unfilled, "entered")]
    queued = await notifications(temp_db)
    for sid in (filled, unfilled):
        assert queued[f"signal/{sid}/auto-entered"]["kind"] == NotificationKind.CARD_AUTO_ENTERED
        assert f"signal/{sid}/auto-refused" not in queued
    for item in await temp_db.workflows.list_work(WorkKind.ENTRY):
        assert item.payload["entry_trigger"] == EntryTrigger.AUTO
    await _drain(dispatcher)
    swapped = edit.await_args_list[0].kwargs
    assert swapped["message_id"] == card_message
    assert _buttons(swapped["reply_markup"]) == [[(CANCEL_ENTRY_ASK_LABEL, f"cancelentry_ask:{filled}")]]

    # 4. Accepted (the resolution notice is folded) and filled: one fill notice, in the fill transaction.
    first_item = await _accept(temp_db, filled, "entry-1")
    second_item = await _accept(temp_db, unfilled, "entry-2")
    [first_entry] = [i for i in await temp_db.workflows.list_work(WorkKind.ENTRY) if i.payload["signal_id"] == filled]
    await temp_db.workflows.observe_orders(
        [
            observation(
                status="filled",
                submitted_at=datetime.now(UTC),
                client_order_id=first_entry.payload["client_order_id"],
                filled="10",
            ).model_copy(update={"average_fill_price": "100.25"})
        ]
    )
    await temp_db.update_signal_execution(
        filled, "entry-1", fill_price=100.25, quantity=10, executed_at=datetime.now(UTC)
    )
    keys = await notice_keys(temp_db)
    assert keys[f"signal/{filled}/auto-filled"] == {
        "text": f"🤖 Auto-entry #{filled} SPY filled 10 @ $100.25; stop $95.00, target $120.00."
    }
    assert f"entry/{first_item}/accepted" not in keys and f"entry/{second_item}/accepted" not in keys
    sent_before = len(send.await_args_list)
    await _drain(dispatcher)
    assert any("filled 10 @ $100.25" in call.kwargs["text"] for call in send.await_args_list[sent_before:])

    # 5. The second card rests unfilled to its deadline: intent and confirmed-unfilled result are folded.
    [entry] = [i for i in await temp_db.workflows.list_work(WorkKind.ENTRY) if i.payload["signal_id"] == unfilled]
    cancel = await begin_cancel(temp_db, unfilled, entry, "deadline", order_id="entry-2", symbol="QQQ")
    await resolve_cancel(temp_db, cancel, entry, order_id="entry-2", symbol="QQQ")
    keys = await notice_keys(temp_db)
    assert f"entry-cancel/{cancel.id}/intent" not in keys
    assert f"entry-cancel/{cancel.id}/accepted" not in keys
    assert (await temp_db.get_signal_by_id(unfilled))["status"] == SignalStatus.FAILED

    # 6. The digest at the close + 15 minutes reports both cards in rank order.
    auto_desk.lifetime_service.calendar = FakeCalendar(MarketCalendarDay(today, True, False, time(9, 30), time(16, 0)))
    assert await auto_desk.publish_auto_entry_digest(datetime.combine(today, time(16, 20), ET_TZ)) is True
    digest = (await notice_keys(temp_db))[f"auto-entry-digest/{today.isoformat()}"]["text"]
    assert digest.split("\n") == [
        f"🤖 Auto-entry digest — {today.isoformat()}",
        f"SPY #{filled}: filled 10 @ $100.25 (open)",
        f"QQQ #{unfilled}: cancelled unfilled at the close",
        FOOTER,
    ]

    # 7. cards outcomes: both are auto entries the operator kept (a deadline cancel is no operator act).
    frame = pd.DataFrame(
        [
            {"signal_id": sid, "contract": contract, "direction": "LONG", "sent": True, "entry": 100.0, "stop": 95.0}
            for sid, contract in ((filled, "SPY"), (unfilled, "QQQ"))
        ]
    )
    rows = (await collect_execution(temp_db, frame)).set_index("signal_id")
    assert rows.loc[filled, "entry_trigger"] == rows.loc[unfilled, "entry_trigger"] == "auto"
    assert rows.loc[filled, "operator_action"] == rows.loc[unfilled, "operator_action"] == "auto_entered"
    assert rows.loc[filled, "fill_price"] == 100.25
    assert {item.status for item in await temp_db.workflows.list_work(WorkKind.ENTRY)} == {WorkStatus.ACCEPTED}
