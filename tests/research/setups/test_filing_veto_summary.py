"""``filing_veto_summary`` and the ``filings_would_block`` frame column."""

from __future__ import annotations

import pandas as pd
import pytest

from agentic_trader.research.setups.outcomes import _filing_veto_group, _filings_would_block, filing_veto_summary
from tests.research.setups.test_outcomes import DECIDED_AT, _candidate, _event


def _with(filings, **kwargs):
    return {**_candidate(**kwargs), "filings": filings}


def _dynamic(filings, **kwargs):
    """A dynamic-universe candidate, tagged as ``_journal_scan_ranking`` tags one."""
    return {**_with(filings, **kwargs), "dynamic": True, "dynamic_source": "most_actives"}


UNCOVERED = {"status": "uncovered", "would_block": None, "category": None}


def _ok(would_block, category=None):
    return {"status": "ok", "would_block": would_block, "category": category}


EVENTS = [
    _event(
        "s1",
        DECIDED_AT,
        [
            _with(_ok(True, "late_filing"), contract="AAA"),
            _with(_ok(False), contract="BBB"),
            _with(UNCOVERED, contract="CCC"),
            _with(None, contract="DDD"),
        ],
    ),
    _event(
        "s2",
        DECIDED_AT,
        [
            _with(_ok(True, "adverse_8k"), contract="EEE"),
            _with(_ok(True, "late_filing"), contract="FFF"),
            _candidate(contract="GGG"),  # written before the field
        ],
    ),
]


def _frame() -> pd.DataFrame:
    rows = [
        # (sent, hit, would_block, net_excess_r)
        (True, "stop", True, -1.0),
        (True, "target", True, 0.5),
        (True, "immature", True, None),
        (False, "stop", True, -2.0),  # a runner-up: not a card
        (True, "stop", False, 3.0),
        (True, "stop", None, 4.0),
    ]
    return pd.DataFrame(rows, columns=["sent", "hit", "filings_would_block", "net_excess_r"])


def test_summary_counts_rates_and_card_means():
    out = filing_veto_summary(EVENTS, _frame())
    assert out["candidates"] == {"n": 5, "by_status": {"ok": 4, "uncovered": 1}, "coverage": pytest.approx(4 / 5)}
    assert out["would_block"]["total"] == 3
    assert out["would_block"]["by_category"] == {"adverse_8k": 1, "late_filing": 2}
    assert out["would_block"]["rate"] == pytest.approx(3 / 4)
    assert out["cards"]["n_would_block"] == 3
    assert out["cards"]["n_matured"] == 2
    assert out["cards"]["mean_net_excess_r"] == pytest.approx(-0.25)


def test_dynamic_names_are_reported_apart_from_static_ones():
    """The capture never covers dynamic-universe names in v1, so they journal ``uncovered`` by design."""
    dynamic_scan = _event(
        "s3",
        DECIDED_AT,
        [
            _dynamic(UNCOVERED, contract="DYN1"),
            _dynamic(UNCOVERED, contract="DYN2"),
            _dynamic(_ok(True, "offering"), contract="DYN3"),
            _dynamic(None, contract="DYN4", direction="SHORT"),  # a short carries no block
        ],
    )
    out = filing_veto_summary([*EVENTS, dynamic_scan], pd.DataFrame())
    assert out["candidates"] == {"n": 8, "by_status": {"ok": 5, "uncovered": 3}, "coverage": pytest.approx(5 / 8)}
    assert out["would_block"] == {
        "total": 4,
        "by_category": {"adverse_8k": 1, "late_filing": 2, "offering": 1},
        "rate": pytest.approx(4 / 5),
    }
    assert out["by_universe"]["static"] == {
        "n": 5,
        "by_status": {"ok": 4, "uncovered": 1},
        "coverage": pytest.approx(4 / 5),
        "would_block": 3,
        "would_refuse": 0,
        "by_category": {"adverse_8k": 1, "late_filing": 2},
        "rate": pytest.approx(3 / 4),
    }
    assert out["by_universe"]["dynamic"] == {
        "n": 3,
        "by_status": {"ok": 1, "uncovered": 2},
        "coverage": pytest.approx(1 / 3),
        "would_block": 1,
        "would_refuse": 0,
        "by_category": {"offering": 1},
        "rate": pytest.approx(1.0),
    }


def test_summary_of_nothing_is_empty_not_an_error():
    out = filing_veto_summary([], pd.DataFrame())
    assert out["candidates"] == {"n": 0, "by_status": {}, "coverage": None}
    assert out["would_block"] == {"total": 0, "by_category": {}, "rate": None}
    empty = {
        "n": 0,
        "by_status": {},
        "coverage": None,
        "would_block": 0,
        "would_refuse": 0,
        "by_category": {},
        "rate": None,
    }
    assert out["by_universe"] == {"static": empty, "dynamic": empty}
    assert out["cards"] == {"n_would_block": 0, "mean_net_excess_r": None, "n_matured": 0}


def test_filings_would_block_column_reads_only_ok_blocks():
    assert _filings_would_block(_with(_ok(True, "late_filing"))) is True
    assert _filings_would_block(_with(_ok(False))) is False
    assert _filings_would_block(_with({"status": "stale", "would_block": True})) is None
    assert _filings_would_block(_with(None)) is None
    assert _filings_would_block(_candidate()) is None


def test_group_counts_would_refuse_beside_would_block():
    blocks = [
        {**_ok(True, "offering"), "would_refuse": False},
        {**_ok(True, "late_filing"), "would_refuse": True},
        {**_ok(False), "would_refuse": False},
        UNCOVERED,
    ]
    group = _filing_veto_group(blocks)
    assert group["would_block"] == 2 and group["would_refuse"] == 1
