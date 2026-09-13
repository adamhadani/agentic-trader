from dataclasses import dataclass, field
from datetime import datetime

from agentic_trader.constants import AssetClass, Direction, ExitReason, StrategyType


@dataclass
class BacktestTrade:
    """Represents a simulated executed trade in the backtester."""

    symbol: str
    asset_class: AssetClass
    strategy: StrategyType
    direction: Direction
    entry_timestamp: datetime
    entry_price: float
    quantity: float
    stop_loss: float
    take_profit: float
    risk_dollars: float
    exit_timestamp: datetime | None = None
    exit_price: float | None = None
    exit_reason: ExitReason | None = None
    pnl_dollars: float | None = None
    pnl_pct: float | None = None
    duration_bars: int = 0
    commission: float = 0.0
    slippage_dollars: float = 0.0
    initial_stop_loss: float = 0.0
    high_water_mark: float = 0.0
    low_water_mark: float = 0.0


@dataclass
class EquityPoint:
    """Snapshot of portfolio equity and drawdown at a point in time."""

    timestamp: datetime
    portfolio_equity: float
    cash_reserve: float
    drawdown_pct: float


@dataclass
class MonteCarloResult:
    """Statistical distribution of performance metrics across bootstrap resamplings."""

    n_simulations: int
    median_equity: float
    ci_5th_equity: float
    ci_95th_equity: float
    median_drawdown_pct: float
    ci_95th_drawdown_pct: float
    median_sharpe: float
    ci_5th_sharpe: float
    risk_of_ruin_10pct: float
    risk_of_ruin_20pct: float
    var_95_pct: float
    cvar_95_pct: float


@dataclass
class FactorAttribution:
    """Performance contribution decomposed by quantitative strategy factor."""

    factor_name: str
    pnl_dollars: float
    return_contribution_pct: float
    trade_count: int
    win_rate: float
    profit_factor: float
    pct_of_total_alpha: float


@dataclass
class RegimeAttribution:
    """Strategy performance metrics segmented by macro market regime."""

    regime: str
    trade_count: int
    pnl_dollars: float
    win_rate: float
    profit_factor: float
    avg_trade_pnl: float


@dataclass
class AssetClassAttribution:
    """Performance contribution segmented by asset class or sector cluster."""

    name: str
    trade_count: int
    pnl_dollars: float
    win_rate: float
    profit_factor: float
    pct_of_total_pnl: float


@dataclass
class PerformanceAttributionResult:
    """Aggregated multi-dimensional performance attribution."""

    factors: list[FactorAttribution] = field(default_factory=list)
    regimes: list[RegimeAttribution] = field(default_factory=list)
    asset_classes: list[AssetClassAttribution] = field(default_factory=list)
    sectors: list[AssetClassAttribution] = field(default_factory=list)


@dataclass
class BacktestResult:
    """Aggregated performance results and trade log from a backtest run."""

    starting_cash: float
    ending_equity: float
    strategy_pnl: float
    strategy_return_pct: float
    cash_yield_pnl: float
    combined_total_pnl: float
    combined_return_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    profit_factor: float
    max_drawdown_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    annualized_return_pct: float
    avg_trade_duration_bars: float
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[EquityPoint] = field(default_factory=list)
    monte_carlo: MonteCarloResult | None = None
    gross_strategy_pnl: float = 0.0
    total_commissions: float = 0.0
    total_slippage: float = 0.0
    attribution: PerformanceAttributionResult | None = None
