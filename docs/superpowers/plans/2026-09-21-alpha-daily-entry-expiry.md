# Session-Bounded Daily Alpha Entries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a native-daily alpha carry an entry-only lifetime of one trading session that means the same thing in research and live, make mining use it, and freeze a confirmatory power run that measures whether the funnel can then recognise a planted signal.

**Architecture:** One new identity rule (`semantics_version == 5`: daily, no clock, exactly one fixed entry-only lifetime) in `AlphaDefinition`. Everything downstream already reads the lifetime from the execution policy: the simulator, the screener's `alpha_policy` stamp, byte-exact admission matching, and the live `TradeLifetimeService` cancellation path. The miner gains an `execution` input applied to every trial; the mine CLI and the existing power study expose it. No new clock class, bar layout, table, queue or service.

**Tech Stack:** Python 3.14, `uv`, pandas, Pydantic v2, Click, pytest (async auto mode), SQLAlchemy 2 async.

**Spec:** `docs/superpowers/specs/2026-09-21-alpha-daily-entry-expiry-design.md`

## Global Constraints

- Work only in the worktree `/Users/adamhadani/Development/agentic-trader-entry-expiry` (branch `feat/alpha-entry-expiry`). Never touch the installed checkout `/Users/adamhadani/Development/agentic-trader`; a live daemon and a watchdog that executes `scripts/launchd.sh` every 60 seconds run from it.
- The constant is exactly `DAILY_ENTRY_LIFETIME_SECONDS = 57_600`. The only legal version-5 lifetime is `TradeLifetimePolicy(resting_seconds=57_600, holding_seconds=None, version="elapsed_utc_v2")`.
- Version 5 requires `timeframe == "1d"` and `clock is None`. It places **no** constraint on `data_feed` or `adjustment`.
- Do **not** add a field to `AlphaDefinition` and do not touch the version-2 `clock` pop in `to_dict`: every existing `version_id` must stay byte-exact. Rules for versions 2, 3 and 4 are unchanged.
- Do **not** add, remove or rename a field of `ValidationPolicy` (`asdict(ValidationPolicy())` is compared against every stored qualification).
- No holding deadline, no repricing, no market entries. Built-in (non-alpha) strategies are out of scope.
- A `gtc` `PowerProtocol` document and identity must be byte-identical to before; previously frozen protocol files must still load.
- No change to gates, thresholds, lifetime trial accounting, the broker adapter, `TradeLifetimeService`, `entry_cancel` handling, or `_alpha_entry_rejection`.
- POST/PATCH/DELETE broker calls are never retried; this plan adds no broker call.
- Tests use temporary SQLite and blocked network I/O; never relax those guards. PostgreSQL tests need `--run-postgres` and `TEST_POSTGRES_URL` naming a `test_*` database. Tests must be calendar-independent: never compare a fixed date with the real clock.
- Commit process: `uv run ruff check --fix && uv run ruff format && uv run mypy agentic_trader`, focused tests, then one `git commit`; the commit hook is the pre-commit gate (ruff, mypy, impacted-tests pytest). Never `--no-verify`.
- Commit messages end with: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`

## File Structure

| File | Responsibility |
| --- | --- |
| `agentic_trader/execution/lifetime_policy.py` (modify) | `DAILY_ENTRY_LIFETIME_SECONDS`, `daily_entry_lifetime()` |
| `agentic_trader/research/alpha/strategy.py` (modify) | `session_entry_policy(base=None)` — the one constructor of the deployed timed policy |
| `agentic_trader/research/alpha/models.py` (modify) | Version-5 identity rule |
| `agentic_trader/research/alpha/miner.py` (modify) | `mine(..., execution=None)` applied to every trial; `evaluate_alpha` unchanged |
| `agentic_trader/cli/commands/alpha.py` (modify) | `--entry-policy` on `mine`, `test`, `power-plan` |
| `agentic_trader/research/alpha/power_study.py` (modify) | `PowerProtocol.entry_policy` |
| `scripts/launchd.sh` (modify) | Pin the scheduled benchmark to `--entry-policy gtc` |
| `tests/research/test_alpha_entry_expiry.py` (create) | Identity, simulator timing, research/live parity |
| `tests/research/test_alpha_miner_entry_policy.py` (create) | Miner behaviour |
| `tests/research/test_power_entry_policy.py` (create) | Protocol identity and mining under the policy |
| `tests/integration/test_trade_lifetimes.py` (modify) | Entry-only live siblings |
| `tests/workflows/test_alpha_admission.py` (modify) | Version-5 signal passes the active gate |

---

### Task 1: The version-5 contract

**Files:**
- Modify: `agentic_trader/execution/lifetime_policy.py` (after the `TRADE_LIFETIME_VERSION_INDEPENDENT` constant and at end of file)
- Modify: `agentic_trader/research/alpha/strategy.py` (after `execution_policy_from_dict`, ~line 82)
- Modify: `agentic_trader/research/alpha/models.py:72-104`
- Modify: `tests/research/test_alpha_trade_lifetimes.py:108-111`
- Test: `tests/research/test_alpha_entry_expiry.py`

**Interfaces:**
- Produces:
  - `agentic_trader.execution.lifetime_policy.DAILY_ENTRY_LIFETIME_SECONDS: int = 57_600`
  - `agentic_trader.execution.lifetime_policy.daily_entry_lifetime() -> TradeLifetimePolicy`
  - `agentic_trader.research.alpha.strategy.session_entry_policy(base: AlphaExecutionPolicy | None = None) -> TimedAlphaExecutionPolicy`
  - `agentic_trader.research.alpha.models.DAILY_SESSION_SEMANTICS_VERSION: int = 5`
  - `AlphaDefinition(..., semantics_version=5, timeframe="1d", clock=None, execution=session_entry_policy())` is valid; every other version-5 combination raises `ValueError`.

- [ ] **Step 1: Write the failing tests**

Create `tests/research/test_alpha_entry_expiry.py`:

```python
"""Version 5: one trading session of resting entry, identical in research and live."""

from dataclasses import replace
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from agentic_trader.execution.lifetime_policy import (
    DAILY_ENTRY_LIFETIME_SECONDS,
    TRADE_LIFETIME_VERSION_INDEPENDENT,
    TradeLifetimePolicy,
    daily_entry_lifetime,
)
from agentic_trader.market.bars import FixedDailyClockPolicy
from agentic_trader.research.alpha.models import DAILY_SESSION_SEMANTICS_VERSION, AlphaDefinition
from agentic_trader.research.alpha.simulation import BracketIntent, simulate_execution
from agentic_trader.research.alpha.strategy import (
    AlphaExecutionPolicy,
    TimedAlphaExecutionPolicy,
    session_entry_policy,
)


NEW_YORK = ZoneInfo("America/New_York")


def daily(**changes):
    fields = {
        "alpha_id": "alpha_v5",
        "name": "V5",
        "expression": "returns",
        "timeframe": "1d",
        "semantics_version": DAILY_SESSION_SEMANTICS_VERSION,
        "execution": session_entry_policy(),
    }
    fields.update(changes)
    return AlphaDefinition(**fields)


def test_the_constant_and_the_only_legal_lifetime():
    assert DAILY_ENTRY_LIFETIME_SECONDS == 57_600 and DAILY_SESSION_SEMANTICS_VERSION == 5
    assert daily_entry_lifetime() == TradeLifetimePolicy(
        resting_seconds=57_600, holding_seconds=None, version=TRADE_LIFETIME_VERSION_INDEPENDENT
    )
    policy = session_entry_policy(AlphaExecutionPolicy(stop_atr=2.0))
    assert isinstance(policy, TimedAlphaExecutionPolicy)
    assert policy.stop_atr == 2.0 and policy.lifetime == daily_entry_lifetime()


def test_version_five_round_trips_with_any_feed_and_a_distinct_identity():
    for feed in ("alpaca:iex", "alpaca:sip", "yfinance", "synthetic", "unverified"):
        definition = daily(data_feed=feed)
        assert AlphaDefinition.from_dict(definition.to_dict()) == definition
        assert definition.to_dict()["clock"] is None
        assert definition.to_dict()["execution"]["lifetime"]["resting_seconds"] == 57_600
    gtc = AlphaDefinition("alpha_v5", "V5", "returns", timeframe="1d")
    assert daily().version_id != gtc.version_id


@pytest.mark.parametrize(
    "changes",
    [
        {"timeframe": "4h"},
        {"clock": FixedDailyClockPolicy()},
        {"execution": AlphaExecutionPolicy()},
        {"execution": TimedAlphaExecutionPolicy(lifetime=TradeLifetimePolicy(86_400, None, version="elapsed_utc_v2"))},
        {
            "execution": TimedAlphaExecutionPolicy(
                lifetime=TradeLifetimePolicy(57_600, 86_400, version="elapsed_utc_v2")
            )
        },
        {"execution": TimedAlphaExecutionPolicy(lifetime=TradeLifetimePolicy(57_600, 57_600))},
    ],
)
def test_every_other_version_five_combination_is_rejected(changes):
    with pytest.raises(ValueError, match="Version 5"):
        daily(**changes)


def test_version_two_still_cannot_carry_a_lifetime_and_its_identity_is_unchanged():
    with pytest.raises(ValueError, match="Timed execution"):
        AlphaDefinition("timed", "Timed", "returns", timeframe="1d", execution=session_entry_policy())
    plain = AlphaDefinition("alpha_pin", "Pin", "delta(close,3)", timeframe="1d", eligible_symbols=("SPY",))
    # Pinned before this change on the same commit; a new AlphaDefinition field would move it.
    assert plain.version_id == PINNED_V2_VERSION_ID
    assert "clock" not in plain.to_dict()


def bars(labels, rows):
    frame = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=pd.DatetimeIndex(labels))
    frame["volume"] = 1_000
    return frame


def new_york_midnights(start, sessions):
    days, day = [], start
    while len(days) < sessions:
        if day.weekday() < 5:
            days.append(datetime.combine(day, time(0), NEW_YORK))
        day += timedelta(days=1)
    return days


def long_entry(limit):
    return BracketIntent(direction=1, limit=limit, stop=limit - 5, target=limit + 10, signal_timestamp="signal")


def test_an_unfilled_entry_expires_at_the_next_bar_before_its_fill_check():
    # Thursday entry bar trades entirely above the limit; Friday would fill it.
    labels = new_york_midnights(datetime(2026, 3, 5).date(), 3)
    frame = bars(labels, [[101, 102, 100.5, 101], [101, 103, 100.6, 102], [102, 102, 99, 100]])
    timed = simulate_execution(frame, {1: long_entry(100.0)}, session_entry_policy(), trace=True)
    assert timed["total_trades"] == 0 and timed["pending_entry"] is False
    assert [e["kind"] for e in timed["events"]].count("entry_expired") == 1
    gtc = simulate_execution(frame, {1: long_entry(100.0)}, AlphaExecutionPolicy(), trace=True)
    assert [e["kind"] for e in gtc["events"]].count("entry_filled") == 1


def test_a_friday_entry_does_not_survive_the_weekend_or_the_dst_change():
    # Friday 6 March 2026 precedes the US daylight-saving change on Sunday 8 March.
    labels = new_york_midnights(datetime(2026, 3, 5).date(), 3)
    assert labels[1].weekday() == 4 and labels[2].weekday() == 0
    assert (labels[2] - labels[1]) == timedelta(hours=71)
    frame = bars(labels, [[101, 102, 100.5, 101], [101, 103, 100.6, 102], [102, 102, 99, 100]])
    result = simulate_execution(frame, {1: long_entry(100.0)}, session_entry_policy(), trace=True)
    assert result["total_trades"] == 0
    assert [e["kind"] for e in result["events"]].count("entry_expired") == 1


def test_an_entry_filled_on_its_own_bar_is_unaffected_by_the_lifetime():
    labels = new_york_midnights(datetime(2026, 3, 9).date(), 3)
    frame = bars(labels, [[101, 102, 100.5, 101], [101, 102, 99.5, 101], [101, 120, 100.5, 119]])
    timed = simulate_execution(frame, {1: long_entry(100.0)}, session_entry_policy(), trace=True)
    gtc = simulate_execution(frame, {1: long_entry(100.0)}, AlphaExecutionPolicy(), trace=True)
    assert timed["total_trades"] == gtc["total_trades"] == 1
    assert timed["total_return_pct"] == pytest.approx(gtc["total_return_pct"])
    assert not [e for e in timed["events"] if e["kind"] == "holding_expired"]


@pytest.mark.parametrize("day", [datetime(2026, 1, 14).date(), datetime(2026, 7, 15).date()])
@pytest.mark.parametrize("submit", [time(9, 30), time(12, 0), time(15, 59, 59)])
def test_live_deadline_precedes_the_next_regular_open_in_est_and_edt(day, submit):
    submitted = datetime.combine(day, submit, NEW_YORK)
    next_open = datetime.combine(day + timedelta(days=1), time(9, 30), NEW_YORK)
    assert daily_entry_lifetime().entry_deadline(submitted) < next_open
    one_day = TradeLifetimePolicy(86_400, None, version=TRADE_LIFETIME_VERSION_INDEPENDENT)
    # Documents why the constant is not 86,400: a live order would span two sessions.
    assert one_day.entry_deadline(submitted) >= next_open


def test_research_deadline_precedes_the_next_daily_label():
    labels = new_york_midnights(datetime(2026, 1, 12).date(), 5)
    for label, following in zip(labels, labels[1:], strict=False):
        assert daily_entry_lifetime().entry_deadline(label) < following
    assert np.all(np.diff([label.timestamp() for label in labels]) >= 86_400)
```

Before implementing, compute the pin and paste it into the test module as a module constant directly under `NEW_YORK`:

```bash
uv run python -c "from agentic_trader.research.alpha.models import AlphaDefinition; print(AlphaDefinition('alpha_pin','Pin','delta(close,3)',timeframe='1d',eligible_symbols=('SPY',)).version_id)"
```

```python
PINNED_V2_VERSION_ID = "<the 64 hex characters printed above>"
```

`BracketIntent(direction, limit, stop, target, signal_timestamp)`, `simulate_execution(frame, intents, policy, *, start=0, trace=False)` and the event strings (`order_created`, `entry_expired`, `holding_expired`, `entry_filled`, `exit_filled`) are the existing names in `agentic_trader/research/alpha/simulation.py:132-176`. The simulator requires a timezone-aware index when a lifetime is present; the New York labels above satisfy that.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/research/test_alpha_entry_expiry.py -q`
Expected: `ImportError: cannot import name 'DAILY_ENTRY_LIFETIME_SECONDS'`.

- [ ] **Step 3: Implement the constant and constructor**

`agentic_trader/execution/lifetime_policy.py`, after `TRADE_LIFETIME_VERSION_INDEPENDENT`:

```python
# One regular trading session. Research stamps a daily order at its New York midnight
# label and live stamps it at submission inside 09:30-16:00; +16 h expires both before
# the next session can fill them. 86,400 would give a live order two partial sessions.
DAILY_ENTRY_LIFETIME_SECONDS = 57_600
```

and at the end of the file:

```python
def daily_entry_lifetime() -> TradeLifetimePolicy:
    """The only lifetime a native-daily (semantics version 5) alpha may declare."""
    return TradeLifetimePolicy(
        resting_seconds=DAILY_ENTRY_LIFETIME_SECONDS,
        holding_seconds=None,
        version=TRADE_LIFETIME_VERSION_INDEPENDENT,
    )
```

`agentic_trader/research/alpha/strategy.py`, after `execution_policy_from_dict` (extend the existing `lifetime_policy` import with `daily_entry_lifetime`):

```python
def session_entry_policy(base: AlphaExecutionPolicy | None = None) -> TimedAlphaExecutionPolicy:
    """Bracket economics of ``base`` with a one-session resting entry and no holding deadline."""
    fields = (base or AlphaExecutionPolicy()).to_dict()
    fields.pop("lifetime", None)
    return TimedAlphaExecutionPolicy(**fields, lifetime=daily_entry_lifetime())
```

- [ ] **Step 4: Implement the identity rule**

`agentic_trader/research/alpha/models.py` — add near the other module constants:

```python
DAILY_SESSION_SEMANTICS_VERSION = 5
```

Import `daily_entry_lifetime` from `agentic_trader.execution.lifetime_policy`. Change the allowed versions:

```python
            or self.semantics_version not in (2, 3, 4, DAILY_SESSION_SEMANTICS_VERSION)
```

Immediately **before** the existing line `if isinstance(self.execution, TimedAlphaExecutionPolicy) and self.clock is None:` insert:

```python
        if self.semantics_version == DAILY_SESSION_SEMANTICS_VERSION and (
            self.timeframe != "1d"
            or self.clock is not None
            or not isinstance(self.execution, TimedAlphaExecutionPolicy)
            or self.execution.lifetime != daily_entry_lifetime()
        ):
            raise ValueError(
                "Version 5 requires an unclocked native daily definition with the fixed one-session entry lifetime"
            )
```

and narrow the existing rule to exclude version 5 (message unchanged so its existing test still matches `"session"`):

```python
        if (
            isinstance(self.execution, TimedAlphaExecutionPolicy)
            and self.clock is None
            and self.semantics_version != DAILY_SESSION_SEMANTICS_VERSION
        ):
            raise ValueError("Timed execution requires a versioned session clock")
```

Do not change `to_dict`, `from_dict` or the identity function. `from_dict` already restores `semantics_version`, a `None` clock and a timed execution via `execution_policy_from_dict`.

- [ ] **Step 5: Keep the existing fence test honest**

In `tests/research/test_alpha_trade_lifetimes.py::test_fixed_duration_simulation_cannot_silently_use_session_lifetimes`, the assertion still holds (a version-2 definition with a timed policy is rejected with a message containing `session`). Leave it unmodified and confirm it passes.

- [ ] **Step 6: Run**

Run: `uv run pytest tests/research/test_alpha_entry_expiry.py tests/research/test_alpha_trade_lifetimes.py tests/research/test_alpha_contract.py tests/research/test_alpha_lifetime_attribution.py -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
uv run ruff check --fix && uv run ruff format && uv run mypy agentic_trader
git add agentic_trader/execution/lifetime_policy.py agentic_trader/research/alpha/strategy.py agentic_trader/research/alpha/models.py tests/research/test_alpha_entry_expiry.py
git commit -m "feat(alpha): add the native-daily one-session entry contract (semantics v5)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Mine under the deployed entry policy

**Files:**
- Modify: `agentic_trader/research/alpha/miner.py` (`mine` signature ~309-323; the per-trial `replace` ~396-398)
- Test: `tests/research/test_alpha_miner_entry_policy.py`

**Interfaces:**
- Consumes (Task 1): `session_entry_policy`, `DAILY_SESSION_SEMANTICS_VERSION`.
- Produces: `AlphaMiner.mine(df, ..., execution: AlphaExecutionPolicy | None = None)`. With `execution=None` behaviour and every produced `version_id` are identical to before. With a `TimedAlphaExecutionPolicy`, every trial definition (catalog, random and genetic) carries that execution and `semantics_version=5`; any other timeframe than `"1d"` raises `ValueError("Session-bounded entries require native daily bars")` before any trial is evaluated.

- [ ] **Step 1: Write the failing tests**

Create `tests/research/test_alpha_miner_entry_policy.py`:

```python
import pandas as pd
import pytest

from agentic_trader.research.alpha.miner import AlphaMiner
from agentic_trader.research.alpha.models import DAILY_SESSION_SEMANTICS_VERSION
from agentic_trader.research.alpha.strategy import session_entry_policy
from agentic_trader.research.alpha.study import MarketScenario, market_bars, study_catalog
from agentic_trader.research.alpha.validation import ValidationPolicy


@pytest.fixture(scope="module")
def frame():
    scenario = MarketScenario(name="dense", observations=900, interval=8, effect=0.004, volatility_persistence=0.0)
    return market_bars(scenario, seed=11)


def mine(frame, method, **kwargs):
    miner = AlphaMiner(seed=5, catalog=study_catalog(), policy=ValidationPolicy())
    miner.mine(
        frame,
        iterations=3,
        symbol="SYNTH",
        method=method,
        min_sharpe=float("-inf"),
        min_dsr=0,
        min_ic=float("-inf"),
        max_seconds=60,
        **kwargs,
    )
    return miner.last_run


@pytest.mark.parametrize("method", ["random", "genetic"])
def test_every_trial_is_evaluated_under_the_requested_policy(frame, method):
    run = mine(frame, method, execution=session_entry_policy())
    definitions = [t["definition"] for t in run["trials"] if t.get("definition")]
    assert definitions
    assert {d["semantics_version"] for d in definitions} == {DAILY_SESSION_SEMANTICS_VERSION}
    assert {d["execution"]["lifetime"]["resting_seconds"] for d in definitions} == {57_600}
    assert {d["execution"]["lifetime"]["holding_seconds"] for d in definitions} == {None}
    assert any(d["alpha_id"] == "synthetic_pulse" for d in definitions)


@pytest.mark.parametrize("method", ["random", "genetic"])
def test_default_mining_is_unchanged(frame, method):
    baseline, again = mine(frame, method), mine(frame, method, execution=None)
    ids = lambda run: [
        t["definition"]["version_id"] if "version_id" in t["definition"] else t["definition"] for t in run["trials"]
    ]  # noqa: E731
    assert ids(baseline) == ids(again)
    assert all("lifetime" not in t["definition"]["execution"] for t in baseline["trials"] if t.get("definition"))


def test_the_policy_changes_which_trades_happen(frame):
    gtc, session = mine(frame, "random"), mine(frame, "random", execution=session_entry_policy())
    known = lambda run: next(t for t in run["trials"] if t["definition"]["alpha_id"] == "synthetic_pulse")  # noqa: E731
    assert known(gtc)["candidate"]["definition"]["execution"] != known(session)["candidate"]["definition"]["execution"]


def test_intraday_mining_rejects_the_daily_policy(frame):
    intraday = frame.copy()
    intraday.attrs["timeframe"] = "4h"
    miner = AlphaMiner(seed=5, catalog=study_catalog(), policy=ValidationPolicy())
    with pytest.raises(ValueError, match="native daily"):
        miner.mine(intraday, iterations=1, timeframe="4h", execution=session_entry_policy())
```

Confirm the `MarketScenario` field names and `market_bars` signature with `grep -n "class MarketScenario" -A12 agentic_trader/research/alpha/study.py` and `grep -n "^def market_bars" -A4 agentic_trader/research/alpha/study.py`; `agentic_trader/research/alpha/lifetime_attribution.py:165-171` shows a working construction. Replace the two `lambda` helpers with small named functions if ruff objects; keep the assertions. If `miner.last_run["trials"][i]["definition"]` does not contain a `version_id` key, compare the whole definition dict instead (the helper already falls back to that).

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/research/test_alpha_miner_entry_policy.py -q`
Expected: `TypeError: AlphaMiner.mine() got an unexpected keyword argument 'execution'`.

- [ ] **Step 3: Implement**

`agentic_trader/research/alpha/miner.py` — extend imports:

```python
from agentic_trader.research.alpha.models import DAILY_SESSION_SEMANTICS_VERSION
from agentic_trader.research.alpha.strategy import AlphaExecutionPolicy, TimedAlphaExecutionPolicy
```

(merge with the module's existing imports from those modules). Add the keyword to `mine`:

```python
        method: str = "random",
        max_seconds: float = 300,
        execution: AlphaExecutionPolicy | None = None,
    ) -> list[AlphaCandidate]:
```

Directly after `validate_sampling(df, timeframe)`:

```python
        if isinstance(execution, TimedAlphaExecutionPolicy) and timeframe != "1d":
            raise ValueError("Session-bounded entries require native daily bars")
```

Find the single per-trial rebuild that every trial passes through:

```python
            definition = replace(
                definition, data_feed=df.attrs.get("feed", "unverified"), adjustment=df.attrs.get("adjustment", "raw")
            )
```

and follow it with:

```python
            if execution is not None:
                # Evaluate, select and journal the policy that will actually trade.
                definition = replace(
                    definition,
                    execution=execution,
                    semantics_version=DAILY_SESSION_SEMANTICS_VERSION
                    if isinstance(execution, TimedAlphaExecutionPolicy)
                    else definition.semantics_version,
                )
```

Verify with `grep -n "definitions\[trial_number\]\|definition = replace" agentic_trader/research/alpha/miner.py` that catalog trials (`definitions[trial_number]`) and generated trials both reach that statement before evaluation; if a catalog trial is evaluated before it, move the new block so it precedes the first use of `definition` in the trial body.

- [ ] **Step 4: Run**

Run: `uv run pytest tests/research/test_alpha_miner_entry_policy.py tests/research/test_alpha_miner.py tests/research/test_alpha_discovery.py -q`
Expected: all pass; pre-existing miner tests unmodified.

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix && uv run ruff format && uv run mypy agentic_trader
git add agentic_trader/research/alpha/miner.py tests/research/test_alpha_miner_entry_policy.py
git commit -m "feat(alpha): mine candidates under the entry policy that will trade

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Operator CLI and the scheduled benchmark

**Files:**
- Modify: `agentic_trader/cli/commands/alpha.py` (`mine` options ~194-232 and the `miner.mine` call ~282-292; `test` command ~409-429)
- Modify: `scripts/launchd.sh:103` and `:195`
- Test: `tests/cli/test_cli_alpha.py` (append)

**Interfaces:**
- Consumes: `AlphaMiner.mine(..., execution=)`, `session_entry_policy`, `DAILY_SESSION_SEMANTICS_VERSION`.
- Produces: `copilot alpha mine --entry-policy [session|gtc]` and `copilot alpha test --entry-policy [session|gtc]`. When the option is omitted the default is `session` for `--interval 1d` and `gtc` otherwise. `--entry-policy session` with a non-daily interval is a `ClickException`.

- [ ] **Step 1: Write the failing tests**

Read the top of `tests/cli/test_cli_alpha.py` for how `download_bars` and `alpha_repository` are patched in the existing `mine` tests (`grep -n "def test_.*mine" -A25 tests/cli/test_cli_alpha.py | head -80`), then append tests that reuse that module's existing patching helpers/fixtures:

```python
def resolve(interval, choice):
    from agentic_trader.cli.commands.alpha import resolve_entry_policy

    return resolve_entry_policy(interval, choice)


def test_entry_policy_defaults_to_session_for_daily_and_gtc_otherwise():
    from agentic_trader.research.alpha.strategy import TimedAlphaExecutionPolicy

    assert isinstance(resolve("1d", None), TimedAlphaExecutionPolicy)
    assert isinstance(resolve("1d", "session"), TimedAlphaExecutionPolicy)
    assert resolve("1d", "gtc") is None
    assert resolve("4h", None) is None and resolve("4h", "gtc") is None


def test_session_entry_policy_rejects_intraday_intervals():
    import click

    with pytest.raises(click.ClickException, match="native daily"):
        resolve("4h", "session")


def test_mine_help_documents_the_entry_policy():
    result = CliRunner().invoke(cli, ["alpha", "mine", "--help"])
    assert result.exit_code == 0 and "--entry-policy" in result.output
    result = CliRunner().invoke(cli, ["alpha", "test", "--help"])
    assert result.exit_code == 0 and "--entry-policy" in result.output


def test_scheduled_benchmark_is_pinned_to_gtc():
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "scripts" / "launchd.sh"
    lines = [line for line in script.read_text().splitlines() if "copilot alpha mine --universe etf32" in line]
    assert len(lines) == 2 and all("--entry-policy gtc" in line for line in lines)
```

If the module already has an end-to-end `mine` test with patched bars, add one assertion to it: after a default daily `mine`, every journaled trial definition has `semantics_version == 5`; and add a twin invocation with `--entry-policy gtc` asserting none has a `lifetime`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/cli/test_cli_alpha.py -q -k "entry_policy or benchmark_is_pinned"`
Expected: `ImportError: cannot import name 'resolve_entry_policy'` and the pin assertion failing.

- [ ] **Step 3: Implement**

`agentic_trader/cli/commands/alpha.py` — add near the other helpers:

```python
ENTRY_POLICY_OPTION = click.option(
    "--entry-policy",
    type=click.Choice(["session", "gtc"]),
    default=None,
    help="Resting entry: 'session' expires after one regular session (default for 1d); "
    "'gtc' rests until filled and reproduces earlier benchmarks.",
)


def resolve_entry_policy(interval: str, choice: str | None):
    """None means the historical GTC execution policy; otherwise the deployed daily policy."""
    if choice is None:
        choice = "session" if interval == "1d" else "gtc"
    if choice == "gtc":
        return None
    if interval != "1d":
        raise click.ClickException("--entry-policy session requires native daily bars (--interval 1d)")
    return session_entry_policy()
```

Import `session_entry_policy` and `DAILY_SESSION_SEMANTICS_VERSION`. Decorate `alpha_mine_cmd` with `@ENTRY_POLICY_OPTION` (place it after `--min-dsr`), add `entry_policy` to its parameter list, resolve once before the per-symbol loop:

```python
    execution = resolve_entry_policy(interval, entry_policy)
```

and pass `execution=execution` to the `miner.mine` call. Echo the choice once: `click.echo(f"Entry policy: {'session (one regular session)' if execution else 'gtc'}")`.

For `alpha test`, decorate with `@ENTRY_POLICY_OPTION`, add the parameter, and build the definition under the chosen policy:

```python
execution = resolve_entry_policy(interval, entry_policy)
definition = AlphaDefinition(
    "alpha_diagnostic",
    "Diagnostic",
    expression,
    timeframe=interval,
    **({"execution": execution, "semantics_version": DAILY_SESSION_SEMANTICS_VERSION} if execution else {}),
)
```

`scripts/launchd.sh` — in both occurrences of the scheduled command (lines ~103 and ~195) append `--entry-policy gtc` after `--max-seconds 120`. Change nothing else in that script.

- [ ] **Step 4: Run**

Run: `uv run pytest tests/cli/test_cli_alpha.py -q` and `bash -n scripts/launchd.sh`
Expected: all pass; the shell syntax check prints nothing.

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix && uv run ruff format && uv run mypy agentic_trader
git add agentic_trader/cli/commands/alpha.py scripts/launchd.sh tests/cli/test_cli_alpha.py
git commit -m "feat(cli): choose the entry policy when mining; pin the scheduled benchmark to gtc

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Live path regressions for a version-5 signal

No production change is expected. These tests prove the claim "nothing else changes" and become the guard against someone later re-fencing on `semantics_version`.

**Files:**
- Modify: `tests/workflows/test_alpha_admission.py` (append one test)
- Modify: `tests/integration/test_trade_lifetimes.py` (append two tests)
- Modify: `tests/screeners/` formulaic test module (append one test; locate with `grep -rln "FormulaicAlphaStrategy" tests/screeners`)

**Interfaces:**
- Consumes: `session_entry_policy`, `DAILY_SESSION_SEMANTICS_VERSION`, `DAILY_ENTRY_LIFETIME_SECONDS`.

- [ ] **Step 1: Admission accepts an active version-5 alpha**

Append to `tests/workflows/test_alpha_admission.py`, reusing its `alpha_entry` fixture through indirect parametrization (the fixture forwards `request.param` into `AlphaDefinition(**param)` and stamps `alpha_policy=definition.execution.to_dict()`):

```python
@pytest.mark.parametrize(
    "alpha_entry",
    [{"semantics_version": DAILY_SESSION_SEMANTICS_VERSION, "execution": session_entry_policy()}],
    indirect=True,
)
async def test_active_daily_session_entry_alpha_reserves_risk_with_its_exact_policy(store, app_config, alpha_entry):
    _, definition, request = alpha_entry
    assert definition.execution.lifetime.resting_seconds == 57_600
    item, reason = await store.enqueue_entry(request, app_config)
    assert item, reason
```

Add the imports. Run `uv run pytest tests/workflows/test_alpha_admission.py -q` — expected: pass without production changes. If it fails, stop and report the refusal reason verbatim: that would falsify the spec's "every production fence keys on the clock" claim and must be ruled on, not patched around.

- [ ] **Step 2: The screener stamps the timed policy into the candidate**

In the formulaic screener test module, append a test that builds `FormulaicAlphaStrategy(AlphaDefinition(..., timeframe="1d", semantics_version=5, execution=session_entry_policy()))`, evaluates it with that module's existing triggering market-data fixture, and asserts `candidate.alpha_policy == definition.execution.to_dict()` and `candidate.alpha_policy["lifetime"]["resting_seconds"] == 57_600`. Copy the fixture usage from the nearest existing test in that module that asserts `alpha_policy`.

- [ ] **Step 3: The live service cancels an unfilled entry-only order and never schedules a close**

Append to `tests/integration/test_trade_lifetimes.py`:

```python
async def use_daily_entry_policy(c, *, submitted_seconds_ago):
    policy = session_entry_policy().to_dict()
    async with c.db.session_factory() as session, session.begin():
        row = await session.get(SignalRecord, c.signal_id)
        row.alpha_policy, row.timeframe = json.dumps(policy, allow_nan=False), "1d"
    c.venue.entry.update(submitted_at=(datetime.now(UTC) - timedelta(seconds=submitted_seconds_ago)).isoformat())


async def test_daily_entry_only_policy_cancels_after_one_session_and_not_before(lifecycle_case):
    c = lifecycle_case
    await use_daily_entry_policy(c, submitted_seconds_ago=DAILY_ENTRY_LIFETIME_SECONDS - 60)
    await c.services[0].reconcile()
    assert not any(call[0] == "DELETE" for call in c.venue.calls)
    await use_daily_entry_policy(c, submitted_seconds_ago=DAILY_ENTRY_LIFETIME_SECONDS + 1)

    def response(method, path, query, body):
        if method == "DELETE":
            complete_cancel(c)
            return 204, None
        return None

    c.venue.override = response
    await asyncio.gather(*(service.reconcile() for service in c.services))
    cancels = await c.db.workflows.list_work(WorkKind.ENTRY_CANCEL)
    assert len(cancels) == 1 and cancels[0].status == WorkStatus.ACCEPTED
    assert len([call for call in c.venue.calls if call[0] == "DELETE"]) == 1
    assert not any(call[0] in ("POST", "PATCH") for call in c.venue.calls)


async def test_daily_entry_only_policy_never_schedules_a_holding_close(lifecycle_case):
    c = lifecycle_case
    await use_daily_entry_policy(c, submitted_seconds_ago=10 * 86_400)
    filled_position(c)
    c.venue.entry.update(filled_at=(datetime.now(UTC) - timedelta(days=9)).isoformat())
    await asyncio.gather(*(service.reconcile() for service in c.services))
    assert not any(call[0] != "GET" for call in c.venue.calls)
    assert await c.db.get_state(SystemStateKey.TRADING_HALTED) != "true"
```

Add imports for `json`, `SignalRecord`, `session_entry_policy` and `DAILY_ENTRY_LIFETIME_SECONDS` as needed. Run the SQLite parametrization: `uv run pytest tests/integration/test_trade_lifetimes.py -q -k daily_entry`. Then, if PostgreSQL is available: `TEST_POSTGRES_URL=postgresql+asyncpg://localhost/test_trader uv run pytest tests/integration/test_trade_lifetimes.py --run-postgres -q -k daily_entry`; otherwise state that in the report (CI's PostgreSQL job runs it).

As in Step 1, a failure here is evidence about the design. Report it verbatim rather than changing `TradeLifetimeService`.

- [ ] **Step 4: Commit**

```bash
uv run ruff check --fix && uv run ruff format && uv run mypy agentic_trader
git add tests/
git commit -m "test(execution): prove version-5 signals pass admission and expire live after one session

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Power study under the deployed policy

**Files:**
- Modify: `agentic_trader/research/alpha/power_study.py` (`PowerProtocol` ~88-168, `select_power_candidates` ~199-211)
- Modify: `agentic_trader/cli/commands/alpha.py` (`power-plan` ~661-680)
- Test: `tests/research/test_power_entry_policy.py`

**Interfaces:**
- Consumes: `AlphaMiner.mine(..., execution=)`, `session_entry_policy`.
- Produces: `PowerProtocol.entry_policy: Literal["gtc", "session"] = "gtc"`; `copilot alpha power-plan --entry-policy [gtc|session]` (default `gtc`).

- [ ] **Step 1: Write the failing tests**

Create `tests/research/test_power_entry_policy.py`:

```python
import json

import pytest

from agentic_trader.research.alpha.models import DAILY_SESSION_SEMANTICS_VERSION
from agentic_trader.research.alpha.power_study import PowerProtocol, power_jobs, power_seeds, select_power_candidates
from agentic_trader.research.alpha.study import market_bars


SMALL = {
    "observations": 900,
    "development_replicates": 1,
    "null_replicates": 1,
    "edge_replicates": 1,
    "generated_candidates": 2,
}


def test_gtc_protocol_document_and_identity_are_unchanged():
    protocol = PowerProtocol(seed=7, **SMALL)
    document = protocol.document()
    assert "entry_policy" not in document
    assert PowerProtocol.from_document(json.loads(json.dumps(document))) == protocol
    # Pinned on the commit before this change; an added serialized field would move it.
    assert protocol.identity == PINNED_GTC_PROTOCOL_IDENTITY


def test_session_protocol_has_a_distinct_identity_and_round_trips():
    gtc, session = PowerProtocol(seed=7, **SMALL), PowerProtocol(seed=7, entry_policy="session", **SMALL)
    assert session.document()["entry_policy"] == "session"
    assert session.identity != gtc.identity
    assert PowerProtocol.from_document(json.loads(json.dumps(session.document()))) == session


@pytest.mark.parametrize(("entry_policy", "timed"), [("gtc", False), ("session", True)])
def test_selection_mines_the_known_control_and_winner_under_the_declared_policy(entry_policy, timed):
    protocol = PowerProtocol(seed=7, entry_policy=entry_policy, **SMALL)
    job = next(job for job in power_jobs(protocol) if job.effect > 0)
    seeds = power_seeds(job, protocol)
    bars = market_bars(job.scenario, seed=seeds["data"])
    selection = select_power_candidates(bars, protocol, search_seed=seeds[f"search/{job.method}"], method=job.method)
    for route in ("known", "winner"):
        definition = selection["routes"][route]["definition"]
        assert ("lifetime" in definition["execution"]) is timed
        assert (definition["semantics_version"] == DAILY_SESSION_SEMANTICS_VERSION) is timed
```

Compute the pin before implementing and paste it under `SMALL`:

```bash
uv run python -c "from agentic_trader.research.alpha.power_study import PowerProtocol; print(PowerProtocol(seed=7, observations=900, development_replicates=1, null_replicates=1, edge_replicates=1, generated_candidates=2).identity)"
```

```python
PINNED_GTC_PROTOCOL_IDENTITY = "<the 64 hex characters printed above>"
```

Confirm the job attribute names (`job.effect`, `job.scenario`, `job.method`) and the seed role keys with `sed -n 170,198p agentic_trader/research/alpha/power_study.py`; adjust only the accessors.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/research/test_power_entry_policy.py -q`
Expected: `ValidationError` — `entry_policy` is an unexpected field.

- [ ] **Step 3: Implement**

`PowerProtocol` — add the field after `sparse_lower`:

```python
    entry_policy: Literal["gtc", "session"] = "gtc"
```

In `document()`, build the base mapping so the default is omitted (this is what keeps every previously frozen protocol's identity and loadability):

```python
        fields = self.model_dump(mode="json")
        if self.entry_policy == "gtc":
            fields.pop("entry_policy")
        return {
            **fields,
            "contracts": {
```

`from_document` needs no change: a missing key validates to the default, and `digest(protocol.document()) != digest(document)` still guards frozen contracts.

`select_power_candidates` — pass the policy:

```python
        max_seconds=protocol.search_timeout_seconds,
        execution=session_entry_policy() if protocol.entry_policy == "session" else None,
    )
```

Import `session_entry_policy`. Nothing downstream changes: `evaluate_power_job` reads each route's definition, whose execution now carries the lifetime.

`power-plan` CLI — add the option and pass it through:

```python
@click.option("--entry-policy", type=click.Choice(["gtc", "session"]), default="gtc")
```

```python
        protocol = PowerProtocol(
            seed=seed, family_snapshot_hash=snapshot.identity if snapshot else None, entry_policy=entry_policy
        )
```

and after the existing echo: `click.echo(f"Entry policy: {entry_policy}")`.

- [ ] **Step 4: Run**

Run: `uv run pytest tests/research/test_power_entry_policy.py tests/research/test_alpha_study.py tests/cli/test_cli_power.py -q`
Expected: all pass; pre-existing power tests unmodified.

Then a tiny real CLI run to prove the pipeline end to end under the new policy (synthetic only; writes to a temporary directory):

```bash
D=$(mktemp -d) && uv run python - <<EOF
import json
from pathlib import Path
from agentic_trader.research.alpha.power_study import PowerProtocol
p = PowerProtocol(seed=3, entry_policy="session", observations=900, development_replicates=1, null_replicates=1, edge_replicates=1, generated_candidates=2)
Path("$D/protocol.json").write_text(json.dumps(p.document()))
EOF
uv run copilot alpha power-study "$D/protocol.json" --output "$D/run"
```

Expected: `Power study completed; 16/16 searches retained.` (or `incomplete` only if current-family endpoints are unavailable because no family snapshot was supplied — both outcomes are acceptable here; record which in the report).

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix && uv run ruff format && uv run mypy agentic_trader
git add agentic_trader/research/alpha/power_study.py agentic_trader/cli/commands/alpha.py tests/research/test_power_entry_policy.py
git commit -m "feat(research): run the power study under the session-bounded entry policy

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Documentation

**Files:**
- Modify: `docs/alpha-trade-lifetimes.md`, `docs/alpha-pipeline.md`, `docs/cli-reference.md`, `docs/alpha-roadmap.md`, `docs/alpha-lifetime-attribution-plan.md`, `docs/alpha-lifetime-attribution-2026-09-21.md`, `CLAUDE.md`, `AGENTS.md`

- [ ] **Step 1: Write the docs**

- `docs/alpha-trade-lifetimes.md`: new section "Native-daily one-session entries (semantics version 5)" containing the contract bullets and the full "Why exactly 57,600 seconds" paragraph from the spec, plus: "After this change, 'timed execution is diagnostic-only' is enforced solely by the version rules in `research/alpha/models.py`: versions 3 and 4 remain diagnostic; version 5 is production-eligible because it has no clock and every production fence keys on the clock."
- `docs/alpha-pipeline.md`: in the execution/identity section, state that mined daily candidates default to version 5 and that a timed policy is a new immutable identity requiring fresh research, holdout and qualification.
- `docs/cli-reference.md`: document `--entry-policy [session|gtc]` on `alpha mine` and `alpha test`, and `--entry-policy` on `alpha power-plan`.
- `docs/alpha-roadmap.md`, priority 2: record that the mechanistic lifetime run is complete, link the result, state that version 5 and policy-aware mining are implemented, and that the next action is freezing and running the confirmatory `power-plan --entry-policy session` protocol with a fresh seed and newly sourced family snapshot. Add: "Paper probes should not be enrolled under GTC entries; enrol version-5 candidates."
- `docs/alpha-lifetime-attribution-plan.md`: status line — the power-study confirmation supersedes the unimplemented P2 endpoint; P2 is not supported by the mechanistic result.
- `docs/alpha-lifetime-attribution-2026-09-21.md`, "Next steps" item 1: replace "This requires wiring holdout measurement and family assessment into the lifetime harness" with "Run it through the existing power study (`alpha power-plan --entry-policy session`), which already measures that endpoint."
- `CLAUDE.md` and `AGENTS.md`: in the "Timed alpha execution" section add: "Semantics version 5 is the production native-daily contract: no clock, exactly `DAILY_ENTRY_LIFETIME_SECONDS = 57,600` resting and no holding deadline, so research (bar label) and live (`submitted_at`) both mean one regular session. Never relax it to a configurable value, add a holding deadline to it, or re-fence it on `semantics_version`; the miner's `--entry-policy session` default and the scheduled `--entry-policy gtc` pin are deliberate."

- [ ] **Step 2: Full verification**

```bash
uv run pytest
uv run mypy agentic_trader
uv run pre-commit run --all-files
```

Expected: full suite green, no new mypy errors, pre-commit clean.

- [ ] **Step 3: Commit**

```bash
git add docs/ CLAUDE.md AGENTS.md
git commit -m "docs: record the native-daily one-session entry contract and next confirmation step

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## After this plan (operator-approved, not part of these tasks)

1. Capture a fresh read-only family snapshot and freeze the confirmation: `copilot alpha power-plan --seed <fresh> --family-snapshot SNAPSHOT.json --entry-policy session --output PROTOCOL.json`, then `copilot alpha power-study PROTOCOL.json --family-snapshot SNAPSHOT.json --output NEW_PRIVATE_DIRECTORY`. Record the result as a dated doc; do not change gates based on it.
2. Deployment of the mining default and the `launchd.sh` pin follows the normal procedure: pause watchdog then daemon before updating the installed checkout.
