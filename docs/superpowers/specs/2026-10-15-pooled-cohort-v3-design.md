# Pooled cohort v3: the mid-liquidity band

Date: 2026-10-15. Status: design, written under the operator's standing design trust after the
operator chose it ("continue with your recommendation re cohort v3", 2026-10-15). Research only.
Nothing here touches the registry, broker, Telegram or promotion.

## 1. Why and what

Cohort v2 (the 452 most liquid US names, rank-400 cutoff ≈ $145M a day) has returned no finalist
in three fixed sets: daily literature formulas (v4), first-bucket intraday formulas (intraday-v1)
and event-conditioned formulas (events-v1). Every lane so far ran on that one population. The
anomaly literature places the effects those formulas target (momentum, post-earnings drift,
short-term reversal, dividend yield) mostly in smaller, less-covered names; the paper account's
caps ($7.5k notional, 500 shares) make a $5M-a-day name tradeable without impact. Cohort v3
changes the population and nothing else: the same cube mechanics, label bracket, windows,
selection, gates, bootstrap and the three fixed sets, re-frozen against the new cohort.

Non-goals (rulings): no new formulas; no genetic search (check B stays parked); no live
contract change (the live dynamic-universe gate is unchanged; a finalist would be eligible only
for a separately specified probe with its own liquidity rule); no change to v1–v4,
intraday-v1 or events-v1 files or hashes.

## 2. The screen (`screen-v3.json`)

Same rule family as v2 (`pooled-screen`, `ScreenRule`), same statistic and window, a new
snapshot and a rank **band** instead of a top cut:

- **Snapshot.** The prospective equity snapshot taken 2026-10-15 (id `00a9a363b02a6f49…`,
  file SHA-256 `725a23d735d1dcc4…`, observed 2026-10-15T09:08Z, 6,683
  `listed_non_etf_equity_candidate` members of 33,571 assets). The v2 snapshot file is no longer
  on disk; a cohort pins its own snapshot, so v3 pins this one. Survivorship: current listings
  only, as for v2, and worse for smaller names (delisted failures are absent); declared, not
  corrected. The session-paired pick-minus-pool statistic partly cancels a pool-wide bias; it
  does not remove it.
- **Window and statistic.** SIP raw daily bars, median close × volume over the 60 sessions
  ending 2023-12-29 (before the confirmation window), ≥ 55 sessions with bars, a bar and a raw
  close ≥ $10 on 2023-12-29. Same exclusions (symbol form, NASDAQ fifth letter, name patterns).
- **Band.** Ranks **401 → 1,400** of the descending median dollar volume (ties by symbol):
  exactly 1,000 names when at least 1,400 rank. From the v2 screen's table that is roughly
  $145M down to about $16M a day in late 2023. `ScreenRule` gains `rank_from: int = 1` (ge 1,
  excluded from the dump when 1 so `screen-v2.json`'s hash is unchanged); `selected` becomes
  `rank_from <= rank <= top`.
- **No config-group source.** `config_groups` may be empty (`[]`); `cohort_document` then
  carries the screen as the only source. The 127 configured mega caps are not added, so v3 is
  the band alone; a configured name that ranks inside the band is kept (it is a mid cap by the
  statistic).
- **Output.** `alpha pooled screen config/research/pooled/screen-v3.json --snapshot
  <snapshot.json> --output <dir> --cache <pooled-cache-v1>` writes `cohort.json`, copied
  unchanged to `config/research/pooled/cohort-v3.json` (`version: 3`); its SHA-256 pins every
  v3 protocol. The screen reads raw daily bars for the 6,683 candidates in batches of 100
  (minutes; the window is cached per rule hash).

## 3. Per-session eligibility: an absolute floor

v2's eligibility floor is the 25th percentile of the static reference set's 20-session median
dollar volume (159 configured equities and ETFs, mostly mega caps). It would exclude most of the
band. `UniverseSpec` gains an alternative:

```python
class UniverseSpec(BaseModel, frozen=True, extra="forbid"):
    min_price: float = Field(gt=0)
    static_percentile: float | None = Field(default=None, gt=0, lt=1)
    min_median_dollar_volume: float | None = Field(default=None, gt=0)  # absolute, in dollars
    min_eligible_names: int = Field(ge=1)
    # exactly one of static_percentile / min_median_dollar_volume is set
```

`min_median_dollar_volume` is excluded from the dump when `None`, so the v1–v4, intraday-v1 and
events-v1 cube spec identity (`f1c835a3d3364d2b…`) and file hashes are unchanged (pin tests).
`point_in_time_eligibility` applies the absolute floor to the same 20-session median raw dollar
volume at D−1 (`median_dollar_volume`, the live function) when it is set, and never reads the static
reference (the cube's `reference_names` records 0 and `eligibility_rule` is `absolute`; the
configured static names are still fetched, SPY, the events benchmark, being one, and listed in
`static_used`). v3 protocols use:

```json
"universe": {"min_price": 10.0, "min_median_dollar_volume": 5000000.0, "min_eligible_names": 30}
```

Ruling on the number: $5M a day keeps the band eligible through most of 2016–2021, when these
names were smaller (rank 1,400 was ≈ $16M in late 2023), excludes the illiquid tail, and is
about 0.15% of daily volume for a full-size $7.5k order. `decision_cost_bps` is raised from 5
to **15** per side for the band: spreads and slippage are wider than in v2's names, and the cost
is a cube input, so the honest number must be fixed before the cube exists. Both choices change
the cube spec identity (a v3 cube is new anyway, `cube-<cohort-v3>-<spec-v3>.npz`); the per-symbol
bar cache is reused for any overlapping symbols.

## 4. The three re-frozen protocols

Three files, each byte-identical to its parent except `cohort`, `cohort_sha256`, `universe`,
`decision_cost_bps`, `version`, `title`, `rationale` and the `excluded_families` note:

| File | Parent | id |
| --- | --- | --- |
| `config/research/pooled/cohort3-events-v1.json` | `events-v1.json` | `pooled-events-campaign` |
| `config/research/pooled/cohort3-daily-v1.json` | `campaign-v4.json` | `pooled-campaign` |
| `config/research/pooled/cohort3-intraday-v1.json` | `intraday-v1.json` | `pooled-intraday-campaign` |

Everything else verbatim: windows (discovery 2016-08-01 → 2021-12-31, selection 2022 → 2023,
confirmation 2024-01-02 → 2026-07-31), bracket (10:35 ET, 2 ATR stop, 3R target, 20 sessions),
decile selection, gates, bootstrap, power and null seeds, families and formulas. The events
protocol's `event_inputs.corporate_actions` pins a **new** cohort-v3 corporate-actions acquisition
(`alpha corporate-actions --cohort cohort-v3.json --start 2016-01-01 --end 2027-03-31`, minutes);
the earnings pages are the same cache and digest (it covers all listed symbols). Run order:
events first (the literature's strongest small-cap effect), then daily, then intraday. Check A
on the first protocol builds the cube (≈ 1,000 names × 2 daily + ≈ 11 hourly requests at 150
requests a minute ≈ 85 minutes, plus the build); the other two hit the cache.

## 5. Checks, campaigns and closure (pre-registered)

Each protocol: checks A and C on the v3 cube from a clean worktree at the merged revision,
outside scan windows; a campaign only with the operator's explicit approval, one approval per
protocol; `no_finalists` closes that fixed set on cohort v3. A finalist freezes into the lane's
confirmation window as today and becomes eligible only for a separately specified probe whose
liquidity rule is declared (the live gate would reject most band names as configured). Results
docs: one per protocol run, bundled with the next workstream PR.

## 6. Implementation shape

- `research/pooled/screen.py`: `ScreenRule.rank_from` (default 1, excluded when 1), `config_groups`
  may be empty, `screen_table` selects the band, `cohort_document` skips the config source when
  empty; tests: band selection, hash of `screen-v2.json` unchanged, empty config groups.
- `research/pooled/cohort.py` (`UniverseSpec`, `point_in_time_eligibility`): the absolute floor,
  exactly-one validator, dump exclusion; `cube.py` coverage records which rule applied; tests:
  identity pins, floor semantics at the boundary, no static reference read when absolute.
- Config: `screen-v3.json`; after the real screen run, `cohort-v3.json`; after the real
  corporate-actions run, the three protocol files with pinned digests; pin tests (parent
  equality on every untouched key, band/floor/cost values, cohort SHA).
- Docs: `docs/alpha-pooled-mining.md` ("Cohort v3: the mid-liquidity band", eligibility floor,
  the three protocols), roadmap item 5, CLAUDE.md one sentence; plus the events-v1 results doc.

## 7. Limitations declared up front

Survivorship (current listings only; absent delistings bias a long-only pool upward); the
snapshot is 2026-10-15 while the ranking window is 2023; the 60-session ranking window is a
single point in time (a name's liquidity in 2016 may have been far below the floor, which the
per-session eligibility handles); 15 bp per side is an estimate, not measured slippage; the
Nasdaq calendar and corporate-actions sources are as served now (identity breaks as in events-v1).
