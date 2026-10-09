# tests/risk/test_book_vol.py
import math
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from agentic_trader.risk import BookVolInputs, book_vol_factor


def cov(symbols, vols, corr):
    """Daily covariance from per-unit-notional vols and one common correlation."""
    n = len(symbols)
    c = np.full((n, n), corr, dtype=float)
    np.fill_diagonal(c, 1.0)
    v = np.array(vols, dtype=float)
    return pd.DataFrame(np.outer(v, v) * c, index=symbols, columns=symbols)


def portfolio_vol(weights, covariance):
    w = np.array([weights.get(s, 0.0) for s in covariance.index])
    return math.sqrt(float(w @ covariance.to_numpy() @ w))


def test_empty_book_scales_by_the_candidate_alone():
    inputs = BookVolInputs(
        weights={},
        candidate="AAA",
        candidate_notional=10_000.0,
        covariance=cov(["AAA"], [0.02], 0.0),
        budget_dollars=100.0,
    )
    result = book_vol_factor(inputs)
    assert result.vol_before == 0.0
    assert math.isclose(result.vol_after_full, 200.0)
    assert math.isclose(result.factor, 0.5)
    assert math.isclose(result.vol_after_scaled, 100.0, rel_tol=1e-9)


def test_within_budget_is_factor_one():
    inputs = BookVolInputs(
        weights={"BBB": 5_000.0},
        candidate="AAA",
        candidate_notional=1_000.0,
        covariance=cov(["AAA", "BBB"], [0.01, 0.01], 0.0),
        budget_dollars=1_000.0,
    )
    result = book_vol_factor(inputs)
    assert result.factor == 1.0
    assert result.vol_after_scaled == result.vol_after_full


def test_book_already_over_budget_is_factor_zero():
    inputs = BookVolInputs(
        weights={"BBB": 50_000.0},
        candidate="AAA",
        candidate_notional=1_000.0,
        covariance=cov(["AAA", "BBB"], [0.02, 0.02], 0.5),
        budget_dollars=100.0,
    )
    result = book_vol_factor(inputs)
    assert result.factor == 0.0
    assert result.vol_before > inputs.budget_dollars


def test_hedging_candidate_is_not_scaled():
    """A short against a correlated long book lowers vol: factor 1 even though the candidate's own vol
    exceeds the budget."""
    inputs = BookVolInputs(
        weights={"BBB": 20_000.0},
        candidate="AAA",
        candidate_notional=-15_000.0,
        covariance=cov(["AAA", "BBB"], [0.02, 0.02], 0.9),
        budget_dollars=380.0,
    )
    result = book_vol_factor(inputs)
    assert result.vol_after_full < result.vol_before
    assert result.factor == 1.0


def test_root_matches_numeric_solution():
    covariance = cov(["AAA", "BBB", "CCC"], [0.015, 0.02, 0.025], 0.4)
    weights = {"BBB": 12_000.0, "CCC": -4_000.0}
    inputs = BookVolInputs(
        weights=weights, candidate="AAA", candidate_notional=20_000.0, covariance=covariance, budget_dollars=400.0
    )
    result = book_vol_factor(inputs)
    assert 0.0 < result.factor < 1.0
    scaled = {**weights, "AAA": result.factor * 20_000.0}
    assert math.isclose(portfolio_vol(scaled, covariance), 400.0, rel_tol=1e-9)
    assert math.isclose(result.marginal_vol_full, result.vol_after_full - result.vol_before)


@pytest.mark.parametrize("budget", [100.0, 300.0, 1_000.0, 5_000.0])
def test_factor_is_monotone_in_budget_and_bounded(budget):
    covariance = cov(["AAA", "BBB"], [0.02, 0.02], 0.3)
    base = {"weights": {"BBB": 8_000.0}, "candidate": "AAA", "candidate_notional": 30_000.0, "covariance": covariance}
    lo = book_vol_factor(BookVolInputs(**base, budget_dollars=budget)).factor
    hi = book_vol_factor(BookVolInputs(**base, budget_dollars=budget * 2)).factor
    assert 0.0 <= lo <= hi <= 1.0


@pytest.mark.parametrize(
    "bad",
    [
        {"weights": {"ZZZ": 1.0}},  # symbol missing from the covariance
        {"candidate": "ZZZ"},
        {"budget_dollars": 0.0},
        {"covariance": pd.DataFrame([[float("nan")]], index=["AAA"], columns=["AAA"]), "weights": {}},
        {"covariance": pd.DataFrame([[-1e-4]], index=["AAA"], columns=["AAA"]), "weights": {}},
    ],
)
def test_invalid_inputs_raise_value_error(bad):
    base = {
        "weights": {},
        "candidate": "AAA",
        "candidate_notional": 1_000.0,
        "covariance": cov(["AAA"], [0.02], 0.0),
        "budget_dollars": 100.0,
    }
    with pytest.raises(ValueError):
        book_vol_factor(BookVolInputs(**{**base, **bad}))


def test_over_budget_book_with_partial_hedge_is_zero_not_oversized():
    """A short that lowers vol but cannot reach the budget: the rule sizes nothing rather than
    sending a card whose scaled vol is still above budget."""
    inputs = BookVolInputs(
        weights={"BBB": 20_000.0},
        candidate="AAA",
        candidate_notional=-1_000.0,
        covariance=cov(["AAA", "BBB"], [0.02, 0.02], 0.9),
        budget_dollars=300.0,
    )
    result = book_vol_factor(inputs)
    assert result.vol_before > 300.0
    assert result.vol_after_full > 300.0
    assert result.factor == 0.0
    assert result.vol_after_scaled == result.vol_before


def test_over_budget_book_with_overshooting_hedge_is_zero():
    """A short so large the book would flip past the budget on the other side: not sized to the far root."""
    inputs = BookVolInputs(
        weights={"BBB": 20_000.0},
        candidate="AAA",
        candidate_notional=-40_000.0,
        covariance=cov(["AAA", "BBB"], [0.02, 0.02], 0.9),
        budget_dollars=300.0,
    )
    assert book_vol_factor(inputs).factor == 0.0


def test_zero_candidate_notional_over_budget_book_is_zero():
    inputs = BookVolInputs(
        weights={"BBB": 50_000.0},
        candidate="AAA",
        candidate_notional=0.0,
        covariance=cov(["AAA", "BBB"], [0.02, 0.02], 0.5),
        budget_dollars=100.0,
    )
    assert book_vol_factor(inputs).factor == 0.0


def test_scaled_vol_never_exceeds_budget_when_factor_positive():
    covariance = cov(["AAA", "BBB", "CCC"], [0.01, 0.03, 0.02], 0.2)
    for notional in (500.0, 5_000.0, 50_000.0, -500.0, -5_000.0):
        result = book_vol_factor(
            BookVolInputs(
                weights={"BBB": 9_000.0, "CCC": 3_000.0},
                candidate="AAA",
                candidate_notional=notional,
                covariance=covariance,
                budget_dollars=250.0,
            )
        )
        if result.factor > 0:
            assert result.vol_after_scaled <= 250.0 + 1e-9


@pytest.mark.parametrize(
    "bad",
    [
        {"weights": {"AAA": float("nan")}},
        {"weights": {"AAA": "ten"}},
        {"budget_dollars": None},
        {
            "covariance": pd.DataFrame([[1e-4, 2e-4], [1e-4, 1e-4]], index=["AAA", "BBB"], columns=["AAA", "BBB"]),
            "weights": {},
        },
        {
            "covariance": pd.DataFrame([[1e-4, 1e-4], [1e-4, 1e-4]], index=["AAA", "AAA"], columns=["AAA", "AAA"]),
            "weights": {},
        },
    ],
)
def test_more_invalid_inputs_raise_value_error_only(bad):
    base = {
        "weights": {},
        "candidate": "AAA",
        "candidate_notional": 1_000.0,
        "covariance": cov(["AAA"], [0.02], 0.0),
        "budget_dollars": 100.0,
    }
    with pytest.raises(ValueError):
        book_vol_factor(BookVolInputs(**{**base, **bad}))


def test_boundary_rounding_does_not_raise():
    """An uncorrelated candidate against a book sitting exactly at the budget: rounding in
    ``sqrt(a) > budget`` versus ``a > budget^2`` must never reach a negative discriminant."""
    covariance = cov(["AAA", "BBB"], [0.02, 0.02], 0.0)
    inputs = BookVolInputs(
        weights={"BBB": 215.0}, candidate="AAA", candidate_notional=1e5, covariance=covariance, budget_dollars=4.3
    )
    result = book_vol_factor(inputs)
    assert 0.0 <= result.factor <= 1.0


def test_decimal_inputs_are_coerced_to_float():
    inputs = BookVolInputs(
        weights={"BBB": Decimal(5000)},
        candidate="AAA",
        candidate_notional=Decimal(1000),
        covariance=cov(["AAA", "BBB"], [0.01, 0.01], 0.0),
        budget_dollars=Decimal(1000),
    )
    assert book_vol_factor(inputs).factor == 1.0
