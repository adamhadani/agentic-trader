"""An LLM error never sends a native card (docs/superpowers/specs/2026-10-27-llm-gate-hygiene-design.md §2.2).

A native candidate whose gate errored is journaled ``llm_unavailable`` with its error block and is never
recorded or notified. The first error in a scan opens that scan's breaker: every later candidate that would
call the gate gets the error result without a call (``LLMUnavailableEarlierInScan``), so an outage costs one
``llm_gate.timeout_seconds``; the skipped evaluation spends LLM budget like a call, and the next scan starts
closed. A catalog card stays commentary and is sent with the unavailable label. A malformed veto is a veto.
The session digest counts vetoes and unavailable candidates and never lists the latter under "top:".
"""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.agent.llm_gate import LLM_UNAVAILABLE_EARLIER_IN_SCAN, MALFORMED_VETO_REASON
from agentic_trader.config import LLMGateConfig, ScanBudget
from agentic_trader.execution.durable import EventKind, RankedOutcome, ScanTrigger, WorkKind
from tests.agent.test_risk_policy_integration import desk, offer, pending_cards, setup  # noqa: F401  (desk: fixture)
from tests.agent.test_scan_budget import budget_desk  # noqa: F401  (fixture)
from tests.agent.test_scan_drift import drift_desk, suggestion_scan  # noqa: F401  (drift_desk: fixture)
from tests.agent.test_scan_llm_verdict import error_verdict, verdict, with_verdict


class Gate:
    """The evaluator's LLM pass as the scan sees it; the deterministic COLLECT pass approves every candidate.

    ``replies`` maps a contract to ``"veto"``, ``"malformed"`` or an error type name; any other contract is
    approved. A ``skip_llm_reason`` is the error result named by it without a call, as ``RiskEvaluator``
    returns it: a native card withheld, a catalog card approved as labelled commentary.
    """

    def __init__(self, **replies: str) -> None:
        self.replies = replies
        self.calls: list[str] = []
        self.skipped: list[str] = []

    async def evaluate(self, cand, use_llm=False, skip_llm_reason=None, **kwargs):
        if not use_llm:
            return with_verdict(cand, use_llm)
        applied = cand.catalog_event is None
        if skip_llm_reason is not None:
            self.skipped.append(cand.contract)
            return unavailable(cand, skip_llm_reason, applied=applied)
        self.calls.append(cand.contract)
        reply = self.replies.get(cand.contract)
        if reply is None:
            return with_verdict(cand, use_llm, llm_verdict=verdict(True, applied=applied))
        if reply in ("veto", "malformed"):
            reason = "thesis weak" if reply == "veto" else MALFORMED_VETO_REASON
            return with_verdict(
                cand,
                use_llm,
                approved=not applied,
                rejection_reason=reason if applied else None,
                llm_verdict=verdict(False, reason=reason, applied=applied, malformed=reply == "malformed"),
            )
        return unavailable(cand, reply, applied=applied)


def unavailable(cand, error: str, *, applied: bool):
    label = f"LLM unavailable ({error})" if applied else f"LLM commentary unavailable ({error})"
    return with_verdict(
        cand,
        True,
        approved=not applied,
        rejection_reason=label if applied else None,
        thesis_summary=label,
        llm_verdict=error_verdict(error, applied=applied),
    )


def install(copilot, gate: Gate) -> AsyncMock:
    copilot.evaluator.evaluate_candidate = AsyncMock(side_effect=gate.evaluate)
    return copilot.evaluator.evaluate_candidate


async def journaled(db) -> list[dict[str, dict]]:
    """Each ``scan_candidates_ranked`` event's candidates by contract, oldest scan first."""
    events = [e for e in await db.workflows.events() if e["kind"] == EventKind.SCAN_CANDIDATES_RANKED]
    return [{c["contract"]: c for c in e["payload"]["candidates"]} for e in events]


def outcomes(candidates: dict[str, dict]) -> dict[str, str]:
    return {contract: c["outcome"] for contract, c in candidates.items()}


# --- a native error is withheld ------------------------------------------------------------------


async def test_a_native_llm_error_withholds_the_card_and_journals_llm_unavailable(budget_desk, temp_db):  # noqa: F811
    install(budget_desk, Gate(DDD="Timeout"))

    summary = await suggestion_scan(budget_desk)

    [scan] = await journaled(temp_db)
    ddd = scan["DDD"]
    assert ddd["outcome"] == RankedOutcome.LLM_UNAVAILABLE == "llm_unavailable"
    assert ddd["llm"] == error_verdict("Timeout")
    assert ddd["llm"]["applied"] is True and ddd["llm"]["approved"] is False and ddd["llm"]["model"] is None
    assert ddd["signal_id"] is None
    # Never recorded, never notified, so never auto-entered.
    assert await temp_db.get_recent_signals(limit=10) == []
    assert await temp_db.workflows.list_work(WorkKind.NOTIFICATION) == []
    assert summary["sent"] == 0
    reasons = {r["contract"]: r["reason"] for r in summary["runners_up"]}
    assert reasons["DDD"] == "LLM unavailable (Timeout)"
    # The operator's single-name reply names the outage, through the method the bot uses.
    assert "LLM unavailable (Timeout)" in TradingCopilot._scan_result_text("DDD", summary)


async def test_the_breaker_stops_later_gate_calls_and_resets_at_the_next_scan(budget_desk, temp_db):  # noqa: F811
    gate = Gate(DDD="APIConnectionError")
    evaluate = install(budget_desk, gate)

    summary = await suggestion_scan(budget_desk)

    # One call; the next three candidates are classified without one, each spending the LLM budget (4) as a
    # call would, so EEE meets the budget exactly as it would have with a working gate.
    assert gate.calls == ["DDD"] and gate.skipped == ["CCC", "BBB", "AAA"]
    [scan] = await journaled(temp_db)
    assert outcomes(scan) == {
        "DDD": RankedOutcome.LLM_UNAVAILABLE,
        "CCC": RankedOutcome.LLM_UNAVAILABLE,
        "BBB": RankedOutcome.LLM_UNAVAILABLE,
        "AAA": RankedOutcome.LLM_UNAVAILABLE,
        "EEE": "LLM evaluation budget spent",
    }
    assert scan["DDD"]["llm"]["error"] == "APIConnectionError"
    for contract in ("CCC", "BBB", "AAA"):
        assert scan[contract]["llm"] == error_verdict(LLM_UNAVAILABLE_EARLIER_IN_SCAN)
    assert LLM_UNAVAILABLE_EARLIER_IN_SCAN == "LLMUnavailableEarlierInScan"
    llm_passes = [call.kwargs for call in evaluate.await_args_list if call.kwargs.get("use_llm")]
    assert [kwargs["skip_llm_reason"] for kwargs in llm_passes] == [None, *[LLM_UNAVAILABLE_EARLIER_IN_SCAN] * 3]
    assert (summary["llm_unavailable"], summary["llm_vetoed"], summary["sent"]) == (4, 0, 0)
    assert await temp_db.get_recent_signals(limit=10) == []

    # The provider is back: the next scan starts with the breaker closed and calls the gate again.
    gate.replies = {}
    summary = await suggestion_scan(budget_desk)

    assert gate.calls == ["DDD", "DDD"] and gate.skipped == ["CCC", "BBB", "AAA"]
    assert summary["sent"] == 1 and summary["llm_unavailable"] == 0
    [signal] = await temp_db.get_recent_signals(limit=10)
    assert signal["contract"] == "DDD"


async def test_a_slow_provider_costs_one_deadline_per_scan(desk, temp_db, app_config, monkeypatch):  # noqa: F811
    """Review focus 2, through the real evaluator: one call waits one deadline; the breaker skips the rest."""
    timeout = 0.5
    app_config.openai_api_key = "isolated-test-placeholder"
    app_config.llm_gate = LLMGateConfig(timeout_seconds=timeout)
    desk.news_context = None
    offer(desk, setup("SPY", quality=0.9), setup("QQQ", quality=0.8))
    calls: list[str] = []

    async def slow(**kwargs):
        calls.append(kwargs["model"])
        await asyncio.sleep(30)

    monkeypatch.setattr("agentic_trader.agent.evaluator.litellm.acompletion", slow)

    started = time.monotonic()
    await desk.run_scan(use_llm=True, budget=ScanBudget.FULL)
    elapsed = time.monotonic() - started

    assert len(calls) == 1
    assert timeout <= elapsed < 2 * timeout
    [scan] = await journaled(temp_db)
    assert outcomes(scan) == {"SPY": RankedOutcome.LLM_UNAVAILABLE, "QQQ": RankedOutcome.LLM_UNAVAILABLE}
    # The deadline's type name depends on which timer fires first; only its presence is the contract.
    assert scan["SPY"]["llm"]["error"] is not None
    assert scan["QQQ"]["llm"]["error"] == LLM_UNAVAILABLE_EARLIER_IN_SCAN
    assert await pending_cards(temp_db) == []

    # The next scan calls the gate again; a valid approval cards the top setup.
    reply = {"approved": True, "rejection_reason": None, "thesis_summary": "clean pullback"}
    message = SimpleNamespace(content=json.dumps(reply))
    approve = AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=message)], model="gpt-5.6-sol"))
    monkeypatch.setattr("agentic_trader.agent.evaluator.litellm.acompletion", approve)

    await desk.run_scan(use_llm=True, budget=ScanBudget.FULL)

    approve.assert_awaited_once()
    [card] = await pending_cards(temp_db)
    assert card["contract"] == "SPY"
    assert card["decision_provenance"]["llm_verdict"]["error"] is None
    assert card["decision_provenance"]["llm_verdict"]["model"] == "gpt-5.6-sol"


# --- a catalog card stays commentary -------------------------------------------------------------


async def _pead_outcomes(db) -> dict[str, str]:
    [event] = [e for e in await db.workflows.events() if e["kind"] == EventKind.PEAD_DECISION]
    return {item["symbol"]: item["outcome"] for item in event["payload"]["events"]}


async def test_a_catalog_card_after_the_breaker_opened_is_sent_with_the_label(drift_desk, temp_db):  # noqa: F811
    gate = Gate(DDD="Timeout")
    install(drift_desk, gate)

    summary = await suggestion_scan(drift_desk)

    assert gate.calls == ["DDD"] and "WINR" in gate.skipped
    [card] = await temp_db.get_recent_signals(limit=10)
    assert card["contract"] == "WINR"
    assert card["decision_provenance"]["llm_verdict"] == error_verdict(LLM_UNAVAILABLE_EARLIER_IN_SCAN, applied=False)
    assert (await _pead_outcomes(temp_db))["WINR"] == "sent"
    assert summary["sent"] == 1 and summary["drift"]["sent"] == 1


async def test_a_catalog_cards_own_error_sends_it_and_is_not_llm_unavailable(drift_desk, temp_db):  # noqa: F811
    install(drift_desk, Gate(WINR="APIConnectionError"))

    summary = await suggestion_scan(drift_desk)

    signals = {s["contract"]: s for s in await temp_db.get_recent_signals(limit=10)}
    assert sorted(signals) == ["DDD", "WINR"]
    assert signals["WINR"]["decision_provenance"]["llm_verdict"] == error_verdict("APIConnectionError", applied=False)
    assert signals["DDD"]["decision_provenance"]["llm_verdict"]["error"] is None
    assert (await _pead_outcomes(temp_db))["WINR"] == "sent"
    [scan] = await journaled(temp_db)  # catalog candidates are never journaled as ranked candidates
    assert "WINR" not in scan and scan["DDD"]["outcome"] == RankedOutcome.SENT
    assert summary["llm_unavailable"] == 0


# --- a malformed veto is a veto, and never opens the breaker -------------------------------------


async def test_a_malformed_veto_is_journaled_as_a_veto_and_the_next_candidate_is_called(budget_desk, temp_db):  # noqa: F811
    gate = Gate(DDD="malformed")
    install(budget_desk, gate)

    summary = await suggestion_scan(budget_desk)

    assert gate.calls == ["DDD", "CCC"] and gate.skipped == []
    [scan] = await journaled(temp_db)
    assert scan["DDD"]["outcome"] == RankedOutcome.LLM_VETOED
    assert scan["DDD"]["llm"] == verdict(False, reason=MALFORMED_VETO_REASON, malformed=True)
    assert scan["CCC"]["outcome"] == RankedOutcome.SENT
    assert (summary["llm_vetoed"], summary["llm_unavailable"]) == (1, 0)
    assert {r["contract"]: r["reason"] for r in summary["runners_up"]}["DDD"] == "LLM vetoed: malformed veto"


# --- the digest ------------------------------------------------------------------------------------


async def test_the_digest_counts_vetoes_and_unavailable_and_keeps_unavailable_out_of_top(budget_desk):  # noqa: F811
    budget_desk.outbox = AsyncMock()
    install(budget_desk, Gate(DDD="veto", CCC="Timeout"))

    # DDD vetoed; CCC errors and opens the breaker, so BBB and AAA are withheld; EEE meets the LLM budget.
    await suggestion_scan(budget_desk)
    await suggestion_scan(budget_desk)
    # An operator scan never counts in the digest.
    await suggestion_scan(budget_desk, trigger=ScanTrigger.OPERATOR_SCAN, shadow_evidence=False)

    text = await budget_desk.publish_scan_digest()

    assert "2 scan(s)" in text
    assert "10 runners-up — top: DDD LONG (0.90), EEE LONG (0.50), DDD LONG (0.90), EEE LONG (0.50). " in text
    assert "LLM vetoes: 2; LLM unavailable: 6." in text
    for withheld in ("CCC", "BBB", "AAA"):
        assert withheld not in text


async def test_the_digest_reports_zero_llm_counts_for_a_deterministic_scan(budget_desk):  # noqa: F811
    budget_desk.outbox = AsyncMock()
    install(budget_desk, Gate())

    await suggestion_scan(budget_desk, use_llm=False)

    assert "LLM vetoes: 0; LLM unavailable: 0." in await budget_desk.publish_scan_digest()
