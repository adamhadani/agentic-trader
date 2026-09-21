import pytest

from agentic_trader.storage.models import SignalRecord


@pytest.mark.asyncio
async def test_signal_database_operations(temp_db):
    # Check no duplicate initially
    is_dup = await temp_db.is_duplicate_recent("/MES", "TREND_PULLBACK", hours=12)
    assert not is_dup

    # Record a signal
    sig_id = await temp_db.record_signal(
        contract="/MES",
        strategy="TREND_PULLBACK",
        direction="LONG",
        entry_price=5800.0,
        stop_loss=5760.0,
        take_profit=5880.0,
        risk_dollars=200.0,
        reward_dollars=400.0,
        notional_value=29000.0,
        status="PENDING",
    )
    assert sig_id > 0

    # Now it should be detected as duplicate within 12 hours
    is_dup = await temp_db.is_duplicate_recent("/MES", "TREND_PULLBACK", hours=12)
    assert is_dup

    # Different contract or strategy should NOT be duplicate
    assert not await temp_db.is_duplicate_recent("/MNQ", "TREND_PULLBACK", hours=12)
    assert not await temp_db.is_duplicate_recent("/MES", "SQUEEZE_BREAKOUT", hours=12)

    # Initial active exposure should be 0 because status is PENDING
    assert await temp_db.get_active_notional_exposure() == 0.0

    # Update to EXECUTED
    await temp_db.update_signal_status(sig_id, "EXECUTED")
    assert await temp_db.get_active_notional_exposure() == 29000.0
    assert await temp_db.get_active_contract_count() == 1

    # Record second executed signal
    await temp_db.record_signal(
        contract="/MNQ",
        strategy="SQUEEZE_BREAKOUT",
        direction="LONG",
        entry_price=20000.0,
        stop_loss=19900.0,
        take_profit=20200.0,
        risk_dollars=200.0,
        reward_dollars=400.0,
        notional_value=40000.0,
        status="EXECUTED",
    )
    assert await temp_db.get_active_notional_exposure() == 69000.0
    assert await temp_db.get_active_contract_count() == 2

    # Dismiss first signal
    await temp_db.update_signal_status(sig_id, "DISMISSED")
    assert await temp_db.get_active_notional_exposure() == 40000.0
    assert await temp_db.get_active_contract_count() == 1


@pytest.mark.asyncio
async def test_closing_a_probe_signal_tags_the_exit_notification(temp_db):
    """A signal recorded with decision_provenance={"paper_probe": True} gets a
    tagged exit notice; an otherwise-identical twin without the tag does not."""
    probe_id = await temp_db.record_signal(
        contract="AAPL",
        strategy="alpha_probe",
        direction="LONG",
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        risk_dollars=50.0,
        asset_class="EQUITY",
        quantity=1,
        status="EXECUTED",
        decision_provenance={"paper_probe": True},
    )
    assert await temp_db.close_position(
        signal_id=probe_id,
        exit_price=110.0,
        exit_reason="take_profit",
        realized_pnl=100.0,
        status="CLOSED_WIN",
    )
    tagged_item = await temp_db.workflows.claim_notification(lease_seconds=5, max_attempts=3)
    assert tagged_item is not None
    assert tagged_item.payload["arguments"]["strategy"] == "🧪 PAPER PROBE · alpha_probe"

    twin_id = await temp_db.record_signal(
        contract="AAPL",
        strategy="alpha_probe",
        direction="LONG",
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        risk_dollars=50.0,
        asset_class="EQUITY",
        quantity=1,
        status="EXECUTED",
    )
    assert await temp_db.close_position(
        signal_id=twin_id,
        exit_price=110.0,
        exit_reason="take_profit",
        realized_pnl=100.0,
        status="CLOSED_WIN",
    )
    untagged_item = await temp_db.workflows.claim_notification(lease_seconds=5, max_attempts=3)
    assert untagged_item is not None
    assert untagged_item.payload["arguments"]["strategy"] == "alpha_probe"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed_provenance",
    ["not json", "[1, 2]", '"a string"', "null", '{"paper_probe": false}', None],
)
async def test_malformed_or_untagged_provenance_never_blocks_a_close(temp_db, malformed_provenance):
    """The cosmetic paper-probe tag must fail open: a corrupt, non-object, or
    tag-false decision_provenance must never abort a real position close, and
    the exit notice stays untagged in every case."""
    signal_id = await temp_db.record_signal(
        contract="AAPL",
        strategy="alpha_probe",
        direction="LONG",
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        risk_dollars=50.0,
        asset_class="EQUITY",
        quantity=1,
        status="EXECUTED",
    )
    # record_signal always writes valid JSON (or leaves the column NULL); plant a
    # malformed value directly, as the public API cannot produce one.
    async with temp_db.session_factory() as session, session.begin():
        row = await session.get(SignalRecord, signal_id)
        row.decision_provenance = malformed_provenance

    assert await temp_db.close_position(
        signal_id=signal_id,
        exit_price=110.0,
        exit_reason="take_profit",
        realized_pnl=100.0,
        status="CLOSED_WIN",
    )

    async with temp_db.session_factory() as session:
        closed = await session.get(SignalRecord, signal_id)
    assert closed.status == "CLOSED_WIN"
    assert closed.realized_pnl == 100.0

    item = await temp_db.workflows.claim_notification(lease_seconds=5, max_attempts=3)
    assert item is not None
    assert item.payload["arguments"]["strategy"] == "alpha_probe"
