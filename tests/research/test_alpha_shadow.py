import asyncio
import threading
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.alpha.models import AlphaDefinition, RegistrySnapshot
from agentic_trader.research.alpha.shadow import AlphaShadowService, observe_definition


@pytest.fixture
def shadow_inputs():
    now = datetime(2026, 9, 16, 12, tzinfo=UTC)
    frame = pd.DataFrame(
        {"close": np.linspace(100, 120, 60) ** 2}, index=pd.date_range(end="2026-09-16T11:00Z", periods=60, freq="h")
    )
    frame.attrs.update(feed="alpaca:sip", adjustment="raw", timeframe="1h")
    definition = AlphaDefinition(
        "alpha_shadow", "Shadow", "close", timeframe="1h", data_feed="alpaca:sip", eligible_symbols=("SPY",)
    )
    return definition, SimpleNamespace(hourly=frame, symbol="SPY", contract="SPY"), now


@pytest.mark.parametrize("defect", ["feed", "adjustment", "stale"])
def test_invalid_shadow_observations_cannot_earn_promotion_evidence(shadow_inputs, defect):
    definition, data, now = shadow_inputs
    if defect in ("feed", "adjustment"):
        data.hourly.attrs[defect] = "wrong"
    else:
        data.hourly.index -= pd.Timedelta(days=2)
    observation = observe_definition(definition, data, now)
    assert not observation["valid"]
    assert observation["reason"]


def test_observed_shadow_score_is_versioned_and_timestamped(shadow_inputs):
    definition, data, now = shadow_inputs
    observation = observe_definition(definition, data, now)
    assert observation["valid"]
    assert observation["version_id"] == definition.version_id
    assert observation["completed_at"] == now.isoformat()


async def test_shadow_cpu_work_does_not_block_event_loop(shadow_inputs, monkeypatch):
    definition, data, now = shadow_inputs
    entered, release = threading.Event(), threading.Event()
    original = observe_definition

    def slow_observation(*args):
        entered.set()
        assert release.wait(timeout=2)
        return original(*args)

    repository = AsyncMock()
    repository.get.return_value = None
    monkeypatch.setattr("agentic_trader.research.alpha.shadow.observe_definition", slow_observation)
    task = asyncio.create_task(
        AlphaShadowService(repository).observe(RegistrySnapshot(1, (), (definition,)), data, as_of=now)
    )
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        # This coroutine must run while calculation is still waiting in its worker.
        assert not task.done()
    finally:
        release.set()
    assert (await task)[0]["valid"]
