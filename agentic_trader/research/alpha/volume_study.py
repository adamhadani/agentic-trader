"""Frozen train/forward volume diagnostics over the shared daily research harness."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date

import numpy as np
import pandas as pd

from agentic_trader.market.bars import FIXED_BAR_LAYOUT
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.alpha.panel import PanelCoverageError, align_daily_panel
from agentic_trader.research.alpha.panel_study import MAX_PANEL_DAYS, MAX_PANEL_SYMBOLS, MAX_PANEL_TRIALS
from agentic_trader.research.alpha.volume import VolumeCalibration, VolumeContract, VolumePolicy


VOLUME_STUDY_VERSION = "source_volume_study_v1"
VOLUME_REFERENCE_THRESHOLD = 1.5
VOLUME_COMPARISON_COUNT = 2  # trained quantile and frozen relative-volume reference


@dataclass(frozen=True)
class VolumeFold:
    name: str
    training_start: date
    training_end: date
    start: date
    end: date

    def __post_init__(self):
        if (
            not self.name
            or any(type(d) is not date for d in (self.training_start, self.training_end, self.start, self.end))
            or not self.training_start < self.training_end < self.start < self.end
        ):
            raise ValueError("Named training interval must precede evaluation")

    def document(self):
        return {
            "name": self.name,
            **{key: getattr(self, key).isoformat() for key in ("training_start", "training_end", "start", "end")},
        }


@dataclass(frozen=True)
class VolumeStudyPlan:
    campaign_id: str
    symbols: tuple[str, ...]
    start: date
    end: date
    folds: tuple[VolumeFold, ...]
    contract: VolumeContract
    policy: VolumePolicy
    reference_threshold: float = VOLUME_REFERENCE_THRESHOLD

    def __post_init__(self):
        if (
            not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.campaign_id)
            or not isinstance(self.symbols, tuple)
            or not 1 <= len(self.symbols) <= MAX_PANEL_SYMBOLS
            or self.symbols != tuple(sorted(set(self.symbols)))
            or any(not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", s) for s in self.symbols)
            or not isinstance(self.contract, VolumeContract)
            or self.feed not in ("alpaca:iex", "alpaca:sip")
            or not isinstance(self.policy, VolumePolicy)
            or type(self.start) is not date
            or type(self.end) is not date
            or not 0 < (self.end - self.start).days < MAX_PANEL_DAYS
            or not isinstance(self.folds, tuple)
            or not self.folds
            or len({f.name for f in self.folds}) != len(self.folds)
            or any(not self.start <= f.training_start < f.end <= self.end for f in self.folds)
            or any(a.end >= b.start for a, b in zip(self.folds, self.folds[1:], strict=False))
            or isinstance(self.reference_threshold, bool)
            or not np.isfinite(self.reference_threshold)
            or self.reference_threshold <= 0
            or not 1 <= self.trial_count <= MAX_PANEL_TRIALS
        ):
            raise ValueError("Explicit bounded source-specific volume protocol required")

    @property
    def acquisition_symbols(self):
        return self.symbols

    @property
    def feed(self):
        return self.contract.feed

    @property
    def adjustment(self):
        return self.contract.adjustment

    @property
    def trial_count(self):
        return len(self.symbols) * len(self.folds) * VOLUME_COMPARISON_COUNT

    def document(self):
        return {
            "version": VOLUME_STUDY_VERSION,
            "campaign_id": self.campaign_id,
            "symbols": list(self.symbols),
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "folds": [f.document() for f in self.folds],
            "contract": asdict(self.contract),
            "policy": asdict(self.policy),
            "reference_threshold": self.reference_threshold,
            "charged_trials": self.trial_count,
            "authorizes_promotion": False,
            "availability": "Historical native daily completion assumed by next local midnight; receipts are not backdated",
        }

    @classmethod
    def from_document(cls, document):
        result = cls(
            document["campaign_id"],
            tuple(document["symbols"]),
            date.fromisoformat(document["start"]),
            date.fromisoformat(document["end"]),
            tuple(
                VolumeFold(
                    f["name"], *(date.fromisoformat(f[k]) for k in ("training_start", "training_end", "start", "end"))
                )
                for f in document["folds"]
            ),
            VolumeContract(**document["contract"]),
            VolumePolicy(**document["policy"]),
            document["reference_threshold"],
        )
        if document != result.document():
            raise ValueError("Exact frozen volume study contract required")
        return result

    @property
    def identity(self):
        return hashlib.sha256(json.dumps(self.document(), sort_keys=True, allow_nan=False).encode()).hexdigest()


def compute_volume_study(frames, clock, plan: VolumeStudyPlan, sessions):
    if set(frames) != set(plan.symbols) or any(
        f.attrs.get("bar_layout", FIXED_BAR_LAYOUT) != plan.contract.bar_layout for f in frames.values()
    ):
        raise ValueError("Exact universe and volume clock contract required")
    panel = align_daily_panel(frames, clock, feed=plan.feed, adjustment=plan.adjustment)
    if not panel.complete:
        raise PanelCoverageError(panel.coverage)
    profiles = []
    for symbol in plan.symbols:
        volume = panel.frames[symbol].volume
        available = pd.Series(clock + pd.DateOffset(days=1), index=clock)
        for fold in plan.folds:
            training = clock.date <= fold.training_end
            cutoff = pd.Timestamp(fold.training_end, tz=ET_TZ) + pd.DateOffset(days=1)
            calibration = VolumeCalibration.fit(
                volume.loc[training],
                available.loc[training],
                symbol=symbol,
                contract=plan.contract,
                policy=plan.policy,
                training_start=pd.Timestamp(fold.training_start, tz=ET_TZ),
                trained_until=cutoff,
            )
            elapsed = clock.date <= fold.end
            evaluated = calibration.apply(
                volume.loc[elapsed],
                available.loc[elapsed],
                symbol=symbol,
                contract=plan.contract,
                as_of=pd.Timestamp(fold.end, tz=ET_TZ) + pd.DateOffset(days=1),
            )
            evaluated = evaluated.loc[evaluated.index.date >= fold.start]
            if evaluated.empty:
                raise ValueError("No observed forward volume dates")
            reference = np.asarray(calibration.reference)
            profiles.append(
                {
                    "symbol": symbol,
                    "fold": fold.name,
                    "calibration_id": calibration.calibration_id,
                    "calibration": calibration.document(),
                    "threshold": calibration.threshold,
                    "training_observations": len(reference),
                    "evaluation_observations": len(evaluated),
                    "training_surge_fraction": float((reference > calibration.threshold).mean()),
                    "evaluation_surge_fraction": float(evaluated.surge.mean()),
                    "reference_training_surge_fraction": float((reference > plan.reference_threshold).mean()),
                    "reference_evaluation_surge_fraction": float(
                        (evaluated.relative_volume > plan.reference_threshold).mean()
                    ),
                    "evaluation_median_percentile": float(evaluated.percentile.median()),
                    "observations": [
                        {
                            "bar": t.isoformat(),
                            "available_at": available.loc[t].isoformat(),
                            "relative_volume": float(r.relative_volume),
                            "percentile": float(r.percentile),
                            "surge": bool(r.surge),
                        }
                        for t, r in evaluated.iterrows()
                    ],
                }
            )
    return {
        "version": VOLUME_STUDY_VERSION,
        "coverage": panel.coverage,
        "profiles": profiles,
        "authorizes_promotion": False,
        "limitations": [
            "Calibrates within-feed relative volume, not predictive returns or tradable capacity.",
            "Historical next-midnight availability is assumed; actual receipt latency requires prospective evidence.",
            "Reference quantiles and later exceedance rates are descriptive; dependent observations have no IID confidence claim.",
            "Adjusted volume is current-vintage provider evidence; no point-in-time corporate-action claim.",
            "Profiles bind symbol, feed, timeframe, adjustment, clock, training data and policy; no cross-feed conversion.",
        ],
    }
