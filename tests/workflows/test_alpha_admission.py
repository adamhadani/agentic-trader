"""An alpha's lifecycle is checked by the durable submission transaction."""

from dataclasses import asdict

import pytest
from sqlalchemy import select

from agentic_trader.broker.base import OrderRequest
from agentic_trader.execution.durable import EventKind, WorkStatus
from agentic_trader.market.bars import SessionClockPolicy
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.validation import ValidationPolicy
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.models import SignalRecord


@pytest.fixture
async def alpha_entry(store, request):
    definition = AlphaDefinition(
        "alpha_admission",
        "Admission",
        "close",
        timeframe="1d",
        eligible_symbols=("SPY",),
        data_feed="alpaca:sip",
        **getattr(request, "param", {}),
    )
    repository = AlphaRepository(store)
    await repository.register(definition, actor="fixture")
    # Isolate entry fencing from qualification statistics. Qualification and its
    # shadow gates are exercised through the public API in journal tests.
    async with store.db.session_factory() as session, session.begin():
        await store.lock(session, resource="alpha")
        await repository._append(
            session,
            "registry",
            {"generation": 1, "active": [definition.version_id], "shadow": []},
            EventKind.ALPHA_REGISTRY,
            "fixture",
        )
        await repository._append(
            session,
            f"qualification/{definition.version_id}",
            {"policy": asdict(ValidationPolicy())},
            EventKind.ALPHA_RESEARCH,
            "fixture",
        )
    sid = await store.db.record_signal(
        "SPY",
        definition.alpha_id,
        "LONG",
        100,
        98,
        104,
        2,
        asset_class="EQUITY",
        quantity=1,
        timeframe="1d",
        alpha_version=definition.version_id,
        alpha_policy=definition.execution.to_dict(),
    )
    request = OrderRequest(
        signal_id=sid,
        symbol="SPY",
        asset_class="EQUITY",
        direction="LONG",
        quantity=1,
        entry_price=100,
        stop_loss=98,
        take_profit=104,
    )
    return repository, definition, request


@pytest.mark.parametrize("defect", ["demoted", "missing_version", "mismatched_policy", "obsolete_qualification"])
async def test_unqualified_or_changed_alpha_cannot_reserve_new_risk(store, app_config, alpha_entry, defect):
    repository, definition, request = alpha_entry
    if defect == "demoted":
        await repository.demote(definition.version_id, actor="test", expected_generation=1)
    elif defect == "obsolete_qualification":
        async with store.db.session_factory() as session, session.begin():
            await repository._append(
                session,
                f"qualification/{definition.version_id}",
                {"policy": {}},
                EventKind.ALPHA_RESEARCH,
                "fixture",
            )
    else:
        async with store.db.session_factory() as session, session.begin():
            signal = await session.scalar(select(SignalRecord).where(SignalRecord.id == request.signal_id))
            if defect == "missing_version":
                signal.alpha_version = None
            else:
                signal.alpha_policy = "{}"
    item, reason = await store.enqueue_entry(request, app_config)
    assert item is None
    assert "alpha" in reason.lower()


@pytest.mark.parametrize("change", ["demotion", "obsolete_qualification"])
async def test_alpha_change_during_preflight_blocks_submission_commit(store, app_config, alpha_entry, change):
    repository, definition, request = alpha_entry
    item, reason = await store.enqueue_entry(request, app_config)
    assert item, reason
    claim = await store.claim_entry(lease_seconds=60)
    if change == "demotion":
        await repository.demote(definition.version_id, actor="test", expected_generation=1)
    else:
        async with store.db.session_factory() as session, session.begin():
            await store.lock(session, resource="alpha")
            await repository._append(
                session,
                f"qualification/{definition.version_id}",
                {"policy": {}},
                EventKind.ALPHA_RESEARCH,
                "fixture",
            )
    assert not await store.begin_submission(claim)
    assert (await store.get_work(item.id)).status == WorkStatus.CHECKING


@pytest.mark.parametrize("alpha_entry", [{"semantics_version": 3, "clock": SessionClockPolicy()}], indirect=True)
async def test_session_clock_cannot_reserve_risk_even_with_corrupt_active_projection(store, app_config, alpha_entry):
    _, _, request = alpha_entry
    item, reason = await store.enqueue_entry(request, app_config)
    assert item is None
    assert "session" in reason.lower()
