"""Catalog probe identity: a passing catalog leg's frozen definition document.

A catalog alpha is not a DSL formula, so its identity is the entry file's SHA-256 plus
the leg, and its evidence is the study's own manifest and result. It can only ever be a
paper probe (see ``AlphaRepository.enrol_catalog_probe``).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agentic_trader.research.apriori.catalog import load_pead_entry, pead_execution_policy


CATALOG_KIND = "apriori"
LIVE_LEGS = ("LONG",)
ELIGIBLE = "eligible_for_probe"


def catalog_version_id(entry_id: str, version: int, leg: str, sha256: str) -> str:
    return f"apriori:{entry_id}:v{version}:{leg.lower()}:{sha256[:16]}"


def is_catalog_definition(definition: Mapping[str, Any] | None) -> bool:
    return definition is not None and definition.get("kind") == CATALOG_KIND


def load_catalog_probe(entry_path: Path, study_dir: Path, leg: str) -> dict[str, Any]:
    leg = leg.upper()
    if leg not in ("LONG", "SHORT"):
        raise ValueError(f"Unknown leg {leg!r}; expected LONG or SHORT")
    loaded = load_pead_entry(entry_path)
    manifest_bytes = (study_dir / "manifest.json").read_bytes()
    result_bytes = (study_dir / "result.json").read_bytes()
    manifest, result = json.loads(manifest_bytes), json.loads(result_bytes)
    entry = loaded.entry
    if manifest.get("entry_id") != entry.id or manifest.get("version") != entry.version:
        raise ValueError(
            f"Study manifest names {manifest.get('entry_id')} v{manifest.get('version')}, not {entry.id} v{entry.version}"
        )
    if manifest.get("sha256") != loaded.sha256:
        raise ValueError("Study manifest SHA-256 differs from the entry file; the study tested another version")
    if result.get("status") != "completed":
        raise ValueError(f"Study is not completed (status {result.get('status')!r})")
    decision = (result.get("decisions") or {}).get(leg)
    if decision != ELIGIBLE:
        raise ValueError(f"Leg {leg} did not pass its study ({decision}); it cannot be enrolled")
    if leg not in LIVE_LEGS:
        raise ValueError(f"The live path trades only {', '.join(LIVE_LEGS)}")
    version_id = catalog_version_id(entry.id, entry.version, leg, loaded.sha256)
    return {
        "kind": CATALOG_KIND,
        "version_id": version_id,
        "alpha_id": f"{entry.id}_{leg.lower()}",
        "entry_id": entry.id,
        "entry_version": entry.version,
        "entry_sha256": loaded.sha256,
        "leg": leg,
        "timeframe": "1d",
        "execution": pead_execution_policy(entry).to_dict(),
        "study_result_sha256": hashlib.sha256(result_bytes).hexdigest(),
        "study_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "study_mean_r": float(result["legs"][leg]["p1"]["mean_r_cost"]),
        "eligible_symbols": None,
        "clock": None,
    }
