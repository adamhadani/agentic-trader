"""The journal walk and bar source shared by `copilot cards outcomes` and the daemon."""

from datetime import UTC, datetime, timedelta

import agentic_trader.cli.commands.cards as cards_module
from agentic_trader.execution.durable import EventKind
from agentic_trader.research.setups import sources


async def _append(db, stream, kind, payload, key):
    async with db.session_factory() as session, session.begin():
        await db.workflows.lock(session)
        await db.workflows.append(session, stream=stream, kind=kind, payload=payload, key=key)


async def test_scan_ranked_events_walks_each_et_date_and_keeps_only_ranked_events(temp_db):
    await _append(
        temp_db, "scan/2026-03-02", EventKind.SCAN_CANDIDATES_RANKED, {"scan_id": "a"}, "scan_candidates_ranked/a"
    )
    await _append(temp_db, "scan/2026-03-02", EventKind.PEAD_DECISION, {"scan_id": "p"}, "pead_decision/p")
    await _append(
        temp_db, "scan/2026-02-01", EventKind.SCAN_CANDIDATES_RANKED, {"scan_id": "old"}, "scan_candidates_ranked/old"
    )
    # 2026-03-03 15:00 UTC is 10:00 New York: two ET dates back are 03-03 and 03-02.
    events = await sources.scan_ranked_events(temp_db, 2, now=datetime(2026, 3, 3, 15, 0, tzinfo=UTC))
    assert [event["payload"]["scan_id"] for event in events] == ["a"]


def test_the_cli_uses_the_shared_helpers():
    assert cards_module.build_bar_source is sources.build_bar_source
    assert cards_module.build_daily_bar_source is sources.build_daily_bar_source
    assert cards_module.scan_ranked_events is sources.scan_ranked_events
    assert cards_module.DelayedBars is sources.DelayedBars
    assert cards_module.SIP_HISTORY_DELAY is sources.SIP_HISTORY_DELAY
    assert not hasattr(cards_module, "_scan_ranked_events") and not hasattr(cards_module, "_DelayedBars")


class _Recording:
    def __init__(self):
        self.calls: list[tuple] = []

    def fetch_bars(self, symbol, timeframe, start, end, *, adjustment):
        self.calls.append((symbol, timeframe, start, end, adjustment))
        return symbol


def test_delayed_bars_clamp_every_read_to_their_bound():
    inner = _Recording()
    through = datetime(2026, 3, 3, 14, 44, tzinfo=UTC)
    start = datetime(2025, 8, 15, tzinfo=UTC)
    delayed = sources.DelayedBars(inner, through)
    assert delayed.fetch_bars("AAPL", "1d", start, through + timedelta(minutes=16), adjustment="all") == "AAPL"
    delayed.fetch_bars("SPY", "1h", start, through - timedelta(hours=1), adjustment="raw")
    assert inner.calls == [
        ("AAPL", "1d", start, through, "all"),  # a later end is clamped to the bound
        ("SPY", "1h", start, through - timedelta(hours=1), "raw"),  # an earlier one is kept
    ]


def test_the_daily_source_is_sip_ending_sixteen_minutes_before_now(monkeypatch, app_config):
    inner, feeds = _Recording(), []

    def build(config, *, feed=None):
        feeds.append(feed)
        return inner

    monkeypatch.setattr(sources, "build_bar_source", build)
    now = datetime(2026, 3, 3, 13, 31, tzinfo=UTC)
    daily = sources.build_daily_bar_source(app_config, now=now)
    assert isinstance(daily, sources.DelayedBars) and daily.provider is inner and feeds == ["sip"]
    assert timedelta(minutes=16) == sources.SIP_HISTORY_DELAY and daily.through == now - sources.SIP_HISTORY_DELAY
