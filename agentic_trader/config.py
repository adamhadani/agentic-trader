import io
import os
import re
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, Field, field_validator, model_validator

from agentic_trader.constants import (
    CRYPTO_SYMBOL_PREFIXES,
    DEFAULT_ACTIVE_STRATEGIES,
    DEFAULT_ACTIVE_STRATEGY,
    DEFAULT_BACKTEST_LOOKBACK,
    DEFAULT_BREAKEVEN_BUFFER_DOLLARS,
    DEFAULT_CONFLICT_RESOLUTION,
    DEFAULT_DATA_MAX_RETRIES,
    DEFAULT_DATA_RETRY_BACKOFF_FACTOR,
    DEFAULT_DATA_TIMEOUT_SECONDS,
    DEFAULT_DB_ECHO,
    DEFAULT_DB_MAX_OVERFLOW,
    DEFAULT_DB_MAX_RETRIES,
    DEFAULT_DB_POOL_RECYCLE,
    DEFAULT_DB_POOL_SIZE,
    DEFAULT_DB_POOL_TIMEOUT,
    DEFAULT_DB_RETRY_DELAY,
    DEFAULT_FALLBACK_PROVIDERS,
    DEFAULT_MIN_WARMUP_BARS,
    DEFAULT_MONTE_CARLO_SIMULATIONS,
    DEFAULT_PORTFOLIO_CASH,
    DEFAULT_POSTGRES_DB_URL,
    DEFAULT_PRIMARY_EQUITIES_PROVIDER,
    DEFAULT_RANDOM_SEED,
    DEFAULT_RISK_FREE_RATE,
    DEFAULT_SESSION_CACHE_TTL_SECONDS,
    DEFAULT_SESSION_CALENDAR_PROVIDER,
    DEFAULT_SESSION_FALLBACK_PROVIDERS,
    DEFAULT_STRATEGY_ALLOCATIONS,
    DEFAULT_STRATEGY_MODE,
    DEFAULT_STREAM_RECONNECT_INITIAL_SECONDS,
    DEFAULT_STREAM_RECONNECT_MAX_SECONDS,
    DEFAULT_TRAIL_ATR_MULTIPLE,
    DEFAULT_TRAIL_STEP_TICKS,
    DEFAULT_TRAIL_TRIGGER_R,
    DEFAULT_TRAILING_STOP_MODE,
    DEFAULT_VIX_COMPRESSED_THRESHOLD,
    DEFAULT_VIX_ELEVATED_THRESHOLD,
    DEFAULT_VIX_EXTREME_THRESHOLD,
    MAX_DAILY_COMPARISONS,
    AssetClass,
    ExecutionMode,
    RuntimeEnvironment,
    SizingMode,
)
from agentic_trader.market.bars import BAR_DURATIONS
from agentic_trader.research.alpha.probe import MAX_PROBE_TERM_DAYS


WORKSPACE_ROOT = Path(__file__).resolve().parent.parent


def load_envrc(path: str | Path = WORKSPACE_ROOT / ".envrc") -> dict[str, str]:
    """Read simple dotenv assignments without executing shell code or mutating os.environ."""
    source = Path(path)
    if not source.exists():
        return {}
    # .envrc may activate a virtualenv; shell source directives are not settings.
    text = "\n".join(line for line in source.read_text().splitlines() if not re.match(r"^\s*(?:source\s+|\.\s+)", line))
    return {
        key: value
        for key, value in dotenv_values(stream=io.StringIO(text), interpolate=False).items()
        if value is not None
    }


class ContractConfig(BaseModel):
    ticker: str
    name: str
    multiplier: float = 1.0
    tick_size: float = 0.01
    asset_class: AssetClass = AssetClass.FUTURES
    target_risk_dollars: float | None = None

    @field_validator("asset_class", mode="before")
    @classmethod
    def parse_asset_class(cls, v: Any) -> AssetClass:
        if isinstance(v, str):
            v_upper = v.strip().upper()
            if hasattr(AssetClass, v_upper):
                return AssetClass(v_upper)
        if isinstance(v, AssetClass):
            return v
        return AssetClass.FUTURES


InstrumentConfig = ContractConfig


class UniverseEntry(BaseModel):
    symbol: str = Field(pattern=r"^[A-Z][A-Z.]{0,5}$")
    sector: str = Field(default="unknown", pattern=r"^[a-z][a-z0-9_]{0,31}$")


class UniverseConfig(BaseModel):
    """Named groups of equity/ETF symbols expanded into contracts at load time."""

    groups: dict[str, list[UniverseEntry]] = Field(default_factory=dict)
    max_symbols: int = Field(default=250, ge=1, le=500)

    @model_validator(mode="after")
    def validated(self):
        owner: dict[str, str] = {}
        for group, entries in self.groups.items():
            for entry in entries:
                if entry.symbol in owner:
                    raise ValueError(f"Duplicate universe symbol {entry.symbol} in {group} and {owner[entry.symbol]}")
                if entry.symbol.startswith(CRYPTO_SYMBOL_PREFIXES):
                    raise ValueError(f"{entry.symbol} would be routed to the crypto session provider")
                owner[entry.symbol] = group
        if len(owner) > self.max_symbols:
            raise ValueError(f"Universe has {len(owner)} symbols; max_symbols is {self.max_symbols}")
        return self

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted(entry.symbol for entries in self.groups.values() for entry in entries))

    def contract_documents(self) -> dict[str, dict[str, Any]]:
        return {
            entry.symbol: {"ticker": entry.symbol, "name": entry.symbol, "asset_class": "equity"}
            for entries in self.groups.values()
            for entry in entries
        }

    def sector_groups(self) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        for entries in self.groups.values():
            for entry in entries:
                if entry.sector != "unknown":
                    groups.setdefault(f"sector_{entry.sector}", []).append(entry.symbol)
        return {name: sorted(members) for name, members in sorted(groups.items())}


class ScanBudget(StrEnum):
    FULL = "full"  # scheduled scans: per-scan, per-session and per-group limits
    SESSION = "session"  # operator scans: per-session and per-group limits only
    NONE = "none"  # explicit --no-budget or dry runs


class ScanConfig(BaseModel):
    max_cards_per_scan: int = Field(default=1, ge=0)
    max_cards_per_session: int = Field(default=2, ge=0)
    max_cards_per_group_per_session: int = Field(default=1, ge=1)
    max_llm_evaluations_per_scan: int = Field(default=4, ge=1)
    min_bar_coverage: float = Field(default=0.8, ge=0, le=1)
    coverage_sessions: int = Field(default=10, ge=1)
    coverage_reference_symbol: str = Field(default="SPY", pattern=r"^[A-Z][A-Z.]{0,5}$")


DEFAULT_CORRELATION_GROUPS: dict[str, list[str]] = {
    "us_broad_market": ["/MES", "/ES", "SPY", "VOO", "IVV"],
    "us_tech": ["/MNQ", "/NQ", "QQQ", "XLK"],
    "us_smallcap": ["/M2K", "/RTY", "IWM"],
    "gold": ["/MGC", "/GC", "GLD", "IAU"],
    "crude_oil": ["/MCL", "/CL", "USO"],
    "us_treasuries": ["/ZN", "/ZB", "TLT", "IEF"],
    "crypto_btc": ["BTC/USD", "BTCUSD", "/MBT", "BITO"],
    "crypto_eth": ["ETH/USD", "ETHUSD", "/MET"],
}


class PortfolioConfig(BaseModel):
    cash: float = Field(default=100000.0, gt=0, allow_inf_nan=False)
    max_stop_risk_pct: float = Field(default=0.02, gt=0, le=1, allow_inf_nan=False)
    max_notional_exposure: float = 60000.0
    max_concurrent_contracts: int = 2
    max_concurrent_positions: int = 4
    default_equity_risk_dollars: float = 250.0
    max_futures_exposure: float = 40000.0
    max_equity_exposure: float = 40000.0
    max_crypto_exposure: float = 20000.0
    max_correlated_positions: int = 1
    max_correlation_threshold: float = 0.85
    enable_dynamic_correlation: bool = False
    correlation_groups: dict[str, list[str]] = Field(default_factory=lambda: dict(DEFAULT_CORRELATION_GROUPS))


class RiskConfig(BaseModel):
    min_risk_reward_ratio: float = 2.0
    min_stop_atr_multiple: float = 1.5
    deduplication_hours: int = 12
    lockout_pre_event_minutes: int = 60
    lockout_post_event_minutes: int = 30


class TrendPullbackConfig(BaseModel):
    enabled: bool = True
    daily_ema_fast: int = 50
    daily_ema_slow: int = 200
    trigger_ema_span: int = 20
    trigger_atr_distance_mult: float = 0.5
    rsi_period: int = 14
    rsi_oversold: float = 40.0
    rsi_oversold_dip: float = 45.0
    rsi_overbought: float = 60.0
    rsi_overbought_surge: float = 55.0
    atr_period: int = 14
    atr_multiplier: float = 1.5


class SqueezeBreakoutConfig(BaseModel):
    enabled: bool = True
    bb_length: int = 20
    bb_std: float = 2.0
    kc_length: int = 20
    kc_atr_mult: float = 1.5
    min_squeeze_bars: int = 5
    volume_factor: float = 1.3
    volume_sma_period: int = 20


class StrategyConfig(BaseModel):
    mode: str = DEFAULT_STRATEGY_MODE  # "single", "parallel"
    active_strategy: str = DEFAULT_ACTIVE_STRATEGY  # Used when mode == "single"
    active_strategies: list[str] = Field(
        default_factory=lambda: list(DEFAULT_ACTIVE_STRATEGIES)
    )  # Used when mode == "parallel"
    conflict_resolution: str = DEFAULT_CONFLICT_RESOLUTION  # "netting", "highest_conviction", "first"
    strategy_allocations: dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_STRATEGY_ALLOCATIONS))
    trend_pullback: TrendPullbackConfig = Field(default_factory=TrendPullbackConfig)
    squeeze_breakout: SqueezeBreakoutConfig = Field(default_factory=SqueezeBreakoutConfig)


class SchedulerConfig(BaseModel):
    misfire_grace_seconds: int = Field(default=60, gt=0)
    cron_hour_interval: int = 4
    macro_briefing_enabled: bool = True
    macro_briefing_hour: int = 12
    macro_briefing_minute: int = 30
    intraday_scan_enabled: bool = True
    intraday_interval_minutes: int = 15
    suggestion_scan_times_et: list[str] = Field(default_factory=lambda: ["10:35", "14:35"])

    @field_validator("suggestion_scan_times_et")
    @classmethod
    def valid_times(cls, value: list[str]) -> list[str]:
        # An empty list is the documented operator off switch: no cron suggestion scans.
        if len(set(value)) != len(value):
            raise ValueError("suggestion_scan_times_et requires distinct HH:MM times")
        for item in value:
            if not re.fullmatch(r"^(?:[01]\d|2[0-3]):[0-5]\d$", item):
                raise ValueError(f"Invalid HH:MM time: {item}")
        return value


class RegimeConfig(BaseModel):
    vix_compressed_threshold: float = DEFAULT_VIX_COMPRESSED_THRESHOLD
    vix_elevated_threshold: float = DEFAULT_VIX_ELEVATED_THRESHOLD
    vix_extreme_threshold: float = DEFAULT_VIX_EXTREME_THRESHOLD
    vix_watch_threshold: float = 18.0
    elevated_min_rr: float = Field(default=2.2, ge=2)
    extreme_min_rr: float = Field(default=2.5, ge=2)
    cache_ttl_seconds: int = 900
    yield_curve_enabled: bool = True
    credit_oas_enabled: bool = True
    inflation_breakeven_enabled: bool = True
    hy_oas_elevated_threshold: float = 3.50  # 350 bps
    hy_oas_critical_threshold: float = 5.00  # 500 bps


class FrictionConfig(BaseModel):
    enabled: bool = True
    futures_commission_per_contract: float = 0.62
    equity_commission_per_share: float = 0.005
    futures_slippage_points: float = 0.25
    equity_slippage_pct: float = 0.0002


class RedundancyConfig(BaseModel):
    enabled: bool = False
    fallback_mode: str = ExecutionMode.PAPER
    max_consecutive_failures: int = 3
    recovery_probe_interval_seconds: float = 60.0
    auto_failback: bool = True


class PositionSizingConfig(BaseModel):
    mode: SizingMode = SizingMode.STATIC
    target_risk_pct: float = 0.005  # 0.5% of cash
    target_futures_risk_dollars: float = 300.0
    default_equity_risk_dollars: float = 250.0
    max_contracts_per_trade: int = 4
    min_contracts: int = 1
    max_shares_per_trade: int = 500
    min_shares: float = 1.0
    max_risk_pct_cap: float = 0.01  # Hard ceiling: no single trade or tier can exceed 1.0% capital risk
    max_trade_notional_cap: float = 30000.0  # Max notional for a single trade (50% of $60k portfolio cap)
    drawdown_gating_enabled: bool = True  # Dynamically haircut sizes during portfolio drawdown
    drawdown_haircut_threshold_pct: float = Field(default=0.03, ge=0, lt=1, allow_inf_nan=False)
    max_drawdown_stop_pct: float = Field(default=0.06, gt=0, le=1, allow_inf_nan=False)
    drawdown_min_risk_multiplier: float = Field(default=0.10, gt=0, le=1, allow_inf_nan=False)
    suggest_tiers_enabled: bool = True  # Suggest Half, Base, and Max sizing tiers in Telegram

    @model_validator(mode="after")
    def validate_drawdown_thresholds(self):
        if self.drawdown_haircut_threshold_pct >= self.max_drawdown_stop_pct:
            raise ValueError("Drawdown haircut threshold must be below the sizing halt")
        return self


class OperationsConfig(BaseModel):
    notifications_enabled: bool = True
    startup_grace_seconds: float = Field(default=120, ge=0, allow_inf_nan=False)
    failure_seconds: float = Field(default=120, gt=0, allow_inf_nan=False)
    recovery_seconds: float = Field(default=60, gt=0, allow_inf_nan=False)
    reminder_seconds: float = Field(default=3600, gt=0, allow_inf_nan=False)
    probe_timeout_seconds: float = Field(default=5, gt=0, le=30, allow_inf_nan=False)
    snapshot_max_age_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)
    healthy_observation_days: int = Field(default=30, ge=1)
    retention_interval_seconds: float = Field(default=3600, gt=0, allow_inf_nan=False)
    retention_batch_size: int = Field(default=1000, ge=1, le=10000)


class AccountingConfig(BaseModel):
    refresh_seconds: float = Field(default=60, gt=0, le=3600, allow_inf_nan=False)
    max_age_seconds: float = Field(default=180, gt=0, allow_inf_nan=False)
    max_pages: int = Field(default=100, ge=1, le=1000)
    cash_tolerance: float = Field(default=0.01, ge=0, le=0.01, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_freshness(self):
        if self.max_age_seconds <= self.refresh_seconds:
            raise ValueError("Accounting freshness limit must exceed refresh interval")
        return self


class ExecutionConfig(BaseModel):
    journal_history_days: int = Field(default=7, ge=1, le=90)
    journal_max_pages: int = Field(default=20, ge=1, le=100)
    entry_preflight_lease_seconds: float = Field(default=60, gt=0, le=300)
    entry_queue_max_age_seconds: float = Field(default=120, gt=0)
    signal_max_age_seconds: float = Field(default=14400, gt=0)
    entry_quote_max_age_seconds: float = Field(default=60, gt=0)
    entry_evidence_max_age_seconds: float = Field(default=30, gt=0, le=120, allow_inf_nan=False)
    entry_max_price_drift_pct: float = Field(default=0.01, gt=0, le=0.1)
    worker_interval_seconds: float = Field(default=2, gt=0, le=60)
    worker_batch_size: int = Field(default=20, ge=1, le=100)
    notification_delivery_timeout_seconds: float = Field(default=60, gt=0)
    notification_lease_seconds: float = Field(default=120, gt=0)
    notification_max_attempts: int = Field(default=8, ge=1, le=100)
    notification_retry_seconds: float = Field(default=5, gt=0)
    notification_max_retry_seconds: float = Field(default=900, gt=0)

    broker_request_timeout_seconds: float = Field(default=10, gt=0, le=60)
    stop_replace_timeout_seconds: float = Field(default=10, gt=0, le=60)
    close_cancel_timeout_seconds: float = Field(default=10, gt=0, le=60)
    close_cancel_poll_seconds: float = Field(default=0.25, gt=0, le=5)
    algorithm: str = "immediate"  # "immediate", "twap", "vwap"
    min_slice_quantity_futures: float = 2.0
    min_slice_quantity_equity: float = 100.0
    twap_slices: int = 4
    twap_interval_seconds: float = 5.0
    price_collar_ticks: float = 2.0
    price_collar_pct: float = 0.001
    vwap_intraday_profile: list[float] = Field(default_factory=lambda: [0.25, 0.15, 0.10, 0.10, 0.15, 0.25])

    @model_validator(mode="after")
    def validate_delivery_lease(self):
        if self.notification_delivery_timeout_seconds >= self.notification_lease_seconds:
            raise ValueError("Notification delivery timeout must be shorter than the claim lease")
        return self


class OptionsConfig(BaseModel):
    enabled: bool = True
    default_symbols: list[str] = Field(default_factory=lambda: ["SPY", "QQQ", "IWM"])
    max_expirations: int = Field(default=3, ge=1)
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE
    cache_ttl_seconds: int = 60


class TelemetryConfig(BaseModel):
    health_observation_interval_seconds: float = Field(default=10, gt=0)
    notification_max_age_seconds: float = Field(default=1800, gt=0)
    reconciliation_max_age_seconds: float = Field(default=180, gt=0)
    telegram_max_age_seconds: float = Field(default=120, gt=0)
    worker_max_age_seconds: float = Field(default=30, gt=0)
    scan_max_age_seconds: float = Field(default=18000, gt=0)

    metrics_enabled: bool = True
    metrics_host: str = "0.0.0.0"
    metrics_port: int = 9108
    event_loop_sample_seconds: float = Field(default=1, gt=0)
    event_loop_warning_seconds: float = Field(default=2, gt=0)


class PairsConfig(BaseModel):
    p_value_threshold: float = 0.05
    min_half_life_bars: float = 1.0
    max_half_life_bars: float = 60.0
    lookback_days: int = 252
    z_score_lookback: int = 30
    z_entry_threshold: float = 2.0
    z_exit_threshold: float = 0.5
    default_pairs: list[tuple[str, str]] = Field(
        default_factory=lambda: [
            ("SPY", "QQQ"),
            ("SPY", "IWM"),
            ("QQQ", "IWM"),
            ("GLD", "SLV"),
            ("XLE", "USO"),
            ("V", "MA"),
            ("EWA", "EWC"),
        ]
    )


class BacktestConfig(BaseModel):
    initial_cash: float = DEFAULT_PORTFOLIO_CASH
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE
    max_concurrent_positions: int = 4
    lookback: str = DEFAULT_BACKTEST_LOOKBACK
    monte_carlo_simulations: int = DEFAULT_MONTE_CARLO_SIMULATIONS
    monte_carlo_seed: int = DEFAULT_RANDOM_SEED
    min_warmup_bars: int = DEFAULT_MIN_WARMUP_BARS
    apply_friction: bool = True


class SessionConfig(BaseModel):
    enforce_rth: bool = True
    allow_extended_hours: bool = False
    timezone: str = "America/New_York"
    calendar_provider: str = DEFAULT_SESSION_CALENDAR_PROVIDER
    fallback_providers: list[str] = Field(default_factory=lambda: list(DEFAULT_SESSION_FALLBACK_PROVIDERS))
    cache_ttl_seconds: int = DEFAULT_SESSION_CACHE_TTL_SECONDS


class TrailingStopConfig(BaseModel):
    enabled: bool = True
    mode: str = DEFAULT_TRAILING_STOP_MODE  # "chandelier_atr" (quant ATR high-water mark), "breakeven_atr", "disabled"
    trail_trigger_r: float = DEFAULT_TRAIL_TRIGGER_R  # Profit in R-multiples before trailing stop activates
    trail_atr_multiple: float = DEFAULT_TRAIL_ATR_MULTIPLE  # Distance behind price/high-water mark in ATR multiples
    breakeven_trigger_r: float | None = None  # Explicit jump to break-even (opt-in; None = disabled in quant mode)
    breakeven_buffer_dollars: float = (
        DEFAULT_BREAKEVEN_BUFFER_DOLLARS  # Buffer beyond entry price when moving to break-even
    )
    trail_step_ticks: int = DEFAULT_TRAIL_STEP_TICKS  # Minimum ratchet step in ticks


class MarketDataEvidenceConfig(BaseModel):
    max_pages: int = Field(default=100, ge=1, le=1000)
    max_capture_bytes: int = Field(default=128 * 1024 * 1024, ge=1024)
    min_free_bytes: int = Field(default=1024 * 1024 * 1024, ge=0)


class MarketDataConfig(BaseModel):
    evidence: MarketDataEvidenceConfig = Field(default_factory=MarketDataEvidenceConfig)
    alpaca_feed: str = Field(default="sip", pattern="^(sip|iex)$")
    probe_symbol: str = Field(default="SPY", pattern="^[A-Z]{1,10}$")
    primary_equities_provider: str = DEFAULT_PRIMARY_EQUITIES_PROVIDER  # ExecutionMode.ALPACA, "yfinance"
    fallback_providers: list[str] = Field(default_factory=lambda: list(DEFAULT_FALLBACK_PROVIDERS))
    timeout_seconds: float = Field(default=DEFAULT_DATA_TIMEOUT_SECONDS, gt=0, le=120, allow_inf_nan=False)
    max_retries: int = DEFAULT_DATA_MAX_RETRIES
    retry_backoff_factor: float = DEFAULT_DATA_RETRY_BACKOFF_FACTOR
    scan_concurrency: int = Field(default=8, ge=1, le=32)
    max_requests_per_minute: int = Field(default=150, ge=1, le=1000)


class DatabaseConfig(BaseModel):
    name: str = "signals"
    path: str | None = None
    url: str | None = None
    pool_size: int = DEFAULT_DB_POOL_SIZE
    max_overflow: int = DEFAULT_DB_MAX_OVERFLOW
    pool_timeout: float = DEFAULT_DB_POOL_TIMEOUT
    pool_recycle: int = DEFAULT_DB_POOL_RECYCLE
    echo: bool = DEFAULT_DB_ECHO
    max_retries: int = DEFAULT_DB_MAX_RETRIES
    retry_delay: float = DEFAULT_DB_RETRY_DELAY


class BrokerStreamConfig(BaseModel):
    reconnect_initial_seconds: float = Field(default=DEFAULT_STREAM_RECONNECT_INITIAL_SECONDS, gt=0)
    reconnect_max_seconds: float = Field(default=DEFAULT_STREAM_RECONNECT_MAX_SECONDS, gt=0)


class TelegramConfig(BaseModel):
    poll_timeout_seconds: int = Field(default=10, gt=0)
    read_timeout_seconds: float = Field(default=15, gt=0)
    connect_timeout_seconds: float = Field(default=10, gt=0)
    request_attempts: int = Field(default=3, ge=1, le=5)
    request_retry_delay_seconds: float = Field(default=1, gt=0)
    max_retry_after_seconds: float = Field(default=30, gt=0)
    poll_audit_interval_seconds: float = Field(default=60, gt=0)


class SessionWorkerConfig(BaseModel):
    """Bounded polling and explicit observation universe, separate from trading permissions."""

    enabled: bool = False
    feed: Literal["alpaca:sip", "alpaca:iex"] = "alpaca:sip"
    symbols: list[str] = Field(default_factory=lambda: ["SPY"], min_length=1, max_length=5)
    poll_seconds: int = Field(default=30, ge=10, le=60)
    poll_offset_seconds: int = Field(default=5, ge=0, lt=10)
    max_age_seconds: int = Field(default=180, ge=120, le=3600)

    @field_validator("symbols")
    @classmethod
    def stock_symbols(cls, symbols):
        if len(set(symbols)) != len(symbols) or any(not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", s) for s in symbols):
            raise ValueError("Unique explicit US stock symbols required")
        return symbols


class SessionObservationConfig(SessionWorkerConfig):
    timeframe: str = "15m"
    window_seconds: int = Field(default=180, ge=60, le=600)
    calendar_refresh_seconds: int = Field(default=300, ge=30, le=3600)

    @field_validator("timeframe")
    @classmethod
    def signal_timeframe(cls, value):
        if value not in BAR_DURATIONS:
            raise ValueError("Supported session signal timeframe required")
        return value


class SessionDecisionConfig(SessionWorkerConfig):
    """Prospective diagnostic decisions, independently bounded from trading scans."""

    history_days: int = Field(default=14, ge=2, le=31)
    max_candidates: int = Field(default=12, ge=1, le=32)
    max_decisions_per_poll: int = Field(default=128, ge=1, le=512)


class DailyAcquisitionConfig(BaseModel):
    """Bounded read-only research; pace request starts without blocking the event loop."""

    min_request_interval_seconds: float = Field(default=0.6, ge=0, le=60, allow_inf_nan=False)
    max_symbols: int = Field(default=500, ge=1, le=500)
    max_days: int = Field(default=3660, ge=1, le=3660)
    max_elapsed_seconds: float = Field(default=900, ge=1, le=7200, allow_inf_nan=False)


class DailyPanelWorkerConfig(BaseModel):
    """Opt-in native-daily collection; the frozen protocol owns feed and cohort."""

    model_config = {"extra": "forbid"}

    enabled: bool = False
    protocol_path: Path | None = None
    comparison_protocol_paths: tuple[Path, ...] = Field(default=(), max_length=MAX_DAILY_COMPARISONS)
    poll_seconds: int = Field(default=60, ge=10, le=300, strict=True)
    max_age_seconds: int = Field(default=1200, ge=60, le=3600, strict=True)
    calendar_refresh_seconds: int = Field(default=300, ge=30, le=3600, strict=True)

    @field_validator("protocol_path", mode="before")
    @classmethod
    def explicit_path(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError("Explicit nonempty daily-panel protocol path required")
        return value

    @field_validator("comparison_protocol_paths", mode="before")
    @classmethod
    def explicit_comparison_paths(cls, values):
        if not isinstance(values, (list, tuple)) or any(
            isinstance(value, str) and not value.strip() for value in values
        ):
            raise ValueError("Explicit nonempty daily comparison protocol paths required")
        return values

    @field_validator("comparison_protocol_paths")
    @classmethod
    def unique_comparison_paths(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("Daily comparison protocol paths must be unique")
        return values

    @model_validator(mode="after")
    def bounded_worker(self):
        if self.enabled and self.protocol_path is None:
            raise ValueError("Enabled daily-panel worker requires an explicit protocol path")
        if self.max_age_seconds < self.poll_seconds:
            raise ValueError("Daily-panel freshness must cover its polling interval")
        return self


class AlphaPipelineConfig(BaseModel):
    daily_research: DailyAcquisitionConfig = Field(default_factory=DailyAcquisitionConfig)
    daily_panel: DailyPanelWorkerConfig = Field(default_factory=DailyPanelWorkerConfig)
    observations: SessionObservationConfig = Field(default_factory=SessionObservationConfig)
    decisions: SessionDecisionConfig = Field(default_factory=SessionDecisionConfig)
    minimum_shadow_sessions: int = Field(default=20, ge=1)
    minimum_shadow_decisions: int = Field(default=10, ge=1)
    qualification_max_age_days: int = Field(default=45, ge=1)
    max_probes: int = Field(default=3, ge=0)
    probe_risk_dollars: float = Field(default=100.0, gt=0)
    # Review checkpoint, not a risk bound: the -4R kill rule limits losses. Daily alphas trade ~once per 25-50 sessions.
    probe_term_days: int = Field(default=30, ge=1, le=MAX_PROBE_TERM_DAYS)


class AppConfig(BaseModel):
    alpha_pipeline: AlphaPipelineConfig = Field(default_factory=AlphaPipelineConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    broker_stream: BrokerStreamConfig = Field(default_factory=BrokerStreamConfig)
    environment: RuntimeEnvironment = RuntimeEnvironment.DEVELOPMENT
    portfolio: PortfolioConfig = Field(default_factory=PortfolioConfig)
    contracts: dict[str, ContractConfig] = Field(default_factory=dict)
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    scan: ScanConfig = Field(default_factory=ScanConfig)
    explicit_contracts: tuple[str, ...] | None = None
    risk: RiskConfig = Field(default_factory=RiskConfig)
    strategies: StrategyConfig = Field(default_factory=StrategyConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    regime: RegimeConfig = Field(default_factory=RegimeConfig)
    friction: FrictionConfig = Field(default_factory=FrictionConfig)
    redundancy: RedundancyConfig = Field(default_factory=RedundancyConfig)
    sizing: PositionSizingConfig = Field(default_factory=PositionSizingConfig)
    operations: OperationsConfig = Field(default_factory=OperationsConfig)
    accounting: AccountingConfig = Field(default_factory=AccountingConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    options: OptionsConfig = Field(default_factory=OptionsConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    pairs: PairsConfig = Field(default_factory=PairsConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    trailing_stop: TrailingStopConfig = Field(default_factory=TrailingStopConfig)
    market_data: MarketDataConfig = Field(default_factory=MarketDataConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)

    # Environment variables
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    llm_model: str = "openai/gpt-4o"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None
    finnhub_api_key: str | None = None
    db_name: str = "signals"
    db_path: str | None = None
    db_url: str | None = None

    @property
    def resolved_db_url(self) -> str:
        """Resolve explicit model values only; a bare model never selects a production DB."""
        path = self.db_path or self.database.path
        if path:
            return normalize_db_url(path)
        url = self.db_url or self.database.url
        if url:
            return normalize_db_url(url)
        raise ValueError("Database is not configured. Load runtime settings or inject an isolated database.")

    @property
    def llm_api_key(self) -> str | None:
        if self.llm_model.startswith("anthropic/"):
            return self.anthropic_api_key
        if self.llm_model.startswith(("gemini/", "vertex_ai/")):
            return self.gemini_api_key
        return self.openai_api_key

    @property
    def non_universe_contracts(self) -> list[str]:
        """Instruments configured explicitly under contracts:, i.e. the pre-universe scan set.

        `explicit_contracts` is `None` when an `AppConfig` is built directly (not via
        `load_config`); every configured contract is then treated as explicit, so a
        symbol that also appears in `universe` is never silently dropped.
        """
        explicit = set(self.contracts) if self.explicit_contracts is None else set(self.explicit_contracts)
        universe_only = set(self.universe.symbols) - explicit
        return sorted(k for k in self.contracts if k not in universe_only)

    # Broker Execution Configuration
    execution_mode: str = ExecutionMode.PAPER  # ExecutionMode.PAPER, "tradovate", ExecutionMode.ALPACA, "manual"
    tradovate_api_key: str | None = None
    tradovate_api_secret: str | None = None
    tradovate_username: str | None = None
    tradovate_password: str | None = None
    tradovate_account_id: str | None = None
    tradovate_environment: str = "demo"  # "demo" or "live"
    tradovate_ws_url: str | None = None

    # Alpaca Execution Configuration
    alpaca_api_key: str | None = None
    alpaca_api_secret: str | None = None
    alpaca_base_url: str | None = None
    alpaca_data_feed: str = "iex"
    alpaca_paper: bool = True

    # Market Data & News Feeds
    iex_cloud_api_token: str | None = None

    # Conversational Copilot Configuration
    copilot_chat_enabled: bool = True


def normalize_db_url(value: str) -> str:
    if "://" not in value:
        return f"sqlite+aiosqlite:///{Path(value).resolve()}"
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+asyncpg://", 1)
    if value.startswith("sqlite://"):
        return value.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return value


def load_config(
    config_path: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    env_file: str | Path | None = None,
) -> AppConfig:
    """Load settings at the application boundary; explicit mappings never read local secrets.

    COPILOT_CONFIG selects a separate development/test YAML. COPILOT_ENV_FILE=''
    disables dotenv loading. Environment values, including empty strings, win.
    """
    env = dict(os.environ if environ is None else environ)
    environment = env.get("COPILOT_ENV", RuntimeEnvironment.PRODUCTION)
    selected_env_file = env_file
    if environ is None and selected_env_file is None and environment == RuntimeEnvironment.PRODUCTION:
        selected_env_file = env.get("COPILOT_ENV_FILE", str(WORKSPACE_ROOT / ".envrc"))
    if selected_env_file:
        env = {**load_envrc(selected_env_file), **env}
    if not config_path:
        config_path = env.get("COPILOT_CONFIG")
    if not config_path:
        if environment != RuntimeEnvironment.PRODUCTION:
            raise ValueError("Set COPILOT_CONFIG to a separate configuration for development/test runs.")
        config_path = str(WORKSPACE_ROOT / "config" / "config.yaml")

    cfg_dict: dict[str, Any] = {}
    path = Path(config_path)
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            cfg_dict = yaml.safe_load(f) or {}

    # Environment overrides
    telegram_token = env.get("TELEGRAM_BOT_TOKEN")
    telegram_chat = env.get("TELEGRAM_CHAT_ID")
    llm_model = env.get("LLM_MODEL", "openai/gpt-4o")
    openai_key = env.get("OPENAI_API_KEY")
    anthropic_key = env.get("ANTHROPIC_API_KEY")
    gemini_key = env.get("GEMINI_API_KEY")
    finnhub_key = env.get("FINNHUB_API_KEY") or env.get("FINNHUB__API_KEY")
    iex_token = env.get("IEX_CLOUD_API_TOKEN") or env.get("IEX_API_KEY") or env.get("IEX_TOKEN")
    db_cfg = cfg_dict.get("database", {})
    if not isinstance(db_cfg, dict):
        db_cfg = {}

    db_name = env.get("DB_NAME", db_cfg.get("name", "signals"))
    db_filename = db_name if db_name.endswith(".db") else f"{db_name}.db"
    # Explicit environment selection outranks YAML; DB_NAME alone selects a SQLite sandbox.
    explicit_path = env.get("DB_PATH")
    if explicit_path:
        db_url = None
    elif env.get("DATABASE_URL"):
        db_url = env["DATABASE_URL"].strip()
    elif env.get("DB_NAME"):
        explicit_path = str(WORKSPACE_ROOT / "data" / environment / db_filename)
        db_url = None
    else:
        explicit_path = db_cfg.get("path")
        db_url = (
            None
            if explicit_path
            else db_cfg.get("url")
            or (DEFAULT_POSTGRES_DB_URL if environment == RuntimeEnvironment.PRODUCTION else None)
        )
    if environment != RuntimeEnvironment.PRODUCTION and not explicit_path and not db_url:
        raise ValueError("Development/test configuration requires an explicit database.")
    db_path = explicit_path

    execution_mode = env.get("EXECUTION_MODE", ExecutionMode.PAPER).lower()
    tradovate_api_key = env.get("TRADOVATE_API_KEY")
    tradovate_api_secret = env.get("TRADOVATE_API_SECRET")
    tradovate_username = env.get("TRADOVATE_USERNAME")
    tradovate_password = env.get("TRADOVATE_PASSWORD")
    tradovate_account_id = env.get("TRADOVATE_ACCOUNT_ID")
    tradovate_env = env.get("TRADOVATE_ENVIRONMENT", "demo").lower()

    alpaca_api_key = (
        env.get("APCA_API_KEY_ID")
        or env.get("ALPACA_KEY_ID")
        or env.get("ALPACA_API_KEY")
        or env.get("ALPACA_API_KEY_ID")
    )
    alpaca_api_secret = (
        env.get("APCA_API_SECRET_KEY")
        or env.get("ALPACA_SECRET_KEY")
        or env.get("ALPACA_API_SECRET")
        or env.get("ALPACA_API_SECRET_KEY")
    )
    alpaca_base_url = env.get("APCA_API_BASE_URL") or env.get("ALPACA_BASE_URL")
    alpaca_data_feed = env.get("ALPACA_DATA_FEED") or env.get("APCA_DATA_FEED") or "iex"
    market_data_cfg = cfg_dict.get("market_data", {})
    if not isinstance(market_data_cfg, dict):
        market_data_cfg = {}
    if env.get("ALPACA_DATA_FEED") or env.get("APCA_DATA_FEED"):
        market_data_cfg["alpaca_feed"] = alpaca_data_feed
    else:
        market_data_cfg.setdefault("alpaca_feed", alpaca_data_feed)

    if alpaca_base_url:
        alpaca_paper = ExecutionMode.PAPER in alpaca_base_url.lower()
    else:
        alpaca_paper = env.get("ALPACA_PAPER", "true").lower() in ("true", "1", "yes")

    if env.get("PORTFOLIO_CASH"):
        cfg_dict.setdefault("portfolio", {})["cash"] = float(env["PORTFOLIO_CASH"])
    if env.get("MAX_NOTIONAL_EXPOSURE"):
        cfg_dict.setdefault("portfolio", {})["max_notional_exposure"] = float(env["MAX_NOTIONAL_EXPOSURE"])

    tradovate_ws_url = env.get("TRADOVATE_WS_URL")
    redundancy_cfg = cfg_dict.get("redundancy", {})
    if env.get("BROKER_REDUNDANCY_ENABLED"):
        redundancy_cfg["enabled"] = env.get("BROKER_REDUNDANCY_ENABLED", "").lower() in ("true", "1", "yes")
    if env.get("BROKER_FALLBACK_MODE"):
        redundancy_cfg["fallback_mode"] = env.get("BROKER_FALLBACK_MODE", "").lower()

    sizing_cfg = cfg_dict.get("sizing", {})
    if env.get("SIZING_MODE"):
        sizing_cfg["mode"] = env.get("SIZING_MODE", "").lower()
    if env.get("TARGET_RISK_PCT"):
        sizing_cfg["target_risk_pct"] = float(env["TARGET_RISK_PCT"])

    exec_cfg = cfg_dict.get("execution", {})
    strat_dict = cfg_dict.get("strategies", {})
    if env.get("STRATEGY_MODE"):
        strat_dict["mode"] = env.get("STRATEGY_MODE", "").lower()
    if env.get("ACTIVE_STRATEGY"):
        strat_dict["active_strategy"] = env.get("ACTIVE_STRATEGY", "").lower()
    if env.get("ACTIVE_STRATEGIES"):
        strat_dict["active_strategies"] = [
            s.strip().lower() for s in env.get("ACTIVE_STRATEGIES", "").split(",") if s.strip()
        ]
    if env.get("CONFLICT_RESOLUTION"):
        strat_dict["conflict_resolution"] = env.get("CONFLICT_RESOLUTION", "").lower()

    trend_cfg = strat_dict.get("trend_pullback", {})
    squeeze_cfg = strat_dict.get("squeeze_breakout", {})
    strat_kwargs: dict[str, Any] = {
        "trend_pullback": TrendPullbackConfig(**trend_cfg) if isinstance(trend_cfg, dict) else trend_cfg,
        "squeeze_breakout": SqueezeBreakoutConfig(**squeeze_cfg) if isinstance(squeeze_cfg, dict) else squeeze_cfg,
    }
    for field in ("mode", "active_strategy", "active_strategies", "conflict_resolution", "strategy_allocations"):
        if field in strat_dict:
            strat_kwargs[field] = strat_dict[field]

    strategies_config = StrategyConfig(**strat_kwargs)

    universe = UniverseConfig(**(cfg_dict.get("universe") or {}))
    explicit_contracts = dict(cfg_dict.get("contracts", {}))
    contract_documents = {**universe.contract_documents(), **explicit_contracts}  # explicit wins
    portfolio_dict = dict(cfg_dict.get("portfolio", {}))
    groups = {k: list(v) for k, v in dict(portfolio_dict.get("correlation_groups", DEFAULT_CORRELATION_GROUPS)).items()}
    for name, members in universe.sector_groups().items():
        groups[name] = sorted(set(groups.get(name, [])) | set(members))
    portfolio_dict["correlation_groups"] = groups

    config = AppConfig(
        alpha_pipeline=AlphaPipelineConfig(**cfg_dict.get("alpha_pipeline", {})),
        telegram=TelegramConfig(**cfg_dict.get("telegram", {})),
        environment=RuntimeEnvironment(environment),
        broker_stream=BrokerStreamConfig(**cfg_dict.get("broker_stream", {})),
        portfolio=PortfolioConfig(**portfolio_dict),
        contracts={k: ContractConfig(**v) for k, v in contract_documents.items()},
        universe=universe,
        scan=ScanConfig(**(cfg_dict.get("scan") or {})),
        explicit_contracts=tuple(explicit_contracts),
        risk=RiskConfig(**cfg_dict.get("risk", {})),
        strategies=strategies_config,
        scheduler=SchedulerConfig(**cfg_dict.get("scheduler", {})),
        regime=RegimeConfig(**cfg_dict.get("regime", {})),
        friction=FrictionConfig(**cfg_dict.get("friction", {})),
        redundancy=RedundancyConfig(**redundancy_cfg),
        sizing=PositionSizingConfig(**sizing_cfg),
        operations=OperationsConfig(**cfg_dict.get("operations", {})),
        accounting=AccountingConfig(**cfg_dict.get("accounting", {})),
        execution=ExecutionConfig(**exec_cfg),
        options=OptionsConfig(**cfg_dict.get("options", {})),
        telemetry=TelemetryConfig(**cfg_dict.get("telemetry", {})),
        pairs=PairsConfig(**cfg_dict.get("pairs", {})),
        backtest=BacktestConfig(**cfg_dict.get("backtest", {})),
        trailing_stop=TrailingStopConfig(**cfg_dict.get("trailing_stop", {})),
        market_data=MarketDataConfig(**market_data_cfg),
        database=DatabaseConfig(
            name=db_name,
            path=explicit_path,
            url=db_url,
            pool_size=int(db_cfg.get("pool_size", DEFAULT_DB_POOL_SIZE)),
            max_overflow=int(db_cfg.get("max_overflow", DEFAULT_DB_MAX_OVERFLOW)),
            pool_timeout=float(db_cfg.get("pool_timeout", DEFAULT_DB_POOL_TIMEOUT)),
            pool_recycle=int(db_cfg.get("pool_recycle", DEFAULT_DB_POOL_RECYCLE)),
            echo=bool(db_cfg.get("echo", DEFAULT_DB_ECHO)),
            max_retries=int(db_cfg.get("max_retries", DEFAULT_DB_MAX_RETRIES)),
            retry_delay=float(db_cfg.get("retry_delay", DEFAULT_DB_RETRY_DELAY)),
        ),
        telegram_bot_token=telegram_token
        if environment != RuntimeEnvironment.TEST and telegram_token and "your_" not in telegram_token
        else None,
        telegram_chat_id=telegram_chat
        if environment != RuntimeEnvironment.TEST and telegram_chat and "your_" not in telegram_chat
        else None,
        llm_model=llm_model,
        openai_api_key=openai_key,
        anthropic_api_key=anthropic_key,
        gemini_api_key=gemini_key,
        finnhub_api_key=finnhub_key,
        iex_cloud_api_token=iex_token,
        db_name=db_name,
        db_path=db_path,
        db_url=db_url,
        execution_mode=execution_mode,
        tradovate_api_key=tradovate_api_key,
        tradovate_api_secret=tradovate_api_secret,
        tradovate_username=tradovate_username,
        tradovate_password=tradovate_password,
        tradovate_account_id=tradovate_account_id,
        tradovate_environment=tradovate_env,
        tradovate_ws_url=tradovate_ws_url,
        alpaca_api_key=alpaca_api_key,
        alpaca_api_secret=alpaca_api_secret,
        alpaca_base_url=alpaca_base_url,
        alpaca_data_feed=alpaca_data_feed,
        alpaca_paper=alpaca_paper,
    )
    return config
