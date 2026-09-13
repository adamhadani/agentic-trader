from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agentic_trader.backtest.engine import BacktestEngine
from agentic_trader.backtest.models import BacktestResult


if TYPE_CHECKING:
    from agentic_trader.config import AppConfig

logger = logging.getLogger(__name__)


@dataclass
class CrisisScenario:
    """Definition of a historical macro crisis scenario."""

    scenario_id: str
    name: str
    description: str
    start_date: str
    end_date: str
    vix_peak: float | None = None
    default_symbols: list[str] = field(default_factory=lambda: ["SPY", "QQQ", "GLD", "TLT"])


@dataclass
class ScenarioStressResult:
    """Stress test outcome metrics for a specific crisis scenario."""

    scenario: CrisisScenario
    starting_cash: float
    ending_equity: float
    net_pnl: float
    net_return_pct: float
    max_drawdown_pct: float
    worst_trade_pnl: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    profit_factor: float
    cash_yield_pnl: float
    pass_fail: str  # "PASS", "WARNING", "FAIL"
    backtest_result: BacktestResult | None = None


@dataclass
class InstantaneousShockResult:
    """Result of applying an immediate multi-factor repricing shock to active open positions."""

    current_open_positions: int
    total_open_notional: float
    immediate_pnl_impact: float
    post_shock_equity: float
    post_shock_drawdown_pct: float
    margin_call_risk: bool
    shock_breakdown: dict[str, float] = field(default_factory=dict)


CRISIS_CATALOG: dict[str, CrisisScenario] = {
    "2008_GFC": CrisisScenario(
        scenario_id="2008_GFC",
        name="2008 Global Financial Crisis",
        description="Lehman Brothers collapse, subprime mortgage credit contagion, and global equity crash.",
        start_date="2008-09-01",
        end_date="2009-03-31",
        vix_peak=80.06,
        default_symbols=["SPY", "QQQ", "GLD", "TLT"],
    ),
    "2020_COVID": CrisisScenario(
        scenario_id="2020_COVID",
        name="2020 COVID Liquidity Shock",
        description="Fastest 30% crash in market history, multiple circuit-breaker halts, and crude oil demand shock.",
        start_date="2020-02-15",
        end_date="2020-04-30",
        vix_peak=82.69,
        default_symbols=["SPY", "QQQ", "GLD", "/MES", "/MNQ", "/MGC", "/MCL"],
    ),
    "2022_INFLATION": CrisisScenario(
        scenario_id="2022_INFLATION",
        name="2022 Fed Rate & Inflation Shock",
        description="Stagflationary bear market, tech stock crash, simultaneous bond and equity breakdown (60/40 failure).",
        start_date="2022-01-01",
        end_date="2022-10-31",
        vix_peak=36.45,
        default_symbols=["SPY", "QQQ", "TLT", "GLD", "/MES", "/MNQ"],
    ),
}

# Proxy mapping for historical simulation prior to micro-futures launch
PROXY_MAPPINGS: dict[str, str] = {
    "/MES": "SPY",
    "/MNQ": "QQQ",
    "/MGC": "GLD",
    "/MCL": "USO",
    "/M2K": "IWM",
}


class CrisisReplayEngine:
    """Stress tests strategies and portfolios across historical crisis regimes and synthetic shocks."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    @classmethod
    def resolve_proxy_symbols(cls, symbols: list[str], start_year: int) -> list[str]:
        """Resolve futures contract symbols to liquid ETF proxies if scenario predates micro futures."""
        resolved: list[str] = []
        for s in symbols:
            # Micro futures /MES and /MNQ launched May 2019; /MCL launched July 2021
            if start_year < 2019 and s in PROXY_MAPPINGS or start_year < 2022 and s == "/MCL":
                resolved.append(PROXY_MAPPINGS[s])
            else:
                resolved.append(s)
        # Deduplicate while preserving order
        return list(dict.fromkeys(resolved))

    def replay_scenario(
        self,
        scenario_id: str,
        symbols: list[str] | None = None,
        strategy_filter: str = "all",
        initial_cash: float | None = None,
        apply_friction: bool = True,
        market_data_map: Any = None,
    ) -> ScenarioStressResult:
        """Replay trading strategy across a specified historical crisis scenario."""
        scenario = CRISIS_CATALOG.get(scenario_id.upper())
        if not scenario:
            raise ValueError(
                f"Unknown crisis scenario '{scenario_id}'. Available scenarios: {list(CRISIS_CATALOG.keys())}"
            )

        start_year = int(scenario.start_date.split("-")[0])
        sim_symbols = symbols or scenario.default_symbols
        resolved_symbols = self.resolve_proxy_symbols(sim_symbols, start_year)

        cash = initial_cash or self.config.portfolio.cash

        engine = BacktestEngine(
            config=self.config,
            initial_cash=cash,
            apply_friction=apply_friction,
        )

        logger.info(
            "Executing Crisis Replay '%s' (%s to %s) across symbols %s...",
            scenario.name,
            scenario.start_date,
            scenario.end_date,
            resolved_symbols,
        )

        res = engine.run(
            symbols=resolved_symbols,
            market_data_map=market_data_map,
            strategy_filter=strategy_filter,
            start_date=scenario.start_date,
            end_date=scenario.end_date,
            enable_attribution=False,
        )

        # Classify survival status
        max_dd = res.max_drawdown_pct
        if max_dd <= 15.0 and res.combined_total_pnl >= -500.0:
            status = "PASS"
        elif max_dd <= 25.0:
            status = "WARNING"
        else:
            status = "FAIL"

        worst_trade = min((t.pnl_dollars or 0.0 for t in res.trades), default=0.0)

        return ScenarioStressResult(
            scenario=scenario,
            starting_cash=res.starting_cash,
            ending_equity=res.ending_equity,
            net_pnl=res.combined_total_pnl,
            net_return_pct=res.combined_return_pct,
            max_drawdown_pct=res.max_drawdown_pct,
            worst_trade_pnl=worst_trade,
            total_trades=res.total_trades,
            winning_trades=res.winning_trades,
            losing_trades=res.losing_trades,
            win_rate=res.win_rate,
            profit_factor=res.profit_factor,
            cash_yield_pnl=res.cash_yield_pnl,
            pass_fail=status,
            backtest_result=res,
        )

    def replay_all_crises(
        self,
        symbols: list[str] | None = None,
        strategy_filter: str = "all",
        initial_cash: float | None = None,
        apply_friction: bool = True,
    ) -> list[ScenarioStressResult]:
        """Run strategy stress tests sequentially through all cataloged historical crises."""
        results = []
        for sid in CRISIS_CATALOG:
            try:
                res = self.replay_scenario(
                    scenario_id=sid,
                    symbols=symbols,
                    strategy_filter=strategy_filter,
                    initial_cash=initial_cash,
                    apply_friction=apply_friction,
                )
                results.append(res)
            except Exception as e:
                logger.error("Failed executing crisis replay for %s: %s", sid, e)
        return results

    @classmethod
    def simulate_instantaneous_shock(
        cls,
        active_positions: list[dict[str, Any]],
        cash: float,
        shock_map: dict[str, float] | None = None,
    ) -> InstantaneousShockResult:
        """Simulate the immediate mark-to-market P&L impact of a macroeconomic factor shock."""
        # Default standard multi-asset macro crash shock: equities -10%, gold +3%, oil -15%, crypto -20%
        default_shocks = {
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
        shocks = shock_map or default_shocks

        total_notional = 0.0
        total_pnl_impact = 0.0
        breakdown: dict[str, float] = {}

        for pos in active_positions:
            symbol = str(pos.get("symbol") or pos.get("contract") or "").upper()
            qty = float(pos.get("quantity") or 1.0)
            entry = float(pos.get("entry_price") or pos.get("current_price") or 100.0)
            mult = float(pos.get("multiplier") or (1.0 if not symbol.startswith("/") else 5.0))
            notional = entry * mult * qty
            total_notional += notional

            direction = str(pos.get("direction", "LONG")).upper()
            # Find matching shock for symbol or asset class fallback
            shock_pct = shocks.get(symbol, -0.10)
            pos_pnl = -shock_pct * notional if direction in ("SHORT", "SELL") else shock_pct * notional

            breakdown[symbol] = round(pos_pnl, 2)
            total_pnl_impact += pos_pnl

        post_shock_equity = max(0.0, cash + total_pnl_impact)
        peak_equity = max(cash, post_shock_equity)
        dd_pct = ((peak_equity - post_shock_equity) / peak_equity * 100.0) if peak_equity > 0 else 0.0
        margin_call = (total_notional > 0 and (total_notional / max(1.0, post_shock_equity)) > 4.0) or (dd_pct >= 25.0)

        return InstantaneousShockResult(
            current_open_positions=len(active_positions),
            total_open_notional=round(total_notional, 2),
            immediate_pnl_impact=round(total_pnl_impact, 2),
            post_shock_equity=round(post_shock_equity, 2),
            post_shock_drawdown_pct=round(dd_pct, 2),
            margin_call_risk=margin_call,
            shock_breakdown=breakdown,
        )
