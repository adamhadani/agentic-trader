import math

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.spread.formation import (
    CoverageError,
    PairFit,
    eligible_symbols,
    fit_pair,
    half_life,
    select_pairs,
)
from agentic_trader.research.spread.protocol import FormationRule


RULE = FormationRule(
    coint_max_lag=1,
    p_value=0.05,
    half_life_sessions=(5, 42),
    hedge_ratio_abs=(0.25, 4.0),
    top_pairs=20,
    min_eligible_names=2,
)


def random_walk(rng: np.random.Generator, n: int, vol: float = 0.01, start: float = math.log(100.0)) -> np.ndarray:
    return start + np.cumsum(rng.normal(0.0, vol, n))


def ou(rng: np.random.Generator, n: int, half_life_sessions: float, innovation_std: float) -> np.ndarray:
    phi = 0.5 ** (1.0 / half_life_sessions)
    out = np.empty(n)
    out[0] = 0.0
    for t in range(1, n):
        out[t] = phi * out[t - 1] + rng.normal(0.0, innovation_std)
    return out


def planted(
    rng: np.random.Generator,
    n: int = 504,
    beta: float = 1.2,
    alpha: float = 0.5,
    hl: float = 12.0,
    x_vol: float = 0.02,
):
    x = random_walk(rng, n, vol=x_vol)
    return alpha + beta * x + ou(rng, n, hl, 0.01), x


def test_half_life_recovers_a_known_decay():
    rng = np.random.default_rng(1)
    e = ou(rng, 4000, 20.0, 0.01)
    assert 15 < half_life(e) < 26
    walks = [half_life(np.cumsum(np.random.default_rng(500 + s).normal(size=400))) for s in range(100)]
    assert np.median(walks) > 60  # a random walk shows no usable reversion; the Dickey-Fuller bias makes some finite
    assert half_life(np.array([0.0, 1.0, 0.0])) == math.inf  # too short


def test_fit_pair_recovers_a_planted_cointegrated_pair():
    log_y, log_x = planted(np.random.default_rng(2))
    fit = fit_pair(log_y, log_x, RULE, y="AAA", x="BBB", sector="s")
    assert isinstance(fit, PairFit) and fit.eligible and fit.reason == ""
    assert abs(fit.beta - 1.2) < 0.15 and abs(fit.alpha - 0.5) < 0.8
    assert fit.p_value < 0.05 and fit.t_stat < -3.0
    assert 5 <= fit.half_life <= 42 and fit.sigma > 0


def test_independent_random_walks_rarely_pass():
    passes = 0
    for seed in range(200):
        rng = np.random.default_rng(1000 + seed)
        fit = fit_pair(random_walk(rng, 252), random_walk(rng, 252), RULE, y="A", x="B", sector="s")
        passes += fit.eligible
    assert passes <= 30  # nominal 5% plus half-life/hedge filters; a loose bound against flakiness


@pytest.mark.parametrize(
    "make, reason",
    [
        (lambda: (np.full(252, 4.6), np.full(252, 4.6)), "constant"),
        (lambda: (random_walk(np.random.default_rng(3), 252),) * 2, "degenerate"),
        (
            lambda: (
                np.r_[random_walk(np.random.default_rng(4), 251), np.nan],
                random_walk(np.random.default_rng(5), 252),
            ),
            "incomplete",
        ),
        (lambda: (random_walk(np.random.default_rng(6), 12), random_walk(np.random.default_rng(7), 12)), "incomplete"),
    ],
)
def test_degenerate_pair_is_ineligible(make, reason):
    log_y, log_x = make()
    fit = fit_pair(log_y, log_x, RULE, y="A", x="B", sector="s")
    assert not fit.eligible and fit.reason == reason
    assert not math.isnan(fit.sigma) or reason in ("constant", "incomplete")


def test_filters_name_the_failing_rule():
    log_y, log_x = planted(np.random.default_rng(8), beta=6.0)
    fit = fit_pair(log_y, log_x, RULE, y="A", x="B", sector="s")
    assert not fit.eligible and fit.reason == "hedge_ratio"
    log_y, log_x = planted(np.random.default_rng(9), hl=1.5)
    fit = fit_pair(log_y, log_x, RULE, y="A", x="B", sector="s")
    assert not fit.eligible and fit.reason == "half_life"


def _frame(columns: dict[str, np.ndarray]) -> pd.DataFrame:
    index = pd.bdate_range("2020-01-01", periods=len(next(iter(columns.values()))))
    return pd.DataFrame({k: np.exp(v) for k, v in columns.items()}, index=index)


def test_eligible_symbols_requires_complete_positive_closes():
    rng = np.random.default_rng(10)
    frame = _frame({"A": random_walk(rng, 50), "B": random_walk(rng, 50), "C": random_walk(rng, 50)})
    frame.loc[frame.index[7], "B"] = np.nan
    frame.loc[frame.index[3], "C"] = 0.0
    assert eligible_symbols(frame, min_eligible=1) == ("A",)
    with pytest.raises(CoverageError, match="1 eligible names"):
        eligible_symbols(frame, min_eligible=2)


def test_select_pairs_ranks_by_t_stat_and_counts():
    rng = np.random.default_rng(11)
    log_y, log_x = planted(rng)
    frame = _frame(
        {
            "AAA": log_y,
            "BBB": log_x,
            "CCC": random_walk(rng, 504),
            "DDD": random_walk(rng, 504),
            "EEE": random_walk(rng, 504),
        }
    )
    frame.loc[frame.index[0], "EEE"] = np.nan
    pairs = (("AAA", "BBB", "tech"), ("AAA", "CCC", "tech"), ("BBB", "CCC", "tech"), ("DDD", "EEE", "energy"))
    selected, counts = select_pairs(frame, pairs, RULE.model_copy(update={"top_pairs": 1}))
    assert [(f.y, f.x) for f in selected] == [("AAA", "BBB")]
    assert counts["eligible_names"] == 4 and counts["pairs_tested"] == 3 and counts["selected"] == 1
    assert counts["pairs_passing"] >= 1 and set(counts["reasons"]) <= {
        "p_value",
        "half_life",
        "hedge_ratio",
        "degenerate",
        "constant",
        "coint_failed",
    }
