# Card evidence and the card policy

Desk-direction item 1, PR A ([roadmap](alpha-roadmap.md#desk-direction-after-the-fixed-set-lane-october-8),
[design](superpowers/specs/2026-10-08-honest-cards-design.md)). Every native suggestion card states its
own measured record, and one operator-configured switch, `card_policy`, can stop sending setups whose
measured expected value is negative. Nothing here claims or creates edge: it measures, displays and,
only when the operator turns it on, withholds. `card_policy` is not a risk rule
([risk policy](risk-policy.md#what-is-not-a-risk-rule)); it never touches the book, admission, the
FIFO or the broker.

## Configuration

Top-level `card_policy:` (`CardPolicyConfig`; unknown keys fail the load):

| Key | Default | Meaning |
| --- | --- | --- |
| `mode` | `"off"` | `off`: the policy is not evaluated (the evidence block is still rendered); `preview`: evaluate and journal `would_withhold`, still send; `enforce`: withhold |
| `min_measured_ev` | `null` | Threshold on the posterior expected excess R in `net_excess_r_v1`; required when `mode` is not `off` (the key keeps its name) |
| `min_mature_cards` | `20` | Mature labels a `(strategy, timeframe, direction)` needs before the policy may withhold; also the card's "insufficient evidence" floor |
| `stats_window_days` | `90` (shipped `180`) | Labeller window, in New York calendar days |
| `stats_time_et` | `"08:30"` | Daily due time of the statistics worker (HH:MM New York) |
| `stats_poll_seconds` | `300` | Worker poll interval |
| `stats_max_age_seconds` | `345600` | Evidence older than this is `stale` on cards and in `card_stats` readiness (four days covers weekends and holidays) |
| `validity` | `"session_close"` | Card validity; see [Card validity](#card-validity) |
| `prior_path` | `config/research/card-stats-prior-v1.json` | The frozen replay prior ([provenance](card-stats-prior-2026-10-28.md)); a relative path resolves from the workspace root |
| `prior_sha256` | `null` | SHA-256 of the prior file; the worker refuses a missing or mismatched digest |

Validation requires `stats_max_age_seconds` to exceed `stats_poll_seconds`.

The code default is `off`. `config/config.yaml` ships `mode: "preview"`, `min_measured_ev: 0.0`,
`stats_window_days: 180`, the prior path and its pinned digest since 2026-10-28. Under `preview` cards are
still sent and `would_withhold` is journaled. `enforce` stays the operator's switch: no validator refuses
it, but a policy that is read every day should wait for an always-valid confidence sequence over the
posterior (spec section 5), because daily peeking at a fixed threshold inflates false withholds.

Quote `"off"` in YAML. YAML 1.1 reads a bare `off` as `false`; the loader maps `false` back to `"off"`
and rejects `on`/`true`.

## Card statistics snapshot

The daemon task `run_card_stats_worker` (readiness component `card_stats`) polls every
`stats_poll_seconds`. When the New York time is at or after `stats_time_et`, outside every
suggestion-scan window (each `scheduler.suggestion_scan_times_et` slot from 5 minutes before to 20
minutes after), today's key `card_stats/net_excess_r_v1/{et_date}` is missing (a v1 `card_stats/{et_date}`
event does not count), and no scan holds the scan lock (`TradingCopilot.scan_running`: suggestion, swing,
intraday or operator scans, including a late misfire-grace run), it reads every `scan_candidates_ranked`
event of the last `stats_window_days` ET dates (`research/setups/sources.scan_ranked_events`), builds the
population, labels it in a worker thread (`label_journaled`: 20 sessions, SPY proxy, beta and per-name cost
from SIP daily bars) and appends one `card_stats_snapshot` event to stream `card_stats` (idempotent;
retention keeps it). The scan checks apply when a run starts; a scan that begins during a labelling run
shares the provider with it. The worker runs every calendar day without a trading-day check.

Population (`card_population`, `agentic_trader/research/setups/card_stats.py`):

- only `scope == "universe"` events with `trigger == "suggestion_scan"` (swing, intraday and operator scans
  are journal-only or unpriced for cards); contracts in `scan.broad_index_etfs` are never carded and so never counted;
- malformed candidates (no LONG/SHORT direction, a non-finite entry or stop, or `entry == stop`) are dropped
  before labelling and counted as `n_malformed` under their key (a missing part as `?`): displayed, never partial;
- one row per (contract, strategy, timeframe, direction, New York decision date), the earliest decision kept
  (10:35 beats 14:35), before labelling, so the duplicate costs no bar request.

Maturity: a row is eligible only when at least `max_hold_sessions + 1` (21) exchange sessions lie strictly
after its decision date and strictly before the snapshot's New York date (`complete_sessions_between`:
weekdays that are not `MarketHolidayCalendar` holidays). Younger rows are `n_young` and enter no statistic,
which removes censoring exactly.

Payload (`CardStatsSnapshot`):

- provenance: `computed_at`, `window_start`, `window_end` (ET dates), `feed`, `cost_bps_per_side` (the 5 bp
  cost floor), `max_hold_sessions`, `labeller_protocol` (`setup-outcomes-v1`), `code_revision`,
  `events_considered` (suggestion-scan universe events read), `rows_labelled`, `unit` (`net_excess_r_v1`),
  `prior_sha256`, `prior_window_start`, `prior_window_end`, `prior_feed`, `cohort_end` (the latest eligible
  decision session) and `n_malformed`. A v1 payload still validates (all of these default);
- `keys`: one row per (`strategy`, `timeframe`, `direction`): the union of the live keys, the prior's keys and
  the malformed keys, plus the aggregate `("*", "*", "*")`, which has no prior or posterior. Counts: `n_mature`
  (eligible with a finite `net_excess_r`), `n_young`, `n_unresolved` (eligible but still `immature`),
  `n_immature` (= young + unresolved), `n_fetch_failed` (eligible, hourly or daily: a `fetch_failed` hit, or a
  `beta_reason` or `cost_reason` starting `fetch_failed`), `n_no_beta` (eligible, resolved, no net excess, not a
  fetch failure) and `n_malformed`. Rates, `mean_r_cost`, holding and decided-at are over the `n_mature` rows;
  `live_mean` and `live_se` are the mean and standard error of `net_excess_r` (`live_se` is null below two rows);
  `prior_*` and `posterior_*` come from the frozen prior (`prior_source` is `replay` for a prior key and
  `skeptical` otherwise: zero mean at the pooled spread and tau; `prior_se` is `sqrt(tau² + se_session²)`,
  `prior_weight` the pseudo-count `k`); `mean_name_cost_bps` is the mean per-name cost. Every number is finite or
  null; rows without a strategy, timeframe or direction count only in the aggregate.

Failures: a prior that cannot be loaded (unset or wrong digest, unreadable or invalid file) raises
`CardStatsUnavailable("card statistics prior unavailable: PriorUnavailable")`; so do an SPY hourly read that failed
for every row, and every row's beta read failing (the SPY daily read or every symbol's). Nothing is written and the
previous snapshot stays authoritative until it ages out. A single symbol's failed daily read only makes its rows
`n_fetch_failed`; its cost never falls back to the floor silently.

Readiness: every successful poll (detail `not_due`, `scan_window`, `present`, `scan_busy` or
`recorded`) and the start of a labelling run (`labelling N scan events`) observe `card_stats` ready unless a
failure from the same New York date is held;
a failure observes it failed. The detail of `CardStatsUnavailable` is its message (`every bar fetch failed for N candidates: TYPE, …` with up to three exception type
names, or one of the failures above), first 200 characters;
any other error's detail is its type name only, because readiness details reach Telegram incident
notices; messages go to the log. If every candidate's bar fetch fails, nothing is written
(`CardStatsUnavailable`) and the previous snapshot stays authoritative until it is older than
`stats_max_age_seconds`, when cards say "statistics stale". A failure retries on the next poll and is
remembered for the rest of its New York date: until a poll finds today's snapshot `present` or
`recorded`, the idle polls (`not_due`, `scan_window`, `scan_busy`) and a retry's `labelling` start
observe `card_stats` failed with the stored detail, so readiness does not flap between failed and ready
around the scan slots. The next New York date starts clean. Only shutdown ends the loop: if the
readiness write itself fails (the database is down) the worker logs it and polls again.

Shutdown cancels the worker's coroutine. The snapshot is written only by that coroutine after
labelling returns, so a cancelled run records nothing and the next start recomputes. The labelling
thread itself cannot be interrupted: it finishes its provider reads, and process exit waits for it
(asyncio's default-executor join). The labeller paces its own reads at
`market_data.max_requests_per_minute`, apart from the scan's read pool, which is why it avoids the
scan windows and running scans.

## Evidence block

`TradingCopilot.run_scan` reads the newest `card_stats` snapshot in this scope once per scan
(`CardStatsRepository.latest`) and looks up each native candidate's `(strategy, timeframe, direction)`
(`lookup`). The result, `CardEvidence`, is stored in the signal's `decision_provenance["card_evidence"]`
and in its outbox notification (with the key's `n_fetch_failed` and `n_unresolved`, 0 without a row, its
`first_decided_at`, the posterior and the prior), so the Telegram card, the terminal card, the dry-run print
and a re-priced replacement render the same facts from one helper (`format_evidence_lines`). PEAD drift
cards keep their own block and carry none. The block sits inside the card, right after the target line.

The unit is `net_excess_r_v1`: the journaled bracket's R, less the round-trip per-name cost
(`2·c/1e4·fill/risk_unit`, `c` at least 5 bp per side and growing with the name's volatility and the
order's share of its dollar volume), less beta times the matched SPY return (a past-only 120-session
beta), clipped to ±10R. The live record is the mean of that unit over matured, de-duplicated
suggestion-scan candidates; it is shrunk toward the frozen replay prior of its
`(strategy, timeframe, direction)` (`posterior = (n·x̄ + k·prior) / (n + k)`, `k = sd² / (tau² +
se_session²)`), so with no mature live rows the card states the prior alone, and a key the replay never
covered gets a skeptical prior at zero.

`lookup` returns `unavailable` for a snapshot whose `unit` is not `net_excess_r_v1`: a v1 snapshot read on
deploy day renders "no statistics in this scope" and never withholds (`CardStatsSnapshot.snapshot_key` names
a snapshot without a unit by the v1 key `card_stats/{date}`).

| Status | When | Lines |
| --- | --- | --- |
| `measured` | snapshot fresh, at least `min_mature_cards` mature labels, rates and a finite posterior | `• Expected excess (STRATEGY TIMEFRAME, DIRECTION): ±P.PPR ± S.SS vs beta-matched SPY after cost`, then `  replay 2021–26: ±X.XXR over N setups · live: ±Y.YYR over N mature since FIRST_DECIDED (T% target / S% stop / O% timeout)` |
| `insufficient` | fewer mature labels (the prior and posterior are still shown), or no row for the key (the `("*", "*", "*")` aggregate is never a fallback) | the same two lines with `live: N/MIN mature, not yet decisive`; with no row, `• Measured record: insufficient evidence (0/MIN mature candidates)` |
| `stale` | snapshot older than `stats_max_age_seconds` | `• Measured record: statistics stale (last computed DATE)` |
| `unavailable` | no snapshot in this scope (every dry scan), a failed read, an unreadable payload (including a non-finite number), a snapshot not in the v2 unit, or a key at or over the floor without rates or a finite posterior | `• Measured record: no statistics in this scope` |

A skeptical prior reads `no replay prior (shrunk toward 0)` in place of the replay part. When the key has
fetch failures or unresolved candidates the second line ends ` · K unreadable (never withheld)`.

Example, measured:

    • Expected excess (TREND_PULLBACK 4h, LONG): -0.06R ± 0.10 vs beta-matched SPY after cost
      replay 2021–26: -0.05R over 7,049 setups · live: -0.12R over 23 mature since 2026-09-24 (43% target / 52% stop / 4% timeout)

`FIRST_DECIDED` is the New York date of the key's first journaled decision (`first_decided_at`), or
`window_start` when the key has none: the count is of mature labelled candidates, sent or not, not of
cards. A stored payload is rendered by the same rule: `insufficient` only below the floor, and a
`measured` payload without a posterior or live mean renders the `unavailable` line. A queued notification
whose `card_evidence` predates the unit parses with `unit` null and renders the `unavailable` line.

The last line is always, in italics: "Not validated alpha. Replay prior (SIP 2021–26) and live record
(FEED) on journaled candidates' deterministic brackets; mature = 21+ sessions old; per-name cost ≥ 5
bp/side." Evidence without a prior window (no snapshot) carries a shorter caveat. The old "Implied EV at
R:R" line is gone: it mixed gross target and stop R with after-cost timeout R, and was computed from
censored rates.

Two properties of the snapshot the card reads: `events_considered` counts only suggestion-scan universe
events (not every journaled scan), and a journaled event whose `decided_at` does not parse is skipped,
not counted and not labelled.

The card title reads `📋 SETUP:` (it read `🚨 TRADE SIGNAL:`). `Macro Check` states the deterministic
lockout gate: a candidate that reached the LLM already passed it, so the evaluator sets
`macro_clearance` to true after the LLM answers; the LLM's own value is not displayed and the prompt is
unchanged. A notification queued before this change has no `card_evidence` and renders without a
block; a malformed one renders the `unavailable` line and still delivers.

## Send policy

`decide(policy, evidence)` (`agentic_trader/execution/card_policy.py`) is pure:

    would_withhold = mode != "off" and evidence.status == "measured"
                     and n_mature >= min_mature_cards
                     and n_fetch_failed == 0 and n_unresolved == 0
                     and posterior_mean < min_measured_ev
    withhold       = would_withhold and mode == "enforce"

`posterior_mean` is the expected excess R in `net_excess_r_v1`; `CardPolicyDecision.measured_ev` and the
journal key `measured_ev` carry it (the field names are unchanged).

It runs in `run_scan`'s send loop for native candidates only, after the card-budget refusals and before
the LLM. A withheld candidate gets the fixed outcome `card_policy_withheld`
(`RankedOutcome.CARD_POLICY_WITHHELD`), spends no scan, session or LLM budget, becomes a runner-up with
reason `card policy: expected excess -0.06R over 23 < +0.00R` (three decimals when two would print the value
and the threshold as equal, such as `-0.004R … < +0.000R`), and the next rank is considered, like an
LLM veto. Insufficient, stale, unavailable or partial (`n_fetch_failed > 0` or `n_unresolved > 0`) evidence never
withholds: the policy is a switch on complete measured evidence, not a fail-closed rule. Every scan applies it
(suggestion, `/scan`, Re-evaluate); the daemon's swing and intraday jobs are journal-only
(`ScanBudget.JOURNAL_ONLY`) and read neither the snapshot nor the policy. Every full-universe scan
(suggestion, swing including its run at daemon start, unrestricted `/scan`) is journaled and labelled
with its `trigger` (`suggestion_scan` | `swing_scan` | `operator_scan`); symbol- or timeframe-scoped
scans (intraday job, `/scan SYMBOL`, Re-evaluate) are not journaled, and all but the journal-only
intraday job show evidence and obey the policy. `cards outcomes`
reports a `trigger` column and a `triggers` summary block; events without a trigger report under `"unknown"`.
The `triggers` block counts scans from labelled rows only; the `supply` block
([below](#outcome-classes-and-scan-supply)) counts scans from the events, so scans that ranked nothing
appear there.

Outside the policy: PEAD drift and catalog cards (no evidence block), and policy-locked native cards
(a versioned alpha with `alpha_version` or `alpha_policy`, or a paper probe). A policy-locked card
still renders its evidence block, but `run_scan` makes no decision for it: it is never withheld or
counted as `would_withhold`, logs no preview line, and its journal and provenance `card_policy` block
has every field null. Whether such a card is sent belongs to its versioned policy and, for a probe, to
`probe_block_reason`, the single probe liveness rule.

Evidence recorded:

- `scan_candidates_ranked` gains top-level `card_policy: {mode, min_measured_ev, min_mature_cards,
  snapshot_key, unit}` and, per candidate, `card_policy: {measured_ev, n_mature, would_withhold,
  n_fetch_failed, unit}` (`unit` is `net_excess_r_v1`, and marks new blocks apart from old ones, which have
  none; `measured_ev`, the posterior, and `n_mature` are null unless the evidence is measured;
  `would_withhold` null while the mode is `off`; `n_fetch_failed` is the key's count, 0 without a row; every field, `unit` included, null for a
  policy-locked candidate). `would_withhold` is the policy's verdict on every other native candidate,
  whether or not it reached the send step.
- A sent card's `decision_provenance` carries the same `card_policy` block and its `card_evidence`.
- `preview` logs one `card_policy_would_withhold` line for each candidate that reaches the check.
- `copilot cards outcomes` adds the `card_policy_withheld` and `would_withhold` columns and the summary
  block `card_policy: {withheld, would_withhold, withheld_mean_r_cost, would_withhold_mean_r_cost}`
  (counts over every row, means over mature rows). Withheld candidates stay in the journal and keep
  being labelled, so a strategy below the threshold keeps accruing evidence and can recover. The
  existing summary blocks are unchanged; a withheld candidate is still a runner-up there. The
  end-of-session digest counts it among the runners-up; the reason shows in the scan summary and in a
  `/scan SYMBOL` reply.

Operate it in order: `off` until a snapshot exists; `preview` with a threshold, reading `would_withhold`
for several sessions; then `enforce`. Each change is a config edit and the controlled restart.

What `preview` at 0.0 will show. The replay prior for TREND_PULLBACK 4h LONG is near -0.05R, so once a
key's live floor (`min_mature_cards`) is met the posterior sits just below zero unless the live record is
positive enough to lift it, and most native cards will journal `would_withhold: true`. Under `preview`
every card is still sent. That journaled contrast tests whether the replay prior describes live, gated
cards; it is not a verdict on the strategies. `enforce` is a later operator decision, and with a prior this
close to the threshold it would withhold most native cards: read the preview first. Do not enforce on a
daily-peeking basis without the always-valid confidence sequence (spec section 5).

## Broad-index exclusion

An unrestricted scan (no `symbols`, any budget, a dry run included) never sends a native card on a broad US
equity index ETF. `scan.broad_index_etfs` lists them, 23 names by default: SPY, VOO, IVV, SPYM, RSP; VTI,
ITOT, SCHB, IWV; IWB, VV, SCHX, OEF; QQQ, QQQM, DIA; MDY, IJH, IWR, VO; IWM, IJR, VB (SPYM replaced SPLG,
which the 2026-10-15 Alpaca asset snapshot does not list). A native card on a segment benchmark is a
market-timing bet, and the desk's yardstick is SPY at beta 1, so such a card's excess return is zero by
construction or a known size or growth spread, never a setup-specific edge. Sector, style, factor, dividend,
thematic, international, bond and commodity ETFs stay eligible. An empty list turns the rule off.

This is a card-selection rule in `run_scan`, not an entry-risk rule: it lives outside
`agentic_trader/risk`, and admission never sees it. It is the first check in the send loop, before every
budget refusal, so the outcome never depends on how much budget is left. The candidate gets the fixed
outcome `broad_index_excluded` (`RankedOutcome.BROAD_INDEX_EXCLUDED`) and becomes a runner-up with reason
`broad index ETF: excluded from native cards (scan.broad_index_etfs)`. It spends no LLM, card or group
budget, is dropped from news fetching and scoring and from the book-sizing candidates, and the next rank is
considered. Such a name is still collected, ranked, given a dynamic slot when the screener supplies it, and
journaled in `scan_candidates_ranked`, so it is a runner-up refused by a selection rule and `cards outcomes`
keeps labelling it like any other. The end-of-session digest counts it among the runners-up but keeps it out
of the "top:" list (a listed index is scanned every session and would fill it) and reports them as
"N broad-index ETF(s) excluded".

Unaffected: a restricted scan (`/scan SPY`, `copilot scan --symbols SPY`) and Re-evaluate, which still card
these names so operator trades stay possible; PEAD drift and catalog cards; and policy-locked alpha and probe
cards. A journal-only scan never reaches the send loop, so its candidates keep the outcome `journal_only`.

## Card validity

`card_policy.validity` is `session_close` by default: a card is valid until the close of the session it
was issued in (today's rule). `next_session_close` extends only native, non-policy-locked equity cards
(no `alpha_version`/`alpha_policy`, no paper probe, no PEAD event), and only while
`execution.card_freshness.enabled`: such a card records the next trading day's regular close as
`valid_until` (`market/session.py:next_regular_close_after`, which skips weekends and holidays and keeps
early closes; an unavailable calendar keeps today's close). Every other card keeps today's rule:
admission refuses a policy-locked card's original signal after `execution.signal_max_age_seconds`, and
a drift card needs its same-session event.

- A tap while the session refuses entries and before `valid_until` is `WAITING`: retryable, the card
  stays `PENDING`, the tap is journaled `waiting` (`applied: false`), and the reply is "⏳ Market closed;
  card valid until Www DD HH:MM NY. Next regular open YYYY-MM-DD HH:MM UTC.". After `valid_until` the tap
  is `EXPIRED` as before.
- While `execution.card_freshness.fresh_seconds` is shorter than the overnight gap (about 17.5 h), a
  next-session tap is never fresh, so it re-prices or misses at the current price
  against the same stop and target and the required ratio; the re-pricing gate is unchanged, and the
  replacement's new timestamp satisfies admission's signal age.
- `run_scan` skips a native candidate whose `(contract, strategy)` already has a live `PENDING` or
  `SUBMITTING` card (outcome and runner-up reason `live card pending`; a `/scan SYMBOL` reply says "No new
  card for SYMBOL: a live card for this setup is pending."), so the 12-hour duplicate window
  cannot produce a second live card the next morning. The guard runs only under `next_session_close`,
  for a candidate whose card it extends, after the card-budget refusals and before the send policy.
- `Valid until:` shows `HH:MM NY` when the close is on the card's issue date (the signal row's
  timestamp) and `Www DD HH:MM NY` otherwise.
- Accepted limit: a carried card is a `PENDING` signal from an earlier session, so it does not count in
  the new session's card budget (`signals_since`); the live-card guard bounds it to one card per
  `(contract, strategy)`. Carried cards are likewise outside the correlation-group caps and the
  one-dynamic-card cap, which derive from the same `signals_since` rows. Measured statistics never include next-day entries: labels enter at the first
  hourly bar after the scan.

- Switching `validity` back to `session_close` while carried cards exist: the sweep keeps them `PENDING`
  until their day-2 close, and the live-card guard turns off, so a day-2 scan may issue a second live
  card for the same setup. An overnight tap now expires instead of `WAITING`. Nothing can be entered
  without tap re-assessment, so this is safe.

## Book-aware sizing

Code: `agentic_trader/risk/book_vol.py` (`book_vol_factor`), `research/setups/covariance.py`,
`execution/book_sizing.py` (`BookSizer`), consumed in `run_scan`. Config: `book_sizing` (below).

**Rule.** A card's size is limited so the book's daily dollar volatility after the card stays within
a budget: `budget = max_portfolio_daily_vol_pct x risk_capital` (default 0.8%/day). Risk capital is the
smaller of configured cash and observed account equity, as for the per-trade budget. The factor is
closed form: the largest `f` in `[0, 1]` such that the volatility of `book + f x card` is at most the
budget, under a Ledoit-Wolf shrunk covariance of daily close-to-close returns. It never raises size:
if the book plus the full card is within budget the factor is 1 (this includes a hedge that brings an
over-budget book back under), and a book already over budget gives a factor of 0 otherwise.

**Inputs.** The covariance uses completed sessions strictly before the scan's session date over
`lookback_sessions` (120, at least `min_observations` = 60 aligned returns per symbol), from raw
(unadjusted) Alpaca daily bars. The book is the signed dollar notional of the desk's EXECUTED
positions plus the cards already built earlier in the same scan (so card two sees card one), equities
only. A recorded card whose symbol the covariance cannot price makes the rest of the scan
`unavailable` (`unpriced_card`) rather than sized against a stale book. Bars for names in the scan
come from the scan's datasets when those frames are raw Alpaca bars; a candidate whose scan frame came
from the yfinance fallback (auto-adjusted) is fetched raw like a book name. Other book names are
fetched once per scan, off the event loop, with a 20 second bound.

**Modes.** `off`: nothing is computed. `preview` (code default): the factor is computed, journaled and
shown on the card; size is unchanged. `config/config.yaml` ships `enforce` since 2026-10-20 (preview
2026-10-09 → 2026-10-19: six journaled factors, all 1.00 against a one-share book). `enforce`: every size tier is scaled by the factor (quantities
floored to the minimum order size; a scaled default tier below the minimum is `blocked` and the
candidate is skipped with outcome `book_sizing_blocked`). Statuses: `applied` (enforce, factor < 1), `unchanged` (factor 1,
or preview), `blocked`, `unavailable` (covariance missing, invalid or timed out; never blocks and
never scales), `not_applicable` (mode off, non-equity, drift or catalog card, policy-locked card,
dry run).

**Card line**, under the evidence block in both renderers:

- `📐 Book: portfolio vol 0.62% → 0.81%/day with this card (budget 0.80%; size ×0.70)`; in preview a
  factor below 1 appends ` — preview, size unchanged`, and factor 1 reads `size ×1.00`.
- `📐 Book: portfolio vol unavailable (covariance: <reason>)`.
- `not_applicable` and `blocked` show no line.

**Records.** `decision_provenance["book_sizing"]` holds the full decision (status, mode, factor,
`would_scale`, `quantity_before`, `quantity_after`, `budget_pct`, `budget_dollars`, `vol_before_pct`,
`vol_after_full_pct`, `vol_after_scaled_pct`, `n_observations`, `shrinkage`, `book_symbols`,
`missing_symbols`, `reason`, `symbol`, `risk_capital`, `scaled_weight`, `shadow`). The notification arguments carry the same block. Each
`scan_candidates_ranked` candidate carries `book_sizing` = `{mode, status, factor, would_scale,
vol_before_pct, vol_after_full_pct, quantity_before, quantity_after}`; candidates not sized (unsent,
drift, blocked before the decision) are all null except `mode`, and events journaled before this
feature have no block.

**`cards outcomes`** gains a `book_sizing` block: `by_status` counts, `would_scale` (rows with
`would_scale` true or status `applied`), `mean_factor_scaled` over those rows, and `mean_r_cost` of
mature would-scale rows versus mature rows with a status that would not be scaled. It is descriptive
only, never a gate, and uses the same proxy bracket labels as the rest of the report.

**Limitations.**

- Raw bars, no corporate-action adjustment: a split in the window distorts a name's variance. The
  covariance is a risk estimate, not a forecast.
- Equities only; futures and crypto are `not_applicable` and absent from the book vector.
- A book position without usable bars (fewer than `min_observations`, a fetch failure, a non-raw
  frame) makes the decision `unavailable` for every card in the scan. It is never silently dropped from the book.
  A candidate-only name without bars is `unavailable` for that candidate alone (`candidate_missing`).
- A book already over budget blocks every non-hedging card under `enforce` (factor 0); only a card that
  brings the book back within budget passes.
- The LLM thesis is written against the pre-scale size under `enforce`; `quantity_before` records it.
- The shadow optimiser (`shadow_optimizer: true`) is a journaled cross-check of the closed form. It
  never reaches size or orders; a mismatch is logged as `book_sizing_shadow_mismatch`.
- The earlier "Repeated setups" caveat applies to the `book_sizing` block as it does to `card_policy`:
  repeated journaling of one setup inflates counts. Moving to `enforce` is an operator decision after
  reading the journaled factors.

## News context

News context ([news context](news-context.md)) is a fourth descriptive block in `cards outcomes`
(`news_context`, with `news_*` columns), attributed beside selection, the LLM gate and market exposure.
Only its alignment and tone buckets are mature-row-only (and leave out rows without a block); its
counts cover every row with a block (populations in [news context](news-context.md#attribution)). It
never feeds `card_policy` or `card_stats`.

## Regime and recurrence context

Spec: [regime and recurrence](superpowers/specs/2026-10-14-regime-recurrence-design.md). Two more
descriptive blocks in `cards outcomes`, measuring the two hypotheses from the 2026-10-13 review of the
live sandbox: that the funnel's edge appears only in a trending tape, and that repeated setups are
informative. Nothing here is read by `card_policy`, `card_stats`, any risk rule, admission or the
ranking; there is no card line.

**Journaled regime.** Every `scan_candidates_ranked` event carries one top-level `regime` block
(`agent/regime.py:regime_journal_block`; keys `REGIME_BLOCK_KEYS`): `vix`, `vix_regime`
(`COMPRESSED`/`NORMAL`/`ELEVATED`/`EXTREME`), `breakout_allowed`, `risk_multiplier`,
`min_rr_threshold`, `macro_stress` (`LOW`/`MODERATE`/`HIGH`/`EXTREME`, or `UNKNOWN` when a decision
input is missing; null when yield-curve enrichment is disabled) and `observed_at` — the snapshot
observed at the start of the scan (the evaluator may refresh it during a long scan), which cannot be
rebuilt from bars later. Since the Phase 0 fail-safes it also carries the raw inputs:

- `macro_enabled`: `macro_stress` is not null (false under `regime.yield_curve_enabled: false`);
- `inputs`: every input read, `{name: {value, source, observed_on, decision, error}}`. With the
  yield curve enabled that is all ten: the decision inputs `vix`, `3m`, `2y`, `10y`, `hy_oas`
  (`decision: true`) and the context inputs `5y`, `30y`, `dxy`, `t10yie`, `t5yie` (`decision: false`);
  VIX-only reads `vix`, `10y` and `dxy`. `source` is `yfinance:^VIX` or `fred:DGS2` style,
  `observed_on` the last bar's or observation's date, and `error` a type name, `no_observation` or
  `unexpected_payload`;
- `missing` (every missing input) and `missing_decision` (those with `decision: true`; with
  `macro_enabled` they are what forced `UNKNOWN`), in canonical order;
- `clock_regime`: `EDT` or `EST`, New York's clock at `observed_at`.

`UNKNOWN` sizes at `regime.unknown_risk_multiplier` (0.5) with breakouts per
`regime.unknown_breakout_allowed` (off), capped at `EXTREME`'s limits (0.25, `regime.extreme_min_rr`,
breakouts off) when the present VIX or HY OAS already forces `EXTREME`; `cards outcomes` buckets it
like any other `macro_stress` value. Every card's `decision_provenance["regime"]` carries the same block. Events before 2026-10-14
have none, and events before the Phase 0 fail-safes have only the first seven keys.

**Derived tape.** The report fetches SPY daily bars once (raw, 400 calendar days before the earliest
decision, after the hourly fetches) and computes, per decision session, from completed sessions
strictly before it (`research/setups/tape.py:tape_state`): `spy_ret_20` (the 20-session close return)
and `spy_above_200` (last close above the 200-session mean). Columns `regime_vix`,
`regime_vix_regime`, `regime_risk_multiplier`, `regime_macro_stress`, `spy_ret_20`, `spy_above_200`,
`tape_reason` sit after `news_alignment`. A failed daily fetch sets `tape_reason` on every row and leaves
the rest of the report intact; when the fetch returned bars but none precede a session, that row's
`tape_reason` reads "no daily bars before the session". A daily frame with a naive (tz-less) index is
refused (`unknown`): it cannot be placed on the New York calendar without guessing.

**Recurrence.** Over the rows of the report (`with_recurrence`), per `(contract, strategy,
direction)`: `prior_sessions` (distinct earlier sessions in the window), `prior_same_session`
(same-session rows decided strictly earlier) and `prior_outcome` (the hit — `target`/`stop`/`timeout` —
of the most recent earlier row whose exit bar had closed by this decision (exit time plus one hourly
bar), else `none`; a card still open at the decision never counts, so there is no lookahead). The counts are relative to `--days`: a
setup first journaled before the window reads as first. `first` = no earlier session in the window, so a
same-session repeat is still `first`; `repeat` = at least one earlier session.

**Summary blocks.** Buckets are `{n, mean_r_cost, mean_excess_r, hit_rate}` over mature rows
(`mean_excess_r` over the bucket's rows with a market control):

- `regime_context`: `blocks` (rows with a journaled `vix_regime`), `by_vix_regime`, `by_macro_stress`,
  `by_tape` (`ret20_up`/`ret20_down` by the sign of `spy_ret_20`, `above_200`/`below_200`, `unknown`
  when neither measure exists; a row counts in each bucket it belongs to), `tape_reason`.
- `recurrence`: `setups` (distinct setups in the window), `repeated_setups` (journaled in more than
  one session), `same_session_repeats` (rows with `prior_same_session > 0`), `by_prior`
  (`first`/`repeat`), `by_prior_outcome` (`target`/`stop`/`timeout`/`none`).

Reading: a bucket that differs from the others is a hypothesis for a predeclared study (the bracket
counterfactual is next), not a rule. The buckets are threshold-free by design so they cannot be fitted
to the sample; the 20- and 200-session horizons are the setup feature vector's own. On 2026-10-14 the
journal held 39 distinct setups since 2026-09-24, 20 of them journaled in more than one session, and the
feature vector's `spy_above_200` was `1.0` on every candidate that had it. The recurrence split is under a predeclared test on replayed history (`alpha setup-recurrence`, protocol `config/research/setup-recurrence-v1.json`, [spec](superpowers/specs/2026-10-14-setup-recurrence-design.md)); it ran on 2026-10-14 and did **not** fire: on 6,342 historical setups repeats trailed first occurrences by 0.067R (ci90 [−0.138, +0.005]) and repeats after a stop averaged −0.02R, so the live split was a small rising-tape sample ([result](setup-recurrence-2026-10-14.md)); no construction rule follows.

### Bracket counterfactual arms (descriptive)

`cards outcomes` also relabels every journaled candidate under four counterfactual exit arms on the same
hourly bars as the live bracket, so the live record keeps measuring what the frozen study tests:

- `A1_3r`: target 3R, no trail. `A2_2r_trail`: target 2R plus trail. `A3_3r_trail`: target 3R plus trail.
  `A4_trail_only`: no target, trail only. The stop is always the journaled stop.
- The trail is an hourly close-based ratchet: once a close reaches 1.5R of gain the stop follows 1.5R
  behind the best close, effective from the next bar. Trail exits count as stops. This approximates the
  live trailing policy; it is not the broker's tick-level ratchet.
- Columns: `target` and `atr_14` (the journaled bracket inputs) sit after `stop`; for each arm N,
  `cf_N_hit` (`target`/`stop`/`timeout`/`immature`), `cf_N_r_cost` (R after costs) and `cf_N_market_r`
  (SPY return over that arm's own holding window in R units). All twelve are null for a candidate without
  a `target`, malformed geometry or a failed bar fetch. They are present in the labelled frame (available to callers of `label_journaled`) but the CLI table does not print them, and the CLI JSON carries only the `counterfactual` summary.
- `counterfactual` summary: `caveat` and `arms.N` with `n_pairs` (mature rows where the arm also
  resolved), `target_rate`, `stop_rate`, `timeout_rate`, `mean_r_cost`, `mean_excess_r` (arm R minus its
  market R) and `advantage_vs_live_excess` (arm excess minus live excess, rows where all four exist).
  Each statistic is null when its population is empty.

Descriptive only: never a gate, and not read by `risk/`, `card_policy`, `card_stats` or admission. The
decisive test is the frozen study (`alpha bracket-counterfactual`,
[spec](superpowers/specs/2026-10-18-bracket-counterfactual-design.md)).

## Outcome classes and scan supply

`cards outcomes` splits the labelled candidates into three classes, in `counts`, `base_rates` and
`market_exposure` alike:

- `sent`: the outcome `sent`.
- `journal_only`: the outcome `journal_only` (`RankedOutcome.JOURNAL_ONLY`), every native candidate a
  swing scan ranked. It never competed for a card, so it is not a runner-up.
- `runner_up`: every other candidate, which competed for a card and lost: a free-text refusal such as
  `per-scan budget spent`, `llm_vetoed`, `card_policy_withheld`, and `broad_index_excluded` (refused by a
  selection rule like a budget refusal).

`counts` holds `mature`, `immature` and `fetch_failed` per class; `base_rates` and `market_exposure` hold
the mature statistics, null for an empty class. The empty report carries the same three keys. The split
changes nothing else: the labelled rows, `selection`, `triggers`, `card_stats` and `card_policy` read the
same candidates as before.

The `supply` block counts the scans themselves, per trigger (`suggestion_scan`, `swing_scan`,
`operator_scan`, or `unknown` for an event without one). It reads the `scan_candidates_ranked` events, not
the labelled rows, so a completed scan that ranked nothing and a gated scan are counted:

- `scans`, and `by_status`: `completed`, `macro_lockout` or `regime_unavailable`. An event without `status`
  counts as `completed`.
- `zero_candidate`: completed scans with no candidates.
- `sent`: candidates with the outcome `sent`, whatever their scan's status.
- `funnel`: each integer field of the scans' funnels summed, and each `{name: n}` field
  (`raw_setups_by_strategy`, `rejected_by_rule`, `errors`) summed per name. Gated scans carry no funnel and
  add nothing.
- `funnel_unknown`: completed events whose `funnel` is missing or null, which is every event journaled
  before the funnel existed. A gated event is not unknown: `by_status` counts it.

```json
"supply": {
  "suggestion_scan": {
    "scans": 3,
    "by_status": {"completed": 2, "macro_lockout": 1},
    "zero_candidate": 1,
    "sent": 1,
    "funnel": {"selected": 200, "scanned": 189, "raw_setups": 8, "admitted": 3,
               "rejected_by_rule": {"concurrent_positions": 3, "unclassified": 1}},
    "funnel_unknown": 0
  },
  "unknown": {"scans": 1, "by_status": {"completed": 1}, "zero_candidate": 0, "sent": 1,
              "funnel": {}, "funnel_unknown": 1}
}
```

(The funnel is abbreviated here; the report prints every field.) When no candidate was journaled in the
window the report prints `No journaled candidates in N day(s) (M scan events).`, so a window of
zero-candidate or gated scans reads differently from an empty journal. Descriptive only: not read by
`risk/`, `card_policy`, `card_stats` or admission.

### Beta-matched and buy-and-hold controls

Beside the SPY-matched `excess_r`, `cards outcomes` reports two further descriptive controls. Neither is a
gate, and nothing in `risk/`, `card_policy`, `card_stats` or admission reads them. The daemon's `card_stats`
labelling run skips the beta fetch entirely.

- **Beta-matched excess.** `beta` is a past-only trailing beta of the candidate against SPY: 120 sessions
  ending the session before the decision, on adjusted daily bars, with at least 100 paired returns. Rows
  with fewer paired returns, or a failed daily fetch, carry `beta_reason` (`short_history`,
  `fetch_failed: ...`) and no beta. `beta_market_r` is `beta` times `market_r`, and `beta_excess_r` is
  `r_cost` minus `beta_market_r`. Each counterfactual arm also reports `mean_beta_excess_r`
  (`r_cost - beta * arm_market_r`).
- **Buy-and-hold.** `hold_r` is the candidate's own buy-and-hold return over the bracket's full horizon: from
  the open of the entry bar to the close of the last regular bar of the `max_hold_sessions`-th session counted
  from the entry session (the bracket's own timeout exit), signed by direction, in the bracket's R units,
  uncosted. It is `None` until that session has closed and when bars are missing. A stop or target exit does
  not shorten it, so `timing_r` is `r_cost - hold_r`: the value the bracket's stop and target added over simply
  holding for the full horizon.
- **Net excess (`net_excess_r_v1`).** `net_excess_r` is the one cost-adjusted, beta-matched unit shared by
  the labeller and `card_stats`: `clip(clip(r - cost_r, ±10) - beta * market_r, ±10)` with
  `cost_r = 2 * c / 1e4 * fill / risk_unit`, `fill` the entry-bar open and `risk_unit` the planned
  `|entry - stop|`. The per-name cost `c` is a 5 bp floor plus a square-root impact term from the name's
  past 20-session volatility and median dollar volume for a $1,000-risk order, capped at 200 bp; with fewer
  than 15 past returns, no bars or a failed fetch it is the floor and `cost_reason` says why
  (`short_history`, `no_daily_bars`, `fetch_failed: ...`). Beta and cost inputs come from SIP adjusted daily
  bars in every caller (`build_daily_bar_source`); hourly labelling keeps the configured feed. Abdi-Ranaldo
  and Corwin-Schultz daily high-low spread estimators were rejected because on this liquid universe they are
  dominated by estimator error (spec §4.2). `beta_excess_r` keeps its unclipped flat-cost definition.
- **Summaries.** Each `market_exposure` class adds `n_beta`, `mean_beta`, `mean_beta_market_r`,
  `mean_beta_excess_r`, `mean_excess_r_same_rows` (the beta-one excess over exactly the rows that carry a
  `beta_excess_r`), `beta_reasons` (a count per missing-beta reason) and `mean_hold_r` / `mean_timing_r`. The
  hold and timing means, and `beta_reasons`, cover every mature row with a finite value, whether or not SPY
  bars exist for it; the other means cover the SPY-controlled rows (`n`).
- **`by_asset_class`.** Mature rows grouped by `etf_equity`, `rates_credit`, `metals`, `single_stock` (from
  the configured universe groups and sectors) or `unclassified`, with `n`, `mean_r_cost`, `mean_excess_r`
  and `mean_beta_excess_r`. A symbol outside the configured universe falls back to the research ETF cohorts (so a removed BIL or SHY is
  `rates_credit`); any other dynamic name is `unclassified`.

### Insider watch outcomes

`cards outcomes` appends an `insider_watch` block after the PEAD probe when the window holds
`insider_watch_decision` events ([watch cards](apriori-alphas.md#watch-cards)). It is descriptive and read-only:
nothing in `risk/`, `card_policy`, `card_stats` or admission reads it, and a watch card is unvalidated.

- **Events.** Every journaled event that carries a measured ATR (so it passed the frozen eligibility rule):
  those sent as cards and those eligible but unsent (owned, over the card or position cap, late, and so on).
  Ineligible and incomplete-filing events are journaled but not labelled. An event journaled by more than one
  decision scan (a rerun) counts once by its event ID, as the scan that sent it when one did.
- **Labels.** The frozen cube bracket (decision-bar close, 2 x ATR14 stop, 3R target, 20-session time exit, 15 bp
  decisive cost) walked on hourly bars with `label_bracket` from the 10:35 decision, the way the PEAD forward
  record is labelled, on SIP with adjusted prices (the feed the study, the card's +0.087R line and the PEAD
  record use; never the configured feed). Without the SIP subscription a read must end at least 15 minutes
  ago, so every SIP read of the PEAD and watch blocks ends 16 minutes before now, and an hourly bar that had
  not closed by then never matures a label. `n` counts mature rows only; the rest are in
  `unlabelled` by reason (`no_hourly_bars`, `no_decision_price`, `degenerate_levels`, `immature`).
- **Blocks.** `all`, `sent_events` and `unsent_events`, each with `n`, `mean_r_cost` and `mean_beta_excess_r`:
  `r_cost` minus the past-only trailing beta (the controls above) times SPY's return over the same holding
  window in R units. A row without a beta (`short_history`, a failed daily read) is left out of the excess mean.
- **Taps and fills.** From `insider_watch_long` signals in the window: `taps` counts signals that left the card
  (any status but pending, dismissed or expired) and `fills` those with an `executed_at`. `bar_failures`
  names symbols whose bars could not be read.
- **Reading it.** The sent-versus-unsent comparison is the only view of whether the discretionary tap helps or
  hurts; both are small, censored samples, so read them as a record, never as a gate or an edge estimate.

## LLM gate identity and outages

Every native candidate the LLM evaluated carries an `llm` block in its `scan_candidates_ranked` entry, and
a sent card's `decision_provenance["llm_verdict"]` is the same block. It has nine keys: `approved`,
`rejection_reason`, `stop_loss`, `take_profit`, `applied`, `model` (the provider's returned snapshot, null on
error), `prompt_sha256` (SHA-256 of the rendered system prompt plus the user template, without per-card
values; the hash is the prompt's identity), `error` (an exception type name such as `Timeout`, never its
text) and `malformed` (true for a veto whose reply was malformed). Rows journaled before this change was deployed
have only the first five.

An LLM error never approves. A native card is journaled with the fixed outcome `llm_unavailable`
(`RankedOutcome.LLM_UNAVAILABLE`, a runner-up class like `llm_vetoed`), records no signal and sends no card; its
block is `{"error": <TypeName>, "applied": true, "approved": false, "model": null, "prompt_sha256": ...}`. A
malformed veto is `llm_vetoed` with `malformed: true`; a well-formed veto without a reason reads "veto without a
reason". Only the JSON boolean `true` approves.

In `cards outcomes`, rows gain `llm_model`, `llm_prompt_sha256` and `llm_error`. An error row has `llm_ran`
false: it is excluded from approved/vetoed attribution and counted in `llm_gate.llm_errors`, which, like `ran`,
`vetoed` and `approved`, counts mature rows only (an immature or fetch-failed error row keeps its `llm_error`
column but is not counted), so the mature rows that reached the gate are `ran + llm_errors`. `llm_gate` also
carries `by_identity`, the same statistics (`ran`, `vetoed`, `approved`, mean R and bracket-edit fields)
grouped by `"{model}|{prompt_sha256}"`, with rows that predate identity under `"null|null"`, so a prompt or
model change is never averaged into the older sample. Both are descriptive, never a gate.

## Operator outcomes

`cards outcomes` records what was done with each sent card, now that native suggestion cards are entered on
the paper account by [auto-entry](paper-auto-entry.md). All of it is descriptive; nothing in `risk/`,
`card_policy` or admission reads it.

- **`entry_trigger`** per card row: `auto` (the sweep's entry), `tap` (an operator tap or the CLI) or `none`
  (no entry item). `summarize_execution` reports `taps` and `auto_entries` apart; `mean_tap_age_seconds`
  averages taps only.
- **`operator_action`** per card row, the first matching rule: `cancelled` (an operator-cause entry cancel; a
  deadline cancel is not an operator act), `auto_entered`, `auto_refused` (the sweep's attempt was `refused`,
  including a card its tap check expired or re-priced, or the auto entry was rejected without a fill), `tapped` (a tap-trigger entry exists, including a marked
  card the sweep refused or abandoned and the operator then tapped), `dismissed`, `abandoned` (the sweep never
  got to it), `expired`, else `none`. Cards recorded without the `auto_entry` marker use only `cancelled`,
  `tapped`, `dismissed`, `expired` and `none`.
- **`operator` block** of `summarize()`: counts of sent, tapped, auto-entered, dismissed, cancelled and
  expired cards `by_slot` (`10:35`, `14:35`), `by_trigger` and `by_strategy`; `tap_latency` (median, p90 and
  `n`, taps only); `kept_vs_vetoed`; and `tap_era`.
- **`kept_vs_vetoed`** reads auto-marked cards only, so its count starts at the deploy. `kept` is
  `auto_entered`, `vetoed` is `dismissed` (before placement) or `cancelled` by the operator; each side
  reports `n` and `mean_excess_r` (the beta-matched excess R, one row per resolved setup on a session), with
  `difference`, a session-block bootstrap `ci90` and `status`: `insufficient` below 30 distinct resolved
  setups in total or below 10 on either side (no interval then). `excluded` counts `auto_refused` and
  `abandoned` cards, which are on neither side.
- **`tap_era`** compares unmarked cards that were tapped with those that were not (`tapped`, `untapped`,
  `difference`). It is not part of the rule: before auto-entry "untapped" mostly meant the operator was not
  looking.

The predeclared reading, recorded in [paper auto-entry](paper-auto-entry.md#measurement) and not enforced in
code: once `kept_vs_vetoed` is sufficient, keep human selection as a gate only if the kept-minus-vetoed CI is
above zero.

## Limits

- **Proxy bracket.** Labels use the journaled deterministic COLLECT bracket (not the LLM-edited bracket
  that was sent), enter at the open of the first regular hourly bar at or after the scan's `decided_at`
  (not the limit price, not the tap), cost a fixed 5 bp per side, and use the configured feed (IEX in
  production, with its hourly gaps). The record is the setup's, not this card's own win rate.
- **Partial snapshot.** A key's `n_fetch_failed` counts candidates whose bars could not be read; they
  are excluded from its rates. Only a run in which every fetch fails writes nothing: a run with some
  failures is recorded as that New York date's snapshot, and because the key `card_stats/{et_date}` is
  idempotent it blocks a retry until the next date. Such a partial key is still shown as measured and
  journaled with its `n_fetch_failed`, but the policy never withholds on it (`preview` never counts it as
  `would_withhold`).
- **Censoring bias.** A label stays immature until the stop or target hits or 20 regular sessions pass,
  so early samples over-represent fast resolutions. The sample floor does not remove this.
- **Coverage.** Every eligible scan journals exactly one `scan_candidates_ranked` event (key
  `scan_candidates_ranked/{scan_id}`) with its `trigger`: suggestion, swing and unrestricted `/scan`
  alike. A scan is eligible when it has no symbols, no timeframe, a budget (not `NONE`), is not a dry
  run and passed the idle check. A zero-candidate scan journals (`candidates: []`), and so do a
  lockout-gated scan (`status: macro_lockout`) and a scan whose regime read failed. Halted,
  closed-session and idle scans never journal: they evaluated no slot, and a halt keeps its own skipped
  `pead_decision`, as a lockout-gated scan does beside its journaled event.
  Dry runs, intraday, `/scan SYMBOL`, Re-evaluate cards and an unrestricted `copilot scan --no-budget`
  (budget `NONE`; it sends cards) never journal either; they show evidence and obey the policy. A CLI
  `copilot scan` with `--strategy`, `--strategy-mode`, `--asset-class` or `--bypass-session-filter`
  (no `--symbols`, no `--timeframe`) still journals with `"scope": "universe"`. Each event carries:
  - `status`: `completed`, `macro_lockout` or `regime_unavailable`. A reader treats an event without
    `status` (one written before this field existed) as `completed`.
  - `gate`: null when completed; `{rule: "macro_lockout", reason}` with the rule's text; or
    `{rule: "regime_unavailable", missing, errors}` with input names and exception type names.
  - `calendar` on every status; `regime` null when the lockout gated the scan before the read;
    `scheduled_time_et` for a scheduled slot, null otherwise.
  - `funnel`: a completed scan's collect funnel, null when gated; an event without it has an unknown
    funnel. Contract counts: `selected`, `scanned`, `fetch_failed`, `insufficient`,
    `coverage_excluded`, `dynamic_excluded`. These do not partition `selected`: a contract that
    raises after scanning counts in both `scanned` and `errors`. Native setup counts: `raw_setups` (and
    `raw_setups_by_strategy`), `duplicates`, `account_risk_unavailable`, `rejected_by_rule` (by
    `RiskRule` id; `unclassified` when no shared rule refused: sizing, the bracket check or the
    statistical correlation check),
    `errors` (by exception type name, from the per-contract guard), `setups_lost_to_errors` (the
    setups a contract had iterated but not yet classified when its guard fired) and `admitted` (the
    ranked native count, so `len(candidates)`). They satisfy `raw_setups = duplicates +
    account_risk_unavailable + Σ rejected_by_rule + admitted + setups_lost_to_errors`. The funnel holds
    rule ids and type names only, never reason text; PEAD drift candidates are counted in their own
    `pead_decision`, not here.

  `card_stats`' `events_considered` counts the scan events it considered, zero-candidate and gated
  events included; it is not a count of scans with candidates. The labelled rows are unchanged, because
  an empty `candidates` list labels nothing. Cards sent by swing or operator scans before 2026-10-09
  (PR A.1) were never journaled and stay unlabelled. Of the 22 cards sent from 2026-09-24 to 2026-10-08,
  11 were never journaled and 7 more were journaled before the candidate's `signal_id` existed (#106,
  2026-10-05), so those 7 match their card only by contract and time.
- **Journal-only scans.** The daemon's swing job (`trigger=swing_scan`, including its run at daemon
  start) and, when enabled, the intraday job run with `ScanBudget.JOURNAL_ONLY` (`budget: "journal_only"`
  in the event). They collect and rank like any budgeted scan, with `use_llm=False`, then journal every
  native candidate with the fixed outcome `journal_only` (`RankedOutcome.JOURNAL_ONLY`). They read no
  shadow ranker, book, `card_stats` snapshot, card policy, news or card budget, and call no LLM, so a
  `journal_only` candidate has `signal_id`, `llm`, `news`, `shadow` and a decision-free `card_policy`
  and `book_sizing` all null, and was never sent or withheld. It is labelled like any other journaled
  candidate and `cards outcomes` reports it as its own `journal_only` class
  ([below](#outcome-classes-and-scan-supply)), but **swing candidates stay in the `card_stats` denominator
  until `card_stats` v2** separates them, as the repeated-setups note below says. No PEAD drift runs in
  such a scan.
  `ScanBudget.NONE` is unchanged: no limits, and it still sends (dry runs, `--no-budget`, Re-evaluate).
- **Repeated setups.** The same `(contract, strategy, direction)` setup can now be journaled several
  times per session (swing runs, each in-session daemon start, back-to-back scans). Each journaled
  candidate counts once in `n_mature`, so correlated repeats inflate the sample and the
  `min_mature_cards` floor. `cards outcomes` now counts them (`recurrence.same_session_repeats`, `repeated_setups`); de-duplicating the `card_stats` denominator is a separate operator decision before `card_policy` moves to `enforce`.
- **Scope.** Snapshots are per `{environment}/{execution_mode}`: simulator and Alpaca paper statistics
  never mix, and a dry scan (an empty temporary database) always says "no statistics in this scope".
