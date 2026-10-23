from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import agentic_trader.research.setups.outcomes as outcomes_module
from agentic_trader.config import WORKSPACE_ROOT
from agentic_trader.execution.durable import RankedOutcome
from agentic_trader.research.setups.bracket_counterfactual import BracketCounterfactualProtocol
from agentic_trader.research.setups.labels import BracketHit
from agentic_trader.research.setups.outcomes import (
    _COLUMNS,
    CF_COLUMN_PREFIX,
    DEFAULT_COST_BPS_PER_SIDE,
    DEFAULT_MAX_HOLD_SESSIONS,
    FETCH_FAILED_HIT,
    LIVE_COUNTERFACTUAL_ARMS,
    MARKET_PROXY_SYMBOL,
    PRIOR_OUTCOMES,
    RESOLVED_HITS,
    label_journaled,
    market_r,
    news_alignment,
    summarize,
    with_recurrence,
)
from agentic_trader.research.setups.tape import TAPE_BUCKETS, TAPE_HISTORY_DAYS


def _bars(rows: list[tuple[str, float, float, float, float]]) -> pd.DataFrame:
    index = pd.DatetimeIndex([pd.Timestamp(row[0]) for row in rows], tz="UTC")
    return pd.DataFrame(
        {
            "Open": [row[1] for row in rows],
            "High": [row[2] for row in rows],
            "Low": [row[3] for row in rows],
            "Close": [row[4] for row in rows],
        },
        index=index,
    )


class FakeBarSource:
    """A ``BarSource`` stub returning pre-built frames per symbol; records every call."""

    def __init__(self, frames: dict[str, pd.DataFrame], *, raise_for: set[str] | None = None):
        self.frames = frames
        self.raise_for = raise_for or set()
        self.calls: list[tuple[str, str, datetime, datetime, str]] = []

    def fetch_bars(self, symbol, timeframe, start, end, *, adjustment):
        self.calls.append((symbol, timeframe, start, end, adjustment))
        if symbol in self.raise_for:
            raise RuntimeError(f"no data for {symbol}")
        return self.frames[symbol]


def _candidate(
    *,
    contract="AAPL",
    strategy="BREAKOUT",
    direction="LONG",
    entry=100.0,
    stop=99.0,
    target=102.0,
    setup_quality=0.5,
    rank=1,
    outcome="sent",
    shadow=None,
):
    return {
        "contract": contract,
        "strategy": strategy,
        "timeframe": "1h",
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "target": target,
        "atr_14": 1.0,
        "setup_quality": setup_quality,
        "rank": rank,
        "outcome": outcome,
        "shadow": shadow,
    }


def _event(scan_id, decided_at, candidates, *, kind="scan_candidates_ranked", scope="universe", trigger=None):
    event = {
        "id": 1,
        "stream": f"scan/{decided_at.date().isoformat()}",
        "kind": kind,
        "schema_version": 1,
        "recorded_at": decided_at.isoformat(),
        "payload": {
            "scan_id": scan_id,
            "decided_at": decided_at.isoformat(),
            "scope": scope,
            "budget": "full",
            "ranking_key": "setup_quality",
            "candidates": candidates,
        },
    }
    if trigger is not None:
        event["payload"]["trigger"] = trigger
    return event


DECIDED_AT = datetime(2026, 3, 2, 14, 0, tzinfo=UTC)
NOW = datetime(2026, 3, 3, 0, 0, tzinfo=UTC)


def test_label_journaled_marks_immature():
    """A candidate the labeler cannot yet resolve (no bars past decision) is IMMATURE."""
    frame_ok = _bars(
        [
            ("2026-03-02T14:00:00+00:00", 100.5, 100.8, 100.2, 100.6),  # entry bar, no hit
            ("2026-03-02T15:00:00+00:00", 100.6, 102.5, 100.3, 102.0),  # target hit
        ]
    )
    # Only bars from a prior regular session: label_bracket finds no bar at/after the
    # decision time and reports IMMATURE rather than resolving anything.
    frame_thin = _bars(
        [
            ("2026-03-01T14:00:00+00:00", 100.0, 100.0, 100.0, 100.0),
            ("2026-03-01T15:00:00+00:00", 100.0, 100.0, 100.0, 100.0),
        ]
    )

    events = [
        _event(
            "scan-1",
            DECIDED_AT,
            [
                _candidate(contract="AAPL", rank=1, outcome="sent"),
                _candidate(contract="MSFT", rank=2, outcome="rejected: risk"),
            ],
        )
    ]
    bars = FakeBarSource({"AAPL": frame_ok, "MSFT": frame_thin})

    result = label_journaled(events, bars, max_hold_sessions=20, cost_bps=5.0, now=NOW)

    assert set(result["contract"]) == {"AAPL", "MSFT"}
    aapl = result.loc[result["contract"] == "AAPL"].iloc[0]
    msft = result.loc[result["contract"] == "MSFT"].iloc[0]
    assert aapl["hit"] == BracketHit.TARGET.value
    assert aapl["r_cost"] is not None
    assert msft["hit"] == BracketHit.IMMATURE.value
    assert pd.isna(msft["r"])
    assert pd.isna(msft["r_cost"])


def test_label_journaled_fetch_failure_is_fetch_failed_not_immature():
    """A failed bar fetch is distinguishable from a candidate that simply has not
    matured yet: it gets its own outcome and carries the failure's reason."""
    events = [_event("scan-1", DECIDED_AT, [_candidate(contract="ZZZZ")])]
    bars = FakeBarSource({}, raise_for={"ZZZZ"})

    result = label_journaled(events, bars, now=NOW)

    assert len(result) == 1
    row = result.iloc[0]
    assert row["hit"] == FETCH_FAILED_HIT
    assert row["hit"] != BracketHit.IMMATURE.value
    assert pd.isna(row["r"]) and pd.isna(row["r_cost"])
    assert "no data for ZZZZ" in row["reason"]


def test_label_journaled_fetches_once_per_symbol():
    frame = _bars(
        [
            ("2026-03-02T14:00:00+00:00", 100.5, 100.8, 100.2, 100.6),
            ("2026-03-02T15:00:00+00:00", 100.6, 102.5, 100.3, 102.0),
        ]
    )
    events = [
        _event("scan-1", DECIDED_AT, [_candidate(contract="AAPL", rank=1)]),
        _event("scan-2", DECIDED_AT, [_candidate(contract="AAPL", rank=1)]),
    ]
    bars = FakeBarSource({"AAPL": frame})

    result = label_journaled(events, bars, now=NOW, market_symbol=None, tape_symbol=None)

    assert len(result) == 2
    assert len(bars.calls) == 1
    assert bars.calls[0][:2] == ("AAPL", "1h")
    assert bars.calls[0][4] == "raw"


def test_label_journaled_ignores_other_event_kinds():
    events = [_event("scan-1", DECIDED_AT, [_candidate()], kind="health_observed")]
    result = label_journaled(events, FakeBarSource({}), now=NOW)
    assert result.empty


def test_label_journaled_ignores_non_universe_scope():
    """Only full-universe scans match the study population; other scopes are excluded."""
    events = [_event("scan-1", DECIDED_AT, [_candidate()], scope="non_universe_contracts")]
    result = label_journaled(events, FakeBarSource({}), now=NOW)
    assert result.empty


def test_summary_selection_by_scorer():
    """Top-1 by setup_quality beats top-1 by random when quality tracks the outcome."""
    target_bars = _bars(
        [
            ("2026-03-02T14:00:00+00:00", 100.5, 100.8, 100.2, 100.6),
            ("2026-03-02T15:00:00+00:00", 100.6, 102.5, 100.3, 102.0),  # target hit -> positive R
        ]
    )
    stop_bars = _bars(
        [
            ("2026-03-02T14:00:00+00:00", 100.5, 100.8, 100.2, 100.6),
            ("2026-03-02T15:00:00+00:00", 100.6, 100.9, 98.0, 98.5),  # stop hit -> negative R
        ]
    )
    events = [
        _event(
            "scan-1",
            DECIDED_AT,
            [
                _candidate(contract="WIN", rank=1, setup_quality=0.9, outcome="sent", shadow={"score": 0.9}),
                _candidate(
                    contract="LOSE", rank=2, setup_quality=0.1, outcome="rejected: budget", shadow={"score": 0.1}
                ),
            ],
        )
    ]
    bars = FakeBarSource({"WIN": target_bars, "LOSE": stop_bars})

    frame = label_journaled(events, bars, now=NOW)
    summary = summarize(frame)

    assert summary["total"] == 2
    assert summary["immature_total"] == 0
    assert summary["counts"]["sent"]["mature"] == 1
    assert summary["counts"]["runner_up"]["mature"] == 1
    assert summary["base_rates"]["sent"]["mean_r_cost"] > 0
    assert summary["base_rates"]["runner_up"]["mean_r_cost"] < 0

    setup_quality_selection = summary["selection"]["setup_quality"]
    shadow_selection = summary["selection"]["shadow_score"]
    random_selection = summary["selection"]["random"]
    assert setup_quality_selection["top1_mean_r_cost"] == pytest.approx(shadow_selection["top1_mean_r_cost"])
    # Picking the top setup_quality candidate always wins here; random averages both.
    assert setup_quality_selection["top1_mean_r_cost"] > random_selection["top1_mean_r_cost"]
    assert setup_quality_selection["top2_mean_r_cost"] == pytest.approx(random_selection["top2_mean_r_cost"])
    # Selection groups by scan_id, not by trading session: "scans" names it correctly.
    assert setup_quality_selection["scans"] == 1
    assert "sessions" not in setup_quality_selection
    assert random_selection["scans"] == 1 and "sessions" not in random_selection


def test_summary_without_shadow_scores_reports_none():
    frame = label_journaled(
        [_event("scan-1", DECIDED_AT, [_candidate(shadow=None)])],
        FakeBarSource({"AAPL": _bars([("2026-03-02T14:00:00+00:00", 100.5, 102.5, 100.2, 102.0)])}),
        now=NOW,
    )
    summary = summarize(frame)
    assert summary["selection"]["shadow_score"] is None


def test_summary_empty_frame():
    summary = summarize(label_journaled([], FakeBarSource({}), now=NOW))
    assert summary["total"] == 0
    assert summary["selection"]["setup_quality"] is None
    assert summary["fetch_failed_total"] == 0
    assert summary["fetch_failed_reasons"] == {}
    assert summary["counts"]["sent"]["fetch_failed"] == 0
    assert summary["counts"]["runner_up"]["fetch_failed"] == 0
    assert summary["counts"]["journal_only"] == {"mature": 0, "immature": 0, "fetch_failed": 0}
    assert set(summary["base_rates"]) == set(summary["market_exposure"]) - {"reason"}
    assert set(summary["base_rates"]) == {"sent", "runner_up", "journal_only"}
    assert "llm_gate" in summary and "market_exposure" in summary
    assert summary["card_policy"] == {
        "withheld": 0,
        "would_withhold": 0,
        "withheld_mean_r_cost": None,
        "would_withhold_mean_r_cost": None,
    }


def test_summary_counts_fetch_failures_separately_with_reasons():
    """A fetch failure is neither mature nor immature; it is its own counted bucket
    with the failure's reason, and is excluded from base rates and selection."""
    events = [
        _event(
            "scan-1",
            DECIDED_AT,
            [
                _candidate(contract="OK", rank=1, outcome="sent"),
                _candidate(contract="ZZZZ", rank=2, outcome="rejected: budget"),
            ],
        )
    ]
    bars = FakeBarSource({"OK": _bars([("2026-03-02T14:00:00+00:00", 100.5, 102.5, 100.2, 102.0)])}, raise_for={"ZZZZ"})

    frame = label_journaled(events, bars, now=NOW)
    summary = summarize(frame)

    assert summary["total"] == 2
    assert summary["fetch_failed_total"] == 1
    assert any("ZZZZ" in reason or "no data" in reason for reason in summary["fetch_failed_reasons"])
    assert summary["counts"]["runner_up"]["fetch_failed"] == 1
    assert summary["counts"]["sent"]["fetch_failed"] == 0
    # The base-rate/selection population excludes the fetch failure entirely.
    assert summary["base_rates"]["runner_up"] is None


def test_label_journaled_paces_fetches(monkeypatch):
    """One rate-limiter acquisition per symbol, sized off ``max_requests_per_minute``."""
    calls: list[int] = []

    class FakePacer:
        def __init__(self, max_per_minute):
            calls.append(max_per_minute)

        def acquire(self):
            calls.append("acquire")

    monkeypatch.setattr(outcomes_module, "RequestPacer", FakePacer)
    events = [_event("scan-1", DECIDED_AT, [_candidate(contract="AAPL", rank=1), _candidate(contract="MSFT", rank=2)])]
    bars = FakeBarSource(
        {
            "AAPL": _bars([("2026-03-02T14:00:00+00:00", 100.5, 102.5, 100.2, 102.0)]),
            "MSFT": _bars([("2026-03-02T14:00:00+00:00", 100.5, 102.5, 100.2, 102.0)]),
        }
    )

    label_journaled(events, bars, now=NOW, max_requests_per_minute=42, market_symbol=None, tape_symbol=None)

    assert calls[0] == 42
    assert calls.count("acquire") == 2  # once per symbol


def test_defaults_match_the_setup_outcomes_protocol():
    protocol = json.loads(
        (Path(__file__).resolve().parents[3] / "config" / "research" / "setup-outcomes-v1.json").read_text()
    )
    assert protocol["max_hold_sessions"] == DEFAULT_MAX_HOLD_SESSIONS
    assert max(protocol["cost_bps_per_side"]) == DEFAULT_COST_BPS_PER_SIDE


# AAPL: decision 14:00 UTC (09:00 ET, pre-market) -> entry at the 14:30 bar open 100.5; target 102 hits on the 16:00 bar.
AAPL_BARS = _bars(
    [
        ("2026-03-02T14:30:00+00:00", 100.5, 100.8, 100.2, 100.6),
        ("2026-03-02T15:30:00+00:00", 100.6, 101.0, 100.3, 100.9),
        ("2026-03-02T16:30:00+00:00", 100.9, 102.5, 100.8, 102.0),
    ]
)
# SPY over the same bars: open 500 at the entry bar, close 505 at the exit bar -> +1.0%.
SPY_BARS = _bars(
    [
        ("2026-03-02T14:30:00+00:00", 500.0, 501.0, 499.0, 500.5),
        ("2026-03-02T15:30:00+00:00", 500.5, 503.0, 500.0, 502.0),
        ("2026-03-02T16:30:00+00:00", 502.0, 506.0, 501.5, 505.0),
    ]
)


def test_market_r_scales_spy_return_into_the_candidates_r_units():
    entry_time = pd.Timestamp("2026-03-02T14:30:00+00:00")
    exit_time = pd.Timestamp("2026-03-02T16:30:00+00:00")
    # Long, entry 100, stop 99 (risk 1.0 = 1% of entry): SPY +1% -> +1.0R.
    assert market_r("LONG", 100.0, 99.0, entry_time, exit_time, SPY_BARS) == pytest.approx(1.0)
    # Short: the same market move is -1.0R.
    assert market_r("SHORT", 100.0, 101.0, entry_time, exit_time, SPY_BARS) == pytest.approx(-1.0)
    # Risk 2% of entry halves the scaled exposure.
    assert market_r("LONG", 100.0, 98.0, entry_time, exit_time, SPY_BARS) == pytest.approx(0.5)


def test_market_r_is_none_when_spy_bars_do_not_cover_the_window():
    entry_time = pd.Timestamp("2026-03-02T14:30:00+00:00")
    assert market_r("LONG", 100.0, 99.0, entry_time, pd.Timestamp("2026-03-02T13:00:00+00:00"), SPY_BARS) is None
    assert market_r("LONG", 100.0, 99.0, pd.Timestamp("2026-03-03T14:30:00+00:00"), entry_time, SPY_BARS) is None
    assert market_r("LONG", 100.0, 99.0, entry_time, entry_time, SPY_BARS.iloc[0:0]) is None


def test_label_journaled_adds_market_control_and_excess():
    bars = FakeBarSource({"AAPL": AAPL_BARS, MARKET_PROXY_SYMBOL: SPY_BARS})
    frame = label_journaled([_event("s1", DECIDED_AT, [_candidate()])], bars, now=NOW, tape_symbol=None)
    [row] = frame.to_dict("records")
    assert row["hit"] == BracketHit.TARGET.value
    assert row["entry_time"] == pd.Timestamp("2026-03-02T14:30:00+00:00")
    assert row["exit_time"] == pd.Timestamp("2026-03-02T16:30:00+00:00")
    assert row["entry"] == 100.0 and row["stop"] == 99.0
    assert row["market_r"] == pytest.approx(1.0)
    assert row["excess_r"] == pytest.approx(row["r_cost"] - 1.0)
    assert row["market_reason"] is None
    # SPY is fetched once, with the same adjustment as the candidates, after them.
    hourly_calls = [call for call in bars.calls if call[1] == "1h"]
    assert [call[0] for call in hourly_calls] == ["AAPL", MARKET_PROXY_SYMBOL]
    assert hourly_calls[1][4] == "raw"


def test_spy_fetch_failure_leaves_market_r_null_with_a_reason():
    bars = FakeBarSource({"AAPL": AAPL_BARS}, raise_for={MARKET_PROXY_SYMBOL})
    frame = label_journaled([_event("s1", DECIDED_AT, [_candidate()])], bars, now=NOW)
    [row] = frame.to_dict("records")
    assert row["hit"] == BracketHit.TARGET.value and row["r_cost"] is not None
    assert row["market_r"] is None and row["excess_r"] is None
    assert row["market_reason"] == "RuntimeError: no data for SPY"
    exposure = summarize(frame)["market_exposure"]
    assert exposure == {
        "sent": None,
        "runner_up": None,
        "journal_only": None,
        "reason": "RuntimeError: no data for SPY",
    }


def test_market_symbol_none_skips_the_control_entirely():
    bars = FakeBarSource({"AAPL": AAPL_BARS})
    frame = label_journaled(
        [_event("s1", DECIDED_AT, [_candidate()])], bars, now=NOW, market_symbol=None, tape_symbol=None
    )
    assert [call[0] for call in bars.calls] == ["AAPL"]
    assert frame["market_r"].isna().all()


def test_llm_bracket_is_relabelled_only_when_it_differs():
    bars = FakeBarSource({"AAPL": AAPL_BARS, MARKET_PROXY_SYMBOL: SPY_BARS})
    same = {"approved": True, "rejection_reason": None, "stop_loss": 99.0, "take_profit": 102.0, "applied": True}
    # A tighter LLM target (101) is hit on the 15:30 bar: fewer R than the deterministic 102 target.
    edited = {**same, "take_profit": 101.0}
    invalid = {**same, "stop_loss": 101.0}  # stop above a long entry: no geometry to judge
    events = [
        _event(
            "s1",
            DECIDED_AT,
            [
                {**_candidate(contract="AAPL", rank=1), "llm": same, "signal_id": 7},
                {**_candidate(contract="AAPL", rank=2, outcome="llm_vetoed"), "llm": edited},
                {**_candidate(contract="AAPL", rank=3, outcome="per-scan budget spent"), "llm": invalid},
                _candidate(contract="AAPL", rank=4, outcome="per-scan budget spent"),  # old event shape
            ],
        )
    ]
    frame = label_journaled(events, bars, now=NOW).sort_values("rank")
    rows = frame.to_dict("records")
    assert rows[0]["llm_ran"] is True and rows[0]["llm_vetoed"] is False and rows[0]["llm_r_cost"] is None
    assert [row["llm_bracket_edited"] for row in rows] == [False, True, True, False]
    assert rows[0]["signal_id"] == 7
    # LLM target 101 fills at the level on the 15:30 bar: r = (101 - 100.5)/1.0 = 0.5, minus the
    # round-trip cost 2 × 5 bp × 100.5 / 1.0; the deterministic 102 target gives 1.5 before costs.
    assert rows[1]["llm_vetoed"] is True and rows[1]["llm_r_cost"] == pytest.approx(0.5 - 2 * 5.0 / 1e4 * 100.5)
    assert rows[1]["r_cost"] == pytest.approx(1.5 - 2 * 5.0 / 1e4 * 100.5)
    assert rows[1]["llm_r_cost"] < rows[1]["r_cost"]
    assert rows[2]["llm_ran"] is True and rows[2]["llm_r_cost"] is None
    assert rows[3]["llm_ran"] is False and rows[3]["llm_vetoed"] is False and rows[3]["signal_id"] is None


def test_summary_llm_gate_and_market_exposure_blocks():
    bars = FakeBarSource({"AAPL": AAPL_BARS, MARKET_PROXY_SYMBOL: SPY_BARS})
    ok = {"approved": True, "rejection_reason": None, "stop_loss": 99.0, "take_profit": 102.0, "applied": True}
    veto = {**ok, "approved": False, "rejection_reason": "weak"}
    events = [
        _event(
            "s1",
            DECIDED_AT,
            [
                {**_candidate(rank=1), "llm": ok, "signal_id": 1},
                {**_candidate(rank=2, outcome="llm_vetoed"), "llm": {**veto, "take_profit": 101.0}},
                _candidate(rank=3, outcome="per-scan budget spent"),
            ],
        )
    ]
    frame = label_journaled(events, bars, now=NOW)
    summary = summarize(frame)
    gate = summary["llm_gate"]
    assert gate["ran"] == 2 and gate["vetoed"] == 1 and gate["approved"] == 1
    assert gate["approved_mean_r_cost"] == pytest.approx(frame.loc[frame["rank"] == 1, "r_cost"].iloc[0])
    assert gate["vetoed_mean_r_cost"] == pytest.approx(frame.loc[frame["rank"] == 2, "r_cost"].iloc[0])
    assert gate["bracket_edited"] == 1 and gate["bracket_edit_mature"] == 1
    # The only edited bracket was vetoed: no approved edit to average, the vetoed delta reported apart.
    assert gate["bracket_edit_mean_delta_r"] is None
    assert gate["vetoed_bracket_edit_mean_delta_r"] == pytest.approx(
        float(frame.loc[frame["rank"] == 2, "llm_r_cost"].iloc[0] - frame.loc[frame["rank"] == 2, "r_cost"].iloc[0])
    )
    exposure = summary["market_exposure"]
    assert exposure["sent"]["n"] == 1 and exposure["sent"]["mean_market_r"] == pytest.approx(1.0)
    assert exposure["sent"]["mean_excess_r"] == pytest.approx(exposure["sent"]["mean_r_cost"] - 1.0)
    assert exposure["runner_up"]["n"] == 2


def test_summary_blocks_on_an_empty_frame():
    summary = summarize(pd.DataFrame(columns=list(outcomes_module._COLUMNS)))
    assert summary["llm_gate"] == {
        "ran": 0,
        "vetoed": 0,
        "approved": 0,
        "vetoed_mean_r_cost": None,
        "approved_mean_r_cost": None,
        "bracket_edited": 0,
        "bracket_edit_mature": 0,
        "bracket_edit_mean_delta_r": None,
        "vetoed_bracket_edit_mean_delta_r": None,
    }
    assert summary["market_exposure"] == {"sent": None, "runner_up": None, "journal_only": None, "reason": None}


def test_rows_carry_the_applied_llm_stop_for_execution_evidence():
    """``collect_execution`` falls back to the applied LLM stop when a card's raw_response is unusable."""
    bars = FakeBarSource({"AAPL": AAPL_BARS, MARKET_PROXY_SYMBOL: SPY_BARS})
    applied = {"approved": True, "rejection_reason": None, "stop_loss": 98.5, "take_profit": 102.0, "applied": True}
    events = [
        _event(
            "s1",
            DECIDED_AT,
            [
                {**_candidate(rank=1), "llm": applied, "signal_id": 7},
                {**_candidate(rank=2, outcome="per-scan budget spent"), "llm": {**applied, "applied": False}},
                _candidate(rank=3, outcome="per-scan budget spent"),
            ],
        )
    ]
    rows = label_journaled(events, bars, now=NOW).sort_values("rank").to_dict("records")
    assert rows[0]["llm_stop_loss"] == 98.5
    assert pd.isna(rows[1]["llm_stop_loss"]) and pd.isna(rows[2]["llm_stop_loss"])


def test_signal_ids_stay_integers_beside_missing_ones():
    bars = FakeBarSource({"AAPL": AAPL_BARS, MARKET_PROXY_SYMBOL: SPY_BARS})
    events = [
        _event(
            "s1",
            DECIDED_AT,
            [{**_candidate(rank=1), "signal_id": 5}, _candidate(rank=2, outcome="per-scan budget spent")],
        )
    ]
    frame = label_journaled(events, bars, now=NOW).sort_values("rank")
    assert str(frame["signal_id"].dtype) == "Int64"
    assert frame["signal_id"].iloc[0] == 5 and pd.isna(frame["signal_id"].iloc[1])
    assert "5.0" not in frame[["signal_id"]].to_string()


def test_market_r_is_zero_when_the_exit_bar_is_the_entry_bar():
    """A gap-at-entry exit holds for no time: no market exposure, not a full bar of SPY."""
    entry_time = pd.Timestamp("2026-03-02T14:30:00+00:00")
    assert market_r("LONG", 100.0, 99.0, entry_time, entry_time, SPY_BARS) == 0.0
    # Between SPY bars, too: the candidate's own exit bar is its entry bar.
    between = pd.Timestamp("2026-03-02T15:00:00+00:00")
    assert market_r("LONG", 100.0, 99.0, between, between, SPY_BARS) == 0.0


def test_a_gap_at_entry_exit_has_zero_market_r():
    bars = FakeBarSource({"AAPL": AAPL_BARS, MARKET_PROXY_SYMBOL: SPY_BARS})
    # The 14:30 entry bar opens at 100.5, already through a 100.4 target: exit at the entry bar.
    frame = label_journaled([_event("s1", DECIDED_AT, [_candidate(target=100.4)])], bars, now=NOW)
    [row] = frame.to_dict("records")
    assert row["hit"] == BracketHit.TARGET.value and row["entry_time"] == row["exit_time"]
    assert row["market_r"] == 0.0 and row["excess_r"] == pytest.approx(row["r_cost"])


def test_llm_gate_splits_bracket_edits_by_maturity_and_veto():
    thin = _bars([("2026-03-01T14:00:00+00:00", 100.0, 100.0, 100.0, 100.0)])  # nothing after the decision
    bars = FakeBarSource({"AAPL": AAPL_BARS, "MSFT": thin, MARKET_PROXY_SYMBOL: SPY_BARS})
    ok = {"approved": True, "rejection_reason": None, "stop_loss": 99.0, "take_profit": 102.0, "applied": True}
    tighter = {**ok, "take_profit": 101.0}
    events = [
        _event(
            "s1",
            DECIDED_AT,
            [
                {**_candidate(rank=1), "llm": tighter, "signal_id": 1},  # approved, edited, mature
                {
                    **_candidate(rank=2, outcome="llm_vetoed"),
                    "llm": {**tighter, "approved": False, "take_profit": 101.5},
                },  # vetoed, edited, mature
                {**_candidate(rank=3, outcome="per-scan budget spent"), "llm": ok},  # not edited
                {**_candidate(contract="MSFT", rank=4, outcome="per-scan budget spent"), "llm": tighter},  # immature
            ],
        )
    ]
    frame = label_journaled(events, bars, now=NOW)
    by_rank = frame.set_index("rank")
    assert by_rank["llm_bracket_edited"].to_dict() == {1: True, 2: True, 3: False, 4: True}
    gate = summarize(frame)["llm_gate"]
    assert gate["bracket_edited"] == 3 and gate["bracket_edit_mature"] == 2
    assert gate["bracket_edit_mean_delta_r"] == pytest.approx(by_rank.loc[1, "llm_r_cost"] - by_rank.loc[1, "r_cost"])
    assert gate["vetoed_bracket_edit_mean_delta_r"] == pytest.approx(
        by_rank.loc[2, "llm_r_cost"] - by_rank.loc[2, "r_cost"]
    )


def test_a_market_proxy_candidate_is_fetched_once():
    later = DECIDED_AT + timedelta(hours=1)
    spy = _candidate(contract=MARKET_PROXY_SYMBOL, entry=500.0, stop=495.0, target=505.0)
    bars = FakeBarSource({"AAPL": AAPL_BARS, MARKET_PROXY_SYMBOL: SPY_BARS})
    frame = label_journaled(
        [_event("s1", DECIDED_AT, [_candidate()]), _event("s2", later, [spy])], bars, now=NOW, tape_symbol=None
    )
    hourly_calls = [call for call in bars.calls if call[1] == "1h"]
    assert [call[0] for call in hourly_calls] == ["AAPL", MARKET_PROXY_SYMBOL]
    # One fetch covers every candidate's window: it starts at the earliest decision of all.
    assert hourly_calls[1][2] == DECIDED_AT
    aapl = frame.loc[frame["contract"] == "AAPL"].iloc[0]
    assert aapl["market_r"] == pytest.approx(1.0)


def test_card_policy_columns_and_summary_block():
    bars = FakeBarSource({"AAPL": AAPL_BARS, MARKET_PROXY_SYMBOL: SPY_BARS})
    events = [
        _event(
            "s1",
            DECIDED_AT,
            [
                {
                    **_candidate(rank=1, outcome="card_policy_withheld"),
                    "card_policy": {"measured_ev": -0.4, "n_mature": 30, "would_withhold": True},
                },
                {
                    **_candidate(rank=2, outcome="sent"),
                    "card_policy": {"measured_ev": None, "n_mature": None, "would_withhold": False},
                },
                _candidate(rank=3, outcome="per-scan budget spent"),  # journaled before the policy existed
            ],
        )
    ]
    frame = label_journaled(events, bars, now=NOW).sort_values("rank")
    assert frame["card_policy_withheld"].tolist() == [True, False, False]
    assert frame["would_withhold"].tolist() == [True, False, None]
    summary = summarize(frame)
    withheld_r = frame.loc[frame["rank"] == 1, "r_cost"].iloc[0]
    assert summary["card_policy"] == {
        "withheld": 1,
        "would_withhold": 1,
        "withheld_mean_r_cost": pytest.approx(withheld_r),
        "would_withhold_mean_r_cost": pytest.approx(withheld_r),
    }
    # The existing blocks are unchanged: a withheld candidate is still a runner-up there.
    assert summary["counts"]["runner_up"]["mature"] == 2


def _bars_for(*symbols):
    rows = [
        ("2026-03-02T14:00:00+00:00", 100.5, 100.8, 100.2, 100.6),
        ("2026-03-02T15:00:00+00:00", 100.6, 102.5, 100.3, 102.0),
    ]
    return FakeBarSource({symbol: _bars(rows) for symbol in symbols})


def test_trigger_column_comes_from_the_payload_and_is_none_for_older_events():
    events = [
        _event("scan-1", DECIDED_AT, [_candidate(contract="AAA")], trigger="swing_scan"),
        _event("scan-2", DECIDED_AT, [_candidate(contract="BBB")]),
    ]
    frame = label_journaled(events, _bars_for("AAA", "BBB"), now=NOW, market_symbol=None)
    by_contract = dict(zip(frame["contract"], frame["trigger"], strict=True))
    assert by_contract["AAA"] == "swing_scan"
    assert pd.isna(by_contract["BBB"])
    assert list(frame.columns).index("trigger") == list(frame.columns).index("session") + 1


def test_summarize_reports_scans_and_sent_cards_per_trigger():
    events = [
        _event(
            "scan-1",
            DECIDED_AT,
            [_candidate(contract="AAA", rank=1), _candidate(contract="BBB", rank=2, outcome="rejected: budget")],
            trigger="suggestion_scan",
        ),
        _event("scan-2", DECIDED_AT, [_candidate(contract="CCC")], trigger="swing_scan"),
        _event("scan-3", DECIDED_AT, [_candidate(contract="DDD", outcome="rejected: budget")]),
    ]
    frame = label_journaled(events, _bars_for("AAA", "BBB", "CCC", "DDD"), now=NOW, market_symbol=None)
    assert summarize(frame)["triggers"] == {
        "suggestion_scan": {"scans": 1, "sent": 1},
        "swing_scan": {"scans": 1, "sent": 1},
        "unknown": {"scans": 1, "sent": 0},
    }
    assert summarize(label_journaled([], FakeBarSource({}), now=NOW))["triggers"] == {}


def _classed_frame(outcomes):
    """One labelled row per outcome, each on a distinct symbol with SPY as the market control."""
    candidates = [_candidate(contract=f"S{i}", rank=i + 1, outcome=outcome) for i, outcome in enumerate(outcomes)]
    frames = {candidate["contract"]: AAPL_BARS for candidate in candidates}
    bars = FakeBarSource({**frames, MARKET_PROXY_SYMBOL: SPY_BARS})
    return label_journaled([_event("scan-1", DECIDED_AT, candidates)], bars, now=NOW)


def test_summarize_separates_journal_only_from_runners_up():
    outcomes = ["sent", "journal_only", "journal_only", "broad_index_excluded", "per-scan budget spent"]
    s = summarize(_classed_frame(outcomes))
    assert (
        s["counts"]["sent"]["mature"],
        s["counts"]["journal_only"]["mature"],
        s["counts"]["runner_up"]["mature"],
    ) == (1, 2, 2)
    assert set(s["base_rates"]) == set(s["market_exposure"]) - {"reason"} == {"sent", "runner_up", "journal_only"}
    assert [s["base_rates"][name]["n"] for name in ("sent", "runner_up", "journal_only")] == [1, 2, 2]
    assert [s["market_exposure"][name]["n"] for name in ("sent", "runner_up", "journal_only")] == [1, 2, 2]
    assert s["total"] == 5


def test_summarize_counts_journal_only_by_maturity_and_keeps_broad_index_a_runner_up():
    candidates = [
        _candidate(contract="OK", rank=1, outcome=RankedOutcome.JOURNAL_ONLY),
        _candidate(contract="ZZZZ", rank=2, outcome=RankedOutcome.JOURNAL_ONLY),
        _candidate(contract="IDX", rank=3, outcome=RankedOutcome.BROAD_INDEX_EXCLUDED),
        _candidate(contract="BAD", rank=4, outcome=RankedOutcome.BROAD_INDEX_EXCLUDED),
    ]
    prior_session = _bars([("2026-03-01T14:00:00+00:00", 100.0, 100.0, 100.0, 100.0)])
    bars = FakeBarSource({"OK": AAPL_BARS, "IDX": AAPL_BARS, "BAD": prior_session}, raise_for={"ZZZZ"})
    counts = summarize(label_journaled([_event("scan-1", DECIDED_AT, candidates)], bars, now=NOW, market_symbol=None))[
        "counts"
    ]
    assert counts["journal_only"] == {"mature": 1, "immature": 0, "fetch_failed": 1}
    assert counts["runner_up"] == {"mature": 1, "immature": 1, "fetch_failed": 0}
    assert counts["sent"] == {"mature": 0, "immature": 0, "fetch_failed": 0}


def test_summarize_without_journal_only_rows_reports_an_empty_class():
    s = summarize(_classed_frame(["sent", "per-scan budget spent"]))
    assert s["counts"]["journal_only"] == {"mature": 0, "immature": 0, "fetch_failed": 0}
    assert s["base_rates"]["journal_only"] is None and s["market_exposure"]["journal_only"] is None


def test_selection_compares_all_scorers_over_the_shadow_scored_scans():
    events = [
        _event("scan-1", DECIDED_AT, [_candidate(contract="AAA", shadow={"score": 0.5})], trigger="suggestion_scan"),
        _event("scan-2", DECIDED_AT, [_candidate(contract="BBB", shadow={"score": 0.4})], trigger="suggestion_scan"),
        _event("scan-3", DECIDED_AT, [_candidate(contract="CCC", shadow=None)], trigger="swing_scan"),
    ]
    frame = label_journaled(events, _bars_for("AAA", "BBB", "CCC"), now=NOW, market_symbol=None)
    selection = summarize(frame)["selection"]
    assert selection["scans_basis"] == "shadow_scored"
    assert selection["setup_quality"]["scans"] == 2
    assert selection["shadow_score"]["scans"] == 2
    assert selection["random"]["scans"] == 2


def test_selection_basis_is_all_scans_without_shadow_scores():
    events = [
        _event("scan-1", DECIDED_AT, [_candidate(contract="AAA", shadow=None)]),
        _event("scan-2", DECIDED_AT, [_candidate(contract="BBB", shadow=None)]),
    ]
    frame = label_journaled(events, _bars_for("AAA", "BBB"), now=NOW, market_symbol=None)
    selection = summarize(frame)["selection"]
    assert selection["scans_basis"] == "all"
    assert selection["shadow_score"] is None
    assert selection["setup_quality"]["scans"] == selection["random"]["scans"] == 2


def _book(status, factor, would_scale):
    return {
        "mode": "preview",
        "status": status,
        "factor": factor,
        "would_scale": would_scale,
        "vol_before_pct": 0.5,
        "vol_after_full_pct": 0.9,
        "quantity_before": 10,
        "quantity_after": 10,
    }


def test_book_sizing_columns_and_summary_block():
    bars = FakeBarSource({"AAPL": AAPL_BARS, MARKET_PROXY_SYMBOL: SPY_BARS})
    events = [
        _event(
            "s1",
            DECIDED_AT,
            [
                {**_candidate(rank=1), "book_sizing": _book("applied", 0.5, True)},
                {**_candidate(rank=2), "book_sizing": _book("unchanged", 0.8, True)},
                {**_candidate(rank=3), "book_sizing": _book("unchanged", 1.0, False)},
                {**_candidate(rank=4), "book_sizing": _book("unavailable", None, None)},
                _candidate(rank=5),  # journaled before book sizing existed
            ],
        )
    ]
    frame = label_journaled(events, bars, now=NOW).sort_values("rank")
    assert frame["book_sizing_status"].tolist() == ["applied", "unchanged", "unchanged", "unavailable", None]
    assert frame["book_factor"].tolist() == [0.5, 0.8, 1.0, None, None]
    assert frame["book_would_scale"].tolist() == [True, True, False, None, None]
    cols = list(frame.columns)
    assert cols.index("book_sizing_status") == cols.index("would_withhold") + 1
    r_cost = float(frame["r_cost"].iloc[0])
    assert not pd.isna(r_cost)
    assert summarize(frame)["book_sizing"] == {
        "by_status": {"applied": 1, "unchanged": 2, "unavailable": 1},
        "would_scale": 2,
        "mean_factor_scaled": pytest.approx(0.65),
        "mean_r_cost": {"would_scale": pytest.approx(r_cost), "not_scaled": pytest.approx(r_cost)},
    }


def test_book_sizing_summary_without_any_status_and_on_an_empty_frame():
    empty = {
        "by_status": {},
        "would_scale": 0,
        "mean_factor_scaled": None,
        "mean_r_cost": {"would_scale": None, "not_scaled": None},
    }
    assert summarize(label_journaled([], FakeBarSource({}), now=NOW))["book_sizing"] == empty
    bars = FakeBarSource({"AAPL": AAPL_BARS, MARKET_PROXY_SYMBOL: SPY_BARS})
    older = label_journaled([_event("s1", DECIDED_AT, [_candidate()])], bars, now=NOW)
    assert summarize(older)["book_sizing"] == empty


def _frame_for(candidates):
    target_bars = _bars(
        [
            ("2026-03-02T14:00:00+00:00", 100.5, 100.8, 100.2, 100.6),
            ("2026-03-02T15:00:00+00:00", 100.6, 102.5, 100.3, 102.0),
        ]
    )
    bars = FakeBarSource({c["contract"]: target_bars for c in candidates})
    return label_journaled([_event("scan-1", DECIDED_AT, candidates)], bars, now=NOW)


def _news(status="ok", tone="positive", catalyst="earnings", n_items=3):
    return {
        "status": status,
        "as_of": DECIDED_AT.isoformat(),
        "window_hours": 48,
        "n_items": n_items,
        "n_market_wide": 0,
        "truncated": False,
        "headlines": [],
        "score": {"tone": tone, "relevance": "company", "catalyst": catalyst, "one_line": "x"}
        if status == "ok"
        else None,
        "model": "m" if status == "ok" else None,
        "prompt_sha256": "a" * 64 if status == "ok" else None,
        "error_type": None,
        "page_sha256": "p",
        "applied": False,
    }


@pytest.mark.parametrize(
    ("direction", "tone", "expected"),
    [
        ("LONG", "positive", "aligned"),
        ("LONG", "negative", "opposed"),
        ("SHORT", "negative", "aligned"),
        ("SHORT", "positive", "opposed"),
        ("LONG", "neutral", "neutral"),
        ("LONG", None, "none"),
        (None, "positive", "none"),
    ],
)
def test_news_alignment(direction, tone, expected):
    assert news_alignment(direction, tone) == expected


def test_rows_flatten_the_news_block_and_absent_blocks():
    candidates = [
        _candidate(contract="AAPL", rank=1) | {"news": _news()},
        _candidate(contract="MSFT", rank=2, direction="SHORT", stop=101.0, target=98.0, outcome="runner_up")
        | {"news": _news(tone="negative", catalyst="guidance")},
        _candidate(contract="NVDA", rank=3, outcome="runner_up") | {"news": _news(status="unscored")},
        _candidate(contract="TSLA", rank=4, outcome="runner_up"),
    ]
    frame = _frame_for(candidates)  # see note below
    by = frame.set_index("contract")
    assert by.loc["AAPL", "news_status"] == "ok" and by.loc["AAPL", "news_tone"] == "positive"
    assert by.loc["AAPL", "news_alignment"] == "aligned" and by.loc["AAPL", "news_catalyst"] == "earnings"
    assert by.loc["MSFT", "news_alignment"] == "aligned" and by.loc["MSFT", "news_n_items"] == 3
    assert by.loc["NVDA", "news_status"] == "unscored" and by.loc["NVDA", "news_alignment"] == "none"
    assert by.loc["TSLA", "news_status"] == "absent" and by.loc["TSLA", "news_alignment"] == "none"
    assert pd.isna(by.loc["TSLA", "news_n_items"])
    summary = summarize(frame)["news_context"]
    assert summary["blocks"] == 3 and summary["by_status"] == {"ok": 2, "unscored": 1}
    assert summary["scored"] == 2 and summary["by_catalyst"] == {"earnings": 1, "guidance": 1}
    assert set(summary["by_alignment"]) == {"aligned", "opposed", "neutral", "none"}
    aligned = summary["by_alignment"]["aligned"]
    assert aligned["n"] == 2 and isinstance(aligned["mean_r_cost"], float) and 0 <= aligned["hit_rate"] <= 1
    assert summary["by_alignment"]["none"]["n"] == 1  # NVDA (unscored); TSLA has no block and is excluded
    assert summary["by_tone"]["positive"]["n"] == 1 and summary["by_tone"]["negative"]["n"] == 1


def test_summary_empty_frame_carries_the_news_block():
    summary = summarize(pd.DataFrame(columns=_COLUMNS))
    assert summary["news_context"] == {
        "blocks": 0,
        "by_status": {},
        "scored": 0,
        "by_alignment": {
            k: {"n": 0, "mean_r_cost": None, "hit_rate": None} for k in ("aligned", "opposed", "neutral", "none")
        },
        "by_tone": {},
        "by_catalyst": {},
    }


def _regime_block(**overrides):
    block = {
        "vix": 17.25,
        "vix_regime": "NORMAL",
        "breakout_allowed": True,
        "risk_multiplier": 0.9,
        "min_rr_threshold": 1.5,
        "macro_stress": "LOW",
        "observed_at": "2026-03-02T14:00:00+00:00",
    }
    return {**block, **overrides}


def _event_with_regime(scan_id, decided_at, candidates, regime):
    event = _event(scan_id, decided_at, candidates)
    if regime is not None:
        event["payload"]["regime"] = regime
    return event


def _spy_daily(closes: list[float], *, end: str) -> pd.DataFrame:
    index = pd.bdate_range(end=end, periods=len(closes), freq="B") + pd.Timedelta(hours=20)
    return pd.DataFrame({"Close": closes}, index=pd.DatetimeIndex(index, tz="UTC"))


_RESOLVING_BARS = _bars(
    [
        ("2026-03-02T14:30:00Z", 100.0, 100.5, 99.5, 100.2),
        ("2026-03-02T15:30:00Z", 100.2, 102.5, 100.0, 102.2),
    ]
)
_SPY_HOURLY = _bars(
    [
        ("2026-03-02T14:30:00Z", 500.0, 501.0, 499.0, 500.5),
        ("2026-03-02T15:30:00Z", 500.5, 502.0, 500.0, 501.0),
    ]
)
# One bar that hits neither level and no later bars: the bracket is still open (IMMATURE).
_OPEN_BARS = _bars([("2026-03-02T14:30:00Z", 100.0, 100.5, 99.5, 100.2)])


class DailyAwareBarSource(FakeBarSource):
    """Serves ``frames[symbol]`` for hourly calls and ``daily[symbol]`` for ``1d`` calls."""

    def __init__(self, frames, daily, *, raise_for=None, raise_daily_for=None):
        super().__init__(frames, raise_for=raise_for)
        self.daily = daily
        self.raise_daily_for = raise_daily_for or set()

    def fetch_bars(self, symbol, timeframe, start, end, *, adjustment):
        if timeframe == "1d":
            self.calls.append((symbol, timeframe, start, end, adjustment))
            if symbol in self.raise_daily_for:
                raise RuntimeError(f"no daily for {symbol}")
            return self.daily[symbol]
        return super().fetch_bars(symbol, timeframe, start, end, adjustment=adjustment)


def test_regime_columns_come_from_the_event_block_and_are_none_without_one():
    events = [
        _event_with_regime("s1", DECIDED_AT, [_candidate(contract="AAPL")], _regime_block()),
        _event_with_regime("s2", DECIDED_AT + timedelta(hours=1), [_candidate(contract="MSFT")], None),
    ]
    source = DailyAwareBarSource(
        {"AAPL": _RESOLVING_BARS, "MSFT": _RESOLVING_BARS, "SPY": _SPY_HOURLY}, {"SPY": pd.DataFrame()}
    )
    frame = label_journaled(events, source, now=NOW).set_index("contract")
    assert frame.loc[
        "AAPL", ["regime_vix", "regime_vix_regime", "regime_risk_multiplier", "regime_macro_stress"]
    ].tolist() == [
        17.25,
        "NORMAL",
        0.9,
        "LOW",
    ]
    assert frame.loc[
        "MSFT", ["regime_vix", "regime_vix_regime", "regime_risk_multiplier", "regime_macro_stress"]
    ].tolist() == [
        None,
        None,
        None,
        None,
    ]


def test_non_dict_regime_block_is_absent():
    events = [_event_with_regime("s1", DECIDED_AT, [_candidate()], "NORMAL")]
    source = DailyAwareBarSource({"AAPL": _RESOLVING_BARS, "SPY": _SPY_HOURLY}, {"SPY": pd.DataFrame()})
    frame = label_journaled(events, source, now=NOW)
    assert frame["regime_vix_regime"].tolist() == [None]
    assert summarize(frame)["regime_context"]["blocks"] == 0


def test_tape_fetch_is_one_raw_daily_request_after_the_hourly_ones():
    events = [_event("s1", DECIDED_AT, [_candidate()])]
    daily = _spy_daily([100.0] * 20 + [105.0], end="2026-02-27")  # last completed session before 2026-03-02
    source = DailyAwareBarSource({"AAPL": _RESOLVING_BARS, "SPY": _SPY_HOURLY}, {"SPY": daily})
    frame = label_journaled(events, source, now=NOW)
    daily_calls = [call for call in source.calls if call[1] == "1d" and call[4] == "raw"]
    assert len(daily_calls) == 1
    symbol, _, start, end, adjustment = daily_calls[0]
    assert (symbol, adjustment, end) == ("SPY", "raw", NOW)
    assert start == DECIDED_AT - timedelta(days=TAPE_HISTORY_DAYS)
    assert [call for call in source.calls if call[1] == "1h"] == source.calls[:2]
    assert frame["spy_ret_20"].tolist() == [pytest.approx(0.05)]
    assert frame["spy_above_200"].tolist() == [None]
    assert frame["tape_reason"].tolist() == [None]


def test_tape_symbol_none_skips_the_daily_fetch():
    events = [_event("s1", DECIDED_AT, [_candidate()])]
    source = DailyAwareBarSource({"AAPL": _RESOLVING_BARS, "SPY": _SPY_HOURLY}, {"SPY": pd.DataFrame()})
    frame = label_journaled(events, source, now=NOW, tape_symbol=None)
    assert not [call for call in source.calls if call[1] == "1d" and call[4] == "raw"]
    assert frame["spy_ret_20"].tolist() == [None] and frame["tape_reason"].tolist() == [None]


def test_tape_fetch_failure_sets_the_reason_and_leaves_the_rest_intact():
    events = [_event("s1", DECIDED_AT, [_candidate()])]
    source = DailyAwareBarSource(
        {"AAPL": _RESOLVING_BARS, "SPY": _SPY_HOURLY}, {"SPY": pd.DataFrame()}, raise_daily_for={"SPY"}
    )
    frame = label_journaled(events, source, now=NOW)
    assert frame["tape_reason"].tolist() == ["RuntimeError: no daily for SPY"]
    assert frame["spy_ret_20"].tolist() == [None] and frame["spy_above_200"].tolist() == [None]
    assert frame["hit"].tolist() == [BracketHit.TARGET.value]
    assert frame["market_r"].notna().all()
    summary = summarize(frame)
    assert summary["regime_context"]["tape_reason"] == "RuntimeError: no daily for SPY"
    assert summary["regime_context"]["by_tape"]["unknown"]["n"] == 1


def test_tape_is_computed_per_session_from_closes_before_it():
    later = DECIDED_AT + timedelta(days=1)  # 2026-03-03
    events = [
        _event("s1", DECIDED_AT, [_candidate(contract="AAPL")]),
        _event("s2", later, [_candidate(contract="MSFT")]),
    ]
    # 2026-02-27 is the last session before 03-02; 03-02's own close (300) counts only for 03-03.
    daily = _spy_daily([100.0] * 20 + [105.0, 300.0], end="2026-03-02")
    source = DailyAwareBarSource({"AAPL": _RESOLVING_BARS, "MSFT": _RESOLVING_BARS, "SPY": _SPY_HOURLY}, {"SPY": daily})
    frame = label_journaled(events, source, now=NOW).set_index("contract")
    assert frame.loc["AAPL", "spy_ret_20"] == pytest.approx(0.05)
    assert frame.loc["MSFT", "spy_ret_20"] == pytest.approx(2.0)  # 300 / close[-21] (100) - 1


def test_regime_context_buckets_mature_rows_only():
    stop_bars = _bars(
        [
            ("2026-03-02T15:30:00Z", 100.0, 100.2, 98.5, 98.8),
        ]
    )
    events = [
        _event_with_regime("s1", DECIDED_AT, [_candidate(contract="AAPL")], _regime_block()),
        _event_with_regime(
            "s2",
            DECIDED_AT + timedelta(hours=1),
            [_candidate(contract="XOM")],
            _regime_block(vix_regime="ELEVATED", macro_stress=None),
        ),
        _event_with_regime("s3", DECIDED_AT + timedelta(hours=2), [_candidate(contract="MSFT")], _regime_block()),
    ]
    daily = _spy_daily([100.0] * 20 + [105.0], end="2026-02-27")
    source = DailyAwareBarSource(
        {"AAPL": _RESOLVING_BARS, "XOM": stop_bars, "MSFT": _OPEN_BARS, "SPY": _SPY_HOURLY}, {"SPY": daily}
    )
    frame = label_journaled(events, source, now=NOW)
    block = summarize(frame)["regime_context"]
    assert block["blocks"] == 3  # MSFT is immature but has a block
    assert set(block["by_vix_regime"]) == {"NORMAL", "ELEVATED"}
    normal = block["by_vix_regime"]["NORMAL"]
    assert normal["n"] == 1 and normal["hit_rate"] == 1.0 and normal["mean_r_cost"] > 0
    assert normal["mean_excess_r"] == pytest.approx(
        normal["mean_r_cost"] - float(frame.set_index("contract").loc["AAPL", "market_r"])
    )
    assert block["by_vix_regime"]["ELEVATED"]["n"] == 1 and block["by_vix_regime"]["ELEVATED"]["hit_rate"] == 0.0
    assert set(block["by_macro_stress"]) == {"LOW"} and block["by_macro_stress"]["LOW"]["n"] == 1
    assert set(block["by_tape"]) == set(TAPE_BUCKETS)
    assert block["by_tape"]["ret20_up"]["n"] == 2  # both mature rows share the session's tape
    assert block["by_tape"]["ret20_down"]["n"] == 0 and block["by_tape"]["unknown"]["n"] == 0
    assert block["by_tape"]["above_200"]["n"] == 0  # fewer than 200 sessions: no mean comparison
    assert block["tape_reason"] is None


def test_new_columns_follow_news_alignment_and_precede_market_r():
    cols = list(_COLUMNS)
    expected = [
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
    ]
    start = cols.index("news_alignment") + 1
    assert cols[start : start + len(expected)] == expected
    assert cols[start + len(expected)] == "market_r"


def test_empty_summary_has_the_regime_and_recurrence_blocks():
    summary = summarize(pd.DataFrame(columns=list(_COLUMNS)))
    assert summary["regime_context"] == {
        "blocks": 0,
        "by_vix_regime": {},
        "by_macro_stress": {},
        "by_tape": {
            name: {"n": 0, "mean_r_cost": None, "mean_excess_r": None, "hit_rate": None} for name in TAPE_BUCKETS
        },
        "tape_reason": None,
    }
    assert summary["recurrence"] == {
        "setups": 0,
        "repeated_setups": 0,
        "same_session_repeats": 0,
        "by_prior": {
            "first": {"n": 0, "mean_r_cost": None, "mean_excess_r": None, "hit_rate": None},
            "repeat": {"n": 0, "mean_r_cost": None, "mean_excess_r": None, "hit_rate": None},
        },
        "by_prior_outcome": {
            name: {"n": 0, "mean_r_cost": None, "mean_excess_r": None, "hit_rate": None} for name in PRIOR_OUTCOMES
        },
    }


def _labelled_row(
    contract,
    session,
    decided_at,
    hit,
    *,
    exit_time=None,
    strategy="TREND_PULLBACK",
    direction="LONG",
    r_cost=None,
    excess_r=None,
    scan_id=None,
):
    row = dict.fromkeys(_COLUMNS)
    row.update(
        {
            "scan_id": scan_id or f"{contract}-{decided_at}",
            "session": session,
            "contract": contract,
            "strategy": strategy,
            "direction": direction,
            "hit": hit,
            "r_cost": r_cost,
            "excess_r": excess_r,
            "exit_time": pd.Timestamp(exit_time) if exit_time else None,
            "decided_at": pd.Timestamp(decided_at),
            "sent": False,
            "prior_sessions": 0,
            "prior_same_session": 0,
            "prior_outcome": "none",
        }
    )
    return row


def _frame(rows):
    return pd.DataFrame(rows, columns=list(_COLUMNS))


def test_recurrence_constants():
    assert RESOLVED_HITS == ("target", "stop", "timeout")
    assert PRIOR_OUTCOMES == ("target", "stop", "timeout", "none")


def test_recurrence_counts_prior_sessions_and_uses_the_last_resolved_prior_outcome():
    rows = [
        _labelled_row(
            "PFE", "2026-03-02", "2026-03-02T14:35:00Z", "target", exit_time="2026-03-02T19:30:00Z", r_cost=1.9
        ),
        _labelled_row(
            "PFE", "2026-03-03", "2026-03-03T14:35:00Z", "stop", exit_time="2026-03-03T16:30:00Z", r_cost=-1.1
        ),
        _labelled_row("PFE", "2026-03-04", "2026-03-04T14:35:00Z", "timeout", r_cost=0.2),
        _labelled_row(
            "XOM", "2026-03-04", "2026-03-04T14:35:00Z", "target", exit_time="2026-03-05T15:30:00Z", r_cost=1.9
        ),
    ]
    out = with_recurrence(_frame(rows)).set_index(["contract", "session"])
    assert out.loc[("PFE", "2026-03-02"), ["prior_sessions", "prior_same_session", "prior_outcome"]].tolist() == [
        0,
        0,
        "none",
    ]
    assert out.loc[("PFE", "2026-03-03"), ["prior_sessions", "prior_same_session", "prior_outcome"]].tolist() == [
        1,
        0,
        "target",
    ]
    assert out.loc[("PFE", "2026-03-04"), ["prior_sessions", "prior_same_session", "prior_outcome"]].tolist() == [
        2,
        0,
        "stop",
    ]
    assert out.loc[("XOM", "2026-03-04"), ["prior_sessions", "prior_same_session", "prior_outcome"]].tolist() == [
        0,
        0,
        "none",
    ]


def test_recurrence_prior_outcome_ignores_brackets_open_at_the_decision():
    rows = [
        # Resolved, but only after the second decision: the desk did not know it yet.
        _labelled_row("PFE", "2026-03-02", "2026-03-02T14:35:00Z", "target", exit_time="2026-03-03T18:30:00Z"),
        _labelled_row("PFE", "2026-03-03", "2026-03-03T14:35:00Z", "stop", exit_time="2026-03-03T16:30:00Z"),
        # Still immature at the third decision; the second row has resolved.
        _labelled_row("PFE", "2026-03-04", "2026-03-04T14:35:00Z", "immature"),
        _labelled_row("PFE", "2026-03-05", "2026-03-05T14:35:00Z", "fetch_failed"),
        _labelled_row("PFE", "2026-03-06", "2026-03-06T14:35:00Z", "timeout"),
    ]
    out = with_recurrence(_frame(rows)).set_index("session")
    # At the 03-03 decision the 03-02 bracket was still open (it exits at 18:30 that day).
    assert out.loc["2026-03-03", "prior_outcome"] == "none"
    # By 03-04 both earlier brackets had resolved; the most recent by decided_at is the 03-03 stop.
    assert out.loc["2026-03-04", "prior_outcome"] == "stop"
    # Immature and fetch-failed rows never count; the last resolved prior is still the 03-03 stop.
    assert out.loc["2026-03-06", "prior_outcome"] == "stop"
    assert out.loc["2026-03-06", "prior_sessions"] == 4


def test_recurrence_prior_outcome_needs_the_exit_bar_to_have_closed():
    """exit_time is the exit bar's open: the bar closes one hourly bar later."""
    rows = [
        _labelled_row("PFE", "2026-03-02", "2026-03-02T14:00:00Z", "target", exit_time="2026-03-02T15:30:00Z"),
        _labelled_row("PFE", "2026-03-02", "2026-03-02T16:00:00Z", "stop", exit_time="2026-03-02T15:00:00Z"),
        _labelled_row("PFE", "2026-03-02", "2026-03-02T17:00:00Z", "timeout"),
    ]
    out = with_recurrence(_frame(rows)).set_index("decided_at")
    # 15:30 + 1h = 16:30 > 16:00: the target bar was still open at the second decision.
    assert out.loc[pd.Timestamp("2026-03-02T16:00:00Z"), "prior_outcome"] == "none"
    # The 15:00 bar closed exactly at 16:00 only for a decision at or after 16:00; the third (17:00)
    # sees both exit bars closed and the most recent by decided_at is the stop.
    assert out.loc[pd.Timestamp("2026-03-02T17:00:00Z"), "prior_outcome"] == "stop"
    exact = [
        _labelled_row("PFE", "2026-03-02", "2026-03-02T14:00:00Z", "target", exit_time="2026-03-02T15:00:00Z"),
        _labelled_row("PFE", "2026-03-02", "2026-03-02T16:00:00Z", "stop"),
    ]
    assert with_recurrence(_frame(exact))["prior_outcome"].tolist() == ["none", "target"]


def test_recurrence_requires_a_unique_index():
    frame = _frame([_labelled_row("PFE", "2026-03-02", "2026-03-02T14:35:00Z", "target")] * 2)
    frame.index = [0, 0]
    with pytest.raises(ValueError, match="unique index"):
        with_recurrence(frame)


def test_recurrence_same_session_repeats_and_ties_on_decided_at_are_not_prior():
    rows = [
        _labelled_row(
            "PFE", "2026-03-02", "2026-03-02T14:35:00Z", "target", exit_time="2026-03-02T16:30:00Z", scan_id="a"
        ),
        _labelled_row(
            "PFE", "2026-03-02", "2026-03-02T18:35:00Z", "stop", exit_time="2026-03-02T19:30:00Z", scan_id="b"
        ),
        _labelled_row(
            "PFE", "2026-03-02", "2026-03-02T18:35:00Z", "stop", exit_time="2026-03-02T19:30:00Z", scan_id="c"
        ),
    ]
    out = with_recurrence(_frame(rows)).set_index("scan_id")
    assert out.loc["a", ["prior_sessions", "prior_same_session", "prior_outcome"]].tolist() == [0, 0, "none"]
    assert out.loc["b", ["prior_sessions", "prior_same_session", "prior_outcome"]].tolist() == [0, 1, "target"]
    assert out.loc["c", ["prior_sessions", "prior_same_session", "prior_outcome"]].tolist() == [0, 1, "target"]


def test_recurrence_is_per_setup_not_per_symbol():
    rows = [
        _labelled_row(
            "PFE", "2026-03-02", "2026-03-02T14:35:00Z", "target", exit_time="2026-03-02T16:30:00Z", direction="LONG"
        ),
        _labelled_row("PFE", "2026-03-03", "2026-03-03T14:35:00Z", "stop", direction="SHORT"),
        _labelled_row("PFE", "2026-03-04", "2026-03-04T14:35:00Z", "stop", strategy="SQUEEZE_BREAKOUT"),
        _labelled_row("PFE", "2026-03-05", "2026-03-05T14:35:00Z", "stop", strategy=None),
    ]
    out = with_recurrence(_frame(rows))
    assert out["prior_sessions"].tolist() == [0, 0, 0, 0]
    assert out["prior_outcome"].tolist() == ["none"] * 4


def test_recurrence_leaves_other_columns_and_order_unchanged():
    frame = _frame([_labelled_row("PFE", "2026-03-02", "2026-03-02T14:35:00Z", "target")])
    out = with_recurrence(frame)
    assert list(out.columns) == list(_COLUMNS)
    assert out.drop(columns=["prior_sessions", "prior_same_session", "prior_outcome"]).equals(
        frame.drop(columns=["prior_sessions", "prior_same_session", "prior_outcome"])
    )
    empty = with_recurrence(_frame([]))
    assert empty.empty
    assert {"prior_sessions", "prior_same_session", "prior_outcome"} <= set(empty.columns)


def test_recurrence_summary_block():
    rows = [
        _labelled_row(
            "PFE",
            "2026-03-02",
            "2026-03-02T14:35:00Z",
            "target",
            exit_time="2026-03-02T16:30:00Z",
            r_cost=1.9,
            excess_r=1.5,
        ),
        _labelled_row(
            "PFE",
            "2026-03-03",
            "2026-03-03T14:35:00Z",
            "stop",
            exit_time="2026-03-03T16:30:00Z",
            r_cost=-1.1,
            excess_r=-1.3,
        ),
        _labelled_row("PFE", "2026-03-03", "2026-03-03T18:35:00Z", "immature", scan_id="late"),
        _labelled_row("XOM", "2026-03-03", "2026-03-03T14:35:00Z", "timeout", r_cost=0.3),
    ]
    frame = with_recurrence(_frame(rows))
    block = summarize(frame)["recurrence"]
    assert block["setups"] == 2
    assert block["repeated_setups"] == 1
    assert block["same_session_repeats"] == 1
    assert block["by_prior"]["first"] == {
        "n": 2,
        "mean_r_cost": pytest.approx(1.1),
        "mean_excess_r": pytest.approx(1.5),
        "hit_rate": 0.5,
    }
    assert block["by_prior"]["repeat"] == {
        "n": 1,
        "mean_r_cost": pytest.approx(-1.1),
        "mean_excess_r": pytest.approx(-1.3),
        "hit_rate": 0.0,
    }
    assert set(block["by_prior_outcome"]) == set(PRIOR_OUTCOMES)
    assert block["by_prior_outcome"]["target"]["n"] == 1 and block["by_prior_outcome"]["none"]["n"] == 2
    assert block["by_prior_outcome"]["stop"]["n"] == 0


def test_label_journaled_applies_recurrence():
    events = [
        _event("s1", DECIDED_AT, [_candidate()]),
        _event("s2", DECIDED_AT + timedelta(days=1), [_candidate()]),
    ]
    source = DailyAwareBarSource({"AAPL": _RESOLVING_BARS, "SPY": _SPY_HOURLY}, {"SPY": pd.DataFrame()})
    frame = label_journaled(events, source, now=NOW + timedelta(days=1)).sort_values("decided_at")
    assert frame["prior_sessions"].tolist() == [0, 1]
    assert frame["prior_outcome"].tolist() == ["none", "target"]


def test_label_journaled_names_a_daily_frame_with_no_bars_before_the_session():
    events = [_event("s1", DECIDED_AT, [_candidate()])]
    source = DailyAwareBarSource({"AAPL": _RESOLVING_BARS, "SPY": _SPY_HOURLY}, {"SPY": pd.DataFrame()})
    frame = label_journaled(events, source, now=NOW)
    assert frame["tape_reason"].tolist() == ["no daily bars before the session"]
    assert summarize(frame)["regime_context"]["tape_reason"] == "no daily bars before the session"
    # The fetch failure reason is kept when the fetch itself failed.
    failing = DailyAwareBarSource(
        {"AAPL": _RESOLVING_BARS, "SPY": _SPY_HOURLY}, {"SPY": pd.DataFrame()}, raise_daily_for={"SPY"}
    )
    assert label_journaled(events, failing, now=NOW)["tape_reason"].tolist() == ["RuntimeError: no daily for SPY"]


def test_summarize_by_tape_counts_a_row_in_both_the_return_and_the_mean_buckets():
    day2 = DECIDED_AT + timedelta(days=1)
    bars = _bars(
        [
            ("2026-03-02T14:30:00Z", 100.0, 100.5, 99.5, 100.2),
            ("2026-03-02T15:30:00Z", 100.2, 102.5, 100.0, 102.2),
            ("2026-03-03T14:30:00Z", 100.0, 100.5, 99.5, 100.2),
            ("2026-03-03T15:30:00Z", 100.2, 102.5, 100.0, 102.2),
        ]
    )
    # 02-27 closes at 110 (above the 200-session mean, up 10% over 20 sessions); 03-02 closes at 1.
    daily = _spy_daily([100.0] * 229 + [110.0, 1.0], end="2026-03-02")
    events = [_event("s1", DECIDED_AT, [_candidate()]), _event("s2", day2, [_candidate()])]
    source = DailyAwareBarSource({"AAPL": bars, "SPY": bars}, {"SPY": daily})
    frame = label_journaled(events, source, now=NOW + timedelta(days=2))
    by_tape = summarize(frame)["regime_context"]["by_tape"]
    assert [by_tape[name]["n"] for name in ("ret20_up", "ret20_down", "above_200", "below_200", "unknown")] == [
        1,
        1,
        1,
        1,
        0,
    ]


# --------------------------------------------------------------- bracket counterfactual arms

CF_BARS = _bars(
    [
        ("2026-03-02T14:30:00+00:00", 100.5, 100.8, 100.2, 100.6),
        ("2026-03-02T15:30:00+00:00", 100.6, 103.5, 100.3, 103.2),
    ]
)


def _cf_names(suffix):
    return [f"cf_{arm.name}_{suffix}" for arm in LIVE_COUNTERFACTUAL_ARMS]


def test_live_counterfactual_arms_match_the_frozen_protocol():
    doc = json.loads((WORKSPACE_ROOT / "config" / "research" / "bracket-counterfactual-v1.json").read_text())
    assert BracketCounterfactualProtocol(**doc).bracket_arms()[1:] == LIVE_COUNTERFACTUAL_ARMS
    assert [arm.name for arm in LIVE_COUNTERFACTUAL_ARMS] == ["A1_3r", "A2_2r_trail", "A3_3r_trail", "A4_trail_only"]


def test_columns_include_target_atr_and_cf_columns():
    columns = list(_COLUMNS)
    assert columns[columns.index("stop") + 1 : columns.index("stop") + 3] == ["target", "atr_14"]
    expected = [f"cf_{arm.name}_{kind}" for arm in LIVE_COUNTERFACTUAL_ARMS for kind in ("hit", "r_cost", "market_r")]
    assert columns[columns.index("excess_r") + 1 : columns.index("excess_r") + 7] == [
        "beta",
        "beta_reason",
        "beta_market_r",
        "beta_excess_r",
        "hold_r",
        "timing_r",
    ]
    start = columns.index("timing_r") + 1
    assert columns[start : start + 12] == expected
    assert columns[start + 12] == "market_reason"
    assert all(name.startswith(CF_COLUMN_PREFIX) for name in expected)


def test_label_journaled_fills_cf_columns_from_the_same_bars():
    events = [_event("scan-1", DECIDED_AT, [_candidate(contract="AAPL")])]
    bars = FakeBarSource({"AAPL": CF_BARS, MARKET_PROXY_SYMBOL: SPY_BARS})
    row = label_journaled(events, bars, now=NOW, tape_symbol=None).iloc[0]
    assert row["hit"] == BracketHit.TARGET.value
    assert row["target"] == 102.0 and row["atr_14"] == 1.0
    assert row["cf_A1_3r_hit"] == "target"
    assert row["cf_A1_3r_r_cost"] is not None and row["cf_A1_3r_r_cost"] > row["r_cost"]
    assert not pd.isna(row["cf_A1_3r_market_r"])
    assert all(not pd.isna(value) for value in row[_cf_names("hit")])


def test_counterfactual_columns_none_without_target():
    candidate = _candidate()
    del candidate["target"]
    events = [_event("scan-1", DECIDED_AT, [candidate])]
    row = label_journaled(events, FakeBarSource({"AAPL": CF_BARS}), now=NOW, market_symbol=None, tape_symbol=None).iloc[
        0
    ]
    assert row["hit"] == BracketHit.IMMATURE.value
    for suffix in ("hit", "r_cost", "market_r"):
        assert all(pd.isna(value) for value in row[_cf_names(suffix)])


def test_counterfactual_columns_none_on_fetch_failure():
    events = [_event("scan-1", DECIDED_AT, [_candidate(contract="ZZZZ")])]
    row = label_journaled(events, FakeBarSource({}, raise_for={"ZZZZ"}), now=NOW).iloc[0]
    assert row["hit"] == FETCH_FAILED_HIT
    for suffix in ("hit", "r_cost", "market_r"):
        assert all(pd.isna(value) for value in row[_cf_names(suffix)])


def _cf_frame(rows):
    """rows: dicts overriding the live and A1 columns, on top of an all-None frame."""
    records = [{**dict.fromkeys(_COLUMNS), "sent": True, "llm_ran": False, **row} for row in rows]
    return pd.DataFrame(records, columns=list(_COLUMNS))


def test_counterfactual_summary_shapes_and_advantage():
    frame = _cf_frame(
        [
            {
                "hit": "target",
                "r_cost": 2.0,
                "market_r": 0.5,
                "cf_A1_3r_hit": "target",
                "cf_A1_3r_r_cost": 3.0,
                "cf_A1_3r_market_r": 1.0,
            },
            {
                "hit": "stop",
                "r_cost": -1.0,
                "market_r": 0.0,
                "cf_A1_3r_hit": "stop",
                "cf_A1_3r_r_cost": -1.0,
                "cf_A1_3r_market_r": None,
            },
            {
                "hit": "timeout",
                "r_cost": 0.2,
                "market_r": None,
                "cf_A1_3r_hit": "timeout",
                "cf_A1_3r_r_cost": 0.4,
                "cf_A1_3r_market_r": 0.1,
            },
            {"hit": "immature", "r_cost": None, "cf_A1_3r_hit": "immature", "cf_A1_3r_r_cost": None},
        ]
    )
    block = summarize(frame)["counterfactual"]
    assert block["caveat"] == "descriptive; not a gate"
    assert list(block["arms"]) == [arm.name for arm in LIVE_COUNTERFACTUAL_ARMS]
    a1 = block["arms"]["A1_3r"]
    assert a1["n_pairs"] == 3
    assert a1["target_rate"] == pytest.approx(1 / 3) and a1["stop_rate"] == pytest.approx(1 / 3)
    assert a1["timeout_rate"] == pytest.approx(1 / 3)
    assert a1["mean_r_cost"] == pytest.approx(2.4 / 3)
    assert a1["mean_excess_r"] == pytest.approx(((3.0 - 1.0) + (0.4 - 0.1)) / 2)
    # only row 1 has all four values: (3.0 - 1.0) - (2.0 - 0.5) -> +0.5; rows 2 and 3 lack a market_r.
    assert a1["advantage_vs_live_excess"] == pytest.approx(0.5)
    empty = block["arms"]["A2_2r_trail"]
    assert empty == {
        "n_pairs": 0,
        "target_rate": None,
        "stop_rate": None,
        "timeout_rate": None,
        "mean_r_cost": None,
        "mean_excess_r": None,
        "mean_beta_excess_r": None,
        "advantage_vs_live_excess": None,
    }


def test_summarize_empty_frame_has_counterfactual_block():
    block = summarize(pd.DataFrame(columns=list(_COLUMNS)))["counterfactual"]
    assert block["caveat"] == "descriptive; not a gate"
    assert set(block["arms"]) == {arm.name for arm in LIVE_COUNTERFACTUAL_ARMS}
    for arm in block["arms"].values():
        assert arm["n_pairs"] == 0 and arm["mean_r_cost"] is None and arm["advantage_vs_live_excess"] is None


def _adjusted_daily(closes, *, end="2026-02-27"):
    index = pd.bdate_range(end=end, periods=len(closes)) + pd.Timedelta(hours=20)
    return pd.DataFrame({"Close": list(closes)}, index=pd.DatetimeIndex(index, tz="UTC"))


def _half_beta_dailies(n=150):
    rng = np.random.default_rng(3)
    market_ret = rng.normal(0.0, 0.01, n - 1)
    market = 100.0 * np.cumprod(np.concatenate([[1.0], 1.0 + market_ret]))
    symbol = 50.0 * np.cumprod(np.concatenate([[1.0], 1.0 + 0.5 * market_ret]))
    return _adjusted_daily(symbol), _adjusted_daily(market)


def test_rows_carry_beta_and_hold_columns():
    aapl_daily, spy_daily = _half_beta_dailies()
    source = DailyAwareBarSource({"AAPL": _RESOLVING_BARS, "SPY": _SPY_HOURLY}, {"AAPL": aapl_daily, "SPY": spy_daily})
    frame = label_journaled([_event("s1", DECIDED_AT, [_candidate()])], source, now=NOW, tape_symbol=None)
    [row] = frame.to_dict("records")
    assert row["hit"] == BracketHit.TARGET.value
    assert row["beta"] == pytest.approx(0.5, abs=1e-9) and row["beta_reason"] is None
    assert row["beta_market_r"] == pytest.approx(0.5 * row["market_r"])
    assert row["beta_excess_r"] == pytest.approx(row["r_cost"] - row["beta_market_r"])
    # AAPL's own open-to-close over the holding window: 100.0 -> 102.2 on a 1.0 risk unit.
    assert row["hold_r"] == pytest.approx((102.2 / 100.0 - 1.0) * 100.0 / 1.0)
    assert row["timing_r"] == pytest.approx(row["r_cost"] - row["hold_r"])


def test_daily_beta_fetch_uses_adjusted_bars():
    aapl_daily, spy_daily = _half_beta_dailies()
    source = DailyAwareBarSource({"AAPL": _RESOLVING_BARS, "SPY": _SPY_HOURLY}, {"AAPL": aapl_daily, "SPY": spy_daily})
    label_journaled([_event("s1", DECIDED_AT, [_candidate()])], source, now=NOW, tape_symbol=None)
    recorded = [(call[0], call[1], call[4]) for call in source.calls]
    assert recorded == [
        ("AAPL", "1h", "raw"),
        ("SPY", "1h", "raw"),
        ("AAPL", "1d", "all"),
        ("SPY", "1d", "all"),
    ]
    adjusted = [call for call in source.calls if call[4] == "all"]
    assert all(call[2] == DECIDED_AT - timedelta(days=200) for call in adjusted)


def test_daily_fetch_failure_sets_beta_reason_only():
    aapl_daily, spy_daily = _half_beta_dailies()
    source = DailyAwareBarSource(
        {"AAPL": _RESOLVING_BARS, "SPY": _SPY_HOURLY},
        {"AAPL": aapl_daily, "SPY": spy_daily},
        raise_daily_for={"AAPL"},
    )
    [row] = label_journaled([_event("s1", DECIDED_AT, [_candidate()])], source, now=NOW, tape_symbol=None).to_dict(
        "records"
    )
    assert row["beta_reason"] == "fetch_failed: RuntimeError"
    assert row["beta"] is None and row["beta_market_r"] is None and row["beta_excess_r"] is None
    assert row["hit"] == BracketHit.TARGET.value
    assert row["market_r"] is not None and row["hold_r"] is not None and row["timing_r"] is not None
    spy_failed = DailyAwareBarSource(
        {"AAPL": _RESOLVING_BARS, "SPY": _SPY_HOURLY},
        {"AAPL": aapl_daily, "SPY": spy_daily},
        raise_daily_for={"SPY"},
    )
    [row] = label_journaled([_event("s1", DECIDED_AT, [_candidate()])], spy_failed, now=NOW, tape_symbol=None).to_dict(
        "records"
    )
    assert row["beta_reason"] == "fetch_failed: RuntimeError" and row["beta"] is None


def test_immature_rows_carry_no_beta_columns():
    aapl_daily, spy_daily = _half_beta_dailies()
    source = DailyAwareBarSource({"AAPL": _OPEN_BARS, "SPY": _SPY_HOURLY}, {"AAPL": aapl_daily, "SPY": spy_daily})
    [row] = label_journaled([_event("s1", DECIDED_AT, [_candidate()])], source, now=NOW, tape_symbol=None).to_dict(
        "records"
    )
    assert row["hit"] == BracketHit.IMMATURE.value
    assert [row[name] for name in ("beta_market_r", "beta_excess_r", "hold_r", "timing_r")] == [None] * 4


def test_spy_candidate_beta_one_and_hold_equals_market():
    _, spy_daily = _half_beta_dailies()
    spy = _candidate(contract=MARKET_PROXY_SYMBOL, entry=500.0, stop=499.0, target=501.0)
    source = DailyAwareBarSource({"SPY": _SPY_HOURLY}, {"SPY": spy_daily})
    [row] = label_journaled([_event("s1", DECIDED_AT, [spy])], source, now=NOW, tape_symbol=None).to_dict("records")
    assert row["beta"] == pytest.approx(1.0, abs=1e-9)
    assert row["hold_r"] == pytest.approx(row["market_r"])
    assert row["beta_market_r"] == pytest.approx(row["market_r"])
    assert len([call for call in source.calls if call[1] == "1d"]) == 1


class _StartHonouringBarSource(DailyAwareBarSource):
    """Like the real provider, a daily request returns only bars at or after ``start``."""

    def fetch_bars(self, symbol, timeframe, start, end, *, adjustment):
        frame = super().fetch_bars(symbol, timeframe, start, end, adjustment=adjustment)
        return frame.loc[frame.index >= pd.Timestamp(start)] if timeframe == "1d" else frame


def test_market_proxy_beta_series_covers_the_earliest_decision_when_spy_is_ranked_later():
    aapl_daily, spy_daily = _half_beta_dailies()
    later = DECIDED_AT + timedelta(days=60)
    events = [
        _event("s1", DECIDED_AT, [_candidate()]),
        _event("s2", later, [_candidate(contract=MARKET_PROXY_SYMBOL, entry=500.0, stop=499.0, target=501.0)]),
    ]
    source = _StartHonouringBarSource(
        {"AAPL": _RESOLVING_BARS, "SPY": _SPY_HOURLY}, {"AAPL": aapl_daily, "SPY": spy_daily}
    )
    frame = label_journaled(events, source, now=NOW + timedelta(days=90), tape_symbol=None)
    aapl = frame.loc[frame["contract"] == "AAPL"].iloc[0]
    assert aapl["beta_reason"] is None and aapl["beta"] == pytest.approx(0.5, abs=1e-9)
    spy_daily_calls = [c for c in source.calls if c[0] == "SPY" and c[1] == "1d"]
    assert [c[2] for c in spy_daily_calls] == [DECIDED_AT - timedelta(days=200)]


def test_no_daily_beta_fetch_for_a_symbol_whose_hourly_fetch_failed():
    aapl_daily, spy_daily = _half_beta_dailies()
    source = DailyAwareBarSource(
        {"AAPL": _RESOLVING_BARS, "SPY": _SPY_HOURLY}, {"AAPL": aapl_daily, "SPY": spy_daily}, raise_for={"AAPL"}
    )
    label_journaled([_event("s1", DECIDED_AT, [_candidate()])], source, now=NOW, tape_symbol=None)
    assert [c[0] for c in source.calls if c[1] == "1d"] == ["SPY"]


def _synthetic_mature_frame(rows):
    """Mature rows built directly: (contract, r_cost, market_r, beta, hold_r, timing_r)."""
    records = []
    for i, (contract, r_cost, market, beta, hold, timing) in enumerate(rows):
        record = dict.fromkeys(_COLUMNS)
        beta_market = None if beta is None or market is None else beta * market
        record.update(
            scan_id="s1",
            contract=contract,
            rank=i + 1,
            outcome="sent",
            sent=True,
            hit=BracketHit.TARGET.value,
            r_cost=r_cost,
            market_r=market,
            excess_r=None if market is None else r_cost - market,
            beta=beta,
            beta_market_r=beta_market,
            beta_excess_r=None if beta_market is None else r_cost - beta_market,
            hold_r=hold,
            timing_r=timing,
            llm_ran=False,
            llm_vetoed=False,
            llm_bracket_edited=False,
            card_policy_withheld=False,
            prior_same_session=0,
        )
        records.append(record)
    return pd.DataFrame(records, columns=list(_COLUMNS))


def test_exposure_reports_beta_and_timing():
    frame = _synthetic_mature_frame(
        [("AAPL", 2.0, 1.0, 0.5, 1.5, 0.5), ("MSFT", 1.0, 2.0, 1.5, None, None), ("XLK", 0.0, 1.0, None, 0.5, -0.5)]
    )
    sent = summarize(frame)["market_exposure"]["sent"]
    assert sent["n_beta"] == 2
    assert sent["mean_beta"] == pytest.approx(1.0)
    assert sent["mean_beta_market_r"] == pytest.approx((0.5 + 3.0) / 2)
    assert sent["mean_beta_excess_r"] == pytest.approx(((2.0 - 0.5) + (1.0 - 3.0)) / 2)
    assert sent["mean_hold_r"] == pytest.approx(1.0)
    assert sent["mean_timing_r"] == pytest.approx(0.0)


def test_exposure_beta_keys_are_none_without_any_beta():
    frame = _synthetic_mature_frame([("AAPL", 1.0, 1.0, None, None, None)])
    sent = summarize(frame)["market_exposure"]["sent"]
    assert sent["n_beta"] == 0
    assert sent["mean_beta"] is None and sent["mean_beta_excess_r"] is None and sent["mean_hold_r"] is None


def test_by_asset_class_with_map():
    frame = _synthetic_mature_frame(
        [("AAPL", 2.0, 1.0, 0.5, None, None), ("MSFT", 1.0, 0.0, 1.0, None, None), ("TLT", -1.0, 1.0, None, None, None)]
    )
    classes = summarize(frame, asset_classes={"AAPL": "single_stock", "MSFT": "single_stock", "TLT": "rates_credit"})[
        "by_asset_class"
    ]
    assert classes["single_stock"] == {
        "n": 2,
        "mean_r_cost": pytest.approx(1.5),
        "mean_excess_r": pytest.approx((1.0 + 1.0) / 2),
        "mean_beta_excess_r": pytest.approx((1.5 + 1.0) / 2),
    }
    assert classes["rates_credit"]["n"] == 1 and classes["rates_credit"]["mean_beta_excess_r"] is None


def test_by_asset_class_unclassified_without_map():
    frame = _synthetic_mature_frame([("AAPL", 2.0, 1.0, 0.5, None, None), ("TLT", 0.0, 0.0, None, None, None)])
    classes = summarize(frame)["by_asset_class"]
    assert list(classes) == ["unclassified"] and classes["unclassified"]["n"] == 2
    assert summarize(frame, asset_classes={"AAPL": "single_stock"})["by_asset_class"].keys() == {
        "single_stock",
        "unclassified",
    }


def test_counterfactual_beta_excess():
    frame = _synthetic_mature_frame([("AAPL", 2.0, 1.0, 0.5, None, None), ("MSFT", 1.0, 1.0, None, None, None)])
    prefix = f"{CF_COLUMN_PREFIX}{LIVE_COUNTERFACTUAL_ARMS[0].name}_"
    frame[f"{prefix}r_cost"] = [3.0, 1.0]
    frame[f"{prefix}hit"] = [BracketHit.TARGET.value] * 2
    frame[f"{prefix}market_r"] = [2.0, 1.0]
    arm = summarize(frame)["counterfactual"]["arms"][LIVE_COUNTERFACTUAL_ARMS[0].name]
    assert arm["mean_beta_excess_r"] == pytest.approx(3.0 - 0.5 * 2.0)
    empty = summarize(frame)["counterfactual"]["arms"][LIVE_COUNTERFACTUAL_ARMS[1].name]
    assert empty["mean_beta_excess_r"] is None


def test_empty_frame_has_new_keys():
    summary = summarize(pd.DataFrame(columns=list(_COLUMNS)))
    assert summary["by_asset_class"] == {}
    for arm in summary["counterfactual"]["arms"].values():
        assert arm["mean_beta_excess_r"] is None
