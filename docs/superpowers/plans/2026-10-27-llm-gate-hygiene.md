# LLM Gate Hygiene Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The native-card LLM gate never approves on failure: it has a deadline, a malformed veto fails closed, an error withholds the card (`llm_unavailable`) behind a per-scan circuit breaker, `llm_verdict` carries `model` and `prompt_sha256`, and the request sends only parameters it means (no silently dropped temperature).

**Architecture:** A pure classifier (`agent/llm_gate.py`) turns a raw reply or exception into approve / veto / error; the evaluator calls it under `asyncio.wait_for` and records identity fields; `run_scan` withholds errored native cards and trips a breaker; `cards outcomes` groups the gate by identity. The litellm global `drop_params` is removed and callers opt in per call.

**Tech Stack:** Python 3.14, uv, pydantic, litellm 1.100.1, SQLAlchemy async, pytest.

**Spec:** `docs/superpowers/specs/2026-10-27-llm-gate-hygiene-design.md` (binding). Branch `feature/llm-gate-hygiene`, stacked on `feature/paper-auto-entry` (f910c6a).

## Global Constraints

- D7: the LLM stays decisive for native cards; catalog cards stay commentary (`applied: false`).
- Only the JSON boolean `true` approves (raw parsed value, before pydantic coercion).
- No exception text in `thesis_summary`, cards, journal, outbox or `llm_verdict` (type names only); logs keep `exc_info` but `extra` carries no `str(e)`.
- `macro_clearance` is the deterministic lockout result; no LLM outcome sets or overrides it.
- No prompt text change; no temperature or reasoning-effort change on the wire. The gate and news scorer send `drop_params=False` and no `temperature`; the process-global `litellm.drop_params` is never set to `True` by any code path.
- No migration. Config through `load_config` with a shipped-config test. Imports at module top; no `dict()` calls. Tests: temp SQLite, blocked network, `test_*` PostgreSQL only.
- Exact values: `llm_gate.timeout_seconds` default `60.0` (`gt=0, le=180`); `RankedOutcome.LLM_UNAVAILABLE = "llm_unavailable"`; error type names `LLMKeyMissing`, `LLMUnavailableEarlierInScan` (and `TimeoutError` for the deadline); `llm_verdict` keys `approved, rejection_reason, stop_loss, take_profit, applied, model, prompt_sha256, error, malformed`; malformed veto reason `"malformed veto"`; catalog label "LLM commentary unavailable ({TypeName})"; digest lines "LLM vetoes: N" and "LLM unavailable: N".

## Review Focus

1. **A reply that parses but whose `approved` is JSON `true` while another field fails validation** must be an error (withheld), not an approval or a veto. Task 1 test.
2. **A slow provider:** the scan must not wait past `timeout_seconds` once, and the breaker must stop the remaining calls in that scan. Task 2 test with a fake slow `acompletion`.
3. **Auto-entry interplay (1.4):** an `llm_unavailable` candidate is never recorded, so it is never auto-entered; an approved card still is. Task 2 test.
4. **Removing the global `drop_params`** must not break the chat graph or the macro explainer (offline request-building tests). Task 3.
5. **Old rows** (no `model`/`prompt_sha256`/`error` keys) still summarize: they fall under `(null, null)` and count as ran. Task 4 test.

---

### Task 1: The classifier, the deadline and identity in the evaluator

**Files:**
- Create: `agentic_trader/agent/llm_gate.py`
- Modify: `agentic_trader/agent/evaluator.py` (~L690-926), `agentic_trader/agent/prompts.py`, `agentic_trader/config.py`, `config/config.yaml`
- Test: `tests/agent/test_llm_gate.py` (new), `tests/agent/test_evaluator.py`, `tests/agent/test_evaluator_llm_verdict.py`, `tests/agent/test_evaluator_news_context.py`, `tests/agent/test_evaluator_risk_policy.py`, `tests/config/test_shipped_config.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) GateReply`: `kind: Literal["approve", "veto", "error"]`, `data: dict[str, Any] | None`, `error_type: str | None`, `malformed: bool`.
  - `classify_gate_reply(content: str | None, *, error: BaseException | None = None) -> GateReply` (pure; the spec §2.2 table; strips markdown fences as today).
  - `gate_prompt_sha256(config: AppConfig) -> str` in `prompts.py` (SHA-256 of `build_system_prompt(config)` + `USER_EVALUATION_TEMPLATE`).
  - `LLMGateConfig(timeout_seconds: float = 60.0)`; `AppConfig.llm_gate`.
  - `RiskEvaluator.evaluate_candidate(..., skip_llm_reason: str | None = None)`: when set, returns the error result for that type name without calling the LLM.
  - The returned `LLMTradeEvaluation.llm_verdict` always carries the nine keys above for LLM-evaluated cards (approve, veto or error); `error` is a type name or `None`; `model` is `getattr(response, "model", None)`.
- [ ] **Step 1: Write the failing tests**: the classifier truth table from spec §5 (each row, plus Review Focus 1); evaluator: deadline (`timeout_seconds=0.05`, slow mock → error `TimeoutError`), missing key with `use_llm=True` → error `LLMKeyMissing`, `use_llm=False` unchanged; no exception text anywhere in the result (distinctive planted message); `macro_clearance` equals the deterministic value on error and on veto; `prompt_sha256` stable across cards and changing with a `build_system_prompt` input; `model` from the mock; request kwargs contain `timeout`, `drop_params=False`, `response_format`, and no `temperature`; catalog error → approved commentary with the unavailable label; shipped config gives `llm_gate.timeout_seconds == 60`. Update the fail-open tests listed in spec §5 to the new contract.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.** The `try` around the call is replaced by: call under `asyncio.wait_for`, then `classify_gate_reply`; an exception goes to `classify_gate_reply(None, error=exc)`. Do not change prompt text.
- [ ] **Step 4: Run** `uv run pytest tests/agent tests/config -q`.
- [ ] **Step 5: Stage.**

---

### Task 2: The scan: withheld cards, the breaker, the journal and the digest

**Files:**
- Modify: `agentic_trader/execution/durable.py` (`RankedOutcome.LLM_UNAVAILABLE`), `agentic_trader/agent/copilot.py` (`_llm_block` ~L767-785; the send loop's LLM call and veto classification ~L1640-1680; `publish_scan_digest`)
- Test: `tests/agent/test_scan_llm_verdict.py`, `tests/agent/test_scan_llm_unavailable.py` (new)

**Interfaces:**
- Consumes: Task 1 (`skip_llm_reason`, the nine-key `llm_verdict`).
- Produces: `_llm_block` copies the nine keys; a native candidate whose verdict has `error` is journaled `RankedOutcome.LLM_UNAVAILABLE` and not recorded; the first error sets a per-scan breaker so later gate calls pass `skip_llm_reason="LLMUnavailableEarlierInScan"`; the digest's per-session stats count vetoes and unavailable candidates.
- [ ] **Step 1: Write the failing tests**: native error → no signal, no outbox row, journal outcome `llm_unavailable` with the error block; breaker: second candidate makes no `acompletion` call and is withheld, next scan calls again; slow provider bounded by one timeout (Review Focus 2); catalog error card is sent with the label and is not `llm_unavailable`; malformed veto journaled `llm_vetoed` with `malformed: true`; digest lines "LLM vetoes: N" / "LLM unavailable: N" and unavailable candidates absent from "top:"; an `llm_unavailable` candidate is not auto-entry eligible and an approved native card still is (Review Focus 3); update the exact-block tests in `test_scan_llm_verdict.py`.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest tests/agent tests/cli -q`.
- [ ] **Step 5: Stage.**

---

### Task 3: litellm parameters across callers

**Files:**
- Modify: `agentic_trader/agent/evaluator.py` (~L148: remove the global), `agentic_trader/diagnostics/doctor.py` (~L283), `agentic_trader/agent/macro_explainer.py` (~L113-116), `agentic_trader/agent/copilot_graph.py` (~L64-65), `agentic_trader/agent/news_context.py` (~L116-129)
- Test: `tests/agent/test_llm_params.py` (new), existing news/doctor/macro/chat tests

**Interfaces:**
- Produces: no code path sets `litellm.drop_params = True`; the gate and news scorer pass `drop_params=False` and no temperature; the macro explainer, the doctor's LLM check and the chat graph pass `drop_params=True` per call (for `ChatLiteLLM`, via its per-request kwargs; if not possible, a documented alternative tested offline).
- [ ] **Step 1: Write the failing tests**: `litellm.drop_params` stays `False` after constructing a `RiskEvaluator` and after the doctor's check; news scorer kwargs (`drop_params=False`, no `temperature`); macro explainer and chat graph build their requests with `drop_params=True` (offline: mock `acompletion` / inspect the ChatLiteLLM call kwargs); an offline litellm parameter-mapping check that the gate's parameter set for `openai/gpt-5.6` is accepted with `drop_params=False` and that adding `temperature=0.2` is refused (no request sent; force `LITELLM_LOCAL_MODEL_COST_MAP` if needed).
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest tests/agent tests/diagnostics -q` (and any chat tests).
- [ ] **Step 5: Stage.**

---

### Task 4: `cards outcomes` by identity, and docs

**Files:**
- Modify: `agentic_trader/research/setups/outcomes.py` (row columns ~L380-430; `llm_gate` ~L1088-1164)
- Docs: `CLAUDE.md` (contract 8 sentence), `docs/card-evidence.md`, `docs/production.md`
- Test: `tests/research/setups/test_outcomes.py`

**Interfaces:**
- Produces: row columns `llm_model`, `llm_prompt_sha256`, `llm_error`; `llm_ran` false for an error row; error rows excluded from approved/vetoed attribution and counted as `llm_errors`; `llm_gate["by_identity"]` keyed `"{model}|{prompt_sha256}"` (`"null|null"` for old rows) with the existing gate statistics per key.
- [ ] **Step 1: Write the failing tests**: per-row columns; error rows excluded and counted; `by_identity` grouping including old rows (Review Focus 5); update the exact empty `llm_gate` dict test.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement**, then the docs: CLAUDE.md contract 8 ("an LLM error never approves: native cards are withheld as `llm_unavailable` behind a per-scan breaker, catalog cards are labelled; a malformed veto fails closed; `llm_verdict` carries `model` and `prompt_sha256`; the gate sends no temperature and no code sets litellm's global `drop_params`"), `docs/card-evidence.md` (keys, outcome, `by_identity`), `docs/production.md` (`llm_gate.timeout_seconds`, what an outage looks like, the breaker).
- [ ] **Step 4: Run** the full suite, `--run-postgres` integration on `test_agentic_trader_codex_20260915`, and `uv run pre-commit run --all-files`.
- [ ] **Step 5: Stage.**
