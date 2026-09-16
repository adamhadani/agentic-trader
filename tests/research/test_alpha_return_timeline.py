"""Execution returns describe portfolio equity, independently of feature availability."""

from dataclasses import asdict

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.alpha.catalog import AlphaCatalog
from agentic_trader.research.alpha.miner import AlphaMiner
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.promotion import block_bootstrap_mean
from agentic_trader.research.alpha.simulation import return_statistics, simulate_strategy
from agentic_trader.research.alpha.strategy import AlphaExecutionPolicy
from agentic_trader.research.alpha.validation import ValidationPolicy, purged_folds


@pytest.fixture
def flat_bars():
    bars = pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0},
        index=pd.date_range("2020-01-01", periods=600, freq="B", tz="UTC"),
    )
    bars.attrs.update(timeframe="1d", feed="synthetic", adjustment="raw")
    return bars


@pytest.fixture
def definition():
    return AlphaDefinition(
        "timeline_control",
        "Timeline",
        "volume",
        timeframe="1d",
        data_feed="synthetic",
        execution=AlphaExecutionPolicy(
            atr_window=2,
            swing_window=1,
            stop_atr=1,
            reward_risk=2,
            structural_buffer_ticks=0,
            friction_per_side=0,
            trail_trigger_r=100,
        ),
    )


@pytest.mark.parametrize("pattern", ["unavailable", "warmup", "intermittent", "available"])
@pytest.mark.parametrize("start", [0, 100])
def test_cash_returns_keep_every_bar_without_inventing_scores(flat_bars, definition, pattern, start):
    scores = pd.Series(np.nan, index=flat_bars.index)
    if pattern == "warmup":
        scores.iloc[150:] = 0
    elif pattern == "intermittent":
        scores.iloc[::7] = 0
    elif pattern == "available":
        scores[:] = 0
    original = scores.copy()
    result = simulate_strategy(definition, flat_bars, scores=scores, start=start)
    expected = pd.Series(0.0, index=flat_bars.index[start:])
    pd.testing.assert_series_equal(result["net_returns"], expected)
    pd.testing.assert_series_equal(scores, original)
    assert result["sample_length"] == len(expected)
    assert result["annual_factor"] == pytest.approx(return_statistics(expected, [])["annual_factor"])
    available = int(scores.shift(1).iloc[start:].notna().sum())
    assert result["feature_coverage"] == {
        "bars": len(expected),
        "scored_bars": available,
        "unscored_bars": len(expected) - available,
        "score_fraction": available / len(expected),
    }
    assert not result["entries"]
    assert block_bootstrap_mean(result["net_returns"], seed=21) == (0.0, 0.0)


@pytest.mark.parametrize("direction", [-1, 1])
def test_missing_scores_do_not_erase_pending_cash_fills_or_exit_returns(flat_bars, definition, direction):
    bars = flat_bars.iloc[:8].copy()
    bars.iloc[2:5, :4] = [
        100 + direction,
        101.5 if direction == 1 else 99.5,
        100.5 if direction == 1 else 98.5,
        100 + direction,
    ]
    bars.iloc[5, :4] = [100, 100.5, 99.5, 100 + direction * 0.2]
    bars.iloc[6, :4] = [100 + direction * 0.2, 101.5, 98.5, 100 + direction]
    bars.iloc[7, :4] = [
        100 + direction,
        105 if direction == 1 else 100,
        100 if direction == 1 else 95,
        100 + direction * 4,
    ]
    scores = pd.Series(np.nan, index=bars.index)
    scores.iloc[1] = direction * 2
    pending = simulate_strategy(definition, bars.iloc[:5], scores=scores.iloc[:5], start=2)
    assert pending["pending_entry"] and not pending["entries"]
    assert pending["net_returns"].tolist() == [0.0, 0.0, 0.0]
    result = simulate_strategy(definition, bars, scores=scores, start=2)
    equity = pd.Series([1, 1, 1, 1.002, 1.01, 1.04], index=bars.index[2:])
    expected = equity / equity.shift(1, fill_value=1) - 1
    pd.testing.assert_series_equal(result["net_returns"], expected, atol=1e-12)
    assert result["total_trades"] == 1
    assert result["total_return_pct"] == pytest.approx(4)
    assert result["entries"][0]["timestamp"] == str(bars.index[5])


@pytest.mark.parametrize("column", ["open", "high", "low", "close"])
@pytest.mark.parametrize("value", [np.nan, np.inf, 0.0])
def test_unknown_prices_cannot_be_replaced_by_cash_returns(flat_bars, definition, column, value):
    flat_bars.loc[flat_bars.index[-1], column] = value
    with pytest.raises(ValueError, match="OHLC"):
        simulate_strategy(definition, flat_bars)


@pytest.mark.parametrize("value", [np.inf, -np.inf])
def test_infinite_score_cannot_create_a_signal(flat_bars, definition, value):
    scores = pd.Series(0.0, index=flat_bars.index)
    scores.iloc[100] = value
    with pytest.raises(ValueError, match="score"):
        simulate_strategy(definition, flat_bars, scores=scores)


@pytest.mark.parametrize("future_input", ["prices", "scores", "score_index"])
def test_simulation_interval_does_not_inspect_later_observations(flat_bars, definition, future_input):
    scores = pd.Series(0.0, index=flat_bars.index)
    expected = simulate_strategy(definition, flat_bars, scores=scores, start=100, end=300)
    if future_input == "prices":
        flat_bars.iloc[300:, :4] = np.nan
    elif future_input == "scores":
        scores.iloc[300:] = np.inf
    else:
        scores.index = scores.index[:300].append(scores.index[:300])
    actual = simulate_strategy(definition, flat_bars, scores=scores, start=100, end=300)
    pd.testing.assert_series_equal(actual.pop("net_returns"), expected.pop("net_returns"))
    assert actual == expected


@pytest.mark.parametrize("consumer", [return_statistics, block_bootstrap_mean])
@pytest.mark.parametrize("defect", ["nan", "inf", "duplicate", "unordered"])
def test_return_consumers_reject_missing_or_ambiguous_clock(consumer, defect):
    returns = pd.Series(np.tile([-0.01, 0.02], 50), index=pd.date_range("2020-01-01", periods=100))
    if defect in ("nan", "inf"):
        returns.iloc[40] = np.nan if defect == "nan" else np.inf
    elif defect == "duplicate":
        returns.index = returns.index[:50].append(returns.index[:50])
    else:
        returns = returns.iloc[::-1]
    with pytest.raises(ValueError, match="return|observation"):
        consumer(returns, []) if consumer is return_statistics else consumer(returns, seed=7)


def test_real_miner_preserves_cash_clock_and_reports_feature_coverage(flat_bars, definition):
    miner = AlphaMiner(catalog=AlphaCatalog([definition]), seed=21)
    miner.mine(flat_bars, iterations=0, symbol="SYNTH")
    trial = miner.last_run["trials"][0]
    folds = purged_folds(len(flat_bars), ValidationPolicy())
    assert trial["status"] == "evaluated"
    candidate = trial["candidate"]
    assert candidate["metrics"]["sample_length"] == sum(f.validation_end - f.validation_start for f in folds)
    assert all(c["score_fraction"] == 0 for c in candidate["evidence"]["validation_coverage"])
    assert miner.last_run["policy"] == asdict(ValidationPolicy())
