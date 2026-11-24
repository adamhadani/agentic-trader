"""One classification of filing-veto evidence, shared by the evaluator gate, the tap gate and the scan journal.

``risk`` stays pure: the rule lives in ``agentic_trader.risk``; this module reads a ``FilingsLookup`` (a data
type) and says what the rule saw. A stale, missing or uncovered capture never refuses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentic_trader.risk import Rejection, blocking_filing, filing_veto_block


if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from agentic_trader.config import FilingVetoConfig
    from agentic_trader.data.filings_capture import FilingsLookup
    from agentic_trader.screeners.base import ScreenerCandidate


def filing_veto_exempt(candidate: ScreenerCandidate) -> bool:
    """Whether a card is exempt from the filing veto: the cards `TradingCopilot._release_exit_scope` calls
    non-native. Catalog cards carry `catalog_event` (their `alpha_policy` too), paper probes `probe`, alpha
    cards `alpha_version`/`alpha_policy`; a native strategy candidate carries none of them. The evaluator gate
    and the scan journal both read this one predicate."""
    return (
        candidate.catalog_event is not None
        or candidate.probe
        or candidate.alpha_version is not None
        or candidate.alpha_policy is not None
    )


def filing_veto_evidence(
    lookup: FilingsLookup, symbol: str, today: date, policy: FilingVetoConfig, *, exempt: bool = False
) -> tuple[dict[str, Any], Rejection | None]:
    """The provenance block for one long equity and the rule's rejection (None unless the evidence is ok, the
    symbol is inside an enforced category's window and the card is not ``exempt``).

    The label (``would_block``, ``category``, ``reason`` ...) names the newest qualifying filing over all windows;
    the refusal reads only ``policy.enforced_windows()``, so a preview-only category never masks an enforced one.
    ``would_refuse`` says whether enforcing would refuse this card (regardless of mode).
    """
    as_of = lookup.as_of.isoformat() if lookup.as_of else None
    mode = policy.entry_block
    if lookup.status != "ok":
        return {"status": lookup.status, "mode": mode}, None
    if not lookup.covers(symbol):
        return {"status": "uncovered", "mode": mode, "as_of": as_of}, None
    windows = policy.windows()
    filings = lookup.for_symbol(symbol)
    filing = blocking_filing(filings, symbol, today, windows=windows)
    label = filing_veto_block(filings, symbol, today, windows=windows)
    refusal = filing_veto_block(filings, symbol, today, windows=policy.enforced_windows())
    rejection = None if exempt else refusal
    block: dict[str, Any] = {
        "status": "ok",
        "mode": mode,
        "as_of": as_of,
        "would_block": label is not None,
        "category": filing.category.value if filing is not None else None,
        "form": filing.form if filing is not None else None,
        "items": list(filing.items) if filing is not None else None,
        "filing_date": filing.filing_date.isoformat() if filing is not None else None,
        "accession": filing.accession if filing is not None else None,
        "reason": label.reason if label is not None else None,
        "would_refuse": rejection is not None,
    }
    if exempt:
        block["exempt"] = True
    return block, rejection


def journal_filings_block(block: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The scan journal's per-candidate summary of an evidence block: ``{status, would_block, category,
    would_refuse, exempt}``; the last four are None unless the status is ok. None when no block was built."""
    if block is None:
        return None
    ok = block.get("status") == "ok"
    return {
        "status": block.get("status"),
        "would_block": block.get("would_block") if ok else None,
        "category": block.get("category") if ok else None,
        "would_refuse": block.get("would_refuse") if ok else None,
        "exempt": bool(block.get("exempt")) if ok else None,
    }
