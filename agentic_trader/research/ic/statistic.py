"""The rank IC series, its inference, the gate verdict and the tear sheet (research only).

Per-session IC is the Spearman correlation over names with a finite score, a finite target and a true mask
(average ranks for ties); a session below ``min_names`` pairs, or with a constant score or target, is NaN.
The Pearson series applies the same rules to the values themselves, and it is reported only.
Inference drops unavailable sessions first, then bootstraps the mean IC with a stationary session-block
bootstrap of mean block length ``2h`` (primary) and reports Newey-West at ``2h`` lags as a cross-check.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

import numpy as np
from scipy.stats import rankdata

from agentic_trader.research.alpha.information import ICPolicy, summarize_expected_values
from agentic_trader.research.ic.arrays import ICArrays
from agentic_trader.research.pooled.stats import _summary, bootstrap_draws


__all__ = ["gate_verdict", "ic_inference", "pearson_ic_series", "rank_ic_series", "render_tear_sheet", "tear_sheet"]

DECILES = 10
TARGETS = ("raw", "residual")
_NAN = float("nan")


def _ic_series(
    scores: np.ndarray,
    target: np.ndarray,
    mask: np.ndarray,
    min_names: int,
    transform: Callable[[np.ndarray], np.ndarray],
) -> np.ndarray:
    scores = np.asarray(scores, dtype=float)
    target = np.asarray(target, dtype=float)
    if scores.shape != target.shape or scores.shape != np.shape(mask):
        raise ValueError("scores, target and mask must share one shape")
    out = np.full(scores.shape[0], np.nan)
    for s in range(scores.shape[0]):
        keep = np.asarray(mask[s], dtype=bool) & np.isfinite(scores[s]) & np.isfinite(target[s])
        if keep.sum() < max(min_names, 2):
            continue
        a = transform(scores[s][keep])
        b = transform(target[s][keep])
        if np.ptp(a) == 0 or np.ptp(b) == 0:
            continue
        out[s] = float(np.corrcoef(a, b)[0, 1])
    return out


def rank_ic_series(scores: np.ndarray, target: np.ndarray, mask: np.ndarray, *, min_names: int = 30) -> np.ndarray:
    """``[S]`` Spearman IC per session; NaN when unavailable."""
    return _ic_series(scores, target, mask, min_names, rankdata)


def pearson_ic_series(scores: np.ndarray, target: np.ndarray, mask: np.ndarray, *, min_names: int = 30) -> np.ndarray:
    """``[S]`` Pearson IC per session, under the masking and unavailable rules of ``rank_ic_series``."""
    return _ic_series(scores, target, mask, min_names, np.asarray)


def _nan_inference(n_unavailable: int) -> dict:
    return {
        "mean": _NAN,
        "se": _NAN,
        "t": _NAN,
        "ci90": [_NAN, _NAN],
        "p_one_sided": _NAN,
        "newey_west_t": _NAN,
        "icir": _NAN,
        "share_positive": _NAN,
        "n_sessions": 0,
        "n_unavailable": n_unavailable,
    }


def ic_inference(ic: np.ndarray, horizon: int, *, draws: int, seed: int) -> dict:
    ic = np.asarray(ic, dtype=float)
    kept = ic[np.isfinite(ic)]
    n_unavailable = int(ic.size - kept.size)
    n = int(kept.size)
    if n < 2:
        out = _nan_inference(n_unavailable)
        out["n_sessions"] = n
        if n == 1:
            out["mean"] = float(kept[0])
            out["share_positive"] = float(kept[0] > 0)
        return out
    index = bootstrap_draws(n, 2.0 * horizon, draws, seed)
    boot = kept[index].mean(axis=1)
    summary = _summary(float(kept.mean()), boot)
    nw = summarize_expected_values([float(v) for v in kept], ICPolicy(hac_lags=2 * horizon, min_observations=2))
    sd = float(kept.std(ddof=1))
    return {
        "mean": summary["mean"],
        "se": summary["se"],
        "t": summary["t"],
        "ci90": list(summary["ci90"]),
        "p_one_sided": summary["p_one_sided"],
        "newey_west_t": _NAN if nw["hac_t"] is None else float(nw["hac_t"]),
        "icir": float(kept.mean()) / sd if sd > 0 else _NAN,
        "share_positive": float((kept > 0).mean()),
        "n_sessions": n,
        "n_unavailable": n_unavailable,
    }


def _block_means(kept: np.ndarray, blocks: int) -> list[float]:
    return [float(part.mean()) if part.size else _NAN for part in np.array_split(kept, blocks)]


def gate_verdict(
    ic: np.ndarray,
    horizon: int,
    *,
    min_t: float,
    blocks: int,
    min_positive_blocks: int,
    draws: int,
    seed: int,
) -> dict:
    out = ic_inference(ic, horizon, draws=draws, seed=seed)
    ic = np.asarray(ic, dtype=float)
    kept = ic[np.isfinite(ic)]
    means = _block_means(kept, blocks) if kept.size else [_NAN] * blocks
    positive = int(sum(1 for m in means if math.isfinite(m) and m > 0))
    t = out["t"]
    out["block_means"] = means
    out["positive_blocks"] = positive
    out["passes"] = bool(math.isfinite(t) and t >= min_t and positive >= min_positive_blocks)
    return out


def _session_deciles(score: np.ndarray, ret: np.ndarray) -> np.ndarray:
    """Mean return per score decile for one session, ordinal ranks (ties by position)."""
    n = score.size
    decile = (rankdata(score, method="ordinal") - 1) * DECILES // n  # a tie breaks by column position
    return np.array([ret[decile == d].mean() for d in range(DECILES)])


def _decile_table(scores: np.ndarray, target: np.ndarray, mask: np.ndarray, min_names: int) -> dict:
    rows = []
    for s in range(scores.shape[0]):
        keep = mask[s] & np.isfinite(scores[s]) & np.isfinite(target[s])
        if keep.sum() >= max(min_names, DECILES):
            rows.append(_session_deciles(scores[s][keep], target[s][keep]))
    if not rows:
        return {"mean_return_pct": [_NAN] * DECILES, "top_minus_bottom_pct": _NAN, "n_sessions": 0}
    means = np.mean(rows, axis=0) * 100.0
    return {
        "mean_return_pct": [float(v) for v in means],
        "top_minus_bottom_pct": float(means[-1] - means[0]),
        "n_sessions": len(rows),
    }


def _by_year(ic: np.ndarray, sessions: Sequence) -> dict[str, float]:
    years = np.array([d.year for d in sessions])
    out: dict[str, float] = {}
    for year in sorted(set(years.tolist())):
        values = ic[(years == year) & np.isfinite(ic)]
        out[str(year)] = float(values.mean()) if values.size else _NAN
    return out


def _score_stats(scores: np.ndarray, eligible: np.ndarray, min_names: int) -> dict:
    valid = eligible & np.isfinite(scores)
    counts = valid.sum(axis=1)
    autocorr: list[float] = []
    turnover: list[float] = []
    for s in range(1, scores.shape[0]):
        both = valid[s] & valid[s - 1]
        if both.sum() >= max(min_names, 2):
            a = rankdata(scores[s - 1][both])
            b = rankdata(scores[s][both])
            if np.ptp(a) > 0 and np.ptp(b) > 0:
                autocorr.append(float(np.corrcoef(a, b)[0, 1]))
        if valid[s].sum() >= max(min_names, DECILES) and valid[s - 1].sum() >= max(min_names, DECILES):
            top = _top_decile(scores[s], valid[s])
            before = _top_decile(scores[s - 1], valid[s - 1])
            # A name that was not eligible the session before counts as an entrant.
            turnover.append(float((top & ~before).sum() / top.sum()))
    return {
        "median_names": float(np.median(counts)) if counts.size else _NAN,
        "rank_autocorrelation_lag1": float(np.mean(autocorr)) if autocorr else _NAN,
        "top_decile_turnover": float(np.mean(turnover)) if turnover else _NAN,
    }


def _top_decile(score: np.ndarray, valid: np.ndarray) -> np.ndarray:
    top = np.zeros(score.shape, bool)
    idx = np.flatnonzero(valid)
    n = idx.size
    rank = rankdata(score[idx], method="ordinal") - 1  # a tie breaks by column position
    top[idx[(rank * DECILES // n) == DECILES - 1]] = True
    return top


def tear_sheet(
    scores: np.ndarray,
    arrays: ICArrays,
    *,
    horizons: Sequence[int],
    draws: int,
    seed: int,
    min_names: int = 30,
) -> dict:
    scores = np.asarray(scores, dtype=float)
    mask = np.asarray(arrays.eligible, dtype=bool)
    sheet: dict = {"score": _score_stats(scores, mask, min_names), "horizons": {}}
    for horizon in horizons:
        per_target: dict = {}
        for name, targets in (("raw", arrays.returns), ("residual", arrays.residuals)):
            target = targets[horizon]
            ic = rank_ic_series(scores, target, mask, min_names=min_names)
            block = ic_inference(ic, horizon, draws=draws, seed=seed)
            kept = ic[np.isfinite(ic)]
            block["by_year"] = _by_year(ic, arrays.sessions)
            block["block_means"] = _block_means(kept, 4) if kept.size else [_NAN] * 4
            block["deciles"] = _decile_table(scores, target, mask, min_names)
            per_target[name] = block
        sheet["horizons"][str(horizon)] = per_target
    return sheet


def _fmt(value: float, digits: int = 4) -> str:
    return "nan" if value is None or not math.isfinite(value) else f"{value:.{digits}f}"


def render_tear_sheet(sheet: dict) -> str:
    lines = ["IC tear sheet"]
    score = sheet["score"]
    lines.append(
        f"names/session (median) {_fmt(score['median_names'], 1)}  "
        f"rank autocorr lag1 {_fmt(score['rank_autocorrelation_lag1'])}  "
        f"top-decile turnover {_fmt(score['top_decile_turnover'])}"
    )
    for horizon, targets in sheet["horizons"].items():
        for name, b in targets.items():
            lines.append(f"h={horizon} target={name}")
            lines.append(
                f"  IC mean {_fmt(b['mean'])} se {_fmt(b['se'])} t {_fmt(b['t'], 2)} "
                f"NW t {_fmt(b['newey_west_t'], 2)} ICIR {_fmt(b['icir'])} "
                f"positive {_fmt(b['share_positive'], 3)} sessions {b['n_sessions']} unavailable {b['n_unavailable']}"
            )
            lines.append("  by year " + " ".join(f"{y}:{_fmt(v)}" for y, v in b["by_year"].items()))
            lines.append("  by block " + " ".join(_fmt(v) for v in b["block_means"]))
            d = b["deciles"]
            lines.append("  deciles % " + " ".join(_fmt(v, 3) for v in d["mean_return_pct"]))
            lines.append(f"  top-minus-bottom % {_fmt(d['top_minus_bottom_pct'], 3)}")
    return "\n".join(lines) + "\n"
