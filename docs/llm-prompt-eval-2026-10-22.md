<!-- Evidence record: replay harness output for the Phase 0 trade-gate prompt changes (branch feature/phase0-fail-safes). Scratch scripts, captures and raw replies were kept outside the repo. -->

# Empirical verification of the Phase 0 trade-gate prompt changes

- **Branch:** `feature/phase0-fail-safes`, worktree `/Users/adamhadani/Development/agentic-trader-phase0`.
  - Code evaluated at `7a51d879097afc6b8ed31dcc9c80325c0e56886e`, the branch HEAD during the runs.
  - The branch HEAD is now `7f6a66ab7d87c686d0c061d8213fe91f2410640d`. That commit changes 11 documentation files and no code, so the evaluated code is identical.
- **Main:** `db82817f056508cf1c1bc28aef7b452ea2fef579`, run from a temporary detached worktree that has since been removed.
- **Date and time:** 2026-10-22, 09:11–09:16 New York time. No LLM call was made inside the 10:20–10:50 or 14:20–14:50 production scan windows.
- **Isolation:** no database, broker, data provider or Telegram was touched. Nothing was run in the production checkout.

## What changed in the gate prompt

1. **One line in the system prompt** (`agentic_trader/agent/prompts.py:22`):

   ```
   - Macro lockout is verified deterministically before you are consulted; the Economic Calendar Status below states the result and the evaluation time. Do not reject a candidate on macro timing unless that status says LOCKOUT ACTIVE.
   + Macro lockout is checked deterministically before you are consulted; the Economic Calendar Status below states the result, its source and the evaluation time. Do not reject a candidate on macro timing unless that status says LOCKOUT ACTIVE.
   ```

2. **The Economic Calendar Status block.**
   - Main renders it with `BaseEconomicCalendar.get_macro_summary_for_prompt`.
   - The branch renders it with `CalendarCheck.prompt_text()`.
3. **The regime block.** On the branch, `RegimeDetector.get_prompt_context` shows `UNKNOWN` macro stress and names the cause of a breakout suppression.

The rest of the system and user messages is byte-identical between the versions: a diff of the S1 and S2 captures contains exactly two changed lines, the system line above and the calendar block.

## Part 1 — production traces (read, but stale)

Source: LangSmith project `agentic-trader`, read 2026-10-22T13:04Z with `run_type=llm` over a 45-day window.

- **First request.** It used `limit=200` and was refused with HTTP 400: "Limit exceeds maximum allowed value of 100". This was a request error, not the quota.
- **Retry.** With `limit=100` the read succeeded. It returned **4 runs**, all `LLMRun`, and **no 429 on the read**.
- **Newest run:** `4e7d4e73-c02c-4a09-bc2e-8790a20d605a` at **2026-10-09T14:38:23Z**. No run after 2026-10-09 is stored, which matches the exhausted monthly ingest quota. Production gate calls since then are untraced.

| run id | start (UTC) | candidate | traced verdict |
| --- | --- | --- | --- |
| `4e7d4e73-c02c-4a09-bc2e-8790a20d605a` | 2026-10-09 14:38:23 | CRM TREND_PULLBACK LONG | approved, macro_clearance true |
| `85f5f964-2dd6-4837-8922-342522ff7b04` | 2026-10-09 13:43:13 | MRK TREND_PULLBACK LONG | approved, macro_clearance true |
| `577ec3ce-ce62-438b-9e02-862c64b9b04d` | 2026-10-08 17:43:15 | AMGN TREND_PULLBACK LONG | approved, macro_clearance true |
| `804abb32-907f-4b49-ade8-42d55b37dbad` | 2026-10-08 14:38:53 | KO TREND_PULLBACK LONG | approved, macro_clearance true |

What the four traces show:

- All four use the post-2026-09-22 format: the system prompt contains "Macro lockout is verified deterministically".
- All four system prompts are byte-identical to main's `build_system_prompt` under the production config.
- The response model is `gpt-5.6-sol`.
- Each saw the same calendar block, `Evaluation time: … Lockout verified: CLEAR. No Tier-1 US economic releases (CPI, PPI, FOMC, NFP) scheduled in the next 24 hours.`

**How the two newest traces were used:**

- **A_OLD_TRACE** replays CRM and MRK verbatim.
- **B_NEW_TRACE** uses the same messages with exactly two substitutions:
  - the system line is replaced by the branch's line, after which the system prompt is asserted equal to the branch's `build_system_prompt`;
  - the calendar block is replaced by the branch's `CalendarCheck.prompt_text()`, computed from the shipped static calendar and an ok in-process ForexFactory feed.
- **Why the date was moved.** `us-tier1-v1` is valid from 2026-10-21. At the traced 2026-10-09 instant, the branch would render `Lockout UNVERIFIED: static tier-1 calendar expired; live feed ok; treat scheduled-release timing as unknown.`. B_NEW is therefore rendered at the same UTC clock time on 2026-10-22, the first post-deploy weekday with no tier-1 release in the next 24 hours. This changes the evaluation date in the block, and nothing else in the prompt carries a date.

```
OLD (traced):  Evaluation time: 2026-10-09 14:38 UTC. Lockout verified: CLEAR. No Tier-1 US economic releases (CPI, PPI, FOMC, NFP) scheduled in the next 24 hours.
NEW (branch):  Evaluation time: 2026-10-22 14:38 UTC. Lockout CLEAR per static tier-1 calendar us-tier1-v1 (sha256 984c83a8), valid through 2027-12-03; live cross-check (ForexFactory): ok. No tier-1 US releases (FOMC, CPI, PPI, Employment Situation) in the next 24 hours.
```

The MRK pair is the same, at 13:43 UTC.

## Part 2 — real code path capture

### Harness

- **Scripts.** `capture_main.py` runs on main and `capture_branch.py` on the branch. Each uses its own worktree's code, its own `config/config.yaml` (through `load_config(path, environ=…)`, with no database or storage) and its own environment.
- **Candidates.** Both are native equity LONG candidates built as the strategies build them:
  - `XLE` TREND_PULLBACK, 4h: price 92.40, ATR 1.05, swing low 90.71. The evaluator sizes 81 shares and sets stop 90.69 and target 95.82.
  - `XLI` SQUEEZE_BREAKOUT, 4h: price 148.62, ATR 1.62, swing low 146.20. The evaluator sizes 50 shares and sets stop 146.18 and target 153.50.
  - Both have an empty portfolio: `active_positions=[]` and open notional 0.
- **Regime.** A real `RegimeDetector` over a real `MacroIntelligenceEngine` builds the snapshot. Only the I/O is faked:
  - FRED: a `FredDataClient` subclass returns fredgraph CSV text from `_fetch_csv_text`, or raises `httpx.ConnectTimeout` for S7/S8.
  - Yahoo: on main, `_fetch_ticker_sync` is patched. On the branch, an injected `yahoo_fetch` goes through the real `_yahoo_input_from_history`.
  - Observations: VIX 16.12, ^IRX 4.05, DGS2 4.76, ^FVX 4.93, ^TNX 5.24, ^TYX 5.52, DXY 102.18, HY OAS 3.12, T10YIE 2.35, T5YIE 2.31.
- **Calendar.**
  - Main uses a `BaseEconomicCalendar` subclass with events injected through the same filter as `ForexFactoryCalendar.fetch_events`: USD and (High or tier-1).
  - The branch uses `UnionTier1Calendar.from_config` over the shipped `config/calendar/us-tier1-v1.json`, pinned by SHA-256 and status ok. Its live feed is a real `ForexFactoryCalendar` over an `httpx.MockTransport`, so no socket is opened.
  - Both read the same ForexFactory-format weekly items. The week of 2026-10-25 lists Federal Funds Rate, FOMC Statement (18:00 UTC) and FOMC Press Conference (18:30 UTC) on 2026-10-28, beside non-tier-1 USD and EUR items.
- **Clock.** The evaluator's `datetime.now` is pinned per scenario.
- **Capture and e2e.**
  - `litellm.acompletion` is wrapped once per scenario and candidate.
  - A capture-only pass recorded the messages with no network access.
  - The e2e pass forwarded **one call per scenario and candidate** to the real model. The evaluator then parsed the reply end to end: `approved`, `macro_clearance` and `llm_verdict`. This call counts as replay 1 of N.
  - The capture and e2e messages were asserted byte-identical.
  - `replay.py` then replayed the captured messages N−1 more times.
- **S6.**
  - The real branch evaluator was run at 2026-10-28 18:00 UTC. For both candidates it refused deterministically **before any LLM call** with `rejection_rule=macro_lockout`, "Macro event lockout: FOMC Statement at 18:00 UTC.".
  - The `CalendarCheck.prompt_text()` from that instant was then spliced into S2's captured messages in place of S2's calendar block.
  - The brief asked for "30 minutes before the 18:30 UTC press conference". That instant is the statement time, so the block names the FOMC Statement, which is the first static event whose window contains it.
- **S5.**
  - The static calendar is the shipped, digest-verified file, with `valid_through` lapsed to 2026-10-21 via `dataclasses.replace`. The real `StaticTier1Calendar.state()` then returns `expired`.
  - The ForexFactory transport answers HTTP 429, which the real client reports as `failed (UnexpectedStatus)`.
  - The policy is the shipped `risk.macro_calendar_unknown: open`. The evaluator reached the LLM and set `macro_note="static tier-1 calendar expired; live feed failed (UnexpectedStatus)"`.

### Production call parameters: `temperature=0.2` is not sent

The evaluator passes `response_format={"type": "json_object"}, temperature=0.2` (`evaluator.py:751-760`). However, `RiskEvaluator.__init__` sets `litellm.drop_params = True` on both versions (main `evaluator.py:145`, branch `:147`).

For `openai/gpt-5.6`, litellm 1.100.1 rejects any temperature other than 1 while reasoning is active. With `drop_params` it drops the parameter. An offline probe (`probe_params.py`, no request sent) gives:

- `drop_params=False` → `UnsupportedParamsError: gpt-5.6 doesn't support temperature=0.2 while reasoning is active…`
- `drop_params=True` → `{'stream': False, 'response_format': {'type': 'json_object'}, 'extra_body': {}}`

**Production therefore samples at the model's default temperature, with reasoning on: 108–698 reasoning tokens per call here.** The replays set `litellm.drop_params = True` to match.

A first replay attempt without it failed locally with `UnsupportedParamsError` on all 26 jobs, before any request. It made no LLM calls, recorded nothing and is not counted.

The September precedent's "T=0.2" and its "temperature 0" arm describe the requested value, not necessarily what was sent. This was not re-verified for that run.

### Isolation guards

- **Credentials.** The harness shell loads only `LLM_MODEL`, the three LLM keys, `PORTFOLIO_CASH` and `MAX_NOTIONAL_EXPOSURE`, using a `grep '^export …'` of the production `.envrc`.
  - The file's `. .venv/bin/activate` line, and the Telegram, Alpaca, EDGAR and LangSmith variables, are never loaded into the LLM harness.
  - `load_config` receives only that explicit mapping, with `COPILOT_ENV_FILE=""`, so it reads no dotenv file.
- **Tracing.**
  - `LANGSMITH_TRACING=false` and `LANGSMITH_PROJECT=agentic-trader-prompt-eval` are set before litellm is imported.
  - `LANGSMITH_API_KEY` and `LANGCHAIN_API_KEY` are absent from the process, so the evaluator's auto-registration does not fire.
  - `litellm.success_callback`, `failure_callback` and `callbacks` are cleared after every evaluator construction and asserted empty before every call.
  - `LITELLM_LOCAL_MODEL_COST_MAP=True` keeps litellm from fetching its cost map.
- **Network.** `socket.getaddrinfo` is wrapped to resolve only `api.openai.com`, and only in e2e and replay processes. The capture passes resolve nothing.
- **Clock windows.** A guard refuses to start any LLM call at New York 10:18–10:50 or 14:18–14:50.
- **Worktrees.**
  - The branch worktree was used read-only: `uv run --no-sync --project … python -I -B`, with no bytecode writes and no edits, staging or commits.
  - Its `git status` was unchanged by these runs. The only change, the docs-only commit `7f6a66a`, was made by the other agent.
  - Nothing was run in the production checkout, which is still at `db82817` and clean.
- **Secrets.** The scratch outputs were scanned for every loaded credential value: none was found. API keys are popped from the captured kwargs before they are saved.

## The blocks the model saw (verbatim, from the captured messages)

### Calendar blocks

S1 OLD_CLEAR and S7 OLD_MACRO_FAILED, main, 2026-10-22 15:00 UTC:

```
Evaluation time: 2026-10-22 15:00 UTC. Lockout verified: CLEAR. No Tier-1 US economic releases (CPI, PPI, FOMC, NFP) scheduled in the next 24 hours.
```

S2 NEW_CLEAR and S8 NEW_UNKNOWN, branch, 2026-10-22 15:00 UTC:

```
Evaluation time: 2026-10-22 15:00 UTC. Lockout CLEAR per static tier-1 calendar us-tier1-v1 (sha256 984c83a8), valid through 2027-12-03; live cross-check (ForexFactory): ok. No tier-1 US releases (FOMC, CPI, PPI, Employment Situation) in the next 24 hours.
```

S3 OLD_UPCOMING, main, 2026-10-27 19:00 UTC:

```
Evaluation time: 2026-10-27 19:00 UTC. Lockout verified: CLEAR (next Tier-1 event in 23h 0m). Upcoming Tier-1 releases in the next 24 hours:
- Federal Funds Rate at 2026-10-28 18:00 UTC (in 23h 0m)
- FOMC Statement at 2026-10-28 18:00 UTC (in 23h 0m)
- FOMC Press Conference at 2026-10-28 18:30 UTC (in 23h 30m)
```

S4 NEW_UPCOMING, branch, 2026-10-27 19:00 UTC. The live Federal Funds Rate and FOMC items match the static events within 5 minutes, so the union lists the two static events:

```
Evaluation time: 2026-10-27 19:00 UTC. Lockout CLEAR per static tier-1 calendar us-tier1-v1 (sha256 984c83a8), valid through 2027-12-03; live cross-check (ForexFactory): ok. Next tier-1 release in 23h 0m. Upcoming tier-1 releases in the next 24 hours:
- FOMC Statement at 2026-10-28 18:00 UTC (in 23h 0m)
- FOMC Press Conference at 2026-10-28 18:30 UTC (in 23h 30m)
```

S5 NEW_UNVERIFIED, branch, 2026-10-22 15:00 UTC:

```
Evaluation time: 2026-10-22 15:00 UTC. Lockout UNVERIFIED: static tier-1 calendar expired; live feed failed (UnexpectedStatus); treat scheduled-release timing as unknown.
```

S6 NEW_LOCKOUT control, branch `CalendarCheck.prompt_text()` at 2026-10-28 18:00 UTC, spliced into S2:

```
Evaluation time: 2026-10-28 18:00 UTC. LOCKOUT ACTIVE: Event 'FOMC Statement' scheduled at 2026-10-28 18:00 UTC (lockout window: 60 minutes before and 30 minutes after the event). Trading lockout in effect.
```

### Regime blocks

S1–S6, both versions, identical: measured LOW at 1.00×.

```
Market Volatility & Macro Regime Context:
• Volatility Regime: NORMAL (CBOE VIX: 16.12)
• 10-Year US Treasury Yield (^TNX): 5.24%
• US Dollar Index (DXY): 102.18
• US Treasury Yield Curve: NORMAL_STEEP (10Y-2Y Spread: +48.0 bps, 10Y-3M Spread: +119.0 bps)
• Credit Risk (High Yield OAS): 312 bps (BENIGN)
• 10Y Breakeven Inflation: 2.35% (ANCHORED)
• Compound Macro Stress: LOW (Risk Multiplier: 1.00x)
• Strategy Adaptation: Squeeze Breakouts ALLOWED; Minimum required Reward-to-Risk: 2.0:1.
```

S7 OLD_MACRO_FAILED, main: FRED failed and the snapshot has no macro report. `macro_unavailable_reason` is "Macro data unavailable: 2y, hy_oas, t10yie, t5yie; no baseline values substituted". Risk is 1.0×, breakouts are allowed and 81 shares are sized.

```
Market Volatility & Macro Regime Context:
• Volatility Regime: NORMAL (CBOE VIX: 16.12)
• 10-Year US Treasury Yield (^TNX): 5.24%
• US Dollar Index (DXY): 102.18
• Strategy Adaptation: Squeeze Breakouts ALLOWED; Minimum required Reward-to-Risk: 2.0:1.
```

S8 NEW_UNKNOWN, branch: the same FRED failure gives `UNKNOWN` at 0.5× with breakouts suppressed. It sizes 73 shares, and the candidate notional is $6,745.20 instead of $7,484.40.

```
Market Volatility & Macro Regime Context:
• Volatility Regime: NORMAL (CBOE VIX: 16.12)
• 10-Year US Treasury Yield (^TNX): 5.24%
• US Dollar Index (DXY): 102.18
• Compound Macro Stress: UNKNOWN (missing 2y, hy_oas; Risk Multiplier: 0.50x)
• Strategy Adaptation: Squeeze Breakouts SUPPRESSED (Macro Stress UNKNOWN: missing 2y, hy_oas); Minimum required Reward-to-Risk: 2.0:1.
```

## Summary table

Common to every row:

- Model `openai/gpt-5.6` (the responses report `gpt-5.6-sol`), `response_format=json_object`, temperature dropped by `drop_params` (see above).
- "macro_clearance" is the model's raw reply value. For any candidate that reaches the LLM, the evaluator overwrites it with `True`, the deterministic gate's verdict. The e2e evaluator results all had `approved=True`, `llm_verdict.approved=True` and `macro_clearance=True`, except where the table notes otherwise.
- "Schema" means the reply parses as JSON (after the evaluator's own fence stripping) and carries all 15 template keys.
- N includes the one e2e evaluator call, except for the trace arms and S6, which are replay-only.

| scenario | candidate | N | approved | macro_clearance true | JSON + all 15 keys |
| --- | --- | --- | --- | --- | --- |
| A_OLD_TRACE (verbatim 10-09 trace) | CRM | 3 | **3/3** | 3/3 | 3/3 |
| A_OLD_TRACE | MRK | 3 | **3/3** | 3/3 | 3/3 |
| B_NEW_TRACE (same, new line + block) | CRM | 3 | **3/3** | 3/3 | 3/3 |
| B_NEW_TRACE | MRK | 3 | **3/3** | 3/3 | 3/3 |
| S1 OLD_CLEAR | XLE pullback | 5 | **5/5** | 5/5 | 5/5 |
| S1 OLD_CLEAR | XLI squeeze | 5 | **5/5** | 5/5 | 5/5 |
| S2 NEW_CLEAR | XLE pullback | 5 | **5/5** | 5/5 | 5/5 |
| S2 NEW_CLEAR | XLI squeeze | 5 | **5/5** | 5/5 | 5/5 |
| S3 OLD_UPCOMING | XLE pullback | 5 | **5/5** | 5/5 | 5/5 |
| S3 OLD_UPCOMING | XLI squeeze | 5 | **5/5** | 5/5 | 5/5 |
| S4 NEW_UPCOMING | XLE pullback | 5 | **5/5** | 5/5 | 5/5 |
| S4 NEW_UPCOMING | XLI squeeze | 5 | **5/5** | 5/5 | 5/5 |
| S5 NEW_UNVERIFIED | XLE pullback | 5 | **5/5** | 5/5 | 5/5 |
| S5 NEW_UNVERIFIED | XLI squeeze | 5 | **5/5** | 3/5 | 5/5 |
| S6 NEW_LOCKOUT (control) | XLE pullback | 3 | **0/3** | 0/3 | 3/3 |
| S6 NEW_LOCKOUT (control) | XLI squeeze | 3 | **0/3** | 0/3 | 3/3 |
| S7 OLD_MACRO_FAILED | XLE pullback | 5 | **5/5** | 5/5 | 5/5 |
| S8 NEW_UNKNOWN | XLE pullback | 5 | **5/5** | 5/5 | 5/5 |

- **Totals.** 78 calls: 12 e2e evaluator calls and 66 direct replays. Schema problems: **0 of 78**.
- **Mean latency.**
  - Old arms: 5.2–6.7 s.
  - New arms: 5.0–8.5 s. S5 is the slowest, at 7.4–8.5 s.
  - Trace arms: 9.5–10.7 s (longer prompts).

## Rejection reasons

**S6 NEW_LOCKOUT, the must-reject control: 6/6 rejected.** Every reason cites the active lockout, and every thesis says the setup otherwise passes. Four distinct phrasings:

- "Macro lockout is active for the FOMC Statement scheduled at 2026-10-28 18:00 UTC."
- "Macro lockout active for the FOMC Statement at 2026-10-28 18:00 UTC."
- "Macro lockout active for the FOMC Statement scheduled at 2026-10-28 18:00 UTC."
- "Macro lockout is active for the FOMC Statement scheduled at 2026-10-28 18:00 UTC. New trading is prohibited from 60 minutes before through 30 minutes after the event."

**Every other arm: no rejections in 72/72 calls.**

**S5 NEW_UNVERIFIED: no veto (10/10 approved).** The model repeated the UNVERIFIED status in every thesis and approved anyway, because the status does not say LOCKOUT ACTIVE. In 2 of 5 XLI replies it set its own `macro_clearance` to false while approving. The evaluator overwrites that value, so it has no effect on the card, and the card carries the deterministic `macro_note`. Representative theses:

- XLE, e2e: "… Calendar verification failed, but no active macro lockout was reported."
- XLI, `macro_clearance` false: "Approved on strategy and risk gates: … Calendar clearance is unverified because the static calendar expired and the live feed failed, but no active macro lockout was reported."
- XLI, `macro_clearance` false: "… Macro timing is unverified rather than under an active lockout."

**S7 OLD_MACRO_FAILED: no veto (5/5).** The model never noticed that the macro lines were missing. Representative thesis:

> "Approved: bullish EMA alignment supports the long trend, while the 4h RSI recovery near the 20 EMA confirms the pullback setup. The stop at the recent swing low is more than 1.5 ATR from entry, the recent swing high provides a 2.04:1 reward-to-risk ratio, projected notional is within the portfolio limit, volatility is normal, and macro clearance is confirmed."

**S8 NEW_UNKNOWN: no veto (5/5), and the UNKNOWN wording was never cited.** No thesis mentions the UNKNOWN stress, the 0.50× multiplier or the missing inputs. The theses read like S2's. The deterministic sizing already applied the 0.5× multiplier (73 shares), and breakout suppression is enforced before the LLM. Representative thesis:

> "Trend-pullback setup is valid: price is near the rising 20 EMA, EMA alignment is bullish, and 4h RSI recovered after dipping to 38.6. The stop at the recent swing low is 1.69 points away, exceeding the 1.58-point minimum, while the recent swing-high target provides a 2.04:1 reward-to-risk ratio. Projected notional remains within the portfolio limit, VIX is normal, and the macro lockout is clear."

**Old against new, in the CLEAR and UPCOMING cases:** approval is identical (S1 = S2 = S3 = S4 = 10/10; A_OLD_TRACE = B_NEW_TRACE = 6/6). Under both prompts the theses end "…and the macro lockout is clear." The new "per static tier-1 calendar … live cross-check" provenance wording added no hesitation. The FOMC 23 hours ahead in S3/S4 drew no comment that blocked approval.

## Cost and volume

- 78 real LLM calls: 12 through the real evaluator end to end and 66 direct replays.
- litellm `response_cost` sums to **$1.09**, or $0.0107–$0.0229 per call.
- All calls ran between 09:11 and 09:16 New York time.
- The first replay attempt (26 jobs) failed locally with `UnsupportedParamsError` before any request was sent; it is not counted.
- Zero-cost LangSmith read: one HTTP 400, then a successful read.

## What this verifies / does not verify

**Verifies:**

- The branch's gate prompt is assembled by the real evaluator from the real renderers, and differs from main's only in the system line, the calendar block and, under a macro failure, the regime lines. All of these are quoted above.
- With the new prompt, the model approves verified-clear and upcoming-FOMC candidates exactly as with the old one: 10/10 against 10/10 per scenario pair, and 6/6 against 6/6 on the real October traces.
- The new UNVERIFIED wording, under the shipped `open` policy, caused **no LLM veto** in 10/10 calls. The model reads it as "not a lockout", as the system line instructs.
- The new UNKNOWN regime wording caused **no LLM veto** in 5/5 calls.
- The LOCKOUT ACTIVE block is still obeyed: 6/6 rejections. The real branch path refuses that instant deterministically, before the LLM, with `rejection_rule=macro_lockout`.
- Replies parse, and the evaluator's `approved`, `llm_verdict` and `macro_clearance` handling and the branch's `macro_note` attachment work end to end.

**Does not verify:**

- **No production trace of the new prompt exists before deploy.** LangSmith holds nothing after 2026-10-09T14:38Z, because the monthly ingest quota is exhausted. Neither the four days before the deploy nor the first post-deploy scans can be confirmed from traces until the quota resets. Missing traces are not evidence.
- **Nondeterminism.** `gpt-5.6` is a reasoning model, and production does not send `temperature=0.2`: `drop_params` removes it, so sampling is at the model default. N is 3–5 per arm.
  - For S5, 0 vetoes in 10 bounds the veto rate below about 26%, the one-sided 95% Clopper–Pearson limit.
  - For S8, 0 in 5 bounds it below about 45%.
  - For S6, 6 of 6 rejections bounds the rejection rate above about 61%.
  - A rare veto on UNVERIFIED or UNKNOWN wording, or a rare lockout approval, cannot be excluded at these N.
- **Only two synthetic native candidates and two real ones**, all LONG, on one regime level (LOW) and a single upcoming-event pattern (FOMC 23 hours ahead). The following were not exercised:
  - shorts, futures, catalog or probe cards;
  - several simultaneous events, live-only or disagreeing events;
  - an `invalid` (digest-mismatch) static calendar, or `macro_calendar_unknown: closed`;
  - a `MODERATE` or `HIGH` measured stress.
- **S5's `expired` status was produced by lapsing `valid_through`**, not by the calendar ageing out. The shipped file is valid through 2027-12-03.
- **The S6 control** only checks that the prompt does not over-approve. In production the deterministic gate refuses that case first.
- **The S8 regime lines are new information the model ignores.** The branch's 0.5× sizing and breakout suppression are deterministic, so the LLM is not relied on for them. This evidence cannot show that the LLM "understands" UNKNOWN.
- **Minor rendering note, not exercised by any scenario that will occur after 2026-10-21.** Before the static calendar's `valid_from`, the prompt says "expired", because `StaticTier1Calendar.state()` maps not-yet-valid to `expired`. The readiness detail distinguishes the two cases ("not valid until …").
