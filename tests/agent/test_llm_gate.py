"""The native-card LLM gate fails closed (docs/superpowers/specs/2026-10-27-llm-gate-hygiene-design.md §2).

Only the JSON boolean ``true`` approves; a reply that parses as an object but does not approve is a veto
(``malformed`` when it fails validation or ``approved`` is not a JSON boolean); anything else is an
error that never approves a native card. ``llm_verdict`` carries the model snapshot and the prompt hash.
"""

import asyncio
import hashlib
import json
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentic_trader.agent.llm_gate import GateReply, classify_gate_reply
from agentic_trader.agent.prompts import USER_EVALUATION_TEMPLATE, build_system_prompt, gate_prompt_sha256
from agentic_trader.config import LLMGateConfig
from tests.agent.test_evaluator import create_candidate, evaluator_factory  # noqa: F401  (fixture)
from tests.agent.test_evaluator_catalog import drift_candidate


VERDICT_KEYS = {
    "approved",
    "rejection_reason",
    "stop_loss",
    "take_profit",
    "applied",
    "model",
    "prompt_sha256",
    "error",
    "malformed",
}
PLANTED = "PLANTED-SECRET-7f3a91 provider said api_key=sk-live-zzz"
APPROVAL = {"approved": True, "rejection_reason": None, "thesis_summary": "clean pullback", "macro_clearance": True}
VETO = {"approved": False, "rejection_reason": "thesis too thin", "thesis_summary": "weak", "macro_clearance": False}


def reply(document) -> str:
    return json.dumps(document)


# --- the classifier: spec §2.2 table -------------------------------------------------------------


def test_a_valid_approval_approves():
    result = classify_gate_reply(reply(APPROVAL))
    assert result.kind == "approve" and result.error_type is None and result.malformed is False
    assert result.data is not None and result.data["thesis_summary"] == "clean pullback"


def test_a_fenced_approval_still_parses():
    result = classify_gate_reply(f"```json\n{reply(APPROVAL)}\n```")
    assert result.kind == "approve"


def test_a_valid_veto_is_a_well_formed_veto():
    result = classify_gate_reply(reply(VETO))
    assert result.kind == "veto" and result.malformed is False and result.error_type is None
    assert result.data is not None and result.data["rejection_reason"] == "thesis too thin"


def test_a_veto_missing_thesis_summary_is_a_malformed_veto():
    document = {key: value for key, value in VETO.items() if key != "thesis_summary"}
    result = classify_gate_reply(reply(document))
    assert result.kind == "veto" and result.malformed is True and result.error_type is None
    assert result.data is not None and result.data["rejection_reason"] == "thesis too thin"


@pytest.mark.parametrize("approved", ["true", "True", "yes", 1, 1.0, "false", None, [True], {"v": True}])
def test_anything_but_the_json_boolean_true_is_a_malformed_veto(approved):
    result = classify_gate_reply(reply({**APPROVAL, "approved": approved}))
    assert result.kind == "veto" and result.malformed is True and result.error_type is None


def test_a_missing_approved_is_a_malformed_veto_with_the_fixed_reason():
    document = {"thesis_summary": "no verdict given", "rejection_reason": None}
    result = classify_gate_reply(reply(document))
    assert result.kind == "veto" and result.malformed is True
    assert result.data is not None and result.data["rejection_reason"] == "malformed veto"


@pytest.mark.parametrize("reason", [None, 7, ["list"], ""])
def test_a_malformed_veto_without_a_string_reason_says_malformed_veto(reason):
    result = classify_gate_reply(reply({"approved": "no", "rejection_reason": reason}))
    assert result.kind == "veto" and result.data is not None and result.data["rejection_reason"] == "malformed veto"


def test_a_veto_with_a_non_numeric_stop_is_malformed_not_an_error():
    result = classify_gate_reply(reply({**VETO, "stop_loss": "abc"}))
    assert result.kind == "veto" and result.malformed is True
    assert result.data is not None and "stop_loss" not in result.data


@pytest.mark.parametrize(
    "document",
    [
        {key: value for key, value in APPROVAL.items() if key != "thesis_summary"},
        {**APPROVAL, "thesis_summary": 42},
        {**APPROVAL, "stop_loss": "abc"},
        {**APPROVAL, "stop_loss": None},
        {**APPROVAL, "take_profit": {"price": 1}},
        {**APPROVAL, "rejection_reason": 3},
    ],
)
def test_a_json_true_approval_that_fails_validation_is_an_error(document):
    """Review focus 1: never an approval, never a veto."""
    result = classify_gate_reply(reply(document))
    assert result == GateReply(kind="error", data=None, error_type="ValidationError", malformed=False)


@pytest.mark.parametrize(
    ("content", "error_type"),
    [
        ("not json at all", "JSONDecodeError"),
        ("", "JSONDecodeError"),
        (None, "JSONDecodeError"),
        ('{"approved": true', "JSONDecodeError"),
        (json.dumps([APPROVAL]), "TypeError"),
        (json.dumps("approved"), "TypeError"),
        (json.dumps(True), "TypeError"),
    ],
)
def test_a_non_json_or_non_object_reply_is_an_error(content, error_type):
    assert classify_gate_reply(content) == GateReply(kind="error", data=None, error_type=error_type, malformed=False)


@pytest.mark.parametrize("error", [RuntimeError(PLANTED), TimeoutError(PLANTED), ValueError(PLANTED)])
def test_an_exception_is_an_error_named_by_type_only(error):
    result = classify_gate_reply(reply(APPROVAL), error=error)
    assert result == GateReply(kind="error", data=None, error_type=type(error).__name__, malformed=False)
    assert PLANTED not in repr(result)


# --- the evaluator ----------------------------------------------------------------------------------


def completion(monkeypatch, document=None, *, content=None, model="gpt-5.6-sol") -> AsyncMock:
    text = content if content is not None else json.dumps(APPROVAL if document is None else document)
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))], model=model)
    mock = AsyncMock(return_value=response)
    monkeypatch.setattr("agentic_trader.agent.evaluator.litellm.acompletion", mock)
    return mock


def failing(monkeypatch, error: BaseException) -> AsyncMock:
    mock = AsyncMock(side_effect=error)
    monkeypatch.setattr("agentic_trader.agent.evaluator.litellm.acompletion", mock)
    return mock


def keyed(evaluator):
    evaluator.config.openai_api_key = "isolated-test-placeholder"
    return evaluator


def native_candidate(**updates):
    return drift_candidate(catalog_event=None, alpha_version=None, alpha_policy=None, probe=False, **updates)


async def test_the_gate_request_sends_a_deadline_and_no_temperature(evaluator_factory, monkeypatch):  # noqa: F811
    evaluator = keyed(evaluator_factory())
    mock = completion(monkeypatch)

    await evaluator.evaluate_candidate(create_candidate(), use_llm=True)

    kwargs = mock.await_args.kwargs
    assert set(kwargs) == {"api_key", "model", "messages", "response_format", "timeout", "drop_params"}
    assert kwargs["timeout"] == evaluator.config.llm_gate.timeout_seconds == 60.0
    assert kwargs["drop_params"] is False
    assert kwargs["response_format"] == {"type": "json_object"}
    assert "temperature" not in kwargs


async def test_a_slow_provider_is_an_error_at_the_deadline(evaluator_factory, config, monkeypatch):  # noqa: F811
    evaluator = keyed(
        evaluator_factory(config=config.model_copy(update={"llm_gate": LLMGateConfig(timeout_seconds=0.05)}))
    )

    async def slow(**kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr("agentic_trader.agent.evaluator.litellm.acompletion", slow)
    started = time.monotonic()
    result = await evaluator.evaluate_candidate(create_candidate(), use_llm=True)

    assert time.monotonic() - started < 2.0
    assert result.approved is False
    assert result.llm_verdict is not None and result.llm_verdict["error"] == "TimeoutError"
    assert result.thesis_summary == "LLM unavailable (TimeoutError)"


async def test_a_missing_key_on_an_llm_scan_is_an_error_without_a_call(evaluator_factory, monkeypatch):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.openai_api_key = evaluator.config.anthropic_api_key = evaluator.config.gemini_api_key = None
    mock = completion(monkeypatch)

    result = await evaluator.evaluate_candidate(create_candidate(), use_llm=True)

    mock.assert_not_called()
    assert result.approved is False and result.rejection_reason == "LLM unavailable (LLMKeyMissing)"
    assert result.llm_verdict is not None and result.llm_verdict["error"] == "LLMKeyMissing"
    assert set(result.llm_verdict) == VERDICT_KEYS


async def test_deterministic_mode_is_unchanged_without_a_key(evaluator_factory, monkeypatch):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.openai_api_key = evaluator.config.anthropic_api_key = evaluator.config.gemini_api_key = None
    mock = completion(monkeypatch)

    result = await evaluator.evaluate_candidate(create_candidate(), use_llm=False)

    mock.assert_not_called()
    assert result.approved is True and result.llm_verdict is None
    assert result.thesis_summary.startswith("Quantitative trigger verified:")


async def test_a_provider_error_withholds_a_native_card_with_type_names_only(
    evaluator_factory,  # noqa: F811
    monkeypatch,
    caplog,
):
    evaluator = keyed(evaluator_factory())
    deterministic = await evaluator.evaluate_candidate(create_candidate(), use_llm=False)
    failing(monkeypatch, RuntimeError(PLANTED))

    with caplog.at_level(logging.WARNING, logger="agentic_trader.agent.evaluator"):
        result = await evaluator.evaluate_candidate(create_candidate(), use_llm=True)

    assert result.approved is False and result.rejection_rule is None
    assert result.thesis_summary == "LLM unavailable (RuntimeError)"
    assert result.rejection_reason == "LLM unavailable (RuntimeError)"
    assert result.macro_clearance is deterministic.macro_clearance
    assert (result.stop_loss, result.take_profit, result.quantity) == (
        deterministic.stop_loss,
        deterministic.take_profit,
        deterministic.quantity,
    )
    assert result.llm_verdict == {
        "approved": False,
        "rejection_reason": None,
        "stop_loss": None,
        "take_profit": None,
        "applied": True,
        "model": None,
        "prompt_sha256": gate_prompt_sha256(evaluator.config),
        "error": "RuntimeError",
        "malformed": False,
    }
    assert "PLANTED" not in result.model_dump_json()
    [record] = [r for r in caplog.records if r.getMessage() == "LLM evaluation failed (RuntimeError)"]
    assert record.exc_info is not None  # the traceback stays in the local log
    extras = {key: value for key, value in vars(record).items() if key not in ("exc_info", "exc_text", "msg", "args")}
    assert "PLANTED" not in repr(extras)


async def test_a_catalog_card_on_error_stays_commentary_with_the_unavailable_label(
    evaluator_factory,  # noqa: F811
    monkeypatch,
):
    evaluator = keyed(evaluator_factory())
    failing(monkeypatch, RuntimeError(PLANTED))

    result = await evaluator.evaluate_candidate(drift_candidate(), use_llm=True)

    assert result.approved is True and result.rejection_reason is None
    assert result.thesis_summary == "LLM commentary unavailable (RuntimeError)"
    assert result.macro_clearance is True  # no false "Event Alert Active"
    assert result.llm_verdict is not None
    assert result.llm_verdict["applied"] is False and result.llm_verdict["approved"] is False
    assert result.llm_verdict["error"] == "RuntimeError" and result.llm_verdict["malformed"] is False
    assert "PLANTED" not in result.model_dump_json()


@pytest.mark.parametrize(("candidate", "approved"), [(native_candidate, False), (drift_candidate, True)])
async def test_skip_llm_reason_is_the_error_result_without_a_call(
    evaluator_factory,  # noqa: F811
    monkeypatch,
    candidate,
    approved,
):
    evaluator = keyed(evaluator_factory())
    mock = completion(monkeypatch)

    result = await evaluator.evaluate_candidate(
        candidate(), use_llm=True, skip_llm_reason="LLMUnavailableEarlierInScan"
    )

    mock.assert_not_called()
    assert result.approved is approved
    assert result.llm_verdict is not None and result.llm_verdict["error"] == "LLMUnavailableEarlierInScan"
    assert "unavailable (LLMUnavailableEarlierInScan)" in result.thesis_summary


async def test_skip_llm_reason_never_overrides_a_deterministic_refusal(evaluator_factory, monkeypatch):  # noqa: F811
    evaluator = keyed(evaluator_factory())
    completion(monkeypatch)

    result = await evaluator.evaluate_candidate(
        create_candidate(),
        current_open_notional=evaluator.config.portfolio.max_notional_exposure,
        use_llm=True,
        skip_llm_reason="LLMUnavailableEarlierInScan",
    )

    assert result.approved is False and result.llm_verdict is None
    assert result.quantity == 0


async def test_a_json_true_approval_failing_validation_is_withheld(evaluator_factory, monkeypatch):  # noqa: F811
    """Review focus 1 at the evaluator: neither approved nor a veto."""
    evaluator = keyed(evaluator_factory())
    completion(monkeypatch, {"approved": True, "macro_clearance": True})

    result = await evaluator.evaluate_candidate(create_candidate(), use_llm=True)

    assert result.approved is False
    assert result.llm_verdict is not None and result.llm_verdict["error"] == "ValidationError"
    assert result.thesis_summary == "LLM unavailable (ValidationError)"


@pytest.mark.parametrize(
    ("document", "reason"),
    [
        ({"approved": False, "rejection_reason": "late in the move", "macro_clearance": True}, "late in the move"),
        ({**APPROVAL, "approved": "yes"}, "malformed veto"),
        ({**APPROVAL, "approved": "true"}, "malformed veto"),
        ({"approved": 0, "rejection_reason": None}, "malformed veto"),
    ],
)
async def test_a_malformed_veto_fails_closed(evaluator_factory, monkeypatch, document, reason):  # noqa: F811
    evaluator = keyed(evaluator_factory())
    deterministic = await evaluator.evaluate_candidate(create_candidate(), use_llm=False)
    completion(monkeypatch, document)

    result = await evaluator.evaluate_candidate(create_candidate(), use_llm=True)

    assert result.approved is False and result.rejection_reason == reason
    assert (result.stop_loss, result.take_profit) == (deterministic.stop_loss, deterministic.take_profit)
    assert result.llm_verdict is not None
    assert result.llm_verdict["malformed"] is True and result.llm_verdict["error"] is None
    assert result.llm_verdict["approved"] is False and result.llm_verdict["rejection_reason"] == reason
    assert result.llm_verdict["model"] == "gpt-5.6-sol"


async def test_a_failure_while_applying_a_reply_is_an_error_not_an_approval(evaluator_factory, monkeypatch):  # noqa: F811
    evaluator = keyed(evaluator_factory())
    deterministic = await evaluator.evaluate_candidate(create_candidate(), use_llm=False)
    completion(monkeypatch, {**APPROVAL, "stop_loss": deterministic.stop_loss - 5.0})

    def broken(*args, **kwargs):
        raise KeyError(PLANTED)

    monkeypatch.setattr(evaluator, "_final_bracket", broken)

    result = await evaluator.evaluate_candidate(create_candidate(), use_llm=True)

    assert result.approved is False and result.thesis_summary == "LLM unavailable (KeyError)"
    assert result.llm_verdict is not None and result.llm_verdict["error"] == "KeyError"
    assert (result.stop_loss, result.take_profit) == (deterministic.stop_loss, deterministic.take_profit)
    assert "PLANTED" not in result.model_dump_json()


async def test_a_veto_keeps_the_deterministic_macro_clearance(evaluator_factory, monkeypatch):  # noqa: F811
    evaluator = keyed(evaluator_factory())
    deterministic = await evaluator.evaluate_candidate(create_candidate(), use_llm=False)
    completion(monkeypatch, VETO)  # the reply claims macro_clearance false

    result = await evaluator.evaluate_candidate(create_candidate(), use_llm=True)

    assert result.approved is False and result.rejection_reason == "thesis too thin"
    assert result.macro_clearance is deterministic.macro_clearance is True
    assert result.llm_verdict is not None and result.llm_verdict["malformed"] is False


async def test_an_approval_records_identity_and_all_nine_keys(evaluator_factory, monkeypatch):  # noqa: F811
    evaluator = keyed(evaluator_factory())
    completion(monkeypatch)

    first = await evaluator.evaluate_candidate(create_candidate(), use_llm=True)
    second = await evaluator.evaluate_candidate(create_candidate(price=5810.0), use_llm=True)

    assert first.approved is True and first.llm_verdict is not None and second.llm_verdict is not None
    assert set(first.llm_verdict) == VERDICT_KEYS
    assert first.llm_verdict["model"] == "gpt-5.6-sol"
    assert first.llm_verdict["error"] is None and first.llm_verdict["malformed"] is False
    assert first.llm_verdict["prompt_sha256"] == second.llm_verdict["prompt_sha256"]


async def test_a_response_without_a_model_records_none(evaluator_factory, monkeypatch):  # noqa: F811
    evaluator = keyed(evaluator_factory())
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(APPROVAL)))])
    monkeypatch.setattr("agentic_trader.agent.evaluator.litellm.acompletion", AsyncMock(return_value=response))

    result = await evaluator.evaluate_candidate(create_candidate(), use_llm=True)

    assert result.approved is True and result.llm_verdict is not None and result.llm_verdict["model"] is None


def test_the_prompt_hash_is_the_system_prompt_and_the_template(config):
    expected = hashlib.sha256((build_system_prompt(config) + USER_EVALUATION_TEMPLATE).encode()).hexdigest()
    assert gate_prompt_sha256(config) == expected
    assert len(expected) == 64


async def test_the_prompt_hash_changes_with_a_system_prompt_input(evaluator_factory, config, monkeypatch):  # noqa: F811
    completion(monkeypatch)
    richer = config.model_copy(update={"portfolio": config.portfolio.model_copy(update={"cash": 250_000.0})})
    assert build_system_prompt(richer) != build_system_prompt(config)

    base = await keyed(evaluator_factory()).evaluate_candidate(create_candidate(), use_llm=True)
    other = await keyed(evaluator_factory(config=richer)).evaluate_candidate(create_candidate(), use_llm=True)

    assert base.llm_verdict is not None and other.llm_verdict is not None
    assert base.llm_verdict["prompt_sha256"] == gate_prompt_sha256(config)
    assert other.llm_verdict["prompt_sha256"] == gate_prompt_sha256(richer) != base.llm_verdict["prompt_sha256"]
