import json
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from click.testing import CliRunner

from agentic_trader.cli.main import cli
from agentic_trader.market.bars import TradingSession
from agentic_trader.research.alpha.persistent_study import PersistentStudyPlan, compute_persistent_study


@pytest.fixture
def persistent_input(panel_study_input):
    frames, clock, base = panel_study_input
    for f in frames.values():
        f.attrs["adjustment"] = "all"
    kwargs = {name: getattr(base, name) for name in base.__dataclass_fields__}
    plan = PersistentStudyPlan(
        **kwargs,
        blend_window=20,
        rebalance_sessions=10,
        borrow_bps=(0.0, 100.0, 300.0),
        funding_bps=(0.0, 300.0, 500.0),
    )
    sessions = tuple(
        TradingSession(t.date(), t + pd.Timedelta(hours=9, minutes=30), t + pd.Timedelta(hours=16)) for t in clock
    )
    return frames, clock, plan, sessions


def test_exact_protocol_roundtrip_and_budget(persistent_input):
    plan = persistent_input[2]
    assert plan.trial_count == 60
    assert PersistentStudyPlan.from_document(plan.document()) == plan
    document = plan.document()
    document["charged_trials"] -= 1
    with pytest.raises(ValueError):
        PersistentStudyPlan.from_document(document)
    with pytest.raises(ValueError):
        replace(plan, borrow_bps=(0.0,))


@pytest.mark.parametrize(
    "filename", ["persistent-etf-v1.json", "persistent-etf-iex-v1.json", "persistent-etf-iex-v2.json"]
)
def test_frozen_protocol_is_valid_and_bounded(filename):
    doc = json.loads((Path("config/research") / filename).read_text())
    plan = PersistentStudyPlan.from_document(doc)
    assert plan.trial_count == 80
    assert plan.adjustment == "all"


def test_complete_study_preserves_cash_clock_all_modes_and_later_fold_causality(persistent_input):
    frames, clock, plan, sessions = persistent_input
    result = compute_persistent_study(frames, clock, plan, sessions)
    assert result["charged_trials"] == 60 and not result["authorizes_promotion"]
    assert len(result["trials"]) == 6
    assert all(len(t["books"]) == 9 for t in result["trials"])
    for f in frames.values():
        f.iloc[120:, :4] *= 2
    changed = compute_persistent_study(frames, clock, plan, sessions)
    assert [t for t in result["trials"] if t["fold"] == "first"] == [
        t for t in changed["trials"] if t["fold"] == "first"
    ]


@pytest.mark.parametrize("fault", ["raw", "missing", "insufficient_warmup"])
def test_study_fails_closed_on_data_contract_or_warmup(persistent_input, fault):
    frames, clock, plan, sessions = persistent_input
    if fault == "raw":
        frames["AAA"].attrs["adjustment"] = "raw"
    if fault == "missing":
        frames["AAA"] = frames["AAA"].drop(clock[90])
    if fault == "insufficient_warmup":
        plan = replace(plan, blend_window=126)
    with pytest.raises(ValueError):
        compute_persistent_study(frames, clock, plan, sessions)


@pytest.mark.parametrize("fault", [None, "missing"])
def test_book_cli_uses_shared_journal_and_no_promotion(persistent_input, tmp_path, monkeypatch, fault):

    frames, clock, plan, sessions = persistent_input
    if fault:
        frames["AAA"] = frames["AAA"].drop(clock[90])
    source = SimpleNamespace(calendar=lambda *_: sessions, daily=lambda symbol, *_: frames[symbol].copy(deep=True))
    monkeypatch.setattr("agentic_trader.cli.commands.alpha.session_source", lambda *_: nullcontext(source))
    protocol = tmp_path / "protocol.json"
    protocol.write_text(json.dumps(plan.document()))
    output = tmp_path / "study"
    result = CliRunner().invoke(cli, ["alpha", "book-study", str(protocol), "--output", str(output)])
    assert (result.exit_code == 0) == (fault is None), result.output
    screen = json.loads((output / "screen.json").read_text())
    assert screen["charged_trials"] == 60 and not screen["authorizes_promotion"]
    assert len(screen["decisions"]) == (9 if fault is None else 0)
    status = CliRunner().invoke(cli, ["alpha", "status"])
    value = json.loads(status.output)
    assert value["research_family"]["trial_count"] == 60
    assert value["active"] == 0 and value["generation"] == 0
