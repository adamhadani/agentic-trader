"""The live Form 4 parser must reproduce the data-set reader's rows (fixtures: tests/fixtures/edgar)."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest

from agentic_trader.data.edgar_form4 import IndexRow, parse_daily_index, parse_form4, select_form4
from agentic_trader.research.insider_filings import DROP_RULES


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "edgar"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text())
ROLES = ("Officer", "Director")
MONTHS = {
    m: i for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)
}


def _filed(text: str) -> date:
    day, month, year = text.split("-")
    return date(int(year), MONTHS[month], int(day))


def _symbol_map() -> dict[int, tuple[str, ...]]:
    """Issuer CIK -> symbols from expected.json; filings without rows map to a placeholder so they are attempted."""
    mapping: dict[int, tuple[str, ...]] = {}
    for entry in EXPECTED.values():
        symbols = tuple(dict.fromkeys(row["symbol"] for row in entry["transactions"]))
        mapping[entry["issuer_cik"]] = symbols or mapping.get(entry["issuer_cik"], (f"PH{entry['issuer_cik']}",))
    return mapping


def _owner(cik: int, *, officer: str = "0", director: str = "0") -> str:
    return (
        f"<reportingOwner><reportingOwnerId><rptOwnerCik>{cik:010d}</rptOwnerCik></reportingOwnerId>"
        f"<reportingOwnerRelationship><isDirector>{director}</isDirector><isOfficer>{officer}</isOfficer>"
        "<isTenPercentOwner>0</isTenPercentOwner><isOther>0</isOther></reportingOwnerRelationship></reportingOwner>"
    )


def _row(
    *, code: str = "P", side: str = "A", shares: str | None = "100", price: str | None = "10", day: str = "2026-09-14"
) -> str:
    amounts = f"<transactionShares><value>{shares}</value></transactionShares>" if shares is not None else ""
    if price is not None:
        amounts += f"<transactionPricePerShare><value>{price}</value></transactionPricePerShare>"
    return (
        f"<nonDerivativeTransaction><transactionDate><value>{day}</value></transactionDate>"
        f"<transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>"
        f"<transactionAmounts>{amounts}<transactionAcquiredDisposedCode><value>{side}</value>"
        "</transactionAcquiredDisposedCode></transactionAmounts></nonDerivativeTransaction>"
    )


def _doc(*, owners: str | None = None, rows: str = "", doc_type: str = "4", plan: str = "0", issuer: int = 5) -> str:
    owners = _owner(77, officer="1") if owners is None else owners
    xml = (
        '<?xml version="1.0"?><ownershipDocument>'
        f"<documentType>{doc_type}</documentType><issuer><issuerCik>{issuer:010d}</issuerCik></issuer>"
        f"{owners}<aff10b5One>{plan}</aff10b5One><nonDerivativeTable>{rows}</nonDerivativeTable>"
        "<derivativeTable><derivativeTransaction><transactionCoding><transactionCode>P</transactionCode>"
        "</transactionCoding></derivativeTransaction></derivativeTable></ownershipDocument>"
    )
    return f"<SEC-DOCUMENT>\n<DOCUMENT>\n<TEXT>\n<XML>\n{xml}\n</XML>\n</TEXT>\n</DOCUMENT>\n</SEC-DOCUMENT>\n"


def _parse(document: str, symbols: dict[int, tuple[str, ...]] | None = None):
    return parse_form4(
        document,
        filing_date=date(2026, 9, 15),
        cik_to_symbols=symbols if symbols is not None else {5: ("AAA",)},
        owner_roles=ROLES,
    )


def test_index_parse_and_form4_selection():
    rows = parse_daily_index((FIXTURES / "form.20260915.idx").read_text())
    assert rows
    assert all(isinstance(r, IndexRow) and r.date_filed == date(2026, 9, 15) for r in rows)
    assert {"4", "4/A", "10-K", "8-K"} <= {r.form for r in rows}
    first = rows[0]
    assert (first.form, first.company, first.cik) == ("4", "1 800 FLOWERS COM INC", 1084869)
    assert first.path == "edgar/data/1084869/0001437749-26-030411.txt"
    picked = select_form4(rows, {1084869})
    assert picked and all(r.form == "4" and r.cik == 1084869 for r in picked)
    amendments = [r for r in rows if r.form == "4/A"]
    assert amendments
    assert not set(select_form4(rows, {r.cik for r in rows})) & set(amendments)


@pytest.mark.parametrize("accession", sorted(EXPECTED))
def test_parse_matches_data_set_rows(accession):
    entry = EXPECTED[accession]
    text = (FIXTURES / f"{accession}.txt").read_text()
    got, drops = parse_form4(
        text, filing_date=_filed(entry["filing_date"]), cik_to_symbols=_symbol_map(), owner_roles=ROLES
    )
    assert set(drops) == set(DROP_RULES)
    assert {k: v for k, v in drops.items() if v} == entry["drops"]
    assert len(got) == len(entry["transactions"])
    # The data sets list a filing's rows in an unrelated order, so compare as sorted multisets.
    ours = sorted(
        (t.symbol, t.code, t.owner_cik, t.filing_date.isoformat(), t.trans_date.isoformat(), t.dollars) for t in got
    )
    want = sorted(
        (w["symbol"], w["code"], w["owner_cik"], w["filing_date"], w["trans_date"], w["dollars"])
        for w in entry["transactions"]
    )
    for mine, theirs in zip(ours, want, strict=True):
        assert mine[:5] == theirs[:5]
        assert mine[5] == pytest.approx(theirs[5], abs=0.01)


def test_drop_rules_match_reader():
    rows = "".join(
        (
            _row(),  # kept
            _row(code="S", side="D"),  # kept sale
            _row(code="A"),  # code
            _row(side="D"),  # acquired_disposed
            _row(price="0"),  # non_positive
            _row(price=None),  # non_positive (footnote-only price)
            _row(day="2026-09-16"),  # trade_after_filing
        )
    )
    got, drops = _parse(_doc(rows=rows))
    assert [(t.code, t.dollars) for t in got] == [("P", 1000.0), ("S", 1000.0)]
    assert drops == {
        "document_type": 0,
        "code": 1,
        "acquired_disposed": 1,
        "owner_role": 0,
        "plan_10b5_1": 0,
        "non_positive": 2,
        "trade_after_filing": 1,
    }
    # derivative rows never count
    assert sum(drops.values()) == 5

    got, drops = _parse(_doc(owners=_owner(77), rows=_row()))
    assert got == [] and drops["owner_role"] == 1

    got, drops = _parse(_doc(owners=_owner(77, director="true"), rows=_row()))
    assert len(got) == 1 and got[0].owner_cik == 77


def test_rule_order_first_failing_rule_wins():
    got, drops = _parse(_doc(doc_type="4/A", owners=_owner(77), rows=_row(code="A", price="0")))
    assert got == [] and drops["document_type"] == 1 and sum(drops.values()) == 1


def test_plan_flag_kept_by_default_and_excludable():
    document = _doc(rows=_row(), plan="true")
    got, drops = _parse(document)
    assert len(got) == 1 and drops["plan_10b5_1"] == 0
    got, drops = parse_form4(
        document,
        filing_date=date(2026, 9, 15),
        cik_to_symbols={5: ("AAA",)},
        owner_roles=ROLES,
        exclude_10b5_1=True,
    )
    assert got == [] and drops["plan_10b5_1"] == 1
    with pytest.raises(ValueError, match="aff10b5One"):
        _parse(_doc(rows=_row(), plan="maybe"))


def test_multiple_owners_takes_first_qualifying():
    owners = _owner(1) + _owner(2, officer="1") + _owner(3, director="1")
    got, _ = _parse(_doc(owners=owners, rows=_row()))
    assert [t.owner_cik for t in got] == [2]


def test_header_owner_order_picks_first_qualifying_owner():
    document = _doc(owners=_owner(1, officer="1") + _owner(2, director="1"), rows=_row())
    header = "REPORTING-OWNER:\n\t\tCENTRAL INDEX KEY:\t0000000002\nREPORTING-OWNER:\n\t\tCENTRAL INDEX KEY:\t0000000001\nISSUER:\n\t\tCENTRAL INDEX KEY:\t0000000005\n<DOCUMENT>"
    got, _ = _parse(header + document)
    assert [t.owner_cik for t in got] == [2]


def test_price_and_shares_round_to_cents_like_the_data_sets():
    got, _ = _parse(_doc(rows=_row(shares="100", price="267.1850")))
    assert got[0].dollars == pytest.approx(26719.0)


def test_share_class_fan_out():
    got, _ = _parse(_doc(rows=_row()), {5: ("AAA.A", "AAA.B")})
    assert [(t.symbol, t.dollars) for t in got] == [("AAA.A", 1000.0), ("AAA.B", 1000.0)]


def test_unmapped_issuer_yields_nothing():
    got, drops = _parse(_doc(rows=_row()), {9: ("ZZZ",)})
    assert got == [] and sum(drops.values()) == 0


def test_amendment_document_type_dropped():
    got, drops = _parse(_doc(doc_type="4/A", rows=_row() + _row()))
    assert got == [] and drops["document_type"] == 2


@pytest.mark.parametrize(
    "document",
    [
        "no xml here",
        "<XML>\n<ownershipDocument><documentType>4\n</XML>",
        '<XML>\n<!DOCTYPE x [<!ENTITY a "b">]><ownershipDocument/>\n</XML>',
        "<XML>\n<ownershipDocument><!ENTITY a 'b'></ownershipDocument>\n</XML>",
        "<XML>\n<other/>\n</XML>",
    ],
)
def test_malformed_xml_raises_value_error(document):
    with pytest.raises(ValueError, match=re.compile("Form 4", re.IGNORECASE)):
        _parse(document)
