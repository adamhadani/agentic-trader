# Pooled Alpha Mining — Part 1b Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Widen the pooled lane's cohort with a liquidity screen (cohort v2), freeze campaign protocol v2 on it, add the two missing gates (B: search power; C: real-formula false acceptance), and run the budgeted genetic campaign once against a journal-backed ledger.

**Architecture:** A frozen screen rule ranks the prospective snapshot's listed equities by SIP median dollar volume over the 60 sessions ending 2023-12-29 and writes `cohort-v2.json`; `campaign-v2.json` pins it. The genetic search runs per family (seeds, operators and constant windows from the protocol) over the discovery view only, charging every evaluated formula to the journal before evaluation; discovery, selection and confirmation reuse Part 1a's stage logic, split so the search receives each fitness as it goes. Check B plants an edge on a near-seed expression in real discovery labels; check C runs the whole campaign on resampled discovery panels whose labels are demeaned per name. The campaign refuses to start unless A, B and C passed on the same cohort, cube, protocol and clean code revision.

**Tech Stack:** Python 3.14, numpy, pandas, scipy, pydantic v2, SQLAlchemy async (existing journal), click, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-02-pooled-alpha-mining-part1b-design.md` (extends `docs/superpowers/specs/2026-09-28-pooled-alpha-mining-design.md`).

## Global Constraints

- Work only in the worktree `/Users/adamhadani/Development/agentic-trader-pooled1b` (branch `research/pooled-mining-1b`). Never read-write, run or restart anything in `/Users/adamhadani/Development/agentic-trader` (the live installed checkout). Reading its `.envrc` read-only to run a research command is the controller's job, never an implementer's.
- Run everything with `env -u VIRTUAL_ENV uv run …` from the worktree root.
- Research only. The only database writes are the pooled ledger's `EventKind.ALPHA_RESEARCH` events through `AlphaRepository` (Task 10); the pooled lane never touches `family/all`. No registry, broker or Telegram access, no trial or promotion credit. Every result document carries `"authorizes_promotion": false`.
- No new dependencies, no schema migrations (schema head stays `008_alpha_pipeline`).
- Tests: no network, synthetic fixtures, temporary SQLite (`temp_db`); PostgreSQL only through `--run-postgres` and a `test_*` database (`postgres_test_db`).
- Reuse, never reimplement: Part 1a's `research/pooled/*` (cohort, cube, formula, stats, campaign, power, runner, study, entry); `research/alpha/search.py:TypedGeneticSearch`, `canonical_expression`, `WINDOWS`, `MUTATION_OPERATORS`; `research/alpha/dsl.py:compile_expression`, `AlphaDSLSyntaxError`; `research/alpha/operators.py:OPERATOR_SPECS`; `research/apriori/pead_events.py:_by_session`; `research/setups/study.py:_finite_json`, `_stationary_index_draws`, `holm`; `storage/artifacts.py:save_json_report`; `storage/alpha.py:AlphaRepository._get/_append`, `WorkflowStore.lock(resource="alpha")`; `data/providers.py:AlpacaDataProvider.fetch_daily_many`.
- Exact values (verbatim from the spec):
  - **Screen:** snapshot id `bf162c80350e390248813ad461c437787731f76efc15c5f6c5bf49693da9735f`, file SHA-256 `41a7d11eb264cecf57f5e97b447cc17dc2f931bf4f624dcd733e3137a40f303c`, observed 2026-09-17, classification `listed_non_etf_equity_candidate`; window of 60 sessions ending 2023-12-29; at least 55 with bars; raw close ≥ $10 on 2023-12-29; top 400; NASDAQ fifth letters `P Q R U V W Z` (five-letter `nasdaqlisted` symbols only).
  - **Family windows:** high52 126/189/252; reversal 3/5/10/21; max_lottery 5/10/21/42; momentum_12_1 21/63/105/126/189/252; momentum_12_7 84/105/126/147/168; range_location 10/20/40/60/120; trend_slope 10/20/40/60/120; abnormal_volume 5/10/20/50/100; overnight_intraday 5/10/21/42; price_volume_corr 5/10/21/42; signed_volume 5/10/20/40; hl_spread 5/10/21/42.
  - **Protocol extras:** `power_search.seed` 20261002; `null_check` = 40 replicates, at most 2 false acceptances, seed 20261003.
  - **Budget:** 200 formulas split 17 × 8 + 16 × 4; 200 consecutive rejections stop a family.
  - **Gates:** check B ≥ 8 of 10 recovered at Jaccard ≥ 0.5 with δ 0.15R; check C ≤ 2 of 40.
  - **Breaker and ledger:** circuit breaker at 5 consecutive failed fetches; lane-wide confirmation ledger.
- Frozen files (`config/research/pooled/*.json`) may be corrected only before any real-data run that reads them; afterwards a change is a new version file.
- Implementers stage their changes with `git add` and report; the controller commits (the pre-commit hook runs the full suite, about 5 minutes).
- Docs ship in this PR (no docs-only PR).
- Provider reads (the screen, cube builds, every gate and the campaign) are controller-run only. They must never overlap the 10:35 or 14:35 New York suggestion scans.

## Rulings recorded before execution

- **Rights pattern (spec corrected in this PR).** The real snapshot showed that a bare `\brights?\b` drops three ADRs whose names say "representing the right to receive" (AMX, RLX, WDH). The frozen pattern is `-\s*rights?\.?\s*$`; listed rights are already caught by the fifth-letter `R` and symbol-form rules. On the snapshot, the frozen rule keeps 5,315 of 6,721 candidates.
- **Family seed.** Family i's search seed is `search.seed * 100 + i`. This is the spec's `[search.seed, family index]` as one integer, because `random.Random` takes one seed.
- **Check B seed.** Seed i's hidden-expression RNG is `random.Random(power_search.seed * 1000 + i)`.
- **Formula identity in the campaign.** A formula id is the first 16 hex characters of SHA-256 of its canonical expression. The frozen documents also record the full `Formula.identity`.
- **Score precision.** Score panels stay float64 (as Part 1a's literature entries), with an in-memory LRU of 64 panels per process (about 9 MB each).
- **Early check A run.** It runs on the Task 4 revision (Task 5) to build the cube and measure breadth. Only the rerun at the final clean revision (Task 16) can gate the campaign.

## Review Focus

1. **Label reads before consumption.** A search proposal, DSL panel or literature overlap must never read labels, and confirmation labels must never be read before the lane-wide consumption is journaled. Expected: panels touch only `offset`/`sessions`/`eligible`, and the campaign opens confirmation only after `consume_pooled_confirmation` (Task 8 `test_score_panels_never_touch_labels`; Task 13 `test_confirmation_is_read_only_after_the_journal_consumed_it`).
2. **Budget overrun or double charge on a resume.** Expected: a family can never be charged past its reservation, a resumed campaign re-charges nothing, and a crash keeps its charges (Task 10 `test_a_family_cannot_be_charged_past_its_reservation`, `test_charging_the_same_formula_again_is_a_no_op`; Task 13 `test_a_crash_mid_family_keeps_its_charges`).
3. **A gate result from another revision, cube or protocol accepted.** Expected: the campaign refuses each mismatch before reserving anything (Task 13 `test_check_gate_refuses_each_mismatch`, `test_the_campaign_refuses_a_cube_the_gates_did_not_run_on`).
4. **Check C leaves a name effect in, or distorts the cross-section.** Expected: after demeaning, every name's mean equals the overall mean, and within a session every name moves by its own constant only (Task 12 `test_demeaning_removes_name_means_and_keeps_each_session_cross_section`).
5. **The screen reads data after `window_end`, or keeps a non-common instrument.** Expected: a post-window volume spike changes nothing; every request ends on 2023-12-29; warrants, units and preferreds are dropped while MLP units, ADRs and class shares stay (Task 1 `test_screen_table_reads_only_the_window_and_records_each_exclusion`, `test_exclusion_rules_…`; Task 2 `test_screen_writes_manifest_first_then_the_table_and_a_loadable_cohort`).

---

### Task 1: Screen rule, `liquidity_screen` cohort source and the pure screen

**Files:**
- Modify: `agentic_trader/research/pooled/cohort.py`
- Create: `agentic_trader/research/pooled/screen.py`
- Create: `config/research/pooled/screen-v2.json`
- Test: `tests/research/pooled/test_screen.py`

**Interfaces:**
- Produces:
  - `cohort.py`: `ScreenProvenance`; `CohortSource.screen: ScreenProvenance | None`; kind literal `"liquidity_screen"`.
  - `screen.py`:
    - `SnapshotRef`, `ScreenRule`, `LoadedScreenRule(rule, sha256, path)`, `load_screen_rule(path)`;
    - `exclusion_reason(member, rule) -> str | None`;
    - `snapshot_candidates(snapshot, rule) -> (list[str], dict[str, str])`;
    - `screen_table(kept, excluded, frames, sessions, rule) -> pd.DataFrame`;
    - `table_csv(table) -> bytes`;
    - `config_group_symbols(config_path, groups) -> list[str]`;
    - `cohort_document(table, loaded, *, rule_path, config_symbols, screen_sha256, snapshot_sha256) -> dict`.

- [ ] **Step 1: Write the frozen rule file**

```json
{
  "id": "pooled-screen",
  "version": 2,
  "title": "Pooled cohort v2 liquidity screen: top 400 listed US equities by SIP median dollar volume over the 60 sessions ending 2023-12-29",
  "cohort_version": 2,
  "survivorship": "listed and active on 2026-09-17; ranked on SIP raw dollar volume of the 60 sessions ending 2023-12-29; not point-in-time",
  "snapshot": {
    "snapshot_id": "bf162c80350e390248813ad461c437787731f76efc15c5f6c5bf49693da9735f",
    "sha256": "41a7d11eb264cecf57f5e97b447cc17dc2f931bf4f624dcd733e3137a40f303c",
    "observed": "2026-09-17",
    "classification": "listed_non_etf_equity_candidate"
  },
  "config_groups": ["mega_caps", "research_cohort"],
  "feed": "alpaca:sip",
  "adjustment": "raw",
  "window_end": "2023-12-29",
  "window_sessions": 60,
  "min_sessions_with_bars": 55,
  "min_price": 10.0,
  "top": 400,
  "excluded_fifth_letters": ["P", "Q", "R", "U", "V", "W", "Z"],
  "excluded_name_patterns": [
    "\\bwarrants?\\b",
    "-\\s*rights?\\.?\\s*$",
    "\\bpreferred\\b",
    "%",
    "\\bnotes?\\b",
    "\\bdebentures?\\b",
    "\\bfund\\b",
    "\\bunits?,? each consist",
    "\\btangible equity units?\\b",
    "\\bcorporate units?\\b",
    "-\\s*units?\\.?\\s*$"
  ]
}
```

Save it as `config/research/pooled/screen-v2.json`.

- [ ] **Step 2: Write the failing tests**

```python
# tests/research/pooled/test_screen.py
import hashlib
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.pooled.cohort import Cohort, CohortSource, load_cohort
from agentic_trader.research.pooled.screen import (
    LoadedScreenRule,
    cohort_document,
    config_group_symbols,
    exclusion_reason,
    load_screen_rule,
    screen_table,
    snapshot_candidates,
    table_csv,
)


REPO = Path(__file__).resolve().parents[3]
LOADED = load_screen_rule(REPO / "config/research/pooled/screen-v2.json")
RULE = LOADED.rule
SESSIONS = (date(2023, 12, 22), date(2023, 12, 26), date(2023, 12, 27), date(2023, 12, 28), date(2023, 12, 29))
SMALL = RULE.model_copy(update={"window_sessions": 5, "min_sessions_with_bars": 4, "top": 2})


def member(symbol, name, source="otherlisted", classification="listed_non_etf_equity_candidate"):
    return {"symbol": symbol, "classification": classification, "directory": {"Security Name": name, "source": source}}


def daily(closes, volumes, days=SESSIONS):
    index = pd.DatetimeIndex([pd.Timestamp(d).tz_localize(ET_TZ) for d in days]).tz_convert("UTC")
    return pd.DataFrame({"Close": closes, "Volume": volumes}, index=index)


def test_the_frozen_rule_pins_the_snapshot_and_the_pre_confirmation_window():
    assert RULE.snapshot.snapshot_id == "bf162c80350e390248813ad461c437787731f76efc15c5f6c5bf49693da9735f"
    assert RULE.snapshot.sha256 == "41a7d11eb264cecf57f5e97b447cc17dc2f931bf4f624dcd733e3137a40f303c"
    assert RULE.snapshot.classification == "listed_non_etf_equity_candidate"
    assert RULE.window_end == date(2023, 12, 29)  # the last session before the confirmation window
    assert (RULE.window_sessions, RULE.min_sessions_with_bars, RULE.min_price, RULE.top) == (60, 55, 10.0, 400)
    assert RULE.cohort_version == 2 and RULE.config_groups == ("mega_caps", "research_cohort")
    assert RULE.excluded_fifth_letters == ("P", "Q", "R", "U", "V", "W", "Z")


@pytest.mark.parametrize(
    ("symbol", "source", "name", "reason"),
    [
        ("CMCSA", "nasdaqlisted", "Comcast Corporation - Class A Common Stock", None),
        ("GOOGL", "nasdaqlisted", "Alphabet Inc. - Class A Common Stock", None),
        ("ET", "otherlisted", "Energy Transfer LP Common Units ", None),
        (
            "AMX",
            "otherlisted",
            "America Movil, S.A.B. de C.V. American Depositary Shares (each representing the right to receive twenty)",
            None,
        ),
        (
            "BWIV.U",
            "otherlisted",
            "Blue Water Acquisition Corp. IV Units, each consisting of one Class A ordinary share",
            "unsupported symbol form",
        ),
        ("XTERW", "nasdaqlisted", "Karman Line Acquisition Corp. - Warrant", "nasdaq fifth letter W"),
        ("MNSBP", "nasdaqlisted", "MainStreet Bancshares, Inc. - Depositary Shares", "nasdaq fifth letter P"),
        (
            "ZKPW",
            "nasdaqlisted",
            "Lafayette Digital Acquisition Corp. I - Warrant",
            r"security name matches \bwarrants?\b",
        ),
        (
            "ZKPU",
            "nasdaqlisted",
            "Lafayette Digital Acquisition Corp. I - Unit",
            r"security name matches -\s*units?\.?\s*$",
        ),
        (
            "DUKU",
            "otherlisted",
            "Duke Energy Corporation Corporate Units",
            r"security name matches \bcorporate units?\b",
        ),
        (
            "GLV",
            "otherlisted",
            "Clough Global Dividend and Income Fund Common Shares of beneficial interest",
            r"security name matches \bfund\b",
        ),
        (
            "CMSD",
            "otherlisted",
            "CMS Energy Corporation 5.875% Junior Subordinated Notes due 2079",
            "security name matches %",
        ),
        (
            "PFO",
            "otherlisted",
            "Flaherty & Crumrine Preferred and Income Opportunity Fund Incorporated",
            r"security name matches \bpreferred\b",
        ),
        (
            "BHFAL",
            "nasdaqlisted",
            "Brighthouse Financial, Inc. - Junior Subordinated Debentures due 2058",
            r"security name matches \bdebentures?\b",
        ),
    ],
)
def test_exclusion_rules_drop_non_common_instruments_and_keep_units_adrs_and_classes(symbol, source, name, reason):
    assert exclusion_reason(member(symbol, name, source), RULE) == reason


def test_snapshot_candidates_keep_only_the_rule_classification_and_record_every_exclusion():
    snapshot = {
        "snapshot_id": RULE.snapshot.snapshot_id,
        "members": [
            member("MSFT", "Microsoft Corporation - Common Stock", "nasdaqlisted"),
            member("XTERW", "Karman Line Acquisition Corp. - Warrant", "nasdaqlisted"),
            member("AAPL", "Apple Inc. - Common Stock", "nasdaqlisted"),
            member("SPY", "SPDR S&P 500 ETF Trust", classification="excluded"),
        ],
    }
    kept, excluded = snapshot_candidates(snapshot, RULE)
    assert kept == ["AAPL", "MSFT"]
    assert excluded == {"XTERW": "nasdaq fifth letter W"}


def test_snapshot_candidates_refuse_another_snapshot():
    with pytest.raises(ValueError, match="snapshot_id"):
        snapshot_candidates({"snapshot_id": "0" * 64, "members": []}, RULE)


def test_screen_table_ranks_by_median_dollar_volume_and_breaks_ties_by_symbol():
    frames = {
        "AAA": daily([20.0] * 5, [1e6] * 5),
        "BBB": daily([50.0] * 5, [1e6] * 5),
        "CCC": daily([20.0] * 5, [1e6] * 5),
    }
    table = screen_table(["AAA", "BBB", "CCC"], {}, frames, SESSIONS, SMALL)
    assert list(table["symbol"]) == ["BBB", "AAA", "CCC"]
    assert list(table["rank"]) == [1, 2, 3]
    assert list(table["selected"]) == [True, True, False]
    assert table.loc[0, "median_dollar_volume"] == pytest.approx(5.0e7)


def test_screen_table_reads_only_the_window_and_records_each_exclusion():
    later = (date(2024, 1, 2),)
    frames = {
        "OUT": daily([30.0] * 6, [1e6] * 5 + [1e12], days=SESSIONS + later),  # the post-window spike is ignored
        "FEW": daily([30.0] * 3, [1e6] * 3, days=SESSIONS[:3]),
        "GAP": daily([30.0] * 4, [1e6] * 4, days=SESSIONS[:4]),
        "LOW": daily([9.0] * 5, [1e9] * 5),
    }
    table = screen_table(
        ["FEW", "GAP", "LOW", "NONE", "OUT"], {"XTERW": "nasdaq fifth letter W"}, frames, SESSIONS, SMALL
    ).set_index("symbol")
    assert table.loc["OUT", "median_dollar_volume"] == pytest.approx(3.0e7)
    assert table.loc["OUT", "rank"] == 1 and table.loc["OUT", "exclusion"] == ""
    assert table.loc["FEW", "exclusion"] == "bars on fewer than 4 sessions"
    assert table.loc["GAP", "exclusion"] == "no bar on 2023-12-29"
    assert table.loc["LOW", "exclusion"] == "close below 10"
    assert table.loc["NONE", "exclusion"] == "no bars in the screen window"
    assert table.loc["XTERW", "exclusion"] == "nasdaq fifth letter W"
    others = table.drop(index="OUT")
    assert (others["rank"] == 0).all() and not others["selected"].any()


def test_the_window_must_end_on_the_rule_date():
    with pytest.raises(ValueError, match="sessions ending 2023-12-29"):
        screen_table([], {}, {}, (*SESSIONS[:-1], date(2023, 12, 30)), SMALL)


def test_the_csv_is_byte_identical_for_the_same_table():
    frames = {"AAA": daily([20.0] * 5, [1e6] * 5)}
    first = table_csv(screen_table(["AAA"], {}, frames, SESSIONS, SMALL))
    assert first == table_csv(screen_table(["AAA"], {}, frames, SESSIONS, SMALL))
    assert first.startswith(b"symbol,exclusion,sessions_with_bars,median_dollar_volume,last_close,rank,selected\n")


def test_cohort_document_unions_the_scan_equities_and_the_screened_names():
    frames = {s: daily([20.0 + i] * 5, [1e6] * 5) for i, s in enumerate(["AAA", "BBB", "CCC"])}
    table = screen_table(["AAA", "BBB", "CCC"], {"XTERW": "nasdaq fifth letter W"}, frames, SESSIONS, SMALL)
    csv = table_csv(table)
    loaded = LoadedScreenRule(rule=SMALL, sha256="a" * 64, path=Path("screen-v2.json"))
    doc = cohort_document(
        table,
        loaded,
        rule_path="config/research/pooled/screen-v2.json",
        config_symbols=["AAPL", "BBB", "BRK.B"],
        screen_sha256=hashlib.sha256(csv).hexdigest(),
        snapshot_sha256=RULE.snapshot.sha256,
    )
    cohort = Cohort.model_validate(doc)
    assert cohort.version == 2 and cohort.survivorship == RULE.survivorship
    assert cohort.symbols == ("AAPL", "BBB", "CCC")
    assert cohort.excluded == {"BRK.B": "unsupported symbol form"}
    screened = next(source for source in cohort.sources if source.kind == "liquidity_screen")
    assert screened.symbols == ("BBB", "CCC")  # top 2 by median dollar volume: CCC (22) and BBB (21)
    assert screened.identity == hashlib.sha256(csv).hexdigest()
    assert screened.screen.rule_sha256 == "a" * 64
    assert screened.screen.rule == "config/research/pooled/screen-v2.json"
    assert (screened.screen.candidates, screened.screen.excluded, screened.screen.ranked) == (4, 1, 3)
    assert cohort.source_of("BBB") == ("config_groups", "liquidity_screen")


def test_config_group_symbols_are_the_sorted_union_of_the_named_groups(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "universe:\n  groups:\n"
        "    mega_caps:\n      - {symbol: MSFT}\n      - {symbol: AAPL}\n"
        "    research_cohort:\n      - {symbol: AAPL}\n      - {symbol: ZZZ}\n"
        "    other:\n      - {symbol: QQQ}\n"
    )
    assert config_group_symbols(config, ("mega_caps", "research_cohort")) == ["AAPL", "MSFT", "ZZZ"]


def test_only_a_liquidity_screen_source_carries_screen_provenance():
    provenance = {
        "rule": "r",
        "rule_sha256": "a" * 64,
        "snapshot_id": "s",
        "snapshot_sha256": "b" * 64,
        "window_end": "2023-12-29",
        "top": 1,
        "candidates": 1,
        "excluded": 0,
        "ranked": 1,
    }
    with pytest.raises(ValidationError, match="screen provenance"):
        CohortSource(kind="liquidity_screen", description="d", identity="i", symbols=("A",))
    with pytest.raises(ValidationError, match="screen provenance"):
        CohortSource(kind="config_groups", description="d", identity="i", symbols=("A",), screen=provenance)
    source = CohortSource(kind="liquidity_screen", description="d", identity="i", symbols=("A",), screen=provenance)
    assert source.screen.top == 1


def test_cohort_v1_still_loads_with_its_frozen_hash():
    loaded = load_cohort(REPO / "config/research/pooled/cohort-v1.json")
    assert loaded.sha256 == "966b67c53ad617d698089dd5b5786b05d4d5248f8844e3326d7addbe8d4ac5a9"
    assert all(source.screen is None for source in loaded.cohort.sources)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_screen.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentic_trader.research.pooled.screen'`.

- [ ] **Step 4: Add the `liquidity_screen` source kind to `cohort.py`**

Replace the `CohortSource` class (keep everything else in the file) with:

```python
class ScreenProvenance(BaseModel, frozen=True, extra="forbid"):
    """Where a ``liquidity_screen`` source's symbols came from: the frozen rule and the snapshot."""

    rule: str
    rule_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_id: str = Field(min_length=1)
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    window_end: date
    top: int = Field(ge=1)
    candidates: int = Field(ge=0)
    excluded: int = Field(ge=0)
    ranked: int = Field(ge=0)


class CohortSource(BaseModel, frozen=True, extra="forbid"):
    kind: Literal["config_groups", "equity_snapshot", "liquidity_screen"]
    description: str
    # config groups: SHA-256 of the newline-joined sorted symbols; snapshot: its snapshot_id;
    # liquidity screen: SHA-256 of the screen table (screen.csv) its symbols were selected from.
    identity: str = Field(min_length=1)
    symbols: tuple[str, ...]
    screen: ScreenProvenance | None = None

    @model_validator(mode="after")
    def _screen_only_for_a_liquidity_screen(self) -> CohortSource:
        if (self.kind == "liquidity_screen") != (self.screen is not None):
            raise ValueError("a liquidity_screen source needs screen provenance, and no other kind may carry it")
        return self
```

Add `"ScreenProvenance"` to `__all__` (alphabetical order).

- [ ] **Step 5: Write `screen.py` (pure part)**

```python
# agentic_trader/research/pooled/screen.py
"""Cohort v2's liquidity screen: listed US equities ranked by SIP median dollar volume.

The rule file (``config/research/pooled/screen-v2.json``) is frozen before any bar is read.
Candidates come from the prospective equity snapshot, verified by hash. Non-common
instruments are excluded by symbol form, NASDAQ fifth-letter code and security-name
pattern, each with its reason. The rest are ranked by the median raw close x volume over
the ``window_sessions`` sessions ending ``window_end``. That date precedes the campaign's
confirmation window, so membership is not chosen on that window's dollar volume. The
screen reads prices and volumes only, never returns or labels; eligibility per session
is still decided point-in-time by the cube.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from agentic_trader.research.apriori.pead_events import _by_session
from agentic_trader.research.pooled.cohort import SUPPORTED_SYMBOL


__all__ = [
    "SCREEN_COLUMNS",
    "LoadedScreenRule",
    "ScreenRule",
    "SnapshotRef",
    "cohort_document",
    "config_group_symbols",
    "exclusion_reason",
    "load_screen_rule",
    "screen_table",
    "snapshot_candidates",
    "table_csv",
]

SCREEN_COLUMNS = (
    "symbol",
    "exclusion",
    "sessions_with_bars",
    "median_dollar_volume",
    "last_close",
    "rank",
    "selected",
)


class SnapshotRef(BaseModel, frozen=True, extra="forbid"):
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed: date
    classification: str = Field(min_length=1)


class ScreenRule(BaseModel, frozen=True, extra="forbid"):
    id: Literal["pooled-screen"]
    version: int = Field(ge=1)
    title: str
    cohort_version: int = Field(ge=1)
    survivorship: str
    snapshot: SnapshotRef
    config_groups: tuple[str, ...] = Field(min_length=1)
    feed: Literal["alpaca:sip"]
    adjustment: Literal["raw"]
    window_end: date
    window_sessions: int = Field(ge=1)
    min_sessions_with_bars: int = Field(ge=1)
    min_price: float = Field(gt=0)
    top: int = Field(ge=1)
    excluded_fifth_letters: tuple[str, ...]
    excluded_name_patterns: tuple[str, ...]

    @field_validator("excluded_fifth_letters")
    @classmethod
    def _letters(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not re.fullmatch(r"[A-Z]", letter) for letter in value):
            raise ValueError("fifth letters must be single upper-case letters")
        return value

    @field_validator("excluded_name_patterns")
    @classmethod
    def _patterns(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for pattern in value:
            re.compile(pattern)
        return value

    @model_validator(mode="after")
    def _window(self) -> ScreenRule:
        if self.min_sessions_with_bars > self.window_sessions:
            raise ValueError("min_sessions_with_bars cannot exceed window_sessions")
        return self


@dataclass(frozen=True)
class LoadedScreenRule:
    rule: ScreenRule
    sha256: str
    path: Path


def load_screen_rule(path: Path) -> LoadedScreenRule:
    raw = path.read_bytes()
    return LoadedScreenRule(rule=ScreenRule.model_validate_json(raw), sha256=hashlib.sha256(raw).hexdigest(), path=path)


def exclusion_reason(member: Mapping, rule: ScreenRule) -> str | None:
    """Why a snapshot candidate is not a common equity for the screen; None when it is kept."""
    symbol = str(member["symbol"])
    if not SUPPORTED_SYMBOL.fullmatch(symbol):
        return "unsupported symbol form"
    directory = member.get("directory") or {}
    if directory.get("source") == "nasdaqlisted" and len(symbol) == 5 and symbol[-1] in rule.excluded_fifth_letters:
        return f"nasdaq fifth letter {symbol[-1]}"
    name = str(directory.get("Security Name", ""))
    for pattern in rule.excluded_name_patterns:
        if re.search(pattern, name, flags=re.IGNORECASE):
            return f"security name matches {pattern}"
    return None


def snapshot_candidates(snapshot: Mapping, rule: ScreenRule) -> tuple[list[str], dict[str, str]]:
    """(kept symbols, sorted; excluded symbol -> reason) among the rule's candidate classification."""
    if snapshot.get("snapshot_id") != rule.snapshot.snapshot_id:
        raise ValueError(f"snapshot_id {snapshot.get('snapshot_id')!r} is not the rule's {rule.snapshot.snapshot_id!r}")
    kept: set[str] = set()
    excluded: dict[str, str] = {}
    for member in snapshot["members"]:
        if member.get("classification") != rule.snapshot.classification:
            continue
        reason = exclusion_reason(member, rule)
        if reason is None:
            kept.add(str(member["symbol"]))
        else:
            excluded[str(member["symbol"])] = reason
    return sorted(kept), dict(sorted(excluded.items()))


def _window_exclusion(row: Mapping, rule: ScreenRule) -> str:
    if row["sessions_with_bars"] == 0:
        return "no bars in the screen window"
    if row["sessions_with_bars"] < rule.min_sessions_with_bars:
        return f"bars on fewer than {rule.min_sessions_with_bars} sessions"
    if not np.isfinite(row["last_close"]):
        return f"no bar on {rule.window_end.isoformat()}"
    if row["last_close"] < rule.min_price:
        return f"close below {rule.min_price:g}"
    return ""


def _window_row(symbol: str, frame: pd.DataFrame | None, window: set[date], rule: ScreenRule) -> dict:
    row = {
        "symbol": symbol,
        "exclusion": "",
        "sessions_with_bars": 0,
        "median_dollar_volume": float("nan"),
        "last_close": float("nan"),
    }
    if frame is not None and not frame.empty:
        keyed = _by_session(frame)
        keyed = keyed[[day in window for day in keyed.index]]
        close = keyed["Close"].to_numpy(float)
        volume = keyed["Volume"].to_numpy(float)
        finite = np.isfinite(close) & np.isfinite(volume)
        row["sessions_with_bars"] = int(finite.sum())
        if finite.any():
            row["median_dollar_volume"] = float(np.median(close[finite] * volume[finite]))
        if rule.window_end in keyed.index:
            last = float(keyed.loc[rule.window_end, "Close"])
            if np.isfinite(last):
                row["last_close"] = last
    row["exclusion"] = _window_exclusion(row, rule)
    return row


def screen_table(
    kept: Sequence[str],
    excluded: Mapping[str, str],
    frames: Mapping[str, pd.DataFrame],
    sessions: Sequence[date],
    rule: ScreenRule,
) -> pd.DataFrame:
    """One row per candidate: its exclusion, or its window statistics and rank (1 = most liquid).

    Only bars on ``sessions`` (the window ending ``window_end``) are read. Ranked rows come
    first in rank order (median dollar volume descending, ties by symbol), then the rest by
    symbol; ``rank`` is 0 for an excluded row.
    """
    if len(sessions) != rule.window_sessions or sessions[-1] != rule.window_end:
        raise ValueError(
            f"the screen window must be the {rule.window_sessions} sessions ending {rule.window_end.isoformat()}"
        )
    window = set(sessions)
    rows = [
        {
            "symbol": symbol,
            "exclusion": reason,
            "sessions_with_bars": 0,
            "median_dollar_volume": float("nan"),
            "last_close": float("nan"),
        }
        for symbol, reason in excluded.items()
    ]
    rows += [_window_row(symbol, frames.get(symbol), window, rule) for symbol in kept]
    table = pd.DataFrame(rows, columns=list(SCREEN_COLUMNS[:5]))
    ranked = table[table["exclusion"] == ""].sort_values(["median_dollar_volume", "symbol"], ascending=[False, True])
    rank = {symbol: position for position, symbol in enumerate(ranked["symbol"], start=1)}
    table["rank"] = [rank.get(symbol, 0) for symbol in table["symbol"]]
    table["selected"] = (table["rank"] >= 1) & (table["rank"] <= rule.top)
    ordered = pd.concat(
        [table[table["rank"] > 0].sort_values("rank"), table[table["rank"] == 0].sort_values("symbol")],
        ignore_index=True,
    )
    return ordered[list(SCREEN_COLUMNS)]


def table_csv(table: pd.DataFrame) -> bytes:
    return table.to_csv(index=False, float_format="%.6f", lineterminator="\n").encode()


def config_group_symbols(config_path: Path, groups: Sequence[str]) -> list[str]:
    universe = yaml.safe_load(config_path.read_text())["universe"]["groups"]
    return sorted({str(row["symbol"]) for name in groups for row in universe[name]})


def cohort_document(
    table: pd.DataFrame,
    loaded: LoadedScreenRule,
    *,
    rule_path: str,
    config_symbols: Sequence[str],
    screen_sha256: str,
    snapshot_sha256: str,
) -> dict:
    """The cohort file this screen proposes: the scan-universe equities plus the top-ranked names."""
    rule = loaded.rule
    screened = sorted(str(symbol) for symbol in table.loc[table["selected"], "symbol"])
    configured = sorted(set(config_symbols))
    everything = sorted({*configured, *screened})
    return {
        "id": "pooled-cohort",
        "version": rule.cohort_version,
        "survivorship": rule.survivorship,
        "sources": [
            {
                "kind": "config_groups",
                "description": (
                    f"config/config.yaml universe groups {' + '.join(rule.config_groups)} (scan-universe equities)"
                ),
                "identity": hashlib.sha256("\n".join(configured).encode()).hexdigest(),
                "symbols": configured,
            },
            {
                "kind": "liquidity_screen",
                "description": rule.title,
                "identity": screen_sha256,
                "symbols": screened,
                "screen": {
                    "rule": rule_path,
                    "rule_sha256": loaded.sha256,
                    "snapshot_id": rule.snapshot.snapshot_id,
                    "snapshot_sha256": snapshot_sha256,
                    "window_end": rule.window_end.isoformat(),
                    "top": rule.top,
                    "candidates": int(len(table)),
                    "excluded": int((table["exclusion"] != "").sum()),
                    "ranked": int((table["rank"] > 0).sum()),
                },
            },
        ],
        "excluded": {
            symbol: "unsupported symbol form" for symbol in everything if not SUPPORTED_SYMBOL.fullmatch(symbol)
        },
        "symbols": [symbol for symbol in everything if SUPPORTED_SYMBOL.fullmatch(symbol)],
    }
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_screen.py tests/research/pooled/test_cohort.py -q`
Expected: PASS (all tests, including Part 1a's cohort tests).

- [ ] **Step 7: Stage**

```bash
git add agentic_trader/research/pooled/cohort.py agentic_trader/research/pooled/screen.py config/research/pooled/screen-v2.json tests/research/pooled/test_screen.py
```

---

### Task 2: Screen executor and `copilot alpha pooled screen`

**Files:**
- Modify: `agentic_trader/research/pooled/screen.py`
- Modify: `agentic_trader/cli/commands/alpha.py`
- Test: `tests/research/pooled/test_screen_executor.py`, `tests/cli/test_alpha_pooled_cli.py`

**Interfaces:**
- Consumes: Task 1's `screen.py` functions; `research/setups/runner.py:CalendarSource`; `research/pooled/study.py:_save_frame`; `research/setups/study.py:_finite_json`; `storage/artifacts.py:save_json_report`.
- Produces:
  - `screen.py`: `FETCH_BATCH = 100`, `CIRCUIT_BREAKER = 5`;
  - `async screen_sessions(calendar, rule) -> tuple[date, ...]`;
  - `async fetch_window(symbols, sessions, *, fetch, cache_dir, pace, progress=None) -> dict[str, pd.DataFrame]`;
  - `async execute_screen(loaded, snapshot_path, directory, *, rule_path, config_symbols, calendar, fetch, cache_dir, pace, environment, progress=None) -> dict`;
  - the CLI command `alpha pooled screen RULE --snapshot PATH --output DIR --cache DIR`.

- [ ] **Step 1: Write the failing executor tests**

```python
# tests/research/pooled/test_screen_executor.py
import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.pooled import screen as screen_module
from agentic_trader.research.pooled.cohort import Cohort
from agentic_trader.research.pooled.screen import LoadedScreenRule, execute_screen, load_screen_rule


REPO = Path(__file__).resolve().parents[3]
RULE = load_screen_rule(REPO / "config/research/pooled/screen-v2.json").rule


@dataclass
class Day:
    date: date
    is_trading_day: bool


class FakeCalendar:
    async def get_calendar_range(self, start, end):
        out, d = [], start
        while d <= end:
            out.append(Day(d, d.weekday() < 5))
            d += timedelta(days=1)
        return out


class FakeFetch:
    """Batched raw daily bars; the first ``fail`` calls raise."""

    def __init__(self, fail=0):
        self.calls = []
        self.fail = fail

    def __call__(self, symbols, start, end):
        self.calls.append((tuple(symbols), start, end))
        if self.fail:
            self.fail -= 1
            raise RuntimeError("provider down")
        days, d = [], start.date()
        while d <= end.date():
            if d.weekday() < 5:
                days.append(d)
            d += timedelta(days=1)
        index = pd.DatetimeIndex([pd.Timestamp(x).tz_localize(ET_TZ) for x in days]).tz_convert("UTC")
        return {
            symbol: pd.DataFrame({"Close": 20.0 + ord(symbol[0]) - 64, "Volume": 1e6}, index=index)
            for symbol in symbols
        }


async def _no_pace():
    return None


def member(symbol, name="Example Corp - Common Stock", source="nasdaqlisted"):
    return {
        "symbol": symbol,
        "classification": "listed_non_etf_equity_candidate",
        "directory": {"Security Name": name, "source": source},
    }


def setup(tmp_path, symbols=("AAA", "BBB", "CCC")):
    members = [member(s) for s in symbols] + [member("XTERW", "Karman Line Acquisition Corp. - Warrant")]
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps({"snapshot_id": RULE.snapshot.snapshot_id, "members": members}))
    rule = RULE.model_copy(
        update={
            "window_sessions": 5,
            "min_sessions_with_bars": 4,
            "top": 2,
            "snapshot": RULE.snapshot.model_copy(update={"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}),
        }
    )
    return LoadedScreenRule(rule=rule, sha256="e" * 64, path=Path("screen-v2.json")), path


def run(tmp_path, loaded, snapshot, fetch, out="out", progress=None):
    return asyncio.run(
        execute_screen(
            loaded,
            snapshot,
            tmp_path / out,
            rule_path="config/research/pooled/screen-v2.json",
            config_symbols=["AAPL", "MSFT"],
            calendar=FakeCalendar(),
            fetch=fetch,
            cache_dir=tmp_path / "cache",
            pace=_no_pace,
            environment={"python": "x"},
            progress=progress,
        )
    )


def test_screen_writes_manifest_first_then_the_table_and_a_loadable_cohort(tmp_path):
    loaded, snapshot = setup(tmp_path)
    seen = {}

    class Watching(FakeFetch):
        def __call__(self, symbols, start, end):
            seen.setdefault("manifest", (tmp_path / "out" / "manifest.json").exists())
            return super().__call__(symbols, start, end)

    fetch = Watching()
    result = run(tmp_path, loaded, snapshot, fetch)
    assert result["status"] == "completed", result
    assert seen == {"manifest": True}
    # Every request ends on the rule's window_end: nothing after 2023-12-29 is read.
    assert {call[2].date() for call in fetch.calls} == {date(2023, 12, 29)}
    assert {call[1].date() for call in fetch.calls} == {date(2023, 12, 25)}
    cohort_bytes = (tmp_path / "out" / "cohort.json").read_bytes()
    cohort = Cohort.model_validate_json(cohort_bytes)
    assert result["cohort_sha256"] == hashlib.sha256(cohort_bytes).hexdigest()
    assert cohort.symbols == ("AAPL", "BBB", "CCC", "MSFT")
    screened = next(source for source in cohort.sources if source.kind == "liquidity_screen")
    assert screened.identity == hashlib.sha256((tmp_path / "out" / "screen.csv").read_bytes()).hexdigest()
    assert screened.identity == result["screen_sha256"]
    assert (result["kept"], result["excluded"], result["selected"]) == (3, 1, 2)
    manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
    assert manifest["check"] == "screen" and manifest["authorizes_promotion"] is False
    assert result["authorizes_promotion"] is False


def test_a_mismatched_snapshot_fails_before_any_fetch(tmp_path):
    loaded, snapshot = setup(tmp_path)
    snapshot.write_text(snapshot.read_text() + " ")
    fetch = FakeFetch()
    result = run(tmp_path, loaded, snapshot, fetch)
    assert result["status"] == "failed" and "sha256" in result["error"]
    assert fetch.calls == []


def test_chunks_are_cached_and_a_rerun_fetches_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(screen_module, "FETCH_BATCH", 2)
    loaded, snapshot = setup(tmp_path)
    first = run(tmp_path, loaded, snapshot, FakeFetch(), out="one")
    fetch = FakeFetch()
    second = run(tmp_path, loaded, snapshot, fetch, out="two")
    assert fetch.calls == []
    assert first["cohort_sha256"] == second["cohort_sha256"]


def test_five_consecutive_failures_stop_acquisition(tmp_path, monkeypatch):
    monkeypatch.setattr(screen_module, "FETCH_BATCH", 1)
    loaded, snapshot = setup(tmp_path, symbols=("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG"))
    fetch = FakeFetch(fail=99)
    messages: list[str] = []
    result = run(tmp_path, loaded, snapshot, fetch, progress=messages.append)
    assert result["status"] == "failed"
    assert "provider unavailable: 5 consecutive requests failed" in result["error"]
    assert len(fetch.calls) == 5
    assert any("chunk 1/7 failed: RuntimeError: provider down" in m for m in messages)


def test_a_failed_chunk_fails_the_screen_after_every_chunk_and_a_rerun_resumes(tmp_path, monkeypatch):
    monkeypatch.setattr(screen_module, "FETCH_BATCH", 1)
    loaded, snapshot = setup(tmp_path)
    fetch = FakeFetch(fail=1)
    result = run(tmp_path, loaded, snapshot, fetch, out="one")
    assert result["status"] == "failed" and "rerun to resume from the cache" in result["error"]
    assert len(fetch.calls) == 3  # every chunk was attempted
    again = FakeFetch()
    assert run(tmp_path, loaded, snapshot, again, out="two")["status"] == "completed"
    assert [call[0] for call in again.calls] == [("AAA",)]  # only the failed chunk


def test_refuses_an_existing_output_directory(tmp_path):
    loaded, snapshot = setup(tmp_path)
    (tmp_path / "out").mkdir()
    with pytest.raises(FileExistsError):
        run(tmp_path, loaded, snapshot, FakeFetch())
```

`FakeCalendar` treats every weekday as a trading day, so the five sessions ending 2023-12-29 start on Monday 2023-12-25.

- [ ] **Step 2: Write the failing CLI tests (append to `tests/cli/test_alpha_pooled_cli.py`)**

```python
SCREEN_RULE = str(REPO_ROOT / "config/research/pooled/screen-v2.json")


def test_screen_refuses_an_existing_output_without_creating_clients(tmp_path, monkeypatch):
    _forbid_clients(monkeypatch)
    out = tmp_path / "out"
    out.mkdir()
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text("{}")
    result = _invoke(
        "screen", SCREEN_RULE, "--snapshot", str(snapshot), "--output", str(out), "--cache", str(tmp_path / "cache")
    )
    assert result.exit_code != 0
    assert "refusing to overwrite" in result.output


def test_screen_reads_raw_daily_bars_through_the_batched_provider(tmp_path, monkeypatch):
    seen = {}

    class Bars:
        def fetch_daily_many(self, symbols, start, end, *, adjustment):
            seen["adjustment"] = adjustment
            return {}

    @contextmanager
    def clients():
        yield SimpleNamespace(bars=Bars(), calendar=object(), pace=None)

    async def fake_execute(loaded, snapshot_path, output, **kwargs):
        kwargs["fetch"](["AAA"], None, None)
        seen.update(rule_path=kwargs["rule_path"], configured=len(kwargs["config_symbols"]))
        return {"status": "completed"}

    monkeypatch.setattr(alpha_cli, "_apriori_clients", clients)
    monkeypatch.setattr(alpha_cli, "execute_screen", fake_execute)
    monkeypatch.setattr(alpha_cli, "research_environment", lambda: {})
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text("{}")
    result = _invoke(
        "screen",
        SCREEN_RULE,
        "--snapshot",
        str(snapshot),
        "--output",
        str(tmp_path / "out"),
        "--cache",
        str(tmp_path / "cache"),
    )
    assert result.exit_code == 0, result.output
    assert seen["adjustment"] == "raw"
    assert seen["rule_path"] == "config/research/pooled/screen-v2.json"
    assert seen["configured"] > 0
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_screen_executor.py tests/cli/test_alpha_pooled_cli.py -q`
Expected: FAIL (`ImportError: cannot import name 'execute_screen'`; the CLI tests fail with `No such command 'screen'`).

- [ ] **Step 4: Append the executor to `screen.py`**

Extend the imports at the top of `screen.py`:

```python
import asyncio
import json
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta

from agentic_trader.research.pooled.cohort import SUPPORTED_SYMBOL, Cohort
from agentic_trader.research.pooled.study import _save_frame
from agentic_trader.research.setups.runner import CalendarSource
from agentic_trader.research.setups.study import _finite_json
from agentic_trader.storage.artifacts import save_json_report
```

(Merge with the existing `collections.abc`, `datetime` and `cohort` imports; do not duplicate them.) Add `"CIRCUIT_BREAKER"`, `"FETCH_BATCH"`, `"execute_screen"`, `"fetch_window"` and `"screen_sessions"` to `__all__`. Then append:

```python
FETCH_BATCH = 100  # symbols per batched daily request
CIRCUIT_BREAKER = 5  # consecutive failed requests stop acquisition: the provider is down


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as target:
        target.write(data)


async def screen_sessions(calendar: CalendarSource, rule: ScreenRule) -> tuple[date, ...]:
    """The ``window_sessions`` trading sessions ending on ``window_end``."""
    days = await calendar.get_calendar_range(
        rule.window_end - timedelta(days=2 * rule.window_sessions + 30), rule.window_end
    )
    trading = sorted(day.date for day in days if day.is_trading_day and day.date <= rule.window_end)
    if len(trading) < rule.window_sessions or trading[-1] != rule.window_end:
        raise ValueError(f"the calendar has no {rule.window_sessions} sessions ending {rule.window_end.isoformat()}")
    return tuple(trading[-rule.window_sessions :])


def _rows(fetched: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    parts = []
    for symbol, frame in sorted(fetched.items()):
        index = pd.DatetimeIndex(frame.index)
        index = index if index.tz is not None else index.tz_localize("UTC")
        parts.append(
            pd.DataFrame(
                {
                    "symbol": symbol,
                    "timestamp": index.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "close": frame["Close"].to_numpy(float),
                    "volume": frame["Volume"].to_numpy(float),
                }
            )
        )
    if not parts:
        return pd.DataFrame(columns=["symbol", "timestamp", "close", "volume"])
    return pd.concat(parts, ignore_index=True)


def _frames(rows: pd.DataFrame) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for symbol, group in rows.groupby("symbol", sort=True):
        index = pd.DatetimeIndex(pd.to_datetime(group["timestamp"], utc=True))
        out[str(symbol)] = pd.DataFrame(
            {"Close": group["close"].to_numpy(float), "Volume": group["volume"].to_numpy(float)}, index=index
        )
    return out


def _read_chunk(path: Path) -> pd.DataFrame:
    # Symbols such as "NA" must stay strings, so pandas' default NA spellings are off.
    return pd.read_csv(path, dtype={"symbol": str, "timestamp": str}, keep_default_na=False)


async def fetch_window(
    symbols: Sequence[str],
    sessions: Sequence[date],
    *,
    fetch: Callable[[Sequence[str], datetime, datetime], Mapping[str, pd.DataFrame]],
    cache_dir: Path,
    pace: Callable[[], Awaitable[None]],
    progress: Callable[[str], None] | None = None,
) -> dict[str, pd.DataFrame]:
    """Raw daily bars for ``symbols`` over ``sessions`` in batched requests, cached per chunk.

    A failed request is recorded and the next chunk is tried; all chunks are attempted
    before the screen fails, and a rerun fetches only the chunks without a cache file.
    ``CIRCUIT_BREAKER`` consecutive failures stop at once: the provider is down.
    """

    def say(message: str) -> None:
        if progress is not None:
            progress(message)

    cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    start = datetime.combine(sessions[0], time.min, tzinfo=UTC)
    end = datetime.combine(sessions[-1], time.max, tzinfo=UTC)
    chunks = [list(symbols[i : i + FETCH_BATCH]) for i in range(0, len(symbols), FETCH_BATCH)]
    frames: dict[str, pd.DataFrame] = {}
    errors: dict[str, str] = {}
    consecutive = 0
    for number, chunk in enumerate(chunks, start=1):
        digest = hashlib.sha256("\n".join(chunk).encode()).hexdigest()[:12]
        path = cache_dir / f"chunk-{number:03d}-{digest}.csv.gz"
        if path.exists():
            rows = await asyncio.to_thread(_read_chunk, path)
        else:
            await pace()
            try:
                fetched = await asyncio.to_thread(fetch, chunk, start, end)
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"
                errors[f"chunk {number}"] = reason
                consecutive += 1
                say(f"screen bars: chunk {number}/{len(chunks)} failed: {reason}")
                if consecutive >= CIRCUIT_BREAKER:
                    raise ValueError(
                        f"provider unavailable: {consecutive} consecutive requests failed (last: {reason}); "
                        "rerun to resume from the cache"
                    ) from exc
                continue
            consecutive = 0
            rows = _rows(fetched)
            await asyncio.to_thread(_save_frame, rows, path)
        frames.update(_frames(rows))
        say(f"screen bars {number}/{len(chunks)} chunks")
    if errors:
        listed = "; ".join(f"{chunk} ({reason})" for chunk, reason in errors.items())
        raise ValueError(f"screen bar acquisition failed for {listed}; rerun to resume from the cache")
    return frames


async def execute_screen(
    loaded: LoadedScreenRule,
    snapshot_path: Path,
    directory: Path,
    *,
    rule_path: str,
    config_symbols: Sequence[str],
    calendar: CalendarSource,
    fetch: Callable[[Sequence[str], datetime, datetime], Mapping[str, pd.DataFrame]],
    cache_dir: Path,
    pace: Callable[[], Awaitable[None]],
    environment: dict,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Run the frozen screen; writes ``screen.csv`` and the proposed ``cohort.json``.

    ``protocol.json`` and ``manifest.json`` are written before any provider access; a
    failure is recorded as ``status: failed`` instead of raising.
    """
    rule = loaded.rule
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    save_json_report({**rule.model_dump(mode="json"), "sha256": loaded.sha256}, directory / "protocol.json")
    save_json_report(
        {
            "check": "screen",
            "rule_sha256": loaded.sha256,
            "snapshot_sha256": rule.snapshot.sha256,
            "reads": "raw SIP daily close and volume over the screen window; no returns or labels",
            "environment": environment,
            "started_at": datetime.now(UTC).isoformat(),
            "authorizes_promotion": False,
        },
        directory / "manifest.json",
    )
    try:
        raw = await asyncio.to_thread(snapshot_path.read_bytes)
        snapshot_sha256 = hashlib.sha256(raw).hexdigest()
        if snapshot_sha256 != rule.snapshot.sha256:
            raise ValueError(f"snapshot file sha256 {snapshot_sha256} is not the rule's {rule.snapshot.sha256}")
        kept, excluded = snapshot_candidates(json.loads(raw), rule)
        sessions = await screen_sessions(calendar, rule)
        frames = await fetch_window(
            kept,
            sessions,
            fetch=fetch,
            cache_dir=cache_dir / f"screen-{loaded.sha256[:16]}",
            pace=pace,
            progress=progress,
        )
        table = screen_table(kept, excluded, frames, sessions, rule)
        csv = table_csv(table)
        await asyncio.to_thread(_write_private, directory / "screen.csv", csv)
        screen_sha256 = hashlib.sha256(csv).hexdigest()
        document = cohort_document(
            table,
            loaded,
            rule_path=rule_path,
            config_symbols=config_symbols,
            screen_sha256=screen_sha256,
            snapshot_sha256=snapshot_sha256,
        )
        text = (json.dumps(document, indent=2) + "\n").encode()
        Cohort.model_validate_json(text)  # the file the operator commits must load
        await asyncio.to_thread(_write_private, directory / "cohort.json", text)
        result = {
            "status": "completed",
            "kept": len(kept),
            "excluded": len(excluded),
            "ranked": int((table["rank"] > 0).sum()),
            "selected": int(table["selected"].sum()),
            "cohort_symbols": len(document["symbols"]),
            "cohort_sha256": hashlib.sha256(text).hexdigest(),
            "screen_sha256": screen_sha256,
            "window": [sessions[0].isoformat(), sessions[-1].isoformat()],
            "authorizes_promotion": False,
        }
    except Exception as exc:
        result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "authorizes_promotion": False}
    save_json_report(_finite_json(result), directory / "result.json")
    return result
```

- [ ] **Step 5: Add the CLI command to `agentic_trader/cli/commands/alpha.py`**

Add imports next to the other `research.pooled` imports:

```python
from agentic_trader.research.pooled.screen import config_group_symbols, execute_screen, load_screen_rule
```

Add, after `alpha_pooled_study_cmd`:

```python
def _repo_relative(path: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


@alpha_pooled_group.command("screen")
@click.argument("rule_path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--snapshot",
    "snapshot_path",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="The prospective equity snapshot.json the rule pins by SHA-256",
)
@click.option("--output", type=click.Path(path_type=Path), required=True, help="New private directory; no overwrite")
@click.option(
    "--cache", type=click.Path(path_type=Path), required=True, help="Cache directory for the screen's batched bars"
)
@coro
async def alpha_pooled_screen_cmd(rule_path, snapshot_path, output, cache):
    """Cohort v2's liquidity screen: rank listed equities by SIP median dollar volume (research only)."""
    if output.exists():
        raise click.ClickException(f"Output directory already exists; refusing to overwrite: {output}")
    loaded = await asyncio.to_thread(load_screen_rule, rule_path)
    config_symbols = await asyncio.to_thread(
        config_group_symbols, REPO_ROOT / "config/config.yaml", loaded.rule.config_groups
    )
    environment = await asyncio.to_thread(research_environment)
    with _apriori_clients() as clients:
        result = await execute_screen(
            loaded,
            snapshot_path,
            output,
            rule_path=_repo_relative(rule_path),
            config_symbols=config_symbols,
            calendar=clients.calendar,
            fetch=lambda symbols, start, end: clients.bars.fetch_daily_many(symbols, start, end, adjustment="raw"),
            cache_dir=cache,
            pace=clients.pace,
            environment=environment,
            progress=lambda message: click.echo(message, err=True),
        )
    summary = ("status", "selected", "cohort_symbols", "cohort_sha256", "error")
    click.echo(json.dumps({k: result.get(k) for k in summary}, indent=2, default=str))
    if result.get("status") != "completed":
        raise click.ClickException("Screen failed; see result.json for the reason")
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_screen.py tests/research/pooled/test_screen_executor.py tests/cli/test_alpha_pooled_cli.py -q`
Expected: PASS.

- [ ] **Step 7: Stage**

```bash
git add agentic_trader/research/pooled/screen.py agentic_trader/cli/commands/alpha.py tests/research/pooled/test_screen_executor.py tests/cli/test_alpha_pooled_cli.py
```

---
### Task 3: Campaign protocol v2 fields

**Files:**
- Modify: `agentic_trader/research/pooled/campaign.py`
- Test: `tests/research/pooled/test_campaign_protocol.py`

**Interfaces:**
- Consumes: `research/alpha/search.py:WINDOWS`, `MUTATION_OPERATORS`; `research/alpha/operators.py:OPERATOR_SPECS`.
- Produces:
  - `Family.windows: tuple[int, ...] | None`, `Family.constant_windows -> tuple[int, ...]`;
  - `PowerSearchSpec.seed: int | None`;
  - `NullCheckSpec(replicates, max_false_acceptances, seed)`, `CampaignProtocol.null_check: NullCheckSpec | None`;
  - `CampaignProtocol.family_budgets() -> dict[str, int]`, `CampaignProtocol.require_campaign_ready() -> None`.
  - Forbidden operators are compared case-insensitively and must name DSL operators.

- [ ] **Step 1: Write the failing tests (append to `tests/research/pooled/test_campaign_protocol.py`)**

Add `from agentic_trader.research.alpha.search import WINDOWS` to the file's imports, then append:

```python
V2_WINDOWS = {
    "high52": [126, 189, 252],
    "reversal": [3, 5, 10, 21],
    "max_lottery": [5, 10, 21, 42],
    "momentum_12_1": [21, 63, 105, 126, 189, 252],
    "momentum_12_7": [84, 105, 126, 147, 168],
    "range_location": [10, 20, 40, 60, 120],
    "trend_slope": [10, 20, 40, 60, 120],
    "abnormal_volume": [5, 10, 20, 50, 100],
    "overnight_intraday": [5, 10, 21, 42],
    "price_volume_corr": [5, 10, 21, 42],
    "signed_volume": [5, 10, 20, 40],
    "hl_spread": [5, 10, 21, 42],
}


def _v2(doc):
    for family in doc["families"]:
        family["windows"] = V2_WINDOWS[family["id"]]
    doc["power_search"]["seed"] = 20261002
    doc["null_check"] = {"replicates": 40, "max_false_acceptances": 2, "seed": 20261003}


def test_campaign_v1_still_loads_with_its_frozen_hash_and_cannot_run_a_campaign():
    loaded = load_campaign_protocol(PROTOCOL)
    assert loaded.sha256 == "897fd8e59d912975de9377389a75d8954a747ab49dfa6ae368a30879059ca5ba"
    assert loaded.protocol.null_check is None and loaded.protocol.power_search.seed is None
    assert all(f.windows is None and f.constant_windows == WINDOWS for f in loaded.protocol.families)
    with pytest.raises(ValueError, match="checks B and C"):
        loaded.protocol.require_campaign_ready()


def test_the_v2_fields_validate_and_make_the_protocol_campaign_ready(tmp_path):
    protocol = load_campaign_protocol(_mutated(tmp_path, _v2)).protocol
    protocol.require_campaign_ready()
    assert (protocol.null_check.replicates, protocol.null_check.max_false_acceptances) == (40, 2)
    assert protocol.power_search.seed == 20261002
    assert {f.id: f.constant_windows for f in protocol.families} == {k: tuple(v) for k, v in V2_WINDOWS.items()}


def test_the_formula_budget_splits_evenly_with_the_remainder_to_the_first_families():
    protocol = load_campaign_protocol(PROTOCOL).protocol
    budgets = protocol.family_budgets()
    assert list(budgets) == [f.id for f in protocol.families]
    assert list(budgets.values()) == [17] * 8 + [16] * 4
    assert sum(budgets.values()) == protocol.formula_budget == 200


@pytest.mark.parametrize("windows", [[], [20, 10], [5, 5], [1, 5]])
def test_family_windows_must_be_sorted_unique_and_at_least_two(tmp_path, windows):
    def mutate(doc):
        _v2(doc)
        doc["families"][0]["windows"] = windows

    with pytest.raises(ValidationError):
        load_campaign_protocol(_mutated(tmp_path, mutate))


def test_null_check_cannot_allow_more_false_acceptances_than_replicates(tmp_path):
    def mutate(doc):
        _v2(doc)
        doc["null_check"]["max_false_acceptances"] = 41

    with pytest.raises(ValidationError):
        load_campaign_protocol(_mutated(tmp_path, mutate))


def test_forbidden_operators_are_matched_in_any_letter_case(tmp_path):
    def mutate(doc):
        doc["search"]["forbidden_operators"] = ["REALIZED_VOL", "TS_STD", "TS_MAD"]
        doc["families"][0]["mutation_operators"].append("ts_std")

    with pytest.raises(ValidationError, match="forbidden"):
        load_campaign_protocol(_mutated(tmp_path, mutate))


def test_a_forbidden_operator_must_name_a_dsl_operator(tmp_path):
    def mutate(doc):
        doc["search"]["forbidden_operators"].append("ts_stdev")

    with pytest.raises(ValidationError, match="unknown forbidden operators"):
        load_campaign_protocol(_mutated(tmp_path, mutate))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_campaign_protocol.py -q`
Expected: FAIL (`AttributeError: 'Family' object has no attribute 'windows'` and similar; the case test fails because no error is raised).

- [ ] **Step 3: Implement the model changes in `campaign.py`**

Replace the import `from agentic_trader.research.alpha.search import MUTATION_OPERATORS` with:

```python
from agentic_trader.research.alpha.operators import OPERATOR_SPECS
from agentic_trader.research.alpha.search import MUTATION_OPERATORS, WINDOWS
```

In `Family`, after `mutation_operators`, add the field, its validator and the property:

```python
# Campaign v2+: the windows that integer constants inside this family's expressions may
# mutate to (wrapper operators keep the miner's global set). None (v1) is the global set.
windows: tuple[int, ...] | None = None


@field_validator("windows")
@classmethod
def _windows(cls, value: tuple[int, ...] | None) -> tuple[int, ...] | None:
    if value is not None and (not value or list(value) != sorted(set(value)) or value[0] < 2):
        raise ValueError("windows must be non-empty, sorted, unique integers >= 2")
    return value


@property
def constant_windows(self) -> tuple[int, ...]:
    return WINDOWS if self.windows is None else self.windows
```

In `SearchSpec`, add:

```python
    @field_validator("forbidden_operators")
    @classmethod
    def _known_operators(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        # A misspelt name would silently ban nothing.
        unknown = {op.lower() for op in value} - {name.lower() for name in OPERATOR_SPECS}
        if unknown:
            raise ValueError(f"unknown forbidden operators: {sorted(unknown)}")
        return value
```

In `PowerSearchSpec`, add the field `seed: int | None = None  # check B's hidden-expression RNG (campaign v2+)`.

Add, after `PowerSearchSpec`:

```python
class NullCheckSpec(BaseModel, frozen=True, extra="forbid"):
    """Check C: the full campaign on per-name-demeaned discovery panels with real formulas."""

    replicates: int = Field(ge=1)
    max_false_acceptances: int = Field(ge=0)
    seed: int

    @model_validator(mode="after")
    def _bounded(self) -> NullCheckSpec:
        if self.max_false_acceptances > self.replicates:
            raise ValueError("max_false_acceptances cannot exceed replicates")
        return self
```

In `CampaignProtocol`, add the field `null_check: NullCheckSpec | None = None` after `power_search`. In `_consistent`, replace the forbidden check:

```python
        forbidden = {op.lower() for op in self.search.forbidden_operators}
        ids = [family.id for family in self.families]
        if len(ids) != len(set(ids)):
            raise ValueError("family ids must be unique")
        for family in self.families:
            if forbidden & {op.lower() for op in family.mutation_operators}:
                raise ValueError(f"family {family.id} mutates with a forbidden operator")
            for seed in family.seeds:
                if forbidden & _calls(seed):
                    raise ValueError(f"family {family.id} seed {seed!r} uses a forbidden operator")
```

(`_calls` already lower-cases the call names.) Add these methods after `cube_spec`:

```python
def family_budgets(self) -> dict[str, int]:
    """The formula budget split evenly across families, the remainder to the first in file order."""
    base, remainder = divmod(self.formula_budget, len(self.families))
    return {family.id: base + (1 if index < remainder else 0) for index, family in enumerate(self.families)}


def require_campaign_ready(self) -> None:
    if self.null_check is None or self.power_search.seed is None:
        raise ValueError("this protocol predates checks B and C (campaign v1); a campaign needs protocol v2 or later")
```

Add `"Family"` and `"NullCheckSpec"` to `__all__`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/ -q`
Expected: PASS (the whole pooled suite; v1 still loads unchanged).

- [ ] **Step 5: Stage**

```bash
git add agentic_trader/research/pooled/campaign.py tests/research/pooled/test_campaign_protocol.py
```

---

### Task 4: Runner hardening (circuit breaker, bar-file re-check, full-span test) and breadth

**Files:**
- Modify: `agentic_trader/research/pooled/runner.py`
- Modify: `agentic_trader/research/pooled/power.py`
- Test: `tests/research/pooled/test_runner.py`, `tests/research/pooled/test_power.py`

**Interfaces:**
- Produces:
  - `runner.py`: `CIRCUIT_BREAKER = 5`; `bar_file_list` in a new cube's coverage provenance; `_verify_bar_files(coverage, bar_dir, raw_dir) -> str`.
  - `power.py`: `breadth(cube, cohort) -> dict`, and `"breadth"` in `execute_power_check`'s result.

- [ ] **Step 1: Update the test fakes in `tests/research/pooled/test_runner.py`**

- In `FakeBars.fetch_bars`, replace `self.spans[key] = (start, end)` with `self.spans.setdefault(key, []).append((start, end))`.
- In `test_hourly_bars_cover_a_span_that_does_not_depend_on_the_symbols_eligibility`, change the assertion to `assert bars.spans[("AAA", "1h", "all")] == bars.spans[("BBB", "1h", "all")] == [expected]`.
- Give `build` a `spec` parameter:

```python
def build(bars, cache_dir, *, static=STATIC, loaded=None, progress=None, spec=SPEC):
    return asyncio.run(
        build_cube_inputs(
            loaded or cohort(),
            spec,
            bars=bars,
            calendar=FakeCalendar(),
            cache_dir=cache_dir,
            static_symbols=static,
            pace=_no_pace,
            progress=progress,
        )
    )
```

- [ ] **Step 2: Write the failing runner tests (append to `tests/research/pooled/test_runner.py`)**

Add `from itertools import pairwise` to the file's imports, then append:

```python
def test_hourly_requests_cover_the_whole_spec_span_in_contiguous_chunks(tmp_path):
    spec = SPEC.model_copy(update={"bars_through": date(2022, 6, 1)})
    bars = FakeBars()
    build(bars, tmp_path, spec=spec)
    start = datetime.combine(spec.decisions[0], time.min, tzinfo=UTC) - timedelta(days=7)
    end = datetime.combine(spec.bars_through, time.max, tzinfo=UTC)
    for symbol in ("AAA", "BBB"):
        chunks = bars.spans[(symbol, "1h", "all")]
        assert len(chunks) >= 2
        assert chunks[0][0] == start and chunks[-1][1] == end
        assert all(left[1] == right[0] for left, right in pairwise(chunks))


def test_a_cache_hit_rechecks_every_bar_file_the_cube_was_built_from(tmp_path):
    first = build(FakeBars(), tmp_path)
    assert len(first.cube.coverage["bar_file_list"]) == first.cube.coverage["bar_files"] == 46
    messages: list[str] = []
    build(FakeBars(), tmp_path, progress=messages.append)
    assert any("bar files re-checked: 46 unchanged" in message for message in messages)
    # A new cache file that sorts first would be loaded instead: the cached cube is refused.
    original = cached(tmp_path, "bars", "AAA", "1d")[0]
    shutil.copy(original, original.parent / ("0" * 8 + original.name))
    with pytest.raises(ValueError, match="bar files that changed"):
        build(FakeBars(), tmp_path)


def test_a_cube_built_before_the_file_list_is_loaded_with_a_note(tmp_path):
    built = build(FakeBars(), tmp_path)
    (cube_file,) = tmp_path.glob("cube-*.npz")
    view = built.cube.window(*WINDOW)
    older = LabelCube(
        spec_identity=built.cube.spec_identity,
        cohort_sha256=built.cube.cohort_sha256,
        sessions=view.sessions,
        symbols=view.symbols,
        arrays={name: getattr(view, name) for name in _ARRAYS},
        coverage={k: v for k, v in built.cube.coverage.items() if k != "bar_file_list"},
    )
    cube_file.unlink()
    save_cube(older, cube_file)
    messages: list[str] = []
    build(FakeBars(), tmp_path, progress=messages.append)
    assert any("not re-checked" in message for message in messages)


def test_five_consecutive_raised_fetches_stop_acquisition_early(tmp_path):
    fail = {(symbol, "1d", adjustment) for symbol in ("AAA", "BBB", *STATIC) for adjustment in ("all", "raw")}
    bars = FakeBars(fail=fail)
    messages: list[str] = []
    with pytest.raises(ValueError, match="provider unavailable: 5 consecutive fetches failed"):
        build(bars, tmp_path, progress=messages.append)
    assert len({call[0] for call in bars.calls}) < len(STATIC) + 2  # stopped before every symbol
    assert any("fetch failed: AAA 1d/all: RuntimeError: provider down" in message for message in messages)
```

- [ ] **Step 3: Write the failing breadth tests (in `tests/research/pooled/test_power.py`)**

Replace `_executor_inputs` with:

```python
def _executor_inputs(cohort_sha=COHORT):
    protocol = tiny_protocol().model_copy(update={"cohort_sha256": COHORT})
    loaded = LoadedProtocol(protocol=protocol, sha256="p" * 64, path=Path("campaign.json"))
    cohort = SimpleNamespace(sha256=cohort_sha, cohort=SimpleNamespace(source_of=lambda symbol: ("config_groups",)))
    return loaded, cohort
```

In `test_execute_power_check_writes_manifest_first_and_a_strict_result`, add after the `static_used` assertion:

```python
    total = int(built.cube.window(built.cube.sessions[0], built.cube.sessions[-1]).eligible.sum())
    assert result["breadth"]["cohort_symbols"] == 40
    assert result["breadth"]["eligible_cells_by_source"] == {"config_groups": total}
```

Then append:

```python
def test_breadth_counts_eligible_names_per_session_and_cells_by_source():
    days = calendar()[:4]
    eligible = np.array([[1, 1, 0], [1, 0, 0], [1, 1, 1], [0, 0, 0]], dtype=bool)
    shape = eligible.shape
    cube = LabelCube(
        spec_identity="s",
        cohort_sha256=COHORT,
        sessions=days,
        symbols=("A", "B", "C"),
        arrays={
            "eligible": eligible,
            "labelled": eligible,
            "r_gross": np.zeros(shape),
            "r_cost": np.zeros(shape),
            "holding": np.ones(shape, np.int16),
            "hit": np.ones(shape, np.int8),
            "tiebreak": np.zeros(shape, np.uint64),
            "dollar_volume": np.ones(shape),
        },
        coverage={},
    )
    sources = {"A": ("config_groups", "liquidity_screen"), "B": ("liquidity_screen",), "C": ("config_groups",)}
    cohort = SimpleNamespace(cohort=SimpleNamespace(source_of=sources.__getitem__))
    report = power.breadth(cube, cohort)
    assert report["eligible_per_session"] == {"mean": 1.5, "min": 0, "max": 3}
    assert report["by_year_mean"] == {str(days[0].year): 1.5}
    assert report["eligible_cells_by_source"] == {
        "config_groups": 1,
        "config_groups+liquidity_screen": 3,
        "liquidity_screen": 2,
    }
    assert (report["symbols_ever_eligible"], report["cohort_symbols"]) == (3, 3)
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_runner.py tests/research/pooled/test_power.py -q`
Expected: FAIL (`KeyError: 'bar_file_list'`, no circuit breaker, `AttributeError: module ... has no attribute 'breadth'`).

- [ ] **Step 5: Implement the runner changes**

In `runner.py`, add `CIRCUIT_BREAKER = 5  # consecutive raised fetches: the provider is down, stop now` below `_PROGRESS_SYMBOLS`. Replace `_Acquisition.__init__` and the `except` branch of `_Acquisition.fetch`:

```python
    def __init__(self, bars: BarSource, pace: Callable[[], Awaitable[None]], say: Callable[[str], None]):
        self._bars = bars
        self._pace = pace
        self._say = say
        self._consecutive = 0
        self.errors: dict[str, str] = {}  # a fetch raised: fatal once every symbol was attempted
        self.empty: dict[str, str] = {}  # the provider has no bars: recorded, never fatal
        self.files: list[str] = []  # symbol|timeframe|adjustment|<content-digest cache file name>
```

```python
        except Exception as exc:
            reason = f"{label}: {type(exc).__name__}: {exc}"
            _record(self.errors, symbol, reason)
            self._say(f"fetch failed: {symbol} {reason}")
            self._consecutive += 1
            # Each failing fetch already spent its retries (~25 s); a run of them means the
            # provider is down, so stop instead of trying every remaining symbol.
            if self._consecutive >= CIRCUIT_BREAKER:
                raise ValueError(
                    f"provider unavailable: {self._consecutive} consecutive fetches failed "
                    f"(last: {symbol} {reason}); rerun to resume from the cache"
                ) from exc
            return None
        self._consecutive = 0
```

Add below `_lines_sha256`:

```python
def _verify_bar_files(coverage: Mapping, bar_dir: Path, raw_dir: Path) -> str:
    """Re-check, by name, every bar cache file a cached cube was built from; returns a progress note.

    Cache files are named by their content digest and the shared cache loads the first by
    name, so the same first name for every listed (symbol, timeframe, adjustment) means the
    same bars. A cube built before the list was recorded is loaded with a note.
    """
    listed = coverage.get("bar_file_list")
    if listed is None:
        return "no bar file list recorded (a cube built before Part 1b); bar files not re-checked"
    if _lines_sha256(listed) != coverage["bars_sha256"]:
        raise ValueError("the cached cube's bar file list does not match its bars_sha256; delete it and rebuild")
    changed = []
    for entry in listed:
        symbol, timeframe, adjustment, name = entry.split("|")
        folder = (raw_dir if adjustment == "raw" else bar_dir) / f"{symbol}_{timeframe}"
        current = min((path.name for path in folder.glob("*.npz")), default=None)
        if current != name:
            changed.append(f"{symbol} {timeframe}/{adjustment}")
    if changed:
        raise ValueError(
            f"the cached cube was built from bar files that changed since ({len(changed)}: "
            f"{', '.join(changed[:5])}); delete it and rebuild"
        )
    return f"bar files re-checked: {len(listed)} unchanged"
```

In `build_cube_inputs`:
- construct `_Acquisition(bars, pace, say)`;
- in the cache-hit branch, right after the `missing` provenance check, add `say(_verify_bar_files(cube.coverage, bar_dir, raw_dir))`;
- in the `provenance` dict of a new build, add `"bar_file_list": sorted(acquisition.files),`.

Leave `_PROVENANCE` unchanged: the list is optional so older cubes still load.

- [ ] **Step 6: Implement breadth in `power.py`**

Add `from collections import Counter, defaultdict` to the imports, add `"breadth"` to `__all__`, and add above `execute_power_check`:

```python
def breadth(cube: LabelCube, cohort) -> dict:
    """How wide the cube is: eligible names per session (overall, by year) and eligible cells by source.

    Reads eligibility only, never a label. ``cohort`` is a ``LoadedCohort`` (its
    ``cohort.source_of`` names each symbol's sources).
    """
    view = cube.window(cube.sessions[0], cube.sessions[-1])
    per_session = view.eligible.sum(axis=1)
    by_year: dict[str, list[int]] = defaultdict(list)
    for day, count in zip(view.sessions, per_session.tolist(), strict=True):
        by_year[str(day.year)].append(int(count))
    cells = view.eligible.sum(axis=0)
    by_source: Counter[str] = Counter()
    for symbol, count in zip(view.symbols, cells.tolist(), strict=True):
        by_source["+".join(cohort.cohort.source_of(symbol)) or "none"] += int(count)
    return {
        "eligible_per_session": {
            "mean": float(per_session.mean()),
            "min": int(per_session.min()),
            "max": int(per_session.max()),
        },
        "by_year_mean": {year: float(np.mean(counts)) for year, counts in sorted(by_year.items())},
        "eligible_cells_by_source": dict(sorted(by_source.items())),
        "symbols_ever_eligible": int((cells > 0).sum()),
        "cohort_symbols": len(view.symbols),
    }
```

In `execute_power_check`, add `"breadth": breadth(built.cube, cohort),` to the success `result` dict, after `"static_used"`.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/ tests/cli/test_alpha_pooled_cli.py -q`
Expected: PASS.

- [ ] **Step 8: Stage**

```bash
git add agentic_trader/research/pooled/runner.py agentic_trader/research/pooled/power.py tests/research/pooled/test_runner.py tests/research/pooled/test_power.py
```

---

### Task 5 (controller): Run the screen, freeze cohort v2 and campaign v2, start the overnight cube build

Tasks 1–4 must be committed first. The controller runs this task, not a subagent. Every provider read stays clear of the 10:35 and 14:35 New York scans.

- [ ] **Step 1: Run the screen (a few minutes; any time outside 10:25–10:50 and 14:25–14:50 New York)**

Source the installed checkout's `.envrc` **read-only in a subshell** (never print it), then run from the worktree:

```bash
cd /Users/adamhadani/Development/agentic-trader-pooled1b && (set -a; source /Users/adamhadani/Development/agentic-trader/.envrc >/dev/null 2>&1; set +a; env -u VIRTUAL_ENV uv run copilot alpha pooled screen config/research/pooled/screen-v2.json --snapshot ~/.local/state/agentic-trader/research/equity-universe-20260917/v2/snapshot.json --output ~/agentic-trader-research/pooled-screen-v2-$(date +%Y%m%d) --cache ~/agentic-trader-research/pooled-cache-v1)
```

Expected: `"status": "completed"`, `"selected": 400`, `"cohort_symbols"` about 430–470.

- [ ] **Step 2: Sanity-check the screen without editing the rule**

- Read `screen.csv`: the first 400 rows should be ordinary listed equities (no `- Units`, `Warrant`, `Preferred` or `Fund` names).
- Exclusion counts should match the snapshot check recorded in the rulings: 751 fifth-letter, 138 symbol form, about 517 name patterns.
- If a rule defect shows (a non-common instrument in the top 400, or a common stock wrongly excluded), correct `screen-v2.json`. No cohort or campaign file exists yet, so this is still a pre-run correction. Record a ledger ruling, rerun Step 1 into a new output directory, and amend Task 1's tests to match.

- [ ] **Step 3: Freeze `cohort-v2.json`**

```bash
cd /Users/adamhadani/Development/agentic-trader-pooled1b && cp ~/agentic-trader-research/pooled-screen-v2-YYYYMMDD/cohort.json config/research/pooled/cohort-v2.json && shasum -a 256 config/research/pooled/cohort-v2.json
```

(Use the real output directory name.) The digest must equal the screen result's `cohort_sha256`.

- [ ] **Step 4: Write `campaign-v2.json` from v1 plus the spec's changes**

```bash
cd /Users/adamhadani/Development/agentic-trader-pooled1b && env -u VIRTUAL_ENV uv run python - <<'EOF'
import hashlib
import json
from pathlib import Path

windows = {
    "high52": [126, 189, 252],
    "reversal": [3, 5, 10, 21],
    "max_lottery": [5, 10, 21, 42],
    "momentum_12_1": [21, 63, 105, 126, 189, 252],
    "momentum_12_7": [84, 105, 126, 147, 168],
    "range_location": [10, 20, 40, 60, 120],
    "trend_slope": [10, 20, 40, 60, 120],
    "abnormal_volume": [5, 10, 20, 50, 100],
    "overnight_intraday": [5, 10, 21, 42],
    "price_volume_corr": [5, 10, 21, 42],
    "signed_volume": [5, 10, 20, 40],
    "hl_spread": [5, 10, 21, 42],
}
doc = json.loads(Path("config/research/pooled/campaign-v1.json").read_text())
doc["version"] = 2
doc["title"] = (
    "Pooled campaign v2: liquidity-screened cohort v2, budgeted per-family genetic search over 12 OHLCV "
    "families, checks A/B/C, one-shot lane-wide confirmation"
)
doc["cohort"] = "config/research/pooled/cohort-v2.json"
doc["cohort_sha256"] = hashlib.sha256(Path(doc["cohort"]).read_bytes()).hexdigest()
for family in doc["families"]:
    family["windows"] = windows[family["id"]]
doc["power_search"]["seed"] = 20261002
doc["null_check"] = {"replicates": 40, "max_false_acceptances": 2, "seed": 20261003}
Path("config/research/pooled/campaign-v2.json").write_text(json.dumps(doc, indent=2) + "\n")
print(hashlib.sha256(Path("config/research/pooled/campaign-v2.json").read_bytes()).hexdigest())
EOF
```

- [ ] **Step 5: Pin the frozen files in a test**

```python
# tests/research/pooled/test_campaign_v2.py
from pathlib import Path

from agentic_trader.research.pooled.campaign import load_campaign_protocol
from agentic_trader.research.pooled.cohort import load_cohort


REPO = Path(__file__).resolve().parents[3]
V2 = load_campaign_protocol(REPO / "config/research/pooled/campaign-v2.json")
V1 = load_campaign_protocol(REPO / "config/research/pooled/campaign-v1.json").protocol
WINDOWS = {
    "high52": (126, 189, 252),
    "reversal": (3, 5, 10, 21),
    "max_lottery": (5, 10, 21, 42),
    "momentum_12_1": (21, 63, 105, 126, 189, 252),
    "momentum_12_7": (84, 105, 126, 147, 168),
    "range_location": (10, 20, 40, 60, 120),
    "trend_slope": (10, 20, 40, 60, 120),
    "abnormal_volume": (5, 10, 20, 50, 100),
    "overnight_intraday": (5, 10, 21, 42),
    "price_volume_corr": (5, 10, 21, 42),
    "signed_volume": (5, 10, 20, 40),
    "hl_spread": (5, 10, 21, 42),
}


def test_campaign_v2_pins_cohort_v2_and_is_campaign_ready():
    protocol = V2.protocol
    cohort = load_cohort(REPO / protocol.cohort)
    assert protocol.version == 2 and protocol.cohort == "config/research/pooled/cohort-v2.json"
    assert protocol.cohort_sha256 == cohort.sha256
    protocol.require_campaign_ready()
    assert {f.id: f.windows for f in protocol.families} == WINDOWS
    assert protocol.power_search.seed == 20261002
    assert protocol.null_check.model_dump() == {"replicates": 40, "max_false_acceptances": 2, "seed": 20261003}


def test_campaign_v2_changes_nothing_else_from_v1():
    keep = set(type(V1).model_fields) - {
        "version",
        "title",
        "cohort",
        "cohort_sha256",
        "families",
        "power_search",
        "null_check",
    }
    assert {k: getattr(V2.protocol, k) for k in keep} == {k: getattr(V1, k) for k in keep}
    assert [f.model_copy(update={"windows": None}) for f in V2.protocol.families] == list(V1.families)
    assert V2.protocol.power_search.model_copy(update={"seed": None}) == V1.power_search


def test_cohort_v2_is_the_screen_union():
    cohort = load_cohort(REPO / "config/research/pooled/cohort-v2.json").cohort
    screened = next(source for source in cohort.sources if source.kind == "liquidity_screen")
    assert cohort.version == 2 and len(screened.symbols) == screened.screen.top == 400
    assert screened.screen.window_end.isoformat() == "2023-12-29"
    assert {source.kind for source in cohort.sources} == {"config_groups", "liquidity_screen"}
```

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_campaign_v2.py -q` (expected: PASS).

- [ ] **Step 6: Commit the frozen files**

```bash
git add config/research/pooled/cohort-v2.json config/research/pooled/campaign-v2.json tests/research/pooled/test_campaign_v2.py && git commit -m "Pooled 1b: freeze cohort v2 (liquidity screen) and campaign protocol v2"
```

- [ ] **Step 7: Start the overnight cube build and the early check A**

Start no earlier than 14:50 New York, so the roughly four-hour hourly-bar acquisition finishes long before the next 10:35 scan. Launch with `nohup` (background monitors are capped at 30 minutes; arm a fresh Monitor or a ScheduleWakeup fallback to check the log):

```bash
cd /Users/adamhadani/Development/agentic-trader-pooled1b && (set -a; source /Users/adamhadani/Development/agentic-trader/.envrc >/dev/null 2>&1; set +a; nohup env -u VIRTUAL_ENV uv run copilot alpha pooled power config/research/pooled/campaign-v2.json --output ~/agentic-trader-research/pooled-power-a-v2-early-$(date +%Y%m%d) --cache ~/agentic-trader-research/pooled-cache-v1 --workers 6 > ~/agentic-trader-research/logs/pooled-power-a-v2-early-$(date +%Y%m%d).log 2>&1 &)
```

When it finishes:
- record `status`, `curve`, `breadth` and the cube SHA-256 in the ledger;
- report breadth to the operator in one line.

A failed fetch fails closed; rerun with a new `--output` and the same `--cache`. A gate failure is recorded and does not stop the build of Tasks 6–15. The binding check A is the Task 16 rerun.

---

### Task 6: Family-restricted genetic search

**Files:**
- Modify: `agentic_trader/research/alpha/search.py`
- Test: `tests/research/test_alpha_search_families.py`

**Interfaces:**
- Produces: `TypedGeneticSearch(seed, archive_size=32, *, seeds=SEED_EXPRESSIONS, operators=MUTATION_OPERATORS, constant_windows=WINDOWS, wrapper_windows=WINDOWS)`, with attributes `seeds`, `operators`, `constant_windows` and `wrapper_windows`. The defaults reproduce the per-symbol miner exactly.

- [ ] **Step 1: Write the failing tests**

```python
# tests/research/test_alpha_search_families.py
import ast
import hashlib

from agentic_trader.research.alpha import search as search_module
from agentic_trader.research.alpha.dsl import AlphaDSLSyntaxError
from agentic_trader.research.alpha.search import WINDOWS, TypedGeneticSearch


FAMILY_SEEDS = ("close / ts_max(high, 252)", "close / ts_max(high, 126)")
OPERATORS = ("ts_mean", "ts_rank", "zscore", "delay", "ema")
CONSTANTS = (126, 189, 252)


def _fitness(expression: str) -> float:
    return int(hashlib.sha256(expression.encode()).hexdigest()[:8], 16) % 1000 / 1000


def _drive(search: TypedGeneticSearch, n: int = 80) -> list[str]:
    out = []
    for _ in range(n):
        expression = search.ask()
        out.append(expression)
        search.tell(expression, _fitness(expression))
    return out


def _calls(expression: str) -> set[str]:
    tree = ast.parse(expression, mode="eval")
    return {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}


def _int_constants(expression: str) -> set[int]:
    tree = ast.parse(expression, mode="eval")
    return {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and type(node.value) is int}


def test_the_default_search_proposes_exactly_what_it_did_before_families_existed():
    # Digest recorded on the unmodified search (378ab95): the per-symbol miner must not change.
    proposals = _drive(TypedGeneticSearch(seed=11, archive_size=16))
    digest = hashlib.sha256("\n".join(proposals).encode()).hexdigest()
    assert digest == "5ef01834e977f3b8e2eb5781d74834470adbd96c5320fe562bae6f23fae8cbad"


def test_a_family_search_stays_inside_its_seeds_operators_and_windows():
    search = TypedGeneticSearch(
        seed=3, archive_size=16, seeds=FAMILY_SEEDS, operators=OPERATORS, constant_windows=CONSTANTS
    )
    proposals = _drive(search, 120)
    assert proposals[:2] == ["close / ts_max(high, 252)", "close / ts_max(high, 126)"]
    assert all(_calls(expression) <= {"ts_max", *OPERATORS} for expression in proposals)
    assert all(_int_constants(expression) <= set(CONSTANTS) | set(WINDOWS) for expression in proposals)


def test_an_empty_archive_draws_parents_from_the_family_seeds():
    search = TypedGeneticSearch(seed=9, seeds=FAMILY_SEEDS, operators=OPERATORS, constant_windows=CONSTANTS)
    proposals = [search.ask() for _ in range(30)]  # nothing is told: the archive stays empty
    assert all(_calls(expression) <= {"ts_max", *OPERATORS} for expression in proposals)


def test_the_mutation_fallback_returns_a_family_seed_never_a_global_one(monkeypatch):
    search = TypedGeneticSearch(seed=5, seeds=FAMILY_SEEDS, operators=OPERATORS, constant_windows=CONSTANTS)

    def refuse(expression):
        raise AlphaDSLSyntaxError("refused")

    monkeypatch.setattr(search_module, "canonical_expression", refuse)
    for _ in range(20):
        assert search.mutate("close / ts_max(high, 252)") in FAMILY_SEEDS
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/test_alpha_search_families.py -q`
Expected: the digest test PASSES (nothing changed yet); the other three FAIL with `TypeError: ... unexpected keyword argument 'seeds'`.

- [ ] **Step 3: Parameterize `TypedGeneticSearch`**

Add `from collections.abc import Sequence` to the imports. Replace `__init__`:

```python
    def __init__(
        self,
        seed: int,
        archive_size: int = 32,
        *,
        seeds: Sequence[str] = SEED_EXPRESSIONS,
        operators: Sequence[str] = MUTATION_OPERATORS,
        constant_windows: Sequence[int] = WINDOWS,
        wrapper_windows: Sequence[int] = WINDOWS,
    ):
        self.rng = random.Random(seed)
        self.archive_size = archive_size
        self.archive: dict[str, float] = {}
        self.seen: set[str] = set()
        # A family-restricted search (the pooled campaign) passes its own seeds, operators and
        # the windows constants may mutate to; the defaults are the per-symbol miner's, unchanged.
        self.seeds = tuple(seeds)
        self.operators = tuple(operators)
        self.constant_windows = tuple(constant_windows)
        self.wrapper_windows = tuple(wrapper_windows)
        # Evaluate every declared family once before spending the remaining
        # budget on mutations/crossovers.  Previously ``ask`` immediately
        # wrapped a seed in a mutation, so newly added DSL families could be
        # absent from an otherwise successful genetic campaign.
        self.seed_queue = [canonical_expression(expression) for expression in self.seeds]
        self.crossover_count = 0
        self.mutation_count = 0
```

In `mutate`:
- replace `self.rng.choice(windows).value = self.rng.choice(WINDOWS)` with `self.rng.choice(windows).value = self.rng.choice(self.constant_windows)`;
- replace `op = self.rng.choice(MUTATION_OPERATORS)` / `proposed = f"{op}({expression},{self.rng.choice(WINDOWS)})"` with `op = self.rng.choice(self.operators)` / `proposed = f"{op}({expression},{self.rng.choice(self.wrapper_windows)})"`;
- replace the fallback `return self.rng.choice(SEED_EXPRESSIONS)` with `return self.rng.choice(self.seeds)`.

In `ask`, replace `parents = sorted(self.archive) or list(SEED_EXPRESSIONS)` with `parents = sorted(self.archive) or list(self.seeds)`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/test_alpha_search_families.py tests/research/test_alpha_discovery.py -q`
Expected: PASS (including the pinned digest and the miner's existing tests).

- [ ] **Step 5: Stage**

```bash
git add agentic_trader/research/alpha/search.py tests/research/test_alpha_search_families.py
```

---
### Task 7: Split discovery for the search; lane-wide in-memory ledger; cohort-checked windows

**Files:**
- Modify: `agentic_trader/research/pooled/campaign.py`
- Modify: `agentic_trader/research/pooled/power.py`
- Test: `tests/research/pooled/test_campaign_stages.py`

**Interfaces:**
- Produces (`campaign.py`):
  - `DiscoveryScore(row: dict, cells: frozenset[tuple[int, int]])`;
  - `DiscoveryEvaluator(view, protocol).score(formula) -> DiscoveryScore`;
  - `finish_discovery(scores, protocol) -> (list[dict], list[str])`;
  - `later_stages(formulas, discovery, carried, windows, ledger, protocol, *, campaign_id, on_frozen=None) -> dict`;
  - `run_stages` unchanged in signature and output;
  - `CampaignWindows(cube, windows, cohort_sha256)` raises `ValueError` when `cube.cohort_sha256` differs;
  - `InMemoryLedger` refuses any overlapping interval, whatever the cohort.
- Produces (`power.py`): `synthetic_cube(base, calendar, rng, block_mean, *, cohort_sha256="synthetic")`.

- [ ] **Step 1: Write the failing tests (append to `tests/research/pooled/test_campaign_stages.py`)**

Add to the file's imports: `import hashlib`, `import json`, `from agentic_trader.research.setups.study import _finite_json`. Also add `DiscoveryEvaluator` and `finish_discovery` to the `from agentic_trader.research.pooled.campaign import (...)` list. Then append:

```python
def pin_cube() -> LabelCube:
    days = tuple(sessions_between(PROTOCOL.windows.discovery[0], PROTOCOL.windows.confirmation[1]))
    rng = np.random.default_rng(42)
    s, n = len(days), 24
    eligible = rng.random((s, n)) > 0.1
    labelled = eligible & (rng.random((s, n)) > 0.05)
    r = rng.normal(0.02, 1.1, (s, n))
    r[:, 3] += 2.0
    r[:, 7] += 1.0
    r = np.where(labelled, r, np.nan)
    arrays = {
        "eligible": eligible,
        "labelled": labelled,
        "r_gross": r + 0.01,
        "r_cost": r,
        "holding": rng.integers(1, 3, (s, n)).astype(np.int16),
        "hit": np.ones((s, n), np.int8),
        "tiebreak": rng.integers(0, 2**62, (s, n)).astype(np.uint64),
        "dollar_volume": np.ones((s, n)),
    }
    return LabelCube(
        spec_identity="spec",
        cohort_sha256="c" * 64,
        sessions=days,
        symbols=tuple(f"S{j}" for j in range(n)),
        arrays=arrays,
        coverage={},
    )


def pin_formulas(n_names: int = 24) -> list[ScoredFormula]:
    formulas = []
    for column in (3, 7, 11):

        def panel(view, column=column):
            scores = np.zeros(view.eligible.shape)
            scores[:, column] = 1.0
            return scores, np.ones_like(view.eligible)

        formulas.append(ScoredFormula(formula_id=f"fav-{column}", nodes=3 + column % 4, panel=panel))
    for seed in range(5):

        def panel(view, seed=seed):
            field = np.random.default_rng(seed).normal(size=(view.offset + len(view.sessions), n_names))
            return field[view.offset :], np.ones_like(view.eligible)

        formulas.append(ScoredFormula(formula_id=f"noise-{seed}", nodes=4, panel=panel))
    return formulas


def test_run_stages_output_is_unchanged_by_the_discovery_split():
    # Digest recorded on the unsplit stages (378ab95): power check A's machinery must not change.
    cube = pin_cube()
    outcome = run_stages(
        pin_formulas(), CampaignWindows(cube, PROTOCOL.windows, "c" * 64), InMemoryLedger(), PROTOCOL, campaign_id="pin"
    )
    encoded = json.dumps(_finite_json(outcome), sort_keys=True, default=str)
    assert outcome["status"] == "confirmed" and outcome["confirmed"] == ["fav-3"]
    assert hashlib.sha256(encoded.encode()).hexdigest() == (
        "13a006d5bfa98383b98496d3f1660acb630442f54be0f5427894f78bea84386e"
    )


def test_scoring_one_formula_at_a_time_matches_the_batch_discovery():
    view = CampaignWindows(pin_cube(), PROTOCOL.windows, "c" * 64).discovery()
    formulas = pin_formulas()
    evaluator = DiscoveryEvaluator(view, PROTOCOL)
    rows, carried = finish_discovery([evaluator.score(f) for f in reversed(formulas)], PROTOCOL)
    batch_rows, batch_carried = _discovery(formulas, view, PROTOCOL)
    assert carried == batch_carried

    def encoded(items):
        return sorted(json.dumps(_finite_json(row), sort_keys=True) for row in items)

    assert encoded(rows) == encoded(batch_rows)


def test_dedupe_runs_before_the_carry_cap():
    favourites = tuple(range(7))
    r = returns(favourites=favourites)
    for column in favourites:
        r[DISC, column] += 0.3 + 0.1 * column
    # twin6 repeats f6's picks with one more node (lower fitness): it is a duplicate. Capping
    # before deduplicating would carry only four distinct formulas.
    rows, carried = discover(cube_of(r), *[favourite(f"f{c}", c) for c in favourites], favourite("twin6", 6, nodes=4))
    assert rows["twin6"]["passes"]
    assert carried == ["f6", "f5", "f4", "f3", "f2"]


def test_the_recent_window_starts_strictly_after_the_twelve_month_boundary():
    boundary = bisect_left(DAYS, date(2025, 7, 31))
    assert DAYS[boundary] == date(2025, 7, 31)
    r = returns()
    r[CONF, 0] += 0.5
    r[boundary, 0] -= 1000.0  # the boundary session itself is outside the recent window
    assert confirm(cube_of(r), [favourite("f", 0)], ["f"])[0]["f"]["recent_edge"] > 0
    r[boundary, 0] += 1000.0
    r[boundary + 1, 0] -= 1000.0  # the next session is inside it
    assert confirm(cube_of(r), [favourite("f", 0)], ["f"])[0]["f"]["recent_edge"] < 0


def test_campaign_windows_refuse_a_cube_built_for_another_cohort():
    with pytest.raises(ValueError, match="built for cohort"):
        CampaignWindows(synthetic_cube(), PROTOCOL.windows, cohort_sha256="d" * 64)


def test_the_in_memory_ledger_refuses_an_overlap_whatever_the_cohort():
    ledger = InMemoryLedger()
    ledger.consume_confirmation(
        cohort_sha256="a" * 64, interval=(date(2024, 1, 2), date(2026, 7, 31)), campaign_id="one", candidates=("x",)
    )
    with pytest.raises(ValueError, match="already consumed by campaign one"):
        ledger.consume_confirmation(
            cohort_sha256="b" * 64, interval=(date(2026, 7, 1), date(2027, 7, 1)), campaign_id="two", candidates=()
        )
    ledger.consume_confirmation(
        cohort_sha256="b" * 64, interval=(date(2026, 8, 3), date(2027, 7, 30)), campaign_id="three", candidates=()
    )


def test_later_stages_call_on_frozen_before_the_confirmation_is_consumed():
    cube = edge_in({DISC: 0.5, SEL: 0.5, CONF: 0.5})
    windows = windows_of(cube)
    events = []

    class Spy(InMemoryLedger):
        def consume_confirmation(self, **kwargs):
            events.append(("consume", kwargs["candidates"]))
            super().consume_confirmation(**kwargs)

    formulas = [favourite("f", 0)]
    discovery, carried = _discovery(formulas, windows.discovery(), P1)
    outcome = later_stages(
        formulas,
        discovery,
        carried,
        windows,
        Spy(),
        P1,
        campaign_id="hook",
        on_frozen=lambda frozen: events.append(("frozen", tuple(frozen))),
    )
    assert outcome["status"] == "confirmed"
    assert events == [("frozen", ("f",)), ("consume", ("f",))]
```

Also add `later_stages` to the campaign import list.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_campaign_stages.py -q`
Expected:
- FAIL with `ImportError: cannot import name 'DiscoveryEvaluator'`.
- After a temporary import fix, the windows and ledger tests would still fail. The pin test must already pass on the unchanged code; it guards the refactor.

- [ ] **Step 3: Split discovery and add `later_stages` in `campaign.py`**

Replace `_discovery` with:

```python
@dataclass(frozen=True)
class DiscoveryScore:
    """One formula's discovery result row and its pick cells (for dedupe and overlap)."""

    row: dict[str, Any]
    cells: frozenset[tuple[int, int]]


class DiscoveryEvaluator:
    """Scores formulas on the discovery view one at a time, so a search can use each fitness."""

    def __init__(self, view: CubeView, protocol: CampaignProtocol):
        self._view = view
        self._protocol = protocol
        self._draws = _draws(view, protocol.bootstrap.discovery_draws, protocol)
        self._bounds = np.linspace(0, len(view.sessions), protocol.discovery_gate.blocks + 1).astype(int)

    def score(self, formula: ScoredFormula) -> DiscoveryScore:
        gate = self._protocol.discovery_gate
        view = self._view
        picks = _picks(formula, view, self._protocol.k)
        table = session_table(picks, view, purge=True)
        edge = paired_edge_test(table, self._draws)
        leg = leg_mean_test(table, self._draws)
        block_means = []
        for lo, hi in pairwise(self._bounds.tolist()):
            sub = session_table(_restrict(picks, lo, hi), view.sub(lo, hi), purge=True)
            weight = np.isfinite(sub.edge)
            block_means.append(float(sub.edge[weight].mean()) if weight.any() else float("nan"))
        positive = sum(1 for m in block_means if np.isfinite(m) and m > 0)
        t = edge["t"]
        passes = bool(
            np.isfinite(t)
            and t >= gate.min_t
            and positive >= gate.min_positive_blocks
            and np.isfinite(leg["mean"])
            and leg["mean"] > 0
            and edge["n_sessions"] >= gate.min_sessions
        )
        fitness = (t if np.isfinite(t) else float("-inf")) - self._protocol.complexity_penalty_per_node * formula.nodes
        row = {
            "formula_id": formula.formula_id,
            "edge": edge,
            "leg": leg,
            "block_means": block_means,
            "passes": passes,
            "fitness": fitness,
        }
        return DiscoveryScore(row=row, cells=frozenset(picks.cells()))


def finish_discovery(scores: Sequence[DiscoveryScore], protocol: CampaignProtocol) -> tuple[list[dict], list[str]]:
    """Every discovery row, and the gate's survivors deduplicated by pick overlap, then capped."""
    results = [score.row for score in scores]
    cells = {score.row["formula_id"]: score.cells for score in scores}
    passing = sorted((r for r in results if r["passes"]), key=lambda r: (-r["fitness"], r["formula_id"]))
    kept: list[dict[str, Any]] = []
    for candidate in passing:
        if all(
            _jaccard(cells[candidate["formula_id"]], cells[k["formula_id"]]) < protocol.dedupe_jaccard for k in kept
        ):
            kept.append(candidate)
    return results, [r["formula_id"] for r in kept[: protocol.discovery_gate.carry]]


def _discovery(
    formulas: Sequence[ScoredFormula], view: CubeView, protocol: CampaignProtocol
) -> tuple[list[dict], list[str]]:
    evaluator = DiscoveryEvaluator(view, protocol)
    return finish_discovery([evaluator.score(formula) for formula in formulas], protocol)
```

Replace `run_stages` with:

```python
def later_stages(
    formulas: Sequence[ScoredFormula],
    discovery: list[dict],
    carried: list[str],
    windows: CampaignWindows,
    ledger: Ledger,
    protocol: CampaignProtocol,
    *,
    campaign_id: str,
    on_frozen: Callable[[list[str]], None] | None = None,
) -> dict:
    """Selection and confirmation after discovery.

    ``on_frozen`` runs once the candidates are frozen, before the confirmation interval is
    consumed (the campaign writes the frozen documents there).
    """
    outcome: dict = {
        "discovery": discovery,
        "carried": carried,
        "selection": [],
        "frozen": [],
        "confirmation": [],
        "confirmed": [],
    }
    if not carried:
        return {**outcome, "status": "no_finalists"}
    selection, frozen = _selection(formulas, carried, discovery, windows.selection(), protocol)
    outcome.update(selection=selection, frozen=frozen)
    if not frozen:
        return {**outcome, "status": "no_confirmation_candidates"}
    if on_frozen is not None:
        on_frozen(list(frozen))
    view = windows.confirmation(ledger, campaign_id=campaign_id, candidates=tuple(frozen))
    confirmation, confirmed = _confirmation(formulas, frozen, view, protocol)
    outcome.update(confirmation=confirmation, confirmed=confirmed)
    return {**outcome, "status": "confirmed" if confirmed else "none_confirmed"}


def run_stages(
    formulas: Sequence[ScoredFormula],
    windows: CampaignWindows,
    ledger: Ledger,
    protocol: CampaignProtocol,
    *,
    campaign_id: str,
) -> dict:
    ids = [formula.formula_id for formula in formulas]
    if len(set(ids)) != len(ids):
        duplicated = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"duplicate formula_id in campaign: {duplicated}")
    discovery, carried = _discovery(formulas, windows.discovery(), protocol)
    return later_stages(formulas, discovery, carried, windows, ledger, protocol, campaign_id=campaign_id)
```

In `CampaignWindows.__init__`, add first:

```python
        if cube.cohort_sha256 != cohort_sha256:
            raise ValueError(f"the cube was built for cohort {cube.cohort_sha256[:16]}, not {cohort_sha256[:16]}")
```

In `InMemoryLedger.consume_confirmation`, drop the cohort condition (the pooled confirmation ledger is lane-wide):

```python
        for item in self.consumed:
            if not (end < item["interval"][0] or item["interval"][1] < start):
                raise ValueError(f"confirmation interval already consumed by campaign {item['campaign_id']}")
```

Update its docstring to: `"""Power checks and tests: the journal ledger's lane-wide single-use rule, in memory."""`. Add `"DiscoveryEvaluator"`, `"DiscoveryScore"`, `"finish_discovery"` and `"later_stages"` to `__all__`.

- [ ] **Step 4: Pass the cohort through power check A's synthetic cubes (`power.py`)**

Change `synthetic_cube`'s signature to `def synthetic_cube(base: CubeView, calendar: Sequence[date], rng: np.random.Generator, block_mean: float, *, cohort_sha256: str = "synthetic") -> LabelCube:` and pass `cohort_sha256=cohort_sha256` to its `LabelCube(...)`. In `_replicate`, call `synthetic_cube(base, calendar, rng, protocol.bootstrap.block_mean, cohort_sha256=cohort_sha256)`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/ -q`
Expected: PASS (the pinned digest is unchanged; power check A's tests still pass).

- [ ] **Step 6: Stage**

```bash
git add agentic_trader/research/pooled/campaign.py agentic_trader/research/pooled/power.py tests/research/pooled/test_campaign_stages.py
```

---

### Task 8: Real formulas as label-blind, cached `ScoredFormula`s

**Files:**
- Modify: `agentic_trader/research/pooled/formula.py`
- Create: `agentic_trader/research/pooled/scoring.py`
- Test: `tests/research/pooled/test_scoring.py`, `tests/research/pooled/test_formula.py`

**Interfaces:**
- Consumes: `campaign.py:ScoredFormula`, `_calls`; `research/alpha/search.py:canonical_expression`.
- Produces:
  - `formula.py`: `prepare_daily(adjusted) -> dict[str, DataFrame]`; `evaluate_prepared(expression, prepared, trading_days, sessions, symbols) -> ndarray`. `evaluate_panel` is unchanged in behaviour and calls them.
  - `scoring.py`:
    - `formula_id(expression) -> str` (16 hex);
    - `Vetted(expression, reason)`;
    - `vet_expression(expression, forbidden, seen) -> Vetted`, with reasons `"does not compile"`, `"duplicate"`, `"forbidden operator"`, `"not dimensionless"`;
    - `ScoreBook(adjusted, trading_days, sessions, symbols, *, capacity=64)` with `.panel(expression)`, `.formula(expression) -> ScoredFormula`, `.sessions`, `.symbols`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/research/pooled/test_formula.py`:

```python
def test_a_missing_previous_session_bar_gives_no_score_not_an_older_one():
    days = weekdays(date(2021, 1, 4), 30)
    closes = list(np.linspace(50, 60, 30))
    frame = daily(days[:24] + days[25:], closes[:24] + closes[25:])  # no bar on days[24]
    out = evaluate_panel("roc(close, 5)", {"AAA": frame}, days, [days[25], days[26]], ["AAA"])
    assert np.isnan(out[0, 0])  # D-1 = days[24] has no bar
    assert np.isfinite(out[1, 0])


def test_quantile_filter_bounds_are_inclusive_and_min_quantile_keeps_the_top():
    values = np.array([[1.0, 2.0, 3.0, 4.0, 5.0]])
    eligible = np.ones((1, 5), bool)
    low = Formula(
        score="roc(close, 5)", filters=(FormulaFilter(expression="ts_max(returns, 21)", max_quantile=0.5),), k=1
    )
    high = Formula(
        score="roc(close, 5)", filters=(FormulaFilter(expression="ts_max(returns, 21)", min_quantile=0.5),), k=1
    )
    assert allowed_mask(low, [values], eligible).tolist() == [[True, True, True, False, False]]
    assert allowed_mask(high, [values], eligible).tolist() == [[False, False, True, True, True]]


def test_a_filter_quantile_is_taken_over_eligible_names_only():
    values = np.array([[1.0, 2.0, 3.0, 100.0]])
    eligible = np.array([[True, True, True, False]])
    high = Formula(
        score="roc(close, 5)", filters=(FormulaFilter(expression="ts_max(returns, 21)", min_quantile=0.5),), k=1
    )
    assert allowed_mask(high, [values], eligible).tolist() == [[False, True, True, False]]
```

Create `tests/research/pooled/test_scoring.py`:

```python
from datetime import date

import numpy as np
import pytest

from agentic_trader.research.pooled import scoring
from agentic_trader.research.pooled.formula import evaluate_panel, expression_nodes
from agentic_trader.research.pooled.scoring import ScoreBook, formula_id, vet_expression
from tests.research.pooled.test_formula import daily, weekdays


DAYS = weekdays(date(2021, 1, 4), 60)
SESSIONS = tuple(DAYS[20:])
SYMBOLS = ("AAA", "BBB", "CCC")  # CCC has no bars
ADJUSTED = {
    "AAA": daily(DAYS, list(np.linspace(50, 80, 60))),
    "BBB": daily(DAYS, list(np.linspace(90, 40, 60))),
}
FORBIDDEN = ("REALIZED_VOL", "TS_STD", "TS_MAD")


class BlindView:
    """A view whose label arrays raise when touched: a score panel may read none of them."""

    def __init__(self, sessions, symbols, offset):
        self.sessions = tuple(sessions)
        self.symbols = tuple(symbols)
        self.offset = offset
        self.eligible = np.ones((len(sessions), len(symbols)), bool)

    def __getattr__(self, name):
        raise AssertionError(f"a score panel read {name}")


@pytest.mark.parametrize(
    ("expression", "seen", "canonical", "reason"),
    [
        ("roc(close,5)", (), "roc(close, 5)", None),
        ("roc(close,5)", ("roc(close, 5)",), "roc(close, 5)", "duplicate"),
        ("zscore(ts_std(returns, 20), 10)", (), "zscore(ts_std(returns, 20), 10)", "forbidden operator"),
        ("ts_slope(close, 20)", (), "ts_slope(close, 20)", "not dimensionless"),
        ("roc(close, ", (), "roc(close, ", "does not compile"),
        ("nosuchop(close, 5)", (), "nosuchop(close, 5)", "does not compile"),
    ],
)
def test_vet_expression_decides_before_anything_is_charged(expression, seen, canonical, reason):
    vetted = vet_expression(expression, FORBIDDEN, set(seen))
    assert (vetted.expression, vetted.reason) == (canonical, reason)


def test_formula_ids_hash_the_canonical_expression():
    assert formula_id("roc(close,5)") == formula_id("roc(close, 5)")
    assert len(formula_id("roc(close, 5)")) == 16
    assert formula_id("roc(close, 5)") != formula_id("roc(close, 10)")


def test_score_panels_never_touch_labels_and_slice_the_full_calendar_at_the_view_offset():
    formula = ScoreBook(ADJUSTED, DAYS, SESSIONS, SYMBOLS).formula("roc(close, 5)")
    scores, allowed = formula.panel(BlindView(SESSIONS[10:20], SYMBOLS, offset=10))
    expected = evaluate_panel("roc(close, 5)", ADJUSTED, DAYS, SESSIONS[10:20], SYMBOLS)
    np.testing.assert_array_equal(scores, expected)
    assert allowed.shape == (10, 3) and allowed.all()
    assert np.isnan(scores[:, 2]).all()
    assert formula.formula_id == formula_id("roc(close,5)")
    assert formula.nodes == expression_nodes("roc(close, 5)")


def test_the_book_keeps_a_bounded_lru_and_recomputes_evicted_panels_identically(monkeypatch):
    calls = []
    real = scoring.evaluate_prepared

    def counting(expression, *args):
        calls.append(expression)
        return real(expression, *args)

    monkeypatch.setattr(scoring, "evaluate_prepared", counting)
    book = ScoreBook(ADJUSTED, DAYS, SESSIONS, SYMBOLS, capacity=2)
    first = book.panel("roc(close, 5)").copy()
    book.panel("roc(close, 10)")
    book.panel("roc(close, 5)")  # a hit refreshes it
    book.panel("roc(close, 20)")  # evicts roc(close, 10), the least recently used
    book.panel("roc(close, 5)")  # still cached
    book.panel("roc(close, 10)")  # recomputed
    assert calls == ["roc(close, 5)", "roc(close, 10)", "roc(close, 20)", "roc(close, 10)"]
    np.testing.assert_array_equal(book.panel("roc(close, 5)"), first)
    assert not book.panel("roc(close, 5)").flags.writeable


def test_a_view_from_another_calendar_or_cohort_is_refused():
    formula = ScoreBook(ADJUSTED, DAYS, SESSIONS, SYMBOLS).formula("roc(close, 5)")
    with pytest.raises(ValueError, match="calendar"):
        formula.panel(BlindView(DAYS[:5], SYMBOLS, offset=0))
    with pytest.raises(ValueError, match="symbols"):
        formula.panel(BlindView(SESSIONS[:5], ("AAA",), offset=0))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_scoring.py tests/research/pooled/test_formula.py -q`
Expected:
- The three new formula tests PASS: they pin Part 1a behaviour that was previously untested.
- `test_scoring.py` FAILS with `ModuleNotFoundError: No module named 'agentic_trader.research.pooled.scoring'`.

- [ ] **Step 3: Split `evaluate_panel` in `formula.py`**

Replace `evaluate_panel` with:

```python
def prepare_daily(adjusted: Mapping[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Adjusted daily bars keyed by New York session (a DatetimeIndex), ready for repeated scoring."""
    prepared: dict[str, pd.DataFrame] = {}
    for symbol, frame in adjusted.items():
        if frame is None or frame.empty:
            continue
        keyed = _by_session(frame)
        prepared[symbol] = keyed.set_axis(pd.DatetimeIndex(keyed.index))
    return prepared


def evaluate_prepared(
    expression: str,
    prepared: Mapping[str, pd.DataFrame],
    trading_days: Sequence[date],
    sessions: Sequence[date],
    symbols: Sequence[str],
) -> np.ndarray:
    """``evaluate_panel`` on bars already keyed by ``prepare_daily``."""
    position = {day: i for i, day in enumerate(trading_days)}
    # A session with no earlier trading day has no completed bar to read: NaT reindexes to NaN
    # (a bare ``- 1`` would wrap to the last day and leak the future).
    previous = pd.DatetimeIndex([trading_days[position[day] - 1] if position[day] > 0 else pd.NaT for day in sessions])
    out = np.full((len(sessions), len(symbols)), np.nan)
    evaluator = AlphaExpressionEvaluator()
    for col, symbol in enumerate(symbols):
        keyed = prepared.get(symbol)
        if keyed is None:
            continue
        series = evaluator.evaluate(expression, keyed)
        out[:, col] = series.reindex(previous).to_numpy(float)
    out[~np.isfinite(out)] = np.nan
    return out


def evaluate_panel(
    expression: str,
    adjusted: Mapping[str, pd.DataFrame],
    trading_days: Sequence[date],
    sessions: Sequence[date],
    symbols: Sequence[str],
) -> np.ndarray:
    """[S, N] values known at each decision: the expression on adjusted daily bars, read at D-1."""
    return evaluate_prepared(expression, prepare_daily(adjusted), trading_days, sessions, symbols)
```

Add `"evaluate_prepared"` and `"prepare_daily"` to `__all__`.

- [ ] **Step 4: Write `scoring.py`**

```python
# agentic_trader/research/pooled/scoring.py
"""Real DSL formulas as campaign ``ScoredFormula`` objects: label-blind, causal and cached.

A score panel is computed once per canonical expression over the cube's full session
calendar, from adjusted daily bars read at D-1 (``evaluate_prepared``), and kept in a
bounded LRU. A formula's panel reads only a view's symbols, offset, sessions and the shape
of ``eligible``, never a label. ``vet_expression`` decides, before anything is charged or
evaluated, whether a proposal may be scored.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from agentic_trader.research.alpha.search import canonical_expression
from agentic_trader.research.pooled.campaign import ScoredFormula, _calls
from agentic_trader.research.pooled.cube import CubeView
from agentic_trader.research.pooled.formula import (
    evaluate_prepared,
    expression_nodes,
    prepare_daily,
    require_dimensionless,
)


__all__ = ["DEFAULT_CAPACITY", "FORMULA_ID_HEX", "ScoreBook", "Vetted", "formula_id", "vet_expression"]

FORMULA_ID_HEX = 16
DEFAULT_CAPACITY = 64  # panels per process; one panel is sessions x symbols float64 (~9 MB for v2)


def formula_id(expression: str) -> str:
    """The campaign's formula id: the first 16 hex digits of SHA-256 of the canonical expression."""
    return hashlib.sha256(canonical_expression(expression).encode()).hexdigest()[:FORMULA_ID_HEX]


@dataclass(frozen=True)
class Vetted:
    expression: str  # canonical when it compiled, else as proposed
    reason: str | None  # None: the proposal may be charged and evaluated


def vet_expression(expression: str, forbidden: Iterable[str], seen: Collection[str]) -> Vetted:
    """Whether a proposal may be charged and scored; the first failing reason otherwise."""
    try:
        canonical = canonical_expression(expression)
    except SyntaxError, ValueError:  # AlphaDSLSyntaxError is a ValueError
        return Vetted(expression, "does not compile")
    if canonical in seen:
        return Vetted(canonical, "duplicate")
    if {name.lower() for name in forbidden} & _calls(canonical):
        return Vetted(canonical, "forbidden operator")
    try:
        require_dimensionless(canonical)
    except ValueError:
        return Vetted(canonical, "not dimensionless")
    return Vetted(canonical, None)


class ScoreBook:
    """Score panels on the cube's full calendar, one per canonical expression, in a bounded LRU."""

    def __init__(
        self,
        adjusted: Mapping[str, pd.DataFrame],
        trading_days: Sequence[date],
        sessions: Sequence[date],
        symbols: Sequence[str],
        *,
        capacity: int = DEFAULT_CAPACITY,
    ):
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._prepared = prepare_daily(adjusted)
        self._trading_days = tuple(trading_days)
        self._sessions = tuple(sessions)
        self._symbols = tuple(symbols)
        self._capacity = capacity
        self._panels: OrderedDict[str, np.ndarray] = OrderedDict()

    @property
    def sessions(self) -> tuple[date, ...]:
        return self._sessions

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._symbols

    def panel(self, expression: str) -> np.ndarray:
        """[sessions, symbols] scores on the full calendar, read-only; computed once while cached."""
        canonical = canonical_expression(expression)
        cached = self._panels.get(canonical)
        if cached is not None:
            self._panels.move_to_end(canonical)
            return cached
        values = evaluate_prepared(canonical, self._prepared, self._trading_days, self._sessions, self._symbols)
        values.flags.writeable = False
        self._panels[canonical] = values
        if len(self._panels) > self._capacity:
            self._panels.popitem(last=False)
        return values

    def formula(self, expression: str) -> ScoredFormula:
        canonical = canonical_expression(expression)

        def panel(view: CubeView) -> tuple[np.ndarray, np.ndarray]:
            # Label-blind: only the view's symbols, offset, sessions and eligible shape are read.
            if tuple(view.symbols) != self._symbols:
                raise ValueError("the view's symbols are not the score book's")
            stop = view.offset + len(view.sessions)
            if self._sessions[view.offset : stop] != tuple(view.sessions):
                raise ValueError("the view is not a window of the score book's calendar")
            return self.panel(canonical)[view.offset : stop], np.ones(view.eligible.shape, dtype=bool)

        return ScoredFormula(formula_id=formula_id(canonical), nodes=expression_nodes(canonical), panel=panel)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/ -q`
Expected: PASS.

- [ ] **Step 6: Stage**

```bash
git add agentic_trader/research/pooled/formula.py agentic_trader/research/pooled/scoring.py tests/research/pooled/test_scoring.py tests/research/pooled/test_formula.py
```

---

### Task 9: The budgeted per-family search and the full campaign run

**Files:**
- Create: `agentic_trader/research/pooled/genetic.py`
- Create: `tests/research/pooled/mini_world.py` (shared fixture for Tasks 9, 11, 12 and 13)
- Test: `tests/research/pooled/test_genetic.py`

**Interfaces:**
- Consumes: Task 6's `TypedGeneticSearch(..., seeds=, operators=, constant_windows=)`; Task 3's `Family.constant_windows` and `CampaignProtocol.family_budgets()`; Task 7's `DiscoveryEvaluator`, `DiscoveryScore`, `finish_discovery`, `later_stages`; Task 8's `ScoreBook`, `vet_expression`.
- Produces (`genetic.py`):
  - `FAMILY_SEED_STRIDE = 100`, `MAX_CONSECUTIVE_REJECTIONS = 200`;
  - `Charger` protocol (`reserve_family(family_id, budget)`, `charge(family_id, expression, formula_id, nodes)`), `NullCharger`;
  - `FamilyRun` (fields `family_id, budget, charged, errors, rejected, stopped_short, scores, formulas, expressions, records`; `.summary()`);
  - `SearchOutcome(runs)` with properties `scores`, `formulas`, `expressions`, `families`, `records`, `cells`;
  - `family_search(family, index, budget, *, protocol, evaluator, book, charger, seen) -> FamilyRun`;
  - `search_campaign(protocol, view, book, charger, *, progress=None) -> SearchOutcome`;
  - `run_search_stages(protocol, windows, book, ledger, charger, *, campaign_id, on_frozen=None, progress=None) -> (dict, SearchOutcome)`, where `on_frozen(frozen_ids, search)` runs before consumption.

- [ ] **Step 1: Write the shared mini world**

```python
# tests/research/pooled/mini_world.py
"""A small real-DSL world for the pooled campaign tests: 12 names on the campaign calendar."""

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from agentic_trader.market.session import ET_TZ
from agentic_trader.research.pooled.campaign import Family, NullCheckSpec, load_campaign_protocol
from agentic_trader.research.pooled.cube import LabelCube
from agentic_trader.research.pooled.formula import select_picks
from agentic_trader.research.pooled.runner import CubeBuild
from agentic_trader.research.pooled.scoring import ScoreBook


REPO = Path(__file__).resolve().parents[3]
V1 = load_campaign_protocol(REPO / "config/research/pooled/campaign-v1.json").protocol
COHORT = "c" * 64
NAMES = tuple(f"N{j:02d}" for j in range(12))


def weekdays(start: date, end: date) -> tuple[date, ...]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return tuple(out)


BAR_DAYS = weekdays(date(2016, 1, 4), V1.windows.confirmation[1])
SESSIONS = weekdays(V1.windows.discovery[0], V1.windows.confirmation[1])


def adjusted_bars(seed: int = 0) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    n = len(BAR_DAYS)
    index = pd.DatetimeIndex([pd.Timestamp(d).tz_localize(ET_TZ) for d in BAR_DAYS]).tz_convert("UTC")
    frames = {}
    for name in NAMES:
        close = 50.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.02, n)))
        spread = np.abs(rng.normal(0.0, 0.01, n))
        frames[name] = pd.DataFrame(
            {
                "Open": close * (1 + rng.normal(0.0, 0.005, n)),
                "High": close * (1 + spread),
                "Low": close * (1 - spread),
                "Close": close,
                "Volume": rng.lognormal(13.0, 0.4, n),
            },
            index=index,
        )
    return frames


ADJUSTED = adjusted_bars()

FAMILIES = (
    Family(
        id="reversal",
        rationale="r",
        seeds=("-1.0 * roc(close, 5)", "-1.0 * roc(close, 21)"),
        mutation_operators=("ts_mean", "zscore"),
        windows=(3, 5, 10, 21),
    ),
    Family(
        id="range_location",
        rationale="r",
        seeds=("(close - ts_min(low, 20)) / (ts_max(high, 20) - ts_min(low, 20) + 1e-6)",),
        mutation_operators=("ts_rank", "ts_mean"),
        windows=(10, 20, 40),
    ),
    Family(
        id="abnormal_volume",
        rationale="r",
        seeds=("volume / ts_mean(volume, 50)",),
        mutation_operators=("ts_mean", "zscore"),
        windows=(5, 10, 20, 50),
    ),
)


def book(capacity: int = 64) -> ScoreBook:
    return ScoreBook(ADJUSTED, BAR_DAYS, SESSIONS, NAMES, capacity=capacity)


def label_cube(seed: int = 1) -> LabelCube:
    rng = np.random.default_rng(seed)
    shape = (len(SESSIONS), len(NAMES))
    r = rng.normal(0.05, 1.0, shape)
    arrays = {
        "eligible": np.ones(shape, bool),
        "labelled": np.ones(shape, bool),
        "r_gross": r + 0.01,
        "r_cost": r,
        "holding": np.ones(shape, np.int16),
        "hit": np.ones(shape, np.int8),
        "tiebreak": rng.integers(0, 2**62, shape).astype(np.uint64),
        "dollar_volume": np.ones(shape),
    }
    coverage = {"sessions": len(SESSIONS), "skipped_sessions": {}, "unlabelled_by_year": {}, "eligible_by_year": {}}
    return LabelCube(
        spec_identity="spec", cohort_sha256=COHORT, sessions=SESSIONS, symbols=NAMES, arrays=arrays, coverage=coverage
    )


def planted_cube(expression: str, delta: float, seed: int = 1) -> LabelCube:
    """Labels with ``delta`` R added to every cell ``expression`` picks over the whole calendar."""
    cube = label_cube(seed)
    view = cube.window(cube.sessions[0], cube.sessions[-1])
    picks = select_picks(*book().formula(expression).panel(view), view, V1.k)
    return cube.with_shift(picks.session_idx, picks.symbol_idx, delta)


def cube_build(cube: LabelCube) -> CubeBuild:
    return CubeBuild(cube=cube, trading_days=BAR_DAYS, adjusted=ADJUSTED, bar_failures={}, static_used=())


def mini_protocol(**update):
    base = {
        "families": FAMILIES,
        "formula_budget": 9,
        "cohort_sha256": COHORT,
        "bootstrap": V1.bootstrap.model_copy(
            update={"discovery_draws": 200, "selection_draws": 200, "confirmation_draws": 200}
        ),
        "power_search": V1.power_search.model_copy(
            update={
                "families": ("reversal", "range_location", "abnormal_volume"),
                "seeds": 3,
                "min_recovered": 2,
                "seed": 20261002,
            }
        ),
        "null_check": NullCheckSpec(replicates=3, max_false_acceptances=0, seed=20261003),
    }
    base.update(update)
    return V1.model_copy(update=base)


class RecordingCharger:
    def __init__(self) -> None:
        self.reserved: list[tuple[str, int]] = []
        self.charged: list[tuple[str, str, str]] = []

    def reserve_family(self, family_id: str, budget: int) -> None:
        self.reserved.append((family_id, budget))

    def charge(self, family_id: str, expression: str, formula_id: str, nodes: int) -> None:
        self.charged.append((family_id, expression, formula_id))
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/research/pooled/test_genetic.py
import pytest

from agentic_trader.research.pooled import genetic
from agentic_trader.research.pooled.campaign import CampaignWindows, DiscoveryEvaluator, Family, InMemoryLedger
from agentic_trader.research.pooled.genetic import family_search, run_search_stages, search_campaign
from agentic_trader.research.pooled.scoring import formula_id
from tests.research.pooled.mini_world import (
    COHORT,
    FAMILIES,
    V1,
    RecordingCharger,
    book,
    label_cube,
    mini_protocol,
    planted_cube,
)


def discovery_view(protocol):
    return label_cube().window(*protocol.windows.discovery)


def test_each_family_reserves_its_share_and_charges_every_evaluated_formula_once():
    protocol = mini_protocol()
    charger = RecordingCharger()
    outcome = search_campaign(protocol, discovery_view(protocol), book(), charger)
    assert charger.reserved == [("reversal", 3), ("range_location", 3), ("abnormal_volume", 3)]
    assert [run.charged for run in outcome.runs] == [3, 3, 3]
    expressions = [expression for _, expression, _ in charger.charged]
    assert len(set(expressions)) == len(expressions) == 9
    assert (
        {fid for *_, fid in charger.charged} == set(outcome.formulas) == {s.row["formula_id"] for s in outcome.scores}
    )
    assert all(outcome.families[fid] == family for family, _, fid in charger.charged)
    assert [r["expression"] for r in outcome.records[:2]] == ["-1.0 * roc(close, 5)", "-1.0 * roc(close, 21)"]


def test_the_search_is_deterministic_for_a_fixed_protocol():
    protocol = mini_protocol()
    first = search_campaign(protocol, discovery_view(protocol), book(), RecordingCharger())
    second = search_campaign(protocol, discovery_view(protocol), book(), RecordingCharger())
    assert first.records == second.records


def test_forbidden_proposals_are_rejected_uncharged_and_unevaluated_until_the_grammar_runs_out():
    # One int-free seed and one forbidden wrapper: every mutation is ts_std(returns, w).
    trapped = Family(id="trapped", rationale="t", seeds=("returns",), mutation_operators=("ts_std",))
    protocol = mini_protocol(
        families=(trapped, FAMILIES[0]),
        formula_budget=6,
        search=V1.search.model_copy(update={"forbidden_operators": ("TS_STD",)}),
    )
    charger = RecordingCharger()
    outcome = search_campaign(protocol, discovery_view(protocol), book(), charger)
    run = outcome.runs[0]
    assert run.charged == 1 and len(run.scores) == 1  # the seed only
    assert run.rejected == {"forbidden operator": 8}  # one per wrapper window, then nothing new
    assert run.stopped_short.startswith("search exhausted")
    assert all("ts_std" not in expression for _, expression, _ in charger.charged)
    assert outcome.runs[1].charged == 3


def test_a_run_of_rejections_stops_the_family(monkeypatch):
    monkeypatch.setattr(genetic, "MAX_CONSECUTIVE_REJECTIONS", 3)
    trapped = Family(id="trapped", rationale="t", seeds=("returns",), mutation_operators=("ts_std",))
    protocol = mini_protocol(
        families=(trapped,), formula_budget=4, search=V1.search.model_copy(update={"forbidden_operators": ("ts_std",)})
    )
    run = search_campaign(protocol, discovery_view(protocol), book(), RecordingCharger()).runs[0]
    assert run.stopped_short == "3 consecutive rejected proposals"
    assert (run.charged, sum(run.rejected.values())) == (1, 3)


def test_an_evaluation_error_is_charged_recorded_and_the_search_goes_on():
    protocol = mini_protocol()
    evaluator = DiscoveryEvaluator(discovery_view(protocol), protocol)
    first = formula_id("-1.0 * roc(close, 5)")

    class Exploding:
        def score(self, formula):
            if formula.formula_id == first:
                raise RuntimeError("boom")
            return evaluator.score(formula)

    charger = RecordingCharger()
    run = family_search(
        protocol.families[0], 0, 3, protocol=protocol, evaluator=Exploding(), book=book(), charger=charger, seen=set()
    )
    assert (run.charged, run.errors, len(run.scores)) == (3, 1, 2)
    assert run.records[0] == {
        "family": "reversal",
        "expression": "-1.0 * roc(close, 5)",
        "formula_id": first,
        "status": "error",
        "error": "RuntimeError: boom",
    }
    assert charger.charged[0][2] == first


def test_a_planted_seed_is_carried_frozen_before_consumption_and_confirmed():
    protocol = mini_protocol()
    planted = "-1.0 * roc(close, 21)"
    events = []

    class Spy(InMemoryLedger):
        def consume_confirmation(self, **kwargs):
            events.append(("consume", kwargs["candidates"]))
            super().consume_confirmation(**kwargs)

    windows = CampaignWindows(planted_cube(planted, 1.0), protocol.windows, COHORT)
    outcome, search = run_search_stages(
        protocol,
        windows,
        book(),
        Spy(),
        RecordingCharger(),
        campaign_id="mini",
        on_frozen=lambda frozen, found: events.append(("frozen", tuple(frozen))),
    )
    target = formula_id(planted)
    assert outcome["status"] == "confirmed" and target in outcome["confirmed"]
    assert events[0][0] == "frozen" and target in events[0][1]
    assert events[1] == ("consume", events[0][1])
    assert search.expressions[target] == planted
    assert windows.opened == ("discovery", "selection", "confirmation")


@pytest.mark.parametrize("index", [0, 1])
def test_each_family_search_is_seeded_by_the_protocol_seed_and_its_index(monkeypatch, index):
    seeds = []
    real = genetic.TypedGeneticSearch

    def recording(seed, *args, **kwargs):
        seeds.append(seed)
        return real(seed, *args, **kwargs)

    monkeypatch.setattr(genetic, "TypedGeneticSearch", recording)
    protocol = mini_protocol()
    evaluator = DiscoveryEvaluator(discovery_view(protocol), protocol)
    family_search(
        protocol.families[index],
        index,
        1,
        protocol=protocol,
        evaluator=evaluator,
        book=book(),
        charger=RecordingCharger(),
        seen=set(),
    )
    assert seeds == [protocol.search.seed * 100 + index]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_genetic.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentic_trader.research.pooled.genetic'`.

- [ ] **Step 4: Write `genetic.py`**

```python
# agentic_trader/research/pooled/genetic.py
"""The budgeted per-family genetic search over the discovery window, and the full campaign run.

Each family searches with its own seeds, mutation operators and constant windows
(``TypedGeneticSearch``), seeded ``search.seed * FAMILY_SEED_STRIDE + family index``, for
its share of the formula budget (``CampaignProtocol.family_budgets``).

**Vetting.** A proposal is vetted before anything is spent. One that does not compile,
repeats an earlier proposal of this campaign, calls a forbidden operator or is not
dimensionless is recorded as rejected and is never charged or evaluated.

**Charging.** An accepted proposal is charged (``Charger.charge``) *before* it is scored on
the discovery view, and its fitness is told back to the search.

**Label blindness.** The search sees fitness numbers only, and formula panels read no label
(``ScoreBook``).

``run_search_stages`` then runs Part 1a's pooled discovery finish, selection and
confirmation unchanged.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from agentic_trader.research.alpha.search import TypedGeneticSearch
from agentic_trader.research.pooled.campaign import (
    CampaignProtocol,
    CampaignWindows,
    DiscoveryEvaluator,
    DiscoveryScore,
    Family,
    Ledger,
    ScoredFormula,
    finish_discovery,
    later_stages,
)
from agentic_trader.research.pooled.cube import CubeView
from agentic_trader.research.pooled.scoring import ScoreBook, vet_expression


__all__ = [
    "FAMILY_SEED_STRIDE",
    "MAX_CONSECUTIVE_REJECTIONS",
    "Charger",
    "FamilyRun",
    "NullCharger",
    "SearchOutcome",
    "family_search",
    "run_search_stages",
    "search_campaign",
]

FAMILY_SEED_STRIDE = 100
MAX_CONSECUTIVE_REJECTIONS = 200


class Charger(Protocol):
    def reserve_family(self, family_id: str, budget: int) -> None: ...

    def charge(self, family_id: str, expression: str, formula_id: str, nodes: int) -> None: ...


class NullCharger:
    """Checks B and C run the same search but never touch the ledger."""

    def reserve_family(self, family_id: str, budget: int) -> None:
        return None

    def charge(self, family_id: str, expression: str, formula_id: str, nodes: int) -> None:
        return None


@dataclass(frozen=True)
class FamilyRun:
    family_id: str
    budget: int
    charged: int
    errors: int
    rejected: dict[str, int]
    stopped_short: str | None  # why the family ended before its budget; None when it spent it
    scores: tuple[DiscoveryScore, ...]
    formulas: dict[str, ScoredFormula]
    expressions: dict[str, str]
    records: tuple[dict, ...]

    def summary(self) -> dict:
        return {
            "family": self.family_id,
            "budget": self.budget,
            "charged": self.charged,
            "evaluated": len(self.scores),
            "errors": self.errors,
            "rejected": dict(self.rejected),
            "stopped_short": self.stopped_short,
        }


@dataclass(frozen=True)
class SearchOutcome:
    runs: tuple[FamilyRun, ...]

    @property
    def scores(self) -> tuple[DiscoveryScore, ...]:
        return tuple(score for run in self.runs for score in run.scores)

    @property
    def formulas(self) -> dict[str, ScoredFormula]:
        return {fid: formula for run in self.runs for fid, formula in run.formulas.items()}

    @property
    def expressions(self) -> dict[str, str]:
        return {fid: expression for run in self.runs for fid, expression in run.expressions.items()}

    @property
    def families(self) -> dict[str, str]:
        return {fid: run.family_id for run in self.runs for fid in run.formulas}

    @property
    def records(self) -> tuple[dict, ...]:
        return tuple(record for run in self.runs for record in run.records)

    @property
    def cells(self) -> dict[str, frozenset]:
        return {score.row["formula_id"]: score.cells for score in self.scores}


def family_search(
    family: Family,
    index: int,
    budget: int,
    *,
    protocol: CampaignProtocol,
    evaluator: DiscoveryEvaluator,
    book: ScoreBook,
    charger: Charger,
    seen: set[str],
) -> FamilyRun:
    """Spend one family's budget: vet, charge, score and tell, until the budget or the grammar runs out."""
    search = TypedGeneticSearch(
        protocol.search.seed * FAMILY_SEED_STRIDE + index,
        archive_size=protocol.search.archive_size,
        seeds=family.seeds,
        operators=family.mutation_operators,
        constant_windows=family.constant_windows,
    )
    charger.reserve_family(family.id, budget)
    scores: list[DiscoveryScore] = []
    formulas: dict[str, ScoredFormula] = {}
    expressions: dict[str, str] = {}
    records: list[dict] = []
    rejected: Counter[str] = Counter()
    charged = errors = streak = 0
    stopped: str | None = None
    while charged < budget:
        try:
            proposal = search.ask()
        except ValueError as exc:  # no unseen expression is left in this family's grammar
            stopped = f"search exhausted: {exc}"
            break
        vetted = vet_expression(proposal, protocol.search.forbidden_operators, seen)
        if vetted.reason is not None:
            rejected[vetted.reason] += 1
            records.append(
                {"family": family.id, "expression": vetted.expression, "status": "rejected", "reason": vetted.reason}
            )
            streak += 1
            if streak >= MAX_CONSECUTIVE_REJECTIONS:
                stopped = f"{streak} consecutive rejected proposals"
                break
            continue
        streak = 0
        seen.add(vetted.expression)
        formula = book.formula(vetted.expression)
        charger.charge(family.id, vetted.expression, formula.formula_id, formula.nodes)
        charged += 1
        formulas[formula.formula_id] = formula
        expressions[formula.formula_id] = vetted.expression
        base = {"family": family.id, "expression": vetted.expression, "formula_id": formula.formula_id}
        try:
            score = evaluator.score(formula)
        except Exception as exc:  # recorded and charged, never retried with changes
            errors += 1
            records.append({**base, "status": "error", "error": f"{type(exc).__name__}: {exc}"})
            search.tell(vetted.expression, float("-inf"))
            continue
        scores.append(score)
        row = score.row
        records.append(
            {
                **base,
                "status": "evaluated",
                "fitness": row["fitness"],
                "edge_t": row["edge"]["t"],
                "edge_mean": row["edge"]["mean"],
                "leg_mean": row["leg"]["mean"],
                "n_sessions": int(row["edge"]["n_sessions"]),
                "passes": bool(row["passes"]),
            }
        )
        search.tell(vetted.expression, row["fitness"])
    return FamilyRun(
        family_id=family.id,
        budget=budget,
        charged=charged,
        errors=errors,
        rejected=dict(rejected),
        stopped_short=stopped,
        scores=tuple(scores),
        formulas=formulas,
        expressions=expressions,
        records=tuple(records),
    )


def search_campaign(
    protocol: CampaignProtocol,
    view: CubeView,
    book: ScoreBook,
    charger: Charger,
    *,
    progress: Callable[[str], None] | None = None,
) -> SearchOutcome:
    """Every family's search on the discovery view, in file order, sharing one seen-set."""
    evaluator = DiscoveryEvaluator(view, protocol)
    budgets = protocol.family_budgets()
    seen: set[str] = set()
    runs = []
    for index, family in enumerate(protocol.families):
        run = family_search(
            family,
            index,
            budgets[family.id],
            protocol=protocol,
            evaluator=evaluator,
            book=book,
            charger=charger,
            seen=seen,
        )
        runs.append(run)
        if progress is not None:
            note = f"; stopped: {run.stopped_short}" if run.stopped_short else ""
            progress(
                f"family {family.id}: {run.charged}/{run.budget} charged, {len(run.scores)} evaluated, "
                f"{sum(run.rejected.values())} rejected{note}"
            )
    return SearchOutcome(tuple(runs))


def run_search_stages(
    protocol: CampaignProtocol,
    windows: CampaignWindows,
    book: ScoreBook,
    ledger: Ledger,
    charger: Charger,
    *,
    campaign_id: str,
    on_frozen: Callable[[list[str], SearchOutcome], None] | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[dict, SearchOutcome]:
    """The whole campaign: per-family search, pooled discovery finish, selection and confirmation."""
    search = search_campaign(protocol, windows.discovery(), book, charger, progress=progress)
    discovery, carried = finish_discovery(search.scores, protocol)
    hook = None if on_frozen is None else (lambda frozen: on_frozen(frozen, search))
    outcome = later_stages(
        list(search.formulas.values()),
        discovery,
        carried,
        windows,
        ledger,
        protocol,
        campaign_id=campaign_id,
        on_frozen=hook,
    )
    return outcome, search
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_genetic.py -q`
Expected: PASS (each test runs in seconds: 12 names, 9 formulas).

- [ ] **Step 6: Stage**

```bash
git add agentic_trader/research/pooled/genetic.py tests/research/pooled/mini_world.py tests/research/pooled/test_genetic.py
```

---

### Task 10: The pooled ledger in the journal

**Files:**
- Modify: `agentic_trader/storage/alpha.py`
- Create: `agentic_trader/research/pooled/ledger.py`
- Test: `tests/research/pooled/test_ledger.py`, `tests/integration/test_pooled_ledger_postgres.py`

**Interfaces:**
- Produces (`AlphaRepository`):
  - `reserve_pooled_campaign(campaign_id, record) -> dict`. `record` holds `protocol_sha256`, `cohort_sha256`, `cube_sha256`, `code_revision`, `budget`.
  - `reserve_pooled_family(campaign_id, family_id, budget)`.
  - `charge_pooled_formula(campaign_id, family_id, *, formula_id, expression, nodes) -> bool` (False when already charged).
  - `advance_pooled_campaign(campaign_id, status, detail=None)`.
  - `consume_pooled_confirmation(*, campaign_id, cohort_sha256, interval, candidates)`.
  - `status()["pooled_ledger"]`.
- Produces (`ledger.py`): `JournalLedger(repository, loop, campaign_id)`, implementing both the campaign `Ledger` and the `Charger` protocol, plus `.advance(status, detail=None)`.
- Keys:
  - `pooled/campaign/<id>`, `pooled/campaign/<id>/family/<family>`, `pooled/formula/<id>/<formula_id>`;
  - `pooled/ledger` (`formulas_charged`, `campaigns`, `confirmations`);
  - `pooled/confirmation` (lane-wide `intervals`).

- [ ] **Step 1: Write the failing SQLite tests**

```python
# tests/research/pooled/test_ledger.py
import asyncio
from datetime import date

import pytest

from agentic_trader.research.pooled.ledger import JournalLedger
from agentic_trader.storage.alpha import AlphaRepository


RECORD = {
    "protocol_sha256": "p" * 64,
    "cohort_sha256": "c" * 64,
    "cube_sha256": "k" * 64,
    "code_revision": "abc1234",
    "budget": 200,
}
OTHER = {**RECORD, "protocol_sha256": "q" * 64, "cohort_sha256": "d" * 64}
INTERVAL = (date(2024, 1, 2), date(2026, 7, 31))


@pytest.fixture
async def repository(temp_db):
    await temp_db.init_db()
    yield AlphaRepository(temp_db.workflows)
    await temp_db.engine.dispose()


async def test_a_reservation_is_immutable_and_a_resume_returns_it(repository):
    first = await repository.reserve_pooled_campaign("cmp", RECORD)
    assert first["status"] == "reserved" and first["code_revision"] == "abc1234"
    assert await repository.reserve_pooled_campaign("cmp", RECORD) == first
    with pytest.raises(ValueError, match="immutable"):
        await repository.reserve_pooled_campaign("cmp", {**RECORD, "code_revision": "def5678"})
    assert (await repository.get("pooled/ledger"))["campaigns"] == 1
    assert await repository.get("family/all") is None  # the pooled lane never touches the global family


async def test_a_family_cannot_be_charged_past_its_reservation(repository):
    await repository.reserve_pooled_campaign("cmp", RECORD)
    with pytest.raises(ValueError, match="not reserved"):
        await repository.charge_pooled_formula("cmp", "rev", formula_id="f0", expression="returns", nodes=2)
    await repository.reserve_pooled_family("cmp", "rev", 2)
    await repository.reserve_pooled_family("cmp", "rev", 2)  # idempotent
    with pytest.raises(ValueError, match="immutable"):
        await repository.reserve_pooled_family("cmp", "rev", 3)
    assert await repository.charge_pooled_formula("cmp", "rev", formula_id="f1", expression="a", nodes=2)
    assert await repository.charge_pooled_formula("cmp", "rev", formula_id="f2", expression="b", nodes=2)
    with pytest.raises(ValueError, match="exhausted"):
        await repository.charge_pooled_formula("cmp", "rev", formula_id="f3", expression="c", nodes=2)
    assert (await repository.get("pooled/ledger"))["formulas_charged"] == 2


async def test_charging_the_same_formula_again_is_a_no_op(repository):
    await repository.reserve_pooled_campaign("cmp", RECORD)
    await repository.reserve_pooled_family("cmp", "rev", 2)
    assert await repository.charge_pooled_formula("cmp", "rev", formula_id="f1", expression="a", nodes=2) is True
    assert await repository.charge_pooled_formula("cmp", "rev", formula_id="f1", expression="a", nodes=2) is False
    with pytest.raises(ValueError, match="another expression"):
        await repository.charge_pooled_formula("cmp", "rev", formula_id="f1", expression="z", nodes=2)
    assert (await repository.get("pooled/campaign/cmp/family/rev"))["charged"] == 1
    assert (await repository.get("pooled/ledger"))["formulas_charged"] == 1


async def test_the_confirmation_ledger_is_lane_wide(repository):
    await repository.reserve_pooled_campaign("one", RECORD)
    await repository.reserve_pooled_campaign("two", OTHER)
    await repository.consume_pooled_confirmation(
        campaign_id="one", cohort_sha256="c" * 64, interval=INTERVAL, candidates=("f1",)
    )
    with pytest.raises(ValueError, match="already consumed by campaign one"):
        await repository.consume_pooled_confirmation(
            campaign_id="two", cohort_sha256="d" * 64, interval=(date(2026, 7, 1), date(2027, 6, 30)), candidates=()
        )
    await repository.consume_pooled_confirmation(
        campaign_id="two", cohort_sha256="d" * 64, interval=(date(2026, 8, 3), date(2027, 7, 30)), candidates=()
    )
    consumed = await repository.get("pooled/confirmation")
    assert [item["campaign_id"] for item in consumed["intervals"]] == ["one", "two"]
    assert consumed["intervals"][0]["candidates"] == ["f1"]
    assert (await repository.get("pooled/ledger"))["confirmations"] == 2
    assert (await repository.get("pooled/campaign/one"))["status"] == "confirmation_consumed"


async def test_a_campaign_that_consumed_its_confirmation_or_completed_cannot_run_again(repository):
    await repository.reserve_pooled_campaign("one", RECORD)
    await repository.consume_pooled_confirmation(
        campaign_id="one", cohort_sha256="c" * 64, interval=INTERVAL, candidates=()
    )
    await repository.advance_pooled_campaign("one", "failed", {"error": "crashed after consuming"})
    with pytest.raises(ValueError, match="cannot run again"):
        await repository.reserve_pooled_campaign("one", RECORD)
    await repository.reserve_pooled_campaign("two", OTHER)
    await repository.advance_pooled_campaign("two", "completed", {"status": "no_finalists"})
    with pytest.raises(ValueError, match="cannot run again"):
        await repository.reserve_pooled_campaign("two", OTHER)
    with pytest.raises(ValueError, match="completed"):
        await repository.advance_pooled_campaign("two", "failed")


async def test_status_reports_the_pooled_ledger(repository):
    await repository.reserve_pooled_campaign("cmp", RECORD)
    assert (await repository.status())["pooled_ledger"] == {"formulas_charged": 0, "campaigns": 1, "confirmations": 0}


async def test_the_journal_ledger_runs_from_a_worker_thread_and_refuses_the_event_loop(repository):
    await repository.reserve_pooled_campaign("cmp", RECORD)
    ledger = JournalLedger(repository, asyncio.get_running_loop(), "cmp")

    def work():
        ledger.reserve_family("rev", 1)
        ledger.charge("rev", "returns", "f1", 2)
        ledger.advance("frozen", {"candidates": ["f1"]})
        ledger.consume_confirmation(cohort_sha256="c" * 64, interval=INTERVAL, campaign_id="cmp", candidates=("f1",))

    await asyncio.to_thread(work)
    assert (await repository.get("pooled/campaign/cmp/family/rev"))["charged"] == 1
    campaign = await repository.get("pooled/campaign/cmp")
    assert campaign["details"]["frozen"] == {"candidates": ["f1"]}
    assert campaign["status"] == "confirmation_consumed"
    with pytest.raises(RuntimeError, match="worker thread"):
        ledger.charge("rev", "other", "f2", 2)
    with pytest.raises(ValueError, match="belongs to campaign cmp"):
        await asyncio.to_thread(
            ledger.consume_confirmation, cohort_sha256="c" * 64, interval=INTERVAL, campaign_id="x", candidates=()
        )
```

- [ ] **Step 2: Write the failing PostgreSQL integration test**

```python
# tests/integration/test_pooled_ledger_postgres.py
"""Two independent clients: pooled charges never exceed a reservation; a confirmation interval is consumed once."""

import asyncio
from datetime import date

import pytest

from agentic_trader.config import AppConfig
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.db import SignalDatabase


pytestmark = [pytest.mark.postgres, pytest.mark.enable_socket, pytest.mark.allow_hosts(["127.0.0.1", "localhost"])]

RECORD = {
    "protocol_sha256": "p" * 64,
    "cohort_sha256": "c" * 64,
    "cube_sha256": "k" * 64,
    "code_revision": "abc1234",
    "budget": 200,
}


async def _clients(url):
    config = AppConfig(execution_mode="alpaca", alpaca_paper=True)
    first = SignalDatabase(db_url=url, config=config)
    second = SignalDatabase(db_url=url, config=config)
    await first.init_db()
    return first, second, AlphaRepository(first.workflows), AlphaRepository(second.workflows)


async def test_concurrent_charges_never_exceed_a_family_reservation(postgres_test_db):
    first, second, a, b = await _clients(postgres_test_db)
    await a.reserve_pooled_campaign("cmp", RECORD)
    await a.reserve_pooled_family("cmp", "rev", 3)
    results = await asyncio.gather(
        *[
            (a if i % 2 else b).charge_pooled_formula("cmp", "rev", formula_id=f"f{i}", expression=f"e{i}", nodes=2)
            for i in range(8)
        ],
        return_exceptions=True,
    )
    assert sum(result is True for result in results) == 3
    assert sum(isinstance(result, ValueError) for result in results) == 5
    assert (await a.get("pooled/campaign/cmp/family/rev"))["charged"] == 3
    assert (await b.get("pooled/ledger"))["formulas_charged"] == 3
    await first.engine.dispose()
    await second.engine.dispose()


async def test_concurrent_confirmation_consumption_admits_exactly_one(postgres_test_db):
    first, second, a, b = await _clients(postgres_test_db)
    await a.reserve_pooled_campaign("one", RECORD)
    await a.reserve_pooled_campaign("two", {**RECORD, "protocol_sha256": "q" * 64})
    interval = (date(2024, 1, 2), date(2026, 7, 31))
    results = await asyncio.gather(
        a.consume_pooled_confirmation(campaign_id="one", cohort_sha256="c" * 64, interval=interval, candidates=()),
        b.consume_pooled_confirmation(campaign_id="two", cohort_sha256="c" * 64, interval=interval, candidates=()),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ValueError) for result in results) == 1
    assert len((await a.get("pooled/confirmation"))["intervals"]) == 1
    await first.engine.dispose()
    await second.engine.dispose()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_ledger.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentic_trader.research.pooled.ledger'`.

- [ ] **Step 4: Add the pooled ledger methods to `storage/alpha.py`**

- Change the datetime import to `from datetime import UTC, date, datetime, timedelta`.
- Add `from collections.abc import Mapping, Sequence` if they are not already imported.
- Below `ALPHA_EVENT_KINDS`, add:

```python
# The pooled lane (research/pooled) keeps its own ledger and never touches family/all.
POOLED_IMMUTABLE = ("protocol_sha256", "cohort_sha256", "cube_sha256", "code_revision", "budget")
POOLED_LEDGER_KEY = "pooled/ledger"
POOLED_CONFIRMATION_KEY = "pooled/confirmation"
```

Add these methods to `AlphaRepository`, after `reserve_run`:

```python
# --- pooled lane: campaigns, family budgets, formula charges, lane-wide confirmation ---


async def _pooled_counts(self, session) -> dict:
    return await self._get(session, POOLED_LEDGER_KEY) or {"formulas_charged": 0, "campaigns": 0, "confirmations": 0}


async def _pooled_count(self, session, field: str) -> None:
    counts = await self._pooled_counts(session)
    counts[field] += 1
    await self._append(session, POOLED_LEDGER_KEY, counts, EventKind.ALPHA_RESEARCH, "research_worker")


async def reserve_pooled_campaign(self, campaign_id: str, record: Mapping) -> dict:
    """Reserve a pooled campaign before it reads anything; its immutable fields never change.

    A rerun with the same immutable fields resumes (the search is deterministic, so it
    re-proposes the same formulas, whose charges are no-ops). A campaign that consumed its
    confirmation interval or completed can never run again.
    """
    immutable = {key: record[key] for key in POOLED_IMMUTABLE}
    key = f"pooled/campaign/{campaign_id}"
    async with self.store.db.session_factory() as session, session.begin():
        await self.store.lock(session, resource="alpha")
        existing = await self._get(session, key)
        if existing is not None:
            if {k: existing[k] for k in POOLED_IMMUTABLE} != immutable:
                raise ValueError(
                    "Pooled campaign reservation is immutable; a changed protocol, cohort, cube or code "
                    "revision needs a new protocol version"
                )
            if "confirmation_consumed" in existing["stages"] or existing["status"] == "completed":
                raise ValueError(f"Pooled campaign {campaign_id} cannot run again (status {existing['status']})")
            return existing
        payload = {
            **immutable,
            "campaign_id": campaign_id,
            "status": "reserved",
            "stages": {"reserved": datetime.now(UTC).isoformat()},
            "details": {},
        }
        await self._append(session, key, payload, EventKind.ALPHA_RESEARCH, "research_worker")
        await self._pooled_count(session, "campaigns")
        return payload


async def reserve_pooled_family(self, campaign_id: str, family_id: str, budget: int) -> None:
    if type(budget) is not int or budget < 1:
        raise ValueError("Positive integer family budget required")
    key = f"pooled/campaign/{campaign_id}/family/{family_id}"
    async with self.store.db.session_factory() as session, session.begin():
        await self.store.lock(session, resource="alpha")
        if await self._get(session, f"pooled/campaign/{campaign_id}") is None:
            raise ValueError(f"Unknown pooled campaign {campaign_id}")
        existing = await self._get(session, key)
        if existing is not None:
            if existing["budget"] != budget:
                raise ValueError("Pooled family budget is immutable")
            return
        await self._append(session, key, {"budget": budget, "charged": 0}, EventKind.ALPHA_RESEARCH, "research_worker")


async def charge_pooled_formula(
    self, campaign_id: str, family_id: str, *, formula_id: str, expression: str, nodes: int
) -> bool:
    """Charge one formula before it is evaluated. False: it was already charged (a resume)."""
    key = f"pooled/formula/{campaign_id}/{formula_id}"
    family_key = f"pooled/campaign/{campaign_id}/family/{family_id}"
    async with self.store.db.session_factory() as session, session.begin():
        await self.store.lock(session, resource="alpha")
        existing = await self._get(session, key)
        if existing is not None:
            if existing["expression"] != expression or existing["family"] != family_id:
                raise ValueError(f"Pooled formula id {formula_id} is already charged for another expression")
            return False
        family = await self._get(session, family_key)
        if family is None:
            raise ValueError(f"Pooled family {family_id} budget not reserved")
        if family["charged"] >= family["budget"]:
            raise ValueError(f"Pooled family {family_id} budget of {family['budget']} exhausted")
        charge = {
            "family": family_id,
            "expression": expression,
            "nodes": nodes,
            "charged_at": datetime.now(UTC).isoformat(),
        }
        await self._append(session, key, charge, EventKind.ALPHA_RESEARCH, "research_worker")
        family["charged"] += 1
        await self._append(session, family_key, family, EventKind.ALPHA_RESEARCH, "research_worker")
        await self._pooled_count(session, "formulas_charged")
        return True


async def advance_pooled_campaign(self, campaign_id: str, status: str, detail: Mapping | None = None) -> None:
    key = f"pooled/campaign/{campaign_id}"
    async with self.store.db.session_factory() as session, session.begin():
        await self.store.lock(session, resource="alpha")
        existing = await self._get(session, key)
        if existing is None:
            raise ValueError(f"Unknown pooled campaign {campaign_id}")
        if existing["status"] == "completed":
            raise ValueError(f"Pooled campaign {campaign_id} is completed")
        existing["status"] = status
        existing["stages"][status] = datetime.now(UTC).isoformat()
        if detail is not None:
            existing["details"][status] = dict(detail)
        await self._append(session, key, existing, EventKind.ALPHA_RESEARCH, "research_worker")


async def consume_pooled_confirmation(
    self, *, campaign_id: str, cohort_sha256: str, interval: tuple[date, date], candidates: Sequence[str]
) -> None:
    """Journal a confirmation interval's single use before it is read; overlaps are refused lane-wide."""
    start, end = interval
    campaign_key = f"pooled/campaign/{campaign_id}"
    async with self.store.db.session_factory() as session, session.begin():
        await self.store.lock(session, resource="alpha")
        campaign = await self._get(session, campaign_key)
        if campaign is None:
            raise ValueError(f"Unknown pooled campaign {campaign_id}")
        consumed = await self._get(session, POOLED_CONFIRMATION_KEY) or {"intervals": []}
        for item in consumed["intervals"]:
            if not (end < date.fromisoformat(item["start"]) or date.fromisoformat(item["end"]) < start):
                raise ValueError(f"confirmation interval already consumed by campaign {item['campaign_id']}")
        now = datetime.now(UTC).isoformat()
        consumed["intervals"].append(
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "campaign_id": campaign_id,
                "cohort_sha256": cohort_sha256,
                "candidates": list(candidates),
                "consumed_at": now,
            }
        )
        await self._append(session, POOLED_CONFIRMATION_KEY, consumed, EventKind.ALPHA_RESEARCH, "research_worker")
        campaign["status"] = "confirmation_consumed"
        campaign["stages"]["confirmation_consumed"] = now
        await self._append(session, campaign_key, campaign, EventKind.ALPHA_RESEARCH, "research_worker")
        await self._pooled_count(session, "confirmations")
```

In `status()`, add `"pooled_ledger": await self.get(POOLED_LEDGER_KEY),` after `"research_family"`.

- [ ] **Step 5: Write `ledger.py`**

```python
# agentic_trader/research/pooled/ledger.py
"""The pooled campaign's journal ledger: ``AlphaRepository`` calls made from the campaign's thread.

The campaign's stages run in a worker thread (``asyncio.to_thread``). Each ledger call is
scheduled on the event loop that owns the repository and waited for, so a charge is
committed before its formula is evaluated, and the confirmation consumption is committed
before its window is read.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Mapping
from datetime import date
from typing import Any

from agentic_trader.storage.alpha import AlphaRepository


__all__ = ["JournalLedger"]


class JournalLedger:
    """The campaign ``Ledger`` and ``Charger`` over the journal, for one campaign id."""

    def __init__(self, repository: AlphaRepository, loop: asyncio.AbstractEventLoop, campaign_id: str):
        self._repository = repository
        self._loop = loop
        self._campaign_id = campaign_id

    def _call(self, coroutine: Coroutine[Any, Any, Any]) -> Any:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            coroutine.close()
            raise RuntimeError("JournalLedger must be called from a worker thread, not its event loop")
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop).result()

    def reserve_family(self, family_id: str, budget: int) -> None:
        self._call(self._repository.reserve_pooled_family(self._campaign_id, family_id, budget))

    def charge(self, family_id: str, expression: str, formula_id: str, nodes: int) -> None:
        self._call(
            self._repository.charge_pooled_formula(
                self._campaign_id, family_id, formula_id=formula_id, expression=expression, nodes=nodes
            )
        )

    def consume_confirmation(
        self, *, cohort_sha256: str, interval: tuple[date, date], campaign_id: str, candidates: tuple[str, ...]
    ) -> None:
        if campaign_id != self._campaign_id:
            raise ValueError(f"this ledger belongs to campaign {self._campaign_id}, not {campaign_id}")
        self._call(
            self._repository.consume_pooled_confirmation(
                campaign_id=campaign_id, cohort_sha256=cohort_sha256, interval=interval, candidates=candidates
            )
        )

    def advance(self, status: str, detail: Mapping | None = None) -> None:
        self._call(self._repository.advance_pooled_campaign(self._campaign_id, status, detail))
```

- [ ] **Step 6: Run the tests to verify they pass**

Run:
- `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_ledger.py tests/research/test_alpha_journal.py -q`
- `TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_trader env -u VIRTUAL_ENV uv run pytest tests/integration/test_pooled_ledger_postgres.py --run-postgres -q`

Expected: PASS. If the local PostgreSQL server is not running, report that instead of skipping silently; the controller runs it before the PR.

- [ ] **Step 7: Stage**

```bash
git add agentic_trader/storage/alpha.py agentic_trader/research/pooled/ledger.py tests/research/pooled/test_ledger.py tests/integration/test_pooled_ledger_postgres.py
```

---
### Task 11: Check B — search power

**Files:**
- Create: `agentic_trader/research/pooled/search_power.py`
- Test: `tests/research/pooled/test_search_power.py`

**Interfaces:**
- Consumes: Task 9's `family_search`, `NullCharger`; Task 8's `ScoreBook`, `vet_expression`; Task 7's `DiscoveryEvaluator`; `campaign._jaccard`; `study.check_power_gate`; `cube.check_coverage`.
- Produces:
  - `HIDDEN_ATTEMPTS = 200`, `HIDDEN_SEED_STRIDE = 1000`;
  - `hidden_expression(family, *, protocol, rng, book, view) -> (str, Picks)`;
  - `run_search_power(protocol, cube, book, *, progress=None) -> dict`, with `status` `passed` | `gate_failed`;
  - `async execute_search_power(loaded, directory, *, cohort, build, power_result, environment, progress=None) -> dict`. Its manifest records `check: "search_power"`; its result records `cohort_sha256`, `campaign_protocol_sha256` and `cube_sha256`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/research/pooled/test_search_power.py
import asyncio
import json
import random
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from agentic_trader.research.alpha.search import canonical_expression
from agentic_trader.research.pooled import search_power
from agentic_trader.research.pooled.campaign import LoadedProtocol, _calls
from agentic_trader.research.pooled.formula import select_picks
from agentic_trader.research.pooled.search_power import execute_search_power, hidden_expression, run_search_power
from tests.research.pooled.mini_world import COHORT, book, cube_build, label_cube, mini_protocol


def exact_seed(family, *, protocol, rng, book, view):
    """A hidden expression equal to the family's first seed: the search must recover it."""
    expression = canonical_expression(family.seeds[0])
    return expression, select_picks(*book.formula(expression).panel(view), view, protocol.k)


def with_delta(protocol, delta):
    return protocol.model_copy(update={"power_search": protocol.power_search.model_copy(update={"delta": delta})})


def test_hidden_expressions_are_reproducible_near_seed_mutations_with_enough_sessions():
    protocol = mini_protocol()
    family = protocol.families[0]
    view = label_cube().window(*protocol.windows.discovery)
    expression, picks = hidden_expression(family, protocol=protocol, rng=random.Random(5), book=book(), view=view)
    assert expression not in {canonical_expression(seed) for seed in family.seeds}
    assert _calls(expression) <= {"roc", *family.mutation_operators}
    assert np.unique(picks.session_idx).size >= protocol.discovery_gate.min_sessions
    again, _ = hidden_expression(family, protocol=protocol, rng=random.Random(5), book=book(), view=view)
    assert again == expression


def test_a_planted_seed_is_recovered_and_a_vanishing_plant_is_not(monkeypatch):
    monkeypatch.setattr(search_power, "hidden_expression", exact_seed)
    found = run_search_power(with_delta(mini_protocol(), 2.0), label_cube(), book())
    assert found["status"] == "passed" and found["recovered"] == 3
    assert [s["family"] for s in found["seeds"]] == ["reversal", "range_location", "abnormal_volume"]
    assert all(s["best_passing_jaccard"] == 1.0 for s in found["seeds"])
    missed = run_search_power(with_delta(mini_protocol(), 1e-9), label_cube(), book())
    assert missed["status"] == "gate_failed" and missed["recovered"] == 0


def test_search_power_is_reproducible():
    protocol = mini_protocol()
    assert run_search_power(protocol, label_cube(), book()) == run_search_power(protocol, label_cube(), book())


def test_a_protocol_without_a_search_power_seed_is_refused():
    protocol = mini_protocol()
    v1_like = protocol.model_copy(update={"power_search": protocol.power_search.model_copy(update={"seed": None})})
    with pytest.raises(ValueError, match="power_search.seed"):
        run_search_power(v1_like, label_cube(), book())


def _loaded(protocol):
    return LoadedProtocol(protocol=protocol, sha256="p" * 64, path=Path("campaign.json"))


def _power(cube_sha256, **override):
    return {
        "status": "passed",
        "cohort_sha256": COHORT,
        "campaign_protocol_sha256": "p" * 64,
        "cube_sha256": cube_sha256,
        **override,
    }


def test_execute_search_power_writes_its_manifest_first_and_binds_the_cube(tmp_path, monkeypatch):
    monkeypatch.setattr(search_power, "hidden_expression", exact_seed)
    cube = label_cube()
    seen = {}

    async def build():
        seen["manifest"] = (tmp_path / "out" / "manifest.json").exists()
        return cube_build(cube)

    result = asyncio.run(
        execute_search_power(
            _loaded(with_delta(mini_protocol(), 2.0)),
            tmp_path / "out",
            cohort=SimpleNamespace(sha256=COHORT),
            build=build,
            power_result=_power(cube.sha256),
            environment={"runtime": {"revision": "abc1234"}},
        )
    )
    assert seen == {"manifest": True}
    assert result["status"] == "passed" and result["cube_sha256"] == cube.sha256
    assert result["campaign_protocol_sha256"] == "p" * 64 and result["authorizes_promotion"] is False
    assert json.loads((tmp_path / "out" / "manifest.json").read_text())["check"] == "search_power"
    on_disk = json.loads((tmp_path / "out" / "result.json").read_text(), parse_constant=pytest.fail)
    assert on_disk["recovered"] == 3


def test_execute_search_power_refuses_a_failed_power_check_or_another_cube(tmp_path):
    cube = label_cube()
    built = []

    async def build():
        built.append(1)
        return cube_build(cube)

    def run(out, power_result):
        return asyncio.run(
            execute_search_power(
                _loaded(mini_protocol()),
                tmp_path / out,
                cohort=SimpleNamespace(sha256=COHORT),
                build=build,
                power_result=power_result,
                environment={},
            )
        )

    failed = run("one", _power(cube.sha256, status="gate_failed"))
    assert failed["status"] == "failed" and "has not passed" in failed["error"] and built == []
    other = run("two", _power("x" * 64))
    assert other["status"] == "failed" and "different cube" in other["error"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_search_power.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentic_trader.research.pooled.search_power'`.

- [ ] **Step 3: Write `search_power.py`**

```python
# agentic_trader/research/pooled/search_power.py
"""Check B: can each family's real search recover a planted near-seed edge?

Reads discovery-window cells only and charges nothing. For seed i, the family is
``power_search.families[i mod n]``.

1. **Hidden expression.** Draw one or two mutations of one of the family's seeds, with the
   family's own operators and windows. It must compile, be dimensionless, not be a seed,
   and pick on at least ``discovery_gate.min_sessions`` sessions.
2. **Plant.** Add ``power_search.delta`` R (both cost levels) to its discovery picks.
3. **Search.** Run the family's real search on the shifted discovery view, with its
   campaign seed and budget.
4. **Recovery.** Seed i recovers when a formula passing the discovery gate overlaps the
   hidden picks at Jaccard >= ``dedupe_jaccard``.

This certifies recovery near the seeds only. With 16-17 formulas per family, the search
cannot reach edges far from them.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from agentic_trader.research.alpha.search import TypedGeneticSearch, canonical_expression
from agentic_trader.research.pooled.campaign import (
    CampaignProtocol,
    DiscoveryEvaluator,
    Family,
    LoadedProtocol,
    _jaccard,
)
from agentic_trader.research.pooled.cohort import LoadedCohort
from agentic_trader.research.pooled.cube import CubeView, LabelCube, check_coverage
from agentic_trader.research.pooled.formula import Picks, select_picks
from agentic_trader.research.pooled.genetic import NullCharger, family_search
from agentic_trader.research.pooled.runner import CubeBuild
from agentic_trader.research.pooled.scoring import ScoreBook, vet_expression
from agentic_trader.research.pooled.study import check_power_gate
from agentic_trader.research.setups.study import _finite_json
from agentic_trader.storage.artifacts import save_json_report


__all__ = ["HIDDEN_ATTEMPTS", "HIDDEN_SEED_STRIDE", "execute_search_power", "hidden_expression", "run_search_power"]

HIDDEN_ATTEMPTS = 200
HIDDEN_SEED_STRIDE = 1000


def hidden_expression(
    family: Family, *, protocol: CampaignProtocol, rng: random.Random, book: ScoreBook, view: CubeView
) -> tuple[str, Picks]:
    """A near-seed expression the family's grammar can reach, and its discovery picks."""
    mutator = TypedGeneticSearch(
        rng.getrandbits(32),
        archive_size=1,
        seeds=family.seeds,
        operators=family.mutation_operators,
        constant_windows=family.constant_windows,
    )
    seeds = {canonical_expression(seed) for seed in family.seeds}
    for _ in range(HIDDEN_ATTEMPTS):
        expression = canonical_expression(rng.choice(family.seeds))
        for _ in range(rng.choice((1, 2))):
            expression = mutator.mutate(expression)
        vetted = vet_expression(expression, protocol.search.forbidden_operators, seeds)
        if vetted.reason is not None:
            continue
        picks = select_picks(*book.formula(vetted.expression).panel(view), view, protocol.k)
        if np.unique(picks.session_idx).size >= protocol.discovery_gate.min_sessions:
            return vetted.expression, picks
    raise ValueError(f"no usable hidden expression for family {family.id} after {HIDDEN_ATTEMPTS} draws")


def run_search_power(
    protocol: CampaignProtocol, cube: LabelCube, book: ScoreBook, *, progress: Callable[[str], None] | None = None
) -> dict:
    spec = protocol.power_search
    if spec.seed is None:
        raise ValueError("this protocol has no power_search.seed (campaign v1); check B needs protocol v2")
    view = cube.window(*protocol.windows.discovery)
    indexed = {family.id: (index, family) for index, family in enumerate(protocol.families)}
    budgets = protocol.family_budgets()
    seeds = []
    for i in range(spec.seeds):
        index, family = indexed[spec.families[i % len(spec.families)]]
        rng = random.Random(spec.seed * HIDDEN_SEED_STRIDE + i)
        hidden, picks = hidden_expression(family, protocol=protocol, rng=rng, book=book, view=view)
        shifted = cube.with_shift(picks.session_idx + view.offset, picks.symbol_idx, spec.delta)
        evaluator = DiscoveryEvaluator(shifted.window(*protocol.windows.discovery), protocol)
        run = family_search(
            family,
            index,
            budgets[family.id],
            protocol=protocol,
            evaluator=evaluator,
            book=book,
            charger=NullCharger(),
            seen=set(),
        )
        planted = picks.cells()
        overlaps = [(_jaccard(set(score.cells), planted), bool(score.row["passes"])) for score in run.scores]
        best = max((jaccard for jaccard, _ in overlaps), default=0.0)
        best_passing = max((jaccard for jaccard, passes in overlaps if passes), default=0.0)
        recovered = best_passing >= protocol.dedupe_jaccard
        seeds.append(
            {
                **run.summary(),
                "seed": i,
                "hidden": hidden,
                "hidden_sessions": int(np.unique(picks.session_idx).size),
                "best_jaccard": best,
                "best_passing_jaccard": best_passing,
                "recovered": recovered,
            }
        )
        if progress is not None:
            verdict = "recovered" if recovered else "missed"
            progress(
                f"search power {i + 1}/{spec.seeds}: {family.id} {verdict} (best passing Jaccard {best_passing:.2f})"
            )
    recovered = sum(1 for seed in seeds if seed["recovered"])
    return {
        "status": "passed" if recovered >= spec.min_recovered else "gate_failed",
        "recovered": recovered,
        "seeds_run": spec.seeds,
        "min_recovered": spec.min_recovered,
        "delta": spec.delta,
        "seeds": seeds,
    }


async def execute_search_power(
    loaded: LoadedProtocol,
    directory: Path,
    *,
    cohort: LoadedCohort,
    build: Callable[[], Awaitable[CubeBuild]],
    power_result: Mapping,
    environment: dict,
    progress: Callable[[str], None] | None = None,
) -> dict:
    protocol = loaded.protocol
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    save_json_report({**protocol.model_dump(mode="json"), "sha256": loaded.sha256}, directory / "protocol.json")
    save_json_report(
        {
            "check": "search_power",
            "campaign_protocol_sha256": loaded.sha256,
            "cohort_sha256": cohort.sha256,
            "cube_spec_identity": protocol.cube_spec().identity,
            "power_cube_sha256": power_result.get("cube_sha256"),
            "reads": "discovery window cells only",
            "environment": environment,
            "started_at": datetime.now(UTC).isoformat(),
            "authorizes_promotion": False,
        },
        directory / "manifest.json",
    )
    base = {"cohort_sha256": cohort.sha256, "campaign_protocol_sha256": loaded.sha256, "authorizes_promotion": False}
    try:
        if cohort.sha256 != protocol.cohort_sha256:
            raise ValueError("cohort file does not match the protocol's cohort_sha256")
        check_power_gate(power_result, cohort_sha256=cohort.sha256, campaign_protocol_sha256=loaded.sha256)
        built = await build()
        check_coverage(built.cube, protocol.coverage)
        check_power_gate(
            power_result,
            cohort_sha256=cohort.sha256,
            campaign_protocol_sha256=loaded.sha256,
            cube_sha256=built.cube.sha256,
        )
        book = ScoreBook(built.adjusted, built.trading_days, built.cube.sessions, built.cube.symbols)
        outcome = await asyncio.to_thread(run_search_power, protocol, built.cube, book, progress=progress)
        result = {**outcome, **base, "cube_sha256": built.cube.sha256}
    except Exception as exc:
        result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}", **base}
    save_json_report(_finite_json(result), directory / "result.json")
    return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_search_power.py -q`
Expected: PASS.

- [ ] **Step 5: Stage**

```bash
git add agentic_trader/research/pooled/search_power.py tests/research/pooled/test_search_power.py
```

---

### Task 12: Check C — real-formula false acceptance

**Files:**
- Create: `agentic_trader/research/pooled/null_check.py`
- Test: `tests/research/pooled/test_null_check.py`

**Interfaces:**
- Consumes: Task 9's `run_search_stages`, `NullCharger`; Task 7's `synthetic_cube(..., cohort_sha256=)`, `CampaignWindows`, `InMemoryLedger`; Task 3's `NullCheckSpec`.
- Produces:
  - `demeaned(base: CubeView) -> CubeView`;
  - `null_replicate(protocol, base, calendar, cohort_sha256, book, rep) -> dict`;
  - `summarize_null(replicates, spec) -> dict`;
  - `run_null_check(protocol, discovery, calendar, cohort_sha256, *, adjusted, trading_days, symbols, progress=None, workers=1) -> dict`;
  - `async execute_null_check(loaded, directory, *, cohort, build, power_result, environment, progress=None, workers=1) -> dict`. Its manifest records `check: "null_check"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/research/pooled/test_null_check.py
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.stats import beta

from agentic_trader.research.alpha.search import canonical_expression
from agentic_trader.research.pooled.campaign import LoadedProtocol, NullCheckSpec
from agentic_trader.research.pooled.cube import CubeView
from agentic_trader.research.pooled.null_check import (
    demeaned,
    execute_null_check,
    null_replicate,
    run_null_check,
    summarize_null,
)
from agentic_trader.research.setups.study import _finite_json
from tests.research.pooled.mini_world import (
    ADJUSTED,
    BAR_DAYS,
    COHORT,
    NAMES,
    SESSIONS,
    book,
    cube_build,
    label_cube,
    mini_protocol,
)


def uneven_view(s=200, n=6, seed=0) -> CubeView:
    rng = np.random.default_rng(seed)
    labelled = rng.random((s, n)) > 0.2
    r = rng.normal(0.0, 1.0, (s, n)) + np.arange(n) * 0.3  # persistent name effects
    r = np.where(labelled, r, np.nan)
    return CubeView(
        sessions=SESSIONS[:s],
        symbols=NAMES[:n],
        offset=0,
        eligible=labelled | (rng.random((s, n)) > 0.5),
        labelled=labelled,
        r_gross=r + 0.01,
        r_cost=r,
        holding=np.ones((s, n), np.int16),
        hit=np.ones((s, n), np.int8),
        tiebreak=rng.integers(0, 2**62, (s, n)).astype(np.uint64),
        dollar_volume=np.ones((s, n)),
    )


def test_demeaning_removes_name_means_and_keeps_each_session_cross_section():
    base = uneven_view()
    out = demeaned(base)
    labelled = base.labelled
    for column in ("r_cost", "r_gross"):
        before, after = getattr(base, column), getattr(out, column)
        overall = before[labelled].mean()
        for j in range(labelled.shape[1]):
            cells = labelled[:, j]
            assert after[cells, j].mean() == pytest.approx(overall)
            shift = after[cells, j] - before[cells, j]
            assert np.allclose(shift, shift[0])  # one constant per name: co-movement is untouched
        assert np.isnan(after[~labelled]).all()
    assert out.eligible is base.eligible and out.labelled is base.labelled and out.holding is base.holding


def test_a_null_replicate_runs_the_whole_campaign_and_reports_every_seed_t():
    protocol = mini_protocol()
    cube = label_cube()
    base = demeaned(cube.window(*protocol.windows.discovery))
    rep = null_replicate(protocol, base, cube.sessions, COHORT, book(), 0)
    seeds = {canonical_expression(seed) for family in protocol.families for seed in family.seeds}
    assert rep["replicate"] == 0 and rep["evaluated"] == 9
    assert set(rep["seed_t"]) == seeds
    assert rep["status"] in {"no_finalists", "no_confirmation_candidates", "none_confirmed", "confirmed"}
    assert _finite_json(null_replicate(protocol, base, cube.sessions, COHORT, book(), 0)) == _finite_json(rep)


def test_the_summary_gates_on_the_false_acceptance_count_and_reports_seed_t():
    spec = NullCheckSpec(replicates=4, max_false_acceptances=1, seed=1)

    def rep(confirmed=(), survivors=0, carried=0, frozen=0, seed_t=None):
        return {
            "confirmed": list(confirmed),
            "survivors": survivors,
            "carried": carried,
            "frozen": frozen,
            "seed_t": seed_t or {},
        }

    reps = [
        rep(seed_t={"a": 1.0, "b": -1.0}),
        rep(confirmed=["x"], survivors=2, carried=1, frozen=1, seed_t={"a": float("nan")}),
        rep(seed_t={"a": 3.0}),
        rep(),
    ]
    summary = summarize_null(reps, spec)
    assert summary["status"] == "passed" and summary["false_acceptances"] == 1
    assert summary["false_acceptance_rate"] == 0.25
    assert summary["false_acceptance_upper95"] == pytest.approx(beta.ppf(0.95, 2, 3))
    assert summary["stage_counts"] == {"with_survivors": 1, "with_carried": 1, "reached_confirmation": 1}
    assert summary["seed_t"]["n"] == 3
    assert summary["seed_t"]["mean"] == pytest.approx(1.0) and summary["seed_t"]["sd"] == pytest.approx(2.0)
    assert summarize_null([*reps[:3], rep(confirmed=["y"])], spec)["status"] == "gate_failed"


def test_the_null_check_is_identical_for_any_worker_count():
    protocol = mini_protocol()
    cube = label_cube()
    discovery = cube.window(*protocol.windows.discovery)
    kwargs = {"adjusted": ADJUSTED, "trading_days": BAR_DAYS, "symbols": NAMES}
    serial = run_null_check(protocol, discovery, cube.sessions, COHORT, workers=1, **kwargs)
    pooled = run_null_check(protocol, discovery, cube.sessions, COHORT, workers=2, **kwargs)
    assert _finite_json(serial) == _finite_json(pooled)
    assert serial["replicates"] == 3 and len(serial["replicates_detail"]) == 3
    assert serial["status"] == ("passed" if serial["false_acceptances"] == 0 else "gate_failed")


def test_execute_null_check_writes_its_manifest_first_and_binds_the_cube(tmp_path):
    cube = label_cube()
    seen = {}

    async def build():
        seen["manifest"] = (tmp_path / "out" / "manifest.json").exists()
        return cube_build(cube)

    power_result = {
        "status": "passed",
        "cohort_sha256": COHORT,
        "campaign_protocol_sha256": "p" * 64,
        "cube_sha256": cube.sha256,
    }
    result = asyncio.run(
        execute_null_check(
            LoadedProtocol(protocol=mini_protocol(), sha256="p" * 64, path=Path("campaign.json")),
            tmp_path / "out",
            cohort=SimpleNamespace(sha256=COHORT),
            build=build,
            power_result=power_result,
            environment={},
        )
    )
    assert seen == {"manifest": True}
    assert result["status"] in {"passed", "gate_failed"} and result["cube_sha256"] == cube.sha256
    assert json.loads((tmp_path / "out" / "manifest.json").read_text())["check"] == "null_check"
    json.loads((tmp_path / "out" / "result.json").read_text(), parse_constant=pytest.fail)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_null_check.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentic_trader.research.pooled.null_check'`.

- [ ] **Step 3: Write `null_check.py`**

```python
# agentic_trader/research/pooled/null_check.py
"""Check C: the full campaign on null panels where real formulas can have no edge.

**Null panels.** Each replicate block-resamples whole discovery sessions onto the full
calendar, as check A does. At each cost level, every labelled R then moves by its name's
offset: ``R - mean(name) + mean(all)``, both means over the labelled discovery cells.
Every cross-section is a real session, so names keep their real co-movement and factor
exposures. But no name's average differs from the cohort's, so no formula has a true
edge. The leg mean keeps its real level, so the leg gates behave as they will on real data.

**Stages.** The real per-family search, discovery finish, selection and confirmation run
unchanged with an in-memory ledger. A replicate that confirms anything is a false
acceptance.

**Reported, not gated.** The discovery t of each family's seed expressions, evaluated
before any selection in every replicate, is an unselected null sample. A standard
deviation well above 1 means the bootstrap understates the variance of
persistent-exposure formulas.

Scores use real bars over the full calendar; labels come from discovery cells only;
nothing is charged.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import beta

from agentic_trader.research.alpha.search import canonical_expression
from agentic_trader.research.pooled.campaign import (
    CampaignProtocol,
    CampaignWindows,
    InMemoryLedger,
    LoadedProtocol,
    NullCheckSpec,
)
from agentic_trader.research.pooled.cohort import LoadedCohort
from agentic_trader.research.pooled.cube import CubeView, check_coverage
from agentic_trader.research.pooled.genetic import NullCharger, run_search_stages
from agentic_trader.research.pooled.power import synthetic_cube
from agentic_trader.research.pooled.runner import CubeBuild
from agentic_trader.research.pooled.scoring import ScoreBook
from agentic_trader.research.pooled.study import check_power_gate
from agentic_trader.research.setups.study import _finite_json
from agentic_trader.storage.artifacts import save_json_report


__all__ = ["demeaned", "execute_null_check", "null_replicate", "run_null_check", "summarize_null"]


def demeaned(base: CubeView) -> CubeView:
    """Each name's labelled R moved so its mean is the mean over all labelled cells (each cost level)."""
    labelled = base.labelled
    counts = labelled.sum(axis=0)
    out = {}
    for column in ("r_gross", "r_cost"):
        original = getattr(base, column)
        values = np.where(labelled, original, 0.0)
        overall = values.sum() / max(int(labelled.sum()), 1)
        name_mean = np.divide(values.sum(axis=0), counts, out=np.zeros(values.shape[1]), where=counts > 0)
        out[column] = np.where(labelled, original - name_mean + overall, np.nan)
    return replace(base, **out)


def null_replicate(
    protocol: CampaignProtocol,
    base: CubeView,
    calendar: Sequence[date],
    cohort_sha256: str,
    book: ScoreBook,
    rep: int,
) -> dict:
    """One null campaign on a resampled demeaned panel; seeded only by ``[null_check.seed, rep]``."""
    spec = protocol.null_check
    rng = np.random.default_rng([spec.seed, rep])
    cube = synthetic_cube(base, calendar, rng, protocol.bootstrap.block_mean, cohort_sha256=cohort_sha256)
    windows = CampaignWindows(cube, protocol.windows, cohort_sha256=cohort_sha256)
    outcome, search = run_search_stages(
        protocol, windows, book, InMemoryLedger(), NullCharger(), campaign_id=f"null-{rep}"
    )
    seeds = {canonical_expression(seed) for family in protocol.families for seed in family.seeds}
    seed_t = {
        record["expression"]: record["edge_t"]
        for record in search.records
        if record["status"] == "evaluated" and record["expression"] in seeds
    }
    return {
        "replicate": rep,
        "status": outcome["status"],
        "confirmed": [search.expressions[fid] for fid in outcome["confirmed"]],
        "survivors": sum(1 for row in outcome["discovery"] if row["passes"]),
        "carried": len(outcome["carried"]),
        "frozen": len(outcome["frozen"]),
        "evaluated": len(search.scores),
        "seed_t": seed_t,
    }


def summarize_null(replicates: Sequence[Mapping], spec: NullCheckSpec) -> dict:
    n = len(replicates)
    false = sum(1 for replicate in replicates if replicate["confirmed"])
    t_values = np.array(
        [t for replicate in replicates for t in replicate["seed_t"].values() if t is not None and np.isfinite(t)],
        dtype=float,
    )
    upper = 1.0 if false >= n else float(beta.ppf(0.95, false + 1, n - false))  # one-sided Clopper-Pearson
    return {
        "status": "passed" if false <= spec.max_false_acceptances else "gate_failed",
        "false_acceptances": false,
        "replicates": n,
        "false_acceptance_rate": false / n if n else float("nan"),
        "false_acceptance_upper95": upper,
        "max_false_acceptances": spec.max_false_acceptances,
        "stage_counts": {
            "with_survivors": sum(1 for replicate in replicates if replicate["survivors"]),
            "with_carried": sum(1 for replicate in replicates if replicate["carried"]),
            "reached_confirmation": sum(1 for replicate in replicates if replicate["frozen"]),
        },
        "seed_t": {
            "n": int(t_values.size),
            "mean": float(t_values.mean()) if t_values.size else float("nan"),
            "sd": float(t_values.std(ddof=1)) if t_values.size > 1 else float("nan"),
        },
    }


_WORKER: dict = {}


def _init_worker(
    protocol: CampaignProtocol,
    base: CubeView,
    calendar: Sequence[date],
    cohort_sha256: str,
    adjusted: Mapping[str, pd.DataFrame],
    trading_days: Sequence[date],
    symbols: Sequence[str],
) -> None:
    _WORKER.update(
        protocol=protocol,
        base=base,
        calendar=calendar,
        cohort_sha256=cohort_sha256,
        book=ScoreBook(adjusted, trading_days, calendar, symbols),
    )


def _worker_replicate(rep: int) -> tuple[int, dict]:
    w = _WORKER
    return rep, null_replicate(w["protocol"], w["base"], w["calendar"], w["cohort_sha256"], w["book"], rep)


def run_null_check(
    protocol: CampaignProtocol,
    discovery: CubeView,
    calendar: Sequence[date],
    cohort_sha256: str,
    *,
    adjusted: Mapping[str, pd.DataFrame],
    trading_days: Sequence[date],
    symbols: Sequence[str],
    progress: Callable[[str], None] | None = None,
    workers: int = 1,
) -> dict:
    spec = protocol.null_check
    if spec is None:
        raise ValueError("this protocol has no null_check (campaign v1); check C needs protocol v2")
    base = demeaned(discovery)
    results: dict[int, dict] = {}

    def done(rep: int, detail: dict) -> None:
        results[rep] = detail
        if progress is not None:
            progress(f"null replicate {len(results)}/{spec.replicates}: {detail['status']}")

    if workers > 1:
        pool = ProcessPoolExecutor(
            max_workers=workers,
            initializer=_init_worker,
            initargs=(
                protocol,
                base,
                tuple(calendar),
                cohort_sha256,
                dict(adjusted),
                tuple(trading_days),
                tuple(symbols),
            ),
        )
        try:
            for future in as_completed([pool.submit(_worker_replicate, rep) for rep in range(spec.replicates)]):
                done(*future.result())
        except BaseException:
            pool.shutdown(wait=False, cancel_futures=True)  # stop promptly instead of finishing every replicate
            raise
        pool.shutdown()
    else:
        book = ScoreBook(adjusted, trading_days, calendar, symbols)
        for rep in range(spec.replicates):
            done(rep, null_replicate(protocol, base, calendar, cohort_sha256, book, rep))
    ordered = [results[rep] for rep in sorted(results)]
    return {**summarize_null(ordered, spec), "replicates_detail": ordered}


async def execute_null_check(
    loaded: LoadedProtocol,
    directory: Path,
    *,
    cohort: LoadedCohort,
    build: Callable[[], Awaitable[CubeBuild]],
    power_result: Mapping,
    environment: dict,
    progress: Callable[[str], None] | None = None,
    workers: int = 1,
) -> dict:
    protocol = loaded.protocol
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    save_json_report({**protocol.model_dump(mode="json"), "sha256": loaded.sha256}, directory / "protocol.json")
    save_json_report(
        {
            "check": "null_check",
            "campaign_protocol_sha256": loaded.sha256,
            "cohort_sha256": cohort.sha256,
            "cube_spec_identity": protocol.cube_spec().identity,
            "power_cube_sha256": power_result.get("cube_sha256"),
            "reads": "discovery window cells only (per-name demeaned); real bars for scores",
            "environment": environment,
            "started_at": datetime.now(UTC).isoformat(),
            "authorizes_promotion": False,
        },
        directory / "manifest.json",
    )
    base = {"cohort_sha256": cohort.sha256, "campaign_protocol_sha256": loaded.sha256, "authorizes_promotion": False}
    try:
        if cohort.sha256 != protocol.cohort_sha256:
            raise ValueError("cohort file does not match the protocol's cohort_sha256")
        check_power_gate(power_result, cohort_sha256=cohort.sha256, campaign_protocol_sha256=loaded.sha256)
        built = await build()
        check_coverage(built.cube, protocol.coverage)
        check_power_gate(
            power_result,
            cohort_sha256=cohort.sha256,
            campaign_protocol_sha256=loaded.sha256,
            cube_sha256=built.cube.sha256,
        )
        outcome = await asyncio.to_thread(
            run_null_check,
            protocol,
            built.cube.window(*protocol.windows.discovery),
            built.cube.sessions,
            cohort.sha256,
            adjusted=built.adjusted,
            trading_days=built.trading_days,
            symbols=built.cube.symbols,
            progress=progress,
            workers=workers,
        )
        result = {**outcome, **base, "cube_sha256": built.cube.sha256}
    except Exception as exc:
        result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}", **base}
    save_json_report(_finite_json(result), directory / "result.json")
    return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_null_check.py -q`
Expected: PASS. The worker test spawns two processes; it takes tens of seconds.

- [ ] **Step 5: Stage**

```bash
git add agentic_trader/research/pooled/null_check.py tests/research/pooled/test_null_check.py
```

---

### Task 13: The campaign executor

**Files:**
- Create: `agentic_trader/research/pooled/campaign_run.py`
- Test: `tests/research/pooled/test_campaign_run.py`

**Interfaces:**
- Consumes: Task 9's `run_search_stages`, `SearchOutcome`; Task 10's `JournalLedger` and the `AlphaRepository` pooled methods; Task 8's `ScoreBook`; `formula.Formula`, `allowed_mask`, `select_picks`; `campaign._jaccard`.
- Produces:
  - `LITERATURE_ENTRIES`, `GATES = (("power", "power_a"), ("search_power", "search_power"), ("null_check", "null_check"))`;
  - `campaign_id_for(loaded) -> str`;
  - `require_clean_revision(environment) -> str`;
  - `check_gate(directory, *, check, cohort_sha256, protocol_sha256, revision) -> dict`;
  - `literature_cells(entries, view, book) -> dict[str, frozenset]`;
  - `already_tested(carried, cells, literature, threshold) -> dict[str, str]`;
  - `async execute_campaign(loaded, directory, *, cohort, build, gates, repository, entries, environment, progress=None) -> dict`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/research/pooled/test_campaign_run.py
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentic_trader.research.pooled import campaign_run
from agentic_trader.research.pooled.campaign import CampaignWindows, LoadedProtocol
from agentic_trader.research.pooled.campaign_run import check_gate, execute_campaign, require_clean_revision
from agentic_trader.research.pooled.formula import Formula
from agentic_trader.research.pooled.scoring import ScoreBook, formula_id
from agentic_trader.storage.alpha import AlphaRepository
from tests.research.pooled.mini_world import COHORT, cube_build, mini_protocol, planted_cube


PLANTED = "-1.0 * roc(close, 21)"
CAMPAIGN = "pooled-campaign-v1-" + "p" * 16
LITERATURE = [SimpleNamespace(entry=SimpleNamespace(id="lit-reversal", formula=Formula(score=PLANTED, k=3)))]


@pytest.fixture
async def repository(temp_db):
    await temp_db.init_db()
    yield AlphaRepository(temp_db.workflows)
    await temp_db.engine.dispose()


async def run(
    tmp_path, repository, cube, *, out="out", gates_cube=None, revision="abc1234", protocol=None, entries=LITERATURE
):
    loaded = LoadedProtocol(protocol=protocol or mini_protocol(), sha256="p" * 64, path=Path("campaign.json"))
    sha = gates_cube or cube.sha256
    gates = {name: {"status": "passed", "cube_sha256": sha} for name in ("power", "search_power", "null_check")}

    async def build():
        return cube_build(cube)

    return await execute_campaign(
        loaded,
        tmp_path / out,
        cohort=SimpleNamespace(sha256=COHORT),
        build=build,
        gates=gates,
        repository=repository,
        entries=entries,
        environment={"runtime": {"revision": revision}},
    )


async def test_the_campaign_reserves_charges_freezes_consumes_and_completes(tmp_path, repository):
    result = await run(tmp_path, repository, planted_cube(PLANTED, 1.0))
    target = formula_id(PLANTED)
    assert result["status"] == "confirmed" and target in result["confirmed"]
    # The literature entry is the same formula: already tested, keeps its Holm slot, never probe-eligible.
    assert result["already_tested"][target] == "lit-reversal"
    assert target not in result["probe_eligible"]
    assert any(row["formula_id"] == target for row in result["confirmation"])
    assert await repository.get("pooled/ledger") == {"formulas_charged": 9, "campaigns": 1, "confirmations": 1}
    campaign = await repository.get(f"pooled/campaign/{CAMPAIGN}")
    assert campaign["status"] == "completed" and campaign["code_revision"] == "abc1234"
    assert target in [doc["formula_id"] for doc in campaign["details"]["frozen"]["candidates"]]
    frozen = json.loads((tmp_path / "out" / "frozen" / f"{target}.json").read_text())
    assert frozen["expression"] == PLANTED and frozen["formula"]["k"] == 3
    lines = [json.loads(line) for line in (tmp_path / "out" / "formulas.jsonl").read_text().splitlines()]
    assert sum(line["status"] in ("evaluated", "error") for line in lines) == 9
    on_disk = json.loads((tmp_path / "out" / "result.json").read_text(), parse_constant=pytest.fail)
    assert on_disk["status"] == "confirmed" and on_disk["authorizes_promotion"] is False


async def test_a_finalist_no_literature_entry_overlaps_is_probe_eligible(tmp_path, repository):
    result = await run(tmp_path, repository, planted_cube(PLANTED, 1.0), entries=[])
    assert formula_id(PLANTED) in result["probe_eligible"] and result["already_tested"] == {}


async def test_confirmation_is_read_only_after_the_journal_consumed_it(tmp_path, repository, monkeypatch):
    created = []

    class Watched(CampaignWindows):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(campaign_run, "CampaignWindows", Watched)
    real = repository.consume_pooled_confirmation
    opened_at_consumption = []

    async def consume(**kwargs):
        opened_at_consumption.append(created[0].opened)
        await real(**kwargs)

    monkeypatch.setattr(repository, "consume_pooled_confirmation", consume)
    result = await run(tmp_path, repository, planted_cube(PLANTED, 1.0))
    assert result["status"] == "confirmed"
    assert opened_at_consumption == [("discovery", "selection")]


async def test_a_crash_mid_family_keeps_its_charges_and_a_rerun_resumes_without_recharging(
    tmp_path, repository, monkeypatch
):
    cube = planted_cube(PLANTED, 1.0)
    real = ScoreBook.formula
    calls = {"n": 0}

    def flaky(self, expression):
        calls["n"] += 1
        if calls["n"] == 5:
            raise RuntimeError("worker died")
        return real(self, expression)

    monkeypatch.setattr(ScoreBook, "formula", flaky)
    first = await run(tmp_path, repository, cube, out="one")
    assert first["status"] == "failed" and "worker died" in first["error"]
    assert (await repository.get("pooled/ledger"))["formulas_charged"] == 4
    assert (await repository.get(f"pooled/campaign/{CAMPAIGN}"))["status"] == "failed"
    monkeypatch.setattr(ScoreBook, "formula", real)
    second = await run(tmp_path, repository, cube, out="two")
    assert second["status"] == "confirmed"
    ledger = await repository.get("pooled/ledger")
    assert (ledger["formulas_charged"], ledger["campaigns"]) == (9, 1)
    third = await run(tmp_path, repository, cube, out="three")
    assert third["status"] == "failed" and "cannot run again" in third["error"]


async def test_the_campaign_refuses_a_cube_the_gates_did_not_run_on(tmp_path, repository):
    result = await run(tmp_path, repository, planted_cube(PLANTED, 1.0), gates_cube="x" * 64)
    assert result["status"] == "failed" and "not the cube checks A, B and C ran on" in result["error"]
    assert (await repository.get("pooled/ledger"))["formulas_charged"] == 0


async def test_a_dirty_revision_or_a_v1_protocol_is_refused_before_anything_is_reserved(tmp_path, repository):
    dirty = await run(tmp_path, repository, planted_cube(PLANTED, 1.0), out="one", revision="abc1234-dirty")
    assert dirty["status"] == "failed" and "dirty" in dirty["error"]
    v1_like = await run(
        tmp_path, repository, planted_cube(PLANTED, 1.0), out="two", protocol=mini_protocol(null_check=None)
    )
    assert v1_like["status"] == "failed" and "checks B and C" in v1_like["error"]
    assert await repository.get("pooled/ledger") is None


def _gate(tmp_path, *, check="power_a", revision="abc1234", **result_override) -> Path:
    directory = tmp_path / "gate"
    directory.mkdir()
    manifest = {"check": check, "environment": {"runtime": {"revision": revision}}}
    result = {
        "status": "passed",
        "cohort_sha256": COHORT,
        "campaign_protocol_sha256": "p" * 64,
        "cube_sha256": "k" * 64,
        **result_override,
    }
    (directory / "manifest.json").write_text(json.dumps(manifest))
    (directory / "result.json").write_text(json.dumps(result))
    return directory


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"check": "search_power"}, "not 'power_a'"),
        ({"status": "gate_failed"}, "has not passed"),
        ({"cohort_sha256": "d" * 64}, "different cohort"),
        ({"campaign_protocol_sha256": "q" * 64}, "different campaign protocol"),
        ({"revision": "def5678"}, "ran at code revision 'def5678'"),
        ({"cube_sha256": None}, "records no cube"),
    ],
)
def test_check_gate_refuses_each_mismatch(tmp_path, override, message):
    with pytest.raises(ValueError, match=message):
        check_gate(
            _gate(tmp_path, **override),
            check="power_a",
            cohort_sha256=COHORT,
            protocol_sha256="p" * 64,
            revision="abc1234",
        )


def test_check_gate_returns_a_matching_result(tmp_path):
    result = check_gate(
        _gate(tmp_path), check="power_a", cohort_sha256=COHORT, protocol_sha256="p" * 64, revision="abc1234"
    )
    assert result["cube_sha256"] == "k" * 64


@pytest.mark.parametrize("revision", ["abc1234-dirty", "unavailable"])
def test_require_clean_revision_refuses_dirty_or_unknown_code(revision):
    with pytest.raises(ValueError, match="dirty or unknown"):
        require_clean_revision({"runtime": {"revision": revision}})
    assert require_clean_revision({"runtime": {"revision": "abc1234"}}) == "abc1234"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_campaign_run.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentic_trader.research.pooled.campaign_run'`.

- [ ] **Step 3: Write `campaign_run.py`**

```python
# agentic_trader/research/pooled/campaign_run.py
"""The budgeted pooled campaign, run once per protocol against the journal ledger.

**Gate.** It refuses to start unless checks A, B and C all passed for this cohort and
protocol, on one cube, at the clean code revision that is running.

**Sequence.**

1. Write ``protocol.json`` and ``manifest.json``.
2. Reserve the campaign in the ledger before reading anything.
3. Build or load the cube.
4. Run the per-family search, charging each formula before it is scored.
5. Run the pooled discovery finish and selection.
6. Write the frozen documents.
7. Consume the lane-wide confirmation interval, then read it.

**Overlap rule.** A finalist whose discovery picks overlap a literature entry's at Jaccard
>= ``dedupe_jaccard`` is ``already_tested_by`` that entry. It keeps its Holm slot and is
never probe-eligible: both literature entries failed.

**Failure.** A failure is recorded as ``status: failed``; charges remain. A confirmed,
probe-eligible formula earns eligibility for a separately specified paper probe, nothing more.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from agentic_trader.research.pooled.campaign import CampaignProtocol, CampaignWindows, LoadedProtocol, _jaccard
from agentic_trader.research.pooled.cohort import LoadedCohort
from agentic_trader.research.pooled.cube import CubeView, check_coverage
from agentic_trader.research.pooled.formula import Formula, allowed_mask, select_picks
from agentic_trader.research.pooled.genetic import SearchOutcome, run_search_stages
from agentic_trader.research.pooled.ledger import JournalLedger
from agentic_trader.research.pooled.runner import CubeBuild
from agentic_trader.research.pooled.scoring import ScoreBook
from agentic_trader.research.setups.study import _finite_json
from agentic_trader.storage.artifacts import save_json_report


__all__ = [
    "GATES",
    "LITERATURE_ENTRIES",
    "already_tested",
    "campaign_id_for",
    "check_gate",
    "execute_campaign",
    "literature_cells",
    "require_clean_revision",
]

LITERATURE_ENTRIES = ("config/research/pooled/high52-v1.json", "config/research/pooled/reversal-lowmax-v1.json")
# (gate name, the ``check`` its manifest must record)
GATES = (("power", "power_a"), ("search_power", "search_power"), ("null_check", "null_check"))


def campaign_id_for(loaded: LoadedProtocol) -> str:
    return f"pooled-campaign-v{loaded.protocol.version}-{loaded.sha256[:16]}"


def require_clean_revision(environment: Mapping) -> str:
    revision = str(environment.get("runtime", {}).get("revision", "unavailable"))
    if revision == "unavailable" or revision.endswith("-dirty"):
        raise ValueError(f"refusing to run from a dirty or unknown code revision ({revision}); commit first")
    return revision


def check_gate(directory: Path, *, check: str, cohort_sha256: str, protocol_sha256: str, revision: str) -> dict:
    """A gate's passed result, for this cohort and protocol, at this exact code revision."""
    manifest = json.loads((directory / "manifest.json").read_text())
    result = json.loads((directory / "result.json").read_text())
    if manifest.get("check") != check:
        raise ValueError(f"{directory} holds a {manifest.get('check')!r} result, not {check!r}")
    if result.get("status") != "passed":
        raise ValueError(f"{check} has not passed (status {result.get('status')!r})")
    if result.get("cohort_sha256") != cohort_sha256:
        raise ValueError(f"{check} was run on a different cohort")
    if result.get("campaign_protocol_sha256") != protocol_sha256:
        raise ValueError(f"{check} was run under a different campaign protocol")
    ran_at = manifest.get("environment", {}).get("runtime", {}).get("revision")
    if ran_at != revision:
        raise ValueError(f"{check} ran at code revision {ran_at!r}; this campaign runs at {revision!r}")
    if not result.get("cube_sha256"):
        raise ValueError(f"{check} records no cube")
    return result


def literature_cells(entries: Iterable, view: CubeView, book: ScoreBook) -> dict[str, frozenset]:
    """Each literature entry's discovery picks on this cube (scores, filters and hold lengths)."""
    stop = view.offset + len(view.sessions)
    cells: dict[str, frozenset] = {}
    for loaded in entries:
        formula: Formula = loaded.entry.formula
        scores = book.panel(formula.score)[view.offset : stop]
        filters = [book.panel(spec.expression)[view.offset : stop] for spec in formula.filters]
        allowed = allowed_mask(formula, filters, view.eligible)
        cells[loaded.entry.id] = frozenset(select_picks(scores, allowed, view, formula.k).cells())
    return cells


def already_tested(
    carried: Sequence[str], cells: Mapping[str, frozenset], literature: Mapping[str, frozenset], threshold: float
) -> dict[str, str]:
    """Finalist id -> the literature entry whose discovery picks it overlaps at Jaccard >= threshold."""
    marked: dict[str, str] = {}
    for fid in carried:
        for entry_id, entry_cells in literature.items():
            if _jaccard(set(cells[fid]), set(entry_cells)) >= threshold:
                marked[fid] = entry_id
                break
    return marked


def _write_frozen(
    directory: Path, frozen: Sequence[str], search: SearchOutcome, protocol: CampaignProtocol
) -> list[dict]:
    documents = []
    for fid in frozen:
        formula = Formula(score=search.expressions[fid], k=protocol.k)
        save_json_report(
            {
                "formula_id": fid,
                "family": search.families[fid],
                "expression": search.expressions[fid],
                "formula": formula.model_dump(mode="json"),
                "identity": formula.identity,
            },
            directory / f"{fid}.json",
        )
        documents.append({"formula_id": fid, "identity": formula.identity})
    return documents


def _write_records(path: Path, records: Iterable[Mapping]) -> None:
    text = "".join(json.dumps(_finite_json(dict(record)), sort_keys=True) + "\n" for record in records)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as target:
        target.write(text)


async def execute_campaign(
    loaded: LoadedProtocol,
    directory: Path,
    *,
    cohort: LoadedCohort,
    build: Callable[[], Awaitable[CubeBuild]],
    gates: Mapping[str, Mapping],
    repository,
    entries: Sequence,
    environment: dict,
    progress: Callable[[str], None] | None = None,
) -> dict:
    protocol = loaded.protocol
    campaign_id = campaign_id_for(loaded)
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    save_json_report({**protocol.model_dump(mode="json"), "sha256": loaded.sha256}, directory / "protocol.json")
    save_json_report(
        {
            "check": "campaign",
            "campaign_id": campaign_id,
            "campaign_protocol_sha256": loaded.sha256,
            "cohort_sha256": cohort.sha256,
            "cube_spec_identity": protocol.cube_spec().identity,
            "gates": {
                name: {"status": gate.get("status"), "cube_sha256": gate.get("cube_sha256")}
                for name, gate in gates.items()
            },
            "literature_entries": [entry.entry.id for entry in entries],
            "environment": environment,
            "started_at": datetime.now(UTC).isoformat(),
            "authorizes_promotion": False,
        },
        directory / "manifest.json",
    )
    base = {
        "campaign_id": campaign_id,
        "cohort_sha256": cohort.sha256,
        "campaign_protocol_sha256": loaded.sha256,
        "authorizes_promotion": False,
    }
    reserved = False
    try:
        protocol.require_campaign_ready()
        revision = require_clean_revision(environment)
        if cohort.sha256 != protocol.cohort_sha256:
            raise ValueError("cohort file does not match the protocol's cohort_sha256")
        if set(gates) != {name for name, _ in GATES}:
            raise ValueError("checks A, B and C must all be given")
        cubes = {gate.get("cube_sha256") for gate in gates.values()}
        if len(cubes) != 1:
            raise ValueError("checks A, B and C did not all run on one cube")
        (cube_sha256,) = cubes
        await repository.reserve_pooled_campaign(
            campaign_id,
            {
                "protocol_sha256": loaded.sha256,
                "cohort_sha256": cohort.sha256,
                "cube_sha256": cube_sha256,
                "code_revision": revision,
                "budget": protocol.formula_budget,
            },
        )
        reserved = True
        built = await build()
        check_coverage(built.cube, protocol.coverage)
        if built.cube.sha256 != cube_sha256:
            raise ValueError("the cached cube is not the cube checks A, B and C ran on")
        book = ScoreBook(built.adjusted, built.trading_days, built.cube.sessions, built.cube.symbols)
        windows = CampaignWindows(built.cube, protocol.windows, cohort_sha256=cohort.sha256)
        ledger = JournalLedger(repository, asyncio.get_running_loop(), campaign_id)
        frozen_documents: list[dict] = []

        def on_frozen(frozen: list[str], search: SearchOutcome) -> None:
            frozen_documents.extend(_write_frozen(directory / "frozen", frozen, search, protocol))
            ledger.advance("frozen", {"candidates": frozen_documents})

        outcome, search = await asyncio.to_thread(
            run_search_stages,
            protocol,
            windows,
            book,
            ledger,
            ledger,
            campaign_id=campaign_id,
            on_frozen=on_frozen,
            progress=progress,
        )
        literature = await asyncio.to_thread(literature_cells, entries, windows.discovery(), book)
        marked = already_tested(outcome["carried"], search.cells, literature, protocol.dedupe_jaccard)
        confirmed = list(outcome["confirmed"])
        probe_eligible = [fid for fid in confirmed if fid not in marked]
        await asyncio.to_thread(_write_records, directory / "formulas.jsonl", search.records)
        rejected: Counter[str] = Counter()
        for run in search.runs:
            rejected.update(run.rejected)
        rows = {row["formula_id"]: row for row in outcome["discovery"]}
        result = {
            **base,
            "status": outcome["status"],
            "cube_sha256": built.cube.sha256,
            "code_revision": revision,
            "families": [run.summary() for run in search.runs],
            "proposals": {
                "evaluated": len(search.scores),
                "errors": sum(run.errors for run in search.runs),
                "rejected": dict(sorted(rejected.items())),
            },
            "survivors": sum(1 for row in outcome["discovery"] if row["passes"]),
            "carried": [
                {
                    **rows[fid],
                    "expression": search.expressions[fid],
                    "family": search.families[fid],
                    "already_tested_by": marked.get(fid),
                }
                for fid in outcome["carried"]
            ],
            "selection": outcome["selection"],
            "frozen": frozen_documents,
            "confirmation": outcome["confirmation"],
            "confirmed": confirmed,
            "probe_eligible": probe_eligible,
            "already_tested": marked,
            "expressions": {fid: search.expressions[fid] for fid in sorted({*outcome["carried"], *outcome["frozen"]})},
        }
        await repository.advance_pooled_campaign(
            campaign_id,
            "completed",
            {
                "status": outcome["status"],
                "confirmed": confirmed,
                "probe_eligible": probe_eligible,
                "already_tested": marked,
            },
        )
    except Exception as exc:
        result = {**base, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        if reserved:
            try:
                await repository.advance_pooled_campaign(campaign_id, "failed", {"error": result["error"]})
            except Exception as ledger_exc:  # e.g. the campaign was already completed
                result["ledger_error"] = f"{type(ledger_exc).__name__}: {ledger_exc}"
    save_json_report(_finite_json(result), directory / "result.json")
    return result
```

Note on the third run in `test_a_crash_mid_family_…`: the reservation is refused because the campaign completed. `reserved` stays False, so no ledger write follows.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_campaign_run.py -q`
Expected: PASS.

- [ ] **Step 5: Stage**

```bash
git add agentic_trader/research/pooled/campaign_run.py tests/research/pooled/test_campaign_run.py
```

---

### Task 14: CLI — `alpha pooled search-power | null-check | campaign`

**Files:**
- Modify: `agentic_trader/cli/commands/alpha.py`
- Test: `tests/cli/test_alpha_pooled_cli.py`

**Interfaces:**
- Consumes: Tasks 11–13 executors; `campaign_run.GATES`, `LITERATURE_ENTRIES`, `check_gate`, `require_clean_revision`; `alpha_repository()`; `_apriori_clients()`; `_pooled_build`.
- Produces:
  - `copilot alpha pooled search-power PROTOCOL --power DIR --output DIR --cache DIR`;
  - `copilot alpha pooled null-check PROTOCOL --power DIR --output DIR --cache DIR [--workers N]`;
  - `copilot alpha pooled campaign PROTOCOL --power DIR --search-power DIR --null-check DIR --output DIR --cache DIR`.

- [ ] **Step 1: Write the failing tests (append to `tests/cli/test_alpha_pooled_cli.py`)**

Add `from contextlib import asynccontextmanager` and `from agentic_trader.research.pooled.campaign import load_campaign_protocol` to the file's imports, then append:

```python
PROTOCOL_V2 = str(REPO_ROOT / "config/research/pooled/campaign-v2.json")


def _gates(tmp_path, revision="abc1234", **revisions) -> list[Path]:
    loaded = load_campaign_protocol(Path(PROTOCOL_V2))
    dirs = []
    for name, check in (("power", "power_a"), ("search", "search_power"), ("null", "null_check")):
        directory = tmp_path / name
        directory.mkdir()
        manifest = {"check": check, "environment": {"runtime": {"revision": revisions.get(name, revision)}}}
        result = {
            "status": "passed",
            "cohort_sha256": loaded.protocol.cohort_sha256,
            "campaign_protocol_sha256": loaded.sha256,
            "cube_sha256": "k" * 64,
        }
        (directory / "manifest.json").write_text(json.dumps(manifest))
        (directory / "result.json").write_text(json.dumps(result))
        dirs.append(directory)
    return dirs


def _campaign(tmp_path, dirs):
    power, search, null = dirs
    return _invoke(
        "campaign",
        PROTOCOL_V2,
        "--power",
        str(power),
        "--search-power",
        str(search),
        "--null-check",
        str(null),
        "--output",
        str(tmp_path / "out"),
        "--cache",
        str(tmp_path / "cache"),
    )


def _forbid_repository(monkeypatch):
    def boom():
        raise AssertionError("the ledger must not be opened")

    monkeypatch.setattr(alpha_cli, "alpha_repository", boom)


def test_search_power_refuses_a_failed_power_check_without_creating_clients(tmp_path, monkeypatch):
    _forbid_clients(monkeypatch)
    power = tmp_path / "power"
    power.mkdir()
    (power / "result.json").write_text(json.dumps({"status": "gate_failed"}))
    result = _invoke(
        "search-power",
        PROTOCOL_V2,
        "--power",
        str(power),
        "--output",
        str(tmp_path / "out"),
        "--cache",
        str(tmp_path / "c"),
    )
    assert result.exit_code != 0 and "has not passed" in result.output


def test_null_check_passes_the_worker_count_through(tmp_path, monkeypatch):
    seen = {}
    power, _, _ = _gates(tmp_path)

    @contextmanager
    def clients():
        yield SimpleNamespace(bars=None, calendar=None, static_symbols=[], pace=None)

    async def fake_execute(loaded, output, **kwargs):
        seen.update(kwargs)
        return {"status": "passed"}

    monkeypatch.setattr(alpha_cli, "_apriori_clients", clients)
    monkeypatch.setattr(alpha_cli, "execute_null_check", fake_execute)
    monkeypatch.setattr(alpha_cli, "research_environment", lambda: {})
    result = _invoke(
        "null-check",
        PROTOCOL_V2,
        "--power",
        str(power),
        "--output",
        str(tmp_path / "out"),
        "--cache",
        str(tmp_path / "c"),
        "--workers",
        "4",
    )
    assert result.exit_code == 0, result.output
    assert seen["workers"] == 4 and seen["power_result"]["status"] == "passed"


def test_campaign_refuses_a_dirty_revision_before_opening_the_ledger(tmp_path, monkeypatch):
    _forbid_clients(monkeypatch)
    _forbid_repository(monkeypatch)
    monkeypatch.setattr(alpha_cli, "research_environment", lambda: {"runtime": {"revision": "abc1234-dirty"}})
    result = _campaign(tmp_path, _gates(tmp_path))
    assert result.exit_code != 0 and "dirty" in result.output


def test_campaign_refuses_a_gate_from_another_revision(tmp_path, monkeypatch):
    _forbid_clients(monkeypatch)
    _forbid_repository(monkeypatch)
    monkeypatch.setattr(alpha_cli, "research_environment", lambda: {"runtime": {"revision": "abc1234"}})
    result = _campaign(tmp_path, _gates(tmp_path, null="def5678"))
    assert result.exit_code != 0 and "null_check ran at code revision 'def5678'" in result.output


def test_campaign_hands_the_checked_gates_and_the_literature_entries_to_the_executor(tmp_path, monkeypatch):
    seen = {}

    @asynccontextmanager
    async def repository():
        yield "repository"

    @contextmanager
    def clients():
        yield SimpleNamespace(bars=None, calendar=None, static_symbols=[], pace=None)

    async def fake_execute(loaded, output, **kwargs):
        seen.update(kwargs)
        return {"status": "none_confirmed"}

    monkeypatch.setattr(alpha_cli, "alpha_repository", repository)
    monkeypatch.setattr(alpha_cli, "_apriori_clients", clients)
    monkeypatch.setattr(alpha_cli, "execute_campaign", fake_execute)
    monkeypatch.setattr(alpha_cli, "research_environment", lambda: {"runtime": {"revision": "abc1234"}})
    result = _campaign(tmp_path, _gates(tmp_path))
    assert result.exit_code == 0, result.output
    assert set(seen["gates"]) == {"power", "search_power", "null_check"}
    assert seen["repository"] == "repository"
    assert [entry.entry.id for entry in seen["entries"]] == ["high52", "reversal-lowmax"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/cli/test_alpha_pooled_cli.py -q`
Expected: FAIL (`No such command 'search-power'`, and the same for the others).

- [ ] **Step 3: Add the commands to `agentic_trader/cli/commands/alpha.py`**

Add imports next to the other pooled imports:

```python
from agentic_trader.research.pooled.campaign_run import (
    GATES,
    LITERATURE_ENTRIES,
    check_gate,
    execute_campaign,
    require_clean_revision,
)
from agentic_trader.research.pooled.null_check import execute_null_check
from agentic_trader.research.pooled.search_power import execute_search_power
```

Add after `alpha_pooled_screen_cmd`:

```python
async def _pooled_protocol(protocol_path: Path):
    loaded = await asyncio.to_thread(load_campaign_protocol, protocol_path)
    cohort = await asyncio.to_thread(load_cohort, REPO_ROOT / loaded.protocol.cohort)
    if cohort.sha256 != loaded.protocol.cohort_sha256:
        raise click.ClickException("Cohort file does not match the protocol's cohort_sha256")
    return loaded, cohort


def _passed_power(power_dir: Path, loaded, cohort) -> dict:
    path = power_dir / "result.json"
    if not path.exists():
        raise click.ClickException(f"No power result at {path}")
    power_result = json.loads(path.read_text())
    try:
        check_power_gate(power_result, cohort_sha256=cohort.sha256, campaign_protocol_sha256=loaded.sha256)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    return power_result


@alpha_pooled_group.command("search-power")
@click.argument("protocol_path", type=click.Path(exists=True, path_type=Path))
@click.option("--power", "power_dir", type=click.Path(exists=True, path_type=Path), required=True)
@click.option("--output", type=click.Path(path_type=Path), required=True, help="New private directory; no overwrite")
@click.option("--cache", type=click.Path(path_type=Path), required=True, help=_POOLED_CACHE_HELP)
@coro
async def alpha_pooled_search_power_cmd(protocol_path, power_dir, output, cache):
    """Check B: can each family's search recover a planted near-seed edge (discovery cells only)."""
    if output.exists():
        raise click.ClickException(f"Output directory already exists; refusing to overwrite: {output}")
    loaded, cohort = await _pooled_protocol(protocol_path)
    power_result = _passed_power(power_dir, loaded, cohort)
    environment = await asyncio.to_thread(research_environment)
    with _apriori_clients() as clients:
        result = await execute_search_power(
            loaded,
            output,
            cohort=cohort,
            build=_pooled_build(clients, cohort, loaded.protocol.cube_spec(), cache),
            power_result=power_result,
            environment=environment,
            progress=lambda message: click.echo(message, err=True),
        )
    click.echo(json.dumps({k: result.get(k) for k in ("status", "recovered", "error")}, indent=2, default=str))
    if result.get("status") != "passed":
        raise click.ClickException(f"Check B {result.get('status')}; see result.json")


@alpha_pooled_group.command("null-check")
@click.argument("protocol_path", type=click.Path(exists=True, path_type=Path))
@click.option("--power", "power_dir", type=click.Path(exists=True, path_type=Path), required=True)
@click.option("--output", type=click.Path(path_type=Path), required=True, help="New private directory; no overwrite")
@click.option("--cache", type=click.Path(path_type=Path), required=True, help=_POOLED_CACHE_HELP)
@click.option(
    "--workers",
    type=click.IntRange(min=1),
    default=1,
    show_default=True,
    help="Replicates run in this many processes; the result is identical for any worker count.",
)
@coro
async def alpha_pooled_null_check_cmd(protocol_path, power_dir, output, cache, workers):
    """Check C: real formulas on per-name-demeaned discovery panels must rarely be confirmed."""
    if output.exists():
        raise click.ClickException(f"Output directory already exists; refusing to overwrite: {output}")
    loaded, cohort = await _pooled_protocol(protocol_path)
    power_result = _passed_power(power_dir, loaded, cohort)
    environment = await asyncio.to_thread(research_environment)
    with _apriori_clients() as clients:
        result = await execute_null_check(
            loaded,
            output,
            cohort=cohort,
            build=_pooled_build(clients, cohort, loaded.protocol.cube_spec(), cache),
            power_result=power_result,
            environment=environment,
            progress=lambda message: click.echo(message, err=True),
            workers=workers,
        )
    summary = ("status", "false_acceptances", "replicates", "seed_t", "error")
    click.echo(json.dumps({k: result.get(k) for k in summary}, indent=2, default=str))
    if result.get("status") != "passed":
        raise click.ClickException(f"Check C {result.get('status')}; see result.json")


@alpha_pooled_group.command("campaign")
@click.argument("protocol_path", type=click.Path(exists=True, path_type=Path))
@click.option("--power", "power_dir", type=click.Path(exists=True, path_type=Path), required=True)
@click.option("--search-power", "search_dir", type=click.Path(exists=True, path_type=Path), required=True)
@click.option("--null-check", "null_dir", type=click.Path(exists=True, path_type=Path), required=True)
@click.option("--output", type=click.Path(path_type=Path), required=True, help="New private directory; no overwrite")
@click.option("--cache", type=click.Path(path_type=Path), required=True, help=_POOLED_CACHE_HELP)
@coro
async def alpha_pooled_campaign_cmd(protocol_path, power_dir, search_dir, null_dir, output, cache):
    """Run the budgeted pooled campaign once; writes the pooled ledger (research only; grants no credit)."""
    if output.exists():
        raise click.ClickException(f"Output directory already exists; refusing to overwrite: {output}")
    loaded, cohort = await _pooled_protocol(protocol_path)
    environment = await asyncio.to_thread(research_environment)
    try:
        revision = require_clean_revision(environment)
        gates = {
            name: check_gate(
                directory, check=check, cohort_sha256=cohort.sha256, protocol_sha256=loaded.sha256, revision=revision
            )
            for (name, check), directory in zip(GATES, (power_dir, search_dir, null_dir), strict=True)
        }
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    entries = [await asyncio.to_thread(load_pooled_entry, REPO_ROOT / path) for path in LITERATURE_ENTRIES]
    async with alpha_repository() as repository:
        with _apriori_clients() as clients:
            result = await execute_campaign(
                loaded,
                output,
                cohort=cohort,
                build=_pooled_build(clients, cohort, loaded.protocol.cube_spec(), cache),
                gates=gates,
                repository=repository,
                entries=entries,
                environment=environment,
                progress=lambda message: click.echo(message, err=True),
            )
    summary = ("status", "campaign_id", "confirmed", "probe_eligible", "already_tested", "error")
    click.echo(json.dumps({k: result.get(k) for k in summary}, indent=2, default=str))
    if result.get("status") == "failed":
        raise click.ClickException("Pooled campaign failed; see result.json for the reason")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/cli/test_alpha_pooled_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Stage**

```bash
git add agentic_trader/cli/commands/alpha.py tests/cli/test_alpha_pooled_cli.py
```

---

### Task 15: Documentation (the contract; results follow in Task 16)

**Files:**
- Modify: `docs/alpha-pooled-mining.md`, `docs/alpha-roadmap.md`, `CLAUDE.md`

- [ ] **Step 1: `docs/alpha-pooled-mining.md`**

Edit and add the following, in the file's style (short paragraphs and lists, repository-relative links):

- **Cohort (edit):**
  - State that v2 (`cohort-v2.json`) replaces the random 300-name snapshot with the screen's top 400 plus the scan equities.
  - Add a subsection **"Cohort v2: the liquidity screen"** covering:
    - the frozen rule `config/research/pooled/screen-v2.json` (snapshot id and hash; the 60 sessions ending 2023-12-29, a date before confirmation; 55 sessions with bars; $10; top 400);
    - the exclusion rules and their recorded reasons, and the kept kinds (MLP units, ADRs, class shares);
    - `alpha pooled screen` and its outputs (`screen.csv`, `cohort.json`), and that it reads no returns or labels;
    - that eligibility per session is unchanged.
  - Replace the caveat "Cohort hygiene" with: "Cohort v1 let SPAC units, warrants and preferreds through the symbol pattern; v2's screen excludes them by NASDAQ fifth letter and security name."
- **Campaign protocol and stages (edit):**
  - v2 adds per-family `windows` (list the table), `power_search.seed` and `null_check`; v1 cannot run a campaign.
  - Add **"The genetic search"**:
    - per-family `TypedGeneticSearch` (seeds, operators, constant windows; seed `search.seed * 100 + index`);
    - the 17×8 + 16×4 budget and the rejection reasons, which are never charged;
    - 200 consecutive rejections stop a family;
    - charge before scoring; label-blind `ScoreBook` panels.
  - Add **"The pooled ledger"**:
    - the five keys and what each holds;
    - reservation before any read; immutable fields (protocol, cohort, cube, code revision, budget);
    - resume rules; lane-wide confirmation;
    - `alpha status` shows `pooled_ledger`.
  - Replace the heading "Literature overlap rule (Part 1b requirement)" with "Literature overlap rule". State that the campaign implements it (`already_tested_by`), and keep the anchor text in the roadmap link updated.
- **Checks B and C (new section after "Power check A"):**
  - procedure, acceptance (≥ 8/10; ≤ 2/40), what each certifies and does not;
  - check C's reported seed-t standard deviation and its meaning.
- **Commands (edit):** add

```bash
copilot alpha pooled screen RULE --snapshot SNAPSHOT --output DIR --cache DIR
copilot alpha pooled search-power PROTOCOL --power A_DIR --output DIR --cache DIR
copilot alpha pooled null-check PROTOCOL --power A_DIR --output DIR --cache DIR [--workers N]
copilot alpha pooled campaign PROTOCOL --power A_DIR --search-power B_DIR --null-check C_DIR --output DIR --cache DIR
```

  Also add bullets:
  - the gate binding: A, B and C passed for this cohort, protocol and one cube, at the same clean revision; a dirty tree is refused;
  - the circuit breaker (5 consecutive failed fetches);
  - the bar-file re-check on a cube-cache hit;
  - the campaign statuses;
  - the outputs (`formulas.jsonl`, `frozen/`, `result.json`).
- **What it never does (edit):** replace "No database or registry write" with "No registry write; the campaign's only database writes are the pooled ledger's research events (never `family/all`)."

- [ ] **Step 2: `docs/alpha-roadmap.md` (pooled item)**

- Replace "Part 1b is next: the campaign runner, the journal-backed ledger, the genetic search and search check B." with "Part 1b (this workstream): cohort v2 by liquidity screen, campaign protocol v2, the per-family genetic search, the journal ledger, checks B and C and the campaign runner ([spec](superpowers/specs/2026-10-02-pooled-alpha-mining-part1b-design.md))."
- In the breadth paragraph, replace "That is an operator decision and is not made here; a new cohort is a new identity." with "The operator chose a liquidity-screened cohort v2 (2026-10-01)."
- Leave room for the results bullet that Task 16 adds.

- [ ] **Step 3: `CLAUDE.md` (pooled paragraph)**

Replace the pooled paragraph with:

```markdown
The [pooled mining lane](docs/alpha-pooled-mining.md) evaluates one dimensionless DSL
formula across a frozen cohort on a hash-identified long-bracket label cube, testing a
session-paired edge with a session-block bootstrap. Cohort v2 (`config/research/pooled/cohort-v2.json`)
comes from a frozen liquidity screen (`screen-v2.json`, ranked before the confirmation
window); `campaign-v2.json` pins it. The campaign runs once per protocol and only after
power check A, search check B and the real-formula false-acceptance check C passed on the
same cohort, cube, protocol and clean code revision. It charges every formula to the
pooled ledger (journal events, never `family/all`) before scoring it and consumes the
lane-wide confirmation window before reading it. Research only: no registry, broker,
Telegram or promotion credit.
```

- [ ] **Step 4: Check links and stage**

Run: `env -u VIRTUAL_ENV uv run pre-commit run --files docs/alpha-pooled-mining.md docs/alpha-roadmap.md CLAUDE.md`
Expected: PASS.

```bash
git add docs/alpha-pooled-mining.md docs/alpha-roadmap.md CLAUDE.md
```

---

### Task 16 (controller): Verification, the gate runs, the campaign and the result documents

- [ ] **Step 1: Full verification on the branch**

```bash
cd /Users/adamhadani/Development/agentic-trader-pooled1b && env -u VIRTUAL_ENV uv run pytest -q
TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_trader env -u VIRTUAL_ENV uv run pytest tests/integration --run-postgres -q
env -u VIRTUAL_ENV uv run pre-commit run --all-files
```

Expected: all PASS. Run the final whole-branch review and its single fix wave **before** Step 2. The gates bind to the revision, so no code commit may follow Step 2 until the campaign has run.

- [ ] **Step 2: Commit, confirm the tree is clean, rerun check A on the cached cube**

`git status --porcelain` must be empty, and `git describe --always --dirty` must not end in `-dirty`. Then, outside the scan windows:

```bash
cd /Users/adamhadani/Development/agentic-trader-pooled1b && (set -a; source /Users/adamhadani/Development/agentic-trader/.envrc >/dev/null 2>&1; set +a; nohup env -u VIRTUAL_ENV uv run copilot alpha pooled power config/research/pooled/campaign-v2.json --output ~/agentic-trader-research/pooled-power-a-v2-$(date +%Y%m%d) --cache ~/agentic-trader-research/pooled-cache-v1 --workers 6 > ~/agentic-trader-research/logs/pooled-power-a-v2-$(date +%Y%m%d).log 2>&1 &)
```

Expected:
- a cube cache hit with `bar files re-checked: N unchanged`;
- `status: passed`;
- the same cube SHA-256 as Task 5's build.

If A fails, stop: write the result doc and report. No B, C or campaign follows.

- [ ] **Step 3: Check B, then check C**

Both are long; launch each in the background with a log, never in the foreground. Both need
check A's cached cube (a cache miss is refused before any provider access).

```bash
cd /Users/adamhadani/Development/agentic-trader-pooled1b && (set -a; source /Users/adamhadani/Development/agentic-trader/.envrc >/dev/null 2>&1; set +a; nohup env -u VIRTUAL_ENV uv run copilot alpha pooled search-power config/research/pooled/campaign-v2.json --power ~/agentic-trader-research/pooled-power-a-v2-YYYYMMDD --output ~/agentic-trader-research/pooled-search-power-v2-$(date +%Y%m%d) --cache ~/agentic-trader-research/pooled-cache-v1 > ~/agentic-trader-research/logs/pooled-search-power-v2-$(date +%Y%m%d).log 2>&1 &)
cd /Users/adamhadani/Development/agentic-trader-pooled1b && (set -a; source /Users/adamhadani/Development/agentic-trader/.envrc >/dev/null 2>&1; set +a; nohup env -u VIRTUAL_ENV uv run copilot alpha pooled null-check config/research/pooled/campaign-v2.json --power ~/agentic-trader-research/pooled-power-a-v2-YYYYMMDD --output ~/agentic-trader-research/pooled-null-check-v2-$(date +%Y%m%d) --cache ~/agentic-trader-research/pooled-cache-v1 --workers 6 > ~/agentic-trader-research/logs/pooled-null-check-v2-$(date +%Y%m%d).log 2>&1 &)
```

If either fails, stop: write the result doc, record what failed and why (from the result's per-seed or per-replicate detail), and report to the operator with a redesign recommendation. No campaign follows.

- [ ] **Step 4: Ask the operator before the campaign**

The campaign is irreversible:
- it consumes the lane's confirmation window (2024-01-02 → 2026-07-31) for good;
- it writes research events to the live journal database (the daemon's PostgreSQL) under the alpha lock.

Report A, B and C in a few lines and **ask for explicit approval** to run it. The go/no-go must include:
- C's seed-t standard deviation;
- B's and C's `errors_total`, which **must be 0** (a nonzero count is a systematic evaluation error: stop and fix it, no campaign);
- the journal scope the campaign will pin: the daemon's `<environment>/<execution mode>` (for the paper daemon the mode is `alpaca:paper`), taken from the daemon's own configuration. Step 5 passes it as `--journal-scope`; the command refuses any other scope and any non-PostgreSQL journal before reserving anything.

- [ ] **Step 5: Run the campaign (after approval, outside the scan windows)**

Launch in the background with a log, never in the foreground. The run **must not be interrupted**: cancellation (Ctrl-C or SIGTERM) is handled and keeps whatever was consumed and computed, but recovery may be needed. `--journal-scope` is the scope approved in Step 4.

```bash
cd /Users/adamhadani/Development/agentic-trader-pooled1b && (set -a; source /Users/adamhadani/Development/agentic-trader/.envrc >/dev/null 2>&1; set +a; nohup env -u VIRTUAL_ENV uv run copilot alpha pooled campaign config/research/pooled/campaign-v2.json --power ~/agentic-trader-research/pooled-power-a-v2-YYYYMMDD --search-power ~/agentic-trader-research/pooled-search-power-v2-YYYYMMDD --null-check ~/agentic-trader-research/pooled-null-check-v2-YYYYMMDD --output ~/agentic-trader-research/pooled-campaign-v2-$(date +%Y%m%d) --cache ~/agentic-trader-research/pooled-cache-v1 --journal-scope <the daemon's scope> > ~/agentic-trader-research/logs/pooled-campaign-v2-$(date +%Y%m%d).log 2>&1 &)
```

Then confirm the ledger with `env -u VIRTUAL_ENV uv run copilot alpha status` (same sourced environment). Check `pooled_ledger`: formulas charged ≤ 200, campaigns 1, confirmations 1 if a candidate was frozen.

If `result.json` is `failed` or `cancelled`, read `confirmation_consumed`:
- `false`: nothing was consumed; rerun the same command with a new `--output` (the campaign resumes and charges nothing twice).
- `true` without an `outcome.json`: rerun the same command with `--recover` and a new `--output`. It recomputes the confirmation for the journaled frozen candidates and charges and consumes nothing.

- [ ] **Step 6: Result documents and the PR**

- Write `docs/alpha-pooled-checks-<date>.md`, covering breadth (v1 vs v2 eligible names per session and by year), check A on v2 (curve), check B (per seed) and check C (false acceptances, stage counts, seed-t mean and standard deviation, and what they mean).
- Write `docs/alpha-pooled-campaign-<date>.md`, covering:
  - status;
  - per-family charged, evaluated and rejected counts;
  - survivors;
  - carried formulas with expressions, discovery statistics and overlap marks;
  - selection; frozen; confirmation rows with Holm p-values;
  - confirmed and probe-eligible formulas;
  - the caveats that apply.
- Add the results bullet to `docs/alpha-roadmap.md` and one sentence to `CLAUDE.md`.
- Update the memory notes (`pooled-mining-part1b-carryover.md`: delivered; `alpha-expansion-survey.md`: result).
- Commit; push `research/pooled-mining-1b`; open the PR. Its body summarizes the build, the checks and the campaign outcome, with links to the result docs.
- Wait for the operator's approval to merge.
