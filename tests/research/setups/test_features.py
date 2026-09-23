import math

import numpy as np
import pandas as pd
import pytest
import yaml

from agentic_trader.config import WORKSPACE_ROOT
from agentic_trader.research.setups import features
from agentic_trader.research.setups.features import (
    CROSS_SECTIONAL,
    DIRECTIONAL,
    FEATURES_VERSION,
    MARKET,
    SECTOR_ETF,
    SETUP,
    CrossSection,
    cross_section,
    setup_vector,
)


# --- Synthetic 320-session daily panel: 6 equities in two sectors, plus SPY
# and their two SPDR sector ETFs. Returns are built as explicit arrays and
# turned into prices via cumprod, so mutating one array element changes
# exactly one day's pct_change return (pct_change is cumprod's left inverse
# from a fixed start), which lets tests target a single session precisely.

N = 320


def _dates(n: int = N) -> pd.DatetimeIndex:
    return pd.date_range("2024-01-02", periods=n, freq="B")


def _returns_pattern(n: int, *, freq: float, phase: float, amp: float, drift: float) -> np.ndarray:
    t = np.arange(n)
    return drift + amp * np.sin(2 * np.pi * t / freq + phase)


def _close_from_returns(returns: np.ndarray, start: float = 100.0) -> np.ndarray:
    return start * np.cumprod(1.0 + returns)


def _frame_from_close(close: np.ndarray, index: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": close,
            "High": close * 1.002,
            "Low": close * 0.998,
            "Close": close,
            "Volume": np.full(len(close), 5_000_000.0),
        },
        index=index,
    )


def _build_panel(n: int = N) -> tuple[dict[str, pd.DataFrame], dict[str, str], pd.DatetimeIndex]:
    index = _dates(n)

    etf_a_returns = _returns_pattern(n, freq=17, phase=0.0, amp=0.010, drift=0.0004)
    etf_b_returns = _returns_pattern(n, freq=13, phase=1.0, amp=0.008, drift=0.0002)
    spy_returns = _returns_pattern(n, freq=23, phase=2.0, amp=0.006, drift=0.0003)

    tech_idio = {
        "TA1": _returns_pattern(n, freq=29, phase=0.3, amp=0.004, drift=0.0002),
        "TA2": _returns_pattern(n, freq=31, phase=0.6, amp=0.003, drift=0.0001),
        "TA3": _returns_pattern(n, freq=37, phase=0.9, amp=0.005, drift=0.0003),
    }
    fin_idio = {
        "FB1": _returns_pattern(n, freq=19, phase=1.3, amp=0.004, drift=-0.0001),
        "FB2": _returns_pattern(n, freq=41, phase=1.6, amp=0.003, drift=0.0002),
        "FB3": _returns_pattern(n, freq=43, phase=1.9, amp=0.004, drift=0.0),
    }

    frames = {
        "XLK": _frame_from_close(_close_from_returns(etf_a_returns), index),
        "XLF": _frame_from_close(_close_from_returns(etf_b_returns), index),
        "SPY": _frame_from_close(_close_from_returns(spy_returns), index),
    }
    for symbol, idio in tech_idio.items():
        frames[symbol] = _frame_from_close(_close_from_returns(etf_a_returns + idio), index)
    for symbol, idio in fin_idio.items():
        frames[symbol] = _frame_from_close(_close_from_returns(etf_b_returns + idio), index)

    sectors = {
        "TA1": "technology",
        "TA2": "technology",
        "TA3": "technology",
        "FB1": "financial_services",
        "FB2": "financial_services",
        "FB3": "financial_services",
        "XLK": "technology",
        "XLF": "financial_services",
        "SPY": "etf_broad_equity",
    }
    return frames, sectors, index


def test_prefix_invariance():
    daily, sectors, index = _build_panel(n=290)
    as_of = index[-1].date()

    extra_index = pd.date_range(index[-1] + pd.tseries.offsets.BDay(1), periods=30, freq="B")
    extended = {}
    for symbol, frame in daily.items():
        extra_returns = _returns_pattern(30, freq=7, phase=3.7, amp=0.05, drift=-0.01)
        extra_close = frame["Close"].to_numpy()[-1] * np.cumprod(1.0 + extra_returns)
        extended[symbol] = pd.concat([frame, _frame_from_close(extra_close, extra_index)])

    base_cs = cross_section(daily, sectors, as_of)
    extended_cs = cross_section(extended, sectors, as_of)

    pd.testing.assert_frame_equal(base_cs.ranks, extended_cs.ranks)
    assert base_cs.market.keys() == extended_cs.market.keys()
    for key in base_cs.market:
        left, right = base_cs.market[key], extended_cs.market[key]
        if math.isnan(left):
            assert math.isnan(right)
        else:
            assert left == pytest.approx(right)


def test_rows_after_as_of_are_ignored():
    daily, sectors, index = _build_panel(n=290)
    as_of = index[-1].date()

    extra_index = pd.date_range(index[-1] + pd.tseries.offsets.BDay(1), periods=30, freq="B")
    extended = dict(daily)
    # An extreme, obviously-leak-detecting outlier planted after as_of.
    outlier_close = np.full(30, 1.0e9)
    extended["TA1"] = pd.concat([daily["TA1"], _frame_from_close(outlier_close, extra_index)])

    base_cs = cross_section(daily, sectors, as_of)
    polluted_cs = cross_section(extended, sectors, as_of)

    pd.testing.assert_frame_equal(base_cs.ranks, polluted_cs.ranks)
    assert base_cs.market == polluted_cs.market or all(
        math.isnan(base_cs.market[k]) and math.isnan(polluted_cs.market[k])
        for k in base_cs.market
        if base_cs.market[k] != polluted_cs.market.get(k)
    )


def test_short_direction_inverts_directional_ranks_only():
    daily, sectors, index = _build_panel()
    as_of = index[-1].date()
    cs = cross_section(daily, sectors, as_of)

    long_vec = setup_vector(
        cs,
        symbol="TA1",
        direction="LONG",
        strategy="orb",
        timeframe="15m",
        setup_quality=0.5,
        stop_atr=1.0,
        reward_risk=2.0,
    )
    short_vec = setup_vector(
        cs,
        symbol="TA1",
        direction="SHORT",
        strategy="orb",
        timeframe="15m",
        setup_quality=0.5,
        stop_atr=1.0,
        reward_risk=2.0,
    )

    for feature in CROSS_SECTIONAL:
        if feature in DIRECTIONAL:
            if math.isnan(long_vec[feature]):
                assert math.isnan(short_vec[feature])
            else:
                assert short_vec[feature] == pytest.approx(1.0 - long_vec[feature])
        else:
            if math.isnan(long_vec[feature]):
                assert math.isnan(short_vec[feature])
            else:
                assert short_vec[feature] == pytest.approx(long_vec[feature])

    # Non-cross-sectional keys are direction-independent.
    for feature in (*MARKET, *SETUP):
        left, right = long_vec[feature], short_vec[feature]
        if isinstance(left, float) and math.isnan(left):
            assert math.isnan(right)
        else:
            assert left == pytest.approx(right)


def test_v2_names_and_direction_aware_set():
    assert FEATURES_VERSION == "setup_features_v2"
    assert CROSS_SECTIONAL == (
        "mom_231_21",
        "mom_60",
        "rev_5",
        "vol_20",
        "dist_high_240",
        "dollar_volume_20",
        "resid_mom_60",
        "sector_rel_mom_60",
    )
    assert (
        frozenset({"mom_231_21", "mom_60", "rev_5", "dist_high_240", "resid_mom_60", "sector_rel_mom_60"})
        == DIRECTIONAL
    )


def test_every_feature_is_populated_within_the_shortest_live_window():
    """Live fetches period="1y": 249-251 completed sessions after today's bar is dropped.

    v1's mom_252_21 / dist_52w_high / spy_vol20_pct needed 253 / 252 / ~272 rows and were
    NaN on every live scan; every v2 feature must be populated at 249 sessions.
    """
    daily, sectors, index = _build_panel(n=249)
    cs = cross_section(daily, sectors, index[-1].date())

    # The ETFs regress on themselves, so only their resid_mom_60 is (correctly) NaN.
    equities = cs.ranks.drop(index=["XLK", "XLF", "SPY"])
    assert equities.notna().all().all(), equities.isna().sum()
    assert cs.ranks.drop(columns=["resid_mom_60"]).notna().all().all()
    for feature in MARKET:
        assert math.isfinite(cs.market[feature]), feature


@pytest.mark.parametrize(
    ("feature", "rows"),
    [("mom_231_21", 232), ("dist_high_240", 240), ("mom_60", 61), ("dollar_volume_20", 20)],
)
def test_cross_sectional_short_history_threshold(feature, rows):
    for n, populated in ((rows - 1, False), (rows, True)):
        daily, sectors, index = _build_panel(n=n)
        cs = cross_section(daily, sectors, index[-1].date())
        assert cs.ranks[feature].notna().all() if populated else cs.ranks[feature].isna().all(), (feature, n)


@pytest.mark.parametrize(("feature", "rows"), [("spy_vol20_pct", 220), ("spy_above_200", 200)])
def test_market_short_history_threshold(feature, rows):
    for n, populated in ((rows - 1, False), (rows, True)):
        daily, sectors, index = _build_panel(n=n)
        cs = cross_section(daily, sectors, index[-1].date())
        assert math.isfinite(cs.market[feature]) is populated, (feature, n)


def test_long_lookback_definitions():
    n = 300
    close = np.linspace(50.0, 80.0, n)
    high = close + 0.5
    high[-241] = 10_000.0  # just outside the 240-session high window: ignored
    high[-240] = 500.0  # the oldest session inside it: the window's maximum
    frame = pd.DataFrame(
        {"Open": close, "High": high, "Low": close - 0.5, "Close": close, "Volume": np.full(n, 1_000.0)},
        index=_dates(n),
    )

    values = features._basic_features(frame)

    assert values["mom_231_21"] == pytest.approx(close[-22] / close[-232] - 1.0)
    assert values["dist_high_240"] == pytest.approx(close[-1] / 500.0 - 1.0)


def test_spy_vol20_pct_ranks_within_the_last_200_rolling_values():
    # Random returns: the sinusoidal panel's periodic vol_20 has near-ties that float noise reorders.
    returns = np.random.default_rng(11).normal(0.0003, 0.01, N)
    returns[0] = 0.0
    index = _dates(N)
    spy = _frame_from_close(_close_from_returns(returns), index)
    vol20 = spy["Close"].pct_change().rolling(20).std(ddof=1).dropna()
    expected = float(vol20.iloc[-200:].rank(pct=True).iloc[-1])

    assert features._market_features(spy)["spy_vol20_pct"] == pytest.approx(expected)

    # A return old enough to reach only vol_20 values before the last 200 changes nothing;
    # the same shock one session later reaches the oldest value inside the window.
    for position, changed in ((N - 220, False), (N - 219, True)):
        shocked = returns.copy()
        shocked[position] += 0.5
        value = features._market_features(_frame_from_close(_close_from_returns(shocked), index))["spy_vol20_pct"]
        assert (value != pytest.approx(expected)) is changed, position


def test_short_history_gives_nan_not_error():
    daily, sectors, index = _build_panel()
    as_of = index[3].date()  # only 4 sessions of history available

    cs = cross_section(daily, sectors, as_of)
    vector = setup_vector(
        cs,
        symbol="TA1",
        direction="LONG",
        strategy="orb",
        timeframe="15m",
        setup_quality=0.5,
        stop_atr=1.0,
        reward_risk=2.0,
    )

    for feature in CROSS_SECTIONAL:
        assert math.isnan(vector[feature]), feature
    for feature in MARKET:
        assert math.isnan(vector[feature]), feature
    # Setup features are passed through verbatim, never suppressed.
    assert vector["setup_quality"] == pytest.approx(0.5)


def test_missing_symbol_gives_nan_cross_sectional():
    daily, sectors, index = _build_panel()
    as_of = index[-1].date()
    cs = cross_section(daily, sectors, as_of)

    vector = setup_vector(
        cs,
        symbol="NOPE",
        direction="LONG",
        strategy="orb",
        timeframe="15m",
        setup_quality=0.5,
        stop_atr=1.0,
        reward_risk=2.0,
    )
    for feature in CROSS_SECTIONAL:
        assert math.isnan(vector[feature])
    # Market/setup/one-hot are unaffected by an unknown symbol.
    for feature in MARKET:
        assert not math.isnan(vector[feature]) or math.isnan(cs.market[feature])


def test_resid_mom_uses_only_prior_window():
    daily, sectors, index = _build_panel(n=N)
    as_of = index[-1].date()

    def resid_for(mutated_index: int, delta: float) -> float:
        etf_a_returns = _returns_pattern(N, freq=17, phase=0.0, amp=0.010, drift=0.0004)
        idio = _returns_pattern(N, freq=29, phase=0.3, amp=0.004, drift=0.0002)
        combined = etf_a_returns + idio
        combined[mutated_index] += delta
        close = _close_from_returns(combined)
        mutated = dict(daily)
        mutated["TA1"] = _frame_from_close(close, index)
        cs = cross_section(mutated, sectors, as_of)
        return float(cs.ranks.loc["TA1", "resid_mom_60"])

    baseline_cs = cross_section(daily, sectors, as_of)
    baseline_rank = float(baseline_cs.ranks.loc["TA1", "resid_mom_60"])
    assert not math.isnan(baseline_rank)

    old_perturbation = resid_for(10, 0.05)  # aligned position ~9, older than 186
    window_perturbation = resid_for(200, 0.05)  # aligned position ~199, inside training window

    assert old_perturbation == pytest.approx(baseline_rank)
    assert window_perturbation != pytest.approx(baseline_rank)


def test_sector_rel_mom_is_group_demeaned_before_rank():
    n = 61  # exactly enough for mom_60
    index = _dates(n)

    def flat_then_jump(target: float) -> np.ndarray:
        close = np.full(n, 100.0)
        close[-1] = 100.0 * (1.0 + target)
        return close

    daily = {
        "T1": _frame_from_close(flat_then_jump(0.20), index),
        "T2": _frame_from_close(flat_then_jump(0.00), index),
        "F1": _frame_from_close(flat_then_jump(0.09), index),
        "F2": _frame_from_close(flat_then_jump(-0.09), index),
    }
    sectors = {
        "T1": "technology",
        "T2": "technology",
        "F1": "financial_services",
        "F2": "financial_services",
    }
    as_of = index[-1].date()

    cs = cross_section(daily, sectors, as_of)
    ranks = cs.ranks["sector_rel_mom_60"]

    # Group means: technology=0.10, financial_services=0.00. Demeaned:
    # T1=0.10, T2=-0.10, F1=0.09, F2=-0.09 -> ascending order T2,F2,F1,T1.
    assert ranks["T2"] == pytest.approx(0.25)
    assert ranks["F2"] == pytest.approx(0.50)
    assert ranks["F1"] == pytest.approx(0.75)
    assert ranks["T1"] == pytest.approx(1.00)

    # A naive (non-demeaned) mom_60 rank would instead order F2,T2,F1,T1 --
    # i.e. T2 and F2 would swap places. Confirms demeaning happens before rank.
    plain_mom_60 = pd.Series({"T1": 0.20, "T2": 0.00, "F1": 0.09, "F2": -0.09})
    plain_ranks = plain_mom_60.rank(pct=True)
    assert ranks["T2"] != pytest.approx(plain_ranks["T2"])
    assert ranks["F2"] != pytest.approx(plain_ranks["F2"])


def test_setup_vector_one_hot_and_keys():
    daily, sectors, index = _build_panel()
    as_of = index[-1].date()
    cs = cross_section(daily, sectors, as_of)

    vector = setup_vector(
        cs,
        symbol="TA1",
        direction="LONG",
        strategy="opening_range_breakout",
        timeframe="15m",
        setup_quality=0.73,
        stop_atr=1.4,
        reward_risk=2.5,
    )

    expected_keys = (
        set(CROSS_SECTIONAL) | set(MARKET) | set(SETUP) | {"strategy=opening_range_breakout", "timeframe=15m"}
    )
    assert set(vector.keys()) == expected_keys
    assert vector["strategy=opening_range_breakout"] == 1.0
    assert vector["timeframe=15m"] == 1.0
    assert vector["setup_quality"] == pytest.approx(0.73)
    assert vector["stop_atr"] == pytest.approx(1.4)
    assert vector["reward_risk"] == pytest.approx(2.5)


def test_sector_etf_covers_every_config_sector():
    with open(WORKSPACE_ROOT / "config" / "config.yaml") as handle:
        config = yaml.safe_load(handle)

    sectors: set[str] = set()
    for members in config["universe"]["groups"].values():
        for member in members:
            sector = member.get("sector")
            if sector:
                sectors.add(sector)

    assert sectors, "expected at least one sector tag in config/config.yaml"
    missing = sectors - set(SECTOR_ETF.keys())
    assert not missing, f"SECTOR_ETF is missing config sectors: {sorted(missing)}"


def test_cross_section_is_a_frozen_dataclass_snapshot():
    daily, sectors, index = _build_panel()
    as_of = index[-1].date()
    cs = cross_section(daily, sectors, as_of)

    assert isinstance(cs, CrossSection)
    assert cs.as_of == as_of
    assert list(cs.ranks.columns) == list(CROSS_SECTIONAL)
    assert set(cs.ranks.index) == set(daily.keys())
