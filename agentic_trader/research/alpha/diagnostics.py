"""Descriptive forecast/action attribution; never selection or promotion authority."""

import numpy as np
import pandas as pd


FORECAST_DIAGNOSTICS_VERSION = "forecast_diagnostics_v1"
INFLUENCE_ROWS = 5
DISTRIBUTION_QUANTILES = (0.05, 0.5, 0.95)


def _distribution(values: pd.Series):
    if values.empty:
        return {"mean": None, "std": None, "quantiles": None}
    return {
        "mean": float(values.mean()),
        "std": float(values.std(ddof=0)),
        "quantiles": {str(q): float(values.quantile(q)) for q in DISTRIBUTION_QUANTILES},
    }


def forecast_diagnostics(observations: pd.DataFrame) -> dict:
    """Use identical paired support; preserve missing counts and signed error gains.

    An influence share may exceed one because other observations offset its gain.
    Nonpositive net gain has no meaningful positive concentration ratio. Leaving a
    contribution out is attribution only; the original fit and rows are unchanged.
    """
    values = observations[["prediction", "target", "training_mean"]].replace([np.inf, -np.inf], np.nan)
    paired = values.dropna()
    baseline_loss = (paired.target - paired.training_mean) ** 2
    model_loss = (paired.target - paired.prediction) ** 2
    gains = baseline_loss - model_loss
    net = float(gains.sum()) if len(gains) else None
    positive = gains[gains > 0].sort_values(ascending=False, kind="stable")
    negative = gains[gains < 0].sort_values(kind="stable")

    def contributors(series):
        return [
            {"decision_at": str(index), "error_reduction": float(value)}
            for index, value in series.head(INFLUENCE_ROWS).items()
        ]

    return {
        "version": FORECAST_DIAGNOSTICS_VERSION,
        "rows": len(values),
        "paired_rows": len(paired),
        "unavailable_predictions": int(values.prediction.isna().sum()),
        "unavailable_targets": int(values.target.isna().sum()),
        "unavailable_training_means": int(values.training_mean.isna().sum()),
        "paired_distributions": {column: _distribution(paired[column]) for column in paired},
        "direction_definition": "positive_vs_nonpositive",
        "direction_accuracy": float((paired.prediction.gt(0) == paired.target.gt(0)).mean()) if len(paired) else None,
        "always_positive_accuracy": float(paired.target.gt(0).mean()) if len(paired) else None,
        "error_reduction": {
            "sum": net,
            "positive_sum": float(positive.sum()) if len(gains) else None,
            "negative_sum": float(negative.sum()) if len(gains) else None,
            "positive_observations": len(positive),
            "negative_observations": len(negative),
            "largest_positive_share_of_net": float(positive.iloc[0] / net) if net is not None and net > 0 else None,
            "top_positive_share_of_net": float(positive.head(INFLUENCE_ROWS).sum() / net)
            if net is not None and net > 0
            else None,
            "net_without_largest_positive": float(net - positive.iloc[0]) if len(positive) else None,
            "most_positive": contributors(positive),
            "most_negative": contributors(negative),
        },
        "authorizes_promotion": False,
    }


def policy_diagnostics(observations: pd.DataFrame) -> dict:
    """Describe the saved full execution clock, retaining cash and abstention.

    Skipped returns belong to the fixed comparator, not hypothetical model trades.
    Its cost-adjusted return on skipped days exactly explains model excess return.
    """
    required = ["position", "benchmark_position", "gross_return", "net_return", "benchmark_return", "turnover_legs"]
    if not np.isfinite(observations[required].to_numpy(dtype=float)).all():
        raise ValueError("Policy diagnostics require complete finite observations")
    entered = observations.position.gt(0)
    eligible = observations.benchmark_position.gt(0)
    skipped = eligible & ~entered
    gross_wealth = float((1 + observations.gross_return).prod())
    net_wealth = float((1 + observations.net_return).prod())
    return {
        "version": FORECAST_DIAGNOSTICS_VERSION,
        "observations": len(observations),
        "eligible_observations": int(eligible.sum()),
        "entered_observations": int(entered.sum()),
        "skipped_observations": int(skipped.sum()),
        "exposure_fraction": float(entered.sum() / eligible.sum()) if eligible.any() else None,
        "entered_net_return_mean": float(observations.net_return.loc[entered].mean()) if entered.any() else None,
        "skipped_comparator_net_return_mean": float(observations.benchmark_return.loc[skipped].mean())
        if skipped.any()
        else None,
        "mean_excess_return": float((observations.net_return - observations.benchmark_return).mean())
        if len(observations)
        else None,
        "turnover_legs": float(observations.turnover_legs.sum()),
        "gross_return_pct": (gross_wealth - 1) * 100,
        "net_return_pct": (net_wealth - 1) * 100,
        "cost_drag_percentage_points": (gross_wealth - net_wealth) * 100,
        "cost_only_wealth_multiplier": net_wealth / gross_wealth if gross_wealth > 0 else None,
        "authorizes_promotion": False,
    }
