# Assistant handoff

Read [AGENTS.md](AGENTS.md), [development notes](docs/development-notes.md) and
[operations](docs/production.md) before changing runtime behavior. This file is a
starting map; the linked domain guides own detailed contracts.

## Running system and safe workflow

- The installed checkout owns one launchd **Alpaca paper** daemon, Telegram
  poller and a market-hours awake agent. Inspect `git status` and launchd registration first; preserve existing work.
- `EXECUTION_MODE=alpaca`, `ALPACA_PAPER=true` is brokerage paper trading.
  `EXECUTION_MODE=paper` is the simulator. Do not confuse their data or credentials.
- Use an isolated worktree for development, especially supervisor script changes:
  the watchdog executes `scripts/launchd.sh` every 60 seconds. Pause watchdog then
  daemon before updating the installed checkout. Do not start a second poller.
- Python 3.14 and `uv`. Run full affected tests, disposable PostgreSQL integration
  for persistence changes, and `uv run pre-commit run --all-files` before committing.
- Tests select explicit fixture config, temporary SQLite, stripped credentials and
  blocked Python/libcurl network I/O. PostgreSQL requires `--run-postgres` and a
  guarded `TEST_POSTGRES_URL` database named `test_*`. Never relax these guards.
- Inject explicit config/storage before constructing services. Bare `AppConfig()`
  has no runtime database. `load_config()` is the explicit environment boundary;
  it does not mutate `os.environ`. There is no PostgreSQL-to-SQLite failover.
- Dry scans use an empty temporary simulated portfolio with no broker mutations
  or Telegram. `test-alert` previews; explicit sending requires a separate bot/chat.
- Keep secrets, chat identifiers, raw logs, DBs and incident snapshots out of Git.
  Tests and deployed verification are separate evidence. A healthy PID is insufficient.

## Code map

| Boundary | Canonical modules |
| --- | --- |
| CLI composition and lifecycle | `cli/main.py`, `cli/utils.py`, `cli/commands/` |
| Application orchestration | `agent/copilot.py:TradingCopilot` |
| Signal evaluation and risk | `agent/evaluator.py`, `position_sizing.py`, `regime.py`, `macro.py`, `calendar.py` with `config/calendar/`, `risk/` |
| Market data and sessions | `data/`, `data/corporate_actions.py`, `market/session.py`, `market/bars.py`, `resilience/fallback.py` |
| Strategies | `screeners/base.py`, `strategies.py`, `registry.py`, `formulaic.py` |
| Entry/close application services | `execution/entries.py`, `admission.py`, `capacity.py`, `closing.py` |
| Broker adapters and transport | `broker/base.py`, `alpaca.py`, `paper.py`, `tradovate.py` |
| Durable workflows and accounting | `storage/workflow.py`, `storage/ledger.py`, `accounting/` |
| Operational incidents and maintenance | `diagnostics/incidents.py`, `monitor.py`, `probe.py`, `storage/operations.py`, `maintenance.py` |
| Telegram and rendering | `notifier/telegram_bot.py`, `transport.py`, `outbox.py`, `presentation/` |
| Diagnostics and telemetry | `diagnostics/readiness.py`, `doctor.py`, `telemetry/`, `runtime.py` |
| Research and chat | `backtest/`, `research/`, `options/`, `pairs/`, `agent/copilot_graph.py`, `copilot_tools.py` |

`TradingCopilot` still combines orchestration and construction; prefer injected
services and typed results when changing a boundary. Do not introduce compatibility
aliases for internal callers. Update callers to canonical names in the same change.
Reuse domain enums; operator policy belongs in validated config. Leave SDK wire
keys, frozen migrations, mathematical identities and explicit test examples intact.

## Contracts to preserve

1. **Entry authorization:** CLI/buttons call `EntryExecutionService`, atomically
   reserve risk and join the durable FIFO. Only a valid preflight token commits
   `submitting`; never expire or replay a broker submission. Unknown outcomes halt
   new risk and recover by exact client ID, including after a 404. Alpaca admission
   requires fresh journaled account drawdown; every tier shares its cap. Final
   submission pins the risk fingerprint and rechecks the lease after ledger locking. Rejected/unfilled
   terminal evidence releases capacity; acceptance is not a fill.
   Every entry-risk rule (per-trade budget, notional and class caps, concurrent positions,
   aggregate stop risk, correlation group, reward/risk, macro lockout, session, earnings,
   regime) has one implementation in `agentic_trader/risk`; the evaluator, tap gate,
   re-pricing and admission differ only in the book they pass and the observed budget inputs
   ([risk policy](docs/risk-policy.md); admission's Alpaca book is broker-marked).
   `macro_lockout` reads the verified union calendar: the static `config/calendar/us-tier1-vN.json`
   (digest-pinned by `risk.macro_calendar_sha256`) plus the live ForexFactory feed, so a feed outage
   never reads as an empty calendar. A static calendar that is expired, not yet valid or invalid is
   unverified: `risk.macro_calendar_unknown` `open` (default) labels it, `closed` refuses all entries.
   Book-aware sizing (`book_sizing`, code default `preview`, config ships `enforce` since 2026-10-20) scales native equity cards by a
   closed-form factor ≤ 1 against a daily dollar-vol budget at card construction only; the
   shadow optimiser cross-check never reaches size or orders.

   **Paper probes:** `probe` is a third registry list, valid only in the `…/alpaca:paper`
   scope. `probe_block_reason` is the single liveness rule for snapshot, sweep and
   admission. Probes are risk-capped, expiring, renewable only while unkilled (−4R
   from first enrolment, sticky per version), and earn no shadow, holdout or promotion
   credit. Never make `active` depend on the account being paper; leaving `probe`
   never touches the broker.
2. **Closure:** `PositionCloseService` persists exclusive intent, confirms exact
   order-group cancellation, revalidates position/session and submits once.
   `/flatten` previews by default and preserves halt state. Partial/ambiguous
   ownership remains blocked; exact full fills and conditional SQL govern closure.
   Direct replies and durable notices retain refusal reasons. Closed-session refusals
   show the broker's next regular open in UTC; a cancellation attempt, even with a
   lost acknowledgement, must warn that protection may have been removed.
3. **Broker evidence:** never match exits by symbol alone, infer a fill price from
   an order request, or persist a stop ratchet before exact replacement confirmation.
   Preserve original thesis/risk. POST/PATCH/DELETE are never automatically retried.
   Keep a notice for **every** confirmed trailing-stop change (operator preference,
   September 16). Frequent distinct ratchets are expected; correlate exact stop,
   outbox and Telegram request evidence before diagnosing duplicates.
4. **Reports:** CLI/Telegram positions share one broker snapshot. `/perf` separates
   reconciled account activities from tracked full-close statistics. Decimal cash,
   inventory, account binding and broker cost basis govern account totals; unknown,
   failed or stale evidence withholds realized P&L. Never infer partial signal ownership.
5. **Persistence:** schema head is `008_alpha_pipeline`; Alembic exclusively
   owns migrations. Financial/order/work/incident events and dead letters are retained.
   Only redundant old healthy observations may be compacted by the documented policy.
6. **Delivery:** critical business transitions and notification intents share a
   transaction. The outbox retries delivery only; Telegram is at least once.
   Rebuilds do not send messages. Never retry a trade handler to recover its reply.
7. **Async:** offload blocking SDK/provider/research work; check shared-state races
   before adding threads. Keep loop-lag, request/update correlation and poll metrics.
   Long Telegram handlers still serialize; do not enable blanket concurrent trading.
8. **Operations:** HTTP `/healthz` is liveness and `/readyz` passive freshness.
   `doctor` is active CLI diagnostics; `/healthcheck` is removed. External
   `doctor --monitor` persists debounced incidents and uses the existing outbox;
   it never polls Telegram or consumes entry work. Degraded readiness alerts; it
   does not blindly restart or automatically change the trading halt.
   Suggestion scans run at configured New York times and spend a derived per-session
   card budget (signals table, ET trading day); `PENDING` cards reserve no capacity;
   the intraday job scans only `non_universe_contracts`; `setup_quality` orders cards
   and is stored in provenance — it is not validated alpha. Every eligible scan (unrestricted,
   non-dry, with a budget, past the halt, session and idle checks) journals exactly one
   `scan_candidates_ranked` with its `ScanTrigger`, `status` (`ScanStatus`: `completed`,
   `macro_lockout`, `regime_unavailable`), `gate`, `calendar` and `funnel`, zero-candidate,
   lockout-gated and regime-unavailable scans included; halted, closed-session, idle, dry and
   restricted scans never do ([card evidence](docs/card-evidence.md#limits)). Shadow evidence stays
   suggestion-scan only. The daemon's swing job, and the intraday job (`scheduler.intraday_scan_enabled`,
   shipped `false`), run `ScanBudget.JOURNAL_ONLY`: they rank and journal
   (`RankedOutcome.JOURNAL_ONLY`) but call no LLM, record no signal and send no card, so a daemon
   start cannot spend a session slot. An unrestricted scan never sends a native card on a broad US
   equity index ETF (`scan.broad_index_etfs`, 23 names; `RankedOutcome.BROAD_INDEX_EXCLUDED`): a
   card-selection rule in `run_scan`, not a risk rule; the candidate is still ranked and journaled, and
   the digest counts it apart from its "top:" list. Never widen the intraday job to the universe or
   store the budget in a counter.
   A missing macro decision input is `MacroStressLevel.UNKNOWN` at `regime.unknown_risk_multiplier`
   (0.5×, breakouts off), never 1.0×; a missing VIX raises `RegimeUnavailable`, which skips the scan
   visibly and queues one `regime-unavailable/{et_date}` notice per New York date. The computed
   `macro_calendar` readiness check is ready iff the static calendar is `ok` with at least
   `risk.macro_calendar_renewal_days` (30) left before `valid_through`; publishing
   `us-tier1-vN+1.json` and pinning its digest before the check degrades is a standing operator duty
   ([runbook](docs/production.md#tier-1-calendar-renewal)).
   Every LLM-evaluated card records `llm_verdict` (applied for native cards, commentary for
   catalog probes) and `cards outcomes` decomposes selection, LLM gate, market exposure,
   fill slippage, news, the journaled regime with the derived SPY tape, and setup recurrence
   (`regime_context`, `recurrence`; [card evidence](docs/card-evidence.md#regime-and-recurrence-context));
   these are descriptive, never gates. Every journaled universe scan carries a top-level `regime` block
   (`regime_journal_block`), except a lockout-gated one (`regime: null`: it stops before the regime read),
   and every card from any scan carries it in `decision_provenance["regime"]`.
   `alpha setup-recurrence` is the predeclared, research-only test of the recurrence split (R1–R4 on
   replayed history; a pass only makes a construction rule eligible); v1 ran 2026-10-14 and did not fire (`no_change`: repeats −0.067R vs firsts on 2021–2026, [result](docs/setup-recurrence-2026-10-14.md)).
   `alpha bracket-counterfactual` is the predeclared, research-only exit-policy test (five bracket arms paired by setup on replayed history, rules B1–B5, one-shot confirmation read declared in `confirmation.json` before the read; a pass only makes a card-construction PR eligible), and `cards outcomes` reports the same arms descriptively under `counterfactual`, never as a gate. v1 ran 2026-10-18 and did not fire (`no_change`: no arm beats the live 2R bracket net of SPY; the live ratchet arm is worse by 0.013R; every arm trails SPY by 0.10–0.15R, [result](docs/bracket-counterfactual-2026-10-18.md)); the bracket is closed as an explanation and the next alpha work is on the entry side.
   The one gate derived from measured outcomes is `card_policy` ([card evidence](docs/card-evidence.md)):
   operator-configured, default `off`, `preview` before `enforce`, withholding native cards only on
   complete measured evidence above a sample floor (`card_policy_withheld`; partial, stale or missing
   evidence never withholds; alpha, probe and catalog cards are exempt), and never touching risk,
   admission or the broker.
9. **Language:** `/macro` owns combined volatility/macro context; no `/regime` alias.
   Render configured policy and real feed dates. GEX is a research estimate with
   quality notes, never fabricated spot data or measured dealer inventory.

Detailed contracts: [durable execution](docs/durable-execution.md),
[Alpaca integration](docs/alpaca-integration-review.md),
[account ledger](docs/account-ledger.md),
[operational monitoring](docs/operational-monitoring.md).

## Validation and useful commands

```bash
uv run pytest
TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_trader uv run pytest tests/integration --run-postgres
uv run pre-commit run --all-files
uv run copilot doctor --readiness
uv run copilot db queue
uv run copilot db outbox
uv run copilot db incidents
uv run copilot db retention            # preview; --apply compacts one bounded batch
uv run python scripts/verify_runtime.py
```

Full TCP/WebSocket and PostgreSQL checks remain mandatory; the optional in-memory
SDK harness is only an aid for restricted environments. The runtime verifier reads
the daemon's fresh persisted ledger without starting a competing import. Verification
must match the clean committed revision, current run, poll/stream freshness, broker
positions and account reconciliation. Record deployment evidence separately.

Next priorities and accepted limits live in the [architecture review](docs/architecture-review.md).
Corporate actions, per-signal partial allocation/protection, macro-age admission,
live trailing-policy parity and research executor separation remain unfinished.

Read the [alpha pipeline](docs/alpha-pipeline.md) before research, promotion or portfolio changes.
The DSL rejects lookahead and invalid contracts; research/live share scores and
versioned bracket policy. All trials and one-use holdout intervals are journaled.
The registry is transactional and loaded between scans; historical YAML imports
are unqualified shadow-only. No automatic promotion or combined-portfolio execution.
Use actual deployment-feed evidence, 20 shadow dates/10 decisions by default,
and exact immutable versions. Keep historical position protection and broker authority.
Schema 008 adds alpha projections and optional signal attribution. The original
[alpha-stack review](docs/alpha-stack-review.md) remains historical evidence;
its strict expected failures have become passing regression tests.

The [active alpha roadmap](docs/alpha-roadmap.md#current-priorities-after-the-whole-stack-survey) owns the ordered long-horizon plan. Read the [September 18 whole-stack survey](docs/alpha-stack-survey-2026-09-18.md) before further risk or mining changes. Account drawdown and [broker entry capacity](docs/entry-capacity.md) now share the existing FIFO/ledger boundaries: funding, borrowing, exact protection, observed-equity sizing and aggregate planned stop risk are rechecked before submission. Pre-reservation research acquisition, horizon-aligned gate calibration and measured portfolio volatility/tail constraints remain research priorities. The [800-search result](docs/alpha-power-diagnosis-2026-09-18.md) identifies GTC entry conversion and joint-gate power losses; distinguish it from the separately delivered [factor results](docs/alpha-factor-controls-2026-09-18.md). The convex allocator remains shadow-only. Update milestones and evidence when completing work.

The mining funnel now has one canonical cohort resolver. `alpha mine --universe
snapshot --universe-file SNAPSHOT/snapshot.json --max-symbols N` verifies a
prospective equity snapshot and records its hash/cap in each manifest. The
`screen --universe-file SCREEN/result.json --max-symbols N` path consumes a
completed source-specific liquidity screen and records its result hash/cap,
preserving the screen's deterministic ranking. The per-symbol DSL includes
causal volatility-scaled trend, range location, signed-volume, serial-return
and standardized-slope families; all are research-only and prefix-tested. The
scheduled ETF32 job remains the benchmark. Each symbol reserves its trial budget
before provider I/O, preserving charged failure evidence. Read
[alpha-mining-universe](docs/alpha-mining-universe.md) before widening campaigns;
current membership does not create point-in-time historical eligibility.

The matched screened-equity panel is recorded in
[the September 19 report](docs/alpha-matched-panel-2026-09-19.md). It compares
the expanded causal DSL families with constant, volatility and benchmark-residual
controls on one complete declared cohort, and records turnover plus signal-bar
market beta. A full-history run fails closed when current members lack bars; the
short recent diagnostic is descriptive only and cannot promote an alpha.

The [a priori alpha catalog](docs/apriori-alphas.md) tests literature anomalies as one
frozen hypothesis per leg (`config/research/apriori/*.json`, SHA-256 in each manifest;
a change is a new version). `alpha apriori-study` is research only: manifest before any
provider access, no DB/registry/broker/Telegram, no promotion credit. A passing leg only
becomes eligible for a separately specified capped paper probe. A passing leg runs only
as a catalog paper probe (`alpha apriori-probe`): registry `probe` list,
`probe_block_reason` liveness, `apriori_bracket_v1` fixed bracket with
`session_count_v1` time exit, LLM commentary only, one drift card per session outside
the native budget, fail-closed on Nasdaq/SIP gaps.

The [pooled mining lane](docs/alpha-pooled-mining.md) evaluates one dimensionless DSL
formula across a frozen cohort on a hash-identified long-bracket label cube, testing a
session-paired edge with a session-block bootstrap. Cohort v2 (`config/research/pooled/cohort-v2.json`)
comes from a frozen liquidity screen (`screen-v2.json`, ranked before the confirmation
window); `campaign-v2.json` pins it. The campaign runs once per protocol and only after
every check its protocol's mode requires (power check A, search check B and the
real-formula false-acceptance check C for a genetic protocol; A and C for a fixed set)
passed on the same cohort, cube, protocol and clean code revision. It charges every formula to the
pooled ledger (journal events, never `family/all`) before scoring it and consumes the
lane-wide confirmation window before reading it. Research only: no registry, broker,
Telegram or promotion credit. On 2026-10-02 checks A and C passed but check B failed
(1/10; top-3-of-~367 picks give near-neighbour formulas no shared picks), so the v2
campaign did not run and the confirmation window is unused
([checks](docs/alpha-pooled-checks-2026-10-02.md)). Protocol v3 selects the top decile of
eligible names per session (no hold-skipping). On 2026-10-05 its checks A and C passed and
B failed again (3/10), so the pooled genetic campaign is **parked**: no campaign, nothing
charged, confirmation window unused ([v3 checks](docs/alpha-pooled-checks-v3-2026-10-05.md)).
Reopening it needs a new spec and operator approval. Protocol v4 is a fixed-set protocol
(eight predeclared literature formulas, decile selection, no search): it runs with checks
A and C only. On 2026-10-07 both passed and the approved campaign found **no finalists**
(seven of eight formulas have a negative discovery edge), so nothing froze, the confirmation
window is still unused, and the fixed-set lane is closed on this window
([v4 checks](docs/alpha-pooled-checks-v4-2026-10-07.md)). The next alpha source is an
operator decision. `IntradayCampaignProtocol` (`id: pooled-intraday-campaign`) scores regular-session hourly bars read at the first completed regular bucket (09:00 New York, closing 10:00) of the decision session on the same cohort-v2 cube; v1 is a fixed set of eight intraday formulas under checks A and C (both passed 2026-10-14 and the approved campaign found **no finalists** the same day — seven of eight discovery edges negative, all within noise — so the intraday fixed set is closed on this window and the confirmation window is still unused, [results](docs/alpha-pooled-intraday-v1-2026-10-14.md)); the weekly per-symbol ETF32 miner is retired (`launchd.sh install-miner` installs nothing). Protocol `pooled-events-campaign` (`config/research/pooled/events-v1.json`) adds four event-conditioned, dimensionless fields (`earnings_age`, `earnings_surprise`, `earnings_reaction`, `dividend_yield`; state known at the prior close, PEAD visibility D+1, dividends scaled into the adjusted price scale at their ex-date) read at D-1 beside the daily bars from the offline Nasdaq page cache and the cohort-v2 corporate-actions acquisition, both digest-pinned; fields are `NaN` before a symbol's identity break (a `name_change` moving a ticker onto or off it) and the windowed formulas are `NaN`, not zero, out of window; fixed set, checks A and C only; no news or forward-looking fields. Cohort v3 (`config/research/pooled/cohort-v3.json`, the liquidity band ranks 401–1,400 by 60-session median dollar volume on the 2026-10-15 snapshot, no config-group source) re-freezes the three fixed sets as `cohort3-events-v1`, `cohort3-daily-v1` and `cohort3-intraday-v1` with an absolute $5M 20-session median dollar-volume eligibility floor (`universe.min_median_dollar_volume`, instead of the mega-cap static percentile) and 15 bp decisive cost; the events-v1 campaign on cohort v2 found no finalists ([results](docs/alpha-pooled-events-v1-2026-10-15.md)). On 2026-10-16 cohort v3's checks A and C passed for all three protocols and the three approved campaigns all found **no finalists** (largest t +1.85; leg means about half of v2's), so cohort v3 is closed, the confirmation window is still unused, and 48 formula-cohort pairs under the frozen bracket have produced no finalist ([results](docs/alpha-pooled-cohort3-2026-10-16.md)); the bracket is the next thing to test, not another cohort or family. The short-volume lane ([pooled mining](docs/alpha-pooled-mining.md#short-volume-lane-short_fields_v1), 2026-10-20) adds the research-only `alpha short-volume` acquisition of FINRA Reg SHO daily files (digest-pinned cache) and four dimensionless `short_fields_v1` fields (`short_ratio`, `short_ratio_z`, `short_volume_rel`, `offex_share`; off-exchange volume only, NaN rather than carried forward) read at D-1; protocol `pooled-short-volume-campaign` is a fixed set of eight formulas under checks A and C only, frozen as `short-v1` (cohort v2) and `cohort3-short-v1` (cohort v3), with campaigns on operator approval only. On 2026-10-20 checks A and C passed for both and the approved campaigns found **no finalist**: cohort v2 `no_finalists`; cohort v3 `no_confirmation_candidates` (`shorts_retreat`, `-short_ratio_z`, passed discovery at +0.020R, t +3.40, and failed the selection window at +0.006R), so nothing froze, the confirmation window is still unused, the short-volume fixed set is closed on this window and the mid-cap `shorts_retreat` result is a hypothesis only ([results](docs/alpha-pooled-short-v1-2026-10-20.md)); 64 formula-cohort pairs under the frozen bracket have produced no finalist. The EDGAR insider lane (research-only, 2026-10-21) adds the `alpha insider-filings` acquisition of the SEC Form 3/4/5 data sets (`EDGAR_USER_AGENT` required, digest-pinned cache) and six dimensionless `insider_fields_v1` fields (`insider_net_ratio`, `insider_buy_rel`, `insider_sell_rel`, `insider_buyers`, `insider_buy_age`, `insider_opp_buy_rel`) visible from the EDGAR filing date and read at D-1 (Form 4 open-market officer/director trades only; 10b5-1 plan trades kept in v1); protocol `pooled-insider-campaign` is a fixed set of eight formulas under checks A and C only, frozen as `insider-v1` (cohort v2) and `cohort3-insider-v1` (cohort v3), with campaigns on operator approval only.

The [spread-reversion lane](docs/alpha-spread-lane.md) (desk-direction item 2) is a predeclared, research-only pairs protocol on daily SIP bars: same-sector pairs from a frozen 127-name cohort, Engle-Granger formation (`coint`, MacKinnon p-values) over 504 sessions, z-score trading over the next 126 with frozen `α, β, σ_f` and next-open fills, four pass rules (lane bootstrap, trade means, counts, SPY beta) and a one-use confirmation window journaled under `spread/confirmation` before it is read. `alpha spread-study` refuses without passing `spread-power` (A) and `spread-null` (C) results at the same clean revision; nothing promotes, no probe or two-leg execution contract exists, and `/pairs` stays display-only. On 2026-10-10 both checks passed and the v1 study **failed discovery** (lane mean −0.3 bp/day at 5 bp; 84% of entries already beyond the stop because v1 has no upper entry band); the confirmation window is unused and v1 is closed ([results](docs/alpha-spread-results-2026-10-10.md)); a v2 needs a new frozen protocol and operator approval. Protocol v2 (`pairs-v2.json`, `second_look: true`) changes only the entry rule (crossing entry, |z| < 4 at the signal, no re-entry after a stop) and runs through the same checks and study by its own hash. On 2026-10-11 v2's checks passed and its study also **failed discovery** (234 trades, lane mean +0.02 bp/day, 6 of 11 windows positive); the confirmation window is still unused and the lane is closed as an alpha source on this cohort and discovery window ([v2 results](docs/alpha-spread-results-2026-10-11.md)).

Corporate-actions ingestion ([corporate actions](docs/corporate-actions.md), desk item 3 part 1) captures Alpaca corporate actions for the universe, held equities (tracked and broker-held) and `corporate_actions.extra_symbols` once per New York date in the daemon (one `corporate-actions/capture` event per content or symbol-set change, carrying the covered symbols; readiness component `corporate_actions`, four-day limit). The `corporate_action` entry rule (`risk/`) refuses equity entries in `enforce`, for symbols the capture covers, within `pre_days` ahead or `post_days` behind a structural action (splits, mergers, spin-offs, name changes, reorganizations (cash and/or a stock movement into a related symbol; records with several movements fail the whole normalisation: the capture fails closed and evidence goes stale, which never refuses); cash dividends only annotate) and defaults to `entry_block: preview` in code; `config/config.yaml` ships `enforce` since 2026-10-20 (preview week 2026-10-12 → 19: nine blocks, zero `would_block`, three `uncovered`). Stale, missing or uncovered evidence (a symbol the capture never requested) never refuses; the provenance block says which. `/positions` carries notes, and the ledger's unsupported-activity issue names the action but does not resolve it: totals stay withheld. Live bars stay raw and tracked quantities, stops and targets are not adjusted. `alpha corporate-actions` is the research-only cached acquisition (SHA-256-checked, no database or broker). The a priori entry `exdiv` v1 ([catalog](docs/apriori-alphas.md)) tests the pre-ex-dividend run-up on this acquisition (long E−5 → last cum-dividend close, mid-quarter same-name control, PEAD's P1–P4); `alpha apriori-study` dispatches on the entry id; a passing leg only becomes eligible for a separately specified probe. On 2026-10-12 the v1 leg **failed** on P2 (leg +0.069R per event but a same-name mid-quarter placebo +0.046R; paired difference ci90 [−0.026, +0.072]); no probe, the leg stays off ([result](docs/apriori-exdiv-2026-10-12.md)); a yield-conditioned v2 needs its own frozen entry declared as a second look.

News as measured context ([news context](docs/news-context.md), desk item 4) fetches Benzinga headlines through the desk's Alpaca data keys once per scan that ranks candidates without a timeframe (the scheduled universe scans, operator `/scan` and `scan --dry-run`; the intraday job never fetches), for the ranked native equity candidates (48 h window, market-wide lists excluded; futures and drift cards get no block), scores the first `news.max_scored_per_scan` ranked symbols with a direction-free LLM call (tone, relevance, catalyst, one line; `--no-llm` scans keep counts and headlines but score nothing), and carries one block per candidate on `LLMTradeEvaluation.news_context`, the card line "News (context, not a gate)", `decision_provenance["news"]` and, for universe scans, the `scan_candidates_ranked` candidate's `news`. `cards outcomes` attributes it (`news_*` columns, deterministic `news_alignment`, the `news_context` summary) the way the LLM gate is attributed; nothing in `risk/`, `card_policy`, `card_stats` or admission reads it, and the gate's prompt never sees news. Fetch or scoring failures yield `unavailable`/`score_failed` blocks with type names only and never touch readiness or the scan. `alpha news` is the research-only cached acquisition. `load_config` now passes the `corporate_actions:` and `news:` YAML blocks; before 2026-10-13 the `corporate_actions:` block, including `entry_block: enforce`, was silently ignored.

The [forecast-to-fill review](docs/forecast-to-fill-review.md) separates production
candidate arbitration from shadow allocation. [Contract fixes](docs/forecast-contract-hardening.md)
now bound traded notional, reject pending inventory, require matching forecast/risk
units, enforce calibration outcome availability and persist solver evidence. Legacy
retuning/config exports and uncalibrated Kelly sizing are removed. Provider reads use
bounded shared capacity and socket deadlines; doctor separates feed access from freshness.
Recent-SIP entitlement, dependence-aware experiments and
executable target plans remain priorities. Never connect shadow weights directly to orders.

`alpha calibrate` is a bounded synthetic diagnostic: no runtime config/DB/provider,
no promotion or shadow credit. Keep the shared statistical assessment separate from
deployment permissions. Its fixed-panel bootstrap does not replay adaptive search;
never use its output to discount cumulative trials or relax gates retroactively.

A1b uses the [predeclared study protocol](docs/alpha-study-protocol.md) and
`config/research/a1b-v1.json`. `alpha study` persists its manifest before computation
and every replicate afterward; development failures preserve unexamined validation.
Keep development/validation seed namespaces separate, freeze the winner before
holdout, retain incomplete denominators, and never tune gates on validation results.
The [A1b result](docs/alpha-study-2026-09-16.md) retains all 1,952 jobs but is
scientifically incomplete (22 unavailable comparisons). No gate change is justified.
A2a's [return-timeline contract](docs/alpha-return-timeline.md) includes cash bars
independently of feature availability and persists coverage. The validation policy
versions this calculation; old evidence cannot promote or commit new alpha risk.
Lifetime trial counts persist; variance uses comparable current-clock samples.
The [fresh study](docs/alpha-timeline-study-2026-09-16.md), using
`config/research/a2a-v1.json`, completed 1,952 jobs without unavailable comparisons.
Null-search criteria passed; positive-control power is still insufficient. Keep
the gates unchanged. A2b's [session replay groundwork](docs/alpha-session-replay.md)
uses observed calendars, complete minute coverage and the shared execution engine.
`alpha replay` charges one real-data research attempt and excludes the inspected
interval before I/O; it never qualifies or earns shadow credit. Historical minute
data cannot establish point-in-time availability or actual partial fills. Durable
session acquisition/scheduling is implemented; actual feed and broker execution
evidence remains necessary.
Existing fixed-duration research/screening/shadow paths share the market clock
guard and reject explicit session/unknown layouts or ambiguous timestamps. Do not
silently reinterpret an old alpha version as using session-derived bars.
Preserve historical study evidence and use fresh validation after policy changes.

A2b's [prospective data observer](docs/alpha-forward-observations.md) runs in the
existing daemon, independently of scans/halt state. The desk observes SPY/QQQ 15m on
its configured feed. Read `alpha status` for retained capture quality; `alpha_observer`
readiness means collector progress, including idle session waits, not complete data.
Raw receipts/revisions use the existing journal/private artifacts and earn no trial,
shadow or promotion credit. Inspected dates are excluded before price reads. Preserve
existing alpha identities; strategy clock migration and actual execution evidence
remain pending. Shutdown must finish reads before closing the observer's SDK clients.

New [session alpha definitions](docs/alpha-session-decisions.md) use semantics version 3
and an explicit clock inside their immutable identity. Replay and screening/shadow
share delay/expiry; live snapshots require actual request/receipt times. Old version-2
hashes remain exact. Session versions cannot qualify, activate or reserve entry risk;
the dedicated diagnostic worker now owns receipt-aware acquisition and durable session
decisions; native strategy scans retain their own clock.

`alpha benchmark` now evaluates explicit-horizon forecast components, separately from
trading-policy P&L. It retains charged diagnostic artifacts, never promotes, and does
not add forecast statistics to the strategy-Sharpe variance sample. See the
[forecast benchmark contract](docs/alpha-forecast-benchmarks.md) and canonical alpha roadmap.

`alpha benchmark` supports explicit `--label`/`--feature` and optional per-side
`--cost-bps` scenarios. These are charged daily bar-price payoff diagnostics with
no promotion or broker-fill claim; see [timing/cost contracts](docs/alpha-forecast-policy.md).

The [open-gap timing/cost follow-up](docs/alpha-open-gap-policy-2026-09-17.md)
completed 20 attempts and failed its frozen trading criterion. Keep this standalone
long/cash policy paused. Subsequent ETF campaigns are recorded below; measured
forward/execution evidence and the current roadmap govern further work.

The [open-gap postmortem](docs/alpha-open-gap-postmortem-2026-09-17.md) reproduced
calculations but found outlier-concentrated forecast gains and unstable selection.
Fold/influence/action diagnostics now accompany every new benchmark; a rejected
experiment alone is not evidence of a calibrated or bug-free pipeline.

Benchmark reports now expose fold stability, signed error influence, forecast/action
distributions, cost attribution and fitted-model evidence. These are descriptive
diagnostics, not promotion gates; see [report contracts](docs/alpha-forecast-benchmarks.md#automatic-diagnosis-before-lead-selection).


The [frozen ETF session campaign](docs/alpha-session-campaign-2026-09-17.md) completed all 81 attempts with full execution
coverage. None passed every rule: QQQ momentum's positive cost-stressed returns had
only 11 closed trades. Six predeclared SPY/QQQ hypotheses are designated for diagnostic
forward observation, not activation or qualifying shadow credit. Preserve the failed
triage result; timed order/holding lifetimes are implemented. The continuous and sector-panel campaigns are complete; measured forward/lifecycle evidence continues to accumulate.

Inspect `copilot alpha forward --days 7` or `/alphas` for the [shared forward evidence report](docs/alpha-forward-evidence.md). It counts canonical outcomes and measured receipts, exposes gaps/truncation, and grants no qualification credit. Recorded-score fractions are not complete calendar coverage; readiness remains a separate freshness check.

## Timed alpha execution

Read [trade lifetimes](docs/alpha-trade-lifetimes.md) before changing expiry. Pure elapsed-UTC deadlines are shared by replay and the injected `TradeLifetimeService`; optional timed policies create new immutable version-3 identities. Existing policy hashes/positions are unchanged. Entry cancellation persists one `entry_cancel` work item before DELETE, retains risk and blocks fresh admission until exact group confirmation, and recovers by lookup only. Holding expiry reuses `PositionCloseService` with a stable request ID, including failed attempts. Partial/uncertain evidence halts new risk; `/resume` cannot waive it. Journal/outbox writes are atomic; readiness includes `entry_cancellation`. No new schema/queue, automatic promotion, or live qualification claim.

The [continuous timed ETF campaign](docs/alpha-continuous-campaign.md) pairs the new lifetime contract with actual-data discovery: 48 frozen comparisons and a new trend/pullback hypothesis. Replay supports one continuous year with bounded acquisition chunks; trial/holdout accounting and session activation gates remain intact. Report research yield separately from deployment readiness.

The [completed continuous study](docs/alpha-continuous-campaign-2026-09-17.md)
retains 36 complete runs and 12 SPY coverage failures. QQQ momentum has 415 closes
and positive primary-cost returns, but fails cost stress; zero alphas promoted.
The sector-panel path adds explicit [Rank IC/ICIR contracts](docs/alpha-information-coefficient.md).
Existing miner ICIR is overlapping, single-symbol and unannualized.

`copilot alpha panel-study PROTOCOL --output NEW_PRIVATE_DIRECTORY` runs the
[frozen sector-panel research path](docs/alpha-sector-panel.md): observed-calendar
native daily inputs, causal relative features, cross-sectional IC/HAC and five-session
basket/cost diagnostics. All 32 declared comparisons and every member's inspected
interval are journaled before provider access. It sends no orders or notifications,
grants no qualification/registry credit and leaves weekly mining unchanged.

The [completed sector-panel study](docs/alpha-sector-panel-2026-09-17.md) retains
32/32 complete comparisons and zero passes. Volatility-scaled momentum has mean
Rank IC 0.0136 and +3.63% at 1 bp per side, but −4.27% at 5 bp and concentrated
gains. All formulas remain research-only; active alphas remain zero. The [persistent-book follow-up](docs/alpha-persistent-book-2026-09-17.md) is also complete;
use the [current roadmap](docs/alpha-roadmap.md#source-calibration-and-individual-equities--current-ordered-priorities) for next work. [Automatic Alpaca bar evidence](docs/market-data-evidence.md) now retains raw pages and normalization outcomes.

The [persistent ETF book experiment](docs/alpha-persistent-book.md) uses the shared
panel journal and explicit adjusted-price accounting. It is research-only: reserve
the frozen matrix before access; no promotion or live portfolio execution.
[Verified feed access and alternatives](docs/research-data-sources.md) distinguish
recent-SIP entitlement from throttling; historical SIP remains usable.

Source-specific [volume calibration](docs/alpha-volume-calibration.md) uses the shared
daily study harness with frozen training/forward intervals and exact feed identity.
`alpha volume-study` is research-only. `/alphas` now provides a compact explanation
and status summary; use `alpha forward` and `alpha list` for full evidence/definitions.

Prospective research workers have explicit `alpha_pipeline.{observations,decisions}.feed`
settings, independent of trading data. Desk IEX collection and the separately identified
control cohort are documented in [IEX forward evidence](docs/alpha-iex-forward.md).
Keep original SIP failures/identities; other-feed candidates remain visible but uncollected.

[Prospective equity candidates](docs/alpha-equity-universe.md) are captured by
`alpha universe-snapshot`: exact UUIDs, raw metadata, exclusions, receipt fences and
stable-hash selection use the existing diagnostic journal. The actual 300-name
cohort is not a historical constituent list or a verified common-stock/liquidity universe.
Use its snapshot ID and preceding-only screening before any broader forecast study.

`alpha liquidity-study` now applies a frozen daily liquidity/coverage screen to the
complete [dated equity cohort](docs/alpha-equity-universe.md#daily-liquidity-and-coverage-screen).
It reuses the daily study service and diagnostic journal, with bounded paced reads,
immutable member checkpoints and fail-closed selection. IEX activity is source-specific;
this development screen neither establishes historical membership nor qualifies alphas.

[Screened panel forecasts](docs/alpha-panel-forecasts.md) reuse the daily acquisition/journal service. Keep all frozen members on the exchange clock, derive eligibility only from past bars, and train Ridge only on strictly mature labels with training-only scaling. Freeze weights before inspecting outcomes; missing held outcomes withhold the full curve. The old complete-panel and production qualification contracts remain unchanged. No active alpha or calibrated promotion power is implied by a completed diagnostic.

[Retained forecast controls](docs/alpha-forecast-controls.md) reuse the same daily workflow and payoff kernels. Charge and exclude all parent members before reading artifacts; preserve source hashes and original receipt times. Strict endpoint volume masks outcomes only. Never change decisions based on future availability, refit the frozen parent, compound paired return differences, or present long-only context as matched market-neutral risk.


`alpha power-plan`/`power-study` reuse the synthetic artifact lifecycle. Keep paired
known/winner routes, frozen bootstrap and sourced count/variance scenarios. Persist
selection before holdout; mechanical development failures must leave validation
unexamined. Measurement reuse changes no valid qualification thresholds. Missing or
nonfinite sampling evidence is unavailable and fails closed. Synthetic diagnostics
cannot authorize promotion, journal production trials or earn shadow credit.

`alpha lifetime-plan`/`lifetime-study` use the same artifact lifecycle for the
paired P0/P1/P2 execution counterfactual. The fixed-daily clock and
`elapsed_utc_v2` policy are diagnostic-only; selection and scores are frozen once,
independent disabled deadlines are explicit, and each selection is checkpointed
before replay. No lifetime attribution result can qualify an alpha or alter the
running paper service.


[Factor controls](docs/alpha-factor-controls.md) share `AlphaPanelService`, retained
parent hashes and the existing journal. Freeze all six arms before reading prices;
reserve/exclude the whole parent before artifact access. Keep original Ridge
forecasts, past-only residual fits/common support, and strict future outcome masks.
HAC20 and descriptive basket differences do not correct repeated research. ETF
loadings are explanatory proxies, not a factor-neutral or executable portfolio.


The [54-comparison factor study](docs/alpha-factor-controls-2026-09-18.md) is complete
and independently audited. Residual momentum adds no consistent improvement over
the existing blend; support loss, concentration and nonzero factor exposures remain
visible. Active mined alphas remain zero; prospective evidence is the next stage.

The [prospective native-daily panel](docs/alpha-daily-panel.md) freezes four diagnostic
controls over 64 equities plus nine ETF proxies. Its existing-daemon worker uses
next-New-York-midnight windows, actual receipts, strictly mature training, atomic
residual-state/forecast claims and terminal first outcomes. Missed sessions stay
missing. Readiness is worker progress, not forecast/outcome quality. No promotion
or trading authority follows from this experiment.
Independent baseline companions share that capture, fit and outcome acquisition;
preserve the original four-arm artifact and count each comparison separately.
Journal claims pin immutable companion enrollments before price reads. Removing
configuration paths does not retire enrollment or cancel outcomes. Validate saved
artifacts against those pins; publish completion only after every required artifact
is saved. Parent campaign retirement/draining remains unimplemented.

Semantics version 5 is the production native-daily contract: no clock, exactly
`DAILY_ENTRY_LIFETIME_SECONDS = 57,600` resting and no holding deadline, so
research (bar label) and live (`submitted_at`) both mean one regular session.
Never relax it to a configurable value, add a holding deadline to it, or re-fence
it on `semantics_version`; the miner's `--entry-policy session` default is deliberate, and a
scheduled `alpha mine` run must pin `--entry-policy gtc` (the weekly ETF32 job itself was retired on
2026-10-14; `launchd.sh install-miner` installs nothing).
