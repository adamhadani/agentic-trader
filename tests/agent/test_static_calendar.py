"""The static tier-1 calendar: loader fails closed to `invalid`, NY-date status, readiness detail."""

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic_trader.agent.calendar import (
    StaticTier1Calendar,
    Tier1Kind,
    macro_calendar_readiness,
)
from agentic_trader.constants import CalendarStatus


FIXTURE = Path(__file__).parent.parent / "fixtures/calendar/us-tier1-test-v1.json"
NOW = datetime(2026, 10, 21, 16, 0, tzinfo=UTC)
CPI = {
    "kind": "cpi",
    "at": "2026-11-10T08:30:00-05:00",
    "source_url": "https://www.bls.gov/schedule/news_release/cpi.htm",
}
PPI = {
    "kind": "ppi",
    "at": "2026-11-13T08:30:00-05:00",
    "source_url": "https://www.bls.gov/schedule/news_release/ppi.htm",
}


def write_calendar(
    tmp_path: Path,
    events: list[dict],
    *,
    calendar_id: str = "us-tier1-v1",
    valid_from: str = "2026-10-21",
    valid_through: str = "2027-12-03",
    series_last_listed: dict[str, str] | None = None,
    **overrides,
) -> Path:
    document = {
        "schema_version": 1,
        "calendar_id": calendar_id,
        "timezone": "America/New_York",
        "valid_from": valid_from,
        "valid_through": valid_through,
        "retrieved_on": "2026-10-21",
        "series_last_listed": series_last_listed or {kind.value: valid_through for kind in Tier1Kind},
        "events": events,
        **overrides,
    }
    path = tmp_path / f"{calendar_id}.json"
    path.write_text(json.dumps(document, indent=2) + "\n")
    return path


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _broken(tmp_path: Path, case: str) -> tuple[Path, str | None]:
    """The (path, expected digest) a `load` of each kind of broken calendar receives."""
    events: list[dict] = [CPI]
    if case == "missing":
        return tmp_path / "us-tier1-v1.json", None
    if case == "digest":
        return write_calendar(tmp_path, events), "0" * 64
    if case == "id_not_stem":
        return write_calendar(tmp_path, events).rename(tmp_path / "us-tier1-v9.json"), None
    kwargs: dict = {}
    match case:
        case "unknown_kind":
            events = [{**CPI, "kind": "gdp"}]
        case "extra_key":
            events = [{**CPI, "note": "unexpected"}]
        case "naive_at":
            events = [{**CPI, "at": "2026-11-10T08:30:00"}]
        case "edt_offset_on_est_date":
            events = [{**CPI, "at": "2026-11-10T08:30:00-04:00"}]
        case "utc_offset":
            events = [{**CPI, "at": "2026-11-10T13:30:00Z"}]
        case "outside_validity":
            events = [{**CPI, "at": "2026-10-20T08:30:00-04:00"}]
        case "unsorted":
            events = [PPI, CPI]
        case "duplicate":
            events = [CPI, CPI]
        case "http_scheme":
            events = [{**CPI, "source_url": "http://www.bls.gov/schedule/news_release/cpi.htm"}]
        case "other_host":
            events = [{**CPI, "source_url": "https://www.forexfactory.com/calendar"}]
        case "from_after_through":
            kwargs = {"valid_from": "2027-12-04", "valid_through": "2027-12-03"}
        case "through_beyond_series":
            kwargs = {"series_last_listed": {kind.value: "2027-12-02" for kind in Tier1Kind}}
        case "through_beyond_shortest_series":
            # Only one series ends before `valid_through`: the rule is the shortest series, not the longest.
            kwargs = {
                "series_last_listed": {kind.value: "2027-12-31" for kind in Tier1Kind}
                | {Tier1Kind.EMPLOYMENT_SITUATION.value: "2027-12-02"}
            }
        case "missing_series_key":
            kwargs = {"series_last_listed": {kind.value: "2027-12-03" for kind in Tier1Kind if kind != Tier1Kind.CPI}}
        case "schema_version":
            kwargs = {"schema_version": 2}
        case "timezone":
            kwargs = {"timezone": "UTC"}
        case _:
            raise AssertionError(case)
    return write_calendar(tmp_path, events, **kwargs), None


def test_a_valid_file_loads_ok_with_static_events(tmp_path):
    path = write_calendar(tmp_path, [CPI])
    cal = StaticTier1Calendar.load(path, hashlib.sha256(path.read_bytes()).hexdigest())
    state = cal.state(NOW)
    assert (state.status, state.pinned, state.error) == (CalendarStatus.OK, True, None)
    [event] = cal.events
    assert (event.title, event.kind, event.source) == ("CPI", Tier1Kind.CPI, "static:us-tier1-v1")
    assert event.timestamp == datetime(2026, 11, 10, 13, 30, tzinfo=UTC)


def test_every_static_event_is_a_high_impact_usd_event_with_its_tier1_title(tmp_path):
    events = [{**CPI, "kind": kind.value, "at": f"2026-11-{10 + i}T08:30:00-05:00"} for i, kind in enumerate(Tier1Kind)]
    cal = StaticTier1Calendar.load(write_calendar(tmp_path, events), None)
    assert [(e.title, e.country, e.impact) for e in cal.events] == [
        ("FOMC Statement", "USD", "High"),
        ("FOMC Press Conference", "USD", "High"),
        ("CPI", "USD", "High"),
        ("PPI", "USD", "High"),
        ("Employment Situation", "USD", "High"),
    ]
    assert all(e.timestamp.tzinfo is UTC for e in cal.events)


def test_the_validity_range_and_remaining_days_come_from_the_file(tmp_path):
    state = StaticTier1Calendar.load(write_calendar(tmp_path, [CPI]), None).state(NOW)
    assert (state.calendar_id, str(state.valid_from), str(state.valid_through)) == (
        "us-tier1-v1",
        "2026-10-21",
        "2027-12-03",
    )
    assert state.days_remaining == 408


def test_the_synthetic_fixture_calendar_is_valid_and_spans_both_offsets():
    cal = StaticTier1Calendar.load(FIXTURE, None)
    assert cal.state(NOW).status is CalendarStatus.OK
    assert [(e.kind, e.timestamp) for e in cal.events] == [
        (Tier1Kind.EMPLOYMENT_SITUATION, datetime(2021, 3, 5, 13, 30, tzinfo=UTC)),  # EST
        (Tier1Kind.CPI, datetime(2021, 3, 10, 13, 30, tzinfo=UTC)),
        (Tier1Kind.PPI, datetime(2021, 3, 12, 13, 30, tzinfo=UTC)),
        (Tier1Kind.FOMC_STATEMENT, datetime(2021, 3, 17, 18, 0, tzinfo=UTC)),  # EDT
        (Tier1Kind.FOMC_PRESS_CONFERENCE, datetime(2021, 3, 17, 18, 30, tzinfo=UTC)),
    ]


def test_an_unpinned_load_records_the_digest(tmp_path):
    path = write_calendar(tmp_path, [CPI])
    state = StaticTier1Calendar.load(path, None).state(NOW)
    assert state.pinned is False and state.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("case", "error"),
    [
        ("digest", "DigestMismatch"),
        ("missing", "FileNotFoundError"),
        ("unknown_kind", "ValidationError"),
        ("extra_key", "ValidationError"),
        ("naive_at", "ValidationError"),
        ("missing_series_key", "ValidationError"),
        ("schema_version", "ValidationError"),
        ("timezone", "ValidationError"),
        ("edt_offset_on_est_date", "WrongNewYorkOffset"),
        ("utc_offset", "WrongNewYorkOffset"),
        ("outside_validity", "EventOutsideValidity"),
        ("unsorted", "UnsortedEvents"),
        ("duplicate", "DuplicateEvent"),
        ("http_scheme", "UnofficialSource"),
        ("other_host", "UnofficialSource"),
        ("id_not_stem", "CalendarIdMismatch"),
        ("from_after_through", "InvalidValidityRange"),
        ("through_beyond_series", "ValidityBeyondSeries"),
        ("through_beyond_shortest_series", "ValidityBeyondSeries"),
    ],
)
def test_an_invalid_file_loads_as_invalid_with_its_type_name(tmp_path, caplog, case, error):
    cal = StaticTier1Calendar.load(*_broken(tmp_path, case))  # never raises
    state = cal.state(NOW)
    assert (state.status, state.error, cal.events) == (CalendarStatus.INVALID, error, ())
    assert [r.levelno for r in caplog.records if r.name == "agentic_trader.agent.calendar"] == [logging.ERROR]


def test_an_invalid_load_reports_no_validity_range_and_names_no_message(tmp_path, caplog):
    cal = StaticTier1Calendar.load(*_broken(tmp_path, "outside_validity"))
    state = cal.state(NOW)
    assert (state.valid_from, state.valid_through, state.days_remaining) == (None, None, None)
    [record] = [r for r in caplog.records if r.name == "agentic_trader.agent.calendar"]
    assert record.__dict__["error"] == "EventOutsideValidity"
    assert "2026-10-20" not in record.getMessage()


def test_a_digest_mismatch_still_reports_the_digest_of_the_bytes_read(tmp_path):
    path, expected = _broken(tmp_path, "digest")
    state = StaticTier1Calendar.load(path, expected).state(NOW)
    assert (state.pinned, state.sha256) == (True, _digest(path))


def test_a_missing_file_has_no_digest_and_is_named_by_its_stem(tmp_path):
    state = StaticTier1Calendar.load(tmp_path / "us-tier1-v1.json", "a" * 64).state(NOW)
    assert (state.calendar_id, state.sha256, state.pinned) == ("us-tier1-v1", None, True)


def test_two_events_may_share_an_instant_with_different_kinds(tmp_path):
    statement = {**CPI, "kind": "fomc_statement", "at": "2026-12-09T14:00:00-05:00"}
    same_instant = {**statement, "kind": "fomc_press_conference"}
    # Sorted by (at, kind): at a shared instant the kind's string order decides.
    cal = StaticTier1Calendar.load(write_calendar(tmp_path, [same_instant, statement]), None)
    assert cal.state(NOW).status is CalendarStatus.OK and len(cal.events) == 2


def test_status_flips_at_new_york_midnight_without_reload(tmp_path):  # Review Focus 1
    cal = StaticTier1Calendar.load(write_calendar(tmp_path, [CPI]), None)
    assert cal.state(datetime(2027, 12, 4, 4, 59, tzinfo=UTC)).status is CalendarStatus.OK  # 23:59 NY, 12-03
    assert cal.state(datetime(2027, 12, 4, 5, 1, tzinfo=UTC)).status is CalendarStatus.EXPIRED  # 00:01 NY, 12-04
    assert cal.state(datetime(2026, 10, 20, 16, 0, tzinfo=UTC)).status is CalendarStatus.EXPIRED  # before valid_from


@pytest.mark.parametrize(
    ("now", "ready", "detail"),
    [
        (datetime(2027, 11, 3, 16, tzinfo=UTC), True, "us-tier1-v1 valid through 2027-12-03 (30 days)"),
        (datetime(2027, 11, 4, 16, tzinfo=UTC), False, "us-tier1-v1 expires 2027-12-03 (29 days); publish v2"),
        (
            datetime(2027, 12, 5, 16, tzinfo=UTC),
            False,
            "us-tier1-v1 expired 2027-12-03; lockout unverified (policy open)",
        ),
        (
            datetime(2026, 10, 20, 16, tzinfo=UTC),
            False,
            "us-tier1-v1 not valid until 2026-10-21; lockout unverified (policy open)",
        ),
    ],
)
def test_macro_calendar_readiness_detail(tmp_path, now, ready, detail):
    state = StaticTier1Calendar.load(write_calendar(tmp_path, [CPI]), None).state(now)
    check = macro_calendar_readiness(state, renewal_days=30, unknown_policy="open")
    assert (check["ready"], check["detail"]) == (ready, detail)


def test_the_readiness_check_carries_status_and_days_remaining(tmp_path):
    cal = StaticTier1Calendar.load(write_calendar(tmp_path, [CPI]), None)
    check = macro_calendar_readiness(
        cal.state(datetime(2027, 11, 4, 16, tzinfo=UTC)), renewal_days=30, unknown_policy="open"
    )
    assert (check["status"], check["days_remaining"]) == ("ok", 29)
    expired = macro_calendar_readiness(
        cal.state(datetime(2027, 12, 5, 16, tzinfo=UTC)), renewal_days=30, unknown_policy="closed"
    )
    assert (expired["status"], expired["days_remaining"]) == ("expired", -2)
    assert expired["detail"].endswith("(policy closed)")


def test_the_renewal_window_is_the_configured_number_of_days(tmp_path):
    state = StaticTier1Calendar.load(write_calendar(tmp_path, [CPI]), None).state(datetime(2027, 11, 4, 16, tzinfo=UTC))
    assert macro_calendar_readiness(state, renewal_days=29, unknown_policy="open")["ready"] is True
    assert macro_calendar_readiness(state, renewal_days=30, unknown_policy="open")["ready"] is False


def test_an_invalid_calendar_is_not_ready(tmp_path):
    path = write_calendar(tmp_path, [CPI])
    state = StaticTier1Calendar.load(path, "0" * 64).state(NOW)
    assert (
        macro_calendar_readiness(state, renewal_days=30, unknown_policy="open")["detail"] == "invalid: DigestMismatch"
    )
    assert macro_calendar_readiness(state, renewal_days=30, unknown_policy="open")["ready"] is False
