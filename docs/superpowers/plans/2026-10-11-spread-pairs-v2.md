# Spread Pairs v2 (entry rule) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the three-facet entry rule (crossing entry, upper entry band, no re-entry after a stop) to the spread lane's trading rule as defaulted fields, and freeze it as `config/research/spread/pairs-v2.json`, leaving pairs v1 loadable and bit-identical.

**Architecture:** `TradingRule` gains three defaulted fields and `SpreadProtocol` a `second_look` flag; `simulate_pair`'s step 3 (signals at the close) consults them; a new frozen protocol file pins the same cohort. Checks, study, CLI and journal are unchanged and run on v2 by its own protocol hash after merge.

**Tech Stack:** Python 3.14 (`env -u VIRTUAL_ENV uv run ...`), pydantic v2 frozen models, numpy, pytest.

**Spec:** `docs/superpowers/specs/2026-10-11-spread-pairs-v2-design.md`

## Global Constraints

- Research only: no registry, broker, Telegram, order, `/pairs`, scheduler, CLI or journal change.
- `pairs-v1.json` is byte-identical before and after (SHA-256 `88dc4945751bfdbcb45b21dca9ad84f3266239ab0b839c9a01f44986bcaaf352`) and v1 behaviour is bit-identical: the defaulted `TradingRule` must reproduce every existing trading test and fixture exactly.
- New `TradingRule` fields and their defaults: `entry: Literal["threshold", "crossing"] = "threshold"`, `z_entry_max: float | None = None`, `reenter_after_stop: bool = True`; validator: when `z_entry_max` is set, `z_entry < z_entry_max <= z_stop`. `SpreadProtocol.second_look: bool = False`, `second_look_note: str = ""`. `extra="forbid"` everywhere.
- `pairs-v2.json` differs from v1 only in `version: 2`, `title`, `hypotheses`, `decision_rule`, `"second_look": true`, and `trading` (`entry: "crossing"`, `z_entry_max: 4.0`, `reenter_after_stop: false`); `cohort_sha256` unchanged (`6f3a4a322d6333845b75208af71d3a81f46d5cc882ce110d870b6349ae3adbea`).
- Crossing semantics: entry at `t` requires `t >= 1`, `math.isfinite(z[t-1])`, `abs(z[t-1]) < z_entry` and `abs(z[t]) >= z_entry`; a non-finite `z[t-1]` blocks the entry. Band: when `z_entry_max` is set, also `abs(z[t]) < z_entry_max`. Retirement: after a trade closes with reason `"stop"` and `reenter_after_stop` is false, no further entry signal in that window; `"reverted"` never retires.
- Lint is strict: `ruff` (C408 — no `dict()` literals; PLC0415 — top-level imports only; RUF059; PLR0133), `mypy`, `pre-commit run --all-files` green before each commit. Tests: no network, deterministic.
- Docs ship in this PR (no docs-only PRs): `docs/alpha-spread-lane.md`, CLAUDE.md spread paragraph, `docs/alpha-roadmap.md` item 2.

## Review Focus

1. **A gap on the session before a would-be crossing** (`z[t-1]` NaN, `|z[t]| ≥ 2`) — no entry; the trader must not infer a crossing across a missing bar. (Task 2 `test_crossing_blocked_by_gap_before_signal`.)
2. **A spread that opens the window already outside the band** (`|z[0]| ≥ 2`) — `crossing` never enters at `t = 0` and does not enter while |z| stays ≥ 2; it enters only after z comes back inside and crosses out again. (Task 2 `test_crossing_requires_previous_z_inside_band`.)
3. **A stop followed by a clean new divergence in the same window** — with `reenter_after_stop = false` the second signal is ignored; with the default it is taken. (Task 2 `test_stop_retires_the_pair_for_the_window`.)
4. **A reversion exit followed by a new divergence** — re-entry still allowed under v2 (`reverted` never retires). (Task 2 `test_reverted_exit_does_not_retire`.)
5. **`z_entry_max` equal to `z_stop`** — allowed (the spec's v2 value); `z_entry_max > z_stop` or `≤ z_entry` rejected. (Task 1 `test_trading_rule_entry_band_validation`.)

## File map

| File | Responsibility |
| --- | --- |
| `agentic_trader/research/spread/protocol.py` | `TradingRule` new fields + validator; `SpreadProtocol.second_look` |
| `agentic_trader/research/spread/trading.py` | `simulate_pair` step-3 entry logic |
| `config/research/spread/pairs-v2.json` | frozen v2 protocol |
| `tests/research/spread/test_protocol.py`, `tests/research/spread/test_trading.py` | tests |
| `docs/alpha-spread-lane.md`, `CLAUDE.md`, `docs/alpha-roadmap.md` | docs |

---

### Task 1: `TradingRule` fields and `SpreadProtocol.second_look`

**Files:**
- Modify: `agentic_trader/research/spread/protocol.py` (`TradingRule` at ~145-155; `SpreadProtocol` at ~199-236)
- Test: `tests/research/spread/test_protocol.py`

**Interfaces:**
- Produces: `TradingRule(z_entry, z_exit, z_stop, fill, entry="threshold", z_entry_max=None, reenter_after_stop=True)`; `SpreadProtocol.second_look: bool` (default `False`) and `second_look_note: str` (default `""`). Task 2 reads `rule.entry`, `rule.z_entry_max`, `rule.reenter_after_stop`.

- [ ] **Step 1: Write the failing tests** (append to `tests/research/spread/test_protocol.py`; `TradingRule` import is needed)

```python
from agentic_trader.research.spread.protocol import TradingRule


def test_trading_rule_defaults_reproduce_v1():
    rule = TradingRule(z_entry=2.0, z_exit=0.5, z_stop=4.0, fill="next_open")
    assert rule.entry == "threshold"
    assert rule.z_entry_max is None
    assert rule.reenter_after_stop is True


def test_trading_rule_v2_fields_load():
    rule = TradingRule(
        z_entry=2.0,
        z_exit=0.5,
        z_stop=4.0,
        fill="next_open",
        entry="crossing",
        z_entry_max=4.0,
        reenter_after_stop=False,
    )
    assert rule.entry == "crossing" and rule.z_entry_max == 4.0 and rule.reenter_after_stop is False


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"z_entry_max": 2.0}, "z_entry_max"),  # must exceed z_entry
        ({"z_entry_max": 1.5}, "z_entry_max"),
        ({"z_entry_max": 4.5}, "z_entry_max"),  # must not exceed z_stop
        ({"entry": "momentum"}, "entry"),
        ({"band": 3.0}, "extra"),
    ],
)
def test_trading_rule_entry_band_validation(kwargs, match):
    with pytest.raises(ValidationError, match=match):
        TradingRule(z_entry=2.0, z_exit=0.5, z_stop=4.0, fill="next_open", **kwargs)


def test_protocol_second_look_defaults_false_and_v1_hash_is_frozen():
    loaded = load_spread_protocol(PROTOCOL_PATH)
    assert loaded.protocol.second_look is False
    assert loaded.sha256 == "88dc4945751bfdbcb45b21dca9ad84f3266239ab0b839c9a01f44986bcaaf352"
    assert loaded.protocol.second_look_note == ""
    doc = _protocol_doc()
    doc["second_look"] = True
    doc["second_look_note"] = "declared"
    assert SpreadProtocol.model_validate(doc).second_look is True
```

(If `LoadedSpreadProtocol` exposes the hash under a different attribute than `sha256`, use that attribute — check the dataclass in `protocol.py`; do not change it.)

- [ ] **Step 2: Run to verify failure**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/spread/test_protocol.py -q`
Expected: FAIL — `TradingRule` has no attribute `entry` / extra fields forbidden.

- [ ] **Step 3: Implement**

In `protocol.py`, replace the `TradingRule` class with:

```python
class TradingRule(BaseModel, frozen=True, extra="forbid"):
    z_entry: float = Field(gt=0)
    z_exit: float = Field(ge=0)
    z_stop: float = Field(gt=0)
    fill: Literal["next_open"]
    entry: Literal["threshold", "crossing"] = "threshold"
    z_entry_max: float | None = None
    reenter_after_stop: bool = True

    @model_validator(mode="after")
    def _bands(self) -> TradingRule:
        if not self.z_exit < self.z_entry < self.z_stop:
            raise ValueError("bands must satisfy z_exit < z_entry < z_stop")
        if self.z_entry_max is not None and not self.z_entry < self.z_entry_max <= self.z_stop:
            raise ValueError("z_entry_max must satisfy z_entry < z_entry_max <= z_stop")
        return self
```

In `SpreadProtocol`, add after `null_check: NullSpec`:

```python
    second_look: bool = False
    second_look_note: str = ""
```

- [ ] **Step 4: Run tests**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/spread -q`
Expected: all pass (the existing v1 tests are untouched).

- [ ] **Step 5: Commit**

```bash
git add agentic_trader/research/spread/protocol.py tests/research/spread/test_protocol.py
git commit -m "Spread protocol: TradingRule entry mode, z_entry_max and reenter_after_stop (defaulted to v1); SpreadProtocol.second_look"
```

---

### Task 2: `simulate_pair` entry logic

**Files:**
- Modify: `agentic_trader/research/spread/trading.py` (`simulate_pair`, step 3 at ~150-165)
- Test: `tests/research/spread/test_trading.py`

**Interfaces:**
- Consumes: `TradingRule.entry`, `.z_entry_max`, `.reenter_after_stop` (Task 1).
- Produces: unchanged signature `simulate_pair(fit, closes_y, closes_x, opens_y, opens_x, rule, *, window) -> PairSimulation`.

- [ ] **Step 1: Write the failing tests** (append to `tests/research/spread/test_trading.py`; `RULE`, `fit`, `paths` exist at the top of the file)

```python
V2 = TradingRule(
    z_entry=2.0, z_exit=0.5, z_stop=4.0, fill="next_open", entry="crossing", z_entry_max=4.0, reenter_after_stop=False
)


def test_crossing_requires_previous_z_inside_band():
    # starts outside: never enters while it stays out; enters only after coming back inside and crossing again
    cy, cx, oy, ox = paths([2.5, 2.6, 2.4, 0.0, 0.0, 2.5, 0.0, 0.0, 0.0, 0.0])
    sim = simulate_pair(fit(), cy, cx, oy, ox, V2, window=0)
    assert [t.entry_pos for t in sim.trades] == [6]
    assert sim.trades[0].side == -1 and sim.trades[0].entry_z == pytest.approx(2.5)
    v1 = simulate_pair(fit(), cy, cx, oy, ox, RULE, window=0)
    assert v1.trades[0].entry_pos == 1  # threshold mode enters at the first signal


def test_crossing_blocked_by_gap_before_signal():
    cy, cx, oy, ox = paths([0.0, 0.0, 2.5, 2.5, 0.0, 0.0, 0.0])
    cy[1] = np.nan  # the close before the divergence is missing
    sim = simulate_pair(fit(), cy, cx, oy, ox, V2, window=0)
    assert sim.trades == ()
    cy2, cx2, oy2, ox2 = paths([0.0, 0.0, 2.5, 2.5, 0.0, 0.0, 0.0])
    assert simulate_pair(fit(), cy2, cx2, oy2, ox2, V2, window=0).trades[0].entry_pos == 3


def test_upper_band_blocks_entries_beyond_z_entry_max():
    cy, cx, oy, ox = paths([0.0, 4.5, 4.5, 0.0, 0.0, 3.9, 0.0, 0.0])
    sim = simulate_pair(fit(), cy, cx, oy, ox, V2, window=0)
    assert [t.entry_pos for t in sim.trades] == [6]
    assert sim.trades[0].entry_z == pytest.approx(3.9)


def test_stop_retires_the_pair_for_the_window():
    z = [0.0, 2.5, 4.5, 4.5, 0.0, 0.0, 2.5, 0.0, 0.0, 0.0]
    cy, cx, oy, ox = paths(z)
    sim = simulate_pair(fit(), cy, cx, oy, ox, V2, window=0)
    assert [t.exit_reason for t in sim.trades] == ["stop"]
    allow = TradingRule(z_entry=2.0, z_exit=0.5, z_stop=4.0, fill="next_open", entry="crossing", z_entry_max=4.0)
    again = simulate_pair(fit(), cy, cx, oy, ox, allow, window=0)
    assert [t.exit_reason for t in again.trades] == ["stop", "reverted"]


def test_reverted_exit_does_not_retire():
    cy, cx, oy, ox = paths([0.0, 2.5, 2.5, 0.0, 0.0, -2.5, -2.5, 0.0, 0.0, 0.0])
    sim = simulate_pair(fit(), cy, cx, oy, ox, V2, window=0)
    assert [t.exit_reason for t in sim.trades] == ["reverted", "reverted"]
    assert [t.side for t in sim.trades] == [-1, 1]


def test_defaulted_rule_is_bit_identical_to_v1_on_every_fixture():
    rng = np.random.default_rng(7)
    explicit = TradingRule(
        z_entry=2.0,
        z_exit=0.5,
        z_stop=4.0,
        fill="next_open",
        entry="threshold",
        z_entry_max=None,
        reenter_after_stop=True,
    )
    for _ in range(20):
        z = list(np.cumsum(rng.normal(0.0, 1.2, 60)))
        cy, cx, oy, ox = paths(z)
        if rng.random() < 0.5:
            cy[int(rng.integers(1, 59))] = np.nan
        a = simulate_pair(fit(), cy, cx, oy, ox, RULE, window=0)
        b = simulate_pair(fit(), cy, cx, oy, ox, explicit, window=0)
        assert a.trades == b.trades
        np.testing.assert_array_equal(a.daily, b.daily)
        np.testing.assert_array_equal(a.turnover, b.turnover)
```

- [ ] **Step 2: Run to verify failure**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/spread/test_trading.py -q`
Expected: the four v2 behaviour tests FAIL (entries happen at the first threshold signal; stops do not retire); the identity test passes already.

- [ ] **Step 3: Implement**

In `simulate_pair`, add `retired = False` beside `pending_exit`, and replace the step-3 block with:

```python
        # 3. signals at the close (a missing close on either leg emits none)
        if math.isfinite(z[t]):
            if position is not None and pending_exit is None:
                if abs(z[t]) <= rule.z_exit:
                    pending_exit = ("reverted", float(z[t]))
                elif abs(z[t]) >= rule.z_stop:
                    pending_exit = ("stop", float(z[t]))
            elif position is None and pending_entry is None and not last and not retired:
                if _entry_allowed(z, t, rule):
                    pending_entry = (1 if z[t] < 0.0 else -1, float(z[t]))
```

Set `retired = True` where a position is closed with reason `"stop"` and `not rule.reenter_after_stop` (inside the fill-at-the-open branch that closes `pending_exit`; the window-end close never matters since the window ends). Add the module-level helper:

```python
def _entry_allowed(z: np.ndarray, t: int, rule: TradingRule) -> bool:
    """Flat-only entry test at close ``t`` (``z[t]`` is finite)."""
    magnitude = abs(z[t])
    if magnitude < rule.z_entry:
        return False
    if rule.z_entry_max is not None and magnitude >= rule.z_entry_max:
        return False
    if rule.entry == "crossing":
        return t >= 1 and math.isfinite(z[t - 1]) and abs(z[t - 1]) < rule.z_entry
    return True
```

Keep the existing side convention: `z <= -z_entry` → side `+1` (long Y), `z >= z_entry` → side `-1`.

- [ ] **Step 4: Run tests**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/spread -q`
Expected: all pass, including every pre-existing trading test unchanged.

- [ ] **Step 5: Commit**

```bash
git add agentic_trader/research/spread/trading.py tests/research/spread/test_trading.py
git commit -m "Spread trading: crossing entry, upper entry band and stop retirement; v1 defaults bit-identical"
```

---

### Task 3: Frozen `pairs-v2.json` and docs

**Files:**
- Create: `config/research/spread/pairs-v2.json`
- Test: `tests/research/spread/test_protocol.py`
- Modify: `docs/alpha-spread-lane.md` (§3 table trading row, §9 limits), `CLAUDE.md` (spread paragraph), `docs/alpha-roadmap.md` (item 2 status)

**Interfaces:**
- Consumes: Task 1 fields.
- Produces: the v2 protocol file the operator runs after merge.

- [ ] **Step 1: Write the failing test** (append to `tests/research/spread/test_protocol.py`)

```python
PROTOCOL_V2_PATH = REPO / "config/research/spread/pairs-v2.json"


def test_pairs_v2_is_v1_with_the_entry_rule_and_a_declared_second_look():
    v1 = load_spread_protocol(PROTOCOL_PATH)
    v2 = load_spread_protocol(PROTOCOL_V2_PATH)
    assert v2.sha256 != v1.sha256
    assert v2.protocol.version == 2 and v2.protocol.second_look is True
    assert v2.protocol.cohort_sha256 == v1.protocol.cohort_sha256
    t = v2.protocol.trading
    assert (t.entry, t.z_entry_max, t.reenter_after_stop) == ("crossing", 4.0, False)
    assert (t.z_entry, t.z_exit, t.z_stop, t.fill) == (2.0, 0.5, 4.0, "next_open")
    same = (
        "cohort",
        "feed",
        "adjustment",
        "bars",
        "windows",
        "schedule",
        "formation",
        "costs_bps_per_side",
        "decision_cost_bps",
        "bootstrap",
        "pass_rules",
        "confirmation_rules",
        "power",
        "null_check",
        "references",
    )
    for field in same:
        assert getattr(v2.protocol, field) == getattr(v1.protocol, field), field
    assert (
        "crossing" in v2.protocol.hypotheses
        and "second" in json.loads(PROTOCOL_V2_PATH.read_text())["second_look_note"].lower()
    )
```

- [ ] **Step 2: Run to verify failure**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/spread/test_protocol.py -q`
Expected: FAIL — file not found.

- [ ] **Step 3: Write `config/research/spread/pairs-v2.json`**

Copy `pairs-v1.json` byte for byte and change only these keys:

```json
  "version": 2,
  "title": "Same-sector pairs v2: Engle-Granger formation, z-score reversion trading with a crossing entry, an upper entry band and no re-entry after a stop (Gatev-style 504/126 tiles)",
  "hypotheses": "Among same-sector US large-cap equities, pairs whose log prices are cointegrated over the previous 504 sessions (Engle-Granger, p<0.05, half-life 5-42 sessions) have spreads that revert over the next 126 sessions, so that entering when |z| crosses 2 from inside (previous close |z|<2, current 2<=|z|<4), exiting at |z|<=0.5 (stop |z|>=4 retires the pair for the window; window-end close-out), dollar-neutral at the formation hedge ratio, earns a positive mean return after 5 bp per side per leg with market beta near zero.",
  "decision_rule": "Discovery (trading windows 2018-01-02..2023-12-29) passes when S1 (lane mean daily return > 0, stationary-bootstrap ci90 lower bound > 0), S2 (closed-trade mean and 1%-trimmed mean > 0), S3 (>=100 closed trades, >=8 windows, >=60% of windows positive) and S4 (|beta to SPY| <= 0.2) all hold at 5 bp per side. Only then is the confirmation window (2024-01-02..2026-07-31) journaled as consumed and read; the same rule with S3 relaxed to >=30 trades and >=4 windows decides confirmed vs failed_confirmation. Nothing promotes.",
  "trading": {"z_entry": 2.0, "z_exit": 0.5, "z_stop": 4.0, "fill": "next_open", "entry": "crossing", "z_entry_max": 4.0, "reenter_after_stop": false},
  "second_look": true,
  "second_look_note": "Second protocol evaluated on the same discovery window; the entry rule was chosen from construction statistics of the v1 run (where entries occurred, how often |z|>=4 occurs), not from returns. A v2 discovery pass is weaker evidence than v1's would have been; the confirmation window, never read, is the clean test."
```

Keep every other key and value identical to v1 (including `references`, `cohort`, `cohort_sha256`, `bars`, `windows`, `schedule`, `formation`, `costs_bps_per_side`, `decision_cost_bps`, `bootstrap`, `pass_rules`, `confirmation_rules`, `power`, `null_check`).

- [ ] **Step 4: Run tests**

Run: `env -u VIRTUAL_ENV uv run pytest tests/research/spread -q`
Expected: all pass.

- [ ] **Step 5: Docs**

`docs/alpha-spread-lane.md` §3: change the `trading` row to
`| trading | v1: z entry 2.0, exit 0.5, stop 4.0, fills at the next open. v2 (`pairs-v2.json`): the same levels, entry only when |z| crosses 2.0 from inside (previous close |z| < 2.0) and |z| < 4.0 at the signal; a stop retires the pair for the rest of the window (a reversion does not) |`
and add to §9 after the "Detection band." paragraph:

```markdown
Second look. Protocol v2 is the second protocol evaluated on the same discovery window; it changes only
the entry rule, chosen from construction statistics of the v1 run (84% of v1 entries were re-entries into
spreads already beyond the stop), not from returns. Its file declares `second_look: true`. A v2 discovery
pass is weaker evidence than v1's would have been; the confirmation window, never read, is the clean test.
No third protocol runs on this discovery window without a new spec and operator approval.
```

`CLAUDE.md` spread paragraph: append the sentence
`Protocol v2 (`pairs-v2.json`, `second_look: true`) changes only the entry rule (crossing entry, |z| < 4 at the signal, no re-entry after a stop) and runs through the same checks and study by its own hash.`

`docs/alpha-roadmap.md` item 2 status: append
`v2 (entry rule only: crossing entry, upper band, no re-entry after a stop; declared second look) is frozen as `pairs-v2.json` and runs checks A/C and the study after merge.`

- [ ] **Step 6: Lint and commit**

Run: `env -u VIRTUAL_ENV uv run pre-commit run --all-files`
Expected: green (ruff-format may rewrap markdown; re-add and commit again).

```bash
git add config/research/spread/pairs-v2.json tests/research/spread/test_protocol.py docs/alpha-spread-lane.md CLAUDE.md docs/alpha-roadmap.md
git commit -m "Spread pairs v2: frozen protocol with the entry rule and a declared second look; docs"
```
