"""Filesystem evidence has the same fail-closed test boundary as database access."""

import pytest

from agentic_trader.runtime import state_directory


@pytest.mark.parametrize("root", [None, ""])
def test_test_runtime_cannot_default_to_production_artifacts(monkeypatch, root):
    monkeypatch.setenv("COPILOT_ENV", "test")
    if root is None:
        monkeypatch.delenv("COPILOT_TEST_ROOT", raising=False)
    else:
        monkeypatch.setenv("COPILOT_TEST_ROOT", root)
    with pytest.raises(ValueError, match="COPILOT_TEST_ROOT"):
        state_directory()


def test_explicit_test_artifact_root_is_preserved(monkeypatch, tmp_path):
    monkeypatch.setenv("COPILOT_ENV", "test")
    monkeypatch.setenv("COPILOT_TEST_ROOT", str(tmp_path))
    assert state_directory() == tmp_path
