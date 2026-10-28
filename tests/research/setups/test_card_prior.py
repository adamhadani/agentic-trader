"""The frozen replay prior for card_stats v2: estimator, posterior, loader and replay rows."""

import hashlib
import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.setups.beta import trailing_beta
from agentic_trader.research.setups.card_prior import (
    PRIOR_TAU_FLOOR,
    CardStatsPrior,
    PriorEntry,
    PriorUnavailable,
    estimate_prior,
    load_prior,
    posterior,
    prior_rows,
)
from agentic_trader.research.setups.net_excess import (
    COST_CAP_BPS,
    COST_FLOOR_BPS,
    COST_MIN_SESSIONS,
    COST_WINDOW_SESSIONS,
    IMPACT_COEFFICIENT,
    NET_EXCESS_UNIT,
    R_CLIP,
    REFERENCE_RISK_USD,
    cost_inputs,
    name_cost_bps,
    net_excess_r,
)


KEY = ("TREND_PULLBACK", "4h", "LONG")
QUARTER_STARTS = [date(2022 + i // 4, 3 * (i % 4) + 1, 1) for i in range(9)]
# 12 x +1, 12 x -1, one 0: sums to zero, sum of squares 24, ddof=1 variance exactly 1.
PATTERN = [1.0] * 12 + [-1.0] * 12 + [0.0]


def synthetic(key, quarter_means, rows_per_quarter=25):
    records = []
    for start, mean in zip(QUARTER_STARTS, quarter_means, strict=False):
        records.extend(
            {
                "strategy": key[0],
                "timeframe": key[1],
                "direction": key[2],
                "session": (start + timedelta(days=i)).isoformat(),
                "net_excess_r": mean + PATTERN[i],
            }
            for i in range(rows_per_quarter)
        )
    return pd.DataFrame(records)


def entry(**overrides):
    fields = {
        "strategy": "S",
        "timeframe": "4h",
        "direction": "LONG",
        "n_rows": 100,
        "n_sessions": 50,
        "mean": -0.05,
        "sd": 1.5,
        "se_session": 0.0,
        "tau": 0.25,
        "tau_source": "key",
    }
    return PriorEntry(**{**fields, **overrides})


def test_posterior_is_prior_at_zero():
    e = entry(se_session=0.1)
    mean, se, k = posterior(0, None, e)
    assert mean == e.mean
    assert se == pytest.approx(math.sqrt(0.25**2 + 0.1**2))
    assert k == pytest.approx(1.5**2 / (0.25**2 + 0.1**2))


def test_posterior_weights():
    mean, se, k = posterior(20, 0.10, entry())
    assert k == pytest.approx(36.0)
    assert mean == pytest.approx((20 * 0.10 + 36 * -0.05) / 56)
    assert se == pytest.approx(math.sqrt(1 / (20 / 2.25 + 1 / 0.0625)))


def test_estimate_prior_hand_computed():
    means = [0.1 * i for i in range(9)]
    keys, pooled_sd, pooled_tau, pooled_n = estimate_prior(synthetic(KEY, means))
    assert len(keys) == 1 and pooled_n == 225
    got = keys[0]
    # quarter means 0..0.8: var(ddof=1) = 0.075; mean within-variance share = 1/25 = 0.04.
    expected_sd = math.sqrt(231 / 224)
    assert got.mean == pytest.approx(0.4, abs=1e-9)
    assert got.sd == pytest.approx(expected_sd, abs=1e-9)
    assert got.tau == pytest.approx(math.sqrt(0.075 - 0.04), abs=1e-9)
    assert got.se_session == pytest.approx(expected_sd / 15, abs=1e-9)
    assert (got.n_rows, got.n_sessions, got.tau_source) == (225, 225, "key")
    assert pooled_sd == pytest.approx(expected_sd, abs=1e-9)
    assert pooled_tau == pytest.approx(got.tau, abs=1e-9)


def test_estimate_prior_sparse_key_uses_pooled_tau():
    sparse = ("TREND_PULLBACK", "1h", "LONG")
    frame = pd.concat([synthetic(KEY, [0.1 * i for i in range(9)]), synthetic(sparse, [0.0, 0.5, 1.0])])
    keys, _, pooled_tau, pooled_n = estimate_prior(frame)
    by_key = {(k.strategy, k.timeframe, k.direction): k for k in keys}
    assert by_key[KEY].tau_source == "key"
    assert by_key[sparse].tau_source == "pooled"
    assert by_key[sparse].tau == pooled_tau
    assert pooled_n == 300


def test_tau_floor():
    keys, _, pooled_tau, _ = estimate_prior(synthetic(KEY, [0.2] * 9))
    assert keys[0].tau == PRIOR_TAU_FLOOR == 0.05
    assert pooled_tau == 0.05


def test_non_finite_rows_are_ignored():
    frame = synthetic(KEY, [0.1 * i for i in range(9)])
    frame.loc[0, "net_excess_r"] = np.nan
    _, _, _, pooled_n = estimate_prior(frame)
    assert pooled_n == 224


def minimal_prior(**overrides):
    fields = {
        "version": "card-stats-prior-v1",
        "unit": NET_EXCESS_UNIT,
        "cost_model": {
            "cost_floor_bps": COST_FLOOR_BPS,
            "impact_coefficient": IMPACT_COEFFICIENT,
            "reference_risk_usd": REFERENCE_RISK_USD,
            "cost_cap_bps": COST_CAP_BPS,
            "cost_window_sessions": float(COST_WINDOW_SESSIONS),
            "cost_min_sessions": float(COST_MIN_SESSIONS),
        },
        "r_clip": R_CLIP,
        "beta_window_sessions": 120,
        "tau_floor": 0.05,
        "min_rows_per_quarter": 20,
        "min_quarters": 8,
        "source": {"frame": "f"},
        "excluded_symbols": ("BIL",),
        "pooled_sd": 1.4,
        "pooled_tau": 0.2,
        "pooled_n_rows": 10,
        "keys": (entry(strategy=KEY[0], timeframe=KEY[1], direction=KEY[2]),),
        "cost_bps_quantiles": {"p50": 5.0},
        "n_no_beta": 0,
        "code_revision": "abc",
    }
    return CardStatsPrior(**{**fields, **overrides})


def test_skeptical_entry_for_unknown_key():
    prior = minimal_prior()
    missing = prior.entry("TREND_PULLBACK", "4h", "SHORT")
    assert (missing.mean, missing.sd, missing.se_session, missing.tau) == (0.0, 1.4, 0.0, 0.2)
    assert (missing.n_rows, missing.n_sessions, missing.tau_source) == (0, 0, "pooled")
    assert not prior.is_replay("TREND_PULLBACK", "4h", "SHORT")
    assert prior.is_replay(*KEY)
    assert prior.entry(*KEY).mean == -0.05


def write_prior(tmp_path, prior):
    path = tmp_path / "prior.json"
    path.write_text(prior.model_dump_json(indent=2))
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_load_prior_round_trip(tmp_path):
    path, digest = write_prior(tmp_path, minimal_prior())
    assert load_prior(path, digest) == minimal_prior()


def test_load_prior_refuses_digest_mismatch(tmp_path):
    path, _ = write_prior(tmp_path, minimal_prior())
    with pytest.raises(PriorUnavailable) as caught:
        load_prior(path, "0" * 64)
    assert "pooled_sd" not in str(caught.value)


def test_load_prior_refuses_missing_file_and_none_sha(tmp_path):
    path, digest = write_prior(tmp_path, minimal_prior())
    with pytest.raises(PriorUnavailable):
        load_prior(path, None)
    with pytest.raises(PriorUnavailable):
        load_prior(tmp_path / "absent.json", digest)


def test_load_prior_refuses_invalid_content(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json SECRET")
    with pytest.raises(PriorUnavailable) as caught:
        load_prior(path, hashlib.sha256(path.read_bytes()).hexdigest())
    assert "SECRET" not in str(caught.value)


def test_load_prior_refuses_unit_and_constant_mismatch(tmp_path):
    for overrides in (
        {"unit": "net_excess_r_v0"},
        {"cost_model": {**minimal_prior().cost_model, "cost_floor_bps": 6.0}},
        {"r_clip": 5.0},
        {"beta_window_sessions": 60},
    ):
        path, digest = write_prior(tmp_path, minimal_prior(**overrides))
        with pytest.raises(PriorUnavailable):
            load_prior(path, digest)


def daily(closes, volume=1_000_000.0):
    index = pd.bdate_range("2023-01-02", periods=len(closes), tz="America/New_York")
    return pd.DataFrame({"Close": closes, "Volume": volume}, index=index)


def test_prior_rows_excess():
    n = 200
    market_close = 100 * np.cumprod(1 + 0.01 * np.sin(np.arange(n)))
    market = daily(market_close)
    dailies = {
        "AAA": daily(50 * np.cumprod(1 + 0.012 * np.sin(np.arange(n) + 0.1))),
        "BBB": None,
    }
    session = market.index[-1].date().isoformat()
    frame = pd.DataFrame(
        {
            "symbol": ["AAA", "BBB"],
            "session": [session, session],
            "entry": [60.0, 20.0],
            "stop": [58.0, 19.0],
            "entry_price": [60.5, 20.2],
            "r": [0.7, -1.0],
            "market_r": [0.4, 0.2],
        }
    )
    out = prior_rows(frame, dailies, market)
    day = date.fromisoformat(session)
    beta, _ = trailing_beta(dailies["AAA"], market, day)
    bps = name_cost_bps(60.5, 2.0, cost_inputs(dailies["AAA"], day))
    assert out.loc[0, "beta"] == pytest.approx(beta)
    assert out.loc[0, "name_cost_bps"] == pytest.approx(bps)
    assert out.loc[0, "net_excess_r"] == pytest.approx(net_excess_r(0.7, 60.5, 2.0, bps, beta, 0.4))
    assert math.isnan(out.loc[1, "beta"]) and math.isnan(out.loc[1, "net_excess_r"])
    assert out.loc[1, "beta_reason"] == "no_daily_bars"
    assert out.loc[1, "name_cost_bps"] == COST_FLOOR_BPS
