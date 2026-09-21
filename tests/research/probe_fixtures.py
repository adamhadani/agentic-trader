"""Shared paper-probe test fixtures/builders.

Used by tests/research/test_alpha_probe_registry.py and the CLI probe tests
(tests/cli/test_alpha_probe_cli.py). Keep names stable: `make_definition` and
`seed` are consumed by name from outside this module.
"""

from dataclasses import asdict

from agentic_trader.config import AppConfig
from agentic_trader.execution.durable import EventKind
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.validation import ValidationPolicy
from agentic_trader.storage.db import SignalDatabase


def criterion(value, status="pass"):
    return {"value": value, "status": status}


def qualification(**overrides):
    criteria = {
        "holdout_available": criterion(True),
        "holdout_sharpe": criterion(1.2, "fail"),
        "holdout_trade_count": criterion(8, "fail"),
        "cost_stress": criterion(3.0),
        "deployment_data_contract": criterion({}),
        "intraday_session_execution_unverified": criterion("1d"),
        "recursive_feature_requires_shared_initialization": criterion(False),
    }
    criteria.update(overrides)
    return {"qualified": False, "reasons": ["holdout_dsr"], "policy": asdict(ValidationPolicy()), "criteria": criteria}


def paper_database(tmp_path, *, paper=True, mode="alpaca"):
    config = AppConfig(execution_mode=mode, alpaca_paper=paper)
    return SignalDatabase(db_path=str(tmp_path / "probe.db"), config=config)


def make_definition(alpha_id="alpha_probe", symbol="AAPL", **changes):
    defaults = {"timeframe": "1d", "eligible_symbols": (symbol,), "data_feed": "alpaca:iex"}
    defaults.update(changes)
    return AlphaDefinition(alpha_id, "Probe", "delta(close,3)", **defaults)


async def seed(repository, definition, decision=None):
    await repository.register(definition, actor="test")
    async with repository.store.db.session_factory() as session, session.begin():
        await repository.store.lock(session, resource="alpha")
        await repository._append(
            session,
            f"qualification/{definition.version_id}",
            decision or qualification(),
            EventKind.ALPHA_RESEARCH,
            "fixture",
        )
