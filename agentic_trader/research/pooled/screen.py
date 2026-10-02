"""Cohort v2's liquidity screen: listed US equities ranked by SIP median dollar volume.

The rule file (``config/research/pooled/screen-v2.json``) is frozen before any bar is read.
Candidates come from the prospective equity snapshot, verified by hash. Non-common
instruments are excluded by symbol form, NASDAQ fifth-letter code and security-name
pattern, each with its reason. The rest are ranked by the median raw close x volume over
the ``window_sessions`` sessions ending ``window_end``. That date precedes the campaign's
confirmation window, so membership is not chosen on that window's dollar volume. The
screen reads prices and volumes only, never returns or labels; eligibility per session
is still decided point-in-time by the cube.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from agentic_trader.research.apriori.pead_events import _by_session
from agentic_trader.research.pooled.cohort import SUPPORTED_SYMBOL


__all__ = [
    "SCREEN_COLUMNS",
    "LoadedScreenRule",
    "ScreenRule",
    "SnapshotRef",
    "cohort_document",
    "config_group_symbols",
    "exclusion_reason",
    "load_screen_rule",
    "screen_table",
    "snapshot_candidates",
    "table_csv",
]

SCREEN_COLUMNS = (
    "symbol",
    "exclusion",
    "sessions_with_bars",
    "median_dollar_volume",
    "last_close",
    "rank",
    "selected",
)


class SnapshotRef(BaseModel, frozen=True, extra="forbid"):
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed: date
    classification: str = Field(min_length=1)


class ScreenRule(BaseModel, frozen=True, extra="forbid"):
    id: Literal["pooled-screen"]
    version: int = Field(ge=1)
    title: str
    cohort_version: int = Field(ge=1)
    survivorship: str
    snapshot: SnapshotRef
    config_groups: tuple[str, ...] = Field(min_length=1)
    feed: Literal["alpaca:sip"]
    adjustment: Literal["raw"]
    window_end: date
    window_sessions: int = Field(ge=1)
    min_sessions_with_bars: int = Field(ge=1)
    min_price: float = Field(gt=0)
    top: int = Field(ge=1)
    excluded_fifth_letters: tuple[str, ...]
    excluded_name_patterns: tuple[str, ...]

    @field_validator("excluded_fifth_letters")
    @classmethod
    def _letters(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not re.fullmatch(r"[A-Z]", letter) for letter in value):
            raise ValueError("fifth letters must be single upper-case letters")
        return value

    @field_validator("excluded_name_patterns")
    @classmethod
    def _patterns(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for pattern in value:
            re.compile(pattern)
        return value

    @model_validator(mode="after")
    def _window(self) -> ScreenRule:
        if self.min_sessions_with_bars > self.window_sessions:
            raise ValueError("min_sessions_with_bars cannot exceed window_sessions")
        return self


@dataclass(frozen=True)
class LoadedScreenRule:
    rule: ScreenRule
    sha256: str
    path: Path


def load_screen_rule(path: Path) -> LoadedScreenRule:
    raw = path.read_bytes()
    return LoadedScreenRule(rule=ScreenRule.model_validate_json(raw), sha256=hashlib.sha256(raw).hexdigest(), path=path)


def exclusion_reason(member: Mapping, rule: ScreenRule) -> str | None:
    """Why a snapshot candidate is not a common equity for the screen; None when it is kept."""
    symbol = str(member["symbol"])
    if not SUPPORTED_SYMBOL.fullmatch(symbol):
        return "unsupported symbol form"
    directory = member.get("directory") or {}
    if directory.get("source") == "nasdaqlisted" and len(symbol) == 5 and symbol[-1] in rule.excluded_fifth_letters:
        return f"nasdaq fifth letter {symbol[-1]}"
    name = str(directory.get("Security Name", ""))
    for pattern in rule.excluded_name_patterns:
        if re.search(pattern, name, flags=re.IGNORECASE):
            return f"security name matches {pattern}"
    return None


def snapshot_candidates(snapshot: Mapping, rule: ScreenRule) -> tuple[list[str], dict[str, str]]:
    """(kept symbols, sorted; excluded symbol -> reason) among the rule's candidate classification."""
    if snapshot.get("snapshot_id") != rule.snapshot.snapshot_id:
        raise ValueError(f"snapshot_id {snapshot.get('snapshot_id')!r} is not the rule's {rule.snapshot.snapshot_id!r}")
    kept: set[str] = set()
    excluded: dict[str, str] = {}
    for member in snapshot["members"]:
        if member.get("classification") != rule.snapshot.classification:
            continue
        reason = exclusion_reason(member, rule)
        if reason is None:
            kept.add(str(member["symbol"]))
        else:
            excluded[str(member["symbol"])] = reason
    return sorted(kept), dict(sorted(excluded.items()))


def _window_exclusion(row: Mapping, rule: ScreenRule) -> str:
    if row["sessions_with_bars"] == 0:
        return "no bars in the screen window"
    if row["sessions_with_bars"] < rule.min_sessions_with_bars:
        return f"bars on fewer than {rule.min_sessions_with_bars} sessions"
    if not np.isfinite(row["last_close"]):
        return f"no bar on {rule.window_end.isoformat()}"
    if row["last_close"] < rule.min_price:
        return f"close below {rule.min_price:g}"
    return ""


def _window_row(symbol: str, frame: pd.DataFrame | None, window: set[date], rule: ScreenRule) -> dict:
    row = {
        "symbol": symbol,
        "exclusion": "",
        "sessions_with_bars": 0,
        "median_dollar_volume": float("nan"),
        "last_close": float("nan"),
    }
    if frame is not None and not frame.empty:
        keyed = _by_session(frame)
        keyed = keyed[[day in window for day in keyed.index]]
        close = keyed["Close"].to_numpy(float)
        volume = keyed["Volume"].to_numpy(float)
        finite = np.isfinite(close) & np.isfinite(volume)
        row["sessions_with_bars"] = int(finite.sum())
        if finite.any():
            row["median_dollar_volume"] = float(np.median(close[finite] * volume[finite]))
        if rule.window_end in keyed.index:
            last = float(keyed.loc[rule.window_end, "Close"])
            if np.isfinite(last):
                row["last_close"] = last
    row["exclusion"] = _window_exclusion(row, rule)
    return row


def screen_table(
    kept: Sequence[str],
    excluded: Mapping[str, str],
    frames: Mapping[str, pd.DataFrame],
    sessions: Sequence[date],
    rule: ScreenRule,
) -> pd.DataFrame:
    """One row per candidate: its exclusion, or its window statistics and rank (1 = most liquid).

    Only bars on ``sessions`` (the window ending ``window_end``) are read. Ranked rows come
    first in rank order (median dollar volume descending, ties by symbol), then the rest by
    symbol; ``rank`` is 0 for an excluded row.
    """
    if len(sessions) != rule.window_sessions or sessions[-1] != rule.window_end:
        raise ValueError(
            f"the screen window must be the {rule.window_sessions} sessions ending {rule.window_end.isoformat()}"
        )
    window = set(sessions)
    rows = [
        {
            "symbol": symbol,
            "exclusion": reason,
            "sessions_with_bars": 0,
            "median_dollar_volume": float("nan"),
            "last_close": float("nan"),
        }
        for symbol, reason in excluded.items()
    ]
    rows += [_window_row(symbol, frames.get(symbol), window, rule) for symbol in kept]
    table = pd.DataFrame(rows, columns=list(SCREEN_COLUMNS[:5]))
    ranked = table[table["exclusion"] == ""].sort_values(["median_dollar_volume", "symbol"], ascending=[False, True])
    rank = {symbol: position for position, symbol in enumerate(ranked["symbol"], start=1)}
    table["rank"] = [rank.get(symbol, 0) for symbol in table["symbol"]]
    table["selected"] = (table["rank"] >= 1) & (table["rank"] <= rule.top)
    ordered = pd.concat(
        [table[table["rank"] > 0].sort_values("rank"), table[table["rank"] == 0].sort_values("symbol")],
        ignore_index=True,
    )
    return ordered[list(SCREEN_COLUMNS)]


def table_csv(table: pd.DataFrame) -> bytes:
    return table.to_csv(index=False, float_format="%.6f", lineterminator="\n").encode()


def config_group_symbols(config_path: Path, groups: Sequence[str]) -> list[str]:
    universe = yaml.safe_load(config_path.read_text())["universe"]["groups"]
    return sorted({str(row["symbol"]) for name in groups for row in universe[name]})


def cohort_document(
    table: pd.DataFrame,
    loaded: LoadedScreenRule,
    *,
    rule_path: str,
    config_symbols: Sequence[str],
    screen_sha256: str,
    snapshot_sha256: str,
) -> dict:
    """The cohort file this screen proposes: the scan-universe equities plus the top-ranked names."""
    rule = loaded.rule
    screened = sorted(str(symbol) for symbol in table.loc[table["selected"], "symbol"])
    configured = sorted(set(config_symbols))
    everything = sorted({*configured, *screened})
    return {
        "id": "pooled-cohort",
        "version": rule.cohort_version,
        "survivorship": rule.survivorship,
        "sources": [
            {
                "kind": "config_groups",
                "description": (
                    f"config/config.yaml universe groups {' + '.join(rule.config_groups)} (scan-universe equities)"
                ),
                "identity": hashlib.sha256("\n".join(configured).encode()).hexdigest(),
                "symbols": configured,
            },
            {
                "kind": "liquidity_screen",
                "description": rule.title,
                "identity": screen_sha256,
                "symbols": screened,
                "screen": {
                    "rule": rule_path,
                    "rule_sha256": loaded.sha256,
                    "snapshot_id": rule.snapshot.snapshot_id,
                    "snapshot_sha256": snapshot_sha256,
                    "window_end": rule.window_end.isoformat(),
                    "top": rule.top,
                    "candidates": len(table),
                    "excluded": int((table["exclusion"] != "").sum()),
                    "ranked": int((table["rank"] > 0).sum()),
                },
            },
        ],
        "excluded": {
            symbol: "unsupported symbol form" for symbol in everything if not SUPPORTED_SYMBOL.fullmatch(symbol)
        },
        "symbols": [symbol for symbol in everything if SUPPORTED_SYMBOL.fullmatch(symbol)],
    }
