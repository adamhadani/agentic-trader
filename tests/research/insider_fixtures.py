"""In-memory SEC Form 3/4/5 archive builder shared by the insider-lane tests."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Mapping, Sequence


SUBMISSION_COLUMNS = ("ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE", "ISSUERCIK", "AFF10B5ONE")
OWNER_COLUMNS = ("ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNER_RELATIONSHIP")
TRANS_COLUMNS = (
    "ACCESSION_NUMBER",
    "TRANS_DATE",
    "TRANS_CODE",
    "TRANS_SHARES",
    "TRANS_PRICEPERSHARE",
    "TRANS_ACQUIRED_DISP_CD",
)

DEFAULT_SUBMISSION = {
    "ACCESSION_NUMBER": "0001-25-000001",
    "FILING_DATE": "05-MAR-2025",
    "DOCUMENT_TYPE": "4",
    "ISSUERCIK": "0000320193",
    "AFF10B5ONE": "0",
}
DEFAULT_OWNER = {"ACCESSION_NUMBER": "0001-25-000001", "RPTOWNERCIK": "0001214156", "RPTOWNER_RELATIONSHIP": "Officer"}
DEFAULT_TRANS = {
    "ACCESSION_NUMBER": "0001-25-000001",
    "TRANS_DATE": "03-MAR-2025",
    "TRANS_CODE": "P",
    "TRANS_SHARES": "100",
    "TRANS_PRICEPERSHARE": "10.5",
    "TRANS_ACQUIRED_DISP_CD": "A",
}


def _tsv(columns: Sequence[str], rows: Sequence[Mapping[str, str]], header: str | None) -> bytes:
    lines = [header if header is not None else "\t".join(columns)]
    lines += ["\t".join(row.get(column, "") for column in columns) for row in rows if header is None]
    return ("\n".join(lines) + "\n").encode()


def make_archive(
    *,
    submissions: Sequence[Mapping[str, str]] | None = None,
    owners: Sequence[Mapping[str, str]] | None = None,
    trans: Sequence[Mapping[str, str]] | None = None,
    drop: str | None = None,
    nonderiv_header: str | None = None,
    submission_columns: Sequence[str] = SUBMISSION_COLUMNS,
) -> bytes:
    """A zip with SUBMISSION, REPORTINGOWNER and NONDERIV_TRANS tables (one default row each).

    ``drop`` omits a table; ``nonderiv_header`` replaces NONDERIV_TRANS's header line (and its rows);
    ``submission_columns`` sets SUBMISSION's header (an old archive has no ``AFF10B5ONE``).
    """
    tables = {
        "SUBMISSION.tsv": _tsv(submission_columns, [DEFAULT_SUBMISSION] if submissions is None else submissions, None),
        "REPORTINGOWNER.tsv": _tsv(OWNER_COLUMNS, [DEFAULT_OWNER] if owners is None else owners, None),
        "NONDERIV_TRANS.tsv": _tsv(
            TRANS_COLUMNS,
            [DEFAULT_TRANS] if trans is None else trans,
            nonderiv_header.rstrip("\n") if nonderiv_header else None,
        ),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in tables.items():
            if name != drop:
                archive.writestr(name, data)
    return buffer.getvalue()


def trade(
    *,
    code: str = "P",
    ad: str = "A",
    shares: float | str = 100,
    price: float | str = 10.0,
    role: str = "Officer",
    doc: str = "4",
    plan: str = "0",
    filing: str = "05-MAR-2025",
    trans: str = "03-MAR-2025",
    cik: int = 1000,
    owner: int = 7,
    owners: Sequence[tuple[int, str]] | None = None,
) -> dict[str, list[dict[str, str]]]:
    """One accession with one non-derivative row; ``owners`` (CIK, relationship) replaces ``owner``/``role``.

    ``trades_archive`` assigns the accession number.
    """
    return {
        "submissions": [
            {
                "FILING_DATE": filing,
                "DOCUMENT_TYPE": doc,
                "ISSUERCIK": f"{cik:010d}",
                "AFF10B5ONE": plan,
            }
        ],
        "owners": [
            {"RPTOWNERCIK": f"{cik_:010d}", "RPTOWNER_RELATIONSHIP": relationship}
            for cik_, relationship in (owners if owners is not None else [(owner, role)])
        ],
        "trans": [
            {
                "TRANS_DATE": trans,
                "TRANS_CODE": code,
                "TRANS_SHARES": str(shares),
                "TRANS_PRICEPERSHARE": str(price),
                "TRANS_ACQUIRED_DISP_CD": ad,
            }
        ],
    }


def trades_archive(
    trades: Sequence[Mapping[str, Sequence[Mapping[str, str]]]],
    *,
    submission_columns: Sequence[str] = SUBMISSION_COLUMNS,
) -> bytes:
    """An archive holding ``trades`` (from ``trade``), the i-th as accession ``0000000001-25-<i>``."""
    tables: dict[str, list[dict[str, str]]] = {"submissions": [], "owners": [], "trans": []}
    for i, one in enumerate(trades):
        accession = f"0000000001-25-{i:06d}"
        for name, rows in one.items():
            tables[name] += [{**row, "ACCESSION_NUMBER": accession} for row in rows]
    return make_archive(
        submissions=tables["submissions"],
        owners=tables["owners"],
        trans=tables["trans"],
        submission_columns=submission_columns,
    )
