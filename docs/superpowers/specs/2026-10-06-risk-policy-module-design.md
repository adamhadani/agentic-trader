# L2: one risk-policy module (design)

**Date:** 2026-10-06. **Status:** decision table approved by the operator in chat (2026-10-06, all rows as recommended, including the two behaviour changes in rows 3 and 5); execution by subagent-driven development; implemented 2026-10-06, with the amendments at the end of this document. **Contract:** [risk policy](../../risk-policy.md). **Roadmap:** [Stage layering and attribution, step L2](../../alpha-roadmap.md#stage-layering-and-attribution-october-5). **Survey evidence:** the per-site extraction recorded in the L2 ledger (`.superpowers/sdd/2026-10-06-risk-policy-module/survey.md`).

## Goal

Every entry-risk rule has exactly one implementation, with typed inputs and a typed result, and every layer that re-checks a card (scan-time evaluation, sizing, tap-time gates, re-pricing, transactional admission, final submission) calls that implementation. Today the correlation cap, the per-trade risk base, the minimum reward/risk, notional caps, macro lockout, session and earnings checks are each re-implemented in two to four places with different semantics, so the evaluator and admission can disagree about the same card.

The operator's emphasis for this work: clean interfaces between the risk module and its callers, test-driven design, integration and adversarial testing, and docs and comments that describe the final state.

## Non-goals

- No change to the scan's per-session card budget (`max_cards_per_scan`, `max_cards_per_session`, `max_cards_per_group_per_session`) or to the PEAD open-position cap; they are budgets, not risk rules. They only start sharing the group-membership function.
- No change to broker capacity evidence (`execution/capacity.py`'s freshness, funding, borrowability and protection checks). Capacity keeps calling admission for the shared rules.
- No new config key, no migration (head stays `008_alpha_pipeline`), no new Telegram message. Operator-facing reason texts change wording where a rule is unified; the tests that pin them are updated in the same change.
- No change to how positions are stored or to the ledger; the module reads books built from existing rows.
- The statistical return-correlation check (`enable_dynamic_correlation`, evaluator only, needs a data fetcher) stays in the evaluator as is.

## Decisions (approved 2026-10-06)

| # | Rule | Decision |
| --- | --- | --- |
| 1 | Correlation cap | Same direction at every layer; one group-membership function with symbol normalisation (`/MES` ≡ `MES`, case-insensitive); an empty `correlation_groups` disables the rule at every layer (no fallback to `DEFAULT_CORRELATION_GROUPS`). The scan's per-session card cap stays a separate budget rule sharing the membership function. |
| 2 | Per-trade risk budget | One formula: `min(cash, equity) × max_risk_pct_cap × drawdown_factor × clamp(macro_multiplier, 0.10, 1.0)`, applied to every sizing tier, to admission, to the tap-time regime gate and to re-priced sizing. Consequence: the max tier shrinks under macro stress; a tier offered on a card is never refused later for size. |
| 3 | Minimum reward/risk | `required_reward_risk = max(config.risk.min_risk_reward_ratio, regime.min_rr_threshold)`. The evaluator builds the deterministic target at that ratio (today: config only), clamps the LLM's target to it, and admission, the tap gate and re-pricing use the same. **Behaviour change:** under an elevated or extreme VIX regime, cards get wider targets; today such cards fail the tap gate on reward/risk. |
| 4 | Notional and asset-class caps | One predicate priced at `max(entry, current_price)` when a current price is known; FX and any class without a configured cap is explicitly uncapped (no `KeyError`). Each layer keeps its own book (scan: open positions plus this scan's cards; admission: open positions plus reservations). |
| 5 | Concurrent positions and aggregate stop-risk | The scan runs both against the open book before sending a card. **Behaviour change:** a full book or an exhausted stop-risk budget stops a card at scan time instead of letting it fail at tap. |
| 6 | Macro lockout | One predicate; admission runs it on both broker branches (simulated and Alpaca), not only the Alpaca branch. |
| 7 | Session / RTH | One `entry_session_open(is_open, is_rth, enforce_rth)` used by the evaluator, the tap, re-evaluate, `/scan` and card validity; `enforce_rth=False` now means extended hours are allowed at every entry gate (today the tap paths require RTH regardless). Scheduling (`is_session_active`) is unchanged. |
| 8 | Earnings blackout | One pure predicate shared by the evaluator and the tap; admission stays out (no calendar at that layer). |
| 9 | Drawdown inputs outside Alpaca | Unchanged behaviour, made explicit: the simulator passes `equity=None, drawdown_pct=0.0` into the typed budget. |

## Package and interface

`agentic_trader/risk.py` becomes the package `agentic_trader/risk/`. The import path `agentic_trader.risk` stays canonical; every internal caller imports the public names from it.

```
agentic_trader/risk/
  __init__.py     public API re-exports (the only import path callers use)
  capital.py      requires_account_risk, risk_capital, drawdown_risk_factor (moved),
                  macro_risk_factor, RiskBudget, per_trade_risk_budget
  limits.py       RiskLimits (frozen, from AppConfig), normalize_symbol
  book.py         BookPosition, Book (typed view of open positions / reservations / this scan's cards)
  rules.py        RiskRule, Rejection, EntryIntent, one function per rule, required_reward_risk,
                  meets_min_reward_risk (moved from execution/freshness.py), in_lockout_window,
                  earnings_days_out, entry_session_open, composites book_gates / admission_gates
```

### Types

```python
class RiskRule(StrEnum):
    EXPOSURE_UNKNOWN = "exposure_unknown"; DRAWDOWN_HALT = "drawdown_halt"
    PER_TRADE_RISK = "per_trade_risk"; PER_TRADE_NOTIONAL = "per_trade_notional"; QUANTITY_CAP = "quantity_cap"
    REWARD_RISK = "reward_risk"; AGGREGATE_STOP_RISK = "aggregate_stop_risk"
    CONCURRENT_POSITIONS = "concurrent_positions"; SAME_SYMBOL = "same_symbol"
    PORTFOLIO_NOTIONAL = "portfolio_notional"; ASSET_CLASS_NOTIONAL = "asset_class_notional"
    CORRELATION_GROUP = "correlation_group"; MACRO_LOCKOUT = "macro_lockout"
    EARNINGS_BLACKOUT = "earnings_blackout"; SESSION_CLOSED = "session_closed"; SESSION_NOT_RTH = "session_not_rth"
    REGIME_BREAKOUT = "regime_breakout"

@dataclass(frozen=True)
class Rejection:
    rule: RiskRule
    reason: str            # operator-facing text; str(rejection) == reason

@dataclass(frozen=True)
class EntryIntent:        # what is about to be risked; validated on construction
    symbol: str; direction: str; asset_class: str; quantity: float
    entry: float; stop: float; target: float; multiplier: float = 1.0
    current_price: float | None = None; strategy: str | None = None
    # derived: key (normalised symbol), risk_distance, reward_distance, risk_dollars, exposure_price, notional

@dataclass(frozen=True)
class BookPosition:
    symbol: str; direction: str; asset_class: str
    notional: float | None; planned_risk: float | None; reservation: bool = False
    # valid: both numbers finite and >= 0

@dataclass(frozen=True)
class Book:
    positions: tuple[BookPosition, ...]
    @classmethod from_signal_rows(rows, *, reservations: bool) -> Book   # normalises dict keys/case
    with_position(position) -> Book
    count, invalid (positions with unknown exposure), notional(), notional_for(asset_class),
    planned_risk(), holds(key), same_direction_in(keys, direction)

@dataclass(frozen=True)
class RiskBudget:
    capital: float; drawdown_factor: float; macro_factor: float; dollars: float
```

`RiskLimits.from_config(config)` snapshots every limit the rules read (cash, caps, drawdown policy, class caps as a mapping with no entry for uncapped classes, normalised correlation groups, min reward/risk, lockout minutes, blackout days, `enforce_rth`). Rules never read `AppConfig` directly.

### Functions

Each rule is a pure function returning `Rejection | None`:

`exposure_known(book)`, `drawdown_halt(budget)`, `per_trade_risk(intent, budget)`, `per_trade_notional(intent, limits)`, `quantity_cap(intent, limits)`, `reward_risk(intent, required)`, `aggregate_stop_risk(intent, book, budget, limits)`, `concurrent_positions(book, limits)`, `same_symbol(intent, book)`, `portfolio_notional(intent, book, limits)`, `asset_class_notional(intent, book, limits)`, `correlation_group(intent, book, limits)`, `macro_lockout(event_title, event_at, now, limits)`, `entry_session_open(is_open, is_rth, enforce_rth)`, `regime_breakout(strategy, breakout_allowed)`.

Pure predicates shared with callers that format their own text: `in_lockout_window(now, event_at, pre_minutes, post_minutes) -> bool` (used by `agent/calendar.py`), `earnings_days_out(event_date, today, blackout_days) -> int | None` (used by `agent/earnings.py`), `required_reward_risk(limits, regime_min_rr) -> float`, `meets_min_reward_risk(reward, risk, minimum) -> bool` (moved; `execution/freshness.py` imports it).

Budget: `per_trade_risk_budget(limits, *, equity, drawdown_pct, macro_multiplier=1.0) -> RiskBudget`; `macro_risk_factor(multiplier)` clamps to `[0.10, 1.0]`.

Composites fix the rule order once:

- `book_gates(intent, book, budget, limits) -> tuple[Rejection, ...]`: every shared book rule in order `exposure_known, drawdown_halt, aggregate_stop_risk, concurrent_positions, portfolio_notional, asset_class_notional, correlation_group`. The scan reports the first.
- `admission_gates(intent, book, budget, limits, *, regime_min_rr=None) -> Rejection | None`: the first of `exposure_known, drawdown_halt, reward_risk, per_trade_risk, per_trade_notional, quantity_cap, aggregate_stop_risk, concurrent_positions, same_symbol, portfolio_notional, asset_class_notional, correlation_group`.

Validation policy: `EntryIntent` and `BookPosition` reject non-finite or negative numbers at construction (`ValueError`); callers that receive untrusted dicts (admission, the scan) convert that into the existing "finite and positive" / "exposure unknown" rejections. Rule functions themselves never raise on valid inputs. `macro_risk_factor` and `risk_capital` raise on non-finite inputs, as today.

## Consumers

| Site | Today | After |
| --- | --- | --- |
| `agent/evaluator.py` blocks 1–1c, 2, 2b, 3, 4 | inline caps and gates | `Book.from_signal_rows(active_positions, reservations=False)`, `book_gates(...)` (adds concurrent positions and aggregate stop-risk), then `macro_lockout`, the earnings reason via `earnings_days_out`, `regime_breakout`, `entry_session_open`. The deterministic target uses `required_reward_risk(limits, regime.min_rr_threshold)`; the LLM clamp uses the same. The statistical-correlation block stays. |
| `agent/position_sizing.py` | `max_risk_dollars = capital × cap × drawdown` | `budget = per_trade_risk_budget(limits, equity=…, drawdown_pct=…, macro_multiplier=…)`; `max_risk_dollars = budget.dollars` (macro now applies to the max tier); base tier unchanged. |
| `execution/admission.py reservation_rejection` | inline rules, any-direction correlation, config-only R:R, FX `KeyError` | intent/book/budget built from the request and rows; `admission_gates(..., regime_min_rr=None)`; signature unchanged so `workflow.py` and `capacity.py` keep calling it. |
| `execution/entries.py _preflight` | macro check on the Alpaca branch only | macro check before the branch split, so the simulated branch rejects during a lockout too. |
| `agent/copilot.py _regime_gate` | cash × cap × unclamped multiplier; config/regime R:R | `regime_breakout`, `reward_risk(required_reward_risk(...))`, `per_trade_risk(intent, per_trade_risk_budget(limits, equity=None, drawdown_pct=0.0, macro_multiplier=regime.risk_multiplier))`. |
| `agent/copilot.py _capped_replacement_quantity` | cash × cap × min(1, mult) | `per_trade_risk_budget(...).dollars / unit_risk`, same clamps. |
| `agent/copilot.py` tap, re-evaluate, `/scan`, `_card_valid_until` | `is_open and is_rth` regardless of config | `entry_session_open(info.is_open, info.is_rth, limits.enforce_rth) is None`. `assess_card` receives `session_open` instead of `session_is_rth`. |
| `agent/copilot.py` scan start; `agent/calendar.py is_in_lockout_window` | own arithmetic | `in_lockout_window(...)` from the module. |
| `agent/earnings.py earnings_blackout_reason` | own arithmetic | `earnings_days_out(...)` from the module; keeps its text. |
| `run_scan` in-scan `active_positions.append` | no `risk_dollars` | adds `risk_dollars` so the stop-risk pre-check sees this scan's cards. |
| `cli/commands/db.py queue` | `default=WorkKind.ENTRY` fails under click 8.5 | `default=str(WorkKind.ENTRY)`. |
| `agent/position_sizing.py:14` | stale type-only import | removed. |

## Testing

- **Unit, table-driven, per rule** (`tests/risk/test_rules.py`, `test_capital.py`, `test_book.py`, `test_limits.py`): one parametrised table per rule covering pass, boundary (equal to the cap), fail, uncapped class, empty groups, normalised symbols, reservation flag, None/NaN exposure.
- **Adversarial** (`tests/risk/test_adversarial.py`): a seeded generator (numpy, fixed seed, at least 2,000 cases) of intents, books and limits with hostile values: NaN/inf/negative/zero numbers, mixed-case and slash-prefixed symbols, duplicate positions, unknown asset classes, drawdown above the stop, macro multipliers 0/NaN/>1, empty and overlapping groups. Properties: typed constructors reject invalid numbers with `ValueError`; given valid inputs no rule raises; a book with any invalid position is rejected by `exposure_known` before any numeric rule; results are deterministic and independent of position order; adding a position never turns a rejection into a pass.
- **Cross-layer consistency** (`tests/risk/test_consistency.py`): for every generated case, the first shared-rule rejection from `book_gates` equals the shared-rule rejection from `admission_gates` on the same book, budget and limits; `reservation_rejection` on an equivalent `OrderRequest` and rows agrees with `admission_gates`.
- **Integration on the temp database** (`tests/agent/test_risk_policy_integration.py`), through the real `run_scan`, tap and enqueue paths: (a) a card produced under an elevated regime passes `_regime_gate` on reward/risk; (b) a full book stops the card at scan time with the concurrent-positions reason; (c) `correlation_groups = {}` admits a second same-group position at both layers; (d) in simulator mode admission rejects during a macro lockout; (e) `enforce_rth=False` lets a tap through outside RTH.
- **Regression gates per task:** `tests/agent tests/execution tests/workflows tests/storage tests/risk tests/cli tests/notifier tests/broker tests/market`; final task: full `uv run pytest`, `pre-commit run --all-files`, and the PostgreSQL integration run (`TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_agentic_trader_codex_20260915 uv run pytest tests/integration --run-postgres`), since `storage/workflow.py` and admission change.
- **Deployment evidence** (separate): controlled restart, `/readyz`, `scripts/verify_runtime.py`, and the first scan's cards carrying targets at the regime-adjusted ratio when the regime is elevated.

## Documentation

- New `docs/risk-policy.md`: the rule table (rule id, inputs, which layers run it, reason text, test file), the budget formula, the book definitions per layer, and the behaviour changes with their date.
- `docs/production.md`: pointer from the sizing/caps paragraphs; `docs/entry-capacity.md`: admission now delegates shared rules; `docs/architecture-review.md`: the "same rule, different semantics" finding recorded as resolved with the PR.
- `CLAUDE.md`: code map row adds `risk/`; contract 1 gains one sentence: every entry-risk rule has one implementation in `agentic_trader/risk`; layers differ only in the book they pass.
- `docs/alpha-roadmap.md`: L2 row → implemented with the PR.
- Module and function docstrings state the rule, its inputs and which layers call it; consumer comments name the rule they delegate to. No comment may describe the pre-L2 behaviour.

## Acceptance

- `grep` finds no second implementation of any rule in the table (correlation membership, per-trade budget arithmetic, notional/class caps, lockout window arithmetic, blackout day arithmetic, session-open predicate, reward/risk comparison) outside `agentic_trader/risk/`.
- Every rule has a table-driven test; the adversarial and consistency suites pass; the five integration scenarios pass.
- Full suite, pre-commit and the PostgreSQL integration run are green.
- Docs describe the final behaviour, including the three behaviour changes (rows 3, 5, 7) and the removed default-groups fallback.

## Amendments after implementation (2026-10-06)

Rulings made during the task reviews. They supersede the text above where they differ;
[risk policy](../../risk-policy.md) describes the resulting behaviour.

- **Target rounding (decision 3, ruling R7 as amended).** The deterministic target price
  is computed from the unrounded actual stop distance (`entry − stop_loss` on the actual
  prices) times the required ratio and rounded *away from the entry* to the next tick
  (ceiling for LONG, floor for SHORT, with a 1e-6-tick tolerance). `reward_risk` on the
  actual prices therefore always holds, including sub-penny entries. A target already on
  a tick stays, so on-tick ratio-2.0 targets equal the earlier nearest-tick result.
  Research replay (`research/setups/replay.py`) and the legacy backtest share
  `calculate_levels_deterministic` at the configured ratio, so their targets may move up
  to one tick farther from the entry; studies completed before 2026-10-06 used the
  earlier rounding.
- **Decision 6 as implemented.** Admission's injected macro check is the lockout window
  **and** the regime gate (`regime_breakout`, `reward_risk` at the refreshed regime's
  ratio, `per_trade_risk` on configured cash with the regime multiplier). It runs on both
  broker branches, after the Alpaca price-drift check and the unsupported-evidence
  refusal. Consequences, accepted as the one-gate principle:
  - simulator entries can be refused by regime rules;
  - the regime half fails closed when VIX or macro data is unavailable, while the
    lockout half fails open when the economic calendar is down;
  - the preflight makes provider calls inside the 60 s preflight lease;
  - `get_regime(force_refresh=True)` refreshes the shared regime cache on every dispatch.
- **Final-bracket check after the LLM (rulings R10, R14).** When the LLM's bracket differs
  from the deterministic one, the evaluator builds the final `EntryIntent`. It restores
  both deterministic levels in three cases: the construction fails; `reward_risk` at the
  required ratio or `per_trade_risk` for the card's budget refuses it; or, for a paper
  probe, the final risk exceeds `alpha_pipeline.probe_risk_dollars`. Approval and the
  LLM's verdict are untouched, and `risk_reward_ratio` is reported from the final stop
  distance. A card is never refused later for size because of an LLM edit.
- **Card validity.** `valid_until` is recorded only when `entry_session_open` allows
  entries and the provider's `next_close` is timezone-aware **and still in the future**. A
  CME evening session reports that day's already-passed 17:00 ET halt, which would expire
  the card at its first tap. Otherwise the card expires at the New York date change.
- **Probe cap.** Sizing caps a probe's max tier at `alpha_pipeline.probe_risk_dollars`
  (one evaluator helper, `_probe_risk_cap`). The final-bracket check above applies the
  same cap, so the probe cap only ever reduces risk.
- **Book validation.** `BookPosition` does not raise. A missing, non-finite or negative
  `notional`/`planned_risk` makes the position invalid, and `exposure_known` refuses
  before any numeric rule at every layer (`book_gates` then reports that rejection
  alone). `EntryIntent` remains the raising validation boundary, and the scan converts
  its `ValueError` into admission's "Quantity and bracket prices must be finite and
  positive." text.
- **Card budget membership.** The per-group card cap keeps
  `TradingCopilot.correlation_groups_of`; its root/slash matching is equivalent to
  `normalize_symbol`. Sharing the function is left to the card-selector seam (L3).
