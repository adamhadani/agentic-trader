from agentic_trader.research.models import OptimizationResult


def format_optimization_report(result: OptimizationResult, top_n: int = 5) -> str:
    """Format an institutional ASCII summary report of parameter optimization results."""
    border = "=" * 88
    sub_border = "-" * 88

    candidates = result.ranked_candidates[:top_n]
    table_rows = []
    for rank, c in enumerate(candidates, start=1):
        param_str = ", ".join(f"{k}={v}" for k, v in c.parameters.items())
        table_rows.append(
            f"#{rank:<2d} | {param_str:<26s} | {c.total_return_pct:+7.2f}% | {c.win_rate:6.2f}% | "
            f"{c.sharpe_ratio:6.2f} | {c.max_drawdown_pct:6.2f}% | {c.profit_factor:5.2f} | {c.total_trades:5d}"
        )

    rows_text = "\n".join(table_rows) if table_rows else "No valid parameter combinations evaluated."

    best_candidate = candidates[0] if candidates else None
    recommendation_text = ""
    if best_candidate:
        param_desc = ", ".join(f"{k}={v}" for k, v in best_candidate.parameters.items())
        recommendation_text = f"""
{sub_border}
RECOMMENDED CONFIGURATION TUNING (Top Sharpe Ratio: {best_candidate.sharpe_ratio:.2f})
{sub_border}
Parameters: {param_desc}
Performance: Total Return: {best_candidate.total_return_pct:+.2f}%, Win Rate: {best_candidate.win_rate:.2f}%, Max DD: {best_candidate.max_drawdown_pct:.2f}%
To apply to config.yaml:
  strategies:
    {result.strategy}:
"""
        for k, v in best_candidate.parameters.items():
            recommendation_text += f"      {k}: {v}\n"

    report = f"""
{border}
CASH-PLUS TRADING COPILOT: PARAMETER GRID OPTIMIZATION REPORT
{border}
Asset Symbol:        {result.symbol}
Strategy:            {result.strategy}
Historical Lookback: {result.lookback}
Optimization Engine: {result.engine_used}
Total Configurations Tested: {result.total_combinations_tested}
{sub_border}
TOP {len(candidates)} PARAMETER CONFIGURATIONS (Ranked by Sharpe Ratio & Return)
{sub_border}
Rank | Parameters                 | Return   | WinRate | Sharpe | MaxDD   | P.Fact | Trades
{sub_border}
{rows_text}
{recommendation_text}{border}
"""
    return report.strip()
