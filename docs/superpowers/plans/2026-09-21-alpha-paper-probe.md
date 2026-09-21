# Alpha Paper-Probe Lane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator enrol a mined alpha that clears a relaxed, versioned `ProbePolicy` into a time-boxed, risk-capped, tagged `probe` registry state that trades through the existing scan → Telegram card → FIFO → Alpaca bracket path on the Alpaca **paper** scope only.

**Architecture:** `probe` is a third list in the existing journaled alpha registry projection (no schema migration). A pure module assesses stored qualification criteria; one shared in-session function (`probe_block_reason`) decides whether a probe is live, and is used identically by the registry snapshot, the scan-time sweep and durable entry admission. Sizing receives an optional dollar-risk cap; everything downstream of admission is untouched.

**Tech Stack:** Python 3.14, `uv`, SQLAlchemy 2 async, pytest (async auto mode), Click, python-telegram-bot, Pydantic v2.

**Spec:** `docs/superpowers/specs/2026-09-21-alpha-paper-probe-design.md`

## Global Constraints

- Work only in the worktree `/Users/adamhadani/Development/agentic-trader-paper-probe` (branch `feat/alpha-paper-probe`). Never touch the installed checkout `/Users/adamhadani/Development/agentic-trader`; a live daemon runs from it.
- No Alembic revision, no new table, queue, daemon or Telegram poller. Schema head stays `008_alpha_pipeline`.
- `ValidationPolicy`, `promote`, and the `active` admission path must remain byte-for-byte equivalent in behaviour. Existing tests must pass unmodified unless a task says otherwise.
- Probe behaviour exists only when `WorkflowStore.scope` ends with `/alpaca:paper`. The simulator (`…/paper`) and `…/alpaca:live` are refused.
- Probes earn no shadow sessions, holdout credit or promotion credit. `AlphaShadowService.observe` is not changed.
- Leaving `probe` never calls the broker and never closes, cancels or modifies a position or order.
- POST/PATCH/DELETE broker calls are never retried; this plan adds none.
- Tests use temporary SQLite and blocked network I/O (root `tests/conftest.py`). Never relax those guards. PostgreSQL tests require `--run-postgres` and `TEST_POSTGRES_URL` naming a `test_*` database.
- Do not add compatibility aliases for internal callers; update callers in the same change.
- Before every commit: `uv run pre-commit run --all-files`. The commit hook runs the test suite; do not bypass it with `--no-verify`.
- Commit messages end with: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`
- Defaults (verbatim from spec): `probe_policy_v1`; holdout Sharpe `> 0.0`; cost-stressed return `> 0.0`; holdout trades `>= 5`; max term `180` days; default term `90` days; kill at cumulative realized R `<= -4.0`; `probe_risk_dollars = 100.0`; `max_probes = 3`.

## File Structure

| File | Responsibility |
| --- | --- |
| `agentic_trader/research/alpha/probe.py` (create) | Pure: `ProbePolicy`, `ProbeAssessment`, `assess_probe`, `forward_record`, `is_paper_scope`, constants. No storage imports. |
| `agentic_trader/storage/probe_state.py` (create) | In-session reads shared by registry and admission: `probe_block_reason`, `load_forward_record`. |
| `agentic_trader/storage/alpha.py` (modify) | `enrol_probe`, `sweep_probes`, `probe_report`, snapshot/supersession over three lists. |
| `agentic_trader/research/alpha/models.py` (modify) | `RegistrySnapshot.probe`. |
| `agentic_trader/config.py` (modify) | `AlphaPipelineConfig.max_probes / probe_risk_dollars / probe_term_days`. |
| `agentic_trader/storage/workflow.py` (modify) | `_alpha_entry_rejection` accepts live probes. |
| `agentic_trader/agent/position_sizing.py`, `agent/evaluator.py`, `screeners/base.py` (modify) | `risk_dollars_cap`, `ScreenerCandidate.probe`. |
| `agentic_trader/screeners/registry.py`, `screeners/formulaic.py`, `agent/copilot.py` (modify) | Install probes, type-based pickup, active-over-probe arbitration, sweep, provenance tag. |
| `agentic_trader/cli/commands/alpha.py`, `notifier/telegram_bot.py`, `presentation/formatters.py`, `storage/db.py` (modify) | Operator surface and tags. |
| `tests/research/test_alpha_probe.py`, `tests/research/test_alpha_probe_registry.py`, `tests/workflows/test_alpha_probe_admission.py`, `tests/agent/test_probe_sizing.py`, `tests/screeners/test_probe_scan.py`, `tests/integration/test_alpha_probe_postgres.py` (create) | Tests. |

---

### Task 1: Pure probe policy, assessment and forward record

**Files:**
- Create: `agentic_trader/research/alpha/probe.py`
- Test: `tests/research/test_alpha_probe.py`

**Interfaces:**
- Consumes: the stored qualification decision written by `AlphaQualificationService.qualify` — a dict whose `"criteria"` maps a code to `{"value", "threshold", "comparison", "status": "pass"|"fail"|"unavailable", "passed", "unavailable_reason", "reason_code"}` (see `research/alpha/promotion.py:_criterion`). Relevant codes: `holdout_available`, `holdout_sharpe`, `holdout_trade_count`, `cost_stress`, `deployment_data_contract`, `intraday_session_execution_unverified`, `recursive_feature_requires_shared_initialization`.
- Produces:
  - `PROBE_POLICY_VERSION = "probe_policy_v1"`, `PAPER_PROBE_TAG = "paper_probe"`
  - `ProbePolicy` (frozen dataclass; fields `version, min_holdout_sharpe, min_cost_stressed_return_pct, min_holdout_trades, max_term_days, kill_r`)
  - `ProbeAssessment(eligible: bool, reasons: tuple[str, ...], observations: dict[str, Any])` with `.to_dict()`
  - `assess_probe(qualification: dict | None, policy: ProbePolicy = ProbePolicy()) -> ProbeAssessment`
  - `forward_record(rows: Iterable[tuple[float | None, float | None]], policy: ProbePolicy = ProbePolicy()) -> dict` returning `{"trades": int, "unknown": int, "cumulative_r": float, "kill_r": float, "killed": bool}`; each row is `(realized_pnl, risk_dollars)`
  - `is_paper_scope(scope: str) -> bool`

- [ ] **Step 1: Write the failing tests**

Create `tests/research/test_alpha_probe.py`:

```python
import math
from dataclasses import asdict

import pytest

from agentic_trader.research.alpha.probe import (
    PROBE_POLICY_VERSION,
    ProbePolicy,
    assess_probe,
    forward_record,
    is_paper_scope,
)


def criterion(value, status="pass"):
    return {
        "value": value,
        "threshold": None,
        "comparison": "ge",
        "status": status,
        "passed": None if status == "unavailable" else status == "pass",
        "unavailable_reason": "boom" if status == "unavailable" else None,
        "reason_code": None,
    }


def decision(**overrides):
    criteria = {
        "holdout_available": criterion(True),
        "holdout_sharpe": criterion(1.31, "fail"),  # fails ValidationPolicy's 1.0? irrelevant: probe reads the value
        "holdout_trade_count": criterion(7, "fail"),
        "cost_stress": criterion(2.4),
        "deployment_data_contract": criterion({"feed": "alpaca:iex"}),
        "intraday_session_execution_unverified": criterion("1d"),
        "recursive_feature_requires_shared_initialization": criterion(False),
    }
    criteria.update(overrides)
    return {"qualified": False, "reasons": ["holdout_dsr"], "criteria": criteria}


def test_policy_defaults_are_the_frozen_spec_values():
    assert asdict(ProbePolicy()) == {
        "version": PROBE_POLICY_VERSION,
        "min_holdout_sharpe": 0.0,
        "min_cost_stressed_return_pct": 0.0,
        "min_holdout_trades": 5,
        "max_term_days": 180,
        "kill_r": -4.0,
    }


def test_statistically_rejected_but_economically_positive_candidate_is_eligible():
    result = assess_probe(decision())
    assert result.eligible and result.reasons == ()
    assert result.observations == {"holdout_sharpe": 1.31, "holdout_trade_count": 7, "cost_stress": 2.4}


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"holdout_sharpe": criterion(0.0, "fail")}, "holdout_sharpe_below_probe_floor"),
        ({"holdout_sharpe": criterion(-0.2, "fail")}, "holdout_sharpe_below_probe_floor"),
        ({"cost_stress": criterion(0.0, "fail")}, "cost_stress_below_probe_floor"),
        ({"holdout_trade_count": criterion(4, "fail")}, "holdout_trade_count_below_probe_floor"),
        ({"holdout_sharpe": criterion(None, "unavailable")}, "holdout_sharpe_unavailable"),
        ({"cost_stress": criterion(math.nan)}, "cost_stress_invalid"),
        ({"holdout_trade_count": criterion(True)}, "holdout_trade_count_invalid"),
        ({"holdout_available": criterion(False, "unavailable")}, "holdout_available_failed"),
        ({"deployment_data_contract": criterion({}, "fail")}, "deployment_data_contract_failed"),
        (
            {"intraday_session_execution_unverified": criterion("4h", "fail")},
            "intraday_session_execution_unverified_failed",
        ),
        (
            {"recursive_feature_requires_shared_initialization": criterion(True, "fail")},
            "recursive_feature_requires_shared_initialization_failed",
        ),
    ],
)
def test_each_probe_criterion_fails_closed(override, reason):
    result = assess_probe(decision(**override))
    assert not result.eligible
    assert reason in result.reasons


def test_missing_document_or_criteria_is_rejected_not_treated_as_zero():
    assert assess_probe(None).reasons == ("qualification_missing",)
    missing = decision()
    del missing["criteria"]["cost_stress"]
    assert "cost_stress_missing" in assess_probe(missing).reasons


def test_forward_record_sums_r_and_ignores_unknown_outcomes():
    record = forward_record([(50.0, 100.0), (-100.0, 100.0), (None, 100.0), (10.0, 0.0), (math.inf, 100.0)])
    assert record == {"trades": 2, "unknown": 3, "cumulative_r": -0.5, "kill_r": -4.0, "killed": False}


def test_forward_record_kills_at_the_threshold_inclusive():
    assert forward_record([(-100.0, 100.0)] * 4)["killed"] is True
    assert forward_record([(-100.0, 100.0)] * 3 + [(-99.0, 100.0)])["killed"] is False


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        ("production/alpaca:paper", True),
        ("development/alpaca:paper", True),
        ("production/alpaca:live", False),
        ("production/paper", False),
        ("production/unknown", False),
        ("alpaca:paper", False),
    ],
)
def test_only_brokerage_paper_scope_is_a_probe_scope(scope, expected):
    assert is_paper_scope(scope) is expected
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/research/test_alpha_probe.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'agentic_trader.research.alpha.probe'`.

- [ ] **Step 3: Implement**

Create `agentic_trader/research/alpha/probe.py`:

```python
"""Paper-probe (incubation) policy: pure assessment over stored qualification evidence.

A probe is a time-boxed, risk-capped paper-account trial. It is not qualification:
it reads already-recorded criterion observations and never touches price data, so
enrolment consumes no holdout and charges no research trial.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from numbers import Real
from typing import Any


PROBE_POLICY_VERSION = "probe_policy_v1"
PAPER_PROBE_TAG = "paper_probe"
PAPER_SCOPE_SUFFIX = "/alpaca:paper"

# Structural contracts shared with promotion; each must have status "pass".
STRUCTURAL_CRITERIA = (
    "holdout_available",
    "deployment_data_contract",
    "intraday_session_execution_unverified",
    "recursive_feature_requires_shared_initialization",
)


@dataclass(frozen=True)
class ProbePolicy:
    version: str = PROBE_POLICY_VERSION
    min_holdout_sharpe: float = 0.0
    min_cost_stressed_return_pct: float = 0.0
    min_holdout_trades: int = 5
    max_term_days: int = 180
    kill_r: float = -4.0


@dataclass(frozen=True)
class ProbeAssessment:
    eligible: bool
    reasons: tuple[str, ...]
    observations: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"eligible": self.eligible, "reasons": list(self.reasons), "observations": dict(self.observations)}


def is_paper_scope(scope: str) -> bool:
    """Only the brokerage paper account; the local simulator scope is ``…/paper``."""
    return scope.endswith(PAPER_SCOPE_SUFFIX)


def _finite(value: Any) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def assess_probe(qualification: dict | None, policy: ProbePolicy | None = None) -> ProbeAssessment:
    policy = policy or ProbePolicy()
    if not qualification:
        return ProbeAssessment(False, ("qualification_missing",))
    criteria = qualification.get("criteria") or {}
    reasons: list[str] = []
    observations: dict[str, Any] = {}
    for code in STRUCTURAL_CRITERIA:
        criterion = criteria.get(code)
        if criterion is None:
            reasons.append(f"{code}_missing")
        elif criterion.get("status") != "pass":
            reasons.append(f"{code}_failed")
    floors = (
        ("holdout_sharpe", policy.min_holdout_sharpe, True),
        ("cost_stress", policy.min_cost_stressed_return_pct, True),
        ("holdout_trade_count", policy.min_holdout_trades, False),
    )
    for code, floor, strict in floors:
        criterion = criteria.get(code)
        if criterion is None:
            reasons.append(f"{code}_missing")
            continue
        if criterion.get("status") == "unavailable":
            reasons.append(f"{code}_unavailable")
            continue
        value = criterion.get("value")
        if not _finite(value):
            reasons.append(f"{code}_invalid")
            continue
        observations[code] = value
        if value <= floor if strict else value < floor:
            reasons.append(f"{code}_below_probe_floor")
    return ProbeAssessment(not reasons, tuple(reasons), observations)


def forward_record(
    rows: Iterable[tuple[float | None, float | None]], policy: ProbePolicy | None = None
) -> dict[str, Any]:
    """Cumulative realized R from reconciled closes; unknown outcomes never kill or protect."""
    policy = policy or ProbePolicy()
    trades, unknown, cumulative = 0, 0, 0.0
    for realized_pnl, risk_dollars in rows:
        if not _finite(realized_pnl) or not _finite(risk_dollars) or risk_dollars <= 0:
            unknown += 1
            continue
        trades += 1
        cumulative += realized_pnl / risk_dollars
    return {
        "trades": trades,
        "unknown": unknown,
        "cumulative_r": cumulative,
        "kill_r": policy.kill_r,
        "killed": trades > 0 and cumulative <= policy.kill_r,
    }


def policy_document(policy: ProbePolicy | None = None) -> dict[str, Any]:
    return asdict(policy or ProbePolicy())
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/research/test_alpha_probe.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
uv run pre-commit run --all-files
git add agentic_trader/research/alpha/probe.py tests/research/test_alpha_probe.py
git commit -m "feat(alpha): add pure paper-probe policy and forward record

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Registry enrolment, renewal, liveness, sweep and report

**Files:**
- Create: `agentic_trader/storage/probe_state.py`
- Modify: `agentic_trader/research/alpha/models.py` (`RegistrySnapshot`, ~line 226)
- Modify: `agentic_trader/config.py` (`AlphaPipelineConfig`, ~line 514)
- Modify: `agentic_trader/storage/alpha.py` (`_change` ~242-309, `snapshot` ~320-334, new methods after `demote`)
- Test: `tests/research/test_alpha_probe_registry.py`

**Interfaces:**
- Consumes (Task 1): `ProbePolicy`, `assess_probe`, `forward_record`, `is_paper_scope`, `policy_document`.
- Produces:
  - `RegistrySnapshot.probe: tuple[AlphaDefinition, ...] = ()` (third field, defaulted).
  - `AlphaPipelineConfig.max_probes: int = 3`, `.probe_risk_dollars: float = 100.0`, `.probe_term_days: int = 90`.
  - `storage.probe_state.load_forward_record(session, db, version_id, since, policy) -> dict`
  - `storage.probe_state.probe_block_reason(session, *, scope, db, version_id, now, policy=None) -> str | None` — `None` means the probe may take new risk.
  - `AlphaRepository.enrol_probe(version_id, *, actor, expected_generation, days=None, renew=False, now=None) -> int`
  - `AlphaRepository.sweep_probes(*, now=None) -> list[str]` (version ids retired)
  - `AlphaRepository.probe_report(*, now=None) -> list[dict]`
  - `AlphaRepository.snapshot(*, now=None)` returns only **live** probes in `.probe`.
  - Enrolment record at key `probe/{version_id}`: `{"policy", "assessment", "first_enrolled_at", "enrolled_at", "expires_at", "term_days", "renewals", "actor"}` (ISO-8601 UTC strings).

- [ ] **Step 1: Write the failing tests**

Create `tests/research/test_alpha_probe_registry.py`:

```python
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from agentic_trader.config import AppConfig
from agentic_trader.execution.durable import EventKind, WorkKind
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.probe import policy_document
from agentic_trader.research.alpha.validation import ValidationPolicy
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.models import WorkItemRecord


NOW = datetime(2026, 9, 21, 15, 0, tzinfo=UTC)


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


@pytest.fixture
async def db(tmp_path):
    database = paper_database(tmp_path)
    await database.init_db()
    yield database
    await database.engine.dispose()


@pytest.fixture
def repository(db):
    return AlphaRepository(db.workflows)


def make_definition(alpha_id="alpha_probe", symbol="AAPL", **changes):
    return AlphaDefinition(
        alpha_id,
        "Probe",
        "delta(close,3)",
        timeframe="1d",
        eligible_symbols=(symbol,),
        data_feed="alpaca:iex",
        **changes,
    )


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


async def close_trade(db, definition, pnl, *, risk=100.0, when=NOW):
    sid = await db.record_signal(
        definition.eligible_symbols[0],
        definition.alpha_id,
        "LONG",
        100,
        98,
        104,
        risk,
        asset_class="EQUITY",
        quantity=1,
        timeframe="1d",
        alpha_version=definition.version_id,
        alpha_policy=definition.execution.to_dict(),
    )
    async with db.session_factory() as session, session.begin():
        from agentic_trader.storage.models import SignalRecord

        row = await session.get(SignalRecord, sid)
        row.status, row.realized_pnl, row.exit_timestamp = "CLOSED_LOSS" if pnl < 0 else "CLOSED_WIN", pnl, when
    return sid


async def test_enrolment_journals_state_and_snapshot_exposes_the_probe(repository):
    definition = make_definition()
    await seed(repository, definition)
    generation = await repository.enrol_probe(definition.version_id, actor="op", expected_generation=0, now=NOW)
    assert generation == 1
    snapshot = await repository.snapshot(now=NOW)
    assert snapshot.probe == (definition,) and not snapshot.active and not snapshot.shadow
    record = await repository.get(f"probe/{definition.version_id}")
    assert record["policy"] == policy_document()
    assert record["term_days"] == 90 and record["renewals"] == 0
    assert record["expires_at"] == (NOW + timedelta(days=90)).isoformat()
    assert record["first_enrolled_at"] == record["enrolled_at"] == NOW.isoformat()
    await repository.rebuild()
    assert (await repository.snapshot(now=NOW)).probe == (definition,)


@pytest.mark.parametrize(("paper", "mode"), [(False, "alpaca"), (True, "paper")])
async def test_only_the_brokerage_paper_scope_may_enrol(tmp_path, paper, mode):
    database = paper_database(tmp_path, paper=paper, mode=mode)
    await database.init_db()
    repository = AlphaRepository(database.workflows)
    definition = make_definition()
    await seed(repository, definition)
    with pytest.raises(ValueError, match="Alpaca paper"):
        await repository.enrol_probe(definition.version_id, actor="op", expected_generation=0, now=NOW)
    await database.engine.dispose()


@pytest.mark.parametrize(
    ("definition", "decision", "message"),
    [
        (make_definition(timeframe="4h"), qualification(), "native daily"),
        (make_definition(data_feed="yfinance"), qualification(), "deployment feed"),
        (make_definition(), qualification(cost_stress=criterion(-1.0, "fail")), "cost_stress_below_probe_floor"),
        (make_definition(), None, "qualification_missing"),
    ],
)
async def test_ineligible_candidates_are_refused_with_reasons(repository, definition, decision, message):
    await repository.register(definition, actor="test")
    if decision is not None:
        async with repository.store.db.session_factory() as session, session.begin():
            await repository._append(
                session, f"qualification/{definition.version_id}", decision, EventKind.ALPHA_RESEARCH, "fixture"
            )
    with pytest.raises(ValueError, match=message):
        await repository.enrol_probe(definition.version_id, actor="op", expected_generation=0, now=NOW)
    assert not (await repository.snapshot(now=NOW)).probe


async def test_stale_generation_and_term_bounds_are_refused(repository):
    definition = make_definition()
    await seed(repository, definition)
    with pytest.raises(ValueError, match="Registry changed"):
        await repository.enrol_probe(definition.version_id, actor="op", expected_generation=7, now=NOW)
    for days in (0, 181):
        with pytest.raises(ValueError, match="term"):
            await repository.enrol_probe(definition.version_id, actor="op", expected_generation=0, days=days, now=NOW)


async def test_symbol_ownership_and_slot_limit_ignore_expired_probes(repository):
    first, rival = make_definition(), make_definition("alpha_rival")
    await seed(repository, first)
    await seed(repository, rival)
    await repository.enrol_probe(first.version_id, actor="op", expected_generation=0, days=1, now=NOW)
    with pytest.raises(ValueError, match="already has an alpha owner"):
        await repository.enrol_probe(rival.version_id, actor="op", expected_generation=1, now=NOW)
    later = NOW + timedelta(days=2)
    # No sweep has run; the expired enrolment must not block the symbol.
    assert await repository.enrol_probe(rival.version_id, actor="op", expected_generation=1, now=later) == 2
    assert (await repository.snapshot(now=later)).probe == (rival,)


async def test_slot_limit(repository):
    repository.policy = repository.policy.model_copy(update={"max_probes": 1})
    first, second = make_definition(), make_definition("alpha_second", "MSFT")
    await seed(repository, first)
    await seed(repository, second)
    await repository.enrol_probe(first.version_id, actor="op", expected_generation=0, now=NOW)
    with pytest.raises(ValueError, match="probe slots"):
        await repository.enrol_probe(second.version_id, actor="op", expected_generation=1, now=NOW)


async def test_a_version_lives_in_exactly_one_list_and_new_versions_supersede(repository):
    definition = make_definition()
    await seed(repository, definition)
    await repository.set_shadow(definition.version_id, actor="op", expected_generation=0)
    await repository.enrol_probe(definition.version_id, actor="op", expected_generation=1, now=NOW)
    snapshot = await repository.snapshot(now=NOW)
    assert snapshot.probe == (definition,) and not snapshot.shadow
    successor = replace(definition, expression="delta(close,5)")
    await seed(repository, successor)
    await repository.set_shadow(successor.version_id, actor="op", expected_generation=2)
    snapshot = await repository.snapshot(now=NOW)
    assert snapshot.shadow == (successor,) and not snapshot.probe


async def test_renewal_extends_from_now_and_requires_membership(repository):
    definition = make_definition()
    await seed(repository, definition)
    with pytest.raises(ValueError, match="not a current probe"):
        await repository.enrol_probe(definition.version_id, actor="op", expected_generation=0, renew=True, now=NOW)
    await repository.enrol_probe(definition.version_id, actor="op", expected_generation=0, days=30, now=NOW)
    later = NOW + timedelta(days=20)
    await repository.enrol_probe(
        definition.version_id, actor="op", expected_generation=1, days=30, renew=True, now=later
    )
    record = await repository.get(f"probe/{definition.version_id}")
    assert record["renewals"] == 1
    assert record["first_enrolled_at"] == NOW.isoformat()
    assert record["expires_at"] == (later + timedelta(days=30)).isoformat()


async def test_kill_rule_is_sticky_across_renewal_and_re_enrolment(db, repository):
    definition = make_definition()
    await seed(repository, definition)
    await repository.enrol_probe(definition.version_id, actor="op", expected_generation=0, now=NOW)
    for _ in range(4):
        await close_trade(db, definition, -100.0, when=NOW + timedelta(days=1))
    later = NOW + timedelta(days=2)
    assert not (await repository.snapshot(now=later)).probe
    with pytest.raises(ValueError, match="kill"):
        await repository.enrol_probe(definition.version_id, actor="op", expected_generation=1, renew=True, now=later)
    assert await repository.sweep_probes(now=later) == [definition.version_id]
    with pytest.raises(ValueError, match="kill"):
        await repository.enrol_probe(definition.version_id, actor="op", expected_generation=2, now=later)


async def test_trades_before_first_enrolment_do_not_count(db, repository):
    definition = make_definition()
    await seed(repository, definition)
    for _ in range(5):
        await close_trade(db, definition, -100.0, when=NOW - timedelta(days=1))
    await repository.enrol_probe(definition.version_id, actor="op", expected_generation=0, now=NOW)
    report = await repository.probe_report(now=NOW)
    assert report[0]["forward"]["trades"] == 0 and report[0]["live"] is True


async def test_sweep_retires_expired_probes_once_with_one_notice(db, repository):
    definition = make_definition()
    await seed(repository, definition)
    await repository.enrol_probe(definition.version_id, actor="op", expected_generation=0, days=1, now=NOW)
    assert await repository.sweep_probes(now=NOW) == []
    later = NOW + timedelta(days=2)
    assert await repository.sweep_probes(now=later) == [definition.version_id]
    assert await repository.sweep_probes(now=later) == []
    registry = await repository.get("registry")
    assert registry["probe"] == [] and registry["generation"] == 2
    async with db.session_factory() as session:
        notices = (
            await session.scalars(select(WorkItemRecord).where(WorkItemRecord.kind == WorkKind.NOTIFICATION))
        ).all()
    assert len(notices) == 1 and "expired" in notices[0].payload


async def test_legacy_registry_payload_without_probe_key_still_reads(repository):
    definition = make_definition()
    await seed(repository, definition)
    async with repository.store.db.session_factory() as session, session.begin():
        await repository._append(
            session, "registry", {"generation": 3, "active": [], "shadow": []}, EventKind.ALPHA_REGISTRY, "fixture"
        )
    assert (await repository.snapshot(now=NOW)).probe == ()
    assert await repository.enrol_probe(definition.version_id, actor="op", expected_generation=3, now=NOW) == 4


async def test_demote_removes_a_probe(repository):
    definition = make_definition()
    await seed(repository, definition)
    await repository.enrol_probe(definition.version_id, actor="op", expected_generation=0, now=NOW)
    await repository.demote(definition.version_id, actor="op", expected_generation=1)
    assert not (await repository.snapshot(now=NOW)).probe
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/research/test_alpha_probe_registry.py -q`
Expected: failures — `AttributeError: 'AlphaRepository' object has no attribute 'enrol_probe'` and `TypeError: snapshot() got an unexpected keyword argument 'now'`.

If `WorkKind` is not exported from `agentic_trader.execution.durable`, locate it with `grep -rn "class WorkKind" agentic_trader` and fix the test import; do not change production code for this.

- [ ] **Step 3: Add config fields**

In `agentic_trader/config.py`, inside `class AlphaPipelineConfig`, after `qualification_max_age_days`:

```python
    max_probes: int = Field(default=3, ge=0)
    probe_risk_dollars: float = Field(default=100.0, gt=0)
    probe_term_days: int = Field(default=90, ge=1, le=180)
```

- [ ] **Step 4: Extend the snapshot model**

In `agentic_trader/research/alpha/models.py`:

```python
@dataclass(frozen=True)
class RegistrySnapshot:
    generation: int
    active: tuple[AlphaDefinition, ...]
    shadow: tuple[AlphaDefinition, ...]
    probe: tuple[AlphaDefinition, ...] = ()
```

- [ ] **Step 5: Create the shared in-session state module**

Create `agentic_trader/storage/probe_state.py`:

```python
"""In-transaction probe liveness shared by the registry, the sweep and entry admission.

Admission (``WorkflowStore``) cannot import ``AlphaRepository`` without a cycle, so
the single source of truth for "may this probe take new risk" lives here.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentic_trader.constants import SignalStatus
from agentic_trader.research.alpha.probe import ProbePolicy, forward_record, is_paper_scope, policy_document
from agentic_trader.storage.models import AlphaProjectionRecord, SignalRecord


CLOSED_STATUSES = (SignalStatus.CLOSED_WIN, SignalStatus.CLOSED_LOSS, SignalStatus.CLOSED_MANUAL)


async def load_enrolment(session: AsyncSession, scope: str, version_id: str) -> dict | None:
    row = await session.get(AlphaProjectionRecord, (scope, f"probe/{version_id}"))
    return json.loads(row.payload) if row else None


async def load_forward_record(session: AsyncSession, db, version_id: str, since: datetime, policy: ProbePolicy) -> dict:
    rows = await session.execute(
        select(SignalRecord.realized_pnl, SignalRecord.risk_dollars).where(
            SignalRecord.alpha_version == version_id,
            SignalRecord.environment == db.environment,
            SignalRecord.execution_mode == db.execution_mode,
            SignalRecord.status.in_(CLOSED_STATUSES),
            SignalRecord.exit_timestamp >= since,
        )
    )
    return forward_record([(pnl, risk) for pnl, risk in rows], policy)


async def probe_block_reason(
    session: AsyncSession, *, scope: str, db, version_id: str, now: datetime, policy: ProbePolicy | None = None
) -> str | None:
    """Return why this probe may not take new risk, or ``None`` when it is live."""
    policy = policy or ProbePolicy()
    if not is_paper_scope(scope):
        return "paper probes run only on the Alpaca paper account"
    enrolment = await load_enrolment(session, scope, version_id)
    if enrolment is None:
        return "probe enrolment evidence is missing"
    if enrolment.get("policy") != policy_document(policy):
        return "probe policy changed; renew the probe under the current policy"
    if datetime.fromisoformat(enrolment["expires_at"]) <= now:
        return "probe term expired; renew it or let it retire"
    record = await load_forward_record(
        session, db, version_id, datetime.fromisoformat(enrolment["first_enrolled_at"]), policy
    )
    if record["killed"]:
        return f"probe kill rule reached ({record['cumulative_r']:.2f}R <= {policy.kill_r:.2f}R)"
    return None
```

Confirm `SignalRecord.environment`/`execution_mode` are what `record_signal` stamps: `grep -n "execution_mode=" agentic_trader/storage/db.py`. They are set from `self.environment` / `self.execution_mode`.

- [ ] **Step 6: Implement registry behaviour in `agentic_trader/storage/alpha.py`**

Add imports near the top:

```python
from datetime import UTC, datetime, timedelta

from agentic_trader.execution.durable import EventKind, NotificationKind
from agentic_trader.research.alpha.probe import ProbePolicy, assess_probe, is_paper_scope, policy_document
from agentic_trader.storage.probe_state import load_enrolment, load_forward_record, probe_block_reason
```

(merge with the existing `datetime` and `EventKind` imports rather than duplicating them).

Add `probe_policy` to the constructor:

```python
    def __init__(
        self,
        store: WorkflowStore,
        policy: AlphaPipelineConfig | None = None,
        *,
        validation_policy: ValidationPolicy | None = None,
        probe_policy: ProbePolicy | None = None,
    ):
        self.store = store
        self.policy = policy or AlphaPipelineConfig()
        self.validation_policy = validation_policy or ValidationPolicy()
        self.probe_policy = probe_policy or ProbePolicy()
```

Change the `_change` signature and the registry default, and add the probe branch. The registry default literal appears in `_change` and `snapshot`; replace both with a module-level helper:

```python
def _empty_registry():
    return {"generation": 0, "active": [], "shadow": [], "probe": []}


def _normalized(registry):
    return {**_empty_registry(), **registry} if registry else _empty_registry()
```

In `_change`:

```python
    async def _change(self, version_id, *, actor, expected_generation, mode, days=None, renew=False, now=None):
        now = now or datetime.now(UTC)
        async with self.store.db.session_factory() as session, session.begin():
            await self.store.lock(session, resource="alpha")
            version = await self._get(session, f"version/{version_id}")
            if not version:
                raise ValueError("Unknown alpha version")
            registry = _normalized(await self._get(session, REGISTRY_KEY))
            if registry["generation"] != expected_generation:
                raise ValueError("Registry changed; refresh generation before retrying")
            definition = AlphaDefinition.from_dict(version["definition"])
            if mode == "probe":
                await self._authorize_probe(session, registry, definition, days=days, renew=renew, actor=actor, now=now)
            if mode == "active":
                ...  # existing block unchanged
```

Inside the existing `mode == "active"` block, extend the one-owner check so a live probe also owns its symbols. Replace the `for current_id in registry["active"]:` loop header with:

```python
                for current_id in (*registry["active"], *await self._live_probes(session, registry, now)):
```

Replace the supersession loop's field tuple and the append condition:

```python
            for field in ("active", "shadow", "probe"):
                keep = []
                for existing in registry[field]:
                    old = await self._get(session, f"version/{existing}")
                    if old["definition"]["alpha_id"] != definition.alpha_id:
                        keep.append(existing)
                registry[field] = keep
            if mode in ("active", "shadow", "probe"):
                registry[mode].append(version_id)
                registry[mode].sort()
```

Add the helpers and public methods after `demote`:

```python
async def _live_probes(self, session, registry, now):
    live = []
    for version_id in registry["probe"]:
        if (
            await probe_block_reason(
                session,
                scope=self.store.scope,
                db=self.store.db,
                version_id=version_id,
                now=now,
                policy=self.probe_policy,
            )
            is None
        ):
            live.append(version_id)
    return live


async def _authorize_probe(self, session, registry, definition, *, days, renew, actor, now):
    version_id = definition.version_id
    if not is_paper_scope(self.store.scope):
        raise ValueError("Paper probes run only on the Alpaca paper account scope")
    days = self.policy.probe_term_days if days is None else days
    if type(days) is not int or not 1 <= days <= self.probe_policy.max_term_days:
        raise ValueError(f"Probe term must be 1..{self.probe_policy.max_term_days} days")
    if definition.clock is not None or definition.timeframe != "1d":
        raise ValueError("Paper probes require an unclocked native daily definition")
    if definition.data_feed not in ("alpaca:iex", "alpaca:sip"):
        raise ValueError("Paper probes require an explicit deployment feed")
    if not definition.eligible_symbols:
        raise ValueError("Paper probes require an explicit symbol universe")
    assessment = assess_probe(await self._get(session, f"qualification/{version_id}"), self.probe_policy)
    if not assessment.eligible:
        raise ValueError("Probe policy not met: " + ", ".join(assessment.reasons))
    previous = await load_enrolment(session, self.store.scope, version_id)
    first = datetime.fromisoformat(previous["first_enrolled_at"]) if previous else now
    record = await load_forward_record(session, self.store.db, version_id, first, self.probe_policy)
    if record["killed"]:
        raise ValueError(
            f"Probe kill rule reached ({record['cumulative_r']:.2f}R); this version cannot be enrolled again"
        )
    if renew and version_id not in registry["probe"]:
        raise ValueError("Version is not a current probe; enrol it instead of renewing")
    live = await self._live_probes(session, registry, now)
    others = [v for v in live if v != version_id]
    if len(others) >= self.policy.max_probes:
        raise ValueError(f"All {self.policy.max_probes} probe slots are in use")
    for current_id in (*registry["active"], *others):
        current = await self._get(session, f"version/{current_id}")
        incumbent = AlphaDefinition.from_dict(current["definition"])
        if incumbent.alpha_id != definition.alpha_id and set(incumbent.eligible_symbols or ()) & set(
            definition.eligible_symbols
        ):
            raise ValueError("Instrument already has an alpha owner; demote it or wait for its probe to end")
    payload = {
        "policy": policy_document(self.probe_policy),
        "assessment": assessment.to_dict(),
        "first_enrolled_at": first.isoformat(),
        "enrolled_at": now.isoformat(),
        "expires_at": (now + timedelta(days=days)).isoformat(),
        "term_days": days,
        "renewals": (previous["renewals"] + 1) if renew and previous else 0,
        "actor": actor,
    }
    await self._append(session, f"probe/{version_id}", payload, EventKind.ALPHA_REGISTRY, actor)


async def enrol_probe(self, version_id, *, actor, expected_generation, days=None, renew=False, now=None):
    return await self._change(
        version_id, actor=actor, expected_generation=expected_generation, mode="probe", days=days, renew=renew, now=now
    )


async def sweep_probes(self, *, now=None) -> list[str]:
    """Make derived expiry/kill durable. Correctness never depends on this running."""
    now = now or datetime.now(UTC)
    retired: list[str] = []
    async with self.store.db.session_factory() as session, session.begin():
        # Lock order everywhere: trading admission, then alpha registry.
        await self.store.lock(session)
        await self.store.lock(session, resource="alpha")
        registry = _normalized(await self._get(session, REGISTRY_KEY))
        for version_id in list(registry["probe"]):
            reason = await probe_block_reason(
                session,
                scope=self.store.scope,
                db=self.store.db,
                version_id=version_id,
                now=now,
                policy=self.probe_policy,
            )
            if reason is None:
                continue
            registry["probe"].remove(version_id)
            retired.append(version_id)
            enrolment = await load_enrolment(session, self.store.scope, version_id) or {}
            version = await self._get(session, f"version/{version_id}")
            await self.store.add_notification(
                session,
                f"alpha-probe/{version_id}/retired/{enrolment.get('enrolled_at', 'unknown')}",
                NotificationKind.MESSAGE,
                {
                    "text": f"🧪 Paper probe {version['definition']['alpha_id']} retired: {reason}. "
                    "Open positions keep their broker-held protection.",
                    "formatted": False,
                },
            )
        if retired:
            registry["generation"] += 1
            await self._append(session, REGISTRY_KEY, registry, EventKind.ALPHA_REGISTRY, "probe_sweep")
    return retired


async def probe_report(self, *, now=None) -> list[dict]:
    now = now or datetime.now(UTC)
    async with self.store.db.session_factory() as session:
        registry = _normalized(await self._get(session, REGISTRY_KEY))
        report = []
        for version_id in registry["probe"]:
            version = await self._get(session, f"version/{version_id}")
            enrolment = await load_enrolment(session, self.store.scope, version_id)
            reason = await probe_block_reason(
                session,
                scope=self.store.scope,
                db=self.store.db,
                version_id=version_id,
                now=now,
                policy=self.probe_policy,
            )
            forward = await load_forward_record(
                session,
                self.store.db,
                version_id,
                datetime.fromisoformat(enrolment["first_enrolled_at"]),
                self.probe_policy,
            )
            report.append(
                {
                    "version_id": version_id,
                    "alpha_id": version["definition"]["alpha_id"],
                    "symbols": version["definition"].get("eligible_symbols") or [],
                    "expires_at": enrolment["expires_at"],
                    "renewals": enrolment["renewals"],
                    "live": reason is None,
                    "blocked_reason": reason,
                    "forward": forward,
                }
            )
        return report
```

`add_notification` wraps its payload as `{"kind", "arguments"}`; confirm by reading `WorkflowStore.add_notification` (`storage/workflow.py:564`). If it stores `payload` verbatim instead, pass `{"kind": NotificationKind.MESSAGE, "arguments": {...}}` — match what `OutboxService.enqueue_message` does (`notifier/outbox.py:~28`).

Replace `snapshot`:

```python
    async def snapshot(self, *, now=None) -> RegistrySnapshot:
        now = now or datetime.now(UTC)
        async with self.store.db.session_factory() as session:
            registry = _normalized(await self._get(session, REGISTRY_KEY))
            members = {
                "active": registry["active"],
                "shadow": registry["shadow"],
                # Only probes that may take new risk are ever installed for scanning.
                "probe": await self._live_probes(session, registry, now),
            }
            groups = {}
            for field, version_ids in members.items():
                versions = []
                for version_id in version_ids:
                    row = await self._get(session, f"version/{version_id}")
                    if not row:
                        raise ValueError("Registry references missing immutable version")
                    versions.append(AlphaDefinition.from_dict(row["definition"]))
                groups[field] = tuple(versions)
            return RegistrySnapshot(registry["generation"], groups["active"], groups["shadow"], groups["probe"])
```

- [ ] **Step 7: Run the new and the neighbouring tests**

Run: `uv run pytest tests/research/test_alpha_probe_registry.py tests/research/test_alpha_journal.py tests/workflows/test_alpha_admission.py tests/research/test_alpha_shadow.py -q`
Expected: all pass. `test_replay_restores_registry_without_mutations` compares snapshots for equality; it must still pass because `probe` defaults to `()`.

- [ ] **Step 8: Commit**

```bash
uv run pre-commit run --all-files
git add agentic_trader/storage/probe_state.py agentic_trader/storage/alpha.py agentic_trader/research/alpha/models.py agentic_trader/config.py tests/research/test_alpha_probe_registry.py
git commit -m "feat(alpha): journal paper-probe enrolment, renewal, kill rule and sweep

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Durable entry admission accepts live probes only

**Files:**
- Modify: `agentic_trader/storage/workflow.py` (`_alpha_entry_rejection`, ~383-413)
- Test: `tests/workflows/test_alpha_probe_admission.py`

**Interfaces:**
- Consumes (Task 2): `probe_block_reason(session, *, scope, db, version_id, now, policy=None) -> str | None`.
- Produces: no new public names. Refusal strings begin with `"Paper probe blocked: "`.

- [ ] **Step 1: Write the failing tests**

Create `tests/workflows/test_alpha_probe_admission.py`:

```python
"""A probe reserves new risk only while live, and only on the Alpaca paper scope."""

from datetime import UTC, datetime, timedelta

import pytest

from agentic_trader.broker.base import OrderRequest
from agentic_trader.config import AppConfig
from agentic_trader.execution.durable import EventKind
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.research.alpha.probe import policy_document
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.db import SignalDatabase
from agentic_trader.storage.workflow import WorkflowStore


def enrolment(*, expires_in_days=30, policy=None):
    now = datetime.now(UTC)
    return {
        "policy": policy if policy is not None else policy_document(),
        "assessment": {"eligible": True, "reasons": [], "observations": {}},
        "first_enrolled_at": now.isoformat(),
        "enrolled_at": now.isoformat(),
        "expires_at": (now + timedelta(days=expires_in_days)).isoformat(),
        "term_days": 30,
        "renewals": 0,
        "actor": "fixture",
    }


async def build(tmp_path, *, paper=True, record=None, listed=True):
    config = AppConfig(execution_mode="alpaca", alpaca_paper=paper)
    db = SignalDatabase(db_path=str(tmp_path / "admission.db"), config=config)
    await db.init_db()
    store = WorkflowStore(db)
    definition = AlphaDefinition(
        "alpha_probe_admission", "Probe", "close", timeframe="1d", eligible_symbols=("SPY",), data_feed="alpaca:iex"
    )
    repository = AlphaRepository(store)
    await repository.register(definition, actor="fixture")
    async with db.session_factory() as session, session.begin():
        await store.lock(session, resource="alpha")
        await repository._append(
            session,
            "registry",
            {"generation": 1, "active": [], "shadow": [], "probe": [definition.version_id] if listed else []},
            EventKind.ALPHA_REGISTRY,
            "fixture",
        )
        if record is not None:
            await repository._append(
                session, f"probe/{definition.version_id}", record, EventKind.ALPHA_REGISTRY, "fixture"
            )
    sid = await db.record_signal(
        "SPY",
        definition.alpha_id,
        "LONG",
        100,
        98,
        104,
        2,
        asset_class="EQUITY",
        quantity=1,
        timeframe="1d",
        alpha_version=definition.version_id,
        alpha_policy=definition.execution.to_dict(),
    )
    request = OrderRequest(
        signal_id=sid,
        symbol="SPY",
        asset_class="EQUITY",
        direction="LONG",
        quantity=1,
        entry_price=100,
        stop_loss=98,
        take_profit=104,
    )
    return db, store, repository, definition, request, config


async def test_live_probe_passes_the_alpha_gate(tmp_path):
    db, store, _, _, request, config = await build(tmp_path, record=enrolment())
    item, reason = await store.enqueue_entry(request, config)
    # Later, non-alpha admission layers may still refuse in this minimal fixture;
    # the alpha gate itself must not be the refusal.
    assert item is not None or "probe" not in (reason or "").lower() and "alpha" not in (reason or "").lower()
    await db.engine.dispose()


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"record": enrolment(expires_in_days=-1)}, "expired"),
        ({"record": enrolment(policy={"version": "old"})}, "policy changed"),
        ({"record": None}, "evidence is missing"),
        ({"record": enrolment(), "listed": False}, "not active"),
        ({"record": enrolment(), "paper": False}, "Alpaca paper"),
    ],
)
async def test_non_live_probe_cannot_reserve_risk(tmp_path, kwargs, fragment):
    db, store, _, _, request, config = await build(tmp_path, **kwargs)
    item, reason = await store.enqueue_entry(request, config)
    assert item is None
    assert fragment in reason
    await db.engine.dispose()


async def test_probe_demoted_during_preflight_blocks_submission_commit(tmp_path):
    db, store, repository, definition, request, config = await build(tmp_path, record=enrolment())
    item, reason = await store.enqueue_entry(request, config)
    if item is None:
        pytest.skip(f"fixture refused by a non-alpha layer: {reason}")
    claim = await store.claim_entry(lease_seconds=60)
    await repository.demote(definition.version_id, actor="test", expected_generation=1)
    committed, reason = await store.begin_submission(claim, request, config)
    assert not committed and "alpha" in reason.lower()
    await db.engine.dispose()
```

Before relying on `begin_submission(claim, request, config)`, read the second half of `tests/workflows/test_alpha_admission.py::test_alpha_change_during_preflight_blocks_submission_commit` and copy its exact call and unpacking; adjust the last test to match.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/workflows/test_alpha_probe_admission.py -q`
Expected: `test_live_probe_passes_the_alpha_gate` fails with reason `"Alpha version is not active/qualified; …"`; the `"not active"` parametrization passes already; the other refusal cases fail on the fragment.

- [ ] **Step 3: Implement**

In `agentic_trader/storage/workflow.py`, add imports:

```python
from agentic_trader.storage.probe_state import probe_block_reason
```

Replace the body of `_alpha_entry_rejection` from the `registry = …` line through the qualification-policy check with:

```python
registry = await session.get(AlphaProjectionRecord, (self.scope, "registry"))
listed = json.loads(registry.payload) if registry else {}
is_active = bool(signal.alpha_version) and signal.alpha_version in listed.get("active", [])
is_probe = bool(signal.alpha_version) and signal.alpha_version in listed.get("probe", [])
if not is_active and not is_probe:
    return "Alpha version is not active/qualified; request a fresh scan after qualification."
row = await session.get(AlphaProjectionRecord, (self.scope, f"version/{signal.alpha_version}"))
if row is None:
    return "Alpha version evidence is missing."
if is_active:
    qualification = await session.get(AlphaProjectionRecord, (self.scope, f"qualification/{signal.alpha_version}"))
    if not qualification or json.loads(qualification.payload).get("policy") != asdict(ValidationPolicy()):
        return "Alpha qualification policy is obsolete; fresh research and qualification are required."
else:
    # A probe is never authorized by qualification; only by live, current enrolment.
    blocked = await probe_block_reason(
        session, scope=self.scope, db=self.db, version_id=signal.alpha_version, now=datetime.now(UTC)
    )
    if blocked:
        return f"Paper probe blocked: {blocked}."
```

Leave the subsequent `definition = …`, clock check and immutable-contract comparison exactly as they are; they apply to both paths. Ensure `datetime` and `UTC` are imported in this module (they already are; verify with `grep -n "^from datetime" agentic_trader/storage/workflow.py`).

Check for an import cycle: `uv run python -c "import agentic_trader.storage.workflow, agentic_trader.storage.alpha"`. `probe_state` imports only `constants`, `research.alpha.probe` and `storage.models`, so none is expected.

- [ ] **Step 4: Run to verify pass, plus the unchanged active path**

Run: `uv run pytest tests/workflows/ tests/research/test_alpha_journal.py -q`
Expected: all pass, including every pre-existing `test_alpha_admission.py` case unmodified.

- [ ] **Step 5: Commit**

```bash
uv run pre-commit run --all-files
git add agentic_trader/storage/workflow.py tests/workflows/test_alpha_probe_admission.py
git commit -m "feat(execution): admit live paper probes through the durable alpha gate

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Probe risk cap in sizing

**Files:**
- Modify: `agentic_trader/screeners/base.py` (`ScreenerCandidate`, ~line 38)
- Modify: `agentic_trader/agent/position_sizing.py` (`calculate_dynamic_sizing`, signature ~38-50 and ~line 87)
- Modify: `agentic_trader/agent/evaluator.py` (every `calculate_dynamic_sizing(` call that passes `candidate=`)
- Test: `tests/agent/test_probe_sizing.py`

**Interfaces:**
- Produces: `ScreenerCandidate.probe: bool = False`; `calculate_dynamic_sizing(..., risk_dollars_cap: float | None = None)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/agent/test_probe_sizing.py`:

```python
import pytest

from agentic_trader.agent.position_sizing import calculate_dynamic_sizing
from agentic_trader.constants import AssetClass


def size(app_config, **kwargs):
    return calculate_dynamic_sizing(
        entry=200.0,
        stop_distance=4.0,
        target_distance=8.0,
        multiplier=1.0,
        asset_class=AssetClass.EQUITY,
        config=app_config,
        **kwargs,
    )


def test_probe_cap_bounds_every_tier_and_never_raises_risk(app_config):
    uncapped = size(app_config)
    capped = size(app_config, risk_dollars_cap=100.0)
    assert uncapped.max_tier.risk_dollars > 100.0
    assert all(tier.risk_dollars <= 100.0 for tier in capped.tiers)
    assert capped.max_tier.quantity == 25  # floor(100 / 4)
    assert any("probe" in reason.lower() for reason in capped.gating_reasons)
    generous = size(app_config, risk_dollars_cap=1_000_000.0)
    assert [t.quantity for t in generous.tiers] == [t.quantity for t in uncapped.tiers]


def test_probe_cap_below_one_share_blocks_instead_of_rounding_up(app_config):
    blocked = size(app_config, risk_dollars_cap=3.0)  # one share risks $4
    assert blocked.default_tier.tier_id == "blocked" and blocked.default_tier.quantity == 0


@pytest.mark.parametrize("cap", [0.0, -5.0])
def test_nonpositive_cap_is_rejected(app_config, cap):
    with pytest.raises(ValueError, match="risk cap"):
        size(app_config, risk_dollars_cap=cap)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agent/test_probe_sizing.py -q`
Expected: `TypeError: calculate_dynamic_sizing() got an unexpected keyword argument 'risk_dollars_cap'`.

- [ ] **Step 3: Implement**

`agentic_trader/screeners/base.py`, after `contributors`:

```python
    probe: bool = False
```

`agentic_trader/agent/position_sizing.py` — add the parameter last in the signature:

```python
    current_equity: float | None = None,
    risk_dollars_cap: float | None = None,
) -> PositionSizingResult:
```

Immediately after `gating_reasons: list[str] = []`:

```python
    if risk_dollars_cap is not None and not risk_dollars_cap > 0:
        raise ValueError("Positive risk cap required")
```

Immediately after the existing line `max_risk_dollars = portfolio_cash * sizing_cfg.max_risk_pct_cap * drawdown_factor`:

```python
    if risk_dollars_cap is not None and risk_dollars_cap < max_risk_dollars:
        # A paper probe may only ever reduce risk. Base/half tiers clamp to max_qty below.
        max_risk_dollars = risk_dollars_cap
        gating_reasons.append(f"Paper probe risk cap applied: ${risk_dollars_cap:,.0f}")
```

`agentic_trader/agent/evaluator.py` — list the call sites with `grep -n "calculate_dynamic_sizing(" agentic_trader/agent/evaluator.py`. To each call that passes `candidate=candidate`, add:

```python
risk_dollars_cap = (self.config.alpha_pipeline.probe_risk_dollars if candidate.probe else None,)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/agent/test_probe_sizing.py tests/agent/ -q`
Expected: all pass. If `capped.max_tier.quantity == 25` fails because the default test config's `max_shares_per_trade` or notional cap binds first, print `capped.gating_reasons` and lower `entry` in the test helper until risk is the binding constraint; do not change production code to satisfy it.

- [ ] **Step 5: Commit**

```bash
uv run pre-commit run --all-files
git add agentic_trader/screeners/base.py agentic_trader/agent/position_sizing.py agentic_trader/agent/evaluator.py tests/agent/test_probe_sizing.py
git commit -m "feat(sizing): cap dollar risk for paper-probe candidates

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Scan integration — install, pickup, arbitration, sweep, provenance

**Files:**
- Modify: `agentic_trader/screeners/formulaic.py` (constructor ~32-46, candidate ~129-149)
- Modify: `agentic_trader/screeners/registry.py` (`ConflictResolver.resolve` ~96-99, `install_alphas` ~114-121, parallel pickup ~192-195)
- Modify: `agentic_trader/agent/copilot.py` (~293-297 and the `record_signal(...)` call ~491-519)
- Test: `tests/screeners/test_probe_scan.py`

**Interfaces:**
- Consumes: `RegistrySnapshot.probe`, `AlphaRepository.sweep_probes`, `ScreenerCandidate.probe`, `PAPER_PROBE_TAG`.
- Produces: `FormulaicAlphaStrategy(definition, …, probe: bool = False)`; `StrategyRegistry.install_alphas(definitions, probes=())`; signals whose `decision_provenance` contains `"paper_probe": true` and whose notification dict contains `"probe_risk_cap": float`.

- [ ] **Step 1: Write the failing tests**

Create `tests/screeners/test_probe_scan.py`:

```python
from agentic_trader.constants import AssetClass
from agentic_trader.research.alpha.models import AlphaDefinition
from agentic_trader.screeners.base import ScreenerCandidate
from agentic_trader.screeners.formulaic import FormulaicAlphaStrategy
from agentic_trader.screeners.registry import ConflictResolver, StrategyRegistry


def definition(alpha_id, symbol="AAPL"):
    return AlphaDefinition(alpha_id, alpha_id, "delta(close,3)", timeframe="1d", eligible_symbols=(symbol,))


def candidate(strategy, *, probe):
    return ScreenerCandidate(
        contract="AAPL",
        symbol="AAPL",
        asset_class=AssetClass.EQUITY,
        timeframe="1d",
        strategy=strategy,
        direction="LONG",
        current_price=100.0,
        ema_20=1.0,
        ema_50=1.0,
        ema_200=1.0,
        rsi_14=50.0,
        atr_14=1.0,
        candle_timestamp="2026-09-21T00:00:00+00:00",
        trigger_detail="t",
        alpha_version="v-" + strategy,
        probe=probe,
    )


def test_install_marks_probe_strategies_and_replaces_the_previous_snapshot():
    registry = StrategyRegistry()
    registry.install_alphas((definition("alpha_live"),), probes=(definition("imported_without_prefix", "MSFT"),))
    live, probe = registry.get("alpha_live"), registry.get("imported_without_prefix")
    assert isinstance(live, FormulaicAlphaStrategy) and live.probe is False
    assert isinstance(probe, FormulaicAlphaStrategy) and probe.probe is True
    registry.install_alphas(())
    assert registry.get("alpha_live") is None and registry.get("imported_without_prefix") is None


def test_parallel_pickup_uses_type_not_the_alpha_prefix(app_config):
    registry = StrategyRegistry()
    registry.install_alphas((), probes=(definition("imported_without_prefix"),))
    picked = [s.strategy_id for s in registry.get_active_strategies(app_config, override_mode="parallel")]
    assert "imported_without_prefix" in picked


def test_active_candidate_beats_probe_candidate_for_one_instrument(app_config):
    winner = ConflictResolver.resolve([candidate("alpha_a_probe", probe=True), candidate("alpha_z_live", probe=False)])
    assert [c.strategy for c in winner] == ["alpha_z_live"]
```

`ConflictResolver.resolve(candidates, mode=NETTING)` is a staticmethod and `StrategyRegistry.get_active_strategies(config, override_strategy=None, override_mode=None)` is the parallel-mode entry point (`screeners/registry.py:22`, `:150`). Confirm the parallel mode literal with `grep -n "class StrategyMode" -A4 agentic_trader/constants.py`. If `ScreenerCandidate` requires fields not given above, the validation error lists them; add them with neutral values.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/screeners/test_probe_scan.py -q`
Expected: `TypeError: install_alphas() got an unexpected keyword argument 'probes'`.

- [ ] **Step 3: Implement the screener changes**

`agentic_trader/screeners/formulaic.py` — add a keyword to the constructor and store it:

```python
        clock: Callable[[], datetime] | None = None,
        *,
        probe: bool = False,
    ) -> None:
        super().__init__(config=config)
        self.definition = definition
        self.probe = probe
```

and stamp the candidate (add next to `alpha_policy=`):

```python
probe = (self.probe,)
```

`agentic_trader/screeners/registry.py`:

```python
def install_alphas(self, definitions: tuple[AlphaDefinition, ...], probes: tuple[AlphaDefinition, ...] = ()) -> int:
    """Build then atomically swap one complete registry snapshot between scans."""
    strategies = {sid: s for sid, s in self._strategies.items() if not isinstance(s, FormulaicAlphaStrategy)}
    for definition in definitions:
        strategy = FormulaicAlphaStrategy(definition)
        strategies[strategy.strategy_id] = strategy
    for definition in probes:
        strategy = FormulaicAlphaStrategy(definition, probe=True)
        strategies[strategy.strategy_id] = strategy
    self._strategies = strategies
    return len(definitions) + len(probes)
```

Parallel pickup — replace the prefix test:

```python
        # Include every installed formulaic alpha (active and paper probe); the
        # registry snapshot, not a naming convention, decides what is installed.
        for strat in list(self._strategies.values()):
            if isinstance(strat, FormulaicAlphaStrategy) and strat.is_enabled(config) and strat not in active_strats:
                active_strats.append(strat)
```

Arbitration — prefer non-probe owners:

```python
            winner = min(
                sym_candidates,
                key=lambda candidate: (
                    candidate.probe,
                    candidate.strategy,
                    candidate.timeframe,
                    candidate.alpha_version or "",
                ),
            )
```

- [ ] **Step 4: Implement the copilot changes**

`agentic_trader/agent/copilot.py`, at the top of the `_scan_lock` block:

```python
        async with self._scan_lock:
            if not dry_run:
                # Durable visibility only; snapshot() already excludes non-live probes.
                try:
                    await self.alpha_repository.sweep_probes()
                except Exception:
                    logger.exception("Paper-probe sweep failed; retrying next scan", extra={"event": "probe_sweep_failed"})
            alpha_snapshot = await self.alpha_repository.snapshot()
            self.strategy_engine.registry.install_alphas(alpha_snapshot.active, alpha_snapshot.probe)
```

In the `record_signal(...)` call, extend the two dicts:

```python
decision_provenance = (
    {
        "candle_timestamp": candidate.candle_timestamp,
        "alpha_score": candidate.alpha_score,
        "contributors": candidate.contributors,
        "account_risk_fingerprint": account_risk.fingerprint if account_risk else None,
        PAPER_PROBE_TAG: candidate.probe,
    },
)
```

```python
notification = (
    {
        "eval_res": eval_res.model_dump(mode="json"),
        "strategy": candidate.strategy,
        "regime_summary": regime.summary_text,
        "probe_risk_cap": self.config.alpha_pipeline.probe_risk_dollars if candidate.probe else None,
    },
)
```

Import `PAPER_PROBE_TAG` from `agentic_trader.research.alpha.probe`. The `probe_risk_cap` key is consumed in Task 6; until then `send_signal_alert(**args)` would reject it, so Task 6 Step 3 must land in the same PR. To keep this task's suite green on its own, add the parameter stub now in `agentic_trader/notifier/telegram_bot.py`:

```python
    async def send_signal_alert(
        self,
        eval_res: LLMTradeEvaluation,
        strategy: str,
        signal_id: int,
        regime_summary: str | None = None,
        probe_risk_cap: float | None = None,
    ) -> int | None:
```

Check every other implementer of `send_signal_alert` (`grep -rn "def send_signal_alert" agentic_trader tests`) and add the same defaulted parameter to each so the outbox `**args` call cannot fail.

- [ ] **Step 5: Run**

Run: `uv run pytest tests/screeners/ tests/agent/ tests/notifier/ -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
uv run pre-commit run --all-files
git add agentic_trader/screeners/ agentic_trader/agent/copilot.py agentic_trader/notifier/telegram_bot.py tests/screeners/test_probe_scan.py
git commit -m "feat(scan): install paper probes, retire them durably and tag their signals

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Operator surface — CLI, cards, dashboard

**Files:**
- Modify: `agentic_trader/cli/commands/alpha.py` (`alpha list` ~173-192, `alpha status` ~538-546, new `probe` command after the `lifecycle_command` loop ~390)
- Modify: `agentic_trader/notifier/telegram_bot.py` (`format_alert_card` ~62, `send_signal_alert` ~1026-1057)
- Modify: `agentic_trader/storage/db.py` (exit notification, ~691-694)
- Modify: `agentic_trader/presentation/formatters.py` (`format_alphas_dashboard_html` ~562-567)
- Test: `tests/cli/test_alpha_probe_cli.py`, additions to `tests/notifier/` and `tests/presentation/`

**Interfaces:**
- Consumes: `AlphaRepository.enrol_probe`, `.probe_report`, `RegistrySnapshot.probe`, `PAPER_PROBE_TAG`.
- Produces: `copilot alpha probe VERSION_ID --generation N [--days D] [--renew]`.

- [ ] **Step 1: Write the failing presentation tests**

Find the existing card test module with `grep -rln "format_alert_card" tests`. Append to it (reuse that module's existing `eval_res` fixture/builder — read the first test in the file for its name):

```python
def test_probe_card_is_tagged_and_states_the_cap(eval_res):
    plain = format_alert_card(eval_res, "alpha_x")
    tagged = format_alert_card(eval_res, "alpha_x", probe_risk_cap=100.0)
    assert "PAPER PROBE" not in plain
    assert tagged.startswith("🧪 <b>PAPER PROBE</b>")
    assert "$100" in tagged and "no promotion credit" in tagged
    assert tagged.endswith(plain)
```

Find the dashboard test with `grep -rln "format_alphas_dashboard_html" tests` and append:

```python
def test_dashboard_counts_paper_probes():
    snapshot = RegistrySnapshot(3, (), (), (AlphaDefinition("alpha_p", "P", "close", timeframe="1d"),))
    text = TelegramHtmlFormatter.format_alphas_dashboard_html(
        snapshot, evidence={"days": 7, "candidates": [], "truncated": False}
    )
    assert "1 paper probe" in text
```

If the existing dashboard tests build `evidence` with more keys, copy their evidence dict instead.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/notifier tests/presentation -q -k "probe"`
Expected: `TypeError: format_alert_card() got an unexpected keyword argument 'probe_risk_cap'` and an assertion failure on `"1 paper probe"`.

- [ ] **Step 3: Implement cards and dashboard**

`agentic_trader/notifier/telegram_bot.py` — add the parameter to `format_alert_card` and wrap its return. Rename the existing function body's final `return <expr>` to `card = <expr>` and finish with:

```python
    if probe_risk_cap is None:
        return card
    return (
        f"🧪 <b>PAPER PROBE</b> — risk capped at ${probe_risk_cap:,.0f}; paper account only; "
        "earns no promotion credit\n\n" + card
    )
```

with signature:

```python
def format_alert_card(
    eval_res: LLMTradeEvaluation,
    strategy: str,
    portfolio_cash: float = DEFAULT_PORTFOLIO_CASH,
    execution_mode: str = ExecutionMode.PAPER,
    regime_summary: str | None = None,
    probe_risk_cap: float | None = None,
) -> str:
```

If the function has several `return` statements, apply the wrap to each via a small inner helper `def _tagged(card): …` defined at the top of the function.

In `send_signal_alert`, pass it through:

```python
        card_html = format_alert_card(
            eval_res,
            strategy,
            self.portfolio_cash,
            execution_mode=self.execution_mode,
            regime_summary=regime_summary,
            probe_risk_cap=probe_risk_cap,
        )
```

`agentic_trader/presentation/formatters.py` — in `format_alphas_dashboard_html`, after the second header line is built:

```python
        if snapshot.probe:
            count = len(snapshot.probe)
            lines.append(
                f"🧪 <b>{count} paper probe{'s' if count != 1 else ''}</b> trading with capped risk on the paper account: "
                + html.escape(", ".join(d.alpha_id for d in snapshot.probe))
            )
```

`agentic_trader/storage/db.py` — tag exit cards. Immediately before `if notification is not None:` in the close method (~line 691):

```python
if notification is not None and "strategy" in notification:
    provenance = await session.scalar(select(SignalRecord.decision_provenance).where(SignalRecord.id == signal_id))
    if provenance and json.loads(provenance).get(PAPER_PROBE_TAG):
        notification = {**notification, "strategy": f"🧪 PAPER PROBE · {notification['strategy']}"}
```

Import `PAPER_PROBE_TAG` from `agentic_trader.research.alpha.probe`; confirm `json` and `select` are already imported in `db.py`. The EXIT payload may nest its arguments (`{"kind", "arguments": {...}}`) depending on the caller; print one in a test (`grep -rn "NotificationKind.EXIT\|exit_notification\|notification=" agentic_trader/agent/copilot.py | head`) and apply the prefix at the level where the `strategy` key actually lives.

Add a test next to the existing close-notification tests (`grep -rln "signal/.*closed\|NotificationKind.EXIT" tests`) asserting that closing a signal recorded with `decision_provenance={"paper_probe": True}` enqueues an exit notice whose strategy starts with `🧪 PAPER PROBE`, and that one recorded without the tag is unchanged.

- [ ] **Step 4: Write the failing CLI test**

Existing CLI tests (`tests/cli/test_cli_alpha.py`) invoke the real `cli` under the autouse isolated test config, whose scope is not `…/alpaca:paper`. Cover both the refusal in that default scope and success with a patched paper repository. First move `paper_database`, `qualification`, `criterion`, `make_definition` and `seed` from `tests/research/test_alpha_probe_registry.py` into a new `tests/research/probe_fixtures.py` and import them back, then create `tests/cli/test_alpha_probe_cli.py`:

```python
from contextlib import asynccontextmanager

from click.testing import CliRunner

from agentic_trader.cli.main import cli
from agentic_trader.storage.alpha import AlphaRepository
from tests.research.probe_fixtures import criterion, make_definition, paper_database, qualification, seed


def patched_repository(monkeypatch, repository):
    @asynccontextmanager
    async def manager():
        yield repository

    monkeypatch.setattr("agentic_trader.cli.commands.alpha.alpha_repository", manager)


async def prepared(tmp_path, decision=None):
    database = paper_database(tmp_path)
    await database.init_db()
    repository = AlphaRepository(database.workflows)
    definition = make_definition()
    await seed(repository, definition, decision)
    return repository, definition


def test_default_test_scope_refuses_probe_enrolment():
    result = CliRunner().invoke(cli, ["alpha", "probe", "0" * 64, "--generation", "0"])
    assert result.exit_code != 0
    assert "Unknown alpha version" in result.output or "Alpaca paper" in result.output


async def test_probe_command_enrols_and_lists(tmp_path, monkeypatch):
    repository, definition = await prepared(tmp_path)
    patched_repository(monkeypatch, repository)
    runner = CliRunner()
    result = await asyncio.to_thread(
        runner.invoke, cli, ["alpha", "probe", definition.version_id, "--generation", "0", "--days", "30"]
    )
    assert result.exit_code == 0, result.output
    assert "paper probe" in result.output.lower() and "generation 1" in result.output
    listing = await asyncio.to_thread(runner.invoke, cli, ["alpha", "list"])
    assert f"{definition.version_id} | probe" in listing.output


async def test_probe_command_reports_each_unmet_criterion(tmp_path, monkeypatch):
    repository, definition = await prepared(tmp_path, qualification(cost_stress=criterion(-1.0, "fail")))
    patched_repository(monkeypatch, repository)
    result = await asyncio.to_thread(
        CliRunner().invoke, cli, ["alpha", "probe", definition.version_id, "--generation", "0"]
    )
    assert result.exit_code != 0
    assert "cost_stress_below_probe_floor" in result.output
```

Add `import asyncio` at the top. The `@coro` command wrapper starts its own event loop, so the async tests invoke the runner in a worker thread (`asyncio.to_thread`), the same way `tests/cli/test_cli_alpha.py` drives async-backed commands; if that module uses a different idiom for this, copy it.

Run: `uv run pytest tests/cli/test_alpha_probe_cli.py -q` — expected: `No such command 'probe'`.

- [ ] **Step 5: Implement the CLI**

In `agentic_trader/cli/commands/alpha.py`, update `alpha list`:

```python
        probe = {d.version_id for d in snapshot.probe}
        ...
            status = (
                "active"
                if definition.version_id in active
                else "probe"
                if definition.version_id in probe
                else "shadow"
                if definition.version_id in shadow
                else "inactive"
            )
```

Add after the `lifecycle_command` loop:

```python
@alpha_group.command("probe")
@click.argument("version_id")
@click.option("--generation", type=int, required=True, help="Observed registry generation; stale changes are rejected")
@click.option(
    "--days", type=click.IntRange(1, 180), default=None, help="Probe term; defaults to alpha_pipeline.probe_term_days"
)
@click.option("--renew", is_flag=True, help="Extend a current, unkilled probe from now")
@coro
async def alpha_probe_cmd(version_id, generation, days, renew):
    """Enrol a relaxed-bar candidate as a capped, expiring Alpaca paper probe."""
    async with alpha_repository() as repository:
        try:
            updated = await repository.enrol_probe(
                version_id, actor="cli_operator", expected_generation=generation, days=days, renew=renew
            )
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        record = await repository.get(f"probe/{version_id}")
        click.echo(
            f"{'Renewed' if renew else 'Enrolled'} paper probe {version_id}; registry generation {updated}; "
            f"expires {record['expires_at']}. Effective next scan."
        )
        click.echo(
            "Paper account only; risk-capped; earns no shadow, holdout or promotion credit. "
            "Leaving probe never closes or modifies a position."
        )
```

In `alpha status`, add one line before `click.echo`:

```python
        report["probes"] = await repository.probe_report()
```

- [ ] **Step 6: Run**

Run: `uv run pytest tests/cli tests/notifier tests/presentation tests/storage -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
uv run pre-commit run --all-files
git add agentic_trader/cli/commands/alpha.py agentic_trader/notifier/telegram_bot.py agentic_trader/presentation/formatters.py agentic_trader/storage/db.py tests/
git commit -m "feat(operator): enrol, list, report and tag paper probes

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Invariant regressions, PostgreSQL races and documentation

**Files:**
- Create: `tests/integration/test_alpha_probe_postgres.py`
- Modify: `tests/research/test_alpha_probe_registry.py` (one invariant test)
- Modify: `docs/superpowers/specs/2026-09-21-alpha-paper-probe-design.md`, `docs/alpha-pipeline.md`, `docs/cli-reference.md`, `docs/alpha-roadmap.md`, `CLAUDE.md`, `AGENTS.md`

- [ ] **Step 1: Pin "leaving probe never touches the broker"**

Append to `tests/research/test_alpha_probe_registry.py`:

```python
async def test_retiring_a_probe_leaves_its_open_position_untouched(db, repository):
    from agentic_trader.storage.models import SignalRecord

    definition = make_definition()
    await seed(repository, definition)
    await repository.enrol_probe(definition.version_id, actor="op", expected_generation=0, days=1, now=NOW)
    sid = await db.record_signal(
        "AAPL",
        definition.alpha_id,
        "LONG",
        100,
        98,
        104,
        100.0,
        asset_class="EQUITY",
        quantity=1,
        timeframe="1d",
        alpha_version=definition.version_id,
        alpha_policy=definition.execution.to_dict(),
    )
    async with db.session_factory() as session, session.begin():
        row = await session.get(SignalRecord, sid)
        row.status, row.broker_order_id = SignalStatus.EXECUTED, "entry-1"
        before = (row.status, row.broker_order_id, row.stop_loss, row.take_profit)
    await repository.sweep_probes(now=NOW + timedelta(days=2))
    async with db.session_factory() as session:
        row = await session.get(SignalRecord, sid)
        assert (row.status, row.broker_order_id, row.stop_loss, row.take_profit) == before
```

Import `SignalStatus` from `agentic_trader.constants`; `EXECUTED` is the open-position status.

Run: `uv run pytest tests/research/test_alpha_probe_registry.py -q` — expected pass (the sweep has no broker or signal writes; this test keeps it that way).

- [ ] **Step 2: PostgreSQL race tests**

Create `tests/integration/test_alpha_probe_postgres.py`:

```python
"""Two independent clients: enrolment is compare-and-swap; symbol ownership is exclusive."""

import asyncio

import pytest

from agentic_trader.config import AppConfig
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.db import SignalDatabase
from tests.research.probe_fixtures import make_definition, seed  # created in Task 6


pytestmark = pytest.mark.postgres


async def test_concurrent_enrolments_for_one_symbol_admit_exactly_one(postgres_test_db):
    config = AppConfig(execution_mode="alpaca", alpaca_paper=True)
    first = SignalDatabase(db_url=postgres_test_db, config=config)
    second = SignalDatabase(db_url=postgres_test_db, config=config)
    await first.init_db()
    a, b = AlphaRepository(first.workflows), AlphaRepository(second.workflows)
    one, two = make_definition("alpha_one"), make_definition("alpha_two")
    await seed(a, one)
    await seed(a, two)
    results = await asyncio.gather(
        a.enrol_probe(one.version_id, actor="op", expected_generation=0),
        b.enrol_probe(two.version_id, actor="op", expected_generation=0),
        return_exceptions=True,
    )
    assert sum(isinstance(r, ValueError) for r in results) == 1
    assert len((await a.snapshot()).probe) == 1
    await first.engine.dispose()
    await second.engine.dispose()
```

Match the marker and fixture names used by the neighbouring file: `sed -n 1,30p tests/integration/test_alpha_pipeline.py` and `grep -n "postgres" tests/integration/conftest.py pyproject.toml`.

Run: `TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_trader uv run pytest tests/integration/test_alpha_probe_postgres.py --run-postgres -q`
Expected: pass. If PostgreSQL is unavailable locally, report that explicitly; CI's "PostgreSQL Integration" job runs it.

- [ ] **Step 3: Amend the spec for decisions made while planning**

In `docs/superpowers/specs/2026-09-21-alpha-paper-probe-design.md`:

1. In "State machine", after the exits table, add:
   > A killed version is terminal for that version: neither renewal nor fresh enrolment is accepted, because the forward record is measured from `first_enrolled_at`, which survives retirement. A newly mined version is a new identity.
2. In component 2, replace the `snapshot` wording with: "`snapshot()` returns only live probes, so a non-live probe is never installed for scanning even if the sweep has not run."
3. In component 7, replace the `/perf` sentence with: "`/perf` is unchanged in this version; the probe forward record in `alpha status` and `/alphas` is the separate view of probe outcomes."

- [ ] **Step 4: Update the living docs**

- `docs/alpha-pipeline.md`: add a "Paper probes" subsection under the lifecycle section with the state table from the spec, the `ProbePolicy` defaults table, and the sentence "Enrolment reads stored qualification criteria; it consumes no holdout and charges no trial."
- `docs/cli-reference.md`: document `copilot alpha probe VERSION_ID --generation N [--days 1..180] [--renew]` next to `promote`/`shadow`/`demote`.
- `docs/alpha-roadmap.md`, item 6 of "Current priorities after the whole-stack survey": mark the paper-probe lane implemented, link the spec, and add under item 4: "Holdout consumption is keyed by symbol alone (`storage/alpha.py:begin_holdout`), so one qualification attempt by any alpha locks that symbol for roughly one holdout length; measure consumed-symbol coverage before scaling campaigns."
- `CLAUDE.md` and `AGENTS.md`: add one paragraph after "Contracts to preserve" item 1:
  > **Paper probes:** `probe` is a third registry list, valid only in the `…/alpaca:paper` scope. `probe_block_reason` is the single liveness rule for snapshot, sweep and admission. Probes are risk-capped, expiring, renewable only while unkilled (−4R from first enrolment, sticky per version), and earn no shadow, holdout or promotion credit. Never make `active` depend on the account being paper; leaving `probe` never touches the broker.

- [ ] **Step 5: Full verification**

```bash
uv run pytest
uv run mypy agentic_trader
uv run pre-commit run --all-files
```

Expected: full suite green, no new mypy errors, pre-commit clean.

- [ ] **Step 6: Commit**

```bash
git add tests/ docs/ CLAUDE.md AGENTS.md
git commit -m "test,docs: pin paper-probe invariants, races and operator contract

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## After this plan

Deployment is separate, operator-approved evidence and is **not** part of these tasks: pause the watchdog then the daemon, update the installed checkout, restart, run `uv run python scripts/verify_runtime.py`, then `alpha mine` → `alpha qualify` → `alpha probe` one candidate and confirm scan → `🧪` card → order → reconciliation on the Alpaca paper account.
