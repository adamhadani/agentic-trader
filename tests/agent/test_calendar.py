"""The typed live feed and the union tier-1 calendar (spec §5.1, §7.1 "Live fetch status", "Union and statuses")."""

import dataclasses
import json
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from agentic_trader.agent.calendar import (
    MATCH_TOLERANCE,
    BaseEconomicCalendar,
    CalendarDisagreement,
    CalendarFetch,
    ForexFactoryCalendar,
    MacroEvent,
    StaticTier1Calendar,
    Tier1Kind,
    UnionTier1Calendar,
    tier1_kind,
)
from agentic_trader.config import AppConfig
from agentic_trader.constants import CalendarStatus
from agentic_trader.risk import RiskLimits


FIXTURE = Path(__file__).parent.parent / "fixtures/calendar/us-tier1-test-v1.json"
# The fixture calendar's CPI release: 2021-03-10 08:30 EST.
CPI_AT = datetime(2021, 3, 10, 13, 30, tzinfo=UTC)
FED = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


class Counter:
    """A MockTransport handler that counts its requests."""

    def __init__(self, handler) -> None:
        self.handler = handler
        self.n = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.n += 1
        return self.handler(request)


def raise_(exc: Exception):
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


def respond(status: int, **kwargs):
    return lambda request: httpx.Response(status, **kwargs)


def item(title: str, at: str, *, country: str = "USD", impact: str = "High") -> dict:
    return {"title": title, "country": country, "date": at, "impact": impact, "forecast": None, "previous": None}


def feed(payload, clock: Clock | None = None) -> tuple[ForexFactoryCalendar, Counter]:
    calls = Counter(respond(200, json=payload))
    clock = clock or Clock(datetime(2026, 10, 21, 16, tzinfo=UTC))
    return ForexFactoryCalendar(transport=httpx.MockTransport(calls), clock=clock), calls


class FixedFeed(BaseEconomicCalendar):
    source = "fixed"
    label = "Fixed"

    def __init__(self, fetch: CalendarFetch) -> None:
        self.fetch = fetch

    async def fetch_events(self, force_refresh: bool = False) -> CalendarFetch:
        return self.fetch


def live_ok(*events: MacroEvent, coverage: tuple[date, date] | None = (date(2021, 3, 7), date(2021, 3, 13))):
    return FixedFeed(CalendarFetch(CalendarStatus.OK, tuple(events), CPI_AT, coverage=coverage))


def live_failed(error: str = "ReadTimeout") -> FixedFeed:
    return FixedFeed(CalendarFetch(CalendarStatus.FAILED, fetched_at=CPI_AT, error=error))


def live_event(title: str, at: datetime, kind: Tier1Kind | None) -> MacroEvent:
    return MacroEvent(title=title, country="USD", impact="High", timestamp=at, kind=kind)


def union(live: BaseEconomicCalendar, static: StaticTier1Calendar | None = None, *, policy="open"):
    return UnionTier1Calendar(static or StaticTier1Calendar.load(FIXTURE, None), live, unknown_policy=policy)


def write_calendar(tmp_path: Path, events: list[dict], *, valid_through: str = "2099-12-31") -> Path:
    document = {
        "schema_version": 1,
        "calendar_id": "us-tier1-v1",
        "timezone": "America/New_York",
        "valid_from": "2020-01-01",
        "valid_through": valid_through,
        "retrieved_on": "2026-10-21",
        "series_last_listed": {kind.value: valid_through for kind in Tier1Kind},
        "events": events,
    }
    path = tmp_path / "us-tier1-v1.json"
    path.write_text(json.dumps(document))
    return path


# --- live fetch status ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("handler", "error", "http_status"),
    [
        (raise_(httpx.ReadTimeout("t")), "ReadTimeout", None),
        (respond(403, text="denied"), "UnexpectedStatus", 403),
        (respond(429, text="slow"), "UnexpectedStatus", 429),
        (respond(200, text="<html>maintenance</html>"), "JSONDecodeError", None),
        (respond(200, json={"events": []}), "UnexpectedPayload", None),
        # The weekly feed always lists dozens of items: an empty week, or one where no item parses, is broken.
        (respond(200, json=[]), "UnexpectedPayload", None),
        (respond(200, json=[{"date": "garbage"}, "x", {"title": "CPI m/m"}]), "UnexpectedPayload", None),
    ],
)
async def test_a_failed_fetch_is_typed_never_cached_as_ok_and_retried_after_backoff(handler, error, http_status):
    clock = Clock(datetime(2026, 10, 21, 16, tzinfo=UTC))
    calls = Counter(handler)
    feed = ForexFactoryCalendar(transport=httpx.MockTransport(calls), clock=clock)
    first = await feed.fetch_events()
    assert (first.status, first.error, first.http_status, first.events) == (
        CalendarStatus.FAILED,
        error,
        http_status,
        (),
    )
    assert (await feed.fetch_events()).status is CalendarStatus.FAILED and calls.n == 1  # inside the backoff
    clock.advance(minutes=5, seconds=1)
    await feed.fetch_events()
    assert calls.n == 2


async def test_a_failure_logs_one_warning_with_the_type_name_only(caplog):
    calls = Counter(raise_(httpx.ConnectError("secret host detail")))
    feed = ForexFactoryCalendar(transport=httpx.MockTransport(calls), clock=Clock(datetime(2026, 10, 21, tzinfo=UTC)))
    with caplog.at_level(logging.WARNING, logger="agentic_trader.agent.calendar"):
        await feed.fetch_events()
    [record] = [r for r in caplog.records if getattr(r, "event", None) == "macro_calendar_fetch_failed"]
    assert (record.levelno, record.error, record.http_status) == (logging.WARNING, "ConnectError", None)
    assert "secret host detail" not in record.getMessage()


async def test_zero_usd_events_is_ok_with_coverage():
    live, _ = feed(
        [
            item("German Prelim CPI m/m", "2026-10-19T08:00:00-04:00", country="EUR"),
            item("ECB Main Refinancing Rate", "2026-10-23T08:15:00-04:00", country="EUR"),
        ]
    )
    result = await live.fetch_events()
    assert (result.status, result.events, result.error, result.unparsed_items) == (CalendarStatus.OK, (), None, 0)
    assert result.coverage == (date(2026, 10, 19), date(2026, 10, 23))
    assert result.fetched_at == datetime(2026, 10, 21, 16, tzinfo=UTC)


async def test_a_malformed_item_is_counted_not_dropped():
    live, _ = feed([item("CPI m/m", "2026-10-15T08:30:00-04:00"), {"date": "garbage"}, "x"])
    result = await live.fetch_events()
    assert result.status is CalendarStatus.OK and result.unparsed_items == 2
    [event] = result.events
    assert (event.title, event.kind, event.source) == ("CPI m/m", Tier1Kind.CPI, "forexfactory")
    assert event.timestamp == datetime(2026, 10, 15, 12, 30, tzinfo=UTC)
    assert result.coverage == (date(2026, 10, 15), date(2026, 10, 15))


async def test_usd_high_impact_items_are_events_and_only_tier1_titles_carry_a_kind():
    live, _ = feed(
        [
            item("Retail Sales m/m", "2026-10-16T08:30:00-04:00"),
            item("Core PPI m/m", "2026-10-16T08:30:00-04:00", impact="Medium"),
            item("Crude Oil Inventories", "2026-10-16T10:30:00-04:00", impact="Medium"),
        ]
    )
    result = await live.fetch_events()
    assert [(e.title, e.kind) for e in result.events] == [("Retail Sales m/m", None), ("Core PPI m/m", Tier1Kind.PPI)]


async def test_a_success_is_cached_for_thirty_minutes():
    clock = Clock(datetime(2026, 10, 21, 16, tzinfo=UTC))
    live, calls = feed([item("CPI m/m", "2026-10-15T08:30:00-04:00")], clock)
    await live.fetch_events()
    clock.advance(minutes=29)
    assert (await live.fetch_events()).status is CalendarStatus.OK and calls.n == 1
    clock.advance(minutes=1, seconds=1)
    await live.fetch_events()
    assert calls.n == 2


async def test_an_expired_success_is_not_served_when_the_refetch_fails():
    clock = Clock(datetime(2026, 10, 21, 16, tzinfo=UTC))
    outcomes = [respond(200, json=[item("CPI m/m", "2026-10-21T08:30:00-04:00")]), raise_(httpx.ReadTimeout("t"))]
    calls = Counter(lambda request: outcomes[calls.n - 1](request))
    live = ForexFactoryCalendar(transport=httpx.MockTransport(calls), clock=clock)
    assert len((await live.fetch_events()).events) == 1
    clock.advance(minutes=31)
    stale = await live.fetch_events()
    assert (stale.status, stale.events, stale.error) == (CalendarStatus.FAILED, (), "ReadTimeout")


def test_live_titles_map_to_kinds_press_conference_first():
    assert tier1_kind("FOMC Press Conference", "USD") is Tier1Kind.FOMC_PRESS_CONFERENCE
    assert tier1_kind("FOMC Statement", "USD") is Tier1Kind.FOMC_STATEMENT
    assert tier1_kind("Federal Funds Rate", "USD") is Tier1Kind.FOMC_STATEMENT
    assert tier1_kind("Non-Farm Employment Change", "USD") is Tier1Kind.EMPLOYMENT_SITUATION
    assert tier1_kind("Core CPI m/m", "USD") is Tier1Kind.CPI and tier1_kind("PPI m/m", "US") is Tier1Kind.PPI
    assert tier1_kind("FOMC Meeting Minutes", "USD") is None and tier1_kind("CPI m/m", "EUR") is None


def test_speeches_and_other_releases_have_no_kind():
    for title in (
        "FOMC Member Barr Speaks",
        "Fed Chair Powell Speaks",
        "Initial Jobless Claims",
        "Crude Oil Inventories",
    ):
        assert tier1_kind(title, "USD") is None
    assert tier1_kind("FOMC Economic Projections", "USD") is Tier1Kind.FOMC_STATEMENT
    assert tier1_kind("Unemployment Rate", "USD") is Tier1Kind.EMPLOYMENT_SITUATION


def test_the_live_feed_is_the_abstract_base_with_a_source_and_label():
    with pytest.raises(TypeError):
        BaseEconomicCalendar()  # type: ignore[abstract]
    assert (ForexFactoryCalendar.source, ForexFactoryCalendar.label) == ("forexfactory", "ForexFactory")


# --- union and statuses -----------------------------------------------------------------------


async def test_a_static_event_locks_out_while_the_live_feed_is_failed():
    check = await union(live_failed()).check(CPI_AT + timedelta(minutes=10), 60, 30)
    assert check.verified and check.live.status is CalendarStatus.FAILED
    assert check.lockout is not None
    assert (check.lockout.title, check.lockout.at, check.lockout.kind, check.lockout.sources) == (
        "CPI",
        CPI_AT,
        Tier1Kind.CPI,
        ("static",),
    )
    assert check.disagreements == ()  # no live evidence to disagree with


async def test_a_live_only_tier1_event_locks_out():
    emergency = datetime(2021, 3, 15, 16, tzinfo=UTC)  # an unscheduled FOMC action the static file cannot hold
    action = live_event("FOMC Statement", emergency, Tier1Kind.FOMC_STATEMENT)
    live = live_ok(action, coverage=(date(2021, 3, 15), date(2021, 3, 15)))
    check = await union(live).check(emergency - timedelta(minutes=30), 60, 30)
    assert check.lockout is not None
    assert (check.lockout.title, check.lockout.sources) == ("FOMC Statement", ("live",))
    assert check.disagreements == (
        CalendarDisagreement("live_only", Tier1Kind.FOMC_STATEMENT, emergency, "FOMC Statement"),
    )


async def test_a_live_event_without_a_kind_never_locks_out():
    retail = datetime(2021, 3, 16, 12, 30, tzinfo=UTC)
    check = await union(live_ok(live_event("Retail Sales m/m", retail, None), coverage=None)).check(retail, 60, 30)
    assert check.lockout is None and check.disagreements == ()


async def test_matching_is_same_kind_within_five_minutes():
    cpi_day = (date(2021, 3, 10), date(2021, 3, 10))
    near_cpi = live_event("CPI m/m", CPI_AT + timedelta(minutes=4), Tier1Kind.CPI)
    near = await union(live_ok(near_cpi, coverage=cpi_day)).check(CPI_AT, 60, 30)
    assert near.lockout is not None and near.lockout.sources == ("static", "live")
    assert near.disagreements == ()
    assert timedelta(minutes=5) == MATCH_TOLERANCE

    off = CPI_AT + timedelta(minutes=6)
    far = await union(live_ok(live_event("CPI m/m", off, Tier1Kind.CPI), coverage=cpi_day)).check(CPI_AT, 60, 30)
    assert far.lockout is not None and far.lockout.sources == ("static",)
    assert far.disagreements == (
        CalendarDisagreement("static_only", Tier1Kind.CPI, CPI_AT, "CPI"),
        CalendarDisagreement("live_only", Tier1Kind.CPI, off, "CPI m/m"),
    )

    other_kind = await union(live_ok(live_event("PPI m/m", CPI_AT, Tier1Kind.PPI), coverage=cpi_day)).check(
        CPI_AT, 60, 30
    )
    assert [d.side for d in other_kind.disagreements] == ["static_only", "live_only"]


async def test_static_only_is_judged_inside_the_live_coverage():
    # An empty live week covering 2021-03-07..13: CPI and PPI have no live match; the 03-05 jobs report and the
    # 03-17 FOMC pair fall outside the coverage and are not disagreements.
    check = await union(live_ok()).check(datetime(2021, 3, 1, tzinfo=UTC), 60, 30)
    assert check.disagreements == (
        CalendarDisagreement("static_only", Tier1Kind.CPI, CPI_AT, "CPI"),
        CalendarDisagreement("static_only", Tier1Kind.PPI, datetime(2021, 3, 12, 13, 30, tzinfo=UTC), "PPI"),
    )
    assert (await union(live_ok(coverage=None)).check(datetime(2021, 3, 1, tzinfo=UTC), 60, 30)).disagreements == ()


async def test_disagreements_are_bounded_to_ten():
    base = datetime(2021, 3, 8, 15, tzinfo=UTC)
    extra = [live_event(f"CPI {n}", base + timedelta(hours=n), Tier1Kind.CPI) for n in range(12)]
    check = await union(live_ok(*extra)).check(datetime(2021, 3, 1, tzinfo=UTC), 60, 30)
    assert len(check.disagreements) == 10
    assert [d.at for d in check.disagreements] == sorted(d.at for d in check.disagreements)
    assert len(check.journal_block()["disagreements"]) == 10


async def test_a_tentative_static_event_locks_out_like_any_other(tmp_path):
    at = "2027-01-27T14:00:00-05:00"
    path = write_calendar(tmp_path, [{"kind": "fomc_statement", "at": at, "source_url": FED, "tentative": True}])
    static = StaticTier1Calendar.load(path, None)
    check = await union(live_failed(), static).check(datetime(2027, 1, 27, 19, 10, tzinfo=UTC), 60, 30)
    assert check.lockout is not None and check.lockout.title == "FOMC Statement"


async def test_upcoming_merges_static_and_unmatched_live_events_for_24_hours():
    now = datetime(2021, 3, 9, 14, tzinfo=UTC)
    live_ppi = live_event("PPI m/m", datetime(2021, 3, 10, 12, tzinfo=UTC), Tier1Kind.PPI)
    check = await union(live_ok(live_ppi)).check(now, 60, 30)
    assert [e.title for e in check.upcoming] == ["PPI m/m", "CPI"]
    assert check.lockout is None


async def test_expired_static_under_open_policy_is_unverified_with_no_static_lockout():
    # The fixture calendar's CPI, judged once its validity has lapsed: its events are no longer trusted.
    expired = dataclasses.replace(StaticTier1Calendar.load(FIXTURE, None), valid_through=date(2021, 3, 9))
    check = await union(live_ok(coverage=None), expired).check(CPI_AT, 60, 30)
    assert check.static.status is CalendarStatus.EXPIRED and check.verified is False
    assert check.lockout is None
    assert check.lockout_rejection(RiskLimits.from_config(AppConfig())) is None
    assert "UNVERIFIED" in check.prompt_text()
    assert check.macro_note() == "static tier-1 calendar expired; live feed ok"


async def test_an_unverified_static_still_locks_out_on_a_live_event():
    offline = StaticTier1Calendar("us-tier1-v1", None, False, "FileNotFoundError", None, None, ())
    check = await union(live_ok(live_event("CPI m/m", CPI_AT, Tier1Kind.CPI)), offline).check(CPI_AT, 60, 30)
    assert check.verified is False and check.static.status is CalendarStatus.INVALID
    assert check.lockout is not None and check.lockout.sources == ("live",)
    assert check.disagreements == ()  # only a verified static calendar can disagree
    limits = RiskLimits.from_config(AppConfig())
    assert check.lockout_rejection(limits).reason == "Macro event lockout: CPI m/m at 13:30 UTC."


async def test_journal_block_always_has_every_key():
    for live in (live_ok(), live_failed()):
        block = (await union(live).check(CPI_AT, 60, 30)).journal_block()
        assert set(block) == {
            "verified",
            "unknown_policy",
            "match_tolerance_minutes",
            "static",
            "live",
            "lockout",
            "disagreements",
        }
        assert set(block["static"]) == {
            "calendar_id",
            "sha256",
            "pinned",
            "status",
            "valid_through",
            "days_remaining",
            "error",
        }
        assert set(block["live"]) == {
            "source",
            "status",
            "fetched_at",
            "error",
            "http_status",
            "tier1_events",
            "coverage",
        }
        assert block["match_tolerance_minutes"] == 5 and block["unknown_policy"] == "open"
        json.dumps(block)  # journal-ready


async def test_journal_block_values():
    check = await union(live_ok(live_event("CPI m/m", CPI_AT, Tier1Kind.CPI))).check(CPI_AT, 60, 30)
    block = check.journal_block()
    assert block["verified"] is True
    assert block["static"] == {
        "calendar_id": "us-tier1-test-v1",
        "sha256": check.static.sha256,
        "pinned": False,
        "status": "ok",
        "valid_through": "2099-12-31",
        "days_remaining": (date(2099, 12, 31) - date(2021, 3, 10)).days,
        "error": None,
    }
    assert block["live"] == {
        "source": "fixed",
        "status": "ok",
        "fetched_at": CPI_AT.isoformat(),
        "error": None,
        "http_status": None,
        "tier1_events": 1,
        "coverage": ["2021-03-07", "2021-03-13"],
    }
    assert block["lockout"] == {
        "title": "CPI",
        "at": CPI_AT.isoformat(),
        "kind": "cpi",
        "sources": ["static", "live"],
    }
    failed = (await union(live_failed()).check(datetime(2021, 3, 1, tzinfo=UTC), 60, 30)).journal_block()
    assert (failed["live"]["status"], failed["live"]["error"], failed["lockout"]) == ("failed", "ReadTimeout", None)


async def test_from_config_reads_the_configured_file_and_policy(app_config):
    app_config.risk.macro_calendar_unknown = "closed"
    calendar = UnionTier1Calendar.from_config(app_config, live=live_failed())
    assert calendar.static.calendar_id == "us-tier1-test-v1" and calendar.unknown_policy == "closed"
    assert isinstance(UnionTier1Calendar.from_config(app_config).live, ForexFactoryCalendar)


async def test_offline_union_reads_nothing_and_is_unverified():
    calendar = UnionTier1Calendar.offline()
    state = calendar.static.state(CPI_AT)
    assert (state.status, state.error, calendar.static.events) == (CalendarStatus.INVALID, "Offline", ())
    live = await calendar.live.fetch_events()
    assert (live.status, live.error, live.events) == (CalendarStatus.FAILED, "Offline", ())
