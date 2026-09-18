"""Frozen six-arm factor experiment over a complete retained forecast parent."""

import re
from dataclasses import dataclass

from agentic_trader.research.alpha.equity_universe import document_hash
from agentic_trader.research.alpha.factor_features import FACTOR_COUNT, ResidualMomentumSpec
from agentic_trader.research.alpha.forecast_controls_plan import CONTROL_EXPRESSIONS
from agentic_trader.research.alpha.forecast_policy import DailyLongFlatPolicy
from agentic_trader.research.alpha.panel_forecast_plan import PanelForecastPlan
from agentic_trader.research.alpha.panel_study import MAX_PANEL_TRIALS


FACTOR_CONTROLS_VERSION = "retained_factor_controls_v1"
FACTOR_MODELS = (
    "ridge",
    "volatility20",
    "rank_blend",
    "skipped_month_momentum",
    "residual_momentum",
    "residual_rank_blend",
)
FACTOR_OUTCOME_SCOPE = "positive_endpoint_volume"
FACTOR_BENCHMARK = "rank_blend"
FACTOR_HORIZON = 20
FACTOR_TOP_K = 8
FACTOR_MIN_ASSETS = 16
FACTOR_COSTS_BPS = (1.0, 5.0)


@dataclass(frozen=True)
class FactorControlsPlan:
    parent: PanelForecastPlan
    cohort_name: str
    parent_result_sha256: str
    factor_symbols: tuple[str, ...]
    costs_bps: tuple[float, ...] = FACTOR_COSTS_BPS

    def __post_init__(self):
        object.__setattr__(self, "costs_bps", DailyLongFlatPolicy(self.costs_bps).costs_bps)
        cohort = next((c for c in self.parent.cohorts if c.name == self.cohort_name), None)
        other = tuple(c for c in self.parent.cohorts if c.name != self.cohort_name)
        if (
            cohort is None
            or len(other) != 1
            or not isinstance(self.factor_symbols, tuple)
            or self.factor_symbols != tuple(sorted(set(self.factor_symbols)))
            or len(self.factor_symbols) != FACTOR_COUNT
            or self.factor_symbols != other[0].symbols
            or not isinstance(self.parent_result_sha256, str)
            or not re.fullmatch(r"[a-f0-9]{64}", self.parent_result_sha256)
            or self.cohort_name != self.parent.selection.cohort_name
            or cohort.top_k != FACTOR_TOP_K
            or cohort.min_assets != FACTOR_MIN_ASSETS
            or self.parent.target.horizon_bars != FACTOR_HORIZON
            or self.parent.ic.hac_lags != FACTOR_HORIZON
            or self.parent.feed != "alpaca:iex"
            or self.costs_bps != FACTOR_COSTS_BPS
            or not set(self.costs_bps) <= set(self.parent.costs_bps)
            or self.trial_count > MAX_PANEL_TRIALS
        ):
            raise ValueError(
                "Exact retained factor cohort, nine proxies, 20-session target and matched policy required"
            )

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
        return self.parent.acquisition_symbols

    @property
    def trial_count(self):
        return len(FACTOR_MODELS) * len(self.parent.folds) * (1 + len(self.costs_bps))

    @property
    def identity(self):
        return document_hash(self.document())

    def validate_as_of(self, now):
        self.parent.validate_as_of(now)

    def document(self):
        return {
            "version": FACTOR_CONTROLS_VERSION,
            "parent": self.parent.document(),
            "cohort_name": self.cohort_name,
            "parent_result_sha256": self.parent_result_sha256,
            "factor_symbols": list(self.factor_symbols),
            "costs_bps": list(self.costs_bps),
            "models": list(FACTOR_MODELS),
            "control_expressions": dict(CONTROL_EXPRESSIONS),
            "residual_feature": ResidualMomentumSpec().document(),
            "outcome_scope": FACTOR_OUTCOME_SCOPE,
            "support": "Common finite past-only features and frozen parent Ridge; future labels never select members",
            "rank_blend": "Equal average percentile ranks of reversal60 and volatility20 on common support",
            "residual_rank_blend": "Equal average percentile ranks of rank_blend and residual_momentum on common support",
            "rank_ties": "Average percentile score ranks; shared equal-weight boundary-tie basket policy",
            "training": "No supervised refitting; original parent finite-price Ridge training is unchanged",
            "factor_scope": "Nine explicit ETF return proxies; no inferred stock sectors or factor-neutral portfolio claim",
            "endpoint_scope": "Positive entry/exit source volume masks outcomes only; unknown held outcomes withhold curves",
            "risk_attribution": "Past-only ETF loadings at each decision; missing held loadings withhold complete exposure",
            "comparison": "Matched support, at most unit gross and zero net targets; not matched beta, volatility, borrow or execution",
            "uncertainty": "Expected-date paired IC with shared HAC20; paired baskets descriptive without significance claims",
            "source": "Hash-bound retained parent and all inspected members; artifact reads are not provider observations",
            "scope": "Post-selection current-cohort historical development; no independent validation or broker fills",
            "charged_trials": self.trial_count,
            "authorizes_promotion": False,
        }

    @classmethod
    def from_document(cls, document):
        try:
            plan = cls(
                parent=PanelForecastPlan.from_document(document["parent"]),
                cohort_name=document["cohort_name"],
                parent_result_sha256=document["parent_result_sha256"],
                factor_symbols=tuple(document["factor_symbols"]),
                costs_bps=tuple(document["costs_bps"]),
            )
        except (KeyError, TypeError) as exc:
            raise ValueError("Complete explicit factor-controls contract required") from exc
        if plan.document() != document:
            raise ValueError("Exact frozen factor-controls contract required")
        return plan
