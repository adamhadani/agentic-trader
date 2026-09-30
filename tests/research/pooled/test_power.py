from datetime import date, timedelta
from pathlib import Path

import numpy as np

from agentic_trader.research.pooled.campaign import load_campaign_protocol
from agentic_trader.research.pooled.cube import CubeView
from agentic_trader.research.pooled.power import ar1_scores, run_power, synthetic_cube


REPO = Path(__file__).resolve().parents[3]
PROTOCOL = load_campaign_protocol(REPO / "config/research/pooled/campaign-v1.json").protocol


def calendar() -> tuple[date, ...]:
    out, d = [], PROTOCOL.windows.discovery[0]
    while d <= PROTOCOL.windows.confirmation[1]:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return tuple(out)


def base_view(n_sessions=300, n_names=40, seed=0) -> CubeView:
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0, 1.2, (n_sessions, n_names))
    return CubeView(
        sessions=calendar()[:n_sessions],
        symbols=tuple(f"S{j}" for j in range(n_names)),
        offset=0,
        eligible=np.ones(r.shape, bool),
        labelled=np.ones(r.shape, bool),
        r_gross=r,
        r_cost=r,
        holding=np.full(r.shape, 3, np.int16),
        hit=np.ones(r.shape, np.int8),
        tiebreak=rng.integers(0, 2**62, r.shape).astype(np.uint64),
        dollar_volume=np.ones(r.shape),
    )


def tiny_protocol():
    return PROTOCOL.model_copy(
        update={
            "power": PROTOCOL.power.model_copy(
                update={"replicates": 2, "null_formulas": 5, "deltas": (0.0, 1.5), "detection_delta": 1.5}
            )
        }
    )


def test_synthetic_cube_resamples_whole_sessions_onto_the_calendar():
    base = base_view()
    cube = synthetic_cube(base, calendar(), np.random.default_rng(1), block_mean=20)
    view = cube.window(calendar()[0], calendar()[-1])
    assert view.sessions == calendar()
    rows = {tuple(np.round(base.r_cost[i], 9)) for i in range(len(base.sessions))}
    assert all(tuple(np.round(view.r_cost[i], 9)) in rows for i in range(0, len(view.sessions), 97))


def test_ar1_scores_are_reproducible_and_persistent():
    a = ar1_scores((500, 10), 0.95, seed=7)
    assert np.array_equal(a, ar1_scores((500, 10), 0.95, seed=7))
    lag1 = np.corrcoef(a[1:, 0], a[:-1, 0])[0, 1]
    assert 0.85 < lag1 < 0.99


def test_power_detects_a_huge_edge_and_rejects_the_null_on_a_small_run():
    result = run_power(tiny_protocol(), base_view(), calendar(), cohort_sha256="c" * 64)
    assert result["curve"]["1.5"]["detection"] == 1.0
    assert result["curve"]["0.0"]["detection"] == 0.0
    assert result["status"] in ("passed", "gate_failed")


def test_curve_is_identical_for_any_worker_count_and_progress_reports_each_replicate():
    seen: list[str] = []
    serial = run_power(tiny_protocol(), base_view(), calendar(), cohort_sha256="c" * 64)
    parallel = run_power(
        tiny_protocol(), base_view(), calendar(), cohort_sha256="c" * 64, workers=2, progress=seen.append
    )
    assert parallel == serial
    assert len(seen) == 2


def test_ar1_prefix_matches_the_full_field():
    full = ar1_scores((200, 4), 0.95, seed=3)
    assert np.array_equal(full[:80], ar1_scores((80, 4), 0.95, seed=3))
