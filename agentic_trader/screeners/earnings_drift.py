"""PEAD drift candidates for the 10:35 suggestion scan (catalog paper probe).

Events come from the study's own ``build_events`` via ``live_events``; the bracket is
the catalog policy (``apriori_bracket_v1``), never re-priced and never trailed. The
LLM writes commentary only (see ``RiskEvaluator``); ranking is by reaction z, never
``setup_quality``.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from agentic_trader.constants import AssetClass
from agentic_trader.execution.lifetime_policy import SessionLifetimePolicy
from agentic_trader.research.apriori.catalog import load_pead_entry
from agentic_trader.research.apriori.pead_live import live_events
from agentic_trader.research.apriori.probe import catalog_alpha_id
from agentic_trader.screeners.base import ScreenerCandidate
from agentic_trader.screeners.indicators import calculate_ema, calculate_rsi


__all__ = [
    "PEAD_STRATEGY_ID",
    "DriftPreparation",
    "EarningsDriftService",
    "drift_card_facts",
]

logger = logging.getLogger(__name__)

PEAD_STRATEGY_ID = catalog_alpha_id("pead", "LONG")
NEW_YORK = ZoneInfo("America/New_York")
# Calendar days looked ahead from the decision session; comfortably covers the frozen
# 20-session hold (session_count_v1) even across long holiday clusters.
_EXIT_CALENDAR_DAYS = 45


@dataclass(frozen=True)
class DriftPreparation:
    status: str
    session: date
    version_id: str | None = None
    policy: dict | None = None
    events: tuple[dict, ...] = ()
    skipped: dict[str, str] = field(default_factory=dict)
    counts: dict = field(default_factory=dict)
    reason: str | None = None
    report_date: str | None = None
    page_sha256: str | None = None
    time_exit_at: str | None = None


def _finite(value: Any, default: float) -> float:
    number = float(value)
    return number if math.isfinite(number) else default


class EarningsDriftService:
    """Decides whether the PEAD long leg produces a drift card for this scan."""

    def __init__(
        self,
        entry_path: Path,
        *,
        earnings,
        calendar,
        bars,
        static_symbols: Sequence[str],
    ):
        self.loaded = load_pead_entry(Path(entry_path))
        self.earnings = earnings
        self.calendar = calendar
        self.bars = bars
        self.static_symbols = tuple(static_symbols)

    @property
    def decision_time_et(self) -> str:
        return self.loaded.entry.trade.decision_time_et

    @property
    def holding_sessions(self) -> int:
        return self.loaded.entry.trade.max_hold_sessions

    def applies(self, scheduled_time_et: str | None) -> bool:
        return scheduled_time_et == self.decision_time_et

    def live_version(self, snapshot) -> dict | None:
        for definition in getattr(snapshot, "catalog_probes", ()):
            if definition.get("entry_sha256") == self.loaded.sha256 and definition.get("leg") == "LONG":
                return definition
        return None

    async def _time_exit(self, now: datetime, policy: dict) -> str | None:
        try:
            lifetime = SessionLifetimePolicy(**policy["lifetime"])
            start = now.astimezone(NEW_YORK).date()
            days = await self.calendar.get_calendar_range(start, start + timedelta(days=_EXIT_CALENDAR_DAYS))
            trading = [day for day in days if day.is_trading_day]
            return lifetime.holding_deadline(now, trading).isoformat()
        except Exception:
            # The card shows the frozen hold length regardless; the running service
            # (never this preview) owns the real deadline once the order is filled.
            logger.warning("PEAD time exit unavailable", extra={"event": "pead_time_exit_unavailable"})
            return None

    async def prepare(self, *, now: datetime, snapshot, owned: Mapping[str, str]) -> DriftPreparation:
        session = now.astimezone(NEW_YORK).date()
        version = self.live_version(snapshot)
        if version is None:
            return DriftPreparation("inactive", session, reason="no live catalog probe for this entry")
        result = await live_events(
            self.loaded.entry,
            session,
            earnings=self.earnings,
            calendar=self.calendar,
            bars=self.bars,
            static_symbols=self.static_symbols,
        )
        common = {
            "version_id": version["version_id"],
            "policy": version["execution"],
            "counts": result.counts,
            "report_date": result.report_date.isoformat() if result.report_date else None,
            "page_sha256": result.page.body_sha256 if result.page else None,
        }
        if result.status != "ok":
            return DriftPreparation("unavailable", session, reason=result.reason, **common)
        events, skipped = [], {}
        for event in result.events:
            reason = owned.get(event["symbol"])
            if reason:
                skipped[event["symbol"]] = reason
            else:
                events.append(event)
        return DriftPreparation(
            "ok",
            session,
            events=tuple(events),
            skipped=skipped,
            time_exit_at=await self._time_exit(now, version["execution"]),
            **common,
        )

    def candidate(self, event: dict, data: Any, prep: DriftPreparation) -> ScreenerCandidate | None:
        if data is None or isinstance(data, BaseException):
            return None
        intraday = getattr(data, "hourly", None)
        if intraday is None or intraday.empty:
            intraday = getattr(data, "four_hour", None)
        daily = getattr(data, "daily", None)
        if intraday is None or intraday.empty or daily is None or len(daily) < 20:
            return None
        price = float(intraday["Close"].iloc[-1])
        if not math.isfinite(price) or price <= 0:
            return None
        close = daily["Close"]
        exit_text = f"time exit {prep.time_exit_at[:10]} 15:45 NY" if prep.time_exit_at else "20-session time exit"
        return ScreenerCandidate(
            contract=event["symbol"],
            symbol=event["symbol"],
            asset_class=AssetClass.EQUITY,
            timeframe="1d",
            strategy=PEAD_STRATEGY_ID,
            direction="LONG",
            current_price=price,
            ema_20=_finite(calculate_ema(close, 20).iloc[-1], price),
            ema_50=_finite(calculate_ema(close, 50).iloc[-1], price),
            ema_200=_finite(calculate_ema(close, 200).iloc[-1], price),
            rsi_14=_finite(calculate_rsi(close, 14).iloc[-1], 50.0),
            atr_14=float(event["atr"]),
            candle_timestamp=str(intraday.index[-1]),
            recent_swing_low=float(daily["Low"].tail(10).min()),
            recent_swing_high=float(daily["High"].tail(10).max()),
            trigger_detail=(
                f"PEAD probe: EPS beat {event['surprise_pct']:+.1f}%, reaction {event['z']:+.1f}σ vs SPY "
                f"(report {event['report_date']}); 20-session hold, {exit_text}."
            ),
            alpha_version=prep.version_id,
            alpha_policy=prep.policy,
            probe=True,
            setup_quality=0.0,
            catalog_event=event,
        )


def drift_card_facts(event: dict, prep: DriftPreparation, holding_sessions: int) -> dict:
    return {
        "surprise_pct": event["surprise_pct"],
        "z": event["z"],
        "report_date": event["report_date"],
        "holding_sessions": holding_sessions,
        "time_exit_at": prep.time_exit_at,
    }
