# Monitor Quiet Hours Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Outside weekday 09:15–16:30 New York, `doctor --monitor` withholds ordinary incident notices, journals
what it withheld, and sends one summary at the next alert-window start. Broker-work checks still page at any hour.

**Architecture:**
- A pure calendar module (`diagnostics/quiet_hours.py`) decides whether an instant is in an alert window and which
  quiet period preceded it.
- The incident state machine (`diagnostics/incidents.py:advance`) gains two inputs, `muted` and `alert_since`, and
  returns which notice it withheld and why. `OperationsStore.observe` journals that withheld notice.
- A new store method builds the summary from the journal, and a new notification kind delivers it through the
  existing outbox.

**Tech Stack:** Python 3.14, pydantic v2, SQLAlchemy async (SQLite in tests, PostgreSQL in production), `zoneinfo`,
pytest with asyncio auto mode.

**Spec:** `docs/superpowers/specs/2026-11-03-quiet-hours-design.md`

## Global Constraints

- No migration: schema head stays `008_alpha_pipeline`. The incident event `schema_version` stays 1, and the new
  payload field is additive.
- Code default `operations.quiet_hours.enabled = false`, and the shipped `config/config.yaml` sets it to `true`.
  Defaults are `alert_start_et 09:15`, `alert_end_et 16:30` and
  `exempt_components [entry_recovery, entry_cancellation, close_recovery]`.
- Times are New York wall-clock through `zoneinfo.ZoneInfo("America/New_York")`. Alert days are Monday–Friday
  that are not `MarketHolidayCalendar.is_equity_holiday`. The window is `[alert_start_et, alert_end_et)`.
- Telegram text carries component and type names only: no raw logs, chat IDs or secrets. HTML-escape every
  component and detail.
- Do not change readiness limits, the debounce, recovery hysteresis, startup grace, reminder delivery rule, outbox
  retry semantics or `rebuild()`.
- No compatibility aliases: update every caller of a changed signature in the same task.
- Tests use the fixture config (`tests/fixtures/config.yaml`, quiet hours off) and the `temp_db` SQLite fixture.
  Never touch the installed checkout or the production database.
- Run `uv run pytest tests/operations tests/config tests/presentation tests/notifier tests/workflows -q` and
  `uv run pre-commit run --all-files` before reporting. Ruff is strict (C408, PLC0415): use no `dict()` calls and
  no function-level imports.
- Implementers stage only (`git add`); the controller commits.

## Review Focus

1. **Timestamps exactly on a window edge.** 09:15:00 ET is alert time and 16:30:00 ET is quiet; pinned in Task 1.
2. **DST weeks.** The window follows New York wall-clock on both sides of 2026-11-01. A quiet period that spans the
   change (Fri 10-30 16:30 EDT → Mon 11-02 09:15 EST) has the right UTC bounds; pinned in Task 1.
3. **A misspelled exempt component.** It must fail config load rather than mute a broker-work check; pinned in
   Task 1.
4. **A summary-building failure.** It must not stop the component observations in the same pass; pinned in
   Task 3.
5. **A held incident that clears in the first two minutes of the window.** It must send neither `opened` nor
   `recovered`; pinned in Task 2.

---

### Task 1: Quiet-hours config and calendar

**Files:**
- Modify: `agentic_trader/config.py`. Add `READINESS_CHECK_NAMES` and `QuietHoursConfig` above `OperationsConfig`
  (line 664), and add the `quiet_hours` field to `OperationsConfig`.
- Create: `agentic_trader/diagnostics/quiet_hours.py`
- Test: `tests/operations/test_quiet_hours.py`, `tests/config/test_quiet_hours_config.py`

**Interfaces:**
- Produces:
  - `READINESS_CHECK_NAMES: frozenset[str]`, defined in `agentic_trader/config.py`. Its value is exactly the
    `HealthComponent` values (`reconciliation, worker, scan, telegram, delivery, accounting, alpha_observer,
    alpha_decisions, alpha_daily_panel, card_stats, corporate_actions, insider_capture`), plus `daemon_started,
    alpha_registry, entry_recovery, entry_cancellation, outbox, close_recovery, trading_halt, broker_stream,
    macro_calendar, event_loop, storage, endpoint`. Spell these out as string literals: `config.py` must not import
    `diagnostics`.
  - `class QuietHoursConfig(BaseModel)` with these fields:
    - `enabled: bool = False`
    - `alert_start_et: time = time(9, 15)`
    - `alert_end_et: time = time(16, 30)`
    - `exempt_components: list[str] = ["entry_recovery", "entry_cancellation", "close_recovery"]`

    Its `model_validator(mode="after")` raises `ValueError` when `alert_start_et >= alert_end_et`. It also raises
    `ValueError` naming each exempt entry not in `READINESS_CHECK_NAMES`.
  - `OperationsConfig.quiet_hours: QuietHoursConfig = Field(default_factory=QuietHoursConfig)`
  - In `agentic_trader/diagnostics/quiet_hours.py`:
    - `def is_alert_day(day: date) -> bool`
    - `def alert_since(at: datetime, policy: QuietHoursConfig) -> datetime | None` returns the UTC-aware start of
      the alert window containing `at`. It returns `None` when `policy.enabled` is false or `at` is quiet time.
    - `def is_muted(component: str, at: datetime, policy: QuietHoursConfig) -> bool` is
      `policy.enabled and component not in policy.exempt_components and alert_since(at, policy) is None`.
    - `def quiet_period_before(window_start: datetime, policy: QuietHoursConfig) -> tuple[datetime, datetime]`
      returns `(start, window_start)`, both UTC-aware. `start` is `alert_end_et` on the nearest earlier alert day,
      searching back at most 14 days, and raises `ValueError` if none is found. It ignores `policy.enabled`.
    - Every function given a naive `datetime` raises `ValueError("timezone-aware datetime required")`.

- [ ] **Step 1: Write the failing calendar tests** in `tests/operations/test_quiet_hours.py`, using
  `on = QuietHoursConfig(enabled=True)`. Use these test names and assertions:
  - `test_alert_window_edges_in_est`:
    - `alert_since(datetime(2026, 11, 3, 14, 15, tzinfo=UTC), on) == datetime(2026, 11, 3, 14, 15, tzinfo=UTC)`
    - at `14:14:59` the result is `None`
    - at `21:29:59` it equals `datetime(2026, 11, 3, 14, 15, tzinfo=UTC)`
    - at `21:30:00` it is `None`
  - `test_alert_window_follows_new_york_wall_clock_across_dst`:
    - `alert_since(datetime(2026, 10, 30, 13, 15, tzinfo=UTC), on) == datetime(2026, 10, 30, 13, 15, tzinfo=UTC)`
    - `alert_since(datetime(2026, 11, 2, 13, 15, tzinfo=UTC), on) is None`, because that is 08:15 EST
  - `test_weekends_and_holidays_are_quiet`: `None` for `datetime(2026, 11, 7, 15, 0, tzinfo=UTC)` (Saturday) and for
    `datetime(2026, 11, 26, 15, 0, tzinfo=UTC)` (Thanksgiving).
  - `test_disabled_policy_has_no_alert_window_and_mutes_nothing`:
    - `alert_since(datetime(2026, 11, 3, 15, tzinfo=UTC), QuietHoursConfig()) is None`
    - `is_muted("scan", datetime(2026, 11, 3, 8, tzinfo=UTC), QuietHoursConfig()) is False`
  - `test_is_muted_exempts_broker_work`, at `night = datetime(2026, 11, 3, 8, 0, tzinfo=UTC)`:
    - `is_muted("scan", night, on)` is True
    - `is_muted("entry_recovery", night, on)` is False
    - `is_muted("scan", datetime(2026, 11, 3, 15, tzinfo=UTC), on)` is False
  - `test_quiet_period_spans_the_weekend_and_dst`:
    `quiet_period_before(datetime(2026, 11, 2, 14, 15, tzinfo=UTC), on) == (datetime(2026, 10, 30, 20, 30, tzinfo=UTC), datetime(2026, 11, 2, 14, 15, tzinfo=UTC))`
  - `test_quiet_period_skips_a_holiday`:
    `quiet_period_before(datetime(2026, 11, 27, 14, 15, tzinfo=UTC), on)[0] == datetime(2026, 11, 25, 21, 30, tzinfo=UTC)`
  - `test_naive_datetimes_are_rejected`: `pytest.raises(ValueError, match="timezone-aware")` for each of
    `alert_since`, `is_muted` and `quiet_period_before` given `datetime(2026, 11, 3, 15, 0)`.

- [ ] **Step 2: Write the failing config tests** in `tests/config/test_quiet_hours_config.py`:
  - `test_window_must_start_before_it_ends`: `QuietHoursConfig(alert_start_et="16:30", alert_end_et="09:15")`
    raises `ValidationError`, and so do equal times.
  - `test_unknown_exempt_component_fails_load`: `QuietHoursConfig(exempt_components=["entry_recovry"])` raises
    `ValidationError` whose message contains `entry_recovry`.
  - `test_yaml_strings_parse`: `OperationsConfig(quiet_hours={"enabled": True, "alert_start_et": "09:00", "exempt_components": ["endpoint", "trading_halt"]})`
    gives `quiet_hours.alert_start_et == time(9, 0)`.
  - `test_check_names_cover_readiness`:
    - `{c.value for c in HealthComponent} <= READINESS_CHECK_NAMES`.
    - Build a `ReadinessService` (`agentic_trader/diagnostics/readiness.py`) over `temp_db.workflows` with every
      optional source enabled:
      - flags `accounting_enabled`, `card_stats_enabled`, `corporate_actions_enabled`, `insider_capture_enabled`;
      - `alpha_registry_report` set to an async function returning `{"ready": True}`;
      - `stream_connected=lambda: True`;
      - `macro_calendar=lambda now: {"ready": True}`;
      - a config from `app_config.model_copy(deep=True)` with `telegram_bot_token`/`telegram_chat_id` set to dummy
        strings and `alpha_pipeline.observations/decisions/daily_panel.enabled = True`.
    - Assert `set((await service.report())["checks"]) | {"endpoint", "storage"} == READINESS_CHECK_NAMES`.

- [ ] **Step 3: Run the tests to confirm they fail**

Run: `uv run pytest tests/operations/test_quiet_hours.py tests/config/test_quiet_hours_config.py -q`
Expected: FAIL. Collection errors on the missing imports `quiet_hours` and `QuietHoursConfig`.

- [ ] **Step 4: Implement** `READINESS_CHECK_NAMES`, `QuietHoursConfig` and the `OperationsConfig.quiet_hours`
  field in `agentic_trader/config.py`, and the four functions in `agentic_trader/diagnostics/quiet_hours.py`.
  - **Window bounds:** build each in New York with `datetime.combine(day, policy.alert_start_et, tzinfo=NY)`, then
    `.astimezone(UTC)`.
  - **Alert days:** `day.weekday() < 5 and not MarketHolidayCalendar.is_equity_holiday(day)[0]`.

- [ ] **Step 5: Run the tests to confirm they pass**

Run: `uv run pytest tests/operations/test_quiet_hours.py tests/config/test_quiet_hours_config.py tests/config -q`
Expected: PASS.

- [ ] **Step 6: Stage**

```bash
git add agentic_trader/config.py agentic_trader/diagnostics/quiet_hours.py tests/operations/test_quiet_hours.py tests/config/test_quiet_hours_config.py
```

---

### Task 2: Withheld notices in the state machine, store and monitor

**Files:**
- Modify: `agentic_trader/diagnostics/incidents.py` (`advance`, plus the new types)
- Modify: `agentic_trader/storage/operations.py:19-74` (`observe`)
- Modify: `agentic_trader/diagnostics/monitor.py` (`observe`)
- Test: `tests/operations/test_incidents.py` (update the two-tuple unpacking and add tests),
  `tests/operations/test_operations_store.py`, `tests/operations/test_probe.py`

**Interfaces:**
- Consumes: from Task 1, `QuietHoursConfig`, `OperationsConfig.quiet_hours`, `alert_since` and `is_muted`.
- Produces:
  - `class WithheldReason(StrEnum)` with `QUIET_HOURS = "quiet_hours"`,
    `NOTIFICATIONS_DISABLED = "notifications_disabled"` and `NOT_ANNOUNCED = "not_announced"`.
  - `class WithheldNotice(BaseModel)` with `kind: NoticeKind` and `reason: WithheldReason`.
  - `advance(state, *, ready, observed_at, policy, delivery_complete, muted: bool = False, alert_since: datetime | None = None) -> tuple[IncidentState, NoticeKind | None, WithheldNotice | None]`
  - `OperationsStore.observe(component, *, ready, observed_at, detail, policy, muted: bool = False, alert_since: datetime | None = None) -> OperationalNotice | None`.
    When the state changes, the `incident_changed` payload gains
    `"withheld": withheld.model_dump(mode="json") if withheld else None`.
  - `agentic_trader/diagnostics/monitor.py` gains a module-level function
    `incident_detail(check: Mapping[str, Any]) -> str`: the existing detail-building lines, moved out of the loop
    unchanged and truncated to `MAX_INCIDENT_DETAIL`. Task 3 reuses it.

`advance` keeps every phase transition as it is today. Only the notice decision changes:

```python
# unannounced OPEN branch (replaces `elif state.phase == OPEN and state.last_notified_at is None: notice = OPENED`)
elif state.phase == IncidentPhase.OPEN and state.last_notified_at is None:
    since = max(state.failed_since, alert_since) if alert_since else state.failed_since
    if (observed_at - since).total_seconds() >= policy.failure_seconds:
        notice = NoticeKind.OPENED
...
withheld = None
if notice:
    reason = (
        WithheldReason.QUIET_HOURS if muted
        else WithheldReason.NOTIFICATIONS_DISABLED if not policy.notifications_enabled
        else WithheldReason.NOT_ANNOUNCED if notice == NoticeKind.RECOVERED and state.last_notified_at is None
        else None
    )
    if reason:
        withheld, notice = WithheldNotice(kind=notice, reason=reason), None
if notice:
    state.last_notified_at = observed_at
return state, notice, withheld
```

- [ ] **Step 1: Update the existing tests and write the failing state-machine tests.**
  - In `tests/operations/test_incidents.py`, change every `state, notice = advance(...)` to
    `state, notice, _ = advance(...)`.
  - Add these tests, with `epoch = datetime(2026, 9, 16, tzinfo=UTC)` and the file's `policy` fixture
    (`failure_seconds=10, recovery_seconds=5, reminder_seconds=100`):
    - `test_muted_opening_is_withheld_and_unannounced`: advance `(0, False)` then `(10, False)`, both with
      `muted=True`.
      - The second returns `notice is None` and
        `withheld == WithheldNotice(kind="opened", reason="quiet_hours")`.
      - Afterwards `state.phase == "open"` and `state.last_notified_at is None`.
    - `test_held_incident_announces_after_failure_seconds_of_alert_time`:
      - Start from the open, unannounced state of the previous test, and let `since = epoch + timedelta(seconds=1000)`.
      - Advancing `ready=False` at `since + 9s` with `alert_since=since` gives `(None, None)` for (notice, withheld).
      - At `since + 10s` it gives `notice == "opened"`.
    - `test_unannounced_open_without_alert_since_announces_immediately`: the same unannounced state advanced at
      `epoch + 11s` with defaults gives `notice == "opened"`. This is the existing re-enable behaviour.
    - `test_unannounced_recovery_is_silent`:
      - From the unannounced open state, advance `ready=True` at `20s` and again at `25s`.
      - The second returns `notice is None`, `withheld == WithheldNotice(kind="recovered", reason="not_announced")`
        and `state.phase == "healthy"`.
    - `test_announced_recovery_during_quiet_hours_is_withheld`:
      - Open normally: `(0, False)` and `(10, False)` unmuted give `opened`.
      - Then `(11, True)` and `(16, True)` with `muted=True`.
      - The last gives `withheld == WithheldNotice(kind="recovered", reason="quiet_hours")`.
    - `test_quiet_hours_take_precedence_over_disabled_notifications`: with
      `policy.notifications_enabled = False` and `muted=True`, the opening's `withheld.reason == "quiet_hours"`.
    - `test_muted_reminder_keeps_last_notified_at`:
      - Open normally at `10s` (notice sent, so `last_notified_at = epoch + 10s`).
      - Advance `(200, False)` muted with `delivery_complete=True`. It gives `notice is None`,
        `withheld.kind == "reminder"` and `state.last_notified_at == epoch + timedelta(seconds=10)`.

- [ ] **Step 2: Write the failing store and monitor tests.**
  - **In `tests/operations/test_operations_store.py`** (`desk` fixture):
    - `test_muted_opening_journals_withheld_and_queues_nothing`:
      - Observe `"scan"` at `now` and `now + 10s`, both `ready=False` and `muted=True`.
      - `list_work(WorkKind.NOTIFICATION)` is empty.
      - The last `incident/scan` event payload has `withheld == {"kind": "opened", "reason": "quiet_hours"}`.
      - `await store.rebuild()` leaves `incidents()` unchanged.
    - `test_unmuted_transition_journals_null_withheld`: a normal opening's event payload has `withheld is None`.
    - `test_reenabled_notifications_do_not_send_recovery_for_unannounced_incident` (the documented behaviour
      change):
      - Open with `notifications_enabled = False` at `0s` and `10s`.
      - Re-enable, then observe `ready=True` at `11s` and `16s`.
      - No notification work item exists.
  - **In `tests/operations/test_probe.py`**, use
    `policy = OperationsConfig(failure_seconds=10, quiet_hours=QuietHoursConfig(enabled=True))` and reports with no
    `started_at` (so there is no endpoint or grace):
    - `test_quiet_hours_mute_ordinary_components_but_not_broker_work`:
      - At `night = datetime(2026, 11, 3, 8, 0, tzinfo=UTC)` and `night + 10s`, run `monitor.observe` with
        `{"checked_at": ..., "checks": {"scan": {"ready": False}, "entry_recovery": {"ready": False, "unresolved": 1}}}`.
      - It returns exactly one notice, with `component == "entry_recovery"`.
    - `test_held_incident_pages_two_minutes_into_the_window`:
      - With `failure_seconds=120`, open `scan` while quiet at `08:00Z` and `08:02Z`.
      - Observing failing at `14:15Z` returns `[]`.
      - At `14:17Z` it returns one `opened` notice for `scan`.
    - `test_held_incident_that_clears_early_in_the_window_stays_silent`:
      - Same quiet opening as the previous test.
      - Observe `scan` failing at `14:15Z`, then ready at `14:16Z` and `14:17Z`.
      - All three passes return `[]`, no notification work item exists, and the projection phase is `healthy`.

- [ ] **Step 3: Run the tests to confirm they fail**

Run: `uv run pytest tests/operations -q`
Expected: FAIL. Unpacking or an unknown `muted` keyword fails, and `WithheldNotice` is not importable.

- [ ] **Step 4: Implement**
  - Put `WithheldReason`, `WithheldNotice` and the new `advance` in `incidents.py`, following the block above.
  - Thread `muted` and `alert_since` through `OperationsStore.observe`, and add the payload `withheld` key.
  - In `OperationsMonitor.observe`, compute once per pass
    `since = alert_since(observed_at, self.policy.quiet_hours)`. For each component, pass
    `muted=is_muted(component, observed_at, self.policy.quiet_hours)` and `alert_since=since`. Use
    `incident_detail(check)` for `detail`.

- [ ] **Step 5: Run the tests to confirm they pass**

Run: `uv run pytest tests/operations tests/integration/test_operations_http.py -q`
Expected: PASS. The PostgreSQL-marked tests are skipped without `--run-postgres`.

- [ ] **Step 6: Stage**

```bash
git add agentic_trader/diagnostics/incidents.py agentic_trader/storage/operations.py agentic_trader/diagnostics/monitor.py tests/operations
```

---

### Task 3: Morning summary, delivery, shipped config and docs

**Files:**
- Modify: `agentic_trader/diagnostics/incidents.py`, adding the summary models
- Modify: `agentic_trader/execution/durable.py:123-130`, adding
  `NotificationKind.OPERATIONS_SUMMARY = "operations_summary"`
- Modify: `agentic_trader/storage/operations.py`, adding `queue_quiet_summary`
- Modify: `agentic_trader/diagnostics/monitor.py`, calling it before the component loop
- Modify: `agentic_trader/notifier/outbox.py:70-84`, dispatching the new kind
- Modify: `agentic_trader/presentation/operations.py`, adding `format_quiet_hours_summary`
- Modify: `config/config.yaml`, adding a top-level `operations:` block with `quiet_hours: {enabled: true}`
- Modify: `docs/operational-monitoring.md`, `docs/production.md`, `CLAUDE.md` (contract 8)
- Test: `tests/operations/test_operations_store.py`, `tests/operations/test_probe.py`,
  `tests/presentation/test_quiet_hours_summary.py` (new), `tests/notifier/test_operations_summary_delivery.py`
  (new), `tests/config/test_shipped_config.py`

**Interfaces:**
- Consumes:
  - from Task 1, `quiet_period_before` and `alert_since`;
  - from Task 2, `WithheldReason.QUIET_HOURS`, the payload `withheld` key and `incident_detail`.
- Produces:
  - In `incidents.py`:
    - `class ComponentTally(BaseModel)` with `component: str`, `opened: int`, `recovered: int`.
    - `class OpenIncident(BaseModel)` with `component: str`, `phase: IncidentPhase`, `failed_since: datetime`,
      `detail: str`.
    - `class QuietHoursSummary(BaseModel)` with `period_start: datetime`, `period_end: datetime`,
      `observed_at: datetime`, `failure_seconds: float`, `tallies: list[ComponentTally]` and
      `open_incidents: list[OpenIncident]`.
  - `OperationsStore.queue_quiet_summary(*, period_start: datetime, period_end: datetime, observed_at: datetime, details: Mapping[str, str], policy: OperationsConfig) -> QuietHoursSummary | None`.
    It runs in one transaction under `self.store.lock(session)`:
    1. Return `None` if a notification work item with dedup key
       `f"operations-summary/{period_end.astimezone(NY).date().isoformat()}"` exists.
    2. Select `incident_changed` events with
       `DomainEventRecord.stream >= "incident/", DomainEventRecord.stream < "incident0"` and `recorded_at` in
       `[period_start − 1 h, period_end + 1 h]`. Keep those whose payload `withheld.reason == "quiet_hours"` and
       whose `period_start <= observed_at < period_end`.
    3. Tally `opened` and `recovered` per component. Sort by `(-(opened + recovered), component)`.
    4. Take open incidents from projections whose phase is `open` or `recovering`. Each one's detail is
       `details.get(component, "")[:120]`. Sort with `trading_halt` first, then by `(failed_since, component)`.
    5. If both lists are empty, return `None`.
    6. Otherwise call `add_notification(session, key, NotificationKind.OPERATIONS_SUMMARY, summary.model_dump(mode="json"))`
       and return the summary, with `failure_seconds = policy.failure_seconds`.
  - `format_quiet_hours_summary(summary: QuietHoursSummary) -> str` in `presentation/operations.py`. New York
    times use the format `%a %H:%M`.

The rendered copy is fixed. Lines that are conditional are marked:

```
<b>{APP_DISPLAY_NAME}: Overnight operations summary</b>
Quiet hours {start} → {end} ET: {A} alert(s) and {R} recover(y/ies) held across {C} component(s).   # A = Σopened, R = Σrecovered, C = len(tallies)
Quiet hours {start} → {end} ET: no alerts held.                                                     # instead, when tallies is empty
• {component}: {o} opened, {r} recovered          # one per tally, first 12; drop a zero part ("1 opened"; "2 recovered")
… +{n} more components                             # only when len(tallies) > 12
Still open:                                        # only when open_incidents
• {component} ({phase} since {failed_since} ET): {detail}     # first 10; omit ": {detail}" when detail is empty
… +{n} more                                        # only when len(open_incidents) > 10
Each incident still failing {F} into alert hours sends its own alert.        # only when open_incidents; F = "2 min" when failure_seconds % 60 == 0 else f"{failure_seconds:g} s"
Incidents that open and recover overnight usually mean the host slept (<code>pmset -g log</code>).   # only when tallies
Inspect <code>copilot doctor --readiness</code> and <code>copilot db incidents</code>.
```

In `OperationsMonitor.observe`, do the following before the component loop:
- Build `details = {name: incident_detail(check) for name, check in checks.items()}`.
- Call the summary only when `self.policy.notifications_enabled` and `since` is not `None`.
- Wrap the call in `try`/`except Exception`, logging with `logging.getLogger(__name__).warning("quiet_summary_failed: %s", type(exc).__name__)`
  and continuing. This is a deliberate fail-open exception for the summary only: the component observations
  that follow must still run.

- [ ] **Step 1: Write the failing store tests** in `tests/operations/test_operations_store.py`. Use
  `start = datetime(2026, 11, 2, 21, 30, tzinfo=UTC)`, `end = datetime(2026, 11, 3, 14, 15, tzinfo=UTC)` and a
  helper that drives `store.observe(..., muted=True)` through a full open/recover cycle at given instants.
  - `test_summary_tallies_withheld_notices_and_lists_open_incidents`:
    - Inside the period: `accounting` goes through two full withheld cycles, and `scan` opens (withheld) and stays
      open.
    - With `details={"scan": "Observation age: 40000s; limit: 18000s"}`, the summary has
      `tallies == [ComponentTally(component="accounting", opened=2, recovered=2), ComponentTally(component="scan", opened=1, recovered=0)]`.
    - `[i.component for i in summary.open_incidents] == ["scan"]`, and that incident's detail is the given string.
    - Exactly one work item has payload kind `operations_summary` and dedup key `operations-summary/2026-11-03`.
  - `test_summary_is_queued_once_per_window`: a second call returns `None` and the work-item count is unchanged.
  - `test_summary_ignores_events_outside_the_period_and_other_reasons`:
    - Withheld cycles at `start − 1 s` and at `end` are not counted.
    - A `not_announced` recovery inside the period is not counted. Build it by opening with
      `policy.notifications_enabled = False`, then re-enabling and recovering unmuted.
    - Nothing else is open, so the result is `None` and no work item exists.
  - `test_summary_lists_trading_halt_first`: `scan` opens at `start + 1 h` and `trading_halt` at `start + 2 h`, both
    still open, so the order is `["trading_halt", "scan"]`.

- [ ] **Step 2: Write the failing monitor, formatter, delivery and shipped-config tests.**
  - **`tests/operations/test_probe.py`, `test_summary_precedes_the_first_alert_of_the_window`:**
    - Use `failure_seconds=120` and quiet hours on. `scan` opens while quiet at `08:00Z`/`08:02Z`.
    - Passes at `14:15Z` and `14:17Z` queue work items ordered by sequence: first the
      `operations_summary` item, then the `operational` `opened` item for `scan`.
  - **`tests/operations/test_probe.py`, `test_summary_failure_does_not_stop_observations`:**
    - Monkeypatch `store.queue_quiet_summary` to raise `RuntimeError`.
    - A pass at `14:17Z` for the held `scan` incident still returns its `opened` notice.
  - **`tests/presentation/test_quiet_hours_summary.py`:**
    - `test_summary_copy` asserts the exact full string for: `period_start = 2026-11-02T21:30Z`,
      `period_end = 2026-11-03T14:15Z`, `failure_seconds = 120`, tallies `accounting 2/2` and `scan 1/0`, and one
      open `scan` incident with `failed_since = 2026-11-03T03:20Z` and detail `"age <limit>"`.
    - That string contains these lines exactly:
      - `Quiet hours Mon 16:30 → Tue 09:15 ET: 3 alerts and 2 recoveries held across 2 components.`
      - `• scan: 1 opened`
      - `• scan (open since Mon 22:20 ET): age &lt;limit&gt;`
      - `Each incident still failing 2 min into alert hours sends its own alert.`
    - `test_summary_caps_long_lists`: with 13 tallies and 11 open incidents, the text contains
      `… +1 more components` and `… +1 more`, and has 12 tally bullets and 10 open bullets.
    - `test_empty_tallies_copy`: with no tallies and one open incident, it contains `no alerts held.` and no
      `pmset` line.
  - **`tests/notifier/test_operations_summary_delivery.py`, `test_dispatcher_renders_operations_summary`:**
    - Enqueue an `operations_summary` notification on `temp_db.workflows`.
    - Run `NotificationDispatcher(...).dispatch_one()` with a fake notifier whose `send_message` records its text.
      Follow the dispatcher construction in `tests/notifier/test_card_expiry_delivery.py`.
    - The recorded text equals `format_quiet_hours_summary(summary)`.
  - **`tests/config/test_shipped_config.py`, `test_shipped_quiet_hours_reach_app_config`:**
    - `load_config(str(SHIPPED), environ=PRODUCTION).operations.quiet_hours.enabled is True`
    - `"enabled" in ...quiet_hours.model_fields_set`

- [ ] **Step 3: Run the tests to confirm they fail**

Run: `uv run pytest tests/operations tests/presentation/test_quiet_hours_summary.py tests/notifier/test_operations_summary_delivery.py tests/config/test_shipped_config.py -q`
Expected: FAIL, from missing names and the missing YAML block.

- [ ] **Step 4: Implement** the models, `NotificationKind.OPERATIONS_SUMMARY`, `queue_quiet_summary`, the monitor
  call, the outbox branch
  (`return await self.notifier.send_message(format_quiet_hours_summary(QuietHoursSummary.model_validate(args)))`),
  the formatter, and the `config/config.yaml` block:

  ```yaml
  operations:
    quiet_hours:
      enabled: true   # weekdays 09:15-16:30 NY alert; keep aligned with scripts/awake.sh
  ```

- [ ] **Step 5: Docs.**
  - In `docs/operational-monitoring.md`:
    - Add a `### Quiet hours` section under "Incident lifecycle and delivery" covering the alert window, exempt
      components, the `withheld` payload and its three reasons, the `failure_seconds` announcement rule, silent
      unannounced recoveries (the behaviour change for re-enabled notifications), and the summary with its dedup
      key and fail-open build.
    - Add four rows to the configuration table: `quiet_hours.enabled`, `.alert_start_et`, `.alert_end_et` and
      `.exempt_components`.
  - In `docs/production.md`, next to the awake agent, add one line saying the alert window should stay aligned
    with `scripts/awake.sh`.
  - In `CLAUDE.md` contract 8, after "Degraded readiness alerts; it does not blindly restart or automatically change
    the trading halt.", add: "Outside `operations.quiet_hours` (weekdays 09:15–16:30 ET) ordinary incident notices
    are withheld and journaled (`withheld`), then summarized once at the next window start (`operations-summary/{et_date}`);
    `entry_recovery`, `entry_cancellation` and `close_recovery` page at any hour."

- [ ] **Step 6: Run the full affected suite and hooks**

Run: `uv run pytest tests/operations tests/config tests/presentation tests/notifier tests/workflows tests/integration/test_operations_http.py -q && uv run pre-commit run --all-files`
Expected: PASS, and every hook passes.

- [ ] **Step 7: Stage**

```bash
git add agentic_trader config/config.yaml docs/operational-monitoring.md docs/production.md CLAUDE.md tests
```
