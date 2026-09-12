from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class OrderRequest(BaseModel):
    """Specification for submitting a micro futures order with bracket exits."""

    signal_id: int
    contract: str  # e.g. "/MES", "/MNQ", "/MGC", "/MCL"
    ticker: str  # e.g. "MES=F"
    direction: str  # "LONG" or "SHORT"
    entry_price: float
    stop_loss: float
    take_profit: float
    quantity: int = 1
    order_type: str = "LIMIT"  # "LIMIT" or "MARKET"


class OrderResult(BaseModel):
    """Result returned by broker execution."""

    success: bool
    order_id: str | None = None
    fill_price: float | None = None
    fill_timestamp: datetime | None = None
    error_message: str | None = None
    bracket_orders: dict[str, str] = Field(default_factory=dict)
    raw_response: dict[str, Any] = Field(default_factory=dict)


class BrokerPosition(BaseModel):
    """Active position reported by broker."""

    contract: str
    direction: str  # "LONG" or "SHORT"
    quantity: int
    entry_price: float
    current_price: float | None = None
    unrealized_pnl: float | None = None


class BaseBroker(ABC):
    """Abstract Base Class for futures broker execution engines."""

    @abstractmethod
    async def connect(self) -> bool:
        """Establish session or authenticate with the broker."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Tear down active connections or sessions."""

    @abstractmethod
    async def submit_entry_order(self, request: OrderRequest) -> OrderResult:
        """
        Submit entry order with bracket stop loss and take profit targets.
        Must return OrderResult indicating success or failure.
        """

    @abstractmethod
    async def close_position(
        self,
        contract: str,
        exit_reason: str,
        exit_price: float | None = None,
    ) -> OrderResult:
        """
        Close an open position for a given contract.
        """

    @abstractmethod
    async def get_positions(self) -> list[BrokerPosition]:
        """Fetch active positions currently open at the broker."""
