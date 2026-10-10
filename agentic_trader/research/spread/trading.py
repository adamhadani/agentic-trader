"""Spread trading over one trading window with frozen formation parameters (causal, next-open fills)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from agentic_trader.research.spread.formation import PairFit
from agentic_trader.research.spread.protocol import TradingRule


__all__ = ["EXIT_REASONS", "PairSimulation", "Trade", "lane_series", "net_daily", "simulate_pair", "trade_net_return"]

EXIT_REASONS = ("reverted", "stop", "window_end")


@dataclass(frozen=True)
class Trade:
    window: int
    y: str
    x: str
    sector: str
    side: int
    entry_pos: int
    exit_pos: int
    entry_z: float
    exit_z: float
    exit_reason: str
    gross_return: float
    holding_sessions: int
    beta: float


@dataclass(frozen=True)
class PairSimulation:
    fit: PairFit
    window: int
    trades: tuple[Trade, ...]
    daily: np.ndarray
    turnover: np.ndarray


def trade_net_return(trade: Trade, cost_bps: float) -> float:
    """Two legs in and two legs out on gross notional 1: a round trip costs ``2c/10⁴``."""
    return trade.gross_return - 2.0 * cost_bps / 1e4


def net_daily(simulation: PairSimulation, cost_bps: float) -> np.ndarray:
    return simulation.daily - simulation.turnover * cost_bps / 1e4


def lane_series(simulations: Sequence[PairSimulation], *, slots: int, cost_bps: float, length: int) -> np.ndarray:
    """Committed-capital lane return: each of ``slots`` pair slots holds 1/slots of capital; flat slots earn 0."""
    out = np.zeros(length, dtype=float)
    for simulation in simulations:
        out += net_daily(simulation, cost_bps)
    return out / float(slots)


@dataclass
class _Position:
    side: int
    entry_pos: int
    entry_z: float
    entry_y: float
    entry_x: float
    last_y: float
    last_x: float


def _weights(beta: float) -> tuple[float, float]:
    return 1.0 / (1.0 + abs(beta)), abs(beta) / (1.0 + abs(beta))


def simulate_pair(
    fit: PairFit,
    closes_y: np.ndarray,
    closes_x: np.ndarray,
    opens_y: np.ndarray,
    opens_x: np.ndarray,
    rule: TradingRule,
    *,
    window: int,
) -> PairSimulation:
    cy, cx = np.asarray(closes_y, dtype=float), np.asarray(closes_x, dtype=float)
    oy, ox = np.asarray(opens_y, dtype=float), np.asarray(opens_x, dtype=float)
    n = cy.size
    with np.errstate(invalid="ignore", divide="ignore"):
        z = (np.log(cy) - fit.alpha - fit.beta * np.log(cx)) / fit.sigma
    w_y, w_x = _weights(fit.beta)
    x_sign = -math.copysign(1.0, fit.beta)
    daily = np.zeros(n, dtype=float)
    turnover = np.zeros(n, dtype=float)
    trades: list[Trade] = []
    position: _Position | None = None
    pending_entry: tuple[int, float] | None = None  # (side, signal z)
    pending_exit: tuple[str, float] | None = None  # (reason, signal z)

    def mark(pos: _Position, price_y: float, price_x: float) -> float:
        value = pos.side * w_y * (price_y - pos.last_y) / pos.entry_y
        value += pos.side * x_sign * w_x * (price_x - pos.last_x) / pos.entry_x
        pos.last_y, pos.last_x = price_y, price_x
        return value

    def close(pos: _Position, t: int, reason: str, exit_z: float) -> None:
        gross = pos.side * w_y * (pos.last_y / pos.entry_y - 1.0)
        gross += pos.side * x_sign * w_x * (pos.last_x / pos.entry_x - 1.0)
        turnover[t] += 1.0
        trades.append(
            Trade(
                window=window,
                y=fit.y,
                x=fit.x,
                sector=fit.sector,
                side=pos.side,
                entry_pos=pos.entry_pos,
                exit_pos=t,
                entry_z=pos.entry_z,
                exit_z=exit_z,
                exit_reason=reason,
                gross_return=gross,
                holding_sessions=t - pos.entry_pos,
                beta=fit.beta,
            )
        )

    for t in range(n):
        opens_ok = math.isfinite(oy[t]) and math.isfinite(ox[t])
        # 1. fills at the open
        if position is not None and pending_exit is not None and opens_ok:
            daily[t] += mark(position, oy[t], ox[t])
            close(position, t, pending_exit[0], pending_exit[1])
            position, pending_exit = None, None
        if position is None and pending_entry is not None and opens_ok:
            side, signal_z = pending_entry
            position = _Position(side, t, signal_z, oy[t], ox[t], oy[t], ox[t])
            turnover[t] += 1.0
            pending_entry = None
        # 2. mark at the close with the last available prices
        if position is not None:
            price_y = cy[t] if math.isfinite(cy[t]) else position.last_y
            price_x = cx[t] if math.isfinite(cx[t]) else position.last_x
            daily[t] += mark(position, price_y, price_x)
        last = t == n - 1
        # 3. signals at the close (a missing close on either leg emits none)
        if math.isfinite(z[t]):
            if position is not None and pending_exit is None:
                if abs(z[t]) <= rule.z_exit:
                    pending_exit = ("reverted", float(z[t]))
                elif abs(z[t]) >= rule.z_stop:
                    pending_exit = ("stop", float(z[t]))
            elif position is None and pending_entry is None and not last:
                if z[t] <= -rule.z_entry:
                    pending_entry = (1, float(z[t]))
                elif z[t] >= rule.z_entry:
                    pending_entry = (-1, float(z[t]))
        # 4. the window's last session closes out at its (last available) close
        if last and position is not None:
            close(position, t, "window_end", float(z[t]) if math.isfinite(z[t]) else float("nan"))
            position = None
    return PairSimulation(fit=fit, window=window, trades=tuple(trades), daily=daily, turnover=turnover)
