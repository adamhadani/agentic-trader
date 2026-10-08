"""Both card renderers: the SETUP header and the measured-record block inside the plain card."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentic_trader.agent.evaluator import LLMTradeEvaluation
from agentic_trader.config import load_config
from agentic_trader.constants import AssetClass
from agentic_trader.execution.durable import WorkKind, WorkStatus
from agentic_trader.notifier.outbox import NotificationDispatcher
from agentic_trader.notifier.telegram_bot import TelegramNotifier, format_alert_card, format_terminal_card
from tests.execution.card_stats_fixtures import EVIDENCE
from tests.notifier.test_drift_card import DRIFT


@pytest.fixture
def eval_res():
    return LLMTradeEvaluation(
        approved=True,
        contract="AAPL",
        direction="LONG",
        entry_price=190.0,
        stop_loss=186.0,
        take_profit=198.0,
        stop_distance_points=4.0,
        target_distance_points=8.0,
        risk_reward_ratio=2.0,
        risk_dollars=100.0,
        reward_dollars=200.0,
        notional_value=1900.0,
        effective_leverage=0.19,
        macro_clearance=True,
        thesis_summary="Pullback thesis",
        quantity=10.0,
        asset_class=AssetClass.EQUITY,
    )


def test_both_cards_say_setup_not_trade_signal(eval_res):
    card = format_alert_card(eval_res, "TREND_PULLBACK", card_evidence=EVIDENCE)
    term = format_terminal_card(eval_res, "TREND_PULLBACK", card_evidence=EVIDENCE)
    assert "📋 <b>SETUP: 10 shares AAPL (LONG)</b>" in card and "TRADE SIGNAL" not in card
    assert "📋 SETUP: 10 shares AAPL (LONG)" in term and "TRADE SIGNAL" not in term


def test_the_block_sits_between_the_target_and_the_thesis_in_both_renderers(eval_res):
    card = format_alert_card(eval_res, "TREND_PULLBACK", card_evidence=EVIDENCE)
    assert card.index("<b>Target (2.0:1):</b>") < card.index("Measured record") < card.index("Implied EV")
    assert card.index("Not validated alpha") < card.index("Risk &amp; Portfolio Context") < card.index("Thesis")
    term = format_terminal_card(eval_res, "TREND_PULLBACK", card_evidence=EVIDENCE)
    assert term.index("• Target:") < term.index("• Measured record (") < term.index("Not validated alpha")
    assert term.index("Not validated alpha") < term.index("Risk & Portfolio Context") < term.index("Thesis")


def test_a_card_without_evidence_renders_no_block(eval_res):
    card = format_alert_card(eval_res, "TREND_PULLBACK")
    assert "Measured record" not in card and "Not validated alpha" not in card
    assert "(+8.00 pts | +$200.00)\n\n🛡️" in card  # the old layout, byte for byte around the gap


def test_probe_and_drift_cards_still_end_with_the_plain_card(eval_res):
    plain = format_alert_card(eval_res, "alpha_x", card_evidence=EVIDENCE)
    assert format_alert_card(eval_res, "alpha_x", probe_risk_cap=100.0, card_evidence=EVIDENCE).endswith(plain)
    drift_plain = format_alert_card(eval_res, "pead_long", card_evidence=EVIDENCE)
    drift = format_alert_card(eval_res, "pead_long", probe_risk_cap=100.0, drift=DRIFT, card_evidence=EVIDENCE)
    assert drift.endswith(drift_plain)


async def _dispatch(temp_db, eval_res, notification_extra):
    await temp_db.record_signal(
        contract="AAPL",
        strategy="TREND_PULLBACK",
        direction="LONG",
        entry_price=190.0,
        stop_loss=186.0,
        take_profit=198.0,
        risk_dollars=100.0,
        asset_class=AssetClass.EQUITY,
        quantity=10.0,
        notification={
            "eval_res": eval_res.model_dump(mode="json"),
            "strategy": "TREND_PULLBACK",
            "regime_summary": "calm",
            "probe_risk_cap": None,
            **notification_extra,
        },
    )
    notifier = TelegramNotifier(bot_token="test_token", chat_id="123456", db=temp_db)
    send = AsyncMock(return_value=SimpleNamespace(message_id=7))
    notifier.app = SimpleNamespace(bot=SimpleNamespace(send_message=send))
    assert await NotificationDispatcher(temp_db.workflows, notifier, load_config().execution).dispatch_one()
    [item] = await temp_db.workflows.list_work(WorkKind.NOTIFICATION)
    return item, send.await_args.kwargs["text"]


async def test_a_queued_card_without_evidence_delivers_unchanged(temp_db, eval_res):
    item, text = await _dispatch(temp_db, eval_res, {})
    assert item.status == WorkStatus.DELIVERED
    assert "📋 <b>SETUP:" in text and "Measured record" not in text


async def test_a_card_with_evidence_delivers_its_block(temp_db, eval_res):
    item, text = await _dispatch(temp_db, eval_res, {"card_evidence": EVIDENCE})
    assert item.status == WorkStatus.DELIVERED
    assert "• <b>Implied EV at 2.0:1:</b> -0.25R" in text


async def test_malformed_evidence_delivers_as_no_statistics(temp_db, eval_res):
    item, text = await _dispatch(temp_db, eval_res, {"card_evidence": {"status": "bogus"}})
    assert item.status == WorkStatus.DELIVERED
    assert "• <b>Measured record:</b> no statistics in this scope" in text
