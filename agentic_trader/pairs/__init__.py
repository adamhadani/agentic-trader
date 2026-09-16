"""Statistical Pairs Trading & Cointegration Screener Package."""

from agentic_trader.pairs.cointegration import (
    calculate_rolling_spread_zscore,
    compute_half_life,
    generate_spread_signal,
    run_engle_granger_test,
)
from agentic_trader.pairs.models import (
    CointegrationResult,
    PairEvaluation,
    SignalType,
    SpreadSignal,
)
from agentic_trader.pairs.reporting import (
    format_pairs_report,
    format_pairs_telegram,
)
from agentic_trader.pairs.screener import PairsScreener


__all__ = [
    "CointegrationResult",
    "PairEvaluation",
    "PairsScreener",
    "SignalType",
    "SpreadSignal",
    "calculate_rolling_spread_zscore",
    "compute_half_life",
    "format_pairs_report",
    "format_pairs_telegram",
    "generate_spread_signal",
    "run_engle_granger_test",
]
