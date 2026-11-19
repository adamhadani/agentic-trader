# A priori investor-day run-up v1 — November 19, 2026

**Result: `failed` (P1, P2, P3 and P5).** The rule buys a name on the second session after its first 8-K that
announces an investor, analyst or capital markets day, and sells at the close of the session before the event.
Net of a beta-matched SPY and per-name cost, it returns **−0.022R per leg** (318 legs, ci90 [−0.105, +0.061]).

Against 5 matched same-session names, the legs differ by **+0.018R** (302 pairs, ci90 [−0.058, +0.095]).
A planted return of half the published effect (1.85% of the decision price, a mean shift of 0.33R) was
detected. The minimum detectable effect on the paired test is 0.21R. A run-up of the published size
(Cabrera, Kolokolova and Zhang 2025: +3.70% from announcement to the eve of the event) would have shown, so
this is a powered null, not an inconclusive read.

This was a development read on a spent window with today's survivors: it could fail but not pass. It failed,
so the investor-day run-up is closed on this source. There is no live 8-K capture, no prospective read and no
card.

## Protocol and run

- **Entry.** `config/research/apriori/idday-v1.json`, SHA-256 `0451e11a…f989`, frozen at commit `4e87cc3`
  before any price was read. The [spec](superpowers/specs/2026-11-19-apriori-idday-design.md) has the design.
  The pinned inputs:
  - the pooled protocols `cohort3-events-v1.json` (`b692d7f9…`) and `cohort-xu-insider-v1.json` (`33445bda…`),
    and their cohorts v3 (`8c0d807a…`) and xu (`6ece7c54…`): 2,156 names, ranks 1–2,156 of the screen-v3
    ranking;
  - the cached cubes for v3 (`75dbb80a…`) and xu (`f9c4c5ea…`), merged as `a00c39b4…`;
  - the Nasdaq page archive (`9273e40a…`) and both cohorts' corporate actions (`8a547b06…`, `9f38dffd…`);
  - the SEC 8-K full-text acquisition (`content_sha256` `4e1da43c…`), the `idday-parser-v1` event table
    (`events_sha256` `c1c6bd48…`) and the parser check (`d186f3f5…`).
- **Acquisition.** 2026-11-19, 16:05 to 18:13 ET with a retry from 18:14 to 18:37, at 2 requests per second
  with the operator's contact address.
  - EDGAR full-text search for "investor day", "analyst day", "capital markets day", "investor meeting" and
    "analyst meeting" in 8-Ks filed 2016-01-01 → 2026-07-31.
  - Coverage: 2,135 issuers, 5,958 hits and 5,939 documents.
  - 1,881 issuers are visible (domestic 10-K/10-Q filers).
  - No search was capped.
  - 13 cohort symbols are not in SEC's ticker file.
  - The first pass left 22 issuers with a search or document failure; the retry recovered all but one (UAL, two
    documents).
- **Parser check.** A seeded sample of 60 candidate legs, drawn from issuers outside the 50 feasibility names
  and labelled by hand, had **2 errors** (precision 96.7%, limit 9).
  - TE Connectivity's sentence gave a fiscal quarter-end, not the meeting date.
  - Orthofix's filing announced a postponement.
- **Run.** 2026-11-19, 18:43:25 to 18:46:10 ET, at clean revision `4e87cc3`, run `3ec2ddf1`.
  - Everything came from cache except SPY hourly bars, fetched once into the run directory.
  - The trading calendar has 2,681 sessions (SHA-256 `e829346d…`, the run-up leg's).
  - Output is in `~/agentic-trader-research/apriori-idday-v1-20261119`. Peak memory was 5.9 GB.
- **Bootstrap.** A stationary session block (mean block 10), 2,000 draws, seed 20261120. The issuer-cluster
  bootstrap used 2,000 draws, seed 20261121.

## Event funnel

| Step | Count |
| --- | ---: |
| Parsed events | 1,460 |
| Event-only (first seen on or after the event day; never legs) | 832 |
| Announced ahead | 628 |
| Lead under 1 session (D after X) / over 63 sessions | 58 / 93 |
| A session outside the merged cube's calendar | 38 |
| Archive report inside the hold (`earnings_in_hold`) | 69 |
| Second event of the issuer within 63 sessions | 2 |
| **Candidate legs (price-free gates)** | **368** (287 with earnings, 81 standalone) |
| Identity break / not cube-eligible on D | 17 / 32 |
| **Legs** | **319** (248 with earnings, 71 standalone) |
| Labelled with a finite net excess | 318 |
| Paired with 5 labelled controls | 302 |
| Pseudo-events labelled / paired | 296 / 280 |

- **Unpaired legs.** 13 had fewer than 5 candidates, and 3 lost a control cell to missing bars. Only 4 of the
  16 are standalone, so the quiet window the standalone controls need (`[D − 30, X]` with no report) was less
  binding than the spec feared.
- **Issuers.** 232 symbols carry a labelled leg. The most legs for one issuer is 10 (Intuit).
- **Item 2.02.** 79% of first announcements sit in an earnings 8-K, which is why most legs are `with_earnings`.

## Rules

| Rule | Value | 90% CI | Holds |
| --- | ---: | --- | --- |
| P1: legs' mean `net_excess_r_v1` | −0.022R | [−0.105, +0.061] | ❌ |
| P2: legs − matched controls (`r_cost`) | +0.018R (controls +0.058R) | [−0.058, +0.095] | ❌ |
| P3: 1%-trimmed mean; mean from 2023-01-01; issuer-cluster P2 | −0.034R; −0.094R; +0.018R | —; —; [−0.081, +0.126] | ❌ |
| P4: ≥ 250 legs, ≥ 60 from 2023 | 318; 131 | — | ✅ |
| P5: legs' paired difference − pseudo-events' | +0.076R (+0.018 vs −0.058) | [−0.081, +0.240] | ❌ |
| Negative control: pseudo-events' P2 must not hold | −0.058R | [−0.198, +0.076] | ✅ (does not hold) |
| Planted 1.85%: P1 and P2 on the pseudo-events | +0.218R; +0.269R | [+0.083, +0.347]; [+0.123, +0.406] | ✅ detected |

- P1 covers 265 decision sessions, one-sided p = 0.66; P2 covers 251, p = 0.34; the issuer-cluster P2 uses 223
  issuers.
- The planted shift averaged 0.33R per pseudo-event (range 0.06–0.84R).
- The pseudo-events' P2 bootstrap SD is 0.084R, so the MDE is (1.645 + 0.84) × 0.084 = 0.21R.

## Descriptives (never decisive)

### Where the −0.022R comes from

At 15 bp per side the legs return **+0.073R** before the market is removed. `net_excess_r_v1` subtracts beta
× SPY over the same hold (mean beta 1.14; the legs held through a rising tape) and uses the per-name cost (median
8.5 bp per side), which leaves −0.022R. The matched controls earned +0.058R on the same sessions. On
`net_excess_r`, legs minus controls is +0.042R (controls −0.052R, beta 1.06): the same small, noisy gap as P2.

| Cost per side | 5 bp | 15 bp | 25 bp |
| --- | ---: | ---: | ---: |
| Legs' mean `r_cost` | +0.108R | +0.073R | +0.039R |

The same-firm pseudo-events (the firm's ordinary earnings release, or a quiet session, with no investor day)
returned −0.105R net, so the legs did beat them by about 0.08R. P5 measures exactly that, with ci90 [−0.081,
+0.240]. It is not distinguishable from zero with 302 pairs.

### Splits

| Split | n | `net_excess_r` | paired diff (`r_cost`) |
| --- | ---: | ---: | ---: |
| `with_earnings` (announced in an earnings release) | 247 | −0.046R | −0.016R |
| `standalone` | 71 | +0.060R | +0.138R |
| Cohort v3 (ranks 401–1,400) | 172 | +0.012R | −0.012R |
| Cohort xu large (ranks 1–400) | 87 | +0.057R | +0.213R |
| Cohort xu small (ranks 1,401–2,156) | 59 | −0.239R | −0.178R |
| Hold 1–9 sessions | 106 | −0.070R | +0.023R |
| Hold 9–21 sessions | 106 | −0.149R | −0.111R |
| Hold 21–61 sessions | 106 | +0.152R | +0.153R |
| First announcement lists Item 2.02 / does not | 251 / 67 | −0.049R / +0.077R | — |
| Excluding the five most frequent symbols (INTU, CMPR, IDXX, NOW, IOT) | 285 | −0.001R | +0.038R |
| First leg per symbol only | 232 | +0.024R | +0.058R |

- **Standalone and large-cap.** The positive cells are a standalone announcement (a dedicated 8-K, not
  an earnings release), a large name and a long hold. Each has at most 106 legs and a per-leg SD near 0.95R, so
  their standard errors are about 0.09–0.11R. None is distinguishable from zero, and reading them as an effect
  would be a forking-path claim. They are a hypothesis for a prospective read at most, never a reason to revisit
  this one.
- **The earnings confound.** In with-earnings legs, post-earnings drift and the run-up are mixed. Those legs
  show nothing (−0.016R against reporting peers), so neither effect survives that control.
- **By year.** `net_excess_r` swings from −0.23R (2017) to +0.41R (2016, 8 legs), with no trend. 2023–2026 is
  −0.09R.
- **Keywords.** "Investor day" carries 240 legs at +0.007R. "Analyst day" carries 34 at −0.30R. The other
  keywords have 1–18 legs each.

### Exits

- 94 legs (30%) hit the stop, mean −1.05R.
- 224 timed out at the close before the event, mean +0.54R.
- Mean hold 16.8 sessions; mean time in the trade 13.0 sessions.
- **Without the stop** (adjusted close of X minus the decision price, net of beta × SPY, in the same R): legs
  −0.018R, pseudo-events −0.103R; standalone +0.102R, with earnings −0.052R. The stop does not hide a run-up.

### Dispersion and power

- Per-leg SD 0.95R, 1.2 legs per decision session, session design effect 1.09.
- Mean block 10 is short against holds of up to 63 sessions, but legs rarely overlap: the design effect is
  near 1, so the session-block bootstrap understates little.
- At the spec's MDE convention ((1.645 + 0.84) × SD / √n, times the design effect), resolving +0.05R at this SD
  would take about 2,400 legs: roughly 75 years of this universe's announcements at about 32 legs a year.

## Known limits

- **Survivorship.** Membership is today's 2,156 names. This favours the rule, and it still failed.
- **Point in time.** `F` is the first announcing 8-K. A press release that never reached an 8-K is missed, which
  shortens leads (Align's April 2 release, for instance, appears only in the April 30 8-K). That costs lead, not
  validity.
- **Reorganised registrants.** A ticker whose CIK changed (spin-offs, holding-company reorganisations) is searched
  under today's CIK only. Crane NXT (CIK 25445) carries Crane Co.'s 2020 investor day. The identity-break gate
  excluded 17 events with a ticker change near D.
- **Acquisition gaps.** UAL lost two documents (not a control; any event in those documents is missing), and
  17 hits were not HTML.
- **Window.** The window was spent (W1, W2 and W6), which is why this read could only fail.

## What this changes

- **Investor-day lane: closed on this source.** `idday` v1 is closed. A different parser, universe or window
  is a declared second look. The standalone and large-cap cells are not grounds for one on this window.
- **No live 8-K capture.** It was the prospective step that `survives_development` would have unlocked.
- **The SEC tools stay.** The EDGAR full-text acquisition (`alpha investor-days fetch`) and the shared
  `edgar_fetch` retry keyword are reusable for any future filing-text hypothesis.
- **News history.** The feasibility check found that Alpaca now returns no Benzinga headlines before 2026-04-29.
  [News context](news-context.md) records the correction.
- **Ledger.** Row L45 records the verdict.
