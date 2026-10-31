"""The offline prior builder records the revision of its own checkout, wherever it is run from."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentic_trader.research.setups.card_prior import load_prior
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


V1 = Path(__file__).resolve().parents[2] / "config" / "research" / "card-stats-prior-v1.json"
V1_SHA = "6bab4e8d76a53ca099f9e5c82cd249e6cee2b9d26c4fc7e1613bd1e8469e5432"


def test_shrink_mode_writes_v2_and_prints_digest(tmp_path, capsys):
    v1 = tmp_path / "v1.json"
    v1.write_bytes(V1.read_bytes())
    out = tmp_path / "v2.json"
    card_stats_prior.main_shrink(v1, V1_SHA, out)
    printed = capsys.readouterr().out.strip()
    prior = load_prior(out, printed)
    v1_source = json.loads(V1.read_text())["source"]
    assert prior.source == {**v1_source, "shrunk_from": "card-stats-prior-v1", "shrunk_from_sha256": V1_SHA}
    assert [k.replay_mean for k in prior.keys] == [k["mean"] for k in json.loads(V1.read_text())["keys"]]


def test_shrink_mode_refuses_wrong_v1_digest(tmp_path):
    with pytest.raises(SystemExit):
        card_stats_prior.main_shrink(V1, "0" * 64, tmp_path / "v2.json")
    assert not (tmp_path / "v2.json").exists()
