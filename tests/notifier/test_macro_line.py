"""The card's Macro Check line: an unverified tier-1 calendar is labelled, never shown as "Cleared"."""

from agentic_trader.notifier.telegram_bot import format_alert_card, format_terminal_card
from tests.notifier.test_book_line import eval_res  # noqa: F401  (fixture)


NOTE = "static tier-1 calendar expired; live feed ok"


def test_an_unverified_calendar_is_labelled_on_both_cards(eval_res):  # noqa: F811
    unverified = eval_res.model_copy(update={"macro_note": NOTE})
    alert = format_alert_card(unverified, "TREND_PULLBACK")
    terminal = format_terminal_card(unverified, "TREND_PULLBACK")
    assert f"Macro Check:</b> Unverified — {NOTE}" in alert
    assert f"Macro Check:      Unverified — {NOTE}" in terminal
    assert "Cleared" not in alert and "Cleared" not in terminal


def test_the_alert_card_escapes_the_note(eval_res):  # noqa: F811
    alert = format_alert_card(eval_res.model_copy(update={"macro_note": "live feed failed (<Odd>)"}), "TREND_PULLBACK")
    assert "Unverified — live feed failed (&lt;Odd&gt;)" in alert


def test_no_note_keeps_cleared(eval_res):  # noqa: F811
    assert eval_res.macro_note is None
    assert "Macro Check:</b> Cleared" in format_alert_card(eval_res, "TREND_PULLBACK")
    assert "Macro Check:      Cleared" in format_terminal_card(eval_res, "TREND_PULLBACK")
