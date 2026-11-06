"""The shared filing-veto evidence helper and its scan-journal block (descriptive; nothing reads it as a gate)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from agentic_trader.agent.filing_veto import filing_veto_evidence, journal_filings_block
from agentic_trader.config import FilingVetoConfig, ScanBudget
from agentic_trader.data.edgar_filings import FilingCategory, MatchedFiling
from agentic_trader.data.filings_capture import FilingsLookup
from agentic_trader.execution.durable import EventKind
from agentic_trader.market.session import ET_TZ
from tests.agent.test_scan_budget import budget_desk, candidate, evaluation  # noqa: F401  (fixture + helpers)
from tests.agent.test_scan_shadow_ranker import shadow_desk  # noqa: F401  (fixture)


NOW = datetime.now(UTC)
TODAY = NOW.astimezone(ET_TZ).date()


def _late(symbol: str, days_ago: int = 3) -> MatchedFiling:
    return MatchedFiling(
        symbol=symbol,
        category=FilingCategory.LATE_FILING,
        form="NT 10-K",
        items=(),
        filing_date=TODAY - timedelta(days=days_ago),
        acceptance="2026-11-03T16:05:00.000Z",
        accession="0000320193-26-000101",
    )


def _lookup(status="ok", covered=("AAA", "CCC"), filings=None) -> FilingsLookup:
    if status != "ok":
        return FilingsLookup(status, NOW)
    return FilingsLookup(status, NOW, frozenset(covered), filings if filings is not None else {"AAA": (_late("AAA"),)})


def test_evidence_blocks_match_the_evaluator_shapes():
    policy = FilingVetoConfig(entry_block="preview")
    block, rejection = filing_veto_evidence(_lookup(), "AAA", TODAY, policy)
    assert rejection is not None and rejection.rule == "filing_veto"
    assert block == {
        "status": "ok",
        "mode": "preview",
        "as_of": NOW.isoformat(),
        "would_block": True,
        "category": "late_filing",
        "form": "NT 10-K",
        "items": [],
        "filing_date": (TODAY - timedelta(days=3)).isoformat(),
        "accession": "0000320193-26-000101",
        "reason": rejection.reason,
    }
    clear, none = filing_veto_evidence(_lookup(), "CCC", TODAY, policy)
    assert none is None and clear["would_block"] is False and clear["category"] is None
    assert filing_veto_evidence(_lookup(), "BBB", TODAY, policy) == (
        {"status": "uncovered", "mode": "preview", "as_of": NOW.isoformat()},
        None,
    )
    assert filing_veto_evidence(_lookup("stale"), "AAA", TODAY, policy) == (
        {"status": "stale", "mode": "preview"},
        None,
    )


def test_journal_block_summarises_only_ok_evidence():
    policy = FilingVetoConfig(entry_block="enforce")
    ok, _ = filing_veto_evidence(_lookup(), "AAA", TODAY, policy)
    assert journal_filings_block(ok) == {"status": "ok", "would_block": True, "category": "late_filing"}
    unc, _ = filing_veto_evidence(_lookup(), "BBB", TODAY, policy)
    assert journal_filings_block(unc) == {"status": "uncovered", "would_block": None, "category": None}
    assert journal_filings_block(None) is None


async def test_scan_journals_a_filings_block_per_candidate(shadow_desk, temp_db):  # noqa: F811
    lookup = _lookup()
    shadow_desk.config.filing_veto = shadow_desk.config.filing_veto.model_copy(update={"entry_block": "preview"})
    shadow_desk._filings_lookup = AsyncMock(return_value=lookup)
    # The evaluation carries no filings block (as for a candidate that left before gate 2d): the journal
    # classifies from the scan's lookup, never from the evaluator's result.
    shadow_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=lambda cand, **kw: evaluation(cand))
    qualities = {"AAA": 0.6, "BBB": 0.7, "CCC": 0.8, "DDD": 0.9, "EEE": 0.5}
    shadow_desk.strategy_engine.scan_contract.side_effect = lambda data, **kw: [
        candidate(data.contract, qualities[data.contract], direction="SHORT" if data.contract == "CCC" else "LONG")
    ]

    await shadow_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    events = [e for e in await temp_db.workflows.events() if e["kind"] == EventKind.SCAN_CANDIDATES_RANKED]
    [event] = events
    by_contract = {c["contract"]: c["filings"] for c in event["payload"]["candidates"]}
    assert by_contract["AAA"] == {"status": "ok", "would_block": True, "category": "late_filing"}
    assert by_contract["BBB"] == {"status": "uncovered", "would_block": None, "category": None}
    assert by_contract["CCC"] is None  # a short never evaluates the veto


async def test_scan_journals_null_filings_when_the_veto_is_off(shadow_desk, temp_db):  # noqa: F811
    shadow_desk.config.filing_veto = shadow_desk.config.filing_veto.model_copy(update={"entry_block": "off"})
    shadow_desk._filings_lookup = AsyncMock(return_value=_lookup())
    shadow_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=lambda cand, **kw: evaluation(cand))
    shadow_desk.strategy_engine.scan_contract.side_effect = lambda data, **kw: [candidate(data.contract, 0.6)]

    await shadow_desk.run_scan(use_llm=False, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    [event] = [e for e in await temp_db.workflows.events() if e["kind"] == EventKind.SCAN_CANDIDATES_RANKED]
    assert event["payload"]["candidates"]
    assert all(c["filings"] is None for c in event["payload"]["candidates"])
