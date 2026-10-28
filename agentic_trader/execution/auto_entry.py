"""Paper auto-entry: the one resolver, the card marker rule and the sweep's attempt record."""

import html
import re
from collections.abc import Mapping
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
# A synchronous preflight or broker rejection: the reply embeds broker or exception text, which the journal never carries.
REJECTED_AT_SUBMISSION_REASON = "rejected at submission"
# A card the sweep's own tap assessment expired: re-priced (a replacement card was sent), or expired with
# no journaled assessment reason to quote.
REPRICED_REASON = "re-priced"
EXPIRED_REASON = "card expired"
# Refusal texts that interpolate an exception's text, by their desk-authored prefix (after
# ``attempt_reason``): the sweep journals the fixed phrase instead. Admission's own replies are unchanged.
_EXCEPTION_BEARING_REFUSALS = (
    # ``WorkflowStore.enqueue_entry``: f"Account risk unavailable: {exc}. No order submitted."
    ("Account risk unavailable", "account risk unavailable"),
    # ``EntryExecutionService.dispatch_one`` around ``_preflight``: f"Admission evidence unavailable ({type}: {exc})…"
    ("Admission evidence unavailable", "admission evidence unavailable"),
    # ``execute_signal_by_id``'s failure view (``format_execution_html``), whose Error line is the work item's
    # ``error_message``: ``_preflight``'s ``str(exc)`` from the capacity check, the line above, or broker text.
    ("Execution Failed", REJECTED_AT_SUBMISSION_REASON),
)

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


def auto_entry_status_line(config: AppConfig) -> str:
    """The ``/status`` line: the switch and whether this account can act on it."""
    if auto_entry_active(config):
        return "Auto-entry: paper (on)"
    if config.execution.auto_entry == "off":
        return "Auto-entry: off"
    return "Auto-entry: inactive (not an Alpaca paper account)"


def llm_approved(llm_verdict: Mapping[str, Any] | None) -> bool:
    """True only for a verdict the LLM gate applied and approved without error.

    ``applied`` and ``approved`` must be exactly ``True`` and ``error`` absent or None. A card no LLM saw (a
    ``--no-llm`` scan's deterministic approval: no verdict) is never LLM-approved.
    """
    return (
        llm_verdict is not None
        and llm_verdict.get("applied") is True
        and llm_verdict.get("approved") is True
        and llm_verdict.get("error") is None
    )


def auto_entry_marker(
    config: AppConfig,
    *,
    trigger: ScanTrigger,
    is_catalog: bool,
    alpha: bool,
    lifetime_marker: dict[str, Any] | None,
    llm_verdict: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """The marker an eligible card records: a native, lifetime-marked card of a scheduled suggestion scan that the
    LLM gate approved.

    ``llm_verdict`` is the card's recorded ``llm_verdict`` block (``TradingCopilot._llm_block``); see
    ``llm_approved``. Without an LLM approval the card is an ordinary tap card.
    """
    if (
        not auto_entry_active(config)
        or trigger != ScanTrigger.SUGGESTION_SCAN
        or is_catalog
        or alpha
        or lifetime_marker is None
        or not llm_approved(llm_verdict)
    ):
        return None
    return dict(AUTO_ENTRY_MARKER)


AutoEntryOutcome = Literal["entered", "refused", "skipped_not_pending", "abandoned"]
# Why a ``refused`` attempt left its card ``EXPIRED``: the digest and ``cards outcomes`` read this token,
# while ``reason`` carries the desk-authored assessment reason (or "re-priced").
ExpiryKind = Literal["repriced", "expired"]


@dataclass(frozen=True)
class AutoEntryAttempt:
    """One sweep attempt on one marked card: the ``auto_entry_attempted`` event's payload.

    ``expiry_kind`` is set only for an attempt that left its card ``EXPIRED`` (``MISSED``/``EXPIRED`` or
    ``REPRICE`` at the sweep's tap assessment); otherwise it is null.
    """

    signal_id: int
    scan_id: str | None
    outcome: AutoEntryOutcome
    reason: str | None
    work_status: str | None
    signal_status: str
    attempted_at: datetime
    expiry_kind: ExpiryKind | None = None

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


def sweep_reason(reply_text: str) -> str:
    """The journaled reason of a reply: ``attempt_reason``, with exception-bearing refusals as fixed phrases."""
    reason = attempt_reason(reply_text)
    for prefix, fixed in _EXCEPTION_BEARING_REFUSALS:
        if reason.startswith(prefix):
            return fixed
    return reason


@dataclass(frozen=True)
class ExpiredCard:
    """The follow-up of an attempt that left its card ``EXPIRED``: the expiry strike and one notice.

    ``reevaluable`` is decided as the card-expiry sweep decides it (the contract is configured); ``notice`` is
    the "not placed" text, queued raw (desk-authored, never exception text).
    """

    notice: str
    reevaluable: bool


def not_placed_notice(signal_id: int, symbol: str, reason: str, *, new_signal_id: int | None = None) -> str:
    """The "not placed" notice of an auto card the sweep's tap assessment expired (or re-priced)."""
    if new_signal_id is not None:
        return f"🤖 Auto-entry #{signal_id} {symbol} not placed: re-priced; review card #{new_signal_id}."
    return f"🤖 Auto-entry #{signal_id} {symbol} not placed: {reason}."


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


DigestOutcome = Literal[
    "filled", "cancelled_unfilled", "still_resting", "refused", "dismissed", "cancelled_by_operator", "abandoned"
]


@dataclass(frozen=True)
class AutoEntryDigestRow:
    """One marked card's line in the daily digest: its outcome and a desk-authored detail (never error text)."""

    signal_id: int
    symbol: str
    outcome: DigestOutcome
    detail: str | None = None
