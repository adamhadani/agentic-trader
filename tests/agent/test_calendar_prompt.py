"""The Economic Calendar Status block: rendered from one `CalendarCheck`, it states the evaluation time, the
verdict and its source, and says "verified" only as "UNVERIFIED" when the static calendar is not ok.

The deterministic lockout gate runs before the LLM is consulted; the prompt has to carry that verdict and the
clock it was computed against, otherwise the model refuses candidates because it cannot judge macro timing.
"""

import dataclasses
import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from agentic_trader.agent.calendar import (
    CalendarCheck,
    ForexFactoryCalendar,
    StaticTier1Calendar,
    UnionTier1Calendar,
)
from agentic_trader.agent.prompts import build_system_prompt
from agentic_trader.config import AppConfig


NOW = datetime(2026, 9, 22, 19, 18, 0, tzinfo=UTC)
FIXTURE = Path(__file__).parent.parent / "fixtures/calendar/us-tier1-test-v1.json"
SHA = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
CLEAR = (
    "Evaluation time: 2026-09-22 19:18 UTC. Lockout CLEAR per static tier-1 calendar us-tier1-test-v1 "
    f"(sha256 {SHA[:8]}), valid through 2099-12-31; live cross-check (ForexFactory): ok."
)


def iso(when: datetime) -> str:
    return when.astimezone(UTC).isoformat()


def usd(title: str, when: datetime) -> dict:
    return {"title": title, "country": "USD", "date": iso(when), "impact": "High"}


# An ok live week with no USD release (an empty or unparseable payload is a failed feed).
QUIET = [
    {"title": "ECB Main Refinancing Rate", "country": "EUR", "date": iso(NOW + timedelta(days=1)), "impact": "High"}
]


def verified() -> StaticTier1Calendar:
    return StaticTier1Calendar.load(FIXTURE, None)


def expired() -> StaticTier1Calendar:
    return dataclasses.replace(verified(), valid_through=date(2026, 9, 1))


def invalid() -> StaticTier1Calendar:
    return StaticTier1Calendar.load(FIXTURE, "0" * 64)  # DigestMismatch


async def check(
    static: StaticTier1Calendar,
    items: list[dict] | Exception,
    *,
    pre: int = 60,
    post: int = 30,
) -> CalendarCheck:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(items, Exception):
            raise items
        return httpx.Response(200, json=items)

    live = ForexFactoryCalendar(transport=httpx.MockTransport(handler), clock=lambda: NOW)
    return await UnionTier1Calendar(static, live, unknown_policy="open").check(NOW, pre, post)


async def test_verified_clear_names_the_source_and_says_no_releases():
    text = (await check(verified(), QUIET)).prompt_text()
    assert text == (f"{CLEAR} No tier-1 US releases (FOMC, CPI, PPI, Employment Situation) in the next 24 hours.")


async def test_verified_clear_with_a_failed_live_feed_names_the_failure():
    text = (await check(verified(), httpx.ReadTimeout("t"))).prompt_text()
    assert "; live cross-check (ForexFactory): failed (ReadTimeout). No tier-1 US releases" in text


async def test_verified_with_an_upcoming_release_states_the_delta_and_full_date():
    text = (await check(verified(), [usd("CPI", NOW + timedelta(hours=2, minutes=5))])).prompt_text()
    assert text == (
        f"{CLEAR} Next tier-1 release in 2h 5m. Upcoming tier-1 releases in the next 24 hours:\n"
        "- CPI at 2026-09-22 21:23 UTC (in 2h 5m)"
    )


async def test_upcoming_releases_are_sorted_one_line_each():
    items = [
        usd("NFP", NOW + timedelta(hours=20)),
        usd("CPI", NOW + timedelta(hours=2, minutes=5)),
        usd("PPI", NOW + timedelta(hours=9, minutes=30)),
        usd("CPI", NOW + timedelta(hours=25)),  # beyond 24 hours
    ]
    text = (await check(verified(), items)).prompt_text()
    lines = [line for line in text.splitlines() if line.startswith("- ")]
    assert lines == [
        "- CPI at 2026-09-22 21:23 UTC (in 2h 5m)",
        "- PPI at 2026-09-23 04:48 UTC (in 9h 30m)",
        "- NFP at 2026-09-23 15:18 UTC (in 20h 0m)",
    ]
    assert " Next tier-1 release in 2h 5m. " in text


async def test_inside_the_window_the_block_reports_lockout_active():
    text = (await check(verified(), [usd("FOMC Statement", NOW + timedelta(minutes=30))])).prompt_text()
    assert text == (
        "Evaluation time: 2026-09-22 19:18 UTC. LOCKOUT ACTIVE: Event 'FOMC Statement' scheduled at "
        "2026-09-22 19:48 UTC (lockout window: 60 minutes before and 30 minutes after the event). "
        "Trading lockout in effect."
    )


async def test_the_lockout_window_reflects_the_supplied_minutes():
    text = (await check(verified(), [usd("CPI", NOW + timedelta(minutes=90))], pre=120, post=45)).prompt_text()
    assert "LOCKOUT ACTIVE" in text and "120 minutes before and 45 minutes after" in text


async def test_unverified_states_both_sources_and_unknown_timing():
    text = (await check(expired(), httpx.ReadTimeout("t"))).prompt_text()
    assert text == (
        "Evaluation time: 2026-09-22 19:18 UTC. Lockout UNVERIFIED: static tier-1 calendar expired; "
        "live feed failed (ReadTimeout); treat scheduled-release timing as unknown."
    )


async def test_unverified_lists_live_releases_but_never_claims_there_are_none():
    upcoming = (await check(invalid(), [usd("CPI", NOW + timedelta(hours=2, minutes=5))])).prompt_text()
    assert upcoming.startswith(
        "Evaluation time: 2026-09-22 19:18 UTC. Lockout UNVERIFIED: static tier-1 calendar invalid; live feed ok; "
        "treat scheduled-release timing as unknown. Next tier-1 release in 2h 5m."
    )
    assert "- CPI at 2026-09-22 21:23 UTC (in 2h 5m)" in upcoming
    assert "No tier-1" not in (await check(invalid(), QUIET)).prompt_text()


BRANCHES: dict[str, Callable[[], Awaitable[CalendarCheck]]] = {
    "verified clear": lambda: check(verified(), QUIET),
    "verified clear, live failed": lambda: check(verified(), httpx.ReadTimeout("t")),
    "verified upcoming": lambda: check(verified(), [usd("CPI", NOW + timedelta(hours=3))]),
    "verified lockout": lambda: check(verified(), [usd("CPI", NOW + timedelta(minutes=10))]),
    "expired, live ok": lambda: check(expired(), QUIET),
    "expired, live failed": lambda: check(expired(), httpx.ReadTimeout("t")),
    "invalid, live ok": lambda: check(invalid(), QUIET),
    "invalid, live failed": lambda: check(invalid(), httpx.ConnectError("x")),
    "expired, live upcoming": lambda: check(expired(), [usd("PPI", NOW + timedelta(hours=3))]),
    "invalid, live lockout": lambda: check(invalid(), [usd("PPI", NOW + timedelta(minutes=10))]),
}


@pytest.mark.parametrize("branch", list(BRANCHES))
async def test_the_prompt_says_verified_only_when_the_static_calendar_is_ok(branch):
    check_ = await BRANCHES[branch]()
    text = check_.prompt_text().lower()
    if check_.verified:
        assert "unverified" not in text
    else:
        assert text.count("verified") == text.count("unverified") >= 1


def test_system_prompt_claims_no_verification():
    prompt = build_system_prompt(AppConfig())
    assert "verified" not in prompt.lower()
    assert (
        "Macro lockout is checked deterministically before you are consulted; the Economic Calendar Status below "
        "states the result, its source and the evaluation time." in prompt
    )
    assert "Do not reject a candidate on macro timing unless that status says LOCKOUT ACTIVE." in prompt
