import os
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/launchd.sh"
PLIST = "com.agentictrader.alphaminer.plist"


def _run(action: str, home: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["zsh", str(SCRIPT), action],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home)},
        check=False,
    )


def test_install_miner_is_retired_and_installs_nothing(tmp_path):
    result = _run("install-miner", tmp_path)
    assert result.returncode == 0, result.stderr
    assert "Alpha miner retired 2026-10-14" in result.stdout
    assert not (tmp_path / "Library/LaunchAgents" / PLIST).exists()


def test_run_miner_is_retired_and_runs_nothing(tmp_path):
    result = _run("run-miner", tmp_path)
    assert result.returncode == 0, result.stderr
    assert "Alpha miner retired 2026-10-14" in result.stdout
    assert not (tmp_path / "Library/LaunchAgents" / PLIST).exists()
