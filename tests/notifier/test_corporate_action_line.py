"""The corporate-action card line: preview and enforce blocks, dividend notes, nothing otherwise."""

import pytest

from agentic_trader.agent.evaluator import LLMTradeEvaluation
from agentic_trader.notifier.telegram_bot import format_alert_card, format_terminal_card
from tests.notifier.test_book_line import eval_res as base_eval_res  # noqa: F401  (fixture)


ACTION = {
    "id": "s",
    "kind": "forward_split",
    "symbol": "NVDA",
    "related_symbol": None,
    "effective_on": "2026-10-15",
    "payable_date": None,
    "describe": "forward split 10-for-1",
}
NOTE = {
    "id": "d",
    "kind": "cash_dividend",
    "symbol": "NVDA",
    "related_symbol": None,
    "effective_on": "2026-10-14",
    "payable_date": "2026-11-11",
    "describe": "cash dividend $0.98/share",
}


def block(**overrides):
    return {
        "status": "ok",
        "mode": "preview",
        "as_of": "2026-10-12",
        "would_block": False,
        "action": None,
        "reason": None,
        "note": None,
        **overrides,
    }


def renderers(eval_res, corporate):
    with_block = eval_res.model_copy(update={"corporate_actions": corporate})
    return [format_alert_card(with_block, "TREND_PULLBACK"), format_terminal_card(with_block, "TREND_PULLBACK")]


def test_preview_block_line(base_eval_res):  # noqa: F811
    for text in renderers(base_eval_res, block(would_block=True, action=ACTION, reason="x")):
        assert "🏷 Corporate action: NVDA forward split 10-for-1 effective 2026-10-15 — preview, card unchanged" in text


def test_enforce_block_line_has_no_preview_suffix(base_eval_res):  # noqa: F811
    for text in renderers(base_eval_res, block(mode="enforce", would_block=True, action=ACTION, reason="x")):
        assert "🏷 Corporate action: NVDA forward split 10-for-1 effective 2026-10-15" in text and "preview" not in text


def test_dividend_note_line(base_eval_res):  # noqa: F811
    for text in renderers(base_eval_res, block(note=NOTE)):
        assert "🏷 Ex-dividend 2026-10-14 (cash dividend $0.98/share)" in text


@pytest.mark.parametrize(
    "corporate",
    [
        None,
        {"status": "stale", "mode": "preview"},
        {"status": "ok", "mode": "preview", "would_block": False, "action": None, "note": None},
    ],
)
def test_nothing_to_say_renders_no_line(base_eval_res, corporate):  # noqa: F811
    for text in renderers(base_eval_res, corporate):
        assert "🏷" not in text


def test_old_payload_without_the_field_still_validates(base_eval_res):  # noqa: F811
    payload = base_eval_res.model_dump(mode="json")
    payload.pop("corporate_actions", None)
    assert LLMTradeEvaluation.model_validate(payload).corporate_actions is None
