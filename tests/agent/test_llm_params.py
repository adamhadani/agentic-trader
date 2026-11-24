"""litellm request parameters: no process-global ``drop_params``, per-call flags, offline parameter mapping."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import litellm
import pytest
from litellm.exceptions import UnsupportedParamsError
from litellm.utils import get_optional_params

from agentic_trader.agent.copilot_graph import create_copilot_graph
from agentic_trader.agent.evaluator import RiskEvaluator
from agentic_trader.agent.macro import (
    MacroIntelligenceReport,
    TreasuryYields,
    calculate_yield_curve_spreads,
    classify_credit_stress,
    classify_inflation_expectations,
    compute_compound_macro_stress,
)
from agentic_trader.agent.macro_explainer import MacroExplainer
from agentic_trader.config import AppConfig
from agentic_trader.diagnostics.doctor import check_llm


GATE_PARAMS = {"response_format": {"type": "json_object"}}


def reply(text="OK"):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


def test_constructing_a_risk_evaluator_leaves_the_global_false(app_config):
    RiskEvaluator(app_config)
    assert litellm.drop_params is False


async def test_doctor_llm_check_drops_per_call_not_globally(monkeypatch):
    complete = AsyncMock(return_value=reply())
    monkeypatch.setattr(litellm, "acompletion", complete)
    health = await check_llm(AppConfig())
    assert health.status == "OK"
    assert complete.await_args.kwargs["drop_params"] is True
    assert litellm.drop_params is False


async def test_macro_explainer_drops_per_call(monkeypatch):
    complete = AsyncMock(return_value=reply("briefing"))
    monkeypatch.setattr(litellm, "acompletion", complete)
    yields = TreasuryYields(yield_3m=3.90, yield_2y=4.30, yield_5y=4.60, yield_10y=4.90, yield_30y=5.20)
    spreads = calculate_yield_curve_spreads(yields)
    credit = classify_credit_stress(2.65)
    report = MacroIntelligenceReport(
        timestamp=datetime.now(UTC),
        yields=yields,
        spreads=spreads,
        credit=credit,
        inflation=classify_inflation_expectations(2.37, 2.40),
        vix=17.5,
        dxy=102.5,
        stress=compute_compound_macro_stress(vix=17.5, yield_spreads=spreads, credit=credit),
        summary_text="Macro Report Test",
    )
    text = await MacroExplainer(AppConfig(openai_api_key="k")).explain(report)
    assert text == "briefing"
    assert complete.await_args.kwargs["drop_params"] is True


async def test_chat_graph_passes_drop_params_through_the_request(monkeypatch):
    copilot = MagicMock()
    copilot.config.llm_model = "openai/gpt-4o-mini"
    copilot.config.llm_api_key = "k"
    seen = {}

    async def fake_acompletion(**kwargs):
        seen.update(kwargs)
        return litellm.ModelResponse(choices=[{"message": {"role": "assistant", "content": "hi"}}])

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    graph = create_copilot_graph(copilot)
    await graph.ainvoke({"messages": [("user", "hello")]}, {"configurable": {"thread_id": "t"}})
    assert seen["drop_params"] is True
    assert litellm.drop_params is False


def test_gate_parameter_set_is_accepted_without_dropping(monkeypatch):
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    mapped = get_optional_params(model="gpt-5.6", custom_llm_provider="openai", drop_params=False, **GATE_PARAMS)
    assert mapped["response_format"] == {"type": "json_object"}
    assert "temperature" not in mapped


def test_a_temperature_is_refused_for_gpt_5_6_instead_of_dropped(monkeypatch):
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    with pytest.raises(UnsupportedParamsError):
        get_optional_params(
            model="gpt-5.6", custom_llm_provider="openai", drop_params=False, temperature=0.2, **GATE_PARAMS
        )
