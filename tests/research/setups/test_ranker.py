"""Frozen setup-ranker artifact: loading, integrity checks and scoring parity with study.py."""

import hashlib
import json
import logging
import pickle

import numpy as np
import pandas as pd
import pytest

from agentic_trader.research.setups.features import FEATURES_VERSION
from agentic_trader.research.setups.ranker import load_ranker
from agentic_trader.research.setups.study import SetupStudyProtocol, _linear_payload, fit_scorer


FEATURES = [
    "mom_60",
    "resid_mom_60",
    "reward_risk",
    "setup_quality",
    "stop_atr",
    "strategy=BREAKOUT",
    "strategy=TREND_PULLBACK",
    "timeframe=4h",
]


def _training_frame(rows: int = 120, *, empty_feature: bool = True) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    frame = pd.DataFrame(
        {
            "mom_60": rng.uniform(0, 1, rows),
            # All-missing in training: SimpleImputer drops it, so linear scoring must too.
            # (HistGradientBoosting cannot bin an all-missing column, so HGB gets values.)
            "resid_mom_60": np.full(rows, np.nan) if empty_feature else rng.uniform(0, 1, rows),
            "reward_risk": rng.uniform(1.5, 3.0, rows),
            "setup_quality": rng.uniform(0, 1, rows),
            "stop_atr": rng.uniform(1.0, 3.0, rows),
            "strategy=BREAKOUT": np.where(np.arange(rows) % 2 == 0, 1.0, np.nan),
            "strategy=TREND_PULLBACK": np.where(np.arange(rows) % 2 == 1, 1.0, np.nan),
            "timeframe=4h": 1.0,
        }
    )
    frame.loc[::7, "mom_60"] = np.nan
    frame["r_cost"] = frame["setup_quality"] * 0.8 - frame["mom_60"].fillna(0.5) * 0.3 + rng.normal(0, 0.1, rows)
    frame["hit"] = np.where(frame["r_cost"] > 0.2, "target", "stop")
    return frame


def _protocol() -> SetupStudyProtocol:
    return SetupStudyProtocol.model_construct(logistic_C=1.0, ridge_alpha=1.0, hgb_params={"max_iter": 20})


def _write_artifact(directory, scorer: str, fitted, *, features_version: str = FEATURES_VERSION) -> None:
    doc: dict = {"scorer": scorer, "features_version": features_version, "features": list(fitted.features)}
    if scorer == "hgb":
        blob = pickle.dumps(fitted.model, protocol=5)
        (directory / "ranker.pkl").write_bytes(blob)
        doc["pickle_path"] = "ranker.pkl"
        doc["pickle_sha256"] = hashlib.sha256(blob).hexdigest()
    elif scorer in ("logistic", "ridge"):
        doc.update(_linear_payload(fitted))
    (directory / "ranker.json").write_text(json.dumps(doc))
    sha = hashlib.sha256((directory / "ranker.json").read_bytes()).hexdigest()
    (directory / "selection.json").write_text(json.dumps({"winner": scorer, "ranker_sha256": sha}))


def _vectors() -> list[dict[str, float]]:
    return [
        {
            "mom_60": 0.3,
            "resid_mom_60": 0.9,
            "reward_risk": 2.0,
            "setup_quality": 0.7,
            "stop_atr": 1.5,
            "strategy=TREND_PULLBACK": 1.0,
            "timeframe=4h": 1.0,
        },
        # NaN numeric feature is imputed; an unseen one-hot key is ignored; absent one-hots are 0.0.
        {
            "mom_60": float("nan"),
            "reward_risk": 2.5,
            "setup_quality": 0.2,
            "stop_atr": 2.0,
            "strategy=BREAKOUT": 1.0,
            "strategy=NEW_ONE": 1.0,
            "timeframe=1h": 1.0,
        },
    ]


@pytest.mark.filterwarnings("ignore::UserWarning")
@pytest.mark.parametrize("scorer", ["logistic", "ridge", "hgb"])
def test_loaded_ranker_scores_exactly_as_the_study_fitted_model(tmp_path, scorer):
    train = _training_frame(empty_feature=scorer != "hgb")
    fitted = fit_scorer(scorer, train, FEATURES, _protocol())
    _write_artifact(tmp_path, scorer, fitted)

    ranker = load_ranker(tmp_path / "ranker.json")
    assert ranker is not None
    assert ranker.sha256 == hashlib.sha256((tmp_path / "ranker.json").read_bytes()).hexdigest()
    for vector in _vectors():
        expected = float(fitted.predict(pd.DataFrame([vector]))[0])
        assert ranker.score(vector) == pytest.approx(expected, rel=1e-9, abs=1e-12)


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_setup_quality_winner_scores_the_setup_quality_feature(tmp_path):
    fitted = fit_scorer("setup_quality", _training_frame(), FEATURES, _protocol())
    _write_artifact(tmp_path, "setup_quality", fitted)
    ranker = load_ranker(tmp_path / "ranker.json")
    assert ranker is not None and ranker.score(_vectors()[0]) == 0.7


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_features_version_mismatch_is_rejected_with_a_warning(tmp_path, caplog):
    fitted = fit_scorer("ridge", _training_frame(), FEATURES, _protocol())
    _write_artifact(tmp_path, "ridge", fitted, features_version="setup_features_v0")
    with caplog.at_level(logging.WARNING):
        assert load_ranker(tmp_path / "ranker.json") is None
    assert "features_version" in caplog.text


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_selection_sha_mismatch_is_rejected(tmp_path, caplog):
    fitted = fit_scorer("ridge", _training_frame(), FEATURES, _protocol())
    _write_artifact(tmp_path, "ridge", fitted)
    (tmp_path / "selection.json").write_text(json.dumps({"winner": "ridge", "ranker_sha256": "0" * 64}))
    with caplog.at_level(logging.WARNING):
        assert load_ranker(tmp_path / "ranker.json") is None
    assert "sha256" in caplog.text


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_tampered_pickle_is_never_unpickled(tmp_path, monkeypatch):
    fitted = fit_scorer("hgb", _training_frame(empty_feature=False), FEATURES, _protocol())
    _write_artifact(tmp_path, "hgb", fitted)
    (tmp_path / "ranker.pkl").write_bytes(pickle.dumps({"tampered": True}))

    unpickled = []
    monkeypatch.setattr(pickle, "loads", lambda *args, **kwargs: unpickled.append(args))
    assert load_ranker(tmp_path / "ranker.json") is None
    assert unpickled == []  # the sha256 check ran first; load_ranker's own guard can't hide a call


def test_missing_artifact_is_rejected_not_raised(tmp_path):
    assert load_ranker(tmp_path / "absent.json") is None
