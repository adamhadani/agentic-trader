"""Version 5: one trading session of resting entry, identical in research and live."""

import itertools
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from agentic_trader.execution.lifetime_policy import (
    DAILY_ENTRY_LIFETIME_SECONDS,
    TRADE_LIFETIME_VERSION_INDEPENDENT,
    TradeLifetimePolicy,
    daily_entry_lifetime,
)
from agentic_trader.market.bars import FixedDailyClockPolicy
from agentic_trader.research.alpha.models import DAILY_SESSION_SEMANTICS_VERSION, AlphaDefinition
from agentic_trader.research.alpha.simulation import BracketIntent, simulate_execution
from agentic_trader.research.alpha.strategy import (
    AlphaExecutionPolicy,
    TimedAlphaExecutionPolicy,
    session_entry_policy,
)


NEW_YORK = ZoneInfo("America/New_York")

PINNED_V2_VERSION_ID = "988aac219294424dc023d9a6a557c7e3ae65a274725917b7b3f49e960d0f27dd"


def daily(**changes):
    fields = {
        "alpha_id": "alpha_v5",
        "name": "V5",
        "expression": "returns",
        "timeframe": "1d",
        "semantics_version": DAILY_SESSION_SEMANTICS_VERSION,
        "execution": session_entry_policy(),
    }
    fields.update(changes)
    return AlphaDefinition(**fields)


def test_the_constant_and_the_only_legal_lifetime():
    assert DAILY_ENTRY_LIFETIME_SECONDS == 57_600 and DAILY_SESSION_SEMANTICS_VERSION == 5
    assert daily_entry_lifetime() == TradeLifetimePolicy(
        resting_seconds=57_600, holding_seconds=None, version=TRADE_LIFETIME_VERSION_INDEPENDENT
    )
    policy = session_entry_policy(AlphaExecutionPolicy(stop_atr=2.0))
    assert isinstance(policy, TimedAlphaExecutionPolicy)
    assert policy.stop_atr == 2.0 and policy.lifetime == daily_entry_lifetime()


def test_session_entry_policy_replaces_a_timed_base_lifetime_and_pins_the_default_document():
    # Rebuilding through a shallow field copy (not to_dict()/asdict(), which recurses
    # into nested dataclasses) must still be byte-identical to the old implementation.
    pinned = TimedAlphaExecutionPolicy(**AlphaExecutionPolicy().to_dict(), lifetime=daily_entry_lifetime())
    assert session_entry_policy().to_dict() == pinned.to_dict()

    other_lifetime = TradeLifetimePolicy(57_600, 57_600)
    timed_base = TimedAlphaExecutionPolicy(stop_atr=3.0, reward_risk=4.0, lifetime=other_lifetime)
    result = session_entry_policy(timed_base)
    assert result.lifetime == daily_entry_lifetime() and result.lifetime != other_lifetime
    assert result.stop_atr == 3.0 and result.reward_risk == 4.0


def test_version_five_round_trips_with_any_feed_and_a_distinct_identity():
    for feed in ("alpaca:iex", "alpaca:sip", "yfinance", "synthetic", "unverified"):
        definition = daily(data_feed=feed)
        assert AlphaDefinition.from_dict(definition.to_dict()) == definition
        assert definition.to_dict()["clock"] is None
        assert definition.to_dict()["execution"]["lifetime"]["resting_seconds"] == 57_600
    gtc = AlphaDefinition("alpha_v5", "V5", "returns", timeframe="1d")
    assert daily().version_id != gtc.version_id


@pytest.mark.parametrize(
    "changes",
    [
        {"timeframe": "4h"},
        {"clock": FixedDailyClockPolicy()},
        {"execution": AlphaExecutionPolicy()},
        {"execution": TimedAlphaExecutionPolicy(lifetime=TradeLifetimePolicy(86_400, None, version="elapsed_utc_v2"))},
        {
            "execution": TimedAlphaExecutionPolicy(
                lifetime=TradeLifetimePolicy(57_600, 86_400, version="elapsed_utc_v2")
            )
        },
        {"execution": TimedAlphaExecutionPolicy(lifetime=TradeLifetimePolicy(57_600, 57_600))},
    ],
)
def test_every_other_version_five_combination_is_rejected(changes):
    with pytest.raises(ValueError, match="Version 5"):
        daily(**changes)


def test_version_two_still_cannot_carry_a_lifetime_and_its_identity_is_unchanged():
    with pytest.raises(ValueError, match="Timed execution"):
        AlphaDefinition("timed", "Timed", "returns", timeframe="1d", execution=session_entry_policy())
    plain = AlphaDefinition("alpha_pin", "Pin", "delta(close,3)", timeframe="1d", eligible_symbols=("SPY",))
    # Pinned before this change on the same commit; a new AlphaDefinition field would move it.
    assert plain.version_id == PINNED_V2_VERSION_ID
    assert "clock" not in plain.to_dict()


def bars(labels, rows):
    frame = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=pd.DatetimeIndex(labels))
    frame["volume"] = 1_000
    return frame


def new_york_midnights(start, sessions):
    days, day = [], start
    while len(days) < sessions:
        if day.weekday() < 5:
            days.append(datetime.combine(day, time(0), NEW_YORK))
        day += timedelta(days=1)
    return days


def long_entry(limit):
    return BracketIntent(direction=1, limit=limit, stop=limit - 5, target=limit + 10, signal_timestamp="signal")


def test_an_unfilled_entry_expires_at_the_next_bar_before_its_fill_check():
    # Friday entry bar trades entirely above the limit; the following Monday would fill it.
    # Friday 6 March 2026 also precedes the US daylight-saving change on Sunday 8 March.
    labels = new_york_midnights(date(2026, 3, 5), 3)
    assert labels[1].weekday() == 4 and labels[2].weekday() == 0
    # Subtracting these datetime.datetime objects directly would take Python's
    # "same tzinfo attribute" naive-subtraction shortcut (both share the module
    # level NEW_YORK ZoneInfo instance), silently discarding the DST offset
    # change and yielding a wrong 72-hour answer; compare via POSIX timestamps
    # (as the research-deadline test below already does) for the true elapsed time.
    assert timedelta(seconds=labels[2].timestamp() - labels[1].timestamp()) == timedelta(hours=71)
    frame = bars(labels, [[101, 102, 100.5, 101], [101, 103, 100.6, 102], [102, 102, 99, 100]])
    timed = simulate_execution(frame, {1: long_entry(100.0)}, session_entry_policy(), trace=True)
    assert timed["total_trades"] == 0 and timed["pending_entry"] is False
    assert [e["kind"] for e in timed["events"]].count("entry_expired") == 1
    gtc = simulate_execution(frame, {1: long_entry(100.0)}, AlphaExecutionPolicy(), trace=True)
    assert [e["kind"] for e in gtc["events"]].count("entry_filled") == 1


def test_an_entry_filled_on_its_own_bar_is_unaffected_by_the_lifetime():
    labels = new_york_midnights(date(2026, 3, 9), 3)
    frame = bars(labels, [[101, 102, 100.5, 101], [101, 102, 99.5, 101], [101, 120, 100.5, 119]])
    timed = simulate_execution(frame, {1: long_entry(100.0)}, session_entry_policy(), trace=True)
    gtc = simulate_execution(frame, {1: long_entry(100.0)}, AlphaExecutionPolicy(), trace=True)
    assert timed["total_trades"] == gtc["total_trades"] == 1
    assert timed["total_return_pct"] == pytest.approx(gtc["total_return_pct"])
    assert not [e for e in timed["events"] if e["kind"] == "holding_expired"]


@pytest.mark.parametrize("day", [date(2026, 1, 14), date(2026, 7, 15)])
@pytest.mark.parametrize("submit", [time(9, 30), time(12, 0), time(15, 59, 59)])
def test_live_deadline_outlives_its_session_and_precedes_the_next_open(day, submit):
    submitted = datetime.combine(day, submit, NEW_YORK)
    next_open = datetime.combine(day + timedelta(days=1), time(9, 30), NEW_YORK)
    assert daily_entry_lifetime().entry_deadline(submitted) < next_open
    # An order submitted at any point in the session must survive to that
    # session's 16:00 close; a too-short lifetime would expire it intraday.
    assert daily_entry_lifetime().entry_deadline(submitted) > datetime.combine(day, time(16, 0), NEW_YORK)
    too_short = TradeLifetimePolicy(21_600, None, version="elapsed_utc_v2")
    # Documents why the constant is not smaller: 21,600 s (6 h) from a 09:30
    # submit would not even reach that same session's 16:00 close.
    assert too_short.entry_deadline(datetime.combine(day, time(9, 30), NEW_YORK)) <= datetime.combine(
        day, time(16, 0), NEW_YORK
    )
    one_day = TradeLifetimePolicy(86_400, None, version=TRADE_LIFETIME_VERSION_INDEPENDENT)
    # Documents why the constant is not 86,400: a live order would span two sessions.
    assert one_day.entry_deadline(submitted) >= next_open


def test_research_deadline_precedes_the_next_daily_label():
    labels = new_york_midnights(date(2026, 1, 12), 5)
    for label, following in itertools.pairwise(labels):
        assert daily_entry_lifetime().entry_deadline(label) < following
    assert np.all(np.diff([label.timestamp() for label in labels]) >= 86_400)
