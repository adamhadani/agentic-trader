# Paper auto-entry for native and catalog suggestion cards

Review item 1.4 (CS-R4, CS-R5, CS-R6); design `docs/superpowers/specs/2026-10-27-paper-auto-entry-design.md`.
Operator decision D9 (2026-10-23), confirmed for this item on 2026-10-27. It builds on the
session-bounded entries and `/cancel` of 1.3 ([operator entry cancel](production.md#operator-entry-cancel)).

Entry was tap-only, so the book stayed nearly empty (3 accepted entries in about 24 sessions, holistic
review 2026-10-21): book-aware sizing, aggregate stop risk and the correlation caps never bound, and the
desk collected about one execution record a week. Labels say what a card would have done; only fills say
what it did. On the **Alpaca paper account** every native suggestion card is now entered through the exact
path a tap uses, with every risk rule still applied at admission. The operator keeps a veto. Nothing here
changes risk rules, admission, `card_policy`, book sizing or the card budget.

**Catalog sources (2026-11-14).** The same switch also enters the PEAD probe and insider watch cards, when
`execution.auto_entry_catalog` lists them ([catalog sources](#catalog-sources)). Operator decision O3
(non-native sources share at most 5 of the 8 slots) shipped with it ([non-native cap](#non-native-cap)).

## Switch and scope

- `execution.auto_entry: off | paper` (code default `off`; `config/config.yaml` ships `paper`), read by
  `load_config`. `auto_entry_active(config)` is true only when it is `paper`, `execution_mode` is `alpaca`,
  `alpaca_paper` is true and `execution.native_entry_lifetime` is `session`. Under the simulator
  (`EXECUTION_MODE=paper`) it is inactive and nothing errors.
- `load_config` refuses to start when `auto_entry` is `paper` and the account is Alpaca live, or
  `native_entry_lifetime` is `off` (an auto entry must never rest as an untimed GTC order). A live deploy sets
  `auto_entry: off` explicitly.
- The sweep re-checks `is_paper_scope(workflows.scope)` immediately before the entry call; a non-paper scope
  refuses with reason `not_paper_scope` and journals it.
- A native card is auto-eligible only when it was **sent** by a `SUGGESTION_SCAN` (not the swing or intraday
  journal-only jobs, not an operator `/scan`), is native (not a catalog card: no PEAD drift, no insider watch
  card, no `alpha_policy`/`alpha_version`), carries the 1.3 lifetime marker
  (`decision_provenance["entry_lifetime"]`), **the LLM gate approved it** (its `llm_verdict` is present, with
  `applied` and `approved` exactly `true` and `error` null), and `auto_entry_active` held at record time. It
  records `decision_provenance["auto_entry"] = {"mode": "paper"}`, which the card renderer, notices, digest and
  `cards outcomes` read. A re-priced card never carries it: it is an ordinary tap card. Registry alpha cards and
  futures stay tap-only, and so does a catalog card whose source `auto_entry_catalog` does not list. A native
  card no LLM approved is never auto-entered: a daemon started with `copilot service daemon --no-llm` scans
  with deterministic approvals and no verdict, so its native cards are
  ordinary tap cards (Execute and Dismiss, no auto line), and an LLM error withholds the native card altogether
  (`llm_unavailable`, [production](production.md#llm-gate-outage)).

## Catalog sources

`execution.auto_entry_catalog` (code default `[]`; `config/config.yaml` ships `["pead", "insider_watch"]` since the
2026-11-14 operator approval) lists the catalog card sources the same sweep enters. The ids are `pead` (the PEAD
paper probe) and `insider_watch`; any other id fails validation, and a non-empty list needs `execution.auto_entry:
paper` (`load_config` refuses otherwise, and a live Alpaca account already refuses `auto_entry`). Remove an id, or
set `[]`, and do the [controlled restart](production.md#controlled-maintenance-and-restart) to return a source to
tap-only.

- **Marker.** A catalog card of a listed source, recorded by a scheduled suggestion scan while `auto_entry_active`
  holds, gets the same `decision_provenance["auto_entry"] = {"mode": "paper"}` marker. It needs **no LLM approval**:
  its LLM pass is commentary only and its evidence is the frozen rule, so a commentary failure does not block it.
  It carries no native lifetime marker either: its lifetime is its `alpha_policy`'s, read through
  `entry_lifetime_of`, and a card whose stored policy resolves none is sent as a tap card. The shipped PEAD
  probe policy and the frozen insider-watch contract policy both resolve one session of resting entry and a
  15:45 New York time exit (pinned by a shipped-config test).
- **Unchanged.** The sweep, the 1,800 s abandonment, the re-card, the notices and the digest are the native path.
  The tap gates and admission still run in full, including `probe_block_reason` (the sticky −4R kill) for PEAD,
  the frozen-contract check for watch, the per-source caps and the risk caps ($100 for watch). Registry alpha
  cards, operator `/scan`, swing and intraday journal-only scans and futures are not eligible.
- **Card text.** The auto-entry line renders on a marked catalog card. The watch card's policy clause reads
  "auto-entered on paper; unvalidated; earns no promotion credit" while its source is listed, and "tap-only,
  never auto-entered; earns no promotion credit" when it is not, or when the sweep refused or abandoned the card
  (it is a tap card again beside its refusal line). Both are pinned by tests.
- **Labels.** `entry_trigger: auto` marks the entries; the watch block of `cards outcomes` and the weekly
  scorecard count auto entries apart from taps.

## Non-native cap

`portfolio.max_non_native_positions` (code default `None`, off; shipped `5`; 1 up to
`max_concurrent_positions`) is operator decision O3 (approved 2026-10-30, implemented 2026-11-14). Non-native means
a catalog or watch strategy (`pead_long`, `insider_watch_long`), a registry probe, or an `alpha_version`; the
predicate equals `_release_exit_scope != native`. Under the alpha lock, admission refuses such an entry with
`non_native_capacity` when the scope's open non-native positions plus in-flight non-native submissions reach the
cap: a business refusal like the per-source caps, so no halt. Catalog sources read the same combined count as a
ceiling and never card when full; an event crowded out stays in its source's decision journal and keeps its
counterfactual label. Natives keep at least 3 of the 8 slots. Set it to `null` and restart to remove the cap.

## The entry

`TradingCopilot.auto_enter_pending(now)` is a sweep over the database, not a hand-off from `run_scan`: it
selects the current New York day's marked cards that are still `PENDING` with no `auto_entry_attempted` event,
in rank order, so a scan that raises after recording its cards loses nothing. It runs in the scheduled
suggestion job's `finally` (outside the scan lock) and from an every-minute interval job that also runs at
daemon start; both are registered only when `auto_entry_active`. One `asyncio.Lock` serializes calls. `PENDING`
selection and the `signal_id` dedup in `enqueue_entry` make it idempotent.

- **Too late.** A card recorded more than `execution.card_freshness.fresh_seconds` (1,800 s) before the sweep
  is not attempted: it is journaled `abandoned` (`not_attempted_in_time`) and re-carded as a tap card.
- **How.** Each card gets one call to `execute_signal_by_id(signal_id, quantity=None,
  trigger=EntryTrigger.AUTO)`, the only entry point that applies card-freshness assessment. `EntryTrigger`
  (`tap` default, `auto`) is journaled on `card_tap_assessed` and on `entry_queued` and stored on the entry
  work item payload (`entry_trigger`), so an auto entry is never counted as a tap. Size is the card's default
  tier, already scaled by book sizing. There is no second entry path and no retry of any kind.
- **Outcome is read from the signal after the call**, not from the reply:

  | Signal status after the call | Outcome | Follow-up |
  |---|---|---|
  | `SUBMITTING` or `EXECUTED` | `entered` | the card's buttons are swapped in place |
  | `PENDING` (queue-time or retryable refusal) | `refused` | the `auto-refused` re-card |
  | `FAILED` (synchronous preflight or broker rejection) | `refused`, reason `rejected at submission` | none; the immediate `entry/{work_id}/rejected` notice stays |
  | `EXPIRED` (`MISSED`, `EXPIRED`, or `REPRICE` with its replacement card) | `refused`, `expiry_kind` `expired` or `repriced` | the expiry strike and one "not placed" notice |
  | not `PENDING` before the call, or an entry item the sweep did not create | `skipped_not_pending` | none |

  A rejection after `entered` (the FIFO head was another entry) keeps its rejection notice; the digest and
  `cards outcomes` read the final signal and work item state, not the attempt event. An exception after the
  entry was queued still takes its outcome from state.
- **Journal.** One `auto_entry_attempted` event per selected card on `card/{signal_id}`, key
  `auto-entry/{signal_id}`: `signal_id`, `scan_id` (null: the card provenance carries none; join through the
  scan journal's signal ids), `outcome` (`entered | refused | skipped_not_pending | abandoned`), `reason`,
  `work_status`, `signal_status`, `attempted_at`, `expiry_kind` (`expired | repriced`, or null). `reason` is
  the desk-authored reply with markup stripped, leading label and trailing period dropped, cut to 300
  characters; an exception's type name; the fixed `rejected at submission` for a `FAILED` signal (the reply
  embeds broker or exception text, which the journal and digest never carry); for an `EXPIRED` signal the
  reason of the sweep's own applied `card_tap_assessed` event cleaned the same way (`card expired` without
  one), or `re-priced`. A reply that interpolates an exception reads as a fixed phrase: `Account risk
  unavailable: …` is `account risk unavailable`, `Admission evidence unavailable (…)` is `admission evidence
  unavailable`, and the failure view of a rejected entry (`Execution Failed …`) is `rejected at submission`.
  Never exception text.
- **Isolation.** One card's exception is journaled and does not stop the others or the digest;
  `auto_enter_pending` never raises into the job or startup. The attempted ids are also kept per process, so a
  failed journal write cannot cause a second attempt in the same process.

## The card

The card is queued in `record_signal`'s transaction (payload `auto_entry: "placing"`) and delivered by the
outbox, almost always while the signal is still `PENDING`. Its line is the same before and after placement:
"Paper auto-entry: entered for you on the paper account; no tap needed. Unfilled entry is cancelled at the
session close." Only the buttons depend on state:

A placing card omits the separate "Unfilled entry is cancelled at the session close." validity bullet, because
its auto line already says it; refused and abandoned re-cards, and any card rendered as a tap card, keep it.

| State at delivery | Line | Buttons |
|---|---|---|
| `placing`, `PENDING` | the auto line | **Dismiss** (a veto before placement) |
| `placing`, `SUBMITTING` or `EXECUTED` (late delivery) | the auto line | **Cancel entry...** |
| re-card (`refused` or `abandoned`), `PENDING` | "Auto-entry refused: {reason}. Tap Execute to enter it yourself." or "Auto-entry was not placed in time. Tap Execute to enter it yourself." | the normal Execute and Dismiss |
| any other status | exactly as a tap card | as today (none, or Re-evaluate) |

- **Swap in place.** An `entered` outcome queues, in the transaction that journals it, a
  `CARD_AUTO_ENTERED` notification keyed `signal/{id}/auto-entered`, payload `{"signal_id", "contract"}`. Its
  delivery replaces the card's keyboard with **Cancel entry...** through `edit_message_reply_markup`, as
  `strike_expired_card` does: the message id is read at delivery; a card never delivered is acknowledged
  without calling Telegram (its late delivery renders from the status); the same `BadRequest` cases are
  acknowledged; any other failure is retried by the outbox. Expiry strikes only `PENDING` cards, so the two
  edits cannot race.
- **Cancel entry...** has callback `cancelentry_ask:{signal_id}` and runs the `/cancel` preview with its
  **Cancel entry** and **Keep** buttons. No one-tap cancel. `/cancel {id}` works throughout.
- **Veto window.** The Dismiss-before-placement window is short, from seconds up to about one minute, because
  the sweep runs right after the scan and every minute. Most vetoes will therefore be cancels of resting
  entries (Cancel entry... or `/cancel`).
- **Not every refusal gets a fresh card.** The follow-up commits in the same transaction as the journal event:
  - a refusal that leaves the card `PENDING` sends a fresh card: a `SIGNAL` notification keyed
    `signal/{id}/auto-refused` carrying the original payload plus `auto_entry: "refused" | "abandoned"` and
    `auto_refusal`, the reason queued **raw** and escaped once by the renderer. The re-card's line shows only
    while the card is `PENDING`;
  - an expired, missed or re-priced card (`EXPIRED`) gets the card-expiry sweep's own `CARD_EXPIRED` strike
    (key `signal/{id}/expired`, payload `signal_id`, `contract`, `reevaluable` decided as that sweep decides
    it) and a `MESSAGE` keyed `signal/{id}/auto-not-placed`: "Auto-entry #{id} {symbol} not placed:
    {reason}." with the attempt's reason, or "… not placed: re-priced; review card #{new_id}." The original
    card's message id is unchanged on this path, so the strike reaches the card that said "no tap needed";
  - a `FAILED` card keeps the existing `entry/{work_id}/rejected` notice;
  - `skipped_not_pending` sends nothing.
- The watch card's "tap-only, never auto-entered" line stays true for a source `auto_entry_catalog` does not list
  and for a refused or abandoned card, and is pinned by a test.

## Notices and the digest

The 2026-09-16 preference stands: every confirmed trailing-stop change keeps its own notice. Exits, ratchets,
halts, `UNKNOWN` outcomes, partial fills, missed bounds, `filled_before_cancel` and every refusal keep their
immediate notices. For an entry whose work item carries `entry_trigger: auto`:

- **New fill notice.** When fill sync first records the fill (`executed_at` goes from unset to set), a
  `MESSAGE` keyed `signal/{id}/auto-filled` is queued in the same transaction: "Auto-entry #{id} {symbol}
  filled {qty} @ ${price}; stop ${stop}, target ${target}." Tap entries get none.
- **Three routine notices are folded into the digest** (not queued; their journal events are unchanged): the
  resolution notice `entry/{work_id}/accepted`, the deadline-cause cancel intent `entry-cancel/{id}/intent`,
  and the deadline-cause cancel result confirmed unfilled. An operator-cause cancel keeps both notices. A
  notice that resolves an `UNKNOWN` outcome is **never** folded (its halt was announced), and neither is one the
  digest can no longer report: once the digest of the entry's New York date (the signal's creation date) is
  queued, or for an entry of an earlier date, the notice is queued as usual. The fold and fill-notice decisions
  read the trigger tolerantly: an unknown stored value reads as a tap (never folded, no fill notice) with a
  warning naming its type, so a display decision never fails the fact transaction it sits in.
  `signal/{id}/entry-terminal` is **kept**: for an auto entry it fires only when the broker itself ends the
  order (rejected, expired, external cancel).
- **Daily digest.** One `MESSAGE` keyed `auto-entry-digest/{et_date}` at the regular session close + 15
  minutes (early closes from the lifetime service's calendar; 16:15 New York on a normal day), sent by a
  5-minute interval job on its first run at or after that time. One line per marked card in scan then rank
  order, read from the journal, the signals table and the entry/cancel work items only: `filled {qty} @
  ${price} (open|closed)`, `cancelled unfilled at the close`, `not yet resolved (entry or cancel pending)`,
  `refused: {reason}` (`refused: expired ({reason})` or `refused: re-priced` for a card the sweep's tap check
  expired), `dismissed before placement`, `cancelled by you`, `not placed in time`; the last line
  is "Paper fills are optimistic: no queue position or market impact." A day with no marked cards queues and
  journals nothing; the key makes it once per date.

## Measurement

`cards outcomes` ([card evidence](card-evidence.md#operator-outcomes)) is descriptive and never a gate:

- Every card row has `entry_trigger` (`auto | tap | none`) and `operator_action`.
- `summarize_execution` counts `taps` and `auto_entries` apart; `mean_tap_age_seconds` is taps only. The
  `insider_watch` block splits its `taps` (a tap-triggered entry) from `auto_entries` the same way
  ([watch outcomes](card-evidence.md#insider-watch-outcomes)); the PEAD probe's forward record never read the
  trigger and is unchanged.
- The [weekly scorecard](card-evidence.md#weekly-scorecard) reports each lane's auto and tap entries, fills and
  closes every Saturday. A catalog auto entry earns no promotion credit: the PEAD probe's shadow, holdout and
  kill rules and the watch lane's "unvalidated" label are unchanged.
- The `operator` block holds counts by slot, trigger and strategy, tap latency (taps only), `tap_era` and
  `kept_vs_vetoed`.

**Predeclared rule (Gilliland, forecast value added).** Once `kept_vs_vetoed` is sufficient, human selection is
kept as a gate only if the kept-minus-vetoed 90% CI is above zero. Kept is mean beta-matched excess R (the D3
unit, one row per setup) of auto-marked cards that were auto-entered and not cancelled by the operator; vetoed
is the same for cards dismissed before placement or cancelled by the operator; the interval is a session-block
bootstrap. Cards risk or admission refused (`auto_refused`, including later rejections) and `abandoned` cards
are on neither side. `status` is `insufficient` below 30 distinct resolved setups in total or below 10 on
either side. The count starts at the deploy (only auto-marked cards). Tap-era cards keep a separate
descriptive tapped-versus-untapped line: before auto-entry "untapped" mostly meant the operator was not
looking. The rule is recorded here, not enforced in code.

**Pass criteria after 20 sessions**, checked by the operator, not code: at least one `book_sizing` factor
below 1; per-rule admission refusals visible; at least 20 entries with entry and exit slippage; labelled R
reconciled with realized R card by card.

## Visibility and bundled follow-ups

- `/status` shows "Auto-entry: paper (on)", "Auto-entry: off" or "Auto-entry: inactive (not an Alpaca paper
  account)". `scripts/verify_runtime.py` prints `auto_entry: {setting, active}`.
- `verify_runtime.py` makes up to 3 attempts in total on a 503 from `/readyz` (2 retries, 2 s apart; a GET),
  then fails with a `RuntimeError` naming each not-ready check and its detail; other HTTP errors raise.
- `insider_capture` readiness is ready (`not_due; no capture yet`) while the worker is `waiting` or in a scan
  window with no capture and no pass has run in this process; once a pass has run and produced none it stays
  not ready until a capture exists (removes the 2026-10-24 first-deploy alert).
- `/cancel` replies "never entered ({status}); there is nothing to cancel." for a dismissed or expired card and
  "still being submitted; try again in a minute." for an entry not yet at the broker.
- The lifetime service remembers per process each signal whose missed-bound verdict is final (either way), so
  its order read and calendar range run once per signal; a restart re-checks each once.

## Out of scope

Auto-entry for registry alpha cards, futures or any live account (PEAD drift and insider watch are in scope
only through `auto_entry_catalog`, above); a runtime on/off command
(the switch is config plus a controlled restart; `/panic` still halts all new risk); batching ratchet notices;
an operator resolve path for an `UNKNOWN` cancel; any change to risk rules, admission, `card_policy`, book
sizing or the card budget.
