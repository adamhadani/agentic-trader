# Native card supply — November 7, 2026

**Finding.** The native card stream dried up because live scans read **IEX** bars. Host sleep and the market
played only a small part. Replayed on the same tape, the unchanged strategy engine finds about three times as
many setups on **SIP** bars, the feed every piece of card evidence was measured on. The fix: scan bars move to
delayed SIP ([spec](superpowers/specs/2026-11-07-scan-sip-bars-design.md)).

## Symptom

- **Native cards:** 9 native cards in the 8 sessions from Oct 14 to Oct 23, then 2 in the 9 sessions from
  Oct 26 to Nov 6. Three of those nine were lost to the halt.
- **Candidates:** 7 ranked candidates in the 14 days to Nov 6.
- **Last scan:** the scan of Nov 6 found no setup in 157 names.
- **Catalog cards** (PEAD, insider watch) kept arriving at one or two a day.

## Missed scans: sleep, halt and slot spending

64 scheduled suggestion slots (10:35 and 14:35 ET) fell on weekdays from Sep 24 to Nov 6. 20 have no
`scan_candidates_ranked` event:

| Cause | Slots | Evidence |
| --- | --- | --- |
| Trading halt from the Oct 26 wake-up `ConnectionError` (lifetime review) | Oct 27 ×2, Oct 28 ×2, Oct 29 10:35, and Oct 29 14:35 (ran late at 14:49 and was halted) | Daemon log, "Trading scan halted: Emergency kill switch active (Lifetime review …)" |
| Host asleep | Sep 24 14:35, Sep 28 10:35 | APScheduler "Run time of job … was missed"; no journal activity in the slot |
| Before PR #111 (Oct 9), a scan that found nothing was not journaled | Sep 28 – Oct 1 | No funnel field exists before Oct 22 |
| Before Phase 0 (#129, Oct 22), a swing scan or a daemon start could spend session slots, and the slot's scan had no budget | Oct 9 – Oct 22 (9 slots, daemon awake) | Fixed by Phase 0's journal-only swing job; there is no dated log to confirm each slot |

Every slot from Oct 30 to Nov 6 ran (12 of 12). The power log starts on Oct 31 and shows no sleep during a
scan since then. Sleep cost a few September scans, but it does not explain the recent drought.

## Setups when a scan does run: the feed

The replay ran the unchanged live strategy engine (`research.setups.replay.replay_symbol`, frames exactly as
live `fetch_data` builds them) on the 157-name universe:
- at every 10:35 and 14:35 ET decision from Sep 1 to Nov 6 (96 decisions);
- with raw adjustment and no de-duplication, comparable with the journal's `raw_setups`;
- once on SIP bars and once on IEX bars.

The output is in `~/agentic-trader-research/native-supply-20261107`.

| Feed | Sep 1 – Oct 21 (72 scans) | Oct 22 – Nov 6 (24 scans) | All 96 | Scans with none |
| --- | ---: | ---: | ---: | ---: |
| SIP | 3.67 per scan | 3.00 per scan | 3.50 | 11% |
| IEX | 2.79 per scan | 1.00 per scan | 2.34 | 24% |

- **The live pipeline matches the engine.** On the 17 suggestion scans that ran from Oct 22, live
  `raw_setups` total 16, against 17 replayed on IEX and 51 on SIP. Nov 6 had 1 and 2 live against 7 and 12 on
  SIP.
- **Setup kinds:**

  | Feed | TREND_PULLBACK 4h | SQUEEZE_BREAKOUT 1h | SQUEEZE_BREAKOUT 4h |
  | --- | ---: | ---: | ---: |
  | SIP | 286 | 47 | 3 |
  | IEX | 214 | 11 | 0 |

- **The market played a small part.** SIP supply fell from 3.67 to 3.00 a scan; IEX fell from 2.79 to 1.00.
- **The reference rate.** The 2021–2026 SIP replay on the same 157 names, from the bracket counterfactual,
  ran at about 4–5 setups a scan in 2024–2025 and about 2 in 2022. Live IEX since Oct 22, at 0.94, is below
  even the 2022 bear market.

### Why IEX differs

IEX carries a small share of consolidated volume. Its hourly bars are a thin sample of trades, so the highs,
lows and closes behind the 4h bars, RSI, ATR and the pullback distance differ from the consolidated tape.
TREND_PULLBACK needs:
- a daily uptrend (EMA50 above EMA200);
- a pullback to within 0.5 ATR of the 4h EMA20;
- RSI dipping to 40–45.

It sits on thresholds that a thin sample moves across. Squeeze breakouts also read volume against its 20-bar
mean.

## Consequence for the evidence

The card-stats replay prior, the setup base rates and the bracket counterfactual were all measured on SIP bars.
The live stream, built on IEX, was a thinner and differently selected population than the one that evidence
describes. Scanning on SIP makes the live cards the population the evidence is about. It changes no threshold,
strategy or gate.
