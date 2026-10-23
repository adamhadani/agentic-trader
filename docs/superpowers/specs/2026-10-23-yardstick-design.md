# Fix the yardstick: beta-matched and buy-and-hold controls, asset-class excess, erratum, no T-bill cards

Status: design, 2026-10-23. This is holistic-review item 1.1 (RM-R2), approved by the operator on
2026-10-23 ("go with all your recommendations … start on … yardstick PR"). It also settles
decision D3: the evidence unit for `card_policy` is beta-adjusted excess R with an explicit cost.
That unit is introduced here and is used by `card_stats` v2 (item 1.2), which is not built here.

## 1. Why

`cards outcomes` and the bracket counterfactual judge every native card against a **beta-one** SPY
control. `market_r` is SPY's open-to-close return over the card's own holding window, in the card's
R units (`research/setups/outcomes.py:197`). The review reproduced two consequences on the frozen
2026-10-18 frames:
- With per-stop-width betas, the A0_live gap shrinks from −0.109R to −0.062R.
- In the robustness window, dropping the rates/credit ETFs turns the headline −0.103R into −0.006R.

Low-beta cards (bonds, metals, defensives) are charged for SPY moves they never carried. The CLAUDE.md
line "every arm trails SPY by 0.10–0.15R" therefore overstates the deficit, and every later gate
(`card_stats` v2, `card_policy`, the L3 selector) would inherit the wrong unit.

BIL is a T-bill fund. A 1.5×4h-ATR stop on it implies 650–2,000× notional per unit of risk. Its cards
are meaningless in R and distort the averages. SHY (1–3-year Treasuries) has the same problem at
lower scale.

## 2. Scope

In:
- **Beta-matched control in `cards outcomes`.**
  - Each labelled candidate gets `beta`: the OLS slope of its daily close-to-close simple returns on
    SPY's, over the **120 sessions ending at the session before the decision**. It is past-only and
    frozen at the decision. Both series use adjusted (`all`) daily bars.
  - At least 100 paired finite returns are required; otherwise `beta` is `None`, with `beta_reason`
    set to `short_history`, `no_daily_bars` or `fetch_failed: Type`.
  - `beta_market_r = beta × market_r` and `beta_excess_r = r_cost − beta_market_r`.
  - The beta-one columns stay unchanged beside them.
- **Own-asset buy-and-hold control.**
  - `hold_r` is the candidate's own buy-and-hold return over the bracket's full horizon: from the
    open of the entry bar to the close of the last regular bar of the `max_hold_sessions`-th
    session counted from the entry session, signed by direction, in R units, uncosted.
  - `timing_r = r_cost − hold_r`, the value the bracket's stop and target add over simply holding
    the name for the full horizon.
- **Summaries.**
  - `market_exposure` per outcome class adds `n_beta`, `mean_beta`, `mean_beta_market_r`,
    `mean_beta_excess_r`, `mean_excess_r_same_rows`, `beta_reasons`, `mean_hold_r` and `mean_timing_r`.
  - `counterfactual` arms add `mean_beta_excess_r`, using the candidate's beta times the arm's
    `market_r`.
  - A new `by_asset_class` block covers mature rows per class, with `n`, `mean_r_cost`,
    `mean_excess_r` and `mean_beta_excess_r`.
- **Asset classes.** `asset_class_map(universe) -> dict[str, str]` uses the configured universe:
  - in group `etf32`, sector `etf_rates_credit` is `rates_credit`, `etf_metals` is `metals`, and
    every other sector is `etf_equity`;
  - every other group is `single_stock`;
  - a symbol outside the configured universe (dynamic universe, `/scan` names) is `unclassified`.

  The CLI passes the map to `summarize`.
- **Native universe.** Remove BIL and SHY from `universe.groups.etf32` in `config/config.yaml`. The
  group keeps its name; a YAML comment records why. The research `ETF_RESEARCH_UNIVERSE`
  (`research/alpha/universe.py`) is a separate frozen constant and stays unchanged.
- **Erratum.**
  - Add a descriptive erratum section to `docs/bracket-counterfactual-2026-10-18.md`.
  - Replace the CLAUDE.md summary's "every arm trails SPY by 0.10–0.15R" with the beta-one figure,
    the past-only beta-matched figure and the ex-rates/metals figure, each for both windows.
  - Compute them with this PR's `trailing_beta` on the frozen frames and the cached daily bars of
    the study's caches.
  - The frozen `no_change` stays the decision of record.
- Docs: `docs/card-evidence.md` (the new columns and blocks), CLAUDE.md contract 8 (one clause), and
  the holistic-review README's Phase 0 row (merged #129, deployed 2026-10-22; 10-28 acceptance
  pending).

Out:
- `card_stats` v2 and `card_policy` changes (item 1.2);
- excess by asset class in the setup study's own output (later setup studies add it when they are
  next built);
- shorts' beta handling beyond the sign `market_r` already carries;
- any gate reading the new columns.

## 3. Behaviour details

- Beta source: `beta` reads the candidate's and SPY's adjusted daily bars, fetched once per symbol
  from 200 calendar days before its earliest decision through `now`. The fetch reuses
  `label_journaled`'s paced `fetch`, with one extra adjusted-daily request per symbol plus one for
  SPY. A failed fetch sets `beta_reason` on that symbol's rows; it never fails the report.
- The returns window is the 121 closes ending at the last daily bar dated before the decision's New
  York session. Returns are aligned by date. Only dates where both returns are finite count.
- `beta` is computed with `np.cov(…, ddof=1)` divided by the variance of SPY returns. A zero SPY
  variance gives `None` with `short_history`.
- A row without `market_r` (no proxy) has `beta_market_r` and `beta_excess_r` as `None`, with
  `beta` still recorded.
- `hold_r` is `None` until the horizon session has closed, and when the entry bar is missing.
- The **erratum script** (`scripts/yardstick_erratum.py`, research only, reads local caches only):
  - for each arm and window (primary and robustness), it computes the mean `excess_r` (beta-one as
    frozen), the mean `r_cost − trailing_beta × market_r` (rows without a beta are dropped and
    counted), and both again excluding `rates_credit` and `metals` symbols, using the research ETF
    sectors (`ETF_RESEARCH_UNIVERSE`);
  - it prints JSON. Its output is pasted into the erratum with the run date and revision.

## 4. Testing

- `trailing_beta`:
  - a series that is exactly 0.5 × SPY gives 0.5;
  - the window ends at the session before the decision, so a planted shock on the decision day is
    ignored;
  - fewer than 100 observations gives `None`;
  - zero variance gives `None`;
  - bars are aligned by date when one series misses a day.
- `label_journaled`:
  - beta columns are present;
  - a daily fetch failure gives a reason per symbol and the other columns are unchanged;
  - `hold_r` is the full-horizon buy-and-hold (stop exit then rally, timeout, immature horizon, SHORT sign);
  - `timing_r` arithmetic.
- `summarize`:
  - the new `market_exposure` keys;
  - `by_asset_class` with a map;
  - `unclassified` without one;
  - counterfactual `mean_beta_excess_r`.
- `asset_class_map` on the shipped config:
  - XLK is `etf_equity`, TLT `rates_credit`, GLD `metals`, AAPL `single_stock`;
  - BIL and SHY are absent from the map and from `config.universe.symbols`.
- Shipped-config tests that count universe symbols are updated to the new count.
- Full suite, mypy and pre-commit.
