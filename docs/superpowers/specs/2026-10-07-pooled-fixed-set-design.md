# Pooled fixed-set protocol (campaign v4) — design

**Date:** 2026-10-07. **Status:** operator approved the sprint order on 2026-10-07 ("go ahead": small PR W0 → this spec → build → checks A and C → operator approval before the campaign). **Lane docs:** [pooled mining](../../alpha-pooled-mining.md), [v3 checks](../../alpha-pooled-checks-v3-2026-10-05.md) ("a small fixed set of literature-defined decile formulas tested without search, which needs no check B"). **Roadmap:** new entry under the alpha-discovery sprint (added by the plan's docs task).

## Goal

Test a small, predeclared set of literature-defined cross-sectional formulas on the frozen cohort-v2 cube with decile selection, through the existing staged gates (discovery → selection → confirmation with Holm), **without genetic search**, so search-power check B does not apply. A confirmed formula becomes the first candidate for a pooled Part 2 paper probe (not in this spec). Nothing here touches the registry, broker or Telegram.

## Why this and not a new mining campaign

The genetic campaign is parked: check B failed twice because 17 mutations per family cannot recover a planted near-neighbour under either selection rule. A fixed set has no search, so B is moot; checks A (statistical power of the staged gates under the decile rule, already 0.99 at +0.15R on v3) and C (real-formula false acceptance on demeaned panels) still bind. The cube is cached (identity excludes families and budget), so the whole run is CPU only. The two earlier literature entries failed under **top-3** picks on cohort v1; this is their first test under decile baskets, with distinct formulas where the earlier ones already read the confirmation window.

## Non-goals

- No change to the cube, cohort, bracket, costs, windows, bootstrap or gate thresholds (all inherited from v3). No change to the v1–v3 protocol files (their byte hashes stay valid).
- No probe, registry, broker or Telegram change. No new config key in `AppConfig`; the protocol JSON is the only new configuration.
- No change to `alpha pooled study` (single-formula, top-k, unledgered).
- No per-formula tuning after seeing results; the set is frozen in the protocol file and pinned by its SHA-256.

## Decisions

1. **Protocol mode.** `CampaignProtocol` gains `search_mode: Literal["genetic", "fixed_set"] = "genetic"`. Existing files omit it, so their bytes and hashes are unchanged. In `fixed_set` mode the validators require: `power_search` is `None`; `null_check` is set; every family has `mutation_operators == []` and `windows is None`; `formula_budget` equals the total number of seeds; seed canonical expressions are unique across all families (the search shares one `seen` set); `search.archive_size` and `forbidden_operators` are still validated (the forbidden list still applies to seeds). In `genetic` mode nothing changes.
2. **Search loop.** In `fixed_set` mode `family_search` scores the seeds and stops when the queue is empty; it never calls `mutate`. Independently of mode, `TypedGeneticSearch.mutate` with no operators must stop the family cleanly (`stopped_short="no_operators"`) instead of raising `IndexError` from `rng.choice([])`.
3. **Gates by mode.** `GATES` becomes a function of the protocol: `fixed_set` → `(power, null_check)`; `genetic` → the current three. The `campaign` CLI makes `--search-power` optional: required in genetic mode, refused in fixed-set mode. `_run_campaign`'s gate-set equality, the manifest `gates` block and `require_campaign_ready` follow the same rule. Checks A and C refuse to run on a protocol whose mode they do not support (`search-power` refuses fixed-set protocols with a clear message).
4. **Check A** is unchanged in computation (it reads no family information) and reruns only because the protocol hash and code revision change. **Check C** is unchanged in computation: with no operators it already scores exactly the fixed set on demeaned panels and runs the real stages.
5. **Discovery carry and Holm.** `carry` stays 5 and Holm runs over the frozen candidates (at most 5), exactly as in v3. Multiplicity over the predeclared set is controlled by the staged out-of-sample windows plus Holm at confirmation; the set is small by design (8).
6. **Literature overlap stays enforced.** `already_tested` keeps marking any finalist with Jaccard ≥ 0.5 against `high52-v1` (`close / ts_max(high, 252)`) or `reversal-lowmax-v1` (`-1.0 * roc(close, 21)` filtered on low MAX); a marked formula keeps its Holm slot but is not probe-eligible. The fixed set therefore avoids those two scores and their close variants: a label-blind probe on 2026-10-07 (scores only, discovery window) showed the 126-session high overlapping the 52-week-high entry at pick Jaccard 0.76, so no nearness-to-high formula is included, and the one-month reversal is not included (its unfiltered score would overlap the literature entry). Every remaining formula overlaps both entries at Jaccard ≤ 0.18.
7. **Window consumption is the irreversible step.** The confirmation window (2024-01-02 → 2026-07-31, lane-wide, one-use) is consumed when selection freezes at least one candidate, whether or not confirmation then passes. Running the campaign therefore needs explicit operator approval after A and C have passed, as before.

## The fixed set (protocol v4, `config/research/pooled/campaign-v4.json`)

Copy of v3 with: `version: 4`, a new title, `search_mode: "fixed_set"`, `formula_budget: 8`, `power_search` removed, `null_check` kept (40 replicates, ≤2 false acceptances), `excluded_families` kept (the exclusion rationale still applies: volatility, sector-relative and residual-momentum hypotheses read 2021–2026 in earlier studies), and these eight one-seed families (all dimensionless, all vetted against the v3 forbidden operators `realized_vol`, `ts_std`, `ts_mad`):

| Family id | Score | Hypothesis (literature) |
| --- | --- | --- |
| `reversal_5` | `-1.0 * roc(close, 5)` | one-week reversal (Jegadeesh 1990; Lehmann 1990) |
| `signed_volume` | `ts_sum(sign(returns) * volume, 10) / ts_sum(volume, 10)` | signed-volume order imbalance predicts continuation (Chordia & Subrahmanyam 2004) |
| `overnight_intraday` | `ts_sum(open_gap, 21) - ts_sum(oc_spread, 21)` | overnight-minus-intraday return persistence (Lou, Polk & Skouras 2019) |
| `abnormal_volume` | `volume / ts_mean(volume, 50)` | high-volume return premium (Gervais, Kaniel & Mingelgrin 2001) |
| `momentum_12_1` | `delay(close, 21) / delay(close, 252) - 1.0` | 12-1 momentum (Jegadeesh & Titman 1993) |
| `price_volume_corr` | `-1.0 * ts_corr(returns, volume, 21)` | price-volume divergence |
| `illiquidity` | `ts_mean(hl_spread, 21)` | illiquidity premium, range-based proxy (Amihud 2002; Corwin & Schultz 2012) |
| `reversal_x_volume` | `-1.0 * roc(close, 5) * (volume / ts_mean(volume, 50))` | reversal is stronger after high volume (Conrad, Hameed & Niden 1994) |

Excluded on purpose: any realized-volatility score (the `vol_20` exclusion class and the forbidden operators), the unfiltered one-month reversal and any nearness-to-high formula (literature overlap, decision 6), sector-relative momentum (no group operator in the DSL; the 2026-09-24 prospective-only decision). The 252-lookback momentum score is NaN for roughly the first 107 discovery sessions; the discovery gate's ≥400-session requirement still has ample room.

The eight directions are long-only by construction of the lane (long bracket label cube); a formula whose sign is wrong simply fails discovery.

## Runs and decision rule (pre-registered)

1. Build, test and merge the code; deploy with the usual controlled restart (the research CLI runs from the installed checkout environment).
2. Run on the cached cohort-v2 cube at the clean merged revision, outside the 10:35/14:35 ET scan windows, with a thermal watch as before: check A (`alpha pooled power campaign-v4.json`, about 2 h 10 min on 6 workers), then check C (`alpha pooled null-check`, about 30 min). Both must pass on the same cube SHA and revision.
3. Report A and C to the operator. **Only with explicit approval** run `alpha pooled campaign` with `--power` and `--null-check` (no `--search-power`), journal scope `production/alpaca:paper`.
4. Decision: at least one confirmed, probe-eligible formula → write the pooled Part 2 probe spec (cross-sectional daily ranking, one card per session under the probe budget and kill rule). At least one confirmed formula but none probe-eligible (every confirmed formula's picks overlap a literature entry at Jaccard ≥ 0.5) → the result is recorded against that literature entry, no probe follows, and the lane is closed on this window. Zero confirmed → the fixed-set lane is closed on this window. In both closed cases no further formulas are tried against this window, and the next alpha source is decided with the operator.

## Interfaces

- `CampaignProtocol.search_mode`, `CampaignProtocol.gates` (tuple of `(name, check)` by mode), `CampaignProtocol.requires_search_power` (bool). `require_campaign_ready` demands `power_search.seed` only in genetic mode.
- `TypedGeneticSearch.mutate` returns `None` when it has no operators; `family_search` records `stopped_short="no_operators"` and returns.
- `campaign_run.GATES` removed in favour of `protocol.gates`; `check_gate` unchanged; `execute_campaign` accepts `search_power_dir: Path | None`.
- CLI `copilot alpha pooled campaign PROTOCOL --power DIR --null-check DIR [--search-power DIR] …`; `search-power` refuses fixed-set protocols.

## Testing

- Protocol: v4 loads; v1–v3 still load with `search_mode == "genetic"` and their pinned hashes unchanged; fixed-set validators reject a non-empty operator list, a budget ≠ seed count, duplicate canonical seeds, a present `power_search`, a missing `null_check`; all eight v4 seeds compile, are dimensionless and pass the forbidden-operator vet; none equals a literature score canonically.
- Search: in fixed-set mode a family scores exactly its seeds and never calls `mutate` (spy); in genetic mode with an empty operator list the family stops with `no_operators` instead of raising.
- Gates: `protocol.gates` by mode; `campaign` CLI accepts the two-gate form for v4 and refuses `--search-power`; refuses the two-gate form for v3; `search-power` refuses v4.
- Checks: A and C on a tiny synthetic cube in fixed-set mode produce artifacts with the v4 hash and no reference to search power; a C replicate scores exactly the fixed set.
- Regression: the whole `tests/research/pooled` suite, then the full suite and pre-commit.

## Documentation

`docs/alpha-pooled-mining.md`: a "Fixed-set mode" section (what changes, what does not, the literature-overlap rule, the consumption rule); `docs/apriori-alphas.md`: cross-reference from the two literature entries to the fixed set; `docs/alpha-roadmap.md`: the alpha-discovery sprint entry with this step and the decision rule; `CLAUDE.md` pooled paragraph: one sentence that v4 is a fixed-set protocol run without check B. After the runs: `docs/alpha-pooled-checks-v4-<date>.md` with A, C and (if approved) the campaign outcome.

## Acceptance

- v4 passes A and C on the cached cube at the merged revision; artifacts pin the v4 hash; v1–v3 hashes unchanged.
- The campaign command runs the two-gate form only for fixed-set protocols.
- Nothing is charged or consumed before the operator's campaign approval.

## Amendments (2026-10-07)

Recorded during the build; where they differ from the sections above, the built code and these notes govern.

- **Decision 2 and Interfaces, no-operator stop.** `TypedGeneticSearch.ask` raises `ValueError("no mutation operators")` once a no-operator family's seeds are exhausted, and `mutate` raises the same only on its operator-wrapping branch (a window-only edit can still be returned, but `ask` never reaches `mutate` without operators); `family_search` catches it as it catches any exhausted grammar, so the family stops with `stopped_short="search exhausted: no mutation operators"`, not the literal `"no_operators"`, and `mutate` does not return `None`.
- **Interfaces, gates.** `execute_campaign` and `_run_campaign` keep taking the checked `gates` mapping; there is no `search_power_dir` parameter. The CLI builds the mapping from `protocol.gates`, and `--search-power` is optional: required for a genetic protocol and refused for a fixed-set protocol.
- **Additive artifact key.** Each family summary in the campaign result carries `mutations` (the search's successful mutations; 0 for a fixed-set run).
- **Protocol fields.** Protocols have a top-level optional `rationale` field (v4 carries one). In fixed-set mode `family_budgets` returns each family's seed count, so every seed is scored whatever the families' sizes.
- **No v1 exemption.** Genetic mode requires `power_search`; the v1 protocol already carried a `power_search` block (without its seed), so it loads unchanged and no exemption was needed.
