import json
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from agentic_trader.data.corporate_actions import CorporateAction
from agentic_trader.research.alpha.dsl import FIELD_UNITS, compile_expression
from agentic_trader.research.insider_filings import (
    DROP_RULES,
    InsiderFilingStore,
    InsiderTransaction,
    files_sha256,
    parse_quarter,
    quarters_between,
)
from agentic_trader.research.pooled.campaign import InsiderInputs
from agentic_trader.research.pooled.formula import evaluate_prepared, require_dimensionless
from agentic_trader.research.pooled.insider_fields import (
    INSIDER_FIELDS,
    LoadedInsiderFilings,
    build_insider_frames,
    load_insider_filings,
    verify_insider_sources,
)
from tests.research.insider_fixtures import trade, trades_archive
from tests.research.pooled.test_formula import daily, weekdays


DAYS = weekdays(date(2017, 3, 1), 300)  # 2017-03-01 .. 2018-04-24
INPUTS = InsiderInputs.model_validate(
    {
        "fields_version": "insider_fields_v1",
        "insider_filings": {
            "source": "sec-form345-datasets",
            "files_sha256": "a" * 64,
            "quarters_from": "2017q1",
            "quarters_through": "2018q2",
            "buy_window": 63,
            "net_window": 126,
            "owner_roles": ["Officer", "Director"],
            "exclude_10b5_1": True,
            "max_trade_rel": 10,
        },
        "corporate_actions": {
            "source": "alpaca",
            "content_sha256": "b" * 64,
            "window": ["2017-01-01", "2018-12-31"],
            "trailing_sessions": 252,
        },
    }
)
NO_DROPS = dict.fromkeys(DROP_RULES, 0)


def _tx(symbol: str, filing: date, code: str, dollars: float, owner: int = 7, trans: date | None = None):
    return InsiderTransaction(
        symbol=symbol, filing_date=filing, trans_date=trans or filing, code=code, dollars=dollars, owner_cik=owner
    )


def _loaded(txs, *, unscored_rows=0, without_cik=()) -> LoadedInsiderFilings:
    by_symbol: dict[str, list[InsiderTransaction]] = {}
    for tx in txs:
        by_symbol.setdefault(tx.symbol, []).append(tx)
    return LoadedInsiderFilings(
        by_symbol={symbol: tuple(rows) for symbol, rows in by_symbol.items()},
        files_sha256="a" * 64,
        quarters=("2017q1",),
        rows=len(txs),
        unscored_rows=unscored_rows,
        drops={**NO_DROPS, "code": 3},
        symbols_without_cik=tuple(without_cik),
    )


def _raw(symbols, closes=None):
    """Raw bars with Close x Volume = 1e6 (Volume 1e6), so DV_T is 1e6 from index 19."""
    return {symbol: daily(DAYS, closes if closes is not None else [1.0] * len(DAYS)) for symbol in symbols}


def _build(txs, symbols=("AAA",), actions=(), raw=None, **loaded):
    raw = _raw(symbols) if raw is None else raw
    return build_insider_frames(INPUTS, _loaded(txs, **loaded), actions, raw, DAYS, list(symbols))


def _frame(txs, symbol="AAA", **kwargs) -> pd.DataFrame:
    return _build(txs, symbols=(symbol,), **kwargs).frames[symbol]


def _rename(old: str, new: str, on: date) -> CorporateAction:
    return CorporateAction(
        id=f"{old}-{new}", kind="name_change", symbol=old, related_symbol=new, effective_on=on, process_date=on
    )


def test_a_filing_counts_on_its_filing_session_and_not_before():
    f = _frame([_tx("AAA", DAYS[30], "P", 50_000.0)])
    assert list(f.columns) == list(INSIDER_FIELDS)
    assert f.iloc[:30].isna().all().all()  # before coverage
    buy = f["insider_buy_rel"]
    assert buy.iloc[30] == pytest.approx(0.05) and buy.iloc[92] == pytest.approx(0.05)  # (T-63, T]
    assert buy.iloc[93] == 0.0 and (buy.iloc[93:] == 0.0).all()


def test_a_filing_dated_on_a_non_session_counts_from_the_next_session():
    monday = next(i for i in range(40, 60) if DAYS[i].weekday() == 0)
    saturday = DAYS[monday] - timedelta(days=2)
    f = _frame([_tx("AAA", saturday, "P", 50_000.0)])
    assert np.isnan(f["insider_buy_rel"].iloc[monday - 1])
    assert f["insider_buy_rel"].iloc[monday] == pytest.approx(0.05)
    assert f["insider_buy_age"].iloc[monday] == 0.0
    late = _frame([_tx("AAA", DAYS[-1] + timedelta(days=1), "P", 50_000.0)])
    assert late.isna().all().all()  # filed after the last session: never visible


def test_net_ratio_is_nan_without_activity_and_plus_or_minus_one_with_one_side():
    f = _frame([_tx("AAA", DAYS[10], "P", 30_000.0), _tx("AAA", DAYS[20], "S", 10_000.0, owner=8)])
    net = f["insider_net_ratio"]
    assert np.isnan(net.iloc[9])
    assert net.iloc[10] == 1.0 and net.iloc[19] == 1.0
    assert net.iloc[20] == pytest.approx(0.5)  # (30k - 10k) / 40k
    assert net.iloc[135] == pytest.approx(0.5) and net.iloc[136] == -1.0  # the purchase leaves (T-126, T]
    assert net.iloc[145] == -1.0 and np.isnan(net.iloc[146]) and net.iloc[146:].isna().all()


def test_sell_rel_and_buy_rel_are_zero_once_covered_and_nan_before():
    f = _frame([_tx("AAA", DAYS[40], "S", 20_000.0)])
    buy, sell = f["insider_buy_rel"], f["insider_sell_rel"]
    assert buy.iloc[:40].isna().all() and sell.iloc[:40].isna().all()
    assert buy.iloc[40] == 0.0 and (buy.iloc[40:] == 0.0).all()
    assert sell.iloc[40] == pytest.approx(0.02) and sell.iloc[102] == pytest.approx(0.02)
    assert (sell.iloc[103:] == 0.0).all()
    assert (f["insider_buyers"].iloc[40:] == 0.0).all() and (f["insider_opp_buy_rel"].iloc[40:] == 0.0).all()
    assert f["insider_buy_age"].isna().all()  # a sale is not a purchase


def test_zero_windows_are_exact_zeros_after_many_uneven_amounts():
    amounts = [1234.567, 0.1, 98765.4321, 0.3, 77.7]
    txs = [_tx("AAA", DAYS[30 + i], "P", amount) for i, amount in enumerate(amounts)]
    txs += [_tx("AAA", DAYS[30 + i], "S", amount * 3.3) for i, amount in enumerate(amounts)]
    f = _frame(txs)
    for name in ("insider_buy_rel", "insider_sell_rel", "insider_opp_buy_rel"):
        assert (f[name].iloc[34 + 63 :] == 0.0).all(), name  # not a 1e-17 residue a formula would rank as > 0
    assert f["insider_net_ratio"].iloc[34 + 126 :].isna().all()


def test_buyers_counts_distinct_owners_across_filings():
    txs = [
        _tx("AAA", DAYS[10], "P", 1000.0, owner=7),
        _tx("AAA", DAYS[20], "P", 1000.0, owner=8),
        _tx("AAA", DAYS[30], "P", 1000.0, owner=7),
        _tx("AAA", DAYS[30], "P", 1000.0, owner=7),  # a second same-day filing by the same owner
        _tx("AAA", DAYS[25], "S", 1000.0, owner=9),  # a seller is not a buyer
    ]
    buyers = _frame(txs)["insider_buyers"]
    assert np.isnan(buyers.iloc[9]) and buyers.iloc[10] == 1.0 and buyers.iloc[20] == 2.0
    assert buyers.iloc[30] == 2.0 and buyers.iloc[72] == 2.0 and buyers.iloc[73] == 2.0
    assert buyers.iloc[82] == 2.0 and buyers.iloc[83] == 1.0  # owner 8's purchase leaves the window
    assert buyers.iloc[92] == 1.0 and buyers.iloc[93] == 0.0 and (buyers.iloc[93:] == 0.0).all()
    assert _frame(txs)["insider_buy_rel"].iloc[30] == pytest.approx(4000.0 / 1e6)


def test_buy_age_counts_sessions_since_the_last_purchase_filing():
    txs = [
        _tx("AAA", DAYS[10], "P", 1000.0),
        _tx("AAA", DAYS[20], "P", 1000.0, owner=8),
        _tx("AAA", DAYS[25], "S", 1000.0),
    ]
    age = _frame(txs)["insider_buy_age"]
    assert np.isnan(age.iloc[9]) and age.iloc[10] == 0.0 and age.iloc[15] == 5.0
    assert age.iloc[19] == 9.0 and age.iloc[20] == 0.0
    assert age.iloc[25] == 5.0 and age.iloc[299] == 279.0  # sales do not reset it; no window


def test_routine_purchases_leave_the_opportunistic_field():
    march_2017, march_2018, april_2018 = (DAYS.index(d) for d in (date(2017, 3, 6), date(2018, 3, 5), date(2018, 4, 9)))
    txs = [
        _tx("AAA", DAYS[march_2017], "P", 10_000.0, owner=7),
        _tx("AAA", DAYS[march_2018], "P", 20_000.0, owner=7),  # same owner, same month a year later: routine
        _tx("AAA", DAYS[march_2018 + 1], "P", 40_000.0, owner=8),  # another owner: opportunistic
        _tx("AAA", DAYS[april_2018], "P", 30_000.0, owner=7),  # owner 7 bought nothing in April 2017
    ]
    f = _frame(txs)
    opp, buy = f["insider_opp_buy_rel"], f["insider_buy_rel"]
    assert opp.iloc[20] == pytest.approx(0.01)  # nothing a year earlier (DV_T exists from index 19)
    assert buy.iloc[march_2018] == pytest.approx(0.02) and opp.iloc[march_2018] == 0.0
    assert opp.iloc[march_2018 + 1] == pytest.approx(0.04) and buy.iloc[march_2018 + 1] == pytest.approx(0.06)
    assert opp.iloc[april_2018] == pytest.approx(0.07) and buy.iloc[april_2018] == pytest.approx(0.09)


def test_filings_before_the_calendar_cover_and_classify_but_leave_incomplete_windows_nan():
    before = DAYS[0] - timedelta(days=5)  # 2017-02-24: no session position on this calendar
    feb_2018 = DAYS.index(date(2018, 2, 26))
    built = _build([_tx("AAA", before, "P", 10_000.0, owner=7), _tx("AAA", DAYS[feb_2018], "P", 20_000.0, owner=7)])
    f = built.frames["AAA"]
    assert built.coverage.coverage_start["AAA"] == DAYS[0].isoformat()
    for name in ("insider_buy_rel", "insider_buyers", "insider_opp_buy_rel"):
        # (T-63, T] reaches before the calendar for T < 62, where the earlier filing could lie.
        assert f[name].iloc[19:62].isna().all() and f[name].iloc[62] == 0.0, name
    assert f["insider_sell_rel"].iloc[19] == 0.0  # no sale precedes the calendar
    assert f["insider_net_ratio"].iloc[:feb_2018].isna().all()
    assert f["insider_buy_age"].iloc[:feb_2018].isna().all() and f["insider_buy_age"].iloc[feb_2018] == 0.0
    assert f["insider_buy_rel"].iloc[feb_2018] == pytest.approx(0.02)
    assert f["insider_opp_buy_rel"].iloc[feb_2018] == 0.0  # February 2017's purchase makes it routine


def test_never_filing_issuer_is_nan_not_zero():
    built = _build([_tx("AAA", DAYS[30], "P", 1000.0)], symbols=("AAA", "BBB"), without_cik=("BBB",))
    assert built.frames["BBB"].isna().all().all()
    assert built.coverage.symbols_without_rows == ("BBB",)
    assert built.coverage.symbols_without_cik == ("BBB",)
    assert built.coverage.coverage_start == {"AAA": DAYS[30].isoformat(), "BBB": None}
    assert built.coverage.first_finite["BBB"] == dict.fromkeys(INSIDER_FIELDS)


def test_dollar_volume_denominator_is_the_20_session_median_and_nan_without_bars():
    closes = [1.0] * 100 + [2.0] * 200  # Close x Volume doubles from index 100
    txs = [_tx("AAA", DAYS[5], "P", 50_000.0), _tx("AAA", DAYS[95], "P", 50_000.0)]
    buy = _frame(txs, raw=_raw(["AAA"], closes))["insider_buy_rel"]
    assert buy.iloc[5:19].isna().all()  # covered, but fewer than 20 sessions of bars
    assert buy.iloc[19] == pytest.approx(0.05)
    assert buy.iloc[99] == pytest.approx(0.05)
    assert buy.iloc[109] == pytest.approx(50_000.0 / 1.5e6)  # ten old and ten new values
    assert buy.iloc[110] == pytest.approx(0.025) and buy.iloc[120] == pytest.approx(0.025)  # halved
    raw = _raw(["AAA"])
    raw["AAA"].loc[raw["AAA"].index[50], "Volume"] = np.nan  # a missing bar has no DV that session
    gap = _frame(txs, raw=raw)["insider_buy_rel"]
    assert np.isnan(gap.iloc[50]) and gap.iloc[51] == pytest.approx(0.05)
    bare = _frame(txs, raw={})
    assert bare["insider_buy_rel"].isna().all() and bare["insider_opp_buy_rel"].isna().all()
    assert bare["insider_buyers"].iloc[5] == 1.0 and bare["insider_buy_age"].iloc[5] == 0.0


def test_an_implausibly_large_trade_is_dropped_before_any_field_is_computed():
    txs = [
        _tx("AAA", DAYS[20], "S", 10_000.0, owner=9),  # covers AAA
        _tx("AAA", DAYS[30], "P", 50_000.0, owner=7),
        _tx("AAA", DAYS[40], "P", 50_000_000.0, owner=8),  # 50 x DV 1e6 > max_trade_rel 10: dropped
        _tx("BBB", DAYS[40], "P", 5_000_000.0, owner=8),  # 5 x DV: kept
        _tx("CCC", DAYS[5], "P", 50_000_000.0, owner=8),  # filed before DV is finite (index 19): kept
    ]
    built = _build(txs, symbols=("AAA", "BBB", "CCC"))
    a, b, c = (built.frames[symbol] for symbol in ("AAA", "BBB", "CCC"))
    assert a["insider_buy_rel"].iloc[40] == pytest.approx(0.05) and a["insider_buyers"].iloc[40] == 1.0
    assert a["insider_buy_age"].iloc[40] == 10.0 and a["insider_net_ratio"].iloc[40] == pytest.approx(
        2 / 3
    )  # (50k - 10k) / 60k
    assert (a["insider_buy_rel"].iloc[93:] == 0.0).all() and (a["insider_buyers"].iloc[93:] == 0.0).all()
    assert a["insider_buy_age"].iloc[299] == 269.0  # still counted from the purchase on index 30
    assert b["insider_buy_rel"].iloc[40] == pytest.approx(5.0) and b["insider_buyers"].iloc[40] == 1.0
    assert b["insider_buy_age"].iloc[40] == 0.0
    assert c["insider_buyers"].iloc[5] == 1.0 and c["insider_buy_age"].iloc[5] == 0.0
    assert c["insider_buy_rel"].iloc[19] == pytest.approx(50.0)
    assert built.coverage.drops == {**NO_DROPS, "code": 3, "implausible_size": 1}
    alone = _build([_tx("AAA", DAYS[40], "P", 50_000_000.0)])
    assert alone.frames["AAA"].isna().all().all()  # nothing left to cover the symbol
    assert alone.coverage.coverage_start == {"AAA": None}


def test_identity_break_blanks_fields_before_the_rename():
    txs = [_tx("META", DAYS[100], "P", 50_000.0), _tx("META", DAYS[120], "S", 30_000.0, owner=8)]
    built = _build(txs, symbols=("META",), actions=[_rename("FB", "META", DAYS[150])])
    f = built.frames["META"]
    assert f.iloc[:150].isna().all().all()
    assert np.isfinite(f.iloc[150]).all()
    assert f["insider_buy_rel"].iloc[150] == pytest.approx(0.05)  # the pre-break filing still counts: rows kept
    assert f["insider_buy_age"].iloc[150] == 50.0
    assert built.coverage.identity_breaks == {"META": DAYS[150].isoformat()}
    assert built.coverage.first_finite["META"]["insider_buy_rel"] == DAYS[150].isoformat()


def test_coverage_reports_drops_unscored_rows_and_first_finite():
    txs = [_tx("AAA", DAYS[30], "S", 1000.0), _tx("AAA", DAYS[40], "P", 1000.0), _tx("ZZZ", DAYS[30], "P", 1.0)]
    built = _build(txs, unscored_rows=5)
    coverage = built.coverage
    assert coverage.unscored_rows == 5 + 1  # the loader's plus the loaded rows of a name not scored
    assert coverage.drops == {**NO_DROPS, "code": 3, "implausible_size": 0}  # the reader's merged with the builder's
    assert coverage.coverage_start == {"AAA": DAYS[30].isoformat()}
    assert coverage.first_finite["AAA"] == {
        "insider_net_ratio": DAYS[30].isoformat(),
        "insider_buy_rel": DAYS[30].isoformat(),
        "insider_sell_rel": DAYS[30].isoformat(),
        "insider_buyers": DAYS[30].isoformat(),
        "insider_buy_age": DAYS[40].isoformat(),
        "insider_opp_buy_rel": DAYS[30].isoformat(),
    }
    assert set(built.frames) == {"AAA"}


def test_frames_align_with_prepare_daily_index():
    built = _build([_tx("AAA", DAYS[30], "P", 1000.0)])
    expected = pd.DatetimeIndex([pd.Timestamp(day) for day in DAYS])
    assert built.frames["AAA"].index.equals(expected)


NOW = datetime(2026, 10, 20, tzinfo=UTC)
TICKERS = {
    "0": {"cik_str": 1000, "ticker": "AAA", "title": "A"},
    "1": {"cik_str": 2000, "ticker": "GOOG", "title": "Alphabet"},
    "2": {"cik_str": 2000, "ticker": "GOOGL", "title": "Alphabet"},
}
FILINGS = INPUTS.insider_filings.model_copy(update={"quarters_from": "2025q1", "quarters_through": "2025q2"})
QUARTERS = quarters_between(parse_quarter("2025q1"), parse_quarter("2025q2"))


def _store(tmp_path, *, quarters=QUARTERS, tickers=True) -> InsiderFilingStore:
    store = InsiderFilingStore(tmp_path / "insider")
    bodies = {
        "2025q1": [trade(), trade(cik=2000, code="S", ad="D"), trade(cik=3000), trade(code="M")],
        "2025q2": [trade(filing="02-APR-2025", trans="01-APR-2025", owner=8), trade(doc="4/A")],
    }
    for quarter in quarters:
        store.save(quarter, trades_archive(bodies[str(quarter)]), url="u", requested_at=NOW, received_at=NOW)
    if tickers:
        store.save_tickers(json.dumps(TICKERS).encode(), url="t", requested_at=NOW, received_at=NOW)
    return store


def test_load_insider_filings_is_offline_and_digests(tmp_path):
    store = _store(tmp_path)
    before = sorted(store.directory.iterdir())
    loaded = load_insider_filings(store.directory, FILINGS, ["AAA", "GOOG", "GOOGL", "NOCIK"])
    assert sorted(store.directory.iterdir()) == before  # nothing written, nothing fetched
    assert loaded.files_sha256 == files_sha256(store, QUARTERS)
    assert loaded.quarters == ("2025q1", "2025q2")
    assert [(t.code, t.owner_cik, t.filing_date) for t in loaded.by_symbol["AAA"]] == [
        ("P", 7, date(2025, 3, 5)),
        ("P", 8, date(2025, 4, 2)),
    ]
    assert [t.code for t in loaded.by_symbol["GOOG"]] == ["S"] and [t.code for t in loaded.by_symbol["GOOGL"]] == ["S"]
    assert loaded.rows == 4 and loaded.unscored_rows == 1  # CIK 3000 is not in the cohort
    assert loaded.drops == {**NO_DROPS, "code": 1, "document_type": 1}
    assert loaded.symbols_without_cik == ("NOCIK",)


def test_load_insider_filings_fails_closed_on_a_missing_quarter_or_ticker_map(tmp_path):
    only_q1 = _store(tmp_path / "a", quarters=QUARTERS[:1])
    with pytest.raises(ValueError, match="insider archive for 2025q2 is missing"):
        load_insider_filings(only_q1.directory, FILINGS, ["AAA"])
    no_map = _store(tmp_path / "b", tickers=False)
    with pytest.raises(ValueError, match="ticker map"):
        load_insider_filings(no_map.directory, FILINGS, ["AAA"])
    tampered = _store(tmp_path / "c")
    tampered.paths(QUARTERS[1])[0].write_bytes(b"tampered")
    with pytest.raises(ValueError, match="SHA-256"):
        load_insider_filings(tampered.directory, FILINGS, ["AAA"])


def test_verify_insider_sources_names_the_mismatching_digest():
    verify_insider_sources(INPUTS, _loaded([]), "b" * 64)
    stale = LoadedInsiderFilings(
        by_symbol={},
        files_sha256="c" * 64,
        quarters=(),
        rows=0,
        unscored_rows=0,
        drops=NO_DROPS,
        symbols_without_cik=(),
    )
    with pytest.raises(ValueError, match=r"insider_inputs\.insider_filings\.files_sha256"):
        verify_insider_sources(INPUTS, stale, "b" * 64)
    with pytest.raises(ValueError, match=r"insider_inputs\.corporate_actions\.content_sha256"):
        verify_insider_sources(INPUTS, _loaded([]), "d" * 64)


def test_insider_fields_are_dimensionless_dsl_fields_that_fail_closed_when_absent():
    for name in INSIDER_FIELDS:
        assert FIELD_UNITS[name] == (0.0, 0.0)
        require_dimensionless(name)
    expression = "insider_buy_rel / (insider_buyers > 1)"
    require_dimensionless(expression)
    assert compile_expression(expression).required_fields == {"insider_buy_rel", "insider_buyers"}
    index = pd.DatetimeIndex([pd.Timestamp(day) for day in DAYS[:8]])
    bars = {"AAA": pd.DataFrame({"Close": np.linspace(10, 11, 8), "Volume": 1e6}, index=index)}
    with pytest.raises(ValueError, match="Missing required fields"):
        evaluate_prepared(expression, bars, DAYS[:8], DAYS[1:8], ["AAA"])
