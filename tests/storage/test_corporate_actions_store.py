"""Capture store: one capture event per content change, one check per run, bounded, fresh lookups."""

from datetime import UTC, date, datetime, timedelta

import pytest

from agentic_trader.data.corporate_actions import CorporateActionsPage, normalize_actions
from agentic_trader.execution.durable import EventKind
from agentic_trader.storage.corporate_actions import CAPTURE_STREAM, CHECK_STREAM, CorporateActionsStore, build_capture
from agentic_trader.storage.workflow import WorkflowStore
from tests.data.test_corporate_actions import RAW


NOW = datetime(2026, 10, 12, 13, 0, tzinfo=UTC)


def page(data, *, received=NOW):
    return CorporateActionsPage(
        actions=normalize_actions(data),
        requested_at=received - timedelta(seconds=2),
        received_at=received,
        symbols=3,
        window=(date(2026, 8, 28), date(2027, 2, 9)),
        pages=1,
        page_sha256=("deadbeef",),
    )


@pytest.fixture
async def store(temp_db):
    return CorporateActionsStore(WorkflowStore(temp_db), max_actions=5)


def test_build_capture_is_deterministic_and_counts_kinds():
    first = build_capture(page(RAW), as_of=date(2026, 10, 12), symbols=["AAPL", "NVDA", "PXD", "VZIO"])
    again = build_capture(
        page(RAW, received=NOW + timedelta(hours=1)), as_of=date(2026, 10, 13), symbols=["VZIO", "AAPL", "NVDA", "PXD"]
    )
    assert first["content_sha256"] == again["content_sha256"]  # content, not clock or order
    assert first["symbols_sha256"] == again["symbols_sha256"]
    assert first["counts_by_kind"] == {"cash_dividend": 1, "cash_merger": 1, "forward_split": 1, "stock_merger": 1}
    assert first["window"] == ["2026-08-28", "2027-02-09"] and first["as_of"] == "2026-10-12"
    assert first["captured_at"] == NOW.isoformat() and first["pages"] == 1 and first["page_sha256"] == ["deadbeef"]
    assert [a["id"] for a in first["actions"]] == ["div-aapl-1", "merger-pxd", "split-nvda", "merger-vzio"]


async def test_record_appends_capture_once_per_content_and_a_check_every_run(store, temp_db):
    capture = build_capture(page(RAW), as_of=date(2026, 10, 12), symbols=["AAPL"])
    assert await store.record(capture) is True
    same_day = build_capture(page(RAW, received=NOW + timedelta(hours=2)), as_of=date(2026, 10, 12), symbols=["AAPL"])
    assert await store.record(same_day) is False
    captures = await temp_db.workflows.events(CAPTURE_STREAM)
    checks = await temp_db.workflows.events(CHECK_STREAM)
    assert (
        len(captures) == 1
        and captures[0]["kind"] == EventKind.CORPORATE_ACTIONS_CAPTURED == "corporate_actions_captured"
    )
    assert len(checks) == 2 and checks[-1]["kind"] == EventKind.CORPORATE_ACTIONS_CHECKED
    assert (await store.latest_check())["checked_at"] == same_day["captured_at"]
    assert (await store.latest_capture())["content_sha256"] == capture["content_sha256"]


async def test_changed_content_appends_and_a_return_to_old_content_appends_again(store):
    a = build_capture(page(RAW), as_of=date(2026, 10, 12), symbols=["AAPL"])
    b = build_capture(
        page({"forward_splits": RAW["forward_splits"]}, received=NOW + timedelta(days=1)),
        as_of=date(2026, 10, 13),
        symbols=["AAPL"],
    )
    a_again = build_capture(page(RAW, received=NOW + timedelta(days=2)), as_of=date(2026, 10, 14), symbols=["AAPL"])
    assert [await store.record(c) for c in (a, b, a_again)] == [True, True, True]
    assert (await store.latest_capture())["as_of"] == "2026-10-14"


async def test_capture_bound_fails_closed(store):
    many = {"cash_dividends": [{**RAW["cash_dividends"][0], "id": f"d{i}", "symbol": f"S{i}"} for i in range(6)]}
    with pytest.raises(ValueError, match="exceeds 5 actions"):
        await store.record(build_capture(page(many), as_of=date(2026, 10, 12), symbols=["AAPL"]))
    assert await store.latest_capture() is None and await store.latest_check() is None


async def test_lookup_reads_capture_and_check(store):
    assert (await store.lookup(now=NOW, max_age_seconds=3600)).status == "unavailable"
    await store.record(build_capture(page(RAW), as_of=date(2026, 10, 12), symbols=["AAPL"]))
    fresh = await store.lookup(now=NOW + timedelta(minutes=5), max_age_seconds=3600)
    assert fresh.status == "ok" and len(fresh.actions) == 4 and fresh.as_of == date(2026, 10, 12)
    assert (await store.lookup(now=NOW + timedelta(days=5), max_age_seconds=3600)).status == "stale"
