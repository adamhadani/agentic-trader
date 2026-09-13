import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import (
    OrderClass as AlpacaOrderClass,
    OrderSide as AlpacaOrderSide,
    QueryOrderStatus,
    TimeInForce as AlpacaTimeInForce,
)
from alpaca.trading.requests import (
    ClosePositionRequest,
    GetOrdersRequest,
    LimitOrderRequest,
    MarketOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)
from alpaca.trading.stream import TradingStream

from agentic_trader.broker.base import (
    BaseBroker,
    BrokerPosition,
    OrderRequest,
    OrderResult,
    ReconciliationEvent,
)
from agentic_trader.config import AppConfig
from agentic_trader.constants import AssetClass, Direction, ExitReason


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
            fill_price = float(getattr(order, "filled_avg_price", None) or request.entry_price or 0.0)
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
                fill_timestamp=datetime.now(UTC),
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
        """Close an open position at Alpaca via client.close_position."""
        if not self._connected or not self.client:
            connected = await self.connect()
            if not connected or not self.client:
                return OrderResult(success=False, error_message="Not connected to Alpaca API.")

        clean_symbol = (symbol or contract or "").strip("/").upper()
        try:
            logger.info(
                "Liquidating Alpaca position for %s (Reason: %s) via SDK...",
                clean_symbol,
                exit_reason,
                extra={"symbol": clean_symbol, "exit_reason": exit_reason, "broker": "AlpacaBroker"},
            )
            close_opts = ClosePositionRequest(qty=str(quantity)) if quantity else None
            res = await asyncio.to_thread(self.client.close_position, clean_symbol, close_opts)
            order_id = str(getattr(res, "id", f"ALP-EXIT-{int(datetime.now(UTC).timestamp())}"))
            raw_resp: dict[str, Any] = res.model_dump() if hasattr(res, "model_dump") else {"id": order_id}
            return OrderResult(
                success=True,
                order_id=order_id,
                fill_price=exit_price,
                fill_timestamp=datetime.now(UTC),
                raw_response=raw_resp,
            )
        except APIError as e:
            logger.error("Alpaca API error closing position for %s: %s", clean_symbol, e)
            return OrderResult(success=False, error_message=str(e))
        except Exception as e:
            logger.exception("Failed to close position at Alpaca")
            return OrderResult(success=False, error_message=str(e))

    async def get_positions(self) -> list[BrokerPosition]:
        """Fetch open positions from Alpaca via SDK."""
        if not self._connected or not self.client:
            connected = await self.connect()
            if not connected or not self.client:
                return []

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
            return []

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

    async def reconcile_positions(self, active_positions: list[dict[str, Any]]) -> list[ReconciliationEvent]:
        """Reconcile active SQLite positions against Alpaca open positions and closed orders.

        Detects when bracket stop-loss or take-profit legs have filled at Alpaca.
        """
        if not self.client:
            await self.connect()
        if not self.client:
            return []

        reconciliation_events: list[ReconciliationEvent] = []
        try:
            open_positions = await asyncio.to_thread(self.client.get_all_positions)
            open_symbols = {
                str(getattr(p, "symbol", "") or (p.get("symbol", "") if isinstance(p, dict) else "")).upper()
                for p in open_positions
            }

            for pos in active_positions:
                signal_id = pos["id"]
                raw_symbol = pos.get("contract") or pos.get("symbol", "")
                symbol = raw_symbol.strip("/").upper()
                direction = str(pos["direction"]).upper()
                entry_price = float(pos["entry_price"])
                take_profit = float(pos["take_profit"])

                # If symbol is still open at Alpaca, it has not closed yet
                if symbol in open_symbols:
                    continue

                # Position is closed at Alpaca; query closed orders to extract fill details
                closed_orders_req = GetOrdersRequest(
                    status=QueryOrderStatus.CLOSED,
                    limit=20,
                    symbols=[symbol],
                )
                closed_orders = await asyncio.to_thread(self.client.get_orders, closed_orders_req)

                exit_price = entry_price
                exit_reason = ExitReason.MANUAL_CLOSE
                exit_time = datetime.now(UTC)
                exit_order_id: str | None = None

                filled_exit_order = None
                for order in closed_orders:
                    ord_status = str(getattr(order, "status", "")).lower()
                    if "filled" in ord_status:
                        filled_exit_order = order
                        break

                if filled_exit_order:
                    exit_order_id = str(getattr(filled_exit_order, "id", None))
                    avg_fill = getattr(filled_exit_order, "filled_avg_price", None)
                    if avg_fill is not None:
                        exit_price = float(avg_fill)
                    order_type_str = str(getattr(filled_exit_order, "order_type", "")).lower()

                    if "stop" in order_type_str:
                        exit_reason = ExitReason.STOP_LOSS
                    elif "limit" in order_type_str:
                        exit_reason = ExitReason.TAKE_PROFIT
                    elif direction in ("LONG", str(Direction.LONG)):
                        exit_reason = ExitReason.TAKE_PROFIT if exit_price >= take_profit else ExitReason.STOP_LOSS
                    else:
                        exit_reason = ExitReason.TAKE_PROFIT if exit_price <= take_profit else ExitReason.STOP_LOSS

                    filled_at = getattr(filled_exit_order, "filled_at", None)
                    if isinstance(filled_at, datetime):
                        exit_time = filled_at

                pos_qty = float(pos.get("quantity") or 1.0)
                if direction in ("LONG", str(Direction.LONG)):
                    realized_pnl = (exit_price - entry_price) * pos_qty
                else:
                    realized_pnl = (entry_price - exit_price) * pos_qty

                event = ReconciliationEvent(
                    signal_id=signal_id,
                    symbol=symbol,
                    contract=raw_symbol,
                    direction=direction,
                    exit_price=exit_price,
                    exit_reason=exit_reason,
                    exit_timestamp=exit_time,
                    realized_pnl=realized_pnl,
                    broker_order_id=exit_order_id,
                )
                reconciliation_events.append(event)
        except Exception as e:
            logger.error(
                "Error reconciling Alpaca positions: %s",
                e,
                extra={"broker": "AlpacaBroker", "error": str(e)},
            )

        return reconciliation_events

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
                        direction=Direction.LONG if "sell" in order_side else Direction.SHORT,
                        exit_price=fill_price,
                        exit_reason=exit_reason,
                        exit_timestamp=datetime.now(UTC),
                        broker_order_id=order_id,
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
