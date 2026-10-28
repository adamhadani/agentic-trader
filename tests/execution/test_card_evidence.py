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
from agentic_trader.research.setups.net_excess import NET_EXCESS_UNIT
from tests.execution.card_stats_fixtures import key_stats, make_snapshot


NOW = datetime(2026, 10, 8, 14, 35, tzinfo=UTC)  # 10:35 New York
MAX_AGE = timedelta(seconds=345600)
FRESH = make_snapshot(computed_at=NOW - timedelta(hours=6))


def _lookup(snapshot=FRESH, strategy="TREND_PULLBACK", direction="LONG", timeframe="4h", **kw):
    return lookup(
        snapshot, strategy, timeframe, direction, now=NOW, max_age=MAX_AGE, min_mature=kw.get("min_mature", 20)
    )


def test_measured_evidence_carries_the_key_and_the_snapshot_provenance():
    evidence = _lookup()
    assert evidence.status == "measured" and evidence.n_mature == 23 and evidence.min_mature == 20
    assert (evidence.target_rate, evidence.stop_rate, evidence.timeout_rate) == (0.43, 0.52, 0.04)
    assert (evidence.live_mean, evidence.posterior_mean, evidence.posterior_se) == (-0.12, -0.06, 0.10)
    assert (evidence.prior_mean, evidence.prior_n_rows, evidence.prior_source) == (-0.05, 7049, "replay")
    assert (evidence.prior_window_start, evidence.prior_window_end) == (date(2021, 6, 1), date(2026, 1, 29))
    assert evidence.unit == NET_EXCESS_UNIT and evidence.timeframe == "4h"
    assert evidence.snapshot_key == "card_stats/net_excess_r_v1/2026-10-08" and evidence.feed == "iex"
    assert evidence.cost_bps_per_side == 5.0 and evidence.window_start == date(2026, 7, 11)
    assert evidence.first_decided_at == datetime(2026, 9, 24, 14, 35, tzinfo=UTC)
    assert evidence.n_fetch_failed == 0 and evidence.n_unresolved == 0


@pytest.mark.parametrize(
    ("snapshot", "direction", "status", "n_fetch_failed"),
    [
        (make_snapshot(keys=[key_stats(n_fetch_failed=3)], computed_at=NOW), "LONG", "measured", 3),
        (make_snapshot(keys=[key_stats(n_mature=7, n_fetch_failed=2)], computed_at=NOW), "LONG", "insufficient", 2),
        (make_snapshot(keys=[key_stats(n_fetch_failed=3)], computed_at=NOW), "SHORT", "insufficient", 0),  # no row
        (None, "LONG", "unavailable", 0),
    ],
)
def test_a_keys_fetch_failures_are_carried_and_zero_without_a_row(snapshot, direction, status, n_fetch_failed):
    evidence = _lookup(snapshot, direction=direction)
    assert (evidence.status, evidence.n_fetch_failed) == (status, n_fetch_failed)


@pytest.mark.parametrize(
    ("snapshot", "direction", "status", "n_mature"),
    [
        (make_snapshot(keys=[key_stats(n_mature=19)], computed_at=NOW), "LONG", "insufficient", 19),
        # No row for the key: the aggregate ("*", "*") row is never a fallback.
        (
            make_snapshot(keys=[key_stats(), key_stats("*", "*", n_mature=50)], computed_at=NOW),
            "SHORT",
            "insufficient",
            0,
        ),
        # Enough mature labels but no finite posterior: nothing measurable to show.
        (make_snapshot(keys=[key_stats(posterior_mean=None)], computed_at=NOW), "LONG", "unavailable", 23),
        (make_snapshot(computed_at=NOW - MAX_AGE - timedelta(seconds=1)), "LONG", "stale", None),
        (make_snapshot(computed_at=NOW - MAX_AGE), "LONG", "measured", 23),  # exactly at the limit
        (None, "LONG", "unavailable", None),
    ],
)
def test_lookup_statuses(snapshot, direction, status, n_mature):
    evidence = _lookup(snapshot, direction=direction)
    assert evidence.status == status and evidence.n_mature == n_mature


def test_lookup_v1_snapshot_unavailable():
    """A snapshot written before the unit existed (read on deploy day) is "no statistics", never a withhold."""
    v1 = make_snapshot(computed_at=NOW).model_copy(update={"unit": None})
    evidence = _lookup(v1)
    assert evidence.status == "unavailable" and evidence.posterior_mean is None and evidence.unit is None
    assert format_evidence_lines(evidence, html=True)[0] == NO_STATISTICS


def test_snapshot_key_follows_the_unit():
    snapshot = make_snapshot(computed_at=NOW)
    assert snapshot.snapshot_key == "card_stats/net_excess_r_v1/2026-10-08"
    assert snapshot.model_copy(update={"unit": None}).snapshot_key == "card_stats/2026-10-08"


def test_lookup_insufficient_carries_prior():
    snapshot = make_snapshot(keys=[key_stats(n_mature=17)], computed_at=NOW)
    evidence = _lookup(snapshot, min_mature=20)
    assert evidence.status == "insufficient" and evidence.n_mature == 17
    assert (evidence.posterior_mean, evidence.posterior_se) == (-0.06, 0.10)
    assert (evidence.prior_mean, evidence.prior_n_rows, evidence.prior_source) == (-0.05, 7049, "replay")


def test_lookup_by_timeframe():
    snapshot = make_snapshot(
        keys=[key_stats(timeframe="4h", posterior_mean=-0.06), key_stats(timeframe="1h", posterior_mean=0.21)],
        computed_at=NOW,
    )
    assert _lookup(snapshot, timeframe="4h").posterior_mean == -0.06
    assert _lookup(snapshot, timeframe="1h").posterior_mean == 0.21
    assert _lookup(snapshot, timeframe="1d").status == "insufficient"


LINE_1 = "• <b>Expected excess</b> (TREND_PULLBACK 4h, LONG): -0.06R ± 0.10 vs beta-matched SPY after cost"
LINE_2_MEASURED = (
    "  replay 2021–26: -0.05R over 7,049 setups · "
    "live: -0.12R over 23 mature since 2026-09-24 (43% target / 52% stop / 4% timeout)"
)
CAVEAT_HTML = (
    "<i>Not validated alpha. Replay prior (SIP 2021–26) and live record (iex) on journaled candidates' "
    "deterministic brackets; mature = 21+ sessions old; per-name cost ≥ 5 bp/side.</i>"
)


def test_card_lines_measured():
    assert format_evidence_lines(_lookup(), html=True) == [LINE_1, LINE_2_MEASURED, CAVEAT_HTML]
    text_lines = format_evidence_lines(_lookup(), html=False)
    # The same facts, from the same helper: stripping the markup gives the terminal lines.
    html_lines = format_evidence_lines(_lookup(), html=True)
    assert [html_module.unescape(re.sub(r"</?[bi]>", "", line)) for line in html_lines] == text_lines


def test_card_lines_insufficient():
    evidence = _lookup(make_snapshot(keys=[key_stats(n_mature=17)], computed_at=NOW))
    assert format_evidence_lines(evidence, html=True) == [
        LINE_1,
        "  replay 2021–26: -0.05R over 7,049 setups · live: 17/20 mature, not yet decisive",
        CAVEAT_HTML,
    ]


def test_card_lines_skeptical():
    snapshot = make_snapshot(
        keys=[key_stats(prior_source="skeptical", prior_mean=0.0, prior_n_rows=0)], computed_at=NOW
    )
    assert format_evidence_lines(_lookup(snapshot), html=True) == [
        LINE_1,
        (
            "  no replay prior (shrunk toward 0) · "
            "live: -0.12R over 23 mature since 2026-09-24 (43% target / 52% stop / 4% timeout)"
        ),
        CAVEAT_HTML,
    ]


def test_card_lines_partial():
    snapshot = make_snapshot(keys=[key_stats(n_fetch_failed=1, n_unresolved=1)], computed_at=NOW)
    lines = format_evidence_lines(_lookup(snapshot), html=True)
    assert lines[1] == LINE_2_MEASURED + " · 2 unreadable (never withheld)"


def test_card_html_escapes():
    evidence = _lookup().model_copy(update={"strategy": "<b>x</b>", "timeframe": "<1h>", "feed": "<i>"})
    lines = format_evidence_lines(evidence, html=True)
    assert "(&lt;b&gt;x&lt;/b&gt; &lt;1h&gt;, LONG)" in lines[0]
    assert "live record (&lt;i&gt;)" in lines[-1] and "(<i>)" not in lines[-1]


def test_the_measured_line_dates_from_the_first_decision_in_new_york_or_the_window_start():
    late = key_stats(first_decided_at="2026-09-25T02:30:00+00:00")  # 22:30 New York on September 24
    assert (
        "over 23 mature since 2026-09-24 ("
        in format_evidence_lines(_lookup(make_snapshot(keys=[late], computed_at=NOW)), html=False)[1]
    )
    unknown = _lookup(make_snapshot(keys=[key_stats(first_decided_at=None)], computed_at=NOW))
    assert unknown.first_decided_at is None
    assert "over 23 mature since 2026-07-11 (" in format_evidence_lines(unknown, html=False)[1]


NO_STATISTICS = "• <b>Measured record:</b> no statistics in this scope"


@pytest.mark.parametrize(
    ("evidence", "line"),
    [
        (
            # No row for the key: nothing to state but the shortfall.
            _lookup(make_snapshot(keys=[key_stats(n_mature=7)], computed_at=NOW), direction="SHORT"),
            "• <b>Measured record:</b> insufficient evidence (0/20 mature candidates)",
        ),
        (
            # 01:30 UTC on October 3 is 21:30 New York on October 2: the date is New York's.
            _lookup(make_snapshot(computed_at=datetime(2026, 10, 3, 1, 30, tzinfo=UTC))),
            "• <b>Measured record:</b> statistics stale (last computed 2026-10-02)",
        ),
        (_lookup(None), NO_STATISTICS),
        # Enough mature labels without a finite posterior (lookup) or without rates (a stored payload):
        # never "insufficient", which is only for fewer than the floor.
        (_lookup(make_snapshot(keys=[key_stats(posterior_mean=None)], computed_at=NOW)), NO_STATISTICS),
        (_lookup().model_copy(update={"posterior_mean": None}), NO_STATISTICS),
        (_lookup().model_copy(update={"live_mean": None}), NO_STATISTICS),
        (_lookup().model_copy(update={"status": "insufficient"}), NO_STATISTICS),  # 23 >= 20 is not insufficient
    ],
    ids=[
        "insufficient-no-row",
        "stale",
        "unavailable",
        "no-posterior",
        "measured-without-posterior",
        "measured-without-live",
        "insufficient-over-floor",
    ],
)
def test_other_status_lines_and_the_caveat_always_last(evidence, line):
    lines = format_evidence_lines(evidence, html=True)
    assert lines[0] == line and lines[-1].startswith("<i>Not validated alpha.")


def test_v1_notification_evidence_renders_no_statistics():
    """A queued card whose stored ``card_evidence`` predates the unit parses and renders as no statistics."""
    v1 = {
        "status": "measured",
        "strategy": "TREND_PULLBACK",
        "direction": "LONG",
        "n_mature": 30,
        "min_mature": 20,
        "target_rate": 0.2,
        "stop_rate": 0.7,
        "timeout_rate": 0.1,
        "mean_r_cost": -0.35,
        "mean_timeout_r": 0.5,
        "feed": "iex",
        "cost_bps_per_side": 5.0,
        "snapshot_key": "card_stats/2026-10-08",
    }
    evidence = parse_card_evidence(v1, strategy="TREND_PULLBACK", direction="LONG")
    assert evidence is not None and evidence.unit is None
    lines = format_evidence_lines(evidence, html=True)
    assert lines[0] == NO_STATISTICS and lines[-1].startswith("<i>Not validated alpha.")


def test_parse_card_evidence_never_raises():
    assert parse_card_evidence(None, strategy="S", direction="LONG") is None
    assert parse_card_evidence(_lookup().model_dump(mode="json"), strategy="S", direction="LONG") == _lookup()
    naive = {**_lookup().model_dump(mode="json"), "computed_at": "2026-10-08T08:35:00"}  # no offset
    for malformed in ({"status": "bogus"}, "not a dict", {"strategy": "S"}, naive):
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


async def plant_snapshot_payload(db, payload, key="card_stats/x"):
    async with db.session_factory() as session, session.begin():
        await db.workflows.lock(session)
        await db.workflows.append(
            session, stream="card_stats", kind=EventKind.CARD_STATS_SNAPSHOT, payload=payload, key=key
        )


async def test_an_unreadable_snapshot_is_no_evidence_not_an_error(temp_db):
    await plant_snapshot_payload(temp_db, {"bogus": 1})
    assert await CardStatsRepository(temp_db.workflows).latest() is None


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-inf"])
async def test_a_non_finite_key_statistic_is_no_evidence(temp_db, value):
    """The journal refuses a float NaN, but a JSON string parses as one: the reader rejects it as unreadable."""
    payload = make_snapshot(computed_at=NOW).model_dump(mode="json")
    payload["keys"][0]["posterior_mean"] = value
    await plant_snapshot_payload(temp_db, payload)
    snapshot = await CardStatsRepository(temp_db.workflows).latest()
    assert snapshot is None
    assert _lookup(snapshot).status == "unavailable"
