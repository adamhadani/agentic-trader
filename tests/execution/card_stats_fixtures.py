"""Shared card-statistics fixtures: snapshot builders (more helpers join in Task 3)."""

from datetime import UTC, datetime, timedelta
from typing import Any

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
