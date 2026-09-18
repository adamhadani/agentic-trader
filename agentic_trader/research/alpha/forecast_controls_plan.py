"""Frozen, descriptive controls over an immutable prior forecast study."""

import re
from dataclasses import dataclass

from agentic_trader.research.alpha.equity_universe import document_hash
from agentic_trader.research.alpha.forecast_policy import DailyLongFlatPolicy
from agentic_trader.research.alpha.panel_forecast_plan import PanelForecastPlan
from agentic_trader.research.alpha.panel_study import MAX_PANEL_TRIALS


FORECAST_CONTROLS_VERSION = "retained_forecast_controls_v1"
CONTROL_EXPRESSIONS = {"reversal60": "-roc(close,60)", "volatility20": "realized_vol(returns,20)"}
CONTROL_MODELS = ("ridge", "reversal60", "volatility20", "rank_blend", "equal_weight_long_only")
ENDPOINT_SCOPES = ("finite_source_price", "positive_endpoint_volume")


@dataclass(frozen=True)
class ForecastControlsPlan:
    parent: PanelForecastPlan
    cohort_name: str
    parent_result_sha256: str
    costs_bps: tuple[float, ...] = (1.0, 5.0)

    def __post_init__(self):
        object.__setattr__(self, "costs_bps", DailyLongFlatPolicy(self.costs_bps).costs_bps)
        if (
            self.cohort_name not in {c.name for c in self.parent.cohorts}
            or not re.fullmatch(r"[a-f0-9]{64}", self.parent_result_sha256)
            or any(c not in self.parent.costs_bps for c in self.costs_bps)
            or self.trial_count > MAX_PANEL_TRIALS
        ):
            raise ValueError("Bounded controls require an exact parent cohort, result hash and matched costs")

    @property
    def start(self):
        return self.parent.start

    @property
    def end(self):
        return self.parent.end

    @property
    def feed(self):
        return self.parent.feed

    @property
    def adjustment(self):
        return self.parent.adjustment

    @property
    def acquisition_symbols(self):
        # Loading the parent result inspects every cohort, including warmup/controls.
        return self.parent.acquisition_symbols

    @property
    def trial_count(self):
        return len(CONTROL_MODELS) * len(self.parent.folds) * len(ENDPOINT_SCOPES) * (1 + len(self.costs_bps))

    @property
    def identity(self):
        return document_hash(self.document())

    def validate_as_of(self, now):
        self.parent.validate_as_of(now)

    def document(self):
        return {
            "version": FORECAST_CONTROLS_VERSION,
            "parent": self.parent.document(),
            "cohort_name": self.cohort_name,
            "parent_result_sha256": self.parent_result_sha256,
            "costs_bps": list(self.costs_bps),
            "controls": list(CONTROL_MODELS),
            "expressions": dict(CONTROL_EXPRESSIONS),
            "rank_blend": "Equal blend of within-date average percentile ranks of reversal60 and volatility20",
            "endpoint_scopes": list(ENDPOINT_SCOPES),
            "support": "Original eligibility and finite frozen Ridge forecasts; no future-outcome selection",
            "strict_endpoints": "Positive source daily volume at entry and exit masks outcomes only, never decisions",
            "training": "Frozen parent Ridge; original finite-price training labels unchanged; no refitting",
            "comparison": "Matched gross/net tail construction, not beta/sector/volatility risk; long-only is context",
            "uncertainty": "Paired basket vectors and descriptive dispersion only; no economic significance claim",
            "source": "Hash-verified retained artifacts; current reads are not new provider observations",
            "scope": "Post-selection current-cohort historical development; not independent validation or fills",
            "charged_trials": self.trial_count,
            "authorizes_promotion": False,
        }

    @classmethod
    def from_document(cls, document):
        try:
            plan = cls(
                PanelForecastPlan.from_document(document["parent"]),
                document["cohort_name"],
                document["parent_result_sha256"],
                tuple(document["costs_bps"]),
            )
        except (KeyError, TypeError) as exc:
            raise ValueError("Complete explicit forecast-controls contract required") from exc
        if plan.document() != document:
            raise ValueError("Exact forecast-controls contract required")
        return plan
