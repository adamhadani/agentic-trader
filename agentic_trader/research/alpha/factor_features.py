"""Causal native-daily residual momentum; no labels, model search or I/O.

Each residual uses a fit ending before its own return. The caller supplies the
complete observed calendar and all-adjusted prices from its verified source;
missing endpoints remain missing. ETF returns are proxies, not stock sectors.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import asdict, dataclass
from enum import StrEnum

import numpy as np
import pandas as pd

from agentic_trader.market.bars import FIXED_BAR_LAYOUT
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.alpha.validation import frame_digest


FACTOR_FEATURE_VERSION = "causal_daily_etf_residual_momentum_v1"
FACTOR_COUNT = 9
MAX_FEATURE_ROWS = 3660
MAX_FEATURE_SYMBOLS = 500


@dataclass(frozen=True)
class ResidualMomentumSpec:
    fit_sessions: int = 126
    lookback_sessions: int = 252
    skip_sessions: int = 21
    ddof: int = 1
    svd_rcond: float = 1e-12
    max_condition: float = 1e8
    min_residual_std: float = 1e-12

    def __post_init__(self):
        # This version is one predeclared feature, not another parameter search.
        for name, field in self.__dataclass_fields__.items():
            value = getattr(self, name)
            if type(value) is not type(field.default) or value != field.default:
                raise ValueError("Fixed residual-momentum specification required; changes need a new version")

    def document(self):
        return {"version": FACTOR_FEATURE_VERSION, **asdict(self)}


DEFAULT_RESIDUAL_MOMENTUM_SPEC = ResidualMomentumSpec()


class FitReason(StrEnum):
    WARMUP = "insufficient_history"
    MISSING = "incomplete_training_returns"
    RANK = "rank_deficient_factors"
    CONDITION = "ill_conditioned_factors"
    NUMERICAL = "nonfinite_fit"
    OUTCOME = "unqualified_current_return"


@dataclass(frozen=True)
class ResidualMomentumFeatures:
    raw_momentum: pd.DataFrame
    residual_momentum: pd.DataFrame
    residuals: pd.DataFrame
    loadings: dict[str, pd.DataFrame]
    fits: list[dict]
    contract: dict
    input_hashes: dict[str, str]

    def document(self):
        return {
            "contract": deepcopy(self.contract),
            "input_hashes": dict(self.input_hashes),
            "fits": deepcopy(self.fits),
            "support": {
                "expected_symbol_dates": self.residuals.size,
                "observed_residuals": int(self.residuals.notna().to_numpy().sum()),
                "raw_momentum_scores": int(self.raw_momentum.notna().to_numpy().sum()),
                "residual_momentum_scores": int(self.residual_momentum.notna().to_numpy().sum()),
            },
            "authorizes_promotion": False,
        }


def _validate(closes, volumes, symbols, factors, feed, spec):
    clock = closes.index
    if (
        not isinstance(spec, ResidualMomentumSpec)
        or not isinstance(clock, pd.DatetimeIndex)
        or clock.tz is None
        or clock.hasnans
        or not clock.is_unique
        or not clock.is_monotonic_increasing
        or not 1 <= len(clock) <= MAX_FEATURE_ROWS
        or not clock.tz_convert(ET_TZ).equals(clock.tz_convert(ET_TZ).normalize())
        or not clock.equals(volumes.index)
        or not closes.columns.is_unique
        or not closes.columns.equals(volumes.columns)
    ):
        raise ValueError("Aligned unique native daily price/volume clock and fixed specification required")
    if (
        not isinstance(symbols, tuple)
        or not isinstance(factors, tuple)
        or not 1 <= len(symbols) <= MAX_FEATURE_SYMBOLS
        or len(factors) != FACTOR_COUNT
        or any(not isinstance(s, str) or not s for s in (*symbols, *factors))
        or symbols != tuple(sorted(set(symbols)))
        or factors != tuple(sorted(set(factors)))
        or set(symbols) & set(factors)
        or set(closes.columns) != set(symbols) | set(factors)
        or feed not in ("alpaca:iex", "alpaca:sip", "synthetic")
    ):
        raise ValueError("Explicit disjoint equity symbols, nine sorted factor proxies and source required")


def _source_returns(closes: pd.DataFrame, volumes: pd.DataFrame):
    prices = closes.to_numpy(dtype=float)
    amounts = volumes.to_numpy(dtype=float)
    valid = np.isfinite(prices) & (prices > 0) & np.isfinite(amounts) & (amounts > 0)
    values = np.full(prices.shape, np.nan)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        np.divide(prices[1:], prices[:-1], out=values[1:], where=valid[1:] & valid[:-1])
    values -= 1
    values[~np.isfinite(values)] = np.nan
    return pd.DataFrame(values, index=closes.index, columns=closes.columns)


def _source_hash(clock, closes, volumes, positions, columns, names):
    digest = hashlib.sha256(json.dumps(names, separators=(",", ":")).encode())
    digest.update(np.asarray(clock[positions].as_unit("ns").asi8, dtype="<i8").tobytes())
    for values in (closes, volumes):
        digest.update(np.asarray(values[positions][:, columns], dtype="<f8").tobytes())
    return digest.hexdigest()


def _factor_inverse(values, spec):
    """One shared, checked SVD per date; no inverse normal-equation matrix."""
    design = np.column_stack([np.ones(len(values)), values])
    left, singular, right = np.linalg.svd(design, full_matrices=False)
    if not all(np.isfinite(array).all() for array in (left, singular, right)):
        return None, {"rank": None, "singular_values": [], "condition": None}, FitReason.NUMERICAL
    rank = int((singular > singular[0] * spec.svd_rcond).sum())
    condition = float(singular[0] / singular[-1]) if singular[-1] > 0 else None
    evidence = {
        "rank": rank,
        "singular_values": singular.tolist(),
        "condition": condition if condition is not None and np.isfinite(condition) else None,
    }
    reason = (
        FitReason.RANK
        if rank != design.shape[1]
        else FitReason.CONDITION
        if condition is None or not np.isfinite(condition) or condition > spec.max_condition
        else None
    )
    return ((right.T / singular) @ left.T if reason is None else None), evidence, reason


def residual_momentum_features(
    closes: pd.DataFrame,
    volumes: pd.DataFrame,
    *,
    symbols: tuple[str, ...],
    factors: tuple[str, ...],
    feed: str,
    spec: ResidualMomentumSpec = DEFAULT_RESIDUAL_MOMENTUM_SPEC,
) -> ResidualMomentumFeatures:
    """Return aligned scores and immutable-history fit evidence for adjusted bars.

    Coefficients indexed at ``s`` use only returns ``[s-126, s)``. Their residual
    additionally observes return ``s``; its availability is next local midnight.
    Current-day missing prices/volume cannot invalidate an otherwise known beta.
    Global input hashes bind the full supplied artifact; individual fit hashes
    cover only their causal source windows and remain stable under future changes.
    """
    _validate(closes, volumes, symbols, factors, feed, spec)
    columns = (*symbols, *factors)
    prices = closes.loc[:, list(columns)].to_numpy(dtype=float)
    amounts = volumes.loc[:, list(columns)].to_numpy(dtype=float)
    returns = _source_returns(closes, volumes).loc[:, list(columns)]
    values = returns.to_numpy()
    clock, width = closes.index, len(symbols)
    factor_values = values[:, width:]
    residuals = pd.DataFrame(np.nan, index=clock, columns=symbols)
    loadings = {s: pd.DataFrame(np.nan, index=clock, columns=("intercept", *factors)) for s in symbols}
    fits = []
    for position, timestamp in enumerate(clock):
        first = max(1, position - spec.fit_sessions)
        factors_known = np.isfinite(factor_values[first:position]).all(axis=1)
        inverse, factor_reason = None, None
        numerical: dict[str, object] = {"rank": None, "singular_values": [], "condition": None}
        if position > spec.fit_sessions and factors_known.all():
            try:
                inverse, numerical, factor_reason = _factor_inverse(factor_values[first:position], spec)
            except np.linalg.LinAlgError:
                factor_reason = FitReason.NUMERICAL
        for number, symbol in enumerate(symbols):
            observed = np.isfinite(values[first:position, number]) & factors_known
            reason = (
                FitReason.WARMUP
                if position <= spec.fit_sessions
                else FitReason.MISSING
                if not observed.all()
                else factor_reason
            )
            source_columns = [number, *range(width, len(columns))]
            record = {
                "symbol": symbol,
                "return_bar": timestamp.isoformat(),
                "training_start": clock[first].isoformat() if first < position else None,
                "training_end": clock[position - 1].isoformat() if first < position else None,
                "assumed_training_available_at": (clock[position - 1] + pd.DateOffset(days=1)).isoformat()
                if first < position
                else None,
                "assumed_residual_available_at": (timestamp + pd.DateOffset(days=1)).isoformat(),
                "training_rows": int(observed.sum()),
                "required_training_rows": spec.fit_sessions,
                "coefficient_names": ["intercept", *factors],
                "coefficients": None,
                "training_source_hash": _source_hash(
                    clock, prices, amounts, slice(first - 1, position), source_columns, (symbol, *factors)
                ),
                "evaluation_source_hash": _source_hash(
                    clock,
                    prices,
                    amounts,
                    slice(max(0, position - 1), position + 1),
                    source_columns,
                    (symbol, *factors),
                ),
                **numerical,
                "fit_status": "unavailable",
                "reason": reason,
                "residual": None,
                "residual_reason": reason,
            }
            if reason is None and inverse is not None:
                beta = inverse @ values[first:position, number]
                if np.isfinite(beta).all():
                    loadings[symbol].iloc[position] = beta
                    record.update(fit_status="fitted", coefficients=beta.tolist())
                    current = values[position, source_columns]
                    if np.isfinite(current).all():
                        residual = float(current[0] - beta[0] - beta[1:] @ current[1:])
                        if np.isfinite(residual):
                            residuals.iloc[position, number] = residual
                            record.update(residual=residual, residual_reason=None)
                        else:
                            record["residual_reason"] = FitReason.NUMERICAL
                    else:
                        record["residual_reason"] = FitReason.OUTCOME
                else:
                    record.update(reason=FitReason.NUMERICAL, residual_reason=FitReason.NUMERICAL)
            fits.append(record)
    window = spec.lookback_sessions - spec.skip_sessions
    observed_window = returns.loc[:, list(symbols)].shift(spec.skip_sessions).rolling(window, min_periods=window)
    raw = (
        closes.loc[:, list(symbols)].shift(spec.skip_sessions)
        / closes.loc[:, list(symbols)].shift(spec.lookback_sessions)
        - 1
    )
    raw = raw.where(observed_window.count().eq(window) & np.isfinite(raw))
    residual_window = residuals.shift(spec.skip_sessions).rolling(window, min_periods=window)
    standard = residual_window.std(ddof=spec.ddof)
    momentum = (residual_window.mean() / standard.where(standard > spec.min_residual_std)).replace(
        [np.inf, -np.inf], np.nan
    )
    return ResidualMomentumFeatures(
        raw,
        momentum,
        residuals,
        loadings,
        fits,
        {
            "spec": spec.document(),
            "symbols": list(symbols),
            "factors": list(factors),
            "feed": feed,
            "timeframe": "1d",
            "adjustment": "all",
            "bar_layout": FIXED_BAR_LAYOUT,
            "availability": "Historical daily completion assumed at next local midnight; no backdated receipt",
            "vintage_scope": "Adjusted historical source vintage, not point-in-time corporate-action evidence",
            "factor_scope": "Observed ETF return proxies, not verified stock sectors or Fama-French factors",
            "return_support": "Finite positive closes and finite positive volumes at both return endpoints",
        },
        {"closes": frame_digest(closes), "volumes": frame_digest(volumes)},
    )
