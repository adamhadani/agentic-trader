import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import websockets
from websockets.asyncio.client import ClientConnection

from agentic_trader.broker.base import (
    BaseBroker,
    BrokerPosition,
    OrderRequest,
    OrderResult,
    ReconciliationEvent,
)
from agentic_trader.config import AppConfig
from agentic_trader.constants import (
    TRADOVATE_DEMO_URL,
    TRADOVATE_LIVE_URL,
    Direction,
    ExitReason,
)


logger = logging.getLogger(__name__)


class TradovateBroker(BaseBroker):
    """
    Tradovate Futures Broker Integration.
    Communicates via Tradovate's cloud REST API and WebSocket connection for headless
    automated execution, real-time fill streaming, and bracket order reconciliation
    of CME micro futures contracts (/MES, /MNQ, /MGC, /MCL).
    """

    def __init__(
        self,
        config: AppConfig,
        client: httpx.AsyncClient | None = None,
        ws_connect: Any | None = None,
    ):
        self.config = config
        self.env = config.tradovate_environment.lower()
        self.base_url = TRADOVATE_LIVE_URL if self.env == "live" else TRADOVATE_DEMO_URL
        ws_default = (
            "wss://live.tradovateapi.com/v1/websocket"
            if self.env == "live"
            else "wss://demo.tradovateapi.com/v1/websocket"
        )
        self.ws_url = config.tradovate_ws_url or ws_default
        self.client: httpx.AsyncClient | None = client
        self._ws_connect = ws_connect or websockets.connect
        self._access_token: str | None = None
        self._token_expiration: datetime | None = None
        self._account_id: int | None = int(config.tradovate_account_id) if config.tradovate_account_id else None
        self._ws: ClientConnection | None = None
        self._trade_stream_task: asyncio.Task[None] | None = None
        self._stream_running: bool = False

    def _validate_credentials(self) -> None:
        missing: list[str] = []
        if not self.config.tradovate_username:
            missing.append("TRADOVATE_USERNAME")
        if not self.config.tradovate_password:
            missing.append("TRADOVATE_PASSWORD")
        if not self.config.tradovate_api_key:
            missing.append("TRADOVATE_API_KEY")
        if not self.config.tradovate_api_secret:
            missing.append("TRADOVATE_API_SECRET")

        if missing:
            raise ValueError(
                f"Missing required Tradovate credentials: {', '.join(missing)}. "
                "Please configure these variables in your .envrc file."
            )

    async def connect(self) -> bool:
        """Authenticate with Tradovate API and obtain JWT Bearer token."""
        self._validate_credentials()
        if not self.client:
            self.client = httpx.AsyncClient(base_url=self.base_url, timeout=15.0)

        payload = {
            "name": self.config.tradovate_username,
            "password": self.config.tradovate_password,
            "appId": "CashPlusCopilot",
            "appVersion": "1.0",
            "cid": self.config.tradovate_api_key,
            "sec": self.config.tradovate_api_secret,
        }

        logger.info("Connecting to Tradovate (%s environment)...", self.env)
        try:
            resp = await self.client.post("/auth/accesstokenrequest", json=payload)
            resp.raise_for_status()
            data = resp.json()

            self._access_token = data.get("accessToken")
            if not self._access_token:
                error_text = data.get("errorText", "No accessToken returned")
                logger.error("Tradovate authentication failed: %s", error_text)
                return False

            logger.info("Successfully authenticated with Tradovate (%s environment).", self.env)

            # Reconcile account ID if not explicitly specified
            if not self._account_id:
                await self._fetch_default_account_id()

            return True
        except Exception as e:
            logger.error("Failed to connect to Tradovate: %s", e)
            return False

    async def _fetch_default_account_id(self) -> None:
        """Query user accounts and select the primary account ID."""
        if not self.client or not self._access_token:
            return
        headers = {"Authorization": f"Bearer {self._access_token}"}
        try:
            resp = await self.client.get("/account/list", headers=headers)
            resp.raise_for_status()
            accounts = resp.json()
            if accounts and isinstance(accounts, list):
                self._account_id = accounts[0].get("id")
                logger.info("Auto-discovered Tradovate Account ID: %s (%s)", self._account_id, accounts[0].get("name"))
        except Exception as e:
            logger.warning("Could not auto-fetch Tradovate account ID: %s", e)

    async def disconnect(self) -> None:
        """Close HTTP client session and stop trade stream."""
        await self.stop_trade_stream()
        if self.client:
            await self.client.aclose()
            self.client = None
        self._access_token = None
        logger.info("Tradovate broker session disconnected.")

    async def submit_entry_order(self, request: OrderRequest) -> OrderResult:
        """Submit entry order with bracket OCO (stop loss and take profit) to Tradovate."""
        if not self._access_token or not self.client:
            connected = await self.connect()
            if not connected or not self.client:
                return OrderResult(
                    success=False,
                    error_message="Not connected to Tradovate. Please verify credentials.",
                )

        headers = {"Authorization": f"Bearer {self._access_token}"}
        action = "Buy" if str(request.direction).upper() in ("LONG", str(Direction.LONG)) else "Sell"
        exit_action = "Sell" if action == "Buy" else "Buy"

        symbol = request.symbol.strip("/").upper()

        payload: dict[str, Any] = {
            "accountSpec": self.config.tradovate_username,
            "accountId": self._account_id,
            "action": action,
            "symbol": symbol,
            "orderQty": int(request.quantity),
            "orderType": "Limit" if str(request.order_type).upper() == "LIMIT" else "Market",
            "price": request.entry_price,
            "isAutomated": True,
            "bracket1": {
                "action": exit_action,
                "orderType": "Limit",
                "price": request.take_profit,
            },
            "bracket2": {
                "action": exit_action,
                "orderType": "Stop",
                "stopPrice": request.stop_loss,
            },
        }

        try:
            logger.info(
                "Submitting Tradovate bracket order for %s %d %s...",
                action,
                int(request.quantity),
                symbol,
                extra={"symbol": symbol, "action": action, "quantity": request.quantity, "broker": "TradovateBroker"},
            )
            resp = await self.client.post("/order/placebracketorder", json=payload, headers=headers)
            data = resp.json()

            if resp.status_code != 200 or data.get("error"):
                err_msg = data.get("errorText", str(data))
                logger.error(
                    "Tradovate order submission error: %s", err_msg, extra={"error": err_msg, "symbol": symbol}
                )
                return OrderResult(success=False, error_message=err_msg, raw_response=data)

            order_id = str(data.get("orderId", data.get("id", "")))
            logger.info(
                "Tradovate order placed successfully: Order ID %s",
                order_id,
                extra={"order_id": order_id, "symbol": symbol, "broker": "TradovateBroker"},
            )

            return OrderResult(
                success=True,
                order_id=order_id,
                fill_price=request.entry_price,
                fill_timestamp=datetime.now(UTC),
                bracket_orders={
                    "take_profit_order_id": str(data.get("bracket1OrderId", "")),
                    "stop_loss_order_id": str(data.get("bracket2OrderId", "")),
                },
                raw_response=data,
            )
        except Exception as e:
            logger.exception("Failed to submit order to Tradovate")
            return OrderResult(success=False, error_message=str(e))

    async def close_position(
        self,
        symbol: str | None = None,
        exit_reason: str = "MANUAL_CLOSE",
        exit_price: float | None = None,
        quantity: float | None = None,
        contract: str | None = None,
    ) -> OrderResult:
        """Close position for a given symbol via Tradovate liquidation endpoint."""
        target_symbol = symbol or contract or ""
        if not self._access_token or not self.client:
            connected = await self.connect()
            if not connected or not self.client:
                return OrderResult(
                    success=False,
                    error_message="Not connected to Tradovate.",
                )

        headers = {"Authorization": f"Bearer {self._access_token}"}
        clean_symbol = target_symbol.strip("/").upper()

        payload = {
            "accountId": self._account_id,
            "contract": clean_symbol,
            "admin": False,
        }

        try:
            logger.info(
                "Liquidating Tradovate position for %s (Reason: %s)...",
                clean_symbol,
                exit_reason,
                extra={"symbol": clean_symbol, "exit_reason": exit_reason, "broker": "TradovateBroker"},
            )
            resp = await self.client.post("/order/liquidateposition", json=payload, headers=headers)
            data = resp.json()

            if resp.status_code != 200 or data.get("error"):
                err_msg = data.get("errorText", str(data))
                logger.error(
                    "Tradovate liquidation error: %s", err_msg, extra={"error": err_msg, "symbol": clean_symbol}
                )
                return OrderResult(success=False, error_message=err_msg, raw_response=data)

            order_id = str(data.get("orderId", f"LIQ-{int(datetime.now(UTC).timestamp())}"))
            return OrderResult(
                success=True,
                order_id=order_id,
                fill_price=exit_price,
                fill_timestamp=datetime.now(UTC),
                raw_response=data,
            )
        except Exception as e:
            logger.exception("Failed to liquidate position on Tradovate")
            return OrderResult(success=False, error_message=str(e))

    async def get_positions(self) -> list[BrokerPosition]:
        """Fetch open positions from Tradovate."""
        if not self._access_token or not self.client:
            connected = await self.connect()
            if not connected or not self.client:
                return []

        headers = {"Authorization": f"Bearer {self._access_token}"}
        try:
            resp = await self.client.get("/position/list", headers=headers)
            resp.raise_for_status()
            data = resp.json()
            positions: list[BrokerPosition] = []
            for item in data:
                net_pos = item.get("netPos", 0)
                if net_pos == 0:
                    continue
                direction = "LONG" if net_pos > 0 else "SHORT"
                positions.append(
                    BrokerPosition(
                        contract=item.get("contract", ""),
                        direction=direction,
                        quantity=abs(net_pos),
                        entry_price=float(item.get("netPrice", 0.0)),
                        current_price=float(item.get("marketPrice", 0.0)) if "marketPrice" in item else None,
                    )
                )
            return positions
        except Exception as e:
            logger.error("Failed to fetch positions from Tradovate: %s", e)
            return []

    async def get_account_balance(self) -> dict[str, float]:
        """Fetch cash balance and margin for the active Tradovate account."""
        if not self._access_token or not self.client:
            connected = await self.connect()
            if not connected or not self.client:
                return {}

        headers = {"Authorization": f"Bearer {self._access_token}"}
        try:
            params = {"accountId": self._account_id} if self._account_id else {}
            resp = await self.client.get("/cashBalance/get", headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict):
                cash_val = data.get("cashBalance") or data.get("amount") or 0.0
                realized_val = data.get("realizedPnL") or 0.0
                net_liq_val = data.get("netLiquidity") or data.get("totalCashValue") or 0.0
                return {
                    "cash": float(cash_val),
                    "realized_pnl": float(realized_val),
                    "net_liquidity": float(net_liq_val),
                }
            return {}
        except Exception as e:
            logger.debug("Tradovate cashBalance/get query failed (%s), querying account/item...", e)
            try:
                resp = await self.client.get(f"/account/item?id={self._account_id}", headers=headers)
                resp.raise_for_status()
                data = resp.json()
                return {"cash": float(data.get("balance", 0.0))}
            except Exception:
                return {}

    async def reconcile_positions(self, active_positions: list[dict[str, Any]]) -> list[ReconciliationEvent]:
        """Reconcile active positions against Tradovate open positions and order fills."""
        if not active_positions:
            return []
        if not self._access_token or not self.client:
            connected = await self.connect()
            if not connected or not self.client:
                return []

        headers = {"Authorization": f"Bearer {self._access_token}"}
        try:
            resp = await self.client.get("/position/list", headers=headers)
            resp.raise_for_status()
            broker_positions = resp.json() or []
        except Exception as e:
            logger.error("Failed to query Tradovate positions for reconciliation: %s", e)
            return []

        open_contracts: dict[str, int] = {}
        for bp in broker_positions:
            c = str(bp.get("contract", "")).strip("/").upper()
            net_pos = bp.get("netPos", 0)
            if net_pos != 0:
                open_contracts[c] = net_pos

        orders_cache: list[dict[str, Any]] | None = None
        fills_cache: list[dict[str, Any]] | None = None
        events: list[ReconciliationEvent] = []

        for pos in active_positions:
            sig_id = pos["id"]
            contract = pos["contract"]
            clean_sym = contract.strip("/").upper()

            is_open = False
            for oc, o_net in open_contracts.items():
                if o_net != 0 and (clean_sym == oc or clean_sym in oc or oc.startswith(clean_sym)):
                    is_open = True
                    break
            if is_open:
                continue

            if orders_cache is None:
                try:
                    oresp = await self.client.get("/order/list", headers=headers)
                    oresp.raise_for_status()
                    orders_cache = oresp.json() or []
                except Exception as e:
                    logger.warning("Could not fetch Tradovate orders for reconciliation: %s", e)
                    orders_cache = []

            if fills_cache is None:
                try:
                    fresp = await self.client.get("/fill/list", headers=headers)
                    fresp.raise_for_status()
                    fills_cache = fresp.json() or []
                except Exception as e:
                    logger.warning("Could not fetch Tradovate fills for reconciliation: %s", e)
                    fills_cache = []

            pos_dir = str(pos.get("direction", Direction.LONG)).upper()
            exit_action = "Sell" if pos_dir in ("LONG", str(Direction.LONG)) else "Buy"

            matched_fill = None
            for f in fills_cache:
                f_contract = str(f.get("contract", "")).strip("/").upper()
                f_action = str(f.get("action", "")).capitalize()
                if (f_contract == clean_sym or clean_sym in f_contract) and f_action == exit_action:
                    matched_fill = f
                    break

            matched_order = None
            if matched_fill:
                f_order_id = matched_fill.get("orderId")
                for o in orders_cache:
                    if o.get("id") == f_order_id:
                        matched_order = o
                        break
            else:
                for o in orders_cache:
                    o_contract = str(o.get("symbol", o.get("contract", ""))).strip("/").upper()
                    o_action = str(o.get("action", "")).capitalize()
                    o_status = str(o.get("ordStatus", "")).capitalize()
                    if (
                        (o_contract == clean_sym or clean_sym in o_contract)
                        and o_action == exit_action
                        and o_status == "Filled"
                    ):
                        matched_order = o
                        break

            order_type = str(matched_order.get("orderType", "")).lower() if matched_order else ""
            if "stop" in order_type:
                exit_reason = ExitReason.STOP_LOSS
            elif "limit" in order_type:
                exit_reason = ExitReason.TAKE_PROFIT
            else:
                exit_reason = ExitReason.MANUAL_CLOSE

            if matched_fill and matched_fill.get("price") is not None:
                exit_price = float(matched_fill["price"])
            elif matched_order and (matched_order.get("avgPx") or matched_order.get("price")):
                exit_price = float(matched_order.get("avgPx") or matched_order.get("price", 0.0))
            else:
                exit_price = float(
                    pos.get("stop_loss", 0.0) if exit_reason == ExitReason.STOP_LOSS else pos.get("take_profit", 0.0)
                )

            order_id: str | None = None
            if matched_order and matched_order.get("id"):
                order_id = str(matched_order.get("id"))
            elif matched_fill and matched_fill.get("orderId"):
                order_id = str(matched_fill.get("orderId"))

            entry_price = float(pos.get("entry_price", 0.0))
            contract_info = self.config.contracts.get(contract)
            if contract_info:
                multiplier = contract_info.multiplier
            elif "/MES" in contract or "MES" in clean_sym:
                multiplier = 5.0
            elif "/MNQ" in contract or "MNQ" in clean_sym:
                multiplier = 2.0
            elif "/MGC" in contract or "MGC" in clean_sym:
                multiplier = 10.0
            elif "/MCL" in contract or "MCL" in clean_sym:
                multiplier = 100.0
            else:
                multiplier = 1.0

            qty = float(pos.get("quantity", 1.0))
            if pos_dir in ("LONG", str(Direction.LONG)):
                realized_pnl = (exit_price - entry_price) * multiplier * qty
            else:
                realized_pnl = (entry_price - exit_price) * multiplier * qty

            events.append(
                ReconciliationEvent(
                    signal_id=sig_id,
                    contract=contract,
                    symbol=clean_sym,
                    direction=Direction.LONG if pos_dir in ("LONG", str(Direction.LONG)) else Direction.SHORT,
                    exit_price=exit_price,
                    exit_reason=exit_reason,
                    exit_timestamp=datetime.now(UTC),
                    realized_pnl=realized_pnl,
                    broker_order_id=order_id,
                )
            )

        return events

    @property
    def supports_trade_stream(self) -> bool:
        """True if Tradovate credentials are configured."""
        return bool(
            self.config.tradovate_api_key
            and self.config.tradovate_api_secret
            and self.config.tradovate_username
            and self.config.tradovate_password
        )

    async def start_trade_stream(self, on_fill_callback: Callable[[ReconciliationEvent], Awaitable[None]]) -> None:
        """Connect to Tradovate real-time WebSocket and stream execution fill events."""
        if not self._access_token:
            connected = await self.connect()
            if not connected or not self._access_token:
                logger.warning("Cannot start Tradovate trade stream: authentication failed")
                return

        logger.info("Opening Tradovate WebSocket trade stream at %s...", self.ws_url)
        self._stream_running = True

        async with self._ws_connect(self.ws_url) as ws:
            self._ws = ws
            try:
                while self._stream_running:
                    msg = await ws.recv()
                    if not isinstance(msg, str):
                        continue

                    # SockJS protocol handling:
                    # 'o' -> connection open frame
                    if msg == "o":
                        auth_frame = f"authorize\n1\n\n{self._access_token}"
                        await ws.send(auth_frame)
                        logger.info("Tradovate WebSocket authorized")
                        continue

                    # 'h' -> heartbeat frame
                    if msg == "h":
                        await ws.send("[]")
                        continue

                    # 'a["..."]' -> message array frame
                    if msg.startswith("a["):
                        try:
                            items = json.loads(msg[1:])
                        except Exception as e:
                            logger.debug("Failed to decode Tradovate SockJS frame: %s (%s)", msg[:40], e)
                            continue

                        for item in items:
                            parsed = json.loads(item) if isinstance(item, str) else item
                            if not isinstance(parsed, dict):
                                continue

                            event_type = parsed.get("e")
                            if event_type == "props":
                                d = parsed.get("d", {})
                                entity_type = d.get("entityType")
                                entity = d.get("entity", {})

                                if entity_type == "fill":
                                    contract = str(entity.get("contract", "")).strip("/").upper()
                                    price = float(entity.get("price", 0.0))
                                    action = str(entity.get("action", "")).upper()
                                    order_id = str(entity.get("orderId", ""))
                                    event = ReconciliationEvent(
                                        signal_id=0,
                                        symbol=contract,
                                        contract=contract,
                                        direction=Direction.SHORT if action in ("BUY", "LONG") else Direction.LONG,
                                        exit_price=price,
                                        exit_reason=ExitReason.MANUAL_CLOSE,
                                        exit_timestamp=datetime.now(UTC),
                                        broker_order_id=order_id,
                                    )
                                    await on_fill_callback(event)

                                elif entity_type == "order" and str(entity.get("ordStatus", "")).lower() == "filled":
                                    contract = str(entity.get("symbol", entity.get("contract", ""))).strip("/").upper()
                                    price = float(entity.get("avgPx", entity.get("price", 0.0)))
                                    action = str(entity.get("action", "")).upper()
                                    order_type = str(entity.get("orderType", "")).lower()
                                    order_id = str(entity.get("id", ""))

                                    if "stop" in order_type:
                                        reason = ExitReason.STOP_LOSS
                                    elif "limit" in order_type:
                                        reason = ExitReason.TAKE_PROFIT
                                    else:
                                        reason = ExitReason.MANUAL_CLOSE

                                    event = ReconciliationEvent(
                                        signal_id=0,
                                        symbol=contract,
                                        contract=contract,
                                        direction=Direction.SHORT if action in ("BUY", "LONG") else Direction.LONG,
                                        exit_price=price,
                                        exit_reason=reason,
                                        exit_timestamp=datetime.now(UTC),
                                        broker_order_id=order_id,
                                    )
                                    await on_fill_callback(event)
            except asyncio.CancelledError:
                logger.info("Tradovate WebSocket stream cancelled")
                raise
            except Exception as e:
                logger.warning("Tradovate WebSocket stream disconnected: %s", e)
                raise
            finally:
                self._ws = None

    async def stop_trade_stream(self) -> None:
        """Stop Tradovate WebSocket trade stream."""
        self._stream_running = False
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
            self._ws = None
        if self._trade_stream_task and not self._trade_stream_task.done():
            self._trade_stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._trade_stream_task
            self._trade_stream_task = None
        logger.info("Tradovate trade stream stopped.")

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
        """Modify resting stop order on Tradovate exchange with graceful degradation."""
        if not self._access_token or not self.client:
            try:
                connected = await self.connect()
            except Exception as conn_err:
                return OrderResult(
                    success=False,
                    error_message=f"Not connected to Tradovate ({conn_err}). Broker stop modification degraded.",
                )
            if not connected or not self.client:
                return OrderResult(
                    success=False,
                    error_message="Not connected to Tradovate. Broker stop modification degraded.",
                )

        if not order_id:
            return OrderResult(
                success=False,
                error_message="Tradovate order modification requires order_id",
            )

        headers = {"Authorization": f"Bearer {self._access_token}"}
        payload = {
            "orderId": int(order_id) if order_id.isdigit() else order_id,
            "orderType": "Stop",
            "stopPrice": new_stop_price,
        }

        try:
            logger.info(
                "Tradovate: Modifying resting stop order %s to %.2f...",
                order_id,
                new_stop_price,
                extra={"event": "tradovate_modify_stop", "order_id": order_id, "new_stop": new_stop_price},
            )
            resp = await self.client.post("/order/modifyorder", json=payload, headers=headers)
            data = resp.json()
            if resp.status_code != 200 or data.get("error"):
                err_msg = data.get("errorText", str(data))
                return OrderResult(success=False, error_message=err_msg, raw_response=data)

            return OrderResult(success=True, order_id=str(order_id), raw_response=data)
        except Exception as e:
            logger.warning("Exception modifying Tradovate stop order (degrading): %s", e)
            return OrderResult(success=False, error_message=str(e))
