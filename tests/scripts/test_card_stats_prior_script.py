"""The offline prior builder records the revision of its own checkout, wherever it is run from."""

import subprocess
from pathlib import Path
from types import SimpleNamespace

from scripts import card_stats_prior


def test_revision_runs_git_in_the_repository_root(monkeypatch, tmp_path):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout="abc1234\n")

    monkeypatch.chdir(tmp_path)  # a working directory outside the checkout
    monkeypatch.setattr(subprocess, "run", run)
    assert card_stats_prior._revision() == "abc1234"
    [(args, kwargs)] = calls
    assert args[:2] == ["git", "describe"]
    assert kwargs["cwd"] == Path(card_stats_prior.__file__).resolve().parents[1]
