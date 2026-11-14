"""``format_scorecard``: pure Telegram HTML over a ``LaneScorecard``."""

from datetime import UTC, datetime

from agentic_trader.presentation.scorecard import SCORECARD_FOOTER, format_scorecard
from agentic_trader.research.setups.scorecard import AccountRow, LaneRow, LaneScorecard


def _row(lane: str, **values) -> LaneRow:
    base = {
        "lane": lane,
        "sent": 0,
        "entered_auto": 0,
        "entered_tap": 0,
        "filled": 0,
        "closed": 0,
        "realized_r_sum": 0.0,
        "record_closed": 0,
        "record_mean_r": None,
        "open_positions": 0,
        "cf_mature": 0,
        "cf_mean_r": None,
        "cf_mean_excess_r": None,
        "note": None,
    }
    return LaneRow(**{**base, **values})


def _card(*rows: LaneRow, account: AccountRow | None = None) -> LaneScorecard:
    return LaneScorecard(
        window_start=datetime(2026, 11, 8, 5, 0, tzinfo=UTC),
        window_end=datetime(2026, 11, 14, 14, 0, tzinfo=UTC),
        lanes=rows,
        account=account,
    )


def test_full_card_snapshot():
    card = _card(
        _row(
            "native",
            sent=4,
            entered_auto=2,
            entered_tap=1,
            filled=3,
            closed=2,
            realized_r_sum=1.0,
            record_closed=3,
            record_mean_r=0.8333,
            open_positions=1,
            cf_mature=12,
            cf_mean_r=0.05,
            cf_mean_excess_r=-0.02,
        ),
        _row(
            "pead",
            sent=2,
            entered_auto=1,
            filled=1,
            closed=1,
            realized_r_sum=-0.5,
            record_closed=1,
            record_mean_r=-0.5,
            cf_mature=2,
            cf_mean_r=0.2,
        ),
        _row("insider_watch"),
        account=AccountRow(start_equity=100000.0, end_equity=101500.5),
    )

    assert format_scorecard(card).split("\n") == [
        "📊 <b>Weekly lane scorecard</b> 2026-11-08 to 2026-11-14 (ET)",
        "",
        "<b>Native cards</b>",
        "This week: sent 4, entered 3 (auto 2, tap 1), filled 3, closed 2, realized +1.00R",
        "Last 90 days: 3 closed, mean +0.83R, 1 open",
        "Counterfactual: 12 matured, mean +0.05R, SPY beta-matched excess -0.02R",
        "",
        "<b>PEAD paper probe</b>",
        "This week: sent 2, entered 1 (auto 1, tap 0), filled 1, closed 1, realized -0.50R",
        "Last 90 days: 1 closed, mean -0.50R, 0 open",
        "Counterfactual: 2 matured, mean +0.20R, SPY beta-matched excess n/a",
        "",
        "<b>Insider watch cards</b>",
        "This week: none",
        "Last 90 days: none closed, mean n/a, 0 open",
        "Counterfactual: none matured, mean n/a, SPY beta-matched excess n/a",
        "",
        "Account (adjusted equity): $100,000.00 to $101,500.50 (+1.50%)",
        SCORECARD_FOOTER,
    ]


def test_footer_text_is_fixed():
    assert SCORECARD_FOOTER == "Counterfactual labels assume study-price fills; no lane has validated edge."


def test_missing_account_and_unavailable_counterfactual_render_n_a():
    card = _card(
        _row("native", cf_mature=None, note="bar fetch failed: TimeoutError"),
        _row("pead"),
        _row("insider_watch"),
    )
    text = format_scorecard(card)

    assert "Account (adjusted equity): n/a" in text
    assert "Counterfactual: n/a (bar fetch failed: TimeoutError)" in text
    assert text.endswith(SCORECARD_FOOTER)


def test_account_with_one_missing_end_is_n_a():
    card = _card(_row("native"), account=AccountRow(start_equity=None, end_equity=101.0))
    assert "Account (adjusted equity): n/a to $101.00" in format_scorecard(card)


def test_notes_are_html_escaped():
    card = _card(_row("native", cf_mature=None, note="<b>x</b> & y"))
    text = format_scorecard(card)
    assert "&lt;b&gt;x&lt;/b&gt; &amp; y" in text and "<b>x</b>" not in text
