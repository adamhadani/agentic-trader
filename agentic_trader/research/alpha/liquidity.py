"""Past-only source-specific liquidity screens over an immutable current cohort."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any
from uuid import UUID

import pandas as pd

from agentic_trader.market.bars import FIXED_BAR_LAYOUT, SessionSchedule, utc_timestamp
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.alpha.daily_inputs import DailyAcquisitionError, DailyStudyInputs, validate_daily_window
from agentic_trader.research.alpha.equity_universe import (
    CLASSIFICATION,
    MAX_CANDIDATES,
    candidate_symbols,
    document_hash,
    verify_snapshot,
)
from agentic_trader.research.alpha.panel import align_daily_panel
from agentic_trader.research.alpha.panel_study import PanelStudyStatus


LIQUIDITY_STUDY_VERSION = "equity_liquidity_screen_v1"
MAX_LIQUIDITY_DAYS = 90
MAX_LIQUIDITY_SELECTION = 64
MAX_LIQUIDITY_LOOKBACK = 60


@dataclass(frozen=True)
class LiquidityMember:
    asset_id: str
    symbol: str
    instrument_subtype: str

    def __post_init__(self):
        if (
            str(UUID(self.asset_id)) != self.asset_id
            or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", self.symbol)
            or self.instrument_subtype != "unknown"
        ):
            raise ValueError("Exact current asset identity and explicit unknown subtype required")


def _snapshot_members(snapshot: dict) -> tuple[LiquidityMember, ...]:
    verify_snapshot(snapshot)
    candidate_symbols(snapshot, at=utc_timestamp(snapshot["observed_at"]))
    selected = snapshot["selected"]
    members = [row for row in snapshot["members"] if row["selection"] == "selected"]
    if (
        not 1 <= len(selected) <= MAX_CANDIDATES
        or snapshot["selected_count"] != len(selected)
        or len({row["asset_id"] for row in selected}) != len(selected)
        or len({row["symbol"] for row in selected}) != len(selected)
        or len(members) != len(selected)
        or {(row["asset_id"], row["symbol"]) for row in selected}
        != {(row["asset_id"], row["symbol"]) for row in members}
        or any(row["reasons"] or row["classification"] != CLASSIFICATION for row in members)
    ):
        raise ValueError("Every selected snapshot asset must have its exact eligible UUID/symbol identity")
    return tuple(
        sorted(
            (LiquidityMember(row["asset_id"], row["symbol"], row["instrument_subtype"]) for row in members),
            key=lambda row: row.asset_id,
        )
    )


@dataclass(frozen=True)
class EquityLiquidityPlan:
    campaign_id: str
    snapshot_id: str
    members: tuple[LiquidityMember, ...]
    snapshot_observed_at: pd.Timestamp
    start: date
    end: date
    feed: str
    lookback: int = 20
    selection_size: int = 32
    price_floor: float = 5.0
    min_median_dollar_volume: float = 0.0
    min_positive_volume_sessions: int = 20

    def __post_init__(self):
        if (
            not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.campaign_id)
            or not re.fullmatch(r"[a-f0-9]{64}", self.snapshot_id)
            or not isinstance(self.members, tuple)
            or not 1 <= len(self.members) <= MAX_CANDIDATES
            or any(not isinstance(m, LiquidityMember) for m in self.members)
            or len({m.asset_id for m in self.members}) != len(self.members)
            or len({m.symbol for m in self.members}) != len(self.members)
            or tuple(sorted(self.members, key=lambda m: m.asset_id)) != self.members
            or type(self.start) is not date
            or type(self.end) is not date
            or not 0 <= (self.end - self.start).days < MAX_LIQUIDITY_DAYS
            or self.feed not in ("alpaca:iex", "alpaca:sip")
            or type(self.lookback) is not int
            or not 2 <= self.lookback <= MAX_LIQUIDITY_LOOKBACK
            or type(self.selection_size) is not int
            or not 1 <= self.selection_size <= MAX_LIQUIDITY_SELECTION
            or type(self.min_positive_volume_sessions) is not int
            or not 1 <= self.min_positive_volume_sessions <= self.lookback
        ):
            raise ValueError("Explicit bounded equity liquidity protocol required")
        for name, minimum in (("price_floor", 0), ("min_median_dollar_volume", 0)):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < minimum
                or (name == "price_floor" and value == 0)
            ):
                raise ValueError("Finite nonnegative liquidity and positive price policy required")
            object.__setattr__(self, name, float(value))
        object.__setattr__(self, "snapshot_observed_at", utc_timestamp(self.snapshot_observed_at))

    @classmethod
    def from_snapshot(cls, snapshot: dict, **policy):
        return cls(
            snapshot_id=snapshot["snapshot_id"],
            members=_snapshot_members(snapshot),
            snapshot_observed_at=utc_timestamp(snapshot["observed_at"]),
            **policy,
        )

    @property
    def acquisition_symbols(self):
        return tuple(sorted(member.symbol for member in self.members))

    @property
    def adjustment(self):
        return "raw"

    @property
    def trial_count(self):
        return 1

    def validate_as_of(self, now):
        observed = utc_timestamp(now)
        if observed < self.snapshot_observed_at:
            raise ValueError("Current cohort cannot be observed before its actual snapshot receipt")
        validate_daily_window(self.end, observed)

    def document(self):
        return {
            "version": LIQUIDITY_STUDY_VERSION,
            "campaign_id": self.campaign_id,
            "snapshot_id": self.snapshot_id,
            "snapshot_observed_at": self.snapshot_observed_at.isoformat(),
            "cohort_members_hash": document_hash({"members": [asdict(member) for member in self.members]}),
            "cohort_size": len(self.members),
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "feed": self.feed,
            "timeframe": "1d",
            "adjustment": self.adjustment,
            "bar_layout": FIXED_BAR_LAYOUT,
            "lookback": self.lookback,
            "selection_size": self.selection_size,
            "price_floor": self.price_floor,
            "min_median_dollar_volume": self.min_median_dollar_volume,
            "min_positive_volume_sessions": self.min_positive_volume_sessions,
            "ranking": "median_close_times_volume_descending_asset_uuid_tiebreak",
            "price_filter": "latest_completed_close",
            "window": "last_observed_exchange_dates_through_end",
            "incomplete_acquisition": "withhold_entire_selection",
            "scope": "current_cohort_development_and_future_research_only",
            "charged_trials": self.trial_count,
            "authorizes_promotion": False,
            "point_in_time_historical_membership": False,
        }

    @classmethod
    def from_document(cls, document: dict, snapshot: dict):
        try:
            plan = cls.from_snapshot(
                snapshot,
                campaign_id=document["campaign_id"],
                start=date.fromisoformat(document["start"]),
                end=date.fromisoformat(document["end"]),
                **{
                    name: document[name]
                    for name in (
                        "feed",
                        "lookback",
                        "selection_size",
                        "price_floor",
                        "min_median_dollar_volume",
                        "min_positive_volume_sessions",
                    )
                },
            )
        except (KeyError, TypeError) as exc:
            raise ValueError("Invalid frozen equity liquidity protocol") from exc
        if plan.document() != document:
            raise ValueError("Exact frozen liquidity protocol and complete snapshot cohort required")
        return plan

    @property
    def identity(self):
        return document_hash(self.document())


def _member_evidence(member: LiquidityMember, frame, failure, clock, plan: EquityLiquidityPlan):
    window = clock[-plan.lookback :]
    row: dict[str, Any] = {
        "asset_id": member.asset_id,
        "symbol": member.symbol,
        "instrument_subtype": member.instrument_subtype,
        "classification": CLASSIFICATION,
        "expected_sessions": len(clock),
        "observed_sessions": None,
        "missing_dates": None,
        "missing_window_dates": None,
        "positive_volume_sessions": None,
        "latest_close": None,
        "median_dollar_volume": None,
        "eligible": False,
        "reasons": [],
    }
    reasons = row["reasons"]
    if failure is not None:
        reasons.append("acquisition_failed")
        row["failure"] = failure
        return row, False
    if frame is None:
        reasons.append("missing_frame")
        return row, False
    try:
        if frame.attrs.get("bar_layout", FIXED_BAR_LAYOUT) != FIXED_BAR_LAYOUT:
            raise ValueError("Native daily bar layout required")
        panel = align_daily_panel({member.symbol: frame}, clock, feed=plan.feed, adjustment=plan.adjustment)
    except (ValueError, TypeError, AttributeError) as exc:
        reasons.append("invalid_source_evidence")
        row["validation_error"] = str(exc)
        return row, False
    coverage = panel.coverage[member.symbol]
    row.update(observed_sessions=coverage["observed"], missing_dates=coverage["missing_dates"])
    window_frame = panel.frames[member.symbol].loc[window]
    missing = window[window_frame.close.isna()]
    row["missing_window_dates"] = [t.date().isoformat() for t in missing]
    if not missing.empty:
        reasons.append("missing_window_sessions")
        return row, True
    dollar_volume = window_frame.close * window_frame.volume
    if not all(math.isfinite(v) for v in dollar_volume):
        reasons.append("invalid_dollar_volume")
        return row, False
    row.update(
        positive_volume_sessions=int((window_frame.volume > 0).sum()),
        latest_close=float(window_frame.close.iloc[-1]),
        median_dollar_volume=float(dollar_volume.median()),
    )
    if row["positive_volume_sessions"] < plan.min_positive_volume_sessions:
        reasons.append("insufficient_positive_volume")
    if row["latest_close"] < plan.price_floor:
        reasons.append("price_below_floor")
    if row["median_dollar_volume"] < plan.min_median_dollar_volume:
        reasons.append("median_dollar_volume_below_floor")
    row["eligible"] = not reasons
    return row, True


def compute_liquidity_study(batch: DailyStudyInputs, clock, plan: EquityLiquidityPlan, sessions):
    """Retain every cohort outcome; absent/invalid acquisitions cannot choose winners."""
    SessionSchedule(plan.start, plan.end, sessions, source="observed_exchange_calendar")
    expected = pd.DatetimeIndex([pd.Timestamp(s.date, tz=ET_TZ) for s in sessions])
    if not isinstance(clock, pd.DatetimeIndex) or not clock.equals(expected) or len(clock) < plan.lookback:
        raise ValueError("Complete observed exchange-date calendar and sufficient trailing sessions required")
    symbols = set(plan.acquisition_symbols)
    if set(batch.frames) - symbols or set(batch.failures) - symbols or set(batch.frames) & set(batch.failures):
        raise ValueError("One acquisition outcome per frozen cohort symbol required")
    members = []
    selection_available = True
    for member in plan.members:
        row, known = _member_evidence(
            member, batch.frames.get(member.symbol), batch.failures.get(member.symbol), clock, plan
        )
        members.append(row)
        selection_available &= known
    eligible = sorted(
        (row for row in members if row["eligible"]),
        key=lambda row: (-row["median_dollar_volume"], row["asset_id"]),
    )
    selected = eligible[: plan.selection_size] if selection_available else []
    selected_ids = {row["asset_id"] for row in selected}
    for row in members:
        row["selection"] = (
            "selected"
            if row["asset_id"] in selected_ids
            else "ineligible"
            if not row["eligible"]
            else "candidate_cap"
            if selection_available
            else "withheld"
        )
    return {
        "version": LIQUIDITY_STUDY_VERSION,
        "status": PanelStudyStatus.COMPLETED if selection_available else PanelStudyStatus.FAILED,
        "error_type": None if selection_available else DailyAcquisitionError.__name__,
        "snapshot_id": plan.snapshot_id,
        "snapshot_observed_at": plan.snapshot_observed_at.isoformat(),
        "feed": plan.feed,
        "volume_scope": "single_venue_iex" if plan.feed == "alpaca:iex" else "consolidated_sip",
        "window_dates": [t.date().isoformat() for t in clock[-plan.lookback :]],
        "selection_available": selection_available,
        "target_size": plan.selection_size,
        "selected_count": len(selected),
        "eligible_count": len(eligible),
        "shortfall": plan.selection_size - len(selected),
        "members": members,
        "selected": [{"asset_id": row["asset_id"], "symbol": row["symbol"]} for row in selected],
        "authorizes_promotion": False,
        "point_in_time_historical_membership": False,
        "common_stock_classification": False,
        "market_capacity_estimate": False,
    }
