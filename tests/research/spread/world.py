# tests/research/spread/world.py
"""A small spread-lane world: 14 names in two sectors, 120/30 tiles, two short stages."""

import hashlib
import json
from pathlib import Path

from agentic_trader.research.spread.protocol import (
    LoadedSpreadCohort,
    LoadedSpreadProtocol,
    load_spread_cohort,
    load_spread_protocol,
)


REPO = Path(__file__).resolve().parents[3]
PROTOCOL_V1 = json.loads((REPO / "config/research/spread/pairs-v1.json").read_text())

COHORT_DOC = {
    "id": "spread-cohort",
    "version": 1,
    "survivorship": "test world",
    "market": "SPY",
    "sectors": {"tech": [f"T{c}" for c in "ABCDEFGH"], "energy": [f"E{c}" for c in "ABCDEF"]},
}


def write_world(directory: Path, **overrides) -> tuple[LoadedSpreadProtocol, LoadedSpreadCohort]:
    """Writes cohort and protocol files under ``directory`` and returns both loaded; ``overrides`` patch top-level protocol keys."""
    directory.mkdir(parents=True, exist_ok=True)
    cohort_path = directory / "cohort.json"
    cohort_path.write_text(json.dumps(COHORT_DOC))
    doc = {
        **PROTOCOL_V1,
        "cohort": str(cohort_path),
        "cohort_sha256": hashlib.sha256(cohort_path.read_bytes()).hexdigest(),
        "bars": {"start": "2016-01-04", "through": "2019-12-31"},
        "windows": {"discovery": ["2016-06-01", "2018-12-31"], "confirmation": ["2019-01-02", "2019-12-31"]},
        "schedule": {"formation_sessions": 120, "trading_sessions": 30},
        "formation": {
            **PROTOCOL_V1["formation"],
            "top_pairs": 6,
            "min_eligible_names": 4,
            "half_life_sessions": [2, 42],
        },
        "bootstrap": {"block_mean": 5, "draws": 300, "seed": 1},
        "pass_rules": {**PROTOCOL_V1["pass_rules"], "min_closed_trades": 5, "min_windows": 2},
        "confirmation_rules": {**PROTOCOL_V1["confirmation_rules"], "min_closed_trades": 2, "min_windows": 1},
        "power": {
            **PROTOCOL_V1["power"],
            "seeds": 2,
            "min_pass": 1,
            "planted_pairs": 4,
            "half_life_sessions": 3.0,
            "innovation_std": 0.02,
        },
        "null_check": {**PROTOCOL_V1["null_check"], "seeds": 2, "max_pass": 1, "shift_block_sessions": 21},
        **overrides,
    }
    protocol_path = directory / "protocol.json"
    protocol_path.write_text(json.dumps(doc))
    return load_spread_protocol(protocol_path), load_spread_cohort(cohort_path)


ENVIRONMENT = {"runtime": {"revision": "abc1234"}, "python": "3.14"}


def gate_dir(
    directory: Path,
    *,
    check: str,
    loaded: LoadedSpreadProtocol,
    cohort: LoadedSpreadCohort,
    status: str = "passed",
    revision: str = "abc1234",
) -> Path:
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(
        json.dumps({"check": check, "environment": {"runtime": {"revision": revision}}})
    )
    (directory / "result.json").write_text(
        json.dumps({"status": status, "protocol_sha256": loaded.sha256, "cohort_sha256": cohort.sha256, "passes": 2})
    )
    return directory
