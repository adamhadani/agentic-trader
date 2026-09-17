"""Prospective clock evidence cannot become strategy or promotion evidence."""

import asyncio
import hashlib
import threading
from types import SimpleNamespace

import pandas as pd
import pytest

from agentic_trader.config import SessionObservationConfig
from agentic_trader.data.sessions import SessionAcquisitionError
from agentic_trader.market.bars import session_bar_windows
from agentic_trader.research.alpha.observation import SessionObservationService, observation_target
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.workflow import encode


@pytest.mark.parametrize(
    ("day", "close", "now", "expected"),
    [
        ("2024-03-08", "16:00", "2024-03-08 14:45:05Z", "2024-03-08 14:45Z"),
        ("2024-03-11", "16:00", "2024-03-11 13:45:05Z", "2024-03-11 13:45Z"),
        ("2024-11-29", "13:00", "2024-11-29 18:00:05Z", "2024-11-29 18:00Z"),
        ("2024-11-29", "13:00", "2024-11-29 14:44:59Z", None),
        ("2024-11-29", "13:00", "2024-11-29 18:03:01Z", None),
        ("2024-11-29", "13:00", "2024-11-30 18:00:05Z", None),
    ],
)
def test_forward_sampling_uses_observed_session_closes(schedule_for, day, close, now, expected):
    schedule = schedule_for((day, close))
    target = observation_target(schedule.sessions, "15m", pd.Timestamp(now), window_seconds=180)
    assert (target.closed_at if target else None) == (pd.Timestamp(expected) if expected else None)


def test_clipped_signal_windows_are_shared_with_replay(schedule_for):
    session = schedule_for(("2024-11-29", "13:00")).sessions[0]
    windows = session_bar_windows(session, "1h")
    assert len(windows) == 4
    assert windows[-1].closed_at == session.close
    assert windows[-1].closed_at - windows[-1].opened_at == pd.Timedelta(minutes=30)


@pytest.mark.parametrize(
    "values",
    [
        {"symbols": []},
        {"symbols": ["/MES"]},
        {"symbols": ["SPY", "SPY"]},
        {"timeframe": "5m"},
        {"poll_seconds": 0},
        {"window_seconds": 1},
        {"feed": "sip"},
        {"feed": "alpaca:unknown"},
    ],
)
def test_observation_policy_rejects_unbounded_or_ambiguous_inputs(values):
    with pytest.raises(ValueError):
        SessionObservationConfig(**values)


@pytest.fixture
async def observer(temp_db, tmp_path, schedule_for, minute_bars):
    await temp_db.init_db()
    repo = AlphaRepository(temp_db.workflows)
    schedule = schedule_for(("2024-11-29", "13:00"))
    frame = minute_bars(schedule)
    frame.attrs["feed"] = "alpaca:iex"
    now = [pd.Timestamp("2024-11-29 14:45:05Z")]
    calls = []

    def minutes(symbol, start, end, feed):
        calls.append((symbol, start, end, feed))
        return frame.loc[frame.index < end].copy()

    source = SimpleNamespace(calendar=lambda start, end: schedule.sessions, minutes=minutes)
    service = SessionObservationService(
        repo,
        source,
        SessionObservationConfig(enabled=True, feed="alpaca:iex"),
        directory=tmp_path / "observations",
        clock=lambda: now[0],
        runtime={"run_id": "fixture"},
    )
    yield service, repo, frame, now, calls
    await temp_db.engine.dispose()


@pytest.mark.parametrize("failure", [None, "coverage", "transport"])
async def test_observer_retains_raw_evidence_without_shadow_or_trial_credit(observer, failure):
    service, repo, frame, _now, _calls = observer
    if failure == "coverage":
        frame.drop(frame.index[2], inplace=True)
    if failure == "transport":

        def fail(*args):
            raise OSError("fixture unavailable")

        service.source.minutes = fail
    results = await service.run_once()
    result = results[0]
    assert result["status"] == ("complete" if failure is None else "unavailable")
    assert result["authorizes_promotion"] is False
    assert result["artifact_hash"]
    assert (await repo.snapshot()).generation == 0
    assert (await repo.get("family/all"))["trial_count"] == 0
    assert await repo.get("research/latest") is None
    assert await repo.get("observation/latest") == result
    assert (await repo.status())["latest_observation"] == result
    await repo.rebuild()
    assert await repo.get("observation/latest") == result
    if failure == "coverage":
        assert result["coverage"]["missing_minutes"] == 1
    if failure is None:
        assert result["closed_at"] == "2024-11-29T14:45:00+00:00"
        assert result["received_at"] >= result["requested_at"] >= result["closed_at"]
        assert result["availability_upper_bound_seconds"] == 5
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in service.directory.rglob("*") if p.is_file())


async def test_restart_and_revision_preserve_first_seen_and_changed_evidence(observer):
    service, repo, frame, now, calls = observer
    first = (await service.run_once())[0]
    now[0] += pd.Timedelta(seconds=30)
    frame.loc[frame.index[14], "close"] = 100.5
    second = (await service.run_once())[0]
    now[0] += pd.Timedelta(seconds=30)
    third = (await service.run_once())[0]
    assert len(calls) == 3
    assert second["content_hash"] != first["content_hash"]
    assert third["content_hash"] == second["content_hash"]
    bar = await repo.get(first["bar_key"])
    assert bar["first_observed_at"] == first["received_at"]
    assert bar["revision_count"] == 1
    assert bar["last_observed_at"] == third["received_at"]
    await repo.rebuild()
    assert await repo.get(first["bar_key"]) == bar
    assert await repo.get(f"observation/{first['observation_id']}") == first


async def test_capture_is_off_loop_and_exposure_precedes_price_io(observer):
    service, repo, _frame, _now, _calls = observer
    entered, release = threading.Event(), threading.Event()
    original = service.source.minutes

    def blocked(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    service.source.minutes = blocked
    task = asyncio.create_task(service.run_once())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        latest = await repo.get("observation/latest")
        assert latest["status"] == "capturing"
        assert (await repo.get("family/all"))["trial_count"] == 0
        assert latest["started_at"]
    finally:
        release.set()
    await task


async def test_holiday_and_outside_sampling_window_do_not_read_prices(observer):
    service, _repo, _frame, now, calls = observer
    now[0] = pd.Timestamp("2024-11-29 14:40:00Z")
    assert await service.run_once() == []
    now[0] = pd.Timestamp("2024-11-30 14:45:05Z")
    service.source.calendar = lambda *args: ()
    assert await service.run_once() == []
    assert calls == []


async def test_calendar_refresh_does_not_keep_a_withdrawn_session_for_whole_day(observer):
    service, _repo, _frame, now, calls = observer
    await service.run_once()
    now[0] += pd.Timedelta(minutes=15)
    service.source.calendar = lambda *args: ()
    assert await service.run_once() == []
    assert len(calls) == 1


async def test_mismatched_feed_cannot_be_accepted_as_forward_evidence(observer):
    service, repo, frame, _now, _calls = observer
    frame.attrs["feed"] = "synthetic"
    result = (await service.run_once())[0]
    assert result["status"] == "unavailable"
    assert await repo.get(result["bar_key"]) is None


async def test_actual_request_clock_is_after_durable_preparation(observer):
    service, repo, _frame, now, _calls = observer
    original = repo.begin_observation

    async def slow_journal(*args):
        await original(*args)
        now[0] += pd.Timedelta(seconds=2)

    repo.begin_observation = slow_journal
    result = (await service.run_once())[0]
    assert pd.Timestamp(result["requested_at"]) > pd.Timestamp(result["started_at"])
    assert result["availability_upper_bound_seconds"] == 7


async def test_minute_failure_retains_calendar_and_input_hashes(observer):
    service, _repo, frame, _now, _calls = observer
    frame.drop(frame.index[2], inplace=True)
    result = (await service.run_once())[0]
    assert result["inputs_hash"] and result["calendar_hash"] and result["manifest_hash"]


async def test_observed_session_excludes_daily_labels_from_future_holdouts(observer):
    service, repo, _frame, _now, _calls = observer
    await service.run_once()
    key = "holdout/" + hashlib.sha256(encode({"symbol": "SPY"}).encode()).hexdigest()
    intervals = (await repo.get(key))["intervals"]
    # A daily bar's start label precedes RTH open; it is still inspected evidence.
    daily_label = pd.Timestamp("2024-11-29 00:00Z")
    assert any(pd.Timestamp(p["start"]) <= daily_label <= pd.Timestamp(p["end"]) for p in intervals)


async def test_transport_failure_retains_actual_request_and_receipt_times(observer):
    service, _repo, _frame, now, _calls = observer

    def fail(*args):
        now[0] += pd.Timedelta(seconds=2)
        raise OSError("fixture transport failure")

    service.source.minutes = fail
    result = (await service.run_once())[0]
    assert result["status"] == "unavailable"
    assert pd.Timestamp(result["received_at"]) - pd.Timestamp(result["requested_at"]) == pd.Timedelta(seconds=2)


async def test_clock_rollback_after_preparation_cannot_authorize_availability(observer):
    service, repo, _frame, now, _calls = observer
    original = repo.begin_observation

    async def skew_clock(*args):
        await original(*args)
        now[0] -= pd.Timedelta(seconds=1)

    repo.begin_observation = skew_clock
    result = (await service.run_once())[0]
    assert result["status"] == "unavailable"
    assert await repo.get(result["bar_key"]) is None


async def test_failed_acquisition_pages_survive_observer_journal_replay(observer):

    service, repo, _frame, _now, _calls = observer
    receipts = [{"error_type": "BarAcquisitionError", "evidence": {"artifact": "fixture", "sha256": "abc"}}]

    def fail(*args):
        raise SessionAcquisitionError(receipts)

    service.source.minutes = fail
    result = (await service.run_once())[0]
    assert result["acquisition"] == receipts
    await repo.rebuild()
    assert (await repo.get(f"observation/{result['observation_id']}"))["acquisition"] == receipts
