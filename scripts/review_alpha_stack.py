"""Offline alpha review diagnostics; no config, database, broker or network access.

Optional CSV inputs must have Date/Open/High/Low/Close/Volume columns. This checks causal entry parity and optimizer safety; it is not promotion evidence.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from agentic_trader.constants import AssetClass
from agentic_trader.data.market_data import ContractMarketData
from agentic_trader.research.alpha.catalog import AlphaCatalog
from agentic_trader.research.alpha.metrics import calculate_deflated_sharpe_ratio
from agentic_trader.research.alpha.miner import AlphaMiner
from agentic_trader.research.alpha.optimizer import ConvexAlphaPortfolioOptimizer
from agentic_trader.research.alpha.strategy import alpha_scores, entry_directions
from agentic_trader.screeners.formulaic import FormulaicAlphaStrategy


SEED = 20260916


def measure(market_directory: Path | None, mining_runs: int) -> dict:
    rng = np.random.default_rng(SEED)
    samples = rng.normal(0, 0.01, (10_000, 150))
    sharpes = samples.mean(axis=1) / samples.std(axis=1, ddof=1)
    confidence = [
        [calculate_deflated_sharpe_ratio(float(sr * factor), 1, 0, 150) for sr in sharpes]
        for factor in (1, math.sqrt(409.5))
    ]
    report = {
        "seed": SEED,
        "scope": "Diagnostics only; entry-threshold disagreement is not executed-trade disagreement or P&L.",
        "single_trial_null": {
            "independent_zero_mean_trials": len(sharpes),
            "bars_per_trial": samples.shape[1],
            "confidence_threshold": 0.85,
            "per_bar_input_passes": sum(p >= 0.85 for p in confidence[0]),
            "annualized_input_passes": sum(p >= 0.85 for p in confidence[1]),
            "caveat": "Normal moments and one trial isolate the unit error; not the mining false-discovery rate.",
        },
        "market_parity": [],
        "synthetic_mining": [],
        "optimizer_load": [],
    }
    if market_directory:
        for path in sorted(market_directory.glob("*.csv")):
            frame = pd.read_csv(path, index_col=0, parse_dates=True)
            for definition in AlphaCatalog().list_alphas():
                definition = replace(definition, timeframe="1d")
                expected = entry_directions(alpha_scores(definition, frame), definition).iloc[59:]
                strategy = FormulaicAlphaStrategy(definition)
                actual = []
                for end in range(60, len(frame) + 1):
                    candidates = strategy.evaluate(
                        ContractMarketData(symbol=path.stem, daily=frame.iloc[:end]), AssetClass.EQUITY
                    )
                    actual.append(1 if candidates and candidates[0].direction == "LONG" else -1 if candidates else 0)
                signals = [expected.to_numpy(), np.array(actual)]
                report["market_parity"].append(
                    {
                        "symbol": path.stem,
                        "alpha": definition.alpha_id,
                        "first": str(frame.index[0]),
                        "last": str(frame.index[-1]),
                        "bars_compared": len(signals[0]),
                        "disagreements": int(np.sum(signals[0] != signals[1])),
                    }
                )
    for seed in range(mining_runs):
        data_rng = np.random.default_rng(seed)
        returns = data_rng.normal(0, 0.015, 504)
        close = 100 * np.cumprod(1 + returns)
        frame = pd.DataFrame(
            {
                "Close": close,
                "Open": np.r_[100, close[:-1]],
                "High": np.maximum(close, np.r_[100, close[:-1]]) * 1.02,
                "Low": np.minimum(close, np.r_[100, close[:-1]]) * 0.98,
                "Volume": data_rng.integers(1000, 10000, len(close)),
            },
            index=pd.date_range("2020-01-01", periods=len(close), freq="D"),
        )
        frame.attrs["timeframe"] = "1d"
        started = time.monotonic()
        candidates = AlphaMiner(seed=seed).mine(frame, iterations=25)
        report["synthetic_mining"].append(
            {
                "seed": seed,
                "bars": len(frame),
                "random_candidates_requested": 25,
                "qualified": len(candidates),
                "seconds": round(time.monotonic() - started, 3),
                "top_expression": candidates[0].definition.expression if candidates else None,
            }
        )
    # Numerical convergence can vary with BLAS/platform. Report this stress case
    # rather than making a platform-specific expected failure part of CI.
    for assets in (3, 10, 50, 100):
        optimizer_rng = np.random.default_rng(assets)
        basis = optimizer_rng.normal(size=(assets, 3))
        covariance = basis @ basis.T * 0.01 + np.eye(assets) * 0.02
        started = time.monotonic()
        result = ConvexAlphaPortfolioOptimizer(
            risk_aversion=1,
            dollar_neutral=True,
            max_factor_exposure=0.05,
        ).optimize(optimizer_rng.normal(0, 0.02, assets), covariance, basis)
        report["optimizer_load"].append(
            {
                "assets": assets,
                "success": result.success,
                "message": result.message,
                "iterations": result.iterations,
                "seconds": round(time.monotonic() - started, 3),
                "gross": float(np.abs(result.weights).sum()) if result.weights is not None else None,
                "max_weight": float(np.abs(result.weights).max()) if result.weights is not None else None,
                "net": float(result.weights.sum()) if result.weights is not None else None,
                "max_factor_exposure": float(np.abs(basis.T @ result.weights).max())
                if result.weights is not None
                else None,
            }
        )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-directory", type=Path)
    parser.add_argument("--mining-runs", type=int, choices=range(101), default=20, metavar="0..100")
    args = parser.parse_args()
    print(json.dumps(measure(args.market_directory, args.mining_runs), indent=2))
