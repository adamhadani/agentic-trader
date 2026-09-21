"""Pure strategy contract shared by research and formulaic live screening.

Signals use completed observations. Research execution occurs at the following
bar with a resting GTC limit; actual fills, admission filters and intra-bar trailing remain execution
observations, never fabricated backtest promises.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from agentic_trader.execution.lifetime_policy import TradeLifetimePolicy, daily_entry_lifetime
from agentic_trader.research.alpha.dsl import AlphaExpressionEvaluator


if TYPE_CHECKING:
    from agentic_trader.research.alpha.models import AlphaDefinition

NORMALIZATION_WINDOW = 30
TIMEFRAME_FIELDS = {"15m": "fifteen_minute", "1h": "hourly", "4h": "four_hour", "1d": "daily"}
# US regular-session sampling: the final intraday bar may be short.


@dataclass(frozen=True)
class AlphaExecutionPolicy:
    stop_atr: float = 1.5
    atr_method: str = "simple_true_range"
    reward_risk: float = 2.0
    swing_window: int = 10
    atr_window: int = 14
    tick_size: float = 0.01
    structural_buffer_ticks: int = 2
    friction_per_side: float = 0.0005
    # Initial protection is immutable; optional live trailing uses initial risk.
    trail_trigger_r: float = 1.5
    trail_distance_r: float = 1.5

    def __post_init__(self):
        if self.atr_method != "simple_true_range":
            raise ValueError("Unsupported alpha ATR contract")
        for name in ("stop_atr", "reward_risk", "tick_size", "trail_trigger_r", "trail_distance_r"):
            if not math.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f"Invalid execution policy {name}")
        if not 0 <= self.friction_per_side < 0.1:
            raise ValueError("Invalid friction")
        if any(
            type(getattr(self, name)) is not int for name in ("swing_window", "atr_window", "structural_buffer_ticks")
        ):
            raise ValueError("Execution windows/buffers require integers")
        if (
            not 1 <= self.swing_window <= 252
            or not 2 <= self.atr_window <= 252
            or not 0 <= self.structural_buffer_ticks <= 10
        ):
            raise ValueError("Invalid execution lookback/buffer")

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True, kw_only=True)
class TimedAlphaExecutionPolicy(AlphaExecutionPolicy):
    lifetime: TradeLifetimePolicy

    def __post_init__(self):
        super().__post_init__()
        if not isinstance(self.lifetime, TradeLifetimePolicy):
            raise TypeError("Explicit validated lifetime policy required")


def execution_policy_from_dict(document: dict) -> AlphaExecutionPolicy:
    """Deserialize versioned policy without rewriting historical financial identity."""
    if "lifetime" in document:
        fields = dict(document)
        fields["lifetime"] = TradeLifetimePolicy(**fields["lifetime"])
        return TimedAlphaExecutionPolicy(**fields)
    return AlphaExecutionPolicy(**document)


def session_entry_policy(base: AlphaExecutionPolicy | None = None) -> TimedAlphaExecutionPolicy:
    """Bracket economics of ``base`` with a one-session resting entry and no holding deadline.

    Reads ``base``'s own attributes directly rather than round-tripping through
    ``to_dict()``/``asdict()``, which recurses into nested dataclasses (``lifetime``)
    and would silently hand a future nested-dataclass field back as a plain dict.
    """
    policy = base or AlphaExecutionPolicy()
    values = {f.name: getattr(policy, f.name) for f in dataclasses.fields(AlphaExecutionPolicy)}
    return TimedAlphaExecutionPolicy(**values, lifetime=daily_entry_lifetime())


def normalize_scores(raw: pd.Series, window: int = NORMALIZATION_WINDOW) -> pd.Series:
    mean = raw.rolling(window, min_periods=window).mean()
    std = raw.rolling(window, min_periods=window).std()
    return ((raw - mean) / std.where(std > 0)).replace([np.inf, -np.inf], np.nan)


def alpha_scores(definition: AlphaDefinition, bars: pd.DataFrame, evaluator=None) -> pd.Series:
    raw = (evaluator or AlphaExpressionEvaluator()).evaluate(definition.expression, bars)
    return normalize_scores(raw, definition.normalization_window)


def entry_directions(scores: pd.Series, definition: AlphaDefinition) -> pd.Series:
    result = pd.Series(0, index=scores.index)
    if definition.direction in ("long", "bi_directional"):
        result.loc[scores >= definition.entry_threshold] = 1
    if definition.direction in ("short", "bi_directional"):
        result.loc[scores <= -definition.entry_threshold] = -1
    return result


def bracket_prices(
    entry: float, direction: int, atr: float, swing_low: float, swing_high: float, policy: AlphaExecutionPolicy
) -> tuple[float, float]:
    if direction not in (-1, 1) or not all(math.isfinite(x) and x > 0 for x in (entry, atr, swing_low, swing_high)):
        raise ValueError("Bracket observations must be finite and positive")
    structural = (
        swing_low - policy.structural_buffer_ticks * policy.tick_size
        if direction == 1
        else swing_high + policy.structural_buffer_ticks * policy.tick_size
    )
    distance = max(direction * (entry - structural), policy.stop_atr * atr)
    # Round protection outwards, then the reward outwards to retain the minimum RR.
    ticks = math.ceil(distance / policy.tick_size - 1e-10)
    stop = round(entry - direction * ticks * policy.tick_size, 8)
    target = round(entry + direction * math.ceil(ticks * policy.reward_risk - 1e-10) * policy.tick_size, 8)
    if min(stop, target) <= 0:
        raise ValueError("Bracket would cross zero")
    return stop, target


def trailing_price(
    entry: float, current: float, stop: float, initial_risk: float, direction: int, policy: AlphaExecutionPolicy
) -> float:
    if direction * (current - entry) < policy.trail_trigger_r * initial_risk:
        return stop
    proposed = current - direction * max(initial_risk, policy.trail_distance_r * initial_risk)
    return max(stop, proposed) if direction == 1 else min(stop, proposed)


def strategy_atr(bars: pd.DataFrame, policy: AlphaExecutionPolicy) -> pd.Series:
    """Finite-window ATR gives identical protection with different history prefixes."""
    frame = bars.rename(columns=str.lower)
    previous = frame.close.shift(1)
    ranges = pd.concat([frame.high - frame.low, (frame.high - previous).abs(), (frame.low - previous).abs()], axis=1)
    true_range = ranges.max(axis=1)
    return true_range.rolling(policy.atr_window, min_periods=policy.atr_window).mean()


def entry_limit(price: float, policy: AlphaExecutionPolicy) -> float:
    return round(round(price / policy.tick_size) * policy.tick_size, 8)
