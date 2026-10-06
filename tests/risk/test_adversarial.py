"""Hostile inputs: constructors reject, rules never raise on valid inputs, results are order-independent and monotone."""

import math

import numpy as np
import pytest

from agentic_trader.config import AppConfig
from agentic_trader.risk import (
    Book,
    BookPosition,
    EntryIntent,
    RiskLimits,
    RiskRule,
    admission_gates,
    book_gates,
    per_trade_risk_budget,
)


SEED, CASES = 20261006, 2000
HOSTILE_NUMBERS = [math.nan, math.inf, -math.inf, -1.0, 0.0]
SYMBOLS = ["AAPL", "aapl", "/MES", "MES", "mes ", "SPY", "BTC/USD", "", "X" * 40]
CLASSES = ["EQUITY", "equity", "FUTURES", "CRYPTO", "FX", "BOND", ""]
DIRECTIONS = ["LONG", "SHORT", "long", "FLAT", ""]


def rng():
    return np.random.default_rng(SEED)


def pick(r, options):
    """Pick by index, so the draw is deterministic for the seed and returns each option with its own type."""
    return options[int(r.integers(len(options)))]


def random_config(r) -> AppConfig:
    """A config whose every limit a rule reads is drawn: capital, position and group limits, the stop-risk
    percentage, the notional and class caps (``None`` is uncapped), the minimum reward/risk and the drawdown policy.
    """
    config = AppConfig()
    config.portfolio.cash = float(r.choice([1_000.0, 100_000.0, 1e9]))
    config.portfolio.max_concurrent_positions = int(r.integers(0, 6))
    config.portfolio.max_correlated_positions = int(r.integers(0, 3))
    config.portfolio.correlation_groups = pick(
        r, [{}, {"g": ["/MES", "SPY", "aapl"]}, {"a": ["AAPL"], "b": ["AAPL", "MSFT"]}]
    )
    config.portfolio.max_crypto_exposure = pick(r, [None, 20_000.0])
    config.portfolio.max_stop_risk_pct = float(r.choice([0.005, 0.02, 0.1]))
    config.portfolio.max_notional_exposure = float(r.choice([10_000.0, 60_000.0, 1e7]))
    config.portfolio.max_equity_exposure = pick(r, [None, 10_000.0, 40_000.0])
    config.portfolio.max_futures_exposure = pick(r, [None, 10_000.0, 40_000.0])
    config.sizing.max_trade_notional_cap = float(r.choice([5_000.0, 30_000.0, 1e7]))
    config.risk.min_risk_reward_ratio = float(r.choice([1.5, 2.0, 3.0]))
    config.sizing.drawdown_gating_enabled = bool(r.integers(0, 2))
    threshold, halt = pick(r, [(0.03, 0.06), (0.0, 0.02), (0.1, 0.5)])
    config.sizing.drawdown_haircut_threshold_pct, config.sizing.max_drawdown_stop_pct = threshold, halt
    config.sizing.drawdown_min_risk_multiplier = float(r.choice([0.1, 0.5, 1.0]))
    return config


def random_limits(r) -> RiskLimits:
    return RiskLimits.from_config(random_config(r))


def random_position(r) -> BookPosition:
    return BookPosition(
        symbol=str(r.choice(SYMBOLS)),
        direction=str(r.choice(DIRECTIONS)),
        asset_class=str(r.choice(CLASSES)),
        notional=pick(r, [None, float(r.uniform(0, 50_000)), *HOSTILE_NUMBERS]),
        planned_risk=pick(r, [None, float(r.uniform(0, 2_000)), *HOSTILE_NUMBERS]),
        reservation=bool(r.integers(0, 2)),
    )


def valid_intent(r) -> EntryIntent:
    entry = float(r.uniform(1, 500))
    direction = str(r.choice(["LONG", "SHORT"]))
    stop = entry * (0.98 if direction == "LONG" else 1.02)
    target = entry * float(r.uniform(0.9, 1.1))  # may be an inverted bracket on purpose
    return EntryIntent(
        symbol=str(r.choice(SYMBOLS[:7])),
        direction=direction,
        asset_class=str(r.choice(CLASSES[:5])),
        quantity=float(r.uniform(1, 2000)),
        entry=entry,
        stop=stop,
        target=target,
        multiplier=float(r.choice([1.0, 5.0, 50.0])),
        current_price=pick(r, [None, entry * float(r.uniform(0.95, 1.05))]),
    )


def test_constructors_reject_hostile_numbers():
    for bad in HOSTILE_NUMBERS[:3] + [-5.0, 0.0]:
        with pytest.raises(ValueError):
            EntryIntent("AAPL", "LONG", "EQUITY", quantity=bad, entry=100.0, stop=99.0, target=102.0)
    with pytest.raises(ValueError):
        EntryIntent("AAPL", "SIDEWAYS", "EQUITY", quantity=1.0, entry=100.0, stop=99.0, target=102.0)
    for bad in (math.nan, math.inf):
        with pytest.raises(ValueError):
            per_trade_risk_budget(
                RiskLimits.from_config(AppConfig()), equity=None, drawdown_pct=0.0, macro_multiplier=bad
            )


def test_rules_never_raise_and_fail_closed_on_unknown_exposure():
    r = rng()
    for _ in range(CASES):
        limits = random_limits(r)
        book = Book(tuple(random_position(r) for _ in range(int(r.integers(0, 6)))))
        budget = per_trade_risk_budget(
            limits,
            equity=pick(r, [None, float(r.uniform(100, 1e6))]),
            drawdown_pct=float(r.choice([0.0, 0.02, 0.045, 0.06, 0.5])),
            macro_multiplier=float(r.choice([0.0, 0.3, 1.0, 2.0])),
        )
        intent = valid_intent(r)
        scan = book_gates(intent, book, budget, limits)
        admission = admission_gates(intent, book, budget, limits, regime_min_rr=float(r.choice([1.0, 2.0, 2.5])))
        if book.invalid:
            assert scan and scan[0].rule is RiskRule.EXPOSURE_UNKNOWN
            assert admission is not None and admission.rule is RiskRule.EXPOSURE_UNKNOWN
        for rejection in (*scan, admission):
            assert rejection is None or (isinstance(rejection.reason, str) and rejection.reason)


def test_results_are_order_independent_and_monotone():
    r = rng()
    for _ in range(CASES // 4):
        limits = random_limits(r)
        positions = [
            BookPosition(
                str(r.choice(SYMBOLS[:6])),
                str(r.choice(["LONG", "SHORT"])),
                "EQUITY",
                float(r.uniform(0, 20_000)),
                float(r.uniform(0, 500)),
            )
            for _ in range(int(r.integers(0, 5)))
        ]
        budget = per_trade_risk_budget(limits, equity=None, drawdown_pct=0.0)
        intent = valid_intent(r)
        forward = {x.rule for x in book_gates(intent, Book(tuple(positions)), budget, limits)}
        backward = {x.rule for x in book_gates(intent, Book(tuple(reversed(positions))), budget, limits)}
        assert forward == backward
        larger = Book((*positions, BookPosition("ZZZ", intent.direction, "EQUITY", 1.0, 1.0)))
        assert forward <= {x.rule for x in book_gates(intent, larger, budget, limits)}
