import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from agentic_trader.constants import (
    DEFAULT_VIX_COMPRESSED_THRESHOLD,
    DEFAULT_VIX_ELEVATED_THRESHOLD,
    DEFAULT_VIX_EXTREME_THRESHOLD,
    AssetClass,
)


WORKSPACE_ROOT = Path(__file__).resolve().parent.parent


def load_envrc():
    """Load export KEY=VAL lines from .envrc if present."""
    envrc_path = WORKSPACE_ROOT / ".envrc"
    if not envrc_path.exists():
        return
    try:
        with open(envrc_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                match = re.match(r'^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*["\']?(.*?)["\']?$', line)
                if match:
                    key, val = match.groups()
                    if key not in os.environ or not os.environ[key]:
                        os.environ[key] = val
    except Exception:
        pass


load_envrc()


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
    cash: float = 100000.0
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
    trend_pullback: TrendPullbackConfig = Field(default_factory=TrendPullbackConfig)
    squeeze_breakout: SqueezeBreakoutConfig = Field(default_factory=SqueezeBreakoutConfig)


class SchedulerConfig(BaseModel):
    cron_hour_interval: int = 4
    retune_enabled: bool = True
    retune_day_of_week: str = "sat"
    retune_hour: int = 2
    retune_minute: int = 0


class RegimeConfig(BaseModel):
    vix_compressed_threshold: float = DEFAULT_VIX_COMPRESSED_THRESHOLD
    vix_elevated_threshold: float = DEFAULT_VIX_ELEVATED_THRESHOLD
    vix_extreme_threshold: float = DEFAULT_VIX_EXTREME_THRESHOLD
    cache_ttl_seconds: int = 900


class FrictionConfig(BaseModel):
    enabled: bool = True
    futures_commission_per_contract: float = 0.62
    equity_commission_per_share: float = 0.005
    futures_slippage_points: float = 0.25
    equity_slippage_pct: float = 0.0002


class RedundancyConfig(BaseModel):
    enabled: bool = False
    fallback_mode: str = "paper"
    max_consecutive_failures: int = 3
    recovery_probe_interval_seconds: float = 60.0
    auto_failback: bool = True


class PositionSizingConfig(BaseModel):
    mode: str = "static"  # "static", "volatility_targeted", "fractional_kelly"
    target_risk_pct: float = 0.005  # 0.5% of cash
    target_futures_risk_dollars: float = 300.0
    default_equity_risk_dollars: float = 250.0
    max_contracts_per_trade: int = 4
    min_contracts: int = 1
    max_shares_per_trade: int = 500
    min_shares: float = 1.0
    kelly_fraction: float = 0.5
    baseline_win_rate: float = 0.50


class ExecutionConfig(BaseModel):
    algorithm: str = "immediate"  # "immediate", "twap", "vwap"
    min_slice_quantity_futures: float = 2.0
    min_slice_quantity_equity: float = 100.0
    twap_slices: int = 4
    twap_interval_seconds: float = 5.0
    price_collar_ticks: float = 2.0
    price_collar_pct: float = 0.001
    vwap_intraday_profile: list[float] = Field(default_factory=lambda: [0.25, 0.15, 0.10, 0.10, 0.15, 0.25])


class OptionsConfig(BaseModel):
    enabled: bool = True
    default_symbols: list[str] = Field(default_factory=lambda: ["SPY", "QQQ", "IWM"])
    max_expirations: int = 3
    risk_free_rate: float = 0.045
    cache_ttl_seconds: int = 60


class AppConfig(BaseModel):
    portfolio: PortfolioConfig = Field(default_factory=PortfolioConfig)
    contracts: dict[str, ContractConfig] = Field(default_factory=dict)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    strategies: StrategyConfig = Field(default_factory=StrategyConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    regime: RegimeConfig = Field(default_factory=RegimeConfig)
    friction: FrictionConfig = Field(default_factory=FrictionConfig)
    redundancy: RedundancyConfig = Field(default_factory=RedundancyConfig)
    sizing: PositionSizingConfig = Field(default_factory=PositionSizingConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    options: OptionsConfig = Field(default_factory=OptionsConfig)

    # Environment variables
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    llm_model: str = "openai/gpt-4o"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None
    finnhub_api_key: str | None = None
    db_path: str = str(WORKSPACE_ROOT / "data" / "signals.db")

    # Broker Execution Configuration
    execution_mode: str = "paper"  # "paper", "tradovate", "alpaca", "manual"
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


def load_config(config_path: str | None = None) -> AppConfig:
    load_envrc()
    if not config_path:
        config_path = str(WORKSPACE_ROOT / "config" / "config.yaml")

    cfg_dict: dict[str, Any] = {}
    path = Path(config_path)
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            cfg_dict = yaml.safe_load(f) or {}

    # Environment overrides
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_chat = os.getenv("TELEGRAM_CHAT_ID")
    llm_model = os.getenv("LLM_MODEL", "openai/gpt-4o")
    openai_key = os.getenv("OPENAI_API_KEY")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    gemini_key = os.getenv("GEMINI_API_KEY")
    finnhub_key = os.getenv("FINNHUB_API_KEY") or os.getenv("FINNHUB__API_KEY")
    iex_token = os.getenv("IEX_CLOUD_API_TOKEN") or os.getenv("IEX_API_KEY") or os.getenv("IEX_TOKEN")
    db_path = os.getenv("DB_PATH", str(WORKSPACE_ROOT / "data" / "signals.db"))
    execution_mode = os.getenv("EXECUTION_MODE", "paper").lower()
    tradovate_api_key = os.getenv("TRADOVATE_API_KEY")
    tradovate_api_secret = os.getenv("TRADOVATE_API_SECRET")
    tradovate_username = os.getenv("TRADOVATE_USERNAME")
    tradovate_password = os.getenv("TRADOVATE_PASSWORD")
    tradovate_account_id = os.getenv("TRADOVATE_ACCOUNT_ID")
    tradovate_env = os.getenv("TRADOVATE_ENVIRONMENT", "demo").lower()

    alpaca_api_key = (
        os.getenv("APCA_API_KEY_ID")
        or os.getenv("ALPACA_KEY_ID")
        or os.getenv("ALPACA_API_KEY")
        or os.getenv("ALPACA_API_KEY_ID")
    )
    alpaca_api_secret = (
        os.getenv("APCA_API_SECRET_KEY")
        or os.getenv("ALPACA_SECRET_KEY")
        or os.getenv("ALPACA_API_SECRET")
        or os.getenv("ALPACA_API_SECRET_KEY")
    )
    alpaca_base_url = os.getenv("APCA_API_BASE_URL") or os.getenv("ALPACA_BASE_URL")
    alpaca_data_feed = os.getenv("ALPACA_DATA_FEED") or os.getenv("APCA_DATA_FEED") or "iex"

    if alpaca_base_url:
        alpaca_paper = "paper" in alpaca_base_url.lower()
    else:
        alpaca_paper = os.getenv("ALPACA_PAPER", "true").lower() in ("true", "1", "yes")

    if os.getenv("PORTFOLIO_CASH"):
        cfg_dict.setdefault("portfolio", {})["cash"] = float(os.environ["PORTFOLIO_CASH"])
    if os.getenv("MAX_NOTIONAL_EXPOSURE"):
        cfg_dict.setdefault("portfolio", {})["max_notional_exposure"] = float(os.environ["MAX_NOTIONAL_EXPOSURE"])

    tradovate_ws_url = os.getenv("TRADOVATE_WS_URL")
    redundancy_cfg = cfg_dict.get("redundancy", {})
    if os.getenv("BROKER_REDUNDANCY_ENABLED"):
        redundancy_cfg["enabled"] = os.getenv("BROKER_REDUNDANCY_ENABLED", "").lower() in ("true", "1", "yes")
    if os.getenv("BROKER_FALLBACK_MODE"):
        redundancy_cfg["fallback_mode"] = os.getenv("BROKER_FALLBACK_MODE", "").lower()

    sizing_cfg = cfg_dict.get("sizing", {})
    if os.getenv("SIZING_MODE"):
        sizing_cfg["mode"] = os.getenv("SIZING_MODE", "").lower()
    if os.getenv("TARGET_RISK_PCT"):
        sizing_cfg["target_risk_pct"] = float(os.environ["TARGET_RISK_PCT"])

    exec_cfg = cfg_dict.get("execution", {})
    if os.getenv("EXECUTION_ALGORITHM"):
        exec_cfg["algorithm"] = os.getenv("EXECUTION_ALGORITHM", "").lower()

    config = AppConfig(
        portfolio=PortfolioConfig(**cfg_dict.get("portfolio", {})),
        contracts={k: ContractConfig(**v) for k, v in cfg_dict.get("contracts", {}).items()},
        risk=RiskConfig(**cfg_dict.get("risk", {})),
        strategies=StrategyConfig(
            trend_pullback=TrendPullbackConfig(**cfg_dict.get("strategies", {}).get("trend_pullback", {})),
            squeeze_breakout=SqueezeBreakoutConfig(**cfg_dict.get("strategies", {}).get("squeeze_breakout", {})),
        ),
        scheduler=SchedulerConfig(**cfg_dict.get("scheduler", {})),
        regime=RegimeConfig(**cfg_dict.get("regime", {})),
        friction=FrictionConfig(**cfg_dict.get("friction", {})),
        redundancy=RedundancyConfig(**redundancy_cfg),
        sizing=PositionSizingConfig(**sizing_cfg),
        execution=ExecutionConfig(**exec_cfg),
        options=OptionsConfig(**cfg_dict.get("options", {})),
        telegram_bot_token=telegram_token if telegram_token and "your_" not in telegram_token else None,
        telegram_chat_id=telegram_chat if telegram_chat and "your_" not in telegram_chat else None,
        llm_model=llm_model,
        openai_api_key=openai_key,
        anthropic_api_key=anthropic_key,
        gemini_api_key=gemini_key,
        finnhub_api_key=finnhub_key,
        iex_cloud_api_token=iex_token,
        db_path=db_path,
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
