from __future__ import annotations

import logging
import random

import numpy as np
import pandas as pd

from agentic_trader.research.alpha.catalog import AlphaCatalog
from agentic_trader.research.alpha.dsl import AlphaExpressionEvaluator
from agentic_trader.research.alpha.metrics import (
    calculate_cross_strategy_correlations,
    calculate_deflated_sharpe_ratio,
    calculate_rank_ic,
    simulate_alpha_performance,
)
from agentic_trader.research.alpha.models import (
    AlphaCandidate,
    AlphaDefinition,
    AlphaEvaluationMetrics,
    AlphaOrigin,
)


logger = logging.getLogger(__name__)


class AlphaMiner:
    """
    Quantitative Formulaic Alpha Mining & Genetic Search Engine.
    Discovers, optimizes, and validates formulaic alpha expressions with
    Deflated Sharpe Ratio (DSR) overfitting protection and correlation gating.
    """

    def __init__(
        self,
        evaluator: AlphaExpressionEvaluator | None = None,
        catalog: AlphaCatalog | None = None,
    ) -> None:
        self.evaluator = evaluator or AlphaExpressionEvaluator()
        self.catalog = catalog or AlphaCatalog()

    def generate_candidate_expression(self, seed: int | None = None) -> AlphaDefinition:
        """Generate a syntactically valid randomized formulaic alpha expression."""
        if seed is not None:
            random.seed(seed)

        fields = ["close", "volume", "open", "high", "low", "returns"]
        lookbacks = [3, 5, 8, 10, 14, 20, 30]

        f1 = random.choice(fields)
        f2 = random.choice(fields)
        d1 = random.choice(lookbacks)
        d2 = random.choice(lookbacks)
        d3 = random.choice(lookbacks)

        templates = [
            # Price-volume momentum synchronization
            (
                f"ts_corr(ts_rank({f1}, {d1}), ts_rank({f2}, {d2}), {d3})",
                "Cross-feature rank correlation",
                "bi_directional",
                0.6,
            ),
            # Mean reversion on extreme price displacement
            (
                f"-1.0 * delta({f1}, {d1}) * ts_rank({f2}, {d2})",
                "Volume/feature weighted mean reversion",
                "bi_directional",
                1.0,
            ),
            # Normalized moving average stretch
            (
                f"({f1} - sma({f1}, {d1})) / (ts_std({f1}, {d1}) + 1e-6)",
                "Moving average z-score stretch",
                "bi_directional",
                1.5,
            ),
            # Volume acceleration counter-trend
            (
                f"-1.0 * delta(ts_rank(volume, {d1}), {d2})",
                "Volume exhaustion deceleration",
                "bi_directional",
                0.8,
            ),
            # Linearly decayed trend impulse
            (
                f"decay_linear(delta({f1}, {d1}), {d2})",
                "Linearly decayed displacement impulse",
                "bi_directional",
                1.0,
            ),
            # Range expansion breakout
            (
                f"ts_rank({f1} - sma({f1}, {d1}), {d2}) * ts_rank(volume, {d3})",
                "Trend-volume expansion breakout",
                "long",
                0.6,
            ),
        ]

        expr, desc, direction, thresh = random.choice(templates)
        alpha_hash = abs(hash(expr)) % 1000000
        alpha_id = f"alpha_m_{alpha_hash:06d}"

        return AlphaDefinition(
            alpha_id=alpha_id,
            name=f"Mined Alpha {alpha_id}",
            expression=expr,
            description=desc,
            direction=direction,
            entry_threshold=thresh,
            exit_threshold=0.0,
            timeframe="4h",
            origin=AlphaOrigin.MINED,
        )

    def evaluate_alpha(
        self,
        definition: AlphaDefinition,
        df: pd.DataFrame,
        train_ratio: float = 0.70,
        benchmark_returns: dict[str, pd.Series] | None = None,
        total_trials: int = 1,
        trial_variance: float = 0.5,
    ) -> AlphaCandidate | None:
        """
        Evaluate an alpha expression across In-Sample and Out-of-Sample splits.
        Computes Sharpe, Rank IC, Deflated Sharpe Ratio, and cross-strategy correlations.
        """
        try:
            alpha_series = self.evaluator.evaluate(definition.expression, df)
            close = df["Close"] if "Close" in df else df["close"]
        except Exception as e:
            logger.debug("Failed evaluating alpha %s (%s): %s", definition.alpha_id, definition.expression, e)
            return None

        n = len(df)
        if n < 50:
            return None

        split_idx = int(n * train_ratio)
        is_alpha, oos_alpha = alpha_series.iloc[:split_idx], alpha_series.iloc[split_idx:]
        is_close, oos_close = close.iloc[:split_idx], close.iloc[split_idx:]

        # Simulate In-Sample
        is_sim = simulate_alpha_performance(
            is_alpha,
            is_close,
            entry_threshold=definition.entry_threshold,
            exit_threshold=definition.exit_threshold,
            direction=definition.direction,
        )

        # Simulate Out-of-Sample
        oos_sim = simulate_alpha_performance(
            oos_alpha,
            oos_close,
            entry_threshold=definition.entry_threshold,
            exit_threshold=definition.exit_threshold,
            direction=definition.direction,
        )

        # Compute Out-of-Sample Rank IC (1-bar and 5-bar forward return)
        fwd_returns_1 = oos_close.pct_change(1).shift(-1).fillna(0.0)
        ic_mean, ic_std, ic_ir = calculate_rank_ic(oos_alpha, fwd_returns_1, window=min(30, len(oos_alpha) // 2))

        # Deflated Sharpe Ratio
        dsr = calculate_deflated_sharpe_ratio(
            sharpe=oos_sim["sharpe"],
            num_trials=max(1, total_trials),
            variance_trials=max(0.1, trial_variance),
            sample_length=oos_sim["sample_length"],
            skewness=oos_sim["skewness"],
            kurtosis=oos_sim["kurtosis"],
        )

        # Correlations with active desk strategies
        correlations: dict[str, float] = {}
        if benchmark_returns:
            correlations = calculate_cross_strategy_correlations(
                candidate_returns=oos_sim["net_returns"],
                benchmark_returns=benchmark_returns,
            )

        metrics = AlphaEvaluationMetrics(
            rank_ic_mean=ic_mean,
            rank_ic_std=ic_std,
            rank_ic_ir=ic_ir,
            sharpe_is=is_sim["sharpe"],
            sharpe_oos=oos_sim["sharpe"],
            dsr=dsr,
            win_rate=oos_sim["win_rate"],
            profit_factor=oos_sim["profit_factor"],
            max_drawdown_pct=oos_sim["max_drawdown_pct"],
            total_trades=oos_sim["total_trades"],
            annualized_return_pct=oos_sim["total_return_pct"],
        )

        return AlphaCandidate(
            definition=definition,
            metrics=metrics,
            correlations=correlations,
        )

    def mine(
        self,
        df: pd.DataFrame,
        iterations: int = 20,
        include_catalog: bool = True,
        train_ratio: float = 0.70,
        min_sharpe: float = 1.0,
        min_dsr: float = 0.85,
        min_ic: float = 0.01,
        max_correlation: float = 0.50,
        benchmark_returns: dict[str, pd.Series] | None = None,
    ) -> list[AlphaCandidate]:
        """
        Execute exploration search combining pre-cataloged alphas and genetic formula generation.
        Returns ranked alpha candidates passing minimum quantitative gating filters.
        """
        candidates_to_test: list[AlphaDefinition] = []

        if include_catalog:
            candidates_to_test.extend(self.catalog.list_alphas())

        # Generate unique formula expressions
        seen_exprs = {c.expression for c in candidates_to_test}
        attempts = 0
        while len(candidates_to_test) < (len(self.catalog.list_alphas()) if include_catalog else 0) + iterations:
            attempts += 1
            if attempts > iterations * 5:
                break
            cand_def = self.generate_candidate_expression()
            if cand_def.expression not in seen_exprs and self.evaluator.validate(cand_def.expression):
                seen_exprs.add(cand_def.expression)
                candidates_to_test.append(cand_def)

        total_tested = len(candidates_to_test)

        # Preliminary pass to gauge Sharpe variance across all tested trials
        raw_candidates: list[AlphaCandidate] = []
        trial_sharpes: list[float] = []

        for c_def in candidates_to_test:
            evaluated = self.evaluate_alpha(
                definition=c_def,
                df=df,
                train_ratio=train_ratio,
                benchmark_returns=benchmark_returns,
                total_trials=total_tested,
                trial_variance=0.5,  # initial placeholder
            )
            if evaluated:
                raw_candidates.append(evaluated)
                trial_sharpes.append(evaluated.metrics.sharpe_oos)

        if not raw_candidates:
            return []

        # Recalculate true empirical variance of trials for DSR precision
        empirical_variance = float(np.var(trial_sharpes)) if len(trial_sharpes) > 1 else 0.5
        for c in raw_candidates:
            c.metrics.dsr = calculate_deflated_sharpe_ratio(
                sharpe=c.metrics.sharpe_oos,
                num_trials=total_tested,
                variance_trials=max(0.1, empirical_variance),
                sample_length=int(len(df) * (1.0 - train_ratio)),
            )

        # Apply gating criteria
        qualified: list[AlphaCandidate] = []
        for c in raw_candidates:
            if c.metrics.sharpe_oos < min_sharpe:
                continue
            if c.metrics.dsr < min_dsr:
                continue
            if c.metrics.rank_ic_mean < min_ic:
                continue
            # Correlation check
            if c.correlations:
                max_observed_corr = max(abs(v) for v in c.correlations.values())
                if max_observed_corr > max_correlation:
                    continue
            qualified.append(c)

        # Rank candidates by composite score: 0.5 * Sharpe + 0.3 * DSR + 0.2 * IC_IR
        def _score(cand: AlphaCandidate) -> float:
            m = cand.metrics
            ic_part = max(0.0, min(2.0, m.rank_ic_ir))
            return float(0.5 * m.sharpe_oos + 0.3 * m.dsr + 0.2 * ic_part)

        qualified.sort(key=_score, reverse=True)
        return qualified
