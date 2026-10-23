"""Descriptive erratum figures for the 2026-10-18 bracket counterfactual (research only).

Reads the frozen study frames and the study's adjusted daily-bar caches from local disk only (no
provider, database or network) and prints, per window and arm, the frozen beta-one excess beside the
past-only trailing-beta excess (``research/setups/beta.py``), both again without the rates/credit and
metals ETFs of the research ETF universe, and without only BIL and SHY (the native universe from
2026-10-23). The frozen ``no_change`` decision is not revisited.

Usage: uv run python scripts/yardstick_erratum.py STUDY_DIR PRIMARY_CACHE ROBUSTNESS_CACHE
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from agentic_trader.research.alpha.data import load_dataset
from agentic_trader.research.alpha.universe import ETF_RESEARCH_UNIVERSE
from agentic_trader.research.setups.beta import trailing_beta
from agentic_trader.research.setups.bracket_counterfactual import winsorized


MARKET = "SPY"
EXCLUDED_COHORTS = ("rates_credit", "metals")
REMOVED_FROM_UNIVERSE = ("BIL", "SHY")


def _daily(cache: Path, symbol: str) -> pd.DataFrame | None:
    files = sorted((cache / f"{symbol}_1d").glob("*.npz"))
    return load_dataset(files[0]) if files else None


def _mean(values: pd.Series) -> float | None:
    values = pd.to_numeric(values, errors="coerce").dropna()
    return round(float(values.mean()), 4) if len(values) else None


def _block(rows: pd.DataFrame, r_cap: float) -> dict:
    with_beta = rows.dropna(subset=["beta"])
    # The frozen descriptives clip r, r_cost and excess_r to +-r_cap; the beta-matched excess is clipped alike.
    beta_excess = (with_beta["r_cost"] - with_beta["beta"] * with_beta["market_r"].astype(float)).clip(-r_cap, r_cap)
    return {
        "n": len(rows),
        "mean_r_cost": _mean(rows["r_cost"]),
        "mean_excess_beta_one": _mean(rows["excess_r"]),
        "n_beta": len(with_beta),
        "mean_excess_beta_one_same_rows": _mean(with_beta["excess_r"]),
        "mean_excess_trailing_beta": _mean(beta_excess),
        "median_beta": round(float(with_beta["beta"].median()), 4) if len(with_beta) else None,
    }


def window_figures(frame_path: Path, cache: Path, r_cap: float) -> dict:
    frame = winsorized(pd.read_csv(frame_path), r_cap)
    market = _daily(cache, MARKET)
    betas: dict[tuple[str, str], float | None] = {}
    reasons: dict[str, int] = {}
    dailies: dict[str, pd.DataFrame | None] = {}
    for symbol, session in frame[["symbol", "session"]].drop_duplicates().itertuples(index=False):
        if symbol not in dailies:
            dailies[symbol] = _daily(cache, symbol)
        beta, reason = trailing_beta(dailies[symbol], market, date.fromisoformat(session))
        betas[(symbol, session)] = beta
        if reason is not None:
            reasons[reason] = reasons.get(reason, 0) + 1
    frame["beta"] = [betas[(s, d)] for s, d in zip(frame["symbol"], frame["session"], strict=True)]
    excluded = {s for name, members in ETF_RESEARCH_UNIVERSE.cohorts if name in EXCLUDED_COHORTS for s in members}
    per_symbol = frame.drop_duplicates(["symbol", "session"]).groupby("symbol")["beta"].median().dropna()
    out: dict = {
        "r_cap": r_cap,
        "beta_missing_reasons": reasons,
        "lowest_median_beta": per_symbol.nsmallest(5).round(3).to_dict(),
        "arms": {},
    }
    for arm, rows in frame.groupby("arm"):
        out["arms"][arm] = {
            "all": _block(rows, r_cap),
            "ex_rates_metals": _block(rows.loc[~rows["symbol"].isin(excluded)], r_cap),
            # The native universe from 2026-10-23: only the T-bill funds are gone.
            "ex_bil_shy": _block(rows.loc[~rows["symbol"].isin(REMOVED_FROM_UNIVERSE)], r_cap),
        }
    return out


def main(study: Path, primary_cache: Path, robustness_cache: Path) -> None:
    r_cap = float(json.loads((study / "protocol.json").read_text())["r_cap"])
    result = {
        "primary": window_figures(study / "frames" / "primary.csv.gz", primary_cache, r_cap),
        "robustness": window_figures(study / "frames" / "robustness.csv.gz", robustness_cache, r_cap),
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
