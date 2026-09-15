from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from agentic_trader.constants import (
    AssetClass,
    Direction,
    ExitReason,
    OrderClass,
    OrderSide,
    OrderType,
    TimeInForce,
)


class OrderRequest(BaseModel):
    """
    Generalized multi-asset order specification.
    Supports futures, equities, crypto, and derivatives with bracket or simple order structures.
    """

    symbol: str = Field(default="", description="Trading symbol (e.g. '/MES', 'SPY', 'AAPL', 'BTC/USD')")
    contract: str | None = Field(default=None, description="Legacy futures contract alias")
    ticker: str | None = Field(default=None, description="Underlying quote ticker (e.g. 'MES=F')")
    asset_class: AssetClass = Field(default=AssetClass.FUTURES)
    direction: Direction | str = Field(default=Direction.LONG)
    side: OrderSide | str | None = Field(default=None)
    quantity: float = Field(default=1.0, gt=0)
    order_type: OrderType | str = Field(default=OrderType.LIMIT)
    order_class: OrderClass | str = Field(default=OrderClass.BRACKET)
    time_in_force: TimeInForce | str = Field(default=TimeInForce.GTC)
    entry_price: float | None = Field(default=None)
    stop_loss: float | None = Field(default=None)
    take_profit: float | None = Field(default=None)
    signal_id: int | None = Field(default=None)
    client_order_id: str | None = Field(default=None)

    @model_validator(mode="before")
    @classmethod
    def reconcile_symbol_and_side(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # If contract is provided without symbol, mirror to symbol
            if "contract" in data and not data.get("symbol"):
                data["symbol"] = data["contract"]
            elif "symbol" in data and not data.get("contract"):
                data["contract"] = data["symbol"]

            # Derive side from direction if not specified
            direction = str(data.get("direction", Direction.LONG)).upper()
            if not data.get("side"):
                data["side"] = OrderSide.BUY if direction in ("LONG", "BUY") else OrderSide.SELL

            # Default ticker to symbol if missing
            if not data.get("ticker") and data.get("symbol"):
                data["ticker"] = data["symbol"]
        return data

    @property
    def is_bracket(self) -> bool:
        """True if order includes stop-loss and take-profit exit targets."""
        return bool(self.stop_loss is not None and self.take_profit is not None)


class OrderResult(BaseModel):
    """Execution result returned by broker."""

    success: bool
    order_id: str | None = None
    fill_price: float | None = None
    fill_timestamp: datetime | None = None
    filled_quantity: float | None = None
    error_message: str | None = None
    bracket_orders: dict[str, str] = Field(default_factory=dict)
    raw_response: dict[str, Any] = Field(default_factory=dict)
    status: str | None = None


class BrokerPosition(BaseModel):
    """Active open position reported by broker."""

    symbol: str = Field(default="")
    contract: str | None = Field(default=None)
    asset_class: AssetClass = Field(default=AssetClass.FUTURES)
    direction: Direction | str = Field(default=Direction.LONG)
    quantity: float = Field(default=1.0)
    entry_price: float = Field(default=0.0)
    current_price: float | None = Field(default=None)
    unrealized_pnl: float | None = Field(default=None)

    @model_validator(mode="before")
    @classmethod
    def sync_symbol_contract(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "contract" in data and not data.get("symbol"):
                data["symbol"] = data["contract"]
            elif "symbol" in data and not data.get("contract"):
                data["contract"] = data["symbol"]
        return data


class ReconciliationEvent(BaseModel):
    """
    Event generated when a broker detects that an order filled (exit TP/SL/market, or entry fill confirmation).

    Fields:
        signal_id: ID of the matching signal (0 if arriving from stream before matching).
        symbol: Clean symbol (e.g. SPY, MES).
        contract: Formatted contract identifier (e.g. /MES, SPY).
        direction: Position direction (LONG or SHORT) that this event relates to.
        exit_price: Fill price of the executed order.
        exit_reason: Reason for exit if applicable (STOP_LOSS, TAKE_PROFIT, MANUAL_CLOSE).
        exit_timestamp: Timestamp of the fill event.
        realized_pnl: Realized PnL in USD (if computed).
        broker_order_id: Broker order ID of the specific order that filled.
        order_side: Explicit order side ("buy" or "sell"). Critical for verifying that exit
                    orders strictly oppose the position direction (sell closes long, buy closes short).
    """

    signal_id: int
    symbol: str = Field(default="")
    contract: str | None = Field(default=None)
    direction: str = Field(default=Direction.LONG)
    exit_price: float
    exit_reason: str = Field(default=ExitReason.TAKE_PROFIT)
    exit_timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    realized_pnl: float | None = None
    broker_order_id: str | None = None
    order_side: str | None = None


class BaseBroker(ABC):
    """
    Abstract Base Class for multi-asset execution brokers.
    Provides standard interfaces for futures, equities, crypto, and derivatives.
    """

    @abstractmethod
    async def connect(self) -> bool:
        """Establish session or authenticate with the broker."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Tear down active connections or sessions."""

    async def submit_order(self, request: OrderRequest) -> OrderResult:
        """
        Submit a general order (market, limit, stop, or bracket).
        Default implementation delegates to submit_entry_order for backward compatibility.
        """
        return await self.submit_entry_order(request)

    @abstractmethod
    async def submit_entry_order(self, request: OrderRequest) -> OrderResult:
        """
        Submit an entry order with optional bracket targets.
        Must return OrderResult indicating success or failure.
        """

    @abstractmethod
    async def close_position(
        self,
        symbol: str | None = None,
        exit_reason: str = "MANUAL_CLOSE",
        exit_price: float | None = None,
        quantity: float | None = None,
        contract: str | None = None,
    ) -> OrderResult:
        """
        Close an open position for a given symbol or contract.
        """

    @abstractmethod
    async def get_positions(self) -> list[BrokerPosition]:
        """Fetch active positions currently open at the broker."""

    async def get_account_balance(self) -> dict[str, float]:
        """Fetch current cash balance and portfolio value if supported by broker."""
        return {}

    async def reconcile_positions(self, active_positions: list[dict[str, Any]]) -> list[ReconciliationEvent]:
        """Reconcile active positions against broker order and position states.

        Default implementation returns an empty list. Subclasses inspect open/closed
        bracket orders or market prices to report exits.
        """
        return []

    @property
    def authoritative_positions(self) -> bool:
        """Whether broker positions carry authoritative account cost basis and valuation."""
        return False

    async def get_entry_execution(self, position: dict[str, Any]) -> OrderResult | None:
        """Return a confirmed execution of the exact tracked entry, when supported."""
        return None

    @property
    def supports_trade_stream(self) -> bool:
        """Whether this broker supports real-time WebSocket trade/fill streaming."""
        return False

    async def start_trade_stream(self, on_fill_callback: Callable[[ReconciliationEvent], Awaitable[None]]) -> None:
        """Start listening to real-time fill events via broker WebSocket stream if supported."""
        return

    async def stop_trade_stream(self) -> None:
        """Stop real-time fill events listener."""
        return

    @property
    def supports_order_modification(self) -> bool:
        """Whether this broker supports amending resting bracket stop orders."""
        return False

    async def modify_order_stop(
        self,
        order_id: str | None = None,
        symbol: str | None = None,
        new_stop_price: float = 0.0,
        client_order_id: str | None = None,
    ) -> OrderResult:
        """
        Modify the price of a resting stop order on the broker exchange.
        Default implementation returns an unsupported failure result for graceful degradation.
        """
        return OrderResult(
            success=False,
            error_message=f"{self.__class__.__name__} does not support broker-side stop modification",
        )

    async def cancel_all_orders(self) -> int:
        """Cancel all open or resting orders at the broker.

        Returns the number of orders successfully cancelled.
        """
        return 0
