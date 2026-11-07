"""The filing-veto card line: a preview block that would block, and nothing otherwise."""

import pytest

from agentic_trader.agent.evaluator import LLMTradeEvaluation
from agentic_trader.notifier.telegram_bot import format_alert_card, format_terminal_card
from tests.notifier.test_book_line import eval_res as base_eval_res  # noqa: F401  (fixture)


REASON = "Filing veto: AAPL filed 8-K Items 2.05 on 2026-10-07 (30 days ago) — within the 60-day adverse_8k window."
LINE = (
    "🗂 Filing veto (preview): AAPL filed 8-K Items 2.05 on 2026-10-07 (30 days ago) — within the 60-day "
    "adverse_8k window — card unchanged"
)


def block(**overrides):
    return {
        "status": "ok",
        "mode": "preview",
        "as_of": "2026-11-06T11:30:00+00:00",
        "would_block": True,
        "category": "adverse_8k",
        "form": "8-K",
        "items": ["2.05"],
        "filing_date": "2026-10-07",
        "accession": "0000320193-26-000101",
        "reason": REASON,
        **overrides,
    }


def renderers(eval_res, filings):
    with_block = eval_res.model_copy(update={"filings": filings})
    return [format_alert_card(with_block, "TREND_PULLBACK"), format_terminal_card(with_block, "TREND_PULLBACK")]


def test_preview_block_that_would_block_renders_the_line(base_eval_res):  # noqa: F811
    for text in renderers(base_eval_res, block()):
        assert LINE in text


def test_the_line_is_html_escaped_on_the_alert_card(base_eval_res):  # noqa: F811
    alert, terminal = renderers(base_eval_res, block(reason="Filing veto: A&B filed 424B5 on 2026-11-05 (1 day ago)."))
    assert "🗂 Filing veto (preview): A&amp;B filed 424B5 on 2026-11-05 (1 day ago) — card unchanged" in alert
    assert "🗂 Filing veto (preview): A&B filed 424B5 on 2026-11-05 (1 day ago) — card unchanged" in terminal


@pytest.mark.parametrize(
    "filings",
    [
        None,
        {"status": "stale", "mode": "preview"},
        {"status": "unavailable", "mode": "preview"},
        {"status": "uncovered", "mode": "preview", "as_of": "2026-11-06T11:30:00+00:00"},
        block(would_block=False, category=None, form=None, items=None, filing_date=None, accession=None, reason=None),
        # Enforce never sends a card with a blocking filing; a block that says so anyway gets no line.
        block(mode="enforce"),
    ],
)
def test_nothing_to_say_renders_no_line(base_eval_res, filings):  # noqa: F811
    for text in renderers(base_eval_res, filings):
        assert "🗂" not in text and "Filing veto" not in text


def test_old_payload_without_the_field_still_validates(base_eval_res):  # noqa: F811
    payload = base_eval_res.model_dump(mode="json")
    payload.pop("filings", None)
    assert LLMTradeEvaluation.model_validate(payload).filings is None
