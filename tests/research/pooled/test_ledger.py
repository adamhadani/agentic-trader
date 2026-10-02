# tests/research/pooled/test_ledger.py
import asyncio
import inspect
from datetime import date

import pytest

from agentic_trader.research.pooled.ledger import CampaignAborted, JournalLedger
from agentic_trader.storage.alpha import AlphaRepository


RECORD = {
    "protocol_sha256": "p" * 64,
    "cohort_sha256": "c" * 64,
    "cube_sha256": "k" * 64,
    "code_revision": "abc1234",
    "budget": 200,
    "journal_scope": "test/paper",
}
OTHER = {**RECORD, "protocol_sha256": "q" * 64, "cohort_sha256": "d" * 64}
INTERVAL = (date(2024, 1, 2), date(2026, 7, 31))


@pytest.fixture
async def repository(temp_db):
    await temp_db.init_db()
    yield AlphaRepository(temp_db.workflows)
    await temp_db.engine.dispose()


async def test_a_reservation_is_immutable_and_a_resume_returns_it(repository):
    first = await repository.reserve_pooled_campaign("cmp", RECORD)
    assert first["status"] == "reserved" and first["code_revision"] == "abc1234"
    assert await repository.reserve_pooled_campaign("cmp", RECORD) == first
    with pytest.raises(ValueError, match="immutable"):
        await repository.reserve_pooled_campaign("cmp", {**RECORD, "code_revision": "def5678"})
    with pytest.raises(ValueError, match="immutable"):
        await repository.reserve_pooled_campaign("cmp", {**RECORD, "journal_scope": "production/alpaca:paper"})
    assert (await repository.get("pooled/ledger"))["campaigns"] == 1
    assert await repository.get("family/all") is None  # the pooled lane never touches the global family


async def test_a_family_cannot_be_charged_past_its_reservation(repository):
    await repository.reserve_pooled_campaign("cmp", RECORD)
    with pytest.raises(ValueError, match="not reserved"):
        await repository.charge_pooled_formula("cmp", "rev", formula_id="f0", expression="returns", nodes=2)
    await repository.reserve_pooled_family("cmp", "rev", 2)
    await repository.reserve_pooled_family("cmp", "rev", 2)  # idempotent
    with pytest.raises(ValueError, match="immutable"):
        await repository.reserve_pooled_family("cmp", "rev", 3)
    assert await repository.charge_pooled_formula("cmp", "rev", formula_id="f1", expression="a", nodes=2)
    assert await repository.charge_pooled_formula("cmp", "rev", formula_id="f2", expression="b", nodes=2)
    with pytest.raises(ValueError, match="exhausted"):
        await repository.charge_pooled_formula("cmp", "rev", formula_id="f3", expression="c", nodes=2)
    assert (await repository.get("pooled/ledger"))["formulas_charged"] == 2


async def test_charging_the_same_formula_again_is_a_no_op(repository):
    await repository.reserve_pooled_campaign("cmp", RECORD)
    await repository.reserve_pooled_family("cmp", "rev", 2)
    assert await repository.charge_pooled_formula("cmp", "rev", formula_id="f1", expression="a", nodes=2) is True
    assert await repository.charge_pooled_formula("cmp", "rev", formula_id="f1", expression="a", nodes=2) is False
    with pytest.raises(ValueError, match="another expression"):
        await repository.charge_pooled_formula("cmp", "rev", formula_id="f1", expression="z", nodes=2)
    assert (await repository.get("pooled/campaign/cmp/family/rev"))["charged"] == 1
    assert (await repository.get("pooled/ledger"))["formulas_charged"] == 1


async def test_the_confirmation_ledger_is_lane_wide(repository):
    await repository.reserve_pooled_campaign("one", RECORD)
    await repository.reserve_pooled_campaign("two", OTHER)
    await repository.consume_pooled_confirmation(
        campaign_id="one", cohort_sha256="c" * 64, interval=INTERVAL, candidates=("f1",)
    )
    with pytest.raises(ValueError, match="already consumed by campaign one"):
        await repository.consume_pooled_confirmation(
            campaign_id="two", cohort_sha256="d" * 64, interval=(date(2026, 7, 1), date(2027, 6, 30)), candidates=()
        )
    await repository.consume_pooled_confirmation(
        campaign_id="two", cohort_sha256="d" * 64, interval=(date(2026, 8, 3), date(2027, 7, 30)), candidates=()
    )
    consumed = await repository.get("pooled/confirmation")
    assert [item["campaign_id"] for item in consumed["intervals"]] == ["one", "two"]
    assert consumed["intervals"][0]["candidates"] == ["f1"]
    assert (await repository.get("pooled/ledger"))["confirmations"] == 2
    assert (await repository.get("pooled/campaign/one"))["status"] == "confirmation_consumed"


async def test_a_campaign_that_consumed_its_confirmation_or_completed_cannot_run_again(repository):
    await repository.reserve_pooled_campaign("one", RECORD)
    await repository.consume_pooled_confirmation(
        campaign_id="one", cohort_sha256="c" * 64, interval=INTERVAL, candidates=()
    )
    await repository.advance_pooled_campaign("one", "failed", {"error": "crashed after consuming"})
    with pytest.raises(ValueError, match="cannot run again"):
        await repository.reserve_pooled_campaign("one", RECORD)
    await repository.reserve_pooled_campaign("two", OTHER)
    await repository.advance_pooled_campaign("two", "completed", {"status": "no_finalists"})
    with pytest.raises(ValueError, match="cannot run again"):
        await repository.reserve_pooled_campaign("two", OTHER)
    with pytest.raises(ValueError, match="completed"):
        await repository.advance_pooled_campaign("two", "failed")


async def test_status_reports_the_pooled_ledger(repository):
    await repository.reserve_pooled_campaign("cmp", RECORD)
    assert (await repository.status())["pooled_ledger"] == {"formulas_charged": 0, "campaigns": 1, "confirmations": 0}


async def test_the_journal_ledger_runs_from_a_worker_thread_and_refuses_the_event_loop(repository):
    await repository.reserve_pooled_campaign("cmp", RECORD)
    ledger = JournalLedger(repository, asyncio.get_running_loop(), "cmp")

    def work():
        ledger.reserve_family("rev", 1)
        ledger.charge("rev", "returns", "f1", 2)
        ledger.advance("frozen", {"candidates": ["f1"]})
        ledger.consume_confirmation(cohort_sha256="c" * 64, interval=INTERVAL, campaign_id="cmp", candidates=("f1",))

    await asyncio.to_thread(work)
    assert (await repository.get("pooled/campaign/cmp/family/rev"))["charged"] == 1
    campaign = await repository.get("pooled/campaign/cmp")
    assert campaign["details"]["frozen"] == {"candidates": ["f1"]}
    assert campaign["status"] == "confirmation_consumed"
    with pytest.raises(RuntimeError, match="worker thread"):
        ledger.charge("rev", "other", "f2", 2)
    with pytest.raises(ValueError, match="belongs to campaign cmp"):
        await asyncio.to_thread(
            ledger.consume_confirmation, cohort_sha256="c" * 64, interval=INTERVAL, campaign_id="x", candidates=()
        )


async def test_an_aborted_journal_ledger_starts_no_further_write(repository, monkeypatch):
    await repository.reserve_pooled_campaign("cmp", RECORD)
    ledger = JournalLedger(repository, asyncio.get_running_loop(), "cmp")
    await asyncio.to_thread(ledger.reserve_family, "rev", 2)
    refused = []
    real_charge = repository.charge_pooled_formula

    def watched_charge(*args, **kwargs):
        refused.append(real_charge(*args, **kwargs))
        return refused[-1]

    monkeypatch.setattr(repository, "charge_pooled_formula", watched_charge)
    ledger.abort()

    def work():
        with pytest.raises(CampaignAborted):
            ledger.charge("rev", "returns", "f1", 2)
        with pytest.raises(CampaignAborted):
            ledger.consume_confirmation(cohort_sha256="c" * 64, interval=INTERVAL, campaign_id="cmp", candidates=())
        with pytest.raises(CampaignAborted):
            ledger.advance("frozen", {"candidates": []})

    await asyncio.to_thread(work)
    # The refused coroutine was closed, never scheduled or left unawaited.
    assert [inspect.getcoroutinestate(coroutine) for coroutine in refused] == [inspect.CORO_CLOSED]
    assert (await repository.get("pooled/campaign/cmp/family/rev"))["charged"] == 0
    assert await repository.get("pooled/confirmation") is None
    assert (await repository.get("pooled/campaign/cmp"))["status"] == "reserved"


async def test_a_journal_call_that_never_finishes_times_out(repository, monkeypatch):
    release = asyncio.Event()

    async def stuck(*args, **kwargs):
        await release.wait()

    monkeypatch.setattr(repository, "reserve_pooled_family", stuck)
    ledger = JournalLedger(repository, asyncio.get_running_loop(), "cmp", timeout=0.05)
    with pytest.raises(TimeoutError, match="did not finish"):
        await asyncio.to_thread(ledger.reserve_family, "rev", 1)
    release.set()  # the write that started is allowed to finish
    await asyncio.sleep(0.01)
