"""Qualification service and explicit YAML interchange; the journal owns live state."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from numbers import Integral, Real
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import numpy as np
import pandas as pd
import yaml

from agentic_trader.research.alpha.dsl import compile_expression
from agentic_trader.research.alpha.metrics import calculate_deflated_sharpe_ratio, observed_return_values
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.orthogonalization import residual_validation
from agentic_trader.research.alpha.simulation import simulate_strategy
from agentic_trader.research.alpha.strategy import alpha_scores
from agentic_trader.research.alpha.validation import ValidationPolicy, frame_digest, validate_sampling


if TYPE_CHECKING:
    from agentic_trader.storage.alpha import AlphaRepository


BLOCK_BOOTSTRAP_SAMPLES: Final = 1000
BLOCK_BOOTSTRAP_BLOCK: Final = 10
BLOCK_BOOTSTRAP_QUANTILES: Final = (0.025, 0.975)


def read_alpha_definitions(path: Path) -> list[AlphaDefinition]:
    """Explicit import only. Old YAML metrics are unverified, not qualification evidence."""
    payload = yaml.safe_load(path.read_text())
    if not isinstance(payload, dict):
        raise TypeError("Invalid alpha import document")
    records = payload.get("promoted_alphas", payload.get("alphas", []))
    if not isinstance(records, list):
        raise TypeError("Invalid alpha records")
    return [AlphaDefinition.from_dict(record.get("definition", record)) for record in records]


def block_bootstrap_mean(
    returns: pd.Series,
    *,
    seed: int,
    samples: int = BLOCK_BOOTSTRAP_SAMPLES,
    block: int = BLOCK_BOOTSTRAP_BLOCK,
) -> tuple[float, float]:
    values = observed_return_values(returns)
    if len(values) < 2 * block:
        raise ValueError("Insufficient block-bootstrap observations")
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, len(values) - block + 1, size=(samples, int(np.ceil(len(values) / block))))
    indices = (starts[:, :, None] + np.arange(block)).reshape(samples, -1)[:, : len(values)]
    lower, upper = np.quantile(values[indices].mean(axis=1), BLOCK_BOOTSTRAP_QUANTILES)
    return float(lower), float(upper)


class AlphaPromotionService:
    def __init__(self, repository: AlphaRepository):
        self.repository = repository
        self.policy = repository.validation_policy

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
        if definition.clock is not None:
            raise ValueError("Session-clock qualification requires live acquisition and execution evidence")
        validate_sampling(bars, definition.timeframe)
        if definition.eligible_symbols != (manifest["symbol"],):
            raise ValueError("Finalist requires an explicitly validated symbol universe")
        # Freeze and consume before any holdout computation. Failure stays consumed.
        family = await self.repository.begin_holdout(run_id, version_id)
        decision = await asyncio.to_thread(
            assess_qualification, definition, bars, run, manifest, family, policy=self.policy
        )
        await self.repository.record_qualification(run_id, version_id, decision)
        return decision


def _finite(value):
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


MEASUREMENT_VERSION = "statistical_measurements_v1"
SAMPLING_FIELDS = ("per_bar_sharpe", "sample_length", "skewness", "kurtosis")


@dataclass(frozen=True)
class StatisticalMeasurements:
    """Canonical detached scientific evidence; never a qualification credential."""

    canonical_json: str

    def __post_init__(self):
        document = self.document()
        if document.get("version") != MEASUREMENT_VERSION or document.get("authorizes_promotion") is not False:
            raise ValueError("Unsupported statistical measurement contract")
        if self.canonical_json != json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False):
            raise ValueError("Statistical measurements must use canonical JSON")

    def document(self) -> dict[str, Any]:
        return json.loads(self.canonical_json)

    @property
    def identity(self) -> str:
        return hashlib.sha256(self.canonical_json.encode()).hexdigest()


def _json_safe(value):
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    return value


def _number_error(value) -> str | None:
    if value is None:
        return "missing"
    if not isinstance(value, Real) or isinstance(value, bool):
        return "invalid_type"
    return None if math.isfinite(value) else "nonfinite"


def _fold_error(folds) -> str | None:
    if folds is None:
        return "missing"
    if not isinstance(folds, (list, tuple)):
        return "invalid_type"
    return next((_number_error(value) for value in folds if _number_error(value)), None)


def _first_error(*errors):
    return next((error for error in errors if error is not None), None)


def _measure_novelty(definition, bars, run, manifest, policy, start):
    novelty = {"status": "no_incumbents"}
    if "incumbents" in manifest and not isinstance(manifest["incumbents"], (list, tuple)):
        return {"status": "unavailable", "reason": "Invalid frozen incumbent snapshot"}
    if manifest.get("incumbents"):
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
                if type(novelty.get("novel")) is not bool or any(
                    not _finite(novelty.get(key)) for key in ("residual_ic", "p_value")
                ):
                    raise ValueError("Invalid incremental predictive evidence")
        except (ValueError, ArithmeticError, TypeError, KeyError) as exc:
            novelty = {"status": "unavailable", "reason": str(exc)}
    return novelty


def measure_statistical_evidence(
    definition, bars, run, manifest, *, policy: ValidationPolicy, bootstrap_seed: int | None = None
) -> StatisticalMeasurements:
    """Run expensive fixed-evidence computations once, independent of family sensitivity.

    The default bootstrap seed preserves qualification behavior. Diagnostic callers
    can explicitly bind a separate predeclared seed without changing live policy.
    """
    if run.get("policy") != asdict(policy):
        raise ValueError("Qualification policy differs from frozen discovery; do not reinterpret old evidence")
    recursive = any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id.lower() == "ema"
        for node in ast.walk(compile_expression(definition.expression).tree)
    )
    trial = next(
        t for t in run["trials"] if AlphaDefinition.from_dict(t["definition"]).version_id == definition.version_id
    )
    metrics = trial.get("candidate", {}).get("metrics", {})
    evidence = trial.get("candidate", {}).get("evidence", {})
    start = run["holdout_start"] + policy.embargo_bars + policy.label_horizon
    selected_seed = run["seed"] if bootstrap_seed is None else bootstrap_seed
    if not isinstance(selected_seed, Integral) or isinstance(selected_seed, bool) or selected_seed < 0:
        raise ValueError("Bootstrap seed must be a nonnegative integer")
    selected_seed = int(selected_seed)
    holdout: dict[str, Any] = {
        "summary": {},
        "simulation_error": None,
        "bootstrap_error": None,
        "bootstrap_mean_interval": None,
        "stressed_return_pct": None,
    }
    try:
        result = simulate_strategy(definition, bars, start=start)
        stress = simulate_strategy(
            replace(
                definition,
                execution=replace(definition.execution, friction_per_side=definition.execution.friction_per_side * 2),
            ),
            bars,
            start=start,
        )
        holdout["summary"] = {k: v for k, v in result.items() if k not in ("net_returns", "trades", "entries")}
        holdout["stressed_return_pct"] = stress.get("total_return_pct")
        for key in (*SAMPLING_FIELDS, "sharpe", "total_trades", "max_drawdown_pct", "total_return_pct"):
            if error := _number_error(result.get(key)):
                raise ValueError(f"Invalid holdout {key}: {error}")
        if error := _number_error(stress.get("total_return_pct")):
            raise ValueError(f"Invalid stressed return: {error}")
    except (ValueError, ArithmeticError, TypeError, KeyError) as exc:
        holdout["simulation_error"] = str(exc)
    else:
        try:
            confidence = block_bootstrap_mean(result["net_returns"], seed=selected_seed)
            if len(confidence) != 2 or any(not _finite(value) for value in confidence) or confidence[0] > confidence[1]:
                raise ValueError("Invalid bootstrap mean interval")
            holdout["bootstrap_mean_interval"] = confidence
        except (ValueError, ArithmeticError, TypeError, KeyError) as exc:
            holdout["bootstrap_error"] = str(exc)
    document = {
        "version": MEASUREMENT_VERSION,
        "authorizes_promotion": False,
        "definition": definition.to_dict(),
        "dataset_hash": frame_digest(bars),
        "manifest": manifest,
        "policy": asdict(policy),
        "seed": run["seed"],
        "bootstrap_seed": selected_seed,
        "holdout_start": run["holdout_start"],
        "evaluation_start": start,
        "recursive_feature": recursive,
        "validation_metrics": metrics,
        "validation_errors": {key: error for key, value in metrics.items() if (error := _number_error(value))},
        "validation_evidence": evidence,
        "validation_fold_error": _fold_error(evidence.get("fold_sharpes")),
        "holdout_measurement": holdout,
        "novelty": _measure_novelty(definition, bars, run, manifest, policy, start),
    }
    return StatisticalMeasurements(
        json.dumps(_json_safe(document), sort_keys=True, separators=(",", ":"), allow_nan=False)
    )


def _criterion(value, threshold, comparison, passed, *, error=None, reason_code=None):
    return {
        "value": _json_safe(value),
        "threshold": threshold,
        "comparison": comparison,
        "status": "unavailable" if error is not None else "pass" if passed else "fail",
        "passed": None if error is not None else bool(passed),
        "unavailable_reason": error,
        "reason_code": reason_code if error is not None or not passed else None,
    }


def _family_dsr(metrics, family, errors=None):
    for key in SAMPLING_FIELDS:
        if error := (errors or {}).get(key) or _number_error(metrics.get(key)):
            raise ValueError(f"Invalid Sharpe sampling evidence: {key} ({error})")
    return calculate_deflated_sharpe_ratio(
        metrics["per_bar_sharpe"],
        family.get("trial_count"),
        family.get("trial_variance"),
        metrics["sample_length"],
        metrics["skewness"],
        metrics["kurtosis"],
    )


def assess_statistical_measurements(measurements: StatisticalMeasurements, family, *, policy: ValidationPolicy):
    """Apply unchanged thresholds; only the two family DSR calculations are repeated."""
    document = measurements.document()
    if document["policy"] != asdict(policy):
        raise ValueError("Assessment policy differs from frozen measurements")
    definition = AlphaDefinition.from_dict(document["definition"])
    manifest, metrics = document["manifest"], document["validation_metrics"]
    evidence, holdout = document["validation_evidence"], document["holdout_measurement"]
    reasons: list[str] = []
    criteria: dict[str, Any] = {}
    for code, observed_contract, expected_contract in (
        ("intraday_session_execution_unverified", definition.timeframe, "1d"),
        ("recursive_feature_requires_shared_initialization", document["recursive_feature"], False),
    ):
        criteria[code] = _criterion(
            observed_contract, expected_contract, "eq", observed_contract == expected_contract, reason_code=code
        )
        if observed_contract != expected_contract:
            reasons.append(code)
    for key, minimum in (
        ("sharpe_oos", policy.min_sharpe),
        ("dsr", policy.min_dsr),
        ("rank_ic_mean", policy.min_ic),
        ("total_trades", policy.minimum_trades),
    ):
        code = f"validation_{key}"
        error = document["validation_errors"].get(key) or _number_error(metrics.get(key))
        passed = not error and metrics[key] >= minimum
        criteria[code] = _criterion(
            None if error else metrics[key], minimum, "ge", passed, error=error, reason_code=code
        )
        if not passed:
            reasons.append(code)
    folds = evidence.get("fold_sharpes")
    fold_error = document["validation_fold_error"]
    folds_pass = not fold_error and len(folds) >= policy.folds and all(value > 0 for value in folds)
    criteria["unstable_validation_folds"] = _criterion(
        folds,
        {"minimum_folds": policy.folds, "each_sharpe_gt": 0},
        "all",
        folds_pass,
        error=fold_error,
        reason_code="unstable_validation_folds",
    )
    if not folds_pass:
        reasons.append("unstable_validation_folds")
    family_dsr, family_error = None, None
    try:
        family_dsr = _family_dsr(metrics, family, document["validation_errors"])
    except (ValueError, TypeError, ArithmeticError) as exc:
        family_error = str(exc)
        reasons.append("invalid_validation_sharpe_evidence")
    criteria["family_adjusted_validation_dsr"] = _criterion(
        family_dsr,
        policy.min_dsr,
        "ge",
        family_dsr is not None and family_dsr >= policy.min_dsr,
        error=family_error,
        reason_code="family_adjusted_validation_dsr",
    )
    if family_dsr is None or family_dsr < policy.min_dsr:
        reasons.append("family_adjusted_validation_dsr")
    dsr, dsr_error = None, None
    if holdout["simulation_error"] is None:
        try:
            dsr = _family_dsr(holdout["summary"], family)
        except (ValueError, TypeError, ArithmeticError) as exc:
            dsr_error = str(exc)
    measurement_error = _first_error(holdout["simulation_error"], holdout["bootstrap_error"])
    holdout_error = _first_error(holdout["simulation_error"], dsr_error, holdout["bootstrap_error"])
    criteria["holdout_available"] = _criterion(
        measurement_error is None,
        True,
        "eq",
        measurement_error is None,
        error=measurement_error,
        reason_code=f"holdout_unavailable:{measurement_error}" if measurement_error is not None else None,
    )
    summary = holdout["summary"]
    interval = holdout["bootstrap_mean_interval"]
    gates: tuple[tuple[str, Any, float, str], ...] = (
        ("holdout_sharpe", summary.get("sharpe"), policy.min_sharpe, "ge"),
        ("holdout_trade_count", summary.get("total_trades"), policy.minimum_trades, "ge"),
        ("holdout_dsr", dsr, policy.min_dsr, "ge"),
        ("holdout_drawdown", summary.get("max_drawdown_pct"), policy.max_drawdown_pct, "le"),
        ("cost_stress", holdout["stressed_return_pct"], 0, "gt"),
        ("bootstrap_uncertainty", interval[0] if interval else None, 0, "gt"),
    )
    for code, value, threshold, comparison in gates:
        error = _first_error(
            holdout["simulation_error"],
            dsr_error if code == "holdout_dsr" else None,
            holdout["bootstrap_error"] if code == "bootstrap_uncertainty" else None,
            _number_error(value),
        )
        passed = error is None and (
            value >= threshold
            if comparison == "ge"
            else value <= threshold
            if comparison == "le"
            else value > threshold
        )
        criteria[code] = _criterion(value, threshold, comparison, passed, error=error, reason_code=code)
        if holdout_error is None and not passed:
            reasons.append(code)
    if holdout_error is not None:
        reasons.append(f"holdout_unavailable:{holdout_error}")
        summary = {}
    else:
        summary = {
            **summary,
            "dsr": dsr,
            "bootstrap_mean_interval": tuple(interval),
            "stressed_return_pct": holdout["stressed_return_pct"],
        }
    novelty = document["novelty"]
    snapshot_available = isinstance(manifest.get("incumbents"), list)
    criteria["frozen_incumbent_snapshot"] = _criterion(
        snapshot_available,
        True,
        "eq",
        snapshot_available,
        error=None if snapshot_available else "missing" if "incumbents" not in manifest else "invalid_type",
        reason_code="missing_frozen_incumbent_snapshot",
    )
    novelty_error = novelty.get("reason") if novelty.get("status") == "unavailable" else None
    novelty_pass = novelty.get("status") == "no_incumbents" or novelty.get("novel") is True
    criteria["incremental_predictive_evidence"] = _criterion(
        novelty,
        {"novel": True, "or_status": "no_incumbents"},
        "any",
        novelty_pass,
        error=novelty_error,
        reason_code="incremental_evidence_unavailable"
        if novelty_error is not None
        else "no_incremental_predictive_evidence",
    )
    if "incumbents" not in manifest:
        reasons.append("missing_frozen_incumbent_snapshot")
    elif novelty_error is not None:
        reasons.append("incremental_evidence_unavailable")
    elif not novelty_pass:
        reasons.append("no_incremental_predictive_evidence")
    return {
        "passed": not reasons,
        "reasons": reasons,
        "policy": asdict(policy),
        "holdout": summary,
        "eligible_symbols": list(definition.eligible_symbols or ()),
        "manifest": manifest,
        "family": _json_safe(family),
        "family_validation_dsr": family_dsr,
        "novelty": novelty,
        "calibration": evidence.get("calibration"),
        "criteria": criteria,
    }


def assess_statistical_evidence(definition, bars, run, manifest, family, *, policy: ValidationPolicy):
    """Scientific assessment only; qualification additionally checks deployment provenance."""
    measurements = measure_statistical_evidence(definition, bars, run, manifest, policy=policy)
    return assess_statistical_measurements(measurements, family, policy=policy)


def assess_qualification(definition, bars, run, manifest, family, *, policy: ValidationPolicy):
    """Combine scientific evidence with the required deployment data contract."""
    result = assess_statistical_evidence(definition, bars, run, manifest, family, policy=policy)
    reasons = result["reasons"]
    # Feed equivalence is mandatory for execution eligibility. Yfinance runs are
    # discovery evidence and require revalidation on the deployment feed.
    deployment_mismatch = (
        manifest["feed"] not in ("alpaca:iex", "alpaca:sip")
        or manifest["adjustment"] != "raw"
        or definition.data_feed != manifest["feed"]
        or definition.adjustment != manifest["adjustment"]
    )
    if deployment_mismatch:
        reasons.append("deployment_data_contract_mismatch")
    result["criteria"]["deployment_data_contract"] = _criterion(
        {
            "feed": manifest["feed"],
            "adjustment": manifest["adjustment"],
            "definition_feed": definition.data_feed,
            "definition_adjustment": definition.adjustment,
        },
        {"allowed_feeds": ["alpaca:iex", "alpaca:sip"], "adjustment": "raw", "matches_definition": True},
        "contract",
        not deployment_mismatch,
        reason_code="deployment_data_contract_mismatch",
    )
    result.pop("passed")
    return {**result, "qualified": not reasons}
