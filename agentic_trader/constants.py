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


class StopAdjustmentReason(StrEnum):
    BREAKEVEN = "BREAKEVEN"
    TRAILING_STOP = "TRAILING_STOP"


class ExitReason(StrEnum):
    """Reason for closing an active trade."""

    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_LOSS = "STOP_LOSS"
    TRAILING_STOP = "TRAILING_STOP"
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
    PAIRS_REVERSION = "PAIRS_REVERSION"
    OPTIONS_VERTICAL = "OPTIONS_VERTICAL"


class StrategyMode(StrEnum):
    """Strategy execution orchestration mode."""

    SINGLE = "single"
    PARALLEL = "parallel"


class ConflictResolutionMode(StrEnum):
    """Signal conflict resolution strategy."""

    NETTING = "netting"
    HIGHEST_CONVICTION = "highest_conviction"
    FIRST = "first"


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


class VolatilityRegime(StrEnum):
    """Macro volatility market regimes based on VIX levels."""

    COMPRESSED = "COMPRESSED"  # VIX < 15.0: Low volatility compression, favorable trend runs
    NORMAL = "NORMAL"  # 15.0 <= VIX <= 22.0: Standard orderly market conditions
    ELEVATED = "ELEVATED"  # 22.0 < VIX <= 30.0: Choppy, wider ATRs, heightened risk
    EXTREME = "EXTREME"  # VIX > 30.0: Panic/turbulence, suppress breakout strategies


# Macro Indicator Tickers
VIX_TICKER = "^VIX"
TNX_TICKER = "^TNX"
DXY_TICKER = "DX-Y.NYB"
IRX_TICKER = "^IRX"
FRED_TWO_YEAR_SERIES = "DGS2"
FVX_TICKER = "^FVX"
TYX_TICKER = "^TYX"

# FRED Series Identifiers
FRED_HY_OAS_SERIES = "BAMLH0A0HYM2"
FRED_T10YIE_SERIES = "T10YIE"
FRED_T5YIE_SERIES = "T5YIE"
FRED_CSV_BASE_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

# Default Volatility Thresholds
DEFAULT_VIX_COMPRESSED_THRESHOLD = 15.0
DEFAULT_VIX_ELEVATED_THRESHOLD = 22.0
DEFAULT_VIX_EXTREME_THRESHOLD = 30.0

# Financial Math & Annualization Benchmarks
DEFAULT_RISK_FREE_RATE = 0.045  # 4.5% annual cash yield benchmark
TRADING_DAYS_PER_YEAR = 252
CALENDAR_DAYS_PER_YEAR = 365.0
INFINITE_RATIO_SENTINEL = 999.99
FLOAT_EPSILON = 1e-5

# Backtest & Simulation Defaults
DEFAULT_BACKTEST_LOOKBACK = "2y"
DEFAULT_MIN_WARMUP_BARS = 20

# Monte Carlo Bootstrap Resampling Defaults
DEFAULT_MONTE_CARLO_SIMULATIONS = 1000
DEFAULT_RANDOM_SEED = 42
MIN_TRADES_FOR_MONTE_CARLO = 3
DEFAULT_RUIN_THRESHOLD_LOW_PCT = 10.0
DEFAULT_RUIN_THRESHOLD_HIGH_PCT = 20.0
DEFAULT_VAR_CONFIDENCE_PCT = 95.0

# Macro Stress Testing & Historical Crisis Replay
STRESS_PASS_MAX_DRAWDOWN_PCT = 15.0
STRESS_WARNING_MAX_DRAWDOWN_PCT = 25.0
STRESS_PASS_MIN_NET_PNL = -500.0
MICRO_FUTURES_LAUNCH_YEAR = 2019
MICRO_CRUDE_LAUNCH_YEAR = 2022

PROXY_MAPPINGS: dict[str, str] = {
    "/MES": "SPY",
    "/MNQ": "QQQ",
    "/MGC": "GLD",
    "/MCL": "USO",
    "/M2K": "IWM",
}

DEFAULT_CRISIS_SHOCKS: dict[str, float] = {
    "SPY": -0.10,
    "QQQ": -0.12,
    "/MES": -0.10,
    "/MNQ": -0.12,
    "GLD": 0.03,
    "/MGC": 0.03,
    "/MCL": -0.15,
    "TLT": 0.02,
    "BTC/USD": -0.20,
    "ETH/USD": -0.25,
}

# Sector & Industry Cluster Taxonomy
SECTOR_MAP: dict[str, str] = {
    "/MES": "US Broad Market",
    "/ES": "US Broad Market",
    "SPY": "US Broad Market",
    "VOO": "US Broad Market",
    "IVV": "US Broad Market",
    "/MNQ": "US Tech",
    "/NQ": "US Tech",
    "QQQ": "US Tech",
    "XLK": "US Tech",
    "AAPL": "US Tech",
    "MSFT": "US Tech",
    "NVDA": "US Tech",
    "/M2K": "US Small Cap",
    "/RTY": "US Small Cap",
    "IWM": "US Small Cap",
    "/MGC": "Precious Metals",
    "/GC": "Precious Metals",
    "GLD": "Precious Metals",
    "IAU": "Precious Metals",
    "/MCL": "Energy",
    "/CL": "Energy",
    "USO": "Energy",
    "XLE": "Energy",
    "/ZN": "US Treasuries",
    "/ZB": "US Treasuries",
    "TLT": "US Treasuries",
    "IEF": "US Treasuries",
}

# Research, Walk-Forward & Optimization Defaults
DEFAULT_OPTIMIZATION_LOOKBACK = "2y"
DEFAULT_WALK_FORWARD_SPLITS = 3
DEFAULT_TRAIN_RATIO = 0.70
DEFAULT_MIN_WFE = 0.50
DEFAULT_MIN_OOS_SHARPE = 0.80
DEFAULT_CALIBRATIONS_FILENAME = "calibrated_parameters.json"

DEFAULT_TREND_PULLBACK_RSI_GRID: list[float] = [35.0, 38.0, 40.0, 42.0, 45.0, 48.0, 50.0]
DEFAULT_TREND_PULLBACK_EMA_GRID: list[int] = [15, 20, 25]
DEFAULT_SQUEEZE_VOLUME_GRID: list[float] = [1.1, 1.2, 1.3, 1.4, 1.5]
DEFAULT_SQUEEZE_BARS_GRID: list[int] = [3, 4, 5, 6, 8]

# Options & Gamma Exposure (GEX) Constants
DEFAULT_IMPLIED_VOLATILITY = 0.20
OPTIONS_CONTRACT_MULTIPLIER = 100.0
GEX_MILLION_CONVERSION = 1_000_000.0
DEFAULT_GEX_REGIME_THRESHOLD = 5.0
DEFAULT_TIME_TO_EXPIRY_DAYS = 14.0

# Pairs Trading & Statistical Arbitrage Constants
DEFAULT_PAIRS_LOOKBACK = 30
DEFAULT_PAIRS_P_VALUE_THRESHOLD = 0.05
DEFAULT_SPREAD_Z_ENTRY = 2.0
DEFAULT_SPREAD_Z_EXIT = 0.5
MIN_COINTEGRATION_BARS = 20
MIN_HALF_LIFE_BARS = 10

# Market Data & Resilience Defaults
DEFAULT_PRIMARY_EQUITIES_PROVIDER = "alpaca"
DEFAULT_FALLBACK_PROVIDERS: tuple[str, ...] = ("yfinance",)
DEFAULT_DATA_TIMEOUT_SECONDS = 10.0
DEFAULT_DATA_MAX_RETRIES = 2
DEFAULT_DATA_RETRY_BACKOFF_FACTOR = 0.5
DEFAULT_DAILY_LOOKBACK_PERIOD = "1y"
DEFAULT_INTRADAY_LOOKBACK_PERIOD = "60d"

# Trailing Stop & Ratchet Defaults
DEFAULT_TRAILING_STOP_MODE = "chandelier_atr"  # Quant high-water mark trailing stop
DEFAULT_TRAIL_TRIGGER_R = 1.5
DEFAULT_TRAIL_ATR_MULTIPLE = 1.5
DEFAULT_TRAIL_STEP_TICKS = 4
DEFAULT_BREAKEVEN_BUFFER_DOLLARS = 10.0

# Market Calendar Delegation & Resilience Defaults
DEFAULT_SESSION_CALENDAR_PROVIDER = "alpaca"
DEFAULT_SESSION_FALLBACK_PROVIDERS: tuple[str, ...] = ("finnhub", "deterministic")
DEFAULT_SESSION_CACHE_TTL_SECONDS = 3600

# Multi-Strategy Orchestration Defaults
DEFAULT_STRATEGY_MODE = "parallel"
DEFAULT_ACTIVE_STRATEGY = "trend_pullback"
DEFAULT_ACTIVE_STRATEGIES: tuple[str, ...] = ("trend_pullback", "squeeze_breakout")
DEFAULT_CONFLICT_RESOLUTION = "netting"
DEFAULT_STRATEGY_ALLOCATIONS: dict[str, float] = {
    "trend_pullback": 0.60,
    "squeeze_breakout": 0.40,
}

# Database Persistence & Pooling Defaults
DEFAULT_POSTGRES_DB_URL = "postgresql+asyncpg://adamhadani@localhost:5432/agentic_trader"
DEFAULT_DB_POOL_SIZE = 10
DEFAULT_DB_MAX_OVERFLOW = 20
DEFAULT_DB_POOL_TIMEOUT = 30.0
DEFAULT_DB_POOL_RECYCLE = 1800
DEFAULT_DB_ECHO = False
DEFAULT_DB_MAX_RETRIES = 5
DEFAULT_DB_RETRY_DELAY = 2.0
DEFAULT_DUPLICATE_SIGNAL_WINDOW_HOURS = 12
DEFAULT_RECENT_SIGNALS_LIMIT = 20


class RuntimeEnvironment(StrEnum):
    PRODUCTION = "production"
    DEVELOPMENT = "development"
    TEST = "test"


class SystemStateKey(StrEnum):
    TRADING_HALTED = "trading_halted"
    TRADING_HALT_REASON = "trading_halt_reason"


class AuditEventType(StrEnum):
    CLOSE_REQUEST = "close_request"
    CLOSE_BROKER_STEP = "close_broker_step"
    FLATTEN = "flatten"
    SIGNAL_CREATED = "signal_created"
    SIGNAL_QUARANTINED = "signal_quarantined"
    ENTRY_EXECUTION_UPDATED = "entry_execution_updated"
    ENTRY_SYNC_FAILED = "entry_sync_failed"
    POSITION_CLOSED = "position_closed"
    EXIT_NOTIFICATION = "exit_notification"
    EXIT_ORDER_SUBMITTED = "exit_order_submitted"
    EXIT_SUBMISSION_FAILED = "exit_submission_failed"
    BROKER_STREAM_UPDATE = "broker_stream_update"
    RECONCILIATION = "reconciliation"
    POSITIONS_VALUATION = "positions_valuation"
    VALUATION_FAILED = "valuation_failed"
    PERFORMANCE_REPORT = "performance_report"
    MACRO_REPORT = "macro_report"
    STOP_REPLACEMENT = "stop_replacement"
    STOP_UPDATED = "stop_updated"
    ENTRY_SUBMISSION = "entry_submission"
    ENTRY_SUBMISSION_UNKNOWN = "entry_submission_unknown"
    EXECUTION_CLAIMED = "execution_claimed"
    RUNTIME_STARTED = "runtime_started"
    HISTORICAL_TRADE_RESTORED = "historical_trade_restored"
    INCIDENT_REPAIR = "incident_repair"
    TELEGRAM_POLL = "telegram_poll"
    TELEGRAM_COMMAND = "telegram_command"
    TELEGRAM_ERROR = "telegram_error"
    TELEGRAM_REQUEST = "telegram_request"
    EVENT_LOOP_STALL = "event_loop_stall"


UNKNOWN_EXECUTION_MODE = "unknown"


class CloseRequestStatus(StrEnum):
    CLAIMED = "claimed"
    SUBMITTED = "submitted"
    UNKNOWN = "unknown"
    COMPLETED = "completed"
    FAILED = "failed"


ACTIVE_CLOSE_STATUSES = (CloseRequestStatus.CLAIMED, CloseRequestStatus.SUBMITTED, CloseRequestStatus.UNKNOWN)
ALPACA_MAX_ORDERS_PER_PAGE = 500
ALPACA_MAX_REPLACEMENT_CHAIN = 100
BROKER_QUANTITY_TOLERANCE = 1e-6
BROKER_PRICE_TOLERANCE = 1e-8
DEFAULT_STREAM_RECONNECT_INITIAL_SECONDS = 2.0
DEFAULT_STREAM_RECONNECT_MAX_SECONDS = 60.0
STREAM_RECONNECT_MULTIPLIER = 2.0
DEFAULT_MARKET_DATA_CACHE_TTL_SECONDS = 300
DEFAULT_AUDIT_LIMIT = 100
MAX_AUDIT_LIMIT = 1000


def normalize_asset_class(value: str) -> str:
    """Normalize public CLI plurals without corrupting 'equities'."""
    aliases = {
        "equities": AssetClass.EQUITY.value.lower(),
        "futures": AssetClass.FUTURES.value.lower(),
        "stocks": AssetClass.EQUITY.value.lower(),
    }
    return aliases.get(value.lower(), value.lower())


APP_DISPLAY_NAME = "Agentic Trader"
DEFAULT_RESEARCH_SYMBOL = "SPY"
TELEGRAM_MESSAGE_CHUNK_LENGTH = 4000
