"""Executable review findings, not promotion evidence. See docs/alpha-stack-review.md.

Strict xfails describe required invariants the current implementation violates.
They must become ordinary passing tests with the corresponding fixes; unexpected
exceptions are not accepted as evidence of the documented assertion failure.
All inputs are synthetic and the repository's normal isolation guards apply.
"""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier, Lock

import numpy as np
import pandas as pd
import pytest
from click.testing import CliRunner

from agentic_trader.cli.main import cli
from agentic_trader.constants import AssetClass
from agentic_trader.data.market_data import ContractMarketData
from agentic_trader.research.alpha.catalog import AlphaCatalog
from agentic_trader.research.alpha.dsl import AlphaExpressionEvaluator
from agentic_trader.research.alpha.metrics import calculate_deflated_sharpe_ratio, simulate_alpha_performance
from agentic_trader.research.alpha.miner import AlphaMiner
from agentic_trader.research.alpha.models import AlphaCandidate, AlphaDefinition, AlphaEvaluationMetrics
from agentic_trader.research.alpha.optimizer import ConvexAlphaPortfolioOptimizer
from agentic_trader.research.alpha.orthogonalization import build_factor_annihilator, gram_schmidt_orthogonalize
from agentic_trader.research.alpha.promotion import AlphaPromotionManager
from agentic_trader.screeners.formulaic import FormulaicAlphaStrategy
from agentic_trader.screeners.registry import StrategyRegistry


def gap(finding):
    return pytest.mark.xfail(strict=True, raises=AssertionError, reason=f"Alpha review {finding}")


@pytest.fixture
def alpha_bars():
    rng = np.random.default_rng(20260916)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.015, 500)))
    return pd.DataFrame(
        {
            "Open": close * (1 + rng.normal(0, 0.002, len(close))),
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": rng.integers(1000, 10000, len(close)),
        },
        index=pd.date_range("2020-01-01", periods=len(close), freq="D"),
    )


@pytest.mark.parametrize(
    "expression",
    [
        *(a.expression for a in AlphaCatalog().list_alphas()),
        "delay(close, 5)",
        "zscore(close, 20)",
        pytest.param("rank(close)", marks=gap("A1: global rank reads future observations")),
        pytest.param("scale(close)", marks=gap("A1: global scale reads future observations")),
        pytest.param("delay(close, -1)", marks=gap("A1: negative lag reads next price")),
        pytest.param("delta(close, -1)", marks=gap("A1: negative delta reads next price")),
    ],
)
def test_alpha_prefix_invariance(alpha_bars, expression):
    evaluator = AlphaExpressionEvaluator()
    full = evaluator.evaluate(expression, alpha_bars)
    prefix = evaluator.evaluate(expression, alpha_bars.iloc[:250])
    assert np.allclose(full.iloc[:250], prefix), "Appending future bars changed historical scores"


@gap("A1: missing required fields silently become zeros")
def test_missing_input_is_rejected(alpha_bars):
    try:
        AlphaExpressionEvaluator().evaluate("ts_rank(volume, 10)", alpha_bars.drop(columns="Volume"))
    except ValueError:
        return
    raise AssertionError("Missing required data was accepted")


@pytest.mark.parametrize("expression", ["delay(close, -1)", "sma(close)", "close // 2", "sma(close, 5, d=10)"])
@gap("A1: syntax validation does not validate operator contracts")
def test_invalid_operator_contract_is_rejected(expression):
    assert not AlphaExpressionEvaluator().validate(expression)


@gap("A2: research 50-bar and live 30-bar normalization disagree")
def test_research_and_live_entry_scores_match(alpha_bars):
    definition = AlphaDefinition("alpha_parity", "Parity", "delta(close, 3)", entry_threshold=1.0)
    raw = AlphaExpressionEvaluator().evaluate(definition.expression, alpha_bars)
    research_z = (raw - raw.rolling(50, min_periods=10).mean()) / raw.rolling(50, min_periods=10).std()
    strategy = FormulaicAlphaStrategy(definition)
    mismatches = []
    for length in range(60, len(alpha_bars) + 1):
        frame = alpha_bars.iloc[:length]
        results = strategy.evaluate(ContractMarketData(symbol="SPY", four_hour=frame, daily=frame), AssetClass.EQUITY)
        live_direction = results[0].direction if results else None
        z = research_z.iloc[length - 1]
        expected = "LONG" if z >= 1 else "SHORT" if z <= -1 else None
        if live_direction != expected:
            mismatches.append(length)
    assert not mismatches, f"{len(mismatches)}/{len(alpha_bars) - 59} entry decisions differ"


@gap("A2: missing requested timeframe silently uses a different timeframe")
def test_missing_timeframe_does_not_emit_mislabeled_signal(alpha_bars):
    strategy = FormulaicAlphaStrategy(
        AlphaDefinition("alpha_tf", "Timeframe", "close", timeframe="15m", entry_threshold=0.01)
    )
    data = ContractMarketData(symbol="SPY", hourly=alpha_bars, daily=alpha_bars)
    assert strategy.evaluate(data, AssetClass.EQUITY) == []


@gap("A3: miner sends annualized Sharpe to a per-observation statistic")
def test_miner_dsr_uses_per_bar_sharpe(alpha_bars):
    definition = AlphaDefinition("alpha_dsr", "DSR", "delta(close, 3)", entry_threshold=0.5)
    candidate = AlphaMiner().evaluate_alpha(definition, alpha_bars)
    assert candidate is not None
    raw = AlphaExpressionEvaluator().evaluate(definition.expression, alpha_bars).iloc[350:]
    sim = simulate_alpha_performance(raw, alpha_bars.Close.iloc[350:], entry_threshold=0.5)
    returns = sim["net_returns"]
    expected = calculate_deflated_sharpe_ratio(
        sharpe=float(returns.mean() / returns.std()),
        num_trials=1,
        variance_trials=0,
        sample_length=len(returns),
        skewness=sim["skewness"],
        kurtosis=sim["kurtosis"],
    )
    assert candidate.metrics.dsr == pytest.approx(expected, abs=0.001)


@gap("A4: alpha IDs depend on the process hash seed")
def test_generated_identity_is_stable_across_processes():
    code = (
        "from agentic_trader.research.alpha.miner import AlphaMiner; "
        "a=AlphaMiner().generate_candidate_expression(seed=123); print(a.expression, a.alpha_id)"
    )
    results = [
        subprocess.check_output([sys.executable, "-c", code], env=dict(os.environ, PYTHONHASHSEED=str(seed)), text=True)
        for seed in (1, 2)
    ]
    assert results[0] == results[1]


@pytest.mark.parametrize("outcome", ["redundant", "error"])
@gap("A5: empty qualification falls back to primary symbol at promotion")
def test_cli_rejected_novelty_never_promotes(monkeypatch, alpha_bars, outcome):
    mgr = AlphaPromotionManager()
    incumbent = AlphaDefinition("alpha_incumbent", "Incumbent", "delta(close, 3)")
    mgr.promote(incumbent)
    candidate = AlphaCandidate(
        replace(incumbent, alpha_id="alpha_candidate"),
        AlphaEvaluationMetrics(sharpe_oos=2, dsr=0.99, rank_ic_mean=0.1),
    )
    monkeypatch.setattr("agentic_trader.cli.commands.alpha.yf.download", lambda *a, **kw: alpha_bars)
    monkeypatch.setattr(AlphaMiner, "mine", lambda *a, **kw: [candidate])
    monkeypatch.setattr(AlphaMiner, "evaluate_alpha", lambda *a, **kw: candidate)

    def novelty(*args):
        if outcome == "error":
            raise ValueError("Synthetic unavailable novelty evidence")
        return False, 0.0, 1.0

    monkeypatch.setattr("agentic_trader.cli.commands.alpha.evaluate_residual_predictive_power", novelty)
    result = CliRunner().invoke(cli, ["alpha", "mine", "--symbol", "SPY", "--auto-promote"])
    assert result.exit_code == 0, result.output
    assert mgr.get_record("alpha_candidate") is None


@gap("A6: independent promotion read-modify-write loses an update")
def test_concurrent_promotions_preserve_both_records(monkeypatch, tmp_path):
    managers = [AlphaPromotionManager(tmp_path / "alphas.yaml") for _ in range(2)]
    barrier, write_lock = Barrier(2), Lock()
    for manager in managers:
        original_load, original_save = manager.load_records, manager._save_records

        def load(original=original_load):
            result = original()
            barrier.wait(timeout=5)
            return result

        def save(records, original=original_save):
            # Even serializing atomic renames cannot protect a stale read.
            with write_lock:
                original(records)

        monkeypatch.setattr(manager, "load_records", load)
        monkeypatch.setattr(manager, "_save_records", save)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(manager.promote, AlphaDefinition(f"alpha_{i}", "Concurrent", "close"))
            for i, manager in enumerate(managers)
        ]
        for future in futures:
            future.result(timeout=10)
    records = AlphaPromotionManager(tmp_path / "alphas.yaml").load_records()
    assert {record.alpha_id for record in records} == {"alpha_0", "alpha_1"}


def test_registry_snapshot_is_explicit(tmp_path):
    path = tmp_path / "alphas.yaml"
    mgr, registry = AlphaPromotionManager(path), StrategyRegistry()
    mgr.promote(AlphaDefinition("alpha_a", "A", "close"))
    assert registry.load_promoted_alphas(path) == 1
    mgr.promote(AlphaDefinition("alpha_b", "B", "close"))
    assert registry.list_strategies() == ["alpha_a"]
    assert registry.load_promoted_alphas(path) == 2


@pytest.mark.parametrize("duplicate", [False, True])
def test_residualization_handles_collinear_basis(duplicate):
    rng = np.random.default_rng(14)
    x = rng.normal(size=500)
    basis = np.column_stack([x, 2 * x]) if duplicate else x[:, None]
    residual, _, _ = gram_schmidt_orthogonalize(x + rng.normal(size=500), basis)
    assert np.max(np.abs(basis.T @ residual)) < 1e-8


@gap("A7: WLS residuals are weighted-orthogonal, not dollar-factor neutral")
def test_weighted_projection_claims_are_valid():
    x = np.array([[1, -1], [1, 0], [1, 1], [1, 2]], dtype=float)
    weights = np.array([1, 2, 4, 8], dtype=float)
    projection = build_factor_annihilator(x, weights)
    assert np.allclose(x.T @ np.diag(weights) @ projection, 0)
    assert np.allclose(x.T @ projection, 0), "Unweighted factor exposure remains"


@pytest.mark.parametrize("invalid", ["factor_shape", "weight_shape", "indefinite_covariance", "nan_alpha"])
@gap("A8: optimizer accepts malformed inputs or silently drops constraints")
def test_optimizer_rejects_invalid_inputs(invalid):
    args = {"alpha": np.array([0.03, 0.02, -0.01]), "covariance": np.eye(3)}
    if invalid == "factor_shape":
        args["factor_matrix"] = np.ones((2, 1))
    elif invalid == "weight_shape":
        args["current_weights"] = np.ones(2)
    elif invalid == "indefinite_covariance":
        args["covariance"] = -np.eye(3)
    else:
        args["alpha"][0] = np.nan
    try:
        ConvexAlphaPortfolioOptimizer(max_factor_exposure=0).optimize(**args)
    except ValueError:
        return
    raise AssertionError("Malformed optimization input was accepted")


@pytest.mark.parametrize("assets", [3, 10])
def test_optimizer_feasible_constraints_under_load(assets):
    rng = np.random.default_rng(assets)
    basis = rng.normal(size=(assets, 3))
    covariance = basis @ basis.T * 0.01 + np.eye(assets) * 0.02
    result = ConvexAlphaPortfolioOptimizer(
        risk_aversion=1,
        dollar_neutral=True,
        max_factor_exposure=0.05,
    ).optimize(rng.normal(0, 0.02, assets), covariance, basis)
    assert result.success, result.message
    assert np.abs(result.weights).sum() <= 0.60 + 1e-6
    assert np.abs(result.weights).max() <= 0.15 + 1e-6
    assert abs(result.weights.sum()) <= 1e-6
    assert np.abs(basis.T @ result.weights).max() <= 0.05 + 1e-6
