import math

from agentic_trader.risk import Book, BookPosition


def test_position_validity():
    assert BookPosition("AAPL", "LONG", "EQUITY", 1000.0, 50.0).valid
    assert not BookPosition("AAPL", "LONG", "EQUITY", None, 50.0).valid
    assert not BookPosition("AAPL", "LONG", "EQUITY", 1000.0, math.nan).valid
    assert not BookPosition("AAPL", "LONG", "EQUITY", -1.0, 50.0).valid
    assert BookPosition("/MES", "LONG", "FUTURES", 1.0, 1.0).key == "MES"


def test_from_signal_rows_normalises_and_flags_reservations():
    rows = [
        {
            "contract": "aapl",
            "direction": "long",
            "asset_class": "equity",
            "notional_value": 1000,
            "risk_dollars": 50,
            "status": "EXECUTED",
        },
        {
            "symbol": "/MES",
            "direction": "SHORT",
            "asset_class": "FUTURES",
            "notional_value": "2500.5",
            "risk_dollars": None,
            "status": "SUBMITTING",
        },
    ]
    book = Book.from_signal_rows(rows, reservations=True)
    assert [p.key for p in book.positions] == ["AAPL", "MES"]
    assert book.positions[0].direction == "LONG" and book.positions[0].asset_class == "EQUITY"
    assert book.positions[1].reservation is True and book.positions[0].reservation is False
    assert book.positions[1].notional == 2500.5 and book.positions[1].planned_risk is None
    assert [p.key for p in book.invalid] == ["MES"]
    assert Book.from_signal_rows(rows, reservations=False).positions[1].reservation is False


def test_book_aggregates():
    book = Book.from_signal_rows(
        [
            {
                "contract": "AAPL",
                "direction": "LONG",
                "asset_class": "EQUITY",
                "notional_value": 1000,
                "risk_dollars": 50,
            },
            {
                "contract": "MSFT",
                "direction": "SHORT",
                "asset_class": "EQUITY",
                "notional_value": 500,
                "risk_dollars": 25,
            },
            {
                "contract": "/MES",
                "direction": "LONG",
                "asset_class": "FUTURES",
                "notional_value": 2000,
                "risk_dollars": 100,
            },
        ],
        reservations=False,
    )
    assert book.count == 3
    assert book.notional() == 3500 and book.notional_for("EQUITY") == 1500 and book.planned_risk() == 175
    assert book.holds("mes") and not book.holds("TSLA")
    assert [p.key for p in book.same_direction_in(frozenset({"AAPL", "MSFT", "MES"}), "LONG")] == ["AAPL", "MES"]
    extended = book.with_position(BookPosition("TSLA", "LONG", "EQUITY", 10.0, 1.0))
    assert extended.count == 4 and book.count == 3


def test_invalid_numbers_are_not_coerced_to_zero():
    book = Book.from_signal_rows(
        [{"contract": "X", "direction": "LONG", "asset_class": "EQUITY", "notional_value": "abc", "risk_dollars": 1}],
        reservations=False,
    )
    assert book.positions[0].notional is None and not book.positions[0].valid
