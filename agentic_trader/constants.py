from enum import StrEnum


class Direction(StrEnum):
    """Trade direction."""

    LONG = "LONG"
    SHORT = "SHORT"


class OrderSide(StrEnum):
    """Order transaction side."""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    """Order execution type."""

    LIMIT = "LIMIT"
    MARKET = "MARKET"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"
    TRAILING_STOP = "TRAILING_STOP"


class OrderClass(StrEnum):
    """Order grouping and bracket structure."""

    SIMPLE = "SIMPLE"
    BRACKET = "BRACKET"
    OCO = "OCO"
    OTO = "OTO"


class TimeInForce(StrEnum):
    """Order validity duration."""

    GTC = "GTC"  # Good 'til Canceled
    DAY = "DAY"  # Day order
    IOC = "IOC"  # Immediate or Cancel
    FOK = "FOK"  # Fill or Kill


class SignalStatus(StrEnum):
    """Lifecycle state of a signal / trade in the database."""

    PENDING = "PENDING"
    SUBMITTING = "SUBMITTING"
    EXECUTED = "EXECUTED"
    DISMISSED = "DISMISSED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    CLOSED_WIN = "CLOSED_WIN"
    CLOSED_LOSS = "CLOSED_LOSS"
    CLOSED_MANUAL = "CLOSED_MANUAL"


class ExitReason(StrEnum):
    """Reason for closing an active trade."""

    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_LOSS = "STOP_LOSS"
    MANUAL_CLOSE = "MANUAL_CLOSE"
    TIME_EXPIRY = "TIME_EXPIRY"
    EMERGENCY_EXIT = "EMERGENCY_EXIT"


class AssetClass(StrEnum):
    """Tradable asset classes."""

    FUTURES = "FUTURES"
    EQUITY = "EQUITY"
    CRYPTO = "CRYPTO"
    FX = "FX"
    OPTION = "OPTION"


class ExecutionMode(StrEnum):
    """Broker execution mode."""

    PAPER = "paper"
    TRADOVATE = "tradovate"
    ALPACA = "alpaca"
    MANUAL = "manual"


class StrategyType(StrEnum):
    """Quantitative screening strategy names."""

    TREND_PULLBACK = "TREND_PULLBACK"
    SQUEEZE_BREAKOUT = "SQUEEZE_BREAKOUT"


# External Provider & Broker Endpoints
FOREX_FACTORY_CALENDAR_URL = "https://www.forexfactory.com/calendar"
FOREX_FACTORY_JSON_FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FINNHUB_ECONOMIC_CALENDAR_URL = "https://finnhub.io/api/v1/calendar/economic"

TRADOVATE_DEMO_URL = "https://demo.tradovateapi.com/v1"
TRADOVATE_LIVE_URL = "https://live.tradovateapi.com/v1"

ALPACA_PAPER_URL = "https://paper-api.alpaca.markets"
ALPACA_LIVE_URL = "https://api.alpaca.markets"

# Observability & Monitoring
CALLBACK_LANGSMITH = "langsmith"
DEFAULT_CURRENCY = "USD"

# Default Risk Invariant Thresholds
DEFAULT_PORTFOLIO_CASH = 100000.0
DEFAULT_MAX_NOTIONAL_EXPOSURE = 60000.0
DEFAULT_MAX_CONCURRENT_CONTRACTS = 2
DEFAULT_MIN_RISK_REWARD_RATIO = 2.0
DEFAULT_MIN_STOP_ATR_MULTIPLE = 1.5
DEFAULT_DEDUPLICATION_HOURS = 12
DEFAULT_LOCKOUT_PRE_EVENT_MINUTES = 60
DEFAULT_LOCKOUT_POST_EVENT_MINUTES = 30
