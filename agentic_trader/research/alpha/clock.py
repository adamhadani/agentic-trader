"""One definition-aware observation boundary for live screening and shadow reports."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

from agentic_trader.market.bars import (
    BAR_DURATIONS,
    SessionClockPolicy,
    completed_fixed_bars,
    fixed_bar_closes,
    utc_timestamp,
)
from agentic_trader.research.alpha.strategy import TIMEFRAME_FIELDS


if TYPE_CHECKING:
    from agentic_trader.data.market_data import ContractMarketData
    from agentic_trader.research.alpha.models import AlphaDefinition


class AlphaClockRejection(ValueError):
    """Keep an observed candle's identity even when its decision is rejected."""

    def __init__(self, reason: str, frame: pd.DataFrame, closed_at: pd.Timestamp):
        super().__init__(reason)
        timestamp = frame.index[-1]
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        self.observation = {"candle_timestamp": timestamp.isoformat(), "completed_at": closed_at.isoformat()}


def closed_alpha_bars(
    definition: AlphaDefinition, data: ContractMarketData, *, as_of: datetime, require_verified: bool = False
) -> tuple[pd.DataFrame, pd.Timestamp]:
    now = utc_timestamp(as_of)
    if definition.clock is None:
        frame = completed_fixed_bars(
            getattr(data, TIMEFRAME_FIELDS[definition.timeframe]), definition.timeframe, as_of=now
        )
        if frame.empty:
            raise ValueError("missing_timeframe")
        closed_at = fixed_bar_closes(frame, definition.timeframe)[-1]
        if (require_verified or definition.data_feed != "unverified") and now - closed_at > BAR_DURATIONS[
            definition.timeframe
        ]:
            raise AlphaClockRejection("stale_closed_bar", frame, closed_at)
    else:
        snapshot = data.session_bars.get(definition.timeframe)
        if snapshot is None:
            raise ValueError("missing_session_snapshot")
        if snapshot.symbol != (data.symbol or data.contract):
            raise ValueError("session_snapshot_symbol_mismatch")
        bars = snapshot.bars
        bars.validate(definition.timeframe)
        if snapshot.received_at > now:
            raise ValueError("future_session_receipt")
        if (bars.closed_at > snapshot.received_at).any():
            raise ValueError("session_close_after_receipt")
        if not isinstance(definition.clock, SessionClockPolicy):
            raise ValueError("Live/session observation requires the regular-session clock")
        available, expires = definition.clock.windows(bars.closed_at)
        if bars.signals.empty:
            raise ValueError("missing_timeframe")
        location = int(available.searchsorted(now, side="right")) - 1
        if location < 0:
            raise AlphaClockRejection("session_decision_not_available", bars.signals, bars.closed_at[-1])
        # Only the most recent eligible decision wins. Later observed bars may
        # still be inside their delay; they cannot enter this score's prefix.
        frame, closed_at = bars.signals.iloc[: location + 1].copy(), bars.closed_at[location]
        if now >= expires[location]:
            raise AlphaClockRejection("session_decision_expired", frame, closed_at)
    if (require_verified or definition.data_feed != "unverified") and (
        frame.attrs.get("feed") != definition.data_feed or frame.attrs.get("adjustment") != definition.adjustment
    ):
        raise AlphaClockRejection("deployment_data_contract_mismatch", frame, closed_at)
    return frame, closed_at
