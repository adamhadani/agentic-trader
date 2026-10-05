"""A catalog probe tests the unfiltered study rule: the LLM writes commentary, never a veto.

The deterministic gates (sizing, exposure, calendar, session) still run before the LLM
and still reject; only the LLM's own verdict is recorded rather than applied.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentic_trader.constants import AssetClass
from agentic_trader.research.alpha.strategy import bracket_prices, entry_limit
from agentic_trader.research.apriori.catalog import load_pead_entry, pead_execution_policy
from agentic_trader.screeners.base import ScreenerCandidate
from tests.agent.test_evaluator import evaluator_factory  # noqa: F401  (fixture)


POLICY = pead_execution_policy(load_pead_entry(Path("config/research/apriori/pead-v2.json")).entry)
LLM_VETO = {
    "approved": False,
    "rejection_reason": "earnings momentum fading",
    "thesis_summary": "Weak follow-through",
    "stop_loss": 1.0,
    "take_profit": 999.0,
    "macro_clearance": True,
}


def drift_candidate(**updates) -> ScreenerCandidate:
    candidate = ScreenerCandidate(
        contract="WINR",
        symbol="WINR",
        asset_class=AssetClass.EQUITY,
        timeframe="1d",
        strategy="pead_long",
        direction="LONG",
        current_price=50.0,
        ema_20=49.0,
        ema_50=48.0,
        ema_200=45.0,
        rsi_14=60.0,
        atr_14=1.2345,
        candle_timestamp="2026-10-28T14:30:00+00:00",
        recent_swing_low=47.0,
        recent_swing_high=51.0,
        trigger_detail="PEAD probe: EPS beat +10.0%, reaction +2.0σ vs SPY (report 2026-10-26).",
        alpha_version="apriori:pead:v2:long:abc",
        alpha_policy=POLICY.to_dict(),
        probe=True,
        catalog_event={"symbol": "WINR"},
    )
    return candidate.model_copy(update=updates)


def veto_completion(monkeypatch, **overrides) -> AsyncMock:
    content = json.dumps({**LLM_VETO, **overrides})
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    completion = AsyncMock(return_value=response)
    monkeypatch.setattr("agentic_trader.agent.evaluator.litellm.acompletion", completion)
    return completion


async def test_llm_verdict_on_a_catalog_probe_is_commentary_not_a_gate(evaluator_factory, monkeypatch):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.openai_api_key = "isolated-test-placeholder"
    completion = veto_completion(monkeypatch)
    candidate = drift_candidate()

    result = await evaluator.evaluate_candidate(candidate, use_llm=True)

    completion.assert_awaited_once()
    assert result.approved is True
    assert result.rejection_reason is None
    assert (
        result.llm_verdict["approved"] is False
        and result.llm_verdict["rejection_reason"] == "earnings momentum fading"
        and result.llm_verdict["applied"] is False
    )
    assert result.thesis_summary.startswith("LLM commentary (not a gate): Weak follow-through")
    entry = entry_limit(candidate.current_price, POLICY)
    stop, target = bracket_prices(
        entry, 1, candidate.atr_14, candidate.recent_swing_low, candidate.recent_swing_high, POLICY
    )
    assert (result.stop_loss, result.take_profit) == (stop, target)


@pytest.mark.parametrize(
    ("approved", "recorded"),
    [("false", False), ("FALSE", False), ("no", False), (1, False), (None, False), ("True", True), (True, True)],
)
async def test_the_recorded_llm_verdict_is_true_only_for_an_explicit_true(
    evaluator_factory,  # noqa: F811
    monkeypatch,
    approved,
    recorded,
):
    evaluator = evaluator_factory()
    evaluator.config.openai_api_key = "isolated-test-placeholder"
    veto_completion(monkeypatch, approved=approved)

    result = await evaluator.evaluate_candidate(drift_candidate(), use_llm=True)

    assert result.approved is True
    assert result.llm_verdict["approved"] is recorded


async def test_the_same_llm_veto_still_rejects_a_native_candidate(evaluator_factory, monkeypatch):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.openai_api_key = "isolated-test-placeholder"
    veto_completion(monkeypatch)

    result = await evaluator.evaluate_candidate(drift_candidate(catalog_event=None), use_llm=True)

    assert result.approved is False
    assert result.rejection_reason == "earnings momentum fading"
    assert result.llm_verdict["applied"] is True and result.llm_verdict["approved"] is False
    assert result.thesis_summary == "Weak follow-through"


async def test_a_deterministic_gate_still_rejects_a_catalog_probe(evaluator_factory, monkeypatch):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.openai_api_key = "isolated-test-placeholder"
    completion = veto_completion(monkeypatch)

    result = await evaluator.evaluate_candidate(
        drift_candidate(), current_open_notional=evaluator.config.portfolio.max_notional_exposure, use_llm=True
    )

    assert result.approved is False
    assert result.rejection_reason
    completion.assert_not_called()
