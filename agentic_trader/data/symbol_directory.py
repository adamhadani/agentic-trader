"""Strict parsing of public current-day Nasdaq symbol directories."""

import csv
import io
from datetime import date


MAX_METADATA_ROWS = 100_000
DIRECTORY_NAMES = ("nasdaqlisted", "otherlisted")


def parse_directory(text: str, name: str) -> dict:
    if name not in DIRECTORY_NAMES:
        raise ValueError("Unknown Nasdaq symbol directory")
    lines = text.splitlines()
    if len(lines) < 3 or not lines[-1].startswith("File Creation Time: "):
        raise ValueError("Complete directory with generation footer required")
    raw_date = lines[-1].split(": ", 1)[1].split("|", 1)[0][:8]
    generated = date(int(raw_date[4:8]), int(raw_date[:2]), int(raw_date[2:4]))
    rows = list(csv.DictReader(io.StringIO("\n".join(lines[:-1])), delimiter="|"))
    required = {"Security Name", "Test Issue", "ETF"} | (
        {"Symbol", "Financial Status", "NextShares"}
        if name == "nasdaqlisted"
        else {"ACT Symbol", "CQS Symbol", "Exchange"}
    )
    if not rows or len(rows) > MAX_METADATA_ROWS or not required <= rows[0].keys():
        raise ValueError("Invalid or unbounded directory schema")
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError("Malformed directory row")
    return {"name": name, "file_date": generated.isoformat(), "file_creation_time": lines[-1], "rows": rows}
