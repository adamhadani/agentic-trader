"""A pooled formula: a dimensionless DSL score, optional cross-sectional filters and k.

Scores are evaluated per symbol on adjusted daily bars and read at D-1, so a decision at
10:35 on D uses only completed sessions -- the live scan sees the same bars. Picks are the
top-k eligible names per session by score, skipping names the formula still holds and
breaking ties by a hash (never the alphabet).
"""

from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, field_validator, model_validator

from agentic_trader.research.alpha.dsl import AlphaExpressionEvaluator, compile_expression
from agentic_trader.research.apriori.pead_events import _by_session
from agentic_trader.research.pooled.cube import CubeView


__all__ = [
    "Formula",
    "FormulaFilter",
    "Picks",
    "allowed_mask",
    "evaluate_panel",
    "evaluate_prepared",
    "expression_nodes",
    "prepare_daily",
    "require_dimensionless",
    "select_picks",
]


def require_dimensionless(expression: str) -> None:
    units = compile_expression(expression).units
    if tuple(float(u) for u in units) != (0.0, 0.0):
        raise ValueError(f"expression must be dimensionless to rank across names: {expression!r} has units {units}")


def expression_nodes(expression: str) -> int:
    return sum(1 for _ in ast.walk(ast.parse(expression, mode="eval")))


class FormulaFilter(BaseModel, frozen=True, extra="forbid"):
    expression: str
    min_quantile: float | None = Field(default=None, ge=0, le=1)
    max_quantile: float | None = Field(default=None, ge=0, le=1)

    @field_validator("expression")
    @classmethod
    def _dimensionless(cls, value: str) -> str:
        require_dimensionless(value)
        return value

    @model_validator(mode="after")
    def _bounds(self) -> FormulaFilter:
        if self.min_quantile is None and self.max_quantile is None:
            raise ValueError("a filter needs min_quantile or max_quantile")
        if self.min_quantile is not None and self.max_quantile is not None and self.min_quantile >= self.max_quantile:
            raise ValueError("min_quantile must be below max_quantile")
        return self


class Formula(BaseModel, frozen=True, extra="forbid"):
    score: str
    filters: tuple[FormulaFilter, ...] = ()
    k: int = Field(ge=1)

    @field_validator("score")
    @classmethod
    def _dimensionless(cls, value: str) -> str:
        require_dimensionless(value)
        return value

    @property
    def lookback(self) -> int:
        return max(compile_expression(e).lookback for e in (self.score, *(f.expression for f in self.filters)))

    @property
    def identity(self) -> str:
        encoded = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()


def prepare_daily(adjusted: Mapping[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Adjusted daily bars keyed by New York session (a DatetimeIndex), ready for repeated scoring."""
    prepared: dict[str, pd.DataFrame] = {}
    for symbol, frame in adjusted.items():
        if frame is None or frame.empty:
            continue
        keyed = _by_session(frame)
        prepared[symbol] = keyed.set_axis(pd.DatetimeIndex(keyed.index))
    return prepared


def evaluate_prepared(
    expression: str,
    prepared: Mapping[str, pd.DataFrame],
    trading_days: Sequence[date],
    sessions: Sequence[date],
    symbols: Sequence[str],
) -> np.ndarray:
    """``evaluate_panel`` on bars already keyed by ``prepare_daily``."""
    position = {day: i for i, day in enumerate(trading_days)}
    # A session with no earlier trading day has no completed bar to read: NaT reindexes to NaN
    # (a bare ``- 1`` would wrap to the last day and leak the future).
    previous = pd.DatetimeIndex([trading_days[position[day] - 1] if position[day] > 0 else pd.NaT for day in sessions])
    out = np.full((len(sessions), len(symbols)), np.nan)
    evaluator = AlphaExpressionEvaluator()
    for col, symbol in enumerate(symbols):
        keyed = prepared.get(symbol)
        if keyed is None:
            continue
        series = evaluator.evaluate(expression, keyed)
        out[:, col] = series.reindex(previous).to_numpy(float)
    out[~np.isfinite(out)] = np.nan
    return out


def evaluate_panel(
    expression: str,
    adjusted: Mapping[str, pd.DataFrame],
    trading_days: Sequence[date],
    sessions: Sequence[date],
    symbols: Sequence[str],
) -> np.ndarray:
    """[S, N] values known at each decision: the expression on adjusted daily bars, read at D-1."""
    return evaluate_prepared(expression, prepare_daily(adjusted), trading_days, sessions, symbols)


def allowed_mask(formula: Formula, filter_values: Sequence[np.ndarray], eligible: np.ndarray) -> np.ndarray:
    """Eligible names passing every cross-sectional quantile filter, session by session."""
    mask = eligible.copy()
    for spec, values in zip(formula.filters, filter_values, strict=True):
        for row in range(values.shape[0]):
            finite = eligible[row] & np.isfinite(values[row])
            if not finite.any():
                mask[row] = False
                continue
            keep = finite.copy()
            if spec.max_quantile is not None:
                keep &= values[row] <= np.quantile(values[row][finite], spec.max_quantile)
            if spec.min_quantile is not None:
                keep &= values[row] >= np.quantile(values[row][finite], spec.min_quantile)
            mask[row] &= keep
    return mask


@dataclass(frozen=True)
class Picks:
    session_idx: np.ndarray  # int, relative to the view
    symbol_idx: np.ndarray  # int

    def cells(self) -> set[tuple[int, int]]:
        return set(zip(self.session_idx.tolist(), self.symbol_idx.tolist(), strict=True))


def select_picks(scores: np.ndarray, allowed: np.ndarray, view: CubeView, k: int) -> Picks:
    """Top-k eligible names per session by score; a picked name stays held through its exit session."""
    sessions, names = scores.shape
    held_through = np.full(names, -1, dtype=np.int64)
    rows: list[int] = []
    cols: list[int] = []
    for row in range(sessions):
        candidates = np.flatnonzero(allowed[row] & view.eligible[row] & np.isfinite(scores[row]) & (held_through < row))
        if candidates.size == 0:
            continue
        order = np.lexsort((view.tiebreak[row, candidates], -scores[row, candidates]))
        for col in candidates[order[:k]]:
            held_through[col] = row + int(view.holding[row, col]) - 1
            rows.append(row)
            cols.append(int(col))
    return Picks(np.asarray(rows, dtype=np.int64), np.asarray(cols, dtype=np.int64))
