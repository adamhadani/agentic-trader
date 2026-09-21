from __future__ import annotations

import hashlib
import logging
import random
import time
from dataclasses import asdict, replace

import numpy as np
import pandas as pd

from agentic_trader.market.bars import FIXED_BAR_LAYOUT, fixed_bar_closes
from agentic_trader.research.alpha.catalog import AlphaCatalog
from agentic_trader.research.alpha.dsl import AlphaExpressionEvaluator, compile_expression
from agentic_trader.research.alpha.forecasts import ForecastCalibration, ForecastContract
from agentic_trader.research.alpha.metrics import (
    calculate_cross_strategy_correlations,
    calculate_deflated_sharpe_ratio,
    calculate_rank_ic,
)
from agentic_trader.research.alpha.models import (
    DAILY_SESSION_SEMANTICS_VERSION,
    AlphaCandidate,
    AlphaDefinition,
    AlphaEvaluationMetrics,
    AlphaOrigin,
)
from agentic_trader.research.alpha.search import TypedGeneticSearch
from agentic_trader.research.alpha.simulation import return_statistics, simulate_strategy
from agentic_trader.research.alpha.strategy import AlphaExecutionPolicy, TimedAlphaExecutionPolicy, alpha_scores
from agentic_trader.research.alpha.targets import ForecastTarget
from agentic_trader.research.alpha.validation import ValidationPolicy, frame_digest, purged_folds, validate_sampling


logger = logging.getLogger(__name__)


SEARCH_FIELDS = ("close", "volume", "open", "high", "low", "returns", "hl_spread", "oc_spread", "open_gap", "vwap")
SEARCH_WINDOWS = (3, 5, 8, 10, 14, 20, 30, 60)


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
        seed: int = 20260916,
        policy: ValidationPolicy | None = None,
    ) -> None:
        self.seed = seed
        self.rng = random.Random(seed)
        self.policy = policy or ValidationPolicy()
        self.last_run: dict = {}
        self.evaluator = evaluator or AlphaExpressionEvaluator()
        self.catalog = catalog or AlphaCatalog()

    def generate_candidate_expression(
        self, seed: int | None = None, *, available_fields: set[str] | None = None
    ) -> AlphaDefinition:
        """Generate a syntactically valid randomized formulaic alpha expression."""
        rng = random.Random(seed) if seed is not None else self.rng

        fields = [field for field in SEARCH_FIELDS if available_fields is None or field in available_fields]
        if len(fields) < 2:
            raise ValueError("At least two causal research fields are required")
        lookbacks = SEARCH_WINDOWS

        f1 = rng.choice(fields)
        f2 = rng.choice(fields)
        d1 = rng.choice(lookbacks)
        d2 = rng.choice(lookbacks)
        d3 = rng.choice(lookbacks)

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
            # Overnight gap reversal, measured only from the prior close.
            (
                f"-1.0 * open_gap * ts_rank(volume, {d1})",
                "Overnight gap reversal conditioned on volume",
                "bi_directional",
                0.8,
            ),
            # Intraday range and close-to-close return dependence.
            (
                f"ts_corr(hl_spread, returns, {d1})",
                "Range-return dependence",
                "bi_directional",
                0.6,
            ),
            # Volatility shock, normalized by its own causal history.
            (
                f"zscore(realized_vol(returns, {d1}), {d2})",
                "Realized-volatility shock",
                "bi_directional",
                1.0,
            ),
            # Open-close spread and participation alignment.
            (
                f"ts_rank(oc_spread, {d1}) * ts_rank(volume, {d2})",
                "Open-close participation alignment",
                "bi_directional",
                0.6,
            ),
            # Scale trend by only the volatility known at the same bar.
            (
                f"roc(close, {d1}) / (realized_vol(returns, {d2}) + 1e-6)",
                "Volatility-scaled trend impulse",
                "bi_directional",
                0.6,
            ),
            # Where the close sits in the preceding observed range.  High and
            # low are complete at a bar close; no future extrema are used.
            (
                f"(close - ts_min(low, {d1})) / (ts_max(high, {d1}) - ts_min(low, {d1}) + 1e-6)",
                "Causal range location",
                "bi_directional",
                0.6,
            ),
            # Directional participation is dimensionless and keeps volume
            # normalized by its own trailing history.
            (
                f"sign(returns) * zscore(volume, {d1})",
                "Signed volume pressure",
                "bi_directional",
                0.6,
            ),
            # Short-memory return dependence, with the lagged return observed
            # before the current close.
            (
                f"ts_corr(returns, delay(returns, 1), {d1})",
                "Serial return dependence",
                "bi_directional",
                0.6,
            ),
            # Smooth the price slope before standardizing it against its own
            # causal history; this is distinct from raw displacement.
            (
                f"zscore(ts_slope(close, {d1}), {d2})",
                "Standardized trend slope",
                "bi_directional",
                0.6,
            ),
        ]

        # Derived-field templates are only valid when the acquisition supplied
        # the necessary OHLCV columns.  Keep random discovery fail-closed rather
        # than spending its budget on expressions that cannot be evaluated.
        templates = [
            template for template in templates if compile_expression(template[0]).required_fields.issubset(fields)
        ]

        expr, desc, direction, thresh = rng.choice(templates)
        alpha_id = "alpha_m_" + hashlib.sha256(expr.encode()).hexdigest()[:16]

        return AlphaDefinition(
            alpha_id=alpha_id,
            name=f"Mined Alpha {alpha_id}",
            expression=expr,
            description=desc,
            direction=direction,
            entry_threshold=thresh,
            timeframe="4h",
            origin=AlphaOrigin.MINED,
        )

    def _candidate(self, definition, df, validation, training, *, total_trials=1, trial_variance=0):
        scores = alpha_scores(definition, df, self.evaluator)
        simulations = [simulate_strategy(definition, df, start=a, end=b, scores=scores) for a, b in validation]
        returns = pd.concat([s["net_returns"] for s in simulations])
        sim = return_statistics(returns, [t for s in simulations for t in s["trades"]])
        train = simulate_strategy(definition, df, start=0, end=training, scores=scores)
        close = df.rename(columns=str.lower).close
        # The last label in each fold is unknown inside that fold, so exclude it.
        labels = close.shift(-self.policy.label_horizon) / close - 1
        indices = np.concatenate([np.arange(a, max(a, b - self.policy.label_horizon)) for a, b in validation])
        ic, std, ir = calculate_rank_ic(scores.iloc[indices], labels.iloc[indices], window=20)
        metrics = AlphaEvaluationMetrics(
            rank_ic_mean=ic,
            rank_ic_std=std,
            rank_ic_ir=ir,
            sharpe_is=train["sharpe"],
            sharpe_oos=sim["sharpe"],
            dsr=calculate_deflated_sharpe_ratio(
                sim["per_bar_sharpe"],
                total_trials,
                trial_variance,
                sim["sample_length"],
                sim["skewness"],
                sim["kurtosis"],
            ),
            win_rate=sim["win_rate"],
            profit_factor=sim["profit_factor"],
            max_drawdown_pct=sim["max_drawdown_pct"],
            total_trades=sim["total_trades"],
            annualized_return_pct=sim["annualized_return_pct"],
            per_bar_sharpe=sim["per_bar_sharpe"],
            sample_length=sim["sample_length"],
            skewness=sim["skewness"],
            kurtosis=sim["kurtosis"],
        )
        calibration = None
        try:
            # Scores/labels are available at bar CLOSE, not the start-labelled index.
            closes = fixed_bar_closes(df, definition.timeframe)
            train_end = training - self.policy.label_horizon
            availability = pd.Series(closes, index=closes).shift(-self.policy.label_horizon)
            calibrated = ForecastCalibration.fit(
                pd.Series(scores.to_numpy(), index=closes).iloc[:train_end],
                pd.Series(labels.to_numpy(), index=closes).iloc[:train_end],
                label_observed_at=availability.iloc[:train_end],
                trained_until=closes[training - 1].isoformat(),
                contract=ForecastContract(
                    ForecastTarget(definition.timeframe, self.policy.label_horizon),
                    definition.data_feed,
                    definition.adjustment,
                    FIXED_BAR_LAYOUT,
                    "USD",
                ),
            )
            calibration = calibrated.document()
        except ValueError:
            pass
        candidate = AlphaCandidate(
            definition,
            metrics,
            evidence={
                "version_id": definition.version_id,
                "validation_intervals": validation,
                "fold_sharpes": [s["sharpe"] for s in simulations],
                "validation_coverage": [s["feature_coverage"] for s in simulations],
                "training_coverage": train["feature_coverage"],
                "trial_count": total_trials,
                "holdout_evaluated": False,
                "calibration": calibration,
                "execution_model": "gtc_limit_conservative_brackets_v2",
            },
        )
        return candidate, returns

    def evaluate_alpha(
        self,
        definition: AlphaDefinition,
        df: pd.DataFrame,
        train_ratio: float = 0.7,
        benchmark_returns: dict[str, pd.Series] | None = None,
        total_trials: int = 1,
        trial_variance: float = 0,
    ) -> AlphaCandidate | None:
        """Diagnostic split evaluation; this alone never authorizes promotion."""
        validate_sampling(df, definition.timeframe)
        if not 0.2 <= train_ratio <= 0.9:
            raise ValueError("Invalid training split")
        if len(df) < 50:
            return None
        split = int(len(df) * train_ratio)
        candidate, returns = self._candidate(
            definition, df, [(split, len(df))], split, total_trials=total_trials, trial_variance=trial_variance
        )
        if benchmark_returns:
            candidate.correlations = calculate_cross_strategy_correlations(returns, benchmark_returns)
        return candidate

    def mine(
        self,
        df: pd.DataFrame,
        iterations: int = 20,
        include_catalog: bool = True,
        min_sharpe: float = 1,
        min_dsr: float = 0.95,
        min_ic: float = 0.01,
        max_correlation: float = 0.5,
        benchmark_returns: dict[str, pd.Series] | None = None,
        *,
        timeframe: str = "1d",
        symbol: str | None = None,
        method: str = "random",
        max_seconds: float = 300,
        execution: AlphaExecutionPolicy | None = None,
    ) -> list[AlphaCandidate]:
        """Seeded discovery over purged validation folds; never inspect the holdout.

        All trials, including failures, are retained in last_run for journal persistence.
        Relaxing display gates changes discovery output, never promotion policy.
        """
        if isinstance(execution, TimedAlphaExecutionPolicy) and timeframe != "1d":
            raise ValueError("Session-bounded entries require native daily bars")
        validate_sampling(df, timeframe)
        if method not in ("random", "genetic"):
            raise ValueError("Unknown discovery method")
        if not np.isfinite(max_seconds) or max_seconds <= 0:
            raise ValueError("Positive finite compute budget required")
        deadline = time.monotonic() + max_seconds
        search = TypedGeneticSearch(self.seed) if method == "genetic" else None
        if not 0 <= iterations <= 10000:
            raise ValueError("Trial budget must be between zero and 10000")
        folds = purged_folds(len(df), self.policy)
        holdout_start = int(len(df) * (1 - self.policy.holdout_fraction))
        discovery = df.iloc[:holdout_start]
        available_fields = set(self.evaluator.prepare_data_fields(discovery))
        definitions = (
            [
                replace(
                    d,
                    timeframe=timeframe,
                    eligible_symbols=(symbol,) if symbol else None,
                    data_feed=df.attrs.get("feed", "unverified"),
                )
                for d in self.catalog.list_alphas()
            ]
            if include_catalog
            else []
        )
        seen = {d.expression for d in definitions}
        budget = len(definitions) + iterations
        definitions = [
            replace(d, data_feed=df.attrs.get("feed", "unverified"), adjustment=df.attrs.get("adjustment", "raw"))
            for d in definitions
        ]
        trials, evaluated = [], []
        semantic_signatures = set()
        exhausted = False
        for trial_number in range(budget):
            if time.monotonic() >= deadline:
                break
            if trial_number < len(definitions):
                definition = definitions[trial_number]
            else:
                for _ in range(100):
                    if search:
                        try:
                            expression = search.ask()
                        except ValueError:
                            exhausted = True
                            break
                        definition = AlphaDefinition(
                            "alpha_gp_" + hashlib.sha256(expression.encode()).hexdigest()[:16],
                            "Genetic candidate",
                            expression,
                            timeframe=timeframe,
                            eligible_symbols=(symbol,) if symbol else None,
                        )
                    else:
                        definition = replace(
                            self.generate_candidate_expression(available_fields=available_fields),
                            timeframe=timeframe,
                            eligible_symbols=(symbol,) if symbol else None,
                        )
                    if definition.expression not in seen:
                        seen.add(definition.expression)
                        break
                else:
                    exhausted = True
                if exhausted:
                    break
            definition = replace(
                definition, data_feed=df.attrs.get("feed", "unverified"), adjustment=df.attrs.get("adjustment", "raw")
            )
            if execution is not None:
                # Evaluate, select and journal the policy that will actually trade.
                definition = replace(
                    definition,
                    execution=execution,
                    semantics_version=DAILY_SESSION_SEMANTICS_VERSION
                    if isinstance(execution, TimedAlphaExecutionPolicy)
                    else definition.semantics_version,
                )
            try:
                scores = alpha_scores(definition, discovery).round(10)
                signature = hashlib.sha256(pd.util.hash_pandas_object(scores, index=True).values.tobytes()).hexdigest()
                semantic_key = (signature, definition.entry_threshold, definition.direction)
                if semantic_key in semantic_signatures:
                    raise ValueError("Duplicate normalized score behavior in discovery observations")
                semantic_signatures.add(semantic_key)
                candidate, returns = self._candidate(
                    definition, discovery, [(f.validation_start, f.validation_end) for f in folds], folds[0].train_end
                )
                if benchmark_returns:
                    candidate.correlations = calculate_cross_strategy_correlations(returns, benchmark_returns)
                if search:
                    search.tell(definition.expression, candidate.metrics.sharpe_oos - 0.01 * len(definition.expression))
                evaluated.append(candidate)
                trials.append(
                    {"definition": definition.to_dict(), "status": "evaluated", "candidate": candidate.to_dict()}
                )
            except (ValueError, ArithmeticError) as exc:
                trials.append({"definition": definition.to_dict(), "status": "rejected", "reason": str(exc)})
        variance = float(np.var([c.metrics.per_bar_sharpe for c in evaluated], ddof=1)) if len(evaluated) > 1 else 0
        for c in evaluated:
            m = c.metrics
            m.dsr = calculate_deflated_sharpe_ratio(
                m.per_bar_sharpe, len(trials), variance, m.sample_length, m.skewness, m.kurtosis
            )
            c.evidence["trial_count"] = len(trials)
            c.evidence["trial_variance"] = variance
        # Capture updated trial metrics, not stale pre-adjustment dictionaries.
        by_version = {c.definition.version_id: c for c in evaluated}
        for trial in trials:
            if trial["status"] == "evaluated":
                trial["candidate"] = by_version[AlphaDefinition.from_dict(trial["definition"]).version_id].to_dict()
        self.last_run = {
            "seed": self.seed,
            "status": "search_exhausted" if exhausted else "completed" if len(trials) == budget else "budget_exhausted",
            "max_seconds": max_seconds,
            "requested_trials": budget,
            "method": method,
            "policy": asdict(self.policy),
            "timeframe": timeframe,
            "discovery_hash": frame_digest(discovery),
            "holdout_start": holdout_start,
            "trial_count": len(trials),
            "trials": trials,
        }
        qualified = [
            c
            for c in evaluated
            if c.metrics.sharpe_oos >= min_sharpe
            and c.metrics.dsr >= min_dsr
            and c.metrics.rank_ic_mean >= min_ic
            and all(abs(v) <= max_correlation for v in c.correlations.values())
            and c.metrics.sample_length > 0
        ]
        qualified.sort(
            key=lambda c: (
                -(0.5 * c.metrics.sharpe_oos + 0.3 * c.metrics.dsr + 0.2 * max(0, min(2, c.metrics.rank_ic_ir))),
                c.definition.version_id,
            )
        )
        return qualified
