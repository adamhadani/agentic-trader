# Earnings blackout: fall back to the dated capture, and refuse when neither source can vouch

## Why

From about 10:38 ET on 2026-11-10 until about 07:50 ET on 2026-11-12, Nasdaq's CDN returned HTTP 403 to the desk's Python HTTP clients. That is about 45 hours.

- `pead_decision` was unavailable on 11-10 and 11-11, and the 11-11 earnings capture failed all day (`CaptureIncomplete`).
- The equity earnings blackout **failed open** for the whole outage. It is implemented twice, with the same fail-open code:
  - in the scan (`RiskEvaluator`, `agent/evaluator.py` step 2b);
  - at tap/auto-entry time (`TradingCopilot._earnings_blackout_reason`, `agent/copilot.py`).
- When `next_earnings` raises, both build `EarningsLookup(event=None, verified=False)`. That applies no blackout and only adds an "Unverified" card note.
- A native card, auto-entered on paper, could therefore have been sent days before a report.

The daily capture (`earnings/calendar/<D>`, 06:00 ET, all or nothing, 28-day horizon) already holds the forward calendar. The release exit already reads it beside the live lookup (`EarningsCaptureStore.reports_between`). On 11-10 and 11-11 the 11-10 capture was fresh and covered every date the blackout needed.

**Operator decision, 2026-11-10:** stricter blackout if the block persisted. On 2026-11-12 the operator approved this variant: fall back to the capture, and refuse only when neither source can vouch.

## What changes

### 1. One earnings resolver (`agent/earnings.py`)

`async def resolve_earnings(symbol, now, horizon_days, *, live, captures) -> EarningsLookup`, used by both call sites:

1. **Live lookup first** (`live.next_earnings`), when `live` is not None.
   - An exception is caught and logged with its type name only (`event: earnings_lookup_failed`).
   - A found event is returned as is, verified or not: a found report always blocks, as today.
   - A verified result is returned as is.
2. **Fallback.** If the live lookup raised, or returned unverified with no event, and `captures` is not None:
   - Call `captures.reports_between(today, horizon_end, today=today)`, using the New York date. An exception there counts as None, logged by type name.
   - When it returns `CapturedReports`, scan its dates in order for the symbol with the live rule: an ahead report is a date after today, or today with a timing other than `PRE_MARKET`.
   - Return `EarningsLookup(event or None, verified=True, horizon_end, source="capture", capture_date=...)`.
3. **Otherwise**, return the unverified lookup: `event=None, verified=False, horizon_end=today + horizon_days`.

**`EarningsLookup`** gains two fields: `source: Literal["live", "capture"] = "live"` and `capture_date: date | None = None`.

**The capture source** is a small Protocol in `agent/earnings.py` (`reports_between(start, end, *, today)`). `CapturedReports` is imported under `TYPE_CHECKING`, so `data/earnings_capture.py` keeps importing from `agent/earnings.py` without a cycle.

### 2. Unknown-calendar policy

**Config.** Add `risk.earnings_calendar_unknown: Literal["open", "closed"] = "open"`, mirroring `risk.macro_calendar_unknown`. `config/config.yaml` ships `closed`.

**Reason.** `earnings_blackout_reason(lookup, symbol, now, blackout_days, *, unknown="open")`:
- Unchanged for a found event.
- New: when `unknown == "closed"`, `blackout_days > 0`, `lookup.event is None` and `not lookup.verified`, it returns `{symbol}: earnings calendar unavailable and no fresh capture covers the next {blackout_days} days (policy closed)`.

Both call sites prefix it with `Earnings Blackout: ` under rule `RiskRule.EARNINGS_BLACKOUT`, as today.

**Unchanged:**
- A blackout of 0 days disables the gate entirely.
- A non-equity is never evaluated.
- A desk with no live calendar (`earnings_calendar is None`) skips the gate as today, so it never refuses everything.

### 3. Card note

`earnings_note(...)`, when `lookup.source == "capture"`, appends ` (calendar capture {capture_date})`, for example `No report within 7 days (calendar capture 2026-11-12)`. The unverified note is unchanged and only reachable under `open`.

### 4. Call sites and wiring

**`RiskEvaluator`:**
- Gains the constructor keyword `earnings_captures` (the capture Protocol, or None).
- Step 2b calls `resolve_earnings` and passes `limits`' unknown policy. Add the field to `risk/limits.py` beside `earnings_blackout_days`, with the same derivation.
- The old fail-open `try/except` and its message-bearing log are removed.

**`TradingCopilot._earnings_blackout_reason`:**
- Calls the same resolver with the same capture source and passes `config.risk.earnings_calendar_unknown`.

**Wiring:** `TradingCopilot` builds one `EarningsCaptureStore(self.db.workflows, default_capture_directory())` and passes it to the release exit (as today) and to the evaluator.

## Unchanged

- The live Nasdaq client, its headers and its HTTP behaviour (no fingerprint workaround).
- The capture worker, its schedule and payload.
- The release exit and its decision rule.
- `earnings_days_out`.
- The blackout length (7).
- Admission, which does not evaluate earnings.
- Schema head `008`.
- No new provider calls: the fallback reads the journal only.

## Docs

- `docs/risk-policy.md`, the earnings blackout section: the resolver order (live, then capture, then unverified) and `earnings_calendar_unknown`.
- `docs/production.md`:
  - the earnings-capture section: the capture is now also the blackout's fallback;
  - an incident note: the Nasdaq 403, 11-10 10:38 → 11-12 ~07:50 ET, about 45 h, blackout open throughout.
- `CLAUDE.md`, operations contract 8, earnings-capture sentence: the blackout falls back to the capture when the live lookup fails. With neither source, `risk.earnings_calendar_unknown` `closed` (shipped) refuses equity entries.

## Rulings (controller)

- **Fallback, not union.** A verified live lookup stays authoritative because it is newer than the morning capture. The capture is read only when the live lookup cannot vouch.
- **Config ships `closed`, code default `open`.** This mirrors the macro calendar and gives a config-only rollback.
- **One resolver for both call sites** replaces the duplicated fail-open code, in line with "one implementation per entry rule".
- **Refusing with neither source** may stop native equity cards for a whole outage that lasts longer than the capture's 4-day age limit. That is the intended trade.
