# Pooled fixed-set protocol (campaign v4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `fixed_set` search mode for pooled campaign protocols that scores a predeclared formula set through the existing staged gates with no genetic search and no check B, plus protocol v4 (eight literature formulas, decile selection).

**Architecture:** `CampaignProtocol` gains `search_mode` with mode-specific validators and mode-derived gate lists; the family search never mutates in fixed-set mode (and a family with no operators stops cleanly in any mode); the campaign runner and CLI take their gate list from the protocol instead of a module constant; checks A and C are unchanged in computation. Protocol v4 is a copy of v3 with the fixed set.

**Tech Stack:** Python 3.14, `uv`, pydantic v2 frozen models (`extra="forbid"`), pytest, click.

**Spec:** `docs/superpowers/specs/2026-10-07-pooled-fixed-set-design.md`

Run every command from `/Users/adamhadani/Development/agentic-trader-fixedset` with `env -u VIRTUAL_ENV uv run …`.

## Global Constraints

- The v1, v2 and v3 protocol files are not modified; their byte hashes stay valid (pinned test). `search_mode` defaults to `"genetic"`.
- In `fixed_set` mode: `power_search` must be absent, `null_check` present, every family `mutation_operators == []` and `windows is None`, `formula_budget == total seeds`, seed canonical expressions unique across families. Genetic mode validators are unchanged.
- No change to cube, cohort, bracket, costs, windows, bootstrap, gates or check computations. Checks A and C only gain the protocol-mode acceptance.
- The research CLI runs only against the cached cube; nothing in this plan runs a check or a campaign (the controller runs them after merge). Tests use the mini synthetic world (`tests/research/pooled/mini_world.py`) and never touch the network or the production journal.
- No registry, broker or Telegram change; no migration; no `AppConfig` key.
- TDD per task (RED recorded, then GREEN); regression gate before committing: `env -u VIRTUAL_ENV uv run pytest -q tests/research/pooled tests/cli/test_alpha_pooled_cli.py`. Final task: full `pytest -q` and `pre-commit run --all-files`.
- Docstrings and docs describe final behaviour only.
- Commit trailer: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Review Focus

1. **A fixed-set family whose seed duplicates another family's seed (canonically)** must fail protocol validation, because the search shares one `seen` set and would otherwise silently skip it — pinned in Task 1.
2. **A genetic-mode family with an empty operator list** must stop with `stopped_short="no_operators"` rather than raise `IndexError` deep in `mutate` — pinned in Task 2.
3. **A fixed-set protocol passed to `search-power`** must be refused before any cube read — pinned in Task 3.
4. **A genetic protocol passed to `campaign` without `--search-power`** must be refused before any reservation — pinned in Task 3.
5. **v1–v3 hashes** must be unchanged after Task 1 — pinned in Task 1.

---

### Task 1: `search_mode`, fixed-set validators, mode-derived gates, protocol v4

**Files:**
- Modify: `agentic_trader/research/pooled/campaign.py` (`CampaignProtocol` fields, `_consistent`, `require_campaign_ready`, new `gates`/`requires_search_power` properties)
- Create: `config/research/pooled/campaign-v4.json`
- Test: `tests/research/pooled/test_campaign_v4.py` (new), `tests/research/pooled/test_campaign_protocol.py` (extend)

**Interfaces:**
- Produces: `CampaignProtocol.search_mode: Literal["genetic", "fixed_set"]`, `CampaignProtocol.gates -> tuple[tuple[str, str], ...]` (`(("power","power_a"),("null_check","null_check"))` for fixed-set; the current three for genetic), `CampaignProtocol.requires_search_power -> bool`, `CampaignProtocol.seed_expressions -> tuple[str, ...]` (canonical, in file order).
- `power_search: PowerSearchSpec | None = None` (was required). Genetic mode validators require it to be present (so v2/v3 still validate and v1 keeps its existing "predates checks B and C" behaviour through `require_campaign_ready`).

- [ ] **Step 1: Write the failing tests**

`tests/research/pooled/test_campaign_v4.py`:

```python
import hashlib
from pathlib import Path

import pytest

from agentic_trader.research.alpha.dsl import canonical_expression
from agentic_trader.research.pooled.campaign import CampaignProtocol, load_campaign_protocol
from agentic_trader.research.pooled.formula import require_dimensionless

REPO = Path(__file__).resolve().parents[3]
V3 = load_campaign_protocol(REPO / "config/research/pooled/campaign-v3.json")
V4 = load_campaign_protocol(REPO / "config/research/pooled/campaign-v4.json")

FIXED_SET = {
    "reversal_5": "-1.0 * roc(close, 5)",
    "high_126": "close / ts_max(high, 126)",
    "overnight_intraday": "ts_sum(open_gap, 21) - ts_sum(oc_spread, 21)",
    "abnormal_volume": "volume / ts_mean(volume, 50)",
    "momentum_12_1": "delay(close, 21) / delay(close, 252) - 1.0",
    "price_volume_corr": "-1.0 * ts_corr(returns, volume, 21)",
    "illiquidity": "ts_mean(hl_spread, 21)",
    "reversal_x_volume": "-1.0 * roc(close, 5) * (volume / ts_mean(volume, 50))",
}
LITERATURE_SCORES = ("close / ts_max(high, 252)", "-1.0 * roc(close, 21)")


def test_earlier_protocols_are_byte_identical_and_genetic():
    for name, sha in (("campaign-v1.json", None), ("campaign-v2.json", "6470ca97"), ("campaign-v3.json", "d713b575")):
        loaded = load_campaign_protocol(REPO / "config/research/pooled" / name)
        assert loaded.protocol.search_mode == "genetic"
        if sha:
            assert loaded.sha256.startswith(sha)
    assert V3.protocol.gates == (("power", "power_a"), ("search_power", "search_power"), ("null_check", "null_check"))
    assert V3.protocol.requires_search_power is True


def test_v4_is_v3_with_the_fixed_set():
    p3, p4 = V3.protocol, V4.protocol
    assert p4.version == 4 and p4.search_mode == "fixed_set"
    for field in (
        "cohort",
        "cohort_sha256",
        "feed",
        "bars_from",
        "bars_through",
        "windows",
        "selection",
        "bracket",
        "universe",
        "decision_cost_bps",
        "coverage",
        "search",
        "complexity_penalty_per_node",
        "dedupe_jaccard",
        "bootstrap",
        "discovery_gate",
        "selection_gate",
        "confirmation_gate",
        "power",
        "null_check",
        "excluded_families",
    ):
        assert getattr(p4, field) == getattr(p3, field), field
    assert p4.power_search is None and p4.k is None
    assert p4.formula_budget == 8 and len(p4.families) == 8
    assert {f.id: f.seeds[0] for f in p4.families} == FIXED_SET
    assert all(f.mutation_operators == () and f.windows is None for f in p4.families)
    assert p4.gates == (("power", "power_a"), ("null_check", "null_check"))
    assert p4.requires_search_power is False
    assert p4.family_budgets() == {f.id: 1 for f in p4.families}
    p4.require_campaign_ready()  # fixed-set needs null_check only
    assert p4.cube_spec().identity == p3.cube_spec().identity  # same cached cube


def test_v4_seeds_are_valid_unique_and_distinct_from_the_literature_entries():
    seeds = V4.protocol.seed_expressions
    assert len(seeds) == 8 and len(set(seeds)) == 8
    for expression in FIXED_SET.values():
        require_dimensionless(expression)
    literature = {canonical_expression(s) for s in LITERATURE_SCORES}
    assert not (set(seeds) & literature)


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        (
            {
                "families": [
                    {"id": "reversal_5", "seeds": ["-1.0 * roc(close, 5)"], "mutation_operators": ["ts_mean"]}
                ],
                "formula_budget": 1,
            },
            "mutation_operators",
        ),
        ({"formula_budget": 9}, "formula_budget"),
        (
            {"power_search": {"families": ["reversal_5"], "seeds": 1, "delta": 0.15, "min_recovered": 1, "seed": 1}},
            "power_search",
        ),
        ({"null_check": None}, "null_check"),
        (
            {
                "families": [
                    {"id": "a", "seeds": ["-1.0 * roc(close, 5)"], "mutation_operators": []},
                    {"id": "b", "seeds": ["-1 * roc(close, 5)"], "mutation_operators": []},
                ],
                "formula_budget": 2,
            },
            "unique",
        ),
        (
            {
                "families": [
                    {"id": "a", "seeds": ["-1.0 * roc(close, 5)"], "mutation_operators": [], "windows": [5, 10]}
                ],
                "formula_budget": 1,
            },
            "windows",
        ),
    ],
)
def test_fixed_set_validators(patch, message):
    document = {**V4.protocol.model_dump(mode="json"), **patch}
    with pytest.raises(ValueError, match=message):
        CampaignProtocol.model_validate(document)


def test_genetic_mode_still_requires_power_search():
    document = {**V3.protocol.model_dump(mode="json"), "power_search": None}
    with pytest.raises(ValueError, match="power_search"):
        CampaignProtocol.model_validate(document)
```

Check the exact names `require_dimensionless` and `canonical_expression` exist (`formula.py:45-48`, `research/alpha/dsl.py`); adjust imports, never the assertions. The v1 file predates `power_search` — verify whether it carries one; if it does not, the `power_search: PowerSearchSpec | None` change is what lets v1 keep loading; if it does, fine.

- [ ] **Step 2: Run to verify failure**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/research/pooled/test_campaign_v4.py`
Expected: FAIL — `campaign-v4.json` missing / `search_mode` unknown field.

- [ ] **Step 3: Implement**

`campaign.py`:

```python
SearchMode = Literal["genetic", "fixed_set"]

class CampaignProtocol(BaseModel, frozen=True, extra="forbid"):
    ...
    search_mode: SearchMode = "genetic"
    ...
    power_search: PowerSearchSpec | None = None
    null_check: NullCheckSpec | None = None

    @model_validator(mode="after")
    def _consistent(self) -> CampaignProtocol:
        ...existing checks...
        if self.search_mode == "fixed_set":
            if self.power_search is not None:
                raise ValueError("a fixed_set protocol has no power_search: check B does not apply without search")
            if self.null_check is None:
                raise ValueError("a fixed_set protocol needs null_check (check C)")
            for family in self.families:
                if family.mutation_operators:
                    raise ValueError(f"family {family.id}: mutation_operators must be empty in fixed_set mode")
                if family.windows is not None:
                    raise ValueError(f"family {family.id}: windows must be null in fixed_set mode")
            seeds = self.seed_expressions
            if len(set(seeds)) != len(seeds):
                raise ValueError("fixed_set seeds must be unique across families (canonical expressions)")
            if self.formula_budget != len(seeds):
                raise ValueError("fixed_set formula_budget must equal the number of seeds")
        else:
            if self.power_search is None:
                raise ValueError("a genetic protocol needs power_search (check B)")
            ...the existing power_search family/min_recovered checks move under this branch...
        return self

    @property
    def seed_expressions(self) -> tuple[str, ...]:
        return tuple(canonical_expression(seed) for family in self.families for seed in family.seeds)

    @property
    def requires_search_power(self) -> bool:
        return self.search_mode == "genetic"

    @property
    def gates(self) -> tuple[tuple[str, str], ...]:
        """(gate name, the ``check`` its manifest must record), by search mode."""
        if self.search_mode == "fixed_set":
            return (("power", "power_a"), ("null_check", "null_check"))
        return (("power", "power_a"), ("search_power", "search_power"), ("null_check", "null_check"))

    def require_campaign_ready(self) -> None:
        if self.null_check is None or (self.requires_search_power and (self.power_search is None or self.power_search.seed is None)):
            raise ValueError("this protocol predates checks B and C (campaign v1); a campaign needs protocol v2 or later")
```

`config/research/pooled/campaign-v4.json`: start from the v3 bytes; set `"version": 4`; title `"Pooled campaign v4: cohort v2, top-decile basket selection, predeclared fixed set of eight literature formulas, no search (checks A and C only), one-shot lane-wide confirmation"`; add `"search_mode": "fixed_set"`; `"formula_budget": 8`; delete the `power_search` block; replace `families` with the eight one-seed families of the spec table (each `"mutation_operators": []`, no `windows`, one `rationale` line naming the paper); keep `excluded_families`, `search`, `null_check` and everything else byte-for-byte where unchanged. Add a `rationale` at the top noting the literature-overlap exclusions (252-high and 21-day reversal).

- [ ] **Step 4: Run to verify pass**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/research/pooled/test_campaign_v4.py tests/research/pooled/test_campaign_protocol.py tests/research/pooled/test_campaign_v3.py tests/research/pooled/test_campaign_v2.py`
Expected: PASS.

- [ ] **Step 5: Regression gate, then commit**

```bash
git add agentic_trader/research/pooled/campaign.py config/research/pooled/campaign-v4.json tests/research/pooled/test_campaign_v4.py tests/research/pooled/test_campaign_protocol.py
git commit -m "Pooled protocol: fixed_set search mode with mode-derived gates; campaign v4 fixed set of eight literature formulas

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: The search never mutates in fixed-set mode; no-operator families stop cleanly

**Files:**
- Modify: `agentic_trader/research/alpha/search.py` (`mutate`, `ask`), `agentic_trader/research/pooled/genetic.py` (`family_search`)
- Test: `tests/research/pooled/test_genetic.py` (extend), `tests/research/alpha/test_search.py` or the existing search test module (extend)

**Interfaces:**
- Produces: `TypedGeneticSearch.ask()` raises `ValueError("no mutation operators")` once the seed queue is exhausted and `operators` is empty (instead of `IndexError` inside `mutate`); `family_search` records `stopped_short="search exhausted: no mutation operators"` (through the existing `except ValueError` path) and in fixed-set mode asserts it never reached `mutate` (a counter `search.mutation_count == 0`).

- [ ] **Step 1: Write the failing tests**

In `tests/research/pooled/test_genetic.py` (use the mini world's protocol builder; read the module for the helper that builds a `CampaignProtocol` and a `DiscoveryEvaluator`):

```python
def test_fixed_set_family_scores_only_its_seeds_and_never_mutates(mini_world):
    protocol = mini_world.protocol(
        search_mode="fixed_set",
        families=[
            {"id": "a", "seeds": ["-1.0 * roc(close, 5)"], "mutation_operators": []},
            {"id": "b", "seeds": ["volume / ts_mean(volume, 20)"], "mutation_operators": []},
        ],
        formula_budget=2,
    )
    outcome = search_campaign(protocol, mini_world.evaluator(), InMemoryLedger(), ...)
    charged = [r for r in outcome.records if r["status"] in ("evaluated", "error")]
    assert [r["expression"] for r in charged] == protocol.seed_expressions
    assert all(f.stopped_short is None for f in outcome.families)
    assert all(f.mutation_count == 0 for f in outcome.families)  # expose the search's counter on FamilyRun


def test_genetic_family_without_operators_stops_cleanly(mini_world):
    protocol = mini_world.protocol(
        families=[{"id": "a", "seeds": ["-1.0 * roc(close, 5)"], "mutation_operators": []}], formula_budget=3
    )
    outcome = search_campaign(protocol, mini_world.evaluator(), InMemoryLedger(), ...)
    [family] = outcome.families
    assert family.charged == 1
    assert family.stopped_short == "search exhausted: no mutation operators"
```

Adapt the helper names to the module (`search_campaign`, `FamilyRun`, the mini-world fixture), never the assertions. Add a unit test on `TypedGeneticSearch` directly: with `operators=()` and one seed, the first `ask()` returns the seed and the second raises `ValueError` matching "no mutation operators".

- [ ] **Step 2: Run to verify failure**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/research/pooled/test_genetic.py -k "fixed_set or without_operators"`
Expected: FAIL — `IndexError` from `rng.choice(self.operators)` or the fixture builder lacking `search_mode`.

- [ ] **Step 3: Implement**

`search.py` `ask()`: after the seed queue is exhausted, `if not self.operators and not self.crossover_enabled(): raise ValueError("no mutation operators")` — check whether crossover can produce new expressions without operators (read `crossover`); if crossover needs ≥2 parents and operators to be meaningful, treat "no operators" as exhausted. `mutate()`: guard `if not self.operators: raise ValueError("no mutation operators")` at the top of the loop's operator branch (never `rng.choice([])`). `genetic.py`: expose `mutation_count` on `FamilyRun` (from `search.mutation_count`). No fixed-set-specific branch is needed if the above holds; add a comment in `family_search` stating the fixed-set contract (seeds only, budget == seeds, so the loop ends before any mutation).

- [ ] **Step 4: Run to verify pass**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/research/pooled/test_genetic.py tests/research/alpha -k "search or genetic"`
Expected: PASS.

- [ ] **Step 5: Regression gate, then commit**

```bash
git add agentic_trader/research/alpha/search.py agentic_trader/research/pooled/genetic.py tests/
git commit -m "Pooled search: no-operator families stop cleanly; fixed-set runs score seeds only

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Gates by protocol mode in the runner and the CLI; checks accept or refuse by mode

**Files:**
- Modify: `agentic_trader/research/pooled/campaign_run.py` (`GATES` → `protocol.gates`; `execute_campaign`/`_run_campaign` accept `search_power` absent in fixed-set mode; manifest `gates`), `agentic_trader/cli/commands/alpha.py` (`campaign`: `--search-power` optional; `search-power`: refuse fixed-set), `agentic_trader/research/pooled/search_power.py` (refuse fixed-set with a clear message before any cube read)
- Test: `tests/cli/test_alpha_pooled_cli.py` (extend), `tests/research/pooled/test_campaign_run.py` (extend), `tests/research/pooled/test_search_power.py` (extend)

**Interfaces:**
- Consumes: `protocol.gates`, `protocol.requires_search_power` (Task 1).
- Produces: `campaign` CLI: `--search-power` required iff `protocol.requires_search_power`, refused otherwise ("fixed_set protocols take no search-power check"); `_run_campaign` requires `set(gates) == {name for name, _ in protocol.gates}`; manifest `gates` lists only the given gates; `search_power.run_search_power` raises `ValueError("check B does not apply to a fixed_set protocol")` before building anything.

- [ ] **Step 1: Write the failing tests**

CLI (`tests/cli/test_alpha_pooled_cli.py`, following the existing `test_campaign_hands_the_checked_gates_and_the_literature_entries_to_the_executor` pattern with monkeypatched `check_gate`/`execute_campaign`):

```python
def test_campaign_with_a_fixed_set_protocol_takes_two_gates(
    tmp_path, monkeypatch
): ...  # v4 path, --power + --null-check only → executor receives gates {"power","null_check"}
def test_campaign_refuses_search_power_for_a_fixed_set_protocol(
    tmp_path, monkeypatch
): ...  # v4 + --search-power → exit 1, "fixed_set protocols take no search-power check", executor not called
def test_campaign_requires_search_power_for_a_genetic_protocol(
    tmp_path, monkeypatch
): ...  # v3 without --search-power → exit 1, "genetic protocols require --search-power", executor not called
def test_search_power_refuses_a_fixed_set_protocol_before_any_cube_read(
    tmp_path, monkeypatch
): ...  # v4 → exit 1, message, cube builder not called
```

Runner (`tests/research/pooled/test_campaign_run.py`): `_run_campaign` with v4-shaped protocol and gates `{power, null_check}` passes the gate-set check; with gates `{power, search_power, null_check}` on v4 it raises ("checks A and C"); on v3 with two gates it raises ("checks A, B and C").

- [ ] **Step 2: Run to verify failure**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/cli/test_alpha_pooled_cli.py tests/research/pooled/test_campaign_run.py tests/research/pooled/test_search_power.py -k "fixed_set or two_gates or requires_search_power or refuses_a_fixed"`
Expected: FAIL (option required; `GATES` constant).

- [ ] **Step 3: Implement**

`campaign_run.py`: delete `GATES`; `execute_campaign(..., search_power_dir: Path | None, ...)` builds `gates` from `protocol.gates`; `_run_campaign`: `expected = {name for name, _ in protocol.gates}`; message "checks A and C must all be given" / "checks A, B and C must all be given" by mode; the one-cube check text likewise. `alpha.py` `campaign`: `--search-power` `required=False`; after loading the protocol: `if loaded.protocol.requires_search_power and search_dir is None: raise click.ClickException("genetic protocols require --search-power (check B)")`; `if not loaded.protocol.requires_search_power and search_dir is not None: raise click.ClickException("fixed_set protocols take no search-power check (check B does not apply)")`; build the `(name, check) → directory` mapping from `protocol.gates` (`power` → power_dir, `search_power` → search_dir, `null_check` → null_dir). `search_power.py`: at the top of the run function, `if protocol.search_mode == "fixed_set": raise ValueError("check B does not apply to a fixed_set protocol")`; the CLI surfaces it as a `ClickException`. `power.py` and `null_check.py` need no change (confirm by test that they accept v4).

- [ ] **Step 4: Run to verify pass**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/cli/test_alpha_pooled_cli.py tests/research/pooled`
Expected: PASS.

- [ ] **Step 5: Regression gate, then commit**

```bash
git add agentic_trader/research/pooled/campaign_run.py agentic_trader/research/pooled/search_power.py agentic_trader/cli/commands/alpha.py tests/
git commit -m "Pooled campaign: gates derived from the protocol mode; fixed_set protocols run with checks A and C only

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Checks A and C on a fixed-set protocol (mini world), docs, full suite

**Files:**
- Test: `tests/research/pooled/test_null_check.py` and `tests/research/pooled/test_power.py` (extend with a fixed-set protocol on the mini world)
- Modify docs: `docs/alpha-pooled-mining.md` (new "Fixed-set mode (campaign v4)" section), `docs/apriori-alphas.md` (cross-reference from the two literature entries), `docs/alpha-roadmap.md` (alpha-discovery sprint entry: W0, fixed set, decision rule, "campaign run needs operator approval"), `CLAUDE.md` pooled paragraph (one sentence)

- [ ] **Step 1: Tests**

Null check on a fixed-set mini protocol: each replicate's records contain exactly the seed expressions (no mutations), the manifest pins the v4-style hash and `check == "null_check"`, and the result has `false_acceptances` computed as before. Power on a fixed-set mini protocol: runs and writes a `power_a` manifest without touching `power_search` (assert `"power_search" not in manifest` or the protocol dump shows `None`).

- [ ] **Step 2: Docs**

`docs/alpha-pooled-mining.md` section: what fixed-set mode changes (no search, no B, gates by mode, budget == seeds), what it does not (cube, gates, consumption rule), the literature-overlap rule, the v4 set with one line per hypothesis, the run plan and the pre-registered decision rule. `docs/apriori-alphas.md`: after the two literature rows, one sentence pointing at v4 as their decile-basket retest with distinct formulas. `docs/alpha-roadmap.md`: a new subsection "Alpha-discovery sprint (October 7)" listing: production/alpha state summary (link to the state memory's facts as recorded in docs? — no: summarise in two sentences), W0 (PR number pending), the fixed-set protocol step with the decision rule, the deferred items (sparse layout, L3–L5). `CLAUDE.md`: append to the pooled paragraph: "Protocol v4 is a fixed-set protocol (eight predeclared literature formulas, decile selection, no search): it runs with checks A and C only; its campaign still consumes the one-use confirmation window and needs operator approval."

- [ ] **Step 3: Full suite and pre-commit**

```bash
env -u VIRTUAL_ENV uv run pytest -q
env -u VIRTUAL_ENV uv run pre-commit run --all-files
```

- [ ] **Step 4: Commit**

```bash
git add tests/ docs/ CLAUDE.md
git commit -m "docs/tests: pooled fixed-set mode (campaign v4), checks A and C on a fixed set

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### After the plan (controller, not a task)

PR → operator merge → controlled restart → run A then C on the cached cube at the clean merged revision (outside scan windows, thermal watch) → report → operator approval → campaign → results doc `docs/alpha-pooled-checks-v4-<date>.md` in the follow-up PR with the decision.
