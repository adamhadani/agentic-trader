"""Every LLM-evaluated card records ``llm_verdict`` -- an approval, a veto or an error -- and only a
native card applies it."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentic_trader.agent.prompts import gate_prompt_sha256
from agentic_trader.execution.durable import RankedOutcome
from tests.agent.test_evaluator import evaluator_factory  # noqa: F401  (fixture)
from tests.agent.test_evaluator_catalog import LLM_VETO, drift_candidate


def completion(monkeypatch, **overrides) -> AsyncMock:
    content = json.dumps({**LLM_VETO, **overrides})
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    mock = AsyncMock(return_value=response)
    monkeypatch.setattr("agentic_trader.agent.evaluator.litellm.acompletion", mock)
    return mock


def native_candidate(**updates):
    return drift_candidate(catalog_event=None, alpha_version=None, alpha_policy=None, probe=False, **updates)


def test_ranked_outcome_vocabulary():
    assert RankedOutcome.SENT == "sent"
    assert RankedOutcome.LLM_VETOED == "llm_vetoed"
    assert RankedOutcome.CARD_POLICY_WITHHELD == "card_policy_withheld"
    assert json.dumps({"outcome": RankedOutcome.LLM_VETOED}) == '{"outcome": "llm_vetoed"}'


async def test_native_veto_is_applied_and_recorded(evaluator_factory, monkeypatch):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.openai_api_key = "isolated-test-placeholder"
    completion(monkeypatch)

    result = await evaluator.evaluate_candidate(native_candidate(), use_llm=True)

    assert result.approved is False
    assert result.rejection_reason == "earnings momentum fading"
    assert result.llm_verdict == {
        "approved": False,
        "rejection_reason": "earnings momentum fading",
        "stop_loss": result.stop_loss,
        "take_profit": result.take_profit,
        "applied": True,
        "model": None,  # the mock response carries no ``model``
        "prompt_sha256": gate_prompt_sha256(evaluator.config),
        "error": None,
        "malformed": False,
    }


async def test_native_approval_records_the_clamped_bracket(evaluator_factory, monkeypatch):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.openai_api_key = "isolated-test-placeholder"
    # stop 49.9 is inside the minimum ATR distance, so the evaluator clamps it to its own stop.
    completion(monkeypatch, approved=True, rejection_reason=None, stop_loss=49.9, take_profit=60.0)

    result = await evaluator.evaluate_candidate(native_candidate(), use_llm=True)

    assert result.approved is True
    assert result.llm_verdict["applied"] is True and result.llm_verdict["approved"] is True
    assert result.llm_verdict["stop_loss"] == result.stop_loss != 49.9
    assert result.llm_verdict["take_profit"] == result.take_profit


async def test_catalog_verdict_is_recorded_but_not_applied(evaluator_factory, monkeypatch):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.openai_api_key = "isolated-test-placeholder"
    completion(monkeypatch)

    result = await evaluator.evaluate_candidate(drift_candidate(), use_llm=True)

    assert result.approved is True
    assert result.llm_verdict["applied"] is False and result.llm_verdict["approved"] is False
    assert result.llm_verdict["rejection_reason"] == "earnings momentum fading"
    # A frozen policy card's recorded bracket is the policy bracket, not the LLM's proposal.
    assert (result.llm_verdict["stop_loss"], result.llm_verdict["take_profit"]) == (
        result.stop_loss,
        result.take_profit,
    )


@pytest.mark.parametrize("approved", ["false", "no", 1, None, "True", "true", True])
async def test_commentary_verdict_keeps_the_explicit_true_rule(evaluator_factory, monkeypatch, approved):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.openai_api_key = "isolated-test-placeholder"
    completion(monkeypatch, approved=approved)

    result = await evaluator.evaluate_candidate(drift_candidate(), use_llm=True)

    # Only the JSON boolean true approves; a string "True" is a malformed veto, recorded as commentary.
    assert result.approved is True
    assert result.llm_verdict["approved"] is (approved is True)
    assert result.llm_verdict["malformed"] is (approved is not True and approved is not False)


async def test_no_verdict_without_the_llm_and_an_error_verdict_when_it_fails(evaluator_factory, monkeypatch):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.openai_api_key = "isolated-test-placeholder"

    disabled = await evaluator.evaluate_candidate(native_candidate(), use_llm=False)
    assert disabled.approved is True and disabled.llm_verdict is None

    failing = AsyncMock(side_effect=RuntimeError("provider down"))
    monkeypatch.setattr("agentic_trader.agent.evaluator.litellm.acompletion", failing)
    unavailable = await evaluator.evaluate_candidate(native_candidate(), use_llm=True)
    assert unavailable.approved is False and unavailable.thesis_summary == "LLM unavailable (RuntimeError)"
    assert unavailable.llm_verdict is not None
    assert unavailable.llm_verdict["error"] == "RuntimeError" and unavailable.llm_verdict["applied"] is True
    assert "provider down" not in unavailable.model_dump_json()


async def test_macro_line_is_the_deterministic_gate_not_the_llm(evaluator_factory, monkeypatch):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.openai_api_key = "isolated-test-placeholder"
    completion(
        monkeypatch, approved=True, rejection_reason=None, stop_loss=49.9, take_profit=60.0, macro_clearance=False
    )

    result = await evaluator.evaluate_candidate(native_candidate(), use_llm=True)

    assert result.llm_verdict is not None  # the LLM path, not the deterministic fallback
    assert result.macro_clearance is True


async def test_an_llm_answer_without_macro_clearance_is_not_a_fallback(evaluator_factory, monkeypatch):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.openai_api_key = "isolated-test-placeholder"
    document = {**LLM_VETO, "approved": True, "rejection_reason": None, "stop_loss": 49.9, "take_profit": 60.0}
    del document["macro_clearance"]
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(document)))])
    monkeypatch.setattr("agentic_trader.agent.evaluator.litellm.acompletion", AsyncMock(return_value=response))

    result = await evaluator.evaluate_candidate(native_candidate(), use_llm=True)

    assert result.llm_verdict is not None and result.macro_clearance is True
    assert not result.thesis_summary.startswith("Automated thesis")
