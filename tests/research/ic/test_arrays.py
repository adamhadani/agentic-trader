import inspect
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.alpha.data import save_dataset
from agentic_trader.research.alpha.dsl import AlphaExpressionEvaluator
from agentic_trader.research.alpha.validation import frame_digest
from agentic_trader.research.apriori.pead_events import by_session
from agentic_trader.research.ic.arrays import (
    BETA_EXPRESSION,
    build_ic_arrays,
    load_spy,
    market_frames,
    truncate_bars,
)
from tests.research.ic.world import NAMES, TRADING_DAYS, WINDOW, make_world


POS = {day: i for i, day in enumerate(TRADING_DAYS)}


def _open(world, name, day):
    return float(by_session(world.build.adjusted[name]).at[day, "Open"])


def _arrays(world, **kwargs):
    return build_ic_arrays(world.build, world.spy, world.groups, world.window, **kwargs)


def _drop(frame, day):
    keep = np.asarray(pd.DatetimeIndex(frame.index).tz_convert(ET_TZ).date) != day
    return frame[keep]


def test_forward_return_is_open_to_open_over_h_sessions(ic_world):
    arrays = _arrays(ic_world)
    for s in (0, 7, 100):
        day = arrays.sessions[s]
        for j, name in enumerate(NAMES):
            expected = _open(ic_world, name, TRADING_DAYS[POS[day] + 20]) / _open(ic_world, name, day) - 1
            assert arrays.returns[20][s, j] == expected
    day = arrays.sessions[3]
    spy_open = by_session(ic_world.spy)["Open"]
    assert arrays.spy_returns[5][3] == spy_open[TRADING_DAYS[POS[day] + 5]] / spy_open[day] - 1


def test_forward_return_is_nan_when_exit_is_after_window_end(ic_world):
    arrays = _arrays(ic_world)
    assert arrays.sessions[-1] == WINDOW[1]
    for horizon in (1, 5, 20, 60):
        r, spy_r = arrays.returns[horizon], arrays.spy_returns[horizon]
        assert np.isnan(r[-horizon:]).all()
        assert np.isnan(spy_r[-horizon:]).all()
        assert np.isfinite(r[-horizon - 1]).all()
        assert np.isfinite(spy_r[-horizon - 1])


def test_forward_return_never_uses_the_next_available_open(ic_world):
    s = 10
    arrays = _arrays(ic_world)
    decision = arrays.sessions[s]
    exit_day = TRADING_DAYS[POS[decision] + 20]
    adjusted = dict(ic_world.build.adjusted)
    adjusted["S2"] = _drop(adjusted["S2"], exit_day)
    got = _arrays(ic_world.with_bars(adjusted))
    assert np.isnan(got.returns[20][s, 2])
    assert np.isfinite(got.returns[20][s, 1])
    # the neighbouring decisions' own exits are intact
    assert np.isfinite(got.returns[20][s - 1, 2]) and np.isfinite(got.returns[20][s + 1, 2])
    # a missing entry bar is NaN too
    adjusted["S3"] = _drop(adjusted["S3"], decision)
    assert np.isnan(_arrays(ic_world.with_bars(adjusted)).returns[20][s, 3])


def test_entry_delay_starts_from_the_next_open(ic_world):
    arrays = _arrays(ic_world, entry_delay=1)
    for s in (0, 50):
        day = arrays.sessions[s]
        entry, exit_ = TRADING_DAYS[POS[day] + 1], TRADING_DAYS[POS[day] + 1 + 20]
        for j, name in enumerate(NAMES):
            assert arrays.returns[20][s, j] == _open(ic_world, name, exit_) / _open(ic_world, name, entry) - 1
    spy_open = by_session(ic_world.spy)["Open"]
    day = arrays.sessions[4]
    assert arrays.spy_returns[20][4] == spy_open[TRADING_DAYS[POS[day] + 21]] / spy_open[TRADING_DAYS[POS[day] + 1]] - 1
    assert np.isnan(arrays.returns[20][-21:]).all()
    assert np.isfinite(arrays.returns[20][-22]).all()


def test_a_name_that_is_spy_has_beta_one_and_a_zero_raw_residual(ic_world):
    adjusted = dict(ic_world.build.adjusted)
    adjusted["S0"] = ic_world.spy.copy()
    arrays = _arrays(ic_world.with_bars(adjusted))
    s = 120
    assert arrays.beta_60[s, 0] == pytest.approx(1.0, abs=1e-9)
    raw = arrays.returns[20][s, 0] - arrays.beta_60[s, 0] * arrays.spy_returns[20][s]
    assert raw == pytest.approx(0.0, abs=1e-9)


def test_residual_uses_the_beta_known_at_d_minus_one(ic_world):
    one_group = np.array(["g"] * len(NAMES))
    arrays = build_ic_arrays(ic_world.build, ic_world.spy, one_group, ic_world.window)
    s = 150
    day = arrays.sessions[s]
    previous = TRADING_DAYS[POS[day] - 1]
    keyed = by_session(ic_world.build.adjusted["S4"])
    keyed = keyed.set_axis(pd.DatetimeIndex(keyed.index))
    mkt = market_frames({"S4": ic_world.build.adjusted["S4"]}, ic_world.spy)["S4"]["mkt_ret"]
    beta = AlphaExpressionEvaluator().evaluate(BETA_EXPRESSION, keyed.assign(mkt_ret=mkt.to_numpy()))
    assert arrays.beta_60[s, 4] == pytest.approx(beta[pd.Timestamp(previous)], abs=1e-12)
    assert arrays.beta_60[s, 4] != pytest.approx(beta[pd.Timestamp(day)], abs=1e-9)
    raw = arrays.returns[20][s] - arrays.beta_60[s] * arrays.spy_returns[20][s]
    np.testing.assert_allclose(arrays.residuals[20][s], raw - raw.mean(), atol=1e-12)


def test_residual_is_demeaned_within_each_sector_each_session(ic_world):
    arrays = _arrays(ic_world)
    for s in (100, 180, 200):
        for group in ("a", "b"):
            cols = np.flatnonzero(arrays.groups == group)
            values = arrays.residuals[20][s, cols]
            assert np.isfinite(values).all()
            assert values.mean() == pytest.approx(0.0, abs=1e-12)
    assert arrays.residuals[20][100, 6] == pytest.approx(0.0, abs=1e-12)  # a sector of one


def test_residual_sector_mean_ignores_ineligible_names():
    world = make_world(ineligible=(0,))  # S0 is cube-ineligible but sits in sector a
    base = _arrays(world)
    adjusted = dict(world.build.adjusted)
    wild = adjusted["S0"].copy()
    wild.loc[wild.index[200:], ["Open", "High", "Low", "Close"]] *= 1e4
    adjusted["S0"] = wild
    got = _arrays(world.with_bars(adjusted))
    assert np.nanmax(np.abs(got.returns[20][:, 0])) > 100  # the ineligible name really is wild
    for horizon in (1, 5, 20):
        np.testing.assert_array_equal(got.residuals[horizon], base.residuals[horizon])
        assert np.isnan(got.residuals[horizon][:, 0]).all()
        assert np.isnan(got.residuals[horizon][:, 7]).all()
    # sector a's eligible residuals still sum to zero, and the raw return stays unmasked
    assert got.residuals[20][150, 1:3].sum() == pytest.approx(0.0, abs=1e-12)
    assert np.isfinite(got.returns[20][100, 0])


def test_nan_beta_gives_nan_residual(ic_world):
    arrays = _arrays(ic_world)
    # the window opens 40 sessions into the bars (20 into the cube): 60 returns are not yet available
    assert np.isnan(arrays.beta_60[0]).all()
    assert np.isfinite(arrays.returns[20][0]).all()
    assert np.isnan(arrays.residuals[20][0]).all()
    assert np.isfinite(arrays.beta_60[150]).all()
    assert np.isfinite(arrays.residuals[20][150, :7]).all()


def test_mkt_ret_compounds_spy_across_a_symbol_gap(ic_world):
    gap_day = TRADING_DAYS[200]
    frame = _drop(ic_world.build.adjusted["S1"], gap_day)
    mkt = market_frames({"S1": frame}, ic_world.spy)["S1"]["mkt_ret"]
    close = by_session(ic_world.spy)["Close"]
    after = pd.Timestamp(TRADING_DAYS[201])
    assert mkt[after] == pytest.approx(close[TRADING_DAYS[201]] / close[TRADING_DAYS[199]] - 1)
    ordinary = pd.Timestamp(TRADING_DAYS[150])
    assert mkt[ordinary] == pytest.approx(close[TRADING_DAYS[150]] / close[TRADING_DAYS[149]] - 1)
    assert pd.Timestamp(gap_day) not in mkt.index


def test_beta_60_does_not_change_when_bars_from_d_on_change(ic_world):
    base = _arrays(ic_world)
    s = 150
    cut = base.sessions[s]
    adjusted = {name: frame.copy() for name, frame in ic_world.build.adjusted.items()}
    spy = ic_world.spy.copy()
    for frame in (*adjusted.values(), spy):
        late = np.asarray(pd.DatetimeIndex(frame.index).tz_convert(ET_TZ).date) >= cut
        frame.loc[late, ["Open", "High", "Low", "Close"]] *= 3.0
    changed = _arrays(ic_world.with_bars(adjusted, spy))
    np.testing.assert_array_equal(changed.beta_60[: s + 1], base.beta_60[: s + 1])
    assert not np.array_equal(changed.beta_60[s + 5], base.beta_60[s + 5])


def test_nonpositive_dollar_volume_gives_nan_log_adv(ic_world):
    arrays = _arrays(ic_world)
    row = 30 - arrays.offset  # the zero sits at cube row 30
    assert arrays.offset == 20
    assert np.isnan(arrays.log_adv[row, 1])
    assert np.isfinite(arrays.log_adv[row, 0])
    view = ic_world.build.cube.window(*ic_world.window)
    expected = np.log(np.where(view.dollar_volume > 0, view.dollar_volume, np.nan))
    np.testing.assert_array_equal(arrays.log_adv, expected)
    assert np.isnan(arrays.log_adv).sum() == 1


def test_unknown_sector_is_not_eligible(ic_world):
    arrays = _arrays(ic_world)
    assert not arrays.eligible[:, 7].any()
    assert arrays.eligible[:, :7].all()


@pytest.mark.parametrize("entry_delay", [0, 1])
def test_poisoned_prices_after_window_end_change_nothing(ic_world, entry_delay):
    base = _arrays(ic_world, entry_delay=entry_delay)
    end = ic_world.window[1]
    adjusted = {name: frame.copy() for name, frame in ic_world.build.adjusted.items()}
    spy = ic_world.spy.copy()
    for frame in (*adjusted.values(), spy):
        late = np.asarray(pd.DatetimeIndex(frame.index).tz_convert(ET_TZ).date) > end
        assert late.any()
        frame.loc[late, ["Open", "High", "Low", "Close", "Volume"]] = 1e12
    poisoned = _arrays(ic_world.with_bars(adjusted, spy), entry_delay=entry_delay)
    for horizon in (1, 5, 20, 60):
        np.testing.assert_array_equal(poisoned.returns[horizon], base.returns[horizon])
        np.testing.assert_array_equal(poisoned.residuals[horizon], base.residuals[horizon])
        np.testing.assert_array_equal(poisoned.spy_returns[horizon], base.spy_returns[horizon])
    np.testing.assert_array_equal(poisoned.beta_60, base.beta_60)
    np.testing.assert_array_equal(poisoned.log_adv, base.log_adv)
    np.testing.assert_array_equal(poisoned.eligible, base.eligible)
    # the book's whole calendar, past the window end included
    for expression in ("roc(delay(close, 21), 42)", "ts_resid_std(returns, mkt_ret, 252)", "ts_mean(volume, 20)"):
        np.testing.assert_array_equal(poisoned.book.panel(expression), base.book.panel(expression))
    kept = truncate_bars(adjusted, end)
    assert all(frame.index.max() < pd.Timestamp(end + timedelta(days=1), tz="UTC") for frame in kept.values())


def test_a_symbol_bar_with_no_spy_bar_gives_nan_mkt_ret_there_and_at_the_next_bar(ic_world):
    spy = _drop(ic_world.spy, TRADING_DAYS[200])
    mkt = market_frames({"S1": ic_world.build.adjusted["S1"]}, spy)["S1"]["mkt_ret"]
    assert np.isnan(mkt[pd.Timestamp(TRADING_DAYS[200])])
    assert np.isnan(mkt[pd.Timestamp(TRADING_DAYS[201])])
    assert np.isfinite(mkt[pd.Timestamp(TRADING_DAYS[199])])
    assert np.isfinite(mkt[pd.Timestamp(TRADING_DAYS[202])])


def test_a_window_ending_after_the_limit_is_refused(ic_world):
    with pytest.raises(ValueError, match="limit"):
        build_ic_arrays(ic_world.build, ic_world.spy, ic_world.groups, (WINDOW[0], date(2024, 1, 2)))


def _cache_spy(root, spy):
    save_dataset(spy, root / "bars" / "SPY_1d", frame_digest(spy))
    return min(p.name for p in (root / "bars" / "SPY_1d").glob("*.npz"))


def test_load_spy_refuses_a_cube_not_built_with_spy(tmp_path):
    world = make_world(bar_files=("AAA|1d|all|x.npz", "SPYX|1d|all|y.npz"))
    with pytest.raises(ValueError, match="SPY is not among the bar files"):
        load_spy(world.build, tmp_path)


def test_load_spy_loads_the_listed_file(tmp_path, ic_world):
    name = _cache_spy(tmp_path, ic_world.spy)
    world = make_world(bar_files=(f"SPY|1d|all|{name}",))
    got = load_spy(world.build, tmp_path)
    assert len(got) == len(ic_world.spy)
    np.testing.assert_allclose(got["Close"].to_numpy(), ic_world.spy["Close"].to_numpy())


def test_load_spy_refuses_a_different_file_on_disk(tmp_path, ic_world):
    _cache_spy(tmp_path, ic_world.spy)
    world = make_world(bar_files=("SPY|1d|all|not-the-cached-file.npz",))
    with pytest.raises(ValueError, match="not the 'not-the-cached-file.npz'"):
        load_spy(world.build, tmp_path)


def test_load_spy_refuses_a_wrong_cache_root_and_touches_no_provider(tmp_path, ic_world):
    name = _cache_spy(tmp_path / "real", ic_world.spy)
    world = make_world(bar_files=(f"SPY|1d|all|{name}",))
    with pytest.raises(ValueError, match="None"):
        load_spy(world.build, tmp_path / "elsewhere")  # the function has no provider argument to touch
    assert "clients" not in inspect.signature(load_spy).parameters
