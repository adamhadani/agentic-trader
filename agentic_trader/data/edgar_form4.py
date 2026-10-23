"""Pure live SEC Form 4 parsing: the daily form index and full-submission ``.txt`` documents.

``parse_form4`` applies the rules of ``insider_filings.read_transactions`` in the same order
(``DROP_RULES``) to the ``<XML>`` block of a submission, so a live filing yields the rows the quarterly
data sets later hold. The filing date is supplied by the caller (the daily index), never read from the XML.
No I/O happens here.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from agentic_trader.research.insider_filings import DROP_RULES, InsiderTransaction


__all__ = ["IndexRow", "parse_daily_index", "parse_form4", "select_form4"]

_INDEX_ROW = re.compile(r"^(.{1,16}?)\s{2,}(.+?)\s+(\d+)\s+(\d{8})\s+(\S+)\s*$")
_XML_BLOCK = re.compile(r"<XML>(.*?)</XML>", re.DOTALL | re.IGNORECASE)
_FORBIDDEN = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)
_HEADER_OWNER = re.compile(r"CENTRAL INDEX KEY:\s*(\d+)")
_SIDES = {"P": "A", "S": "D"}
_PLAN_FLAGS = {"1": True, "true": True, "0": False, "false": False, "": False}
# Data-set RPTOWNER_RELATIONSHIP names, as the XML booleans that carry them.
_ROLE_FLAGS = {
    "director": "isDirector",
    "officer": "isOfficer",
    "tenpercentowner": "isTenPercentOwner",
    "other": "isOther",
}


@dataclass(frozen=True)
class IndexRow:
    """One row of an EDGAR daily form index."""

    form: str
    company: str
    cik: int
    date_filed: date
    path: str


def parse_daily_index(text: str) -> list[IndexRow]:
    """The data rows of a ``form.YYYYMMDD.idx`` file; header and separator lines are skipped."""
    rows: list[IndexRow] = []
    for line in text.splitlines():
        match = _INDEX_ROW.match(line)
        if match is None:
            continue
        form, company, cik, filed, path = match.groups()
        rows.append(
            IndexRow(
                form=form.strip(),
                company=company.strip(),
                cik=int(cik),
                date_filed=date(int(filed[:4]), int(filed[4:6]), int(filed[6:])),
                path=path,
            )
        )
    return rows


def select_form4(rows: Sequence[IndexRow], ciks: Collection[int]) -> list[IndexRow]:
    """Rows whose form is exactly ``4`` (amendments excluded) and whose CIK is in ``ciks``."""
    return [row for row in rows if row.form == "4" and row.cik in ciks]


def _text(node: ET.Element | None, path: str) -> str:
    found = None if node is None else node.find(path)
    return "" if found is None or found.text is None else found.text.strip()


def _positive(text: str) -> float | None:
    """``text`` rounded half-up to two places as the data sets store it, when finite and positive.

    The quarterly data sets hold ``TRANS_SHARES`` and ``TRANS_PRICEPERSHARE`` to cents (a 112.1215 price is
    112.12 there), so the live path rounds the same way to yield the same dollars.
    """
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None
    cents = float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return cents if math.isfinite(cents) and cents > 0 else None


def _flag(text: str) -> bool:
    return text.strip().lower() in ("1", "true")


def _parse_xml(document: str) -> ET.Element:
    match = _XML_BLOCK.search(document)
    if match is None:
        raise ValueError("Form 4 submission has no <XML> block")
    body = match.group(1).strip()
    if _FORBIDDEN.search(body):
        raise ValueError("Form 4 XML declares a DOCTYPE or ENTITY")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ValueError(f"Form 4 XML is malformed ({type(exc).__name__})") from exc
    if root.tag != "ownershipDocument":
        raise ValueError(f"Form 4 XML root is {root.tag!r}, not ownershipDocument")
    return root


def _header_owner_order(document: str) -> dict[int, int]:
    """Each reporting owner's CIK -> its position among the SEC header's ``REPORTING-OWNER`` blocks."""
    header = document.split("<DOCUMENT>", 1)[0]
    order: dict[int, int] = {}
    for block in header.split("REPORTING-OWNER:")[1:]:
        found = _HEADER_OWNER.search(block.split("\nISSUER:", 1)[0])
        if found is not None:
            order.setdefault(int(found.group(1)), len(order))
    return order


def _first_owner_cik(root: ET.Element, roles: Sequence[str], header_order: Mapping[int, int]) -> int | None:
    """The CIK of the first reporting owner whose relationship lists one of ``roles``.

    "First" follows the SEC header's owner order, which is the order the data sets list owners in (the
    XML order differs on multi-owner filings); owners absent from the header follow in XML order.
    """
    flags = [_ROLE_FLAGS[key] for role in roles if (key := role.strip().lower().replace(" ", "")) in _ROLE_FLAGS]
    qualifying: list[int] = []
    for owner in root.findall("reportingOwner"):
        relationship = owner.find("reportingOwnerRelationship")
        if any(_flag(_text(relationship, flag)) for flag in flags):
            qualifying.append(int(_text(owner, "reportingOwnerId/rptOwnerCik")))
    return min(qualifying, key=lambda cik: header_order.get(cik, len(header_order)), default=None)


def parse_form4(
    document: str,
    *,
    filing_date: date,
    cik_to_symbols: Mapping[int, tuple[str, ...]],
    owner_roles: Sequence[str],
    exclude_10b5_1: bool = False,
) -> tuple[list[InsiderTransaction], dict[str, int]]:
    """The qualifying trades of one Form 4 submission and its per-rule drop counts.

    Each non-derivative row is tested against ``DROP_RULES`` in order and counted under the first it
    fails, as ``read_transactions`` does; a qualifying row whose issuer is unmapped yields nothing, and a
    mapped issuer with several symbols yields one transaction per symbol. ``filing_date`` is the index
    date. A malformed document raises ``ValueError``.
    """
    root = _parse_xml(document)
    drops = dict.fromkeys(DROP_RULES, 0)
    doc_type = _text(root, "documentType")
    flag = _text(root, "aff10b5One").lower()
    if flag not in _PLAN_FLAGS:
        raise ValueError(f"Form 4 aff10b5One {flag!r} is not 1, true, 0, false or blank")
    plan = _PLAN_FLAGS[flag]
    owner = _first_owner_cik(root, owner_roles, _header_owner_order(document))
    issuer = _text(root, "issuer/issuerCik")
    symbols = cik_to_symbols.get(int(issuer)) if issuer.isdigit() else None
    out: list[InsiderTransaction] = []
    for row in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = _text(row, "transactionCoding/transactionCode")
        side = _text(row, "transactionAmounts/transactionAcquiredDisposedCode/value")
        rule: str | None = None
        if doc_type != "4":
            rule = "document_type"
        elif code not in _SIDES:
            rule = "code"
        elif side != _SIDES[code]:
            rule = "acquired_disposed"
        elif owner is None:
            rule = "owner_role"
        elif exclude_10b5_1 and plan:
            rule = "plan_10b5_1"
        if rule is not None:
            drops[rule] += 1
            continue
        shares = _positive(_text(row, "transactionAmounts/transactionShares/value"))
        price = _positive(_text(row, "transactionAmounts/transactionPricePerShare/value"))
        if shares is None or price is None:
            drops["non_positive"] += 1
            continue
        try:
            traded = date.fromisoformat(_text(row, "transactionDate/value"))
        except ValueError as exc:
            raise ValueError("Form 4 transactionDate is not an ISO date") from exc
        if traded > filing_date:
            drops["trade_after_filing"] += 1
            continue
        if symbols is None or owner is None:
            continue
        out.extend(
            InsiderTransaction(
                symbol=symbol,
                filing_date=filing_date,
                trans_date=traded,
                code="P" if code == "P" else "S",
                dollars=shares * price,
                owner_cik=owner,
            )
            for symbol in symbols
        )
    return out, drops
