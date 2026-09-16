from unittest.mock import AsyncMock

import pytest

from agentic_trader.broker.base import BaseBroker, BrokerPosition, OrderRequest, OrderResult
from agentic_trader.config import AppConfig, ContractConfig, ExecutionConfig
from agentic_trader.constants import AssetClass, Direction, OrderClass
from agentic_trader.execution.engine import SlicedExecutionEngine
from agentic_trader.execution.slicer import OrderSlicer


class DummyBroker(BaseBroker):
    @property
    def simulated_execution(self) -> bool:
        return True

    def __init__(self):
        self.submitted_orders: list[OrderRequest] = []
        self.simulated_fill_price: float | None = None
        self.fail_all: bool = False

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        pass

    async def submit_entry_order(self, request: OrderRequest) -> OrderResult:
        self.submitted_orders.append(request)
        if self.fail_all:
            return OrderResult(success=False, error_message="Simulated broker rejection")
        fill_px = self.simulated_fill_price or request.entry_price or 100.0
        return OrderResult(
            success=True,
            order_id=f"ORD-{len(self.submitted_orders)}",
            fill_price=fill_px,
        )

    async def close_position(
        self,
        symbol: str | None = None,
        exit_reason: str = "MANUAL_CLOSE",
        exit_price: float | None = None,
        quantity: float | None = None,
        contract: str | None = None,
    ) -> OrderResult:
        return OrderResult(success=True)

    async def get_positions(self) -> list[BrokerPosition]:
        return []


def test_compute_price_collar():
    config = AppConfig(
        execution=ExecutionConfig(price_collar_ticks=2.0, price_collar_pct=0.001),
        contracts={
            "/MES": ContractConfig(
                ticker="MES=F", name="Micro ES", multiplier=5.0, tick_size=0.25, asset_class=AssetClass.FUTURES
            ),
            "AAPL": ContractConfig(
                ticker="AAPL", name="Apple", multiplier=1.0, tick_size=0.01, asset_class=AssetClass.EQUITY
            ),
        },
    )

    # Futures: 2 ticks = 0.50 pts
    long_futures_collar = OrderSlicer.compute_price_collar(
        direction=Direction.LONG,
        entry_price=5000.0,
        tick_size=0.25,
        asset_class=AssetClass.FUTURES,
        config=config,
    )
    assert long_futures_collar == 5000.50

    short_futures_collar = OrderSlicer.compute_price_collar(
        direction=Direction.SHORT,
        entry_price=5000.0,
        tick_size=0.25,
        asset_class=AssetClass.FUTURES,
        config=config,
    )
    assert short_futures_collar == 4999.50

    # Equity: 0.1% of $200 = $0.20
    long_equity_collar = OrderSlicer.compute_price_collar(
        direction=Direction.LONG,
        entry_price=200.0,
        tick_size=0.01,
        asset_class=AssetClass.EQUITY,
        config=config,
    )
    assert long_equity_collar == 200.20


def test_slice_quantities_discrete_futures():
    # 4 contracts over 4 slices -> [1.0, 1.0, 1.0, 1.0]
    slices_4 = OrderSlicer.slice_quantities(total_quantity=4.0, num_slices=4, is_integer_asset=True)
    assert slices_4 == [1.0, 1.0, 1.0, 1.0]

    # 3 contracts over 2 slices -> [2.0, 1.0]
    slices_3 = OrderSlicer.slice_quantities(total_quantity=3.0, num_slices=2, is_integer_asset=True)
    assert slices_3 == [2.0, 1.0]

    # 1 contract over 4 slices -> clamped to [1.0]
    slices_1 = OrderSlicer.slice_quantities(total_quantity=1.0, num_slices=4, is_integer_asset=True)
    assert slices_1 == [1.0]


def test_slice_quantities_equity_vwap():
    weights = [0.25, 0.15, 0.10, 0.10, 0.15, 0.25]
    slices = OrderSlicer.slice_quantities(total_quantity=1000.0, num_slices=6, is_integer_asset=False, weights=weights)
    assert len(slices) == 6
    assert slices == [250.0, 150.0, 100.0, 100.0, 150.0, 250.0]
    assert sum(slices) == 1000.0


def test_plan_order_threshold_filtering():
    config = AppConfig(
        execution=ExecutionConfig(
            algorithm="twap",
            min_slice_quantity_futures=2.0,
            min_slice_quantity_equity=100.0,
            twap_slices=3,
        ),
        contracts={
            "/MES": ContractConfig(
                ticker="MES=F", name="Micro ES", multiplier=5.0, tick_size=0.25, asset_class=AssetClass.FUTURES
            )
        },
    )

    # 1 contract futures is below threshold -> immediate single slice
    req_small = OrderRequest(
        symbol="/MES",
        asset_class=AssetClass.FUTURES,
        direction=Direction.LONG,
        quantity=1.0,
        entry_price=5000.0,
    )
    plan_small = OrderSlicer.plan_order(req_small, config)
    assert plan_small.algorithm == "immediate"
    assert len(plan_small.slices) == 1

    # 3 contracts futures >= threshold -> multi-slice TWAP
    req_large = OrderRequest(
        symbol="/MES",
        asset_class=AssetClass.FUTURES,
        direction=Direction.LONG,
        quantity=3.0,
        entry_price=5000.0,
    )
    plan_large = OrderSlicer.plan_order(req_large, config)
    assert plan_large.algorithm == "twap"
    assert len(plan_large.slices) == 3
    assert [s.quantity for s in plan_large.slices] == [1.0, 1.0, 1.0]


@pytest.mark.asyncio
async def test_sliced_execution_engine_twap_success():
    config = AppConfig(
        execution=ExecutionConfig(
            algorithm="twap",
            min_slice_quantity_futures=2.0,
            twap_slices=3,
            twap_interval_seconds=0.001,  # fast test interval
        ),
        contracts={
            "/MNQ": ContractConfig(
                ticker="MNQ=F", name="Micro NQ", multiplier=2.0, tick_size=0.25, asset_class=AssetClass.FUTURES
            )
        },
    )

    engine = SlicedExecutionEngine(config=config)
    broker = DummyBroker()
    broker.simulated_fill_price = 18000.25

    req = OrderRequest(
        symbol="/MNQ",
        asset_class=AssetClass.FUTURES,
        direction=Direction.LONG,
        quantity=3.0,
        entry_price=18000.0,
        stop_loss=17950.0,
        take_profit=18100.0,
    )

    res = await engine.execute_order(req, broker)

    assert res.success is True
    assert res.fill_price == 18000.25
    assert len(broker.submitted_orders) == 3
    assert all(o.quantity == 1.0 for o in broker.submitted_orders)
    assert all(o.order_class == OrderClass.SIMPLE for o in broker.submitted_orders)


@pytest.mark.asyncio
async def test_sliced_execution_engine_price_collar_rejection():
    config = AppConfig(
        execution=ExecutionConfig(
            algorithm="twap",
            min_slice_quantity_futures=2.0,
            twap_slices=2,
            twap_interval_seconds=0.001,
            price_collar_ticks=2.0,  # 0.50 pts max collar
        ),
        contracts={
            "/MES": ContractConfig(
                ticker="MES=F", name="Micro ES", multiplier=5.0, tick_size=0.25, asset_class=AssetClass.FUTURES
            )
        },
    )

    engine = SlicedExecutionEngine(config=config)
    broker = DummyBroker()

    # Price checker returns price that breaches collar: entry 5000.0, collar limit 5000.50, quote 5005.00
    mock_price_fn = AsyncMock(return_value=5005.0)

    req = OrderRequest(
        symbol="/MES",
        asset_class=AssetClass.FUTURES,
        direction=Direction.LONG,
        quantity=2.0,
        entry_price=5000.0,
    )

    res = await engine.execute_order(req, broker, get_current_price=mock_price_fn)

    assert res.success is False
    assert "skipped by collar" in (res.error_message or "").lower()
    assert len(broker.submitted_orders) == 0


@pytest.mark.parametrize("simulated", [False, True])
async def test_multi_slice_requires_explicit_simulated_execution(config, simulated):
    class TestBroker(DummyBroker):
        @property
        def simulated_execution(self):
            return simulated

    config.execution.algorithm = "twap"
    config.execution.twap_interval_seconds = 0
    broker = TestBroker()
    result = await SlicedExecutionEngine(config).execute_order(
        OrderRequest(symbol="SPY", asset_class=AssetClass.EQUITY, quantity=200, entry_price=100), broker
    )
    assert result.success is simulated
    assert bool(broker.submitted_orders) is simulated
