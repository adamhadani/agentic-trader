"""Pure per-(strategy, timeframe, direction) card statistics from a labelled frame."""

import json
import math
from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest
from pydantic import AwareDatetime, BaseModel

import agentic_trader.research.setups.outcomes as outcomes_module
from agentic_trader.execution.durable import EventKind
from agentic_trader.research.setups.card_stats import (
    AGGREGATE,
    CARD_STATS_STREAM,
    LABELLER_PROTOCOL,
    CardStatsSnapshot,
    card_population,
    card_stats_key,
    complete_sessions_between,
    compute_card_stats,
)
from agentic_trader.research.setups.net_excess import COST_FLOOR_BPS
from agentic_trader.research.setups.outcomes import label_journaled
from agentic_trader.research.setups.sources import scan_ranked_events
from tests.execution.card_stats_fixtures import make_prior, prior_entry
from tests.research.setups.test_outcomes import (
    AAPL_BARS,
    DECIDED_AT,
    NOW,
    SPY_BARS,
    FakeBarSource,
    _candidate,
    _event,
)
from tests.research.setups.test_scan_supply import MIXED


COMPUTED_AT = datetime(2026, 10, 8, 12, 31, tzinfo=UTC)
KEY = ("TREND_PULLBACK", "4h", "LONG")


def _row(
    key=KEY,
    hit="stop",
    net=-0.5,
    decided_at="2026-08-03T14:35:00+00:00",
    r_cost=-1.1,
    holding=1,
    beta_reason=None,
    cost_reason=None,
    name_cost_bps=6.0,
):
    strategy, timeframe, direction = key
    return {
        **dict.fromkeys(outcomes_module._COLUMNS),
        "strategy": strategy,
        "timeframe": timeframe,
        "direction": direction,
        "hit": hit,
        "net_excess_r": net,
        "r_cost": r_cost,
        "holding_sessions": holding,
        "decided_at": pd.Timestamp(decided_at),
        "beta_reason": beta_reason,
        "cost_reason": cost_reason,
        "name_cost_bps": name_cost_bps,
    }


def _labelled(rows):
    return pd.DataFrame(rows, columns=list(outcomes_module._COLUMNS))


def _compute(frame, **overrides):
    kwargs = {
        "prior": make_prior(),
        "prior_sha256": "ab" * 32,
        "malformed": {},
        "window_start": date(2026, 4, 11),
        "window_end": date(2026, 10, 8),
        "feed": "iex",
        "max_hold_sessions": 20,
        "code_revision": "test-rev",
        "now": COMPUTED_AT,
        "events_considered": 12,
    }
    return compute_card_stats(frame, **{**kwargs, **overrides})


FIRST, MID, LAST = "2026-07-01T14:35:00+00:00", "2026-08-03T14:35:00+00:00", "2026-09-02T18:35:00+00:00"
TP = [
    _row(hit="target", net=1.7, r_cost=1.9, holding=2, decided_at=FIRST),
    _row(hit="target", net=1.7, r_cost=1.9, holding=2),
    _row(hit="target", net=1.7, r_cost=1.9, holding=2),
    *[_row(hit="stop", net=-1.3, r_cost=-1.1, holding=1)] * 5,
    _row(hit="timeout", net=0.5, r_cost=0.3, holding=20),
    _row(hit="timeout", net=-0.3, r_cost=-0.1, holding=20),
    _row(hit="immature", net=None, r_cost=None, holding=0),
    _row(hit="fetch_failed", net=None, r_cost=None, holding=0, decided_at=LAST),
]
SQ_KEY = ("SQUEEZE_BREAKOUT", "1h", "SHORT")
SQ = [_row(SQ_KEY, hit="stop", net=-1.2, r_cost=-1.05, holding=1)]


def _by_key(payload):
    return {(row["strategy"], row["timeframe"], row["direction"]): row for row in payload["keys"]}


def _edge(day: str) -> str:
    return f"{day}T14:35:00+00:00"


def test_rates_are_per_key_over_mature_rows_with_fetch_failures_excluded():
    keys = _by_key(_compute(_labelled([*TP, *SQ])))
    tp = keys[KEY]
    assert (tp["n_mature"], tp["n_immature"], tp["n_unresolved"], tp["n_young"], tp["n_fetch_failed"]) == (
        10,
        1,
        1,
        0,
        1,
    )
    assert tp["target_rate"] == pytest.approx(0.3)
    assert tp["stop_rate"] == pytest.approx(0.5)
    assert tp["timeout_rate"] == pytest.approx(0.2)
    assert tp["mean_r_cost"] == pytest.approx(0.04)  # (3*1.9 - 5*1.1 + 0.3 - 0.1) / 10
    assert tp["mean_timeout_r"] == pytest.approx(0.1)  # the timeouts' partial R
    assert tp["median_holding_sessions"] == pytest.approx(1.5)
    assert tp["live_mean"] == pytest.approx((3 * 1.7 - 5 * 1.3 + 0.5 - 0.3) / 10)
    assert tp["live_se"] is not None and tp["mean_name_cost_bps"] == pytest.approx(6.0)
    assert tp["first_decided_at"] == FIRST and tp["last_decided_at"] == MID  # mature rows only
    sq = keys[SQ_KEY]
    assert sq["n_mature"] == 1 and sq["stop_rate"] == 1.0 and sq["mean_timeout_r"] is None
    assert sq["live_se"] is None  # one row: no sample spread


def test_the_aggregate_key_covers_every_row_and_has_no_prior():
    agg = _by_key(_compute(_labelled([*TP, *SQ])))[(AGGREGATE, AGGREGATE, AGGREGATE)]
    assert (agg["n_mature"], agg["n_immature"], agg["n_fetch_failed"]) == (11, 1, 1)
    assert agg["target_rate"] == pytest.approx(3 / 11)
    assert agg["stop_rate"] == pytest.approx(6 / 11)
    assert agg["mean_r_cost"] == pytest.approx(-0.65 / 11)
    assert not {"posterior_mean", "prior_mean", "prior_source"} & agg.keys()  # validated, they default to None


def test_a_row_without_a_strategy_counts_only_in_the_aggregate():
    payload = _compute(_labelled([*SQ, _row((None, "1h", "LONG"))]), prior=make_prior(keys=()))
    keys = _by_key(payload)
    assert set(keys) == {SQ_KEY, (AGGREGATE, AGGREGATE, AGGREGATE)}
    assert keys[(AGGREGATE, AGGREGATE, AGGREGATE)]["n_mature"] == 2


def test_output_is_nan_free_and_a_key_without_finite_net_excess_has_no_mature_rows():
    payload = _compute(_labelled([_row(("X", "1h", "LONG"), net=float("nan")), *SQ]))
    json.dumps(payload, allow_nan=False)  # the journal encoder's own rule
    row = _by_key(payload)[("X", "1h", "LONG")]
    assert row["n_mature"] == 0 and row["n_no_beta"] == 1 and row["live_mean"] is None


@pytest.mark.parametrize(
    "field", ["target_rate", "mean_r_cost", "mean_timeout_r", "median_holding_sessions", "posterior_mean", "live_mean"]
)
@pytest.mark.parametrize("value", [float("nan"), float("inf"), "NaN", "-Infinity"])
def test_a_snapshot_with_a_non_finite_statistic_does_not_validate(field, value):
    payload = _compute(_labelled(TP))
    payload["keys"][0][field] = value
    with pytest.raises(ValueError, match="finite"):
        CardStatsSnapshot.model_validate(payload)


def test_a_key_with_only_unresolved_rows_has_no_rates():
    row = _by_key(_compute(_labelled([_row(("X", "1h", "LONG"), hit="immature", net=None)])))[("X", "1h", "LONG")]
    assert row["n_mature"] == 0 and row["n_immature"] == 1 and row["n_unresolved"] == 1
    assert row["target_rate"] is None and row["mean_r_cost"] is None and row["median_holding_sessions"] is None


def test_an_empty_frame_gives_the_prior_keys_aggregate_and_full_provenance():
    payload = _compute(pd.DataFrame(columns=list(outcomes_module._COLUMNS)))
    assert set(_by_key(payload)) == {KEY, (AGGREGATE, AGGREGATE, AGGREGATE)}
    assert payload["rows_labelled"] == 0 and payload["events_considered"] == 12 and payload["cohort_end"] is None
    assert payload["labeller_protocol"] == LABELLER_PROTOCOL == "setup-outcomes-v1"
    assert payload["unit"] == "net_excess_r_v1" and payload["cost_bps_per_side"] == COST_FLOOR_BPS
    assert payload["computed_at"] == COMPUTED_AT.isoformat()
    assert (payload["window_start"], payload["window_end"]) == ("2026-04-11", "2026-10-08")
    assert (payload["feed"], payload["max_hold_sessions"]) == ("iex", 20)
    assert payload["prior_sha256"] == "ab" * 32
    assert (payload["prior_window_start"], payload["prior_window_end"], payload["prior_feed"]) == (
        "2021-01-04",
        "2026-06-30",
        "sip",
    )
    snapshot = CardStatsSnapshot.model_validate(payload)
    assert snapshot.snapshot_key == card_stats_key(date(2026, 10, 8)) == "card_stats/net_excess_r_v1/2026-10-08"


def test_the_snapshot_reads_back_and_finds_a_key():
    snapshot = CardStatsSnapshot.model_validate(_compute(_labelled(TP)))
    assert snapshot.stats("TREND_PULLBACK", "4h", "LONG").n_mature == 10
    assert snapshot.stats("TREND_PULLBACK", "1h", "LONG") is None  # timeframes are separate keys
    assert snapshot.stats("TREND_PULLBACK", "4h", "SHORT") is None
    assert snapshot.computed_at.utcoffset() is not None
    assert snapshot.cohort_end == date.fromisoformat("2026-09-02")


def test_snapshot_key_has_unit():
    assert card_stats_key(date(2026, 10, 28)) == "card_stats/net_excess_r_v1/2026-10-28"


def _event_with(candidates, *, scan_id="s", decided_at=DECIDED_AT, trigger="suggestion_scan", scope="universe"):
    return _event(scan_id, decided_at, candidates, trigger=trigger, scope=scope)


def test_population_suggestion_only_and_dedup():
    am = datetime(2026, 3, 2, 15, 35, tzinfo=UTC)  # 10:35 New York
    pm = datetime(2026, 3, 2, 19, 35, tzinfo=UTC)  # 14:35
    events = [
        _event_with([_candidate(contract="AAPL")], scan_id="swing", trigger="swing_scan", decided_at=am),
        _event_with([_candidate(contract="AAPL"), _candidate(contract="SPY")], scan_id="pm", decided_at=pm),
        _event_with([_candidate(contract="AAPL")], scan_id="am", decided_at=am),
    ]
    population = card_population(events, excluded=frozenset({"SPY"}))
    [event] = population.events
    assert event["payload"]["scan_id"] == "am"  # the earliest decision of the session wins
    assert [c["contract"] for c in event["payload"]["candidates"]] == ["AAPL"]
    assert population.considered == 2 and population.malformed == {}
    assert events[1]["payload"]["candidates"][0]["contract"] == "AAPL" and len(events[1]["payload"]["candidates"]) == 2


def test_population_keeps_timeframes_apart():
    one_h = _candidate(contract="AAPL")
    four_h = {**one_h, "timeframe": "4h"}
    population = card_population([_event_with([one_h, four_h])], excluded=frozenset())
    assert [c["timeframe"] for c in population.events[0]["payload"]["candidates"]] == ["1h", "4h"]


def test_population_counts_malformed():
    flat = _candidate(contract="FLAT", entry=100.0, stop=100.0)
    no_direction = {**_candidate(contract="NODIR"), "direction": None}
    no_stop = {**_candidate(contract="NOSTOP"), "stop": float("nan")}
    no_target = {k: v for k, v in _candidate(contract="NOTGT").items() if k != "target"}
    inverted = _candidate(contract="INV", entry=100.0, stop=99.0, target=98.0)  # LONG target below entry
    good = _candidate(contract="GOOD")
    population = card_population(
        [_event_with([flat, no_direction, no_stop, no_target, inverted, good])], excluded=frozenset()
    )
    assert [c["contract"] for c in population.events[0]["payload"]["candidates"]] == ["GOOD"]
    assert population.malformed == {("BREAKOUT", "1h", "LONG"): 4, ("BREAKOUT", "1h", "?"): 1}


def test_population_counts_a_malformed_setup_once_per_session():
    am = datetime(2026, 3, 2, 15, 35, tzinfo=UTC)
    pm = datetime(2026, 3, 2, 19, 35, tzinfo=UTC)
    bad = _candidate(contract="FLAT", entry=100.0, stop=100.0)
    events = [
        _event_with([bad], scan_id="am", decided_at=am),
        _event_with([bad], scan_id="pm", decided_at=pm),
        _event_with([bad], scan_id="next", decided_at=am.replace(day=3)),
    ]
    assert card_population(events, excluded=frozenset()).malformed == {("BREAKOUT", "1h", "LONG"): 2}


def test_complete_sessions_between_skips_holidays():
    # Nov 23, 24, 25 and 27 are sessions; Nov 26 is Thanksgiving; Nov 21-22 and 28-29 are weekends.
    assert complete_sessions_between(date(2026, 11, 20), date(2026, 11, 30)) == 4
    assert complete_sessions_between(date(2026, 10, 8), date(2026, 10, 8)) == 0


def test_maturity_boundary():
    # Sessions strictly between Sep 8 and Oct 8: Sep 9 .. Oct 7 = 21 (Sep 7 is Labor Day); from Sep 9 it is 20.
    assert complete_sessions_between(date(2026, 9, 8), date(2026, 10, 8)) == 21
    assert complete_sessions_between(date(2026, 9, 9), date(2026, 10, 8)) == 20
    payload = _compute(_labelled([_row(decided_at=_edge("2026-09-08")), _row(decided_at=_edge("2026-09-09"))]))
    row = _by_key(payload)[KEY]
    assert (row["n_mature"], row["n_young"]) == (1, 1)
    assert payload["cohort_end"] == "2026-09-08"


def test_young_resolved_row_excluded():
    """Censoring regression: a stop hit three sessions ago is not evidence; it only counts as young."""
    young = _row(hit="stop", net=-1.3, r_cost=-1.1, decided_at=_edge("2026-10-05"))
    old = _row(hit="target", net=1.7, r_cost=1.9)
    row = _by_key(_compute(_labelled([young, old])))[KEY]
    assert (row["n_mature"], row["n_young"], row["n_immature"]) == (1, 1, 1)
    assert row["stop_rate"] == 0.0 and row["target_rate"] == 1.0 and row["live_mean"] == pytest.approx(1.7)


def test_fetch_failures_partial():
    rows = [
        _row(cost_reason="fetch_failed: ConnectionError", net=None),
        _row(beta_reason="short_history", net=None),
        _row(hit="fetch_failed", net=None, r_cost=None),
        _row(),
    ]
    row = _by_key(_compute(_labelled(rows)))[KEY]
    assert (row["n_fetch_failed"], row["n_no_beta"], row["n_mature"]) == (2, 1, 1)


def test_counts_partition_the_key_rows():
    rows = [
        _row(),  # mature
        _row(hit="immature", net=None, r_cost=None),  # unresolved
        _row(hit="immature", net=None, r_cost=None, beta_reason="fetch_failed: ConnectionError"),  # fetch failed only
        _row(hit="stop", net=None, beta_reason="short_history"),  # no beta
        _row(hit="fetch_failed", net=None, r_cost=None),
        _row(decided_at=_edge("2026-10-05")),  # young
    ]
    row = _by_key(_compute(_labelled(rows)))[KEY]
    assert (row["n_mature"], row["n_unresolved"], row["n_fetch_failed"], row["n_no_beta"], row["n_young"]) == (
        1,
        1,
        2,
        1,
        1,
    )
    total = sum(row[n] for n in ("n_young", "n_unresolved", "n_fetch_failed", "n_no_beta", "n_mature"))
    assert total == len(rows)


def test_malformed_counts_are_displayed_per_key_and_in_total():
    payload = _compute(_labelled([_row()]), malformed={("Z", "?", "?"): 3, (KEY): 1})
    keys = _by_key(payload)
    assert keys[("Z", "?", "?")]["n_malformed"] == 3 and keys[KEY]["n_malformed"] == 1
    assert payload["n_malformed"] == 4 and keys[(AGGREGATE, AGGREGATE, AGGREGATE)]["n_malformed"] == 4


def test_every_prior_key_has_a_row():
    row = _by_key(_compute(pd.DataFrame(columns=list(outcomes_module._COLUMNS))))[KEY]
    prior = make_prior().keys[0]
    assert row["n_mature"] == 0 and row["posterior_mean"] == prior.mean and row["prior_mean"] == prior.mean
    assert row["prior_source"] == "replay" and row["prior_n_rows"] == prior.n_rows
    assert row["prior_se"] == pytest.approx(math.sqrt(prior.tau**2 + prior.se_session**2))
    assert row["prior_weight"] == pytest.approx(prior.sd**2 / (prior.tau**2 + prior.se_session**2))
    assert row["live_mean"] is None


def test_posterior_shrinks_the_live_mean_toward_the_prior():
    frame = _labelled([_row(net=0.5, decided_at=_edge(f"2026-08-{day:02d}")) for day in range(3, 13)])
    row = _by_key(_compute(frame))[KEY]
    entry = make_prior().keys[0]
    k = row["prior_weight"]
    assert row["posterior_mean"] == pytest.approx((10 * 0.5 + k * entry.mean) / (10 + k))
    assert entry.mean < row["posterior_mean"] < 0.5


def test_unknown_key_skeptical():
    short = ("TREND_PULLBACK", "4h", "SHORT")
    row = _by_key(_compute(_labelled([_row(short)])))[short]
    assert row["prior_source"] == "skeptical" and row["prior_mean"] == 0.0 and row["prior_n_rows"] == 0


V1_PAYLOAD_KEYS = {
    "strategy",
    "direction",
    "n_mature",
    "n_immature",
    "n_fetch_failed",
    "target_rate",
    "stop_rate",
    "timeout_rate",
    "mean_r_cost",
    "mean_timeout_r",
    "median_holding_sessions",
    "first_decided_at",
    "last_decided_at",
}


class _V1Key(BaseModel, frozen=True, allow_inf_nan=False):
    """card_stats.py's CardKeyStats at 0c02a97 (what a rollback's reader validates)."""

    strategy: str
    direction: str
    n_mature: int
    n_immature: int
    n_fetch_failed: int
    target_rate: float | None
    stop_rate: float | None
    timeout_rate: float | None
    mean_r_cost: float | None
    mean_timeout_r: float | None
    median_holding_sessions: float | None
    first_decided_at: AwareDatetime | None
    last_decided_at: AwareDatetime | None


class _V1Snapshot(BaseModel, frozen=True, allow_inf_nan=False):
    """card_stats.py's CardStatsSnapshot at 0c02a97."""

    computed_at: AwareDatetime
    window_start: date
    window_end: date
    feed: str
    cost_bps_per_side: float
    max_hold_sessions: int
    labeller_protocol: str
    code_revision: str
    events_considered: int
    rows_labelled: int
    keys: tuple[_V1Key, ...] = ()


def test_v1_payload_still_validates():
    v2 = _compute(_labelled(TP))
    v1 = {
        **{k: v for k, v in v2.items() if k in _V1Snapshot.model_fields},
        "keys": [{k: v for k, v in row.items() if k in V1_PAYLOAD_KEYS} for row in v2["keys"]],
    }
    snapshot = CardStatsSnapshot.model_validate(v1)
    assert snapshot.unit is None and snapshot.keys[0].timeframe == "*" and snapshot.cohort_end is None
    assert _V1Snapshot.model_validate(v2).keys[0].strategy == "TREND_PULLBACK"  # a rollback reads the new payload


def test_vocabulary():
    assert CARD_STATS_STREAM == "card_stats"
    assert EventKind.CARD_STATS_SNAPSHOT == "card_stats_snapshot"


async def test_journaled_candidates_round_trip_into_a_snapshot(temp_db):
    """Integration: the real SQLite journal -> scan_ranked_events -> population -> label_journaled -> compute -> model."""
    event = _event_with(
        [
            _candidate(contract="AAPL", rank=1, outcome="sent"),
            _candidate(contract="MSFT", rank=2, outcome="per-scan budget spent"),
        ],
        scan_id="scan-1",
    )
    async with temp_db.session_factory() as session, session.begin():
        await temp_db.workflows.lock(session)
        await temp_db.workflows.append(
            session,
            stream=event["stream"],
            kind=EventKind.SCAN_CANDIDATES_RANKED,
            payload=event["payload"],
            key="scan_candidates_ranked/scan-1",
        )
    events = await scan_ranked_events(temp_db, 3, now=NOW)
    population = card_population(events, excluded=frozenset())
    frame = label_journaled(
        population.events,
        FakeBarSource({"AAPL": AAPL_BARS, "SPY": SPY_BARS}, raise_for={"MSFT"}),
        now=NOW,
        with_beta=False,
    )
    payload = _compute(frame, window_end=date(2026, 4, 15), events_considered=population.considered)
    snapshot = CardStatsSnapshot.model_validate(json.loads(json.dumps(payload, allow_nan=False)))
    stats = snapshot.stats("BREAKOUT", "1h", "LONG")
    # Both rows are old enough, but this run read no betas: AAPL resolved without a net excess.
    assert (stats.n_mature, stats.n_fetch_failed, stats.n_no_beta, stats.n_young) == (0, 1, 1, 0)
    assert stats.prior_source == "skeptical" and stats.posterior_mean == stats.prior_mean == 0.0
    aggregate = snapshot.stats(AGGREGATE, AGGREGATE, AGGREGATE)
    assert (aggregate.n_mature, aggregate.n_immature, aggregate.n_fetch_failed) == (0, 0, 1)
    assert snapshot.events_considered == 1 and snapshot.rows_labelled == 2


def test_card_stats_population_excludes_non_suggestion_triggers():
    """Rewritten from v1, which counted every trigger: the swing job's candidates are journal-only."""
    events = [
        _event_with([_candidate(contract="AAPL")], scan_id="scan-1", trigger="suggestion_scan"),
        _event_with([_candidate(contract="MSFT")], scan_id="scan-2", trigger="swing_scan"),
        _event_with([_candidate(contract="NVDA")], scan_id="scan-3", trigger=None),
    ]
    population = card_population(events, excluded=frozenset())
    assert [c["contract"] for e in population.events for c in e["payload"]["candidates"]] == ["AAPL"]


def test_regime_tape_and_recurrence_columns_are_inert_in_the_snapshot():
    """card_stats reads hit, net_excess_r, r_cost, holding, name cost, reasons, key and decided_at only."""
    frame = _labelled([*TP, *SQ])
    payload = _compute(frame)
    extra = (
        "regime_vix",
        "regime_vix_regime",
        "regime_risk_multiplier",
        "regime_macro_stress",
        "spy_ret_20",
        "spy_above_200",
        "tape_reason",
        "prior_sessions",
        "prior_same_session",
        "prior_outcome",
    )
    assert set(extra) <= set(outcomes_module._COLUMNS)
    stripped = _compute(frame.drop(columns=list(extra)))
    assert payload == stripped


def test_card_stats_ignores_cf_columns():
    plain = _labelled([*TP, *SQ])
    filled = plain.copy()
    for column in filled.columns:
        if column.startswith(outcomes_module.CF_COLUMN_PREFIX):
            filled[column] = 0.7 if column.endswith(("r_cost", "market_r")) else "target"
    assert _compute(filled)["keys"] == _compute(plain)["keys"]


class _SplitBars:
    """Hourly and daily frames per symbol, as the worker's two sources provide them."""

    def __init__(self, frames):
        self.frames = frames

    def fetch_bars(self, symbol, timeframe, start, end, *, adjustment):
        return self.frames[symbol, timeframe]


def _daily(symbol_scale, sessions=200):
    days = pd.bdate_range(end="2026-01-19", periods=sessions)
    index = pd.DatetimeIndex([pd.Timestamp(d.date().isoformat() + "T05:00:00+00:00") for d in days])
    market_ret = 0.01 * np.sin(np.arange(sessions))
    rets = symbol_scale * market_ret + 0.002 * np.cos(3.0 * np.arange(sessions))
    close = 100.0 * np.cumprod(1.0 + rets)
    return pd.DataFrame(
        {"Open": close, "High": close, "Low": close, "Close": close, "Volume": 5_000_000.0}, index=index
    )


def test_a_mature_candidate_flows_through_the_real_labeller_into_a_shrunk_posterior():
    decided_at = datetime(2026, 1, 20, 15, 35, tzinfo=UTC)
    hourly = pd.DataFrame(
        {"Open": [100.5, 100.6], "High": [100.8, 102.5], "Low": [100.2, 100.3], "Close": [100.6, 102.0]},
        index=pd.DatetimeIndex(["2026-01-20T16:30:00+00:00", "2026-01-20T17:30:00+00:00"]),
    )
    spy_hourly = hourly.assign(Open=[500.0, 501.0], High=[501.0, 503.0], Low=[499.0, 500.0], Close=[500.5, 502.0])
    source = _SplitBars(
        {
            ("AAPL", "1h"): hourly,
            ("SPY", "1h"): spy_hourly,
            ("AAPL", "1d"): _daily(1.2),
            ("SPY", "1d"): _daily(1.0),
        }
    )
    event = _event_with([_candidate(contract="AAPL", strategy="TREND_PULLBACK")], decided_at=decided_at)
    population = card_population([event], excluded=frozenset())
    frame = label_journaled(
        population.events, source, now=datetime(2026, 3, 3, tzinfo=UTC), tape_symbol=None, daily_bars=source
    )
    key = ("TREND_PULLBACK", "1h", "LONG")
    payload = _compute(
        frame,
        window_end=date(2026, 3, 3),
        prior=make_prior(keys=(prior_entry(*key, mean=-0.2),)),
    )
    row = _by_key(payload)[key]
    assert row["n_mature"] == 1 and row["n_no_beta"] == 0 and row["n_young"] == 0
    assert row["live_mean"] is not None and math.isfinite(row["live_mean"])
    assert row["live_mean"] > row["posterior_mean"] > row["prior_mean"] == -0.2


def test_label_journaled_and_card_stats_see_the_same_rows_on_mixed_events():
    """Zero-candidate and gated events, and events from before status/funnel existed, add no rows."""
    nonempty = [event for event in MIXED if event["payload"]["candidates"]]
    assert len(nonempty) == 3 and len(MIXED) == 5
    contracts = {c["contract"] for event in nonempty for c in event["payload"]["candidates"]}
    frames = {contract: AAPL_BARS for contract in contracts} | {"SPY": SPY_BARS}
    mixed = label_journaled(MIXED, FakeBarSource(frames), now=NOW)
    only = label_journaled(nonempty, FakeBarSource(frames), now=NOW)
    pd.testing.assert_frame_equal(mixed, only)
    assert len(mixed) == 6
    assert (
        _compute(mixed, events_considered=len(MIXED))["keys"] == _compute(only, events_considered=len(nonempty))["keys"]
    )
    assert _compute(mixed, events_considered=len(MIXED))["events_considered"] == 5
