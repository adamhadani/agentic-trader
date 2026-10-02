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

import asyncio
import hashlib
import json
import os
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from agentic_trader.research.apriori.pead_events import _by_session
from agentic_trader.research.pooled.cohort import SUPPORTED_SYMBOL, Cohort
from agentic_trader.research.pooled.study import _save_frame
from agentic_trader.research.setups.runner import CalendarSource
from agentic_trader.research.setups.study import _finite_json
from agentic_trader.storage.artifacts import save_json_report


__all__ = [
    "CIRCUIT_BREAKER",
    "FETCH_BATCH",
    "SCREEN_COLUMNS",
    "LoadedScreenRule",
    "ScreenRule",
    "SnapshotRef",
    "cohort_document",
    "config_group_symbols",
    "exclusion_reason",
    "execute_screen",
    "fetch_window",
    "load_screen_rule",
    "screen_sessions",
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


FETCH_BATCH = 100  # symbols per batched daily request
CIRCUIT_BREAKER = 5  # consecutive failed requests stop acquisition: the provider is down


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as target:
        target.write(data)


async def screen_sessions(calendar: CalendarSource, rule: ScreenRule) -> tuple[date, ...]:
    """The ``window_sessions`` trading sessions ending on ``window_end``."""
    days = await calendar.get_calendar_range(
        rule.window_end - timedelta(days=2 * rule.window_sessions + 30), rule.window_end
    )
    trading = sorted(day.date for day in days if day.is_trading_day and day.date <= rule.window_end)
    if len(trading) < rule.window_sessions or trading[-1] != rule.window_end:
        raise ValueError(f"the calendar has no {rule.window_sessions} sessions ending {rule.window_end.isoformat()}")
    return tuple(trading[-rule.window_sessions :])


def _rows(fetched: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    parts = []
    for symbol, frame in sorted(fetched.items()):
        index = pd.DatetimeIndex(frame.index)
        index = index if index.tz is not None else index.tz_localize("UTC")
        parts.append(
            pd.DataFrame(
                {
                    "symbol": symbol,
                    "timestamp": index.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "close": frame["Close"].to_numpy(float),
                    "volume": frame["Volume"].to_numpy(float),
                }
            )
        )
    if not parts:
        return pd.DataFrame(columns=["symbol", "timestamp", "close", "volume"])
    return pd.concat(parts, ignore_index=True)


def _frames(rows: pd.DataFrame) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for symbol, group in rows.groupby("symbol", sort=True):
        index = pd.DatetimeIndex(pd.to_datetime(group["timestamp"], utc=True))
        out[str(symbol)] = pd.DataFrame(
            {"Close": group["close"].to_numpy(float), "Volume": group["volume"].to_numpy(float)}, index=index
        )
    return out


def _read_chunk(path: Path) -> pd.DataFrame:
    # Symbols such as "NA" must stay strings, so pandas' default NA spellings are off.
    return pd.read_csv(path, dtype={"symbol": str, "timestamp": str}, keep_default_na=False)


async def fetch_window(
    symbols: Sequence[str],
    sessions: Sequence[date],
    *,
    fetch: Callable[[Sequence[str], datetime, datetime], Mapping[str, pd.DataFrame]],
    cache_dir: Path,
    pace: Callable[[], Awaitable[None]],
    progress: Callable[[str], None] | None = None,
) -> dict[str, pd.DataFrame]:
    """Raw daily bars for ``symbols`` over ``sessions`` in batched requests, cached per chunk.

    A failed request is recorded and the next chunk is tried; all chunks are attempted
    before the screen fails, and a rerun fetches only the chunks without a cache file.
    ``CIRCUIT_BREAKER`` consecutive failures stop at once: the provider is down.
    """

    def say(message: str) -> None:
        if progress is not None:
            progress(message)

    cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    start = datetime.combine(sessions[0], time.min, tzinfo=UTC)
    end = datetime.combine(sessions[-1], time.max, tzinfo=UTC)
    chunks = [list(symbols[i : i + FETCH_BATCH]) for i in range(0, len(symbols), FETCH_BATCH)]
    frames: dict[str, pd.DataFrame] = {}
    errors: dict[str, str] = {}
    consecutive = 0
    for number, chunk in enumerate(chunks, start=1):
        digest = hashlib.sha256("\n".join(chunk).encode()).hexdigest()[:12]
        path = cache_dir / f"chunk-{number:03d}-{digest}.csv.gz"
        if path.exists():
            rows = await asyncio.to_thread(_read_chunk, path)
        else:
            await pace()
            try:
                fetched = await asyncio.to_thread(fetch, chunk, start, end)
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"
                errors[f"chunk {number}"] = reason
                consecutive += 1
                say(f"screen bars: chunk {number}/{len(chunks)} failed: {reason}")
                if consecutive >= CIRCUIT_BREAKER:
                    raise ValueError(
                        f"provider unavailable: {consecutive} consecutive requests failed (last: {reason}); "
                        "rerun to resume from the cache"
                    ) from exc
                continue
            consecutive = 0
            rows = _rows(fetched)
            await asyncio.to_thread(_save_frame, rows, path)
        frames.update(_frames(rows))
        say(f"screen bars {number}/{len(chunks)} chunks")
    if errors:
        listed = "; ".join(f"{chunk} ({reason})" for chunk, reason in errors.items())
        raise ValueError(f"screen bar acquisition failed for {listed}; rerun to resume from the cache")
    return frames


async def execute_screen(
    loaded: LoadedScreenRule,
    snapshot_path: Path,
    directory: Path,
    *,
    rule_path: str,
    config_symbols: Sequence[str],
    calendar: CalendarSource,
    fetch: Callable[[Sequence[str], datetime, datetime], Mapping[str, pd.DataFrame]],
    cache_dir: Path,
    pace: Callable[[], Awaitable[None]],
    environment: dict,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Run the frozen screen; writes ``screen.csv`` and the proposed ``cohort.json``.

    ``protocol.json`` and ``manifest.json`` are written before any provider access; a
    failure is recorded as ``status: failed`` instead of raising.
    """
    rule = loaded.rule
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    save_json_report({**rule.model_dump(mode="json"), "sha256": loaded.sha256}, directory / "protocol.json")
    save_json_report(
        {
            "check": "screen",
            "rule_sha256": loaded.sha256,
            "snapshot_sha256": rule.snapshot.sha256,
            "reads": "raw SIP daily close and volume over the screen window; no returns or labels",
            "environment": environment,
            "started_at": datetime.now(UTC).isoformat(),
            "authorizes_promotion": False,
        },
        directory / "manifest.json",
    )
    try:
        raw = await asyncio.to_thread(snapshot_path.read_bytes)
        snapshot_sha256 = hashlib.sha256(raw).hexdigest()
        if snapshot_sha256 != rule.snapshot.sha256:
            raise ValueError(f"snapshot file sha256 {snapshot_sha256} is not the rule's {rule.snapshot.sha256}")
        kept, excluded = snapshot_candidates(json.loads(raw), rule)
        sessions = await screen_sessions(calendar, rule)
        frames = await fetch_window(
            kept,
            sessions,
            fetch=fetch,
            cache_dir=cache_dir / f"screen-{loaded.sha256[:16]}",
            pace=pace,
            progress=progress,
        )
        table = screen_table(kept, excluded, frames, sessions, rule)
        csv = table_csv(table)
        await asyncio.to_thread(_write_private, directory / "screen.csv", csv)
        screen_sha256 = hashlib.sha256(csv).hexdigest()
        document = cohort_document(
            table,
            loaded,
            rule_path=rule_path,
            config_symbols=config_symbols,
            screen_sha256=screen_sha256,
            snapshot_sha256=snapshot_sha256,
        )
        text = (json.dumps(document, indent=2) + "\n").encode()
        Cohort.model_validate_json(text)  # the file the operator commits must load
        await asyncio.to_thread(_write_private, directory / "cohort.json", text)
        result = {
            "status": "completed",
            "kept": len(kept),
            "excluded": len(excluded),
            "ranked": int((table["rank"] > 0).sum()),
            "selected": int(table["selected"].sum()),
            "cohort_symbols": len(document["symbols"]),
            "cohort_sha256": hashlib.sha256(text).hexdigest(),
            "screen_sha256": screen_sha256,
            "window": [sessions[0].isoformat(), sessions[-1].isoformat()],
            "authorizes_promotion": False,
        }
    except Exception as exc:
        result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "authorizes_promotion": False}
    save_json_report(_finite_json(result), directory / "result.json")
    return result
