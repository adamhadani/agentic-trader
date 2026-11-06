from __future__ import annotations

from datetime import date

import pytest

from agentic_trader.data.edgar_filings import (
    FilingCategory,
    MatchedFiling,
    classify,
    normalize_ticker,
    parse_recent,
    ticker_ciks,
)


def test_classify_late_filing_forms() -> None:
    assert classify("NT 10-K", ()) is FilingCategory.LATE_FILING
    assert classify("NT 10-Q/A", ()) is FilingCategory.LATE_FILING
    assert classify("10-K", ()) is None


def test_classify_adverse_8k_exact_items() -> None:
    assert classify("8-K", ("2.02", "9.01")) is None
    assert classify("8-K", ("2.05", "9.01")) is FilingCategory.ADVERSE_8K
    assert classify("8-K/A", ("4.02",)) is FilingCategory.ADVERSE_8K
    assert classify("8-K", ("2.050",)) is None


def test_classify_offering_excludes_424b2_424b3() -> None:
    assert classify("424B5", ()) is FilingCategory.OFFERING
    assert classify("424B4", ()) is FilingCategory.OFFERING
    assert classify("424B2", ()) is None
    assert classify("424B3", ()) is None


def _payload() -> dict:
    return {
        "filings": {
            "recent": {
                "accessionNumber": ["a1", "a2", "a3", "a4"],
                "filingDate": ["2026-10-30", "2026-09-01", "2026-10-02", "2026-10-15"],
                "acceptanceDateTime": [
                    "2026-10-30T16:05:23.000Z",
                    "2026-09-01T10:00:00.000Z",
                    "2026-10-02T12:00:00.000Z",
                    "2026-10-15T12:00:00.000Z",
                ],
                "form": ["8-K", "NT 10-Q", "424B5", "8-K"],
                "items": ["2.05, 9.01", "", "", "2.02,9.01"],
            }
        }
    }


def test_parse_recent_filters_window_and_sorts_newest_first() -> None:
    got = parse_recent(_payload(), "ABC", date(2026, 9, 7))
    assert [f.accession for f in got] == ["a1", "a3"]
    assert got[0].items == ("2.05", "9.01")
    assert got[0].category is FilingCategory.ADVERSE_8K
    assert got[0].symbol == "ABC"
    assert got[1].items == ()


def test_parse_recent_rejects_mismatched_columns() -> None:
    p = _payload()
    p["filings"]["recent"]["form"] = ["8-K"]
    with pytest.raises(ValueError):
        parse_recent(p, "ABC", date(2026, 9, 7))
    with pytest.raises(ValueError):
        parse_recent({"filings": {}}, "ABC", date(2026, 9, 7))


def test_ticker_ciks_maps_class_shares_and_drops_collisions() -> None:
    payload = {
        "0": {"cik_str": 1, "ticker": "BF-B"},
        "1": {"cik_str": 2, "ticker": "XX"},
        "2": {"cik_str": 3, "ticker": "XX"},
    }
    assert ticker_ciks(payload, ["BF.B", "XX", "ZZ"]) == ({"BF.B": 1}, ["XX", "ZZ"])


def test_normalize_ticker() -> None:
    assert normalize_ticker("bf-b") == "BF.B"


def test_matched_filing_payload_round_trip() -> None:
    f = MatchedFiling("ABC", FilingCategory.ADVERSE_8K, "8-K", ("2.05",), date(2026, 10, 30), None, "a1")
    assert MatchedFiling.from_payload(f.to_payload()) == f
    g = MatchedFiling("ABC", FilingCategory.OFFERING, "424B5", (), date(2026, 10, 30), "2026-10-30T16:05:23.000Z", "a2")
    assert MatchedFiling.from_payload(g.to_payload()) == g


def test_describe() -> None:
    d = date(2026, 10, 30)
    assert (
        MatchedFiling("A", FilingCategory.ADVERSE_8K, "8-K", ("2.05", "2.06"), d, None, "x").describe()
        == "8-K Items 2.05, 2.06"
    )
    assert MatchedFiling("A", FilingCategory.LATE_FILING, "NT 10-K", (), d, None, "x").describe() == "NT 10-K"
    assert MatchedFiling("A", FilingCategory.OFFERING, "424B5", (), d, None, "x").describe() == "424B5"
