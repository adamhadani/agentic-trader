# Regime and recurrence as measured context — design

A measurement-only follow-up to the 2026-10-13 live-sandbox review ([roadmap, desk direction
item 1](../../alpha-roadmap.md#desk-direction-after-the-fixed-set-lane-october-8)). Two hypotheses
came out of that review and neither is measurable from the journal today: *the card funnel's edge
appears only when the index is trending* and *names recur across sessions and the repeats are
informative*. This PR records what is missing and attributes both in `cards outcomes` beside the
LLM gate, market exposure and news. Nothing here is a gate, a rule, a sizing input or a signal.
Designed 2026-10-14 under the operator's standing trust to take design decisions; decisions and
the measured facts behind them are in §7.

## 1. Why

- On 2026-10-13 the 45-day report read: sent cards +0.24R mean after cost against a SPY move worth
  +0.21R over the same windows (excess +0.04R); on 2026-10-07 the same report read −0.39R. The
  difference is the tape, but the journal records no regime, so "edge only in a trending tape"
  cannot be attributed over the whole funnel the way `llm_gate` or `news_context` can.
- The live journal already carries one market feature per candidate (`shadow.features.spy_above_200`
  from the setup feature vector), and it was `1.0` on all 68 candidates that have it: the whole
  record sits above the 200-session mean, so that feature cannot split the sample. A shorter trend
  measure and the desk's own volatility regime (VIX bucket, risk multiplier) are what is missing.
- Of the 39 distinct `(contract, strategy, direction)` setups journaled since 2026-09-24, 20 were
  journaled in more than one session (PFE five times; TALO, CVX, SEIC, PRGO four). The card-evidence
  doc lists "counting distinct setups per session" as a precondition before `card_policy` moves to
  `enforce`, and nothing counts it yet.
- Both hypotheses are cheap to measure, and measuring them first is the agreed order of work: the
  review's recommendation was a measurement PR, then one predeclared bracket counterfactual, then
  more taps in a trending tape.

## 2. Scope

**In this PR:**

1. The scan journals the regime it already fetched: one `regime` block per `scan_candidates_ranked`
   event and in every card's `decision_provenance`.
2. `cards outcomes` derives the tape at each decision from SPY daily bars (one extra fetch per
   report), the regime from the journal, and the recurrence of each setup from the other rows in the
   report, and adds `regime_*`, `spy_*`, `tape_reason` and `prior_*` columns plus two descriptive
   summary blocks, `regime_context` and `recurrence`.
3. Documentation: card-evidence section, production operations line, roadmap status, CLAUDE.md;
   this spec and the plan.

**Out of scope (named so they are not implied):** any gate, rule, sizing or ranking input derived
from regime or recurrence (nothing in `agentic_trader/risk`, `execution/card_policy.py`,
`research/setups/card_stats.py`, admission or `run_scan`'s ordering reads them); any change to the
regime detector, its thresholds or its macro engine; a change to the setup feature vector
(`features.py` is frozen for the setup study); a new `EventKind`, schema or migration; a card line
(the card already carries the regime summary and the evidence block); the bracket counterfactual
(the next PR, as its own frozen entry); a de-duplication of repeated setups in `card_stats` (this
PR counts them; changing the floor's denominator is a separate decision).

## 3. Components

### 3.1 Regime journal block — `agentic_trader/agent/regime.py`, `agent/copilot.py`

`regime.py` gains one pure function:

```python
REGIME_BLOCK_KEYS = (
    "vix",
    "vix_regime",
    "breakout_allowed",
    "risk_multiplier",
    "min_rr_threshold",
    "macro_stress",
    "observed_at",
)


def regime_journal_block(snapshot: Any) -> dict[str, Any]:
    """JSON-safe record of the regime a scan observed; every key present, missing values None."""
```

- `vix`, `risk_multiplier`, `min_rr_threshold`: `finite_or_none(float(...))` of the attribute, `None`
  when absent or non-numeric.
- `vix_regime`: `str(snapshot.vix_regime.value)` when it has `.value`, else `str(...)`, else `None`.
- `breakout_allowed`: `bool(...)` when present, else `None`.
- `macro_stress`: `snapshot.macro_report.stress.level.value` when every link exists, else `None`.
- `observed_at`: `snapshot.timestamp.isoformat()` when it is a `datetime`, else `None`.
- Never raises: any attribute access error yields `None` for that key. The scan fixture stubs the
  detector with a `SimpleNamespace(summary_text="test")`, so a snapshot with no fields must produce
  an all-`None` block, not an exception.

`run_scan` already holds `regime` (the detector's cached snapshot at the start of the scan). Right
after fetching it, the scan computes `regime_block = regime_journal_block(regime)` once, passes
`regime_block=regime_block` to `_journal_scan_ranking`, which writes `payload["regime"] = regime_block`
at the top level of the event (scan-level, like `trigger` and `budget`; not per candidate), and adds
`"regime": regime_block` to every `decision_provenance` recorded in the send loop (native, drift and
catalog cards alike; the block is scan-level data). The notification arguments are unchanged (they
already carry `regime_summary`).

### 3.2 Tape at decision — `agentic_trader/research/setups/tape.py` (new, pure)

```python
TAPE_HISTORY_DAYS = 400          # calendar days of SPY daily bars fetched before the earliest decision
RET_SESSIONS = 20
MEAN_SESSIONS = 200
TAPE_BUCKETS = ("ret20_up", "ret20_down", "above_200", "below_200", "unknown")

@dataclass(frozen=True)
class TapeState:
    ret_20: float | None        # close[-1] / close[-21] - 1 over completed sessions before the decision's session
    above_200: bool | None      # close[-1] > mean(close[-200:])
    sessions: int               # completed sessions available before the decision's session

def tape_state(daily: pd.DataFrame | None, session: date) -> TapeState
def tape_buckets(state: TapeState) -> tuple[str, ...]
```

- `daily` is a frame with a `Close` column indexed by timestamp (any zone); a row belongs to the
  session of its New York date. `tape_state` keeps rows whose New York date is strictly before
  `session` (today's bar, partial or complete, is never used: the decision is at 10:35 or 14:35 and
  the live feature vector uses the last completed session the same way), drops rows without a finite close, sorts them, and computes
  `ret_20` when at least 21 closes exist and `above_200` when at least 200 exist; otherwise `None`.
  An empty or `None` frame gives `TapeState(None, None, 0)`. Non-finite results are `None`.
- `tape_buckets` returns the bucket names a state belongs to: `ret20_up` when `ret_20 > 0`,
  `ret20_down` when `ret_20 <= 0`, `above_200`/`below_200` by the boolean, and `("unknown",)` when
  both measures are `None`. A state with one measure present and one absent belongs only to the
  present measure's bucket (never `unknown`).

### 3.3 Recurrence — `agentic_trader/research/setups/outcomes.py` (pure, over the report's own rows)

```python
RESOLVED_HITS = (BracketHit.TARGET.value, BracketHit.STOP.value, BracketHit.TIMEOUT.value)
PRIOR_OUTCOMES = (*RESOLVED_HITS, "none")


def with_recurrence(frame: pd.DataFrame) -> pd.DataFrame:
    """Adds prior_sessions, prior_same_session, prior_outcome by (contract, strategy, direction)."""
```

For each row, over the other rows of the same `(contract, strategy, direction)` in the frame:

- `prior_sessions` (int): distinct `session` values strictly earlier than the row's `session`.
- `prior_same_session` (int): rows with the same `session` and an earlier `decided_at`
  (ties on `decided_at` count as not earlier).
- `prior_outcome` (str): the `hit` of the most recent earlier row (by `decided_at`) whose `hit` is in
  `RESOLVED_HITS` and whose `exit_time` is not null and `<= ` this row's `decided_at`; `"none"` when
  there is no such row. A prior row that was still open at this decision is not information the
  desk had, so it never counts (no lookahead). `fetch_failed` and `immature` rows never count.
- Rows without a strategy or direction get `prior_sessions = 0`, `prior_same_session = 0`,
  `prior_outcome = "none"`.

The columns are relative to the report's window (`--days`): a setup first journaled before the
window reads as first. The doc says so.

### 3.4 Outcomes columns and summary — `research/setups/outcomes.py`

`_candidate_entries` copies the event's top-level `regime` (a dict, else `None`) onto every candidate
entry as `"regime"`. `label_journaled` gains `tape_symbol: str | None = MARKET_PROXY_SYMBOL` and
fetches, once, `bars.fetch_bars(tape_symbol, "1d", market_start - timedelta(days=TAPE_HISTORY_DAYS), now, adjustment="raw")`
through the same pacer; a failure stores `tape_reason = f"{type(exc).__name__}: {exc}"` on every row and
leaves the tape columns `None`. The daily fetch happens after the hourly fetches, and never when
`tape_symbol` is `None` or there are no candidates.

New columns, inserted after `news_alignment` and before `market_r`, in this order:

| Column | Source | Value |
| --- | --- | --- |
| `regime_vix` | journal `regime.vix` | float or `None` (events before this PR: `None`) |
| `regime_vix_regime` | journal `regime.vix_regime` | `COMPRESSED`/`NORMAL`/`ELEVATED`/`EXTREME` or `None` |
| `regime_risk_multiplier` | journal `regime.risk_multiplier` | float or `None` |
| `regime_macro_stress` | journal `regime.macro_stress` | `LOW`/`MODERATE`/`HIGH`/`EXTREME` or `None` |
| `spy_ret_20` | `tape_state(daily, session).ret_20` | float or `None` |
| `spy_above_200` | `tape_state(daily, session).above_200` | bool or `None` |
| `tape_reason` | the daily fetch failure | str or `None` |
| `prior_sessions` | `with_recurrence` | int |
| `prior_same_session` | `with_recurrence` | int |
| `prior_outcome` | `with_recurrence` | `target`/`stop`/`timeout`/`none` |

`with_recurrence` runs inside `label_journaled` after the frame is built, so every caller (the CLI,
`card_stats`' labeller, tests) sees the same columns. `card_stats` ignores them (it reads `hit`,
`r_cost`, `holding_sessions`, `strategy`, `direction` only; a test pins that its snapshot is
unchanged by the new columns).

`summarize` gains two blocks. A bucket is `{"n", "mean_r_cost", "mean_excess_r", "hit_rate"}` over
mature rows (`mean_excess_r` over the bucket's rows with a market control; `None` when none):

```python
"regime_context": {
    "blocks": int,                                    # rows with a journaled vix_regime (mature or not)
    "by_vix_regime": {regime: bucket},                # mature rows with a block, by regime_vix_regime
    "by_macro_stress": {level: bucket},               # mature rows with a block and a level
    "by_tape": {name: bucket for name in TAPE_BUCKETS},  # mature rows; a row counts in each bucket it belongs to
    "tape_reason": str | None,
},
"recurrence": {
    "setups": int,                                    # distinct (contract, strategy, direction) in the frame
    "repeated_setups": int,                           # setups journaled in more than one session
    "same_session_repeats": int,                      # rows with prior_same_session > 0
    "by_prior": {"first": bucket, "repeat": bucket},  # mature rows; prior_sessions == 0 versus > 0
    "by_prior_outcome": {name: bucket for name in PRIOR_OUTCOMES},
},
```

The empty-frame summary carries both blocks with zero counts and empty bucket maps. The existing
blocks are unchanged.

### 3.5 CLI — `cli/commands/cards.py`

No change: the summary is printed as JSON and the frame with every column. The new columns widen
the table; `decided_at`, `entry_time`, `exit_time` stay dropped as today.

## 4. Failure behaviour

- `regime_journal_block` never raises; a detector stub or a snapshot without macro data yields `None` fields.
  A journal write failure is already contained by `_journal_scan_ranking` (`scan_journal_error`).
- A SPY daily fetch failure sets `tape_reason` on every row and `regime_context.tape_reason`; the
  hourly labelling, `market_r` and every other block are unaffected. Too little history (a short
  frame) gives `None` measures and the `unknown` bucket, not an error.
- An event without a `regime` block (every event before this PR) gives `None` regime columns and is
  excluded from `by_vix_regime`/`by_macro_stress`, but still counts in `by_tape` and `recurrence`.
- A malformed `regime` value (not a dict) is treated as absent.

## 5. Testing

Unit tests with fakes only (the suite blocks network I/O):

- `tests/agent/test_regime_journal_block.py`: a full `RegimeSnapshot` with a macro report → every key
  with the right type and the ISO timestamp; a snapshot without a macro report → `macro_stress`
  `None`; a `SimpleNamespace(summary_text="test")` → every key `None`, no exception; a NaN VIX → `None`;
  the key set equals `REGIME_BLOCK_KEYS`.
- `tests/agent/test_scan_shadow_ranker.py`: the journaled payload has a top-level `regime` block with
  the `REGIME_BLOCK_KEYS` key set, and the sent card's `decision_provenance["regime"]` equals it; the
  per-candidate dicts carry no `regime` key.
- `tests/research/setups/test_tape.py`: `tape_state` on a built frame (sessions before/after the
  decision date, a same-day partial bar excluded, a 20-session return computed from 21 closes, 200
  for the mean), short history → `None`, empty/`None` → `(None, None, 0)`, NaN closes dropped before counting (an all-NaN frame → `(None, None, 0)`);
  `tape_buckets` for each branch including `unknown` and the one-measure case.
- `tests/research/setups/test_outcomes.py`: regime columns from an event with/without a block; a
  non-dict block treated as absent; one SPY `1d` fetch with `adjustment="raw"` and the right start,
  made after the hourly fetches, not made with `tape_symbol=None`; `tape_reason` on failure with the
  other columns intact; recurrence on a crafted frame (two setups, three sessions, one prior still
  open at the later decision → `none`, one resolved → its hit, a same-session repeat, ties on
  `decided_at`); the summary blocks on that frame (bucket membership, `mean_excess_r`, the empty-frame
  shape); the column order and `_COLUMNS` membership.
- `tests/research/setups/test_card_stats.py`: the snapshot computed from a frame with the new columns
  equals the one computed before they existed (the columns are inert there).

## 6. Operations

No config, no restart-time behaviour, no readiness component. After deploy the next universe scan's
event carries `regime`; `copilot cards outcomes --days 30` shows the new columns and blocks. The
report makes one more provider request (SPY daily) per run. Reading: a `by_tape` or `by_vix_regime`
bucket that differs from the others is a hypothesis for the bracket-counterfactual entry, not a
rule; `same_session_repeats` and `repeated_setups` are the denominators the card-evidence doc's
"Repeated setups" limit asked for.

## 7. Design decisions

1. **Journal the desk's regime, derive the tape in the report.** The VIX bucket, breakout flag,
   risk multiplier and macro stress are what the desk observed and acted on; they cannot be rebuilt
   later from bars, so they are journaled. The tape (SPY 20-session return, 200-session mean) is a
   deterministic function of past closes, so the report computes it for every row including the
   pre-PR history, with no runtime fetch. Cost if wrong: one extra provider request per report.
2. **Scan-level block, not per candidate.** The regime is the same for every candidate of a scan;
   repeating it per row would bloat the event and invite divergence. The reader copies it onto
   each entry.
3. **Threshold-free buckets.** `ret20_up/down` by sign and `above/below_200` by the boolean, no tuned
   cut-offs, so the buckets cannot be fitted to the sample. The 20 and 200 session lengths are the
   setup feature vector's own (`mom_60`'s sibling horizons and `spy_above_200`), not new parameters.
4. **`spy_above_200` recomputed rather than read from the shadow block.** Seven of 75 journaled
   candidates have no shadow block, and the report should not depend on the shadow ranker having
   run; the live value is in-sample uninformative anyway (all `1.0`).
5. **Recurrence without lookahead.** `prior_outcome` only uses a prior row whose bracket had
   resolved by the later decision time. A still-open prior card is `none`.
6. **No de-duplication of `card_stats`.** Counting repeats is measurement; changing the policy
   floor's denominator changes what `enforce` would withhold and is an operator decision after
   reading `same_session_repeats` (which is 0 in the current journal) and `repeated_setups`.
7. **Daily SPY raw, 400 calendar days.** Raw matches the report's hourly convention; dividends move
   a 200-session mean by well under a percent. 400 calendar days yields about 275 sessions before
   the earliest decision, enough for the 200-session mean with margin.
8. **No card line.** The card already shows the regime summary; recurrence on a card would be a
   nudge toward or away from a tap before its information content is measured.
