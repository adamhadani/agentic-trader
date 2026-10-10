from datetime import date

import pytest

from agentic_trader.storage.alpha import AlphaRepository


INTERVAL = (date(2024, 1, 2), date(2026, 7, 31))


@pytest.fixture
async def repository(temp_db):
    await temp_db.init_db()
    yield AlphaRepository(temp_db.workflows)
    await temp_db.engine.dispose()


async def test_confirmation_is_journaled_once_per_lane_and_overlaps_are_refused(repository):
    record = await repository.consume_lane_confirmation(
        "spread",
        protocol_sha256="a" * 64,
        cohort_sha256="c" * 64,
        interval=INTERVAL,
        detail={"stage": "discovery_passed"},
    )
    assert (
        record["start"] == "2024-01-02"
        and record["end"] == "2026-07-31"
        and record["detail"] == {"stage": "discovery_passed"}
    )
    stored = await repository.get("spread/confirmation")
    assert len(stored["intervals"]) == 1 and stored["intervals"][0]["protocol_sha256"] == "a" * 64
    with pytest.raises(ValueError, match="already consumed"):
        await repository.consume_lane_confirmation(
            "spread",
            protocol_sha256="b" * 64,
            cohort_sha256="c" * 64,
            interval=(date(2026, 7, 1), date(2027, 6, 30)),
            detail={},
        )
    await repository.consume_lane_confirmation(
        "spread",
        protocol_sha256="b" * 64,
        cohort_sha256="c" * 64,
        interval=(date(2026, 8, 3), date(2027, 7, 30)),
        detail={},
    )
    assert len((await repository.get("spread/confirmation"))["intervals"]) == 2
    assert await repository.get("family/all") is None
    assert await repository.get("pooled/confirmation") is None  # lanes do not share a ledger
    await repository.consume_lane_confirmation(
        "basket", protocol_sha256="e" * 64, cohort_sha256="c" * 64, interval=INTERVAL, detail={}
    )


@pytest.mark.parametrize(
    ("lane", "kwargs", "match"),
    [
        ("pooled", {}, "pooled"),
        ("Spread", {}, "lane"),
        ("s", {}, "lane"),
        ("spread", {"protocol_sha256": "zz"}, "sha256"),
        ("spread", {"interval": (date(2026, 7, 31), date(2024, 1, 2))}, "ordered"),
    ],
)
async def test_invalid_requests_are_refused_before_any_write(repository, lane, kwargs, match):
    base = {"protocol_sha256": "a" * 64, "cohort_sha256": "c" * 64, "interval": INTERVAL, "detail": {}}
    with pytest.raises(ValueError, match=match):
        await repository.consume_lane_confirmation(lane, **{**base, **kwargs})
    assert await repository.get(f"{lane}/confirmation") is None
