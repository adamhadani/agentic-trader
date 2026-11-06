"""Pure parsing and classification of EDGAR submissions for the filing veto (no I/O)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any


class FilingCategory(StrEnum):
    LATE_FILING = "late_filing"
    ADVERSE_8K = "adverse_8k"
    OFFERING = "offering"


ADVERSE_8K_ITEMS: frozenset[str] = frozenset({"2.04", "2.05", "2.06", "4.02", "5.01"})
LATE_FILING_FORMS: frozenset[str] = frozenset({"NT 10-K", "NT 10-Q", "NT 20-F", "NT 10-K/A", "NT 10-Q/A", "NT 20-F/A"})
OFFERING_FORMS: frozenset[str] = frozenset({"424B4", "424B5"})
_EIGHT_K_FORMS = frozenset({"8-K", "8-K/A"})
_COLUMNS = ("accessionNumber", "filingDate", "acceptanceDateTime", "form", "items")
# Read when present (an absent column reads as None): the primary document lets preview evidence tell a debt
# prospectus from an equity one, since ``424B4``/``424B5`` cover equity, convertible and debt offerings alike.
_OPTIONAL_COLUMNS = ("primaryDocument", "primaryDocDescription")


def normalize_ticker(ticker: object) -> str:
    """Upper-case with ``-`` read as ``.`` (``BF-B`` is ``BF.B``)."""
    return str(ticker).upper().replace("-", ".")


def classify(form: str, items: Sequence[str]) -> FilingCategory | None:
    """The veto category of a filing, or None. Items match as exact stripped tokens."""
    if form in LATE_FILING_FORMS:
        return FilingCategory.LATE_FILING
    if form in OFFERING_FORMS:
        return FilingCategory.OFFERING
    if form in _EIGHT_K_FORMS and any(item.strip() in ADVERSE_8K_ITEMS for item in items):
        return FilingCategory.ADVERSE_8K
    return None


@dataclass(frozen=True)
class MatchedFiling:
    symbol: str
    category: FilingCategory
    form: str
    items: tuple[str, ...]
    filing_date: date
    acceptance: str | None
    accession: str
    # EDGAR's ``primaryDocument`` and ``primaryDocDescription``; None when the column or value is absent.
    primary_document: str | None = None
    primary_doc_description: str | None = None

    def describe(self) -> str:
        if self.items and self.category is FilingCategory.ADVERSE_8K:
            return f"{self.form} Items {', '.join(self.items)}"
        return self.form

    def to_payload(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "category": self.category.value,
            "form": self.form,
            "items": list(self.items),
            "filing_date": self.filing_date.isoformat(),
            "acceptance": self.acceptance,
            "accession": self.accession,
            "primary_document": self.primary_document,
            "primary_doc_description": self.primary_doc_description,
        }

    @classmethod
    def from_payload(cls, d: Mapping[str, Any]) -> MatchedFiling:
        """The inverse of ``to_payload``; a payload journaled before the primary-document fields reads them as None."""
        return cls(
            symbol=str(d["symbol"]),
            category=FilingCategory(d["category"]),
            form=str(d["form"]),
            items=tuple(str(i) for i in d["items"]),
            filing_date=date.fromisoformat(str(d["filing_date"])),
            acceptance=_optional_text(d.get("acceptance")),
            accession=str(d["accession"]),
            primary_document=_optional_text(d.get("primary_document")),
            primary_doc_description=_optional_text(d.get("primary_doc_description")),
        )


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


def _non_empty(value: object) -> str | None:
    return str(value) if value else None


def parse_recent(payload: Mapping[str, Any], symbol: str, since: date) -> list[MatchedFiling]:
    """Veto-category filings in ``filings.recent`` dated ``since`` or later, newest first."""
    try:
        recent = payload["filings"]["recent"]
        columns = [list(recent[key]) for key in _COLUMNS]
        optional = [list(recent[key]) if key in recent else None for key in _OPTIONAL_COLUMNS]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"submissions payload lacks filings.recent column {exc}") from exc
    rows = len(columns[0])
    filled = [column if column is not None else [None] * rows for column in optional]
    if len({len(c) for c in (*columns, *filled)}) != 1:
        raise ValueError("submissions columns have mismatched lengths")
    out: list[MatchedFiling] = []
    for accession, filed, accepted, form, raw_items, document, description in zip(*columns, *filled, strict=True):
        filing_date = date.fromisoformat(str(filed))
        if filing_date < since:
            continue
        items = tuple(t.strip() for t in str(raw_items or "").split(",") if t.strip())
        category = classify(str(form), items)
        if category is None:
            continue
        out.append(
            MatchedFiling(
                symbol=symbol,
                category=category,
                form=str(form),
                items=items,
                filing_date=filing_date,
                acceptance=_non_empty(accepted),
                accession=str(accession),
                primary_document=_non_empty(document),
                primary_doc_description=_non_empty(description),
            )
        )
    out.sort(key=lambda f: (f.filing_date, f.acceptance or "", f.accession), reverse=True)
    return out


def ticker_ciks(payload: Mapping[str, Any], symbols: Sequence[str]) -> tuple[dict[str, int], list[str]]:
    """Map ``symbols`` to CIKs; a symbol under two CIKs, or absent, is unmapped (sorted)."""
    wanted = set(symbols)
    cik_of: dict[str, int] = {}
    ambiguous: set[str] = set()
    for entry in payload.values():
        symbol = normalize_ticker(entry["ticker"])
        if symbol not in wanted:
            continue
        cik = int(entry["cik_str"])
        if cik_of.setdefault(symbol, cik) != cik:
            ambiguous.add(symbol)
    mapped = {s: cik_of[s] for s in dict.fromkeys(symbols) if s in cik_of and s not in ambiguous}
    return mapped, sorted(wanted - mapped.keys())
