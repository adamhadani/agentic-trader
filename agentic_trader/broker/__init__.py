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
from agentic_trader.broker.redundant import CircuitState, RedundantBroker
from agentic_trader.broker.tradovate import TradovateBroker
from agentic_trader.config import AppConfig
from agentic_trader.constants import ExecutionMode
from agentic_trader.data.market_data import MarketDataFetcher


logger = logging.getLogger(__name__)


def _instantiate_single_broker(
    mode: str,
    config: AppConfig,
    data_fetcher: MarketDataFetcher | None = None,
) -> BaseBroker:
    mode_clean = mode.lower()
    if mode_clean == ExecutionMode.TRADOVATE:
        logger.info(
            "Instantiating TradovateBroker (%s environment)...",
            config.tradovate_environment,
            extra={"broker": "TradovateBroker", "environment": config.tradovate_environment},
        )
        return TradovateBroker(config=config)
    elif mode_clean == ExecutionMode.ALPACA:
        logger.info(
            "Instantiating AlpacaBroker (paper=%s)...",
            config.alpaca_paper,
            extra={"broker": "AlpacaBroker", "is_paper": config.alpaca_paper},
        )
        return AlpacaBroker(config=config)
    elif mode_clean == ExecutionMode.PAPER:
        logger.info(
            "Instantiating PaperBroker (simulated execution against live quotes)...",
            extra={"broker": "PaperBroker"},
        )
        return PaperBroker(config=config, data_fetcher=data_fetcher)
    else:
        logger.warning(
            "Unrecognized EXECUTION_MODE '%s'. Defaulting to PaperBroker.",
            mode_clean,
            extra={"mode": mode_clean},
        )
        return PaperBroker(config=config, data_fetcher=data_fetcher)


def create_broker(config: AppConfig, data_fetcher: MarketDataFetcher | None = None) -> BaseBroker:
    """
    Factory function to instantiate the configured multi-asset execution broker.
    Supports 'paper' (default), 'tradovate', 'alpaca', or high-availability RedundantBroker wrapper.
    """
    primary = _instantiate_single_broker(config.execution_mode, config, data_fetcher)

    if not config.redundancy.enabled:
        return primary

    fallback_mode = config.redundancy.fallback_mode
    fallback = _instantiate_single_broker(fallback_mode, config, data_fetcher)
    logger.info(
        "Instantiating RedundantBroker (Primary: %s, Fallback: %s)...",
        type(primary).__name__,
        type(fallback).__name__,
        extra={
            "primary": type(primary).__name__,
            "fallback": type(fallback).__name__,
            "max_failures": config.redundancy.max_consecutive_failures,
        },
    )
    return RedundantBroker(
        primary_broker=primary,
        fallback_broker=fallback,
        config=config.redundancy,
    )


__all__ = [
    "AlpacaBroker",
    "BaseBroker",
    "BrokerPosition",
    "CircuitState",
    "OrderRequest",
    "OrderResult",
    "PaperBroker",
    "ReconciliationEvent",
    "RedundantBroker",
    "TradovateBroker",
    "create_broker",
]
