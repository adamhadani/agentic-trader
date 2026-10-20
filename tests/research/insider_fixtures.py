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
) -> bytes:
    """A zip with SUBMISSION, REPORTINGOWNER and NONDERIV_TRANS tables (one default row each).

    ``drop`` omits a table; ``nonderiv_header`` replaces NONDERIV_TRANS's header line (and its rows).
    """
    tables = {
        "SUBMISSION.tsv": _tsv(SUBMISSION_COLUMNS, [DEFAULT_SUBMISSION] if submissions is None else submissions, None),
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
