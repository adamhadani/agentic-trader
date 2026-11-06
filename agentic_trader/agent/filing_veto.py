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


def filing_veto_evidence(
    lookup: FilingsLookup, symbol: str, today: date, policy: FilingVetoConfig
) -> tuple[dict[str, Any], Rejection | None]:
    """The provenance block for one long equity and the rule's rejection (None unless the evidence is ok and
    the symbol is inside a blocking filing's window)."""
    as_of = lookup.as_of.isoformat() if lookup.as_of else None
    mode = policy.entry_block
    if lookup.status != "ok":
        return {"status": lookup.status, "mode": mode}, None
    if not lookup.covers(symbol):
        return {"status": "uncovered", "mode": mode, "as_of": as_of}, None
    windows = policy.windows()
    filings = lookup.for_symbol(symbol)
    rejection = filing_veto_block(filings, symbol, today, windows=windows)
    filing = blocking_filing(filings, symbol, today, windows=windows)
    return {
        "status": "ok",
        "mode": mode,
        "as_of": as_of,
        "would_block": rejection is not None,
        "category": filing.category.value if filing is not None else None,
        "form": filing.form if filing is not None else None,
        "items": list(filing.items) if filing is not None else None,
        "filing_date": filing.filing_date.isoformat() if filing is not None else None,
        "accession": filing.accession if filing is not None else None,
        "reason": rejection.reason if rejection is not None else None,
    }, rejection


def journal_filings_block(block: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The scan journal's per-candidate summary of an evidence block: ``{status, would_block, category}``;
    ``would_block`` and ``category`` are None unless the status is ok. None when no block was built."""
    if block is None:
        return None
    ok = block.get("status") == "ok"
    return {
        "status": block.get("status"),
        "would_block": block.get("would_block") if ok else None,
        "category": block.get("category") if ok else None,
    }
