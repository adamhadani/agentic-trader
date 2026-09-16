"""Qualification service and explicit YAML interchange; the journal owns live state."""

from __future__ import annotations

import ast
import asyncio
from dataclasses import asdict, replace
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import yaml

from agentic_trader.research.alpha.dsl import compile_expression
from agentic_trader.research.alpha.metrics import calculate_deflated_sharpe_ratio
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.orthogonalization import residual_validation
from agentic_trader.research.alpha.simulation import simulate_strategy
from agentic_trader.research.alpha.strategy import alpha_scores
from agentic_trader.research.alpha.validation import ValidationPolicy, frame_digest, validate_sampling


if TYPE_CHECKING:
    from agentic_trader.storage.alpha import AlphaRepository


DEFAULT_PROMOTED_ALPHAS_PATH = Path("config/promoted_alphas.yaml")


def read_alpha_definitions(path: Path) -> list[AlphaDefinition]:
    """Explicit import only. Old YAML metrics are unverified, not qualification evidence."""
    payload = yaml.safe_load(path.read_text())
    if not isinstance(payload, dict):
        raise TypeError("Invalid alpha import document")
    records = payload.get("promoted_alphas", payload.get("alphas", []))
    if not isinstance(records, list):
        raise TypeError("Invalid alpha records")
    return [AlphaDefinition.from_dict(record.get("definition", record)) for record in records]


def block_bootstrap_mean(returns: pd.Series, *, seed: int, samples: int = 1000, block: int = 10) -> tuple[float, float]:
    values = returns.dropna().to_numpy()
    if len(values) < 2 * block:
        raise ValueError("Insufficient block-bootstrap observations")
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, len(values) - block + 1, size=(samples, int(np.ceil(len(values) / block))))
    indices = (starts[:, :, None] + np.arange(block)).reshape(samples, -1)[:, : len(values)]
    lower, upper = np.quantile(values[indices].mean(axis=1), [0.025, 0.975])
    return float(lower), float(upper)


class AlphaPromotionService:
    def __init__(self, repository: AlphaRepository, policy: ValidationPolicy | None = None):
        self.repository = repository
        self.policy = policy or ValidationPolicy()

    async def qualify(self, run_id: str, version_id: str, bars: pd.DataFrame):
        saved = await self.repository.get(f"run/{run_id}")
        if not saved:
            raise ValueError("Unknown research run")
        manifest, run = saved["manifest"], saved["run"]
        if frame_digest(bars) != manifest["content_hash"]:
            raise ValueError("Dataset differs from the frozen run manifest")
        definition_row = await self.repository.get(f"version/{version_id}")
        if not definition_row:
            raise ValueError("Unknown finalist version")
        definition = AlphaDefinition.from_dict(definition_row["definition"])
        validate_sampling(bars, definition.timeframe)
        if definition.eligible_symbols != (manifest["symbol"],):
            raise ValueError("Finalist requires an explicitly validated symbol universe")
        # Freeze and consume before any holdout computation. Failure stays consumed.
        family = await self.repository.begin_holdout(run_id, version_id)
        decision = await asyncio.to_thread(self._evaluate, definition, bars, run, manifest, family)
        await self.repository.record_qualification(run_id, version_id, decision)
        return decision

    def _evaluate(self, definition, bars, run, manifest, family):
        policy = self.policy
        reasons = []
        if definition.timeframe != "1d":
            reasons.append("intraday_session_execution_unverified")
        if run.get("policy") != asdict(policy):
            reasons.append("qualification_policy_differs_from_frozen_run")
        # Recursive EMA depends on its initialization history. It remains a valid
        # research operator but cannot deploy until shared state is persisted.
        if any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id.lower() == "ema"
            for node in ast.walk(compile_expression(definition.expression).tree)
        ):
            reasons.append("recursive_feature_requires_shared_initialization")
        trial = next(
            t for t in run["trials"] if AlphaDefinition.from_dict(t["definition"]).version_id == definition.version_id
        )
        metrics = trial.get("candidate", {}).get("metrics", {})
        evidence = trial.get("candidate", {}).get("evidence", {})
        for key, minimum in (
            ("sharpe_oos", policy.min_sharpe),
            ("dsr", policy.min_dsr),
            ("rank_ic_mean", policy.min_ic),
            ("total_trades", policy.minimum_trades),
        ):
            if metrics.get(key, float("-inf")) < minimum:
                reasons.append(f"validation_{key}")
        if len(evidence.get("fold_sharpes", [])) < policy.folds or any(
            s <= 0 for s in evidence.get("fold_sharpes", [])
        ):
            reasons.append("unstable_validation_folds")
        family_dsr = calculate_deflated_sharpe_ratio(
            metrics.get("per_bar_sharpe", 0),
            family["trial_count"],
            family["trial_variance"],
            metrics.get("sample_length", 0),
            metrics.get("skewness", 0),
            metrics.get("kurtosis", 3),
        )
        if family_dsr < policy.min_dsr:
            reasons.append("family_adjusted_validation_dsr")
        start = run["holdout_start"] + policy.embargo_bars + policy.label_horizon
        try:
            result = simulate_strategy(definition, bars, start=start)
            stress = simulate_strategy(
                replace(
                    definition,
                    execution=replace(
                        definition.execution, friction_per_side=definition.execution.friction_per_side * 2
                    ),
                ),
                bars,
                start=start,
            )
            # Conservative family adjustment persists across symbol/seed runs.
            dsr = calculate_deflated_sharpe_ratio(
                result["per_bar_sharpe"],
                family["trial_count"],
                family["trial_variance"],
                result["sample_length"],
                result["skewness"],
                result["kurtosis"],
            )
            confidence = block_bootstrap_mean(result["net_returns"], seed=run["seed"])
            if result["sharpe"] < policy.min_sharpe:
                reasons.append("holdout_sharpe")
            if result["total_trades"] < policy.minimum_trades:
                reasons.append("holdout_trade_count")
            if dsr < policy.min_dsr:
                reasons.append("holdout_dsr")
            if result["max_drawdown_pct"] > policy.max_drawdown_pct:
                reasons.append("holdout_drawdown")
            if stress["total_return_pct"] <= 0:
                reasons.append("cost_stress")
            if confidence[0] <= 0:
                reasons.append("bootstrap_uncertainty")
            summary = {k: v for k, v in result.items() if k not in ("net_returns", "trades", "entries")}
            summary.update(dsr=dsr, bootstrap_mean_interval=confidence, stressed_return_pct=stress["total_return_pct"])
        except (ValueError, ArithmeticError) as exc:
            reasons.append(f"holdout_unavailable:{exc}")
            summary = {}
        novelty = {"status": "no_incumbents"}
        if "incumbents" not in manifest:
            reasons.append("missing_frozen_incumbent_snapshot")
        elif manifest["incumbents"]:
            try:
                incumbents = [AlphaDefinition.from_dict(item) for item in manifest["incumbents"]]
                relevant = [
                    d
                    for d in incumbents
                    if (not d.eligible_symbols or manifest["symbol"] in d.eligible_symbols)
                    and d.version_id != definition.version_id
                ]
                if any(d.timeframe != definition.timeframe for d in relevant):
                    raise ValueError("Incremental evidence needs matching incumbent horizons")
                if relevant:
                    candidate_scores = alpha_scores(definition, bars)
                    basis = pd.DataFrame({d.version_id: alpha_scores(d, bars) for d in relevant})
                    close = bars.rename(columns=str.lower).close
                    forward = close.shift(-policy.label_horizon) / close - 1
                    training = (
                        int(run["holdout_start"] * policy.initial_train_fraction)
                        - policy.label_horizon
                        - policy.embargo_bars
                    )
                    novelty = residual_validation(
                        candidate_scores, basis, forward, train_end=training, validation_start=start
                    )
                    if not novelty["novel"]:
                        reasons.append("no_incremental_predictive_evidence")
            except (ValueError, ArithmeticError) as exc:
                novelty = {"status": "unavailable", "reason": str(exc)}
                reasons.append("incremental_evidence_unavailable")
        # Feed equivalence is mandatory for execution eligibility. Yfinance runs are
        # discovery evidence and require revalidation on the deployment feed.
        if (
            manifest["feed"] not in ("alpaca:iex", "alpaca:sip")
            or manifest["adjustment"] != "raw"
            or definition.data_feed != manifest["feed"]
            or definition.adjustment != manifest["adjustment"]
        ):
            reasons.append("deployment_data_contract_mismatch")
        return {
            "qualified": not reasons,
            "reasons": reasons,
            "policy": asdict(policy),
            "holdout": summary,
            "eligible_symbols": list(definition.eligible_symbols or ()),
            "manifest": manifest,
            "family": family,
            "novelty": novelty,
            "calibration": evidence.get("calibration"),
        }
