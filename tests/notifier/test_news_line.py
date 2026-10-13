"""The news card line: one labelled line per status, escaped, clipped, nothing without a block."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agentic_trader.notifier.telegram_bot import _news_line, format_alert_card, format_terminal_card
from tests.notifier.test_book_line import eval_res as base_eval_res  # noqa: F401  (fixture)


NOW = datetime(2026, 10, 13, 14, 35, tzinfo=UTC)


def block(status="ok", **overrides):
    base = {
        "status": status,
        "as_of": NOW.isoformat(),
        "window_hours": 48,
        "n_items": 4,
        "n_market_wide": 1,
        "truncated": False,
        "headlines": [
            {
                "id": 1,
                "created_at": (NOW - timedelta(hours=2)).isoformat(),
                "source": "benzinga",
                "headline": "Apple misses Q3 <est> & guides lower",
            }
        ],
        "score": {
            "tone": "negative",
            "relevance": "company",
            "catalyst": "earnings",
            "one_line": "Revenue miss;\n guidance cut.",
        },
        "model": "m",
        "prompt_sha256": "a" * 64,
        "error_type": None,
        "page_sha256": "p",
        "applied": False,
    }
    return {**base, **overrides}


def test_ok_line_has_counts_tone_catalyst_one_liner_and_latest_headline():
    line = _news_line(block(), markup=False, now=NOW)
    assert line == (
        '📰 News (context, not a gate): 4 items/48h · tone negative · earnings · "Revenue miss; guidance cut." '
        '· latest "Apple misses Q3 <est> & guides lower" (benzinga, 2h ago)\n'
    )


def test_news_line_escapes_html_and_collapses_whitespace():
    line = _news_line(block(), markup=True, now=NOW)
    assert "&lt;est&gt; &amp; guides" in line and "<est>" not in line
    assert "Revenue miss; guidance cut." in line and "\n guidance" not in line
    assert line.startswith("📰 News (context, not a gate):") and line.endswith("\n")


def test_headline_is_clipped_and_age_in_days():
    long = block(
        headlines=[
            {
                "id": 1,
                "created_at": (NOW - timedelta(hours=30)).isoformat(),
                "source": "benzinga",
                "headline": "x" * 300,
            }
        ]
    )
    line = _news_line(long, markup=False, now=NOW)
    assert '"' + "x" * 109 + '…"' in line and "(benzinga, 1d ago)" in line


@pytest.mark.parametrize(
    ("status", "overrides", "expected"),
    [
        (
            "unscored",
            {"score": None, "model": None},
            '4 items/48h · not scored · latest "Apple misses Q3 <est> & guides lower" (benzinga, 2h ago)',
        ),
        ("no_news", {"n_items": 0, "headlines": [], "score": None}, "none in 48h"),
        ("score_failed", {"score": None, "error_type": "TimeoutError"}, "4 items/48h · scoring failed (TimeoutError)"),
        (
            "unavailable",
            {"n_items": 0, "headlines": [], "score": None, "error_type": "ConnectionError", "as_of": None},
            "unavailable (ConnectionError)",
        ),
        (
            "unavailable",
            {"n_items": 0, "headlines": [], "score": None, "error_type": None, "as_of": None},
            "unavailable",
        ),
    ],
)
def test_other_statuses(status, overrides, expected):
    assert (
        _news_line(block(status, **overrides), markup=False, now=NOW) == f"📰 News (context, not a gate): {expected}\n"
    )


def test_truncated_flag_and_missing_or_unknown_block():
    assert _news_line(block(truncated=True), markup=False, now=NOW).endswith(" · feed truncated\n")
    assert _news_line(None, markup=False) == "" and _news_line({}, markup=False) == ""
    assert _news_line(block("weird"), markup=False, now=NOW) == ""


def test_cards_render_the_line_from_the_evaluation(base_eval_res):  # noqa: F811
    with_block = base_eval_res.model_copy(update={"news_context": block()})
    assert "News (context, not a gate)" in format_alert_card(with_block, "TREND_PULLBACK")
    assert "News (context, not a gate)" in format_terminal_card(with_block, "TREND_PULLBACK")
    assert "News (context" not in format_alert_card(base_eval_res, "TREND_PULLBACK")
