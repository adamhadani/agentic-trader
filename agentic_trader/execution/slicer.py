from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from agentic_trader.constants import AssetClass, Direction
from agentic_trader.execution.models import ChildSlice, ExecutionPlan, SliceStatus


if TYPE_CHECKING:
    from agentic_trader.broker.base import OrderRequest
    from agentic_trader.config import AppConfig


class OrderSlicer:
    """Calculates order slicing schedules and microstructure price collars."""

    @staticmethod
    def compute_price_collar(
        direction: Direction | str,
        entry_price: float,
        tick_size: float,
        asset_class: AssetClass,
        config: AppConfig,
    ) -> float:
        """Compute maximum allowable limit price collar to prevent chasing adverse price action."""
        exec_cfg = config.execution
        dir_upper = str(direction).upper()

        if asset_class == AssetClass.FUTURES:
            collar_points = exec_cfg.price_collar_ticks * tick_size
        else:
            # Equities use percentage collar
            collar_points = max(exec_cfg.price_collar_pct * entry_price, tick_size)

        raw_limit = entry_price + collar_points if dir_upper in ("LONG", "BUY") else entry_price - collar_points

        # Round to nearest tick
        ticks = round(raw_limit / tick_size)
        return round(ticks * tick_size, 2)

    @classmethod
    def slice_quantities(
        cls,
        total_quantity: float,
        num_slices: int,
        is_integer_asset: bool = False,
        weights: list[float] | None = None,
    ) -> list[float]:
        """Split total quantity into discrete or fractional slices."""
        if num_slices <= 1 or total_quantity <= 0:
            return [total_quantity]

        if is_integer_asset:
            int_total = round(total_quantity)
            actual_slices = min(num_slices, int_total)
            if actual_slices <= 1:
                return [float(int_total)]

            if weights is None:
                base_qty = int_total // actual_slices
                remainder = int_total % actual_slices
                return [float(base_qty + (1 if i < remainder else 0)) for i in range(actual_slices)]
            else:
                norm_weights = [w / sum(weights[:actual_slices]) for w in weights[:actual_slices]]
                raw_allocations = [round(w * int_total) for w in norm_weights]
                diff = int_total - sum(raw_allocations)
                for i in range(abs(diff)):
                    idx = i % actual_slices
                    raw_allocations[idx] += 1 if diff > 0 else -1
                return [float(max(1, q)) for q in raw_allocations]
        else:
            # Continuous / Equity asset
            if weights is None:
                per_slice = total_quantity / num_slices
                return [round(per_slice, 2) for _ in range(num_slices)]
            else:
                actual_slices = min(num_slices, len(weights))
                sub_weights = weights[:actual_slices]
                total_w = sum(sub_weights)
                return [round((w / total_w) * total_quantity, 2) for w in sub_weights]

    @classmethod
    def plan_order(cls, request: OrderRequest, config: AppConfig) -> ExecutionPlan:
        """Construct an ExecutionPlan based on configured execution algorithm and size thresholds."""
        exec_cfg = config.execution
        algo = exec_cfg.algorithm.lower().strip()
        contract_info = config.contracts.get(request.symbol or request.contract or "")
        tick_size = (
            contract_info.tick_size if contract_info else (0.01 if request.asset_class == AssetClass.EQUITY else 0.25)
        )
        entry_price = request.entry_price if request.entry_price is not None else 0.0

        plan_id = f"plan_{uuid.uuid4().hex[:8]}"

        # Check slicing eligibility thresholds
        is_futures = request.asset_class == AssetClass.FUTURES
        min_thresh = exec_cfg.min_slice_quantity_futures if is_futures else exec_cfg.min_slice_quantity_equity

        if algo == "immediate" or request.quantity < min_thresh or entry_price <= 0:
            # Single-slice immediate order
            limit_price = (
                cls.compute_price_collar(
                    direction=request.direction,
                    entry_price=entry_price,
                    tick_size=tick_size,
                    asset_class=request.asset_class,
                    config=config,
                )
                if entry_price > 0
                else entry_price
            )
            single_slice = ChildSlice(
                slice_id=f"{plan_id}_0",
                slice_index=0,
                quantity=request.quantity,
                target_price=entry_price,
                limit_price=limit_price,
                status=SliceStatus.PENDING,
            )
            return ExecutionPlan(
                plan_id=plan_id,
                request=request,
                algorithm="immediate",
                total_quantity=request.quantity,
                slices=[single_slice],
            )

        # Multi-slice algorithmic execution (TWAP or VWAP)
        num_slices = max(2, exec_cfg.twap_slices)
        weights = exec_cfg.vwap_intraday_profile if algo == "vwap" else None

        quantities = cls.slice_quantities(
            total_quantity=request.quantity,
            num_slices=num_slices,
            is_integer_asset=is_futures,
            weights=weights,
        )

        limit_price = cls.compute_price_collar(
            direction=request.direction,
            entry_price=entry_price,
            tick_size=tick_size,
            asset_class=request.asset_class,
            config=config,
        )

        child_slices = [
            ChildSlice(
                slice_id=f"{plan_id}_{idx}",
                slice_index=idx,
                quantity=qty,
                target_price=entry_price,
                limit_price=limit_price,
                status=SliceStatus.PENDING,
            )
            for idx, qty in enumerate(quantities)
            if qty > 0
        ]

        return ExecutionPlan(
            plan_id=plan_id,
            request=request,
            algorithm=algo,
            total_quantity=request.quantity,
            slices=child_slices,
        )
