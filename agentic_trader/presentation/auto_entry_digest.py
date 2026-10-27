"""The daily paper auto-entry digest: one line per marked card and the optimistic-fill footer.

Pure formatting. The rows come from the journal, the signals table and the entry/cancel work items
(``WorkflowStore.auto_entry_digest_rows``). The text is sent as HTML, so every free-text detail is escaped.
"""

import html
from dataclasses import dataclass
from datetime import date
from typing import Literal


DigestOutcome = Literal[
    "filled", "cancelled_unfilled", "still_resting", "refused", "dismissed", "cancelled_by_operator", "abandoned"
]

DIGEST_FOOTER = "Paper fills are optimistic: no queue position or market impact."


@dataclass(frozen=True)
class AutoEntryDigestRow:
    signal_id: int
    symbol: str
    outcome: DigestOutcome
    detail: str | None = None


def _wording(row: AutoEntryDigestRow) -> str:
    detail = html.escape(row.detail) if row.detail else None
    match row.outcome:
        case "filled":
            return f"filled {detail}" if detail else "filled"
        case "cancelled_unfilled":
            return "cancelled unfilled at the close"
        case "still_resting":
            return "still resting"
        case "refused":
            return f"refused: {detail}" if detail else "refused"
        case "dismissed":
            return "dismissed before placement"
        case "cancelled_by_operator":
            return "cancelled by you"
        case "abandoned":
            return f"abandoned: {detail}" if detail else "abandoned"


def format_auto_entry_digest(rows: list[AutoEntryDigestRow], *, et_date: date) -> str:
    """The digest text: header, one line per card in the given (rank) order, the footer."""
    lines = [f"🤖 Auto-entry digest — {et_date.isoformat()}"]
    lines.extend(f"{row.symbol} #{row.signal_id}: {_wording(row)}" for row in rows)
    lines.append(DIGEST_FOOTER)
    return "\n".join(lines)
