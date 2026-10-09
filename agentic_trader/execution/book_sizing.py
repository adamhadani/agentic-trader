"""Book-aware card sizing: the service around ``risk.book_vol`` (docs/card-evidence.md).

``prepare`` builds the once-per-scan covariance and book vector and never raises; ``decide`` is
synchronous and pure per candidate. A missing covariance never blocks (``unavailable``, factor
unchanged); the factor never raises size. The shadow optimiser block is a journaled cross-check
of the closed-form arithmetic and is never read by the decision.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

import pandas as pd
from pydantic import BaseModel, Field

from agentic_trader.agent.evaluator import LLMTradeEvaluation
from agentic_trader.agent.position_sizing import scale_sizing
from agentic_trader.config import BookSizingConfig
from agentic_trader.constants import AssetClass, Direction
from agentic_trader.research.alpha.optimizer import ConvexAlphaPortfolioOptimizer
from agentic_trader.research.setups.covariance import daily_returns, is_raw_frame, shrunk_covariance
from agentic_trader.risk import normalize_symbol
from agentic_trader.risk.book_vol import BookVolInputs, book_vol_factor


if TYPE_CHECKING:
    from agentic_trader.data.providers import AlpacaDataProvider


__all__ = [
    "BOOK_SIZING_FETCH_TIMEOUT_SECONDS",
    "BookContext",
    "BookSizer",
    "BookSizingDecision",
    "BookSizingStatus",
]

logger = logging.getLogger(__name__)

BOOK_SIZING_FETCH_TIMEOUT_SECONDS = 20.0
SHADOW_TOLERANCE = 1e-6


class BookSizingStatus(StrEnum):
    APPLIED = "applied"  # enforce: factor < 1 changed the card
    UNCHANGED = "unchanged"  # factor == 1, or preview (see would_scale)
    BLOCKED = "blocked"  # enforce: the scaled default tier fell below the minimum size
    UNAVAILABLE = "unavailable"  # covariance missing/invalid/timeout; never blocks
    NOT_APPLICABLE = "not_applicable"  # off, non-equity, drift/catalog, policy-locked, dry run


class BookSizingDecision(BaseModel, frozen=True):
    status: BookSizingStatus
    mode: str
    factor: float | None = Field(default=None, allow_inf_nan=False)
    would_scale: bool = False
    quantity_before: float = Field(allow_inf_nan=False)
    quantity_after: float = Field(allow_inf_nan=False)
    budget_pct: float = Field(allow_inf_nan=False)
    budget_dollars: float | None = Field(default=None, allow_inf_nan=False)
    vol_before_pct: float | None = Field(default=None, allow_inf_nan=False)
    vol_after_full_pct: float | None = Field(default=None, allow_inf_nan=False)
    vol_after_scaled_pct: float | None = Field(default=None, allow_inf_nan=False)
    n_observations: int | None = None
    shrinkage: float | None = Field(default=None, allow_inf_nan=False)
    book_symbols: tuple[str, ...] = ()
    missing_symbols: tuple[str, ...] = ()
    reason: str | None = None  # a type name on failures, never exception text
    # Inputs the shadow cross-check (``attach_shadow``) re-derives the scaled vol from.
    symbol: str | None = None
    risk_capital: float | None = Field(default=None, allow_inf_nan=False)
    scaled_weight: float | None = Field(default=None, allow_inf_nan=False)  # signed dollars after scaling
    shadow: dict[str, Any] | None = None


class BookContext(BaseModel, frozen=True, arbitrary_types_allowed=True):
    status: Literal["ready", "unavailable", "off"]
    reason: str | None
    covariance: pd.DataFrame | None
    shrinkage: float | None
    n_observations: int | None
    weights: dict[str, float]  # signed equity notional per symbol
    book_symbols: tuple[str, ...]
    missing_symbols: tuple[str, ...]

    def with_card(self, *, symbol: str, direction: str, notional: float, asset_class: Any) -> BookContext:
        """The book after a recorded card: its final signed equity notional joins the weights.

        Only a ready context whose covariance holds the card's symbol can use the weight; any
        other context is returned unchanged (an unavailable book stays unavailable, a drift name
        has no row, a non-equity card is never part of this book). Never mutates ``self``.
        """
        key = normalize_symbol(str(symbol))
        if (
            self.status != "ready"
            or self.covariance is None
            or key not in self.covariance.index
            or asset_class != AssetClass.EQUITY
        ):
            return self
        signed = float(notional) * (1.0 if direction == Direction.LONG else -1.0)
        return self.model_copy(update={"weights": {**self.weights, key: self.weights.get(key, 0.0) + signed}})


def _unavailable(reason: str, **kw: Any) -> BookContext:
    fields: dict[str, Any] = {
        "covariance": None,
        "shrinkage": None,
        "n_observations": None,
        "weights": {},
        "book_symbols": (),
        "missing_symbols": (),
    }
    return BookContext(status="unavailable", reason=reason, **{**fields, **kw})


class _InvalidBookRowError(Exception):
    pass


def _book_weights(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """Signed equity notional per symbol; a row whose exposure is unknown is an error, never skipped."""
    weights: dict[str, float] = {}
    for row in rows:
        if not str(row.get("asset_class") or "").upper().endswith("EQUITY"):
            continue
        symbol = normalize_symbol(str(row.get("contract") or row.get("symbol") or ""))
        direction = str(row.get("direction") or "").upper()
        try:
            notional = float(row.get("notional_value"))  # type: ignore[arg-type]
        except TypeError, ValueError:
            raise _InvalidBookRowError from None
        if not symbol or direction not in {Direction.LONG, Direction.SHORT} or not math.isfinite(notional):
            raise _InvalidBookRowError
        weights[symbol] = weights.get(symbol, 0.0) + (notional if direction == Direction.LONG else -notional)
    return weights


class BookSizer:
    def __init__(
        self,
        config: BookSizingConfig,
        bar_source: AlpacaDataProvider | None,
        *,
        portfolio_cash: float,
        min_units: float,
    ) -> None:
        self._config = config
        self._bar_source = bar_source
        self._portfolio_cash = portfolio_cash
        self._min_units = min_units

    async def prepare(
        self,
        active_positions: Sequence[Mapping[str, Any]],
        datasets: Mapping[str, Any],
        *,
        as_of: date,
        candidates: Sequence[str],
    ) -> BookContext:
        if self._config.mode == "off":
            return BookContext(
                status="off",
                reason=None,
                covariance=None,
                shrinkage=None,
                n_observations=None,
                weights={},
                book_symbols=(),
                missing_symbols=(),
            )
        try:
            return await self._prepare(active_positions, datasets, as_of, candidates)
        except Exception as exc:
            # I/O or data trouble is expected here; a programming error must still be visible.
            logger.warning(
                "Book sizing context unavailable",
                exc_info=True,
                extra={"event": "book_sizing_prepare_failed", "reason": type(exc).__name__},
            )
            return _unavailable(type(exc).__name__)

    async def _prepare(
        self,
        active_positions: Sequence[Mapping[str, Any]],
        datasets: Mapping[str, Any],
        as_of: date,
        candidates: Sequence[str],
    ) -> BookContext:
        config = self._config
        try:
            weights = _book_weights(active_positions)
        except _InvalidBookRowError:
            return _unavailable("invalid_book_row")
        book_symbols = tuple(sorted(weights))
        needed = sorted({*book_symbols, *(normalize_symbol(c) for c in candidates)})
        by_normalized = {normalize_symbol(k): v for k, v in datasets.items()}
        frames: dict[str, pd.DataFrame] = {}
        missing: list[str] = []
        for symbol in needed:
            daily = getattr(by_normalized.get(symbol), "daily", None)
            if isinstance(daily, pd.DataFrame) and not daily.empty and is_raw_frame(daily):
                frames[symbol] = daily
            else:
                missing.append(symbol)
        if missing:
            if self._bar_source is None:
                return _unavailable("no_bar_source", weights=weights, book_symbols=book_symbols)
            start = datetime(as_of.year, as_of.month, as_of.day, tzinfo=UTC) - timedelta(
                days=int(config.lookback_sessions * 1.6) + 10
            )
            end = datetime(as_of.year, as_of.month, as_of.day, tzinfo=UTC)
            fetched = await asyncio.wait_for(
                asyncio.to_thread(self._bar_source.fetch_daily_many, missing, start, end, adjustment="raw"),
                BOOK_SIZING_FETCH_TIMEOUT_SECONDS,
            )
            for symbol in missing:
                frame = fetched.get(symbol)
                if isinstance(frame, pd.DataFrame) and not frame.empty and is_raw_frame(frame):
                    frames[symbol] = frame
        lacking_book = tuple(s for s in book_symbols if s not in frames)
        if lacking_book:
            return _unavailable(
                "missing_bars", weights=weights, book_symbols=book_symbols, missing_symbols=lacking_book
            )
        returns, dropped = daily_returns(
            frames, as_of=as_of, lookback_sessions=config.lookback_sessions, min_observations=config.min_observations
        )
        dropped_book = tuple(s for s in book_symbols if s in dropped)
        if dropped_book or returns.empty:
            return _unavailable(
                "insufficient_observations",
                weights=weights,
                book_symbols=book_symbols,
                missing_symbols=dropped_book,
            )
        covariance, shrinkage = shrunk_covariance(returns)
        return BookContext(
            status="ready",
            reason=None,
            covariance=covariance,
            shrinkage=shrinkage,
            n_observations=len(returns),
            weights=weights,
            book_symbols=book_symbols,
            missing_symbols=(),
        )

    def decide(
        self,
        context: BookContext | None,
        *,
        candidate: Any,
        eval_res: LLMTradeEvaluation,
        risk_capital: float,
        dry_run: bool,
    ) -> tuple[BookSizingDecision, LLMTradeEvaluation | None]:
        config = self._config
        quantity = float(eval_res.quantity)
        budget_pct = config.max_portfolio_daily_vol_pct

        def plain(status: BookSizingStatus, reason: str | None, **kw: Any) -> BookSizingDecision:
            fields: dict[str, Any] = {
                "status": status,
                "mode": config.mode,
                "quantity_before": quantity,
                "quantity_after": quantity,
                "budget_pct": budget_pct,
                "reason": reason,
            }
            if context is not None:
                fields |= {
                    "n_observations": context.n_observations,
                    "shrinkage": context.shrinkage,
                    "book_symbols": context.book_symbols,
                    "missing_symbols": context.missing_symbols,
                }
            return BookSizingDecision(**{**fields, **kw})

        def exempt(reason: str) -> tuple[BookSizingDecision, LLMTradeEvaluation | None]:
            return plain(BookSizingStatus.NOT_APPLICABLE, reason), eval_res

        if context is None:
            return exempt("no_context")
        if dry_run:
            return exempt("dry_run")
        if context.status == "off":
            return exempt("off")
        if getattr(candidate, "catalog_event", None) is not None:
            return exempt("drift")
        if candidate.alpha_version or candidate.alpha_policy or candidate.probe:
            return exempt("policy_locked")
        if eval_res.asset_class != AssetClass.EQUITY:
            return exempt("non_equity")
        if context.status == "unavailable":
            return plain(BookSizingStatus.UNAVAILABLE, context.reason), eval_res
        symbol = normalize_symbol(candidate.contract)
        covariance = context.covariance
        if covariance is None or symbol not in covariance.index:
            return plain(BookSizingStatus.UNAVAILABLE, "candidate_missing"), eval_res

        if not (math.isfinite(risk_capital) and risk_capital > 0):
            return plain(BookSizingStatus.UNAVAILABLE, "invalid_risk_capital"), eval_res
        budget = budget_pct * risk_capital
        signed = float(eval_res.notional_value) * (1.0 if eval_res.direction == Direction.LONG else -1.0)
        try:
            result = book_vol_factor(
                BookVolInputs(
                    weights=context.weights,
                    candidate=symbol,
                    candidate_notional=signed,
                    covariance=covariance,
                    budget_dollars=budget,
                )
            )
        except ValueError as exc:
            logger.warning(
                "Book sizing rule refused its inputs",
                exc_info=True,
                extra={"event": "book_sizing_rule_invalid", "symbol": symbol, "reason": type(exc).__name__},
            )
            return plain(BookSizingStatus.UNAVAILABLE, type(exc).__name__), eval_res

        factor = result.factor
        vol_before = result.vol_before / risk_capital
        vol_full = result.vol_after_full / risk_capital
        vol_scaled = result.vol_after_scaled / risk_capital
        outcome_eval: LLMTradeEvaluation | None = eval_res
        status = BookSizingStatus.UNCHANGED
        reason: str | None = None
        quantity_after = quantity
        if config.mode == "enforce" and factor < 1.0:
            outcome_eval = scale_sizing(
                eval_res, factor, min_units=self._min_units, portfolio_cash=self._portfolio_cash
            )
            if outcome_eval is None:
                status = BookSizingStatus.BLOCKED
                reason = f"book sizing: portfolio vol {vol_full:.2%} > budget {budget_pct:.2%}/day"
                quantity_after = 0.0
                vol_scaled = vol_before  # a blocked card adds nothing to the book
            else:
                status = BookSizingStatus.APPLIED
                quantity_after = float(outcome_eval.quantity)
        scaled_weight = 0.0 if status == BookSizingStatus.BLOCKED else factor * signed
        decision = plain(
            status,
            reason,
            factor=factor,
            would_scale=factor < 1.0,
            quantity_after=quantity_after,
            budget_dollars=budget,
            vol_before_pct=vol_before,
            vol_after_full_pct=vol_full,
            vol_after_scaled_pct=vol_scaled,
            symbol=symbol,
            risk_capital=risk_capital,
            scaled_weight=scaled_weight,
        )
        return decision, outcome_eval

    async def attach_shadow(self, context: BookContext | None, decision: BookSizingDecision) -> BookSizingDecision:
        """The D4 cross-check, off the event loop: the decision with its ``shadow`` block, or unchanged.

        Runs only when the optimiser is enabled and the decision came from the rule (not
        unavailable/not_applicable). The block is evidence; nothing reads it.
        """
        if (
            not self._config.shadow_optimizer
            or context is None
            or context.covariance is None
            or decision.status in (BookSizingStatus.UNAVAILABLE, BookSizingStatus.NOT_APPLICABLE)
            or decision.symbol is None
            or decision.risk_capital is None
            or decision.scaled_weight is None
            or decision.vol_after_scaled_pct is None
        ):
            return decision
        block = await asyncio.to_thread(
            self._shadow_check,
            context,
            decision.symbol,
            decision.scaled_weight,
            decision.risk_capital,
            decision.vol_after_scaled_pct,
        )
        return decision.model_copy(update={"shadow": block})

    @staticmethod
    def _shadow_check(
        context: BookContext, symbol: str, scaled_weight: float, risk_capital: float, closed_form_pct: float
    ) -> dict[str, Any]:
        """Re-derive the scaled book vol through the convex optimiser with every weight pinned."""
        started = time.perf_counter()
        try:
            covariance = context.covariance
            if covariance is None:
                return {"status": "failed", "reason": "no_covariance"}
            index = covariance.index
            # The closed form adds the scaled card to whatever the book already holds in that name.
            pinned = pd.Series(
                [(context.weights.get(s, 0.0) + (scaled_weight if s == symbol else 0.0)) / risk_capital for s in index],
                index=index,
                dtype=float,
            )
            alpha = pd.Series([1.0 if s == symbol else 0.0 for s in index], index=index, dtype=float)
            optimizer = ConvexAlphaPortfolioOptimizer(
                gross_leverage_limit=10.0, max_position_weight=10.0, long_only=False, solver_seconds=2
            )
            result = optimizer.optimize(
                alpha, covariance, current_weights=pinned, lower_bounds=pinned, upper_bounds=pinned
            )
            if not result.success:
                return {"status": "failed", "reason": "solver_unsuccessful"}
            vol = math.sqrt(max(result.portfolio_variance, 0.0))
            diff = abs(vol - closed_form_pct)
            if diff > SHADOW_TOLERANCE:
                logger.warning(
                    "book sizing shadow optimiser disagrees with the closed form",
                    extra={
                        "event": "book_sizing_shadow_mismatch",
                        "symbol": symbol,
                        "closed_form_pct": closed_form_pct,
                        "shadow_pct": vol,
                    },
                )
            return {
                "status": "ok",
                "vol_after_scaled_pct": vol,
                "closed_form_pct": closed_form_pct,
                "abs_diff_pct": diff,
                "solver_message": result.message,
                "seconds": time.perf_counter() - started,
            }
        except Exception as exc:
            logger.warning(
                "Book sizing shadow optimiser failed",
                exc_info=True,
                extra={"event": "book_sizing_shadow_failed", "symbol": symbol, "reason": type(exc).__name__},
            )
            return {"status": "failed", "reason": type(exc).__name__}

    def journal_block(self, decision: BookSizingDecision | None) -> dict[str, Any]:
        """The per-candidate ``book_sizing`` journal block; all null but ``mode`` without a decision."""
        if decision is None:
            return dict.fromkeys(_JOURNAL_KEYS) | {"mode": self._config.mode}
        return {
            "mode": decision.mode,
            "status": decision.status.value,
            "factor": decision.factor,
            "would_scale": decision.would_scale,
            "vol_before_pct": decision.vol_before_pct,
            "vol_after_full_pct": decision.vol_after_full_pct,
            "quantity_before": decision.quantity_before,
            "quantity_after": decision.quantity_after,
        }


_JOURNAL_KEYS = (
    "mode",
    "status",
    "factor",
    "would_scale",
    "vol_before_pct",
    "vol_after_full_pct",
    "quantity_before",
    "quantity_after",
)
