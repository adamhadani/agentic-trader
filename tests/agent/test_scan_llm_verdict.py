"""The scan journals an LLM veto as a fixed outcome, with the verdict and the card id."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from agentic_trader.agent.copilot import TradingCopilot
from agentic_trader.config import ScanBudget
from agentic_trader.execution.durable import EventKind, RankedOutcome
from tests.agent.test_scan_budget import budget_desk, evaluation  # noqa: F401  (fixture + helper)
from tests.agent.test_scan_shadow_ranker import shadow_desk  # noqa: F401  (fixture)


def verdict(approved: bool, *, stop=98.0, target=104.0, applied=True, reason=None) -> dict:
    return {
        "approved": approved,
        "rejection_reason": reason,
        "stop_loss": stop,
        "take_profit": target,
        "applied": applied,
    }


def with_verdict(cand, use_llm, **fields):
    base = vars(evaluation(cand))
    return SimpleNamespace(**{**base, **fields, "model_dump": lambda mode=None: {}})


async def _ranked(db):
    return [e for e in await db.workflows.events() if e["kind"] == EventKind.SCAN_CANDIDATES_RANKED]


async def test_llm_veto_is_a_fixed_outcome_with_its_verdict(shadow_desk, temp_db):  # noqa: F811
    async def evaluate(cand, use_llm=False, **kwargs):
        if use_llm and cand.contract == "DDD":
            return with_verdict(
                cand,
                use_llm,
                approved=False,
                rejection_reason="thesis weak",
                llm_verdict=verdict(False, reason="thesis weak"),
            )
        return with_verdict(cand, use_llm, llm_verdict=verdict(True) if use_llm else None)

    shadow_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=evaluate)
    await shadow_desk.run_scan(use_llm=True, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    [event] = await _ranked(temp_db)
    by_contract = {c["contract"]: c for c in event["payload"]["candidates"]}
    assert by_contract["DDD"]["outcome"] == RankedOutcome.LLM_VETOED == "llm_vetoed"
    assert by_contract["DDD"]["llm"] == verdict(False, reason="thesis weak")
    assert by_contract["DDD"]["signal_id"] is None
    assert by_contract["CCC"]["outcome"] == "sent"
    assert by_contract["CCC"]["llm"] == verdict(True)
    [signal] = await temp_db.get_recent_signals(limit=10)
    assert by_contract["CCC"]["signal_id"] == signal["id"]
    assert signal["decision_provenance"]["llm_verdict"] == verdict(True)
    # Candidates the LLM never saw carry no verdict.
    assert by_contract["BBB"]["llm"] is None and by_contract["BBB"]["signal_id"] is None
    reasons = {r["contract"]: r["reason"] for r in shadow_desk.last_scan_summary["runners_up"]}
    assert reasons["DDD"] == "LLM vetoed: thesis weak"
    # The operator's single-name reply names the veto, through the method the bot uses.
    assert "LLM vetoed: thesis weak" in TradingCopilot._scan_result_text("DDD", shadow_desk.last_scan_summary)


async def test_deterministic_rejection_and_old_shape_evaluations_are_unchanged(shadow_desk, temp_db):  # noqa: F811
    async def evaluate(cand, use_llm=False, **kwargs):
        approved = not (use_llm and cand.contract == "DDD")
        return SimpleNamespace(
            **{**vars(evaluation(cand)), "approved": approved, "rejection_reason": None if approved else "exposure cap"}
        )

    shadow_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=evaluate)
    await shadow_desk.run_scan(use_llm=True, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    [event] = await _ranked(temp_db)
    outcomes = {c["contract"]: c["outcome"] for c in event["payload"]["candidates"]}
    assert outcomes["DDD"] == "rejected: exposure cap" and outcomes["CCC"] == "sent"
    reasons = {r["contract"]: r["reason"] for r in shadow_desk.last_scan_summary["runners_up"]}
    assert reasons["DDD"] == "rejected: exposure cap"
    [signal] = await temp_db.get_recent_signals(limit=10)
    assert signal["decision_provenance"]["llm_verdict"] is None


async def test_a_commentary_verdict_never_counts_as_a_veto(shadow_desk, temp_db):  # noqa: F811
    async def evaluate(cand, use_llm=False, **kwargs):
        if use_llm and cand.contract == "DDD":
            # applied=False with approved=False is a catalog-style commentary verdict; the
            # evaluation itself was still rejected by a deterministic gate here.
            return with_verdict(
                cand,
                use_llm,
                approved=False,
                rejection_reason="Sizing blocked: tier",
                llm_verdict=verdict(False, applied=False, reason="meh"),
            )
        return with_verdict(cand, use_llm)

    shadow_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=evaluate)
    await shadow_desk.run_scan(use_llm=True, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    [event] = await _ranked(temp_db)
    outcomes = {c["contract"]: c["outcome"] for c in event["payload"]["candidates"]}
    assert outcomes["DDD"] == "rejected: Sizing blocked: tier"


async def test_non_finite_verdict_prices_are_journaled_as_null(shadow_desk, temp_db):  # noqa: F811
    async def evaluate(cand, use_llm=False, **kwargs):
        return with_verdict(
            cand, use_llm, llm_verdict=verdict(True, stop=float("nan"), target=float("inf")) if use_llm else None
        )

    shadow_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=evaluate)
    await shadow_desk.run_scan(use_llm=True, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    [signal] = await temp_db.get_recent_signals(limit=10)
    assert signal["decision_provenance"]["llm_verdict"]["stop_loss"] is None
    assert signal["decision_provenance"]["llm_verdict"]["take_profit"] is None
    [event] = await _ranked(temp_db)
    sent = next(c for c in event["payload"]["candidates"] if c["outcome"] == "sent")
    assert sent["llm"]["stop_loss"] is None and sent["llm"]["take_profit"] is None


async def test_malformed_verdict_fields_are_coerced_not_raised(shadow_desk, temp_db):  # noqa: F811
    async def evaluate(cand, use_llm=False, **kwargs):
        return with_verdict(cand, use_llm, llm_verdict=verdict(True, stop="abc", reason=["odd"]) if use_llm else None)

    shadow_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=evaluate)
    await shadow_desk.run_scan(use_llm=True, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    [event] = await _ranked(temp_db)
    sent = next(c for c in event["payload"]["candidates"] if c["outcome"] == "sent")
    assert sent["llm"] == {
        "approved": True,
        "rejection_reason": "['odd']",
        "stop_loss": None,
        "take_profit": 104.0,
        "applied": True,
    }
    [signal] = await temp_db.get_recent_signals(limit=10)
    assert signal["decision_provenance"]["llm_verdict"] == sent["llm"]
