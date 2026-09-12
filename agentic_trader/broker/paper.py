import logging
from datetime import UTC, datetime

from agentic_trader.broker.base import BaseBroker, BrokerPosition, OrderRequest, OrderResult
from agentic_trader.config import AppConfig
from agentic_trader.data.market_data import MarketDataFetcher


logger = logging.getLogger(__name__)


class PaperBroker(BaseBroker):
    """
    Simulated Paper Broker.
    Executes simulated orders against live CME micro futures quotes without risking capital.
    """

    def __init__(self, config: AppConfig, data_fetcher: MarketDataFetcher | None = None):
        self.config = config
        self.data_fetcher = data_fetcher or MarketDataFetcher()
        self._positions: dict[str, BrokerPosition] = {}
        self._connected: bool = False

    async def connect(self) -> bool:
        self._connected = True
        logger.info("PaperBroker initialized: simulated execution against live quotes active.")
        return True

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("PaperBroker disconnected.")

    async def submit_entry_order(self, request: OrderRequest) -> OrderResult:
        """
        Simulate an order fill against the current live market quote.
        """
        now_utc = datetime.now(UTC)
        timestamp_sec = int(now_utc.timestamp())
        contract_clean = request.contract.strip("/").upper()

        # Fetch live price for realistic simulated fill
        live_price = self.data_fetcher.fetch_latest_price(request.ticker)
        fill_price = live_price if live_price is not None else request.entry_price

        order_id = f"SIM-{timestamp_sec}-{contract_clean}"
        sl_order_id = f"SIM-SL-{timestamp_sec}-{contract_clean}"
        tp_order_id = f"SIM-TP-{timestamp_sec}-{contract_clean}"

        # Record simulated active position
        pos = BrokerPosition(
            contract=request.contract,
            direction=request.direction,
            quantity=request.quantity,
            entry_price=fill_price,
            current_price=fill_price,
            unrealized_pnl=0.0,
        )
        self._positions[request.contract] = pos

        logger.info(
            "Paper order executed: %s %d %s @ %.2f (SL: %.2f, TP: %.2f) [Order ID: %s]",
            request.direction,
            request.quantity,
            request.contract,
            fill_price,
            request.stop_loss,
            request.take_profit,
            order_id,
        )

        return OrderResult(
            success=True,
            order_id=order_id,
            fill_price=fill_price,
            fill_timestamp=now_utc,
            bracket_orders={
                "stop_loss_id": sl_order_id,
                "take_profit_id": tp_order_id,
            },
            raw_response={
                "mode": "paper",
                "request": request.model_dump(),
                "fill_price": fill_price,
            },
        )

    async def close_position(
        self,
        contract: str,
        exit_reason: str,
        exit_price: float | None = None,
    ) -> OrderResult:
        """
        Simulate closing an open paper position.
        """
        now_utc = datetime.now(UTC)
        timestamp_sec = int(now_utc.timestamp())
        contract_clean = contract.strip("/").upper()

        pos = self._positions.pop(contract, None)

        if exit_price is None:
            contract_info = self.config.contracts.get(contract)
            ticker = contract_info.ticker if contract_info else f"{contract_clean}=F"
            live_price = self.data_fetcher.fetch_latest_price(ticker)
            final_price = live_price if live_price is not None else (pos.entry_price if pos else 0.0)
        else:
            final_price = exit_price

        exit_order_id = f"SIM-EXIT-{timestamp_sec}-{contract_clean}"
        logger.info(
            "Paper position closed: %s @ %.2f (Reason: %s) [Order ID: %s]",
            contract,
            final_price,
            exit_reason,
            exit_order_id,
        )

        return OrderResult(
            success=True,
            order_id=exit_order_id,
            fill_price=final_price,
            fill_timestamp=now_utc,
            raw_response={
                "mode": "paper",
                "contract": contract,
                "exit_reason": exit_reason,
                "fill_price": final_price,
            },
        )

    async def get_positions(self) -> list[BrokerPosition]:
        """
        Return currently active paper positions with updated mark-to-market prices.
        """
        results: list[BrokerPosition] = []
        for contract, pos in list(self._positions.items()):
            contract_info = self.config.contracts.get(contract)
            multiplier = contract_info.multiplier if contract_info else 5.0
            ticker = contract_info.ticker if contract_info else f"{contract.strip('/').upper()}=F"

            current_price = self.data_fetcher.fetch_latest_price(ticker)
            if current_price is not None:
                pos.current_price = current_price
                if pos.direction.upper() == "LONG":
                    pos.unrealized_pnl = (current_price - pos.entry_price) * multiplier * pos.quantity
                else:
                    pos.unrealized_pnl = (pos.entry_price - current_price) * multiplier * pos.quantity
            results.append(pos)
        return results
