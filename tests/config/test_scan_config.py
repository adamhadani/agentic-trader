"""``scan.shadow_ranker_artifact`` must resolve relative to the repository root.

The daemon, an operator CLI invocation and a test can each have a different working
directory; a relative path in `config.yaml` must mean the same file everywhere.
"""

from pathlib import Path

from agentic_trader.config import WORKSPACE_ROOT, ScanConfig


def test_relative_artifact_path_resolves_against_the_repository_root():
    policy = ScanConfig(shadow_ranker_artifact="artifacts/setup-study/ranker.json")
    assert policy.shadow_ranker_artifact == (WORKSPACE_ROOT / "artifacts" / "setup-study" / "ranker.json").resolve()
    assert policy.shadow_ranker_artifact.is_absolute()


def test_absolute_artifact_path_is_kept_exactly(tmp_path):
    absolute = tmp_path / "ranker.json"
    policy = ScanConfig(shadow_ranker_artifact=absolute)
    assert policy.shadow_ranker_artifact == absolute


def test_no_artifact_configured_stays_none():
    assert ScanConfig().shadow_ranker_artifact is None


def test_relative_artifact_path_resolution_is_idempotent_for_path_instances():
    """A caller passing an already-relative ``Path`` (not a string) resolves the same way."""
    policy = ScanConfig(shadow_ranker_artifact=Path("artifacts/setup-study/ranker.json"))
    assert policy.shadow_ranker_artifact == (WORKSPACE_ROOT / "artifacts" / "setup-study" / "ranker.json").resolve()
