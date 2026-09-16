import logging
from datetime import UTC

import pandas as pd
import yfinance as yf

from agentic_trader.agent.evaluator import RiskEvaluator
from agentic_trader.backtest.attribution import calculate_performance_attribution
from agentic_trader.backtest.metrics import (
    calculate_drawdown,
    calculate_profit_factor,
    calculate_sharpe_ratio,
    calculate_sortino_ratio,
    calculate_win_rate,
)
from agentic_trader.backtest.models import BacktestResult, BacktestTrade, EquityPoint
from agentic_trader.config import AppConfig, InstrumentConfig, TrailingStopConfig, load_config
from agentic_trader.constants import (
    DEFAULT_MAX_NOTIONAL_EXPOSURE,
    DEFAULT_MIN_WARMUP_BARS,
    DEFAULT_PORTFOLIO_CASH,
    DEFAULT_RISK_FREE_RATE,
    TRADING_DAYS_PER_YEAR,
    AssetClass,
    Direction,
    ExitReason,
    StrategyType,
)
from agentic_trader.data.market_data import ContractMarketData, MarketDataFetcher
from agentic_trader.screeners.base import ScreenerCandidate
from agentic_trader.screeners.strategies import StrategyEngine


logger = logging.getLogger(__name__)


class BacktestEngine:
    """Offline vectorized & bar-by-bar backtesting simulation engine.

    Executes live screening algorithms across historical multi-asset data,
    evaluates bracket exits, and simulates the combined Cash-Plus portfolio equity curve.
    """

    def __init__(
        self,
        config: AppConfig | None = None,
        initial_cash: float = DEFAULT_PORTFOLIO_CASH,
        risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
        max_concurrent_positions: int = 4,
        max_notional_exposure: float = DEFAULT_MAX_NOTIONAL_EXPOSURE,
        apply_friction: bool = True,
        trailing_stop: TrailingStopConfig | None = None,
    ):
        self.config = config or load_config()
        self.initial_cash = initial_cash
        self.risk_free_rate = risk_free_rate
        self.max_concurrent_positions = max_concurrent_positions
        self.max_notional_exposure = max_notional_exposure
        self.apply_friction = apply_friction
        self.friction = self.config.friction
        self.trailing_stop = trailing_stop if trailing_stop is not None else getattr(self.config, "trailing_stop", None)

        self.strategy_engine = StrategyEngine(self.config)
        self.evaluator = RiskEvaluator(self.config)
        self.data_fetcher = MarketDataFetcher()

    def fetch_historical_market_data(
        self,
        symbol: str,
        lookback: str = "2y",
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> ContractMarketData:
        """Fetch and prepare historical data for a symbol via yfinance."""
        contract_info = self.config.contracts.get(symbol)
        if not contract_info and not symbol.startswith("/"):
            contract_info = self.config.contracts.get(f"/{symbol}")
        ticker = (
            contract_info.ticker
            if contract_info
            else (
                f"{symbol.strip('/').upper()}=F"
                if symbol.startswith("/") or symbol in ("ES", "NQ", "MES", "MNQ", "MGC", "MCL", "GC", "CL")
                else symbol
            )
        )

        yf_ticker = yf.Ticker(ticker)

        if start_date and end_date:
            logger.info("Fetching historical data for %s (%s, from %s to %s)...", symbol, ticker, start_date, end_date)
            df_daily = yf_ticker.history(start=start_date, end=end_date, interval="1d")
        else:
            logger.info("Fetching historical data for %s (%s, lookback=%s)...", symbol, ticker, lookback)
            df_daily = yf_ticker.history(period=lookback, interval="1d")

        df_daily = self.data_fetcher._clean_yfinance_df(df_daily)
        df_daily = self.data_fetcher.compute_daily_indicators(df_daily)

        # 1h data for 4h resampling (yfinance supports up to 730d 1h data)
        try:
            if start_date and end_date:
                df_1h = yf_ticker.history(start=start_date, end=end_date, interval="1h")
            else:
                df_1h = yf_ticker.history(
                    period=lookback if lookback in ("1mo", "3mo", "6mo", "1y", "2y") else "2y", interval="1h"
                )
            df_1h = self.data_fetcher._clean_yfinance_df(df_1h)
            df_1h = self.data_fetcher.compute_intraday_indicators(df_1h)
            df_4h = self.data_fetcher.resample_to_4h(df_1h)
            df_4h = self.data_fetcher.compute_intraday_indicators(df_4h)
        except Exception:
            # Fallback if intraday is unavailable: use daily for 4h as approximation
            df_1h = df_daily.copy()
            df_4h = df_daily.copy()

        return ContractMarketData(
            contract=symbol,
            ticker=ticker,
            daily=df_daily,
            four_hour=df_4h,
            hourly=df_1h,
        )

    def run(
        self,
        symbols: list[str],
        market_data_map: dict[str, ContractMarketData] | None = None,
        strategy_filter: str = "all",
        lookback: str = "2y",
        start_date: str | None = None,
        end_date: str | None = None,
        initial_trades: list[BacktestTrade] | None = None,
        enable_attribution: bool = True,
        vix_df: pd.DataFrame | None = None,
        trailing_stop: TrailingStopConfig | None = None,
    ) -> BacktestResult:
        """
        Execute backtest simulation over the specified symbols and date timeline.
        """
        ts_cfg = trailing_stop if trailing_stop is not None else self.trailing_stop

        # Load market data if not provided
        data_map: dict[str, ContractMarketData] = {}
        if market_data_map:
            if start_date and end_date:
                # Filter provided market data to date window
                for sym, cmd in market_data_map.items():
                    sub_d = cmd.daily.loc[start_date:end_date] if not cmd.daily.empty else cmd.daily
                    sub_4h = cmd.four_hour.loc[start_date:end_date] if not cmd.four_hour.empty else cmd.four_hour
                    sub_1h = cmd.hourly.loc[start_date:end_date] if not cmd.hourly.empty else cmd.hourly
                    data_map[sym] = ContractMarketData(
                        contract=cmd.contract,
                        ticker=cmd.ticker,
                        daily=sub_d,
                        four_hour=sub_4h if not sub_4h.empty else sub_d,
                        hourly=sub_1h if not sub_1h.empty else sub_d,
                    )
            else:
                data_map = market_data_map
        else:
            for sym in symbols:
                try:
                    data_map[sym] = self.fetch_historical_market_data(
                        sym, lookback=lookback, start_date=start_date, end_date=end_date
                    )
                except Exception as e:
                    logger.error("Failed to load historical data for %s: %s", sym, e)

        if not data_map:
            raise ValueError("No market data available for backtest execution.")

        # Build master unified chronological timeline from daily indices
        all_dates: set[pd.Timestamp] = set()
        for md in data_map.values():
            if not md.daily.empty:
                all_dates.update(md.daily.index)

        sorted_timeline = sorted(all_dates)
        if len(sorted_timeline) < DEFAULT_MIN_WARMUP_BARS:
            raise ValueError(f"Insufficient historical bars for backtest execution (< {DEFAULT_MIN_WARMUP_BARS} bars).")

        cash_reserve = self.initial_cash
        open_trades: list[BacktestTrade] = [t for t in initial_trades] if initial_trades else []
        closed_trades: list[BacktestTrade] = []
        equity_points: list[EquityPoint] = []
        cumulative_cash_yield = 0.0

        daily_rf_rate = self.risk_free_rate / float(TRADING_DAYS_PER_YEAR)
        min_warmup_bars = DEFAULT_MIN_WARMUP_BARS

        for idx, current_date in enumerate(sorted_timeline):
            # Accrue daily risk-free interest on cash reserves
            daily_interest = cash_reserve * daily_rf_rate
            cumulative_cash_yield += daily_interest
            cash_reserve += daily_interest

            # 1. Update and check active open positions against current bar's price action
            active_trades_remaining: list[BacktestTrade] = []
            for trade in open_trades:
                trade.duration_bars += 1
                trade_md: ContractMarketData | None = data_map.get(trade.symbol)
                if trade_md is None or trade_md.daily.empty or current_date not in trade_md.daily.index:
                    active_trades_remaining.append(trade)
                    continue

                bar = trade_md.daily.loc[current_date]
                high = float(bar["High"])
                low = float(bar["Low"])
                close = float(bar["Close"])

                contract_info = self.config.contracts.get(trade.symbol)
                multiplier = (
                    contract_info.multiplier
                    if contract_info
                    else (1.0 if trade.asset_class == AssetClass.EQUITY else 5.0)
                )

                closed = False
                exit_price = close
                exit_reason = ExitReason.MANUAL_CLOSE

                if trade.direction == Direction.LONG:
                    if low <= trade.stop_loss:
                        closed = True
                        exit_price = trade.stop_loss
                        exit_reason = (
                            ExitReason.TRAILING_STOP
                            if trade.initial_stop_loss > 0 and trade.stop_loss > trade.initial_stop_loss
                            else ExitReason.STOP_LOSS
                        )
                    elif high >= trade.take_profit:
                        closed = True
                        exit_price = trade.take_profit
                        exit_reason = ExitReason.TAKE_PROFIT
                else:  # SHORT
                    if high >= trade.stop_loss:
                        closed = True
                        exit_price = trade.stop_loss
                        exit_reason = (
                            ExitReason.TRAILING_STOP
                            if trade.initial_stop_loss > 0 and trade.stop_loss < trade.initial_stop_loss
                            else ExitReason.STOP_LOSS
                        )
                    elif low <= trade.take_profit:
                        closed = True
                        exit_price = trade.take_profit
                        exit_reason = ExitReason.TAKE_PROFIT

                if closed:
                    trade.exit_timestamp = (
                        current_date.to_pydatetime() if hasattr(current_date, "to_pydatetime") else current_date
                    )
                    actual_exit_price = exit_price
                    exit_slip_dollars = 0.0
                    exit_comm = 0.0

                    if self.apply_friction and self.friction.enabled:
                        if trade.asset_class == AssetClass.EQUITY:
                            slip_pts = exit_price * self.friction.equity_slippage_pct
                            exit_comm = round(self.friction.equity_commission_per_share * trade.quantity, 2)
                        else:
                            slip_pts = self.friction.futures_slippage_points
                            exit_comm = round(self.friction.futures_commission_per_contract * trade.quantity, 2)

                        if trade.direction == Direction.LONG:
                            actual_exit_price = round(exit_price - slip_pts, 2)
                        else:
                            actual_exit_price = round(exit_price + slip_pts, 2)
                        exit_slip_dollars = round(slip_pts * multiplier * trade.quantity, 2)

                    trade.exit_price = actual_exit_price
                    trade.exit_reason = exit_reason
                    trade.commission = round(trade.commission + exit_comm, 2)
                    trade.slippage_dollars = round(trade.slippage_dollars + exit_slip_dollars, 2)

                    if trade.direction == Direction.LONG:
                        net_pnl = (
                            (actual_exit_price - trade.entry_price) * multiplier * trade.quantity
                        ) - trade.commission
                    else:
                        net_pnl = (
                            (trade.entry_price - actual_exit_price) * multiplier * trade.quantity
                        ) - trade.commission

                    trade.pnl_dollars = round(net_pnl, 2)
                    trade.pnl_pct = round((net_pnl / (trade.entry_price * multiplier * trade.quantity)) * 100.0, 2)

                    cash_reserve += net_pnl
                    closed_trades.append(trade)
                else:
                    if ts_cfg and ts_cfg.enabled:
                        bar_atr = float(bar.get("ATR", 0.0)) if "ATR" in bar else 0.0
                        tick_size = contract_info.tick_size if contract_info else 0.25
                        min_step = tick_size * ts_cfg.trail_step_ticks
                        buffer_pts = (ts_cfg.breakeven_buffer_dollars / multiplier) if multiplier > 0 else 0.5

                        if trade.direction == Direction.LONG:
                            trade.high_water_mark = max(trade.high_water_mark, high)
                            fav_dist = trade.high_water_mark - trade.entry_price
                            init_risk = abs(trade.entry_price - trade.initial_stop_loss)
                            r_mult = fav_dist / init_risk if init_risk > 0 else 0.0

                            # Breakeven ratchet (opt-in if breakeven_trigger_r is set)
                            if ts_cfg.breakeven_trigger_r is not None and r_mult >= ts_cfg.breakeven_trigger_r:
                                be_stop = trade.entry_price + buffer_pts
                                if be_stop > trade.stop_loss:
                                    trade.stop_loss = round(be_stop, 2)

                            # Dynamic Trailing stop ratchet (Chandelier ATR / ATR distance from high water mark)
                            if r_mult >= ts_cfg.trail_trigger_r:
                                trail_dist = (
                                    ts_cfg.trail_atr_multiple * bar_atr
                                    if bar_atr > 0
                                    else max(init_risk, init_risk * ts_cfg.trail_atr_multiple)
                                )
                                proposed_trail = trade.high_water_mark - trail_dist
                                if proposed_trail > trade.stop_loss + min_step:
                                    trade.stop_loss = round(proposed_trail, 2)

                        elif trade.direction == Direction.SHORT:
                            trade.low_water_mark = min(trade.low_water_mark, low)
                            fav_dist = trade.entry_price - trade.low_water_mark
                            init_risk = abs(trade.entry_price - trade.initial_stop_loss)
                            r_mult = fav_dist / init_risk if init_risk > 0 else 0.0

                            # Breakeven ratchet (opt-in if breakeven_trigger_r is set)
                            if ts_cfg.breakeven_trigger_r is not None and r_mult >= ts_cfg.breakeven_trigger_r:
                                be_stop = trade.entry_price - buffer_pts
                                if be_stop < trade.stop_loss:
                                    trade.stop_loss = round(be_stop, 2)

                            # Dynamic Trailing stop ratchet (Chandelier ATR / ATR distance from low water mark)
                            if r_mult >= ts_cfg.trail_trigger_r:
                                trail_dist = (
                                    ts_cfg.trail_atr_multiple * bar_atr
                                    if bar_atr > 0
                                    else max(init_risk, init_risk * ts_cfg.trail_atr_multiple)
                                )
                                proposed_trail = trade.low_water_mark + trail_dist
                                if proposed_trail < trade.stop_loss - min_step:
                                    trade.stop_loss = round(proposed_trail, 2)

                    active_trades_remaining.append(trade)

            open_trades = active_trades_remaining

            # 2. Generate new strategy signals if capacity is available
            if idx >= min_warmup_bars and len(open_trades) < self.max_concurrent_positions:
                current_open_notional = 0.0
                for t in open_trades:
                    c_info = self.config.contracts.get(t.symbol)
                    m = c_info.multiplier if c_info else (1.0 if t.asset_class == AssetClass.EQUITY else 5.0)
                    current_open_notional += t.entry_price * m * t.quantity

                for sym in symbols:
                    if len(open_trades) >= self.max_concurrent_positions:
                        break
                    # Prevent duplicate position in same symbol
                    if any(t.symbol == sym for t in open_trades):
                        continue

                    sym_md: ContractMarketData | None = data_map.get(sym)
                    if sym_md is None or sym_md.daily.empty or current_date not in sym_md.daily.index:
                        continue

                    # Slice data up to current bar
                    loc = sym_md.daily.index.get_loc(current_date)
                    loc_idx = loc.stop - 1 if isinstance(loc, slice) else int(loc)

                    if loc_idx < min_warmup_bars:
                        continue

                    sub_daily = sym_md.daily.iloc[: loc_idx + 1]
                    end_of_bar = current_date + pd.Timedelta(days=1)
                    sub_4h = sym_md.four_hour
                    if not sub_4h.empty:
                        sub_4h = sub_4h[sub_4h.index < end_of_bar]
                    if sub_4h.empty or len(sub_4h) < 10:
                        sub_4h = sub_daily

                    sub_market_data = ContractMarketData(
                        contract=sym,
                        ticker=sym_md.ticker,
                        daily=sub_daily,
                        four_hour=sub_4h,
                        hourly=sub_daily,
                    )

                    contract_cfg = self.config.contracts.get(
                        sym,
                        InstrumentConfig(
                            name=sym,
                            ticker=sym,
                            multiplier=1.0 if not sym.startswith("/") else 5.0,
                            tick_size=0.01 if not sym.startswith("/") else 0.25,
                            asset_class=AssetClass.EQUITY if not sym.startswith("/") else AssetClass.FUTURES,
                        ),
                    )
                    asset_class = contract_cfg.asset_class

                    candidate: ScreenerCandidate | None = None
                    # Run strategies according to strategy filter
                    if strategy_filter in ("all", "trend_pullback"):
                        candidate = self.strategy_engine.check_trend_pullback(sub_market_data, asset_class=asset_class)
                    if candidate is None and strategy_filter in ("all", "squeeze_breakout"):
                        candidate = self.strategy_engine.check_squeeze_breakout(
                            sub_market_data, asset_class=asset_class
                        )

                    if candidate is not None:
                        # Deterministically calculate stop, target, risk, and quantity
                        asset_class = candidate.asset_class or contract_cfg.asset_class
                        multiplier = contract_cfg.multiplier

                        levels = self.evaluator.calculate_levels_deterministic(candidate)
                        stop_loss = levels.stop_loss
                        take_profit = levels.take_profit
                        risk_dollars = levels.risk_dollars
                        notional_value = levels.notional_value
                        quantity = levels.quantity

                        # Apply entry slippage and commission
                        entry_price = candidate.current_price
                        entry_slip_dollars = 0.0
                        entry_comm = 0.0

                        if self.apply_friction and self.friction.enabled:
                            if asset_class == AssetClass.EQUITY:
                                slip_pts = entry_price * self.friction.equity_slippage_pct
                                entry_comm = round(self.friction.equity_commission_per_share * quantity, 2)
                            else:
                                slip_pts = self.friction.futures_slippage_points
                                entry_comm = round(self.friction.futures_commission_per_contract * quantity, 2)

                            if candidate.direction == Direction.LONG:
                                entry_price = round(candidate.current_price + slip_pts, 2)
                            else:
                                entry_price = round(candidate.current_price - slip_pts, 2)
                            entry_slip_dollars = round(slip_pts * multiplier * quantity, 2)

                        # Enforce maximum notional exposure
                        if current_open_notional + notional_value <= self.max_notional_exposure:
                            trade = BacktestTrade(
                                symbol=sym,
                                asset_class=asset_class,
                                strategy=StrategyType(candidate.strategy)
                                if candidate.strategy in StrategyType._value2member_map_
                                else StrategyType.TREND_PULLBACK,
                                direction=Direction(candidate.direction),
                                entry_timestamp=current_date.to_pydatetime()
                                if hasattr(current_date, "to_pydatetime")
                                else current_date,
                                entry_price=entry_price,
                                quantity=quantity,
                                stop_loss=stop_loss,
                                take_profit=take_profit,
                                risk_dollars=risk_dollars,
                                commission=entry_comm,
                                slippage_dollars=entry_slip_dollars,
                                initial_stop_loss=stop_loss,
                                high_water_mark=entry_price,
                                low_water_mark=entry_price,
                            )
                            open_trades.append(trade)
                            current_open_notional += notional_value

            # 3. Calculate portfolio equity at bar close
            unrealized_pnl = 0.0
            for t in open_trades:
                t_md: ContractMarketData | None = data_map.get(t.symbol)
                if t_md is not None and not t_md.daily.empty and current_date in t_md.daily.index:
                    curr_close = float(t_md.daily.loc[current_date]["Close"])
                    contract_info = self.config.contracts.get(t.symbol)
                    multiplier = (
                        contract_info.multiplier
                        if contract_info
                        else (1.0 if t.asset_class == AssetClass.EQUITY else 5.0)
                    )
                    if t.direction == Direction.LONG:
                        unrealized_pnl += (curr_close - t.entry_price) * multiplier * t.quantity
                    else:
                        unrealized_pnl += (t.entry_price - curr_close) * multiplier * t.quantity

            total_equity = cash_reserve + unrealized_pnl
            running_peak = max(
                self.initial_cash,
                max((ep.portfolio_equity for ep in equity_points), default=self.initial_cash),
                total_equity,
            )
            dd_pct = ((running_peak - total_equity) / running_peak) * 100.0 if running_peak > 0 else 0.0

            dt = current_date.to_pydatetime() if hasattr(current_date, "to_pydatetime") else current_date
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)

            equity_points.append(
                EquityPoint(
                    timestamp=dt,
                    portfolio_equity=round(total_equity, 2),
                    cash_reserve=round(cash_reserve, 2),
                    drawdown_pct=round(dd_pct, 2),
                )
            )

        # Build performance metrics
        equity_series = pd.Series([ep.portfolio_equity for ep in equity_points])
        daily_returns = equity_series.pct_change().dropna()

        strategy_pnl = sum(t.pnl_dollars or 0.0 for t in closed_trades)
        strategy_return_pct = round((strategy_pnl / self.initial_cash) * 100.0, 2)
        cash_yield_pnl = round(cumulative_cash_yield, 2)
        ending_equity = round(equity_points[-1].portfolio_equity if equity_points else self.initial_cash, 2)
        combined_total_pnl = round(ending_equity - self.initial_cash, 2)
        combined_return_pct = round((combined_total_pnl / self.initial_cash) * 100.0, 2)

        win_rate = calculate_win_rate(closed_trades)
        profit_factor = calculate_profit_factor(closed_trades)
        max_dd_pct, _ = calculate_drawdown(equity_series)
        sharpe = calculate_sharpe_ratio(daily_returns, risk_free_rate=self.risk_free_rate)
        sortino = calculate_sortino_ratio(daily_returns, risk_free_rate=self.risk_free_rate)

        days = max(1, len(sorted_timeline))
        annualized_return = round((((ending_equity / self.initial_cash) ** (252.0 / days)) - 1.0) * 100.0, 2)
        avg_duration = (
            round(sum(t.duration_bars for t in closed_trades) / len(closed_trades), 1) if closed_trades else 0.0
        )

        winners = sum(1 for t in closed_trades if (t.pnl_dollars or 0.0) > 0.0)
        losers = len(closed_trades) - winners

        total_commissions = round(sum(t.commission for t in closed_trades), 2)
        total_slippage = round(sum(t.slippage_dollars for t in closed_trades), 2)
        gross_strategy_pnl = round(strategy_pnl + total_commissions + total_slippage, 2)

        result = BacktestResult(
            starting_cash=self.initial_cash,
            ending_equity=ending_equity,
            strategy_pnl=round(strategy_pnl, 2),
            strategy_return_pct=strategy_return_pct,
            cash_yield_pnl=cash_yield_pnl,
            combined_total_pnl=combined_total_pnl,
            combined_return_pct=combined_return_pct,
            total_trades=len(closed_trades),
            winning_trades=winners,
            losing_trades=losers,
            win_rate=win_rate,
            profit_factor=profit_factor,
            max_drawdown_pct=max_dd_pct,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            annualized_return_pct=annualized_return,
            avg_trade_duration_bars=avg_duration,
            trades=closed_trades,
            equity_curve=equity_points,
            gross_strategy_pnl=gross_strategy_pnl,
            total_commissions=total_commissions,
            total_slippage=total_slippage,
        )
        if enable_attribution and closed_trades:
            result.attribution = calculate_performance_attribution(result, vix_df=vix_df)
        return result
