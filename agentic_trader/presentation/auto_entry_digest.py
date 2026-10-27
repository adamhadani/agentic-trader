"""The daily paper auto-entry digest: one line per marked card and the optimistic-fill footer.

Pure formatting. The rows come from the journal, the signals table and the entry/cancel work items
(``WorkflowStore.auto_entry_digest_rows``). The text is sent as HTML, so every free-text detail is escaped.
"""

import html
from datetime import date

from agentic_trader.execution.auto_entry import AutoEntryDigestRow


DIGEST_FOOTER = "Paper fills are optimistic: no queue position or market impact."


def _wording(row: AutoEntryDigestRow) -> str:
    detail = html.escape(row.detail) if row.detail else None
    match row.outcome:
        case "filled":
            return f"filled {detail}" if detail else "filled"
        case "cancelled_unfilled":
            return "cancelled unfilled at the close"
        case "still_resting":
            return "not yet resolved (entry or cancel pending)"
        case "refused":
            return f"refused: {detail}" if detail else "refused"
        case "dismissed":
            return "dismissed before placement"
        case "cancelled_by_operator":
            return "cancelled by you"
        case "abandoned":
            return "not placed in time"


def format_auto_entry_digest(rows: list[AutoEntryDigestRow], *, et_date: date) -> str:
    """The digest text: header, one line per card in the given (rank) order, the footer."""
    lines = [f"🤖 Auto-entry digest — {et_date.isoformat()}"]
    lines.extend(f"{row.symbol} #{row.signal_id}: {_wording(row)}" for row in rows)
    lines.append(DIGEST_FOOTER)
    return "\n".join(lines)
