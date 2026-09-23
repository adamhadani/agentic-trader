# Dynamic suggestion universe — design (WS3)

**Approved:** September 23. WS3 of the [alpha expansion survey](../../alpha-expansion-survey-2026-09-23.md);
the operator said "go for them in order and verify as you go".

## Problem

Suggestion scans only see the static 159-name universe. On September 23 the 14:35 scan
found only two setups, and both were duplicates. Liquid names that are in play today
(unusual activity, post-earnings moves) are invisible to the desk.

## Decision

Each **scheduled suggestion scan** adds up to `max_symbols` *dynamic* US equities,
taken from Alpaca's screener and filtered deterministically. The static universe and
every other scan are unchanged. Per CLAUDE.md, the intraday job keeps scanning only
`non_universe_contracts`, and manual scans are unchanged.

### Sources (read-only GETs; failure → the scan proceeds with the static universe)

- `most_actives`: Alpaca `MostActivesRequest(by="trades", top=most_actives_top)`.
  Ranking by trade count rather than share volume keeps sub-penny share counts from
  dominating.
- `movers`: Alpaca `MarketMoversRequest(top=movers_top)`, gainers and losers.

### Filters, in order, each counted per reason

1. Symbol shape `^[A-Z]{1,5}$`. This excludes warrants, units, rights and share
   classes with dots.
2. Not already in the scan's static contracts.
3. Asset metadata comes from the Alpaca asset list, fetched once per NY date and
   cached in memory. The asset must be class `us_equity`, status `active` and
   `tradable`, on exchange NYSE, NASDAQ, ARCA, AMEX or BATS.
4. The name must not contain a leveraged or inverse fund marker (case-insensitive:
   `2x`, `3x`, `-1x`, `ultra`, `ultrapro`, `bull`, `bear`, `leveraged`, `inverse`,
   `daily target`). ETFs in general are allowed only if they pass everything else.
5. The screener's last price must be at least `min_price` (10.0).
6. Take the first `max_candidates` survivors (40), most-actives order first, then
   movers ordered by absolute percent change.

### Liquidity gate (after the scan's normal fetch, before strategies)

Dynamic names need a median of the last 20 daily `Close × Volume` of at least
`min_median_dollar_volume` (50,000,000), computed from the daily bars the scan already
fetched. They also pass the existing hourly-coverage gate. At most `max_symbols` (20)
dynamic names that pass are evaluated by strategies, in source order. Names that fail
are counted in `summary["dynamic_excluded"]` with a reason.

### Treatment of dynamic names

- They get a synthetic in-memory contract entry for this scan only: `asset_class`
  EQUITY, multiplier 1, tick 0.01, `ticker` equal to the symbol, `name` from the asset
  record. `config.contracts` is never mutated.
- **Correlation grouping.** Dynamic names share one correlation group, `"dynamic"`, for
  this scan's per-group card cap (`max_cards_per_group_per_session`). At most one
  dynamic card per session also counts against today's cards on dynamic names, because
  a recorded signal whose provenance has `dynamic: true` belongs to group `dynamic`.
- **Gates.** Every existing gate applies unchanged: dedup, earnings blackout, macro,
  sizing, session, LLM, budget and card freshness.
- **Shadow evidence.** The cross-section stays `universe.groups` only, which is study
  parity. A dynamic candidate's cross-sectional features are therefore NaN (null).
  Journaled candidates and signal provenance carry `"dynamic": true` and
  `"dynamic_source"`.
- **Audit.** Each scan appends one `dynamic_universe_built` domain event with:
  - the sources' raw counts;
  - the per-reason filter counts;
  - the members with source and rank;
  - the liquidity exclusions.

  It is written under the scope lock and contained, following the `record_health`
  pattern. The digest line adds "N dynamic names scanned".

### Config (`universe.dynamic`)

```yaml
universe:
  dynamic:
    enabled: true
    sources: [most_actives, movers]
    most_actives_top: 100
    movers_top: 50
    max_candidates: 40
    max_symbols: 20
    min_price: 10.0
    min_median_dollar_volume: 50000000
```

When `enabled: false`, suggestion scans are byte-for-byte unchanged.

## Constraints

- No migration. `kind` is a String column and provenance is JSON text.
- Blocking SDK calls run off the event loop (`asyncio.to_thread`) with the existing
  bounded clients.
- The intraday job and manual scans never get dynamic names.
- A screener or asset-list failure is logged once
  (`event="dynamic_universe_unavailable"`) and the scan continues with the static
  universe.
- Live broker safety is unchanged. Admission already falls back to multiplier 1 for an
  unconfigured equity, and positions and closes do not need a contract config for
  equities. Verify this in tests.

## Testing

- **Pure filter:** symbol shapes, asset metadata, leveraged-name markers, price,
  ordering and caps, reason counts.
- **Liquidity gate:** median dollar volume boundary, ordering and cap.
- **Scan integration** with fake screener and asset sources:
  - dynamic names are scanned only when `shadow_evidence`/suggestion scans run;
  - manual scans, the intraday job and `enabled: false` are unaffected;
  - the dynamic correlation group caps cards;
  - a failure falls back to the static universe;
  - the journal event is written;
  - provenance carries the dynamic flag;
  - a dynamic card can be executed through the tap path (freshness plus a real
    `EntryExecutionService`) with multiplier 1.
- **Deployment verification:**
  - a dry funnel run on real data lists the dynamic members and reasons;
  - after deploy, the next suggestion scan journals `dynamic_universe_built` and the
    digest counts dynamic names.
