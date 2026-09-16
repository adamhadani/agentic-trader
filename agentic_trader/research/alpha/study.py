"""Predeclared synthetic comparisons. Pure computation; never deployment evidence."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Final, Literal

import numpy as np
import pandas as pd
from arch.bootstrap import SPA, StationaryBootstrap
from pydantic import BaseModel, ConfigDict, Field, model_validator
from scipy.stats import binomtest

from agentic_trader.research.alpha.calibration import SYNTHETIC_FEED, joint_block_max_test
from agentic_trader.research.alpha.catalog import AlphaCatalog
from agentic_trader.research.alpha.miner import AlphaMiner
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.promotion import assess_statistical_evidence
from agentic_trader.research.alpha.simulation import simulate_strategy
from agentic_trader.research.alpha.validation import ValidationPolicy


STUDY_SCHEMA: Final = 1
PRIMARY_BLOCK = 20
TEST_LEVEL = 0.05
MAX_STUDY_JOBS = 5000
MAX_SEARCH_TRIAL_BARS = 100_000_000
MAX_STUDY_RESAMPLED_VALUES = 50_000_000_000
INTRABAR_STEPS = 4
DAILY_NOISE = 0.001
LOG_VOL_NOISE = 0.25
SEARCH_METHODS = ("random", "genetic")
ACCEPTANCE_BOUNDS = {"null": 0.05, "dense": 0.80, "sparse": 0.50, "weak": 0.80}


class StudyPhase(StrEnum):
    DEVELOPMENT = "development"
    VALIDATION = "validation"


class StudyStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    INCOMPLETE = "incomplete"
    CRITERIA_PASSED = "criteria_passed"
    CRITERIA_NOT_MET = "criteria_not_met"


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False)


class MarketScenario(FrozenModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,40}$")
    observations: int = Field(ge=600, le=5000)
    interval: int = Field(ge=4, le=60)
    effect: float = Field(ge=0, le=0.02)
    volatility_persistence: float = Field(ge=0, le=0.9)


class PanelScenario(FrozenModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,40}$")
    observations: int = Field(ge=80, le=2000)
    serial_correlation: float = Field(ge=0, le=0.9)
    cross_correlation: float = Field(ge=0, le=0.9)


class StudyProtocol(FrozenModel):
    schema_version: Literal[1] = STUDY_SCHEMA
    seed: int = Field(default=730241884, ge=0, lt=2**128)
    development_search_replicates: int = Field(default=8, ge=1, le=32)
    development_panel_replicates: int = Field(default=16, ge=1, le=64)
    validation_null_search_replicates: int = Field(default=128, ge=1, le=256)
    validation_edge_search_replicates: int = Field(default=64, ge=1, le=256)
    validation_null_panel_replicates: int = Field(default=256, ge=1, le=512)
    validation_edge_panel_replicates: int = Field(default=128, ge=1, le=512)
    generated_candidates: int = Field(default=8, ge=1, le=32)
    bootstrap_samples: int = Field(default=199, ge=99, le=999)
    block_lengths: tuple[int, ...] = (5, 10, PRIMARY_BLOCK)
    panel_candidates: int = Field(default=8, ge=2, le=32)
    panel_effects: tuple[float, ...] = (0.0, 0.15, 0.30)
    historical_trials: int = Field(default=7049, ge=1, le=100_000_000)
    historical_variance: float = Field(default=0.0028764548818829777, ge=0)
    search_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    market_scenarios: tuple[MarketScenario, ...] = (
        MarketScenario(name="iid_null", observations=1000, interval=8, effect=0.0, volatility_persistence=0.0),
        MarketScenario(name="clustered_null", observations=1250, interval=20, effect=0.0, volatility_persistence=0.8),
        MarketScenario(name="dense_edge", observations=2500, interval=8, effect=0.004, volatility_persistence=0.0),
        MarketScenario(name="sparse_edge", observations=2500, interval=20, effect=0.02, volatility_persistence=0.8),
    )
    panel_scenarios: tuple[PanelScenario, ...] = (
        PanelScenario(name="independent", observations=504, serial_correlation=0.0, cross_correlation=0.25),
        PanelScenario(name="dependent", observations=1008, serial_correlation=0.5, cross_correlation=0.75),
    )

    @model_validator(mode="after")
    def bounded_protocol(self):
        if (
            not self.block_lengths
            or len(set(self.block_lengths)) != len(self.block_lengths)
            or PRIMARY_BLOCK not in self.block_lengths
            or any(type(b) is not int or not 2 <= b <= 40 for b in self.block_lengths)
            or not self.panel_effects
            or len(set(self.panel_effects)) != len(self.panel_effects)
            or any(not 0 <= e <= 0.5 for e in self.panel_effects)
            or not self.market_scenarios
            or not self.panel_scenarios
            or len({s.name for s in self.market_scenarios}) != len(self.market_scenarios)
            or len({s.name for s in self.panel_scenarios}) != len(self.panel_scenarios)
        ):
            raise ValueError("Distinct bounded scenarios, effects and blocks including primary block 20 required")
        jobs = list(study_jobs(self))
        if len(jobs) > MAX_STUDY_JOBS:
            raise ValueError("Study job budget exceeded")
        search_work = sum(j.observations * self.trial_budget for j in jobs if j.kind == "search")
        panel_work = sum(
            j.observations * self.panel_candidates * self.bootstrap_samples * len(self.block_lengths) * 2
            for j in jobs
            if j.kind == "panel"
        )
        if search_work > MAX_SEARCH_TRIAL_BARS or panel_work > MAX_STUDY_RESAMPLED_VALUES:
            raise ValueError("Study numerical budget exceeded")
        return self

    @property
    def trial_budget(self):
        return len(AlphaCatalog().list_alphas()) + 1 + self.generated_candidates

    def document(self):
        return {
            **self.model_dump(mode="json"),
            "contracts": {
                "qualification_policy": asdict(ValidationPolicy()),
                "catalog": [d.to_dict() for d in study_catalog().list_alphas()],
                "test_level": TEST_LEVEL,
                "primary_block": PRIMARY_BLOCK,
                "intrabar_steps": INTRABAR_STEPS,
                "daily_noise": DAILY_NOISE,
                "log_vol_noise": LOG_VOL_NOISE,
                "search_methods": list(SEARCH_METHODS),
                "selection": "one_top_miner_composite_rank_with_display_filters_disabled",
                "holdout_alternative": "arch_stationary_percentile_lower_95_plus_economic_gates",
                "acceptance": {
                    "null_upper": ACCEPTANCE_BOUNDS["null"],
                    "dense_lower": ACCEPTANCE_BOUNDS["dense"],
                    "sparse_lower": ACCEPTANCE_BOUNDS["sparse"],
                },
            },
        }

    @property
    def identity(self):
        return hashlib.sha256(json.dumps(self.document(), sort_keys=True, allow_nan=False).encode()).hexdigest()

    @classmethod
    def from_document(cls, document):
        if not isinstance(document, dict):
            raise TypeError("A frozen protocol object is required")
        values = {key: value for key, value in document.items() if key != "contracts"}
        protocol = cls.model_validate_json(json.dumps(values, allow_nan=False))
        if document.get("contracts") != protocol.document()["contracts"]:
            raise ValueError("Frozen scientific contracts differ from this implementation")
        return protocol


@dataclass(frozen=True)
class StudyJob:
    phase: StudyPhase
    kind: str
    scenario: MarketScenario | PanelScenario
    effect: float
    replicate: int
    method: str = ""

    @property
    def observations(self):
        return self.scenario.observations

    @property
    def key(self):
        return f"{self.phase}/{self.kind}/{self.scenario.name}/{self.effect}/{self.method}/{self.replicate}"

    @property
    def identity(self):
        return hashlib.sha256(self.key.encode()).hexdigest()


def study_jobs(protocol):
    for phase in StudyPhase:
        for scenario in protocol.market_scenarios:
            count = (
                protocol.development_search_replicates
                if phase == StudyPhase.DEVELOPMENT
                else (
                    protocol.validation_null_search_replicates
                    if scenario.effect == 0
                    else protocol.validation_edge_search_replicates
                )
            )
            for method in SEARCH_METHODS:
                for replicate in range(count):
                    yield StudyJob(phase, "search", scenario, scenario.effect, replicate, method)
        for scenario in protocol.panel_scenarios:
            for effect in protocol.panel_effects:
                count = (
                    protocol.development_panel_replicates
                    if phase == StudyPhase.DEVELOPMENT
                    else (
                        protocol.validation_null_panel_replicates
                        if effect == 0
                        else protocol.validation_edge_panel_replicates
                    )
                )
                for replicate in range(count):
                    yield StudyJob(phase, "panel", scenario, effect, replicate)


def study_seed(root, phase, scenario, replicate, role):
    """128-bit, domain-separated streams; never truncate streams to a 32-bit seed."""
    key = json.dumps(["alpha_study_v1", root, str(phase), scenario, replicate, role])
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:16], "big")


def study_catalog():
    return AlphaCatalog(
        [
            *AlphaCatalog().list_alphas(),
            AlphaDefinition(
                "synthetic_pulse",
                "Synthetic volume control",
                "volume",
                timeframe="1d",
                eligible_symbols=("SYNTH",),
                data_feed=SYNTHETIC_FEED,
            ),
        ]
    )


def market_bars(scenario: MarketScenario, *, seed: int, observations: int | None = None):
    n = scenario.observations if observations is None else observations
    if type(n) is not int or not 600 <= n <= 5000:
        raise ValueError("Bounded synthetic observations required")
    shocks = np.random.default_rng(seed).normal(size=(n, INTRABAR_STEPS + 1))
    pulse = np.zeros(n)
    pulse[np.arange(40, n, scenario.interval)] = 1
    log_vol = np.empty(n)
    rho = scenario.volatility_persistence
    previous = 0.0
    for i in range(n):
        previous = rho * previous + LOG_VOL_NOISE * math.sqrt(1 - rho * rho) * shocks[i, -1]
        log_vol[i] = previous
    sigma = DAILY_NOISE * np.exp(log_vol)
    drift = np.r_[0, pulse[:-1]] * scenario.effect
    increments = (drift[:, None] - sigma[:, None] ** 2 / 2) / INTRABAR_STEPS
    increments = increments + sigma[:, None] / math.sqrt(INTRABAR_STEPS) * shocks[:, :INTRABAR_STEPS]
    path = 100 * np.exp(increments.ravel().cumsum()).reshape(n, INTRABAR_STEPS)
    opens = np.r_[100.0, path[:-1, -1]]
    frame = pd.DataFrame(
        {
            "open": opens,
            "high": np.maximum(opens, path.max(axis=1)),
            "low": np.minimum(opens, path.min(axis=1)),
            "close": path[:, -1],
            "volume": 1_000_000 + pulse * 800_000,
        },
        index=pd.date_range("2010-01-01", periods=n, freq="B", tz="UTC"),
    )
    frame.attrs.update(timeframe="1d", feed=SYNTHETIC_FEED, adjustment="raw", synthetic_control=True)
    return frame


def panel_returns(scenario: PanelScenario, *, seed, candidates, effect):
    shocks = np.random.default_rng(seed).normal(size=(scenario.observations, candidates + 1))
    rho, cross = scenario.serial_correlation, scenario.cross_correlation
    innovations = math.sqrt(cross) * shocks[:, :1] + math.sqrt(1 - cross) * shocks[:, 1:]
    values = np.empty_like(innovations)
    values[0] = innovations[0]
    for i in range(1, len(values)):
        values[i] = rho * values[i - 1] + math.sqrt(1 - rho * rho) * innovations[i]
    values[:, 0] += effect
    return pd.DataFrame(
        values,
        columns=[f"candidate_{i}" for i in range(candidates)],
        index=pd.date_range("2020-01-01", periods=len(values), tz="UTC"),
    )


def panel_comparison(panel, *, samples, block, seed):
    # The shared diagnostic validates timestamps, finite values and numerical budgets.
    joint = joint_block_max_test(panel, samples=samples, block_length=block, seed=seed)
    spa = SPA(
        np.zeros(len(panel)),
        -panel,
        block_size=block,
        reps=samples,
        bootstrap="stationary",
        studentize=True,
        nested=False,
        seed=seed,
    )
    spa.compute()
    result = {"joint_max": min(joint["adjusted_pvalues"].values()), "spa_upper": float(spa.pvalues["upper"])}
    if not all(math.isfinite(p) and 0 <= p <= 1 for p in result.values()):
        raise ValueError("Nonfinite comparison evidence")
    return result


def search_comparison(bars, protocol, *, search_seed, bootstrap_seed, method):
    policy = ValidationPolicy()
    miner = AlphaMiner(seed=search_seed, catalog=study_catalog(), policy=policy)
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
    result = {
        "run": run,
        "reserved_trials": protocol.trial_budget,
        "winner": None,
        "authorizes_promotion": False,
        "outcomes": {},
        "assessments": {},
    }
    if run["status"] != "completed" or run["trial_count"] != protocol.trial_budget:
        result["error"] = f"Incomplete search: {run['status']}"
        return result
    keys = ["current_local", "current_lifetime", *(f"holdout_{b}" for b in protocol.block_lengths)]
    if not ranked:
        result["outcomes"] = dict.fromkeys(keys, False)
        return result
    winner = ranked[0].definition
    result["winner"] = winner.version_id  # Fixed before the first holdout computation.
    variance = ranked[0].evidence["trial_variance"]
    families = {
        "current_local": {"trial_count": protocol.trial_budget, "trial_variance": variance},
        "current_lifetime": {
            "trial_count": protocol.historical_trials + protocol.trial_budget,
            "trial_variance": protocol.historical_variance,
        },
    }
    manifest = {"feed": SYNTHETIC_FEED, "adjustment": "raw", "incumbents": []}
    for name, family in families.items():
        assessment = assess_statistical_evidence(winner, bars, run, manifest, family, policy=policy)
        result["assessments"][name] = assessment
        result["outcomes"][name] = assessment["passed"]
    # This comparator is explicitly distinct from the full policy. No rejection
    # strings are removed and no scientific result becomes a deployment permission.
    holdout = result["assessments"]["current_local"]["holdout"]
    if not holdout:
        result["error"] = "Holdout computation unavailable"
        return result
    eligible = (
        holdout["sharpe"] >= policy.min_sharpe
        and holdout["total_trades"] >= policy.minimum_trades
        and holdout["max_drawdown_pct"] <= policy.max_drawdown_pct
        and holdout["stressed_return_pct"] > 0
    )
    simulation = simulate_strategy(
        winner, bars, start=run["holdout_start"] + policy.embargo_bars + policy.label_horizon
    )
    values = simulation["net_returns"].dropna().to_numpy()
    if len(values) < 2 * max(protocol.block_lengths) or not np.isfinite(values).all():
        result["error"] = "Insufficient finite holdout returns"
        return result
    result["holdout_lower_bounds"] = {}
    for block in protocol.block_lengths:
        boot = StationaryBootstrap(block, values, seed=bootstrap_seed)
        lower = float(
            boot.conf_int(
                np.mean, reps=protocol.bootstrap_samples, method="percentile", size=1 - TEST_LEVEL, tail="lower"
            )[0, 0]
        )
        if not math.isfinite(lower):
            raise ValueError("Invalid holdout bootstrap bound")
        result["holdout_lower_bounds"][str(block)] = lower
        result["outcomes"][f"holdout_{block}"] = bool(eligible and lower > 0)
    return result


def endpoint_rows(job, protocol):
    """Declare every expected endpoint, including those a failed job cannot compute."""
    null = job.effect == 0
    if job.kind == "search":
        target = "null" if null else "sparse" if job.scenario.interval >= 20 else "dense"
        procedures = [("current_local", 0, False), ("current_lifetime", 0, True)] + [
            ("holdout", b, b == PRIMARY_BLOCK) for b in protocol.block_lengths
        ]
    else:
        target = "null" if null else "dense" if job.effect >= 0.30 else "weak"
        procedures = [
            (name, b, b == PRIMARY_BLOCK and target != "weak")
            for name in ("joint_max", "spa_upper")
            for b in protocol.block_lengths
        ]
    return [
        {
            "phase": str(job.phase),
            "kind": job.kind,
            "scenario": job.scenario.name,
            "effect": job.effect,
            "search_method": job.method,
            "procedure": name,
            "block": block,
            "target": target,
            "primary": primary,
            "accepted": None,
        }
        for name, block, primary in procedures
    ]


def evaluate_job(job, protocol):
    # Methods/effects share data. Search and resampling have separate role domains.
    namespace = f"{job.kind}/{job.scenario.name}"
    seeds = {
        role: study_seed(protocol.seed, job.phase, namespace, job.replicate, role)
        for role in ("data", f"search/{job.method}", "bootstrap")
    }
    rows = endpoint_rows(job, protocol)
    detail = {}
    if job.kind == "search":
        bars = market_bars(job.scenario, seed=seeds["data"])
        detail = search_comparison(
            bars,
            protocol,
            search_seed=seeds[f"search/{job.method}"],
            bootstrap_seed=seeds["bootstrap"],
            method=job.method,
        )
        if "error" not in detail:
            for row in rows:
                key = f"holdout_{row['block']}" if row["procedure"] == "holdout" else row["procedure"]
                row["accepted"] = detail["outcomes"][key]
    else:
        panel = panel_returns(job.scenario, seed=seeds["data"], candidates=protocol.panel_candidates, effect=job.effect)
        detail["blocks"] = {}
        for block in protocol.block_lengths:
            compared = panel_comparison(panel, samples=protocol.bootstrap_samples, block=block, seed=seeds["bootstrap"])
            detail["blocks"][str(block)] = compared
            for row in rows:
                if row["block"] == block:
                    row["accepted"] = compared[row["procedure"]] <= TEST_LEVEL
    return {
        "job_id": job.identity,
        "job_key": job.key,
        "protocol_id": protocol.identity,
        "status": StudyStatus.FAILED if "error" in detail else StudyStatus.COMPLETED,
        "seeds": seeds,
        "rows": rows,
        "detail": detail,
        "synthetic_only": True,
        "authorizes_promotion": False,
    }


def _endpoint_key(row):
    return tuple(row[k] for k in ("phase", "kind", "scenario", "effect", "search_method", "procedure", "block"))


def summarize_study(protocol, records):
    expected = {job.identity: job for job in study_jobs(protocol)}
    supplied = {}
    for record in records:
        identity = record["job_id"]
        if identity in supplied or identity not in expected or record["protocol_id"] != protocol.identity:
            raise ValueError("Duplicate, unknown or mismatched study evidence")
        supplied[identity] = record
    groups = defaultdict(list)
    for identity, job in expected.items():
        rows = endpoint_rows(job, protocol)
        record = supplied.get(identity)
        if record and record["status"] == StudyStatus.COMPLETED:
            observed = {_endpoint_key(r): r for r in record["rows"]}
            if (
                len(record["rows"]) != len(rows)
                or len(observed) != len(rows)
                or set(observed) != {_endpoint_key(r) for r in rows}
            ):
                raise ValueError("Missing or duplicate endpoint evidence")
            for row in rows:
                value = observed[_endpoint_key(row)]["accepted"]
                if type(value) is not bool:
                    raise ValueError("Completed endpoint requires a Boolean decision")
                row["accepted"] = value
        for row in rows:
            groups[_endpoint_key(row)].append(row)
    primary_count = sum(rows[0]["primary"] and rows[0]["phase"] == StudyPhase.VALIDATION for rows in groups.values())
    summaries = []
    for rows in groups.values():
        descriptor = {k: v for k, v in rows[0].items() if k != "accepted"}
        count = len(rows)
        accepted = sum(r["accepted"] is True for r in rows)
        unavailable = sum(r["accepted"] is None for r in rows)
        primary = descriptor["primary"] and descriptor["phase"] == StudyPhase.VALIDATION
        confidence = 1 - TEST_LEVEL / primary_count if primary else 1 - TEST_LEVEL
        # Treat unavailable decisions pessimistically in bounds, and always make
        # the endpoint incomplete. Never delete failed trials from denominators.
        lower = float(binomtest(accepted, count, alternative="greater").proportion_ci(confidence_level=confidence).low)
        upper = float(
            binomtest(accepted + unavailable, count, alternative="less").proportion_ci(confidence_level=confidence).high
        )
        target = descriptor["target"]
        threshold = ACCEPTANCE_BOUNDS[target]
        passes = upper <= threshold if target == "null" else lower >= threshold
        summaries.append(
            {
                **descriptor,
                "replicates": count,
                "accepted": accepted,
                "unavailable": unavailable,
                "rate": None if unavailable else accepted / count,
                "lower": lower,
                "upper": upper,
                "confidence": confidence,
                "criterion_passed": bool(passes and not unavailable) if primary else None,
            }
        )
    missing = len(expected) - len(supplied)
    failed = sum(r["status"] != StudyStatus.COMPLETED for r in supplied.values())
    passed = all(r["criterion_passed"] for r in summaries if r["criterion_passed"] is not None)
    return {
        "protocol_id": protocol.identity,
        "synthetic_only": True,
        "authorizes_promotion": False,
        "status": StudyStatus.INCOMPLETE
        if missing or failed
        else StudyStatus.CRITERIA_PASSED
        if passed
        else StudyStatus.CRITERIA_NOT_MET,
        "expected_jobs": len(expected),
        "recorded_jobs": len(supplied),
        "missing_jobs": missing,
        "failed_jobs": failed,
        "primary_endpoints": primary_count,
        "summaries": summaries,
    }
