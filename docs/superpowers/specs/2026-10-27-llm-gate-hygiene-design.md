# LLM gate hygiene (review item 1.5: SG-R3, decision D7)

Status: design, 2026-10-27. Operator decision D7 (2026-10-23): the LLM stays **decisive** for native
cards, with hygiene. Ordered after 1.4 (operator, 2026-10-27); the branch is stacked on
`feature/paper-auto-entry` (#134, f910c6a) and is rebased onto `main` once #134 merges. A builder check
ran on this document; its seven questions are settled above (drop_params global, deterministic
`macro_clearance`, branch, circuit breaker, strict `true`, missing key, digest counts).

## 1. Why

The native-card LLM gate (`agent/evaluator.py` ~L720-926) decides whether a card is sent. Today:

- **No deadline.** `litellm.acompletion` has no timeout. Observed latency is 15–26 s per call; a
  hung provider holds the scan.
- **Failure approves.** Every error in the call, parsing or validation (one `try`, ~L754-892) lands in
  a fallback (~L894-926) that returns `approved=True`, `macro_clearance=True`, puts the raw exception
  text in `thesis_summary`, and records no `llm_verdict`. So an outage, a timeout, or a **malformed
  veto** (`{"approved": false, ...}` missing `thesis_summary`) sends the card as approved and
  "cleared".
- **No identity.** `llm_verdict` (~L866-872, re-copied by `copilot._llm_block` ~L767-785) records
  neither the model snapshot nor the prompt, so `cards outcomes`' `llm_gate` mixes prompt versions.
- **A parameter that is never sent.** The gate requests `temperature=0.2`; `RiskEvaluator.__init__` sets
  the process-global `litellm.drop_params = True` (~L148). For `openai/gpt-5.6` with reasoning active,
  litellm 1.100.1 drops any temperature other than 1 (probe in `docs/llm-prompt-eval-2026-10-22.md`
  "Production call parameters"). Production has therefore always run with **reasoning on at the
  model's default temperature**; the `0.2` in code and in LangSmith is fiction. The news scorer's
  `temperature=0` is dropped the same way.

With paper auto-entry (1.4), an approved card is entered automatically, so a fail-open gate now
places orders.

## 2. Decisions

### 2.1 Deadline

- New config block `llm_gate:` (`LLMGateConfig`, through `load_config`, shipped-config test):
  `timeout_seconds: float = 60.0` (`gt=0, le=180, allow_inf_nan=False`).
- The call is wrapped in `asyncio.wait_for(..., timeout=timeout_seconds)` and also passes litellm's
  `timeout=timeout_seconds`, the `news_context.py` precedent (~L115-129).

### 2.2 Outcomes of a gate call

One function classifies the reply; there are exactly three results:

| Reply | Native card | Catalog card (commentary) |
|---|---|---|
| Valid reply (parses, validates as `LLMTradeEvaluation`) whose raw `approved` is the JSON boolean `true` | approved (as today) | sent, commentary (as today) |
| Parses as a JSON object whose raw `approved` is anything other than the JSON boolean `true` (`false`, `"true"`, `"yes"`, `1`, missing; including a reply that then fails validation) | **veto** (`approved: false`, `rejection_reason` from the reply if it is a string, else `"malformed veto"`) | sent, commentary |
| Anything else: exception, timeout, non-JSON, non-object, a JSON-`true` approval that fails validation, or no LLM API key on an LLM scan | **error**: card withheld | sent, labelled "LLM commentary unavailable ({TypeName})" |

Only the JSON boolean `true` approves; the classifier reads the raw parsed value before pydantic's lax
bool coercion (today `_explicit_true` also accepts the string `"true"` and pydantic turns `"yes"` into
`True`). A missing API key on a scan with `use_llm=True` (today a silent approval, `evaluator.py`
~L700-731) is an error with type name `LLMKeyMissing`; `use_llm=False` (deterministic mode, dry runs)
is unchanged.

**Circuit breaker.** The first error in a scan stops LLM calls for the rest of that scan: every later
candidate that would call the gate is classified as an error without a call (native: withheld as
`llm_unavailable`, reason type name `LLMUnavailableEarlierInScan`; catalog: the unavailable label). The
worst-case added scan time is one `timeout_seconds`. The breaker resets at the next scan.

- **Error (native).** The card is not sent and no signal is recorded. The candidate is journaled with a
  new `RankedOutcome.LLM_UNAVAILABLE = "llm_unavailable"`, and its `llm` block is
  `{"error": <TypeName>, "applied": true, "approved": false, "model": null, "prompt_sha256": ...}`.
  The LLM budget already spent stays spent (as today). The end-of-session digest gains two count lines,
  "LLM vetoes: N" and "LLM unavailable: N"; unavailable candidates are not listed among the "top:"
  runners-up.
- **`macro_clearance` is the deterministic lockout result, never the LLM's.** It is computed before the
  call (`evaluator.py` ~L776-778); the fallback no longer overrides it with `True`, and no LLM outcome
  changes it. (A catalog card that reached the gate has passed the lockout, so it still renders as
  cleared; setting it false would print a false "Event Alert Active".)
- Never exception text in `thesis_summary`, the card, the journal or `llm_verdict` (type names only).
  The log line keeps the type name and `exc_info` locally; `extra` carries no `str(e)`.
- A veto keeps today's journaling (`RankedOutcome.LLM_VETOED`); a malformed veto additionally records
  `"malformed": true` in `llm_verdict`.
- Catalog cards keep commentary semantics (`applied: false`), unchanged except the unavailable label on
  error.

### 2.3 Identity in `llm_verdict`

- `model`: the provider's returned snapshot (`response.model`, e.g. `gpt-5.6-sol`); null on error.
- `prompt_sha256`: SHA-256 of the rendered system prompt (`build_system_prompt(config)`, config-only)
  concatenated with the `USER_EVALUATION_TEMPLATE` string, i.e. the prompt's identity without the
  per-card values. Computed once per evaluator. A constant `GATE_PROMPT_VERSION` is not added; the
  hash is the identity.
- `error` (type name) and `malformed` (bool) as above.
- `copilot._llm_block` (~L767-785) carries the new keys to `decision_provenance["llm_verdict"]` and the
  journal's `llm` block. Existing five keys are unchanged.

### 2.4 Request parameters

- The gate sends exactly: `model`, `messages`, `response_format={"type": "json_object"}`,
  `timeout`, and `drop_params=False` (per call). It sends **no `temperature`**. This changes nothing on
  the wire (temperature was already dropped), so approvals are unchanged; the code now says what is
  sent, and a future unsupported parameter fails loudly instead of silently.
- The news scorer (`agent/news_context.py` ~L116-129) drops its `temperature=0` and passes
  `drop_params=False` the same way.
- **The process-global flag is removed.** litellm drops a parameter when `litellm.drop_params or
  drop_params` (`litellm/utils.py`, `gpt_5_transformation.py`), so a per-call `False` is meaningless
  while the global is `True`. `RiskEvaluator.__init__` (~L148) and `diagnostics/doctor.py` (~L283) stop
  setting `litellm.drop_params = True`. The callers that relied on it pass `drop_params=True` per call:
  `agent/macro_explainer.py` (~L113-116), the doctor's LLM check, and the chat graph
  (`agent/copilot_graph.py` `ChatLiteLLM`, through its per-request kwargs; if ChatLiteLLM cannot carry
  it, the builder documents how the chat path stays working and tests it offline). Tests that construct
  a `RiskEvaluator` must not leave the global changed (assert it stays `False`).
- Running at temperature 0.2 would require `reasoning_effort="none"`, a behaviour change. It is out of
  scope and needs its own prompt evaluation (memory rule: LangSmith traces plus a replay harness).
- An offline test asserts litellm accepts the gate's parameter set for `openai/gpt-5.6` with
  `drop_params=False` (litellm's parameter mapping, no request sent).

### 2.5 Measurement

- `cards outcomes`: rows gain `llm_model`, `llm_prompt_sha256`, `llm_error`. `llm_ran` is false for an
  error row; error rows are excluded from approved/vetoed attribution and counted as `llm_errors`.
- The `llm_gate` block adds `by_identity`: the existing gate statistics grouped by
  `(llm_model, llm_prompt_sha256)`, with rows from before this change under `(null, null)`.

## 3. Out of scope

- Switching the LLM to commentary-only for native cards (D7 kept it decisive).
- Changing reasoning effort or temperature (needs a prompt evaluation).
- Any prompt text change.
- Refunding the LLM budget on errors.

## 4. Contract and doc changes

- CLAUDE.md contract 8: "Every LLM-evaluated card records `llm_verdict`…" gains: an LLM error never
  approves (native cards are withheld as `llm_unavailable`; catalog cards are labelled), a malformed
  veto fails closed, and `llm_verdict` carries `model` and `prompt_sha256`.
- `docs/card-evidence.md`: the new `llm_verdict` keys, `llm_unavailable`, `by_identity`.
- `docs/production.md`: `llm_gate.timeout_seconds`; what an LLM outage looks like (no native cards,
  digest line, journal outcome).

## 5. Testing

- Classifier truth table: valid approval; valid veto; veto missing `thesis_summary`; `approved: "true"`,
  `"yes"`, `1`, missing; `approved: true` missing a required field; non-JSON; JSON array; exception;
  timeout (fake slow `acompletion` with a tiny `timeout_seconds`); no API key with `use_llm=True`
  (error) and `use_llm=False` (deterministic, unchanged).
- Native: an error withholds the card, journals `llm_unavailable` with the error block, records no
  signal, sends no notification, and the digest counts it; a malformed veto journals `llm_vetoed` with
  `malformed: true`; after one error, later candidates in the same scan make no LLM call and are
  withheld; the next scan calls again.
- Catalog: an error still sends the card with the unavailable label; `macro_clearance` keeps the
  deterministic value (no false "Event Alert Active").
- `litellm.drop_params` is `False` after constructing a `RiskEvaluator` and after the doctor's LLM
  check; the macro explainer and chat paths still work offline with their per-call `drop_params=True`.
- No exception text anywhere (plant a distinctive message in the raised exception and assert it is
  absent from the evaluation, signal, journal, outbox and card text).
- `llm_verdict` carries `model` (from a mock with `.model`) and a stable `prompt_sha256` (same across
  cards, different when `build_system_prompt` inputs change).
- Request kwargs: no `temperature`, `drop_params=False`, `timeout` set; offline litellm parameter
  acceptance for `openai/gpt-5.6`.
- `cards outcomes`: error rows excluded from attribution and counted; `by_identity` grouping.
- Update the tests that pin today's fail-open behaviour to the new contract:
  `tests/agent/test_evaluator.py` ~L452-456, `tests/agent/test_evaluator_llm_verdict.py` ~L42-48 and
  ~L93-103, `tests/agent/test_evaluator_news_context.py` ~L69-88, `tests/agent/test_evaluator_risk_policy.py`
  ~L557-570 and ~L773-792, `tests/agent/test_scan_llm_verdict.py` (exact journaled block), and
  `tests/research/setups/test_outcomes.py` ~L495-505 (exact `llm_gate` dict).
- Full suite, `--run-postgres` on a `test_*` database, pre-commit.
