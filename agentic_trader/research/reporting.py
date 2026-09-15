from pathlib import Path
from typing import Any

import yaml

from agentic_trader.constants import APP_DISPLAY_NAME
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
    border = "=" * 96
    sub_border = "-" * 96

    candidates = result.ranked_candidates[:top_n]
    table_rows = []

    if result.is_walk_forward:
        for rank, c in enumerate(candidates, start=1):
            param_str = ", ".join(f"{k}={v}" for k, v in c.parameters.items())
            is_ret = f"{c.is_return_pct:+6.1f}%" if c.is_return_pct is not None else "   N/A"
            oos_ret = f"{c.oos_return_pct:+6.1f}%" if c.oos_return_pct is not None else "   N/A"
            wfe = f"{c.wfe_ratio:5.2f}" if c.wfe_ratio is not None else "  N/A"
            oos_sharpe = f"{c.oos_sharpe:6.2f}" if c.oos_sharpe is not None else f"{c.sharpe_ratio:6.2f}"
            table_rows.append(
                f"#{rank:<2d} | {param_str:<24s} | {is_ret} | {oos_ret} | {wfe} | {oos_sharpe} | "
                f"{c.max_drawdown_pct:5.1f}% | {c.profit_factor:5.2f} | {c.total_trades:4d}"
            )
    else:
        for rank, c in enumerate(candidates, start=1):
            param_str = ", ".join(f"{k}={v}" for k, v in c.parameters.items())
            table_rows.append(
                f"#{rank:<2d} | {param_str:<26s} | {c.total_return_pct:+7.2f}% | {c.win_rate:6.2f}% | "
                f"{c.sharpe_ratio:6.2f} | {c.max_drawdown_pct:6.2f}% | {c.profit_factor:5.2f} | {c.total_trades:5d}"
            )

    rows_text = "\n".join(table_rows) if table_rows else "No valid parameter combinations evaluated."

    folds_section = ""
    if result.is_walk_forward and result.walk_forward_folds:
        fold_rows = []
        for f in result.walk_forward_folds:
            p_str = ", ".join(f"{k}={v}" for k, v in f.best_parameters.items())
            fold_rows.append(
                f"#{f.fold_index}   | {f.train_start} -> {f.train_end} | {f.test_start} -> {f.test_end} | "
                f"{f.train_return_pct:+7.1f}% | {f.test_return_pct:+7.1f}% | {f.wfe_ratio:6.2f} | {p_str}"
            )
        folds_table = "\n".join(fold_rows)
        folds_section = f"""
{sub_border}
WALK-FORWARD OUT-OF-SAMPLE CROSS-VALIDATION FOLDS (Anchor/Expanding Windows)
{sub_border}
Fold | In-Sample Train Window  | Out-of-Sample Test Window | Train Ret | Test Ret  | WFE    | Winning Parameters
{sub_border}
{folds_table}
"""

    best_candidate = candidates[0] if candidates else None
    recommendation_text = ""
    if best_candidate:
        param_desc = ", ".join(f"{k}={v}" for k, v in best_candidate.parameters.items())
        perf_summary = f"Total Return: {best_candidate.total_return_pct:+.2f}%, Win Rate: {best_candidate.win_rate:.2f}%, Max DD: {best_candidate.max_drawdown_pct:.2f}%"
        if result.is_walk_forward and best_candidate.wfe_ratio is not None:
            wfe_status = (
                "PASS (Robust Out-of-Sample Edge)"
                if best_candidate.wfe_ratio >= 0.50
                else "WARNING (Marginal / Suspected Overfitting)"
            )
            perf_summary += f"\nWalk-Forward Efficiency: {best_candidate.wfe_ratio:.2f} -> {wfe_status}"

        top_score_label = (
            f"Top Out-of-Sample Sharpe: {best_candidate.oos_sharpe:.2f}"
            if result.is_walk_forward and best_candidate.oos_sharpe is not None
            else f"Top Sharpe Ratio: {best_candidate.sharpe_ratio:.2f}"
        )
        recommendation_text = f"""
{sub_border}
RECOMMENDED CONFIGURATION TUNING ({top_score_label})
{sub_border}
Parameters: {param_desc}
Performance: {perf_summary}
To apply to config.yaml:
{format_candidate_as_yaml(best_candidate, result.strategy)}"""

    wf_status_line = ""
    table_header = (
        "Rank | Parameters               | IS Ret | OOS Ret | WFE   | OOS Sh | MaxDD  | P.Fact | Trades"
        if result.is_walk_forward
        else "Rank | Parameters                 | Return   | WinRate | Sharpe | MaxDD   | P.Fact | Trades"
    )
    if result.is_walk_forward:
        avg_wfe_str = f"{result.avg_wfe_ratio:.2f}" if result.avg_wfe_ratio is not None else "N/A"
        wf_status_line = (
            f"\nWalk-Forward Validation: ACTIVE (Folds: {len(result.walk_forward_folds)}, Avg WFE: {avg_wfe_str})"
        )

    title = (
        "WALK-FORWARD PARAMETER OPTIMIZATION REPORT" if result.is_walk_forward else "PARAMETER GRID OPTIMIZATION REPORT"
    )
    report = f"""
{border}
{APP_DISPLAY_NAME.upper()}: {title}
{border}
Asset Symbol:        {result.symbol}
Strategy:            {result.strategy}
Historical Lookback: {result.lookback}
Optimization Engine: {result.engine_used}
Total Configurations Tested: {result.total_combinations_tested}{wf_status_line}{folds_section}
{sub_border}
TOP {len(candidates)} PARAMETER CONFIGURATIONS (Ranked by Out-of-Sample Sharpe & Robustness)
{sub_border}
{table_header}
{sub_border}
{rows_text}
{recommendation_text}{border}
"""
    return report.strip()
