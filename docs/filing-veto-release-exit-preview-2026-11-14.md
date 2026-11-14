# Filing veto and release exit: preview week 2026-11-07 → 11-14

Both rules shipped in `preview` on 2026-11-07 ([production](production.md#filings-capture),
[release exit](production.md#release-exit)). This records the journaled evidence read for 11-09 → 11-12, the 11-13
session (read on 11-14, below) and the decision to ship `enforce`. The evidence was read from the production
journal, read-only.

**11-13 session.**
- **Capture:** 3 of 3 passes, 0 failed. 157 names were requested and 130 covered, rising to 158 and 131 at the
  14:05 pass once KDP was held.
- **Scans:** 3 universe scans (2 suggestion, 1 journal-only swing) ranked 10 long candidates.
  - 9 were `ok` and 1 `uncovered`; 0 would block.
  - The `uncovered` candidate was KDP, a dynamic-universe name. It was sent as a native card at 10:35 and
    auto-entered.
  - The 14:05 capture then found a KDP 424B5 filed 11-12, inside the offering window. Under the shipped config
    that match is labelled, not refused.
- **Release exit:** 1 pass (`preview`, 15:33 ET) over 4 native positions (UNH, CSX, KDP, MSFT). All were `none`
  and `live_verified`, with 0 `would_close` and 0 `unknown`.

## What was measured

- Whether the EDGAR filings capture ran and covered the names the scans use.
- Which candidates and cards the filing veto would have refused, by category and universe.
- Whether the release exit's passes ran, what they decided and whether any close or notice was owed.

## Filing veto evidence, 11-09 → 11-12

| Measure | Result |
| --- | --- |
| Capture passes | 12 of 12 (3 slots a day); each 157 requested, 130 covered, 27 unmapped, 0 failed |
| `filings_capture` readiness | ready in all 1,724 observations 11-07..11-13; no `filings_capture_failed` log lines |
| Ranked long candidates with a `filings` block | 31, across 17 universe scans (8 suggestion, 9 journal-only swing), 15 distinct symbols |
| Status | `ok` 27, `uncovered` 4 (EWC, EWU: country ETFs EDGAR does not map), `stale` 0, `unavailable` 0 |
| Would block | 1: CART, adverse 8-K Item 2.05 filed 2026-10-26, in a journal-only swing scan on 11-11 (never a card); suggestion scans 0 of 12 |
| Sent native cards | 6, all `ok` and clear |
| Sent catalog cards | 3 (insider watch WCC, GFS; PEAD CRWV), all `uncovered` |
| Dynamic universe | 31 distinct dynamic names scanned (20 per suggestion scan), none on the capture list; 0 dynamic candidates, 0 dynamic cards |
| Offerings | 32 distinct 424B5 (no 424B4); by document name 5 debt (PG, PNC), 0 equity, 0 convertible, 27 unclear |
| Latent set | 6 or 7 of 130 covered names inside a window on any pass: always CART, NKE, SBUX (8-K Item 2.05) plus 3 or 4 megacap offering names a day (META, ORCL, GOOGL, BKNG, QCOM) |

The week refused nothing it should not have, and almost nothing at all: the rule is a low-rate guard on the static
universe. The offering matches are mostly unclear by document name and the debt ones are bond issues, for which
the cited negative drift (convertibles, equity) does not apply.

## Release exit evidence, 11-09 → 11-12

| Measure | Result |
| --- | --- |
| Passes | 4 of 4 (11-09..11-12), all `preview`, journaled 15:30–15:32 ET |
| Entries | 14: 11 native `none`, 3 `exempt` (BLK, PEAD) |
| Decisions | 0 `would_close`, 0 `unknown`, 0 closes, 0 notices, 0 `release_exit_*` failure log lines |
| Nasdaq 403 (11-10 10:38 → 11-12 07:50 ET) | the 11-10 and 11-11 passes had `live_verified: false` and decided from the capture alone (11-11 used the 11-10 capture) |
| Held names | BLK, TMO, UNH, CSX: none reports within the captures' ~28-day horizon, so `none` was right |

The close path and the preview notice were never exercised: no position qualified. On 11-12 the BLK holding close
(a different path, the 20-session lifetime) refused on a stale Alpaca order list, fixed by #150 and deployed 11-13.
The release exit uses the same close service, which is why its `enforce` follows that fix.

## Decision

- `filing_veto.entry_block: enforce` for `late_filing` and `adverse_8k` (`filing_veto.enforce_categories`).
  `offering` stays journal-only: 424B5 matches are mostly unclear and the debt ones are bond issues, while the
  drift evidence is for equity and convertibles. An unenforced match is journaled and its card shows
  `Filing (not enforced)`.
- Catalog (`pead`, `insider_watch`), paper-probe and alpha-versioned cards are exempt at the evaluator and tap
  gates, the release exit's scope, so a frozen probe's entry population does not change.
- `release_exit.mode: enforce`.
- The morning operations summary lists the release exit's gaps: a missing pass, `unknown` native positions and
  closes never notified, each with `/close SIGNAL_ID`.

## Open gap

Dynamic-universe names are uncovered by the filings capture by design in v1: they journal `uncovered` and are
never refused. 11-09 → 11-12 had no dynamic candidate or card.

On 11-13 the first dynamic native card (KDP) was sent and auto-entered `uncovered`. The capture covered KDP only
once it was held, and then found an 11-12 424B5. That is an offering, journal-only under the shipped config, so
coverage would have changed the card's label but not the entry. The gap is now observed and not closed. It is the
next coverage item: request filings for the scan's dynamic names before ranking.

## Rollback

- Veto: `filing_veto.entry_block: preview` (labels only), or add `offering` to `filing_veto.enforce_categories`
  to widen it.
- Release exit: `release_exit.mode: preview`, then a controlled restart of the daemon.
