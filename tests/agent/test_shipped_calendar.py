"""The shipped tier-1 calendar (config/calendar/us-tier1-v1.json): the only tests that read the real file.

None of them reads the clock; `now` is injected so they cannot age out.
"""

import hashlib
from collections import Counter
from datetime import UTC, date, datetime, time
from urllib.parse import urlsplit

import yaml

from agentic_trader.agent.calendar import (
    OFFICIAL_HOSTS,
    BaseEconomicCalendar,
    CalendarFetch,
    StaticTier1Calendar,
    Tier1CalendarFile,
    Tier1EventRecord,
    Tier1Kind,
    UnionTier1Calendar,
)
from agentic_trader.config import WORKSPACE_ROOT, load_config
from agentic_trader.constants import CalendarStatus
from agentic_trader.market.session import ET_TZ
from agentic_trader.risk import RiskLimits


SHIPPED = WORKSPACE_ROOT / "config/calendar/us-tier1-v1.json"
BLS_KINDS = (Tier1Kind.CPI, Tier1Kind.PPI, Tier1Kind.EMPLOYMENT_SITUATION)
FOMC_KINDS = (Tier1Kind.FOMC_STATEMENT, Tier1Kind.FOMC_PRESS_CONFERENCE)


def test_config_pins_the_shipped_calendar():
    risk = yaml.safe_load((WORKSPACE_ROOT / "config/config.yaml").read_text())["risk"]
    assert risk["macro_calendar_path"] == "config/calendar/us-tier1-v1.json"
    assert risk["macro_calendar_sha256"] == hashlib.sha256(SHIPPED.read_bytes()).hexdigest()


def test_shipped_calendar_loads_ok_on_its_first_day_and_counts_per_kind():
    cal = StaticTier1Calendar.load(SHIPPED, hashlib.sha256(SHIPPED.read_bytes()).hexdigest())
    assert cal.state(datetime(2026, 10, 21, 16, tzinfo=UTC)).status is CalendarStatus.OK
    assert Counter(e.kind for e in cal.events) == {
        Tier1Kind.FOMC_STATEMENT: 9,
        Tier1Kind.FOMC_PRESS_CONFERENCE: 9,
        Tier1Kind.CPI: 13,
        Tier1Kind.PPI: 13,
        Tier1Kind.EMPLOYMENT_SITUATION: 14,
    }


def test_valid_through_is_the_shortest_series_horizon():
    doc = Tier1CalendarFile.model_validate_json(SHIPPED.read_bytes())
    assert doc.valid_from == date(2026, 10, 21) and doc.valid_through == date(2027, 12, 3)
    assert doc.valid_through == min(doc.series_last_listed.values())


FOMC_DATES = [
    date(2026, 10, 28),
    date(2026, 12, 9),
    date(2027, 1, 27),
    date(2027, 3, 17),
    date(2027, 4, 28),
    date(2027, 6, 9),
    date(2027, 7, 28),
    date(2027, 9, 15),
    date(2027, 10, 27),
]


def _records(*kinds: Tier1Kind) -> list[Tier1EventRecord]:
    doc = Tier1CalendarFile.model_validate_json(SHIPPED.read_bytes())
    return [r for r in doc.events if r.kind in kinds]


def _new_york(records: list[Tier1EventRecord]) -> list[tuple[date, time]]:
    return [(r.at.astimezone(ET_TZ).date(), r.at.astimezone(ET_TZ).time()) for r in records]


def test_every_fomc_statement_is_1400_ny_with_a_1430_press_conference_the_same_date():
    assert _new_york(_records(Tier1Kind.FOMC_STATEMENT)) == [(d, time(14, 0)) for d in FOMC_DATES]
    assert _new_york(_records(Tier1Kind.FOMC_PRESS_CONFERENCE)) == [(d, time(14, 30)) for d in FOMC_DATES]


def test_every_bls_release_is_0830_ny():
    records = _records(*BLS_KINDS)
    assert len(records) == 40
    assert {t for _, t in _new_york(records)} == {time(8, 30)}


def test_tentative_marks_exactly_the_2027_fomc_events():
    doc = Tier1CalendarFile.model_validate_json(SHIPPED.read_bytes())
    tentative = {(r.kind, r.at.astimezone(ET_TZ).date()) for r in doc.events if r.tentative}
    assert tentative == {(kind, d) for kind in FOMC_KINDS for d in FOMC_DATES[2:]}
    assert len(tentative) == 14


def test_every_source_is_an_official_schedule_page_for_its_series():
    for record in _records(*Tier1Kind):
        url = urlsplit(record.source_url)
        assert (url.scheme, url.hostname) in {("https", host) for host in OFFICIAL_HOSTS}
        if record.kind in BLS_KINDS:
            page = {Tier1Kind.CPI: "cpi", Tier1Kind.PPI: "ppi", Tier1Kind.EMPLOYMENT_SITUATION: "empsit"}[record.kind]
            assert record.source_url == f"https://www.bls.gov/schedule/news_release/{page}.htm"
        else:
            assert url.hostname == "www.federalreserve.gov"


class OkEmptyFeed(BaseEconomicCalendar):
    """A live feed that answers ok with no events: the static calendar alone decides."""

    source = "ok-empty"
    label = "OkEmpty"

    async def fetch_events(self, force_refresh: bool = False) -> CalendarFetch:
        return CalendarFetch(CalendarStatus.OK)


async def test_fomc_2026_10_28_acceptance():
    calendar = UnionTier1Calendar(
        StaticTier1Calendar.load(SHIPPED, hashlib.sha256(SHIPPED.read_bytes()).hexdigest()),
        OkEmptyFeed(),
        unknown_policy="open",
    )
    limits = RiskLimits.from_config(load_config())

    async def check(hour: int, minute: int):
        now = datetime(2026, 10, 28, hour, minute, tzinfo=ET_TZ).astimezone(UTC)
        return await calendar.check(now, limits.lockout_pre_minutes, limits.lockout_post_minutes)

    afternoon = await check(14, 35)
    assert afternoon.now == datetime(2026, 10, 28, 18, 35, tzinfo=UTC)
    assert afternoon.verified and afternoon.lockout is not None
    assert afternoon.lockout.title == "FOMC Press Conference"
    morning = await check(10, 35)
    assert morning.lockout is None and morning.lockout_rejection(limits) is None
    tap = (await check(14, 50)).lockout_rejection(limits)
    assert tap is not None and tap.reason == "Macro event lockout: FOMC Press Conference at 18:30 UTC."
