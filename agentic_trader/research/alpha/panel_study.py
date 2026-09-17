"""Frozen, causal cross-sectional forecast and basket-payoff diagnostics.

No fitted winner, portfolio execution, alpha registration or promotion authority.
Native daily open/close prices are proxies, not verified auction or broker fills.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from enum import StrEnum
from numbers import Real

import numpy as np
import pandas as pd

from agentic_trader.research.alpha.daily_inputs import validate_daily_window
from agentic_trader.research.alpha.dsl import AlphaExpressionEvaluator, compile_expression
from agentic_trader.research.alpha.forecast_policy import BASIS_POINTS, DailyLongFlatPolicy
from agentic_trader.research.alpha.information import ICPolicy, cross_sectional_ic
from agentic_trader.research.alpha.panel import DailyResearchPanel, PanelCoverageError, beta_adjust_scores
from agentic_trader.research.alpha.targets import ForecastLabel, ForecastTarget, forecast_labels


PANEL_STUDY_VERSION = "native_daily_panel_diagnostics_v1"
PANEL_JOURNAL_SYMBOL = "RESEARCH_PANEL"
MAX_PANEL_TRIALS = 128
MAX_PANEL_SYMBOLS = 64
MAX_PANEL_DAYS = 3660
MAX_BETA_WINDOW = 252


class PanelStudyStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class PanelHypothesis:
    name: str
    expression: str
    beta_window: int | None = None

    def __post_init__(self):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.name):
            raise ValueError("Explicit hypothesis identity required")
        object.__setattr__(self, "expression", ast.unparse(compile_expression(self.expression).tree))
        if self.beta_window is not None and (
            type(self.beta_window) is not int or not 2 <= self.beta_window <= MAX_BETA_WINDOW
        ):
            raise ValueError("Bounded causal beta window required")


@dataclass(frozen=True)
class PanelFold:
    name: str
    start: date
    end: date

    def __post_init__(self):
        if not self.name or type(self.start) is not date or type(self.end) is not date or self.start >= self.end:
            raise ValueError("Named ordered evaluation fold required")

    def document(self):
        return {"name": self.name, "start": self.start.isoformat(), "end": self.end.isoformat()}


@dataclass(frozen=True)
class PanelStudyPlan:
    campaign_id: str
    symbols: tuple[str, ...]
    benchmark: str
    start: date
    end: date
    folds: tuple[PanelFold, ...]
    hypotheses: tuple[PanelHypothesis, ...]
    target: ForecastTarget
    costs_bps: tuple[float, ...]
    top_k: int
    ic: ICPolicy
    feed: str = "alpaca:sip"

    def __post_init__(self):
        if (
            not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.campaign_id)
            or not isinstance(self.symbols, tuple)
            or not 3 <= len(self.symbols) <= MAX_PANEL_SYMBOLS
            or self.symbols != tuple(sorted(set(self.symbols)))
            or self.benchmark in self.symbols
            or any(not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", s) for s in (*self.symbols, self.benchmark))
            or self.feed not in ("alpaca:sip", "alpaca:iex")
        ):
            raise ValueError("Explicit bounded unique sorted panel universe and feed required")
        if (
            type(self.start) is not date
            or type(self.end) is not date
            or not 0 < (self.end - self.start).days < MAX_PANEL_DAYS
            or not isinstance(self.folds, tuple)
            or not self.folds
            or len({f.name for f in self.folds}) != len(self.folds)
            or any(not self.start <= f.start < f.end <= self.end for f in self.folds)
            or any(a.end >= b.start for a, b in zip(self.folds, self.folds[1:], strict=False))
        ):
            raise ValueError("Bounded observations and nonoverlapping chronological folds required")
        if (
            not isinstance(self.hypotheses, tuple)
            or not self.hypotheses
            or len({h.name for h in self.hypotheses}) != len(self.hypotheses)
            or len({(h.expression, h.beta_window) for h in self.hypotheses}) != len(self.hypotheses)
            or self.target.timeframe != "1d"
            or self.target.label != ForecastLabel.NEXT_OPEN_TO_CLOSE
            or type(self.top_k) is not int
            or not 1 <= self.top_k <= len(self.symbols) // 2
            or not 2 * self.top_k <= self.ic.min_assets <= len(self.symbols)
        ):
            raise ValueError("Unique hypotheses, next-bar target and bounded basket breadth required")
        object.__setattr__(self, "costs_bps", DailyLongFlatPolicy(self.costs_bps).costs_bps)
        if not 1 <= self.trial_count <= MAX_PANEL_TRIALS:
            raise ValueError("Panel study exceeds bounded trial budget")

    def validate_as_of(self, now):
        validate_daily_window(self.end, now)

    @property
    def acquisition_symbols(self):
        return (*self.symbols, self.benchmark)

    @property
    def adjustment(self):
        return "raw"

    @property
    def trial_count(self):
        return len(self.hypotheses) * len(self.folds) * (1 + len(self.costs_bps))

    def document(self):
        return {
            "version": PANEL_STUDY_VERSION,
            "campaign_id": self.campaign_id,
            "symbols": list(self.symbols),
            "benchmark": self.benchmark,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "feed": self.feed,
            "folds": [f.document() for f in self.folds],
            "hypotheses": [asdict(h) for h in self.hypotheses],
            "horizon_sessions": self.target.horizon_bars,
            "costs_bps": list(self.costs_bps),
            "top_k": self.top_k,
            "ic": asdict(self.ic),
            "charged_trials": self.trial_count,
            "target": self.target.document(),
            "authorizes_promotion": False,
            "availability": "Historical native daily completion assumed by next local midnight; receipts are not backdated",
            "universe_scope": "Fixed curated cohort; not historical point-in-time membership or execution eligibility",
            "payoff_scope": "Gross at most one, half-long/half-short rank baskets; flat between disjoint fixed-horizon proxies",
        }

    @property
    def identity(self):
        return hashlib.sha256(json.dumps(self.document(), sort_keys=True).encode()).hexdigest()

    @classmethod
    def from_document(cls, document):
        plan = cls(**cls.arguments_from_document(document))
        if document != plan.document():
            raise ValueError("Frozen panel version and exact trial count required")
        return plan

    @staticmethod
    def arguments_from_document(document):
        return {
            "campaign_id": document["campaign_id"],
            "symbols": tuple(document["symbols"]),
            "benchmark": document["benchmark"],
            "start": date.fromisoformat(document["start"]),
            "end": date.fromisoformat(document["end"]),
            "folds": tuple(
                PanelFold(f["name"], date.fromisoformat(f["start"]), date.fromisoformat(f["end"]))
                for f in document["folds"]
            ),
            "hypotheses": tuple(PanelHypothesis(**h) for h in document["hypotheses"]),
            "target": ForecastTarget("1d", document["horizon_sessions"], ForecastLabel.NEXT_OPEN_TO_CLOSE),
            "costs_bps": tuple(document["costs_bps"]),
            "top_k": document["top_k"],
            "ic": ICPolicy(**document["ic"]),
            "feed": document["feed"],
        }


def basket_weights(scores: pd.Series, top_k: int) -> pd.Series:
    """Split cutoff ties equally; never choose a name using future outcomes.

    Net weight is zero; gross is at most one. A tie shared across both tails nets
    out to cash instead of inventing stock-picking information or leverage.
    """
    if (
        not scores.index.is_unique
        or not np.isfinite(scores.to_numpy()).all()
        or type(top_k) is not int
        or not 1 <= top_k <= len(scores) // 2
    ):
        raise ValueError("Finite unique basket scores and valid tail size required")
    count = scores.groupby(scores).transform("size")
    long = (top_k + 1 - scores.rank(method="min", ascending=False)).clip(lower=0).clip(upper=count) / count
    short = (top_k + 1 - scores.rank(method="min")).clip(lower=0).clip(upper=count) / count
    return (long - short) / (2 * top_k)


def panel_scores(panel: DailyResearchPanel, plan: PanelStudyPlan, hypothesis: PanelHypothesis):
    evaluator = AlphaExpressionEvaluator()
    scores = pd.DataFrame({s: evaluator.evaluate(hypothesis.expression, panel.frames[s]) for s in plan.symbols})
    if hypothesis.beta_window is not None:
        market = evaluator.evaluate(hypothesis.expression, panel.frames[plan.benchmark])
        returns = panel.close.pct_change(fill_method=None)
        scores = beta_adjust_scores(
            scores, returns.loc[:, list(plan.symbols)], market, returns[plan.benchmark], window=hypothesis.beta_window
        )
    return scores


def _payoffs(plan, scores, labels, dates):
    horizon = plan.target.horizon_bars
    observations = []
    # Positions start after decisions, last exactly H session bars and never overlap.
    for offset in range(0, len(dates) - horizon, horizon):
        stamp = dates[offset]
        observed = scores.loc[stamp]
        available = bool(np.isfinite(observed.to_numpy()).all())
        weights = basket_weights(observed, plan.top_k) if available else pd.Series(0.0, index=plan.symbols)
        outcomes = labels.loc[stamp]
        if not np.isfinite(outcomes.to_numpy()).all():
            raise ValueError("Missing future price cannot be used for basket membership or flat returns")
        gross = float(weights @ outcomes)
        entered = float(weights.abs().sum())
        exited = float(weights.abs() @ (1 + outcomes))
        benchmark = float(outcomes.mean())
        costs = []
        for cost in plan.costs_bps:
            fee = cost / BASIS_POINTS * (entered + exited)
            costs.append(
                {
                    "cost_bps": cost,
                    "net_return": gross - fee,
                    "fees": fee,
                    "benchmark_return": benchmark - cost / BASIS_POINTS * (2 + benchmark),
                }
            )
        observations.append(
            {
                "signal_bar": stamp.isoformat(),
                "assumed_decision_at": (stamp + pd.DateOffset(days=1)).isoformat(),
                "entry_bar": dates[offset + 1].isoformat(),
                "exit_bar": dates[offset + horizon].isoformat(),
                "features_available": available,
                "weights": weights.to_dict(),
                "gross_return": gross,
                "entry_gross": entered,
                "exit_gross": exited,
                "costs": costs,
            }
        )
    return observations


def _payoff_summary(observations, cost):
    rows = [next(c for c in o["costs"] if c["cost_bps"] == cost) for o in observations]
    returns = np.array([r["net_return"] for r in rows])
    if not len(returns) or not np.isfinite(returns).all() or (returns <= -1).any():
        raise ValueError("Complete finite positive-capital payoff observations required")
    log = np.log1p(returns)
    total = float(log.sum())
    wealth = np.exp(log.cumsum())
    peak = np.maximum.accumulate(np.r_[1.0, wealth])[1:]
    return {
        "cost_bps": cost,
        "baskets": len(rows),
        "net_return": float(np.expm1(total)),
        "benchmark_return": float(np.prod([1 + r["benchmark_return"] for r in rows]) - 1),
        "marked_boundary_drawdown": float(np.max(1 - wealth / peak)),
        "sum_fees_per_basket_capital": float(sum(r["fees"] for r in rows)),
        "largest_positive_basket_share_of_net_log_gain": float(log.max() / total) if total > 0 else None,
        "missing_features": sum(not o["features_available"] for o in observations),
        "mean_entry_gross": float(np.mean([o["entry_gross"] for o in observations])),
    }


def compute_panel_study(panel: DailyResearchPanel, plan: PanelStudyPlan):
    if set(panel.frames) != {*plan.symbols, plan.benchmark}:
        raise ValueError("Exact declared panel universe required")
    if not panel.complete:
        raise PanelCoverageError(panel.coverage)
    trials = []
    for hypothesis in plan.hypotheses:
        scores = panel_scores(panel, plan, hypothesis)
        for fold in plan.folds:
            dates = panel.close.index[(panel.close.index.date >= fold.start) & (panel.close.index.date <= fold.end)]
            if len(dates) <= plan.target.horizon_bars:
                raise ValueError("Fold has no matured forecast observations")
            # Label endpoints are restricted to this fold, including at year boundaries.
            labels = pd.DataFrame({s: forecast_labels(panel.frames[s].loc[dates], plan.target) for s in plan.symbols})
            mature = dates[: -plan.target.horizon_bars]
            ic = cross_sectional_ic(
                scores.loc[mature],
                labels.loc[mature],
                pd.Series(fold.name, index=mature),
                plan.ic,
                expected_index=mature,
                target=plan.target,
            )
            observations = _payoffs(plan, scores, labels, dates)
            trials.append(
                {
                    "hypothesis": hypothesis.name,
                    "fold": fold.name,
                    "status": PanelStudyStatus.COMPLETED,
                    "ic": ic.document(),
                    "payoffs": observations,
                    "cost_summaries": [_payoff_summary(observations, c) for c in plan.costs_bps],
                    "unmatured_tail_dates": [t.isoformat() for t in dates[-plan.target.horizon_bars :]],
                }
            )
    return {
        "version": PANEL_STUDY_VERSION,
        "charged_trials": plan.trial_count,
        "trials": trials,
        "coverage": panel.coverage,
        "authorizes_promotion": False,
        "limitations": [
            "Curated historical cohort is not survivorship-free or untouched confirmation.",
            "Native daily boundary prices and availability are assumptions, not verified auction/broker fills.",
            "Payoffs omit intrahorizon drawdown, borrow, dividends, corporate actions, market impact and partial fills.",
            "Fixed baskets are unleveraged gross exposure proxies, not protective-bracket execution.",
            "Per-fold HAC inference does not adjust for adaptive selection or cumulative research attempts.",
        ],
    }


@dataclass(frozen=True)
class PanelTriagePolicy:
    primary_cost_bps: float = 1.0
    stress_cost_bps: float = 5.0
    minimum_mean_ic: float = 0.03
    minimum_ic_dates_per_fold: int = 200
    minimum_baskets: int = 90
    maximum_positive_basket_share: float = 0.5

    def __post_init__(self):
        if any(
            isinstance(v, bool) or not isinstance(v, Real)
            for v in (
                self.primary_cost_bps,
                self.stress_cost_bps,
                self.minimum_mean_ic,
                self.maximum_positive_basket_share,
            )
        ):
            raise ValueError("Numeric panel screening policy required")
        if (
            not 0 <= self.primary_cost_bps < self.stress_cost_bps
            or not np.isfinite(
                [self.primary_cost_bps, self.stress_cost_bps, self.minimum_mean_ic, self.maximum_positive_basket_share]
            ).all()
            or not 0 <= self.minimum_mean_ic <= 1
            or not 0 < self.maximum_positive_basket_share <= 1
            or type(self.minimum_ic_dates_per_fold) is not int
            or self.minimum_ic_dates_per_fold < 2
            or type(self.minimum_baskets) is not int
            or self.minimum_baskets < 1
        ):
            raise ValueError("Explicit finite panel screening policy required")


def assess_panel_hypothesis(trials: list[dict], expected_folds: tuple[str, ...], policy: PanelTriagePolicy):
    """A fixed economic/forecast screen grants further research only, never significance."""
    decision = {"advance_to_further_research": False, "authorizes_promotion": False, "reasons": []}
    if (
        len(trials) != len(expected_folds)
        or {t["fold"] for t in trials} != set(expected_folds)
        or any(t["status"] != PanelStudyStatus.COMPLETED for t in trials)
    ):
        return {**decision, "reasons": ["incomplete_experiment"]}
    primary = []
    stress = []
    ics = []
    logs: list[float] = []
    for t in trials:
        by_cost = {r["cost_bps"]: r for r in t["cost_summaries"]}
        if policy.primary_cost_bps not in by_cost or policy.stress_cost_bps not in by_cost:
            return {**decision, "reasons": ["incomplete_experiment"]}
        primary.append(by_cost[policy.primary_cost_bps])
        stress.append(by_cost[policy.stress_cost_bps])
        ics.append(t["ic"]["folds"][t["fold"]])
        logs.extend(
            np.log1p(next(c["net_return"] for c in p["costs"] if c["cost_bps"] == policy.primary_cost_bps))
            for p in t["payoffs"]
        )
    observed = sum(i["observed"] for i in ics)
    mean_ic = (
        sum(i["mean_ic"] * i["observed"] for i in ics if i["mean_ic"] is not None) / observed if observed else None
    )
    gain = sum(logs)
    influence = float(max(logs) / gain) if logs and gain > 0 else None
    checks = {
        "coverage": all(i["coverage"] == 1 for i in ics)
        and all(t["missing_features"] == 0 for t in [*primary, *stress]),
        "mean_ic": mean_ic is not None and mean_ic >= policy.minimum_mean_ic,
        "ic_fold_stability": all(i["mean_ic"] is not None and i["mean_ic"] > 0 for i in ics),
        "sample_size": all(i["observed"] >= policy.minimum_ic_dates_per_fold for i in ics)
        and sum(t["baskets"] for t in primary) >= policy.minimum_baskets,
        "primary_stability": all(t["net_return"] > 0 for t in primary),
        "stress_stability": all(t["net_return"] > 0 for t in stress),
        "concentration": influence is not None and influence <= policy.maximum_positive_basket_share,
    }
    return {
        **decision,
        "advance_to_further_research": all(checks.values()),
        "reasons": [k for k, v in checks.items() if not v],
        "mean_ic": mean_ic,
        "baskets": sum(t["baskets"] for t in primary),
        "primary_net_return": float(np.prod([1 + t["net_return"] for t in primary]) - 1),
        "stress_net_return": float(np.prod([1 + t["net_return"] for t in stress]) - 1),
        "largest_positive_basket_share": influence,
    }
