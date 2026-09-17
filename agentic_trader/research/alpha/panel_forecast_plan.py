"""Frozen development-only forecast comparisons over an observed equity cohort."""

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import date

import pandas as pd

from agentic_trader.market.bars import utc_timestamp
from agentic_trader.research.alpha.daily_inputs import validate_daily_window
from agentic_trader.research.alpha.equity_universe import document_hash
from agentic_trader.research.alpha.forecast_policy import DailyLongFlatPolicy
from agentic_trader.research.alpha.information import ICPolicy
from agentic_trader.research.alpha.panel_study import (
    MAX_PANEL_DAYS,
    MAX_PANEL_SYMBOLS,
    MAX_PANEL_TRIALS,
    PanelFold,
    PanelHypothesis,
)
from agentic_trader.research.alpha.targets import ForecastLabel, ForecastTarget


PANEL_FORECAST_VERSION = "screened_panel_forecasts_v1"
MAX_FORECAST_COHORTS = 2
MAX_FORECAST_FEATURES = 8
MAX_TRAIN_SESSIONS = 1260
MODEL_CONTROLS = ("ridge", "training_mean")


@dataclass(frozen=True)
class ForecastCohort:
    name: str
    symbols: tuple[str, ...]
    top_k: int
    min_assets: int

    def __post_init__(self):
        if (
            not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.name)
            or not isinstance(self.symbols, tuple)
            or not 3 <= len(self.symbols) <= MAX_PANEL_SYMBOLS
            or self.symbols != tuple(sorted(set(self.symbols)))
            or any(not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", symbol) for symbol in self.symbols)
            or type(self.top_k) is not int
            or type(self.min_assets) is not int
            or not 1 <= self.top_k <= len(self.symbols) // 2
            or not max(3, 2 * self.top_k) <= self.min_assets <= len(self.symbols)
        ):
            raise ValueError("Named bounded cohort and explicit decision-time breadth required")

    def document(self):
        return {**asdict(self), "symbols": list(self.symbols)}


@dataclass(frozen=True)
class ForecastSelection:
    cohort_name: str
    snapshot_id: str
    plan_id: str
    result_sha256: str
    observed_at: pd.Timestamp

    def __post_init__(self):
        if any(
            not re.fullmatch(r"[a-f0-9]{64}", value) for value in (self.snapshot_id, self.plan_id, self.result_sha256)
        ):
            raise ValueError("Exact selection and snapshot identities required")
        object.__setattr__(self, "observed_at", utc_timestamp(self.observed_at))

    def document(self):
        return {**asdict(self), "observed_at": self.observed_at.isoformat()}


@dataclass(frozen=True)
class PanelForecastPlan:
    campaign_id: str
    cohorts: tuple[ForecastCohort, ...]
    start: date
    end: date
    folds: tuple[PanelFold, ...]
    features: tuple[PanelHypothesis, ...]
    economic_features: tuple[str, ...]
    target: ForecastTarget
    costs_bps: tuple[float, ...]
    ic: ICPolicy
    selection: ForecastSelection
    feed: str = "alpaca:iex"
    ridge_alpha: float = 100.0
    train_sessions: int = 252
    min_train_sessions: int = 126
    min_train_rows: int = 500
    refit_sessions: int = 20
    history_sessions: int = 61

    def __post_init__(self):
        if (
            not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.campaign_id)
            or not isinstance(self.cohorts, tuple)
            or not 1 <= len(self.cohorts) <= MAX_FORECAST_COHORTS
            or len({c.name for c in self.cohorts}) != len(self.cohorts)
            or len(self.acquisition_symbols) != sum(len(c.symbols) for c in self.cohorts)
            or self.selection.cohort_name not in {c.name for c in self.cohorts}
            or self.feed not in ("alpaca:iex", "alpaca:sip")
        ):
            raise ValueError("Explicit disjoint cohorts bound to the observed selection required")
        if (
            type(self.start) is not date
            or type(self.end) is not date
            or not 0 < (self.end - self.start).days < MAX_PANEL_DAYS
            or not isinstance(self.folds, tuple)
            or not self.folds
            or len({f.name for f in self.folds}) != len(self.folds)
            or any(not self.start < f.start < f.end <= self.end for f in self.folds)
            or any(a.end >= b.start for a, b in zip(self.folds, self.folds[1:], strict=False))
        ):
            raise ValueError("Bounded chronological train and evaluation intervals required")
        if (
            not isinstance(self.features, tuple)
            or not 1 <= len(self.features) <= MAX_FORECAST_FEATURES
            or len({f.name for f in self.features}) != len(self.features)
            or len({f.expression for f in self.features}) != len(self.features)
            or any(f.beta_window is not None or f.name in MODEL_CONTROLS for f in self.features)
            or not isinstance(self.economic_features, tuple)
            or not self.economic_features
            or len(set(self.economic_features)) != len(self.economic_features)
            or not set(self.economic_features) <= {f.name for f in self.features}
            or self.target.timeframe != "1d"
            or self.target.label != ForecastLabel.NEXT_OPEN_TO_CLOSE
        ):
            raise ValueError("Unique causal features, economic controls and next-open daily target required")
        bounds = {
            "train_sessions": (2, MAX_TRAIN_SESSIONS),
            "min_train_sessions": (2, self.train_sessions),
            "min_train_rows": (3, self.train_sessions * MAX_PANEL_SYMBOLS),
            "refit_sessions": (1, self.train_sessions),
            "history_sessions": (2, MAX_TRAIN_SESSIONS),
        }
        if any(
            type(getattr(self, name)) is not int or not lo <= getattr(self, name) <= hi
            for name, (lo, hi) in bounds.items()
        ):
            raise ValueError("Bounded history, mature training support and refit cadence required")
        if (
            isinstance(self.ridge_alpha, bool)
            or not isinstance(self.ridge_alpha, (int, float))
            or not math.isfinite(self.ridge_alpha)
            or self.ridge_alpha <= 0
        ):
            raise ValueError("Finite positive Ridge regularization required")
        object.__setattr__(self, "ridge_alpha", float(self.ridge_alpha))
        object.__setattr__(self, "costs_bps", DailyLongFlatPolicy(self.costs_bps).costs_bps)
        if not 1 <= self.trial_count <= MAX_PANEL_TRIALS:
            raise ValueError("Forecast study exceeds bounded comparison budget")

    @property
    def acquisition_symbols(self):
        return tuple(sorted({symbol for cohort in self.cohorts for symbol in cohort.symbols}))

    @property
    def adjustment(self):
        return "all"

    @property
    def trial_count(self):
        return (
            len(self.cohorts)
            * len(self.folds)
            * (len(self.economic_features) + len(MODEL_CONTROLS))
            * (1 + len(self.costs_bps))
        )

    @property
    def identity(self):
        return document_hash(self.document())

    def validate_as_of(self, now):
        if utc_timestamp(now) < self.selection.observed_at:
            raise ValueError("Forecast study cannot precede its actual cohort selection")
        validate_daily_window(self.end, now)

    def validate_selection(self, result_bytes: bytes, manifest_bytes: bytes, inputs_bytes: bytes):
        """Bind every selected member; a caller cannot quietly consume a subset."""
        if hashlib.sha256(result_bytes).hexdigest() != self.selection.result_sha256:
            raise ValueError("Frozen liquidity result hash differs")
        result, manifest, inputs = json.loads(result_bytes), json.loads(manifest_bytes), json.loads(inputs_bytes)
        cohort = next(c for c in self.cohorts if c.name == self.selection.cohort_name)
        try:
            selected = result["selected"]
            receipts = inputs["receipts"]
            valid = (
                hashlib.sha256(manifest_bytes).hexdigest() == result["manifest_hash"]
                and hashlib.sha256(inputs_bytes).hexdigest() == result["inputs_hash"]
                and result["status"] == "completed"
                and result["selection_available"] is True
                and result["shortfall"] == 0
                and result["snapshot_id"] == self.selection.snapshot_id
                and result["plan_id"] == manifest["plan_id"] == self.selection.plan_id
                and bool(receipts)
                and max(utc_timestamp(r["received_at"]) for r in receipts) == self.selection.observed_at
                and all(
                    utc_timestamp(manifest["as_of"])
                    <= utc_timestamp(r["requested_at"])
                    <= utc_timestamp(r["received_at"])
                    for r in receipts
                )
                and result["selected_count"] == len(selected) == len(cohort.symbols)
                and tuple(sorted(row["symbol"] for row in selected)) == cohort.symbols
                and len({row["asset_id"] for row in selected}) == len(selected)
            )
        except (KeyError, TypeError) as exc:
            raise ValueError("Incomplete frozen selection evidence") from exc
        if not valid:
            raise ValueError("Exact complete selected cohort and observed manifest required")

    def document(self):
        return {
            "version": PANEL_FORECAST_VERSION,
            "campaign_id": self.campaign_id,
            "cohorts": [c.document() for c in self.cohorts],
            "selection": self.selection.document(),
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "feed": self.feed,
            "adjustment": self.adjustment,
            "folds": [f.document() for f in self.folds],
            "features": [asdict(f) for f in self.features],
            "economic_features": list(self.economic_features),
            "target": self.target.document(),
            "costs_bps": list(self.costs_bps),
            "ic": asdict(self.ic),
            **{
                name: getattr(self, name)
                for name in (
                    "ridge_alpha",
                    "train_sessions",
                    "min_train_sessions",
                    "min_train_rows",
                    "refit_sessions",
                    "history_sessions",
                )
            },
            "charged_trials": self.trial_count,
            "authorizes_promotion": False,
            "universe_scope": "Current membership and liquidity conditioned historical development; not point-in-time constituents",
            "training_scope": "Per-cohort pooled equal-weight eligible date-symbol rows; strictly mature labels before refit; train-only scaling",
            "eligibility_scope": "Consecutive observed past bars and all finite features; no future outcome membership selection",
            "payoff_scope": "Nonoverlapping fixed-horizon rank baskets; unknown held outcomes withhold the full curve; no borrow or funding modeled",
            "corporate_action_scope": "Alpaca all-adjusted return proxies; no physical shares, dividend payment timing or historical borrow claim",
            "availability": "Historical daily completion assumed by next local midnight; actual receipts are not backdated",
            "selection_clock": "observed_at is the final source receipt of the completed hashed liquidity screen, not its acquisition start",
            "inference_scope": "Descriptive per-fold Rank IC and HAC; no adaptive-selection correction or promotion gate",
            "missing_label_scope": "Any missing predicted outcome withholds that date's IC; unavailable expected dates withhold complete-fold inference",
            "mse_scope": "Pooled paired date-symbol forecast errors versus the same training-mean support; descriptive, not independent row inference",
        }

    @classmethod
    def from_document(cls, document):
        try:
            plan = cls(
                **{
                    name: document[name]
                    for name in (
                        "campaign_id",
                        "feed",
                        "ridge_alpha",
                        "train_sessions",
                        "min_train_sessions",
                        "min_train_rows",
                        "refit_sessions",
                        "history_sessions",
                    )
                },
                cohorts=tuple(ForecastCohort(**{**c, "symbols": tuple(c["symbols"])}) for c in document["cohorts"]),
                selection=ForecastSelection(**document["selection"]),
                start=date.fromisoformat(document["start"]),
                end=date.fromisoformat(document["end"]),
                folds=tuple(
                    PanelFold(f["name"], date.fromisoformat(f["start"]), date.fromisoformat(f["end"]))
                    for f in document["folds"]
                ),
                features=tuple(PanelHypothesis(**f) for f in document["features"]),
                economic_features=tuple(document["economic_features"]),
                target=ForecastTarget("1d", document["target"]["horizon_bars"], ForecastLabel.NEXT_OPEN_TO_CLOSE),
                costs_bps=tuple(document["costs_bps"]),
                ic=ICPolicy(**document["ic"]),
            )
        except (KeyError, TypeError) as exc:
            raise ValueError("Explicit complete frozen forecast protocol required") from exc
        if document != plan.document():
            raise ValueError("Exact frozen forecast protocol and comparison count required")
        return plan
