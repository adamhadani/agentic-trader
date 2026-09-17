"""Explicit partial acquisition boundary for diagnostic daily studies."""

from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from agentic_trader.market.bars import utc_timestamp
from agentic_trader.market.session import ET_TZ


class DailyAcquisitionError(ValueError):
    """A complete-input study cannot compute around unavailable members."""


@dataclass(frozen=True)
class DailyStudyInputs:
    frames: dict[str, pd.DataFrame]
    failures: dict[str, dict] = field(default_factory=dict)

    def require_complete(self, symbols: tuple[str, ...]) -> dict[str, pd.DataFrame]:
        if self.failures or set(self.frames) != set(symbols):
            raise DailyAcquisitionError("Daily study requires every frozen member; inspect acquisition checkpoints")
        return self.frames


def validate_daily_window(end: date, now) -> None:
    if utc_timestamp(now) < pd.Timestamp(end + timedelta(days=1), tz=ET_TZ):
        raise ValueError("Study requires fully elapsed historical native daily windows")
