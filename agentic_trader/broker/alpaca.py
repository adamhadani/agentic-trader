import asyncio
import contextlib
import logging
import math
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import (
    OrderClass as AlpacaOrderClass,
    OrderSide as AlpacaOrderSide,
    OrderStatus as AlpacaOrderStatus,
    OrderType as AlpacaOrderType,
    QueryOrderStatus,
    TimeInForce as AlpacaTimeInForce,
)
from alpaca.trading.requests import (
    GetOrderByIdRequest,
    GetOrdersRequest,
    LimitOrderRequest,
    MarketOrderRequest,
    ReplaceOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)
from alpaca.trading.stream import TradingStream

from agentic_trader.broker.base import (
    BaseBroker,
    BrokerPosition,
    OrderRequest,
    OrderResult,
    PositionCloseRequest,
    ReconciliationEvent,
)
from agentic_trader.config import AppConfig
from agentic_trader.constants import (
    ALPACA_MAX_ORDERS_PER_PAGE,
    BROKER_PRICE_TOLERANCE,
    BROKER_QUANTITY_TOLERANCE,
    AssetClass,
    CloseRequestStatus,
    Direction,
    ExitReason,
)


logger = logging.getLogger(__name__)


class AlpacaBroker(BaseBroker):
    """
    Alpaca Trading API Integration using the official alpaca-py SDK.
    Supports automated execution for Equities and Crypto with bracket orders.
    Works with both Alpaca Paper Trading and Live Trading accounts.
    """

    def __init__(self, config: AppConfig, client: TradingClient | None = None):
        self.config = config
        self.is_paper = getattr(config, "alpaca_paper", True)
        self.api_key = getattr(config, "alpaca_api_key", None)
        self.api_secret = getattr(config, "alpaca_api_secret", None)
        base_url_raw = getattr(config, "alpaca_base_url", None)
        if base_url_raw:
            self.base_url: str | None = base_url_raw.strip().rstrip("/").removesuffix("/v2").rstrip("/")
        else:
            self.base_url = None

        if self.base_url and "paper" in self.base_url.lower():
            self.is_paper = True
        self.client: TradingClient | None = client
        self.reconciliation_evidence: list[dict[str, Any]] = []
        self._connected: bool = False
        self._trade_stream: TradingStream | None = None

    def _validate_credentials(self) -> None:
        missing: list[str] = []
        if not self.api_key:
            missing.append("APCA_API_KEY_ID / ALPACA_KEY_ID")
        if not self.api_secret:
            missing.append("APCA_API_SECRET_KEY / ALPACA_SECRET_KEY")
        if missing:
            raise ValueError(
                f"Missing required Alpaca credentials: {', '.join(missing)}. "
                "Please configure these variables in your .envrc file."
            )

    async def connect(self) -> bool:
        """Verify Alpaca credentials and account connectivity via TradingClient."""
        self._validate_credentials()
        if self.client is None:
            self.client = TradingClient(
                api_key=self.api_key,
                secret_key=self.api_secret,
                paper=self.is_paper,
                url_override=self.base_url,
            )

        env_name = "paper" if self.is_paper else "live"
        logger.info(
            "Connecting to Alpaca via official SDK (%s environment)...",
            env_name,
            extra={"broker": "AlpacaBroker", "env": env_name},
        )
        try:
            account = await asyncio.to_thread(self.client.get_account)
            status = str(account.get("status") if isinstance(account, dict) else getattr(account, "status", ""))
            if "ACTIVE" in status.upper():
                self._connected = True
                acct_num = str(
                    account.get("account_number")
                    if isinstance(account, dict)
                    else getattr(account, "account_number", "")
                )
                buying_pwr = str(
                    account.get("buying_power") if isinstance(account, dict) else getattr(account, "buying_power", "0")
                )
                acct_id = str(account.get("id") if isinstance(account, dict) else getattr(account, "id", ""))
                logger.info(
                    "Successfully connected to Alpaca Account %s (Buying Power: $%s)",
                    acct_num,
                    buying_pwr,
                    extra={"account_id": acct_id, "status": status, "broker": "AlpacaBroker"},
                )
                return True
            else:
                logger.error("Alpaca account is not active: status=%s", status)
                return False
        except Exception as e:
            logger.error("Failed to connect to Alpaca API via SDK: %s", e)
            return False

    async def disconnect(self) -> None:
        """Close Alpaca broker session."""
        self.client = None
        self._connected = False
        logger.info("Alpaca broker session closed.", extra={"broker": "AlpacaBroker"})

    async def submit_entry_order(self, request: OrderRequest) -> OrderResult:
        """
        Submit a bracket or simple order to Alpaca via the official SDK.
        """
        if not self._connected or not self.client:
            connected = await self.connect()
            if not connected or not self.client:
                return OrderResult(
                    success=False,
                    error_message="Not connected to Alpaca API. Please verify credentials.",
                )

        side = (
            AlpacaOrderSide.BUY
            if str(request.direction).upper() in ("LONG", str(Direction.LONG))
            else AlpacaOrderSide.SELL
        )
        symbol = request.symbol.strip("/").upper()

        time_in_force = AlpacaTimeInForce.GTC
        if str(request.time_in_force).upper() == "DAY":
            time_in_force = AlpacaTimeInForce.DAY

        take_profit = (
            TakeProfitRequest(limit_price=round(request.take_profit, 2)) if request.take_profit is not None else None
        )
        stop_loss = StopLossRequest(stop_price=round(request.stop_loss, 2)) if request.stop_loss is not None else None

        order_class = AlpacaOrderClass.SIMPLE
        if request.is_bracket and take_profit and stop_loss:
            order_class = AlpacaOrderClass.BRACKET

        req_args: dict[str, Any] = {
            "symbol": symbol,
            "qty": request.quantity,
            "side": side,
            "time_in_force": time_in_force,
            "order_class": order_class,
        }
        if order_class == AlpacaOrderClass.BRACKET:
            req_args["take_profit"] = take_profit
            req_args["stop_loss"] = stop_loss

        is_limit = str(request.order_type).upper() == "LIMIT" and request.entry_price is not None
        if is_limit and request.entry_price is not None:
            req_args["limit_price"] = round(request.entry_price, 2)
            alpaca_order_req: MarketOrderRequest | LimitOrderRequest = LimitOrderRequest(**req_args)
        else:
            alpaca_order_req = MarketOrderRequest(**req_args)

        try:
            logger.info(
                "Submitting Alpaca %s order for %s %.2f %s via SDK...",
                order_class.value,
                side.value,
                request.quantity,
                symbol,
                extra={
                    "symbol": symbol,
                    "side": side.value,
                    "quantity": request.quantity,
                    "order_class": order_class.value,
                    "broker": "AlpacaBroker",
                },
            )
            order = await asyncio.to_thread(self.client.submit_order, alpaca_order_req)

            order_id = str(getattr(order, "id", ""))
            avg_price = getattr(order, "filled_avg_price", None)
            fill_price = float(avg_price) if avg_price is not None else None
            bracket_orders: dict[str, str] = {}
            legs = getattr(order, "legs", None)
            if legs:
                for leg in legs:
                    leg_type = str(getattr(leg, "order_type", getattr(leg, "type", "")))
                    leg_id = str(getattr(leg, "id", ""))
                    if "stop" in leg_type.lower():
                        bracket_orders["stop_loss_id"] = leg_id
                    elif "limit" in leg_type.lower():
                        bracket_orders["take_profit_id"] = leg_id

            raw_resp: dict[str, Any] = order.model_dump() if hasattr(order, "model_dump") else {"id": order_id}
            return OrderResult(
                success=True,
                order_id=order_id,
                fill_price=fill_price,
                fill_timestamp=self._filled_at(order),
                bracket_orders=bracket_orders,
                raw_response=raw_resp,
                status=str(getattr(order, "status", "")),
            )
        except APIError as e:
            logger.error("Alpaca API error during order submission: %s", e)
            return OrderResult(success=False, error_message=str(e))
        except Exception as e:
            logger.exception("Exception during Alpaca order submission")
            return OrderResult(success=False, error_message=str(e))

    async def close_position(
        self,
        symbol: str | None = None,
        exit_reason: str = "MANUAL_CLOSE",
        exit_price: float | None = None,
        quantity: float | None = None,
        contract: str | None = None,
    ) -> OrderResult:
        """Use the same bracket-aware workflow as coordinated operator closes."""
        positions = await self.get_positions()
        clean_symbol = (symbol or contract or "").strip("/").upper()
        position = next((p for p in positions if p.symbol == clean_symbol), None)
        if position is None:
            return OrderResult(success=False, error_message="Broker position is no longer open.")

        async def observe(payload: dict[str, Any]) -> None:
            logger.info("Position close: %s", payload)

        return await self.submit_position_close(
            PositionCloseRequest(
                symbol=clean_symbol,
                direction=Direction(position.direction),
                quantity=quantity if quantity is not None else position.quantity,
                client_order_id=f"close-{uuid4().hex}",
                allow_queued=exit_reason == ExitReason.EMERGENCY_EXIT,
            ),
            observe,
        )

    def _close_result(self, order: Any) -> OrderResult:
        order_id = self._field(order, "id")
        if not order_id:
            raise ValueError("Broker close response has no order ID")
        status = self._enum(order, "status")
        qty = float(self._field(order, "filled_qty", 0) or 0)
        terminal_failure = status in {
            AlpacaOrderStatus.CANCELED.value,
            AlpacaOrderStatus.EXPIRED.value,
            AlpacaOrderStatus.REJECTED.value,
        }
        close_status = CloseRequestStatus.SUBMITTED
        if status == AlpacaOrderStatus.FILLED.value:
            close_status = CloseRequestStatus.COMPLETED
        elif terminal_failure:
            close_status = CloseRequestStatus.UNKNOWN if qty else CloseRequestStatus.FAILED
        return OrderResult(
            success=not terminal_failure,
            order_id=str(order_id),
            fill_price=float(self._field(order, "filled_avg_price"))
            if self._field(order, "filled_avg_price")
            else None,
            fill_timestamp=self._filled_at(order),
            filled_quantity=qty,
            status=status,
            close_status=close_status,
            error_message="Close order ended with a partial fill; reconcile before another close."
            if terminal_failure and qty
            else (f"Broker close order {status}." if terminal_failure else None),
        )

    async def find_position_close(self, client_order_id: str) -> OrderResult | None:
        if not self.client and not await self.connect():
            raise RuntimeError("Broker connection failed")
        assert self.client is not None
        try:
            order = await asyncio.to_thread(self.client.get_order_by_client_id, client_order_id)
        except APIError as exc:
            if exc.status_code == 404:
                return None
            raise
        return self._close_result(order)

    async def _expand_close_orders(self, orders: list[Any], request: PositionCloseRequest) -> list[Any]:
        """Alpaca omits HELD bracket stops from OPEN queries, even with nested=True.

        Resolve groups by exact order/leg IDs, then explicitly verify every active leg.
        An untracked bracket can be resolved from bounded nested history; incomplete
        or ambiguous evidence refuses the close instead of guessing by symbol.
        """
        assert self.client is not None
        expanded = {str(self._field(order, "id")): order for order in orders}
        terminal = {
            AlpacaOrderStatus.FILLED.value,
            AlpacaOrderStatus.CANCELED.value,
            AlpacaOrderStatus.REJECTED.value,
            AlpacaOrderStatus.EXPIRED.value,
        }
        if request.entry_order_id:
            entry = await asyncio.to_thread(
                self.client.get_order_by_id, request.entry_order_id, GetOrderByIdRequest(nested=True)
            )
            for leg in self._field(entry, "legs", []) or []:
                if self._field(leg, "symbol") != request.symbol:
                    raise ValueError("Bracket leg symbol mismatch; no orders changed.")
                if self._enum(leg, "status") not in terminal:
                    expanded[str(self._field(leg, "id"))] = leg
        history: list[Any] | None = None
        for order in list(expanded.values()):
            order_class = self._enum(order, "order_class")
            if order_class not in {
                AlpacaOrderClass.BRACKET.value,
                AlpacaOrderClass.OCO.value,
                AlpacaOrderClass.OTO.value,
            }:
                continue
            order_id = str(self._field(order, "id"))
            parent_id = (request.entry_order_id or order_id) if order_class != AlpacaOrderClass.OCO.value else order_id
            parent = await asyncio.to_thread(self.client.get_order_by_id, parent_id, GetOrderByIdRequest(nested=True))
            legs = list(self._field(parent, "legs", []) or [])
            if not legs:
                if history is None:
                    response = await asyncio.to_thread(
                        self.client.get_orders,
                        GetOrdersRequest(
                            status=QueryOrderStatus.ALL,
                            symbols=[request.symbol],
                            nested=True,
                            limit=ALPACA_MAX_ORDERS_PER_PAGE,
                        ),
                    )
                    if not isinstance(response, list):
                        raise TypeError("Expected typed broker order history")
                    history = response
                matches = [
                    candidate
                    for candidate in history
                    if order_id in {str(self._field(leg, "id")) for leg in self._field(candidate, "legs", []) or []}
                ]
                if len(matches) != 1:
                    raise ValueError("Cannot identify the exact bracket group, including held legs; no orders changed.")
                parent = matches[0]
                legs = list(self._field(parent, "legs", []) or [])
            group_ids = {str(self._field(parent, "id")), *(str(self._field(leg, "id")) for leg in legs)}
            if order_id not in group_ids:
                raise ValueError("Working order does not belong to the verified bracket group; no orders changed.")
            for leg in legs:
                if self._field(leg, "symbol") != request.symbol:
                    raise ValueError("Bracket leg symbol mismatch; no orders changed.")
                if self._enum(leg, "status") not in terminal:
                    expanded[str(self._field(leg, "id"))] = leg
        return list(expanded.values())

    async def submit_position_close(
        self,
        request: PositionCloseRequest,
        observe: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> OrderResult:
        """Verify identity, cancel symbol orders, await terminal states, recheck, submit once.

        Market-closed preflight deliberately leaves protective orders intact. A lost
        submission response is recovered only by client_order_id, never by replay.
        """
        if not self.client and not await self.connect():
            return OrderResult(success=False, error_message="Broker connection failed")
        assert self.client is not None
        client = self.client
        submitting = False
        cancelled: list[str] = []
        terminal = {
            AlpacaOrderStatus.CANCELED.value,
            AlpacaOrderStatus.EXPIRED.value,
            AlpacaOrderStatus.REJECTED.value,
            AlpacaOrderStatus.FILLED.value,
        }

        async def check_position() -> Any:
            position = await asyncio.to_thread(client.get_open_position, request.symbol)
            direction = Direction.LONG if self._enum(position, "side") == "long" else Direction.SHORT
            if direction != request.direction or not math.isclose(
                abs(float(self._field(position, "qty"))),
                request.quantity,
                rel_tol=0,
                abs_tol=BROKER_QUANTITY_TOLERANCE,
            ):
                raise ValueError(
                    "Broker position changed; refresh positions before closing (partial fills require review)."
                )
            return position

        async def check_session(position: Any) -> None:
            if self._enum(position, "asset_class") == "us_equity":
                clock = await asyncio.to_thread(client.get_clock)
                if not self._field(clock, "is_open", False) and not request.allow_queued:
                    raise ValueError(
                        f"Regular market is closed; no close submitted. Next open: {self._field(clock, 'next_open')}."
                    )

        async def open_orders() -> list[Any]:
            orders = await asyncio.to_thread(
                client.get_orders,
                GetOrdersRequest(
                    status=QueryOrderStatus.OPEN,
                    symbols=[request.symbol],
                    nested=False,
                    limit=ALPACA_MAX_ORDERS_PER_PAGE,
                ),
            )
            if not isinstance(orders, list):
                raise TypeError("Expected typed broker order list")
            if len(orders) >= ALPACA_MAX_ORDERS_PER_PAGE:
                raise ValueError("Open-order list may be truncated; refusing incomplete cancellation.")
            return orders

        try:
            position = await check_position()
            await check_session(position)
            if request.entry_order_id:
                entry = await self._entry_order(
                    {
                        "broker_order_id": request.entry_order_id,
                        "contract": request.symbol,
                        "direction": request.direction,
                    }
                )
                if self._enum(entry, "status") != AlpacaOrderStatus.FILLED.value or not math.isclose(
                    float(self._field(entry, "filled_qty", 0) or 0),
                    request.quantity,
                    rel_tol=0,
                    abs_tol=BROKER_QUANTITY_TOLERANCE,
                ):
                    raise ValueError("Tracked entry must be fully filled and match the broker position quantity.")
                if any(
                    float(self._field(leg, "filled_qty", 0) or 0) > 0 for leg in self._field(entry, "legs", []) or []
                ):
                    raise ValueError("Tracked bracket already has exit fills; reconcile before another close.")
                if not math.isclose(
                    float(self._field(entry, "filled_avg_price")),
                    float(self._field(position, "avg_entry_price")),
                    rel_tol=0,
                    abs_tol=BROKER_PRICE_TOLERANCE,
                ):
                    raise ValueError("Broker cost basis differs from the tracked entry; reconcile before closing.")
            orders = await self._expand_close_orders(await open_orders(), request)
            if any(
                self._enum(order, "type") == AlpacaOrderType.MARKET.value
                or self._enum(order, "order_type") == AlpacaOrderType.MARKET.value
                for order in orders
            ):
                raise ValueError("A market order is already working for this symbol; wait for reconciliation.")
            await observe(
                {
                    "phase": "preflight",
                    "symbol": request.symbol,
                    "quantity": request.quantity,
                    "cancel_order_ids": [str(self._field(order, "id")) for order in orders],
                }
            )
            for order in orders:
                order_id = str(self._field(order, "id"))
                current = await asyncio.to_thread(client.get_order_by_id, order_id)
                if self._enum(current, "status") not in terminal:
                    # OCO sibling cancellation may race; terminal-state polling decides.
                    with contextlib.suppress(APIError):
                        await asyncio.to_thread(client.cancel_order_by_id, order_id)
                    cancelled.append(order_id)
            deadline = time.monotonic() + self.config.execution.close_cancel_timeout_seconds
            while True:
                pending = []
                for order in orders:
                    current = await asyncio.to_thread(client.get_order_by_id, str(self._field(order, "id")))
                    if (
                        self._enum(current, "status") == AlpacaOrderStatus.FILLED.value
                        or float(self._field(current, "filled_qty", 0) or 0) > 0
                    ):
                        raise ValueError("An order filled during cancellation; reconcile before closing.")
                    if self._enum(current, "status") not in terminal:
                        pending.append(str(self._field(current, "id")))
                if not pending:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("Order cancellations were not confirmed; no close order submitted.")
                await asyncio.sleep(self.config.execution.close_cancel_poll_seconds)
            await observe({"phase": "cancellations_confirmed", "order_ids": cancelled})
            while True:
                if await open_orders():
                    raise ValueError("New or remaining orders appeared during cancellation; no close submitted.")
                position = await check_position()
                if (
                    abs(float(self._field(position, "qty_available", 0) or 0)) + BROKER_QUANTITY_TOLERANCE
                    >= request.quantity
                ):
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("Broker quantity remains reserved; no close order submitted.")
                await asyncio.sleep(self.config.execution.close_cancel_poll_seconds)
            await check_session(position)
            side = AlpacaOrderSide.SELL if request.direction == Direction.LONG else AlpacaOrderSide.BUY
            order_request = MarketOrderRequest(
                symbol=request.symbol,
                qty=request.quantity,
                side=side,
                time_in_force=AlpacaTimeInForce.GTC
                if self._enum(position, "asset_class") == "crypto"
                else AlpacaTimeInForce.DAY,
                client_order_id=request.client_order_id,
            )
            await observe({"phase": "submitting", "client_order_id": request.client_order_id})
            submitting = True
            order = await asyncio.to_thread(client.submit_order, order_request)
            return self._close_result(order)
        except Exception as exc:
            # Even an HTTP error after submit may be an ambiguous acknowledgement.
            # The durable request stays exclusive until an exact broker lookup resolves it.
            detail = str(exc)
            if cancelled:
                detail += " Protective orders may have been cancelled; inspect this position at the broker."
            await observe(
                {
                    "phase": "failed",
                    "error_type": type(exc).__name__,
                    "detail": detail,
                    "submission_uncertain": submitting,
                }
            )
            return OrderResult(success=False, error_message=detail, submission_uncertain=submitting)

    async def get_positions(self) -> list[BrokerPosition]:
        """Fetch open positions from Alpaca via SDK."""
        if not self._connected or not self.client:
            connected = await self.connect()
            if not connected or not self.client:
                raise RuntimeError("Alpaca positions connection failed")

        try:
            positions_data = await asyncio.to_thread(self.client.get_all_positions)
            positions: list[BrokerPosition] = []
            for item in positions_data:
                qty = float(getattr(item, "qty", 0.0))
                side_str = str(getattr(item, "side", "long")).upper()
                is_equity = "equity" in str(getattr(item, "asset_class", "")).lower()
                current_price = getattr(item, "current_price", None)
                unrealized_pl = getattr(item, "unrealized_pl", None)
                sym = str(getattr(item, "symbol", ""))

                positions.append(
                    BrokerPosition(
                        symbol=sym,
                        contract=sym,
                        asset_class=AssetClass.EQUITY if is_equity else AssetClass.CRYPTO,
                        direction=Direction.LONG if "LONG" in side_str else Direction.SHORT,
                        quantity=abs(qty),
                        entry_price=float(getattr(item, "avg_entry_price", 0.0)),
                        current_price=float(current_price) if current_price is not None else None,
                        unrealized_pnl=float(unrealized_pl) if unrealized_pl is not None else None,
                    )
                )
            return positions
        except Exception as e:
            logger.error("Failed to fetch positions from Alpaca SDK: %s", e)
            raise

    async def get_account_balance(self) -> dict[str, float]:
        """Fetch account balance metrics via SDK."""
        if not self.client:
            return {}
        try:
            account = await asyncio.to_thread(self.client.get_account)
            cash_val = account.get("cash") if isinstance(account, dict) else getattr(account, "cash", 0.0)
            bp_val = account.get("buying_power") if isinstance(account, dict) else getattr(account, "buying_power", 0.0)
            pv_val = (
                account.get("portfolio_value")
                if isinstance(account, dict)
                else getattr(account, "portfolio_value", 0.0)
            )
            return {
                "cash": float(cash_val or 0.0),
                "buying_power": float(bp_val or 0.0),
                "portfolio_value": float(pv_val or 0.0),
            }
        except Exception as e:
            logger.error("Failed to fetch Alpaca account balance: %s", e)
            return {}

    @staticmethod
    def _field(order: Any, name: str, default: Any = None) -> Any:
        return order.get(name, default) if isinstance(order, dict) else getattr(order, name, default)

    @classmethod
    def _enum(cls, order: Any, name: str) -> str:
        return str(cls._field(order, name, "")).lower().split(".")[-1]

    @classmethod
    def _filled_at(cls, order: Any) -> datetime | None:
        value = cls._field(order, "filled_at")
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        return value.replace(tzinfo=UTC) if isinstance(value, datetime) and value.tzinfo is None else value

    @property
    def authoritative_positions(self) -> bool:
        return True

    async def _entry_order(self, position: dict[str, Any]) -> Any:
        if not self.client:
            await self.connect()
        order_id = position.get("broker_order_id")
        if not self.client or not order_id:
            raise ValueError("Tracked position has no broker entry order")
        order = await asyncio.to_thread(self.client.get_order_by_id, order_id, GetOrderByIdRequest(nested=True))
        symbol = str(position.get("contract") or position.get("symbol", "")).strip("/").upper()
        side = (
            AlpacaOrderSide.BUY.value
            if str(position["direction"]).upper() == Direction.LONG
            else AlpacaOrderSide.SELL.value
        )
        if (
            str(self._field(order, "id")) != str(order_id)
            or self._field(order, "symbol") != symbol
            or self._enum(order, "side") != side
        ):
            raise ValueError("Broker entry ID, symbol or side does not match tracked position")
        return order

    async def get_entry_execution(self, position: dict[str, Any]) -> OrderResult | None:
        entry = await self._entry_order(position)
        # Keep the requested size until the whole entry fills. Partial executions are
        # not evidence that the remaining order quantity has been cancelled.
        if self._enum(entry, "status") != AlpacaOrderStatus.FILLED.value:
            return None
        price = self._field(entry, "filled_avg_price")
        qty = self._field(entry, "filled_qty")
        filled_at = self._filled_at(entry)
        if price is None or qty is None or not filled_at or float(qty) <= 0:
            raise ValueError("Filled entry is missing price, quantity or timestamp")
        return OrderResult(
            success=True,
            order_id=str(self._field(entry, "id")),
            fill_price=float(price),
            filled_quantity=float(qty),
            fill_timestamp=filled_at,
            status=AlpacaOrderStatus.FILLED.value,
        )

    async def reconcile_positions(self, active_positions: list[dict[str, Any]]) -> list[ReconciliationEvent]:
        """Only exact entry bracket legs or explicitly recorded manual exits can close a signal.

        Never infer ownership from symbol, side, price, or absence of a broker position.
        Partial exits require further reconciliation and do not close an entire signal.
        """
        self.reconciliation_evidence = []
        events: list[ReconciliationEvent] = []
        for pos in active_positions:
            try:
                entry = await self._entry_order(pos)
                fields = ("id", "symbol", "side", "status", "filled_qty", "filled_avg_price", "filled_at", "order_type")
                self.reconciliation_evidence.append(
                    {
                        "signal_id": pos["id"],
                        "entry": {k: self._field(entry, k) for k in fields},
                        "legs": [
                            {k: self._field(leg, k) for k in fields} for leg in self._field(entry, "legs", []) or []
                        ],
                    }
                )
                if self._enum(entry, "status") != AlpacaOrderStatus.FILLED.value:
                    continue
                entry_time = self._filled_at(entry)
                entry_price = self._field(entry, "filled_avg_price")
                qty = float(self._field(entry, "filled_qty", 0) or 0)
                if not entry_time or entry_price is None or qty <= 0:
                    continue
                candidates = list(self._field(entry, "legs", []) or [])
                manual_id = pos.get("broker_exit_order_id")
                if manual_id and self.client:
                    candidates.append(await asyncio.to_thread(self.client.get_order_by_id, manual_id))
                side = (
                    AlpacaOrderSide.SELL.value
                    if str(pos["direction"]).upper() == Direction.LONG
                    else AlpacaOrderSide.BUY.value
                )
                for order in candidates:
                    if self._enum(order, "status") != AlpacaOrderStatus.FILLED.value:
                        continue
                    exit_time = self._filled_at(order)
                    exit_qty = float(self._field(order, "filled_qty", 0) or 0)
                    exit_price = self._field(order, "filled_avg_price")
                    if (
                        self._field(order, "symbol") != self._field(entry, "symbol")
                        or self._enum(order, "side") != side
                        or not exit_time
                        or exit_time < entry_time
                        or not math.isclose(exit_qty, qty, abs_tol=BROKER_QUANTITY_TOLERANCE)
                        or not math.isclose(qty, float(pos.get("quantity") or 0), abs_tol=BROKER_QUANTITY_TOLERANCE)
                        or exit_price is None
                    ):
                        logger.warning(
                            "Rejected exit evidence signal=%s entry_order=%s exit_order=%s",
                            pos["id"],
                            pos.get("broker_order_id"),
                            self._field(order, "id"),
                        )
                        continue
                    exit_price = float(exit_price)
                    kind = self._enum(order, "order_type") or self._enum(order, "type")
                    reason = (
                        ExitReason.MANUAL_CLOSE
                        if str(self._field(order, "id")) == str(manual_id)
                        else (ExitReason.STOP_LOSS if "stop" in kind else ExitReason.TAKE_PROFIT)
                    )
                    pnl = (exit_price - float(entry_price)) * qty * (1 if side == AlpacaOrderSide.SELL.value else -1)
                    events.append(
                        ReconciliationEvent(
                            signal_id=pos["id"],
                            symbol=self._field(entry, "symbol"),
                            contract=pos.get("contract"),
                            direction=pos["direction"],
                            exit_price=exit_price,
                            exit_reason=reason,
                            exit_timestamp=exit_time,
                            realized_pnl=pnl,
                            broker_order_id=str(self._field(order, "id")),
                            order_side=side,
                        )
                    )
                    break
            except Exception as exc:
                self.reconciliation_evidence.append({"signal_id": pos["id"], "error_type": type(exc).__name__})
                logger.exception("Cannot reconcile signal=%s entry_order=%s", pos["id"], pos.get("broker_order_id"))
        return events

    @property
    def supports_trade_stream(self) -> bool:
        """True if Alpaca API credentials are configured."""
        return bool(self.api_key and self.api_secret)

    async def start_trade_stream(self, on_fill_callback: Callable[[ReconciliationEvent], Awaitable[None]]) -> None:
        """Start listening to real-time fill events via Alpaca TradingStream WebSocket."""
        if not self.api_key or not self.api_secret:
            logger.warning("Cannot start TradingStream: missing Alpaca credentials")
            return

        stream_kwargs: dict[str, Any] = {
            "api_key": self.api_key,
            "secret_key": self.api_secret,
            "paper": self.is_paper,
        }
        if self.base_url and (self.base_url.startswith("wss://") or self.base_url.startswith("ws://")):
            stream_kwargs["url_override"] = self.base_url

        self._trade_stream = TradingStream(**stream_kwargs)

        async def _trade_update_handler(data: Any) -> None:
            try:
                event_type = getattr(data, "event", None) or (data.get("event") if isinstance(data, dict) else "")
                event_str = str(event_type).lower()
                if event_str in ("fill", "partial_fill", "tradeevent.fill", "tradeevent.partial_fill"):
                    order = getattr(data, "order", None) or (data.get("order") if isinstance(data, dict) else {})
                    symbol = str(
                        getattr(order, "symbol", "") or (order.get("symbol", "") if isinstance(order, dict) else "")
                    )
                    fill_price = float(
                        getattr(data, "price", 0.0)
                        or getattr(order, "filled_avg_price", 0.0)
                        or (order.get("filled_avg_price", 0.0) if isinstance(order, dict) else 0.0)
                        or 0.0
                    )
                    order_id = str(getattr(order, "id", "") or (order.get("id", "") if isinstance(order, dict) else ""))
                    order_side = str(
                        getattr(order, "side", "") or (order.get("side", "") if isinstance(order, dict) else "")
                    ).lower()
                    order_type = str(
                        getattr(order, "order_type", "")
                        or getattr(order, "type", "")
                        or (order.get("order_type", "") if isinstance(order, dict) else "")
                        or ""
                    ).lower()

                    if "stop" in order_type:
                        exit_reason = ExitReason.STOP_LOSS
                    elif "limit" in order_type:
                        exit_reason = ExitReason.TAKE_PROFIT
                    else:
                        exit_reason = ExitReason.MANUAL_CLOSE

                    event = ReconciliationEvent(
                        signal_id=0,
                        symbol=symbol,
                        contract=symbol,
                        direction=Direction.LONG if AlpacaOrderSide.SELL.value in order_side else Direction.SHORT,
                        exit_price=fill_price,
                        exit_reason=exit_reason,
                        exit_timestamp=datetime.now(UTC),
                        broker_order_id=order_id,
                        order_side=order_side,
                    )
                    await on_fill_callback(event)
            except Exception as ex:
                logger.error("Error processing trade update: %s", ex, extra={"error": str(ex)})

        self._trade_stream.subscribe_trade_updates(_trade_update_handler)
        logger.info("Alpaca TradingStream trade update listener registered")
        try:
            await self._trade_stream._run_forever()
        except asyncio.CancelledError:
            logger.info("Alpaca TradingStream cancelled")
            raise
        except Exception as e:
            logger.warning("Alpaca TradingStream exited: %s", e, extra={"error": str(e)})
            raise

    async def stop_trade_stream(self) -> None:
        """Stop listening to Alpaca TradingStream."""
        if self._trade_stream:
            try:
                await self._trade_stream.stop_ws()
                await self._trade_stream.close()
            except Exception as e:
                logger.debug("Error stopping Alpaca TradingStream: %s", e)
            finally:
                self._trade_stream = None

    @property
    def supports_order_modification(self) -> bool:
        return True

    async def modify_order_stop(
        self,
        order_id: str | None = None,
        symbol: str | None = None,
        new_stop_price: float = 0.0,
        client_order_id: str | None = None,
    ) -> OrderResult:
        """
        Replace resting stop order at Alpaca with updated stop price.
        Gracefully resolves stop leg order ID from parent order or open orders.
        """
        if not self._connected or not self.client:
            connected = await self.connect()
            if not connected or not self.client:
                return OrderResult(success=False, error_message="Not connected to Alpaca API.")

        clean_symbol = (symbol or "").strip("/").upper()
        target_stop_id = order_id

        # 1. Resolve stop order ID if target_stop_id is a parent bracket or missing
        try:
            if target_stop_id:
                try:
                    ord_obj = await asyncio.to_thread(self.client.get_order_by_id, target_stop_id)
                    legs = getattr(ord_obj, "legs", None)
                    if legs:
                        for leg in legs:
                            leg_type = str(getattr(leg, "order_type", getattr(leg, "type", "")))
                            if "stop" in leg_type.lower():
                                target_stop_id = str(getattr(leg, "id", ""))
                                break
                except Exception:
                    pass  # target_stop_id is likely already the stop order ID

            if not target_stop_id and clean_symbol:
                open_orders = await asyncio.to_thread(
                    self.client.get_orders,
                    GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[clean_symbol]),
                )
                for o in open_orders:
                    order_type = str(getattr(o, "order_type", getattr(o, "type", "")))
                    if "stop" in order_type.lower():
                        target_stop_id = str(getattr(o, "id", ""))
                        break
        except Exception as lookup_err:
            logger.debug("Alpaca open stop order lookup failed for %s: %s", clean_symbol, lookup_err)

        if not target_stop_id:
            return OrderResult(
                success=False,
                error_message=f"No active stop-loss order found for {clean_symbol or order_id}",
            )

        replace_req = ReplaceOrderRequest(stop_price=round(new_stop_price, 2))
        try:
            logger.info(
                "Alpaca: Replacing resting stop order %s for %s with new stop %.2f...",
                target_stop_id,
                clean_symbol,
                new_stop_price,
                extra={
                    "event": "alpaca_replace_stop",
                    "order_id": target_stop_id,
                    "symbol": clean_symbol,
                    "new_stop": new_stop_price,
                },
            )
            res = await asyncio.to_thread(self.client.replace_order_by_id, target_stop_id, replace_req)
            new_id = str(getattr(res, "id", target_stop_id))
            raw_resp = res.model_dump() if hasattr(res, "model_dump") else {"id": new_id, "new_stop": new_stop_price}
            return OrderResult(success=True, order_id=new_id, raw_response=raw_resp)
        except APIError as e:
            logger.warning("Alpaca APIError modifying stop order: %s", e)
            return OrderResult(success=False, error_message=str(e))
        except Exception as e:
            logger.exception("Exception modifying Alpaca stop order")
            return OrderResult(success=False, error_message=str(e))

    async def cancel_all_orders(self) -> int:
        """Cancel all open orders at Alpaca via client.cancel_orders()."""
        if not self.client:
            return 0
        try:
            res = await asyncio.to_thread(self.client.cancel_orders)
            cancelled_count = len(res) if isinstance(res, list) else 0
            logger.info(
                "Alpaca: Cancelled %d open orders",
                cancelled_count,
                extra={"event": "alpaca_cancel_all_orders", "count": cancelled_count, "broker": "AlpacaBroker"},
            )
            return cancelled_count
        except Exception as e:
            logger.warning("Alpaca cancel_all_orders failed: %s", e, extra={"error": str(e)})
            return 0
