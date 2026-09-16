# Synthetic alpha calibration pilot — September 16, 2026

This is A1a instrument verification, **not A1b statistical-policy approval**. The
[active roadmap](alpha-roadmap.md) owns the follow-up. No alpha was qualified or
activated, no shadow credit was created, and no real observations were consumed.

## Frozen protocol and provenance

**Corrected pilot (protocol schema 2):** PR review found that the first pilot reused
the same seed for data and resampling. The corrected implementation spawns four
independent NumPy `SeedSequence` child streams for strategy data/assessment and
panel data/resampling, recording all derived seeds. Paired effect/rho scenarios
still share innovations intentionally. See [NumPy's stream spawning documentation](https://numpy.org/doc/stable/reference/random/parallel.html).
The superseded first pilot is retained below; no gates changed in response to either result.

Executed the actual CLI on clean source `1ad71a0` in an isolated worktree, Python
3.14.7, with runtime dotenv disabled and numerical worker threads limited to one:

```bash
uv run copilot alpha calibrate --seeds 32 --observations 2500 --seed 20260916 \
  --family-trials 7049 --trial-variance 0.0028764548818829777 \
  --bootstrap-samples 499 --block-length 10 --output /private/path/new-report.json
```

- Protocol: `dad8a7aa0906b06fa03de73d8b4a351a68edf2cb000e3bd58687c846e6169030`.
- Private artifact: `calibration-20260916/report-v2.json` in the research state directory.
- Artifact SHA256: `aa572324401525e3f9da34e82a044b4a160f55531d225fd7382b419e8cb74e01`.
- Lock SHA256: `57cc3c8c1e3aaf4ec55d5712b6cd4926198d12960df59ba649054c429e6a140e`.
- 192 strategy controls and 128 fixed-panel controls. All predefined scenarios
  and seeds are retained, including failures. Output permissions are 0600.

The family count/variance reproduce a comparison scenario from the earlier market
campaign; the command did not read or change its ledger. Seeds/settings were fixed
before computation. This small pilot is exploratory instrument verification, not
an independently held-out validation of a new statistical policy.

## Shared strategy assessment

An observable volume pulse affects the following log return, with independent
0.001-standard-deviation innovations. OHLC and timestamps are artificial; prices,
liquidity and fills do not represent a realistic market forecast. The same miner,
score normalization, bracket simulator, costs and statistical assessment are used.
All synthetic provenance remains explicit, so deployment qualification rejects it
even when the scientific checks pass.

| Pulse spacing (bars) | Next-return effect | Statistical passes | 95% Wilson interval |
| --- | --- | --- | --- |
| 8 | 0 | 0/32 | 0–10.7% |
| 8 | 0.004 | 32/32 | 89.3–100% |
| 8 | 0.02 | 32/32 | 89.3–100% |
| 20 | 0 | 0/32 | 0–10.7% |
| 20 | 0.004 | 0/32 | 0–10.7% |
| 20 | 0.02 | 0/32 | 0–10.7% |

Sparse positives failed holdout DSR in all runs; validation family DSR failed in
31/32 and 32/32 respectively. The weaker sparse control also failed the minimum
holdout trade count in 32/32. Both dense-positive scenarios passed in every replicate.
These results demonstrate power for some deliberately strong controls and motivate
studying sparse-strategy power. Zero null passes does **not** establish a low error
rate: the per-scenario upper interval is still 10.7%. Scenarios share random draws;
do not pool them as independent replicates to manufacture a tighter interval.

## Fixed-panel dependence diagnostic

Each panel has 504 observations and eight correlated candidates, with common
innovations giving cross-candidate correlation 0.25. One candidate receives drift
0.15 in the alternative; the null has zero drift. Circular length-10 row blocks
are resampled jointly across all columns, with 499 draws and plus-one p-values.

| Serial correlation | Scenario / measured event | Count | 95% Wilson interval |
| --- | --- | --- | --- |
| 0 | Null: any candidate rejected | 0/32 | 0–10.7% |
| 0 | Alternative: planted candidate detected | 25/32 | 61.2–89.0% |
| 0.5 | Null: any candidate rejected | 3/32 | 3.2–24.2% |
| 0.5 | Alternative: planted candidate detected | 11/32 | 20.4–51.7% |

The nominal test level is 5%, but these small samples neither validate that error
rate nor distinguish Monte Carlo fluctuation from finite-sample distortion. Review
studentization, block-length sensitivity and established multiple-comparison
procedures before considering a replacement. The diagnostic tests **fixed**
candidate panels: it does not account for generating/selecting new formulas on the
same observations. A1b must replay adaptive search on predeclared fresh scenarios.
The [ARCH multiple-comparison examples](https://bashtage.github.io/arch/multiple-comparison/multiple-comparison_examples.html)
are a reference for established SPA/StepM comparisons, not a claim that this pilot
implements those procedures.

## Superseded first pilot

Source `ffa0d3b`, schema 1, the same declared budgets and base seed produced dense
strategy passes 31/32 and 32/32, null/sparse passes 0/32 per scenario, fixed-panel
null rejections 3/32 and 4/32, and planted detections 26/32 and 9/32. Retain its
artifact for audit, but do not pool it with schema 2 or infer that the seed correction
caused the numerical differences: both the random draws and stream design changed.

- Private artifact: `calibration-20260916/report.json`.
- Protocol: `3cdf94e10c41a3f817512e8ad97d342a43f61a4a6ac45d15174151f3b3e4f696`.
- SHA256: `a3c6fe0a769343e9409947b4e7dd7b0e3ae3aa0718899ecb0fa33ad5c8aaf745`.

## Source verification versus deployment

[PR #35](https://github.com/adamhadani/agentic-trader/pull/35) contains the instruments,
roadmap and fail-closed fixes. Regression tests first reproduced non-finite validation
values bypassing comparisons and invalid Sharpe sampling variance being clamped.

- 881 default tests passed; 15 PostgreSQL tests deliberately require explicit opt-in.
- 60 integration tests passed with a disposable `test_` PostgreSQL database and the
  actual SDK TCP/HTTP/WebSocket fixtures.
- Independent DSR and resampling references, causal prefix tests, synthetic deployment
  rejection, private exclusive artifact output, CLI config/DB/provider isolation,
  mypy and all-file pre-commit passed.
- Deployment evidence belongs in the merged PR's operational verification comment;
  passing source tests or this synthetic study does not prove live readiness.

Promotion thresholds, cumulative attempt accounting, consumed holdouts, registry
permissions and paper trading risk limits are unchanged. Intraday promotion and
combined portfolio execution remain blocked by their existing evidence requirements.
