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
    assert text.index("PAPER PROBE") < text.index(EVENT_LINE) < text.index(HOLD_LINE) < text.index("📋 <b>SETUP:")
    assert text.endswith(format_alert_card(eval_res, "pead_long"))


def test_drift_card_renders_an_early_close_exit_time_and_date(eval_res):
    # 2026-11-27 closes at 13:00 New York: the exit is 12:45 NY (17:45 UTC), not 15:45.
    text = format_alert_card(eval_res, "pead_long", drift={**DRIFT, "time_exit_at": "2026-11-27T17:45:00+00:00"})
    assert "⏱️ 20-session hold · time exit 12:45 NY on 2026-11-27 unless the stop or target fills first" in text
    assert "15:45" not in text


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


BASE_RATE = {
    "status": "measured",
    "sha256": "f" * 64,
    "expected_mean": 0.0123,
    "expected_se": 0.0456,
    "window_start": "2016-08-01",
    "window_end": "2026-07-31",
    "recent_from": "2025-01-01",
    "overall_mean": 0.0712,
    "n_leg": 12345,
    "recent_mean": 0.0321,
    "n_recent": 678,
    "recent_p2_mean": -0.0154,
}
BASE_RATE_LINES = (
    "• <b>Expected excess</b> (PEAD v2, LONG): +0.01R ± 0.05 vs beta-matched SPY after cost\n"
    "  replay 2016–26: +0.07R over 12,345 events · 2025–26: +0.03R over 678 (shrunk toward 0) · "
    "beat vs all reporters 2025–26: -0.02R\n"
    "<i>Not validated alpha. PEAD v2 events replayed on SIP bars in the native card unit; "
    "EPS and consensus as served today, not point-in-time.</i>\n"
)


def test_drift_card_renders_base_rate_block(eval_res):
    text = format_alert_card(eval_res, "pead_long", probe_risk_cap=100.0, drift={**DRIFT, "base_rate": BASE_RATE})
    assert f"{HOLD_LINE}\n{BASE_RATE_LINES}\n" in text
    assert text.index(HOLD_LINE) < text.index("Expected excess") < text.index("📋 <b>SETUP:")
    unavailable = format_alert_card(eval_res, "pead_long", drift={**DRIFT, "base_rate": {"status": "unavailable"}})
    assert f"{HOLD_LINE}\n• <b>PEAD base rate:</b> unavailable\n\n" in unavailable


def test_drift_card_without_base_rate_key_renders_as_before(eval_res):
    text = format_alert_card(eval_res, "pead_long", probe_risk_cap=100.0, drift=DRIFT)
    assert f"{HOLD_LINE}\n\n" in text
    assert "base rate" not in text and "Expected excess" not in text
