# Stage attribution L1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record every LLM verdict, journal vetoes and card ids, and extend `cards outcomes` with market-exposure, LLM-gate and fill-slippage decompositions, with no change to any live decision.

**Architecture:** The evaluator fills the existing `llm_verdict` field for every parsed LLM answer. The scan journals that verdict plus the card's `signal_id` per ranked candidate and uses a fixed `llm_vetoed` outcome. The outcomes reader labels a SPY control and the LLM bracket from the same bars; a new execution-evidence module reads fills and taps per signal. The CLI only wires and prints.

**Tech Stack:** Python 3.14, `uv`, pydantic, pandas, SQLAlchemy async (temp SQLite in tests), pytest (`asyncio_mode=auto`), click.

**Spec:** `docs/superpowers/specs/2026-10-05-stage-attribution-l1-design.md`

Run every command from the worktree root `/Users/adamhadani/Development/agentic-trader-stage-eval` with `env -u VIRTUAL_ENV uv run …`.

## Global Constraints

- No behaviour change to ranking, budgets, caps, the LLM prompt, or when a veto applies. A native card's LLM veto is applied exactly as today; a catalog probe's never is.
- No Alembic migration; schema head stays `008_alpha_pipeline`. New fields live only in JSON payloads (`decision_provenance`, journal event payloads).
- No new Telegram message and no new config key. No broker, Telegram or network I/O in tests.
- Journal events already written (without `llm`/`signal_id` keys) must stay readable by `label_journaled`.
- Keep `agentic_trader/research/setups/outcomes.py` a pure reader over events and bars (no DB). Execution evidence lives in its own module.
- TDD: write the failing test, run it to see it fail, implement, run it to see it pass, then run the task's regression suites before committing.
- After each task run: `env -u VIRTUAL_ENV uv run pytest -q tests/agent tests/research/setups tests/cli tests/notifier`. The final task runs the full suite and `env -u VIRTUAL_ENV uv run pre-commit run --all-files`.
- Commit with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` as the last line.

## Review Focus

1. An evaluation object without an `llm_verdict` attribute (tests use `SimpleNamespace`) must journal `"rejected: <reason>"`, not raise and not say `llm_vetoed` — pinned in Task 2.
2. A scan whose LLM answered with a non-finite stop (e.g. `NaN` after JSON `null`) must still record a signal: `record_signal` encodes with `allow_nan=False`, so the journaled/provenance verdict must pass through `finite_or_none` — pinned in Task 2.
3. A SPY fetch failure or SPY bars that stop before a candidate's exit must leave `market_r` null for that row and never raise — pinned in Task 3.
4. An `llm` block whose bracket equals the deterministic one, or whose geometry is invalid (stop on the wrong side), must give `llm_r_cost` null, not an exception — pinned in Task 3.
5. A sent card with no `entry_resolved` event (never executed, or executed but journal unavailable) must count as `missing_fill_evidence`, not as zero slippage — pinned in Task 4.

---

### Task 1: Journal vocabulary and every LLM verdict recorded

**Files:**
- Modify: `agentic_trader/execution/durable.py` (add `RankedOutcome` next to `EventKind`)
- Modify: `agentic_trader/agent/evaluator.py:60-85` (field comment) and `:686-743` (verdict recording)
- Modify: `tests/agent/test_evaluator_catalog.py` (two pinned shapes change)
- Test: `tests/agent/test_evaluator_llm_verdict.py` (new)

**Interfaces:**
- Produces: `RankedOutcome(StrEnum)` with `SENT = "sent"`, `LLM_VETOED = "llm_vetoed"` in `agentic_trader.execution.durable`.
- Produces: `LLMTradeEvaluation.llm_verdict: dict | None` with keys `approved: bool`, `rejection_reason: str | None`, `stop_loss: float`, `take_profit: float`, `applied: bool`, set whenever the LLM answer parsed; `None` otherwise.

- [ ] **Step 1: Write the failing tests**

Create `tests/agent/test_evaluator_llm_verdict.py`:

```python
"""Every parsed LLM answer is recorded as ``llm_verdict``; only a native card applies it."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentic_trader.execution.durable import RankedOutcome
from tests.agent.test_evaluator import evaluator_factory  # noqa: F401  (fixture)
from tests.agent.test_evaluator_catalog import LLM_VETO, drift_candidate


def completion(monkeypatch, **overrides) -> AsyncMock:
    content = json.dumps({**LLM_VETO, **overrides})
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    mock = AsyncMock(return_value=response)
    monkeypatch.setattr("agentic_trader.agent.evaluator.litellm.acompletion", mock)
    return mock


def native_candidate(**updates):
    return drift_candidate(catalog_event=None, alpha_version=None, alpha_policy=None, probe=False, **updates)


def test_ranked_outcome_vocabulary():
    assert RankedOutcome.SENT == "sent"
    assert RankedOutcome.LLM_VETOED == "llm_vetoed"
    assert json.dumps({"outcome": RankedOutcome.LLM_VETOED}) == '{"outcome": "llm_vetoed"}'


async def test_native_veto_is_applied_and_recorded(evaluator_factory, monkeypatch):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.openai_api_key = "isolated-test-placeholder"
    completion(monkeypatch)

    result = await evaluator.evaluate_candidate(native_candidate(), use_llm=True)

    assert result.approved is False
    assert result.rejection_reason == "earnings momentum fading"
    assert result.llm_verdict == {
        "approved": False,
        "rejection_reason": "earnings momentum fading",
        "stop_loss": result.stop_loss,
        "take_profit": result.take_profit,
        "applied": True,
    }


async def test_native_approval_records_the_clamped_bracket(evaluator_factory, monkeypatch):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.openai_api_key = "isolated-test-placeholder"
    # stop 49.9 is inside the minimum ATR distance, so the evaluator clamps it to its own stop.
    completion(monkeypatch, approved=True, rejection_reason=None, stop_loss=49.9, take_profit=60.0)

    result = await evaluator.evaluate_candidate(native_candidate(), use_llm=True)

    assert result.approved is True
    assert result.llm_verdict["applied"] is True and result.llm_verdict["approved"] is True
    assert result.llm_verdict["stop_loss"] == result.stop_loss != 49.9
    assert result.llm_verdict["take_profit"] == result.take_profit


async def test_catalog_verdict_is_recorded_but_not_applied(evaluator_factory, monkeypatch):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.openai_api_key = "isolated-test-placeholder"
    completion(monkeypatch)

    result = await evaluator.evaluate_candidate(drift_candidate(), use_llm=True)

    assert result.approved is True
    assert result.llm_verdict["applied"] is False and result.llm_verdict["approved"] is False
    assert result.llm_verdict["rejection_reason"] == "earnings momentum fading"
    # A frozen policy card's recorded bracket is the policy bracket, not the LLM's proposal.
    assert (result.llm_verdict["stop_loss"], result.llm_verdict["take_profit"]) == (
        result.stop_loss,
        result.take_profit,
    )


@pytest.mark.parametrize("approved", ["false", "no", 1, None, "True", True])
async def test_commentary_verdict_keeps_the_explicit_true_rule(evaluator_factory, monkeypatch, approved):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.openai_api_key = "isolated-test-placeholder"
    completion(monkeypatch, approved=approved)

    result = await evaluator.evaluate_candidate(drift_candidate(), use_llm=True)

    assert result.llm_verdict["approved"] is (approved in ("True", True))


async def test_no_verdict_when_the_llm_did_not_decide(evaluator_factory, monkeypatch):  # noqa: F811
    evaluator = evaluator_factory()
    evaluator.config.openai_api_key = "isolated-test-placeholder"

    disabled = await evaluator.evaluate_candidate(native_candidate(), use_llm=False)
    assert disabled.approved is True and disabled.llm_verdict is None

    failing = AsyncMock(side_effect=RuntimeError("provider down"))
    monkeypatch.setattr("agentic_trader.agent.evaluator.litellm.acompletion", failing)
    fallback = await evaluator.evaluate_candidate(native_candidate(), use_llm=True)
    assert fallback.approved is True and fallback.llm_verdict is None
```

Update `tests/agent/test_evaluator_catalog.py`:

- line 77: replace `assert result.llm_verdict == {"approved": False, "rejection_reason": "earnings momentum fading"}` with
  `assert result.llm_verdict["approved"] is False and result.llm_verdict["rejection_reason"] == "earnings momentum fading" and result.llm_verdict["applied"] is False`
- line 115: replace `assert result.llm_verdict is None` with `assert result.llm_verdict["applied"] is True and result.llm_verdict["approved"] is False`

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/agent/test_evaluator_llm_verdict.py tests/agent/test_evaluator_catalog.py`
Expected: FAIL — `ImportError: cannot import name 'RankedOutcome'`; after adding the enum alone, the native tests fail on `llm_verdict is None`.

- [ ] **Step 3: Implement**

In `agentic_trader/execution/durable.py`, directly after the `EventKind` class:

```python
class RankedOutcome(StrEnum):
    """Fixed outcomes of a ranked suggestion-scan candidate (``scan_candidates_ranked``).

    Free-text reasons (``"per-scan budget spent"``, ``"rejected: …"``) stay plain strings;
    these two are the outcomes a reader must recognise without parsing text.
    """

    SENT = "sent"
    LLM_VETOED = "llm_vetoed"
```

In `agentic_trader/agent/evaluator.py`, replace the field comment at lines 83-84 with:

```python
    # Every parsed LLM answer: approved/rejection_reason, the bracket as it would be
    # applied, and whether the verdict decided the card (native) or is commentary (catalog).
    llm_verdict: dict[str, Any] | None = None
```

Add a module-level helper after `DeterministicLevels`:

```python
def _explicit_true(value: Any) -> bool:
    """Only an explicit true approves: ``bool("false")`` would record a veto as approval."""
    return value is True or (isinstance(value, str) and value.lower() == "true")
```

Replace lines 728-743 (from `data.pop("llm_verdict", None)` through the `return`) with:

```python
            data.pop("llm_verdict", None)  # only ever set below, never taken from the LLM
            applied = candidate.catalog_event is None
            data["llm_verdict"] = {
                "approved": _explicit_true(data.get("approved")),
                "rejection_reason": data.get("rejection_reason"),
                "stop_loss": llm_stop,
                "take_profit": llm_target,
                "applied": applied,
            }
            if not applied:
                # A catalog probe tests the unfiltered study rule: the verdict is recorded,
                # its text shown as commentary, and it never vetoes (deterministic gates already ran).
                data["approved"], data["rejection_reason"] = True, None
                data["thesis_summary"] = f"LLM commentary (not a gate): {data.get('thesis_summary') or ''}".strip()

            # Never ask the LLM for the earnings note (prompt/schema stay untouched);
            # attach it deterministically after the LLM result is parsed.
            evaluation = LLMTradeEvaluation(**data).model_copy(update={"earnings_note": earnings_note_value})
            if applied and evaluation.llm_verdict is not None:
                # Record exactly the decision taken, after pydantic's own bool parsing.
                evaluation.llm_verdict["approved"] = evaluation.approved
            return evaluation
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/agent/test_evaluator_llm_verdict.py tests/agent/test_evaluator_catalog.py tests/agent/test_evaluator.py`
Expected: PASS.

- [ ] **Step 5: Regression suites, then commit**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/agent tests/research/setups tests/cli tests/notifier`
Expected: PASS.

```bash
git add agentic_trader/execution/durable.py agentic_trader/agent/evaluator.py tests/agent/test_evaluator_llm_verdict.py tests/agent/test_evaluator_catalog.py
git commit -m "Evaluator: record every LLM verdict (applied for native, commentary for catalog); RankedOutcome vocabulary

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: The scan journals vetoes, verdicts and card ids

**Files:**
- Modify: `agentic_trader/agent/copilot.py:1061` (outcome list setup), `:1136-1162` (LLM eval and rejection), `:1182-1206` (provenance), `:1244` (sent outcome), `:1296` (journal call), `:1527-1558` (`_journal_scan_ranking`)
- Test: `tests/agent/test_scan_llm_verdict.py` (new)

**Interfaces:**
- Consumes: `RankedOutcome`, `LLMTradeEvaluation.llm_verdict` from Task 1; `finite_or_none` from `agentic_trader.research.setups.ranker` (already imported in `copilot.py`).
- Produces: each `scan_candidates_ranked` candidate dict gains `"llm": dict | None` and `"signal_id": int | None`; a vetoed candidate's `"outcome"` is `"llm_vetoed"`; a sent card's `decision_provenance["llm_verdict"]` is the same dict or `None`; runner-up reason `"LLM vetoed: <reason>"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/agent/test_scan_llm_verdict.py`:

```python
"""The scan journals an LLM veto as a fixed outcome, with the verdict and the card id."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from agentic_trader.config import ScanBudget
from agentic_trader.execution.durable import EventKind, RankedOutcome
from tests.agent.test_scan_budget import budget_desk, evaluation  # noqa: F401  (fixture + helper)
from tests.agent.test_scan_shadow_ranker import shadow_desk  # noqa: F401  (fixture)


def verdict(approved: bool, *, stop=98.0, target=104.0, applied=True, reason=None) -> dict:
    return {
        "approved": approved,
        "rejection_reason": reason,
        "stop_loss": stop,
        "take_profit": target,
        "applied": applied,
    }


def with_verdict(cand, use_llm, **fields):
    base = vars(evaluation(cand))
    return SimpleNamespace(**{**base, **fields, "model_dump": lambda mode=None: {}})


async def _ranked(db):
    return [e for e in await db.workflows.events() if e["kind"] == EventKind.SCAN_CANDIDATES_RANKED]


async def test_llm_veto_is_a_fixed_outcome_with_its_verdict(shadow_desk, temp_db):  # noqa: F811
    async def evaluate(cand, use_llm=False, **kwargs):
        if use_llm and cand.contract == "DDD":
            return with_verdict(
                cand,
                use_llm,
                approved=False,
                rejection_reason="thesis weak",
                llm_verdict=verdict(False, reason="thesis weak"),
            )
        return with_verdict(cand, use_llm, llm_verdict=verdict(True) if use_llm else None)

    shadow_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=evaluate)
    await shadow_desk.run_scan(use_llm=True, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    [event] = await _ranked(temp_db)
    by_contract = {c["contract"]: c for c in event["payload"]["candidates"]}
    assert by_contract["DDD"]["outcome"] == RankedOutcome.LLM_VETOED == "llm_vetoed"
    assert by_contract["DDD"]["llm"] == verdict(False, reason="thesis weak")
    assert by_contract["DDD"]["signal_id"] is None
    assert by_contract["CCC"]["outcome"] == "sent"
    assert by_contract["CCC"]["llm"] == verdict(True)
    [signal] = await temp_db.get_recent_signals(limit=10)
    assert by_contract["CCC"]["signal_id"] == signal["id"]
    assert signal["decision_provenance"]["llm_verdict"] == verdict(True)
    # Candidates the LLM never saw carry no verdict.
    assert by_contract["BBB"]["llm"] is None and by_contract["BBB"]["signal_id"] is None
    reasons = {r["contract"]: r["reason"] for r in shadow_desk.last_scan_summary["runners_up"]}
    assert reasons["DDD"] == "LLM vetoed: thesis weak"


async def test_deterministic_rejection_and_old_shape_evaluations_are_unchanged(shadow_desk, temp_db):  # noqa: F811
    async def evaluate(cand, use_llm=False, **kwargs):
        approved = not (use_llm and cand.contract == "DDD")
        return SimpleNamespace(
            **{**vars(evaluation(cand)), "approved": approved, "rejection_reason": None if approved else "exposure cap"}
        )

    shadow_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=evaluate)
    await shadow_desk.run_scan(use_llm=True, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    [event] = await _ranked(temp_db)
    outcomes = {c["contract"]: c["outcome"] for c in event["payload"]["candidates"]}
    assert outcomes["DDD"] == "rejected: exposure cap" and outcomes["CCC"] == "sent"
    reasons = {r["contract"]: r["reason"] for r in shadow_desk.last_scan_summary["runners_up"]}
    assert reasons["DDD"] == "rejected: exposure cap"
    [signal] = await temp_db.get_recent_signals(limit=10)
    assert signal["decision_provenance"]["llm_verdict"] is None


async def test_a_commentary_verdict_never_counts_as_a_veto(shadow_desk, temp_db):  # noqa: F811
    async def evaluate(cand, use_llm=False, **kwargs):
        if use_llm and cand.contract == "DDD":
            # applied=False with approved=False is a catalog-style commentary verdict; the
            # evaluation itself was still rejected by a deterministic gate here.
            return with_verdict(
                cand,
                use_llm,
                approved=False,
                rejection_reason="Sizing blocked: tier",
                llm_verdict=verdict(False, applied=False, reason="meh"),
            )
        return with_verdict(cand, use_llm)

    shadow_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=evaluate)
    await shadow_desk.run_scan(use_llm=True, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    [event] = await _ranked(temp_db)
    outcomes = {c["contract"]: c["outcome"] for c in event["payload"]["candidates"]}
    assert outcomes["DDD"] == "rejected: Sizing blocked: tier"


async def test_non_finite_verdict_prices_are_journaled_as_null(shadow_desk, temp_db):  # noqa: F811
    async def evaluate(cand, use_llm=False, **kwargs):
        return with_verdict(
            cand, use_llm, llm_verdict=verdict(True, stop=float("nan"), target=float("inf")) if use_llm else None
        )

    shadow_desk.evaluator.evaluate_candidate = AsyncMock(side_effect=evaluate)
    await shadow_desk.run_scan(use_llm=True, dry_run=False, budget=ScanBudget.FULL, shadow_evidence=True)

    [signal] = await temp_db.get_recent_signals(limit=10)
    assert signal["decision_provenance"]["llm_verdict"]["stop_loss"] is None
    assert signal["decision_provenance"]["llm_verdict"]["take_profit"] is None
    [event] = await _ranked(temp_db)
    sent = next(c for c in event["payload"]["candidates"] if c["outcome"] == "sent")
    assert sent["llm"]["stop_loss"] is None and sent["llm"]["take_profit"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/agent/test_scan_llm_verdict.py`
Expected: FAIL — `KeyError: 'llm'` and runner-up reason `"rejected: thesis weak"`.

- [ ] **Step 3: Implement**

In `agentic_trader/agent/copilot.py`:

(a) Add a class-level helper next to `_runner_up` (line 544):

```python
    @staticmethod
    def _llm_block(evaluation: Any) -> dict[str, Any] | None:
        """The evaluation's recorded LLM verdict with finite prices, or None when the LLM did not decide.

        ``record_signal`` encodes provenance with ``allow_nan=False``; an evaluation object
        without the attribute (older shapes, test doubles) has no verdict.
        """
        verdict = getattr(evaluation, "llm_verdict", None)
        if not isinstance(verdict, dict):
            return None
        return {
            "approved": bool(verdict.get("approved")),
            "rejection_reason": verdict.get("rejection_reason"),
            "stop_loss": finite_or_none(verdict.get("stop_loss")),
            "take_profit": finite_or_none(verdict.get("take_profit")),
            "applied": bool(verdict.get("applied")),
        }
```

(b) At line 1061, after `outcomes: list[str | None] = [None] * len(ranked)`, add:

```python
            llm_by_rank: list[dict[str, Any] | None] = [None] * len(ranked)
            signal_ids_by_rank: list[int | None] = [None] * len(ranked)
```

(c) Replace lines 1147-1151 (the `if not eval_res.approved:` block head through the `runners_up.append`) with:

```python
llm_by_rank[rank - 1] = self._llm_block(eval_res)
if not eval_res.approved:
    verdict = llm_by_rank[rank - 1]
    vetoed = bool(verdict and verdict["applied"] and not verdict["approved"])
    outcomes[rank - 1] = RankedOutcome.LLM_VETOED if vetoed else f"rejected: {eval_res.rejection_reason}"
    label = "LLM vetoed" if vetoed else "rejected"
    summary["runners_up"].append(self._runner_up(candidate, f"{label}: {eval_res.rejection_reason}"))
```

(d) In the `decision_provenance` dict (line 1186 onward) add, after `"shadow_ranker": shadow_by_rank[rank - 1],`:

```python
                            "llm_verdict": llm_by_rank[rank - 1],
```

(e) Replace `outcomes[rank - 1] = "sent"` at line 1244 with:

```python
                    outcomes[rank - 1] = RankedOutcome.SENT
                    signal_ids_by_rank[rank - 1] = sig_id
```

(f) At the call site (line 1296) pass `llm_by_rank=llm_by_rank, signal_ids_by_rank=signal_ids_by_rank,` and add both parameters to `_journal_scan_ranking`'s signature (keyword-only, after `shadow_by_rank`):

```python
        llm_by_rank: list[dict[str, Any] | None],
        signal_ids_by_rank: list[int | None],
```

and in the candidate dict after `"shadow": shadow_by_rank[rank - 1],`:

```python
                    "llm": llm_by_rank[rank - 1],
                    "signal_id": signal_ids_by_rank[rank - 1],
```

Import `RankedOutcome` in the existing `from agentic_trader.execution.durable import …` line (line 54).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/agent/test_scan_llm_verdict.py tests/agent/test_scan_shadow_ranker.py tests/agent/test_scan_budget.py tests/agent/test_scan_drift.py`
Expected: PASS (the drift suite proves a catalog card's commentary verdict still journals as `"sent"`).

- [ ] **Step 5: Regression suites, then commit**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/agent tests/research/setups tests/cli tests/notifier`
Expected: PASS.

```bash
git add agentic_trader/agent/copilot.py tests/agent/test_scan_llm_verdict.py
git commit -m "Scan: journal LLM vetoes as a fixed outcome, with the verdict and the card's signal id

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Market-exposure control and LLM-bracket relabel in `cards outcomes`

**Files:**
- Modify: `agentic_trader/research/setups/labels.py:74` (public `regular_session_bars`, keep usage at `:138`)
- Modify: `agentic_trader/research/setups/outcomes.py` (columns, `market_r`, LLM relabel, summary blocks)
- Test: `tests/research/setups/test_outcomes.py` (extend)

**Interfaces:**
- Consumes: candidate keys `llm` and `signal_id` from Task 2 (both optional in old events); `BracketOutcome.entry_time/exit_time`.
- Produces: `MARKET_PROXY_SYMBOL = "SPY"`; `label_journaled(events, bars, *, …, market_symbol: str | None = MARKET_PROXY_SYMBOL)`; pure `market_r(direction, entry, stop, entry_time, exit_time, market) -> float | None`; new columns `signal_id, entry_time, exit_time, llm_ran, llm_vetoed, llm_r_cost, market_r, excess_r, market_reason`; `summarize` keys `llm_gate` and `market_exposure`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/research/setups/test_outcomes.py` (reuse `_bars`, `FakeBarSource`, `_candidate`, `_event`, `DECIDED_AT`, `NOW`):

```python
from agentic_trader.research.setups.outcomes import MARKET_PROXY_SYMBOL, market_r


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
```

Also update `test_summary_empty_frame` (line 252) if it compares the whole dict: it should assert the new keys exist rather than equality on the old shape.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/research/setups/test_outcomes.py`
Expected: FAIL — `ImportError: cannot import name 'MARKET_PROXY_SYMBOL'`.

- [ ] **Step 3: Implement**

In `labels.py`: rename `_regular_session_bars` to `regular_session_bars`, add it to `__all__` (create `__all__` if absent, listing the existing public names), update the call at line 138.

In `outcomes.py`:

```python
from agentic_trader.research.setups.labels import BracketHit, SetupLevels, label_bracket, regular_session_bars

MARKET_PROXY_SYMBOL = "SPY"

_COLUMNS = (
    "scan_id",
    "session",
    "contract",
    "strategy",
    "direction",
    "rank",
    "outcome",
    "sent",
    "signal_id",
    "setup_quality",
    "shadow_score",
    "hit",
    "r",
    "r_cost",
    "holding_sessions",
    "entry_time",
    "exit_time",
    "llm_ran",
    "llm_vetoed",
    "llm_r_cost",
    "market_r",
    "excess_r",
    "market_reason",
    "decided_at",
    "reason",
)
```

Add `__all__` entries for `MARKET_PROXY_SYMBOL` and `market_r`.

```python
def market_r(
    direction: str,
    entry: float,
    stop: float,
    entry_time: datetime | pd.Timestamp,
    exit_time: datetime | pd.Timestamp,
    market: pd.DataFrame,
) -> float | None:
    """The market proxy's open-to-close return over the candidate's own holding window, in the candidate's R units.

    Entry at the proxy's open on the first regular bar at or after ``entry_time`` (the
    labeler's own entry convention) and exit at its close on the last regular bar at or
    before ``exit_time``. Uncosted: a beta-one exposure control, not a tradable return.
    None when either bar is missing or the geometry has no risk unit.
    """
    risk_unit = abs(float(entry) - float(stop))
    if not risk_unit or market is None or market.empty:
        return None
    regular = regular_session_bars(market)
    at_entry = regular.loc[regular.index >= pd.Timestamp(entry_time)]
    at_exit = regular.loc[regular.index <= pd.Timestamp(exit_time)]
    if at_entry.empty or at_exit.empty or at_exit.index[-1] < at_entry.index[0]:
        return None
    open_price = float(at_entry["Open"].iloc[0])
    close_price = float(at_exit["Close"].iloc[-1])
    if not open_price:
        return None
    sign = 1.0 if direction == "LONG" else -1.0
    return sign * (close_price / open_price - 1.0) * float(entry) / risk_unit
```

Extend `_row(...)` with keyword arguments `entry_time=None, exit_time=None, llm_r_cost=None, market_value=None, market_reason=None` and emit:

```python
        llm = entry.get("llm") if isinstance(entry.get("llm"), dict) else None
        ...
        "signal_id": entry.get("signal_id"),
        "entry_time": entry_time,
        "exit_time": exit_time,
        "llm_ran": llm is not None,
        "llm_vetoed": outcome == "llm_vetoed",
        "llm_r_cost": llm_r_cost,
        "market_r": market_value,
        "excess_r": (r_cost - market_value) if (r_cost is not None and market_value is not None) else None,
        "market_reason": market_reason,
```

In `_label_one(entry, symbol, hourly, max_hold_sessions, cost_bps, market, market_reason)`:

```python
    outcome = label_bracket(levels, entry["decided_at"], hourly, max_hold_sessions=max_hold_sessions, cost_bps_per_side=cost_bps)
    llm_r_cost = _llm_relabel(entry, levels, hourly, max_hold_sessions, cost_bps)
    market_value = None
    if outcome.r_cost is not None and market is not None and outcome.entry_time is not None and outcome.exit_time is not None:
        market_value = market_r(direction, levels.entry, levels.stop, outcome.entry_time, outcome.exit_time, market)
    return _row(entry, symbol, hit=outcome.hit.value, r=outcome.r, r_cost=outcome.r_cost,
                holding_sessions=outcome.holding_sessions, entry_time=outcome.entry_time, exit_time=outcome.exit_time,
                llm_r_cost=llm_r_cost, market_value=market_value, market_reason=market_reason)


def _llm_relabel(entry, levels, hourly, max_hold_sessions, cost_bps) -> float | None:
    """Cost-adjusted R under the LLM's bracket, only when it differs from the deterministic one."""
    llm = entry.get("llm")
    if not isinstance(llm, dict):
        return None
    stop, target = llm.get("stop_loss"), llm.get("take_profit")
    if stop is None or target is None or (float(stop), float(target)) == (levels.stop, levels.target):
        return None
    try:
        llm_levels = SetupLevels(direction=levels.direction, entry=levels.entry, stop=float(stop), target=float(target))
    except ValueError:
        return None
    return label_bracket(llm_levels, entry["decided_at"], hourly, max_hold_sessions=max_hold_sessions, cost_bps_per_side=cost_bps).r_cost
```

In `label_journaled`, add `market_symbol: str | None = MARKET_PROXY_SYMBOL`; after the per-symbol loop collects `rows`… no: the market frame is needed while labelling, so fetch it **after** the candidate symbols but before labelling. Restructure: first fetch bars for every candidate symbol into `frames: dict[str, pd.DataFrame | str]` (frame or failure reason), then fetch the market proxy once (paced; `market: pd.DataFrame | None`, `market_reason: str | None`), then label every entry. Keep the existing "fetch once per symbol" and failure semantics; the fake-source test asserts call order `["AAPL", "SPY"]`. Pass `market` and `market_reason` to `_label_one`; `_immature_row`/`_fetch_failed_row` pass `market_reason` through so the column is populated on every row.

In `summarize`, add after `selection`:

```python
    ran = mature.loc[mature["llm_ran"].astype(bool)]
    vetoed = ran.loc[ran["llm_vetoed"].astype(bool)]
    approved = ran.loc[~ran["llm_vetoed"].astype(bool)]
    edited = ran.dropna(subset=["llm_r_cost"])
    llm_gate = {
        "ran": int(len(ran)),
        "vetoed": int(len(vetoed)),
        "approved": int(len(approved)),
        "vetoed_mean_r_cost": float(vetoed["r_cost"].mean()) if len(vetoed) else None,
        "approved_mean_r_cost": float(approved["r_cost"].mean()) if len(approved) else None,
        "bracket_edited": int(len(edited)),
        "bracket_edit_mean_delta_r": float((edited["llm_r_cost"] - edited["r_cost"]).mean()) if len(edited) else None,
    }
    controlled = mature.dropna(subset=["market_r"])
    market_exposure = {
        "sent": _exposure(controlled.loc[controlled["sent"].astype(bool)]),
        "runner_up": _exposure(controlled.loc[~controlled["sent"].astype(bool)]),
    }
```

with

```python
def _exposure(frame: pd.DataFrame) -> dict[str, Any] | None:
    if frame.empty:
        return None
    return {
        "n": int(len(frame)),
        "mean_r_cost": float(frame["r_cost"].mean()),
        "mean_market_r": float(frame["market_r"].mean()),
        "mean_excess_r": float(frame["excess_r"].mean()),
    }
```

Return both blocks under `"llm_gate"` and `"market_exposure"`; the empty-frame branch returns the zero/None shapes the test pins.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/research/setups/test_outcomes.py tests/research/setups/test_labels.py tests/research/test_alpha_sessions.py`
Expected: PASS.

- [ ] **Step 5: Regression suites, then commit**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/agent tests/research/setups tests/cli tests/notifier`
Expected: PASS (the CLI smoke's fake source serves the same frame for SPY, so it still passes).

```bash
git add agentic_trader/research/setups/labels.py agentic_trader/research/setups/outcomes.py tests/research/setups/test_outcomes.py
git commit -m "cards outcomes: SPY market-exposure control, LLM-bracket relabel and llm_gate/market_exposure summaries

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Execution evidence per sent card

**Files:**
- Create: `agentic_trader/research/setups/execution_evidence.py`
- Modify: `agentic_trader/storage/workflow.py` (add `entry_item_ids` next to `legacy_claims`, line 271)
- Test: `tests/research/setups/test_execution_evidence.py` (new)

**Interfaces:**
- Consumes: `SignalDatabase.get_signal_by_id`, `WorkflowStore.events(stream=…)`, `EventKind.ENTRY_RESOLVED`, `EventKind.CARD_TAP_ASSESSED`, `CardOutcome.EXECUTE` (`agentic_trader/execution/freshness.py`).
- Produces: `WorkflowStore.entry_item_ids(signal_ids: Collection[int]) -> dict[int, str]`; `fill_slip_r(direction, planned_entry, stop, fill_price) -> float | None`; `entry_fill(events) -> tuple[float | None, float | None]`; `execute_tap(events) -> dict | None`; `async collect_execution(db, frame) -> pd.DataFrame` with columns `signal_id, contract, direction, status, planned_entry, stop, fill_price, filled_quantity, fill_slip_r, tap_age_seconds, tap_r_consumed`; `summarize_execution(frame) -> dict`.

- [ ] **Step 1: Write the failing tests**

Create `tests/research/setups/test_execution_evidence.py`:

```python
"""Fill slippage and tap age per sent card, from the entry workflow's own journal evidence."""

import json
from datetime import UTC, datetime

import pandas as pd
import pytest

from agentic_trader.execution.durable import EventKind, WorkKind, WorkStatus
from agentic_trader.research.setups.execution_evidence import (
    collect_execution,
    entry_fill,
    execute_tap,
    fill_slip_r,
    summarize_execution,
)
from agentic_trader.storage.models import SignalRecord, WorkItemRecord


def test_fill_slip_r_is_adverse_positive_in_r_units():
    assert fill_slip_r("LONG", 100.0, 99.0, 100.25) == pytest.approx(0.25)
    assert fill_slip_r("LONG", 100.0, 99.0, 99.75) == pytest.approx(-0.25)
    assert fill_slip_r("SHORT", 100.0, 101.0, 99.75) == pytest.approx(0.25)
    assert fill_slip_r("SHORT", 100.0, 101.0, 100.5) == pytest.approx(-0.5)
    assert fill_slip_r("LONG", 100.0, 100.0, 100.1) is None
    assert fill_slip_r("LONG", 100.0, 99.0, None) is None


def test_entry_fill_reads_the_last_resolved_event_only():
    events = [
        {"kind": EventKind.ENTRY_SUBMITTING, "payload": {"signal_id": 1}},
        {
            "kind": EventKind.ENTRY_RESOLVED,
            "payload": {"status": "rejected", "result": {"fill_price": None, "filled_quantity": None}},
        },
        {
            "kind": EventKind.ENTRY_RESOLVED,
            "payload": {"status": "accepted", "result": {"fill_price": 100.25, "filled_quantity": 4.0}},
        },
    ]
    assert entry_fill(events) == (100.25, 4.0)
    assert entry_fill(events[:1]) == (None, None)


def test_execute_tap_is_the_applied_execute_assessment():
    events = [
        {
            "kind": EventKind.CARD_TAP_ASSESSED,
            "payload": {"outcome": "reprice", "applied": True, "age_seconds": 900.0, "r_consumed": 0.4},
        },
        {
            "kind": EventKind.CARD_TAP_ASSESSED,
            "payload": {"outcome": "execute", "applied": False, "age_seconds": 30.0, "r_consumed": 0.0},
        },
        {
            "kind": EventKind.CARD_TAP_ASSESSED,
            "payload": {"outcome": "execute", "applied": True, "age_seconds": 75.5, "r_consumed": 0.1},
        },
    ]
    assert execute_tap(events) == {"age_seconds": 75.5, "r_consumed": 0.1}
    assert execute_tap(events[:2]) is None


async def _seed(temp_db, *, with_fill: bool, with_tap: bool) -> int:
    async with temp_db.session_factory() as session, session.begin():
        signal = SignalRecord(
            timestamp=datetime(2026, 3, 2, 15, 0, tzinfo=UTC),
            contract="AAPL",
            strategy="BREAKOUT",
            direction="LONG",
            entry_price=100.25,
            stop_loss=99.0,
            take_profit=102.0,
            risk_dollars=5.0,
            status="EXECUTED",
            environment=temp_db.environment,
            execution_mode=temp_db.execution_mode,
        )
        session.add(signal)
        await session.flush()
        item = WorkItemRecord(
            id="client-1",
            scope=temp_db.workflows.scope,
            dedup_key=str(signal.id),
            kind=WorkKind.ENTRY,
            status=WorkStatus.ACCEPTED,
            payload=json.dumps({"signal_id": signal.id}),
            result="{}",
            available_at=datetime(2026, 3, 2, 15, 1, tzinfo=UTC),
            created_at=datetime(2026, 3, 2, 15, 1, tzinfo=UTC),
        )
        session.add(item)
        await temp_db.workflows.lock(session)
        if with_fill:
            await temp_db.workflows.append(
                session,
                stream="entry/client-1",
                kind=EventKind.ENTRY_RESOLVED,
                payload={
                    "status": "accepted",
                    "result": {"fill_price": 100.25, "filled_quantity": 4.0},
                    "recovery": None,
                },
            )
        if with_tap:
            await temp_db.workflows.append(
                session,
                stream=f"card/{signal.id}",
                kind=EventKind.CARD_TAP_ASSESSED,
                payload={
                    "signal_id": signal.id,
                    "outcome": "execute",
                    "applied": True,
                    "age_seconds": 75.5,
                    "r_consumed": 0.1,
                },
                key=f"card_tap_assessed/{signal.id}/t1",
            )
        return signal.id


def _frame(signal_id, *, planned=100.0, stop=99.0):
    return pd.DataFrame(
        [
            {
                "signal_id": signal_id,
                "contract": "AAPL",
                "direction": "LONG",
                "outcome": "sent",
                "sent": True,
                "entry": planned,
                "stop": stop,
            }
        ]
    )


async def test_collect_execution_joins_signal_fill_and_tap(temp_db):
    signal_id = await _seed(temp_db, with_fill=True, with_tap=True)
    assert await temp_db.workflows.entry_item_ids([signal_id, 999]) == {signal_id: "client-1"}

    frame = await collect_execution(temp_db, _frame(signal_id))
    [row] = frame.to_dict("records")
    assert row["status"] == "EXECUTED" and row["fill_price"] == 100.25 and row["filled_quantity"] == 4.0
    assert row["fill_slip_r"] == pytest.approx(0.25)
    assert row["tap_age_seconds"] == 75.5 and row["tap_r_consumed"] == 0.1
    summary = summarize_execution(frame)
    assert summary == {
        "cards": 1,
        "executed": 1,
        "with_fill_evidence": 1,
        "missing_fill_evidence": 0,
        "mean_fill_slip_r": pytest.approx(0.25),
        "mean_tap_age_seconds": 75.5,
    }


async def test_missing_fill_evidence_is_counted_not_zeroed(temp_db):
    signal_id = await _seed(temp_db, with_fill=False, with_tap=False)
    frame = await collect_execution(temp_db, _frame(signal_id))
    [row] = frame.to_dict("records")
    assert row["fill_price"] is None and row["fill_slip_r"] is None and row["tap_age_seconds"] is None
    summary = summarize_execution(frame)
    assert summary["executed"] == 1 and summary["with_fill_evidence"] == 0 and summary["missing_fill_evidence"] == 1
    assert summary["mean_fill_slip_r"] is None and summary["mean_tap_age_seconds"] is None


async def test_collect_execution_skips_rows_without_a_signal_id(temp_db):
    frame = await collect_execution(
        temp_db,
        pd.DataFrame(
            [
                {
                    "signal_id": None,
                    "contract": "AAPL",
                    "direction": "LONG",
                    "outcome": "per-scan budget spent",
                    "sent": False,
                    "entry": 100.0,
                    "stop": 99.0,
                }
            ]
        ),
    )
    assert frame.empty
    assert summarize_execution(frame) == {
        "cards": 0,
        "executed": 0,
        "with_fill_evidence": 0,
        "missing_fill_evidence": 0,
        "mean_fill_slip_r": None,
        "mean_tap_age_seconds": None,
    }
```

Check `SignalRecord`'s required columns and `WorkItemRecord`'s (`sequence`, `attempts` have defaults) before seeding; adjust the seed to whatever the models require, never the assertions.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/research/setups/test_execution_evidence.py`
Expected: FAIL — `ModuleNotFoundError: agentic_trader.research.setups.execution_evidence`.

- [ ] **Step 3: Implement**

In `agentic_trader/storage/workflow.py`, after `legacy_claims`:

```python
    async def entry_item_ids(self, signal_ids: Collection[int]) -> dict[int, str]:
        """Entry work-item ids (the broker client order ids) by signal id, for journal readers."""
        if not signal_ids:
            return {}
        keys = {str(int(signal_id)): int(signal_id) for signal_id in signal_ids}
        async with self.db.session_factory() as session:
            rows = await session.execute(
                select(WorkItemRecord.dedup_key, WorkItemRecord.id).where(
                    WorkItemRecord.scope == self.scope,
                    WorkItemRecord.kind == WorkKind.ENTRY,
                    WorkItemRecord.dedup_key.in_(list(keys)),
                )
            )
            return {keys[dedup_key]: item_id for dedup_key, item_id in rows}
```

Create `agentic_trader/research/setups/execution_evidence.py`:

```python
"""Fill slippage and tap age per sent card, read from the entry workflow's own journal.

Read-only: signal rows, ``entry_resolved`` events on the card's entry work item and the
applied ``execute`` tap assessment. Never infers a fill from a request and never
substitutes a zero for missing evidence.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from agentic_trader.execution.durable import EventKind
from agentic_trader.execution.freshness import CardOutcome
from agentic_trader.storage.db import SignalDatabase

__all__ = ["collect_execution", "entry_fill", "execute_tap", "fill_slip_r", "summarize_execution"]

COLUMNS = (
    "signal_id",
    "contract",
    "direction",
    "status",
    "planned_entry",
    "stop",
    "fill_price",
    "filled_quantity",
    "fill_slip_r",
    "tap_age_seconds",
    "tap_r_consumed",
)


def fill_slip_r(
    direction: str, planned_entry: float | None, stop: float | None, fill_price: float | None
) -> float | None:
    """Adverse-positive slippage of the fill against the planned entry, in the card's R units."""
    if planned_entry is None or stop is None or fill_price is None:
        return None
    risk_unit = abs(float(planned_entry) - float(stop))
    if not risk_unit:
        return None
    adverse = float(fill_price) - float(planned_entry)
    if direction != "LONG":
        adverse = -adverse
    return adverse / risk_unit


def entry_fill(events: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    """``(fill_price, filled_quantity)`` from the last ``entry_resolved`` event, else ``(None, None)``."""
    resolved = [e for e in events if e.get("kind") == EventKind.ENTRY_RESOLVED]
    if not resolved:
        return None, None
    result = (resolved[-1].get("payload") or {}).get("result") or {}
    return result.get("fill_price"), result.get("filled_quantity")


def execute_tap(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The applied ``execute`` tap assessment's age and consumed R, else None."""
    for event in reversed(events):
        payload = event.get("payload") or {}
        if (
            event.get("kind") == EventKind.CARD_TAP_ASSESSED
            and payload.get("outcome") == CardOutcome.EXECUTE
            and payload.get("applied")
        ):
            return {"age_seconds": payload.get("age_seconds"), "r_consumed": payload.get("r_consumed")}
    return None


async def collect_execution(db: SignalDatabase, frame: pd.DataFrame) -> pd.DataFrame:
    """One row per sent card with a ``signal_id`` in ``frame`` (the outcome report's rows)."""
    sent = frame.dropna(subset=["signal_id"]) if "signal_id" in frame else frame.iloc[0:0]
    if sent.empty:
        return pd.DataFrame(columns=list(COLUMNS))
    signal_ids = [int(value) for value in sent["signal_id"]]
    item_ids = await db.workflows.entry_item_ids(signal_ids)
    rows: list[dict[str, Any]] = []
    for record in sent.to_dict("records"):
        signal_id = int(record["signal_id"])
        signal = await db.get_signal_by_id(signal_id) or {}
        fill_price, filled_quantity = (None, None)
        if (item_id := item_ids.get(signal_id)) is not None:
            fill_price, filled_quantity = entry_fill(await db.workflows.events(stream=f"entry/{item_id}"))
        tap = execute_tap(await db.workflows.events(stream=f"card/{signal_id}"))
        rows.append(
            {
                "signal_id": signal_id,
                "contract": record.get("contract"),
                "direction": record.get("direction"),
                "status": signal.get("status"),
                "planned_entry": record.get("entry"),
                "stop": record.get("stop"),
                "fill_price": fill_price,
                "filled_quantity": filled_quantity,
                "fill_slip_r": fill_slip_r(
                    str(record.get("direction")), record.get("entry"), record.get("stop"), fill_price
                ),
                "tap_age_seconds": tap["age_seconds"] if tap else None,
                "tap_r_consumed": tap["r_consumed"] if tap else None,
            }
        )
    return pd.DataFrame(rows, columns=list(COLUMNS))


def summarize_execution(frame: pd.DataFrame) -> dict[str, Any]:
    executed = frame.loc[frame["status"] == "EXECUTED"] if not frame.empty else frame
    with_fill = executed.dropna(subset=["fill_price"]) if not executed.empty else executed
    taps = frame.dropna(subset=["tap_age_seconds"]) if not frame.empty else frame
    return {
        "cards": int(len(frame)),
        "executed": int(len(executed)),
        "with_fill_evidence": int(len(with_fill)),
        "missing_fill_evidence": int(len(executed) - len(with_fill)),
        "mean_fill_slip_r": float(with_fill["fill_slip_r"].mean()) if len(with_fill) else None,
        "mean_tap_age_seconds": float(taps["tap_age_seconds"].mean()) if len(taps) else None,
    }
```

Note: `outcomes.py` rows carry `entry`/`stop` only inside the journaled candidate, not as columns. Task 3's `_row` must also emit `"entry": entry.get("entry")` and `"stop": entry.get("stop")`… **Ruling for the implementer:** add `"entry"` and `"stop"` to `_COLUMNS` and `_row` in this task (it is a one-line extension of Task 3's output and the test frame above uses them), and add a one-line assertion to `test_label_journaled_adds_market_control_and_excess` that `row["entry"] == 100.0 and row["stop"] == 99.0`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/research/setups/test_execution_evidence.py tests/research/setups/test_outcomes.py tests/storage`
Expected: PASS.

- [ ] **Step 5: Regression suites, then commit**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/agent tests/research/setups tests/cli tests/notifier tests/storage`
Expected: PASS.

```bash
git add agentic_trader/research/setups/execution_evidence.py agentic_trader/storage/workflow.py agentic_trader/research/setups/outcomes.py tests/research/setups/test_execution_evidence.py tests/research/setups/test_outcomes.py
git commit -m "Execution evidence: fill slippage and tap age per sent card from entry_resolved and card_tap_assessed

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: CLI wiring, documentation, full suite

**Files:**
- Modify: `agentic_trader/cli/commands/cards.py:91-110`
- Modify: `tests/cli/test_cli_smoke.py:296-379`
- Modify: `docs/production.md` (`cards outcomes` paragraph, around line 526), `docs/alpha-roadmap.md` (L1 row), `CLAUDE.md` (contract 8)

**Interfaces:**
- Consumes: `collect_execution`, `summarize_execution` (Task 4); `summarize`, `label_journaled` (Task 3).
- Produces: `copilot cards outcomes` prints one JSON with the existing keys plus `llm_gate`, `market_exposure` and `execution`, then the outcome table, then an "Execution evidence:" table when non-empty, then the unchanged PEAD section.

- [ ] **Step 1: Extend the failing CLI smoke test**

In `tests/cli/test_cli_smoke.py::test_cli_outputs_table_with_fake_sources`, give the seeded candidate `"llm": {"approved": True, "rejection_reason": None, "stop_loss": 99.0, "take_profit": 102.0, "applied": True}, "signal_id": None` and add assertions after the existing ones:

```python
    assert {call[0] for call in fake_bars.calls} == {"AAPL", "SPY"}
    assert '"llm_gate"' in result.output and '"market_exposure"' in result.output and '"execution"' in result.output
    assert '"ran": 1' in result.output
```

- [ ] **Step 2: Run to verify it fails**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/cli/test_cli_smoke.py -k outcomes`
Expected: FAIL on `'"execution"' in result.output`.

- [ ] **Step 3: Implement**

In `cards.py` `outcomes_cmd`, replace lines 106-110 with:

```python
        summary = summarize(frame)
        execution = await collect_execution(db, frame)
        summary["execution"] = summarize_execution(execution)
        click.echo(json.dumps(summary, indent=2, default=str))
        if frame.empty:
            click.echo(f"No scan_candidates_ranked events in the last {days} day(s).")
        else:
            click.echo(frame.drop(columns=["decided_at", "entry_time", "exit_time"]).to_string(index=False))
        if not execution.empty:
            click.echo("Execution evidence:")
            click.echo(execution.to_string(index=False))
```

and import `collect_execution, summarize_execution` from `agentic_trader.research.setups.execution_evidence`.

- [ ] **Step 4: Run to verify it passes**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/cli/test_cli_smoke.py`
Expected: PASS.

- [ ] **Step 5: Documentation**

`docs/production.md`, after the paragraph that ends "…writes nothing back to the database." add:

```markdown
Since L1 of the [stage-attribution plan](alpha-roadmap.md#stage-layering-and-attribution-october-5)
the report also decomposes each outcome. Every candidate the LLM evaluated carries its
recorded `llm` verdict (`approved`, `rejection_reason`, the bracket as applied, `applied`
— `True` for a native card, `False` for a catalog probe's commentary), and a native card
the LLM rejected is journaled with the fixed outcome `llm_vetoed` (its runner-up reason
reads "LLM vetoed: …"). A sent card carries its `signal_id` and its `decision_provenance`
carries the same `llm_verdict`. The report adds per row `market_r` (SPY's open-to-close
return over the candidate's own entry-to-exit window, scaled into the candidate's R units:
a beta-one, uncosted exposure control), `excess_r = r_cost − market_r`, and `llm_r_cost`
(the cost-adjusted R under the LLM's bracket when it differs from the deterministic one);
and in the summary `llm_gate` (vetoed versus approved mean R, bracket-edit delta),
`market_exposure` (sent and runner-up means with and without the control) and
`execution` (for executed cards: fill minus planned entry in R from the entry work item's
`entry_resolved` event, and the applied tap's age; missing fill evidence is counted, never
zeroed). All of it is descriptive. None of it ranks, gates or sizes a card.
```

`docs/alpha-roadmap.md`: change the L1 row's status to `Implemented — PR #<n>` (the controller fills the number after the PR exists; the implementer writes `Implemented — PR (pending)`), keeping the deliverable text.

`CLAUDE.md` contract 8, append one sentence after "…it is not validated alpha.":

```markdown
   Every LLM-evaluated card records `llm_verdict` (applied for native cards, commentary for
   catalog probes) and `cards outcomes` decomposes selection, LLM gate, market exposure and
   fill slippage; these are descriptive, never gates.
```

- [ ] **Step 6: Full suite and pre-commit**

Run: `env -u VIRTUAL_ENV uv run pytest -q` then `env -u VIRTUAL_ENV uv run pre-commit run --all-files`
Expected: all PASS. Report the test count.

- [ ] **Step 7: Commit**

```bash
git add agentic_trader/cli/commands/cards.py tests/cli/test_cli_smoke.py docs/production.md docs/alpha-roadmap.md CLAUDE.md
git commit -m "cards outcomes: print llm_gate, market_exposure and execution blocks; document L1 stage attribution

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```
