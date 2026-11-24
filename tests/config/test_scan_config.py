"""``scan.shadow_ranker_artifact`` must resolve relative to the repository root.

The daemon, an operator CLI invocation and a test can each have a different working
directory; a relative path in `config.yaml` must mean the same file everywhere.
"""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from agentic_trader.config import DEFAULT_BROAD_INDEX_ETFS, WORKSPACE_ROOT, ScanConfig


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


def test_the_default_broad_index_list_is_the_23_declared_names():
    assert ScanConfig().broad_index_etfs == DEFAULT_BROAD_INDEX_ETFS
    assert len(DEFAULT_BROAD_INDEX_ETFS) == 23
    assert len(set(DEFAULT_BROAD_INDEX_ETFS)) == 23


def test_the_shipped_config_carries_the_default_broad_index_list():
    shipped = yaml.safe_load((WORKSPACE_ROOT / "config/config.yaml").read_text())["scan"]["broad_index_etfs"]
    assert tuple(shipped) == DEFAULT_BROAD_INDEX_ETFS


@pytest.mark.parametrize(
    "entries",
    [("spy",), ("SP Y",), ("SPY", "SPY"), ("",), ("TOOLONGX",), ("1SPY",)],
    ids=["lower-case", "space", "duplicate", "empty", "too-long", "digit-first"],
)
def test_a_malformed_or_repeated_broad_index_entry_is_refused(entries):
    with pytest.raises(ValidationError):
        ScanConfig(broad_index_etfs=entries)


def test_an_empty_broad_index_list_turns_the_rule_off():
    assert ScanConfig(broad_index_etfs=()).broad_index_etfs == ()
