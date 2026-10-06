import math
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from agentic_trader.config import AppConfig
from agentic_trader.constants import StrategyType
from agentic_trader.risk import (
    Book,
    BookPosition,
    EntryIntent,
    Rejection,
    RiskLimits,
    RiskRule,
    admission_gates,
    aggregate_stop_risk,
    asset_class_notional,
    book_gates,
    concurrent_positions,
    correlation_group,
    drawdown_halt,
    earnings_days_out,
    entry_session_open,
    exposure_known,
    in_lockout_window,
    macro_lockout,
    meets_min_reward_risk,
    per_trade_notional,
    per_trade_risk,
    per_trade_risk_budget,
    portfolio_notional,
    quantity_cap,
    regime_breakout,
    required_reward_risk,
    reward_risk,
    same_symbol,
)


def limits(**overrides) -> RiskLimits:
    config = AppConfig()
    config.portfolio.cash = 100_000.0
    config.portfolio.max_notional_exposure = 60_000.0
    config.portfolio.max_equity_exposure = 40_000.0
    config.portfolio.max_concurrent_positions = 4
    config.portfolio.max_correlated_positions = 1
    config.portfolio.max_stop_risk_pct = 0.02
    config.portfolio.correlation_groups = {"index": ["/MES", "SPY"], "tech": ["AAPL", "MSFT"]}
    config.sizing.max_risk_pct_cap = 0.01
    config.sizing.max_trade_notional_cap = 30_000.0
    config.sizing.max_shares_per_trade = 500
    config.sizing.max_contracts_per_trade = 4
    config.risk.min_risk_reward_ratio = 2.0
    for key, value in overrides.items():
        section, _, name = key.partition("__")
        setattr(getattr(config, section), name, value)
    return RiskLimits.from_config(config)


def intent(**overrides) -> EntryIntent:
    base = {
        "symbol": "AAPL",
        "direction": "LONG",
        "asset_class": "EQUITY",
        "quantity": 10.0,
        "entry": 100.0,
        "stop": 99.0,
        "target": 102.0,
    }
    return EntryIntent(**{**base, **overrides})


def position(symbol="MSFT", direction="LONG", asset_class="EQUITY", notional=1000.0, risk=50.0, reservation=False):
    return BookPosition(symbol, direction, asset_class, notional, risk, reservation)


BUDGET = per_trade_risk_budget(limits(), equity=None, drawdown_pct=0.0)


def test_intent_derived_values_and_validation():
    i = intent(current_price=101.0, multiplier=2.0)
    assert (i.key, i.risk_distance, i.reward_distance) == ("AAPL", 1.0, 2.0)
    assert i.risk_dollars == 20.0 and i.exposure_price == 101.0 and i.notional == 2020.0
    assert intent(direction="SHORT", stop=101.0, target=98.0).risk_distance == 1.0
    assert intent(current_price=None).exposure_price == 100.0
    for bad in (
        {"quantity": 0.0},
        {"entry": math.nan},
        {"stop": -1.0},
        {"multiplier": math.inf},
        {"direction": "FLAT"},
        {"quantity": None},
    ):
        with pytest.raises(ValueError):
            intent(**bad)


def test_exposure_known_rejects_any_invalid_position():
    assert exposure_known(Book((position(),))) is None
    rejection = exposure_known(Book((position(), position("X", notional=None))))
    assert rejection is not None and rejection.rule is RiskRule.EXPOSURE_UNKNOWN


def test_drawdown_halt_only_at_zero_factor():
    assert drawdown_halt(BUDGET) is None
    halted = per_trade_risk_budget(limits(), equity=None, drawdown_pct=0.06)
    assert drawdown_halt(halted).rule is RiskRule.DRAWDOWN_HALT and "6.0%" in drawdown_halt(halted).reason


@pytest.mark.parametrize(
    ("quantity", "macro", "expected"),
    [
        (999.0, 1.0, None),
        (1000.0, 1.0, None),
        (1001.0, 1.0, RiskRule.PER_TRADE_RISK),
        (600.0, 0.5, RiskRule.PER_TRADE_RISK),
        (500.0, 0.5, None),
    ],
)
def test_per_trade_risk_against_the_budget(quantity, macro, expected):
    # risk per share 1.0; budget 1,000 at full macro, 500 at macro 0.5; equal to the budget passes
    budget = per_trade_risk_budget(limits(), equity=None, drawdown_pct=0.0, macro_multiplier=macro)
    result = per_trade_risk(intent(quantity=quantity, stop=99.0), budget)
    assert (result.rule if result else None) == expected
    if result and macro < 1:
        assert "macro-adjusted" in result.reason


@pytest.mark.parametrize(
    ("quantity", "price", "expected"),
    [(300.0, None, None), (301.0, None, RiskRule.PER_TRADE_NOTIONAL), (299.0, 101.0, RiskRule.PER_TRADE_NOTIONAL)],
)
def test_per_trade_notional_uses_the_exposure_price(quantity, price, expected):
    result = per_trade_notional(intent(quantity=quantity, current_price=price), limits())
    assert (result.rule if result else None) == expected


@pytest.mark.parametrize(
    ("asset_class", "quantity", "expected"),
    [
        ("EQUITY", 500.0, None),
        ("EQUITY", 501.0, RiskRule.QUANTITY_CAP),
        ("FUTURES", 4.0, None),
        ("FUTURES", 5.0, RiskRule.QUANTITY_CAP),
    ],
)
def test_quantity_cap_by_asset_class(asset_class, quantity, expected):
    result = quantity_cap(intent(asset_class=asset_class, quantity=quantity, stop=99.0, target=1000.0), limits())
    assert (result.rule if result else None) == expected


def test_required_reward_risk_is_the_max_of_config_and_regime():
    assert required_reward_risk(limits(), None) == 2.0
    assert required_reward_risk(limits(), 2.2) == 2.2
    assert required_reward_risk(limits(), 1.5) == 2.0


@pytest.mark.parametrize(
    ("target", "required", "expected"),
    [(102.0, 2.0, None), (101.99, 2.0, RiskRule.REWARD_RISK), (102.0, 2.2, RiskRule.REWARD_RISK), (102.2, 2.2, None)],
)
def test_reward_risk_rounds_like_the_evaluator(target, required, expected):
    result = reward_risk(intent(target=target), required)
    assert (result.rule if result else None) == expected
    assert meets_min_reward_risk(2.0, 0.0, 2.0) is False  # degenerate bracket never passes


def test_inverted_bracket_is_a_reward_risk_rejection_not_an_exception():
    assert reward_risk(intent(stop=101.0, target=102.0), 2.0).rule is RiskRule.REWARD_RISK


@pytest.mark.parametrize(
    ("existing", "quantity", "expected"), [(1500.0, 500.0, None), (1500.0, 501.0, RiskRule.AGGREGATE_STOP_RISK)]
)
def test_aggregate_stop_risk_counts_the_whole_book(existing, quantity, expected):
    # budget 100,000 × 2% = 2,000; intent risk = quantity × 1.0
    book = Book((position(risk=existing),))
    result = aggregate_stop_risk(intent(quantity=quantity, target=200.0), book, BUDGET, limits())
    assert (result.rule if result else None) == expected


def test_concurrent_positions_counts_reservations_too():
    full = Book(tuple(position(f"S{i}", reservation=i % 2 == 0) for i in range(4)))
    assert concurrent_positions(full, limits()).rule is RiskRule.CONCURRENT_POSITIONS
    assert concurrent_positions(Book(full.positions[:3]), limits()) is None


def test_same_symbol_is_normalised():
    assert same_symbol(intent(symbol="/MES"), Book((position("MES"),))).rule is RiskRule.SAME_SYMBOL
    assert same_symbol(intent(), Book((position("MSFT"),))) is None


@pytest.mark.parametrize(
    ("book_notional", "quantity", "expected"), [(59_000.0, 10.0, None), (59_000.0, 11.0, RiskRule.PORTFOLIO_NOTIONAL)]
)
def test_portfolio_notional(book_notional, quantity, expected):
    result = portfolio_notional(intent(quantity=quantity), Book((position(notional=book_notional),)), limits())
    assert (result.rule if result else None) == expected


def test_asset_class_notional_and_uncapped_classes():
    equity_book = Book((position(notional=39_500.0),))
    assert asset_class_notional(intent(quantity=5.0), equity_book, limits()) is None
    assert asset_class_notional(intent(quantity=6.0), equity_book, limits()).rule is RiskRule.ASSET_CLASS_NOTIONAL
    fx = intent(symbol="EURUSD", asset_class="FX", quantity=100_000.0, target=200.0)
    assert asset_class_notional(fx, Book(), limits()) is None  # no cap configured → uncapped, never KeyError
    assert (
        asset_class_notional(
            intent(asset_class="CRYPTO", quantity=1.0), Book(), limits(portfolio__max_crypto_exposure=None)
        )
        is None
    )


@pytest.mark.parametrize(
    ("book", "expected"),
    [
        (Book((position("MSFT", "LONG"),)), RiskRule.CORRELATION_GROUP),  # same group, same direction
        (Book((position("MSFT", "SHORT"),)), None),  # same group, opposite direction
        (Book((position("msft", "LONG"),)), RiskRule.CORRELATION_GROUP),  # case-normalised
        (Book((position("SPY", "LONG"),)), None),  # other group
        (Book(), None),
    ],
)
def test_correlation_group_same_direction_normalised(book, expected):
    result = correlation_group(intent(symbol="AAPL"), book, limits())
    assert (result.rule if result else None) == expected


def test_correlation_group_slash_prefix_and_empty_config():
    assert (
        correlation_group(
            intent(symbol="/MES", asset_class="FUTURES", quantity=1.0), Book((position("SPY"),)), limits()
        ).rule
        is RiskRule.CORRELATION_GROUP
    )
    assert (
        correlation_group(intent(symbol="AAPL"), Book((position("MSFT"),)), limits(portfolio__correlation_groups={}))
        is None
    )


def test_lockout_window_is_inclusive():
    event = datetime(2026, 10, 6, 12, 30, tzinfo=UTC)
    assert in_lockout_window(event - timedelta(minutes=60), event, 60, 30)
    assert in_lockout_window(event + timedelta(minutes=30), event, 60, 30)
    assert not in_lockout_window(event - timedelta(minutes=61), event, 60, 30)
    assert not in_lockout_window(event + timedelta(minutes=31), event, 60, 30)
    inside = macro_lockout("CPI", event, event, limits())
    assert inside.rule is RiskRule.MACRO_LOCKOUT and inside.reason == "Macro event lockout: CPI at 12:30 UTC."
    new_york = event.astimezone(ZoneInfo("America/New_York"))  # 08:30 EDT: the reason prints the UTC clock
    assert macro_lockout("CPI", new_york, event, limits()).reason == "Macro event lockout: CPI at 12:30 UTC."
    untitled = macro_lockout(None, event, event, limits())
    assert untitled.reason == "Macro event lockout: scheduled release at 12:30 UTC."
    assert macro_lockout(None, None, event, limits()) is None


@pytest.mark.parametrize(
    ("event", "today", "days", "expected"),
    [
        (date(2026, 10, 10), date(2026, 10, 6), 7, 4),
        (date(2026, 10, 13), date(2026, 10, 6), 7, 7),
        (date(2026, 10, 14), date(2026, 10, 6), 7, None),
        (date(2026, 10, 5), date(2026, 10, 6), 7, None),
        (None, date(2026, 10, 6), 7, None),
        (date(2026, 10, 7), date(2026, 10, 6), 0, None),
    ],
)
def test_earnings_days_out(event, today, days, expected):
    assert earnings_days_out(event, today, days) == expected


@pytest.mark.parametrize(
    ("is_open", "is_rth", "enforce", "expected"),
    [
        (True, True, True, None),
        (True, False, True, RiskRule.SESSION_NOT_RTH),
        (True, False, False, None),
        (False, False, False, RiskRule.SESSION_CLOSED),
        (False, True, True, RiskRule.SESSION_CLOSED),
    ],
)
def test_entry_session_open(is_open, is_rth, enforce, expected):
    result = entry_session_open(is_open, is_rth, enforce)
    assert (result.rule if result else None) == expected


def test_regime_breakout_only_for_squeeze_breakouts():
    assert regime_breakout("SQUEEZE_BREAKOUT", False).rule is RiskRule.REGIME_BREAKOUT
    assert regime_breakout(StrategyType.SQUEEZE_BREAKOUT, False).rule is RiskRule.REGIME_BREAKOUT
    assert regime_breakout("SQUEEZE_BREAKOUT", True) is None and regime_breakout("TREND_PULLBACK", False) is None


def test_composites_fix_the_order():
    bad_book = Book((position("X", notional=None), *[position(f"S{i}") for i in range(4)]))
    assert book_gates(intent(), bad_book, BUDGET, limits())[0].rule is RiskRule.EXPOSURE_UNKNOWN
    assert admission_gates(intent(), bad_book, BUDGET, limits()).rule is RiskRule.EXPOSURE_UNKNOWN
    full = Book(tuple(position(f"S{i}") for i in range(4)))
    assert book_gates(intent(), full, BUDGET, limits())[0].rule is RiskRule.CONCURRENT_POSITIONS
    assert (
        admission_gates(intent(quantity=2000.0, target=200.0), Book(), BUDGET, limits()).rule is RiskRule.PER_TRADE_RISK
    )
    assert admission_gates(intent(), Book(), BUDGET, limits(), regime_min_rr=2.2).rule is RiskRule.REWARD_RISK
    assert (
        admission_gates(intent(), Book(), BUDGET, limits()) is None
        and book_gates(intent(), Book(), BUDGET, limits()) == ()
    )
    assert str(Rejection(RiskRule.SAME_SYMBOL, "x")) == "x"


def test_book_gates_on_an_invalid_book_report_only_the_unknown_exposure():
    # Full (4 positions), over the stop-risk and notional budgets, same group: none of that is judged
    # while one position's exposure is unknown.
    bad_book = Book(
        (
            position("X", notional=None),
            position("MSFT", risk=5_000.0, notional=70_000.0),
            *[position(f"S{i}") for i in range(3)],
        )
    )
    rejections = book_gates(intent(), bad_book, BUDGET, limits())
    assert [r.rule for r in rejections] == [RiskRule.EXPOSURE_UNKNOWN]
    valid_book = Book(bad_book.positions[1:])
    assert RiskRule.EXPOSURE_UNKNOWN not in {r.rule for r in book_gates(intent(), valid_book, BUDGET, limits())}
    assert len(book_gates(intent(), valid_book, BUDGET, limits())) > 1
