"""The book-sizing line under the evidence block, on both renderers."""

import pytest

from agentic_trader.agent.evaluator import LLMTradeEvaluation
from agentic_trader.constants import AssetClass
from agentic_trader.notifier.telegram_bot import format_alert_card, format_terminal_card


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


def block(status="applied", mode="enforce", factor=0.7, would_scale=True, reason=None):
    return {
        "status": status,
        "mode": mode,
        "factor": factor,
        "would_scale": would_scale,
        "vol_before_pct": 0.0062,
        "vol_after_full_pct": 0.0081,
        "budget_pct": 0.008,
        "reason": reason,
    }


APPLIED = "📐 Book: portfolio vol 0.62% → 0.81%/day with this card (budget 0.80%; size ×0.70)"


def renderers(eval_res, book):
    return [
        format_alert_card(eval_res, "TREND_PULLBACK", book_sizing=book),
        format_terminal_card(eval_res, "TREND_PULLBACK", book_sizing=book),
    ]


def test_applied_line(eval_res):
    for text in renderers(eval_res, block()):
        assert APPLIED in text and "preview" not in text


def test_preview_line_says_size_unchanged(eval_res):
    for text in renderers(eval_res, block("unchanged", "preview")):
        assert f"{APPLIED} — preview, size unchanged" in text


def test_preview_with_factor_one_has_no_suffix(eval_res):
    for text in renderers(eval_res, block("unchanged", "preview", factor=1.0, would_scale=False)):
        assert "size ×1.00)" in text and "preview" not in text


def test_unavailable_line(eval_res):
    unavailable = block("unavailable", factor=None, would_scale=False, reason="missing_bars")
    for text in renderers(eval_res, unavailable):
        assert "📐 Book: portfolio vol unavailable (covariance: missing_bars)" in text


def test_not_applicable_and_absent_render_no_line(eval_res):
    for book in (None, block("not_applicable", factor=None, would_scale=False)):
        for text in renderers(eval_res, book):
            assert "Book:" not in text


def test_old_payload_renders_without_book_line(eval_res):
    assert "Book:" not in format_alert_card(eval_res, "TREND_PULLBACK")
    assert "Book:" not in format_terminal_card(eval_res, "TREND_PULLBACK")
