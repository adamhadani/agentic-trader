"""Predeclared setup-outcome study: protocol, splits, model selection and holdout.

Pure statistical core of the WS2a study. Every random draw is seeded from the
frozen protocol; the only I/O this module performs is through the injected
``development``/``holdout`` callables and ``save_json_report``. The holdout
callable must never be invoked before ``ranker.json`` and ``selection.json``
are saved -- that ordering is the whole point of a predeclared, one-shot
holdout, and a development failure must leave it unexamined.
"""

from __future__ import annotations

import hashlib
import json
import pickle
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, model_validator
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from agentic_trader.research.setups.features import CROSS_SECTIONAL, MARKET, SETUP
from agentic_trader.storage.artifacts import save_json_report


__all__ = [
    "HYPOTHESES",
    "SCORERS",
    "Fitted",
    "SetupStudyProtocol",
    "execute_setup_study",
    "fit_scorer",
    "holm",
    "model_features",
    "purged_walk_forward",
    "random_selection",
    "session_block_bootstrap",
    "spearman_by_session_bootstrap",
    "top_k_selection",
]

# Baseline plus the three fitted candidates, in the order ties are broken.
SCORERS: tuple[str, ...] = ("setup_quality", "logistic", "ridge", "hgb")
_FITTED_CANDIDATES: tuple[str, ...] = ("logistic", "ridge", "hgb")

# H1 (setup_quality) + H2a..H2h (one per CROSS_SECTIONAL feature, in declared order) + H3 (selection).
_H2_LETTERS = "abcdefgh"
HYPOTHESES: tuple[str, ...] = ("H1", *(f"H2{letter}" for letter in _H2_LETTERS), "H3")

_HGB_PICKLE_PROTOCOL = 5
# ~1.5 calendar days per trading session; a fixed approximation, not the real market calendar.
_CALENDAR_DAYS_PER_SESSION = 1.5


class SetupStudyProtocol(BaseModel, frozen=True, extra="forbid"):
    """A predeclared, hash-identified study protocol. See ``docs/superpowers/specs/...``."""

    version: Literal["setup_outcomes_v1"]
    universe_source: str
    feed: Literal["alpaca:sip"]
    adjustment: Literal["all"]
    scan_times_et: tuple[str, ...]
    max_hold_sessions: int
    cost_bps_per_side: tuple[float, ...]
    development: tuple[date, date]
    holdout: tuple[date, date]
    data_cutoff: date
    embargo_sessions: int
    bootstrap: dict
    cv_folds: int
    hgb_params: dict
    ridge_alpha: float
    logistic_C: float
    acceptance: dict
    features_version: Literal["setup_features_v1"]
    sector_etf: dict[str, str]
    strategy_config: dict

    @model_validator(mode="after")
    def _check_windows(self) -> SetupStudyProtocol:
        if self.max_hold_sessions < 1:
            raise ValueError("max_hold_sessions must be at least 1")
        if self.embargo_sessions < 0:
            raise ValueError("embargo_sessions must not be negative")
        if self.cv_folds < 2:
            raise ValueError("cv_folds must be at least 2")

        dev_start, dev_end = self.development
        hold_start, hold_end = self.holdout
        if dev_start >= dev_end:
            raise ValueError("development window must be non-empty and ordered (start < end)")
        if hold_start >= hold_end:
            raise ValueError("holdout window must be non-empty and ordered (start < end)")

        # Development labels run up to `max_hold_sessions` sessions past the last
        # development decision, so the holdout must start at least `embargo_sessions`
        # sessions later or late development outcomes overlap holdout prices. Weekdays
        # strictly between the two dates bound the sessions from above; the runner
        # re-checks against the real market calendar.
        gap_weekdays = int(np.busday_count((dev_end + timedelta(days=1)).isoformat(), hold_start.isoformat()))
        if gap_weekdays < max(1, self.embargo_sessions):
            raise ValueError(
                f"holdout must start at least {self.embargo_sessions} sessions after development ends "
                f"(got {gap_weekdays} weekdays between {dev_end.isoformat()} and {hold_start.isoformat()})"
            )

        required_days = round(self.max_hold_sessions * _CALENDAR_DAYS_PER_SESSION)
        if (self.data_cutoff - hold_end).days < required_days:
            raise ValueError(
                "data_cutoff must leave enough calendar days for holdout labels to mature "
                f"(need >= {required_days} days after holdout end {hold_end.isoformat()}, "
                f"got data_cutoff {self.data_cutoff.isoformat()})"
            )
        return self

    def document(self) -> dict:
        return self.model_dump(mode="json")

    @property
    def identity(self) -> str:
        encoded = json.dumps(self.document(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(encoded.encode()).hexdigest()


# --- Session-block (stationary, Politis-Romano) bootstrap ---------------------------------


def _stationary_index_draws(n: int, block_mean: float, draws: int, seed: int) -> np.ndarray:
    """``draws`` resampled position sequences of length ``n`` over ``range(n)``.

    A stationary bootstrap: each block starts at a uniformly random position and continues
    (wrapping circularly) with probability ``1 - 1/block_mean`` per step, so blocks have a
    geometric length with mean ``block_mean``. This resamples whole *sessions* -- setups on
    the same session are not independent, so a single-row bootstrap would understate variance.
    """
    if n <= 0:
        return np.zeros((draws, 0), dtype=np.int64)
    rng = np.random.default_rng(seed)
    continue_probability = 1.0 - 1.0 / float(block_mean)
    out = np.empty((draws, n), dtype=np.int64)
    for d in range(draws):
        position = 0
        idx = 0
        while position < n:
            idx = int(rng.integers(0, n))
            out[d, position] = idx
            position += 1
            while position < n and rng.random() < continue_probability:
                idx = (idx + 1) % n
                out[d, position] = idx
                position += 1
    return out


def session_block_bootstrap(
    values_by_session: pd.Series,
    stat: Callable[[pd.Series], float],
    *,
    block_mean: int,
    draws: int,
    seed: int,
) -> np.ndarray:
    """Stationary bootstrap of one value per session; returns ``draws`` values of ``stat``."""
    ordered = values_by_session.sort_index()
    n = len(ordered)
    if n == 0:
        return np.zeros(0, dtype=float)
    values = ordered.to_numpy(dtype=float)
    index_draws = _stationary_index_draws(n, block_mean, draws, seed)
    return np.array([float(stat(pd.Series(values[row]))) for row in index_draws], dtype=float)


def spearman_by_session_bootstrap(
    frame: pd.DataFrame,
    feature: str,
    target: str,
    *,
    block_mean: int,
    draws: int,
    seed: int,
) -> dict:
    """Spearman(``feature``, ``target``) over all setups, with a whole-session block bootstrap CI."""
    valid = frame[[feature, target, "session"]].dropna(subset=[feature, target])
    if len(valid) < 2 or valid[feature].nunique() < 2 or valid[target].nunique() < 2:
        return {
            "rho": float("nan"),
            "ci90": [float("nan"), float("nan")],
            "p_one_sided": float("nan"),
            "n_setups": len(valid),
            "n_sessions": int(valid["session"].nunique()),
        }

    rho = float(spearmanr(valid[feature], valid[target]).statistic)
    sessions = sorted(valid["session"].unique())
    n_sessions = len(sessions)
    groups = {session: sub for session, sub in valid.groupby("session")}
    index_draws = _stationary_index_draws(n_sessions, block_mean, draws, seed)

    boot = np.full(len(index_draws), np.nan, dtype=float)
    for d, row in enumerate(index_draws):
        pooled = pd.concat([groups[sessions[i]] for i in row], ignore_index=True)
        if pooled[feature].nunique() < 2 or pooled[target].nunique() < 2:
            continue
        boot[d] = float(spearmanr(pooled[feature], pooled[target]).statistic)

    finite = boot[np.isfinite(boot)]
    if finite.size == 0:
        ci90 = [float("nan"), float("nan")]
        p_one_sided = float("nan")
    else:
        ci90 = [float(np.percentile(finite, 5)), float(np.percentile(finite, 95))]
        p_one_sided = float(np.mean(finite <= 0.0))

    return {
        "rho": rho,
        "ci90": ci90,
        "p_one_sided": p_one_sided,
        "n_setups": len(valid),
        "n_sessions": n_sessions,
    }


def holm(pvalues: Mapping[str, float]) -> dict[str, float]:
    """Holm step-down adjustment of a p-value family; returns monotone adjusted p-values."""
    items = sorted(pvalues.items(), key=lambda kv: kv[1])
    m = len(items)
    adjusted: dict[str, float] = {}
    running_max = 0.0
    for rank, (name, p) in enumerate(items):
        value = min(1.0, (m - rank) * p)
        running_max = max(running_max, value)
        adjusted[name] = running_max
    return adjusted


# --- Selection ------------------------------------------------------------------------------


def top_k_selection(frame: pd.DataFrame, score: str, target: str, k: int) -> pd.Series:
    """Per session, mean ``target`` of the ``k`` highest-``score`` rows (ties: symbol, strategy)."""
    results: dict = {}
    for session, group in frame.groupby("session"):
        if group.empty:
            continue
        ordered = group.sort_values([score, "symbol", "strategy"], ascending=[False, True, True])
        results[session] = float(ordered[target].head(k).mean())
    return pd.Series(results, dtype=float).sort_index()


def random_selection(frame: pd.DataFrame, target: str, k: int) -> pd.Series:
    """Expected value of a random size-``k`` draw per session: the session mean of ``target``."""
    del k  # Expectation of a mean of a random subset (any size) equals the population mean.
    return frame.groupby("session")[target].mean().astype(float).sort_index()


def purged_walk_forward(sessions: Sequence[date], folds: int, embargo: int) -> list[tuple[list[date], list[date]]]:
    """Expanding-window folds over ``sessions``, purging the last ``embargo`` train sessions."""
    ordered = sorted(set(sessions))
    n = len(ordered)
    chunk_count = folds + 1
    base, remainder = divmod(n, chunk_count)
    chunks: list[list[date]] = []
    start = 0
    for i in range(chunk_count):
        size = base + (1 if i < remainder else 0)
        chunks.append(ordered[start : start + size])
        start += size

    result: list[tuple[list[date], list[date]]] = []
    for i in range(folds):
        train_full = [d for chunk in chunks[: i + 1] for d in chunk]
        test = chunks[i + 1]
        if not test:
            result.append((train_full, test))
            continue
        test_start = test[0]
        train = [d for d in train_full if d < test_start]
        if embargo > 0:
            train = train[: max(0, len(train) - embargo)]
        result.append((train, test))
    return result


# --- Scorers ----------------------------------------------------------------------------


def model_features(frame: pd.DataFrame) -> list[str]:
    """Every numeric key produced by ``setup_vector``: CROSS_SECTIONAL + MARKET + SETUP + one-hot."""
    fixed = set(CROSS_SECTIONAL) | set(MARKET) | set(SETUP)
    return sorted(
        column for column in frame.columns if column in fixed or column.startswith(("strategy=", "timeframe="))
    )


@dataclass(frozen=True)
class Fitted:
    name: str
    features: tuple[str, ...]
    model: object
    _predict: Callable[[pd.DataFrame], np.ndarray]

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return np.asarray(self._predict(frame), dtype=float)


def fit_scorer(name: str, train: pd.DataFrame, features: Sequence[str], protocol: SetupStudyProtocol) -> Fitted:
    feature_list = list(features)

    if name == "setup_quality":
        return Fitted(name, tuple(feature_list), None, lambda frame: frame["setup_quality"].to_numpy(dtype=float))

    if name == "logistic":
        target = (train["hit"] == "target").astype(int).to_numpy()
        pipeline = Pipeline(
            [
                ("impute", SimpleImputer(strategy="median")),
                ("scale", StandardScaler()),
                ("model", LogisticRegression(C=protocol.logistic_C, max_iter=1000)),
            ]
        )
        pipeline.fit(train[feature_list], target)
        return Fitted(
            name, tuple(feature_list), pipeline, lambda frame: pipeline.predict_proba(frame[feature_list])[:, 1]
        )

    if name == "ridge":
        target = train["r_cost"].to_numpy(dtype=float)
        pipeline = Pipeline(
            [
                ("impute", SimpleImputer(strategy="median")),
                ("scale", StandardScaler()),
                ("model", Ridge(alpha=protocol.ridge_alpha)),
            ]
        )
        pipeline.fit(train[feature_list], target)
        return Fitted(name, tuple(feature_list), pipeline, lambda frame: pipeline.predict(frame[feature_list]))

    if name == "hgb":
        target = train["r_cost"].to_numpy(dtype=float)
        model = HistGradientBoostingRegressor(**protocol.hgb_params)
        model.fit(train[feature_list], target)
        return Fitted(name, tuple(feature_list), model, lambda frame: model.predict(frame[feature_list]))

    raise ValueError(f"Unknown scorer: {name!r}")


def _linear_payload(fitted: Fitted) -> dict:
    pipeline = fitted.model
    assert isinstance(pipeline, Pipeline)  # only "logistic"/"ridge" winners reach here.
    imputer = pipeline.named_steps["impute"]
    scaler = pipeline.named_steps["scale"]
    model = pipeline.named_steps["model"]
    intercept = float(np.asarray(model.intercept_, dtype=float).reshape(-1)[0])
    return {
        "coefficients": np.asarray(model.coef_, dtype=float).reshape(-1).tolist(),
        "intercept": intercept,
        "imputer_medians": np.asarray(imputer.statistics_, dtype=float).tolist(),
        "scaler_mean": np.asarray(scaler.mean_, dtype=float).tolist(),
        "scaler_scale": np.asarray(scaler.scale_, dtype=float).tolist(),
    }


# --- Study internals -----------------------------------------------------------------------


def _drop_immature(frame: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    immature = frame["hit"].astype(str) == "immature"
    dropped = int(immature.sum())
    return frame.loc[~immature].reset_index(drop=True), dropped


def _base_rates(frame: pd.DataFrame) -> list[dict]:
    rows = []
    for (strategy, timeframe, direction), group in frame.groupby(["strategy", "timeframe", "direction"]):
        hit_rate = {str(k): float(v) for k, v in group["hit"].astype(str).value_counts(normalize=True).items()}
        rows.append(
            {
                "strategy": str(strategy),
                "timeframe": str(timeframe),
                "direction": str(direction),
                "n": len(group),
                "hit_rate": hit_rate,
                "mean_r": float(group["r"].mean()),
                "mean_r_cost": float(group["r_cost"].mean()),
            }
        )
    return rows


def _bootstrap_kwargs(protocol: SetupStudyProtocol) -> dict:
    return {
        "block_mean": protocol.bootstrap["block_mean"],
        "draws": protocol.bootstrap["draws"],
        "seed": protocol.bootstrap["seed"],
    }


def _h1_h2(frame: pd.DataFrame, protocol: SetupStudyProtocol) -> tuple[dict, dict, dict]:
    boot_kwargs = _bootstrap_kwargs(protocol)
    h1 = spearman_by_session_bootstrap(frame, "setup_quality", "r_cost", **boot_kwargs)
    h2 = {
        f"H2{letter}": spearman_by_session_bootstrap(frame, feature, "r_cost", **boot_kwargs)
        for letter, feature in zip(_H2_LETTERS, CROSS_SECTIONAL, strict=True)
    }
    pvalues = {"H1": h1["p_one_sided"], **{name: result["p_one_sided"] for name, result in h2.items()}}
    return h1, h2, holm(pvalues)


def _fold_metrics(dev: pd.DataFrame, features: list[str], protocol: SetupStudyProtocol) -> dict[str, list[float]]:
    sessions = sorted(dev["session"].unique())
    folds = purged_walk_forward(sessions, protocol.cv_folds, protocol.embargo_sessions)
    top_k = protocol.acceptance["top_k"]
    metrics: dict[str, list[float]] = {name: [] for name in (*SCORERS, "random")}

    for train_sessions, test_sessions in folds:
        if not train_sessions or not test_sessions:
            continue
        train = dev[dev["session"].isin(train_sessions)]
        test = dev[dev["session"].isin(test_sessions)]
        if train.empty or test.empty:
            continue

        for name in SCORERS:
            if name == "setup_quality":
                scored, score_col = test, "setup_quality"
            else:
                fitted = fit_scorer(name, train, features, protocol)
                scored = test.copy()
                scored["_score"] = fitted.predict(test)
                score_col = "_score"
            selection = top_k_selection(scored, score_col, "r_cost", top_k)
            if not selection.empty:
                metrics[name].append(float(selection.mean()))

        random_series = random_selection(test, "r_cost", top_k)
        if not random_series.empty:
            metrics["random"].append(float(random_series.mean()))

    return metrics


def _select_winner(fold_metrics: Mapping[str, list[float]]) -> str:
    medians = {
        name: (float(np.median(fold_metrics[name])) if fold_metrics.get(name) else float("-inf"))
        for name in _FITTED_CANDIDATES
    }
    best = max(medians.values())
    for name in _FITTED_CANDIDATES:  # SCORERS order breaks ties.
        if medians[name] == best:
            return name
    raise AssertionError("unreachable: _FITTED_CANDIDATES is non-empty")


def _series_to_json(series: pd.Series) -> dict[str, float]:
    return {str(index): float(value) for index, value in series.items()}


def execute_setup_study(
    protocol: SetupStudyProtocol,
    directory: Path,
    *,
    development: Callable[[], pd.DataFrame],
    holdout: Callable[[], pd.DataFrame],
    environment: dict,
) -> dict:
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)

    save_json_report({**protocol.document(), "identity": protocol.identity}, directory / "protocol.json")
    save_json_report(
        {
            "protocol_id": protocol.identity,
            "environment": environment,
            "started_at": datetime.now(UTC).isoformat(),
            "hypotheses": list(HYPOTHESES),
            "authorizes_promotion": False,
        },
        directory / "manifest.json",
    )

    try:
        raw_development = development()
    except Exception as exc:
        failure = {"status": "failed", "phase": "development", "error": f"{type(exc).__name__}: {exc}"}
        save_json_report(failure, directory / "development.json")
        return failure

    dev, immature_dropped = _drop_immature(raw_development)
    features = model_features(dev)

    h1, h2, holm_adjusted = _h1_h2(dev, protocol)
    fold_metrics = _fold_metrics(dev, features, protocol)
    winner = _select_winner(fold_metrics)

    development_doc = {
        "status": "completed",
        "immature_dropped": immature_dropped,
        "n_setups": len(dev),
        "base_rates": _base_rates(dev),
        "h1": h1,
        "h2": h2,
        "holm_adjusted_p": holm_adjusted,
        "fold_metrics": fold_metrics,
        "winner": winner,
    }
    save_json_report(development_doc, directory / "development.json")

    fitted_winner = fit_scorer(winner, dev, features, protocol)
    ranker_doc: dict = {
        "scorer": winner,
        "features_version": protocol.features_version,
        "features": features,
        "training_window": [protocol.development[0].isoformat(), protocol.development[1].isoformat()],
        "protocol_id": protocol.identity,
    }
    if winner == "hgb":
        pickle_bytes = pickle.dumps(fitted_winner.model, protocol=_HGB_PICKLE_PROTOCOL)
        pickle_path = directory / "ranker.pkl"
        pickle_path.write_bytes(pickle_bytes)
        pickle_path.chmod(0o600)
        ranker_doc["pickle_path"] = "ranker.pkl"
        ranker_doc["pickle_sha256"] = hashlib.sha256(pickle_bytes).hexdigest()
        ranker_doc["hgb_params"] = dict(protocol.hgb_params)
    else:
        ranker_doc.update(_linear_payload(fitted_winner))
    save_json_report(ranker_doc, directory / "ranker.json")

    ranker_sha256 = hashlib.sha256((directory / "ranker.json").read_bytes()).hexdigest()
    save_json_report({"winner": winner, "ranker_sha256": ranker_sha256}, directory / "selection.json")

    # Only now -- after both artifacts above are saved and hashed -- may holdout be read.
    raw_holdout = holdout()
    hold, holdout_immature_dropped = _drop_immature(raw_holdout)

    hold_scored = hold.copy()
    hold_scored["_winner_score"] = fitted_winner.predict(hold)
    top_k = protocol.acceptance["top_k"]
    winner_top2 = top_k_selection(hold_scored, "_winner_score", "r_cost", top_k)
    quality_top2 = top_k_selection(hold, "setup_quality", "r_cost", top_k)
    random_top2 = random_selection(hold, "r_cost", top_k)

    paired = pd.concat({"winner": winner_top2, "setup_quality": quality_top2}, axis=1).dropna()
    diff_series = paired["winner"] - paired["setup_quality"]
    boot_kwargs = _bootstrap_kwargs(protocol)
    diff_boot = session_block_bootstrap(diff_series, np.mean, **boot_kwargs) if not diff_series.empty else np.zeros(0)
    lower_pct = (1.0 - protocol.acceptance["ci"]) / 2.0 * 100.0
    upper_pct = 100.0 - lower_pct
    if diff_boot.size:
        diff_ci90 = [float(np.percentile(diff_boot, lower_pct)), float(np.percentile(diff_boot, upper_pct))]
    else:
        diff_ci90 = [float("nan"), float("nan")]

    session_counts = hold.groupby("session").size()
    sessions_with_ge2 = int((session_counts >= 2).sum())
    winner_mean = float(winner_top2.mean()) if not winner_top2.empty else float("nan")
    quality_mean = float(quality_top2.mean()) if not quality_top2.empty else float("nan")

    acceptance_passed = bool(
        np.isfinite(diff_ci90[0])
        and diff_ci90[0] > 0
        and np.isfinite(winner_mean)
        and winner_mean > 0
        and sessions_with_ge2 >= protocol.acceptance["min_sessions"]
        and np.isfinite(quality_mean)
        and winner_mean > quality_mean
    )

    holdout_h1 = spearman_by_session_bootstrap(hold, "setup_quality", "r_cost", **boot_kwargs)

    holdout_doc = {
        "winner": winner,
        "immature_dropped": holdout_immature_dropped,
        "n_setups": len(hold),
        "top2_by_session": {
            "winner": _series_to_json(winner_top2),
            "setup_quality": _series_to_json(quality_top2),
            "random": _series_to_json(random_top2),
        },
        "winner_top2_mean": winner_mean,
        "setup_quality_top2_mean": quality_mean,
        "diff_ci90": diff_ci90,
        "h1": holdout_h1,
        "sessions_with_ge2_setups": sessions_with_ge2,
        "acceptance_passed": acceptance_passed,
        "acceptance": dict(protocol.acceptance),
    }
    save_json_report(holdout_doc, directory / "holdout.json")
    return holdout_doc
