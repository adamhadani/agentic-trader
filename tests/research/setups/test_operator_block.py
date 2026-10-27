"""The ``operator`` block of ``cards outcomes``: who entered a card, and kept versus vetoed excess R."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from agentic_trader.research.setups.labels import BracketHit
from agentic_trader.research.setups.outcomes import _COLUMNS, attach_execution, operator_block, summarize


BASE = datetime(2026, 10, 5, 14, 40, tzinfo=UTC)  # 10:40 New York: the 10:35 slot


def _row(
    n: int,
    *,
    action: str = "none",
    marked: bool = True,
    excess: float | None = 0.1,
    trigger: str = "none",
    hour_offset: float = 0.0,
    strategy: str = "BREAKOUT",
    session: str | None = None,
    tap_age: float | None = None,
    hit: str = BracketHit.TARGET.value,
) -> dict:
    decided_at = BASE + timedelta(days=n, hours=hour_offset)
    return {
        "scan_id": f"scan-{n}",
        "session": session or decided_at.astimezone(UTC).date().isoformat(),
        "trigger": "suggestion_scan",
        "contract": f"S{n}",
        "strategy": strategy,
        "direction": "LONG",
        "sent": True,
        "signal_id": n,
        "hit": hit,
        "beta_excess_r": excess,
        "decided_at": decided_at,
        "auto_marked": marked,
        "entry_trigger": trigger,
        "operator_action": action,
        "tap_age_seconds": tap_age,
    }


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _sides(kept: list[float], vetoed: list[float], *, start: int = 0) -> list[dict]:
    rows = [_row(start + i, action="auto_entered", trigger="auto", excess=x) for i, x in enumerate(kept)]
    rows += [_row(start + len(kept) + i, action="dismissed", excess=x) for i, x in enumerate(vetoed)]
    return rows


def test_kept_vs_vetoed_only_auto_marked_cards():
    rows = _sides([0.5] * 20, [0.1] * 12)
    # Tap-era cards (no marker) never enter the rule, whatever they did.
    rows += [_row(100 + i, action="tapped", trigger="tap", marked=False, excess=9.0) for i in range(5)]
    rows += [_row(200 + i, action="dismissed", marked=False, excess=-9.0) for i in range(5)]
    block = operator_block(_frame(rows))["kept_vs_vetoed"]
    assert block["status"] == "ok"
    assert block["kept"] == {"n": 20, "mean_excess_r": pytest.approx(0.5)}
    assert block["vetoed"] == {"n": 12, "mean_excess_r": pytest.approx(0.1)}
    assert abs(block["difference"] - 0.4) < 1e-9


def test_operator_cancelled_marked_cards_are_vetoed():
    rows = _sides([0.5] * 20, [0.1] * 6)
    rows += [_row(50 + i, action="cancelled", trigger="auto", excess=0.1) for i in range(6)]
    block = operator_block(_frame(rows))["kept_vs_vetoed"]
    assert block["vetoed"]["n"] == 12 and block["kept"]["n"] == 20


def test_refused_and_abandoned_excluded_and_counted():
    rows = _sides([0.5] * 20, [0.1] * 12)
    rows += [_row(60 + i, action="auto_refused", excess=-5.0) for i in range(3)]
    rows += [_row(70 + i, action="abandoned", excess=-5.0) for i in range(2)]
    block = operator_block(_frame(rows))["kept_vs_vetoed"]
    assert block["excluded"] == {"auto_refused": 3, "abandoned": 2}
    assert block["kept"]["n"] == 20 and block["vetoed"]["n"] == 12
    assert block["kept"]["mean_excess_r"] == pytest.approx(0.5)
    assert block["vetoed"]["mean_excess_r"] == pytest.approx(0.1)


def test_insufficient_below_30_setups_or_10_per_side():
    below_total = operator_block(_frame(_sides([0.5] * 15, [0.1] * 14)))["kept_vs_vetoed"]
    assert below_total["status"] == "insufficient" and below_total["ci90"] is None
    below_side = operator_block(_frame(_sides([0.5] * 31, [0.1] * 9)))["kept_vs_vetoed"]
    assert below_side["status"] == "insufficient"
    # The counts and means are still reported.
    assert below_side["kept"]["n"] == 31 and below_side["vetoed"]["n"] == 9
    assert operator_block(_frame(_sides([0.5] * 20, [0.1] * 10)))["kept_vs_vetoed"]["status"] == "ok"
    assert operator_block(pd.DataFrame())["kept_vs_vetoed"]["status"] == "insufficient"


def test_refused_or_abandoned_then_tapped_is_on_neither_side_nor_excluded():
    rows = _sides([0.5] * 20, [0.1] * 12)
    rows += [_row(80 + i, action="tapped", trigger="tap", excess=-5.0) for i in range(4)]
    block = operator_block(_frame(rows))["kept_vs_vetoed"]
    assert block["excluded"] == {"auto_refused": 0, "abandoned": 0}
    assert block["kept"]["n"] == 20 and block["vetoed"]["n"] == 12


def test_unresolved_and_duplicate_setups_do_not_count():
    rows = _sides([0.5] * 20, [0.1] * 10)
    rows.append(_row(300, action="auto_entered", trigger="auto", excess=None, hit=BracketHit.IMMATURE.value))
    # The same setup journaled twice in one session counts once (the earlier decision).
    first = _row(400, action="auto_entered", trigger="auto", excess=0.5)
    second = {**first, "scan_id": "again", "decided_at": first["decided_at"] + timedelta(hours=4), "beta_excess_r": 9.0}
    rows += [first, second]
    block = operator_block(_frame(rows))["kept_vs_vetoed"]
    assert block["kept"]["n"] == 21 and block["kept"]["mean_excess_r"] == pytest.approx(0.5)


def test_ci_is_session_block_bootstrap_and_seeded():
    rng = [0.05 * ((i * 7) % 11 - 5) for i in range(40)]
    rows = _sides(rng[:25], rng[25:] + [0.3] * 5)
    frame = _frame(rows)
    one = operator_block(frame, seed=3)["kept_vs_vetoed"]
    again = operator_block(frame, seed=3)["kept_vs_vetoed"]
    other = operator_block(frame, seed=4)["kept_vs_vetoed"]
    assert one["ci90"] == again["ci90"]
    assert one["ci90"][0] <= one["difference"] <= one["ci90"][1]
    assert one["ci90"] != other["ci90"]


def test_by_slot_trigger_and_strategy_count_actions():
    rows = [
        _row(0, action="auto_entered", trigger="auto"),
        _row(1, action="dismissed"),
        _row(2, action="tapped", trigger="tap", marked=False, hour_offset=4, strategy="MEAN_REVERSION"),
        _row(3, action="expired", marked=False, hour_offset=4, strategy="MEAN_REVERSION"),
        _row(4, action="cancelled", trigger="auto", hour_offset=4),
    ]
    block = operator_block(_frame(rows))
    zero = {"sent": 0, "tapped": 0, "auto_entered": 0, "dismissed": 0, "cancelled": 0, "expired": 0}
    assert block["by_slot"]["10:35"] == {**zero, "sent": 2, "auto_entered": 1, "dismissed": 1}
    assert block["by_slot"]["14:35"] == {**zero, "sent": 3, "tapped": 1, "expired": 1, "cancelled": 1}
    assert block["by_trigger"]["suggestion_scan"]["sent"] == 5
    assert block["by_strategy"]["MEAN_REVERSION"] == {**zero, "sent": 2, "tapped": 1, "expired": 1}


def test_tap_latency_taps_only():
    rows = [
        _row(0, action="tapped", trigger="tap", marked=False, tap_age=60.0),
        _row(1, action="tapped", trigger="tap", marked=False, tap_age=180.0),
        _row(2, action="tapped", trigger="tap", marked=False, tap_age=600.0),
        _row(3, action="auto_entered", trigger="auto", tap_age=9999.0),
        _row(4, action="tapped", trigger="tap", marked=False, tap_age=None),
    ]
    latency = operator_block(_frame(rows))["tap_latency"]
    assert latency["n"] == 3 and latency["median_seconds"] == 180.0
    assert abs(latency["p90_seconds"] - 516.0) < 1e-9
    assert operator_block(pd.DataFrame())["tap_latency"] == {"median_seconds": None, "p90_seconds": None, "n": 0}


def test_tap_era_line_unmarked_cards_only():
    rows = [_row(i, action="tapped", trigger="tap", marked=False, excess=0.4) for i in range(3)]
    rows += [_row(10 + i, action="none", marked=False, excess=0.1) for i in range(2)]
    rows += _sides([5.0] * 20, [-5.0] * 12, start=100)
    era = operator_block(_frame(rows))["tap_era"]
    assert era["tapped"] == {"n": 3, "mean_excess_r": pytest.approx(0.4)}
    assert era["untapped"] == {"n": 2, "mean_excess_r": pytest.approx(0.1)}
    assert abs(era["difference"] - 0.3) < 1e-9


def test_attach_execution_joins_by_signal_and_summarize_carries_the_block():
    labelled = pd.DataFrame([{**_row(0), "sent": True}, {**_row(1), "sent": False, "signal_id": None}]).drop(
        columns=["auto_marked", "entry_trigger", "operator_action", "tap_age_seconds"]
    )
    execution = pd.DataFrame(
        [
            {
                "signal_id": 0,
                "entry_trigger": "auto",
                "operator_action": "auto_entered",
                "auto_marked": True,
                "tap_age_seconds": None,
            }
        ]
    )
    joined = attach_execution(labelled, execution)
    assert list(joined["operator_action"]) == ["auto_entered", "none"]
    assert list(joined["entry_trigger"]) == ["auto", "none"]
    assert list(joined["auto_marked"]) == [True, False]
    assert operator_block(joined)["by_slot"]["10:35"]["auto_entered"] == 1
    assert "operator" in summarize(pd.DataFrame(columns=list(_COLUMNS)))
