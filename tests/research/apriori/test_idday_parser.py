import json
from datetime import date

import pytest
from click.testing import CliRunner

from agentic_trader.cli.commands.alpha_investor_days import investor_days_group
from agentic_trader.research.apriori.idday_parser import (
    PARSER_VERSION,
    EventTable,
    dates_in,
    load_event_table,
    parse_document,
    parse_filings,
    save_event_table,
)
from agentic_trader.research.investor_days import InvestorDayStore, IssuerRecord, SearchHit


def hit(filed, text="", *, accession="0001-24-000001", file="a.htm", items=("8.01",), description=""):
    return SearchHit(
        accession=accession, file=file, filed=filed, items=tuple(items), file_type="EX-99.1", description=description
    )


def parse(text, filed, **kw):
    return parse_document(hit(filed, **kw), f"<p>{text}</p>".encode())


def announced_dates(text, filed):
    return [a[0] for a in parse(text, filed).announced]


def test_version():
    assert PARSER_VERSION == "idday-parser-v1"


def test_announcement_nearest_date():
    text = (
        "Acme Inc. today announced it will host its Investor Day on Tuesday, March 5, 2024, "
        "following its earnings call on February 27."
    )
    assert parse(text, date(2024, 2, 20)).announced[0][0] == date(2024, 3, 5)


def test_heading_then_sentence():
    text = (
        "Annual Investor Day. Example Corp will host its annual Investor Day at its headquarters on Sept. 21 at 8 a.m."
    )
    assert announced_dates(text, date(2016, 8, 23)) == [date(2016, 9, 21)]


@pytest.mark.parametrize(
    ("text", "filed"),
    [
        ("outlined at our December 7th Investor Day", date(2023, 3, 14)),
        ("thank those who attended our investor day on May 24", date(2022, 8, 8)),
        ("guidance that it reiterated at Investor Day on September 29, 2022", date(2022, 11, 1)),
    ],
)
def test_past_markers_reject(text, filed):
    assert announced_dates(text, filed) == []


def test_past_marker_beats_phrase():
    assert (
        announced_dates("We will host an Investor Day on June 3, 2024, as recently attended.", date(2024, 5, 1)) == []
    )


def test_date_in_next_sentence_rejected():
    assert (
        announced_dates("We look forward to our annual Investor Day. The quarter ends Oct. 31.", date(2016, 8, 23))
        == []
    )


def test_no_verb_rejected():
    assert announced_dates("Sample Society Intellisight Investor Day. August 12, 2020.", date(2020, 8, 7)) == []
    assert announced_dates("INVESTOR DAY 2022 Fiscal Year Oct 20-Sep 21", date(2021, 10, 1)) == []


@pytest.mark.parametrize(
    ("text", "filed", "expected"),
    [
        ("Save the Date June 24, 2025 Sample Co Investor Day", date(2025, 5, 1), date(2025, 6, 24)),
        (
            "intend to make presentations in Houston, Texas on January 26, 2022 at the Example Corp 2022 Investor Day",
            date(2022, 1, 10),
            date(2022, 1, 26),
        ),
        ("with more to come at our long-planned Investor Day on September 26", date(2022, 9, 1), date(2022, 9, 26)),
        ("Announced our upcoming investor day on May 21", date(2022, 5, 1), date(2022, 5, 21)),
        (
            "Additional detail to be provided at Acme's Investor Day on March 23, 2023",
            date(2023, 3, 1),
            date(2023, 3, 23),
        ),
    ],
)
def test_phrases_accept(text, filed, expected):
    assert announced_dates(text, filed) == [expected]


def test_year_rollover():
    assert announced_dates("will host its Investor Day on January 10", date(2024, 12, 15)) == [date(2025, 1, 10)]


def test_date_window_bounds():
    assert announced_dates("will host its Investor Day on June 3, 2030", date(2024, 5, 1)) == []
    assert announced_dates("will host its Investor Day on May 1, 2024", date(2024, 5, 1)) == []


def test_may_verb_not_a_date():
    assert dates_in("It may 5 times", date(2025, 1, 1)) == []
    assert [d for _, d in dates_in("MAY 5, 2025", date(2025, 1, 1))] == [date(2025, 5, 5)]


def test_numeric_dates_need_year():
    assert dates_in("1/2", date(2024, 1, 1)) == []
    assert [d for _, d in dates_in("3/5/2024", date(2024, 1, 1))] == [date(2024, 3, 5)]


def test_event_day_material():
    day = parse("On March 5, 2024, Acme is hosting an Investor Day.", date(2024, 3, 5))
    assert day.event_day
    assert "hosting" in day.day_sentence
    slides = parse_document(hit(date(2024, 3, 5), description="INVESTOR DAY PRESENTATION"), b"<p>Cover.</p>")
    assert slides.event_day
    assert slides.day_sentence == "INVESTOR DAY PRESENTATION"
    assert not parse("Acme will host its Investor Day on April 2, 2024.", date(2024, 3, 5)).event_day


def issuer_store(tmp_path, hits_docs, *, cik=111):
    store = InvestorDayStore(tmp_path)
    hits = []
    for h, text in hits_docs:
        if text is not None:
            store.write_document(h.accession, h.file, f"<p>{text}</p>".encode())
        hits.append(h)
    store.write_hits([IssuerRecord(cik, ("ACME",), True, False, tuple(hits))], [])
    return store


def test_events_merge_and_first_announcement(tmp_path):
    store = issuer_store(
        tmp_path,
        [
            (hit(date(2024, 2, 1), accession="0001-24-000001"), "Acme will host its Investor Day on March 5, 2024."),
            (
                hit(date(2024, 2, 20), accession="0001-24-000002"),
                "Reminder: Acme will host its Investor Day on March 6, 2024.",
            ),
            (hit(date(2024, 3, 5), accession="0001-24-000003"), "On March 5, 2024, Acme is hosting an Investor Day."),
            (hit(date(2024, 3, 6), accession="0001-24-000004"), None),
        ],
    )
    table = parse_filings(store)
    assert len(table.events) == 1
    event = table.events[0]
    assert (event.status, event.filed, event.event_date) == ("announced", date(2024, 2, 1), date(2024, 3, 5))
    assert event.n_announcing == 2
    assert event.event_day_material
    assert event.event_id == "111:2024-02-01:2024-03-05"
    assert event.accession == "0001-24-000001"
    assert event.url == "https://www.sec.gov/Archives/edgar/data/111/000124000001/a.htm"
    assert table.parser_version == PARSER_VERSION


def test_event_only(tmp_path):
    store = issuer_store(tmp_path, [(hit(date(2024, 3, 5)), "On March 5, 2024, Acme is hosting an Investor Day.")])
    (event,) = parse_filings(store).events
    assert (event.status, event.filed, event.n_announcing, event.event_date) == (
        "event_only",
        None,
        0,
        date(2024, 3, 5),
    )
    assert event.event_id == "111:none:2024-03-05"
    assert event.event_day_material
    assert "hosting" in event.sentence


def test_item_2_02_flag(tmp_path):
    store = issuer_store(
        tmp_path,
        [(hit(date(2024, 2, 1), items=("2.02", "9.01")), "Acme will host its Investor Day on March 5, 2024.")],
    )
    assert parse_filings(store).events[0].item_2_02 is True
    store = issuer_store(tmp_path / "b", [(hit(date(2024, 2, 1)), "Acme will host its Investor Day on March 5, 2024.")])
    assert parse_filings(store).events[0].item_2_02 is False


def test_events_sha256_roundtrip(tmp_path):
    store = issuer_store(tmp_path, [(hit(date(2024, 2, 1)), "Acme will host its Investor Day on March 5, 2024.")])
    table = parse_filings(store)
    path = tmp_path / "events.json"
    save_event_table(table, path)
    loaded = load_event_table(path)
    assert isinstance(loaded, EventTable)
    assert loaded == table
    assert loaded.events_sha256 == table.events_sha256
    body = json.loads(path.read_text())
    body["events"][0]["sentence"] = "tampered"
    path.write_text(json.dumps(body))
    with pytest.raises(ValueError, match="events_sha256"):
        load_event_table(path)
    assert parse_filings(store) == table


def test_events_cli(tmp_path):
    issuer_store(tmp_path / "c", [(hit(date(2024, 2, 1)), "Acme will host its Investor Day on March 5, 2024.")])
    out = tmp_path / "events.json"
    args = ["events", "--cache", str(tmp_path / "c"), "--output", str(out)]
    result = CliRunner().invoke(investor_days_group, args)
    assert result.exit_code == 0, result.output
    assert "events_sha256" in result.output and "announced 1 event_only 0" in result.output
    assert CliRunner().invoke(investor_days_group, args).exit_code != 0
