"""A literature entry: one frozen formula tested once on the pooled lane (PEAD's P1-P4 bar).

The file pins the cohort and the campaign protocol by SHA-256 so the campaign is provably
frozen before any literature result exists. It uses the same cube spec as the campaign.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field, model_validator

from agentic_trader.research.pooled.cohort import UniverseSpec
from agentic_trader.research.pooled.cube import BracketSpec, CoverageSpec, CubeSpec
from agentic_trader.research.pooled.formula import Formula
from agentic_trader.research.pooled.stats import SessionTable, leg_mean_test, paired_edge_test, trimmed_mean


__all__ = ["REPO_ROOT", "LoadedEntry", "PooledEntry", "evaluate_entry", "load_pooled_entry"]

REPO_ROOT = Path(__file__).resolve().parents[3]


class EntryWindow(BaseModel, frozen=True, extra="forbid"):
    decisions: tuple[date, date]
    bars_from: date
    bars_through: date
    recent_from: date

    @model_validator(mode="after")
    def _ordered(self) -> EntryWindow:
        start, end = self.decisions
        if not self.bars_from < start < self.recent_from <= end < self.bars_through:
            raise ValueError("window dates must satisfy bars_from < start < recent_from <= end < bars_through")
        return self


class EntryBootstrap(BaseModel, frozen=True, extra="forbid"):
    block_mean: float = Field(gt=1)
    draws: int = Field(ge=100)
    seed: int


class EntryPassRule(BaseModel, frozen=True, extra="forbid"):
    min_sessions: int = Field(ge=1)
    min_recent_sessions: int = Field(ge=1)
    trim_fraction: float = Field(ge=0, lt=0.5)


class PooledEntry(BaseModel, frozen=True, extra="forbid"):
    id: str = Field(pattern=r"^[a-z0-9-]+$")
    version: int = Field(ge=1)
    title: str
    references: tuple[str, ...]
    hypothesis: str
    decision_rule: str
    cohort: str
    cohort_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    campaign_protocol_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    feed: Literal["alpaca:sip"]
    window: EntryWindow
    formula: Formula
    bracket: BracketSpec
    universe: UniverseSpec
    decision_cost_bps: float = Field(ge=0)
    coverage: CoverageSpec
    bootstrap: EntryBootstrap
    pass_rule: EntryPassRule

    def cube_spec(self) -> CubeSpec:
        return CubeSpec(
            feed=self.feed,
            bars_from=self.window.bars_from,
            decisions=self.window.decisions,
            bars_through=self.window.bars_through,
            bracket=self.bracket,
            universe=self.universe,
            decision_cost_bps=self.decision_cost_bps,
        )


@dataclass(frozen=True)
class LoadedEntry:
    entry: PooledEntry
    sha256: str
    path: Path


def load_pooled_entry(path: Path) -> LoadedEntry:
    raw = path.read_bytes()
    return LoadedEntry(entry=PooledEntry.model_validate_json(raw), sha256=hashlib.sha256(raw).hexdigest(), path=path)


def evaluate_entry(table: SessionTable, entry: PooledEntry, draws: np.ndarray) -> dict:
    rule = entry.pass_rule
    p1 = leg_mean_test(table, draws)
    p2 = paired_edge_test(table, draws)
    recent = np.array([d >= entry.window.recent_from for d in table.sessions])
    recent_edges = table.edge[recent & np.isfinite(table.edge)]
    trimmed = trimmed_mean(table.pick_rows["r_cost"].to_numpy(float), rule.trim_fraction)
    recent_edge = float(recent_edges.mean()) if recent_edges.size else float("nan")
    p3 = {
        "trimmed_mean": trimmed,
        "recent_edge": recent_edge,
        "holds": bool(np.isfinite(trimmed) and trimmed > 0 and np.isfinite(recent_edge) and recent_edge > 0),
    }
    sessions = int(np.isfinite(table.edge).sum())
    p4 = {
        "sessions": sessions,
        "recent_sessions": int(recent_edges.size),
        "holds": sessions >= rule.min_sessions and recent_edges.size >= rule.min_recent_sessions,
    }
    return {"p1": p1, "p2": p2, "p3": p3, "p4": p4, "passes": all(p["holds"] for p in (p1, p2, p3, p4))}
