from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from agentic_trader.broker.base import OrderRequest, OrderResult
from agentic_trader.constants import OrderClass, OrderType
from agentic_trader.execution.models import (
    ExecutionPlan,
    ExecutionPlanStatus,
    SliceStatus,
)
from agentic_trader.execution.slicer import OrderSlicer


if TYPE_CHECKING:
    from agentic_trader.broker.base import BaseBroker
    from agentic_trader.config import AppConfig

logger = logging.getLogger(__name__)


class SlicedExecutionEngine:
    """Institutional execution engine supporting TWAP, VWAP, and price-collar guardrails."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def plan(self, request: OrderRequest) -> ExecutionPlan:
        """Construct an ExecutionPlan for a given OrderRequest."""
        return OrderSlicer.plan_order(request, self.config)

    async def execute_order(
        self,
        request: OrderRequest,
        broker: BaseBroker,
        get_current_price: Callable[[str], Awaitable[float | None]] | None = None,
    ) -> OrderResult:
        """Execute order using configured algorithm (immediate, TWAP, or VWAP)."""
        plan = self.plan(request)

        # Immediate single-slice fallback
        if plan.algorithm == "immediate" or len(plan.slices) <= 1:
            logger.info(
                "Executing order immediately without slicing: %s %.2f %s",
                request.direction,
                request.quantity,
                request.symbol,
            )
            return await broker.submit_entry_order(request)

        if not broker.simulated_execution:
            return OrderResult(
                success=False,
                error_message="Broker slicing is disabled until per-slice fills and protection are reconciled; use immediate execution",
            )

        # Multi-slice algorithmic execution
        logger.info(
            "Starting %s algorithmic execution: %d slices for %.2f %s",
            plan.algorithm.upper(),
            len(plan.slices),
            request.quantity,
            request.symbol,
        )

        plan.status = ExecutionPlanStatus.IN_PROGRESS
        total_cost = 0.0
        filled_qty = 0.0
        filled_order_ids: list[str] = []

        is_long = str(request.direction).upper() in ("LONG", "BUY")

        for idx, child_slice in enumerate(plan.slices):
            # Check price collar against real-time market quote if price checker provided
            if get_current_price:
                try:
                    curr_price = await get_current_price(request.symbol)
                    if curr_price is not None:
                        breached = False
                        if (
                            is_long
                            and curr_price > child_slice.limit_price
                            or not is_long
                            and curr_price < child_slice.limit_price
                        ):
                            breached = True

                        if breached:
                            logger.warning(
                                "Microstructure collar breached on slice %d/%d for %s: current %.2f vs limit %.2f",
                                idx + 1,
                                len(plan.slices),
                                request.symbol,
                                curr_price,
                                child_slice.limit_price,
                            )
                            child_slice.status = SliceStatus.SKIPPED_COLLAR
                            child_slice.error_message = (
                                f"Price collar breached: {curr_price} vs {child_slice.limit_price}"
                            )
                            continue
                except Exception as e:
                    logger.debug("Quote check error in sliced execution: %s", e)

            # Build child order request without individual bracket to avoid duplicate exits
            child_req = OrderRequest(
                symbol=request.symbol,
                contract=request.contract,
                ticker=request.ticker,
                asset_class=request.asset_class,
                direction=request.direction,
                quantity=child_slice.quantity,
                order_type=OrderType.LIMIT,
                order_class=OrderClass.SIMPLE,
                entry_price=child_slice.limit_price,
                signal_id=request.signal_id,
                client_order_id=child_slice.slice_id,
            )

            child_slice.status = SliceStatus.SUBMITTED
            try:
                res = await broker.submit_entry_order(child_req)
                if res.success:
                    fill_px = res.fill_price or child_slice.limit_price
                    child_slice.status = SliceStatus.FILLED
                    child_slice.fill_price = fill_px
                    child_slice.fill_quantity = child_slice.quantity
                    child_slice.fill_timestamp = datetime.now(UTC)
                    child_slice.broker_order_id = res.order_id

                    filled_qty += child_slice.quantity
                    total_cost += fill_px * child_slice.quantity
                    if res.order_id:
                        filled_order_ids.append(res.order_id)
                else:
                    child_slice.status = SliceStatus.FAILED
                    child_slice.error_message = res.error_message
                    logger.warning("Child slice %d failed: %s", idx + 1, res.error_message)
            except Exception as e:
                child_slice.status = SliceStatus.FAILED
                child_slice.error_message = str(e)
                logger.error("Exception submitting child slice %d: %s", idx + 1, e)

            # Sleep between slices if not the last slice
            if idx < len(plan.slices) - 1 and self.config.execution.twap_interval_seconds > 0:
                await asyncio.sleep(self.config.execution.twap_interval_seconds)

        plan.completed_at = datetime.now(UTC)
        plan.filled_quantity = filled_qty

        if filled_qty <= 0:
            plan.status = ExecutionPlanStatus.FAILED
            return OrderResult(
                success=False,
                error_message=f"All {len(plan.slices)} algorithmic slices failed or were skipped by collar.",
            )

        avg_price = round(total_cost / filled_qty, 2)
        plan.weighted_avg_fill_price = avg_price

        if filled_qty >= request.quantity:
            plan.status = ExecutionPlanStatus.COMPLETED
        else:
            plan.status = ExecutionPlanStatus.PARTIALLY_FILLED

        logger.info(
            "Algorithmic execution completed for %s: filled %.2f/%.2f @ avg %.2f (%s)",
            request.symbol,
            filled_qty,
            request.quantity,
            avg_price,
            plan.status.value,
        )

        return OrderResult(
            success=True,
            order_id=plan.plan_id,
            fill_price=avg_price,
            fill_timestamp=plan.completed_at,
            status=plan.status.value,
            bracket_orders={"child_orders": ",".join(filled_order_ids)},
        )
