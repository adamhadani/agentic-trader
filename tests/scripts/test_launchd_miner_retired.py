import os
import stat
import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/launchd.sh"
PLIST = "com.agentictrader.alphaminer.plist"
NOTICE = "Alpha miner retired 2026-10-14"


@pytest.fixture
def sandbox(tmp_path):
    """A throwaway HOME/cwd and a ``launchctl`` stub that logs its arguments and succeeds."""
    home = tmp_path
    stubs = tmp_path / "bin"
    stubs.mkdir()
    log = tmp_path / "launchctl.log"
    stub = stubs / "launchctl"
    stub.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\nexit 0\n')
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return home, stubs, log


def _run(action: str, sandbox) -> subprocess.CompletedProcess[str]:
    home, stubs, _ = sandbox
    return subprocess.run(
        ["bash", str(SCRIPT), action],
        capture_output=True,
        text=True,
        cwd=home,
        env={**os.environ, "HOME": str(home), "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}"},
        check=False,
    )


def _no_stray_data_directory(home: Path) -> None:
    assert not (home / "data").exists() and not (home.parent / "data").exists()


def test_install_miner_is_retired_and_installs_nothing(sandbox):
    home, _, _ = sandbox
    result = _run("install-miner", sandbox)
    assert result.returncode == 0, result.stderr
    assert NOTICE in result.stdout
    assert not (home / "Library/LaunchAgents" / PLIST).exists()
    _no_stray_data_directory(home)


def test_run_miner_is_retired_and_runs_nothing(sandbox):
    home, _, _ = sandbox
    result = _run("run-miner", sandbox)
    assert result.returncode == 0, result.stderr
    assert NOTICE in result.stdout
    assert not (home / "Library/LaunchAgents" / PLIST).exists()
    _no_stray_data_directory(home)


def test_uninstall_miner_removes_a_stale_plist(sandbox):
    home, _, log = sandbox
    agents = home / "Library/LaunchAgents"
    agents.mkdir(parents=True)
    (agents / PLIST).write_text("<plist/>")
    result = _run("uninstall-miner", sandbox)
    assert result.returncode == 0, result.stderr
    assert not (agents / PLIST).exists()
    assert log.exists()  # the stub, not the real launchctl, was called
    _no_stray_data_directory(home)


def test_status_reports_the_miner_as_retired(sandbox):
    home, _, _ = sandbox
    result = _run("status", sandbox)
    assert "Alpha Miner: retired" in result.stdout, result.stderr
    _no_stray_data_directory(home)
