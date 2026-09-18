"""Paired predictor/search/execution/gate diagnosis; no deployment authority."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict
from typing import Any, Final, Literal

import numpy as np
import pandas as pd
from pydantic import Field, model_validator
from scipy.stats import binomtest, spearmanr

from agentic_trader.research.alpha.calibration import SYNTHETIC_FEED
from agentic_trader.research.alpha.miner import AlphaMiner
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.power_trace import execution_diagnostics
from agentic_trader.research.alpha.promotion import (
    BLOCK_BOOTSTRAP_BLOCK,
    BLOCK_BOOTSTRAP_QUANTILES,
    BLOCK_BOOTSTRAP_SAMPLES,
    assess_statistical_measurements,
    measure_statistical_evidence,
)
from agentic_trader.research.alpha.strategy import alpha_scores, entry_directions
from agentic_trader.research.alpha.study import (
    DAILY_NOISE,
    INTRABAR_STEPS,
    LOG_VOL_NOISE,
    SEARCH_METHODS,
    FrozenModel,
    MarketScenario,
    StudyJob,
    StudyPhase,
    StudyStatus,
    market_bars,
    study_catalog,
    study_seed,
)
from agentic_trader.research.alpha.validation import ValidationPolicy


POWER_SCHEMA: Final = "alpha_power_diagnosis_v1"
ROUTES = ("known", "winner")
FAMILIES = ("local", "count_only", "variance_only", "historical", "current")
PROFILES = {"dense": (8, 0.004, 0.0), "sparse": (20, 0.02, 0.8)}


def digest(document):
    return hashlib.sha256(json.dumps(document, sort_keys=True, allow_nan=False).encode()).hexdigest()


class FamilySnapshot(FrozenModel):
    schema_version: Literal[1] = 1
    source: Literal["alpha_projections_single_select"] = "alpha_projections_single_select"
    captured_at: str
    source_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    timeframe: Literal["1d"] = "1d"
    return_timeline: str = ValidationPolicy().return_timeline
    global_count: int = Field(ge=1)
    sharpes: tuple[float, ...] = Field(max_length=100_000)
    projection_events: dict[str, int]

    @model_validator(mode="after")
    def sourced(self):
        at = pd.Timestamp(self.captured_at)
        key = f"family/1d/{self.return_timeline}"
        if (
            pd.isna(at)
            or at.tzinfo is None
            or self.return_timeline != ValidationPolicy().return_timeline
            or "family/all" not in self.projection_events
            or set(self.projection_events) - {"family/all", key}
            or any(value <= 0 for value in self.projection_events.values())
            or (self.sharpes and key not in self.projection_events)
        ):
            raise ValueError("Invalid source family snapshot provenance")
        return self

    @property
    def identity(self):
        return digest(self.model_dump(mode="json"))


class PowerProtocol(FrozenModel):
    schema_version: Literal["alpha_power_diagnosis_v1"] = POWER_SCHEMA
    seed: int = Field(ge=0, lt=2**128)
    observations: int = Field(default=2500, ge=600, le=5000)
    development_replicates: int = Field(default=4, ge=1, le=8)
    null_replicates: int = Field(default=128, ge=1, le=256)
    edge_replicates: int = Field(default=64, ge=1, le=128)
    generated_candidates: int = Field(default=8, ge=1, le=16)
    search_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    historical_trials: int = Field(default=7049, ge=1, le=100_000_000)
    historical_variance: float = Field(default=0.0028764548818829777, ge=0)
    family_snapshot_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    confidence: float = Field(default=0.95, gt=0, lt=1)
    null_upper: float = Field(default=0.05, gt=0, lt=1)
    dense_lower: float = Field(default=0.80, gt=0, lt=1)
    sparse_lower: float = Field(default=0.50, gt=0, lt=1)

    @model_validator(mode="after")
    def bounded(self):
        if len(list(power_jobs(self))) * self.trial_budget * self.observations > 100_000_000:
            raise ValueError("Power diagnostic trial-bar budget exceeded")
        return self

    @property
    def trial_budget(self):
        return len(study_catalog().list_alphas()) + self.generated_candidates

    def document(self):
        jobs = list(power_jobs(self))
        return {
            **self.model_dump(mode="json"),
            "contracts": {
                "policy": asdict(ValidationPolicy()),
                "catalog": [d.to_dict() for d in study_catalog().list_alphas()],
                "profiles": PROFILES,
                "methods": SEARCH_METHODS,
                "routes": ROUTES,
                "families": FAMILIES,
                "generator": {
                    "intrabar_steps": INTRABAR_STEPS,
                    "daily_noise": DAILY_NOISE,
                    "log_vol_noise": LOG_VOL_NOISE,
                },
                "bootstrap": {
                    "method": "moving_block_mean",
                    "samples": BLOCK_BOOTSTRAP_SAMPLES,
                    "block": BLOCK_BOOTSTRAP_BLOCK,
                    "quantiles": BLOCK_BOOTSTRAP_QUANTILES,
                    "seed": "independent_bootstrap_role_paired_across_effects_and_methods",
                },
                "cost_stress_multiplier": 2,
                "selection": "unchanged_miner_composite_before_holdout",
                "primary": "full_policy_current_family_each_profile_effect_method_route",
                "multiplicity": "bonferroni_one_sided_exact_binomial_16_endpoints",
                "counterfactual": "append_this_search_once_to_frozen_family_each_replicate_independent",
                "diagnostic_only": True,
            },
            "budget": {
                "datasets": len(jobs) // len(SEARCH_METHODS),
                "searches": len(jobs),
                "expression_evaluations": len(jobs) * self.trial_budget,
                "route_evaluations_max": len(jobs) * len(ROUTES),
                "holdout_simulations_max": len(jobs) * len(ROUTES) * 3,
                "bootstrap_resamples_max": len(jobs) * len(ROUTES) * BLOCK_BOOTSTRAP_SAMPLES,
                "family_assessments_max": len(jobs) * len(ROUTES) * len(FAMILIES),
            },
        }

    @property
    def identity(self):
        return digest(self.document())

    @classmethod
    def from_document(cls, document):
        protocol = cls.model_validate_json(
            json.dumps({k: v for k, v in document.items() if k not in ("contracts", "budget")})
        )
        if digest(protocol.document()) != digest(document):
            raise ValueError("Frozen power contracts differ from current implementation")
        return protocol


def power_jobs(protocol):
    for phase in StudyPhase:
        for profile, (interval, positive, persistence) in PROFILES.items():
            for effect in (0.0, positive):
                scenario = MarketScenario(
                    name=profile,
                    observations=protocol.observations,
                    interval=interval,
                    effect=effect,
                    volatility_persistence=persistence,
                )
                count = (
                    protocol.development_replicates
                    if phase == StudyPhase.DEVELOPMENT
                    else (protocol.null_replicates if effect == 0 else protocol.edge_replicates)
                )
                for method in SEARCH_METHODS:
                    for replicate in range(count):
                        yield StudyJob(phase, "search", scenario, effect, replicate, method)


def power_seeds(job, protocol):
    # Effects and search methods share data; phase/profile/replicate/role are disjoint.
    return {
        role: study_seed(protocol.seed, job.phase, f"{POWER_SCHEMA}/{job.scenario.name}", job.replicate, role)
        for role in ("data", f"search/{job.method}", "bootstrap")
    }


def select_power_candidates(bars, protocol, *, search_seed, method):
    miner = AlphaMiner(seed=search_seed, catalog=study_catalog(), policy=ValidationPolicy())
    ranked = miner.mine(
        bars,
        iterations=protocol.generated_candidates,
        symbol="SYNTH",
        method=method,
        min_sharpe=-math.inf,
        min_dsr=0,
        min_ic=-math.inf,
        max_seconds=protocol.search_timeout_seconds,
    )
    run = miner.last_run
    known: dict[str, Any] = next((t for t in run["trials"] if t["definition"]["alpha_id"] == "synthetic_pulse"), {})
    return {
        "run": run,
        "selected_version": ranked[0].definition.version_id if ranked else None,
        "known_discovery_rank": next(
            (i + 1 for i, c in enumerate(ranked) if c.definition.alpha_id == "synthetic_pulse"), None
        ),
        "routes": {"known": known.get("candidate"), "winner": ranked[0].to_dict() if ranked else None},
        "authorizes_promotion": False,
    }


def family_scenarios(run, protocol, snapshot):
    if (snapshot.identity if snapshot else None) != protocol.family_snapshot_hash:
        raise ValueError("Family snapshot differs from frozen protocol")
    sharpes = [t["candidate"]["metrics"]["per_bar_sharpe"] for t in run["trials"] if t.get("candidate")]
    variance = float(np.var(sharpes, ddof=1)) if len(sharpes) > 1 else 0.0
    count = run["trial_count"]
    families = {
        "local": {"trial_count": count, "trial_variance": variance},
        "count_only": {"trial_count": protocol.historical_trials + count, "trial_variance": variance},
        "variance_only": {"trial_count": count, "trial_variance": protocol.historical_variance},
        "historical": {
            "trial_count": protocol.historical_trials + count,
            "trial_variance": protocol.historical_variance,
        },
        "current": None,
    }
    if snapshot:
        combined = [*snapshot.sharpes, *sharpes]
        families["current"] = {
            "trial_count": snapshot.global_count + count,
            "trial_variance": float(np.var(combined, ddof=1)) if len(combined) > 1 else None,
            "variance_observations": len(combined),
            "prior_variance_observations": len(snapshot.sharpes),
            "snapshot_hash": snapshot.identity,
        }
    return families


def predictor_diagnostics(definition, bars, *, start):
    """One-step rank evidence and constant-exposure comparison, not fill P&L."""
    decision_start = max(0, start - 1)
    scores = alpha_scores(definition, bars).iloc[decision_start:-1]
    target = (bars.close.shift(-1) / bars.close - 1).iloc[decision_start:-1]
    opened = (bars.close / bars.open - 1).shift(-1).iloc[decision_start:-1]
    valid = scores.notna() & target.notna()
    score, forward = scores[valid], target[valid]
    correlation = float(spearmanr(score, forward).statistic) if score.nunique() > 1 and forward.nunique() > 1 else None
    directions = entry_directions(alpha_scores(definition, bars), definition).iloc[decision_start:-1]
    action = directions[valid].astype(float)
    payoff = opened[valid]
    if not len(score):
        return {
            "observations": 0,
            "expected_observations": len(scores),
            "rank_ic": None,
            "status": "unavailable",
            "reason": "No finite predictor/outcome pairs",
        }
    return {
        "decision_start": decision_start,
        "target_start": decision_start + 1,
        "target_end_exclusive": len(bars),
        "observations": len(score),
        "expected_observations": len(scores),
        "rank_ic": correlation,
        "label": "next_close_over_close_minus_one",
        "overlap": False,
        "annualized": False,
        "action_fraction": float((action != 0).mean()),
        "mean_next_open_close_action_payoff": float((action * payoff).mean()),
        "constant_exposure_payoff": float(action.mean() * payoff.mean()),
        "incremental_payoff": float((action * payoff).mean() - action.mean() * payoff.mean()),
        "payoff_scope": "synthetic_next_open_close_bar_proxy_no_fills_or_fees",
    }


def power_endpoint_rows(job, protocol):
    return [
        {
            "phase": str(job.phase),
            "profile": job.scenario.name,
            "effect": job.effect,
            "method": job.method,
            "route": route,
            "family": family,
            "primary": family == "current",
            "accepted": None,
        }
        for route in ROUTES
        for family in FAMILIES
    ]


def drop_one_criteria(criteria):
    if any(c["passed"] is None for c in criteria.values()):
        return dict.fromkeys(criteria, None)
    return {name: all(c["passed"] is True for key, c in criteria.items() if key != name) for name in criteria}


def evaluate_power_job(job, protocol, snapshot, *, checkpoint):
    seeds = power_seeds(job, protocol)
    bars = market_bars(job.scenario, seed=seeds["data"])
    selected = select_power_candidates(bars, protocol, search_seed=seeds[f"search/{job.method}"], method=job.method)
    checkpoint(selected)  # Durable winner/trial history before any holdout access.
    run = selected["run"]
    result = {
        "job_id": job.identity,
        "job_key": job.key,
        "protocol_id": protocol.identity,
        "status": StudyStatus.COMPLETED,
        "seeds": seeds,
        "rows": power_endpoint_rows(job, protocol),
        "selection": selected,
        "routes": {},
        "synthetic_only": True,
        "authorizes_promotion": False,
    }
    if run["status"] != "completed" or run["trial_count"] != protocol.trial_budget:
        result.update(status=StudyStatus.FAILED, error="Incomplete discovery; declared budget retained")
        return result
    families = family_scenarios(run, protocol, snapshot)
    policy = ValidationPolicy()
    start = run["holdout_start"] + policy.label_horizon + policy.embargo_bars
    cache: dict[str, dict[str, Any]] = {}
    for route, candidate in selected["routes"].items():
        detail: dict[str, Any] = {"status": StudyStatus.COMPLETED}
        result["routes"][route] = detail
        try:
            if candidate is None:
                raise ValueError("No evaluated candidate on this route")
            definition = AlphaDefinition.from_dict(candidate["definition"])
            if definition.version_id in cache:
                detail = cache[definition.version_id]
                result["routes"][route] = detail
            else:
                cache[definition.version_id] = detail
                detail["definition"] = definition.to_dict()
                measurement = measure_statistical_evidence(
                    definition,
                    bars,
                    run,
                    {"feed": SYNTHETIC_FEED, "adjustment": "raw", "incumbents": []},
                    policy=policy,
                    bootstrap_seed=seeds["bootstrap"],
                )
                # Retain completed evidence even if a later assessment/trace fails.
                detail.update(measurement=measurement.document(), measurement_hash=measurement.identity)
                compared: dict[str, Any] = {}
                detail["assessments"] = compared
                for name, family in families.items():
                    if family is None:
                        compared[name] = {
                            "status": "unavailable",
                            "reason": "No sourced current family snapshot",
                            "passed": None,
                        }
                        continue
                    assessment = assess_statistical_measurements(measurement, family, policy=policy)
                    compared[name] = {**assessment, "drop_one": drop_one_criteria(assessment["criteria"])}
                    if any(c["passed"] is None for c in assessment["criteria"].values()):
                        detail.update(status=StudyStatus.FAILED, error="Unavailable required scientific measurement")
                detail["predictor"] = predictor_diagnostics(definition, bars, start=start)
                detail["execution"] = execution_diagnostics(
                    definition, bars, start=start, pulse_mask=bars.volume > 1_000_000
                )
            for row in result["rows"]:
                if row["route"] == route:
                    assessment = detail.get("assessments", {}).get(row["family"], {})
                    criteria = assessment.get("criteria", {})
                    row["accepted"] = (
                        None if any(c["passed"] is None for c in criteria.values()) else assessment.get("passed")
                    )
        except Exception as exc:
            detail.update(status=StudyStatus.FAILED, error=f"{type(exc).__name__}: {exc}")
        if detail["status"] != StudyStatus.COMPLETED:
            result.update(status=StudyStatus.FAILED, error="Incomplete route evidence; declared budget retained")
    return result


def _key(row):
    return tuple(row[k] for k in ("phase", "profile", "effect", "method", "route", "family"))


def summarize_power_study(protocol, records):
    expected = {j.identity: j for j in power_jobs(protocol)}
    supplied = {}
    for record in records:
        key = record["job_id"]
        if key in supplied or key not in expected or record["protocol_id"] != protocol.identity:
            raise ValueError("Duplicate, unknown or mismatched power study record")
        supplied[key] = record
    groups = defaultdict(list)
    for identity, job in expected.items():
        rows = power_endpoint_rows(job, protocol)
        record = supplied.get(identity)
        if record and record["status"] == StudyStatus.COMPLETED:
            observed = {_key(r): r for r in record["rows"]}
            if (
                len(observed) != len(rows)
                or len(record["rows"]) != len(rows)
                or set(observed) != {_key(r) for r in rows}
            ):
                raise ValueError("Missing or duplicate power endpoints")
            for row in rows:
                value = observed[_key(row)]["accepted"]
                if value is not None and type(value) is not bool:
                    raise ValueError("Power outcome requires Boolean or explicit unavailable")
                row["accepted"] = value
        for row in rows:
            groups[_key(row)].append(row)
    primary_count = sum(rows[0]["phase"] == StudyPhase.VALIDATION and rows[0]["primary"] for rows in groups.values())
    summaries = []
    for rows in groups.values():
        row = rows[0]
        descriptor = {k: v for k, v in row.items() if k != "accepted"}
        primary = row["phase"] == StudyPhase.VALIDATION and row["primary"]
        confidence = 1 - (1 - protocol.confidence) / primary_count if primary else protocol.confidence
        n = len(rows)
        accepted = sum(r["accepted"] is True for r in rows)
        missing = sum(r["accepted"] is None for r in rows)
        lower = float(binomtest(accepted, n, alternative="greater").proportion_ci(confidence_level=confidence).low)
        upper = float(
            binomtest(accepted + missing, n, alternative="less").proportion_ci(confidence_level=confidence).high
        )
        passes = (
            upper <= protocol.null_upper
            if row["effect"] == 0
            else lower >= getattr(protocol, f"{row['profile']}_lower")
        )
        summaries.append(
            {
                **descriptor,
                "replicates": n,
                "accepted": accepted,
                "unavailable": missing,
                "rate": None if missing else accepted / n,
                "lower": lower,
                "upper": upper,
                "confidence": confidence,
                "criterion_passed": bool(passes and not missing) if primary else None,
            }
        )
    failed = sum(r["status"] != StudyStatus.COMPLETED for r in records)
    unavailable = sum(r["unavailable"] for r in summaries)
    complete = len(supplied) == len(expected) and not failed and not unavailable
    passes = all(r["criterion_passed"] for r in summaries if r["criterion_passed"] is not None)
    cofailing: Counter[tuple] = Counter()
    gate_counts: dict[tuple, Counter] = defaultdict(Counter)
    for record in records:
        if record.get("status") != StudyStatus.COMPLETED:
            continue
        for route, detail in record.get("routes", {}).items():
            current = detail.get("assessments", {}).get("current", {})
            criteria = current.get("criteria", {})
            if criteria:
                job = expected[record["job_id"]]
                context = (str(job.phase), job.scenario.name, job.effect, job.method, route)
                failed_names = tuple(sorted(k for k, c in criteria.items() if c["passed"] is False))
                missing_names = tuple(sorted(k for k, c in criteria.items() if c["passed"] is None))
                cofailing[(*context, failed_names, missing_names)] += 1
                for name, criterion in criteria.items():
                    counts = gate_counts[(*context, name)]
                    counts[criterion["status"]] += 1
                    counts["observed_replicates"] += 1
                    counts["drop_one_passes"] += current["drop_one"][name] is True
    return {
        "protocol_id": protocol.identity,
        "synthetic_only": True,
        "authorizes_promotion": False,
        "status": StudyStatus.INCOMPLETE
        if not complete
        else StudyStatus.CRITERIA_PASSED
        if passes
        else StudyStatus.CRITERIA_NOT_MET,
        "expected_jobs": len(expected),
        "recorded_jobs": len(records),
        "missing_jobs": len(expected) - len(records),
        "failed_jobs": failed,
        "unavailable_endpoints": unavailable,
        "primary_endpoints": primary_count,
        "summaries": summaries,
        "cofailures": [
            {
                **dict(zip(("phase", "profile", "effect", "method", "route"), key[:5], strict=True)),
                "criteria": list(key[5]),
                "unavailable_criteria": list(key[6]),
                "count": n,
            }
            for key, n in sorted(cofailing.items())
        ],
        "gate_counts": [
            {**dict(zip(("phase", "profile", "effect", "method", "route", "criterion"), key, strict=True)), **counts}
            for key, counts in sorted(gate_counts.items())
        ],
    }
