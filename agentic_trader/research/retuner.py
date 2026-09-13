import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentic_trader.config import WORKSPACE_ROOT, AppConfig, load_config
from agentic_trader.constants import (
    DEFAULT_CALIBRATIONS_FILENAME,
    DEFAULT_MIN_OOS_SHARPE,
    DEFAULT_MIN_WFE,
    DEFAULT_OPTIMIZATION_LOOKBACK,
    DEFAULT_WALK_FORWARD_SPLITS,
)
from agentic_trader.data.market_data import ContractMarketData
from agentic_trader.research.models import ParameterCandidate
from agentic_trader.research.optimizer import ParameterGridOptimizer
from agentic_trader.research.reporting import export_candidate_to_config


logger = logging.getLogger(__name__)


class AutoRetuner:
    """Automated parameter re-calibration daemon engine.

    Executes walk-forward out-of-sample optimization on a recurring schedule
    (e.g., weekend market closure), filters overfitted candidates via Walk-Forward
    Efficiency (WFE), and persists robust parameters for live strategy screening.
    """

    def __init__(
        self,
        config: AppConfig | None = None,
        calibrations_path: str | Path | None = None,
    ):
        self.config = config or load_config()
        self.optimizer = ParameterGridOptimizer(self.config)
        filename = getattr(self.config.research, "calibrations_filename", DEFAULT_CALIBRATIONS_FILENAME)
        self.calibrations_path = Path(calibrations_path or (WORKSPACE_ROOT / "data" / filename))

    def run_retune(
        self,
        symbols: list[str] | None = None,
        strategies: list[str] | None = None,
        min_wfe: float = DEFAULT_MIN_WFE,
        min_sharpe: float = DEFAULT_MIN_OOS_SHARPE,
        lookback: str = DEFAULT_OPTIMIZATION_LOOKBACK,
        market_data_map: dict[str, ContractMarketData] | None = None,
    ) -> dict[str, Any]:
        """
        Execute scheduled parameter recalibration across specified symbols and strategies.
        Only candidates meeting both minimum WFE ratio and Out-of-Sample Sharpe thresholds
        are accepted into the calibrated parameters database.
        """
        target_symbols = symbols or list(self.config.contracts.keys())
        if not target_symbols:
            target_symbols = ["SPY", "QQQ", "/MES", "/MNQ"]

        target_strategies = strategies or ["trend_pullback", "squeeze_breakout"]

        calibrations = self.load_calibrations()
        updates_applied = 0
        total_evaluations = 0
        report_lines = []

        logger.info(
            "Starting scheduled parameter retuning across %d symbols and %d strategies...",
            len(target_symbols),
            len(target_strategies),
        )

        for sym in target_symbols:
            for strat in target_strategies:
                total_evaluations += 1
                try:
                    md = market_data_map.get(sym) if market_data_map else None
                    res = self.optimizer.run(
                        symbol=sym,
                        strategy=strat,
                        lookback=lookback,
                        market_data=md,
                        walk_forward=True,
                        splits=getattr(self.config.research, "walk_forward_splits", DEFAULT_WALK_FORWARD_SPLITS),
                    )

                    # Filter candidates meeting robust WFE and OOS Sharpe
                    robust_candidates = [
                        c
                        for c in res.ranked_candidates
                        if c.wfe_ratio is not None
                        and c.wfe_ratio >= min_wfe
                        and (c.oos_sharpe is not None and c.oos_sharpe >= min_sharpe)
                    ]

                    if robust_candidates:
                        winning_candidate = robust_candidates[0]
                        key = f"{sym}:{strat}"
                        calibrations[key] = {
                            "symbol": sym,
                            "strategy": strat,
                            "parameters": winning_candidate.parameters,
                            "wfe_ratio": winning_candidate.wfe_ratio,
                            "oos_sharpe": winning_candidate.oos_sharpe,
                            "oos_return_pct": winning_candidate.oos_return_pct,
                            "calibrated_at": datetime.now(UTC).isoformat(),
                        }
                        updates_applied += 1
                        param_desc = ", ".join(f"{k}={v}" for k, v in winning_candidate.parameters.items())
                        report_lines.append(
                            f"✅ <b>{sym} ({strat})</b>: <code>{param_desc}</code> | "
                            f"WFE: <b>{winning_candidate.wfe_ratio:.2f}</b> | "
                            f"OOS Sharpe: <b>{winning_candidate.oos_sharpe:.2f}</b>"
                        )
                        logger.info(
                            "Calibrated %s (%s): %s (WFE: %.2f, OOS Sharpe: %.2f)",
                            sym,
                            strat,
                            param_desc,
                            winning_candidate.wfe_ratio,
                            winning_candidate.oos_sharpe,
                        )
                    else:
                        logger.info(
                            "No robust configuration found for %s (%s) meeting WFE >= %.2f and Sharpe >= %.2f; retaining prior config.",
                            sym,
                            strat,
                            min_wfe,
                            min_sharpe,
                        )
                except Exception as e:
                    logger.warning("Error during parameter retuning for %s (%s): %s", sym, strat, e)

        # Save calibrations to disk
        self._save_calibrations(calibrations)

        summary_html = (
            f"🤖 <b>WEEKEND STRATEGY AUTO-RETUNING REPORT</b>\n\n"
            f"• <b>Evaluations Tested:</b> {total_evaluations}\n"
            f"• <b>Configurations Updated:</b> {updates_applied}\n"
            f"• <b>Minimum WFE Threshold:</b> {min_wfe:.2f}\n"
            f"• <b>Minimum OOS Sharpe:</b> {min_sharpe:.2f}\n\n"
        )
        if report_lines:
            summary_html += "<b>Calibrated Setups:</b>\n" + "\n".join(report_lines)
        else:
            summary_html += "<i>No parameters met robustness thresholds. Existing settings retained.</i>"

        return {
            "calibrations": calibrations,
            "total_tested": total_evaluations,
            "total_updated": updates_applied,
            "summary_html": summary_html,
        }

    def load_calibrations(self) -> dict[str, Any]:
        """Load currently calibrated parameters from data/calibrated_parameters.json."""
        if not self.calibrations_path.exists():
            return {}
        try:
            with open(self.calibrations_path, "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception as e:
            logger.warning("Could not read calibrations from %s: %s", self.calibrations_path, e)
            return {}

    def _save_calibrations(self, data: dict[str, Any]) -> None:
        """Persist calibrated parameters to disk."""
        self.calibrations_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.calibrations_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def export_all_to_config(self, target_config_path: str) -> int:
        """Export all currently calibrated parameters directly into a config.yaml file."""
        calibrations = self.load_calibrations()
        exported = 0
        for item in calibrations.values():
            strat = item.get("strategy")
            params = item.get("parameters")
            if strat and params:
                cand = ParameterCandidate(
                    parameters=params,
                    total_return_pct=item.get("oos_return_pct", 0.0),
                    win_rate=0.0,
                    profit_factor=0.0,
                    sharpe_ratio=item.get("oos_sharpe", 0.0),
                    max_drawdown_pct=0.0,
                    total_trades=0,
                )
                export_candidate_to_config(cand, strat, target_config_path)
                exported += 1
        return exported
