import logging
from datetime import UTC, datetime

from agentic_trader.broker.base import BaseBroker, BrokerPosition, OrderRequest, OrderResult
from agentic_trader.config import AppConfig
from agentic_trader.constants import Direction
from agentic_trader.data.market_data import MarketDataFetcher


logger = logging.getLogger(__name__)


class PaperBroker(BaseBroker):
    """
    Simulated Paper Broker.
    Executes simulated orders against live market quotes for futures, equities, and crypto without risking capital.
    """

    def __init__(self, config: AppConfig, data_fetcher: MarketDataFetcher | None = None):
        self.config = config
        self.data_fetcher = data_fetcher or MarketDataFetcher()
        self._positions: dict[str, BrokerPosition] = {}
        self._connected: bool = False
        self._realized_pnl: float = 0.0

    async def connect(self) -> bool:
        self._connected = True
        logger.info(
            "PaperBroker initialized: simulated execution active.",
            extra={"broker": "PaperBroker", "status": "CONNECTED"},
        )
        return True

    async def disconnect(self) -> None:
        self._connected = False
        logger.info(
            "PaperBroker disconnected.",
            extra={"broker": "PaperBroker", "status": "DISCONNECTED"},
        )

    async def submit_entry_order(self, request: OrderRequest) -> OrderResult:
        """
        Simulate an order fill against the current live market quote.
        Supports bracket orders or simple market/limit orders across futures, equities, and crypto.
        """
        now_utc = datetime.now(UTC)
        timestamp_sec = int(now_utc.timestamp())
        symbol_clean = request.symbol.strip("/").upper()

        # Fetch live price for realistic simulated fill
        ticker = request.ticker or request.symbol
        live_price = self.data_fetcher.fetch_latest_price(ticker)
        fill_price = live_price if live_price is not None else (request.entry_price or 100.0)

        order_id = f"SIM-{timestamp_sec}-{symbol_clean}"
        bracket_orders: dict[str, str] = {}
        if request.stop_loss is not None:
            bracket_orders["stop_loss_id"] = f"SIM-SL-{timestamp_sec}-{symbol_clean}"
        if request.take_profit is not None:
            bracket_orders["take_profit_id"] = f"SIM-TP-{timestamp_sec}-{symbol_clean}"

        # Record simulated active position
        pos = BrokerPosition(
            symbol=request.symbol,
            contract=request.contract,
            asset_class=request.asset_class,
            direction=request.direction,
            quantity=request.quantity,
            entry_price=fill_price,
            current_price=fill_price,
            unrealized_pnl=0.0,
        )
        self._positions[request.symbol] = pos

        logger.info(
            "Paper order executed: %s %.2f %s @ %.2f [Order ID: %s]",
            request.direction,
            request.quantity,
            request.symbol,
            fill_price,
            order_id,
            extra={
                "order_id": order_id,
                "symbol": request.symbol,
                "asset_class": str(request.asset_class),
                "direction": str(request.direction),
                "quantity": request.quantity,
                "fill_price": fill_price,
                "stop_loss": request.stop_loss,
                "take_profit": request.take_profit,
                "broker": "PaperBroker",
            },
        )

        return OrderResult(
            success=True,
            order_id=order_id,
            fill_price=fill_price,
            fill_timestamp=now_utc,
            bracket_orders=bracket_orders,
            raw_response={
                "mode": "paper",
                "request": request.model_dump(),
                "fill_price": fill_price,
            },
        )

    async def close_position(
        self,
        symbol: str | None = None,
        exit_reason: str = "MANUAL_CLOSE",
        exit_price: float | None = None,
        quantity: float | None = None,
        contract: str | None = None,
    ) -> OrderResult:
        """
        Simulate closing an open paper position.
        """
        target_symbol = symbol or contract or ""
        now_utc = datetime.now(UTC)
        timestamp_sec = int(now_utc.timestamp())
        symbol_clean = target_symbol.strip("/").upper()

        pos = self._positions.pop(target_symbol, None) or self._positions.pop(symbol_clean, None)

        if exit_price is None:
            contract_info = self.config.contracts.get(target_symbol)
            ticker = contract_info.ticker if contract_info else target_symbol
            live_price = self.data_fetcher.fetch_latest_price(ticker)
            final_price = live_price if live_price is not None else (pos.entry_price if pos else 0.0)
        else:
            final_price = exit_price

        if pos:
            contract_info = self.config.contracts.get(target_symbol)
            multiplier = contract_info.multiplier if contract_info else 1.0
            if str(pos.direction).upper() in ("LONG", str(Direction.LONG)):
                pnl = (final_price - pos.entry_price) * multiplier * pos.quantity
            else:
                pnl = (pos.entry_price - final_price) * multiplier * pos.quantity
            self._realized_pnl += pnl

        exit_order_id = f"SIM-EXIT-{timestamp_sec}-{symbol_clean}"
        logger.info(
            "Paper position closed: %s @ %.2f (Reason: %s) [Order ID: %s]",
            target_symbol,
            final_price,
            exit_reason,
            exit_order_id,
            extra={
                "symbol": target_symbol,
                "exit_reason": exit_reason,
                "exit_price": final_price,
                "exit_order_id": exit_order_id,
                "broker": "PaperBroker",
            },
        )

        return OrderResult(
            success=True,
            order_id=exit_order_id,
            fill_price=final_price,
            fill_timestamp=now_utc,
            raw_response={
                "mode": "paper",
                "symbol": target_symbol,
                "exit_reason": exit_reason,
                "fill_price": final_price,
            },
        )

    async def get_positions(self) -> list[BrokerPosition]:
        """
        Return currently active paper positions with updated mark-to-market prices and PnL.
        """
        results: list[BrokerPosition] = []
        for symbol, pos in list(self._positions.items()):
            contract_info = self.config.contracts.get(symbol)
            # If recognized futures contract, use contract multiplier; otherwise default to 1.0 (equities/crypto)
            multiplier = contract_info.multiplier if contract_info else 1.0
            ticker = contract_info.ticker if contract_info else symbol

            current_price = self.data_fetcher.fetch_latest_price(ticker)
            if current_price is not None:
                pos.current_price = current_price
                if str(pos.direction).upper() in ("LONG", str(Direction.LONG)):
                    pos.unrealized_pnl = (current_price - pos.entry_price) * multiplier * pos.quantity
                else:
                    pos.unrealized_pnl = (pos.entry_price - current_price) * multiplier * pos.quantity
            results.append(pos)
        return results

    async def get_account_balance(self) -> dict[str, float]:
        """Simulated cash balance and buying power."""
        current_cash = self.config.portfolio.cash + self._realized_pnl
        return {
            "cash": current_cash,
            "simulated_cash": current_cash,
            "buying_power": current_cash * 2.0,
            "portfolio_value": current_cash,
            "realized_pnl": self._realized_pnl,
        }
