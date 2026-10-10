import math

import numpy as np
import pytest

from agentic_trader.research.spread.evaluate import evaluate_stage
from agentic_trader.research.spread.protocol import load_spread_protocol
from agentic_trader.research.spread.trading import Trade
from tests.research.spread.test_protocol import PROTOCOL_PATH


PROTOCOL = load_spread_protocol(PROTOCOL_PATH).protocol
RULES = PROTOCOL.pass_rules


def trade(window: int, gross: float, sector: str = "tech", reason: str = "reverted") -> Trade:
    return Trade(
        window=window,
        y="A",
        x="B",
        sector=sector,
        side=1,
        entry_pos=1,
        exit_pos=4,
        entry_z=-2.1,
        exit_z=0.2,
        exit_reason=reason,
        gross_return=gross,
        holding_sessions=3,
        beta=1.0,
    )


def lanes(values: np.ndarray) -> dict[float, np.ndarray]:
    return {cost: values - cost * 1e-6 for cost in PROTOCOL.costs_bps_per_side}


def good_trades(n: int = 120, windows: int = 10) -> list[Trade]:
    return [trade(i % windows, 0.02 if i % 5 else -0.01) for i in range(n)]


def test_all_rules_hold_on_a_clearly_positive_stage():
    rng = np.random.default_rng(0)
    lane = 0.0008 + rng.normal(0.0, 0.002, 1200)
    market = rng.normal(0.0, 0.01, 1200)
    result = evaluate_stage(good_trades(), lanes(lane), market, windows=10, protocol=PROTOCOL, rules=RULES)
    assert result["passes"] and all(result[k]["holds"] for k in ("s1", "s2", "s3", "s4"))
    assert result["s1"]["ci90"][0] > 0 and result["s1"]["n_sessions"] == 1200
    assert result["s3"]["closed_trades"] == 120 and result["s3"]["windows_with_trades"] == 10
    assert result["s3"]["positive_window_fraction"] == 0.8
    assert abs(result["s4"]["beta"]) < 0.2 and math.isfinite(result["s4"]["se_hac"])
    d = result["descriptives"]
    assert set(d["by_cost"]) == {"0.0", "5.0", "10.0"} and d["by_cost"]["5.0"]["closed_trades"] == 120
    assert d["by_cost"]["5.0"]["mean_trade_net"] < d["by_cost"]["0.0"]["mean_trade_net"]
    assert d["exit_reasons"] == {"reverted": 120} and d["mean_holding_sessions"] == 3.0
    assert len(d["windows"]) == 10 and d["windows"][0]["window"] == 0 and d["windows"][0]["trades"] == 12
    assert d["sectors"]["tech"]["trades"] == 120 and d["stage_windows"] == 10
    assert math.isfinite(d["sharpe_annualised"]) and d["hit_rate"] == pytest.approx(0.8)


def test_s1_fails_on_a_zero_mean_lane():
    lane = np.tile([0.001, -0.001], 600)
    result = evaluate_stage(good_trades(), lanes(lane), np.zeros(1200), windows=10, protocol=PROTOCOL, rules=RULES)
    assert not result["s1"]["holds"] and not result["passes"]


def test_s2_requires_both_mean_and_trimmed_mean_positive():
    trades = [trade(i % 10, 0.01) for i in range(99)] + [trade(0, -5.0)]
    lane = np.full(1200, 0.001)
    result = evaluate_stage(trades, lanes(lane), np.zeros(1200), windows=10, protocol=PROTOCOL, rules=RULES)
    assert result["s2"]["trimmed_mean"] > 0 > result["s2"]["mean"] and not result["s2"]["holds"]


def test_s3_counts_trades_windows_and_positive_fraction():
    trades = [
        trade(w, 0.01 if w < 4 else -0.01) for w in range(8) for _ in range(13)
    ]  # 104 trades, 8 windows, 50% positive
    lane = np.full(1200, 0.001)
    result = evaluate_stage(trades, lanes(lane), np.zeros(1200), windows=12, protocol=PROTOCOL, rules=RULES)
    s3 = result["s3"]
    assert s3["closed_trades"] == 104 and s3["windows_with_trades"] == 8 and s3["positive_window_fraction"] == 0.5
    assert not s3["holds"]
    few = evaluate_stage(trades[:50], lanes(lane), np.zeros(1200), windows=12, protocol=PROTOCOL, rules=RULES)
    assert not few["s3"]["holds"] and few["s3"]["closed_trades"] == 50


def test_s4_fails_on_market_exposure_and_on_too_few_observations():
    rng = np.random.default_rng(1)
    market = rng.normal(0.0, 0.01, 1200)
    exposed = 0.5 * market + 0.001
    result = evaluate_stage(good_trades(), lanes(exposed), market, windows=10, protocol=PROTOCOL, rules=RULES)
    assert result["s4"]["beta"] == pytest.approx(0.5, abs=0.02) and not result["s4"]["holds"]
    sparse = np.full(1200, np.nan)
    sparse[:20] = market[:20]
    result = evaluate_stage(good_trades(), lanes(exposed), sparse, windows=10, protocol=PROTOCOL, rules=RULES)
    assert math.isnan(result["s4"]["beta"]) and not result["s4"]["holds"] and result["s4"]["n_sessions"] == 20


def test_empty_stage_is_a_clean_failure():
    result = evaluate_stage([], lanes(np.zeros(0)), np.zeros(0), windows=0, protocol=PROTOCOL, rules=RULES)
    assert not result["passes"] and result["s3"]["closed_trades"] == 0 and math.isnan(result["s1"]["mean"])
    assert result["descriptives"]["windows"] == [] and result["descriptives"]["exit_reasons"] == {}
