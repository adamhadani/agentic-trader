# ETF session campaign results — September 17, 2026

**All 81 attempts completed; zero candidates passed every frozen triage rule.**
There were no missing execution minutes or unavailable comparisons. This is a useful
separation between economic failure, insufficient evidence and infrastructure failure:
the reversal policies lost money, while some momentum policies made money but lacked
trade breadth or had concentrated gains. No alpha qualifies or activates from this run.

The [protocol](alpha-session-campaign.md) and runner were committed as `8f2c90f`
**before provider access**. Three fixed hypotheses × SPY/QQQ/XLF × three chronological
blocks × 0/1/5 bp per side produced the complete 81 denominator. Every comparison
in a symbol/block used an identical raw SIP snapshot. Three calendar and nine minute
source reads supplied nine cohorts; SDK pagination is separate from that count.
Lifetime research attempts increased from 7,151 to **7,232**. Existing final holdouts
were not used. These are selected historical stress blocks, not untouched validation.

## Results

Returns below compound June 2024, November 2024 and March 2025 as separated blocks,
resetting flat between blocks. They are **diagnostic selected-block equity returns**,
not continuous performance, actual fills or realized paper-account profits. Cash and
warmup minutes are retained; terminal inventory is marked without forced liquidation.

| Symbol | Hypothesis | Net at 1 bp/side | Net at 5 bp/side | Closed trades | Frozen rejection reasons |
| --- | --- | ---: | ---: | ---: | --- |
| SPY | Momentum | +4.89% | +3.97% | 10 | Too few trades; concentration |
| SPY | Reversal | −6.72% | −12.09% | 74 | Block stability, net/excess, stress, concentration |
| SPY | Volume momentum | +0.91% | +0.02% | 10 | Too few trades; excess; concentration |
| QQQ | Momentum | +8.60% | +7.57% | 11 | Too few trades |
| QQQ | Reversal | −5.11% | −10.36% | 71 | Block stability, net/excess, stress, concentration |
| QQQ | Volume momentum | +5.18% | +4.10% | 12 | Too few trades; concentration |
| XLF | Momentum | +0.50% | −0.47% | 11 | Too few trades; stability; net/excess; stress; concentration |
| XLF | Reversal | −2.46% | −7.22% | 62 | Block stability, net/excess, stress, concentration |
| XLF | Volume momentum | +0.24% | −0.56% | 9 | Too few trades; net/excess; stress; concentration |

Passive-long selected-block returns at 1 bp entry cost were SPY +1.69%, QQQ +1.38%,
XLF +3.52%. A failed concentration check on a losing policy includes an undefined
positive-share denominator; it is not a claim that an outlier caused every loss.

QQQ momentum is the strongest descriptive lead: primary-cost block returns were
+3.33%, +3.72%, +1.33%; its largest positive daily contribution was 42.6% of signed
net log gain, within the frozen 50% limit. But closed trades were **4, 1 and 6**,
below the required aggregate 30. Do not relabel that failure as qualification or
lower the threshold after seeing it. SPY momentum's largest day contributed 68.0%;
QQQ volume momentum's contributed 88.9%.

## Why sample density was low

These are session-clock **entry signals with multi-session bracket/GTC lifecycles**,
not mandatory intraday exits. For QQQ momentum, the longest completed position lasted
293.8 calendar hours; a working GTC entry waited 48.6 calendar hours before filling.
Other policies also retained long positions, pending orders or terminal inventory.
QQQ momentum ended two blocks holding positions. Even counting those two censored
exposures would remain far below 30 closed trades.

This behavior matches the declared shared engine: decision expiry prevents a new
submission after its window, but does not cancel an already-submitted GTC order.
A pending or held position suppresses additional entries. New scores cannot be counted
as independent completed trades. The experiment therefore exposes a **design/evidence
limit**: short sampled blocks have poor power for this slower holding policy, and
old resting orders may outlive the economic information behind their signals.
Changing order lifetime or holding policy requires a new immutable execution contract,
matching broker cancellation/recovery semantics, causal tests and a fresh frozen study.
No such policy was silently changed in this run.

The momentum variants are not independent alphas. Daily-return correlations across
these inspected blocks were 0.776 (SPY momentum/volume), 0.685 (QQQ), 0.768 (XLF).
SPY and QQQ momentum correlated 0.903. These short-sample descriptive correlations
support retaining joint portfolio attribution/risk controls; they are not stable
covariance estimates or permission for competing same-symbol execution owners.

## Independent checks and provenance

A separate audit reconstructed all 81 terminal equity values from closed-trade
entry/exit prices, direction and per-side fees, plus any remaining inventory mark.
Maximum discrepancy was **1.14e−14** in fractional equity. It also confirmed:

- Identical input content across all nine comparisons in every symbol/block cohort.
- Identical entry paths across cost scenarios; cost effects did not select a different price path.
- Every saved result/dataset hash matched its retained file.
- All declared attempts, failures/eligibility decisions and cash clocks stayed in their denominators.

No arithmetic, fee or data-index discrepancy was found in this audited path. That
is narrower than claiming the entire pipeline is bug-free or statistically calibrated.
Historical OHLC fills, complete-size assumptions, borrow/funding/corporate-action
omissions and unmeasured broker latency remain material limits.

Private artifacts: `~/.local/state/agentic-trader/research/etf-session-v1-20260917`.
The frozen runner/protocol, source receipts, per-trial arrays/traces, independent audit
and correlation calculations remain private. Redacted hashes:

- Protocol: `2c101f7c0b4b6eebf6b03da6d3b203f8fdff8c3051d44b0749dfb4fa1ac34025`
- Manifest: `ccad0e001c8f6a0963d4090a7ac3785b5927b26640d58c19585eb66b11571308`
- Results: `127b9cf19bd08be775597b441400d6e8c534b2d517b4586b4298817a485357e0`
- Independent audit: `4c19a22ae38444d39aa84fdf54b3061473906a51f873bba47b2135247314d2de`

## Forward cohort and next steps

The protocol selected all three SPY/QQQ hypotheses at the 1 bp policy **before results**
for receipt-aware forward diagnostics. These six shadow hypotheses include rejected
controls, grant no qualifying shadow credit and remain blocked from trading. Retain
historical hypotheses as well. Actual enrollment/deployment evidence belongs in the
PR's operational verification; healthy idle operation is not a real forward sample.

1. Collect genuine forward availability/score/failure evidence for the frozen cohort;
   compare actual broker lifecycle assumptions through the existing integration path.
2. Specify resting-entry expiry and intended holding horizon as versioned execution
   policy before a longer, adequately sampled momentum study. Preserve this failed
   result and trial budget; do not keep rerunning these blocks until they pass.
3. Build causal aligned ETF panels for relative/residual hypotheses after those
   lifecycle questions. Prefer this over broader GP/RL search or options/futures now.

Validation is separate from deployed evidence: 1,221 full tests and 84 real
HTTP/WebSocket/PostgreSQL integrations passed; all-file pre-commit, Ruff and mypy
passed. The new worker was already deployed as PR #47 and verified at 16/16 readiness
checks. PR #48 records the final research/docs merge, enrollment and restart checks.
