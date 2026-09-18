"""Immutable replay feeds reuse the daily journal without provider access."""

import hashlib
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from agentic_trader.config import DailyAcquisitionConfig
from agentic_trader.market.bars import SessionSchedule, TradingSession
from agentic_trader.research.alpha.forecast_controls_plan import ForecastControlsPlan
from agentic_trader.research.alpha.panel_forecast_plan import PanelForecastPlan
from agentic_trader.research.alpha.panel_workflow import AlphaPanelService, _save_observations
from agentic_trader.research.alpha.retained_panel import RetainedPanelSource


@pytest.fixture
def retained_case(tmp_path):
    doc = json.loads((Path(__file__).parents[2] / "config/research/screened-equity-forecast-iex-v1.json").read_text())
    parent = PanelForecastPlan.from_document(doc["plan"])
    directory = tmp_path / "parent"
    directory.mkdir()
    frames = {}
    for i, symbol in enumerate(parent.acquisition_symbols):
        frame = pd.DataFrame(
            {"open": [10.0 + i], "high": [11.0 + i], "low": [9.0 + i], "close": [10.0 + i], "volume": [100.0]},
            index=pd.DatetimeIndex(["2023-01-03"], tz="America/New_York"),
        )
        frame.attrs.update(feed=parent.feed, adjustment=parent.adjustment, timeframe="1d")
        frames[symbol] = frame
    inputs = {
        "datasets": {symbol: _save_observations(frame, directory) for symbol, frame in frames.items()},
        "failures": {},
        "receipts": [{"requested_at": "2026-09-17T20:00:01Z", "received_at": "2026-09-17T20:01:00Z"}],
    }
    calendar = SessionSchedule(
        parent.start,
        parent.end,
        (TradingSession(date(2023, 1, 3), pd.Timestamp("2023-01-03T14:30Z"), pd.Timestamp("2023-01-03T21:00Z")),),
        "alpaca_calendar",
    ).document()
    manifest = {
        "plan": parent.document(),
        "plan_id": parent.identity,
        "as_of": "2026-09-17T20:00:00Z",
        "run_id": "original",
    }
    result = {
        "status": "completed",
        "plan_id": parent.identity,
        "run_id": "original",
        "completed_comparisons": parent.trial_count,
        "charged_trials": parent.trial_count,
    }
    for name, payload in [("manifest", manifest), ("inputs", inputs), ("calendar", calendar)]:
        data = json.dumps(payload).encode()
        (directory / f"{name}.json").write_bytes(data)
        result[f"{name}_hash"] = hashlib.sha256(data).hexdigest()
    payload = json.dumps(result).encode()
    (directory / "result.json").write_bytes(payload)
    plan = ForecastControlsPlan(parent, "equities", hashlib.sha256(payload).hexdigest())
    return SimpleNamespace(plan=plan, directory=directory, frames=frames, inputs=inputs, result=result)


def test_construction_never_reads_artifacts(tmp_path):
    source = RetainedPanelSource(tmp_path / "missing", None)
    with pytest.raises(RuntimeError, match="calendar"):
        _ = source.parent_result


def test_replay_preserves_source_values_and_original_metadata(retained_case):
    c = retained_case
    source = RetainedPanelSource(c.directory, c.plan)
    sessions = source.calendar(c.plan.start, c.plan.end)
    assert len(sessions) == 1 and source.parent_result == c.result
    for symbol, expected in c.frames.items():
        actual = source.daily(symbol, c.plan.start, c.plan.end, c.plan.feed, c.plan.adjustment)
        pd.testing.assert_frame_equal(actual, expected.tz_convert("UTC"))
        assert actual.attrs == expected.attrs
    with pytest.raises(ValueError):
        source.daily("SPY", c.plan.start, c.plan.end, c.plan.feed, c.plan.adjustment)
    with pytest.raises(ValueError):
        source.calendar(c.plan.start, date(2025, 12, 30))


@pytest.mark.parametrize("name", ["result", "manifest", "inputs", "calendar", "dataset"])
async def test_tampering_is_charged_and_retained_before_any_read(retained_case, tmp_path, name):
    c = retained_case
    filename = c.inputs["datasets"][c.plan.acquisition_symbols[0]]["artifact"] if name == "dataset" else f"{name}.json"
    with (c.directory / filename).open("ab") as target:
        target.write(b" ")
    events = []

    class Repository:
        async def reserve_run(self, run_id, **kwargs):
            events.append(("reserve", kwargs))

        async def exclude_observed_interval(self, **kwargs):
            events.append(("exclude", kwargs))

        async def record_diagnostic(self, run_id, payload):
            events.append(("result", payload))

    class Source(RetainedPanelSource):
        def calendar(self, *args):
            assert len(events) == 1 + len(c.plan.acquisition_symbols)
            assert events[0][1]["trials"] == 90
            return super().calendar(*args)

    def compute(batch, clock, plan, sessions):
        batch.require_complete(plan.acquisition_symbols)
        return {"status": "completed"}

    output = tmp_path / "attempt"
    result = await AlphaPanelService(
        Repository(),
        Source(c.directory, c.plan),
        acquisition=DailyAcquisitionConfig(min_request_interval_seconds=0),
        compute=compute,
    ).run(c.plan, output, environment={"input_origin": "retained_artifacts"}, as_of=pd.Timestamp("2026-09-18T00:00Z"))
    assert result["status"] == "failed" and result["completed_comparisons"] == 0
    assert result["charged_trials"] == 90 and not result["authorizes_promotion"]
    assert events[-1][0] == "result"
    assert len(json.loads((output / "inputs.json").read_text())["members"]) == 73
