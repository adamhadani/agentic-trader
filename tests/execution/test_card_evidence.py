"""Card evidence: lookup statuses, the shared line formatter and the card_stats journal stream."""

import html as html_module
import re
from datetime import UTC, date, datetime, timedelta

import pytest

from agentic_trader.execution.card_evidence import (
    CardEvidence,
    CardStatsRepository,
    format_evidence_lines,
    lookup,
    parse_card_evidence,
)
from agentic_trader.execution.durable import EventKind
from tests.execution.card_stats_fixtures import key_stats, make_snapshot


NOW = datetime(2026, 10, 8, 14, 35, tzinfo=UTC)  # 10:35 New York
MAX_AGE = timedelta(seconds=345600)
FRESH = make_snapshot(computed_at=NOW - timedelta(hours=6))


def _lookup(snapshot=FRESH, strategy="TREND_PULLBACK", direction="LONG", **kw):
    return lookup(snapshot, strategy, direction, now=NOW, max_age=MAX_AGE, min_mature=kw.get("min_mature", 20))


def test_measured_evidence_carries_the_key_and_the_snapshot_provenance():
    evidence = _lookup()
    assert evidence.status == "measured" and evidence.n_mature == 30 and evidence.min_mature == 20
    assert (evidence.target_rate, evidence.stop_rate, evidence.timeout_rate) == (0.2, 0.7, 0.1)
    assert evidence.mean_r_cost == -0.35 and evidence.mean_timeout_r == 0.5
    assert evidence.snapshot_key == "card_stats/2026-10-08" and evidence.feed == "iex"
    assert evidence.cost_bps_per_side == 5.0 and evidence.window_start == date(2026, 7, 11)


@pytest.mark.parametrize(
    ("snapshot", "direction", "status", "n_mature"),
    [
        (make_snapshot(keys=[key_stats(n_mature=19)], computed_at=NOW), "LONG", "insufficient", 19),
        (FRESH, "SHORT", "insufficient", 0),  # no row for the key
        (make_snapshot(computed_at=NOW - MAX_AGE - timedelta(seconds=1)), "LONG", "stale", None),
        (make_snapshot(computed_at=NOW - MAX_AGE), "LONG", "measured", 30),  # exactly at the limit
        (None, "LONG", "unavailable", None),
    ],
)
def test_lookup_statuses(snapshot, direction, status, n_mature):
    evidence = _lookup(snapshot, direction=direction)
    assert evidence.status == status and evidence.n_mature == n_mature


def test_implied_ev_uses_the_cards_own_ratio_and_the_timeouts_r():
    evidence = _lookup()
    assert evidence.implied_ev(2.0) == pytest.approx(0.2 * 2.0 - 0.7 + 0.1 * 0.5)
    assert evidence.model_copy(update={"mean_timeout_r": None}).implied_ev(3.0) == pytest.approx(0.6 - 0.7)
    assert _lookup(None).implied_ev(2.0) is None


def test_measured_lines_in_both_renderers():
    html_lines = format_evidence_lines(_lookup(), 2.0, html=True)
    assert html_lines == [
        (
            "• <b>Measured record</b> (TREND_PULLBACK, LONG): 30 mature cards since 2026-07-11: "
            "20% target / 70% stop / 10% timeout, mean -0.35R after cost"
        ),
        "• <b>Implied EV at 2.0:1:</b> -0.25R",
        (
            "<i>Not validated alpha. Record measured on journaled candidates' deterministic brackets at the next "
            "hourly open (iex, 5 bp/side).</i>"
        ),
    ]
    text_lines = format_evidence_lines(_lookup(), 2.0, html=False)
    # The same facts, from the same helper: stripping the markup gives the terminal lines.
    assert [html_module.unescape(re.sub(r"</?[bi]>", "", line)) for line in html_lines] == text_lines


@pytest.mark.parametrize(
    ("evidence", "line"),
    [
        (
            _lookup(make_snapshot(keys=[key_stats(n_mature=7)], computed_at=NOW)),
            "• <b>Measured record:</b> insufficient evidence (7/20 mature cards)",
        ),
        (
            _lookup(make_snapshot(computed_at=datetime(2026, 10, 2, 12, 31, tzinfo=UTC))),
            "• <b>Measured record:</b> statistics stale (last computed 2026-10-02)",
        ),
        (_lookup(None), "• <b>Measured record:</b> no statistics in this scope"),
    ],
)
def test_other_status_lines_and_the_caveat_always_last(evidence, line):
    lines = format_evidence_lines(evidence, 2.0, html=True)
    assert lines[0] == line and lines[-1].startswith("<i>Not validated alpha.")
    if evidence.status == "unavailable":
        assert lines[-1].endswith("at the next hourly open.</i>")  # no feed or cost without a snapshot


def test_strategy_text_is_html_escaped():
    evidence = _lookup().model_copy(update={"strategy": "<b>x</b>"})
    assert "(&lt;b&gt;x&lt;/b&gt;, LONG)" in format_evidence_lines(evidence, 2.0, html=True)[0]


def test_parse_card_evidence_never_raises():
    assert parse_card_evidence(None, strategy="S", direction="LONG") is None
    assert parse_card_evidence(_lookup().model_dump(mode="json"), strategy="S", direction="LONG") == _lookup()
    for malformed in ({"status": "bogus"}, "not a dict", {"strategy": "S"}):
        fallback = parse_card_evidence(malformed, strategy="S", direction="LONG")
        assert fallback == CardEvidence(status="unavailable", strategy="S", direction="LONG")


async def test_repository_records_reads_and_is_idempotent(temp_db):
    repository = CardStatsRepository(temp_db.workflows)
    assert await repository.latest() is None and not await repository.exists(date(2026, 10, 8))
    payload = FRESH.model_dump(mode="json")
    await repository.record(payload)
    await repository.record(payload)  # same key: the journal keeps one event
    events = await temp_db.workflows.events(stream="card_stats")
    assert len(events) == 1 and events[0]["kind"] == EventKind.CARD_STATS_SNAPSHOT
    assert await repository.exists(date(2026, 10, 8))
    assert await repository.latest() == FRESH


async def test_an_unreadable_snapshot_is_no_evidence_not_an_error(temp_db):
    async with temp_db.session_factory() as session, session.begin():
        await temp_db.workflows.lock(session)
        await temp_db.workflows.append(
            session, stream="card_stats", kind=EventKind.CARD_STATS_SNAPSHOT, payload={"bogus": 1}, key="card_stats/x"
        )
    assert await CardStatsRepository(temp_db.workflows).latest() is None
