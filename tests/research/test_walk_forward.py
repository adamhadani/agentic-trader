import pytest

from agentic_trader.config import AppConfig, load_config
from agentic_trader.research.models import (
    OptimizationResult,
    ParameterCandidate,
    WalkForwardFold,
)
from agentic_trader.research.optimizer import ParameterGridOptimizer
from agentic_trader.research.reporting import format_optimization_report
from tests.research.test_optimizer import make_synthetic_screener_data


@pytest.fixture
def config() -> AppConfig:
    return load_config()


def test_walk_forward_models_and_reporting():
    f1 = WalkForwardFold(
        fold_index=1,
        train_start="2024-01-01",
        train_end="2024-06-30",
        test_start="2024-07-01",
        test_end="2024-09-30",
        best_parameters={"rsi_threshold": 40.0, "ema_span": 20},
        train_return_pct=15.0,
        test_return_pct=10.0,
        train_sharpe=1.8,
        test_sharpe=1.4,
        wfe_ratio=0.67,
    )
    f2 = WalkForwardFold(
        fold_index=2,
        train_start="2024-01-01",
        train_end="2024-09-30",
        test_start="2024-10-01",
        test_end="2024-12-31",
        best_parameters={"rsi_threshold": 40.0, "ema_span": 20},
        train_return_pct=18.0,
        test_return_pct=9.0,
        train_sharpe=2.0,
        test_sharpe=1.1,
        wfe_ratio=0.50,
    )

    c1 = ParameterCandidate(
        parameters={"rsi_threshold": 40.0, "ema_span": 20},
        total_return_pct=25.0,
        win_rate=65.0,
        profit_factor=2.2,
        sharpe_ratio=1.6,
        max_drawdown_pct=4.0,
        total_trades=18,
        is_return_pct=16.5,
        oos_return_pct=9.5,
        is_sharpe=1.9,
        oos_sharpe=1.25,
        wfe_ratio=0.58,
    )

    result = OptimizationResult(
        symbol="SPY",
        strategy="trend_pullback",
        lookback="2y",
        engine_used="Vectorized NumPy",
        total_combinations_tested=21,
        ranked_candidates=[c1],
        is_walk_forward=True,
        walk_forward_folds=[f1, f2],
        avg_wfe_ratio=0.58,
    )

    report = format_optimization_report(result, top_n=1)
    assert "WALK-FORWARD PARAMETER OPTIMIZATION REPORT" in report
    assert "Walk-Forward Validation: ACTIVE (Folds: 2, Avg WFE: 0.58)" in report
    assert "WALK-FORWARD OUT-OF-SAMPLE CROSS-VALIDATION FOLDS" in report
    assert "Fold | In-Sample Train Window" in report
    assert "2024-01-01 -> 2024-06-30" in report
    assert "PASS (Robust Out-of-Sample Edge)" in report
    assert "Walk-Forward Efficiency: 0.58" in report


def test_walk_forward_optimizer_execution(config: AppConfig):
    optimizer = ParameterGridOptimizer(config=config)
    data = make_synthetic_screener_data(symbol="SPY", n_bars=80)

    res = optimizer.run(
        symbol="SPY",
        strategy="trend_pullback",
        lookback="2y",
        market_data=data,
        force_fallback=True,
        walk_forward=True,
        splits=3,
        train_ratio=0.60,
    )

    assert res.is_walk_forward is True
    assert len(res.walk_forward_folds) > 0
    assert res.avg_wfe_ratio is not None
    assert len(res.ranked_candidates) == 21

    # Check that candidates have out-of-sample fields populated
    top = res.ranked_candidates[0]
    assert top.is_return_pct is not None
    assert top.oos_return_pct is not None
    assert top.oos_sharpe is not None
    assert top.wfe_ratio is not None


def test_walk_forward_insufficient_bars_fallback(config: AppConfig):
    optimizer = ParameterGridOptimizer(config=config)
    # Fewer than 30 bars triggers fallback to standard single window
    data = make_synthetic_screener_data(symbol="SPY", n_bars=25)

    res = optimizer.run(
        symbol="SPY",
        strategy="trend_pullback",
        lookback="1m",
        market_data=data,
        force_fallback=True,
        walk_forward=True,
    )

    assert res.is_walk_forward is False
    assert len(res.walk_forward_folds) == 0


def test_walk_forward_squeeze_breakout(config: AppConfig):
    optimizer = ParameterGridOptimizer(config=config)
    data = make_synthetic_screener_data(symbol="QQQ", n_bars=70)

    res = optimizer.run(
        symbol="QQQ",
        strategy="squeeze_breakout",
        lookback="1y",
        market_data=data,
        force_fallback=True,
        walk_forward=True,
        splits=2,
    )

    assert res.is_walk_forward is True
    assert res.strategy == "squeeze_breakout"
    assert len(res.ranked_candidates) == 25
    assert all(c.oos_sharpe is not None for c in res.ranked_candidates)
