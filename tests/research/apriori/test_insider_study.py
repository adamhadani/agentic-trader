import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.apriori.catalog import InsiderEntry, LoadedInsiderEntry, load_insider_entry
from agentic_trader.research.apriori.insider_events import INSIDER_EVENT_COLUMNS, opportunistic_flags
from agentic_trader.research.apriori.insider_study import (
    InsiderLegInputs,
    evaluate_insider,
    execute_insider_study,
    label_insider_events,
)
from agentic_trader.research.apriori.pead_events import decision_at
from agentic_trader.research.insider_filings import InsiderTransaction
from agentic_trader.research.pooled.cube import LabelCube


SHIPPED = Path(__file__).resolve().parents[3] / "config/research/apriori/insider-v1.json"
CLOCK = time(10, 35)


def _calendar(n, first):
    days, d = [], first
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return tuple(days)


DAYS = _calendar(250, date(2021, 1, 4))
FIRST_ROW = 130  # the cube's sessions are DAYS[130:160]
SESSIONS = DAYS[FIRST_ROW : FIRST_ROW + 30]
SYMS = tuple(f"S{i:02d}" for i in range(24))
# dollar_volume (col + 1) x 1e6: cols 0-7 tercile 0, 8-15 tercile 1, 16-23 tercile 2.
RET = {s: 0.01 * i for i, s in enumerate(SYMS)}
RET.update({"S08": 0.20, "S09": 0.08, "S10": 0.04, "S11": 0.05, "S12": 0.07, "S13": 0.09, "S14": 0.10, "S15": 0.10})
R_COST = np.add.outer(0.001 * np.arange(30), 0.1 * np.arange(24) - 1.0)


def _entry(**update):
    base = load_insider_entry(SHIPPED).entry.model_dump(mode="json")
    base["window"] = {
        "decisions": ["2021-01-04", "2021-12-31"],
        "bars_through": "2022-02-01",
        "recent_from": "2021-06-01",
    }
    base.update(update)
    return InsiderEntry.model_validate(base)


ENTRY = _entry()


def _bars(closes, drop=()):
    index = pd.DatetimeIndex([datetime.combine(d, time(5), tzinfo=UTC) for d in DAYS])
    frame = pd.DataFrame({"Close": closes, "Volume": np.full(len(DAYS), 1e6)}, index=index)
    return frame.drop(index[list(drop)])


def _geometric(r):
    return 100.0 * (1.0 + r) ** (np.arange(len(DAYS)) / 20.0)


def _world(
    *, ret=None, eligible=None, labelled=None, tiebreak=None, hit=None, adjusted=None, transactions=None, issuer_of=None
):
    shape = (len(SESSIONS), len(SYMS))
    elig = np.ones(shape, bool) if eligible is None else eligible
    lab = (np.ones(shape, bool) if labelled is None else labelled) & elig
    arrays = {
        "eligible": elig,
        "labelled": lab,
        "r_gross": np.where(lab, R_COST + 0.05, np.nan),
        "r_cost": np.where(lab, R_COST, np.nan),
        "holding": np.full(shape, 7, np.int16),
        "hit": np.ones(shape, np.int8) if hit is None else hit,
        "tiebreak": (np.tile(1000 - np.arange(24), (30, 1)) if tiebreak is None else tiebreak).astype(np.uint64),
        "dollar_volume": np.where(elig, np.tile((np.arange(24) + 1) * 1e6, (30, 1)), np.nan),
    }
    cube = LabelCube(
        spec_identity="spec", cohort_sha256="c" * 64, sessions=SESSIONS, symbols=SYMS, arrays=arrays, coverage={}
    )
    returns = {**RET, **(ret or {})}
    bars = {s: _bars(_geometric(returns[s])) for s in SYMS}
    bars.update(adjusted or {})
    return InsiderLegInputs(
        cube=cube,
        trading_days=DAYS,
        adjusted=bars,
        transactions=transactions or {},
        identity_breaks={},
        issuer_of=issuer_of or {s: 1000 + i for i, s in enumerate(SYMS)},
        decision_time=CLOCK,
        provenance={"cube_sha256": cube.sha256},
    )


def _events(*pairs, buyers=2, dollars=1e6):
    rows = []
    for symbol, row in pairs:
        session = SESSIONS[row]
        filing = DAYS[FIRST_ROW + row - 1]
        rows.append(
            {
                "event_id": f"{symbol}:{filing.isoformat()}",
                "symbol": symbol,
                "filing_date": filing,
                "session": session,
                "decision_at": decision_at(session, CLOCK),
                "buyers": buyers,
                "opp_dollars": dollars,
                "year": session.year,
            }
        )
    return pd.DataFrame(rows, columns=list(INSIDER_EVENT_COLUMNS))


def _tx(symbol, day, owner=1):
    return InsiderTransaction(symbol, day, day, "P", 1000.0, owner)


def _controls_of(controls, event_id):
    return set(controls.loc[controls["pair_id"] == event_id, "control_symbols"].iloc[0].split(","))


def test_leg_reads_cube_cell_at_decision_session():
    hit = np.ones((30, 24), np.int8)
    hit[5, 11] = 2
    legs, controls, counts = label_insider_events(_events(("S11", 5)), _world(hit=hit), ENTRY)
    assert len(legs) == 1
    leg = legs.iloc[0]
    assert leg.r_cost == pytest.approx(R_COST[5, 11]) and leg.r_gross == pytest.approx(R_COST[5, 11] + 0.05)
    assert leg.hit == "stop" and leg.holding == 7
    assert leg.dv == 12e6 and leg.opp_rel == pytest.approx(1e6 / 12e6) and leg.bucket == 1
    assert leg.session_mean_r_cost == pytest.approx(R_COST[5].mean())
    assert set(INSIDER_EVENT_COLUMNS) <= set(legs.columns)
    assert counts["excluded"] == {} and counts["legs"] == 1 and len(controls) == 1


def test_not_in_cube_not_eligible_unlabelled_counted():
    eligible = np.ones((30, 24), bool)
    eligible[4, 3] = False
    labelled = np.ones((30, 24), bool)
    labelled[4, 20] = False
    events = _events(("ZZZ", 4), ("S03", 4), ("S20", 4), ("S11", 4))
    legs, _, counts = label_insider_events(events, _world(eligible=eligible, labelled=labelled), ENTRY)
    assert list(legs["symbol"]) == ["S11"]
    assert counts["excluded"] == {"not_in_cube": {2021: 1}, "not_eligible": {2021: 1}, "unlabelled": {2021: 1}}


def test_ret20_uses_close_d_minus_1_over_20_sessions_earlier():
    p = FIRST_ROW + 5
    closes = np.full(len(DAYS), 50.0)
    closes[p - 1], closes[p - 21] = 110.0, 100.0
    gap = np.full(len(DAYS), 50.0)
    adjusted = {"S11": _bars(closes, drop=(p - 5, p - 30)), "S12": _bars(gap, drop=(p - 21,))}
    legs, _, counts = label_insider_events(_events(("S11", 5), ("S12", 5)), _world(adjusted=adjusted), ENTRY)
    assert list(legs["symbol"]) == ["S11"]
    assert legs.iloc[0].ret20 == pytest.approx(0.10)
    assert counts["excluded"] == {"no_return": {2021: 1}}


def test_hold60_close_to_close_and_nan_when_immature():
    legs, controls, _ = label_insider_events(_events(("S11", 5)), _world(), ENTRY)
    assert legs.iloc[0].hold60 == pytest.approx(1.05**3 - 1)
    expected = np.mean([(1 + RET[s]) ** 3 - 1 for s in ("S09", "S10", "S12", "S13", "S15")])
    assert controls.iloc[0].hold60 == pytest.approx(expected)
    short = {s: _bars(_geometric(RET[s]), drop=range(FIRST_ROW + 65, len(DAYS))) for s in SYMS}
    legs, controls, _ = label_insider_events(_events(("S11", 5)), _world(adjusted=short), ENTRY)
    assert np.isnan(legs.iloc[0].hold60) and np.isnan(controls.iloc[0].hold60)


def test_bucket_is_dollar_volume_tercile_among_labelled_cells():
    labelled = np.ones((30, 24), bool)
    labelled[5, 16:] = False  # 16 labelled cells: ranks 0-5 -> 0, 6-10 -> 1, 11-15 -> 2
    legs, _, _ = label_insider_events(
        _events(("S05", 5), ("S06", 5), ("S10", 5), ("S11", 5)), _world(labelled=labelled), ENTRY
    )
    assert dict(zip(legs["symbol"], legs["bucket"], strict=True)) == {"S05": 0, "S06": 1, "S10": 1, "S11": 2}
    legs, _, _ = label_insider_events(_events(("S11", 6)), _world(labelled=labelled), ENTRY)
    assert legs.iloc[0].bucket == 1  # other sessions are unaffected


def test_control_excludes_recent_buyer_including_routine_and_sibling():
    p = FIRST_ROW + 5
    recent = DAYS[p - 100]
    prior = recent.replace(year=recent.year - 1)
    routine = [_tx("S10", prior, owner=7), _tx("S10", recent, owner=7)]
    assert opportunistic_flags(routine) == [True, False]
    transactions = {
        "S10": tuple(routine),
        "S13": (_tx("S13", DAYS[p - 127]),),  # before the 126-session window: still a candidate
        "S14": (_tx("S14", DAYS[p]),),  # filed on D itself: outside [D - 126, D)
        "S15": (_tx("S15", DAYS[p + 3]),),
    }
    issuer_of = {s: 1000 + i for i, s in enumerate(SYMS)} | {"S09": 1011}
    world = _world(transactions=transactions, issuer_of=issuer_of)
    legs, controls, counts = label_insider_events(_events(("S09", 5), ("S11", 5)), world, ENTRY)
    assert list(legs["symbol"]) == ["S11"] and counts["excluded"] == {"share_class_sibling": {2021: 1}}
    assert _controls_of(controls, legs.iloc[0].event_id) == {"S08", "S12", "S13", "S14", "S15"}
    cols = [8, 12, 13, 14, 15]
    row = controls.iloc[0]
    assert row.r_cost == pytest.approx(R_COST[5, cols].mean()) and row.r_gross == pytest.approx(
        R_COST[5, cols].mean() + 0.05
    )
    assert row.session == SESSIONS[5] and row.pair_id == legs.iloc[0].event_id


def test_purchase_window_start_is_126_sessions_before_d():
    p = FIRST_ROW + 5
    transactions = {"S10": (_tx("S10", DAYS[p - 126]),), "S09": (_tx("S09", DAYS[p - 1]),)}
    legs, controls, _ = label_insider_events(_events(("S11", 5)), _world(transactions=transactions), ENTRY)
    assert _controls_of(controls, legs.iloc[0].event_id) == {"S08", "S12", "S13", "S14", "S15"}


def test_same_session_events_never_control_each_other():
    legs, controls, _ = label_insider_events(_events(("S11", 5), ("S12", 5)), _world(), ENTRY)
    ids = dict(zip(legs["symbol"], legs["event_id"], strict=True))
    assert "S12" not in _controls_of(controls, ids["S11"])
    assert "S11" not in _controls_of(controls, ids["S12"])
    assert _controls_of(controls, ids["S11"]) == {"S09", "S10", "S13", "S14", "S15"}


@pytest.mark.parametrize(("descending", "winner"), [(True, "S15"), (False, "S14")])
def test_five_nearest_by_ret20_tie_broken_by_tiebreak(descending, winner):
    tiebreak = np.tile(1000 - np.arange(24) if descending else np.arange(24) + 1, (30, 1))
    legs, controls, _ = label_insider_events(_events(("S11", 5)), _world(tiebreak=tiebreak), ENTRY)
    # distances to 0.05: S10 .01, S12 .02, S09 .03, S13 .04, then S14 = S15 = .05 (tie), S08 .15
    assert _controls_of(controls, legs.iloc[0].event_id) == {"S10", "S12", "S09", "S13", winner}


def test_unpaired_below_five_candidates():
    p = FIRST_ROW + 5
    transactions = {s: (_tx(s, DAYS[p - 10]),) for s in ("S08", "S09", "S10")}
    legs, controls, counts = label_insider_events(_events(("S11", 5)), _world(transactions=transactions), ENTRY)
    assert len(legs) == 1 and controls.empty
    assert counts["unpaired"] == 1 and counts["unpaired_by_year"] == {2021: 1} and counts["paired"] == 0


def test_share_class_sibling_keeps_larger_dv():
    issuer_of = {s: 1000 + i for i, s in enumerate(SYMS)} | {"S12": 1011}
    events = _events(("S11", 5), ("S12", 5), ("S11", 20))
    legs, _, counts = label_insider_events(events, _world(issuer_of=issuer_of), ENTRY)
    assert sorted(zip(legs["symbol"], legs["session"], strict=True)) == [("S11", SESSIONS[20]), ("S12", SESSIONS[5])]
    assert counts["excluded"] == {"share_class_sibling": {2021: 1}}


def _frames(n, recent, leg_mean, control_mean, seed=3, noise=0.3):
    rng = np.random.default_rng(seed)
    old = [date(2016, 1, 1) + timedelta(days=3 * i) for i in range(n - recent)]
    new = [date(2023, 1, 2) + timedelta(days=3 * i) for i in range(recent)]
    sessions = old + new
    leg_r = leg_mean + rng.normal(0, noise, n)
    legs = pd.DataFrame(
        {
            "event_id": [f"E{i}" for i in range(n)],
            "symbol": [f"S{i % 50}" for i in range(n)],
            "filing_date": [d - timedelta(days=1) for d in sessions],
            "session": sessions,
            "decision_at": [decision_at(d, CLOCK) for d in sessions],
            "buyers": [2 + i % 3 for i in range(n)],
            "opp_dollars": rng.uniform(1e5, 1e6, n),
            "year": [d.year for d in sessions],
            "r_cost": leg_r,
            "r_gross": leg_r + 0.05,
            "hit": ["target", "stop", "timeout"] * (n // 3) + ["stop"] * (n % 3),
            "holding": np.full(n, 10),
            "ret20": rng.normal(0, 0.1, n),
            "bucket": [i % 3 for i in range(n)],
            "dv": np.full(n, 1e7),
            "opp_rel": rng.uniform(0.01, 0.1, n),
            "hold60": rng.normal(0.02, 0.1, n),
            "session_mean_r_cost": rng.normal(0, 0.1, n),
        }
    )
    control_r = control_mean + rng.normal(0, noise, n)
    controls = pd.DataFrame(
        {
            "pair_id": legs["event_id"],
            "session": sessions,
            "r_cost": control_r,
            "r_gross": control_r + 0.05,
            "hold60": rng.normal(0.0, 0.1, n),
            "control_symbols": ["A,B,C,D,E"] * n,
        }
    )
    return legs, controls


def test_planted_edge_passes_all_criteria():
    legs, controls = _frames(400, 150, 0.5, 0.0)
    result = evaluate_insider(legs, controls, ENTRY)
    assert result["decision"] == "eligible_for_probe"
    assert all(result["criteria"][k]["holds"] for k in ("P1", "P2", "P3", "P4"))
    assert result["criteria"]["P4"] | {"n": 400, "n_recent": 150} == result["criteria"]["P4"]
    diagnostics = result["diagnostics"]
    assert set(diagnostics["by_buyers"]) == {"2", "3+"} and diagnostics["unpaired"] == 0
    assert set(diagnostics["hits"]) == {"target", "stop", "timeout"}
    assert diagnostics["excluding_2020"]["n"] == 400
    assert {
        "by_year",
        "opp_rel_terciles",
        "ret20_terciles",
        "events_per_session",
        "vs_session_mean",
        "hold60",
        "r_gross",
    } <= set(diagnostics)
    assert json.dumps(result, default=str)


def test_null_fails_p1_p2():
    legs, controls = _frames(400, 150, 0.0, 0.0, noise=0.3)
    controls["r_cost"] = legs["r_cost"].to_numpy() + np.random.default_rng(9).normal(0, 0.01, 400)
    result = evaluate_insider(legs, controls, ENTRY)
    assert result["decision"] == "failed"
    assert not result["criteria"]["P1"]["holds"] and not result["criteria"]["P2"]["holds"]


def test_p4_fails_below_floor():
    legs, controls = _frames(250, 150, 0.5, 0.0)
    result = evaluate_insider(legs, controls, ENTRY)
    assert result["criteria"]["P1"]["holds"] and result["criteria"]["P2"]["holds"] and result["criteria"]["P3"]["holds"]
    assert not result["criteria"]["P4"]["holds"] and result["decision"] == "failed"
    legs, controls = _frames(400, 90, 0.5, 0.0)
    assert evaluate_insider(legs, controls, ENTRY)["criteria"]["P4"]["holds"] is False


def test_p2_uses_paired_legs_only():
    legs, controls = _frames(400, 150, 0.5, 0.0)
    extra = legs.iloc[:5].assign(event_id=[f"U{i}" for i in range(5)], r_cost=50.0)
    result = evaluate_insider(pd.concat([legs, extra], ignore_index=True), controls, ENTRY)
    assert result["criteria"]["P2"]["n_leg"] == 400 and result["diagnostics"]["unpaired"] == 5
    assert result["criteria"]["P1"]["n"] == 405


def _loaded(entry):
    return LoadedInsiderEntry(entry=entry, sha256="f" * 64, path=SHIPPED)


async def test_executor_writes_manifest_before_build_and_fails_closed(tmp_path):
    directory = tmp_path / "run"
    seen = {}

    async def build():
        seen["written"] = (directory / "manifest.json").exists() and (directory / "protocol.json").exists()
        raise RuntimeError("cube cache missing")

    result = await execute_insider_study(_loaded(ENTRY), directory, build=build, environment={"e": 1})
    assert seen == {"written": True}
    assert result == {"status": "failed", "error": "RuntimeError: cube cache missing", "authorizes_promotion": False}
    manifest = json.loads((directory / "manifest.json").read_text())
    assert (
        manifest["entry_id"] == "insider"
        and manifest["sha256"] == "f" * 64
        and manifest["authorizes_promotion"] is False
    )
    assert manifest["cube_sha256"] == ENTRY.cube_sha256 and manifest["environment"] == {"e": 1}
    assert json.loads((directory / "result.json").read_text())["status"] == "failed"


async def test_executor_fails_closed_on_cube_digest_mismatch(tmp_path):
    world = _world()

    async def build():
        return world

    result = await execute_insider_study(_loaded(ENTRY), tmp_path / "run", build=build, environment={})
    assert (
        result["status"] == "failed" and world.cube.sha256 in result["error"] and ENTRY.cube_sha256 in result["error"]
    )
    assert not (tmp_path / "run" / "legs.csv.gz").exists()


async def test_results_never_authorize_promotion(tmp_path):
    p = FIRST_ROW + 5
    transactions = {"S11": (_tx("S11", DAYS[p - 3], owner=1), _tx("S11", DAYS[p - 1], owner=2))}
    world = _world(transactions=transactions)
    entry = ENTRY.model_copy(update={"cube_sha256": world.cube.sha256})

    async def build():
        return world

    directory = tmp_path / "run"
    result = await execute_insider_study(_loaded(entry), directory, build=build, environment={})
    assert result["status"] == "completed" and result["authorizes_promotion"] is False
    assert result["decision"] == "failed" and result["labels"]["legs"] == 1
    assert result["inputs"] == {"cube_sha256": world.cube.sha256}
    for name in ("events.csv.gz", "legs.csv.gz", "controls.csv.gz", "result.json", "manifest.json", "protocol.json"):
        assert (directory / name).exists()
    saved = json.loads((directory / "result.json").read_text())
    assert saved["authorizes_promotion"] is False and saved["status"] == "completed"
    assert json.loads((directory / "manifest.json").read_text())["authorizes_promotion"] is False
