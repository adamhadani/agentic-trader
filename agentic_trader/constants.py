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
