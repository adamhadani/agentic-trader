"""The PEAD drift card: probe header, then the event and hold facts, then the ordinary card."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentic_trader.agent.evaluator import LLMTradeEvaluation
from agentic_trader.constants import AssetClass
from agentic_trader.notifier.telegram_bot import TelegramNotifier, format_alert_card


EVENT_LINE = "📈 <b>PEAD</b> — EPS beat +12.3%, reaction +1.8σ vs SPY (report 2026-10-26)"
HOLD_LINE = "⏱️ 20-session hold · time exit 15:45 NY on 2026-11-24 unless the stop or target fills first"
DRIFT = {
    "surprise_pct": 12.34,
    "z": 1.83,
    "report_date": "2026-10-26",
    "holding_sessions": 20,
    "time_exit_at": "2026-11-24T20:45:00+00:00",
}


@pytest.fixture
def eval_res():
    return LLMTradeEvaluation(
        approved=True,
        rejection_reason=None,
        contract="WINR",
        direction="LONG",
        entry_price=50.0,
        stop_loss=47.53,
        take_profit=57.41,
        stop_distance_points=2.47,
        target_distance_points=7.41,
        risk_reward_ratio=3.0,
        risk_dollars=98.8,
        reward_dollars=296.4,
        notional_value=2000.0,
        effective_leverage=0.2,
        macro_clearance=True,
        thesis_summary="LLM commentary (not a gate): Strong follow-through",
        quantity=40.0,
        asset_class=AssetClass.EQUITY,
    )


def test_drift_card_shows_the_event_and_the_time_exit(eval_res):
    text = format_alert_card(eval_res, "pead_long", probe_risk_cap=100.0, drift=DRIFT)
    assert "PAPER PROBE" in text
    assert EVENT_LINE in text
    assert HOLD_LINE in text
    # Probe header, drift block, then the ordinary card.
    assert text.index("PAPER PROBE") < text.index(EVENT_LINE) < text.index(HOLD_LINE) < text.index("TRADE SIGNAL")
    assert text.endswith(format_alert_card(eval_res, "pead_long"))


def test_drift_card_without_an_exit_date_names_the_session(eval_res):
    text = format_alert_card(eval_res, "pead_long", probe_risk_cap=100.0, drift={**DRIFT, "time_exit_at": None})
    assert "⏱️ 20-session hold · time exit 15:45 NY on session 20 unless the stop or target fills first" in text


def test_drift_card_escapes_the_report_date(eval_res):
    text = format_alert_card(eval_res, "pead_long", drift={**DRIFT, "report_date": "<b>x</b>"})
    assert "(report &lt;b&gt;x&lt;/b&gt;)" in text


@pytest.mark.parametrize("probe_risk_cap", [None, 100.0])
def test_no_drift_is_byte_identical_to_the_ordinary_card(eval_res, probe_risk_cap):
    assert format_alert_card(eval_res, "pead_long", probe_risk_cap=probe_risk_cap, drift=None) == format_alert_card(
        eval_res, "pead_long", probe_risk_cap=probe_risk_cap
    )


async def test_send_signal_alert_passes_the_drift_facts_to_the_card(temp_db, eval_res):
    notifier = TelegramNotifier(bot_token="test_token", chat_id="123456", db=temp_db)
    send = AsyncMock(return_value=SimpleNamespace(message_id=7))
    notifier.app = SimpleNamespace(bot=SimpleNamespace(send_message=send))

    await notifier.send_signal_alert(eval_res, strategy="pead_long", signal_id=1, probe_risk_cap=100.0, drift=DRIFT)

    assert EVENT_LINE in send.await_args.kwargs["text"]
    assert HOLD_LINE in send.await_args.kwargs["text"]
