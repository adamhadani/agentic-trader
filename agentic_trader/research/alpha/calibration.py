"""Bounded synthetic calibration and fixed-panel diagnostics; no I/O or activation.

Joint circular-block resampling centers each candidate under the null and samples
one row-block sequence for the whole panel. It preserves observed cross-candidate
and within-block serial dependence. It does NOT replay adaptive search, estimate
an independent trial count, or replace the research ledger/qualification policy.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import asdict, dataclass
from numbers import Integral, Real

import numpy as np
import pandas as pd
from scipy.stats import norm

from agentic_trader.research.alpha.catalog import AlphaCatalog
from agentic_trader.research.alpha.miner import AlphaMiner
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.promotion import assess_statistical_evidence
from agentic_trader.research.alpha.validation import ValidationPolicy


CALIBRATION_SCHEMA = 2
CONFIDENCE_LEVEL = 0.95
PANEL_OBSERVATIONS = 504
PANEL_CANDIDATES = 8
MAX_RESAMPLED_VALUES = 500_000_000
RESAMPLE_BATCH_VALUES = 2_000_000
MAX_RESAMPLE_BATCH = 32
SYNTHETIC_FEED = "synthetic"
PULSE_EFFECTS = (0.0, 0.004, 0.02)
PULSE_INTERVALS = (8, 20)
SERIAL_DEPENDENCE = (0.0, 0.5)
PANEL_EFFECTS = (0.0, 0.15)
RANDOM_STREAMS = ("strategy_data", "strategy_assessment", "panel_data", "panel_resampling")


def _integer(value, minimum, maximum):
    return isinstance(value, Integral) and not isinstance(value, bool) and minimum <= value <= maximum


@dataclass(frozen=True)
class CalibrationPlan:
    seeds: int = 10
    observations: int = 2500
    seed: int = 20260916
    family_trials: int = 100
    trial_variance: float = 0.003
    bootstrap_samples: int = 499
    block_length: int = 10

    def __post_init__(self):
        for value, low, high in (
            (self.seeds, 1, 256),
            (self.observations, 600, 10000),
            (self.seed, 0, 2**32 - 256),
            (self.family_trials, 1, 100_000_000),
            (self.bootstrap_samples, 99, 4999),
            (self.block_length, 2, 40),
        ):
            if not _integer(value, low, high):
                raise ValueError("Invalid bounded calibration budget")
        if (
            not isinstance(self.trial_variance, Real)
            or isinstance(self.trial_variance, bool)
            or not math.isfinite(self.trial_variance)
            or self.trial_variance < 0
        ):
            raise ValueError("Finite nonnegative trial variance required")
        work = (
            self.seeds
            * self.bootstrap_samples
            * PANEL_OBSERVATIONS
            * PANEL_CANDIDATES
            * len(SERIAL_DEPENDENCE)
            * len(PANEL_EFFECTS)
        )
        if work > MAX_RESAMPLED_VALUES:
            raise ValueError("Calibration resampling budget exceeded; predeclare a smaller study")

    def to_dict(self):
        return {
            "schema_version": CALIBRATION_SCHEMA,
            **asdict(self),
            "pulse_effects": list(PULSE_EFFECTS),
            "pulse_intervals": list(PULSE_INTERVALS),
            "panel_effects": list(PANEL_EFFECTS),
            "serial_dependence": list(SERIAL_DEPENDENCE),
            "panel_observations": PANEL_OBSERVATIONS,
            "panel_candidates": PANEL_CANDIDATES,
            "random_streams": list(RANDOM_STREAMS),
            "validation_policy": asdict(ValidationPolicy()),
        }

    @property
    def identity(self):
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True, allow_nan=False).encode()).hexdigest()


def synthetic_pulse_bars(*, seed: int, observations: int, effect: float, interval: int) -> pd.DataFrame:
    """An observed volume pulse affects only the following return; prefixes agree."""
    if (
        not _integer(seed, 0, 2**32 - 1)
        or not _integer(observations, 100, 10000)
        or not _integer(interval, 2, 100)
        or not isinstance(effect, Real)
        or isinstance(effect, bool)
        or not math.isfinite(effect)
        or not 0 <= effect <= 0.05
    ):
        raise ValueError("Invalid synthetic scenario")
    pulse = np.zeros(observations)
    pulse[np.arange(40, observations, interval)] = 1
    # Single stream, no sample-size-dependent draws before the return innovations.
    returns = np.r_[0, pulse[:-1] * effect] + np.random.default_rng(seed).normal(0, 0.001, observations)
    close = 100 * np.exp(returns.cumsum())
    opens = np.r_[100, close[:-1]]
    frame = pd.DataFrame(
        {
            "open": opens,
            "high": np.maximum(opens, close) * 1.002,
            "low": np.minimum(opens, close) * 0.998,
            "close": close,
            "volume": 1_000_000 + pulse * 800_000,
        },
        index=pd.date_range("2010-01-01", periods=observations, freq="B", tz="UTC"),
    )
    frame.attrs.update(timeframe="1d", feed=SYNTHETIC_FEED, adjustment="raw", synthetic_control=True)
    return frame


def joint_block_max_test(returns: pd.DataFrame, *, samples: int = 499, block_length: int = 10, seed: int = 0) -> dict:
    """One-sided studentized max test for the supplied fixed candidates only."""
    if (
        not isinstance(returns.index, pd.DatetimeIndex)
        or not returns.index.is_unique
        or not returns.index.is_monotonic_increasing
        or not returns.columns.is_unique
        or any(not isinstance(c, str) for c in returns.columns)
    ):
        raise ValueError("Unique ordered times and unique string candidate labels required")
    n, m = returns.shape
    if (
        not 20 <= n <= 25200
        or not 1 <= m <= 256
        or not _integer(samples, 99, 4999)
        or not _integer(block_length, 1, n // 2)
        or not _integer(seed, 0, 2**32 - 1)
    ):
        raise ValueError("Invalid bounded resampling dimensions")
    if n * m * samples > MAX_RESAMPLED_VALUES:
        raise ValueError("Resampling budget exceeded")
    values = returns.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("Missing/nonfinite observations cannot be imputed")
    means, deviations = values.mean(axis=0), values.std(axis=0, ddof=1)
    if (deviations <= 0).any():
        raise ValueError("Nonconstant candidate returns required")
    observed = means / deviations * np.sqrt(n)
    centered = values - means
    rng = np.random.default_rng(seed)
    blocks = math.ceil(n / block_length)
    # Batches bound working memory; row indices are shared by all candidates.
    maxima = []
    batch_size = max(1, min(MAX_RESAMPLE_BATCH, RESAMPLE_BATCH_VALUES // (n * m)))
    for offset in range(0, samples, batch_size):
        starts = rng.integers(0, n, size=(min(batch_size, samples - offset), blocks))
        indices = ((starts[:, :, None] + np.arange(block_length)) % n).reshape(len(starts), -1)[:, :n]
        draw = centered[indices]
        deviation = draw.std(axis=1, ddof=1)
        if (deviation <= 0).any():
            raise ValueError("Degenerate resample; insufficient variable observations")
        maxima.extend((draw.mean(axis=1) / deviation * np.sqrt(n)).max(axis=1).tolist())
    null_max = np.asarray(maxima)
    return {
        "method": "joint_circular_block_max_t_v1",
        "scope": "fixed_candidates_only",
        "observations": n,
        "candidates": m,
        "samples": samples,
        "block_length": block_length,
        "seed": seed,
        "confidence": CONFIDENCE_LEVEL,
        "adjusted_pvalues": {
            name: float((1 + np.count_nonzero(null_max >= observed[i])) / (samples + 1))
            for i, name in enumerate(returns.columns)
        },
        "critical_value": float(np.quantile(null_max, CONFIDENCE_LEVEL, method="higher")),
    }


def _rate(successes: int, count: int):
    """Wilson interval; even zero observed failures has a positive upper bound."""
    rate = successes / count
    z = float(norm.ppf((1 + CONFIDENCE_LEVEL) / 2))
    denominator = 1 + z * z / count
    center = (rate + z * z / (2 * count)) / denominator
    width = z * math.sqrt(rate * (1 - rate) / count + z * z / (4 * count * count)) / denominator
    return {
        "accepted": successes,
        "replicates": count,
        "acceptance_rate": rate,
        "interval_95": [max(0.0, center - width), min(1.0, center + width)],
    }


def run_calibration(plan: CalibrationPlan) -> dict:
    """Fixed synthetic battery. No real observations, config, DB or network access."""
    policy = ValidationPolicy()
    definition = AlphaDefinition(
        "synthetic_pulse",
        "Synthetic pulse control",
        "volume",
        timeframe="1d",
        eligible_symbols=("SYNTH",),
        data_feed=SYNTHETIC_FEED,
    )
    catalog = AlphaCatalog([definition])
    controls = []
    family_controls = []
    random_streams = []
    for seed in range(plan.seed, plan.seed + plan.seeds):
        # Independent child streams avoid coupling synthetic observations with
        # their resampling indices. Effects/rhos deliberately keep paired draws.
        streams = {
            name: int(child.generate_state(1)[0])
            for name, child in zip(RANDOM_STREAMS, np.random.SeedSequence(seed).spawn(len(RANDOM_STREAMS)), strict=True)
        }
        random_streams.append({"replicate_seed": seed, "seeds": streams})
        for interval in PULSE_INTERVALS:
            for effect in PULSE_EFFECTS:
                bars = synthetic_pulse_bars(
                    seed=streams["strategy_data"], observations=plan.observations, effect=effect, interval=interval
                )
                miner = AlphaMiner(seed=streams["strategy_assessment"], catalog=catalog, policy=policy)
                discovered = miner.mine(bars, iterations=0, timeframe="1d", symbol="SYNTH")
                run = miner.last_run
                assessment = assess_statistical_evidence(
                    definition,
                    bars,
                    run,
                    {"feed": SYNTHETIC_FEED, "adjustment": "raw", "incumbents": []},
                    {"trial_count": plan.family_trials, "trial_variance": plan.trial_variance},
                    policy=policy,
                )
                metrics = run["trials"][0]["candidate"]["metrics"]
                controls.append(
                    {
                        "seed": seed,
                        "interval": interval,
                        "effect": effect,
                        "discovery_pass": bool(discovered),
                        "statistical_pass": assessment["passed"],
                        "reasons": assessment["reasons"],
                        "validation_sharpe": metrics["sharpe_oos"],
                        "family_validation_dsr": assessment["family_validation_dsr"],
                        "holdout": assessment["holdout"],
                    }
                )
        # Paired data across effect sizes: only one candidate receives an edge.
        rng = np.random.default_rng(streams["panel_data"])
        innovations = 0.5 * rng.normal(size=(PANEL_OBSERVATIONS, 1)) + math.sqrt(0.75) * rng.normal(
            size=(PANEL_OBSERVATIONS, PANEL_CANDIDATES)
        )
        for rho in SERIAL_DEPENDENCE:
            panel = np.empty_like(innovations)
            panel[0] = innovations[0]
            for i in range(1, len(panel)):
                panel[i] = rho * panel[i - 1] + math.sqrt(1 - rho * rho) * innovations[i]
            for effect in PANEL_EFFECTS:
                values = panel.copy()
                values[:, 0] += effect
                frame = pd.DataFrame(
                    values,
                    index=pd.date_range("2020-01-01", periods=PANEL_OBSERVATIONS, tz="UTC"),
                    columns=[f"candidate_{i}" for i in range(PANEL_CANDIDATES)],
                )
                result = joint_block_max_test(
                    frame,
                    samples=plan.bootstrap_samples,
                    block_length=plan.block_length,
                    seed=streams["panel_resampling"],
                )
                family_controls.append(
                    {
                        "seed": seed,
                        "serial_correlation": rho,
                        "effect": effect,
                        "any_rejected_null": min(result["adjusted_pvalues"].values()) <= 1 - CONFIDENCE_LEVEL,
                        "planted_candidate_detected": result["adjusted_pvalues"]["candidate_0"] <= 1 - CONFIDENCE_LEVEL,
                        "test": result,
                    }
                )
    summaries = []
    for interval in PULSE_INTERVALS:
        for effect in PULSE_EFFECTS:
            selected = [r for r in controls if r["interval"] == interval and r["effect"] == effect]
            summaries.append(
                {
                    "interval": interval,
                    "effect": effect,
                    **_rate(sum(r["statistical_pass"] for r in selected), len(selected)),
                    "rejection_reasons": dict(Counter(reason for r in selected for reason in r["reasons"])),
                }
            )
    family_summaries = []
    for rho in SERIAL_DEPENDENCE:
        for effect in PANEL_EFFECTS:
            selected = [r for r in family_controls if r["serial_correlation"] == rho and r["effect"] == effect]
            family_summaries.append(
                {
                    "serial_correlation": rho,
                    "effect": effect,
                    **_rate(
                        sum(
                            r["any_rejected_null"] if effect == 0 else r["planted_candidate_detected"] for r in selected
                        ),
                        len(selected),
                    ),
                }
            )
    return {
        "synthetic_only": True,
        "authorizes_promotion": False,
        "protocol": plan.to_dict(),
        "protocol_id": plan.identity,
        "random_streams": random_streams,
        "strategy_controls": controls,
        "strategy_summary": summaries,
        "family_controls": family_controls,
        "family_summary": family_summaries,
        "limitations": [
            "Synthetic fixed hypotheses do not replay adaptive discovery or establish real-market power.",
            "Fixed-panel bootstrap is not a replacement for cumulative trial accounting.",
            "Intervals describe Monte Carlo sampling uncertainty within this scenario only.",
        ],
    }
