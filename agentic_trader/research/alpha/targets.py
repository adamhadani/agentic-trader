"""Explicit forecast endpoints; label prices are outcomes, never predictor inputs."""

from dataclasses import asdict, dataclass
from enum import StrEnum

import numpy as np
import pandas as pd

from agentic_trader.market.bars import BAR_DURATIONS


MAX_FORECAST_HORIZON = 60


class ForecastLabel(StrEnum):
    CLOSE_TO_CLOSE = "close_to_close"
    NEXT_OPEN_TO_CLOSE = "next_open_to_close"


@dataclass(frozen=True)
class ForecastTarget:
    timeframe: str
    horizon_bars: int = 1
    label: ForecastLabel = ForecastLabel.CLOSE_TO_CLOSE

    def __post_init__(self):
        if self.timeframe not in BAR_DURATIONS:
            raise ValueError("Unsupported forecast timeframe")
        if type(self.horizon_bars) is not int or not 1 <= self.horizon_bars <= MAX_FORECAST_HORIZON:
            raise ValueError("Invalid forecast horizon")
        if not isinstance(self.label, ForecastLabel):
            raise TypeError("Explicit supported forecast label required")

    def document(self):
        return {**asdict(self), "label": f"observed_{self.label}_v1"}


def forecast_labels(bars: pd.DataFrame, target: ForecastTarget) -> pd.Series:
    frame = bars.rename(columns=str.lower)
    close = frame.close.where(np.isfinite(frame.close) & (frame.close > 0))
    if target.label == ForecastLabel.NEXT_OPEN_TO_CLOSE:
        opening = frame.open.where(np.isfinite(frame.open) & (frame.open > 0))
        entry = opening.shift(-1)
    else:
        entry = close
    return (close.shift(-target.horizon_bars) / entry - 1).replace([np.inf, -np.inf], np.nan)
