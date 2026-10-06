"""The scan's book gates and admission agree on every shared rule, and admission's string API agrees with the typed API."""

import numpy as np

from agentic_trader.broker.base import OrderRequest
from agentic_trader.config import AppConfig
from agentic_trader.constants import AssetClass, OrderSide
from agentic_trader.execution.admission import reservation_rejection
from agentic_trader.risk import (
    Book,
    EntryIntent,
    RiskLimits,
    RiskRule,
    admission_gates,
    book_gates,
    per_trade_risk_budget,
)
from tests.risk.test_adversarial import pick, random_config


SHARED = {
    RiskRule.EXPOSURE_UNKNOWN,
    RiskRule.DRAWDOWN_HALT,
    RiskRule.AGGREGATE_STOP_RISK,
    RiskRule.CONCURRENT_POSITIONS,
    RiskRule.SAME_SYMBOL,
    RiskRule.PORTFOLIO_NOTIONAL,
    RiskRule.ASSET_CLASS_NOTIONAL,
    RiskRule.CORRELATION_GROUP,
}


def test_scan_and_admission_agree_on_shared_rules():
    """Per seeded case (drawn limits, book, budget inputs and intent): admission's string API
    (``reservation_rejection`` on the equivalent ``OrderRequest``) returns exactly ``admission_gates``' reason, and
    the scan's first shared-rule rejection (``book_gates``) equals admission's whenever that is a shared rule."""
    r = np.random.default_rng(7)
    for _ in range(1500):
        config = random_config(r)
        config.contracts = {}  # AAPL is an unconfigured equity: multiplier 1.0
        limits = RiskLimits.from_config(config)
        rows = [
            {
                "contract": str(r.choice(["AAPL", "MSFT", "SPY"])),
                "direction": str(r.choice(["LONG", "SHORT"])),
                "asset_class": "EQUITY",
                "notional_value": float(r.uniform(0, 30_000)),
                "risk_dollars": float(r.uniform(0, 1_000)),
                "status": str(r.choice(["EXECUTED", "SUBMITTING"])),
            }
            for _ in range(int(r.integers(0, 5)))
        ]
        drawdown = float(r.choice([0.0, 0.04, 0.06]))
        equity = pick(r, [None, 50_000.0, 150_000.0])
        budget = per_trade_risk_budget(limits, equity=equity, drawdown_pct=drawdown)
        quantity, target = float(r.uniform(1, 400)), float(r.choice([102.0, 103.5]))
        current_price = pick(r, [None, float(r.uniform(95, 105))])
        intent = EntryIntent(
            "AAPL", "LONG", "EQUITY", quantity, entry=100.0, stop=99.0, target=target, current_price=current_price
        )
        admission = admission_gates(intent, Book.from_signal_rows(rows, reservations=True), budget, limits)
        request = OrderRequest(
            symbol="AAPL",
            asset_class=AssetClass.EQUITY,
            direction="LONG",
            side=OrderSide.BUY,
            quantity=quantity,
            entry_price=100.0,
            stop_loss=99.0,
            take_profit=target,
        )
        string_api = reservation_rejection(
            request, rows, config, current_drawdown_pct=drawdown, current_equity=equity, current_price=current_price
        )
        assert string_api == (admission.reason if admission else None), (rows, intent, string_api, admission)
        book = Book.from_signal_rows(rows, reservations=False)
        scan_first = next((x.rule for x in book_gates(intent, book, budget, limits) if x.rule in SHARED), None)
        admission_shared = admission.rule if admission and admission.rule in SHARED else None
        # Admission also runs rules the scan's book gates do not (reward/risk, the per-trade caps); a case whose
        # first admission rejection is one of those is skipped.
        if admission is not None and admission.rule not in SHARED:
            continue
        assert scan_first == admission_shared, (rows, intent, scan_first, admission)


def test_reservation_rejection_matches_the_typed_api():
    config = AppConfig()
    config.portfolio.correlation_groups = {"g": ["AAPL", "MSFT"]}
    config.portfolio.max_correlated_positions = 1
    config.contracts = {}
    limits = RiskLimits.from_config(config)
    rows = [
        {
            "contract": "MSFT",
            "direction": "LONG",
            "asset_class": "EQUITY",
            "notional_value": 1000.0,
            "risk_dollars": 10.0,
            "status": "EXECUTED",
        }
    ]
    request = OrderRequest(
        symbol="AAPL",
        asset_class=AssetClass.EQUITY,
        direction="LONG",
        side=OrderSide.BUY,
        quantity=10.0,
        entry_price=100.0,
        stop_loss=99.0,
        take_profit=102.0,
    )
    expected = admission_gates(
        EntryIntent("AAPL", "LONG", "EQUITY", 10.0, 100.0, 99.0, 102.0),
        Book.from_signal_rows(rows, reservations=True),
        per_trade_risk_budget(limits, equity=None, drawdown_pct=0.0),
        limits,
    )
    assert expected is not None and expected.rule is RiskRule.CORRELATION_GROUP
    assert reservation_rejection(request, rows, config) == expected.reason
    rows[0]["direction"] = "SHORT"
    assert reservation_rejection(request, rows, config) is None
