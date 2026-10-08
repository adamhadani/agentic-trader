"""Shared card-statistics fixtures: snapshot builders, a fake repository, a deliverable evaluation."""

from datetime import UTC, datetime, timedelta
from typing import Any

from agentic_trader.agent.evaluator import LLMTradeEvaluation
from agentic_trader.constants import AssetClass
from agentic_trader.execution.card_evidence import lookup
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.setups.card_stats import CardStatsSnapshot


def key_stats(strategy: str = "TREND_PULLBACK", direction: str = "LONG", **overrides: Any) -> dict[str, Any]:
    """One snapshot key row: 30 mature, 20% target / 70% stop / 10% timeout, mean -0.35R, timeouts +0.5R."""
    row = {
        "strategy": strategy,
        "direction": direction,
        "n_mature": 30,
        "n_immature": 4,
        "n_fetch_failed": 0,
        "target_rate": 0.2,
        "stop_rate": 0.7,
        "timeout_rate": 0.1,
        "mean_r_cost": -0.35,
        "mean_timeout_r": 0.5,
        "median_holding_sessions": 3.0,
        "first_decided_at": "2026-07-15T14:35:00+00:00",
        "last_decided_at": "2026-10-07T18:35:00+00:00",
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
        }
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
    make_snapshot(), "TREND_PULLBACK", "LONG", now=datetime.now(UTC), max_age=timedelta(days=4), min_mature=20
).model_dump(mode="json")
