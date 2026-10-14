# agentic_trader/research/pooled/scoring.py
"""Real DSL formulas as campaign ``ScoredFormula`` objects: label-blind, causal and cached.

A score panel is computed once per canonical expression over the cube's full session
calendar, from adjusted daily bars read at D-1 (``evaluate_prepared``), and kept in a
bounded LRU. A formula's panel reads only a view's symbols, offset, sessions and the shape
of ``eligible``, never a label. ``vet_expression`` decides, before anything is charged or
evaluated, whether a proposal may be scored.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from agentic_trader.research.alpha.search import canonical_expression
from agentic_trader.research.pooled.campaign import ScoredFormula, _calls
from agentic_trader.research.pooled.cube import CubeView
from agentic_trader.research.pooled.formula import (
    evaluate_prepared,
    evaluate_prepared_hourly,
    expression_nodes,
    prepare_daily,
    prepare_hourly,
    require_dimensionless,
)


__all__ = ["DEFAULT_CAPACITY", "FORMULA_ID_HEX", "FrozenPanels", "ScoreBook", "Vetted", "formula_id", "vet_expression"]

FORMULA_ID_HEX = 16
DEFAULT_CAPACITY = 64  # panels per process; one panel is sessions x symbols float64 (~9 MB for v2)


def formula_id(expression: str) -> str:
    """The campaign's formula id: the first 16 hex digits of SHA-256 of the canonical expression."""
    return hashlib.sha256(canonical_expression(expression).encode()).hexdigest()[:FORMULA_ID_HEX]


@dataclass(frozen=True)
class Vetted:
    expression: str  # canonical when it compiled, else as proposed
    reason: str | None  # None: the proposal may be charged and evaluated


def vet_expression(expression: str, forbidden: Iterable[str], seen: Collection[str]) -> Vetted:
    """Whether a proposal may be charged and scored; the first failing reason otherwise."""
    try:
        canonical = canonical_expression(expression)
    except SyntaxError, ValueError:  # AlphaDSLSyntaxError is a ValueError
        return Vetted(expression, "does not compile")
    if canonical in seen:
        return Vetted(canonical, "duplicate")
    if {name.lower() for name in forbidden} & _calls(canonical):
        return Vetted(canonical, "forbidden operator")
    try:
        require_dimensionless(canonical)
    except ValueError:
        return Vetted(canonical, "not dimensionless")
    return Vetted(canonical, None)


def _scored_formula(book: ScoreBook | FrozenPanels, expression: str) -> ScoredFormula:
    canonical = canonical_expression(expression)

    def panel(view: CubeView) -> tuple[np.ndarray, np.ndarray]:
        # Label-blind: only the view's symbols, offset, sessions and eligible shape are read.
        if tuple(view.symbols) != book.symbols:
            raise ValueError("the view's symbols are not the score book's")
        stop = view.offset + len(view.sessions)
        if book.sessions[view.offset : stop] != tuple(view.sessions):
            raise ValueError("the view is not a window of the score book's calendar")
        return book.panel(canonical)[view.offset : stop], np.ones(view.eligible.shape, dtype=bool)

    return ScoredFormula(formula_id=formula_id(canonical), nodes=expression_nodes(canonical), panel=panel)


class ScoreBook:
    """Score panels on the cube's full calendar, one per canonical expression, in a bounded LRU."""

    def __init__(
        self,
        adjusted: Mapping[str, pd.DataFrame],
        trading_days: Sequence[date],
        sessions: Sequence[date],
        symbols: Sequence[str],
        *,
        capacity: int = DEFAULT_CAPACITY,
    ):
        prepared = prepare_daily(adjusted)
        trading = tuple(trading_days)
        self._setup(
            sessions,
            symbols,
            capacity,
            lambda expression: evaluate_prepared(expression, prepared, trading, self._sessions, self._symbols),
            "daily",
        )

    def _setup(
        self,
        sessions: Sequence[date],
        symbols: Sequence[str],
        capacity: int,
        evaluate: Callable[[str], np.ndarray],
        score_inputs: str,
    ) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._sessions = tuple(sessions)
        self._symbols = tuple(symbols)
        self._capacity = capacity
        self._panels: OrderedDict[str, np.ndarray] = OrderedDict()
        self._evaluate = evaluate
        self.score_inputs = score_inputs

    @classmethod
    def hourly(
        cls,
        hourly: Mapping[str, pd.DataFrame | None],
        sessions: Sequence[date],
        symbols: Sequence[str],
        *,
        decision_time_et: str,
        bars_before_decision: int,
        capacity: int = DEFAULT_CAPACITY,
    ) -> ScoreBook:
        """A book scoring on regular-session hourly bars read before the decision (spec 2026-10-14 3.2)."""
        book = cls.__new__(cls)
        prepared = prepare_hourly(hourly)
        book._setup(
            sessions,
            symbols,
            capacity,
            lambda expression: evaluate_prepared_hourly(
                expression,
                prepared,
                book._sessions,
                book._symbols,
                decision_time_et=decision_time_et,
                bars_before_decision=bars_before_decision,
            ),
            "hourly",
        )
        return book

    @property
    def sessions(self) -> tuple[date, ...]:
        return self._sessions

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._symbols

    def panel(self, expression: str) -> np.ndarray:
        """[sessions, symbols] scores on the full calendar, read-only; computed once while cached."""
        canonical = canonical_expression(expression)
        cached = self._panels.get(canonical)
        if cached is not None:
            self._panels.move_to_end(canonical)
            return cached
        values = self._evaluate(canonical)
        values.flags.writeable = False
        self._panels[canonical] = values
        if len(self._panels) > self._capacity:
            self._panels.popitem(last=False)
        return values

    def formula(self, expression: str) -> ScoredFormula:
        return _scored_formula(self, expression)

    def freeze(self, expressions: Iterable[str]) -> FrozenPanels:
        """Precomputed panels for exactly these expressions (canonical), for process pools that must
        not pickle the bars."""
        panels = {canonical_expression(e): self.panel(e) for e in expressions}
        return FrozenPanels(panels, self._sessions, self._symbols, score_inputs=self.score_inputs)


class FrozenPanels:
    """A score book that serves only the panels it was frozen with; anything else raises."""

    def __init__(
        self,
        panels: Mapping[str, np.ndarray],
        sessions: Sequence[date],
        symbols: Sequence[str],
        *,
        score_inputs: str,
    ):
        self._panels = dict(panels)
        self._sessions = tuple(sessions)
        self._symbols = tuple(symbols)
        self.score_inputs = score_inputs

    @property
    def sessions(self) -> tuple[date, ...]:
        return self._sessions

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._symbols

    def panel(self, expression: str) -> np.ndarray:
        canonical = canonical_expression(expression)
        try:
            return self._panels[canonical]
        except KeyError:
            raise ValueError(f"expression not in the frozen panels: {canonical!r}") from None

    def formula(self, expression: str) -> ScoredFormula:
        self.panel(expression)  # refuse an unfrozen expression now, not at first evaluation
        return _scored_formula(self, expression)
