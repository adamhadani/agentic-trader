"""PEAD base rate: re-labelling reproduces the study, the unit is the card unit, the file is digest-pinned."""

import hashlib
import json
import math
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.apriori.catalog import load_pead_entry
from agentic_trader.research.apriori.pead_base_rate import (
    BASE_RATE_VERSION,
    BaseRateCell,
    BaseRateUnavailable,
    PeadBaseRate,
    add_net_excess,
    check_reproduces,
    load_pead_base_rate,
    relabel_long,
    summarise,
)
from agentic_trader.research.apriori.pead_events import decision_at
from agentic_trader.research.apriori.pead_study import label_events
from agentic_trader.research.setups.beta import BETA_WINDOW_SESSIONS, trailing_beta
from agentic_trader.research.setups.card_prior import cost_model_constants
from agentic_trader.research.setups.net_excess import (
    NET_EXCESS_UNIT,
    R_CLIP,
    cost_inputs,
    name_cost_bps,
    net_excess_r,
)
from agentic_trader.research.setups.outcomes import market_r


ENTRY = load_pead_entry(Path(__file__).resolve().parents[3] / "config/research/apriori/pead-v2.json").entry
FIRST = date(2021, 6, 1)  # a Tuesday


def sessions(n: int, start: date = FIRST) -> list[date]:
    days = pd.bdate_range(start, periods=n)
    return [d.date() for d in days]


def hourly(prices_by_day: dict[date, list[float]]) -> pd.DataFrame:
    rows = {}
    for day, prices in prices_by_day.items():
        for k, price in enumerate(prices):
            rows[datetime.combine(day, time(9 + k), tzinfo=ET_TZ).astimezone(UTC)] = price
    series = pd.Series(rows, dtype=float)
    return pd.DataFrame(
        {"Open": series, "High": series, "Low": series, "Close": series, "Volume": 1000.0},
        index=pd.DatetimeIndex(series.index),
    )


def path_bars(first: float, later: float) -> pd.DataFrame:
    """Flat at ``first`` on day 0, then ``later`` for the next 24 sessions: a target or stop hit on day 1."""
    days = sessions(25)
    return hourly({d: [first] * 7 if i == 0 else [later] * 7 for i, d in enumerate(days)})


def events_frame() -> pd.DataFrame:
    when = decision_at(FIRST, time(10, 35))
    base = {"session": FIRST, "decision_at": when, "atr": 1.0, "median_dollar_volume": 1e9}
    return pd.DataFrame(
        [
            {**base, "symbol": "UP", "leg": "LONG"},
            {**base, "symbol": "DOWN", "leg": "SHORT"},
            {**base, "symbol": "GAP", "leg": float("nan")},  # no hourly bars: never labelled
        ]
    )


HOURLY = {"UP": path_bars(100.0, 110.0), "DOWN": path_bars(100.0, 90.0)}


def test_relabel_reproduces_study_rows():
    events = events_frame()
    study, _ = label_events(events, HOURLY, ENTRY)
    mine = relabel_long(events, HOURLY, ENTRY)
    long = study[study["direction"] == "LONG"].set_index("symbol")
    got = mine.set_index("symbol")
    assert set(got.index) == set(long.index) == {"UP", "DOWN"}
    for symbol in got.index:
        assert got.loc[symbol, "r"] == pytest.approx(long.loc[symbol, "r"], abs=1e-12)
        assert got.loc[symbol, "hit"] == long.loc[symbol, "hit"]
    assert got["hit"].nunique() == 2  # a target and a stop, so the comparison is not vacuous
    assert bool(got.loc["UP", "is_leg"]) and not bool(got.loc["DOWN", "is_leg"])
    assert {"entry_price", "stop", "entry", "entry_time", "exit_time"} <= set(mine.columns)
    check_reproduces(mine, study)


def test_check_reproduces_refuses_perturbed_label():
    events = events_frame()
    study, _ = label_events(events, HOURLY, ENTRY)
    mine = relabel_long(events, HOURLY, ENTRY)
    bad_r = study.copy()
    bad_r.loc[(bad_r["symbol"] == "UP") & (bad_r["direction"] == "LONG"), "r"] += 1e-6
    with pytest.raises(ValueError, match="1 r mismatch"):
        check_reproduces(mine, bad_r)
    bad_hit = study.copy()
    bad_hit.loc[(bad_hit["symbol"] == "UP") & (bad_hit["direction"] == "LONG"), "hit"] = "STOP"
    with pytest.raises(ValueError, match="1 hit mismatch"):
        check_reproduces(mine, bad_hit)
    with pytest.raises(ValueError, match="row count"):
        check_reproduces(mine.iloc[:1], study)
    with pytest.raises(ValueError, match="row count"):
        check_reproduces(mine, study[~((study["symbol"] == "UP") & (study["direction"] == "LONG"))])


def daily_bars(n: int, seed: int, market: pd.DataFrame | None = None) -> pd.DataFrame:
    end = FIRST - timedelta(days=1)
    days = pd.bdate_range(end=end, periods=n)
    rng = np.random.default_rng(seed)
    base = rng.normal(0, 0.01, n) if market is None else market["Close"].pct_change().fillna(0).to_numpy()
    returns = base * (1.5 if market is not None else 1.0) + rng.normal(0, 0.003, n)
    close = 100 * np.cumprod(1 + returns)
    return pd.DataFrame(
        {"Close": close, "Volume": 2_000_000.0}, index=pd.DatetimeIndex(days).tz_localize(ET_TZ).tz_convert(UTC)
    )


def spy_hourly() -> pd.DataFrame:
    days = sessions(25)
    return hourly({d: [400.0 + i + k for k in range(7)] for i, d in enumerate(days)})


def rows_with_prices() -> pd.DataFrame:
    return relabel_long(events_frame(), HOURLY, ENTRY)


def test_add_net_excess_uses_unit():
    rows = rows_with_prices()
    spy_d = daily_bars(200, 1)
    dailies = {"UP": daily_bars(200, 2, spy_d), "DOWN": daily_bars(200, 3, spy_d)}
    out = add_net_excess(rows, dailies, spy_d, spy_hourly())
    row = out[out["symbol"] == "UP"].iloc[0]
    source = rows[rows["symbol"] == "UP"].iloc[0]
    beta, _ = trailing_beta(dailies["UP"], spy_d, FIRST)
    risk = abs(source["entry"] - source["stop"])
    cost = name_cost_bps(source["entry_price"], risk, cost_inputs(dailies["UP"], FIRST))
    proxy = market_r("LONG", source["entry"], source["stop"], source["entry_time"], source["exit_time"], spy_hourly())
    expected = net_excess_r(source["r"], source["entry_price"], risk, cost, beta, proxy)
    assert expected is not None
    assert row["beta"] == pytest.approx(beta)
    assert row["name_cost_bps"] == pytest.approx(cost)
    assert row["market_r"] == pytest.approx(proxy)
    assert row["net_excess_r"] == pytest.approx(expected)
    assert abs(row["net_excess_r"]) <= R_CLIP


def test_missing_beta_or_market_counted_not_filled():
    rows = rows_with_prices()
    spy_d = daily_bars(200, 1)
    dailies = {"UP": daily_bars(200, 2, spy_d), "DOWN": None}  # DOWN has no daily bars: no beta
    out = add_net_excess(rows, dailies, spy_d, spy_hourly())
    assert out.set_index("symbol").loc["DOWN", "net_excess_r"] != out.set_index("symbol").loc["DOWN", "net_excess_r"]
    no_market = add_net_excess(rows, dailies, spy_d, pd.DataFrame())
    assert no_market["net_excess_r"].isna().all()
    stats_beta = summarise(
        _stats_rows([("UP", 1, 0.5, True), ("DOWN", 1, 0.1, False)], extra_no_beta=1, extra_no_market=2), FIRST
    )
    assert (stats_beta["n_no_beta"], stats_beta["n_no_market"]) == (1, 2)
    assert (stats_beta["n_leg"], stats_beta["n_all"]) == (1, 2)


def _stats_rows(
    items: list[tuple[str, int, float, bool]], extra_no_beta: int = 0, extra_no_market: int = 0
) -> pd.DataFrame:
    """Rows of (symbol, day offset from 2021-06-01, net_excess_r, is_leg)."""
    rows = [
        {
            "symbol": s,
            "session": FIRST + timedelta(days=d),
            "net_excess_r": v,
            "is_leg": leg,
            "beta": 1.0,
            "market_r": 0.0,
        }
        for s, d, v, leg in items
    ]
    rows += [
        {
            "symbol": "NB",
            "session": FIRST,
            "net_excess_r": math.nan,
            "is_leg": False,
            "beta": math.nan,
            "market_r": math.nan,
        }
        for _ in range(extra_no_beta)
    ]
    rows += [
        {"symbol": "NM", "session": FIRST, "net_excess_r": math.nan, "is_leg": False, "beta": 1.0, "market_r": math.nan}
        for _ in range(extra_no_market)
    ]
    return pd.DataFrame(rows)


def test_summarise_p2_is_same_session_paired():
    # Session A (2021-06-01): leg +1.0, others 0.0 and -1.0 -> session mean 0.0 -> leg beat +1.0.
    # Session B (2025-02-01): leg +0.5, other +0.1 -> session mean 0.3 -> beat +0.2 (recent).
    rows = _stats_rows(
        [("A1", 0, 1.0, True), ("A2", 0, 0.0, False), ("A3", 0, -1.0, False)]
        + [("B1", 1340, 0.5, True), ("B2", 1340, 0.1, False)]
    )
    stats = summarise(rows, date(2025, 1, 1))
    assert (stats["n_leg"], stats["n_all"]) == (2, 5)
    overall, recent = stats["overall"], stats["recent"]
    assert overall.n == 2 and overall.mean == pytest.approx(0.75)
    assert overall.control_mean == pytest.approx((1.0 + 0.0 - 1.0 + 0.5 + 0.1) / 5)
    assert overall.p2_mean == pytest.approx((1.0 + 0.2) / 2)
    assert recent.n == 1 and recent.mean == pytest.approx(0.5)
    assert recent.control_mean == pytest.approx(0.3)  # every reporter since recent_from, leg or not
    assert recent.p2_mean == pytest.approx(0.2) and recent.p2_se == 0.0
    assert [y.year for y in stats["by_year"]] == [2021, 2025]
    assert stats["by_year"][0].p2_mean == pytest.approx(1.0)


def test_expected_is_shrunk_toward_zero():
    rng = np.random.default_rng(7)
    items = []
    for quarter in range(12):
        offset = 90 * quarter
        level = rng.normal(0.0, 0.1)
        items.extend((f"S{quarter}_{k}", offset + k, level + rng.normal(0, 1.0), True) for k in range(30))
    stats = summarise(_stats_rows(items), FIRST + timedelta(days=90 * 6))
    assert 0 < stats["shrinkage"] < 1
    assert abs(stats["expected_mean"]) < abs(stats["recent"].mean)
    b = stats["shrinkage"]
    assert stats["expected_mean"] == pytest.approx(b * stats["recent"].mean)
    assert stats["expected_se"] == pytest.approx(math.sqrt(b) * stats["recent"].se_session)
    assert b == pytest.approx(stats["tau"] ** 2 / (stats["tau"] ** 2 + stats["recent"].se_session ** 2))


# --- the file ---------------------------------------------------------------------------------


def cell(n: int = 10, mean: float = 0.05) -> dict:
    return {"n": n, "mean": mean, "se_session": 0.04, "control_mean": 0.01, "p2_mean": 0.02, "p2_se": 0.03}


def payload(**overrides) -> dict:
    base = {
        "version": BASE_RATE_VERSION,
        "unit": NET_EXCESS_UNIT,
        "entry_sha256": "a" * 64,
        "source": {"events_sha256": "b" * 64},
        "cost_model": cost_model_constants(),
        "r_clip": R_CLIP,
        "beta_window_sessions": BETA_WINDOW_SESSIONS,
        "recent_from": "2025-01-01",
        "window_start": "2016-08-01",
        "window_end": "2026-07-31",
        "n_leg": 100,
        "n_all": 300,
        "n_no_beta": 1,
        "n_no_market": 2,
        "overall": cell(100, 0.05),
        "recent": cell(10, -0.1),
        "by_year": [{"year": 2025, **cell(10, -0.1)}],
        "tau": 0.05,
        "shrinkage": 0.4,
        "expected_mean": 0.4 * -0.1,
        "expected_se": math.sqrt(0.4) * 0.04,
        "code_revision": "abc1234",
    }
    return {**base, **overrides}


def write(tmp_path: Path, data: dict) -> tuple[Path, str]:
    raw = json.dumps(data).encode()
    file = tmp_path / "rate.json"
    file.write_bytes(raw)
    return file, hashlib.sha256(raw).hexdigest()


def test_load_pead_base_rate_round_trip(tmp_path):
    file, digest = write(tmp_path, payload())
    loaded = load_pead_base_rate(file, digest)
    assert isinstance(loaded, PeadBaseRate)
    assert loaded.recent == BaseRateCell(**cell(10, -0.1))
    assert loaded.by_year[0].year == 2025 and loaded.recent_from == date(2025, 1, 1)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (payload(version="pead-base-rate-v0"), "base rate version mismatch"),
        (payload(r_clip=R_CLIP + 1), "base rate constants differ from the code"),
        (
            payload(cost_model={**cost_model_constants(), "cost_floor_bps": 99.0}),
            "base rate constants differ from the code",
        ),
        (payload(beta_window_sessions=60), "base rate constants differ from the code"),
        (payload(unit="net_excess_r_v0"), "base rate constants differ from the code"),
        (payload(extra_field=1), "base rate file invalid"),
        (payload(expected_mean=0.3), "base rate file invalid"),  # not shrinkage * recent mean
    ],
)
def test_load_pead_base_rate_refuses_with_fixed_messages(tmp_path, data, message):
    file, digest = write(tmp_path, data)
    with pytest.raises(BaseRateUnavailable) as caught:
        load_pead_base_rate(file, digest)
    assert str(caught.value) == message


def test_load_pead_base_rate_file_failures(tmp_path):
    file, digest = write(tmp_path, payload())
    with pytest.raises(BaseRateUnavailable, match="^base rate digest is not configured$"):
        load_pead_base_rate(file, None)
    with pytest.raises(BaseRateUnavailable, match="^base rate file not found$"):
        load_pead_base_rate(tmp_path / "absent.json", digest)
    with pytest.raises(BaseRateUnavailable, match="^base rate file unreadable$"):
        load_pead_base_rate(tmp_path, digest)  # a directory
    with pytest.raises(BaseRateUnavailable, match="^base rate digest mismatch$"):
        load_pead_base_rate(file, "0" * 64)
    garbage = tmp_path / "garbage.json"
    garbage.write_bytes(b"{not json")
    with pytest.raises(BaseRateUnavailable, match="^base rate file invalid$"):
        load_pead_base_rate(garbage, hashlib.sha256(b"{not json").hexdigest())
