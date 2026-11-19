"""Frozen investor-day filing parser ``idday-parser-v1`` (research only, offline, deterministic).

Reads the cached 8-K documents of ``alpha investor-days fetch`` and emits one event row per issuer and
investor-day date. Any change to a rule below is a new parser version.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

from agentic_trader.research.investor_days import ARCHIVE_URL, InvestorDayStore, SearchHit, content_sha256


__all__ = [
    "PARSER_VERSION",
    "EventTable",
    "FilingParse",
    "IddayEvent",
    "dates_in",
    "load_event_table",
    "parse_document",
    "parse_filings",
    "save_event_table",
    "text_of",
]

PARSER_VERSION = "idday-parser-v1"

KEYWORD = re.compile(
    r"\b(?:(?:investor|analyst)s?(?:\s*(?:and|&|/)\s*(?:investor|analyst)s?)?\s+day"
    r"|capital\s+markets?\s+day|analyst\s+meeting|investor\s+meeting)\b",
    re.IGNORECASE,
)
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“(])")
_MONTHS = (
    ("January", "Jan"),
    ("February", "Feb"),
    ("March", "Mar"),
    ("April", "Apr"),
    ("May",),
    ("June", "Jun"),
    ("July", "Jul"),
    ("August", "Aug"),
    ("September", "Sept", "Sep"),
    ("October", "Oct"),
    ("November", "Nov"),
    ("December", "Dec"),
)
# Title-case or upper-case names only, so the verb "may" never parses as a date.
MONTH_OF = {form: i + 1 for i, names in enumerate(_MONTHS) for name in names for form in (name, name.upper())}
DATE = re.compile(
    r"\b("
    + "|".join(sorted(MONTH_OF, key=len, reverse=True))
    + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s+(\d{4})\b)?"
)
NUMERIC_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
ANNOUNCEMENT = re.compile(
    r"\b(?:(?:will|to|plans to|intends to|scheduled to)\s+"
    r"(?:host|hold|be held|be hosted|be webcast|webcast|present|be presented|make presentations|provide|be provided|discuss)"
    r"|save the date|join us|invite|schedules?|upcoming|more to come|to be held|in the coming"
    r"|will be attending|previously disclosed|previously announced)\b",
    re.IGNORECASE,
)
PAST_MARKER = re.compile(
    r"\b(?:attended|outlined|reiterated|articulated|unveiled|recent|last year|discussed at|presented at our)",
    re.IGNORECASE,
)
TODAY = re.compile(
    r"\b(?:(?:is|are)\s+(?:hosting|holding)|(?:hosted|held|hosting|holding)\b[^.]{0,60}\btoday"
    r"|today(?:'s|’s)?\s+(?:investor|analyst|capital\s+markets?))\b",
    re.IGNORECASE,
)
PRESENTATION = re.compile(r"presentation|slides", re.IGNORECASE)
KEYWORD_DISTANCE = 200
HORIZON_DAYS = 400
MERGE_DAYS = 3


def text_of(raw: bytes) -> str:
    s = raw.decode("utf-8", "replace")
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</(?:p|div|tr|li|h\d)>", ". ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s)


def dates_in(text: str, filed: date) -> list[tuple[int, date]]:
    """(position, date) per calendar date; a year-less date is its next occurrence on or after filed - 7 days."""
    found = []
    for m in DATE.finditer(text):
        month, day = MONTH_OF[m.group(1)], int(m.group(2))
        years = [int(m.group(3))] if m.group(3) else [filed.year, filed.year + 1]
        for year in years:
            try:
                d = date(year, month, day)
            except ValueError:
                continue
            if m.group(3) or d >= filed - timedelta(days=7):
                found.append((m.start(), d))
                break
    for m in NUMERIC_DATE.finditer(text):
        try:
            found.append((m.start(), date(int(m.group(3)), int(m.group(1)), int(m.group(2)))))
        except ValueError:
            continue
    return sorted(found)


@dataclass(frozen=True)
class FilingParse:
    filed: date
    announced: tuple[tuple[date, str, str], ...]
    event_day: bool
    day_sentence: str


def _in_future(d: date, filed: date) -> bool:
    return filed < d <= filed + timedelta(days=HORIZON_DAYS)


def parse_document(hit: SearchHit, raw: bytes) -> FilingParse:
    text = text_of(raw)
    filed = hit.filed
    bounds = [0] + [m.end() for m in SENTENCE_END.finditer(text)] + [len(text)]
    announced: dict[date, tuple[int, str, str]] = {}
    day_sentence = ""
    for m in KEYWORD.finditer(text):
        i = max(k for k in range(len(bounds) - 1) if bounds[k] <= m.start())
        sentence = text[bounds[i] : bounds[i + 1]].strip()
        offset = m.start() - bounds[i]
        dated = dates_in(sentence, filed)
        near = [
            (abs(pos - offset), d) for pos, d in dated if abs(pos - offset) <= KEYWORD_DISTANCE and _in_future(d, filed)
        ]
        if near and ANNOUNCEMENT.search(sentence) and not PAST_MARKER.search(sentence):
            distance, d = min(near)
            if d not in announced or distance < announced[d][0]:
                announced[d] = (distance, sentence, m.group(0))
        no_future = not any(_in_future(d, filed) for _, d in dated)
        today = TODAY.search(sentence) or any(filed - timedelta(days=1) <= d <= filed for _, d in dated)
        if not day_sentence and no_future and today:
            day_sentence = sentence
    if not day_sentence and KEYWORD.search(hit.description) and PRESENTATION.search(hit.description):
        day_sentence = hit.description
    return FilingParse(
        filed=filed,
        announced=tuple((d, s, k) for d, (_, s, k) in sorted(announced.items())),
        event_day=bool(day_sentence),
        day_sentence=day_sentence,
    )


@dataclass(frozen=True)
class IddayEvent:
    event_id: str
    cik: int
    symbols: tuple[str, ...]
    status: Literal["announced", "event_only"]
    filed: date | None
    event_date: date
    n_announcing: int
    event_day_material: bool
    keyword: str
    sentence: str
    accession: str
    url: str
    item_2_02: bool


def _row(event: IddayEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "cik": event.cik,
        "symbols": list(event.symbols),
        "status": event.status,
        "filed": event.filed.isoformat() if event.filed else None,
        "event_date": event.event_date.isoformat(),
        "n_announcing": event.n_announcing,
        "event_day_material": event.event_day_material,
        "keyword": event.keyword,
        "sentence": event.sentence,
        "accession": event.accession,
        "url": event.url,
        "item_2_02": event.item_2_02,
    }


def _from_row(row: dict[str, Any]) -> IddayEvent:
    return IddayEvent(
        event_id=row["event_id"],
        cik=row["cik"],
        symbols=tuple(row["symbols"]),
        status=row["status"],
        filed=date.fromisoformat(row["filed"]) if row["filed"] else None,
        event_date=date.fromisoformat(row["event_date"]),
        n_announcing=row["n_announcing"],
        event_day_material=row["event_day_material"],
        keyword=row["keyword"],
        sentence=row["sentence"],
        accession=row["accession"],
        url=row["url"],
        item_2_02=row["item_2_02"],
    )


def _digest(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class EventTable:
    parser_version: str
    content_sha256: str
    events: tuple[IddayEvent, ...]

    @property
    def events_sha256(self) -> str:
        return _digest([_row(event) for event in self.events])


def _url(cik: int, hit: SearchHit) -> str:
    return ARCHIVE_URL.format(cik=cik, accession_nodash=hit.accession.replace("-", ""), file=hit.file)


def _keyword_of(sentence: str) -> str:
    m = KEYWORD.search(sentence)
    return m.group(0) if m else ""


def _issuer_events(cik: int, symbols: tuple[str, ...], parsed: list[tuple[SearchHit, FilingParse]]) -> list[IddayEvent]:
    candidates = sorted({d for _, p in parsed for d, _, _ in p.announced} | {h.filed for h, p in parsed if p.event_day})
    kept: list[date] = []
    for d in candidates:
        if not kept or (d - kept[-1]).days > MERGE_DAYS:
            kept.append(d)
    window = timedelta(days=MERGE_DAYS)
    events = []
    for k in kept:
        announcing = sorted(
            (
                (h, min((a for a in p.announced if abs(a[0] - k) <= window), key=lambda a: (abs(a[0] - k), a[0])))
                for h, p in parsed
                if h.filed < k and any(abs(a[0] - k) <= window for a in p.announced)
            ),
            key=lambda item: (item[0].filed, item[0].accession, item[0].file),
        )
        if announcing:
            first, (event_date, sentence, keyword) = announcing[0]
            status: Literal["announced", "event_only"] = "announced"
            filed: date | None = first.filed
            n_announcing = len({h.accession for h, _ in announcing})
        else:
            event_date, status, filed, n_announcing = k, "event_only", None, 0
        day = sorted(
            ((h, p) for h, p in parsed if p.event_day and abs(h.filed - event_date) <= window),
            key=lambda item: (item[0].filed, item[0].accession, item[0].file),
        )
        if not announcing:
            first, p = day[0]
            sentence = p.day_sentence
            keyword = _keyword_of(sentence)
        events.append(
            IddayEvent(
                event_id=f"{cik}:{filed or 'none'}:{event_date}",
                cik=cik,
                symbols=symbols,
                status=status,
                filed=filed,
                event_date=event_date,
                n_announcing=n_announcing,
                event_day_material=bool(day),
                keyword=keyword,
                sentence=sentence,
                accession=first.accession,
                url=_url(cik, first),
                item_2_02="2.02" in first.items,
            )
        )
    return events


def parse_filings(store: InvestorDayStore) -> EventTable:
    events: list[IddayEvent] = []
    for record in store.issuers():
        parsed = []
        for hit in record.hits:
            raw = store.document(hit.accession, hit.file)
            if raw is not None:
                parsed.append((hit, parse_document(hit, raw)))
        events.extend(_issuer_events(record.cik, record.symbols, parsed))
    events.sort(key=lambda e: (e.cik, e.event_date))
    return EventTable(PARSER_VERSION, content_sha256(store), tuple(events))


def save_event_table(table: EventTable, path: Path) -> None:
    body = {
        "parser_version": table.parser_version,
        "content_sha256": table.content_sha256,
        "events_sha256": table.events_sha256,
        "events": [_row(event) for event in table.events],
    }
    path.write_text(json.dumps(body, indent=2, sort_keys=True))


def load_event_table(path: Path) -> EventTable:
    body = json.loads(path.read_text())
    table = EventTable(
        parser_version=body["parser_version"],
        content_sha256=body["content_sha256"],
        events=tuple(_from_row(row) for row in body["events"]),
    )
    if table.events_sha256 != body["events_sha256"]:
        raise ValueError(f"events_sha256 mismatch in {path.name}")
    return table
