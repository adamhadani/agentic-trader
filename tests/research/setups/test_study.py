import json
from datetime import UTC, date, datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest
import yaml
from pydantic import ValidationError

from agentic_trader.config import WORKSPACE_ROOT
from agentic_trader.research.setups.features import CROSS_SECTIONAL, MARKET, SECTOR_ETF, SETUP
from agentic_trader.research.setups.study import (
    HYPOTHESES,
    SCORERS,
    SetupStudyProtocol,
    execute_setup_study,
    holm,
    purged_walk_forward,
    random_selection,
    session_block_bootstrap,
    top_k_selection,
)


STRATEGIES = ("trend_pullback", "squeeze_breakout")
TIMEFRAMES = ("1h", "4h")


def _base_protocol_kwargs(**overrides) -> dict:
    fields = {
        "version": "setup_outcomes_v1",
        "universe_source": "synthetic test universe",
        "feed": "alpaca:sip",
        "adjustment": "all",
        "scan_times_et": ("10:35", "14:35"),
        "max_hold_sessions": 20,
        "cost_bps_per_side": (0.0, 5.0),
        "development": (date(2021, 1, 4), date(2021, 6, 30)),
        "holdout": (date(2021, 8, 2), date(2021, 10, 29)),
        "data_cutoff": date(2021, 12, 15),
        "embargo_sessions": 5,
        "bootstrap": {"block_mean": 3, "draws": 40, "seed": 20260923},
        "cv_folds": 3,
        "hgb_params": {
            "max_depth": 2,
            "learning_rate": 0.2,
            "max_iter": 25,
            "min_samples_leaf": 5,
            "random_state": 1,
        },
        "ridge_alpha": 1.0,
        "logistic_C": 1.0,
        "acceptance": {"top_k": 2, "min_sessions": 5, "ci": 0.90},
        "features_version": "setup_features_v1",
        "sector_etf": {"technology": "XLK"},
        "strategy_config": {"mode": "parallel"},
    }
    fields.update(overrides)
    return fields


def _base_protocol(**overrides) -> SetupStudyProtocol:
    return SetupStudyProtocol(**_base_protocol_kwargs(**overrides))


def _synthetic_frame(
    window: tuple[date, date],
    n_sessions: int,
    *,
    per_session: int = 5,
    seed: int = 0,
    signal_feature: str | None = None,
    signal_strength: float = 0.0,
    noise: float = 0.3,
    cost: float = 0.01,
    immature_last: int = 0,
) -> pd.DataFrame:
    """A synthetic frame with all the columns a real `development()`/`holdout()` would build."""
    rng = np.random.default_rng(seed)
    start, _ = window
    sessions = [start + timedelta(days=i) for i in range(n_sessions)]

    rows: list[dict] = []
    for session in sessions:
        for j in range(per_session):
            strategy = STRATEGIES[j % len(STRATEGIES)]
            timeframe = TIMEFRAMES[j % len(TIMEFRAMES)]

            row: dict = {feature: float(rng.normal()) for feature in CROSS_SECTIONAL}
            for feature in MARKET:
                row[feature] = float(rng.uniform(0.0, 1.0))
            row["setup_quality"] = float(rng.uniform(0.0, 1.0))
            row["stop_atr"] = float(rng.uniform(0.5, 2.0))
            row["reward_risk"] = float(rng.uniform(1.0, 3.0))
            assert set(SETUP) == {"setup_quality", "stop_atr", "reward_risk"}
            for strat in STRATEGIES:
                row[f"strategy={strat}"] = 1.0 if strat == strategy else 0.0
            for tf in TIMEFRAMES:
                row[f"timeframe={tf}"] = 1.0 if tf == timeframe else 0.0

            base = row[signal_feature] if signal_feature is not None else 0.0
            r = signal_strength * base + rng.normal(scale=noise)
            r_cost = r - cost
            hit = "target" if r > 0 else "stop"

            row.update(
                {
                    "decision_at": datetime.combine(session, time(10, 35), tzinfo=UTC),
                    "session": session,
                    "symbol": f"SYM{j}",
                    "strategy": strategy,
                    "timeframe": timeframe,
                    "direction": "LONG",
                    "hit": hit,
                    "r": float(r),
                    "r_cost": float(r_cost),
                }
            )
            rows.append(row)

    frame = pd.DataFrame(rows)
    if immature_last:
        frame.loc[frame.index[-immature_last:], "hit"] = "immature"
        frame.loc[frame.index[-immature_last:], ["r", "r_cost"]] = np.nan
    return frame


# --- Protocol -----------------------------------------------------------------------------


def test_protocol_identity_stable_and_changes_with_any_field():
    protocol = _base_protocol()
    assert protocol.identity == _base_protocol().identity

    for override in (
        {"ridge_alpha": 11.0},
        {"embargo_sessions": 6},
        {"scan_times_et": ("10:35",)},
        {"sector_etf": {"technology": "XLY"}},
    ):
        changed = _base_protocol(**override)
        assert changed.identity != protocol.identity, override


def test_protocol_rejects_holdout_inside_embargo():
    with pytest.raises(ValidationError):
        _base_protocol(
            development=(date(2021, 1, 4), date(2021, 6, 30)),
            holdout=(date(2021, 6, 30), date(2021, 10, 29)),
        )


def test_protocol_rejects_gap_shorter_than_embargo():
    # 2021-06-30 (Wed) -> 2021-07-06 (Tue): 3 weekdays in between, embargo 5.
    with pytest.raises(ValidationError):
        _base_protocol(
            development=(date(2021, 1, 4), date(2021, 6, 30)),
            holdout=(date(2021, 7, 6), date(2021, 10, 29)),
        )


# --- Bootstrap ------------------------------------------------------------------------------


def test_bootstrap_deterministic_with_seed():
    values = pd.Series(
        [0.1, 0.2, -0.1, 0.3, 0.05, -0.2, 0.15],
        index=[date(2021, 1, 1) + timedelta(days=i) for i in range(7)],
    )
    first = session_block_bootstrap(values, np.mean, block_mean=3, draws=50, seed=42)
    second = session_block_bootstrap(values, np.mean, block_mean=3, draws=50, seed=42)
    np.testing.assert_array_equal(first, second)

    third = session_block_bootstrap(values, np.mean, block_mean=3, draws=50, seed=7)
    assert not np.array_equal(first, third)


def test_bootstrap_resamples_whole_sessions():
    n = 30
    idx = [date(2021, 1, 1) + timedelta(days=i) for i in range(n)]
    concentrated = pd.Series([0.0] * (n - 1) + [100.0], index=idx)
    spread_out = pd.Series([100.0 / n] * n, index=idx)

    concentrated_draws = session_block_bootstrap(concentrated, np.mean, block_mean=3, draws=500, seed=1)
    spread_draws = session_block_bootstrap(spread_out, np.mean, block_mean=3, draws=500, seed=1)

    concentrated_width = np.percentile(concentrated_draws, 95) - np.percentile(concentrated_draws, 5)
    spread_width = np.percentile(spread_draws, 95) - np.percentile(spread_draws, 5)
    assert concentrated_width > spread_width * 5


def test_holm():
    adjusted = holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adjusted["a"] == pytest.approx(0.03)
    assert adjusted["c"] == pytest.approx(0.06)
    assert adjusted["b"] == pytest.approx(0.06)
    assert all(0.0 <= value <= 1.0 for value in adjusted.values())


# --- Selection ------------------------------------------------------------------------------


def test_top_k_groups_both_scans_of_a_day():
    session_day = date(2021, 1, 4)
    frame = pd.DataFrame(
        {
            "session": [session_day] * 3,
            "score": [0.9, 0.5, 0.1],
            "target": [1.0, 2.0, -5.0],
            "symbol": ["AAA", "BBB", "CCC"],
            "strategy": ["s1", "s1", "s1"],
        }
    )
    result = top_k_selection(frame, "score", "target", k=2)
    assert list(result.index) == [session_day]
    assert result[session_day] == pytest.approx((1.0 + 2.0) / 2)


def test_random_selection_is_session_mean():
    frame = pd.DataFrame(
        {
            "session": [date(2021, 1, 4)] * 3 + [date(2021, 1, 5)] * 2,
            "target": [1.0, 2.0, 3.0, -1.0, 1.0],
        }
    )
    result = random_selection(frame, "target", k=2)
    assert result[date(2021, 1, 4)] == pytest.approx(2.0)
    assert result[date(2021, 1, 5)] == pytest.approx(0.0)


def test_purged_walk_forward_respects_embargo():
    sessions = [date(2021, 1, 1) + timedelta(days=i) for i in range(40)]
    folds = purged_walk_forward(sessions, folds=4, embargo=5)
    assert len(folds) == 4
    for train, test in folds:
        assert test
        test_start_position = sessions.index(min(test))
        if train:
            assert max(train) < min(test)
            train_max_position = sessions.index(max(train))
            assert test_start_position - train_max_position > 5


# --- Study lifecycle --------------------------------------------------------------------


def test_holdout_not_called_before_ranker_saved(tmp_path):
    protocol = _base_protocol()
    directory = tmp_path / "study"
    dev_frame = _synthetic_frame(protocol.development, n_sessions=40, per_session=4, seed=1)
    hold_frame = _synthetic_frame(protocol.holdout, n_sessions=20, per_session=4, seed=2)

    def holdout_fn():
        assert (directory / "ranker.json").exists()
        assert (directory / "selection.json").exists()
        return hold_frame

    result = execute_setup_study(
        protocol,
        directory,
        development=lambda: dev_frame,
        holdout=holdout_fn,
        environment={"test": True},
    )
    assert result["winner"] in ("logistic", "ridge", "hgb")
    assert (directory / "protocol.json").exists()
    assert (directory / "manifest.json").exists()
    assert (directory / "development.json").exists()
    assert (directory / "holdout.json").exists()

    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["authorizes_promotion"] is False
    assert manifest["hypotheses"] == list(HYPOTHESES)


def test_development_failure_leaves_holdout_unexamined(tmp_path):
    protocol = _base_protocol()
    directory = tmp_path / "study"

    def development_fn():
        raise RuntimeError("boom")

    def holdout_fn():
        raise AssertionError("holdout must never be called after a development failure")

    result = execute_setup_study(
        protocol,
        directory,
        development=development_fn,
        holdout=holdout_fn,
        environment={},
    )
    assert result["status"] == "failed"
    assert not (directory / "holdout.json").exists()
    assert not (directory / "ranker.json").exists()
    saved = json.loads((directory / "development.json").read_text())
    assert saved["status"] == "failed"
    assert "boom" in saved["error"]


def test_planted_signal_detected(tmp_path):
    protocol = _base_protocol(
        development=(date(2021, 1, 4), date(2021, 6, 1)),
        holdout=(date(2021, 8, 2), date(2021, 12, 1)),
        data_cutoff=date(2022, 1, 15),
        acceptance={"top_k": 2, "min_sessions": 60, "ci": 0.90},
    )
    dev_frame = _synthetic_frame(
        protocol.development,
        n_sessions=140,
        per_session=6,
        seed=11,
        signal_feature="mom_60",
        signal_strength=3.0,
        noise=0.4,
    )
    hold_frame = _synthetic_frame(
        protocol.holdout,
        n_sessions=70,
        per_session=6,
        seed=12,
        signal_feature="mom_60",
        signal_strength=3.0,
        noise=0.4,
    )
    result = execute_setup_study(
        protocol,
        tmp_path / "study",
        development=lambda: dev_frame,
        holdout=lambda: hold_frame,
        environment={},
    )
    assert result["diff_ci90"][0] > 0
    assert result["acceptance_passed"] is True


def test_null_signal_not_accepted(tmp_path):
    protocol = _base_protocol(
        development=(date(2021, 1, 4), date(2021, 6, 1)),
        holdout=(date(2021, 8, 2), date(2021, 12, 1)),
        data_cutoff=date(2022, 1, 15),
        acceptance={"top_k": 2, "min_sessions": 60, "ci": 0.90},
        ridge_alpha=5.0,
    )
    dev_frame = _synthetic_frame(protocol.development, n_sessions=140, per_session=6, seed=1)
    hold_frame = _synthetic_frame(protocol.holdout, n_sessions=70, per_session=6, seed=2)
    result = execute_setup_study(
        protocol,
        tmp_path / "study",
        development=lambda: dev_frame,
        holdout=lambda: hold_frame,
        environment={},
    )
    assert result["acceptance_passed"] is False


def test_scorers_constant_matches_expected_names():
    assert SCORERS == ("setup_quality", "logistic", "ridge", "hgb")


# --- Committed protocol -----------------------------------------------------------------


def test_committed_protocol_matches_config_and_sector_etf():
    path = WORKSPACE_ROOT / "config" / "research" / "setup-outcomes-v1.json"
    data = json.loads(path.read_text())

    with open(WORKSPACE_ROOT / "config" / "config.yaml") as handle:
        config = yaml.safe_load(handle)

    assert data["strategy_config"] == config["strategies"]
    assert data["sector_etf"] == dict(SECTOR_ETF)

    # Also confirm the frozen document is a constructible, self-consistent protocol.
    protocol = SetupStudyProtocol(**data)
    assert protocol.features_version == "setup_features_v1"
    assert protocol.embargo_sessions == 20
