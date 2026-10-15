# Pooled cohort v3 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new frozen cohort v3 (the mid-liquidity band, ranks 401–1,400 by 60-session median dollar volume) with an absolute per-session dollar-volume eligibility floor, and the three existing fixed-set protocols re-frozen against it (events, daily, intraday), so checks A and C and operator-approved campaigns can run on a different population.

**Architecture:** `ScreenRule` gains `rank_from` (band selection) and allows an empty `config_groups`; `cohort_document` carries only the screen source when there are no config groups. `UniverseSpec` gains `min_median_dollar_volume` as an alternative to `static_percentile` (exactly one); `point_in_time_eligibility` applies the absolute floor to the same 20-session median raw dollar volume at D−1 and skips the static reference; the cube coverage records which rule applied. Frozen files: `screen-v3.json`, then (after the controller's real screen and corporate-actions runs) `cohort-v3.json` and three protocol files byte-identical to their parents except cohort, universe, cost and prose. All existing identities (v1–v4, intraday-v1, events-v1, screen-v2, cube spec `f1c835a3…`) are unchanged.

**Tech Stack:** Python 3.14, `uv`, pandas, numpy, pydantic v2 (frozen), click, pytest (network blocked).

**Spec:** `docs/superpowers/specs/2026-10-15-pooled-cohort-v3-design.md`

## Global Constraints

- Run everything with `env -u VIRTUAL_ENV uv run …` from the worktree `/Users/adamhadani/Development/agentic-trader-cohort3`; never touch `/Users/adamhadani/Development/agentic-trader` or `/Users/adamhadani/Development/agentic-trader-intraday`.
- ruff strict (C408, PLC0415, RUF059, A002, UP038, RUF046, TRY004; sorted `__all__`); mypy clean on touched modules; pristine tests. `env -u VIRTUAL_ENV uv run pre-commit run --all-files` before each commit (ruff-format reflows fenced Markdown code; re-add).
- **Identities unchanged:** `config/research/pooled/screen-v2.json` SHA-256 (test loads it; add a pin of its first 8 hex digits computed once at implementation time from the file on disk and assert it), `campaign-v4.json` (`a72f2e65`), `intraday-v1.json` (`5b4c3fdf`), `events-v1.json` (`4e156fd8`), cube spec identity `f1c835a3d3364d2b` for all three. New optional fields must be excluded from dumps when unset.
- Research only: no registry, broker, Telegram; no change to labels, bracket, windows, gates, bootstrap, stage logic, formulas, or the live dynamic-universe gate; no runtime change.
- Exact values from the spec: snapshot id `00a9a363b02a6f4930489c72e275ed9bcf2b7bf4571a327fca9a496cd7d22fac`, snapshot SHA-256 `725a23d735d1dcc4ceb9950a74ce407a06594e35d75dc2711067d60ada63c9cb`, observed `2026-10-15`, classification `listed_non_etf_equity_candidate`; `rank_from` 401, `top` 1400; `window_end` 2023-12-29, `window_sessions` 60, `min_sessions_with_bars` 55, `min_price` 10.0; universe `{"min_price": 10.0, "min_median_dollar_volume": 5000000.0, "min_eligible_names": 30}`; `decision_cost_bps` 15.0; cohort-v3 SHA-256 and the cohort-v3 corporate-actions `content_sha256` are supplied by the controller in Task 3's dispatch (they come from real runs after Task 1).
- Implementers STAGE and report; never commit; never dispatch subagents.

## Review Focus

1. A rule with `rank_from` > `top` → `ValidationError`, never an empty cohort written silently (Task 1 `test_rank_from_cannot_exceed_top`).
2. A `UniverseSpec` with both or neither of `static_percentile`/`min_median_dollar_volume` → `ValidationError` (Task 2 `test_universe_spec_needs_exactly_one_floor`).
3. Absolute-floor eligibility never reads the static reference: a session whose static set is empty is NOT skipped with `no_reference` (Task 2 `test_absolute_floor_ignores_the_static_reference`).
4. The boundary: median dollar volume exactly equal to the floor is eligible (`volume < floor` is the refusal), mirroring the live `dollar_volume_threshold` semantics (Task 2 `test_absolute_floor_boundary_is_inclusive`).
5. Each v3 protocol equals its parent on every key except the declared set (Task 3 `test_cohort3_protocols_equal_their_parents_except_the_declared_keys`).

---

### Task 1: Screen band and empty config groups; frozen `screen-v3.json`

**Files:**
- Modify: `agentic_trader/research/pooled/screen.py` (`ScreenRule` line 76; `screen_table` line 196; `cohort_document` line 246), `agentic_trader/cli/commands/alpha.py` (the screen command docstring "Cohort v2's liquidity screen" → "The pooled lane's liquidity screen (cohort v2 top cut, cohort v3 band)")
- Create: `config/research/pooled/screen-v3.json`
- Test: `tests/research/pooled/test_screen.py` (append)

**Interfaces:**
- Produces: `ScreenRule.rank_from: int = Field(default=1, ge=1)` (excluded from `model_dump` when 1); `ScreenRule.config_groups: tuple[str, ...]` (no `min_length`); `screen_table` selects `rank_from <= rank <= top`; `cohort_document` emits only the `liquidity_screen` source when `config_groups` is empty and records `rank_from` in the screen provenance when it is not 1.

- [ ] **Step 1: Write the failing tests** (append to `tests/research/pooled/test_screen.py`; it already has `REPO`, `LOADED`, `RULE`, `SMALL`, `load_screen_rule`, `cohort_document`, `screen_table` and the fixtures used by `test_screen_table_ranks_by_median_dollar_volume_and_breaks_ties_by_symbol` — reuse them):

```python
import hashlib as _hashlib

SCREEN_V2_SHA8 = "REPLACE"  # first 8 hex of sha256(config/research/pooled/screen-v2.json) on main d6fd22e; compute once with shasum and paste
V3 = REPO / "config/research/pooled/screen-v3.json"


def test_screen_v2_hash_and_dump_are_unchanged_by_the_band_fields():
    assert LOADED.sha256.startswith(SCREEN_V2_SHA8)
    assert "rank_from" not in RULE.model_dump(mode="json")
    assert RULE.rank_from == 1


def test_screen_v3_is_the_band_401_to_1400_on_the_2026_10_15_snapshot_without_config_groups():
    loaded = load_screen_rule(V3)
    rule = loaded.rule
    assert rule.cohort_version == 3 and rule.version == 3
    assert rule.rank_from == 401 and rule.top == 1400
    assert rule.config_groups == ()
    assert rule.snapshot.snapshot_id == "00a9a363b02a6f4930489c72e275ed9bcf2b7bf4571a327fca9a496cd7d22fac"
    assert rule.snapshot.sha256 == "725a23d735d1dcc4ceb9950a74ce407a06594e35d75dc2711067d60ada63c9cb"
    assert rule.snapshot.observed.isoformat() == "2026-10-15"
    assert rule.snapshot.classification == RULE.snapshot.classification
    for name in (
        "window_end",
        "window_sessions",
        "min_sessions_with_bars",
        "min_price",
        "feed",
        "adjustment",
        "excluded_fifth_letters",
        "excluded_name_patterns",
    ):
        assert getattr(rule, name) == getattr(RULE, name), name
    assert rule.model_dump(mode="json")["rank_from"] == 401


def test_rank_from_cannot_exceed_top():
    document = {**RULE.model_dump(mode="json"), "rank_from": 500, "top": 400}
    with pytest.raises(ValidationError, match="rank_from"):
        ScreenRule.model_validate(document)


def test_screen_table_selects_the_band_only():
    # Reuse the three-name table fixture of test_screen_table_ranks_by_median_dollar_volume_and_breaks_ties_by_symbol
    rule = RULE.model_copy(update={"rank_from": 2, "top": 3})
    table = _three_name_table(rule)  # the helper that test builds inline; factor it into a module function first
    assert list(table.sort_values("rank")["selected"]) == [False, True, True]


def test_cohort_document_without_config_groups_has_only_the_screen_source():
    rule = SMALL.model_copy(update={"config_groups": (), "rank_from": 2})
    loaded = LoadedScreenRule(rule=rule, sha256="a" * 64, path=Path("screen-v3.json"))
    table = ...  # the same table test_cohort_document_unions_the_scan_equities_and_the_screened_names builds
    document = cohort_document(
        table,
        loaded,
        rule_path="config/research/pooled/screen-v3.json",
        config_symbols=[],
        screen_sha256="b" * 64,
        snapshot_sha256=rule.snapshot.sha256,
    )
    assert [source["kind"] for source in document["sources"]] == ["liquidity_screen"]
    assert document["sources"][0]["screen"]["rank_from"] == 2
    assert document["symbols"] == sorted(document["sources"][0]["symbols"])
    Cohort.model_validate(document)  # the union validator accepts a single source
```

Replace the `...` and `_three_name_table` placeholders by factoring the existing inline fixtures of the two named tests into module-level helpers (do not duplicate the fixture bodies). Compute `SCREEN_V2_SHA8` with `shasum -a 256 config/research/pooled/screen-v2.json | cut -c1-8` and paste it.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_screen.py -q`
Expected: FAIL (`screen-v3.json` missing; `rank_from` unknown field).

- [ ] **Step 3: Implement**

`ScreenRule`:

```python
config_groups: tuple[str, ...] = ()
...
top: int = Field(ge=1)
# Band selection (cohort v3): ranks rank_from..top inclusive. Excluded from the dump when 1 so
# screen-v2.json's hash is unchanged.
rank_from: int = Field(default=1, ge=1)


@model_validator(mode="after")
def _window(self) -> ScreenRule:
    if self.min_sessions_with_bars > self.window_sessions:
        raise ValueError("min_sessions_with_bars cannot exceed window_sessions")
    if self.rank_from > self.top:
        raise ValueError("rank_from cannot exceed top")
    return self


@model_serializer(mode="wrap")
def _dump(self, handler):
    data = handler(self)
    if self.rank_from == 1:
        data.pop("rank_from", None)
    return data
```

(Check pydantic's `model_serializer` import; if a wrap serializer interferes with `model_dump(mode="json")` key order, fall back to `Field(default=1, ge=1, exclude=True)` on the base plus an explicit `"rank_from"` key written only in `cohort_document`'s provenance — but then the frozen file's `rank_from` must still validate, which `exclude=True` allows. Prefer the serializer; document the choice in the report.)

`screen_table`: `table["selected"] = (table["rank"] >= rule.rank_from) & (table["rank"] <= rule.top)`.

`cohort_document`: build `sources` as a list; append the `config_groups` source only when `rule.config_groups` is non-empty (then `configured` is `[]` and `everything` is the screened names); add `"rank_from": rule.rank_from` to the `screen` provenance dict only when `rule.rank_from != 1` (keeps the v2 document's shape); update the docstring ("the scan-universe equities, if the rule names config groups, plus the ranked names in the rule's band").

`config/research/pooled/screen-v3.json` — copy `screen-v2.json`, then set: `"version": 3`, `"cohort_version": 3`, `"title": "Pooled cohort v3 screen: the mid-liquidity band, ranks 401-1400 by SIP median raw dollar volume over the 60 sessions ending 2023-12-29, no config-group source"`, `"survivorship": "listed and active on 2026-10-15; ranked on SIP raw dollar volume of the 60 sessions ending 2023-12-29; not point-in-time; delisted names are absent (a stronger bias for smaller names)"`, `"snapshot": {"snapshot_id": "00a9a363b02a6f4930489c72e275ed9bcf2b7bf4571a327fca9a496cd7d22fac", "sha256": "725a23d735d1dcc4ceb9950a74ce407a06594e35d75dc2711067d60ada63c9cb", "observed": "2026-10-15", "classification": "listed_non_etf_equity_candidate"}`, `"config_groups": []`, `"rank_from": 401`, `"top": 1400`. Everything else identical to v2. Same formatting style as v2 (inspect; two-space indent).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_screen.py tests/research/pooled/test_screen_executor.py tests/research/pooled/test_cohort.py -q`
Expected: PASS.

- [ ] **Step 5: Lint, type-check, stage**

Run: `env -u VIRTUAL_ENV uv run ruff check agentic_trader tests && env -u VIRTUAL_ENV uv run ruff format --check agentic_trader tests && env -u VIRTUAL_ENV uv run mypy agentic_trader/research/pooled/screen.py`
Stage and report.

---

### Task 2: `UniverseSpec.min_median_dollar_volume` — the absolute eligibility floor

**Files:**
- Modify: `agentic_trader/research/pooled/cohort.py` (`UniverseSpec` line 117; `point_in_time_eligibility` line 170), `agentic_trader/research/pooled/cube.py` (coverage dict ~line 305)
- Test: `tests/research/pooled/test_eligibility.py` (append), `tests/research/pooled/test_campaign_protocol.py` (append one identity test)

**Interfaces:**
- Produces:
  ```python
  class UniverseSpec(BaseModel, frozen=True, extra="forbid"):
      min_price: float = Field(gt=0)
      static_percentile: float | None = Field(default=None, gt=0, lt=1)
      min_median_dollar_volume: float | None = Field(default=None, gt=0)
      min_eligible_names: int = Field(ge=1)

      # validator: exactly one of static_percentile / min_median_dollar_volume
      # serializer: drop whichever is None (so v1-v4/intraday/events dumps and cube identities are unchanged)
      @property
      def floor_rule(self) -> str: ...  # "static_percentile" | "absolute"
  ```
  `point_in_time_eligibility`: when `universe.min_median_dollar_volume` is set, `ref.value` is the floor for every session, `reference` records the floor, `reference_names` records 0, the static slicers are not consulted, and no `no_reference` skip can occur. Cube coverage gains `"eligibility_rule": universe.floor_rule` and `"dollar_volume_floor": universe.min_median_dollar_volume` (None for the percentile rule) — add both to the `coverage` dict in `build_cube`; `CubeSpec` passes `universe` already.

- [ ] **Step 1: Write the failing tests** (append to `tests/research/pooled/test_eligibility.py`; `days`, `daily`, `reference`, `UNIVERSE` exist):

```python
ABSOLUTE = UniverseSpec(min_price=10.0, min_median_dollar_volume=1.5e7, min_eligible_names=1)


def test_universe_spec_needs_exactly_one_floor():
    with pytest.raises(ValidationError):
        UniverseSpec(min_price=10.0, min_eligible_names=1)
    with pytest.raises(ValidationError):
        UniverseSpec(min_price=10.0, static_percentile=0.25, min_median_dollar_volume=1e7, min_eligible_names=1)
    assert UNIVERSE.floor_rule == "static_percentile" and ABSOLUTE.floor_rule == "absolute"


def test_universe_dump_omits_the_unused_floor():
    assert "min_median_dollar_volume" not in UNIVERSE.model_dump(mode="json")
    assert "static_percentile" not in ABSOLUTE.model_dump(mode="json")
    assert ABSOLUTE.model_dump(mode="json") == {
        "min_price": 10.0,
        "min_median_dollar_volume": 1.5e7,
        "min_eligible_names": 1,
    }


def test_absolute_floor_ignores_the_static_reference():
    sessions = days(60)
    frame = daily(sessions, 20.0, volume=1e6)  # $2e7 median dollar volume
    result = point_in_time_eligibility(
        sessions,
        (sessions[30], sessions[-1]),
        ["AAA"],
        {"AAA": frame},
        {"AAA": frame},
        {},  # no static names at all
        universe=ABSOLUTE,
        atr_window=14,
    )
    assert result.skipped_sessions == {}
    assert result.eligible.all()
    assert set(result.reference) == {1.5e7} and set(result.reference_names) == {0}


def test_absolute_floor_boundary_is_inclusive():
    sessions = days(60)
    exactly = daily(sessions, 15.0, volume=1e6)  # $1.5e7 == floor → eligible
    below = daily(sessions, 14.0, volume=1e6)  # $1.4e7 < floor → illiquid_volume
    result = point_in_time_eligibility(
        sessions,
        (sessions[30], sessions[-1]),
        ["EQ", "LO"],
        {"EQ": exactly, "LO": below},
        {"EQ": exactly, "LO": below},
        {},
        universe=ABSOLUTE,
        atr_window=14,
    )
    assert result.eligible[:, 0].all() and not result.eligible[:, 1].any()
    assert result.reasons["illiquid_volume"] == result.eligible.shape[0]


def test_percentile_rule_is_unchanged():
    sessions = days(60)
    frame = daily(sessions, 20.0, volume=1e6)
    result = point_in_time_eligibility(
        sessions,
        (sessions[30], sessions[-1]),
        ["AAA"],
        {"AAA": frame},
        {"AAA": frame},
        reference(sessions),
        universe=UNIVERSE,
        atr_window=14,
    )
    assert result.eligible.all() and set(result.reference_names) == {20}
```

Append to `tests/research/pooled/test_campaign_protocol.py`:

```python
def test_existing_protocol_identities_survive_the_universe_floor_field():
    for path, sha8 in (
        ("campaign-v4.json", "a72f2e65"),
        ("intraday-v1.json", "5b4c3fdf"),
        ("events-v1.json", "4e156fd8"),
    ):
        loaded = load_campaign_protocol(Path("config/research/pooled") / path)
        assert loaded.sha256.startswith(sha8)
        assert loaded.protocol.cube_spec().identity.startswith("f1c835a3d3364d2b"), path
        assert "min_median_dollar_volume" not in loaded.protocol.model_dump(mode="json")["universe"]
```

Also add to `tests/research/pooled/test_cube.py` (find the existing `build_cube` test that inspects `coverage`) an assertion that `coverage["eligibility_rule"] == "static_percentile"` and `coverage["dollar_volume_floor"] is None` for the existing fixtures.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_eligibility.py tests/research/pooled/test_campaign_protocol.py tests/research/pooled/test_cube.py -q`
Expected: FAIL (`min_median_dollar_volume` unknown; `floor_rule` missing).

- [ ] **Step 3: Implement**

`UniverseSpec`:

```python
class UniverseSpec(BaseModel, frozen=True, extra="forbid"):
    """Per-session eligibility: a price floor and ONE dollar-volume floor -- the static reference
    set's percentile (v1-v4, intraday-v1, events-v1) or an absolute 20-session median raw dollar
    volume in dollars (cohort v3, whose names sit below the mega-cap reference)."""

    min_price: float = Field(gt=0)
    static_percentile: float | None = Field(default=None, gt=0, lt=1)
    min_median_dollar_volume: float | None = Field(default=None, gt=0)
    min_eligible_names: int = Field(ge=1)

    @model_validator(mode="after")
    def _one_floor(self) -> UniverseSpec:
        if (self.static_percentile is None) == (self.min_median_dollar_volume is None):
            raise ValueError("exactly one of static_percentile and min_median_dollar_volume must be set")
        return self

    @model_serializer(mode="wrap")
    def _dump(self, handler):
        data = handler(self)
        return {
            k: v for k, v in data.items() if v is not None or k not in ("static_percentile", "min_median_dollar_volume")
        }

    @property
    def floor_rule(self) -> str:
        return "absolute" if self.min_median_dollar_volume is not None else "static_percentile"
```

Verify that the serializer keeps key order (`min_price, static_percentile, min_eligible_names`) identical to today's dump for the percentile case — the identity pins prove it.

`point_in_time_eligibility`: before the session loop, `absolute = universe.min_median_dollar_volume`; inside the loop:

```python
        if absolute is not None:
            floor, names_used = absolute, 0
        else:
            ref = static_reference({s: sl.upto(as_of) for s, sl in static_slicers.items()}, universe.static_percentile, as_of=as_of)
            floor, names_used = ref.value, ref.names
        reference.append(floor)
        reference_names.append(names_used)
        if floor is None:
            skipped["no_reference"] += 1
            continue
        ...
            if volume < floor:
                reasons["illiquid_volume"] += 1
                continue
```

Update the docstring (the absolute floor "reads the same 20-session median raw dollar volume at D-1 and consults no static name"). `cube.py` coverage: `"eligibility_rule": spec.universe.floor_rule, "dollar_volume_floor": spec.universe.min_median_dollar_volume` (check the function has `spec` in scope; `build_cube(eligibility, hourly, spec, ...)` does).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled -q`
Expected: PASS.

- [ ] **Step 5: Lint, type-check, stage**

Run: `env -u VIRTUAL_ENV uv run ruff check agentic_trader tests && env -u VIRTUAL_ENV uv run ruff format --check agentic_trader tests && env -u VIRTUAL_ENV uv run mypy agentic_trader/research/pooled`
Stage and report.

---

### Task 3: `cohort-v3.json` and the three re-frozen protocols (after the controller's real runs)

**Files:**
- Create: `config/research/pooled/cohort-v3.json` (copied unchanged from the controller's screen run output `cohort.json`), `config/research/pooled/cohort3-events-v1.json`, `config/research/pooled/cohort3-daily-v1.json`, `config/research/pooled/cohort3-intraday-v1.json`
- Test: `tests/research/pooled/test_cohort3.py` (new)

**Interfaces:**
- Consumes: Task 1 (`rank_from`, empty config groups), Task 2 (`min_median_dollar_volume`); the controller supplies in the dispatch: the path of the screen run directory, the cohort-v3 SHA-256, the cohort-v3 corporate-actions `content_sha256` and its acquisition window.

- [ ] **Step 1: Write the failing tests** `tests/research/pooled/test_cohort3.py`:

```python
import hashlib
import json
from pathlib import Path

from agentic_trader.research.pooled.campaign import (
    CampaignProtocol,
    EventsCampaignProtocol,
    IntradayCampaignProtocol,
    load_campaign_protocol,
)
from agentic_trader.research.pooled.cohort import load_cohort

REPO = Path(__file__).resolve().parents[3]
POOLED = REPO / "config/research/pooled"
COHORT3_SHA256 = "REPLACE"  # from the controller's dispatch
ACTIONS_SHA256 = "REPLACE"  # cohort-v3 corporate-actions content_sha256, from the dispatch
PAIRS = (
    ("cohort3-events-v1.json", "events-v1.json", EventsCampaignProtocol),
    ("cohort3-daily-v1.json", "campaign-v4.json", CampaignProtocol),
    ("cohort3-intraday-v1.json", "intraday-v1.json", IntradayCampaignProtocol),
)
DECLARED = {
    "cohort",
    "cohort_sha256",
    "universe",
    "decision_cost_bps",
    "version",
    "title",
    "rationale",
    "excluded_families",
}


def test_cohort_v3_is_the_band_without_config_groups():
    loaded = load_cohort(POOLED / "cohort-v3.json")
    cohort = loaded.cohort
    assert loaded.sha256 == COHORT3_SHA256
    assert cohort.version == 3
    assert [source.kind for source in cohort.sources] == ["liquidity_screen"]
    screen = cohort.sources[0].screen
    assert screen.rank_from == 401 and screen.top == 1400
    assert screen.snapshot_id.startswith("00a9a363b02a6f49")
    assert 900 <= len(cohort.symbols) <= 1000
    v2 = load_cohort(POOLED / "cohort-v2.json").cohort
    screened_v2 = next(s for s in v2.sources if s.kind == "liquidity_screen").symbols
    assert not set(cohort.symbols) & set(screened_v2)  # the band starts where v2's top cut ends


def test_cohort3_protocols_equal_their_parents_except_the_declared_keys():
    for child_name, parent_name, cls in PAIRS:
        child = load_campaign_protocol(POOLED / child_name)
        parent = load_campaign_protocol(POOLED / parent_name)
        assert isinstance(child.protocol, cls)
        c, p = json.loads((POOLED / child_name).read_text()), json.loads((POOLED / parent_name).read_text())
        differing = {k for k in set(c) | set(p) if c.get(k) != p.get(k)}
        if cls is EventsCampaignProtocol:
            differing -= {"event_inputs"}
            assert c["event_inputs"]["corporate_actions"]["content_sha256"] == ACTIONS_SHA256
            assert c["event_inputs"]["earnings"] == p["event_inputs"]["earnings"]
        assert differing <= DECLARED, (child_name, differing - DECLARED)
        assert c["cohort"] == "config/research/pooled/cohort-v3.json" and c["cohort_sha256"] == COHORT3_SHA256
        assert c["universe"] == {"min_price": 10.0, "min_median_dollar_volume": 5000000.0, "min_eligible_names": 30}
        assert c["decision_cost_bps"] == 15.0 and c["version"] == 1
        assert c["families"] == p["families"] and c["search_mode"] == "fixed_set"
        assert (
            child.protocol.cube_spec().identity
            == load_campaign_protocol(POOLED / "cohort3-daily-v1.json").protocol.cube_spec().identity
        )
        assert not child.protocol.cube_spec().identity.startswith("f1c835a3")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled/test_cohort3.py -q`
Expected: FAIL (files missing).

- [ ] **Step 3: Write the files.** Copy the controller-supplied `cohort.json` to `cohort-v3.json` byte-for-byte (its SHA-256 must equal `COHORT3_SHA256`; do not reformat). For each protocol: load the parent JSON, set `cohort`, `cohort_sha256`, `universe` (exactly the dict above), `decision_cost_bps` 15.0, `version` 1, a new `title` ("… cohort v3 (mid-liquidity band) …") and `rationale` (one paragraph: same formulas as the parent, population changed to the band, absolute $5M floor because the static reference is mega-cap, 15 bp cost for wider spreads, survivorship caveat, checks A and C only), and for events `event_inputs.corporate_actions.content_sha256` = `ACTIONS_SHA256` (window unchanged). Append one entry to `excluded_families`? No — leave `excluded_families` identical to the parent unless the parent's note text references the cohort; if you change nothing there, remove `excluded_families` from `DECLARED` in the test. Write with the same style as the parent (sorted keys, two-space indent — verify by reading the parent's first lines). Report the three SHA-256s.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/pooled -q`
Expected: PASS.

- [ ] **Step 5: Lint, stage** (`pre-commit run --files` on the JSON files checks them) and report the SHA-256 of each new file.

---

### Task 4: Docs

**Files:**
- Modify: `docs/alpha-pooled-mining.md` (new subsection "### Cohort v3: the mid-liquidity band" after the cohort v2 subsection (~line 118) and a paragraph in "## Point-in-time eligibility" on the absolute floor; the "Commands" section mentions `screen-v3.json`), `docs/alpha-roadmap.md` (item 5: "*Status (October 15):*" paragraph — events campaign no finalists with the results doc link, cohort v3 built, checks pending), `CLAUDE.md` (one sentence after the events sentence: "Cohort v3 (`config/research/pooled/cohort-v3.json`, the liquidity band ranks 401–1,400 by 60-session median dollar volume on the 2026-10-15 snapshot, no config-group source) re-freezes the three fixed sets as `cohort3-events-v1`, `cohort3-daily-v1` and `cohort3-intraday-v1` with an absolute $5M 20-session median dollar-volume eligibility floor (`universe.min_median_dollar_volume`, instead of the mega-cap static percentile) and 15 bp decisive cost; checks A and C per protocol, campaigns on operator approval; the events-v1 campaign on cohort v2 found no finalists ([results](docs/alpha-pooled-events-v1-2026-10-15.md))."), plus the already-committed `docs/alpha-pooled-events-v1-2026-10-15.md` needs its link target `alpha-pooled-mining.md#event-score-inputs-protocol-pooled-events-campaign` verified against the real heading anchor.
- Test: none (docs); run `pre-commit run --files` on the touched docs.

- [ ] **Step 1: Write the docs** — factual, no results for cohort v3 (its checks have not run). Cover: why a new population; the band and snapshot (ids, counts from `cohort-v3.json`: candidates, ranked, selected); the absolute floor and why; cost; the three protocol files and their SHA-256s; survivorship and other declared limitations (spec §7); the run order and approvals.
- [ ] **Step 2: `env -u VIRTUAL_ENV uv run pre-commit run --files $(git diff --name-only --cached)`**, fix, stage, report.
