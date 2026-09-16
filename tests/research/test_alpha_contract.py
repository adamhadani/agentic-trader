from dataclasses import FrozenInstanceError, replace

import numpy as np
import pandas as pd
import pytest

from agentic_trader.constants import AssetClass
from agentic_trader.data.market_data import ContractMarketData
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.strategy import AlphaExecutionPolicy, alpha_scores, entry_directions, strategy_atr
from agentic_trader.screeners.formulaic import FormulaicAlphaStrategy


@pytest.fixture
def definition():
    return AlphaDefinition("alpha_contract", "Contract", "delta(close, 3)", entry_threshold=1)


def test_definition_is_immutable_and_versioned(definition):
    with pytest.raises(FrozenInstanceError):
        definition.timeframe = "1d"
    assert definition.version_id == AlphaDefinition.from_dict(definition.to_dict()).version_id
    assert definition.version_id != replace(definition, timeframe="1d").version_id
    assert definition.version_id != replace(definition, normalization_window=40).version_id


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeframe": "bogus"},
        {"entry_threshold": float("nan")},
        {"entry_threshold": -1},
        {"direction": "both"},
        {"normalization_window": 1},
        {"expression": "delay(close,-1)"},
    ],
)
def test_invalid_spec_rejected(kwargs):
    with pytest.raises(ValueError):
        AlphaDefinition(
            "alpha_bad", "Bad", "close", **{k: v for k, v in kwargs.items() if k != "expression"}
        ) if "expression" not in kwargs else AlphaDefinition("alpha_bad", "Bad", **kwargs)


def test_research_live_entry_parity_and_provenance(definition):

    rng = np.random.default_rng(124)
    close = 100 + rng.normal(size=180).cumsum()
    bars = pd.DataFrame(
        {"Open": close, "High": close + 1, "Low": close - 1, "Close": close, "Volume": 1000},
        index=pd.date_range("2025-01-01", periods=180),
    )
    expected = entry_directions(alpha_scores(definition, bars), definition)
    strategy = FormulaicAlphaStrategy(definition)
    for end in range(40, 181):
        candidates = strategy.evaluate(ContractMarketData(symbol="SPY", four_hour=bars.iloc[:end]), AssetClass.EQUITY)
        actual = (1 if candidates[0].direction == "LONG" else -1) if candidates else 0
        assert actual == expected.iloc[end - 1]
        if candidates:
            assert candidates[0].alpha_version == definition.version_id
            assert candidates[0].alpha_policy == definition.execution.to_dict()


@pytest.mark.parametrize("timeframe", ["15m", "1h", "4h"])
def test_no_timeframe_fallback(definition, timeframe):
    bars = pd.DataFrame({"Close": np.arange(100)}, index=pd.date_range("2025-01-01", periods=100))
    data = ContractMarketData(symbol="SPY", daily=bars)
    assert FormulaicAlphaStrategy(replace(definition, timeframe=timeframe)).evaluate(data, AssetClass.EQUITY) == []


def test_bracket_observations_do_not_depend_on_extra_history():
    rng = np.random.default_rng(73)
    close = 100 + rng.normal(size=180).cumsum()
    bars = pd.DataFrame({"close": close, "high": close + 1, "low": close - 1})
    policy = AlphaExecutionPolicy()
    assert strategy_atr(bars, policy).iloc[-1] == pytest.approx(strategy_atr(bars.iloc[-30:], policy).iloc[-1])
