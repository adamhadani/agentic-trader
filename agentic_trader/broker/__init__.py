import logging

from agentic_trader.broker.base import BaseBroker, BrokerPosition, OrderRequest, OrderResult
from agentic_trader.broker.paper import PaperBroker
from agentic_trader.broker.tradovate import TradovateBroker
from agentic_trader.config import AppConfig
from agentic_trader.data.market_data import MarketDataFetcher


logger = logging.getLogger(__name__)


def create_broker(config: AppConfig, data_fetcher: MarketDataFetcher | None = None) -> BaseBroker:
    """
    Factory function to instantiate the configured futures execution broker.
    Supports 'paper' (default), 'tradovate', or fallback.
    """
    mode = config.execution_mode.lower()
    if mode == "tradovate":
        logger.info("Instantiating TradovateBroker (%s environment)...", config.tradovate_environment)
        return TradovateBroker(config=config)
    elif mode == "paper":
        logger.info("Instantiating PaperBroker (simulated execution against live quotes)...")
        return PaperBroker(config=config, data_fetcher=data_fetcher)
    else:
        logger.warning("Unrecognized EXECUTION_MODE '%s'. Defaulting to PaperBroker.", mode)
        return PaperBroker(config=config, data_fetcher=data_fetcher)


__all__ = [
    "BaseBroker",
    "BrokerPosition",
    "OrderRequest",
    "OrderResult",
    "PaperBroker",
    "TradovateBroker",
    "create_broker",
]
