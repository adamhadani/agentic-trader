"""Card lifecycle across flows: session-close sweep → Telegram strike → Re-evaluate.

Each piece is tested on its own elsewhere; these drive the real sweep, the real outbox
dispatcher and ``TelegramNotifier.strike_expired_card`` (with a stub bot), then the
copilot's re-evaluation of the struck card.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from agentic_trader.agent import copilot as copilot_module
from agentic_trader.config import ScanBudget
from agentic_trader.constants import SignalStatus
from agentic_trader.execution.durable import WorkKind, WorkStatus
from agentic_trader.notifier.outbox import NotificationDispatcher
from agentic_trader.notifier.telegram_bot import TelegramNotifier
from tests.agent.test_card_freshness_tap import (  # noqa: F401  (tap_desk is a fixture)
    finish_reevaluations,
    record_card,
    tap_desk,
)


def stub_notifier(db) -> TelegramNotifier:
    notifier = TelegramNotifier(bot_token="test_token", chat_id="123456", db=db)
    notifier.app = SimpleNamespace(bot=SimpleNamespace(edit_message_reply_markup=AsyncMock()))
    return notifier


async def sweep_and_deliver(desk, db) -> TelegramNotifier:
    await desk.expire_stale_cards()
    notifier = stub_notifier(db)
    assert await NotificationDispatcher(db.workflows, notifier, desk.config.execution).dispatch_one()
    [item] = await db.workflows.list_work(WorkKind.NOTIFICATION)
    assert item.status == WorkStatus.DELIVERED
    return notifier


async def test_a_swept_card_is_struck_to_reevaluate_and_that_tap_schedules_one_exempt_scan(tap_desk, temp_db):  # noqa: F811
    sid = await record_card(temp_db, valid_until=datetime.now(UTC) - timedelta(minutes=5))
    await temp_db.update_telegram_message_id(sid, 4242)

    notifier = await sweep_and_deliver(tap_desk, temp_db)

    assert (await temp_db.get_signal_by_id(sid))["status"] == SignalStatus.EXPIRED
    edit = notifier.app.bot.edit_message_reply_markup.await_args.kwargs
    assert edit["message_id"] == 4242
    [[button]] = edit["reply_markup"].inline_keyboard
    assert button.callback_data == f"reval_{sid}"

    tap_desk.run_scan = AsyncMock(return_value={"sent": 1, "runners_up": []})
    reply = await tap_desk.reevaluate_signal(int(button.callback_data.split("_")[1]))
    await finish_reevaluations(tap_desk)

    assert reply.ok is True
    tap_desk.run_scan.assert_awaited_once_with(
        symbols=["SPY"],
        budget=ScanBudget.NONE,
        dedup_exempt_setups=frozenset({("SPY", "TREND_PULLBACK", "4h", None)}),
        scan_lock_timeout=copilot_module.REEVALUATE_SCAN_WAIT_SECONDS,
    )


async def test_a_swept_operator_dynamic_card_is_struck_with_no_buttons(tap_desk, temp_db):  # noqa: F811
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
        timeframe="4h",
        decision_provenance={
            "dynamic": True,
            "dynamic_source": "operator",
            "valid_until": (datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
        },
    )
    await temp_db.update_telegram_message_id(sid, 4343)

    notifier = await sweep_and_deliver(tap_desk, temp_db)

    assert (await temp_db.get_signal_by_id(sid))["status"] == SignalStatus.EXPIRED
    notifier.app.bot.edit_message_reply_markup.assert_awaited_once_with(
        chat_id="123456", message_id=4343, reply_markup=None
    )
