# Book-aware sizing (PR B) — design

**Date:** 2026-10-09. **Follows:** `2026-10-08-honest-cards-design.md` (PR #110) and
`2026-10-09-scan-journal-coverage-design.md` (PR #111). **Roadmap:** desk direction item 1, the
deferred L3 seam ("book-aware sizing").

## Problem

The 2026-10-08 operational definition says a suggestion is reasonable when (1) the card states its
measured base rate, (2) its bracket has non-negative EV at that rate, and (3) its size is consistent
with the existing book. PR A delivered (1). Nothing delivers (3): a card's quantity comes from the
per-trade risk budget, the notional caps and the tiers, and the only book-aware checks are counts
(concurrent positions, same-direction correlation groups) and the aggregate planned stop risk. Two
cards on names that move together can each pass every rule and still double the book's daily
variance. The operator's framing of (3) was "convex optimisation vis-a-vis existing positions".

The code survey (`~/agentic-trader-research/pr-b-sizing-survey-20261009.md`) ruled out the shadow
CVXPY optimiser as the live sizer: `ConvexAlphaPortfolioOptimizer.optimize` requires an expected-return
vector, bounds risk only by a penalty, `build_shadow_portfolio` forbids nonzero reservations, and the
forecast-to-fill contracts say its output never reaches orders. The operator approved the recommended
alternative on 2026-10-09: a closed-form marginal-risk rule for the live factor, the optimiser run in
shadow for evidence only.

## Decision

### D1. One closed-form rule: `book_vol_factor`

A pure function in a new module `agentic_trader/risk/book_vol.py` (the risk package's "one
implementation per rule" contract; no config reads, no I/O):

```python
@dataclass(frozen=True)
class BookVolInputs:
    weights: Mapping[str, float]  # signed dollar notional per symbol in the book (long +, short −)
    candidate: str  # candidate symbol
    candidate_notional: float  # signed dollar notional at the proposed quantity
    covariance: pd.DataFrame  # shrunk daily-return covariance, symbols ⊇ book ∪ {candidate}
    budget_dollars: float  # allowed portfolio daily dollar-vol after the card


@dataclass(frozen=True)
class BookVolResult:
    factor: float  # in [0, 1]
    vol_before: float  # portfolio daily dollar-vol without the card
    vol_after_full: float  # with the card at the proposed notional
    vol_after_scaled: float  # with the card at factor × proposed notional
    marginal_vol_full: float  # vol_after_full − vol_before
```

`book_vol_factor(inputs) -> BookVolResult`. With `w` the book vector, `c` the candidate unit vector
and `x` the candidate notional, `vol(x)² = wᵀΣw + 2x·(Σw)_c + x²·Σ_cc`. The factor is the largest
`f ∈ [0, 1]` with `vol(f·x) ≤ budget`, solved in closed form from the quadratic (if `vol_before >
budget`, the factor is 0: the book is already over budget and the card adds risk; if the quadratic has
no positive root the factor is 0; if `vol(x) ≤ budget` the factor is 1). A symbol in the book that is
missing from the covariance makes the inputs invalid: the caller passes nothing and the result is
`unavailable` (below). The function never raises on numeric edge cases: non-finite or negative
variances raise `ValueError`, which the caller maps to `unavailable`.

### D2. Covariance estimation: `CovarianceEstimator`

`agentic_trader/research/setups/covariance.py` (research-grade estimation code, like the shadow
ranker's features):

- `daily_returns(frames: Mapping[str, pd.DataFrame], *, as_of: date, min_observations: int) ->
  pd.DataFrame`: close-to-close simple returns on **completed** sessions only (keep only sessions
  strictly before `as_of`, the scan's session date; this differs from `features._closed_frame`, which
  keeps `<=`), aligned on the intersection of dates,
  symbols with fewer than `min_observations` aligned returns dropped and reported.
- `shrunk_covariance(returns: pd.DataFrame) -> tuple[pd.DataFrame, float]`: `sklearn.covariance.
  LedoitWolf` on the aligned returns; returns the covariance (daily, per unit notional) and the fitted
  shrinkage. A single-symbol frame gives its variance and shrinkage 0.
- Bars come from the scan's `datasets` (`data.daily`) for names in this scan, and from
  `AlpacaDataProvider.fetch_daily_many(symbols, start, end, adjustment="raw")` for book names not in
  this scan, fetched **once per scan** off the event loop (`asyncio.to_thread`) before the send loop,
  with a `BOOK_SIZING_FETCH_TIMEOUT_SECONDS = 20` bound. Raw Alpaca prices are used for every name
  (never mixed with yfinance-adjusted frames: a frame whose `attrs["adjustment"]` is not raw/absent
  is treated as missing). Futures and crypto are excluded from the book vector and never sized by this
  rule (equities only in PR B; a futures candidate gets status `not_applicable`, factor 1).
- Window: `book_sizing.lookback_sessions` (default 120 completed sessions); `min_observations`
  default 60.

### D3. The `BookSizer` service and the L3 seam

`agentic_trader/execution/book_sizing.py`:

```python
class BookSizingStatus(StrEnum):
    APPLIED = "applied"  # enforce: factor < 1 changed the card
    UNCHANGED = "unchanged"  # factor == 1 (or preview with factor < 1: see would_scale)
    BLOCKED = "blocked"  # enforce: scaled default tier below min units → card not sent
    UNAVAILABLE = "unavailable"  # covariance missing/invalid/timeout; factor 1, never blocks
    NOT_APPLICABLE = "not_applicable"  # off mode, non-equity, drift/catalog card, policy-locked card


class BookSizingDecision(BaseModel, frozen=True):  # pydantic; finite floats only
    status: BookSizingStatus
    mode: str  # off | preview | enforce
    factor: float | None  # None when unavailable/not_applicable
    would_scale: bool  # preview: factor < 1 would have changed the card
    quantity_before: float
    quantity_after: float
    budget_pct: float
    budget_dollars: float | None
    vol_before_pct: float | None  # of risk capital
    vol_after_full_pct: float | None
    vol_after_scaled_pct: float | None
    n_observations: int | None
    shrinkage: float | None
    book_symbols: tuple[str, ...]
    missing_symbols: tuple[str, ...]
    reason: str | None  # type name only on failures (never exception text)
    symbol: str | None  # inputs the D4 cross-check re-derives from
    risk_capital: float | None
    scaled_weight: float | None  # signed dollars after scaling
    shadow: dict[str, Any] | None  # D4 optimiser block, or None
```

`BookSizer(config.book_sizing, bar_source, *, portfolio_cash, min_units)` with
`async prepare(active_positions, datasets, *, as_of, candidates) -> BookContext` (the once-per-scan
covariance and book vector over equity candidates only; never raises; records `status unavailable`
with a reason; skipped by `run_scan` when no native candidate is an equity),
`decide(context, *, candidate, eval_res, risk_capital, dry_run) -> (decision, eval | None)` (pure and
synchronous; `None` means blocked), `async attach_shadow(context, decision) -> decision` (the D4
cross-check) and `journal_block(decision) -> dict`. `BookContext.with_card(...)` adds a recorded
card to the book; an equity card the covariance cannot price makes the context `unavailable`
(`unpriced_card`).

Seam in `run_scan`'s send loop, after `evaluate_candidate` returns an approved `eval_res` and before
`record_signal` (L3: ranking, budgets and the LLM are done; the card is about to be recorded):

1. Exemptions → `not_applicable`: `mode == off`; drift/catalog cards (`candidate.catalog_event`);
   policy-locked cards (`alpha_version`/`alpha_policy`, paper probes) — the honest-cards rule;
   non-equity `asset_class`; dry runs (the simulated empty book has nothing to be consistent with:
   status `not_applicable`, reason `dry_run`).
2. Book vector: every row of the scan's `active_positions` (EXECUTED positions plus this scan's
   earlier cards, exactly the rows `book_gates` sees) with an equity asset class, signed by
   direction, at its recorded `notional_value`. The candidate's proposed notional is
   `eval_res.notional_value` signed by direction.
3. Budget: `book_sizing.max_portfolio_daily_vol_pct × risk_capital`, where risk capital is the
   same `risk_capital(limits.cash, current_equity)` the per-trade budget uses (the smaller of
   configured cash and observed equity).
4. `preview`: journal/provenance only; `eval_res` unchanged; `would_scale = factor < 1`.
   `enforce`: scale **every tier** of `eval_res.sizing_tiers` and `eval_res.quantity`,
   `risk_dollars`, `reward_dollars`, `notional_value`, `effective_leverage` by the factor with the
   tier builder's own rounding (whole shares; `risk = per_unit_risk × qty`, etc.; the recomputation
   lives in `position_sizing.scale_sizing(eval_res, factor, config) -> LLMTradeEvaluation` so the
   arithmetic has one home). A tier that rounds to fewer than `sizing.min_shares` is dropped; if the
   default tier is dropped the card is **blocked** with the fixed outcome
   `RankedOutcome.BOOK_SIZING_BLOCKED = "book_sizing_blocked"` and reason
   `book sizing: portfolio vol 0.91% > budget 0.80%/day`; it spends no budget and falls through
   to the next rank like a policy withhold.
5. The decision rides in `decision_provenance["book_sizing"]` (the dataclass dump), in the
   notification payload as `book_sizing=` (a new `send_signal_alert`/`format_alert_card`/
   `format_terminal_card` keyword; payloads without it render no line), and in the
   `scan_candidates_ranked` journal per candidate as `"book_sizing": {...}` (mode, status, factor,
   would_scale, vol_before_pct, vol_after_full_pct, quantity_before, quantity_after).
6. The card joins the scan's book at its **final** (scaled) notional and risk, so later candidates
   in the same scan see the real book.

The factor never raises size (`factor ≤ 1`), so admission, the tap gate and re-pricing — which only
re-check that a tier fits — are untouched; a scaled card passes them at least as easily. Backtest and
replay never see the rule. The LLM thesis (when `use_llm`) was written for the pre-scale size; in
`enforce` the provenance records `quantity_before` so attribution can see that.

### D4. Shadow optimiser cross-check (evidence only)

When `book_sizing.shadow_optimizer` is true (default true) and the covariance is available, the
closed-form result is re-derived through an independent code path: `ConvexAlphaPortfolioOptimizer`
is run with every weight pinned (equal lower/upper bounds: the book at its current weights and the
candidate at `factor × proposed weight`, all as fractions of risk capital), alpha a unit vector on the
candidate, long-only off, gross limit 10 (never binding), `solver_seconds` 2. The solve runs in `BookSizer.attach_shadow`, off the event loop (`asyncio.to_thread`), after `decide`.
The only free output is
the optimiser's own `portfolio_variance`; the shadow block records `vol_after_scaled_pct` from it next
to the closed-form value, the absolute difference, solver status and seconds. A difference above
`1e-6` of risk capital is logged at WARNING (`book_sizing_shadow_mismatch`) and journaled; the
decision never reads the block. A solver failure or timeout records `{"status": "failed", "reason":
<type name>}`. This keeps the forecast-to-fill contract: the optimiser's output never reaches size or
orders; it is a journaled cross-check of the live arithmetic.

### D5. Config

`BookSizingConfig` (`agentic_trader/config.py`, `extra="forbid"`, mirrors `CardPolicyConfig`):

| Key | Default | Meaning |
| --- | --- | --- |
| `mode` | `preview` | `off` \| `preview` \| `enforce` (YAML `off` → "off", like `card_policy`) |
| `max_portfolio_daily_vol_pct` | `0.008` | budget as a fraction of risk capital per day |
| `lookback_sessions` | `120` | completed sessions of daily returns |
| `min_observations` | `60` | aligned returns required per symbol |
| `shadow_optimizer` | `true` | run D4 |

`config/config.yaml` gains a `book_sizing:` block with these defaults (mode `preview`).

### D6. Card line and reports

Native equity cards show one line under the evidence block, both renderers:

- applied/unchanged (enforce) or preview: `📐 Book: portfolio vol 0.62% → 0.81%/day with this card
  (budget 0.80%; size ×0.70)`; preview with factor 1: `(budget 0.80%; size ×1.00)`; preview with
  factor < 1 adds ` — preview, size unchanged`.
- unavailable: `📐 Book: portfolio vol unavailable (covariance: <reason>)`.
- not_applicable: no line.

`copilot cards outcomes` gains a `book_sizing` block: counts by status, `would_scale` count and the
mean factor among `would_scale`/`applied` rows, and the mean cost-adjusted R of rows that would have
been scaled versus not (descriptive only). `docs/card-evidence.md` gets a "Book-aware sizing"
section; `docs/risk-policy.md` adds the rule to its table with the note that it is applied at card
construction only (one implementation, consumed by one layer, by design: admission never shrinks a
tier the operator was offered); `docs/production.md` documents the config and the line; CLAUDE.md
contract 1 gets one clause; `docs/alpha-roadmap.md` item 1 status.

## Non-goals

No change to the per-trade risk budget, notional caps, counts or correlation-group rules; no change
to admission, tap gate, re-pricing, backtest or replay; the optimiser stays shadow-only; no migration
(head 008); no PENDING cards in the book (the book is what `book_gates` sees); no futures/crypto
sizing; no corporate-action adjustment of bars (raw Alpaca; the limitation is documented); no
automatic move to `enforce`.

## Tests

- `risk/book_vol.py`: closed-form table (zero book → factor from candidate variance alone; book
  already over budget → 0; negatively correlated candidate → factor 1 even when its own vol exceeds
  budget; exact root check against a numeric solve; monotone in budget; never > 1; non-finite →
  ValueError).
- `covariance.py`: completed-session rule, alignment/intersection, min-observation drop, Ledoit-Wolf
  PSD, single-symbol path, yfinance-adjusted frame treated as missing.
- `book_sizing.py`: exemptions; preview leaves `eval_res` identical and records `would_scale`;
  enforce scales every tier with the tier arithmetic and drops sub-minimum tiers; blocked default tier
  → `book_sizing_blocked`, no budget spent, next rank considered; unavailable never blocks; later
  cards see the scaled notional; dry run `not_applicable`.
- `run_scan` integration on the `shadow_desk` fixture (GBM dailies): a correlated open position makes
  the second card's factor < 1; provenance, notification payload, journal and outcomes blocks present;
  old payloads render without the line; ranking/sending identical between `off` and `preview`.
- Optimiser shadow block: recorded, never read; solver failure → failed block, decision unchanged.
- Config: defaults, `off` mapping, `extra=forbid`.
