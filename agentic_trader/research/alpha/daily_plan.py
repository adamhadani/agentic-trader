"""Immutable prospective native-daily diagnostic protocol and observed-session clock."""

import math
import re
from dataclasses import asdict, dataclass
from datetime import date, timedelta

import pandas as pd

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.alpha.equity_universe import document_hash
from agentic_trader.research.alpha.factor_features import FACTOR_COUNT, ResidualMomentumSpec
from agentic_trader.research.alpha.forecast_controls_plan import CONTROL_EXPRESSIONS
from agentic_trader.research.alpha.targets import ForecastLabel, ForecastTarget


DAILY_PANEL_VERSION = "prospective_daily_panel_v1"
DAILY_MODELS = ("rank_blend", "volatility20", "residual_rank_blend", "ridge")
DAILY_RIDGE_FEATURES = (
    ("momentum60", "roc(close,60)"),
    ("reversal5", "-roc(close,5)"),
    ("volatility20", "realized_vol(returns,20)"),
)
NATIVE_DELAY_SECONDS = 1800
NATIVE_DEADLINE_SECONDS = 10800
ENTRY_BUFFER_SECONDS = 300
CALENDAR_FORWARD_DAYS = 65


@dataclass(frozen=True)
class DailyDecisionWindow:
    decision_date: date
    native_closed_at: pd.Timestamp
    available_at: pd.Timestamp
    expires_at: pd.Timestamp
    entry_date: date
    entry_open: pd.Timestamp
    exit_date: date
    outcome_available_at: pd.Timestamp
    outcome_expires_at: pd.Timestamp
    economic_scheduled: bool

    def document(self):
        return {k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in asdict(self).items()}


@dataclass(frozen=True)
class DailyPanelPlan:
    campaign_id: str
    symbols: tuple[str, ...]
    factor_symbols: tuple[str, ...]
    start_date: date
    end_date: date
    history_start: date
    selection_hash: str
    parent_plan_id: str
    feed: str = "alpaca:iex"
    adjustment: str = "all"
    top_k: int = 8
    min_assets: int = 16
    costs_bps: tuple[float, ...] = (1.0, 5.0)
    ridge_alpha: float = 100.0
    train_sessions: int = 504
    min_train_sessions: int = 126
    min_train_rows: int = 500
    history_sessions: int = 61

    def __post_init__(self):
        def valid_symbols(xs):
            return (
                isinstance(xs, tuple)
                and xs == tuple(sorted(set(xs)))
                and all(isinstance(x, str) and re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", x) for x in xs)
            )

        if (
            not isinstance(self.campaign_id, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,95}", self.campaign_id)
            or not valid_symbols(self.symbols)
            or not 16 <= len(self.symbols) <= 491
            or not valid_symbols(self.factor_symbols)
            or len(self.factor_symbols) != FACTOR_COUNT
            or set(self.symbols) & set(self.factor_symbols)
            or any(type(d) is not date for d in (self.start_date, self.end_date, self.history_start))
            or not self.history_start < self.start_date <= self.end_date
            or not 1 <= (self.end_date - self.start_date).days <= 730
            or (self.end_date - self.history_start).days >= 3660
            or any(
                not isinstance(h, str) or not re.fullmatch(r"[a-f0-9]{64}", h)
                for h in (self.selection_hash, self.parent_plan_id)
            )
            or self.feed != "alpaca:iex"
            or self.adjustment != "all"
            or any(
                type(getattr(self, name)) is not int
                for name in (
                    "top_k",
                    "min_assets",
                    "train_sessions",
                    "min_train_sessions",
                    "min_train_rows",
                    "history_sessions",
                )
            )
            or self.top_k != 8
            or self.min_assets != 16
            or self.costs_bps != (1.0, 5.0)
            or type(self.ridge_alpha) not in (float, int)
            or not math.isfinite(self.ridge_alpha)
            or self.ridge_alpha != 100.0
            or self.train_sessions != 504
            or self.min_train_sessions != 126
            or self.min_train_rows != 500
            or self.history_sessions != 61
        ):
            raise ValueError("Complete fixed prospective daily-panel protocol required")

    @property
    def target(self):
        return ForecastTarget("1d", 20, ForecastLabel.NEXT_OPEN_TO_CLOSE)

    @property
    def residual_spec(self):
        return ResidualMomentumSpec()

    @property
    def ridge_features(self):
        return dict(DAILY_RIDGE_FEATURES)

    @property
    def acquisition_symbols(self):
        return tuple(sorted((*self.symbols, *self.factor_symbols)))

    @property
    def identity(self):
        return document_hash(self.document())

    def document(self):
        fields = asdict(self)
        for name in ("start_date", "end_date", "history_start"):
            fields[name] = fields[name].isoformat()
        for name in ("symbols", "factor_symbols", "costs_bps"):
            fields[name] = list(fields[name])
        return {
            "version": DAILY_PANEL_VERSION,
            **fields,
            "target": self.target.document(),
            "residual_spec": self.residual_spec.document(),
            "models": list(DAILY_MODELS),
            "ridge_features": self.ridge_features,
            "control_expressions": dict(CONTROL_EXPRESSIONS),
            "rank_blend": "Equal average percentile ranks of reversal60 and volatility20 on common support",
            "residual_rank_blend": "Equal average percentile ranks of rank_blend and residual_momentum on common support",
            "training": "Daily refit; last 504 mature predictor sessions, outcomes strictly before decision date; actual receipts strictly before fit cutoff; train-only scaler",
            "availability": {
                "native_delay_seconds": NATIVE_DELAY_SECONDS,
                "native_deadline_seconds": NATIVE_DEADLINE_SECONDS,
                "entry_buffer_seconds": ENTRY_BUFFER_SECONDS,
            },
            "support": "Common finite past-only support across four arms; 61 complete finite OHLCV sessions; supervised label and residual-return endpoints require positive volume; future outcome availability never selects weights",
            "economic_schedule": "Every 20th observed session from start_date, including missed captures; daily IC overlaps",
            "outcome_policy": "First maturity-window acquisition is terminal; per-symbol entry and exit share one all-adjusted vintage; missing or zero-volume endpoints remain unavailable",
            "residual_policy": "Only pre-campaign history may bootstrap at actual receipt; campaign dates append current innovation or immutable missed-session tombstones under parent-state CAS",
            "scope": "Current-cohort prospective price-proxy diagnostic; no auction fills, borrow, independent selection validation or execution authority",
            "charged_trials": len(DAILY_MODELS),
            "authorizes_promotion": False,
        }

    @classmethod
    def from_document(cls, document):
        try:
            kwargs = {name: document[name] for name in cls.__dataclass_fields__}
            for name in ("start_date", "end_date", "history_start"):
                kwargs[name] = date.fromisoformat(kwargs[name])
            for name in ("symbols", "factor_symbols", "costs_bps"):
                kwargs[name] = tuple(kwargs[name])
            plan = cls(**kwargs)
            if plan.document() != document:
                raise ValueError("Exact frozen prospective protocol required")
            return plan
        except (KeyError, TypeError) as exc:
            raise ValueError("Complete prospective protocol required") from exc

    def decision_window(self, decision_date, sessions):
        dates = [s.date for s in sessions]
        if dates != sorted(set(dates)) or self.start_date not in dates or decision_date not in dates:
            raise ValueError("Complete ordered calendar including fixed campaign anchor required")
        position, anchor = dates.index(decision_date), dates.index(self.start_date)
        if not self.start_date <= decision_date <= self.end_date or position + self.target.horizon_bars >= len(dates):
            raise ValueError("Campaign date and observed future H20 sessions required")
        entry, exit_session = sessions[position + 1], sessions[position + self.target.horizon_bars]
        native_close = pd.Timestamp(decision_date + timedelta(days=1), tz=ET_TZ).tz_convert("UTC")
        outcome_close = pd.Timestamp(exit_session.date + timedelta(days=1), tz=ET_TZ).tz_convert("UTC")
        available = native_close + pd.Timedelta(seconds=NATIVE_DELAY_SECONDS)
        expires = min(
            native_close + pd.Timedelta(seconds=NATIVE_DEADLINE_SECONDS),
            entry.open - pd.Timedelta(seconds=ENTRY_BUFFER_SECONDS),
        )
        if not available < expires:
            raise ValueError("No complete pre-entry observation window")
        return DailyDecisionWindow(
            decision_date,
            native_close,
            available,
            expires,
            entry.date,
            entry.open,
            exit_session.date,
            outcome_close + pd.Timedelta(seconds=NATIVE_DELAY_SECONDS),
            outcome_close + pd.Timedelta(seconds=NATIVE_DEADLINE_SECONDS),
            (position - anchor) % self.target.horizon_bars == 0,
        )
