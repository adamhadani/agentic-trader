from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

import agentic_trader.research.setups.outcomes as outcomes_module
from agentic_trader.research.setups.labels import BracketHit
from agentic_trader.research.setups.outcomes import (
    DEFAULT_COST_BPS_PER_SIDE,
    DEFAULT_MAX_HOLD_SESSIONS,
    FETCH_FAILED_HIT,
    MARKET_PROXY_SYMBOL,
    label_journaled,
    market_r,
    summarize,
)


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


def _event(scan_id, decided_at, candidates, *, kind="scan_candidates_ranked", scope="universe"):
    return {
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

    result = label_journaled(events, bars, now=NOW, market_symbol=None)

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
    assert "llm_gate" in summary and "market_exposure" in summary


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

    label_journaled(events, bars, now=NOW, max_requests_per_minute=42, market_symbol=None)

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
    frame = label_journaled([_event("s1", DECIDED_AT, [_candidate()])], bars, now=NOW)
    [row] = frame.to_dict("records")
    assert row["hit"] == BracketHit.TARGET.value
    assert row["entry_time"] == pd.Timestamp("2026-03-02T14:30:00+00:00")
    assert row["exit_time"] == pd.Timestamp("2026-03-02T16:30:00+00:00")
    assert row["entry"] == 100.0 and row["stop"] == 99.0
    assert row["market_r"] == pytest.approx(1.0)
    assert row["excess_r"] == pytest.approx(row["r_cost"] - 1.0)
    assert row["market_reason"] is None
    # SPY is fetched once, with the same adjustment as the candidates, after them.
    assert [call[0] for call in bars.calls] == ["AAPL", MARKET_PROXY_SYMBOL]
    assert bars.calls[1][4] == "raw"


def test_spy_fetch_failure_leaves_market_r_null_with_a_reason():
    bars = FakeBarSource({"AAPL": AAPL_BARS}, raise_for={MARKET_PROXY_SYMBOL})
    frame = label_journaled([_event("s1", DECIDED_AT, [_candidate()])], bars, now=NOW)
    [row] = frame.to_dict("records")
    assert row["hit"] == BracketHit.TARGET.value and row["r_cost"] is not None
    assert row["market_r"] is None and row["excess_r"] is None
    assert row["market_reason"] == "RuntimeError: no data for SPY"


def test_market_symbol_none_skips_the_control_entirely():
    bars = FakeBarSource({"AAPL": AAPL_BARS})
    frame = label_journaled([_event("s1", DECIDED_AT, [_candidate()])], bars, now=NOW, market_symbol=None)
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
    assert gate["bracket_edited"] == 1
    assert gate["bracket_edit_mean_delta_r"] == pytest.approx(
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
        "bracket_edit_mean_delta_r": None,
    }
    assert summary["market_exposure"] == {"sent": None, "runner_up": None}
