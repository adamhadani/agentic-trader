# Stage attribution L1: measure the LLM gate, market exposure and fill slippage (design)

**Date:** 2026-10-05. **Status:** approved by the operator in chat (2026-10-05), execution by
subagent-driven development. **Roadmap:** [Stage layering and attribution, step L1](../../alpha-roadmap.md#stage-layering-and-attribution-october-5).

## Goal

Make three parts of the suggestion-card pipeline measurable from durable evidence
without changing any live decision:

1. the LLM thesis step's verdict on every card it evaluated (today only catalog probes
   record it, and a native card it vetoed is indistinguishable from a deterministic
   rejection);
2. how much of a card's bracket outcome is market exposure rather than selection
   (a same-window SPY control in the card's own R units);
3. how far the broker fill landed from the planned entry, in R, for executed cards.

Everything is descriptive. Nothing here ranks, gates, sizes or promotes. The report is
read-only over the journal and the `signals` table; the only live change is extra
evidence written where evidence is already written.

## Non-goals

- No change to ranking (`setup_quality`), budgets, caps, the LLM prompt or when the LLM
  may veto. The veto is applied exactly as today.
- No schema migration (head stays `008_alpha_pipeline`); all new fields live in existing
  JSON payloads (`decision_provenance`, journal event payloads).
- No sizing evaluation (needs dollar attribution; belongs with L5) and no fitted beta or
  sector control (SPY only, beta-one, uncosted).
- No new Telegram messages. The operator-facing runner-up reason text changes only in
  that an LLM veto reads "LLM vetoed: …" instead of "rejected: …".

## Design

### 1. The evaluator records every LLM verdict

`LLMTradeEvaluation.llm_verdict` (`agentic_trader/agent/evaluator.py`) is filled whenever
the LLM call succeeded and parsed, for native and catalog candidates alike:

```json
{"approved": bool, "rejection_reason": str|null, "stop_loss": float, "take_profit": float, "applied": bool}
```

- `applied` is `True` for a native candidate (the verdict decided the card) and `False`
  for a catalog probe (commentary only, unchanged behaviour).
- For an applied verdict, `approved` equals the evaluation's own `approved` after pydantic
  parsing, so the record is exactly the decision taken. For a commentary verdict it keeps
  today's strict rule: only an explicit `true`/`"true"` counts as approval.
- `stop_loss`/`take_profit` are the LLM's bracket **as it would be applied** after the
  minimum-stop and risk/reward clamps and after a frozen execution policy (`alpha_policy`)
  overrides it. For a policy card they therefore equal the deterministic bracket.
- The deterministic paths (LLM disabled, no key, LLM call or parse failure) leave
  `llm_verdict` `None`: "the LLM did not decide", distinct from "the LLM approved".

### 2. The scan journals the verdict and the card identity

In `TradingCopilot.run_scan`'s send loop and `_journal_scan_ranking` (`agent/copilot.py`):

- A fixed vocabulary for the two outcomes a reader must recognise, as a `StrEnum` next to
  the journal's other vocabulary in `agentic_trader/execution/durable.py`:
  `RankedOutcome.SENT = "sent"`, `RankedOutcome.LLM_VETOED = "llm_vetoed"`. Free-text
  reasons (`"per-scan budget spent"`, `"rejected: …"`) stay strings, unchanged.
- A candidate whose evaluation is not approved **and** whose `llm_verdict` is applied and
  not approved is journaled with outcome `"llm_vetoed"`; its runner-up reason reads
  `"LLM vetoed: <rejection_reason>"`. Every other rejection keeps `"rejected: <reason>"`.
  An evaluation without an `llm_verdict` attribute (tests use plain namespaces) is a
  deterministic rejection.
- Each journaled candidate gains two keys: `"llm"` (the verdict dict with finite floats,
  or `null` when the LLM did not decide) and `"signal_id"` (the recorded card's id for a
  sent candidate, else `null`). Old events without these keys remain readable.
- A sent card's `decision_provenance` gains `"llm_verdict"` (same dict or `null`).

### 3. `cards outcomes` decomposes the outcome

`agentic_trader/research/setups/outcomes.py` stays a pure reader over journal events and
bars. New per-row columns:

| Column | Meaning |
| --- | --- |
| `signal_id` | the card's `signals.id` when sent, else null |
| `entry_time`, `exit_time` | the labeler's entry bar and exit bar (already computed, now kept) |
| `llm_ran` | the candidate carries a verdict |
| `llm_vetoed` | outcome is `llm_vetoed` |
| `llm_r_cost` | cost-adjusted R under the LLM's bracket when it differs from the deterministic one, else null |
| `market_r` | `sign × (SPY_close_at_exit / SPY_open_at_entry − 1) × entry / |entry − stop|`: SPY's return over the candidate's own holding window in the candidate's R units, uncosted; null when SPY bars are missing for either bar |
| `excess_r` | `r_cost − market_r` |
| `market_reason` | the SPY fetch failure, if any |

SPY (`MARKET_PROXY_SYMBOL = "SPY"`) is fetched once per report from the earliest decision
time, through the same paced `BarSource`; a failure leaves `market_r` null everywhere
with the reason recorded, never raises. `_regular_session_bars` in `labels.py` becomes
public `regular_session_bars` so the control uses the same session filter as the labeler.

`summarize` adds two blocks (over mature rows):

- `llm_gate`: `ran`, `vetoed`, `approved`, `vetoed_mean_r_cost`, `approved_mean_r_cost`,
  `bracket_edited`, `bracket_edit_mean_delta_r` (mean of `llm_r_cost − r_cost`).
- `market_exposure`: for `sent` and `runner_up`, `n`, `mean_r_cost`, `mean_market_r`,
  `mean_excess_r` over rows with a market control.

Selection blocks are unchanged; vetoed candidates stay in them as runners-up, since the
ranker scored them.

### 4. Execution evidence is its own module

`agentic_trader/research/setups/execution_evidence.py` reads, per sent card with a
`signal_id`: the signal row (`status`), the entry work item's `entry_resolved` event
(`result.fill_price`, `result.filled_quantity`) and the applied `execute` tap
(`age_seconds`, `r_consumed`) from `card_tap_assessed`. Pure functions compute

- `fill_slip_r(direction, planned_entry, stop, fill_price)`: adverse-positive slippage in
  R, `(fill − planned)/|planned − stop|` for a long and `(planned − fill)/…` for a short;
- `summarize_execution(frame)`: `cards`, `executed`, `with_fill_evidence`,
  `missing_fill_evidence`, `mean_fill_slip_r`, `mean_tap_age_seconds`.

`WorkflowStore` gains one read helper, `entry_item_ids(signal_ids) -> dict[int, str]`
(entry work items by `dedup_key`), so the module never guesses a stream name. The CLI
prints the execution summary under `"execution"` in the same JSON as the outcome summary
and appends the execution frame after the outcome table. The PEAD section is unchanged.

### 5. Documentation

`docs/production.md` (`cards outcomes` paragraph) describes the new columns, blocks and
the SPY control's definition and limits. `docs/alpha-roadmap.md` L1 row moves to
implemented with the PR. `CLAUDE.md` contract 8 gains one sentence: every LLM-evaluated
card records `llm_verdict`, and `cards outcomes` decompositions are descriptive, never gates.

## Testing

TDD per task (failing test first, RED shown, then GREEN), plus a broader regression run
after each task so the CLI, the Telegram scan path and the scan budget stay functional:

- evaluator unit tests (native verdict recorded and applied; catalog verdict recorded and
  not applied; policy card's bracket equals the deterministic one; fallback leaves `None`);
- scan integration tests on the temp database (`llm_vetoed` outcome, `llm` block,
  `signal_id`, provenance; old-shape evaluations still journal `"rejected: …"`; the
  Telegram "no valid setup" text carries "LLM vetoed: …");
- outcomes tests with synthetic bars (market control long/short, missing SPY bars, SPY
  fetch failure, LLM bracket relabel, old events without new keys, summary blocks);
- execution-evidence tests: pure slippage math, and a temp-database integration test that
  seeds a signal, an entry work item, `entry_resolved` and `card_tap_assessed` events;
- CLI smoke extended: SPY is fetched, the new blocks print, no order/Telegram words;
- after each task: `tests/agent`, `tests/research/setups`, `tests/cli`, `tests/notifier`;
  at the end the full `uv run pytest` and `uv run pre-commit run --all-files`.

Deployment evidence is separate: after merge, the controlled restart, `/readyz`,
`scripts/verify_runtime.py`, and the first scheduled scan's `scan_candidates_ranked`
event showing `llm` and `signal_id` keys.

## Amendments after the whole-branch review (2026-10-05)

The execution-evidence section (§4) changed during the final review, superseding the
text above where they differ:

- "Executed" became **entered**: a signal in `EXECUTED` or any `CLOSED_*` status, so
  closed trades stay in the count. Summary keys: `cards, entered, filled,
  missing_fill_evidence, repriced, missing_signal_rows, fill_source, mean_fill_slip_r,
  mean_tap_age_seconds`.
- The fill is read from the latest `order_observed` event on the signal's broker order
  (largest cumulative `filled_quantity`), because an Alpaca limit-bracket acknowledgement
  carries no fill; `entry_resolved` is used only when it actually carries one.
- Re-priced cards are followed (bounded) to their replacement signal; the row shows
  `final_signal_id` and `repriced`.
- Planned entry and stop come from the final signal's `raw_response` (the card as sent,
  LLM-applied bracket, before fills and ratchets); the journal's levels are the fallback.
- `market_r` is `0.0` for a same-bar (gap-at-entry) exit; `market_exposure` carries the
  SPY fetch failure reason; `llm_gate` separates approved from vetoed bracket edits and
  mature from immature edits; `signal_id` is a nullable integer column.

## Acceptance

- Every sent, runner-up and vetoed candidate has a labelled row; vetoed rows are
  identifiable without parsing text.
- The report separates selection (existing), LLM gate, market exposure and fill slippage.
- Live ranking and gating are unchanged: the existing scan-budget, shadow-ranker, drift
  and evaluator suites pass without weakening any assertion other than the two catalog
  tests that pin the old two-key verdict shape.
- No migration, no new Telegram message, no new config.
