import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentic_trader.research.pooled.cohort import SUPPORTED_SYMBOL, load_cohort


REPO = Path(__file__).resolve().parents[3]
COHORT = REPO / "config/research/pooled/cohort-v1.json"


def _write(tmp_path: Path, **overrides) -> Path:
    doc = {
        "id": "pooled-cohort",
        "version": 1,
        "survivorship": "current membership (2026-09); not point-in-time",
        "sources": [
            {
                "kind": "config_groups",
                "description": "mega_caps+research_cohort",
                "identity": "x",
                "symbols": ["AAA", "BBB"],
            },
            {"kind": "equity_snapshot", "description": "snapshot", "identity": "y", "symbols": ["BBB", "CCC"]},
        ],
        "excluded": {"BRK.B": "unsupported symbol form"},
        "symbols": ["AAA", "BBB", "CCC"],
    }
    doc.update(overrides)
    path = tmp_path / "cohort.json"
    path.write_text(json.dumps(doc))
    return path


def test_identity_is_the_sha256_of_the_file_bytes(tmp_path):
    path = _write(tmp_path)
    loaded = load_cohort(path)
    assert loaded.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert loaded.cohort.symbols == ("AAA", "BBB", "CCC")
    assert loaded.cohort.source_of("BBB") == ("config_groups", "equity_snapshot")


@pytest.mark.parametrize(
    "overrides",
    [
        {"symbols": ["BBB", "AAA", "CCC"]},  # unsorted
        {"symbols": ["AAA", "AAA", "CCC"]},  # duplicate
        {"symbols": ["AAA", "BRK.B"]},  # unsupported form
        {"unknown": 1},  # unknown field
        {"symbols": ["AAA", "BBB"]},  # a source symbol missing from the union
    ],
)
def test_invalid_cohorts_are_rejected(tmp_path, overrides):
    with pytest.raises(ValidationError):
        load_cohort(_write(tmp_path, **overrides))


def test_committed_cohort_is_valid_and_union_of_its_sources():
    loaded = load_cohort(COHORT)
    union = sorted({s for source in loaded.cohort.sources for s in source.symbols if SUPPORTED_SYMBOL.fullmatch(s)})
    assert list(loaded.cohort.symbols) == union
    kinds = {source.kind for source in loaded.cohort.sources}
    assert kinds == {"config_groups", "equity_snapshot"}
    assert len(loaded.cohort.symbols) >= 300
    assert set(loaded.cohort.excluded).isdisjoint(loaded.cohort.symbols)
