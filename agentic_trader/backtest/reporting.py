from agentic_trader.backtest.models import BacktestResult


def format_backtest_report(result: BacktestResult, symbols: list[str], lookback: str, strategy: str = "all") -> str:
    """Format an institutional ASCII summary report of backtest performance."""
    border = "=" * 70
    sub_border = "-" * 70

    top_trades_text = ""
    if result.trades:
        sorted_trades = sorted(result.trades, key=lambda t: t.pnl_dollars or 0.0, reverse=True)
        top_winners = sorted_trades[:3]
        top_losers = sorted_trades[-3:] if len(sorted_trades) > 3 else []

        top_trades_text = "\nTop Performing Trades:\n"
        for t in top_winners:
            top_trades_text += (
                f"  • {t.symbol:6s} {t.direction.value:5s} | PnL: ${t.pnl_dollars:+8.2f} ({t.pnl_pct:+5.2f}%) "
                f"via {t.strategy.value} ({t.duration_bars} bars)\n"
            )

        if top_losers and any((t.pnl_dollars or 0.0) < 0.0 for t in top_losers):
            top_trades_text += "Worst Performing Trades:\n"
            for t in reversed(top_losers):
                top_trades_text += (
                    f"  • {t.symbol:6s} {t.direction.value:5s} | PnL: ${t.pnl_dollars:+8.2f} ({t.pnl_pct:+5.2f}%) "
                    f"via {t.strategy.value} ({t.duration_bars} bars)\n"
                )

    report = f"""
{border}
CASH-PLUS TRADING COPILOT: QUANTITATIVE BACKTEST REPORT
{border}
Universe:            {", ".join(symbols)}
Lookback Period:     {lookback}
Strategy Filter:     {strategy}
Starting Cash Base:  ${result.starting_cash:,.2f}
Ending Portfolio:    ${result.ending_equity:,.2f}
{sub_border}
PORTFOLIO & CASH-PLUS PERFORMANCE ATTRIBUTION
{sub_border}
• Pure Strategy Alpha P&L:      ${result.strategy_pnl:+12,.2f}  ({result.strategy_return_pct:+.2f}%)
• Treasury/Cash Reserve Yield:  ${result.cash_yield_pnl:+12,.2f}
• Combined Total Net Return:    ${result.combined_total_pnl:+12,.2f}  ({result.combined_return_pct:+.2f}%)
• Annualized Return (CAGR):     {result.annualized_return_pct:+.2f}%
{sub_border}
RISK-ADJUSTED METRICS & DRAWDOWN
{sub_border}
• Sharpe Ratio:                 {result.sharpe_ratio:12.2f}
• Sortino Ratio:                {result.sortino_ratio:12.2f}
• Maximum Drawdown:             {result.max_drawdown_pct:12.2f}%
• Profit Factor:                {result.profit_factor:12.2f}
{sub_border}
TRADE STATISTICS
{sub_border}
• Total Executed Trades:        {result.total_trades:12d}
• Winning Trades:               {result.winning_trades:12d}
• Losing Trades:                {result.losing_trades:12d}
• Win Rate:                     {result.win_rate:11.2f}%
• Average Duration:             {result.avg_trade_duration_bars:11.1f} bars
{top_trades_text}{border}
"""
    return report.strip()
