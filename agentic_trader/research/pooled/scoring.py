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
from dataclasses import asdict, dataclass
from datetime import date
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from agentic_trader.research.alpha.search import canonical_expression
from agentic_trader.research.pooled.campaign import (
    CampaignProtocol,
    EventsCampaignProtocol,
    InsiderCampaignProtocol,
    IntradayCampaignProtocol,
    ScoredFormula,
    ShortVolumeCampaignProtocol,
    _calls,
)
from agentic_trader.research.pooled.cube import CubeView
from agentic_trader.research.pooled.events import EVENT_FIELDS
from agentic_trader.research.pooled.formula import (
    evaluate_prepared,
    evaluate_prepared_hourly,
    expression_nodes,
    prepare_daily,
    prepare_hourly,
    require_dimensionless,
)
from agentic_trader.research.pooled.short_fields import SHORT_FIELDS


if TYPE_CHECKING:
    from agentic_trader.research.pooled.runner import CubeBuild


__all__ = [
    "DEFAULT_CAPACITY",
    "FORMULA_ID_HEX",
    "FrozenPanels",
    "ScoreBook",
    "Vetted",
    "event_coverage_document",
    "event_inputs_document",
    "formula_id",
    "score_book",
    "score_read_document",
    "short_coverage_document",
    "short_inputs_document",
    "vet_expression",
]

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

    @classmethod
    def with_fields(
        cls,
        adjusted: Mapping[str, pd.DataFrame],
        frames: Mapping[str, pd.DataFrame],
        field_names: Sequence[str],
        trading_days: Sequence[date],
        sessions: Sequence[date],
        symbols: Sequence[str],
        *,
        capacity: int = DEFAULT_CAPACITY,
    ) -> ScoreBook:
        """A daily book whose frames also carry the extra ``field_names`` columns (events or short volume):
        the same D-1 read for bar and extra fields; a symbol without a frame keeps its bars and gets NaN
        extra fields. Only the ``field_names`` columns are joined; a frame missing one raises."""
        book = cls.__new__(cls)
        prepared = prepare_daily(adjusted)
        names = list(field_names)
        for symbol, keyed in prepared.items():
            frame = frames.get(symbol)
            if frame is None:
                columns = pd.DataFrame(index=keyed.index, columns=names, dtype=float)
            else:
                missing = [name for name in names if name not in frame.columns]
                if missing:
                    raise ValueError(f"{symbol}: field frame lacks column {missing[0]!r}")
                columns = frame[names].reindex(keyed.index)
            prepared[symbol] = keyed.join(columns, how="left")
        trading = tuple(trading_days)
        book._setup(
            sessions,
            symbols,
            capacity,
            lambda expression: evaluate_prepared(expression, prepared, trading, book._sessions, book._symbols),
            "daily",
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


def score_read_document(protocol: CampaignProtocol) -> dict | None:
    """The protocol's hourly read as recorded in manifests; None for a daily protocol."""
    return protocol.score_read.model_dump() if isinstance(protocol, IntradayCampaignProtocol) else None


def event_inputs_document(protocol: CampaignProtocol) -> dict | None:
    """The protocol's event inputs as recorded in manifests; None for a protocol without event fields."""
    return protocol.event_inputs.model_dump(mode="json") if isinstance(protocol, EventsCampaignProtocol) else None


def short_inputs_document(protocol: CampaignProtocol) -> dict | None:
    """The protocol's short-volume inputs as recorded in manifests; None for a protocol without short fields."""
    return protocol.short_inputs.model_dump(mode="json") if isinstance(protocol, ShortVolumeCampaignProtocol) else None


def insider_inputs_document(protocol: CampaignProtocol) -> dict | None:
    """The protocol's insider inputs as recorded in manifests; None for a protocol without insider fields."""
    return protocol.insider_inputs.model_dump(mode="json") if isinstance(protocol, InsiderCampaignProtocol) else None


def event_coverage_document(built: CubeBuild) -> dict | None:
    """The build's event coverage as recorded in results; None for a build without event fields."""
    return asdict(built.events.coverage) if built.events is not None else None


def short_coverage_document(built: CubeBuild) -> dict | None:
    """The build's short-volume coverage as recorded in results; None for a build without short fields."""
    return asdict(built.short.coverage) if built.short is not None else None


def score_book(protocol: CampaignProtocol, built: CubeBuild) -> ScoreBook:
    """The book a protocol's formulas read from: daily bars at D-1, or hourly bars before the decision."""
    if isinstance(protocol, IntradayCampaignProtocol):
        if not built.hourly:
            raise ValueError(
                "this protocol scores hourly bars but the build carries no hourly frames; build with load_hourly=True"
            )
        return ScoreBook.hourly(
            built.hourly,
            built.cube.sessions,
            built.cube.symbols,
            decision_time_et=protocol.bracket.decision_time_et,
            bars_before_decision=protocol.score_read.bars_before_decision,
        )
    if isinstance(protocol, EventsCampaignProtocol):
        if built.events is None:
            raise ValueError(
                "this protocol scores event fields but the build carries no event frames; build with events=..."
            )
        return ScoreBook.with_fields(
            built.adjusted,
            built.events.frames,
            EVENT_FIELDS,
            built.trading_days,
            built.cube.sessions,
            built.cube.symbols,
        )
    if isinstance(protocol, ShortVolumeCampaignProtocol):
        if built.short is None:
            raise ValueError(
                "this protocol scores short-volume fields but the build carries no short frames; build with short=..."
            )
        return ScoreBook.with_fields(
            built.adjusted,
            built.short.frames,
            SHORT_FIELDS,
            built.trading_days,
            built.cube.sessions,
            built.cube.symbols,
        )
    return ScoreBook(built.adjusted, built.trading_days, built.cube.sessions, built.cube.symbols)
