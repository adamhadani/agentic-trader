<!-- Evidence record: replay harness output for PR #88. Scratch scripts/traces were kept outside the repo. -->

# Empirical verification of the macro-clearance prompt change

Branch `fix/macro-prompt-clearance` @ `f9eca96`, worktree
`/Users/adamhadani/Development/agentic-trader-macro-prompt`.
All runs executed from that worktree only. No DB touched (`COPILOT_ENV_FILE=""`, bare `AppConfig()`).

## Part 1 - production traces (found)

LangSmith project `agentic-trader`, window 2026-09-22T19:10Z-19:25Z: exactly **2 LLM runs**,
both `run_type=llm`, name `LLMRun`, model **`gpt-5.6`**, `model_parameters =
{"response_format": {"type": "json_object"}, "temperature": 0.2}`.

| run id | candidate | start (UTC) | latency | prompt/completion tok | cost |
| --- | --- | --- | --- | --- | --- |
| `8fcfe4de-e1a5-49ac-8fe7-ddcb45d66668` | HMY LONG | 19:18:42.818 | 15.44 s | 810 / 728 (453 reasoning) | $0.0178 |
| `1425094e-3a88-4fb9-b717-06dbec71cc06` | PM LONG | 19:18:58.408 | 26.17 s | 809 / 1576 (1279 reasoning) | $0.0348 |

Saved: `traces_raw.json`, `traces_parsed.json`, `trace_PM.json`, `trace_HMY.json`
(full system + user messages and outputs; prompts contain no secrets).

### The "Economic Calendar Status" block the model actually saw (verbatim, identical in both runs)

```
Upcoming Tier-1 releases in next 24 hours:
- FOMC Member Barr Speaks at 14:05 UTC
```

No date, no evaluation time, no clearance verdict. The old system prompt also contained no
statement that lockout had already been verified deterministically.

### What the model returned

HMY - `approved: false`, `macro_clearance: false`
> Macro clearance cannot be confirmed because the current evaluation time relative to the 14:05 UTC tier-1 event is not supplied; the required lockout is 60 minutes before through 30 minutes after the event.

PM - `approved: false`, `macro_clearance: false`
> Macro clearance cannot be verified because the evaluation time relative to the 14:05 UTC tier-1 event is not supplied; approval is prohibited if within the 60-minute pre-event lockout.

Both theses were otherwise positive ("exposure is within limits", "2:1 reward-to-risk"); the
sole blocker was unverifiable macro timing. This reproduces the reported incident exactly.

## The new block (verbatim, rendered by the real `BaseEconomicCalendar.get_macro_summary_for_prompt`)

Same event (`FOMC Member Barr Speaks`, USD, High, 2026-09-23 14:05 UTC - inferred from the old
block: listed as upcoming-within-24h at 19:18 UTC on 09-22, so it is the next day),
`now` = the traced evaluation time, `pre_minutes=60`, `post_minutes=30`:

```
Evaluation time: 2026-09-22 19:18 UTC. Lockout verified: CLEAR (next Tier-1 event in 18h 46m). Upcoming Tier-1 releases in the next 24 hours:
- FOMC Member Barr Speaks at 2026-09-23 14:05 UTC (in 18h 46m)
```

Control (scenario C), `now` = 30 minutes before the event:

```
Evaluation time: 2026-09-23 13:35 UTC. LOCKOUT ACTIVE: Event 'FOMC Member Barr Speaks' scheduled at 2026-09-23 14:05 UTC (lockout window: 60 minutes before and 30 minutes after the event). Trading lockout in effect.
```

## Part 2 - replay harness

`llm_eval.py`. Model `gpt-5.6` (from `$LLM_MODEL`), `response_format={"type":"json_object"}`.

- **A_OLD**: the exact traced system + user messages replayed verbatim, N=5 per candidate, T=0.2.
- **B_NEW**: new `build_system_prompt(AppConfig())` + identical user message with only the
  calendar block swapped for the new renderer's output, N=5 per candidate, T=0.2 (+1 at T=0).
- **C_LOCKOUT**: new system prompt + the LOCKOUT ACTIVE block, N=3 per candidate, T=0.2.

Isolation from production tracing: **both** guards applied - `litellm.success_callback=[]` /
`failure_callback=[]` set in the harness process, **and** `LANGSMITH_PROJECT` overridden to
`agentic-trader-prompt-eval` with `LANGSMITH_TRACING=false` before importing litellm.

### Summary table

| scenario | candidate | N | approved | macro_clearance true | JSON parses + all required keys |
| --- | --- | --- | --- | --- | --- |
| A_OLD | HMY | 5 | **0/5** | 0/5 | 5/5 |
| A_OLD | PM | 5 | **0/5** | 0/5 | 5/5 |
| B_NEW | HMY | 5 | **5/5** | 5/5 | 5/5 |
| B_NEW | PM | 5 | **5/5** | 5/5 | 5/5 |
| B_NEW_T0 (temp 0) | HMY | 1 | **1/1** | 1/1 | 1/1 |
| B_NEW_T0 (temp 0) | PM | 1 | **1/1** | 1/1 | 1/1 |
| C_LOCKOUT | HMY | 3 | **0/3** | 0/3 | 3/3 |
| C_LOCKOUT | PM | 3 | **0/3** | 0/3 | 3/3 |

Schema validation (`approved`, `rejection_reason`, `stop_loss`, `take_profit`,
`macro_clearance`, `thesis_summary`): **0 problems across all 28 calls**.

Mean latency: A_OLD 16.5 s, B_NEW 13.4 s, B_NEW_T0 9.7 s, C_LOCKOUT 8.3 s.

### Distinct rejection reasons

**Under NEW (B_NEW, B_NEW_T0): none - zero rejections in 12/12 calls.** No further funnel gate
was surfaced by the model on these two candidates; the next gate to watch is therefore not
visible from this evidence and would need candidates that actually violate a configured limit.

Under OLD (A_OLD), 9 distinct phrasings of one failure mode, all of the form "macro clearance
cannot be verified/confirmed/established because the current evaluation time relative to the
14:05 UTC tier-1 event is not supplied". Representative:

- "Macro clearance cannot be verified because the current UTC time and exact time-to-event are not supplied. The required lockout is 60 minutes before through 30 minutes after the 14:05 UTC tier-1 event."
- "Macro clearance cannot be established because the current UTC time relative to the 14:05 UTC tier-1 event is not supplied. The mandatory lockout runs from 13:05 through 14:35 UTC, so the candidate must be rejected pending confirmation that execution is outside that window."

Under C_LOCKOUT (the must-reject control), 5 distinct reasons, all correctly citing the active
lockout, e.g. "Macro lockout active: FOMC Member Barr speaks at 2026-09-23 14:05 UTC, and the
13:35 UTC evaluation falls within the configured 60-minute pre-event lockout window."

Raw per-call records: `replay_results.json`, stdout in `replay_stdout.txt`.

## Part 3 - real code path

`part3_real_path.py`: actual `RiskEvaluator.evaluate_candidate(use_llm=True)` from the worktree,
with bare `AppConfig()` plus `llm_model`/three API keys set from env, a `FakeCalendar` holding
the tier-1 event at tomorrow 14:05 UTC, and `regime_detector.get_regime` mocked to a normal
regime (shape copied from `tests/agent/test_evaluator.py::evaluator_factory`). Reconstructed
PM-like `ScreenerCandidate` (price 190.88, ATR 2.93, swing low 185.93, TREND_PULLBACK, 4h).

Macro block assembled by the evaluator itself:

```
Evaluation time: 2026-09-22 19:49 UTC. Lockout verified: CLEAR (next Tier-1 event in 18h 15m). Upcoming Tier-1 releases in the next 24 hours:
- FOMC Member Barr Speaks at 2026-09-23 14:05 UTC (in 18h 15m)
```

Result: **approved = True**, `macro_clearance = True`, `rejection_reason = None`,
`gating_reasons = []`, entry 190.88 / stop 185.75 / target 201.25, R:R 2.02.
Thesis ends "...volatility is normal, and macro clearance is confirmed."
Stdout: `part3_stdout.txt`.

## Cost and volume

29 real LLM calls total (28 harness + 1 real-path). At the traced per-call costs for `gpt-5.6`
($0.018-$0.035, reasoning-token heavy), approximate total **$0.50-$0.75**.

## Caveats

- N is small (5 per arm per candidate; 3 for the control). The separation is complete
  (0/10 vs 10/10) but a 0% / 100% point estimate at N=10 still has a wide confidence interval;
  a rare NEW rejection cannot be excluded.
- `gpt-5.6` is a reasoning model and nondeterministic even at temperature 0.2; the temperature-0
  run is a single sample per candidate, not a proof of determinism.
- Only two real candidates, both TREND_PULLBACK LONG on a clear-macro day with one upcoming
  tier-1 event. The change was not exercised against multiple simultaneous events, missing-event
  days, or candidates that violate other gates.
- Scenario B reuses the traced user message with only the calendar block swapped; production
  also renders a fresh regime block and a fresh clock, so the live prompt will differ slightly.
- The event's date (2026-09-23) was inferred from the old block's 24-hour window, not read from
  the original calendar feed. The old renderer omitted the date, so it cannot be confirmed from
  the trace itself; every alternative consistent with the 24h window is >30 min from 19:18 UTC,
  so the CLEAR verdict is unaffected.
- The C_LOCKOUT arm only checks that the new prompt does not over-suppress; in production the
  deterministic gate blocks that case before the LLM is called.
