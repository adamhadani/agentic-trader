from datetime import UTC, date, datetime, timedelta

from agentic_trader.diagnostics.incidents import (
    ComponentTally,
    GapClose,
    GapPosition,
    IncidentPhase,
    OpenIncident,
    QuietHoursSummary,
    ReleaseExitGaps,
)
from agentic_trader.presentation.operations import format_quiet_hours_summary


START = datetime(2026, 11, 2, 21, 30, tzinfo=UTC)
END = datetime(2026, 11, 3, 14, 15, tzinfo=UTC)
FAILED = datetime(2026, 11, 3, 3, 20, tzinfo=UTC)


def make(tallies, opens, failure_seconds=120):
    return QuietHoursSummary(
        period_start=START,
        period_end=END,
        observed_at=END,
        failure_seconds=failure_seconds,
        tallies=tallies,
        open_incidents=opens,
    )


def incident(component, phase=IncidentPhase.OPEN, *, detail="", ready_now=False, failed_since=FAILED):
    return OpenIncident(component=component, phase=phase, failed_since=failed_since, detail=detail, ready_now=ready_now)


def test_summary_copy():
    text = format_quiet_hours_summary(
        make(
            [
                ComponentTally(component="accounting", opened=2, recovered=2),
                ComponentTally(component="scan", opened=1, recovered=0),
            ],
            [incident("scan", detail="age <limit>")],
        )
    )
    lines = text.split("\n")
    assert lines[0].endswith("Overnight operations summary</b>")
    assert "Quiet hours Mon 16:30 → Tue 09:15 ET: 3 alerts and 2 recoveries held across 2 components." in lines
    assert "• accounting: 2 opened, 2 recovered" in lines
    assert "• scan: 1 opened" in lines
    assert "Still open:" in lines
    assert "• scan (open since Mon 22:20 ET): age &lt;limit&gt;" in lines
    assert "An incident still failing 2 min after this summary sends its own alert." in lines
    assert "Incidents that open and recover overnight usually mean the host slept (<code>pmset -g log</code>)." in lines
    assert lines[-1] == "Inspect <code>copilot doctor --readiness</code> and <code>copilot db incidents</code>."


def test_summary_caps_long_lists():
    tallies = [ComponentTally(component=f"c{i:02d}", opened=1, recovered=0) for i in range(13)]
    opens = [
        incident(f"o{i:02d}", IncidentPhase.RECOVERING, failed_since=FAILED + timedelta(minutes=i)) for i in range(11)
    ]
    text = format_quiet_hours_summary(make(tallies, opens, failure_seconds=90))
    assert "\n… +1 more component\n" in text and "\n… +1 more\n" in text
    assert text.count("• c") == 12 and text.count("• o") == 10
    assert "• o00 (recovering; failing since Mon 22:20 ET)\n" in text
    assert "failing 90 s after this summary" in text
    more = [ComponentTally(component=f"c{i:02d}", opened=1, recovered=0) for i in range(14)]
    assert "\n… +2 more components\n" in format_quiet_hours_summary(make(more, []))


def test_empty_tallies_copy():
    text = format_quiet_hours_summary(make([], [incident("scan")]))
    assert "no alerts held." in text and "pmset" not in text
    assert "• scan (open since Mon 22:20 ET)\n" in text


def test_still_open_lines_show_this_pass_readiness_without_contradicting_detail():
    fresh = "Observation age: 5s; limit: 18000s"
    stale = "Observation age: 40000s; limit: 18000s"
    text = format_quiet_hours_summary(
        make(
            [],
            [
                incident("scan", detail=fresh, ready_now=True),
                incident("accounting", detail=stale),
                incident("telegram", IncidentPhase.RECOVERING, detail=fresh, ready_now=True),
                incident("worker", IncidentPhase.RECOVERING, detail=stale),
            ],
        )
    )
    lines = text.split("\n")
    assert "• scan (open since Mon 22:20 ET; ready now)" in lines
    assert f"• accounting (open since Mon 22:20 ET): {stale}" in lines
    assert "• telegram (recovering; failing since Mon 22:20 ET)" in lines
    assert f"• worker (recovering; failing since Mon 22:20 ET): {stale}" in lines
    assert "5s" not in text


def test_release_exit_gaps_render_fixed_blocks_and_nothing_when_absent():
    base = format_quiet_hours_summary(make([], []))
    assert "Release exit" not in base
    gaps = ReleaseExitGaps(
        trading_day=date(2026, 11, 2),
        missing_pass=True,
        unknown=[GapPosition(symbol="ACME", signal_id="sig-1")],
        unnotified_closes=[GapClose(symbol="BETA", signal_id="sig-2", outcome="refused <x>")],
    )
    summary = make([], [])
    summary = summary.model_copy(update={"release_exit": gaps})
    text = format_quiet_hours_summary(summary)
    assert "Release exit 2026-11-02:" in text
    assert "No release-exit pass was journaled for 2026-11-02." in text
    assert "ACME (sig-1): earnings timing unknown; review and run <code>/close sig-1</code> if needed." in text
    assert "BETA (sig-2): close outcome refused &lt;x&gt; was never notified; run <code>/close sig-2</code>." in text
    only_missing = format_quiet_hours_summary(
        summary.model_copy(update={"release_exit": gaps.model_copy(update={"unknown": [], "unnotified_closes": []})})
    )
    assert "/close" not in only_missing
