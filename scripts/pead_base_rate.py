"""Estimate the frozen PEAD v2 base rate in the card unit (research only, offline).

Reads the PEAD v2 study's saved events and labels and the study's cached bars from local disk only (no
provider, database or network), re-labels the LONG events, refuses unless they reproduce the study's own
labels, converts each to ``net_excess_r_v1`` and writes the digest-pinned summary the PEAD card reads.
Prints the file's SHA-256, which ``apriori.pead_base_rate_sha256`` pins.

Usage: uv run python scripts/pead_base_rate.py STUDY_DIR PEAD_CACHE SPY_HOURLY_CACHE[,SPY_HOURLY_CACHE...] OUT_JSON

``PEAD_CACHE`` holds ``bars/{SYMBOL}_{1h,1d}``; each ``SPY_HOURLY_CACHE`` holds ``SPY_1h`` (concatenated, the
duplicate index entries dropped).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from agentic_trader.research.alpha.data import load_dataset
from agentic_trader.research.apriori.catalog import load_pead_entry
from agentic_trader.research.apriori.pead_base_rate import (
    BASE_RATE_VERSION,
    RECENT_FROM,
    PeadBaseRate,
    add_net_excess,
    check_reproduces,
    relabel_long,
    summarise,
)
from agentic_trader.research.setups.beta import BETA_WINDOW_SESSIONS
from agentic_trader.research.setups.card_prior import cost_model_constants
from agentic_trader.research.setups.net_excess import NET_EXCESS_UNIT, R_CLIP


MARKET = "SPY"
# The checkout this script belongs to: its revision is the file's, whatever the working directory.
REPO_ROOT = Path(__file__).resolve().parents[1]
ENTRY_PATH = REPO_ROOT / "config" / "research" / "apriori" / "pead-v2.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _revision() -> str:
    result = subprocess.run(
        ["git", "describe", "--always", "--dirty"], capture_output=True, text=True, check=False, cwd=REPO_ROOT
    )
    return result.stdout.strip() or "unknown"


def _names_digest(names: list[str]) -> str:
    """SHA-256 over the sorted names of the cache files used; the file names are content hashes."""
    return hashlib.sha256("\n".join(sorted(names)).encode()).hexdigest()


def _cache_names(cache: Path, folders: list[str], prefix: str = "") -> list[str]:
    return [f"{prefix}{folder}/{f.name}" for folder in folders for f in sorted((cache / folder).glob("*.npz"))]


def _bars(cache: Path, symbol: str, timeframe: str) -> pd.DataFrame | None:
    files = sorted((cache / "bars" / f"{symbol}_{timeframe}").glob("*.npz"))
    return _concat([load_dataset(f) for f in files]) if files else None


def _concat(frames: list[pd.DataFrame]) -> pd.DataFrame:
    joined = pd.concat(frames).sort_index()
    return joined[~joined.index.duplicated(keep="first")]


def _spy_hourly(caches: list[Path]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for cache in caches:
        files = sorted((cache / f"{MARKET}_1h").glob("*.npz"))
        if not files:
            raise SystemExit("market hourly bars missing from a listed cache")
        frames.extend(load_dataset(f) for f in files)
    return _concat(frames)


def parse_caches(argument: str) -> list[Path]:
    return [Path(part).expanduser() for part in argument.split(",") if part]


def read_events(path: Path) -> pd.DataFrame:
    events = pd.read_csv(path)
    events["session"] = pd.to_datetime(events["session"]).dt.date
    events["decision_at"] = pd.to_datetime(events["decision_at"], utc=True)
    return events


def build(study: Path, pead_cache: Path, spy_caches: list[Path]) -> PeadBaseRate:
    loaded = load_pead_entry(ENTRY_PATH)
    manifest = json.loads((study / "manifest.json").read_text())
    if manifest["sha256"] != loaded.sha256:
        raise SystemExit("the study manifest's entry digest differs from the frozen entry")
    events_path, labels_path, protocol_path = study / "events.csv.gz", study / "labels.csv.gz", study / "protocol.json"
    events = read_events(events_path)
    labels = pd.read_csv(labels_path)
    symbols = sorted(events["symbol"].unique())
    hourly = {s: frame for s in symbols if (frame := _bars(pead_cache, s, "1h")) is not None}
    rows = relabel_long(events, hourly, loaded.entry)
    check_reproduces(rows, labels)
    market_daily = _bars(pead_cache, MARKET, "1d")
    if market_daily is None:
        raise SystemExit("market daily bars missing from the cache")
    dailies = {s: _bars(pead_cache, s, "1d") for s in sorted(rows["symbol"].unique())}
    rows = add_net_excess(rows, dailies, market_daily, _spy_hourly(spy_caches))
    stats = summarise(rows, RECENT_FROM)
    first, last = loaded.entry.window.decisions
    range_path = pead_cache / "bars" / "cache_range.json"
    return PeadBaseRate(
        version=BASE_RATE_VERSION,
        unit=NET_EXCESS_UNIT,
        entry_sha256=loaded.sha256,
        source={
            "events_sha256": _sha256(events_path),
            "labels_sha256": _sha256(labels_path),
            "protocol_sha256": _sha256(protocol_path),
            "pead_cache": pead_cache.name,
            "pead_cache_range_sha256": _sha256(range_path) if range_path.exists() else "absent",
            "pead_cache_files_sha256": _names_digest(
                _cache_names(
                    pead_cache / "bars",
                    [*(f"{x}_1h" for x in symbols), *(f"{x}_1d" for x in sorted({*rows["symbol"], MARKET}))],
                )
            ),
            "spy_hourly_caches": ", ".join(cache.name for cache in spy_caches),
            "spy_hourly_files_sha256": _names_digest(
                [n for cache in spy_caches for n in _cache_names(cache, [f"{MARKET}_1h"], f"{cache.name}/")]
            ),
        },
        cost_model=cost_model_constants(),
        r_clip=R_CLIP,
        beta_window_sessions=BETA_WINDOW_SESSIONS,
        recent_from=RECENT_FROM,
        window_start=date.fromisoformat(str(first)),
        window_end=date.fromisoformat(str(last)),
        code_revision=_revision(),
        **stats,
    )


def main(study: Path, pead_cache: Path, spy_caches: list[Path], out: Path) -> None:
    rate = build(study, pead_cache, spy_caches)
    out.write_text(json.dumps(json.loads(rate.model_dump_json()), indent=2, sort_keys=True) + "\n")
    print(_sha256(out))


if __name__ == "__main__":
    main(
        Path(sys.argv[1]),
        Path(sys.argv[2]),
        parse_caches(sys.argv[3]),
        Path(sys.argv[4]),
    )
