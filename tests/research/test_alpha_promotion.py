from dataclasses import asdict

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.promotion import assess_qualification, block_bootstrap_mean, read_alpha_definitions
from agentic_trader.research.alpha.validation import ValidationPolicy


def test_yaml_import_is_definition_only(tmp_path):
    path = tmp_path / "import.yaml"
    path.write_text(
        "promoted_alphas:\n- metrics: {dsr: 0.99, sharpe_oos: 9}\n  definition:\n    alpha_id: alpha_test\n    expression: close\n    eligible_symbols: [spy, qqq]\n"
    )
    definitions = read_alpha_definitions(path)
    assert definitions[0].eligible_symbols == ("QQQ", "SPY")
    assert not hasattr(definitions[0], "metrics")


def test_bootstrap_is_reproducible_and_respects_blocks():
    returns = pd.Series(np.tile([-0.02, -0.02, 0.03, 0.03], 100))
    first = block_bootstrap_mean(returns, seed=10)
    assert first == block_bootstrap_mean(returns, seed=10)
    assert first[0] < returns.mean() < first[1]
    with pytest.raises(ValueError, match="Insufficient"):
        block_bootstrap_mean(returns.iloc[:10], seed=10)


@pytest.mark.parametrize("timeframe", ["15m", "1h", "4h"])
def test_intraday_data_cannot_claim_session_correct_bracket_execution(timeframe):
    definition = AlphaDefinition(
        "alpha_session", "Session", "close", timeframe=timeframe, eligible_symbols=("SPY",), data_feed="alpaca:sip"
    )
    bars = pd.DataFrame(
        {"open": [100.0] * 100, "high": [101.0] * 100, "low": [99.0] * 100, "close": [100.0] * 100},
        index=pd.date_range("2025-01-01", periods=100, freq="h", tz="UTC"),
    )
    policy = ValidationPolicy()
    run = {"policy": asdict(policy), "trials": [{"definition": definition.to_dict()}], "holdout_start": 80, "seed": 1}
    manifest = {"feed": "alpaca:sip", "adjustment": "raw", "incumbents": []}
    decision = assess_qualification(
        definition, bars, run, manifest, {"trial_count": 1, "trial_variance": 0}, policy=policy
    )
    assert not decision["qualified"]
    assert "intraday_session_execution_unverified" in decision["reasons"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), None, True])
@pytest.mark.parametrize("metric", ["sharpe_oos", "dsr", "rank_ic_mean", "total_trades"])
def test_nonfinite_validation_evidence_is_rejected(value, metric):
    definition = AlphaDefinition(
        "synthetic", "Synthetic", "close", timeframe="1d", eligible_symbols=("SYNTH",), data_feed="synthetic"
    )
    bars = pd.DataFrame(
        {"open": [100.0] * 100, "high": [101.0] * 100, "low": [99.0] * 100, "close": [100.0] * 100},
        index=pd.date_range("2020-01-01", periods=100, tz="UTC"),
    )
    run = {
        "policy": asdict(ValidationPolicy()),
        "trials": [{"definition": definition.to_dict(), "candidate": {"metrics": {metric: value}}}],
        "holdout_start": 80,
        "seed": 1,
    }
    decision = assess_qualification(
        definition,
        bars,
        run,
        {"feed": "synthetic", "adjustment": "raw", "incumbents": []},
        {"trial_count": 1, "trial_variance": 0},
        policy=ValidationPolicy(),
    )
    assert f"validation_{metric}" in decision["reasons"]
