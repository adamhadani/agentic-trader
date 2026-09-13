import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from agentic_trader.broker.base import (
    BaseBroker,
    BrokerPosition,
    OrderRequest,
    OrderResult,
    ReconciliationEvent,
)
from agentic_trader.config import RedundancyConfig


logger = logging.getLogger(__name__)


class CircuitState:
    CLOSED = "CLOSED"  # Primary is healthy and handling requests
    OPEN = "OPEN"  # Primary has failed; routing to fallback
    HALF_OPEN = "HALF_OPEN"  # Testing primary health after cooldown


class RedundantBroker(BaseBroker):
    """
    High-Availability Redundant Broker Wrapper with Circuit Breaker Failover.
    Wraps a primary broker (e.g. Tradovate or Alpaca) and a fallback broker (e.g. PaperBroker),
    automatically detecting connection outages or repeated order failures, tripping to the fallback broker,
    and probing primary recovery for zero-downtime automated execution.
    """

    def __init__(
        self,
        primary_broker: BaseBroker,
        fallback_broker: BaseBroker,
        config: RedundancyConfig | None = None,
    ):
        self.primary = primary_broker
        self.fallback = fallback_broker
        self.config = config or RedundancyConfig()

        self.state: str = CircuitState.CLOSED
        self.consecutive_failures: int = 0
        self.last_failure_time: datetime | None = None
        self._connected: bool = False

    @property
    def active_broker(self) -> BaseBroker:
        """Return whichever broker is currently active according to circuit state."""
        return self.fallback if self.state == CircuitState.OPEN else self.primary

    def _record_failure(self, error: Exception | str | None = None) -> None:
        self.consecutive_failures += 1
        self.last_failure_time = datetime.now(UTC)
        logger.warning(
            "Primary broker failure #%d: %s",
            self.consecutive_failures,
            error,
            extra={"consecutive_failures": self.consecutive_failures, "circuit_state": self.state},
        )
        if self.consecutive_failures >= self.config.max_consecutive_failures and self.state != CircuitState.OPEN:
            self.state = CircuitState.OPEN
            logger.error(
                "CIRCUIT TRIPPED TO OPEN: Primary broker exceeded %d consecutive failures. Failover to %s active.",
                self.config.max_consecutive_failures,
                type(self.fallback).__name__,
            )

    def _record_success(self) -> None:
        if self.consecutive_failures > 0 or self.state != CircuitState.CLOSED:
            logger.info("Primary broker reported healthy operation. Circuit reset to CLOSED.")
        self.consecutive_failures = 0
        self.state = CircuitState.CLOSED

    def _check_cooldown(self) -> None:
        """Check if recovery probe interval has elapsed to transition from OPEN to HALF_OPEN."""
        if self.state == CircuitState.OPEN and self.config.auto_failback and self.last_failure_time:
            elapsed = (datetime.now(UTC) - self.last_failure_time).total_seconds()
            if elapsed >= self.config.recovery_probe_interval_seconds:
                logger.info(
                    "Recovery cooldown (%.1fs) elapsed. Transitioning circuit to HALF_OPEN to probe primary broker.",
                    elapsed,
                )
                self.state = CircuitState.HALF_OPEN

    async def connect(self) -> bool:
        """Connect primary broker; fall back to fallback broker on failure."""
        primary_ok = False
        try:
            primary_ok = await self.primary.connect()
        except Exception as e:
            logger.warning("Primary broker connect exception: %s", e)
            primary_ok = False

        if primary_ok:
            self._record_success()
            self._connected = True
            try:
                await self.fallback.connect()
            except Exception as fe:
                logger.debug("Pre-connecting fallback broker raised: %s", fe)
            return True

        self._record_failure("Primary connect returned False or raised")
        logger.warning("Failing over connection to fallback broker (%s)...", type(self.fallback).__name__)
        fallback_ok = await self.fallback.connect()
        self._connected = fallback_ok
        return fallback_ok

    async def disconnect(self) -> None:
        """Disconnect both primary and fallback brokers."""
        await asyncio.gather(
            self.primary.disconnect(),
            self.fallback.disconnect(),
            return_exceptions=True,
        )
        self._connected = False

    async def submit_entry_order(self, request: OrderRequest) -> OrderResult:
        """Submit order using active broker with automatic failover."""
        self._check_cooldown()

        if self.state == CircuitState.OPEN:
            logger.info("Routing order to fallback broker due to OPEN circuit breaker...")
            return await self.fallback.submit_entry_order(request)

        try:
            res = await self.primary.submit_entry_order(request)
            if res.success:
                self._record_success()
                return res
            else:
                err = (res.error_message or "").lower()
                if any(
                    x in err
                    for x in (
                        "connection",
                        "timeout",
                        "unavailable",
                        "503",
                        "502",
                        "reset",
                        "disconnected",
                        "not connected",
                    )
                ):
                    self._record_failure(err)
                    if self.state == CircuitState.OPEN:
                        logger.warning("Order failed on primary (%s). Executing failover on fallback broker...", err)
                        return await self.fallback.submit_entry_order(request)
                return res
        except Exception as e:
            self._record_failure(e)
            if self.state == CircuitState.OPEN:
                logger.warning(
                    "Exception on primary broker order submission (%s). Executing failover on fallback broker...", e
                )
                return await self.fallback.submit_entry_order(request)
            raise

    async def close_position(
        self,
        symbol: str | None = None,
        exit_reason: str = "MANUAL_CLOSE",
        exit_price: float | None = None,
        quantity: float | None = None,
        contract: str | None = None,
    ) -> OrderResult:
        """Close position on active broker or fallback if primary fails."""
        self._check_cooldown()
        if self.state == CircuitState.OPEN:
            return await self.fallback.close_position(
                symbol=symbol,
                exit_reason=exit_reason,
                exit_price=exit_price,
                quantity=quantity,
                contract=contract,
            )

        try:
            res = await self.primary.close_position(
                symbol=symbol,
                exit_reason=exit_reason,
                exit_price=exit_price,
                quantity=quantity,
                contract=contract,
            )
            if res.success:
                self._record_success()
                return res
            # If position not found on primary, try closing on fallback
            res_fb = await self.fallback.close_position(
                symbol=symbol,
                exit_reason=exit_reason,
                exit_price=exit_price,
                quantity=quantity,
                contract=contract,
            )
            if res_fb.success:
                return res_fb
            return res
        except Exception as e:
            self._record_failure(e)
            return await self.fallback.close_position(
                symbol=symbol,
                exit_reason=exit_reason,
                exit_price=exit_price,
                quantity=quantity,
                contract=contract,
            )

    async def get_positions(self) -> list[BrokerPosition]:
        """Aggregate positions across primary and fallback brokers."""
        self._check_cooldown()
        if self.state == CircuitState.OPEN:
            return await self.fallback.get_positions()

        try:
            primary_positions = await self.primary.get_positions()
            self._record_success()
            fallback_positions = await self.fallback.get_positions()
            return primary_positions + fallback_positions
        except Exception as e:
            self._record_failure(e)
            return await self.fallback.get_positions()

    async def get_account_balance(self) -> dict[str, float]:
        """Fetch account balance from active broker."""
        self._check_cooldown()
        if self.state == CircuitState.OPEN:
            return await self.fallback.get_account_balance()
        try:
            bal = await self.primary.get_account_balance()
            self._record_success()
            return bal
        except Exception as e:
            self._record_failure(e)
            return await self.fallback.get_account_balance()

    async def reconcile_positions(self, active_positions: list[dict[str, Any]]) -> list[ReconciliationEvent]:
        """Reconcile active positions across primary and fallback brokers."""
        self._check_cooldown()
        events: list[ReconciliationEvent] = []
        try:
            if self.state != CircuitState.OPEN:
                primary_events = await self.primary.reconcile_positions(active_positions)
                self._record_success()
                events.extend(primary_events)
        except Exception as e:
            self._record_failure(e)

        try:
            fallback_events = await self.fallback.reconcile_positions(active_positions)
            seen_ids = {e.signal_id for e in events}
            events.extend(fe for fe in fallback_events if fe.signal_id not in seen_ids)
        except Exception as e:
            logger.warning("Fallback reconcile failed: %s", e)

        return events

    async def start_trade_stream(self, on_fill_callback: Callable[[ReconciliationEvent], Awaitable[None]]) -> None:
        """Start trade stream on primary; trip to fallback if primary stream fails."""
        self._check_cooldown()
        if self.state == CircuitState.OPEN:
            await self.fallback.start_trade_stream(on_fill_callback)
            return

        try:
            await self.primary.start_trade_stream(on_fill_callback)
        except Exception as e:
            self._record_failure(e)
            logger.warning("Primary trade stream terminated: %s. Starting fallback trade stream...", e)
            await self.fallback.start_trade_stream(on_fill_callback)

    async def stop_trade_stream(self) -> None:
        """Stop trade streams on both brokers."""
        await asyncio.gather(
            self.primary.stop_trade_stream(),
            self.fallback.stop_trade_stream(),
            return_exceptions=True,
        )
