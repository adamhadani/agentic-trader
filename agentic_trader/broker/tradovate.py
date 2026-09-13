import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from agentic_trader.broker.base import BaseBroker, BrokerPosition, OrderRequest, OrderResult
from agentic_trader.config import AppConfig
from agentic_trader.constants import TRADOVATE_DEMO_URL, TRADOVATE_LIVE_URL, Direction


logger = logging.getLogger(__name__)


class TradovateBroker(BaseBroker):
    """
    Tradovate Futures Broker Integration.
    Communicates via Tradovate's cloud REST API for headless automated execution
    of CME micro futures contracts (/MES, /MNQ, /MGC, /MCL).
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self.env = config.tradovate_environment.lower()
        self.base_url = TRADOVATE_LIVE_URL if self.env == "live" else TRADOVATE_DEMO_URL
        self.client: httpx.AsyncClient | None = None
        self._access_token: str | None = None
        self._token_expiration: datetime | None = None
        self._account_id: int | None = int(config.tradovate_account_id) if config.tradovate_account_id else None

    def _validate_credentials(self):
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

    async def _fetch_default_account_id(self):
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
        """Close HTTP client session."""
        if self.client:
            await self.client.aclose()
            self.client = None
        self._access_token = None
        logger.info("Tradovate broker session disconnected.")

    async def submit_entry_order(self, request: OrderRequest) -> OrderResult:
        """
        Submit entry order with bracket OCO (stop loss and take profit) to Tradovate.
        """
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

        # Symbol resolution: strip leading '/' and map micro futures
        symbol = request.symbol.strip("/").upper()

        # Tradovate OSO / Bracket order payload
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
        """
        Close position for a given symbol via Tradovate liquidation endpoint or offsetting market order.
        """
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
