"""Frozen monthly ETF book diagnostic, reusing panel, IC, journal and artifact contracts."""

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from agentic_trader.research.alpha.information import cross_sectional_ic
from agentic_trader.research.alpha.panel import PanelCoverageError, align_daily_panel
from agentic_trader.research.alpha.panel_study import PanelStudyPlan, PanelStudyStatus, basket_weights, panel_scores
from agentic_trader.research.alpha.persistent_book import (
    MAX_BOOK_SESSIONS,
    BookMode,
    BookPolicy,
    CostScenario,
    dependence_blend,
    simulate_book,
)
from agentic_trader.research.alpha.targets import forecast_labels


PERSISTENT_STUDY_VERSION = "persistent_daily_panel_v1"
DEPENDENCE_BLEND = "dependence_blend"


@dataclass(frozen=True)
class BookTriage:
    primary_cost_bps: float = 1.0
    stress_cost_bps: float = 5.0
    minimum_mean_ic: float = 0.03
    minimum_rebalances: int = 20
    maximum_drawdown: float = 0.2
    maximum_positive_block_share: float = 0.5

    def __post_init__(self):
        values = (
            self.primary_cost_bps,
            self.stress_cost_bps,
            self.minimum_mean_ic,
            self.maximum_drawdown,
            self.maximum_positive_block_share,
        )
        if (
            not np.isfinite(values).all()
            or any(isinstance(v, bool) for v in values)
            or not 0 <= self.primary_cost_bps < self.stress_cost_bps
            or not 0 <= self.minimum_mean_ic <= 1
            or not 0 < self.maximum_drawdown < 1
            or not 0 < self.maximum_positive_block_share <= 1
            or type(self.minimum_rebalances) is not int
            or self.minimum_rebalances < 1
        ):
            raise ValueError("Finite predeclared research triage required")


@dataclass(frozen=True)
class PersistentStudyPlan(PanelStudyPlan):
    rebalance_sessions: int = 20
    turnover_buffer: float = 0.1
    blend_window: int = 126
    blend_shrinkage: float = 0.2
    borrow_bps: tuple[float, ...] = (0.0, 100.0, 300.0)
    funding_bps: tuple[float, ...] = (0.0, 300.0, 500.0)
    triage: BookTriage = BookTriage()

    def __post_init__(self):
        super().__post_init__()
        BookPolicy(BookMode.BUFFERED, self.rebalance_sessions, self.turnover_buffer)
        if (
            type(self.blend_window) is not int
            or not 2 <= self.blend_window <= MAX_BOOK_SESSIONS
            or isinstance(self.blend_shrinkage, bool)
            or not np.isfinite(self.blend_shrinkage)
            or not 0 < self.blend_shrinkage <= 1
            or len(self.hypotheses) < 2
            or DEPENDENCE_BLEND in [h.name for h in self.hypotheses]
        ):
            raise ValueError("Explicit bounded dependence blend required")
        if (
            len(self.borrow_bps) != len(self.costs_bps)
            or len(self.funding_bps) != len(self.costs_bps)
            or not isinstance(self.borrow_bps, tuple)
            or not isinstance(self.funding_bps, tuple)
        ):
            raise ValueError("One declared borrow/funding assumption per cost scenario required")
        for values in zip(self.costs_bps, self.borrow_bps, self.funding_bps, strict=True):
            CostScenario(*values)
        if any(c not in self.costs_bps for c in (self.triage.primary_cost_bps, self.triage.stress_cost_bps)):
            raise ValueError("Primary and stress costs must appear in the frozen matrix")

    @property
    def adjustment(self):
        return "all"

    @property
    def trial_count(self):
        return (len(self.hypotheses) + 1) * len(self.folds) * (1 + len(BookMode) * len(self.costs_bps))

    def document(self):
        doc = super().document()
        doc.update(
            version=PERSISTENT_STUDY_VERSION,
            adjustment=self.adjustment,
            rebalance_sessions=self.rebalance_sessions,
            turnover_buffer=self.turnover_buffer,
            blend_window=self.blend_window,
            blend_shrinkage=self.blend_shrinkage,
            borrow_bps=list(self.borrow_bps),
            funding_bps=list(self.funding_bps),
            triage=asdict(self.triage),
            modes=list(BookMode),
            blend=DEPENDENCE_BLEND,
            payoff_scope="Self-financing adjusted-price-unit research book; prior-close quantities, next-session opens, daily marks, terminal close liquidation",
            corporate_action_scope="Alpaca all-adjusted price ratios are total-return proxies; no extra dividend credit, physical share, payment-date or historical borrow claim",
            carry_scope="ACT/365 overnight and intraday short-notional borrow and negative-cash funding; zero positive-cash interest and short rebate",
            reset_scope="Matched monthly full roundtrip at the same open; isolates avoidable turnover, not the old disjoint five-session strategy",
            blend_scope="Trailing rank-score covariance with diagonal shrinkage, long-only minimum-variance weights; exact duplicate histories deduplicated before fitting; no return fitting",
        )
        return doc

    @classmethod
    def from_document(cls, document):
        args = cls.arguments_from_document(document)
        args.update(
            {k: document[k] for k in ("rebalance_sessions", "turnover_buffer", "blend_window", "blend_shrinkage")}
        )
        args.update(
            borrow_bps=tuple(document["borrow_bps"]),
            funding_bps=tuple(document["funding_bps"]),
            triage=BookTriage(**document["triage"]),
        )
        plan = cls(**args)
        if plan.document() != document:
            raise ValueError("Exact frozen persistent-book protocol and count required")
        return plan


def compute_persistent_study(frames, clock, plan: PersistentStudyPlan, sessions):
    panel = align_daily_panel(frames, clock, feed=plan.feed, adjustment=plan.adjustment)
    if set(frames) != {*plan.symbols, plan.benchmark}:
        raise ValueError("Exact frozen universe required")
    if not panel.complete:
        raise PanelCoverageError(panel.coverage)
    scores = {h.name: panel_scores(panel, plan, h) for h in plan.hypotheses}
    blend, blend_evidence = dependence_blend(scores, window=plan.blend_window, shrinkage=plan.blend_shrinkage)
    scores[DEPENDENCE_BLEND] = blend
    trials = []
    for name, score in scores.items():
        for fold in plan.folds:
            dates = clock[(clock.date >= fold.start) & (clock.date <= fold.end)]
            if len(dates) <= plan.target.horizon_bars:
                raise ValueError("Matured fold required")
            observed = score.loc[dates]
            if not np.isfinite(observed.to_numpy()).all():
                raise ValueError("Complete feature/blend warmup and fold coverage required")
            weights = observed.apply(lambda row: basket_weights(row, plan.top_k), axis=1)
            labels = pd.DataFrame({s: forecast_labels(panel.frames[s].loc[dates], plan.target) for s in plan.symbols})
            mature = dates[: -plan.target.horizon_bars]
            ic = cross_sectional_ic(
                observed.loc[mature],
                labels.loc[mature],
                pd.Series(fold.name, index=mature),
                plan.ic,
                expected_index=mature,
                target=plan.target,
            )
            books = []
            fold_sessions = tuple(s for s in sessions if fold.start <= s.date <= fold.end)
            for mode in BookMode:
                for values in zip(plan.costs_bps, plan.borrow_bps, plan.funding_bps, strict=True):
                    costs = CostScenario(*values)
                    book = simulate_book(
                        panel.opening.loc[dates, list(plan.symbols)],
                        panel.close.loc[dates, list(plan.symbols)],
                        weights,
                        fold_sessions,
                        BookPolicy(mode, plan.rebalance_sessions, plan.turnover_buffer),
                        costs,
                    )
                    logs = np.log1p([r["return"] for r in book["daily"]])
                    blocks = [
                        float(logs[i : i + plan.rebalance_sessions].sum())
                        for i in range(0, len(logs), plan.rebalance_sessions)
                    ]
                    books.append({"mode": mode, "costs": asdict(costs), "block_log_returns": blocks, **book})
            trials.append(
                {
                    "hypothesis": name,
                    "fold": fold.name,
                    "ic": ic.document(),
                    "books": books,
                    "status": PanelStudyStatus.COMPLETED,
                    "unmatured_tail_dates": [d.isoformat() for d in dates[-plan.target.horizon_bars :]],
                }
            )
    decisions = [_assess(trials, name, mode, plan) for name in scores for mode in BookMode]
    return {
        "version": PERSISTENT_STUDY_VERSION,
        "trials": trials,
        "decisions": decisions,
        "blend_evidence": blend_evidence,
        "coverage": panel.coverage,
        "charged_trials": plan.trial_count,
        "authorizes_promotion": False,
    }


def _assess(trials, name, mode, plan):
    selected = [t for t in trials if t["hypothesis"] == name]
    ics = [t["ic"]["folds"][t["fold"]] for t in selected]
    primary = [
        next(b for b in t["books"] if b["mode"] == mode and b["costs"]["cost_bps"] == plan.triage.primary_cost_bps)
        for t in selected
    ]
    stress = [
        next(b for b in t["books"] if b["mode"] == mode and b["costs"]["cost_bps"] == plan.triage.stress_cost_bps)
        for t in selected
    ]
    count = sum(i["observed"] for i in ics)
    mean = (
        sum(i["mean_ic"] * i["observed"] for i in ics) / count
        if count and all(i["mean_ic"] is not None for i in ics)
        else None
    )
    logs = [v for b in primary for v in b["block_log_returns"]]
    share = max(logs) / sum(logs) if logs and sum(logs) > 0 else None
    checks = {
        "coverage": all(i["coverage"] == 1 for i in ics),
        "mean_ic": mean is not None and mean >= plan.triage.minimum_mean_ic,
        "ic_fold_stability": all(i["mean_ic"] is not None and i["mean_ic"] > 0 for i in ics),
        "sample_size": all(i["observed"] >= plan.ic.min_observations for i in ics)
        and sum(b["rebalances"] for b in primary) >= plan.triage.minimum_rebalances,
        "primary_stability": all(b["net_return"] > 0 for b in primary),
        "stress_stability": all(b["net_return"] > 0 for b in stress),
        "drawdown": all(b["daily_marked_drawdown"] <= plan.triage.maximum_drawdown for b in stress),
        "concentration": share is not None and share <= plan.triage.maximum_positive_block_share,
    }
    return {
        "hypothesis": name,
        "mode": mode,
        "advance_to_further_research": all(checks.values()),
        "authorizes_promotion": False,
        "reasons": [k for k, v in checks.items() if not v],
        "mean_ic": mean,
        "primary_net_return": float(np.prod([1 + b["net_return"] for b in primary]) - 1),
        "stress_net_return": float(np.prod([1 + b["net_return"] for b in stress]) - 1),
        "largest_positive_block_share": share,
    }
