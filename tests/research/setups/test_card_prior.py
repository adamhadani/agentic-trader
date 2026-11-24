"""The frozen replay prior for card_stats v2: estimator, posterior, loader and replay rows."""

import ast
import hashlib
import json
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import agentic_trader.research.setups.card_prior as card_prior_module
from agentic_trader.config import WORKSPACE_ROOT, load_config
from agentic_trader.research.setups.beta import trailing_beta
from agentic_trader.research.setups.card_prior import (
    PRIOR_TAU_FLOOR,
    PRIOR_VERSION,
    CardStatsPrior,
    PriorEntry,
    PriorUnavailable,
    estimate_prior,
    load_prior,
    posterior,
    prior_rows,
    shrink_across_keys,
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


PRODUCTION = {"COPILOT_ENV": "production", "COPILOT_ENV_FILE": ""}
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
        "replay_mean": -0.05,
        "shrinkage": 1.0,
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


@pytest.mark.parametrize(
    "frame",
    [
        synthetic(KEY, [0.0]),  # one qualifying quarter: tau has no between-quarter estimate
        synthetic(KEY, [0.1 * i for i in range(9)], rows_per_quarter=19),  # nine quarters, none with 20 rows
        synthetic(KEY, [0.1 * i for i in range(9)]).assign(net_excess_r=np.nan),  # no finite row
    ],
    ids=["one-quarter", "no-quarter-with-twenty-rows", "no-finite-rows"],
)
def test_estimate_prior_refuses_without_two_qualifying_pooled_quarters(frame):
    """A pooled set this thin would write a floor-tau prior silently: the estimator refuses instead."""
    with pytest.raises(ValueError, match="pooled"):
        estimate_prior(frame)


def test_quarters_under_twenty_rows_do_not_count_toward_the_key_tau():
    """Nine quarters, two of them with 19 rows: seven qualify (< 8), so the key takes the pooled tau."""
    full = synthetic(KEY, [0.1 * i for i in range(9)])
    quarters = pd.to_datetime(full["session"]).dt.to_period("Q")
    thin = set(quarters.unique()[:2])
    frame = full[~quarters.isin(thin) | (full.groupby(quarters).cumcount() < 19)]
    assert sorted(frame.groupby(pd.to_datetime(frame["session"]).dt.to_period("Q")).size().tolist()) == [
        19,
        19,
        *[25] * 7,
    ]
    [got], _, pooled_tau, _ = estimate_prior(frame)
    assert (got.tau_source, got.tau) == ("pooled", pooled_tau)
    [unfiltered], _, _, _ = estimate_prior(full)
    assert unfiltered.tau_source == "key"  # the same key with every quarter at 25 rows


def test_se_session_clusters_rows_that_share_a_session():
    """Five rows per session: the standard error is over session means, not the rows' sd / sqrt(n_rows)."""
    frame = synthetic(KEY, [0.1 * i for i in range(9)])
    first = pd.to_datetime(frame["session"]).dt.to_period("Q").dt.start_time
    position = frame.groupby(first).cumcount()
    frame["session"] = (first + pd.to_timedelta(position // 5, unit="D")).dt.date.map(date.isoformat)
    [got], _, _, _ = estimate_prior(frame)
    by_session = frame.groupby("session")["net_excess_r"].mean()
    assert got.n_rows == 225 and got.n_sessions == 45 == len(by_session)
    expected = float(np.std(by_session.to_numpy(), ddof=1)) / math.sqrt(45)
    assert got.se_session == pytest.approx(expected, abs=1e-12)
    assert got.se_session != pytest.approx(got.sd / math.sqrt(got.n_rows), rel=0.05)


def minimal_prior(**overrides):
    fields = {
        "version": "card-stats-prior-v2",
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
        "cross_key_mean": -0.05,
        "between_key_tau": 0.2,
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
    assert str(caught.value) == "prior digest mismatch"


def test_load_prior_refuses_missing_file_and_none_sha(tmp_path):
    path, digest = write_prior(tmp_path, minimal_prior())
    with pytest.raises(PriorUnavailable) as caught:
        load_prior(path, None)
    assert str(caught.value) == "prior digest is not configured"
    with pytest.raises(PriorUnavailable) as caught:
        load_prior(tmp_path / "absent.json", digest)
    assert str(caught.value) == "prior file not found"  # never the path
    with pytest.raises(PriorUnavailable) as caught:
        load_prior(tmp_path, digest)  # a directory: unreadable, not missing
    assert str(caught.value) == "prior file unreadable"


def test_load_prior_refuses_invalid_content(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json SECRET")
    with pytest.raises(PriorUnavailable) as caught:
        load_prior(path, hashlib.sha256(path.read_bytes()).hexdigest())
    assert str(caught.value) == "prior file invalid"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"unit": "net_excess_r_v0"}, "prior unit mismatch"),
        ({"cost_model": {**minimal_prior().cost_model, "cost_floor_bps": 6.0}}, "prior constants differ from the code"),
        ({"r_clip": 5.0}, "prior constants differ from the code"),
        ({"beta_window_sessions": 60}, "prior constants differ from the code"),
        ({"version": "card-stats-prior-v0"}, "prior version mismatch"),
    ],
)
def test_load_prior_refuses_unit_version_and_constant_mismatch(tmp_path, overrides, message):
    path, digest = write_prior(tmp_path, minimal_prior(**overrides))
    with pytest.raises(PriorUnavailable) as caught:
        load_prior(path, digest)
    assert str(caught.value) == message


def write_raw_prior(tmp_path, mutate):
    """A prior file written from a mutated JSON dump, so values the model refuses can reach the loader."""
    payload = json.loads(minimal_prior().model_dump_json())
    mutate(payload)
    path = tmp_path / "raw-prior.json"
    path.write_text(json.dumps(payload))
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _set_key(field, value):
    def mutate(payload):
        payload["keys"][0][field] = value

    return mutate


def _set(field, value):
    def mutate(payload):
        payload[field] = value

    return mutate


@pytest.mark.parametrize(
    "mutate",
    [
        _set_key("sd", 0.0),
        _set_key("sd", -1.0),
        _set_key("tau", 0.0),
        _set_key("se_session", -0.01),
        _set_key("n_rows", -1),
        _set_key("n_sessions", -1),
        _set("pooled_sd", 0.0),
        _set("pooled_tau", 0.0),
        _set("unexpected_key", 1),
    ],
    ids=[
        "sd-zero",
        "sd-negative",
        "tau-zero",
        "se-negative",
        "rows-negative",
        "sessions-negative",
        "pooled-sd-zero",
        "pooled-tau-zero",
        "extra-key",
    ],
)
def test_load_prior_refuses_a_value_outside_its_constraint(tmp_path, mutate):
    path, digest = write_raw_prior(tmp_path, mutate)
    with pytest.raises(PriorUnavailable) as caught:
        load_prior(path, digest)
    assert str(caught.value) == "prior file invalid"


def test_the_shipped_prior_satisfies_every_constraint():
    """The committed, digest-pinned v2 prior loads under the model (digest from the shipped config)."""
    policy = load_config(str(WORKSPACE_ROOT / "config" / "config.yaml"), environ=PRODUCTION).card_policy
    prior = load_prior(policy.prior_path, policy.prior_sha256)
    assert prior.version == PRIOR_VERSION and len(prior.keys) == 3


PRIOR_UNAVAILABLE_MESSAGES = {
    "prior digest is not configured",
    "prior file not found",
    "prior file unreadable",
    "prior digest mismatch",
    "prior file invalid",
    "prior version mismatch",
    "prior shrinkage inconsistent",
    "prior unit mismatch",
    "prior constants differ from the code",
}


def test_every_prior_unavailable_message_is_fixed_text():
    """Each refusal's text is a literal from a fixed set: never a path, an exception's text or file content,
    because the worker puts it in readiness and so in a Telegram incident notice."""
    tree = ast.parse(Path(card_prior_module.__file__).read_text())
    raised = [
        node.exc
        for node in ast.walk(tree)
        if isinstance(node, ast.Raise)
        and isinstance(node.exc, ast.Call)
        and isinstance(node.exc.func, ast.Name)
        and node.exc.func.id == "PriorUnavailable"
    ]
    assert raised
    messages = []
    for call in raised:
        assert not call.keywords and len(call.args) == 1
        [argument] = call.args
        assert isinstance(argument, ast.Constant) and isinstance(argument.value, str), ast.dump(argument)
        messages.append(argument.value)
    assert set(messages) == PRIOR_UNAVAILABLE_MESSAGES


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


V1_PATH = WORKSPACE_ROOT / "config" / "research" / "card-stats-prior-v1.json"


def _v1_entries():
    v1 = json.loads(V1_PATH.read_text())
    entries = tuple(PriorEntry(**{**k, "replay_mean": k["mean"], "shrinkage": 1.0}) for k in v1["keys"])
    return entries, v1["pooled_tau"]


def test_shrink_across_keys_matches_closed_form():
    entries, tau = _v1_entries()
    assert tau == pytest.approx(0.16426, abs=1e-4)
    shrunk, mu = shrink_across_keys(entries, tau)
    assert mu == pytest.approx(-0.0076, abs=5e-4)
    by_key = {(k.strategy, k.timeframe): k for k in shrunk}
    assert by_key[("SQUEEZE_BREAKOUT", "1h")].mean == pytest.approx(-0.0200, abs=2e-3)
    assert by_key[("SQUEEZE_BREAKOUT", "4h")].mean == pytest.approx(0.074, abs=2e-3)
    assert by_key[("TREND_PULLBACK", "4h")].mean == pytest.approx(-0.0767, abs=2e-3)
    for before, after in zip(entries, shrunk, strict=True):
        assert 0 <= after.shrinkage <= 1
        assert after.replay_mean == before.replay_mean
        for field in ("sd", "se_session", "tau", "n_rows", "n_sessions", "tau_source"):
            assert getattr(after, field) == getattr(before, field)


def test_shrink_single_key_is_identity_mean():
    only = entry(mean=0.3, replay_mean=0.3, se_session=0.2)
    [shrunk], mu = shrink_across_keys([only], 0.16)
    assert mu == pytest.approx(0.3)
    assert shrunk.mean == pytest.approx(0.3)


def test_posterior_weight_unchanged_by_shrink():
    entries, tau = _v1_entries()
    shrunk, _ = shrink_across_keys(entries, tau)
    for before, after in zip(entries, shrunk, strict=True):
        assert posterior(0, None, after)[2] == posterior(0, None, before)[2]


def _shrunk_prior(**overrides):
    entries, tau = _v1_entries()
    keys, mu = shrink_across_keys(entries, tau)
    return minimal_prior(keys=keys, cross_key_mean=mu, between_key_tau=tau, **overrides)


def test_load_prior_round_trips_a_shrunk_prior(tmp_path):
    prior = _shrunk_prior()
    path, digest = write_prior(tmp_path, prior)
    assert load_prior(path, digest) == prior


def test_load_prior_refuses_v1_version(tmp_path):
    path, digest = write_prior(tmp_path, _shrunk_prior(version="card-stats-prior-v1"))
    with pytest.raises(PriorUnavailable) as caught:
        load_prior(path, digest)
    assert str(caught.value) == "prior version mismatch"


def test_load_prior_refuses_inconsistent_mean(tmp_path):
    prior = _shrunk_prior()
    edited = prior.keys[0].model_copy(update={"mean": prior.keys[0].mean + 0.01})
    path, digest = write_prior(tmp_path, prior.model_copy(update={"keys": (edited, *prior.keys[1:])}))
    with pytest.raises(PriorUnavailable) as caught:
        load_prior(path, digest)
    assert str(caught.value) == "prior shrinkage inconsistent"
