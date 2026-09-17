"""Causal regular-session replay using the shared bracket execution state machine."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from enum import StrEnum

import numpy as np
import pandas as pd

from agentic_trader.market.bars import SESSION_BAR_LAYOUT, SessionBars, utc_timestamp
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.simulation import entry_intents, simulate_execution
from agentic_trader.research.alpha.strategy import alpha_scores


MAX_REPLAY_DAYS = 366
SESSION_REPLAY_KIND = "session_replay"


class ReplayStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


SESSION_REPLAY_VERSION = "observed_session_minutes_v2"


def simulate_session_strategy(
    definition: AlphaDefinition,
    data: SessionBars,
    *,
    scores: pd.Series | None = None,
    start=None,
) -> dict:
    """Evaluate each completed signal once, then execute only observed RTH minutes.

    Delay is an explicit assumption, not measured broker/operator latency. If
    several delayed observations become eligible on one bar, the latest wins.
    Accepted GTC orders follow their immutable lifetime independently of subsequent signals.
    """
    if definition.clock is None:
        raise ValueError("Session replay requires a versioned session clock")
    policy = definition.clock
    data.validate(definition.timeframe)
    signals = data.signals
    if signals.attrs.get("feed") != definition.data_feed or signals.attrs.get("adjustment") != definition.adjustment:
        raise ValueError("Session replay feed/adjustment mismatch")
    if signals.empty or signals.attrs.get("timeframe") != definition.timeframe:
        raise ValueError("Completed signal bars matching the definition are required")
    scores = alpha_scores(definition, signals) if scores is None else scores
    if not scores.index.equals(signals.index) or np.isinf(scores.to_numpy(dtype=float)).any():
        raise ValueError("Finite or unavailable scores must align with completed signal observations")
    observations = entry_intents(definition, signals, scores)
    available, expires = policy.windows(data.closed_at)
    locations = data.execution.index.searchsorted(available)
    eligible = [
        location < len(data.execution) and data.execution.index[location] < expires[i]
        for i, location in enumerate(locations)
    ]
    latest = {int(location): i for i, location in enumerate(locations) if eligible[i]}
    proposals = {location: proposal for location, i in latest.items() if (proposal := observations[i]) is not None}
    start_index = 0 if start is None else int(data.execution.index.searchsorted(utc_timestamp(start)))
    result = simulate_execution(data.execution, proposals, definition.execution, start=start_index, trace=True)
    result.update(
        replay_version=SESSION_REPLAY_VERSION,
        replay_policy=asdict(policy),
        authorizes_promotion=False,
        limitations={
            "market_data": "Historical corrected bars do not establish point-in-time publication/revision availability.",
            "fills": "Full-size OHLC fill hypotheses do not measure spread, queue priority, partial fills or buy-stop conversion.",
            "protection": "Trailing changes are assumed effective on the next execution bar; broker acknowledgment latency is unverified.",
            "costs": "Configured friction excludes corporate actions, dividends, borrow and funding.",
            "lifetimes": "Timed entries cancel instantaneously in simulation; intrabar fill times are known only to the minute bar. Broker latency remains unmeasured.",
            "deployment": "Session decisions run diagnostically; actual execution and qualification evidence remain required before activation.",
        },
        execution_scope="diagnostic_observed_session_minutes",
        coverage=data.coverage,
        signal_feature_coverage={"bars": len(scores), "scored_bars": int(scores.notna().sum())},
        decisions=[
            {
                "signal_bar_start": str(signals.index[i]),
                "closed_at": str(data.closed_at[i]),
                "available_at": str(available[i]),
                "expires_at": str(expires[i]),
                "eligible_bar": str(data.execution.index[locations[i]]) if eligible[i] else None,
                "expired_before_eligibility": bool(locations[i] < len(data.execution) and not eligible[i]),
                "score": float(scores.iloc[i]) if pd.notna(scores.iloc[i]) else None,
                "superseded_before_eligibility": bool(eligible[i] and latest[int(locations[i])] != i),
            }
            for i in range(len(signals))
        ],
    )
    return result


@dataclass(frozen=True)
class ReplayPlan:
    symbol: str
    start: date
    end: date
    definition: AlphaDefinition

    def __post_init__(self):
        if self.definition.clock is None:
            raise ValueError("Replay requires a versioned session clock")
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", self.symbol):
            raise ValueError("Explicit US equity/ETF symbol required")
        if not 0 <= (self.end - self.start).days < MAX_REPLAY_DAYS:
            raise ValueError(f"Replay requires an ordered window of at most {MAX_REPLAY_DAYS} calendar days")
        if self.definition.data_feed not in ("alpaca:iex", "alpaca:sip") or self.definition.adjustment != "raw":
            raise ValueError("Replay requires an explicit raw Alpaca stock feed")
        if self.definition.eligible_symbols != (self.symbol,):
            raise ValueError("Replay definition must name exactly the requested symbol")

    @property
    def start_at(self):
        return pd.Timestamp(self.start, tz=ET_TZ).tz_convert("UTC")

    @property
    def end_at(self):
        return pd.Timestamp(self.end + timedelta(days=1), tz=ET_TZ).tz_convert("UTC")

    def document(self):
        assert self.definition.clock is not None  # Validated at plan construction.
        return {
            "symbol": self.symbol,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "definition": self.definition.to_dict(),
            "replay_policy": asdict(self.definition.clock),
            "replay_version": SESSION_REPLAY_VERSION,
            "bar_layout": SESSION_BAR_LAYOUT,
            "authorizes_promotion": False,
        }

    @property
    def identity(self):
        return hashlib.sha256(json.dumps(self.document(), sort_keys=True, allow_nan=False).encode()).hexdigest()
