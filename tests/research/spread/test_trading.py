import math

import numpy as np
import pytest

from agentic_trader.research.spread.formation import PairFit
from agentic_trader.research.spread.protocol import TradingRule
from agentic_trader.research.spread.trading import (
    PairSimulation,
    Trade,
    lane_series,
    net_daily,
    simulate_pair,
    trade_net_return,
)


RULE = TradingRule(z_entry=2.0, z_exit=0.5, z_stop=4.0, fill="next_open")


def fit(beta: float = 1.0) -> PairFit:
    return PairFit(
        y="Y",
        x="X",
        sector="s",
        alpha=0.0,
        beta=beta,
        sigma=1.0,
        t_stat=-4.0,
        p_value=0.01,
        half_life=10.0,
        eligible=True,
        reason="",
    )


def paths(z: list[float], *, x_close: float = 100.0):
    """X constant at ``x_close`` (opens equal closes), Y chosen so that z_t = log Y_t − log X_t."""
    cz = np.array(z, dtype=float)
    cx = np.full(cz.size, x_close)
    cy = x_close * np.exp(cz)
    return cy.copy(), cx.copy(), cy.copy(), cx.copy()  # closes_y, closes_x, opens_y, opens_x


def test_entry_fills_at_next_open_and_exits_on_the_band():
    cy, cx, oy, ox = paths([0.0, -2.5, -2.5, -2.4, -0.3, 0.0, 0.0, 0.0])
    sim = simulate_pair(fit(), cy, cx, oy, ox, RULE, window=3)
    assert isinstance(sim, PairSimulation) and len(sim.trades) == 1
    trade = sim.trades[0]
    assert isinstance(trade, Trade)
    assert trade.side == 1 and trade.entry_pos == 2 and trade.exit_pos == 5 and trade.exit_reason == "reverted"
    assert (
        trade.entry_z == pytest.approx(-2.5)
        and trade.exit_z == pytest.approx(-0.3)
        and trade.holding_sessions == 3
        and trade.window == 3
    )
    assert trade.sector == "s" and trade.beta == 1.0
    expected = 0.5 * (oy[5] / oy[2] - 1.0)  # w_Y = 1/(1+|β|) = 0.5; X leg is flat
    assert trade.gross_return == pytest.approx(expected)
    assert sim.daily.sum() == pytest.approx(expected)
    assert sim.turnover.tolist() == [0, 0, 1.0, 0, 0, 1.0, 0, 0]
    assert sim.daily[:2].tolist() == [0.0, 0.0] and sim.daily[2] == pytest.approx(0.5 * (cy[2] / oy[2] - 1.0))


def test_short_spread_and_stop_exit():
    cy, cx, oy, ox = paths([0.0, 2.2, 2.3, 4.1, 4.5, 0.0, 0.0])
    sim = simulate_pair(fit(), cy, cx, oy, ox, RULE, window=0)
    trade = sim.trades[0]
    assert trade.side == -1 and trade.entry_pos == 2 and trade.exit_pos == 4 and trade.exit_reason == "stop"
    assert trade.gross_return == pytest.approx(-0.5 * (oy[4] / oy[2] - 1.0))
    assert trade.gross_return < 0


def test_window_end_closes_an_open_position_at_the_last_close():
    cy, cx, oy, ox = paths([0.0, -2.5, -2.5, -2.5, -2.5])
    sim = simulate_pair(fit(), cy, cx, oy, ox, RULE, window=0)
    trade = sim.trades[0]
    assert trade.exit_reason == "window_end" and trade.exit_pos == 4 and trade.exit_z == pytest.approx(-2.5)
    assert trade.gross_return == pytest.approx(0.5 * (cy[4] / oy[2] - 1.0))
    assert sim.turnover[4] == 1.0


def test_no_entry_signal_on_the_last_session():
    cy, cx, oy, ox = paths([0.0, 0.0, -2.5])
    assert simulate_pair(fit(), cy, cx, oy, ox, RULE, window=0).trades == ()


def test_gap_inside_open_position_marks_flat_and_emits_no_signal():
    cy, cx, oy, ox = paths([0.0, -2.5, -2.5, -0.1, -2.5, -0.2, 0.0, 0.0])
    cy[3] = np.nan  # the band would have been hit here; the gap must not exit
    sim = simulate_pair(fit(), cy, cx, oy, ox, RULE, window=0)
    trade = sim.trades[0]
    assert sim.daily[3] == 0.0 and trade.exit_pos == 6 and trade.exit_reason == "reverted"
    assert sim.daily.sum() == pytest.approx(trade.gross_return)


def test_window_end_with_missing_last_bar_uses_the_last_available_close():
    cy, cx, oy, ox = paths([0.0, -2.5, -2.5, -2.4, -2.3])
    cy[4] = np.nan
    sim = simulate_pair(fit(), cy, cx, oy, ox, RULE, window=0)
    trade = sim.trades[0]
    assert trade.exit_reason == "window_end" and trade.exit_pos == 4 and math.isnan(trade.exit_z)
    assert trade.gross_return == pytest.approx(0.5 * (cy[3] / oy[2] - 1.0))


def test_fill_is_delayed_past_a_missing_open():
    cy, cx, oy, ox = paths([0.0, -2.5, -2.5, -2.5, 0.0, 0.0, 0.0])
    oy[2] = np.nan
    sim = simulate_pair(fit(), cy, cx, oy, ox, RULE, window=0)
    assert sim.trades[0].entry_pos == 3 and sim.turnover[2] == 0.0 and sim.turnover[3] == 1.0


def test_re_entry_after_an_exit_within_the_window():
    cy, cx, oy, ox = paths([0.0, -2.5, -2.5, 0.0, 0.0, 2.5, 2.5, 0.0, 0.0, 0.0])
    sim = simulate_pair(fit(), cy, cx, oy, ox, RULE, window=0)
    assert [t.side for t in sim.trades] == [1, -1]
    assert sim.trades[1].entry_pos == 6 and sim.trades[1].exit_pos == 8


def test_negative_beta_flips_the_x_leg():
    # β = -1: spread = log Y + log X; long spread is long both legs
    cy = np.array([100.0, 100.0, 100.0, 100.0, 110.0, 110.0])
    cx = np.array([100.0, 100.0, 100.0, 100.0, 110.0, 110.0])
    oy, ox = cy.copy(), cx.copy()
    sigma = 1.0
    alpha = math.log(100.0) + math.log(100.0) - (-2.5 * sigma)  # makes z_0 = -2.5 at both legs at 100
    f = PairFit(
        y="Y",
        x="X",
        sector="s",
        alpha=alpha,
        beta=-1.0,
        sigma=sigma,
        t_stat=-4.0,
        p_value=0.01,
        half_life=10.0,
        eligible=True,
        reason="",
    )
    sim = simulate_pair(f, cy, cx, oy, ox, RULE, window=0)
    trade = sim.trades[0]
    assert trade.side == 1 and trade.entry_pos == 1
    assert trade.gross_return > 0  # both legs rose 10%; a long-long position gains


def test_cost_arithmetic_and_lane_series_divides_by_slots():
    cy, cx, oy, ox = paths([0.0, -2.5, -2.5, -0.3, 0.0, 0.0])
    sim = simulate_pair(fit(), cy, cx, oy, ox, RULE, window=0)
    trade = sim.trades[0]
    assert trade_net_return(trade, 5.0) == pytest.approx(trade.gross_return - 2 * 5.0 / 1e4)
    net = net_daily(sim, 5.0)
    assert net.sum() == pytest.approx(trade_net_return(trade, 5.0))
    lane = lane_series([sim, sim], slots=20, cost_bps=5.0, length=6)
    assert lane.shape == (6,) and lane.sum() == pytest.approx(2 * trade_net_return(trade, 5.0) / 20)
    assert lane_series([], slots=20, cost_bps=5.0, length=6).tolist() == [0.0] * 6


def test_asymmetric_beta_with_moving_x_telescopes_and_weights_legs():
    z = np.array([0.0, -2.5, -2.5, -2.5, -0.3, 0.0, 0.0, 0.0])
    cx = np.array([100.0, 100.0, 104.0, 106.0, 108.0, 110.0, 110.0, 110.0])
    cy = cx**2 * np.exp(z)  # z = log y - 2 log x with alpha = 0, sigma = 1
    sim = simulate_pair(fit(beta=2.0), cy, cx, cy.copy(), cx.copy(), RULE, window=0)
    assert len(sim.trades) == 1
    trade = sim.trades[0]
    assert trade.side == 1 and trade.entry_pos == 2 and trade.exit_pos == 5 and trade.exit_reason == "reverted"
    expected = (1 / 3) * (cy[5] / cy[2] - 1.0) - (2 / 3) * (cx[5] / cx[2] - 1.0)
    assert trade.gross_return == pytest.approx(expected)
    assert sim.daily.sum() == pytest.approx(trade.gross_return)
    assert sim.turnover.sum() == 2.0


def test_one_leg_gap_marks_the_other_leg_and_emits_no_signal():
    z = np.array([0.0, -2.5, -2.5, -0.1, -0.2, 0.0, 0.0, 0.0])
    cx = np.array([100.0, 100.0, 100.0, 102.0, 102.0, 102.0, 102.0, 102.0])
    cy = cx * np.exp(z)
    oy, ox = cy.copy(), cx.copy()
    cy[3] = np.nan  # would-be z = -0.1 is inside the band; no exit may be taken here
    sim = simulate_pair(fit(), cy, cx, oy, ox, RULE, window=0)
    trade = sim.trades[0]
    assert sim.daily[3] == pytest.approx(-0.5 * (cx[3] - cx[2]) / ox[2])  # X leg only; Y carries its last price
    assert trade.exit_pos == 5 and trade.exit_reason == "reverted"
    assert sim.daily.sum() == pytest.approx(trade.gross_return)


def test_pending_exit_at_the_last_session():
    cy, cx, oy, ox = paths([0.0, -2.5, -2.5, -0.2, -0.2])
    sim = simulate_pair(fit(), cy, cx, oy, ox, RULE, window=0)  # (a) both opens finite on the last session
    assert len(sim.trades) == 1
    trade = sim.trades[0]
    assert trade.exit_reason == "reverted" and trade.exit_pos == 4
    assert trade.gross_return == pytest.approx(0.5 * (oy[4] / oy[2] - 1.0))
    assert sim.turnover[4] == 1.0

    oy[4] = np.nan  # (b) the exit cannot fill at the last open: window_end at the last close
    sim = simulate_pair(fit(), cy, cx, oy, ox, RULE, window=0)
    assert len(sim.trades) == 1
    trade = sim.trades[0]
    assert trade.exit_reason == "window_end" and trade.exit_pos == 4
    assert trade.gross_return == pytest.approx(0.5 * (cy[4] / oy[2] - 1.0))
    assert sim.turnover[4] == 1.0
