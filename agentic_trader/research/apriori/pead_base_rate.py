"""The PEAD v2 base rate in the desk's card unit (research only, offline).

Re-labels the PEAD v2 study's LONG events with the study's own functions, proves it reproduces the study's
labels, adds the ``net_excess_r_v1`` inputs (trailing beta, per-name cost, the market's R over the event's own
hold) and summarises the result as a digest-pinned file the PEAD card reads. No provider, network or database
access: every input is a saved artifact or a cached bar file.
"""

from __future__ import annotations

import hashlib
import math
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, ValidationError, model_validator

from agentic_trader.research.apriori.pead_study import LABEL_SPAN, decision_price, levels_for, utc_index
from agentic_trader.research.setups.beta import BETA_WINDOW_SESSIONS, trailing_beta
from agentic_trader.research.setups.card_prior import between_quarter_tau, cost_model_constants
from agentic_trader.research.setups.labels import BracketHit, label_bracket
from agentic_trader.research.setups.net_excess import (
    NET_EXCESS_UNIT,
    R_CLIP,
    cost_inputs,
    name_cost_bps,
    net_excess_r,
)
from agentic_trader.research.setups.outcomes import market_r


if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from agentic_trader.research.apriori.catalog import PeadEntry


__all__ = [
    "BASE_RATE_VERSION",
    "RECENT_FROM",
    "BaseRateCell",
    "BaseRateUnavailable",
    "BaseRateYear",
    "PeadBaseRate",
    "add_net_excess",
    "check_reproduces",
    "load_pead_base_rate",
    "relabel_long",
    "summarise",
]

BASE_RATE_VERSION = "pead-base-rate-v1"
RECENT_FROM = date(2025, 1, 1)
R_TOLERANCE = 1e-9
EXPECTED_TOLERANCE = 1e-9


class BaseRateUnavailable(RuntimeError):
    """The base rate cannot be used; the message is one fixed literal, never a path or file content."""


class BaseRateCell(BaseModel, frozen=True, allow_inf_nan=False, extra="forbid"):
    """Leg events of one window: ``mean`` of ``net_excess_r``, ``control_*`` over all reporters, paired P2."""

    n: int = Field(ge=1)
    mean: float
    se_session: float = Field(ge=0)
    control_n: int = Field(ge=1)
    control_mean: float
    control_se_session: float = Field(ge=0)
    p2_mean: float
    p2_se: float = Field(ge=0)


class BaseRateYear(BaseRateCell, frozen=True, allow_inf_nan=False, extra="forbid"):
    year: int


class PeadBaseRate(BaseModel, frozen=True, allow_inf_nan=False, extra="forbid"):
    version: str
    unit: str
    entry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: dict[str, str]
    cost_model: dict[str, float]
    r_clip: float
    beta_window_sessions: int
    recent_from: date
    window_start: date
    window_end: date
    n_leg: int = Field(ge=1)
    n_all: int = Field(ge=1)
    n_no_beta: int = Field(ge=0)
    n_no_market: int = Field(ge=0)
    overall: BaseRateCell
    recent: BaseRateCell
    by_year: tuple[BaseRateYear, ...]
    tau: float = Field(gt=0)
    shrinkage: float = Field(gt=0, le=1)
    expected_mean: float
    expected_se: float = Field(ge=0)
    code_revision: str

    @model_validator(mode="after")
    def _expected_follows_shrinkage(self) -> PeadBaseRate:
        if abs(self.expected_mean - self.shrinkage * self.recent.mean) > EXPECTED_TOLERANCE or (
            abs(self.expected_se - math.sqrt(self.shrinkage) * self.recent.se_session) > EXPECTED_TOLERANCE
        ):
            raise ValueError("expected figure is not the shrunk recent mean")
        return self


def load_pead_base_rate(path: Path, sha256: str | None) -> PeadBaseRate:
    """Load the digest-pinned base rate; any failure raises ``BaseRateUnavailable`` with one fixed literal."""
    if sha256 is None:
        raise BaseRateUnavailable("base rate digest is not configured")
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise BaseRateUnavailable("base rate file not found") from None
    except OSError:
        raise BaseRateUnavailable("base rate file unreadable") from None
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise BaseRateUnavailable("base rate digest mismatch")
    try:
        rate = PeadBaseRate.model_validate_json(raw)
    except ValidationError:
        raise BaseRateUnavailable("base rate file invalid") from None
    if rate.version != BASE_RATE_VERSION:
        raise BaseRateUnavailable("base rate version mismatch")
    if (
        rate.unit != NET_EXCESS_UNIT
        or rate.cost_model != cost_model_constants()
        or rate.r_clip != R_CLIP
        or rate.beta_window_sessions != BETA_WINDOW_SESSIONS
    ):
        raise BaseRateUnavailable("base rate constants differ from the code")
    return rate


# --- Re-labelling ------------------------------------------------------------------------------


def relabel_long(events: pd.DataFrame, hourly: Mapping[str, pd.DataFrame], entry: PeadEntry) -> pd.DataFrame:
    """One LONG row per event the study labelled, by the study's own price, levels and bracket walk.

    Mirrors ``pead_study.label_events`` for LONG at the decisive cost, and keeps what it drops: the decision
    price (``entry``), the ``stop``, the actual fill (``entry_price``) and the entry and exit times.
    """
    rows: list[dict[str, Any]] = []
    for event in events.to_dict("records"):
        bars = hourly.get(event["symbol"])
        if bars is None or bars.empty:
            continue
        when = event["decision_at"]
        index = utc_index(bars)
        window = bars.loc[(index >= when - timedelta(days=1)) & (index <= when + LABEL_SPAN)]
        price = decision_price(window, when)
        if price is None:
            continue
        levels = levels_for("LONG", price, float(event["atr"]), entry)
        if levels is None:
            continue
        outcome = label_bracket(
            levels,
            when,
            window,
            max_hold_sessions=entry.trade.max_hold_sessions,
            cost_bps_per_side=entry.decision_cost_bps,
        )
        if outcome.hit == BracketHit.IMMATURE:
            continue
        rows.append(
            {
                **event,
                "direction": "LONG",
                "is_leg": event["leg"] == "LONG",
                "entry": levels.entry,
                "stop": levels.stop,
                "entry_price": outcome.entry_price,
                "entry_time": outcome.entry_time,
                "exit_time": outcome.exit_time,
                "hit": str(outcome.hit),
                "r": outcome.r,
                "r_cost": outcome.r_cost,
                "holding_sessions": outcome.holding_sessions,
            }
        )
    return pd.DataFrame(rows)


def check_reproduces(relabelled: pd.DataFrame, study_labels: pd.DataFrame) -> None:
    """Raise ``ValueError`` with counts unless every study LONG label reproduces (``r`` to 1e-9, ``hit`` exactly)."""
    study = study_labels[study_labels["direction"] == "LONG"]
    if len(relabelled) != len(study):
        raise ValueError(f"row count differs: re-labelled {len(relabelled)}, study {len(study)}")
    keys = ["symbol", "session"]
    joined = relabelled.assign(session=pd.to_datetime(relabelled["session"]).dt.date.astype(str)).merge(
        study.assign(session=pd.to_datetime(study["session"]).dt.date.astype(str)),
        on=keys,
        how="outer",
        suffixes=("", "_study"),
        indicator=True,
        validate="one_to_one",
    )
    unmatched = int((joined["_merge"] != "both").sum())
    if unmatched:
        raise ValueError(f"row count differs: {unmatched} (symbol, session) keys are on one side only")
    r_bad = ~np.isclose(joined["r"].astype(float), joined["r_study"].astype(float), rtol=0.0, atol=R_TOLERANCE)
    hit_bad = joined["hit"].astype(str) != joined["hit_study"].astype(str)
    if r_bad.any() or hit_bad.any():
        examples = joined.loc[r_bad | hit_bad, [*keys, "r", "r_study", "hit", "hit_study"]].head(5).to_dict("records")
        raise ValueError(
            f"{int(r_bad.sum())} r mismatch(es), {int(hit_bad.sum())} hit mismatch(es) of {len(joined)}: {examples}"
        )


# --- Net excess ----------------------------------------------------------------------------------


def add_net_excess(
    rows: pd.DataFrame,
    dailies: Mapping[str, pd.DataFrame | None],
    market_daily: pd.DataFrame | None,
    market_hourly: pd.DataFrame | None,
) -> pd.DataFrame:
    """Add ``beta``, ``name_cost_bps``, ``market_r`` and ``net_excess_r`` (NaN when an input is missing, never filled)."""
    out = rows.reset_index(drop=True).copy()
    betas: list[float] = []
    costs: list[float] = []
    markets: list[float] = []
    excess: list[float] = []
    for row in out.itertuples(index=False):
        session = pd.Timestamp(row.session).date()
        daily = dailies.get(row.symbol)
        beta, _ = trailing_beta(daily, market_daily, session)
        fill = float(row.entry_price)
        risk_unit = abs(float(row.entry) - float(row.stop))
        cost = name_cost_bps(fill, risk_unit, cost_inputs(daily, session))
        proxy = (
            market_r("LONG", float(row.entry), float(row.stop), row.entry_time, row.exit_time, market_hourly)
            if market_hourly is not None
            else None
        )
        value = net_excess_r(float(row.r), fill, risk_unit, cost, beta, proxy)
        betas.append(math.nan if beta is None else beta)
        costs.append(cost)
        markets.append(math.nan if proxy is None else proxy)
        excess.append(math.nan if value is None else value)
    out["beta"] = betas
    out["name_cost_bps"] = costs
    out["market_r"] = markets
    out["net_excess_r"] = excess
    return out


# --- Statistics ----------------------------------------------------------------------------------


def _se_session(values: pd.Series, sessions: pd.Series) -> float:
    """Session-clustered standard error of a mean: sd of per-session means over the square root of sessions."""
    by_session = values.groupby(sessions.to_numpy()).mean()
    if len(by_session) < 2:
        return 0.0
    return float(np.std(by_session.to_numpy(dtype=float), ddof=1) / math.sqrt(len(by_session)))


def _cell(leg: pd.DataFrame, control: pd.DataFrame) -> BaseRateCell:
    return BaseRateCell(
        n=len(leg),
        mean=float(leg["net_excess_r"].mean()),
        se_session=_se_session(leg["net_excess_r"], leg["session"]),
        control_n=len(control),
        control_mean=float(control["net_excess_r"].mean()),
        control_se_session=_se_session(control["net_excess_r"], control["session"]),
        p2_mean=float(leg["beat"].mean()),
        p2_se=_se_session(leg["beat"], leg["session"]),
    )


def summarise(rows: pd.DataFrame, recent_from: date) -> dict[str, Any]:
    """The base-rate statistics over rows with a finite ``net_excess_r``.

    ``n_no_beta`` counts rows without a trailing beta, ``n_no_market`` rows with a beta but no market R; both
    are excluded, never filled. P2 pairs each leg event with the mean of all reporters in its own session.
    """
    frame = rows.assign(session=pd.to_datetime(rows["session"]).dt.date)
    excess = pd.to_numeric(frame["net_excess_r"], errors="coerce")
    finite = frame[np.isfinite(excess)].copy()
    finite["net_excess_r"] = pd.to_numeric(finite["net_excess_r"]).astype(float)
    n_no_beta = int(frame["beta"].isna().sum())
    n_no_market = int((frame["beta"].notna() & frame["market_r"].isna()).sum())
    if len(finite) + n_no_beta + n_no_market != len(frame):
        raise ValueError("rows without a finite net excess are neither missing a beta nor a market return")
    finite["beat"] = finite["net_excess_r"] - finite.groupby("session")["net_excess_r"].transform("mean")
    leg = finite[finite["is_leg"].astype(bool)]
    if leg.empty:
        raise ValueError("no leg rows with a finite net excess")
    recent = leg[leg["session"] >= recent_from]
    if recent.empty:
        raise ValueError("no recent leg rows with a finite net excess")

    all_years = pd.Series([s.year for s in finite["session"]], index=finite.index)
    leg_years = all_years.loc[leg.index]
    by_year = tuple(
        BaseRateYear(
            year=int(year),
            **_cell(group, finite[all_years == year]).model_dump(),
        )
        for year, group in leg.groupby(leg_years)
    )
    tau, _ = between_quarter_tau(leg["net_excess_r"], pd.Series(leg["session"]))
    recent_cell = _cell(recent, finite[finite["session"] >= recent_from])
    shrinkage = tau**2 / (tau**2 + recent_cell.se_session**2)
    return {
        "n_leg": len(leg),
        "n_all": len(finite),
        "n_no_beta": n_no_beta,
        "n_no_market": n_no_market,
        "overall": _cell(leg, finite),
        "recent": recent_cell,
        "by_year": by_year,
        "tau": tau,
        "shrinkage": shrinkage,
        "expected_mean": shrinkage * recent_cell.mean,
        "expected_se": math.sqrt(shrinkage) * recent_cell.se_session,
    }
