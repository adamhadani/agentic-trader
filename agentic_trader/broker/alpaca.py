import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import (
    OrderClass as AlpacaOrderClass,
    OrderSide as AlpacaOrderSide,
    TimeInForce as AlpacaTimeInForce,
)
from alpaca.trading.requests import (
    ClosePositionRequest,
    LimitOrderRequest,
    MarketOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)

from agentic_trader.broker.base import BaseBroker, BrokerPosition, OrderRequest, OrderResult
from agentic_trader.config import AppConfig
from agentic_trader.constants import AssetClass, Direction


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
