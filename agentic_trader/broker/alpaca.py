import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from agentic_trader.broker.base import BaseBroker, BrokerPosition, OrderRequest, OrderResult
from agentic_trader.config import AppConfig
from agentic_trader.constants import ALPACA_LIVE_URL, ALPACA_PAPER_URL, AssetClass, Direction


logger = logging.getLogger(__name__)


class AlpacaBroker(BaseBroker):
    """
    Alpaca Trading API Integration.
    Supports headless automated execution for Equities and Crypto with bracket orders.
    Works with both Alpaca Paper Trading and Live Trading accounts.
    """

    def __init__(self, config: AppConfig, client: httpx.AsyncClient | None = None):
        self.config = config
        self.is_paper = getattr(config, "alpaca_paper", True)
        self.base_url = ALPACA_PAPER_URL if self.is_paper else ALPACA_LIVE_URL
        self.api_key = getattr(config, "alpaca_api_key", None)
        self.api_secret = getattr(config, "alpaca_api_secret", None)
        self.client: httpx.AsyncClient | None = client
        self._connected: bool = False

    def _validate_credentials(self):
        missing: list[str] = []
        if not self.api_key:
            missing.append("APCA_API_KEY_ID")
        if not self.api_secret:
            missing.append("APCA_API_SECRET_KEY")
        if missing:
            raise ValueError(
                f"Missing required Alpaca credentials: {', '.join(missing)}. "
                "Please configure these variables in your .envrc file."
            )

    def _get_headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key or "",
            "APCA-API-SECRET-KEY": self.api_secret or "",
            "Content-Type": "application/json",
        }

    async def connect(self) -> bool:
        """Verify Alpaca credentials and account connectivity."""
        self._validate_credentials()
        if self.client is None:
            self.client = httpx.AsyncClient(base_url=self.base_url, headers=self._get_headers(), timeout=15.0)

        env_name = "paper" if self.is_paper else "live"
        logger.info(
            "Connecting to Alpaca (%s environment)...",
            env_name,
            extra={"broker": "AlpacaBroker", "env": env_name},
        )
        try:
            resp = await self.client.get("/v2/account")
            resp.raise_for_status()
            acct_data = resp.json()
            status = acct_data.get("status")
            if status == "ACTIVE":
                self._connected = True
                logger.info(
                    "Successfully connected to Alpaca Account %s (Buying Power: $%s)",
                    acct_data.get("account_number", ""),
                    acct_data.get("buying_power", "0"),
                    extra={"account_id": acct_data.get("id"), "status": status, "broker": "AlpacaBroker"},
                )
                return True
            else:
                logger.error("Alpaca account is not active: status=%s", status)
                return False
        except Exception as e:
            logger.error("Failed to connect to Alpaca API: %s", e)
            return False

    async def disconnect(self) -> None:
        """Close Alpaca client connection."""
        if self.client:
            await self.client.aclose()
            self.client = None
        self._connected = False
        logger.info("Alpaca broker session closed.", extra={"broker": "AlpacaBroker"})

    async def submit_entry_order(self, request: OrderRequest) -> OrderResult:
        """
        Submit a bracket or simple order to Alpaca.
        """
        if not self._connected or not self.client:
            connected = await self.connect()
            if not connected or not self.client:
                return OrderResult(
                    success=False,
                    error_message="Not connected to Alpaca API. Please verify credentials.",
                )

        side = "buy" if str(request.direction).upper() in ("LONG", str(Direction.LONG)) else "sell"
        symbol = request.symbol.strip("/").upper()

        payload: dict[str, Any] = {
            "symbol": symbol,
            "qty": request.quantity,
            "side": side,
            "type": str(request.order_type).lower(),
            "time_in_force": str(request.time_in_force).lower(),
        }

        if request.entry_price is not None and str(request.order_type).upper() == "LIMIT":
            payload["limit_price"] = round(request.entry_price, 2)

        # Handle bracket orders
        if request.is_bracket and request.take_profit and request.stop_loss:
            payload["order_class"] = "bracket"
            payload["take_profit"] = {"limit_price": round(request.take_profit, 2)}
            payload["stop_loss"] = {"stop_price": round(request.stop_loss, 2)}
        else:
            payload["order_class"] = "simple"

        try:
            logger.info(
                "Submitting Alpaca order for %s %.2f %s...",
                side,
                request.quantity,
                symbol,
                extra={
                    "symbol": symbol,
                    "side": side,
                    "quantity": request.quantity,
                    "order_class": payload.get("order_class"),
                    "broker": "AlpacaBroker",
                },
            )
            resp = await self.client.post("/v2/orders", json=payload)
            data = resp.json()

            if resp.status_code not in (200, 201):
                err = data.get("message", str(data))
                logger.error("Alpaca order submission rejected: %s", err)
                return OrderResult(success=False, error_message=err, raw_response=data)

            order_id = str(data.get("id", ""))
            fill_price = float(data.get("filled_avg_price") or request.entry_price or 0.0)
            bracket_orders: dict[str, str] = {}
            for leg in data.get("legs", []):
                leg_type = leg.get("type", "")
                if "stop" in leg_type:
                    bracket_orders["stop_loss_id"] = leg.get("id")
                elif "limit" in leg_type:
                    bracket_orders["take_profit_id"] = leg.get("id")

            return OrderResult(
                success=True,
                order_id=order_id,
                fill_price=fill_price,
                fill_timestamp=datetime.now(UTC),
                bracket_orders=bracket_orders,
                raw_response=data,
                status=data.get("status"),
            )
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
        """Close an open position at Alpaca via DELETE /v2/positions/{symbol}."""
        if not self._connected or not self.client:
            connected = await self.connect()
            if not connected or not self.client:
                return OrderResult(success=False, error_message="Not connected to Alpaca API.")

        clean_symbol = (symbol or contract or "").strip("/").upper()
        try:
            logger.info(
                "Liquidating Alpaca position for %s (Reason: %s)...",
                clean_symbol,
                exit_reason,
                extra={"symbol": clean_symbol, "exit_reason": exit_reason, "broker": "AlpacaBroker"},
            )
            resp = await self.client.delete(f"/v2/positions/{clean_symbol}")
            data = resp.json()
            if resp.status_code not in (200, 204):
                err = data.get("message", str(data))
                return OrderResult(success=False, error_message=err, raw_response=data)

            order_id = str(data.get("id", f"ALP-EXIT-{int(datetime.now(UTC).timestamp())}"))
            return OrderResult(
                success=True,
                order_id=order_id,
                fill_price=exit_price,
                fill_timestamp=datetime.now(UTC),
                raw_response=data,
            )
        except Exception as e:
            logger.exception("Failed to close position at Alpaca")
            return OrderResult(success=False, error_message=str(e))

    async def get_positions(self) -> list[BrokerPosition]:
        """Fetch open positions from Alpaca."""
        if not self._connected or not self.client:
            connected = await self.connect()
            if not connected or not self.client:
                return []

        try:
            resp = await self.client.get("/v2/positions")
            resp.raise_for_status()
            data = resp.json()
            positions: list[BrokerPosition] = []
            for item in data:
                qty = float(item.get("qty", 0.0))
                side = item.get("side", "long").upper()
                positions.append(
                    BrokerPosition(
                        symbol=item.get("symbol", ""),
                        contract=item.get("symbol", ""),
                        asset_class=AssetClass.EQUITY if item.get("asset_class") == "us_equity" else AssetClass.CRYPTO,
                        direction=Direction.LONG if side == "LONG" else Direction.SHORT,
                        quantity=abs(qty),
                        entry_price=float(item.get("avg_entry_price", 0.0)),
                        current_price=float(item.get("current_price", 0.0)) if item.get("current_price") else None,
                        unrealized_pnl=float(item.get("unrealized_pl", 0.0)) if item.get("unrealized_pl") else None,
                    )
                )
            return positions
        except Exception as e:
            logger.error("Failed to fetch positions from Alpaca: %s", e)
            return []

    async def get_account_balance(self) -> dict[str, float]:
        """Fetch account balance metrics."""
        if not self.client:
            return {}
        try:
            resp = await self.client.get("/v2/account")
            resp.raise_for_status()
            data = resp.json()
            return {
                "cash": float(data.get("cash", 0.0)),
                "buying_power": float(data.get("buying_power", 0.0)),
                "portfolio_value": float(data.get("portfolio_value", 0.0)),
            }
        except Exception as e:
            logger.error("Failed to fetch Alpaca account balance: %s", e)
            return {}
