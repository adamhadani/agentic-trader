# Entry trade freshness: a separate, longer age limit for the IEX last trade

## Why

Since 2026-11-10 every paper auto-entry has been refused at preflight with "Market trade is stale, expired or clock-skewed; request fresh approval". In each case the quote was about 1 s old:

| Signal | Time (ET) | Last IEX trade age | Quote age |
| --- | --- | --- | --- |
| COLB | 11-10 14:39 | 68.6 s | 1 s |
| NSC | 11-11 14:39 | about 108 s | 10 s |
| GEHC | 11-12 10:39 | 60.5 s | 1 s |

**The rule.** `assess_entry_capacity` (`execution/capacity.py`) applies `execution.entry_quote_max_age_seconds` (60 s) to both the latest trade and the latest quote.

**Why it fails.** The desk's real-time feed is IEX, a small share of US volume, so the last IEX print for a liquid mid or large cap is routinely more than a minute old.

**Probe** (read-only, 2026-11-12 15:07 ET, 197 names: the configured universe plus the latest dynamic universe):

| Age | Trade | Quote |
| --- | --- | --- |
| p50 | 30 s | 2 s |
| p75 | 73 s | — |
| p90 | 160 s | 24 s |
| p95 | 281 s | — |

- Trades older than 60 s: 30.5% of names. Older than 120 s: 14.2%. Older than 180 s: 7.1%. Older than 300 s: 4.1%. Older than 600 s: 2.0%.
- Quotes older than 60 s: 1.5% of names.

**What changed.** #144 (2026-11-07) widened native supply toward mid caps, which raised the exposure. Before the COLB card, four of five auto-entries were accepted.

**What the last trade does in admission today:**
1. The preflight drift check against the approved limit (`entry_max_price_drift_pct`, 1%), in `EntryExecutionService._preflight`.
2. `exposure_price = max(entry, ask, trade)` in `assess_entry_capacity`.
3. A freshness check.

Liveness, such as a halted or stale market, is already evidenced by the quote (60 s) and the broker clock (60 s).

## What changes

1. **Config.** Add `execution.entry_trade_max_age_seconds: float = Field(default=300, gt=0, le=900, allow_inf_nan=False)`. `config/config.yaml` ships `300.0` with a one-line comment: IEX prints are sparse on mid caps, and the quote keeps the 60 s limit.
2. **`assess_entry_capacity`:** the latest-trade freshness check uses `entry_trade_max_age_seconds`. The following are unchanged:
   - the quote, account/book and receipt checks, with their limits;
   - the clock-skew tolerance (`BROKER_CLOCK_SKEW_TOLERANCE_SECONDS`, 1 s);
   - every refusal text.

   The check runs at preflight and again in `begin_submission`, as today.
3. **Preflight drift.** `_preflight` keeps the trade-price check first, with the same message. It then also refuses when the fresh quote midpoint, `(bid + ask) / 2`, differs from the approved limit by more than `entry_max_price_drift_pct`. The message is: `Market quote midpoint moved to {mid:g}; original approved limit {limit:g} retained.` This check runs before the macro check, like the trade check, and returns the context.
   - With an older trade allowed, the quote catches a move that the trade missed.
   - The quote's bid and ask are already validated as positive and not crossed (`EntryQuoteEvidence`).
4. **Docs:**
   - `docs/durable-execution.md`: add an `entry_trade_max_age_seconds` row beside `entry_quote_max_age_seconds`, plus a short incident note (11-10/11/12, three refusals).
   - `docs/entry-capacity.md`: wherever evidence freshness is described, say that the trade has its own 300 s limit and the quote 60 s.
   - `CLAUDE.md`, `AGENTS.md`: change them only if a statement there becomes false.

**Unchanged:**
- the quote limit (60 s), the evidence limit (30 s) and the clock check;
- POST is never retried, and nothing is resized or replayed;
- lock order;
- the simulator path;
- schema head `008`;
- the drift threshold (1%).

## Rulings (controller)

- **300 s.** 60 s refuses about 30% of names at a random moment. 300 s refuses about 4%, which are names with no IEX print in five minutes, and those are reasonably held back. Above 900 s is rejected by validation.
- **Trade-only change.** The quote keeps 60 s because it is the liveness evidence.
- **Midpoint, not ask, for drift.** The IEX book can be thin, and the midpoint is less sensitive to a one-sided wide quote. `exposure_price` already includes the ask for sizing.
- **No retry or re-read** of market evidence after a refusal. The auto-entry sweep stays one attempt per card.
