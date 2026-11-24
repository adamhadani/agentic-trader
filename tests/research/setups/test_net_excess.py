from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.setups.net_excess import CostInputs, cost_inputs, name_cost_bps, net_excess_r


def _daily(closes, volumes, *, end="2026-02-27"):
    index = pd.bdate_range(end=end, periods=len(closes)) + pd.Timedelta(hours=20)
    return pd.DataFrame({"Close": closes, "Volume": volumes}, index=pd.DatetimeIndex(index, tz="UTC"))


def test_cost_floor_without_inputs():
    assert name_cost_bps(100.0, 2.0, CostInputs(None, None, "short_history")) == 5.0


def test_cost_square_root_impact():
    cost = name_cost_bps(100.0, 2.0, CostInputs(0.02, 5e7, None))
    assert cost == pytest.approx(5.0 + 0.02 * np.sqrt(50_000 / 5e7) * 1e4, rel=1e-6)
    assert cost == pytest.approx(5.0 + 6.324555, rel=1e-6)


def test_cost_is_capped():
    assert name_cost_bps(100.0, 2.0, CostInputs(0.02, 1.0, None)) == 200.0


def test_cost_inputs_use_only_past_sessions():
    rng = np.random.default_rng(1)
    closes = list(100.0 * np.cumprod(1.0 + rng.normal(0.0, 0.01, 40)))
    volumes = [1e6] * 39 + [1e12]
    frame = _daily(closes, volumes, end="2026-03-02")  # last bar is Monday 2026-03-02 = session D
    session = date(2026, 3, 2)
    result = cost_inputs(frame, session)
    expected = np.std(np.diff(np.log(closes[-21 - 1 : -1])), ddof=1)
    assert result.reason is None
    assert result.sigma == pytest.approx(expected)
    assert result.adv_usd == pytest.approx(float(np.median(np.array(closes[-21:-1]) * 1e6)))


def test_cost_inputs_short_history():
    frame = _daily([100.0 + i for i in range(10)], [1e6] * 10)
    assert cost_inputs(frame, date(2026, 3, 2)).reason == "short_history"
    assert cost_inputs(None, date(2026, 3, 2)).reason == "no_daily_bars"
    assert cost_inputs(frame, date(2026, 1, 1)).reason == "no_daily_bars"


def test_net_excess_matches_flat_cost_and_beta_excess():
    assert net_excess_r(1.3, 100.0, 2.0, 5.0, 0.8, 0.4) == pytest.approx(1.3 - 2 * 5 / 1e4 * 100 / 2 - 0.32)


def test_net_excess_clips():
    assert net_excess_r(50, 100, 2, 5, 1.0, -20) == 10.0


@pytest.mark.parametrize(
    "args",
    [
        (1.0, 100.0, 2.0, 5.0, None, 0.1),
        (1.0, 100.0, 2.0, 5.0, 0.5, None),
        (1.0, 100.0, 0.0, 5.0, 0.5, 0.1),
        (float("nan"), 100.0, 2.0, 5.0, 0.5, 0.1),
    ],
)
def test_net_excess_none_inputs(args):
    assert net_excess_r(*args) is None
