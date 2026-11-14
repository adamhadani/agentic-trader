"""The weekly lane scorecard as Telegram HTML: activity, realized record, counterfactual record and the account.

Pure formatting over a ``LaneScorecard`` (``research/setups/scorecard.py``). Only numbers, lane names, fixed text and
a lane's note (an exception type name) are printed; every free-text value is HTML-escaped.
"""

import html

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.setups.scorecard import AccountRow, LaneRow, LaneScorecard


SCORECARD_FOOTER = "Counterfactual labels assume study-price fills; no lane has validated edge."

_TITLES = {"native": "Native cards", "pead": "PEAD paper probe", "insider_watch": "Insider watch cards"}
_NA = "n/a"
_EARLY_EXITS = "early exits; stop-heavy until windows complete"


def _r(value: float | None) -> str:
    return _NA if value is None else f"{value:+.2f}R"


def _money(value: float | None) -> str:
    return _NA if value is None else f"${value:,.2f}"


def _week(row: LaneRow) -> str:
    attempted = row.attempted_auto + row.attempted_tap
    if not (row.sent or attempted or row.filled or row.closed):
        return "This week: none"
    realized = _r(row.realized_r_sum) if row.closed else _NA
    return (
        f"This week: sent {row.sent}, attempted {attempted} (auto {row.attempted_auto}, tap {row.attempted_tap}), "
        f"unfilled {row.unfilled}, filled {row.filled}, closed {row.closed}, realized {realized}"
    )


def _record(row: LaneRow, record_days: int) -> str:
    closed = f"{row.record_closed} closed" if row.record_closed else "none closed"
    return f"Last {record_days} days: {closed}, mean {_r(row.record_mean_r)}, {row.open_positions} open"


def _counterfactual(row: LaneRow) -> str:
    note = f" ({html.escape(row.note)})" if row.note else ""
    if row.cf_mature is None:
        return f"Counterfactual: {_NA}{note}"
    total = row.cf_total if row.cf_total is not None else row.cf_mature
    matured = f"{row.cf_mature} of {total} matured so far" if total else "none matured"
    early = f" ({_EARLY_EXITS})" if row.cf_mature < total else ""
    return (
        f"Counterfactual: {matured}, mean {_r(row.cf_mean_r)}, SPY beta-matched excess {_r(row.cf_mean_excess_r)}"
        f"{early}{note}"
    )


def _account(account: AccountRow | None) -> str:
    label = "Account (adjusted equity): "
    if account is None or (account.start_equity is None and account.end_equity is None):
        return label + _NA
    text = f"{label}{_money(account.start_equity)} to {_money(account.end_equity)}"
    if account.start_equity and account.end_equity is not None and account.start_equity > 0:
        text += f" ({(account.end_equity / account.start_equity - 1) * 100:+.2f}%)"
    return text


def format_scorecard(card: LaneScorecard) -> str:
    """The scorecard text: header, one block per lane in the card's order, the account line and the footer."""
    start = card.window_start.astimezone(ET_TZ).date().isoformat()
    end = card.window_end.astimezone(ET_TZ).date().isoformat()
    lines = [f"📊 <b>Weekly lane scorecard</b> {start} to {end} (ET)"]
    for row in card.lanes:
        lines.extend(
            [
                "",
                f"<b>{_TITLES[row.lane]}</b>",
                _week(row),
                _record(row, card.record_days),
                _counterfactual(row),
            ]
        )
    lines.extend(["", _account(card.account), SCORECARD_FOOTER])
    return "\n".join(lines)
