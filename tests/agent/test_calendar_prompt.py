"""Macro summary prompt text must state the evaluation time and the computed clearance.

The deterministic lockout gate runs before the LLM is consulted; the prompt has to
carry that verdict and the clock it was computed against, otherwise the model
refuses candidates because it cannot verify macro timing itself.
"""

from datetime import UTC, datetime, timedelta

import pytest

from agentic_trader.agent.calendar import BaseEconomicCalendar, MacroEvent
from agentic_trader.agent.prompts import build_system_prompt
from agentic_trader.config import AppConfig


NOW = datetime(2026, 9, 22, 19, 18, 0, tzinfo=UTC)


class FakeCalendar(BaseEconomicCalendar):
    def __init__(self, events: list[MacroEvent] | None = None) -> None:
        self._events = events or []

    async def fetch_events(self, force_refresh: bool = False) -> list[MacroEvent]:
        return self._events


def tier1(title: str, when: datetime) -> MacroEvent:
    return MacroEvent(title=title, country="USD", impact="High", timestamp=when)


@pytest.mark.asyncio
async def test_summary_without_events_states_time_and_clearance():
    summary = await FakeCalendar([]).get_macro_summary_for_prompt(now=NOW)

    assert summary.startswith("Evaluation time: 2026-09-22 19:18 UTC.")
    assert "Lockout verified: CLEAR" in summary
    assert "No Tier-1 US economic releases (CPI, PPI, FOMC, NFP) scheduled in the next 24 hours." in summary


@pytest.mark.asyncio
async def test_summary_with_upcoming_event_states_delta_and_full_date():
    calendar = FakeCalendar([tier1("CPI", NOW + timedelta(hours=18, minutes=47))])

    summary = await calendar.get_macro_summary_for_prompt(now=NOW)

    assert "Evaluation time: 2026-09-22 19:18 UTC." in summary
    assert "Lockout verified: CLEAR (next Tier-1 event in 18h 47m)." in summary
    assert "Upcoming Tier-1 releases in the next 24 hours:" in summary
    assert "- CPI at 2026-09-23 14:05 UTC (in 18h 47m)" in summary


@pytest.mark.asyncio
async def test_summary_inside_pre_window_reports_lockout_active():
    calendar = FakeCalendar([tier1("FOMC Statement", NOW + timedelta(minutes=30))])

    summary = await calendar.get_macro_summary_for_prompt(now=NOW)

    assert summary.startswith("Evaluation time: 2026-09-22 19:18 UTC.")
    assert "LOCKOUT ACTIVE" in summary
    assert "FOMC Statement" in summary
    assert "2026-09-22 19:48 UTC" in summary
    assert "60 minutes before" in summary
    assert "30 minutes after" in summary
    assert "CLEAR" not in summary


@pytest.mark.asyncio
async def test_summary_lockout_window_reflects_supplied_minutes():
    calendar = FakeCalendar([tier1("CPI", NOW + timedelta(minutes=90))])

    summary = await calendar.get_macro_summary_for_prompt(now=NOW, pre_minutes=120, post_minutes=45)

    assert "LOCKOUT ACTIVE" in summary
    assert "120 minutes before" in summary
    assert "45 minutes after" in summary


@pytest.mark.asyncio
async def test_summary_lists_multiple_events_sorted_with_own_deltas():
    calendar = FakeCalendar(
        [
            tier1("NFP", NOW + timedelta(hours=20)),
            tier1("CPI", NOW + timedelta(hours=2, minutes=5)),
            tier1("PPI", NOW + timedelta(hours=9, minutes=30)),
        ]
    )

    summary = await calendar.get_macro_summary_for_prompt(now=NOW)

    lines = [line for line in summary.splitlines() if line.startswith("- ")]
    assert lines == [
        "- CPI at 2026-09-22 21:23 UTC (in 2h 5m)",
        "- PPI at 2026-09-23 04:48 UTC (in 9h 30m)",
        "- NFP at 2026-09-23 15:18 UTC (in 20h 0m)",
    ]
    assert "Lockout verified: CLEAR (next Tier-1 event in 2h 5m)." in summary


def test_system_prompt_tells_model_the_lockout_is_already_verified():
    prompt = build_system_prompt(AppConfig())

    assert "verified deterministically before you are consulted" in prompt
    assert "Do not reject a candidate on macro timing unless that status says LOCKOUT ACTIVE." in prompt
