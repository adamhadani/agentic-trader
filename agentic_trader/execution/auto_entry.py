"""Paper auto-entry: the one resolver, the card marker rule and the sweep's attempt record."""

import html
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Literal

from agentic_trader.config import AppConfig
from agentic_trader.constants import ExecutionMode, SignalStatus
from agentic_trader.execution.durable import EntryTrigger, ScanTrigger


AUTO_ENTRY_KEY = "auto_entry"
AUTO_ENTRY_MARKER = {"mode": "paper"}
# The journaled reason: a desk-authored reply without markup, at most this many characters.
AUTO_ENTRY_REASON_CHARS = 300
ABANDONED_REASON = "not_attempted_in_time"
NOT_PAPER_REASON = "not_paper_scope"

_MARKUP = re.compile(r"<[^>]*>")
# A reply's leading symbols and Title Case label ("⚠️ Execution Rejected: "), which the re-card line restates.
_REPLY_LABEL = re.compile(r"^[^\w(]*(?:[A-Z][a-z]+(?: [A-Z][a-z]+)*:(?:\s+|$))?")


def auto_entry_active(config: AppConfig) -> bool:
    """True only on Alpaca paper with session-bounded entries and the switch on; the simulator is inactive."""
    return (
        config.execution.auto_entry == "paper"
        and config.execution_mode == ExecutionMode.ALPACA
        and config.alpaca_paper
        and config.execution.native_entry_lifetime == "session"
    )


def auto_entry_marker(
    config: AppConfig,
    *,
    trigger: ScanTrigger,
    is_catalog: bool,
    alpha: bool,
    lifetime_marker: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """The marker an eligible card records: a native, lifetime-marked card of a scheduled suggestion scan."""
    if (
        not auto_entry_active(config)
        or trigger != ScanTrigger.SUGGESTION_SCAN
        or is_catalog
        or alpha
        or lifetime_marker is None
    ):
        return None
    return dict(AUTO_ENTRY_MARKER)


AutoEntryOutcome = Literal["entered", "refused", "skipped_not_pending", "abandoned"]


@dataclass(frozen=True)
class AutoEntryAttempt:
    """One sweep attempt on one marked card: the ``auto_entry_attempted`` event's payload."""

    signal_id: int
    scan_id: str | None
    outcome: AutoEntryOutcome
    reason: str | None
    work_status: str | None
    signal_status: str
    attempted_at: datetime

    def payload(self) -> dict[str, Any]:
        return {**asdict(self), "attempted_at": self.attempted_at.isoformat()}


def attempt_reason(reply_text: str) -> str:
    """A desk-authored reply as a journal reason: tags stripped, entities decoded, whitespace collapsed, capped.

    The reply's leading symbols and label and its trailing period go too, so a re-card reads "Auto-entry
    refused: cap reached. Tap Execute…". Decoded because the reason is queued raw on a re-card, whose
    renderer escapes it.
    """
    text = " ".join(html.unescape(_MARKUP.sub("", reply_text)).split())
    return _REPLY_LABEL.sub("", text).removesuffix(".")[:AUTO_ENTRY_REASON_CHARS]


def outcome_after_call(signal_status: str, entry_trigger: EntryTrigger | None) -> AutoEntryOutcome:
    """The attempt's outcome from the signal (and its entry work item's trigger) re-read after the entry call.

    An entry work item the sweep did not create means a tap (or the CLI) owns this card's entry, and
    a card dismissed while the call ran kept its veto: both are ``skipped_not_pending``.
    """
    if entry_trigger is not None and entry_trigger != EntryTrigger.AUTO:
        return "skipped_not_pending"
    if signal_status in (SignalStatus.SUBMITTING, SignalStatus.EXECUTED):
        return "entered"
    if signal_status in (SignalStatus.PENDING, SignalStatus.FAILED, SignalStatus.EXPIRED):
        return "refused"
    return "skipped_not_pending"
