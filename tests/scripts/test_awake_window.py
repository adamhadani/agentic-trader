"""scripts/awake.sh holds the host awake only inside the US market window, computed in New York."""

import os
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "awake.sh"


def run_awake(now_utc: str) -> subprocess.CompletedProcess[str]:
    env = {"PATH": os.environ["PATH"], "AWAKE_NOW_UTC": now_utc, "AWAKE_DRY_RUN": "1"}
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, check=False, timeout=20)


@pytest.mark.parametrize(
    ("now_utc", "seconds"),
    [
        ("2026-10-06T14:00:00Z", 23400),  # Tue 10:00 EDT
        ("2026-10-06T13:15:00Z", 26100),  # Tue 09:15 EDT, window start inclusive
        ("2026-10-06T20:29:59Z", 1),  # Tue 16:29:59 EDT
        ("2026-11-03T15:00:00Z", 23400),  # Tue 10:00 EST, after US DST ended 2026-11-01
    ],
)
def test_inside_window_reports_seconds_to_1630_et(now_utc, seconds):
    result = run_awake(now_utc)
    assert result.returncode == 0
    assert result.stdout.strip() == str(seconds)


@pytest.mark.parametrize(
    "now_utc",
    [
        "2026-10-06T20:31:00Z",  # Tue 16:31 EDT
        "2026-10-06T20:30:00Z",  # Tue 16:30 EDT, end exclusive
        "2026-10-06T13:14:59Z",  # Tue 09:14:59 EDT
        "2026-10-10T14:00:00Z",  # Sat 10:00 EDT
        "2026-10-11T14:00:00Z",  # Sun 10:00 EDT
        "2026-11-03T14:00:00Z",  # Tue 09:00 EST: only 10:00 if DST were wrongly applied
        "2026-11-03T21:30:00Z",  # Tue 16:30 EST
    ],
)
def test_outside_window_exits_silently(now_utc):
    result = run_awake(now_utc)
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
