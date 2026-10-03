# Pooled Alpha Mining — Protocol v3 (Top-Decile Selection) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the `top_fraction` selection rule (top decile, no hold-skipping), route every pick through one protocol-level `select`, freeze campaign protocol v3, and rerun checks A, B and C on the cached v2 cube.

**Architecture:** `formula.select_top_fraction` sits beside Part 1's `select_picks`. `CampaignProtocol` gains an optional `selection` (exactly one of `k` or `selection`) and a `select(scores, allowed, view)` method that every stage, check and overlap rule calls. Pick cells become sorted `int64` codes, so decile baskets stay small in memory. v1/v2 behaviour and every pinned digest are unchanged.

**Tech Stack:** Python 3.14, numpy, pydantic v2, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-03-pooled-alpha-mining-v3-design.md`

## Global Constraints

- Work only in the worktree `/Users/adamhadani/Development/agentic-trader-pooled-v3` (branch `research/pooled-mining-v3`).
- Never read, write, run or restart anything in `/Users/adamhadani/Development/agentic-trader`, except the controller's read-only `.envrc` sourcing for research commands.
- Run everything with `env -u VIRTUAL_ENV uv run …` from the worktree root.
- Before staging, run `ruff check`, `ruff format` and `mypy agentic_trader/research/pooled/` (pre-commit runs mypy).
- Research only. No registry, broker or Telegram change, no migration, no new dependency.
- Implementers stage with `git add` and report; the controller commits.
- Exact values (verbatim from the spec):
  - **Selection:** `selection: {"rule": "top_fraction", "fraction": 0.10}`. Picks are `m = ceil(0.10 × n)` per session, where n counts the eligible, finite, filtered names. Ties break by the existing hash key, and there is **no hold-skipping**.
  - **Descriptive figure:** the confirmation rows' descriptive top-k uses k = 3.
  - **Check B:** stays as frozen in v2: 10 seeds, δ 0.15R, Jaccard ≥ 0.5, ≥ 8 of 10.
  - **Check C:** stays as in v2: ≤ 2 of 40.
  - **Check A:** acceptance unchanged.
- Pins that must stay unchanged:
  - the v1 `run_stages` rounded digest `d2560f0a51efe09865501d843698c6e028e7a4505b5d546ac4da4777d7c11c29`;
  - the miner's search digest `5ef01834e977f3b8e2eb5781d74834470adbd96c5320fe562bae6f23fae8cbad`;
  - the `campaign-v1.json` and `campaign-v2.json` hashes;
  - power check A determinism.
- `campaign-v3.json` must have the same `cube_spec().identity` as v2, so the cached v2 cube is reused and no bars are downloaded.
- Provider reads (only the calendar call on a cube-cache hit) and every gate run are controller-only, never across the 10:35 or 14:35 New York scans. The thermal watch is armed during multi-worker runs.

## Review Focus

1. **v1/v2 drift.** Routing through `protocol.select` must not change top-3 picks or their order. Expected: the v1 pin and the power-A determinism tests still pass (Task 2 runs the whole pooled suite).
2. **Decile size off by one from float noise.** 0.1 × 30 is 3.0000000000000004. Expected: exactly 3 picks (Task 1 `test_top_fraction_count_is_exact_where_float_noise_would_round_up`).
3. **Overlap computed on different coordinate systems.** Expected: codes use `session × len(view.symbols) + symbol` everywhere, and Jaccard on codes equals Jaccard on sets (Task 1 `test_pick_codes_and_their_jaccard_match_the_set_versions`; Task 2 `test_literature_overlap_is_evaluated_under_the_campaign_rule`).
4. **The literature overlap still on top-3 under v3.** Expected: the literature formulas are evaluated with the campaign's rule (Task 2 test above).
5. **The descriptive top-3 leaking into the gate, or appearing in v1/v2 outputs.** Expected: it is present only under `top_fraction`, never in pass/fail logic, and the v1 pin is unchanged (Task 3 tests).

---

### Task 1: `select_top_fraction`, pick codes and code Jaccard

**Files:**
- Modify: `agentic_trader/research/pooled/formula.py`
- Test: `tests/research/pooled/test_formula.py`

**Interfaces:**
- Produces: `select_top_fraction(scores, allowed, view, fraction) -> Picks`; `Picks.codes(names: int) -> np.ndarray` (sorted `int64`); `jaccard_codes(a, b) -> float`.

- [ ] **Step 1: Write the failing tests (append to `tests/research/pooled/test_formula.py`)**

Add `Picks`, `jaccard_codes` and `select_top_fraction` to the file's `from agentic_trader.research.pooled.formula import (...)` list, then append:

```python
def test_top_fraction_picks_the_ceiling_share_of_each_session_without_hold_skipping():
    row = [0.1, 0.9, 0.5, 0.7, 0.3, 0.2, 0.8, 0.4, 0.6, 0.0, 1.0]  # 11 names: ceil(1.1) = 2 picks
    scores = np.array([row, row])
    picks = select_top_fraction(scores, np.ones_like(scores, bool), view(scores.shape, holding=5), 0.10)
    # The same two names again on the next session: a daily basket, no hold-skipping.
    assert list(zip(picks.session_idx.tolist(), picks.symbol_idx.tolist(), strict=True)) == [
        (0, 10),
        (0, 1),
        (1, 10),
        (1, 1),
    ]


def test_top_fraction_count_is_exact_where_float_noise_would_round_up():
    scores = np.arange(30, dtype=float)[None, :]  # 0.1 * 30 == 3.0000000000000004
    picks = select_top_fraction(scores, np.ones_like(scores, bool), view(scores.shape), 0.10)
    assert sorted(picks.symbol_idx.tolist()) == [27, 28, 29]


def test_top_fraction_counts_only_eligible_allowed_finite_names():
    scores = np.array([[5.0, 4.0, np.nan, 3.0, 2.0, 1.0, 0.5, 0.4, 0.3, 0.2, 0.1, 9.0]])
    v = view(scores.shape)
    v.eligible[0, 11] = False  # the top score is ineligible
    allowed = np.ones_like(scores, bool)
    allowed[0, 0] = False  # the next is filtered out
    picks = select_top_fraction(scores, allowed, v, 0.10)
    # Candidates are names 1, 3-10 (9 names): ceil(0.9) = 1 pick, the best of them.
    assert picks.symbol_idx.tolist() == [1]


def test_top_fraction_breaks_score_ties_by_the_hash_key():
    scores = np.ones((1, 10))
    tiebreak = np.array([[9, 3, 7, 1, 8, 2, 6, 0, 5, 4]], dtype=np.uint64)
    picks = select_top_fraction(scores, np.ones_like(scores, bool), view(scores.shape, tiebreak=tiebreak), 0.10)
    assert picks.symbol_idx.tolist() == [7]


def test_top_fraction_with_no_candidates_picks_nothing():
    scores = np.full((2, 5), np.nan)
    picks = select_top_fraction(scores, np.ones((2, 5), bool), view(scores.shape), 0.10)
    assert picks.session_idx.size == 0 and picks.symbol_idx.size == 0


def test_pick_codes_and_their_jaccard_match_the_set_versions():
    a = Picks(np.array([0, 0, 2, 5]), np.array([3, 1, 4, 0]))
    b = Picks(np.array([0, 2, 7]), np.array([1, 4, 2]))
    codes_a, codes_b = a.codes(10), b.codes(10)
    assert codes_a.tolist() == [1, 3, 24, 50] and codes_a.dtype == np.int64
    expected = len(a.cells() & b.cells()) / len(a.cells() | b.cells())
    assert jaccard_codes(codes_a, codes_b) == pytest.approx(expected) == pytest.approx(0.4)
    empty = np.zeros(0, dtype=np.int64)
    assert jaccard_codes(empty, empty) == 0.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_formula.py -q`
Expected: FAIL with `ImportError: cannot import name 'jaccard_codes'`.

- [ ] **Step 3: Implement in `formula.py`**

Add `import math` to the imports, and add `"jaccard_codes"` and `"select_top_fraction"` to `__all__`. In `Picks`, add below `cells`:

```python
    def codes(self, names: int) -> np.ndarray:
        """Each pick as one sorted int64 code (session * names + symbol), for compact overlap tests."""
        return np.sort(self.session_idx.astype(np.int64) * names + self.symbol_idx.astype(np.int64))
```

Add below `select_picks`:

```python
def select_top_fraction(scores: np.ndarray, allowed: np.ndarray, view: CubeView, fraction: float) -> Picks:
    """The top ceil(fraction * n) of each session's eligible, allowed, finite names; no hold-skipping.

    A daily basket, as in decile anomaly studies: a name may be picked on consecutive sessions,
    and its labels overlap exactly as the control's do. Ties break by the hash key.
    """
    rows: list[np.ndarray] = []
    cols: list[np.ndarray] = []
    for row in range(scores.shape[0]):
        candidates = np.flatnonzero(allowed[row] & view.eligible[row] & np.isfinite(scores[row]))
        if candidates.size == 0:
            continue
        # round() keeps float noise from rounding up: 0.1 * 30 is 3.0000000000000004.
        count = math.ceil(round(fraction * candidates.size, 9))
        order = np.lexsort((view.tiebreak[row, candidates], -scores[row, candidates]))
        chosen = candidates[order[:count]]
        rows.append(np.full(chosen.size, row, dtype=np.int64))
        cols.append(chosen.astype(np.int64))
    if not rows:
        return Picks(np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64))
    return Picks(np.concatenate(rows), np.concatenate(cols))


def jaccard_codes(a: np.ndarray, b: np.ndarray) -> float:
    """Jaccard overlap of two sorted, unique pick-code arrays (0 when both are empty)."""
    if a.size == 0 and b.size == 0:
        return 0.0
    common = np.intersect1d(a, b, assume_unique=True).size
    return common / (a.size + b.size - common)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_formula.py -q`
Expected: PASS.

- [ ] **Step 5: Stage**

```bash
git add agentic_trader/research/pooled/formula.py tests/research/pooled/test_formula.py
```

---

### Task 2: Protocol `selection`, one `select` everywhere, pick codes in the stages, campaign v3

**Files:**
- Modify: `agentic_trader/research/pooled/campaign.py`, `genetic.py`, `power.py`, `search_power.py`, `campaign_run.py`
- Create: `config/research/pooled/campaign-v3.json`
- Modify tests: `tests/research/pooled/mini_world.py`, `test_search_power.py`, `test_campaign_run.py`, `test_campaign_protocol.py`
- Create: `tests/research/pooled/test_campaign_v3.py`

**Interfaces:**
- Consumes: Task 1's `select_top_fraction`, `Picks.codes`, `jaccard_codes`.
- Produces:
  - `campaign.py`:
    - `Selection(rule, k=None, fraction=None)`;
    - `CampaignProtocol.k: int | None`, `CampaignProtocol.selection: Selection | None` (exactly one);
    - `CampaignProtocol.selection_rule -> Selection`;
    - `CampaignProtocol.select(scores, allowed, view) -> Picks`;
    - `DiscoveryScore(row, codes: np.ndarray)`, which replaces `cells`.
  - `genetic.SearchOutcome.codes` replaces `.cells`.
  - `campaign_run`:
    - `literature_codes(entries, view, book, protocol)` replaces `literature_cells`;
    - `already_tested(carried, codes, literature, threshold)`;
    - `frozen_document(expression, protocol) -> {"formula", "identity"}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/research/pooled/test_campaign_protocol.py`, adding `Selection` to its campaign import:

```python
@pytest.mark.parametrize(
    "selection",
    [
        {"rule": "top_k"},
        {"rule": "top_k", "k": 3, "fraction": 0.1},
        {"rule": "top_fraction"},
        {"rule": "top_fraction", "fraction": 1.5},
        {"rule": "top_fraction", "fraction": 0.1, "k": 3},
    ],
)
def test_a_selection_carries_exactly_its_own_parameter(selection):
    with pytest.raises(ValidationError):
        Selection(**selection)


def test_a_protocol_has_exactly_one_of_k_or_selection(tmp_path):
    def both(doc):
        doc["selection"] = {"rule": "top_fraction", "fraction": 0.1}

    def neither(doc):
        del doc["k"]

    for mutate in (both, neither):
        with pytest.raises(ValidationError, match="exactly one of k"):
            load_campaign_protocol(_mutated(tmp_path, mutate))


def test_v1_protocols_select_top_k_with_hold_skipping():
    protocol = load_campaign_protocol(PROTOCOL).protocol
    assert protocol.selection_rule == Selection(rule="top_k", k=3)
```

Create `tests/research/pooled/test_campaign_v3.py`:

```python
from pathlib import Path

import numpy as np

from agentic_trader.research.pooled.campaign import Selection, load_campaign_protocol
from agentic_trader.research.pooled.cube import CubeView


REPO = Path(__file__).resolve().parents[3]
V2 = load_campaign_protocol(REPO / "config/research/pooled/campaign-v2.json").protocol
V3 = load_campaign_protocol(REPO / "config/research/pooled/campaign-v3.json").protocol


def test_campaign_v3_changes_only_the_selection_from_v2():
    assert V3.version == 3 and V3.k is None
    assert V3.selection_rule == Selection(rule="top_fraction", fraction=0.10)
    keep = set(type(V3).model_fields) - {"version", "title", "k", "selection"}
    assert {key: getattr(V3, key) for key in keep} == {key: getattr(V2, key) for key in keep}
    V3.require_campaign_ready()
    assert V3.cube_spec().identity == V2.cube_spec().identity  # the cached v2 cube is reused


def _view(n_sessions: int, n_names: int) -> CubeView:
    shape = (n_sessions, n_names)
    return CubeView(
        sessions=tuple(range(n_sessions)),
        symbols=tuple(f"S{j}" for j in range(n_names)),
        offset=0,
        eligible=np.ones(shape, bool),
        labelled=np.ones(shape, bool),
        r_gross=np.zeros(shape),
        r_cost=np.zeros(shape),
        holding=np.full(shape, 3, np.int16),
        hit=np.ones(shape, np.int8),
        tiebreak=np.tile(np.arange(n_names, dtype=np.uint64), (n_sessions, 1)),
        dollar_volume=np.ones(shape),
    )


def test_select_dispatches_on_the_rule():
    view = _view(2, 20)
    scores = np.tile(np.arange(20, dtype=float), (2, 1))
    allowed = np.ones_like(scores, bool)
    top_k = V2.select(scores, allowed, view)  # top 3 with hold-skipping: new names on day 2
    decile = V3.select(scores, allowed, view)  # top 2 of 20 each day, the same names
    assert top_k.symbol_idx.tolist() == [19, 18, 17, 16, 15, 14]
    assert decile.symbol_idx.tolist() == [19, 18, 19, 18]
```

In `tests/research/pooled/mini_world.py`:
- Add `Selection` to the campaign import.
- Give `planted_cube` a protocol parameter:

```python
def planted_cube(expression: str, delta: float, seed: int = 1, protocol=V1) -> LabelCube:
    """Labels with ``delta`` R added to every cell ``expression`` picks (under ``protocol``) over the calendar."""
    cube = label_cube(seed)
    view = cube.window(cube.sessions[0], cube.sessions[-1])
    picks = protocol.select(*book().formula(expression).panel(view), view)
    return cube.with_shift(picks.session_idx, picks.symbol_idx, delta)
```

- Add:

```python
def mini_v3_protocol(**update):
    """The mini protocol under v3's top-decile selection (12 names: 2 picks per session)."""
    return mini_protocol(k=None, selection=Selection(rule="top_fraction", fraction=0.10), **update)
```

- Drop the `select_picks` import if it is no longer used.

In `tests/research/pooled/test_search_power.py`:
- `exact_seed` returns `expression, protocol.select(*book.formula(expression).panel(view), view)`.
- Append:

```python
def test_a_planted_decile_seed_is_recovered_under_v3(monkeypatch):
    monkeypatch.setattr(search_power, "hidden_expression", exact_seed)
    found = run_search_power(with_delta(mini_v3_protocol(), 2.0), label_cube(), book())
    assert found["status"] == "passed" and found["recovered"] == 3
    assert all(s["best_passing_jaccard"] == 1.0 for s in found["seeds"])
```

Import `mini_v3_protocol` from `mini_world`.

In `tests/research/pooled/test_campaign_run.py`:
- Replace the frozen-document assertion `frozen["formula"]["k"] == 3` with `frozen["formula"]["selection"] == {"rule": "top_k", "k": 3, "fraction": None}`.
- Change the literature test's call `campaign_run.literature_cells([entry], view, book)` to `campaign_run.literature_codes([entry], view, book, mini_protocol())` and assert the value is a sorted `int64` array.
- Append:

```python
async def test_a_planted_decile_edge_is_confirmed_under_v3(tmp_path, repository):
    protocol = mini_v3_protocol()
    result = await run(
        tmp_path, repository, planted_cube(PLANTED, 1.0, protocol=protocol), protocol=protocol, entries=[]
    )
    target = formula_id(PLANTED)
    assert result["status"] == "confirmed" and target in result["confirmed"]
    frozen = json.loads((tmp_path / "out" / "frozen" / f"{target}.json").read_text())
    assert frozen["formula"]["selection"] == {"rule": "top_fraction", "k": None, "fraction": 0.1}


def test_literature_overlap_is_evaluated_under_the_campaign_rule():
    protocol = mini_v3_protocol()
    cube = label_cube()
    view = cube.window(*protocol.windows.discovery)
    entry = SimpleNamespace(entry=SimpleNamespace(id="lit", formula=Formula(score=PLANTED, k=3)))
    literature = campaign_run.literature_codes([entry], view, book(), protocol)
    own = protocol.select(*book().formula(PLANTED).panel(view), view).codes(len(view.symbols))
    assert np.array_equal(literature["lit"], own)  # decile picks, not the entry's own top 3
    assert campaign_run.already_tested(["f"], {"f": own}, literature, protocol.dedupe_jaccard) == {"f": "lit"}
```

Import `mini_v3_protocol`, `label_cube`, `book` and `numpy as np` as needed. The `run` helper already accepts `protocol=` and `entries=`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled -q`
Expected: FAIL (`ImportError: cannot import name 'Selection'`; `campaign-v3.json` missing).

- [ ] **Step 3: Implement**

`campaign.py`:
- Import `select_top_fraction` and `jaccard_codes` from `formula`.
- Add before `CampaignProtocol`:

```python
class Selection(BaseModel, frozen=True, extra="forbid"):
    """How a formula's picks are chosen each session: top k with hold-skipping, or a top-fraction basket."""

    rule: Literal["top_k", "top_fraction"]
    k: int | None = Field(default=None, ge=1)
    fraction: float | None = Field(default=None, gt=0, lt=1)

    @model_validator(mode="after")
    def _one_parameter(self) -> Selection:
        if self.rule == "top_k" and (self.k is None or self.fraction is not None):
            raise ValueError("top_k needs k and no fraction")
        if self.rule == "top_fraction" and (self.fraction is None or self.k is not None):
            raise ValueError("top_fraction needs fraction and no k")
        return self
```

- In `CampaignProtocol`, change `k: int = Field(ge=1)` to `k: int | None = Field(default=None, ge=1)` and add `selection: Selection | None = None` right after it.
- In `_consistent`, add first:

```python
        if (self.k is None) == (self.selection is None):
            raise ValueError("a protocol has exactly one of k (top_k with hold-skipping) or selection")
```

- Add these methods after `cube_spec`:

```python
@property
def selection_rule(self) -> Selection:
    """The effective rule; a v1/v2 ``k`` means top_k with hold-skipping."""
    return self.selection if self.selection is not None else Selection(rule="top_k", k=self.k)


def select(self, scores: np.ndarray, allowed: np.ndarray, view: CubeView) -> Picks:
    """Every stage, check and overlap picks through this one rule."""
    rule = self.selection_rule
    if rule.rule == "top_fraction" and rule.fraction is not None:
        return select_top_fraction(scores, allowed, view, rule.fraction)
    if rule.rule == "top_k" and rule.k is not None:
        return select_picks(scores, allowed, view, rule.k)
    raise ValueError(f"unusable selection rule: {rule}")
```

- `_picks(formula, view, protocol)` becomes `scores, allowed = formula.panel(view); return protocol.select(scores, allowed, view)`. Update every caller to pass `protocol` / `self._protocol`, not `.k`.
- `DiscoveryScore`: replace the field `cells` with `codes: np.ndarray` (sorted int64 pick codes). `score()` returns `DiscoveryScore(row=row, codes=picks.codes(len(view.symbols)))`.
- `finish_discovery`: `codes = {score.row["formula_id"]: score.codes for score in scores}` and `jaccard_codes(codes[candidate["formula_id"]], codes[k["formula_id"]])`.
- Delete `_jaccard` once nothing references it.
- Add `"Selection"` to `__all__`.

`genetic.py`: rename `SearchOutcome.cells` to `codes`, returning `{score.row["formula_id"]: score.codes for score in self.scores}`.

`power.py`, in `_replicate`: `picks = protocol.select(scores, allowed, view)`.

`search_power.py`:
- In `hidden_expression`: `picks = protocol.select(*book.formula(vetted.expression).panel(view), view)`.
- In `run_search_power`: `planted = picks.codes(len(view.symbols))` and `overlaps = [(jaccard_codes(score.codes, planted), bool(score.row["passes"])) for score in run.scores]`.
- Import `jaccard_codes`; drop `_jaccard` and `select_picks` if they become unused.

`campaign_run.py`:
- Replace `literature_cells` with:

```python
def literature_codes(
    entries: Iterable, view: CubeView, book: ScoreBook, protocol: CampaignProtocol
) -> dict[str, np.ndarray]:
    """Each literature formula's discovery picks under the campaign's own selection rule, as pick codes."""
    stop = view.offset + len(view.sessions)
    codes: dict[str, np.ndarray] = {}
    for loaded in entries:
        formula: Formula = loaded.entry.formula
        scores = book.panel(formula.score)[view.offset : stop]
        filters = [book.panel(spec.expression)[view.offset : stop] for spec in formula.filters]
        allowed = allowed_mask(formula, filters, view.eligible)
        codes[loaded.entry.id] = protocol.select(scores, allowed, view).codes(len(view.symbols))
    return codes
```

- `already_tested(carried, codes, literature, threshold)` uses `jaccard_codes(codes[fid], entry_codes)`.
- Callers pass `search.codes` and `literature_codes(..., protocol)`. Update `__all__`.
- Add:

```python
def frozen_document(expression: str, protocol: CampaignProtocol) -> dict:
    """A frozen candidate: its score and the selection rule it was tested under, with a stable identity."""
    body = {"score": expression, "filters": [], "selection": protocol.selection_rule.model_dump(mode="json")}
    identity = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"formula": body, "identity": identity}
```

- `_write_frozen` uses it instead of `Formula(score=..., k=protocol.k)`. Each document keeps `formula_id`, `family`, `expression`, `already_tested_by` and `authorizes_promotion`, and takes `formula` and `identity` from `frozen_document`. Add `import hashlib` if missing.

`config/research/pooled/campaign-v3.json` — generate it (run from the worktree root):

```bash
env -u VIRTUAL_ENV uv run python - <<'EOF'
import json
from pathlib import Path

doc = json.loads(Path("config/research/pooled/campaign-v2.json").read_text())
new = {}
for key, value in doc.items():
    if key == "k":
        new["selection"] = {"rule": "top_fraction", "fraction": 0.1}
        continue
    new[key] = value
new["version"] = 3
new["title"] = (
    "Pooled campaign v3: cohort v2, top-decile basket selection, budgeted per-family genetic search over 12 "
    "OHLCV families, checks A/B/C, one-shot lane-wide confirmation"
)
Path("config/research/pooled/campaign-v3.json").write_text(json.dumps(new, indent=2) + "\n")
EOF
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled tests/cli/test_alpha_pooled_cli.py -q`
Expected: PASS, including the unchanged v1 `run_stages` pin (`d2560f0a…`), the v1/v2 hash tests and power check A's determinism tests.

- [ ] **Step 5: Stage**

```bash
git add agentic_trader/research/pooled/campaign.py agentic_trader/research/pooled/genetic.py agentic_trader/research/pooled/power.py agentic_trader/research/pooled/search_power.py agentic_trader/research/pooled/campaign_run.py config/research/pooled/campaign-v3.json tests/research/pooled/mini_world.py tests/research/pooled/test_search_power.py tests/research/pooled/test_campaign_run.py tests/research/pooled/test_campaign_protocol.py tests/research/pooled/test_campaign_v3.py
```

---

### Task 3: Descriptive top-3 edge in confirmation rows (top_fraction only)

**Files:**
- Modify: `agentic_trader/research/pooled/campaign.py`
- Test: `tests/research/pooled/test_campaign_stages.py`

**Interfaces:**
- Produces: `DESCRIPTIVE_TOP_K = 3`. Under `top_fraction`, each confirmation row gains `"top3_edge"`, the `paired_edge_test` of the top-3 hold-skipping picks on the confirmation window. It is never used in pass/fail. Under `top_k`, rows are unchanged.

- [ ] **Step 1: Write the failing tests (append to `tests/research/pooled/test_campaign_stages.py`)**

Add `Selection` to the campaign import, then append:

```python
def test_top_fraction_confirmation_rows_report_a_descriptive_top3_edge_that_never_gates():
    v3 = P1.model_copy(update={"k": None, "selection": Selection(rule="top_fraction", fraction=0.10)})
    cube = confirmation_cube()
    windows = windows_of(cube)
    rows, confirmed = _confirmation(
        [favourite("f", 0)], ["f"], windows.confirmation(InMemoryLedger(), campaign_id="x", candidates=()), v3
    )
    row = rows[0]
    assert set(row["top3_edge"]) >= {"mean", "n_sessions", "p_one_sided"}
    assert row["passes"] and confirmed == ["f"]


def test_top_k_confirmation_rows_have_no_descriptive_top3_edge():
    rows, _ = confirm(confirmation_cube(), [favourite("f", 0)], ["f"])
    assert "top3_edge" not in rows["f"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_campaign_stages.py -q`
Expected: FAIL with `KeyError: 'top3_edge'`.

- [ ] **Step 3: Implement in `_confirmation`**

Add `DESCRIPTIVE_TOP_K = 3  # the descriptive top-k reported beside a top-fraction basket (never a gate)` near the top of `campaign.py`. In `_confirmation`, compute each candidate's panel once and select through the protocol:

```python
    for formula_id in frozen:
        scores, allowed = by_id[formula_id].panel(view)
        picks = protocol.select(scores, allowed, view)
        table = session_table(picks, view, purge=False)
        recent = table.edge[recent_lo:]
        recent = recent[np.isfinite(recent)]
        results[formula_id] = {
            "edge": paired_edge_test(table, draws),
            "leg": leg_mean_test(table, draws),
            "trimmed_mean": trimmed_mean(table.pick_rows["r_cost"].to_numpy(float), gate.trim_fraction),
            "recent_edge": float(recent.mean()) if recent.size else float("nan"),
        }
        if protocol.selection_rule.rule == "top_fraction":
            # Descriptive only: live cards trade the top names of a confirmed basket.
            top = session_table(select_picks(scores, allowed, view, DESCRIPTIVE_TOP_K), view, purge=False)
            results[formula_id]["top3_edge"] = paired_edge_test(top, draws)
```

The pass/fail expression is unchanged.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled -q`
Expected: PASS. The v1 pin is unchanged, because `top_k` rows are untouched.

- [ ] **Step 5: Stage**

```bash
git add agentic_trader/research/pooled/campaign.py tests/research/pooled/test_campaign_stages.py
```

---

### Task 4: Documentation

**Files:**
- Modify: `docs/alpha-pooled-mining.md`, `docs/alpha-roadmap.md`, `CLAUDE.md`

- [ ] **Step 1: `docs/alpha-pooled-mining.md`.** Add a **Selection rules** subsection under "Formulas and picks", covering:
  - `top_k` (Part 1: k = 3, hold-skipping; protocols v1/v2 and the literature entries);
  - `top_fraction` (v3: `ceil(0.10 × n)` per session, ties by hash, no hold-skipping, a daily decile basket with overlapping labels like the control);
  - the protocol's `select` as the single entry point, and pick codes for overlap.

  Also document:
  - campaign protocol v3 (selection only; it reuses the v2 cube);
  - the literature-overlap rule evaluated under the campaign's rule;
  - frozen documents recording the selection;
  - the descriptive `top3_edge` (never a gate);
  - the v3 caveats from the spec: the tested claim, factor exposure, serial dependence.
- [ ] **Step 2: `docs/alpha-roadmap.md`.** After the Part 1b results bullet, add: "Protocol v3 (top-decile selection, [spec](superpowers/specs/2026-10-03-pooled-alpha-mining-v3-design.md)) reruns checks A, B and C on the v2 cube; if B fails again the pooled genetic campaign is parked." Leave room for the results.
- [ ] **Step 3: `CLAUDE.md`.** In the pooled paragraph, replace "protocol v3 needs a redesign first." with "protocol v3 selects the top decile of eligible names per session (no hold-skipping); checks A, B and C rerun on it before any campaign."
- [ ] **Step 4: Check and stage**

Run: `env -u VIRTUAL_ENV uv run pre-commit run --files docs/alpha-pooled-mining.md docs/alpha-roadmap.md CLAUDE.md`

```bash
git add docs/alpha-pooled-mining.md docs/alpha-roadmap.md CLAUDE.md
```

---

### Task 5 (controller): Verification, the gate runs and the decision

- [ ] **Step 1:** Run the full test suite, the PostgreSQL integration suite (`TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_agentic_trader_codex_20260915`) and `pre-commit run --all-files`. Then run the whole-branch final review and its single fix wave. After that, the tree must be clean and nothing else is committed until the gates finish.
- [ ] **Step 2: Check A on v3**, in the background with the thermal watch, outside the scan windows. Expect a cube-cache hit (`bar files re-checked: N unchanged`) and no bar downloads.

```bash
cd /Users/adamhadani/Development/agentic-trader-pooled-v3 && (set -a; source /Users/adamhadani/Development/agentic-trader/.envrc >/dev/null 2>&1; set +a; nohup env -u VIRTUAL_ENV uv run copilot alpha pooled power config/research/pooled/campaign-v3.json --output ~/agentic-trader-research/pooled-power-a-v3-$(date +%Y%m%d) --cache ~/agentic-trader-research/pooled-cache-v1 --workers 6 > ~/agentic-trader-research/logs/pooled-power-a-v3-$(date +%Y%m%d).log 2>&1 &)
```

- [ ] **Step 3: Checks B and C**, in the background, both pointing at the step-2 output with `--power`:

```bash
cd /Users/adamhadani/Development/agentic-trader-pooled-v3 && (set -a; source /Users/adamhadani/Development/agentic-trader/.envrc >/dev/null 2>&1; set +a; nohup env -u VIRTUAL_ENV uv run copilot alpha pooled search-power config/research/pooled/campaign-v3.json --power ~/agentic-trader-research/pooled-power-a-v3-YYYYMMDD --output ~/agentic-trader-research/pooled-search-power-v3-$(date +%Y%m%d) --cache ~/agentic-trader-research/pooled-cache-v1 > ~/agentic-trader-research/logs/pooled-search-power-v3-$(date +%Y%m%d).log 2>&1 &)
cd /Users/adamhadani/Development/agentic-trader-pooled-v3 && (set -a; source /Users/adamhadani/Development/agentic-trader/.envrc >/dev/null 2>&1; set +a; nohup env -u VIRTUAL_ENV uv run copilot alpha pooled null-check config/research/pooled/campaign-v3.json --power ~/agentic-trader-research/pooled-power-a-v3-YYYYMMDD --output ~/agentic-trader-research/pooled-null-check-v3-$(date +%Y%m%d) --cache ~/agentic-trader-research/pooled-cache-v1 --workers 6 > ~/agentic-trader-research/logs/pooled-null-check-v3-$(date +%Y%m%d).log 2>&1 &)
```

- [ ] **Step 4: Decide, as pre-registered.**
  - **If any gate fails:** write `docs/alpha-pooled-checks-v3-<date>.md`. If B failed, record that the pooled genetic campaign is **parked** (roadmap and CLAUDE.md), then open a PR with the machinery and results, and ask the operator to merge.
  - **If A, B and C pass:** report them (journal scope `production/alpaca:paper`, B and C `errors_total` both 0, C's seed-t standard deviation) and **ask the operator for explicit approval** before the campaign. On approval, run the campaign in the background (it must not be interrupted):

```bash
cd /Users/adamhadani/Development/agentic-trader-pooled-v3 && (set -a; source /Users/adamhadani/Development/agentic-trader/.envrc >/dev/null 2>&1; set +a; nohup env -u VIRTUAL_ENV uv run copilot alpha pooled campaign config/research/pooled/campaign-v3.json --power ~/agentic-trader-research/pooled-power-a-v3-YYYYMMDD --search-power ~/agentic-trader-research/pooled-search-power-v3-YYYYMMDD --null-check ~/agentic-trader-research/pooled-null-check-v3-YYYYMMDD --journal-scope production/alpaca:paper --output ~/agentic-trader-research/pooled-campaign-v3-$(date +%Y%m%d) --cache ~/agentic-trader-research/pooled-cache-v1 > ~/agentic-trader-research/logs/pooled-campaign-v3-$(date +%Y%m%d).log 2>&1 &)
```

  Then write the result docs, the roadmap and CLAUDE.md lines and memory notes, open the PR, and ask the operator to merge.
