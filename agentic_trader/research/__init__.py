"""Quantitative research and parameter grid optimization package."""

from agentic_trader.research.models import OptimizationResult, ParameterCandidate
from agentic_trader.research.optimizer import ParameterGridOptimizer
from agentic_trader.research.reporting import (
    export_candidate_to_config,
    format_candidate_as_yaml,
    format_optimization_report,
)


__all__ = [
    "OptimizationResult",
    "ParameterCandidate",
    "ParameterGridOptimizer",
    "export_candidate_to_config",
    "format_candidate_as_yaml",
    "format_optimization_report",
]
