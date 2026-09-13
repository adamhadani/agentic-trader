from pathlib import Path
from typing import Any

import yaml

from agentic_trader.research.models import OptimizationResult, ParameterCandidate


def candidate_to_strategy_dict(candidate: ParameterCandidate, strategy: str) -> dict[str, Any]:
    """Convert a ParameterCandidate's parameters into the strategy config schema."""
    out: dict[str, Any] = {"enabled": True}
    if strategy == "trend_pullback":
        if "ema_span" in candidate.parameters:
            out["trigger_ema_span"] = candidate.parameters["ema_span"]
        if "rsi_threshold" in candidate.parameters:
            thresh = float(candidate.parameters["rsi_threshold"])
            out["rsi_oversold"] = thresh
            out["rsi_oversold_dip"] = thresh + 5.0
            out["rsi_overbought"] = 100.0 - thresh
            out["rsi_overbought_surge"] = 100.0 - thresh - 5.0
    elif strategy == "squeeze_breakout":
        if "volume_factor" in candidate.parameters:
            out["volume_factor"] = float(candidate.parameters["volume_factor"])
        if "min_squeeze_bars" in candidate.parameters:
            out["min_squeeze_bars"] = int(candidate.parameters["min_squeeze_bars"])
    else:
        out.update(candidate.parameters)
    return out


def format_candidate_as_yaml(candidate: ParameterCandidate, strategy: str) -> str:
    """Format candidate parameters as a valid YAML snippet suitable for config/config.yaml."""
    cfg_data = {"strategies": {strategy: candidate_to_strategy_dict(candidate, strategy)}}
    return yaml.dump(cfg_data, sort_keys=False)


def export_candidate_to_config(candidate: ParameterCandidate, strategy: str, config_path: str) -> bool:
    """Export and update strategy config in an existing or new config.yaml file."""
    path = Path(config_path)
    existing: dict[str, Any] = {}
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            existing = yaml.safe_load(f) or {}

    strategies = existing.setdefault("strategies", {})
    strat_cfg = strategies.setdefault(strategy, {})
    strat_cfg.update(candidate_to_strategy_dict(candidate, strategy))

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(existing, f, sort_keys=False)
    return True


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
{format_candidate_as_yaml(best_candidate, result.strategy)}"""

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
