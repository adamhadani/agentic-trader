from datetime import UTC, datetime, timedelta

from agentic_trader.diagnostics.incidents import ComponentTally, IncidentPhase, OpenIncident, QuietHoursSummary
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


def test_summary_copy():
    text = format_quiet_hours_summary(
        make(
            [
                ComponentTally(component="accounting", opened=2, recovered=2),
                ComponentTally(component="scan", opened=1, recovered=0),
            ],
            [OpenIncident(component="scan", phase=IncidentPhase.OPEN, failed_since=FAILED, detail="age <limit>")],
        )
    )
    lines = text.split("\n")
    assert lines[0].endswith("Overnight operations summary</b>")
    assert "Quiet hours Mon 16:30 → Tue 09:15 ET: 3 alerts and 2 recoveries held across 2 components." in lines
    assert "• accounting: 2 opened, 2 recovered" in lines
    assert "• scan: 1 opened" in lines
    assert "Still open:" in lines
    assert "• scan (open since Mon 22:20 ET): age &lt;limit&gt;" in lines
    assert "Each incident still failing 2 min into alert hours sends its own alert." in lines
    assert "Incidents that open and recover overnight usually mean the host slept (<code>pmset -g log</code>)." in lines
    assert lines[-1] == "Inspect <code>copilot doctor --readiness</code> and <code>copilot db incidents</code>."


def test_summary_caps_long_lists():
    tallies = [ComponentTally(component=f"c{i:02d}", opened=1, recovered=0) for i in range(13)]
    opens = [
        OpenIncident(
            component=f"o{i:02d}", phase=IncidentPhase.RECOVERING, failed_since=FAILED + timedelta(minutes=i), detail=""
        )
        for i in range(11)
    ]
    text = format_quiet_hours_summary(make(tallies, opens, failure_seconds=90))
    assert "… +1 more components" in text and "\n… +1 more\n" in text
    assert text.count("• c") == 12 and text.count("• o") == 10
    assert "(recovering since" in text and "ET)\n" in text
    assert "failing 90 s into" in text


def test_empty_tallies_copy():
    text = format_quiet_hours_summary(
        make([], [OpenIncident(component="scan", phase=IncidentPhase.OPEN, failed_since=FAILED, detail="")])
    )
    assert "no alerts held." in text and "pmset" not in text
    assert "• scan (open since Mon 22:20 ET)\n" in text
