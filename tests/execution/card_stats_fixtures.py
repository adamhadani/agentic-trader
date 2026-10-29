"""Shared card-statistics fixtures: snapshot builders, a fake repository, a deliverable evaluation."""

from datetime import UTC, datetime, timedelta
from typing import Any

from agentic_trader.agent.evaluator import LLMTradeEvaluation
from agentic_trader.constants import AssetClass
from agentic_trader.execution.card_evidence import lookup
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.setups.card_prior import CardStatsPrior, PriorEntry
from agentic_trader.research.setups.card_stats import CardStatsSnapshot
from agentic_trader.research.setups.net_excess import (
    COST_CAP_BPS,
    COST_FLOOR_BPS,
    COST_MIN_SESSIONS,
    COST_WINDOW_SESSIONS,
    IMPACT_COEFFICIENT,
    NET_EXCESS_UNIT,
    R_CLIP,
    REFERENCE_RISK_USD,
)


def key_stats(
    strategy: str = "TREND_PULLBACK", direction: str = "LONG", timeframe: str = "4h", **overrides: Any
) -> dict[str, Any]:
    """One snapshot key row: 23 mature since 2026-09-24, 43% target / 52% stop / 4% timeout, live -0.12R.

    The replay prior is -0.05R over 7,049 setups and the posterior -0.06R +- 0.10 (net_excess_r_v1).
    """
    row = {
        "strategy": strategy,
        "timeframe": timeframe,
        "direction": direction,
        "n_mature": 23,
        "n_immature": 4,
        "n_fetch_failed": 0,
        "target_rate": 0.43,
        "stop_rate": 0.52,
        "timeout_rate": 0.04,
        "mean_r_cost": -0.35,
        "mean_timeout_r": 0.5,
        "median_holding_sessions": 3.0,
        "first_decided_at": "2026-09-24T14:35:00+00:00",
        "last_decided_at": "2026-10-07T18:35:00+00:00",
        "live_mean": -0.12,
        "live_se": 0.2,
        "posterior_mean": -0.06,
        "posterior_se": 0.10,
        "prior_mean": -0.05,
        "prior_se": 0.26,
        "prior_weight": 40.0,
        "prior_n_rows": 7049,
        "prior_source": "replay",
    }
    return {**row, **overrides}


def make_snapshot(
    *, keys: list[dict[str, Any]] | None = None, computed_at: datetime | None = None
) -> CardStatsSnapshot:
    computed_at = computed_at or datetime.now(UTC)
    window_end = computed_at.astimezone(ET_TZ).date()
    return CardStatsSnapshot.model_validate(
        {
            "computed_at": computed_at.isoformat(),
            "window_start": (window_end - timedelta(days=89)).isoformat(),
            "window_end": window_end.isoformat(),
            "feed": "iex",
            "cost_bps_per_side": 5.0,
            "max_hold_sessions": 20,
            "labeller_protocol": "setup-outcomes-v1",
            "code_revision": "test-rev",
            "events_considered": 40,
            "rows_labelled": 120,
            "keys": keys if keys is not None else [key_stats()],
            "unit": NET_EXCESS_UNIT,
            "prior_sha256": "6" * 64,
            "prior_window_start": "2021-06-01",
            "prior_window_end": "2026-01-29",
            "prior_feed": "sip",
        }
    )


def prior_entry(
    strategy: str = "TREND_PULLBACK", timeframe: str = "4h", direction: str = "LONG", **overrides: Any
) -> PriorEntry:
    fields: dict[str, Any] = {
        "n_rows": 400,
        "n_sessions": 200,
        "mean": -0.05,
        "sd": 1.5,
        "se_session": 0.05,
        "tau": 0.25,
        "tau_source": "key",
    }
    return PriorEntry(strategy=strategy, timeframe=timeframe, direction=direction, **{**fields, **overrides})


def make_prior(keys: tuple[PriorEntry, ...] | None = None) -> CardStatsPrior:
    """A hand-built prior: one replay key (TREND_PULLBACK, 4h, LONG) unless ``keys`` says otherwise."""
    return CardStatsPrior(
        version="card-stats-prior-v1",
        unit=NET_EXCESS_UNIT,
        cost_model={
            "cost_floor_bps": COST_FLOOR_BPS,
            "impact_coefficient": IMPACT_COEFFICIENT,
            "reference_risk_usd": REFERENCE_RISK_USD,
            "cost_cap_bps": COST_CAP_BPS,
            "cost_window_sessions": float(COST_WINDOW_SESSIONS),
            "cost_min_sessions": float(COST_MIN_SESSIONS),
        },
        r_clip=R_CLIP,
        beta_window_sessions=120,
        tau_floor=0.05,
        min_rows_per_quarter=20,
        min_quarters=8,
        source={"window_start": "2021-01-04", "window_end": "2026-06-30", "feed": "sip"},
        excluded_symbols=("BIL", "SHY"),
        pooled_sd=1.4,
        pooled_tau=0.2,
        pooled_n_rows=1000,
        keys=keys if keys is not None else (prior_entry(),),
        cost_bps_quantiles={"p50": 6.0},
        n_no_beta=0,
        code_revision="test-rev",
    )


class FakeCardStats:
    """A ``CardStatsRepository`` stand-in for ``run_scan``: a fixed snapshot, counting reads."""

    def __init__(self, snapshot: CardStatsSnapshot | None = None, *, error: Exception | None = None):
        self.snapshot, self.error, self.reads = snapshot, error, 0

    async def latest(self) -> CardStatsSnapshot | None:
        self.reads += 1
        if self.error is not None:
            raise self.error
        return self.snapshot


def real_evaluation(candidate: Any, *, rr: float = 2.0) -> LLMTradeEvaluation:
    """A deliverable evaluation (the outbox validates it): entry 100, stop 98, target at ``rr``."""
    return LLMTradeEvaluation(
        approved=True,
        contract=candidate.contract,
        direction=candidate.direction,
        entry_price=100.0,
        stop_loss=98.0,
        take_profit=100.0 + 2.0 * rr,
        stop_distance_points=2.0,
        target_distance_points=2.0 * rr,
        risk_reward_ratio=rr,
        risk_dollars=2.0,
        reward_dollars=2.0 * rr,
        notional_value=100.0,
        effective_leverage=0.001,
        macro_clearance=True,
        thesis_summary="fixture thesis",
        quantity=1.0,
        asset_class=AssetClass.EQUITY,
    )


EVIDENCE = lookup(
    make_snapshot(), "TREND_PULLBACK", "4h", "LONG", now=datetime.now(UTC), max_age=timedelta(days=4), min_mature=20
).model_dump(mode="json")
