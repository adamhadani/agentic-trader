import logging

from agentic_trader.broker.alpaca import AlpacaBroker
from agentic_trader.broker.base import (
    BaseBroker,
    BrokerPosition,
    OrderRequest,
    OrderResult,
    ReconciliationEvent,
)
from agentic_trader.broker.paper import PaperBroker
from agentic_trader.broker.tradovate import TradovateBroker
from agentic_trader.config import AppConfig
from agentic_trader.constants import ExecutionMode
from agentic_trader.data.market_data import MarketDataFetcher


logger = logging.getLogger(__name__)


def create_broker(config: AppConfig, data_fetcher: MarketDataFetcher | None = None) -> BaseBroker:
    """
    Factory function to instantiate the configured multi-asset execution broker.
    Supports 'paper' (default), 'tradovate', 'alpaca', or fallback.
    """
    mode = config.execution_mode.lower()
    if mode == ExecutionMode.TRADOVATE:
        logger.info(
            "Instantiating TradovateBroker (%s environment)...",
            config.tradovate_environment,
            extra={"broker": "TradovateBroker", "environment": config.tradovate_environment},
        )
        return TradovateBroker(config=config)
    elif mode == ExecutionMode.ALPACA:
        logger.info(
            "Instantiating AlpacaBroker (paper=%s)...",
            config.alpaca_paper,
            extra={"broker": "AlpacaBroker", "is_paper": config.alpaca_paper},
        )
        return AlpacaBroker(config=config)
    elif mode == ExecutionMode.PAPER:
        logger.info(
            "Instantiating PaperBroker (simulated execution against live quotes)...",
            extra={"broker": "PaperBroker"},
        )
        return PaperBroker(config=config, data_fetcher=data_fetcher)
    else:
        logger.warning(
            "Unrecognized EXECUTION_MODE '%s'. Defaulting to PaperBroker.",
            mode,
            extra={"mode": mode},
        )
        return PaperBroker(config=config, data_fetcher=data_fetcher)


__all__ = [
    "AlpacaBroker",
    "BaseBroker",
    "BrokerPosition",
    "OrderRequest",
    "OrderResult",
    "PaperBroker",
    "ReconciliationEvent",
    "TradovateBroker",
    "create_broker",
]
