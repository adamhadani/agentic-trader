"""Estimate the frozen replay prior for ``card_stats`` v2 (research only, offline).

Reads the 2026-10-18 bracket-counterfactual frame (``A0_live`` arm) and the study's adjusted daily-bar
caches from local disk only (no provider, database or network), converts each replayed setup to the
``net_excess_r_v1`` unit and writes the per-key prior JSON. Prints the file's SHA-256, which
``card_policy.prior_sha256`` pins.

Usage: uv run python scripts/card_stats_prior.py STUDY_DIR CACHE OUT_JSON
       uv run python scripts/card_stats_prior.py shrink V1_JSON V1_SHA256 OUT_JSON

``shrink`` derives the v2 prior from the digest-checked v1 file's per-key moments by empirical-Bayes
shrinkage across keys; it reads no study frame, cache or network.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from agentic_trader.config import load_config
from agentic_trader.research.alpha.data import load_dataset
from agentic_trader.research.setups.beta import BETA_WINDOW_SESSIONS
from agentic_trader.research.setups.card_prior import (
    PRIOR_MIN_QUARTERS,
    PRIOR_MIN_ROWS_PER_QUARTER,
    PRIOR_TAU_FLOOR,
    PRIOR_VERSION,
    CardStatsPrior,
    PriorEntry,
    cost_model_constants,
    estimate_prior,
    prior_rows,
    shrink_across_keys,
)
from agentic_trader.research.setups.net_excess import NET_EXCESS_UNIT, R_CLIP


MARKET = "SPY"
ARM = "A0_live"
PRIOR_V1_VERSION = "card-stats-prior-v1"
ALWAYS_EXCLUDED = ("BIL", "SHY")
# The checkout this script belongs to: its revision is the prior's, whatever the working directory.
REPO_ROOT = Path(__file__).resolve().parents[1]


def _daily(cache: Path, symbol: str) -> pd.DataFrame | None:
    files = sorted((cache / f"{symbol}_1d").glob("*.npz"))
    return load_dataset(files[0]) if files else None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _revision() -> str:
    result = subprocess.run(
        ["git", "describe", "--always", "--dirty"], capture_output=True, text=True, check=False, cwd=REPO_ROOT
    )
    return result.stdout.strip() or "unknown"


def build(study: Path, cache: Path) -> CardStatsPrior:
    frame_path = study / "frames" / "primary.csv.gz"
    protocol_path = study / "protocol.json"
    protocol = json.loads(protocol_path.read_text())
    config = load_config(environ={"COPILOT_ENV": "production", "COPILOT_ENV_FILE": ""})
    excluded = tuple(sorted({*ALWAYS_EXCLUDED, *config.scan.broad_index_etfs}))
    frame = pd.read_csv(frame_path)
    frame = frame[(frame["arm"] == ARM) & ~frame["symbol"].isin(excluded)]
    market = _daily(cache, MARKET)
    if market is None:
        raise SystemExit("market daily bars missing from the cache")
    dailies = {symbol: _daily(cache, symbol) for symbol in sorted(frame["symbol"].unique())}
    rows = prior_rows(frame, dailies, market)
    keys, pooled_sd, pooled_tau, pooled_n = estimate_prior(rows)
    keys, cross_key_mean = shrink_across_keys(keys, pooled_tau)
    costs = rows["name_cost_bps"].to_numpy(dtype=float)
    quantiles = {
        "p50": float(np.percentile(costs, 50)),
        "p90": float(np.percentile(costs, 90)),
        "p99": float(np.percentile(costs, 99)),
        "max": float(costs.max()),
    }
    start, end = protocol["window"]
    return CardStatsPrior(
        version=PRIOR_VERSION,
        unit=NET_EXCESS_UNIT,
        cost_model=cost_model_constants(),
        r_clip=R_CLIP,
        beta_window_sessions=BETA_WINDOW_SESSIONS,
        tau_floor=PRIOR_TAU_FLOOR,
        min_rows_per_quarter=PRIOR_MIN_ROWS_PER_QUARTER,
        min_quarters=PRIOR_MIN_QUARTERS,
        source={
            "frame": "frames/primary.csv.gz",
            "frame_sha256": _sha256(frame_path),
            "protocol_sha256": _sha256(protocol_path),
            "arm": ARM,
            "feed": str(protocol["feed"]),
            "window_start": start,
            "window_end": end,
            "shrunk_from": "estimate",
            "shrunk_from_sha256": _sha256(frame_path),
        },
        excluded_symbols=excluded,
        pooled_sd=pooled_sd,
        pooled_tau=pooled_tau,
        pooled_n_rows=pooled_n,
        cross_key_mean=cross_key_mean,
        between_key_tau=pooled_tau,
        keys=keys,
        cost_bps_quantiles=quantiles,
        n_no_beta=int(rows["beta"].isna().sum()),
        code_revision=_revision(),
    )


def build_shrunk(v1_path: Path, v1_sha256: str) -> CardStatsPrior:
    """The v2 prior from a digest-checked v1 file; v1 is plain JSON because it no longer validates as v2."""
    if _sha256(v1_path) != v1_sha256:
        raise SystemExit("v1 prior digest mismatch")
    v1 = json.loads(v1_path.read_text())
    entries = tuple(PriorEntry(**{**k, "replay_mean": k["mean"], "shrinkage": 1.0}) for k in v1["keys"])
    keys, cross_key_mean = shrink_across_keys(entries, v1["pooled_tau"])
    fields = {k: v for k, v in v1.items() if k not in ("keys", "version", "source", "code_revision")}
    return CardStatsPrior(
        **fields,
        version=PRIOR_VERSION,
        source={**v1["source"], "shrunk_from": PRIOR_V1_VERSION, "shrunk_from_sha256": v1_sha256},
        cross_key_mean=cross_key_mean,
        between_key_tau=v1["pooled_tau"],
        keys=keys,
        code_revision=_revision(),
    )


def main_shrink(v1_path: Path, v1_sha256: str, out: Path) -> None:
    prior = build_shrunk(v1_path, v1_sha256)
    out.write_text(json.dumps(json.loads(prior.model_dump_json()), indent=2, sort_keys=True) + "\n")
    print(_sha256(out))


def main(study: Path, cache: Path, out: Path) -> None:
    prior = build(study, cache)
    payload = json.loads(prior.model_dump_json())
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(_sha256(out))


if __name__ == "__main__":
    if sys.argv[1] == "shrink":
        main_shrink(Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4]))
    else:
        main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
