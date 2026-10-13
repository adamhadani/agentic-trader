"""`news_context` rides on the evaluation on every path and never reaches the LLM gate's prompt."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from agentic_trader.agent import evaluator as evaluator_module
from agentic_trader.agent.evaluator import LLMTradeEvaluation
from tests.agent.test_evaluator import create_equity_candidate, evaluator_factory  # noqa: F401  (fixture)


BLOCK = {
    "status": "ok",
    "as_of": "2026-10-13T14:35:00+00:00",
    "window_hours": 48,
    "n_items": 2,
    "n_market_wide": 0,
    "truncated": False,
    "headlines": [
        {"id": 1, "created_at": "2026-10-13T13:00:00+00:00", "source": "benzinga", "headline": "SECRET-HEADLINE"}
    ],
    "score": {"tone": "negative", "relevance": "company", "catalyst": "earnings", "one_line": "Missed."},
    "model": "m",
    "prompt_sha256": "a" * 64,
    "error_type": None,
    "page_sha256": "p",
    "applied": False,
}


async def test_deterministic_path_attaches_the_block(config, evaluator_factory):  # noqa: F811
    eval_res = await evaluator_factory().evaluate_candidate(
        create_equity_candidate(), use_llm=False, news_context=BLOCK
    )
    assert eval_res.news_context == BLOCK and eval_res.approved is True


async def test_default_is_none_and_the_field_survives_a_json_round_trip(config, evaluator_factory):  # noqa: F811
    eval_res = await evaluator_factory().evaluate_candidate(create_equity_candidate(), use_llm=False)
    assert eval_res.news_context is None
    with_block = eval_res.model_copy(update={"news_context": BLOCK})
    assert LLMTradeEvaluation.model_validate_json(with_block.model_dump_json()).news_context == BLOCK


def llm_reply(**extra):
    payload = {
        "approved": True,
        "rejection_reason": None,
        "stop_loss": 146.0,
        "take_profit": 158.0,
        "thesis_summary": "ok",
        "macro_clearance": True,
        **extra,
    }
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])


async def test_llm_path_attaches_block_pops_reply_key_and_keeps_news_out_of_the_prompt(
    config,
    evaluator_factory,  # noqa: F811
    monkeypatch,
):
    calls = []

    async def fake_completion(**kwargs):
        calls.append(kwargs)
        return llm_reply(news_context={"status": "ok", "score": "forged"})

    monkeypatch.setattr(evaluator_module.litellm, "acompletion", fake_completion)
    evaluator = evaluator_factory(config=config.model_copy(update={"openai_api_key": "k"}))
    eval_res = await evaluator.evaluate_candidate(create_equity_candidate(), use_llm=True, news_context=BLOCK)
    assert eval_res.news_context == BLOCK  # ours, never the reply's
    assert eval_res.llm_verdict is not None
    prompt_text = json.dumps(calls[0]["messages"])
    assert "SECRET-HEADLINE" not in prompt_text and "news" not in prompt_text.lower()


async def test_fallback_path_attaches_the_block(config, evaluator_factory, monkeypatch):  # noqa: F811
    monkeypatch.setattr(evaluator_module.litellm, "acompletion", AsyncMock(side_effect=RuntimeError("down")))
    evaluator = evaluator_factory(config=config.model_copy(update={"openai_api_key": "k"}))
    eval_res = await evaluator.evaluate_candidate(create_equity_candidate(), use_llm=True, news_context=BLOCK)
    assert eval_res.news_context == BLOCK and eval_res.llm_verdict is None
