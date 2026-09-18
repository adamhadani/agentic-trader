from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from agentic_trader.accounting.ledger import AccountSnapshot, reconcile
from agentic_trader.accounting.risk import advance_risk_checkpoint, require_risk_checkpoint
from agentic_trader.storage.ledger import LedgerStore


NOW = datetime(2026, 9, 18, 12, tzinfo=UTC)


@pytest.fixture
def checkpoint_factory():
    def make(*, gain="0", transfers=(), observed_at=NOW, previous=None, initial_kind="CSD"):
        activities = [
            {"id": "initial", "activity_type": initial_kind, "net_amount": "10000"},
            *transfers,
        ]
        # A fully closed gain/loss is represented by a supported cash income/fee.
        if Decimal(gain):
            activities.append({"id": "performance", "activity_type": "INT", "net_amount": gain})
        cash = sum((Decimal(a["net_amount"]) for a in activities), Decimal(0))
        snapshot = AccountSnapshot(account_id="account", cash=cash, positions={}, observed_at=observed_at)
        report = reconcile(activities, snapshot)
        payload = {"snapshot": snapshot.model_dump(mode="json"), "report": report.model_dump(mode="json")}
        payload.update(
            advance_risk_checkpoint(previous or {}, payload, activities, account_id="account", now=observed_at)
        )
        return payload

    return make


@pytest.mark.parametrize("amount,kind", [("5000", "CSD"), ("-5000", "CSW")])
def test_transfers_do_not_change_existing_drawdown(checkpoint_factory, amount, kind):
    baseline = checkpoint_factory()
    loss = checkpoint_factory(previous=baseline, gain="-500", observed_at=NOW + timedelta(seconds=1))
    transferred = checkpoint_factory(
        previous=loss,
        gain="-500",
        transfers=({"id": "transfer", "activity_type": kind, "net_amount": amount},),
        observed_at=NOW + timedelta(seconds=2),
    )
    risk = require_risk_checkpoint(transferred, max_age_seconds=60, now=NOW + timedelta(seconds=3))
    assert risk.drawdown_pct == Decimal(".05")
    assert risk.adjusted_equity == 9500 and risk.high_water_mark == 10000
    assert risk.baseline_observed_at == NOW and risk.baseline_equity == 10000
    assert risk.equity == Decimal(9500) + Decimal(amount)


def test_new_high_and_restart_identity(checkpoint_factory):
    baseline = checkpoint_factory()
    high = checkpoint_factory(previous=baseline, gain="1000", observed_at=NOW + timedelta(seconds=1))
    loss = checkpoint_factory(previous=high, gain="450", observed_at=NOW + timedelta(seconds=2))
    risk = require_risk_checkpoint(loss, max_age_seconds=60, now=NOW + timedelta(seconds=3))
    assert risk.high_water_mark == 11000 and risk.drawdown_pct == Decimal(".05")
    assert type(risk).model_validate_json(risk.model_dump_json()).fingerprint == risk.fingerprint
    with pytest.raises(ValueError):
        risk.equity = Decimal(1)


@pytest.mark.parametrize(
    "problem", ["missing", "failure", "unreconciled", "stale", "future", "wrong_account", "missing_risk"]
)
def test_unavailable_risk_never_defaults_to_zero(checkpoint_factory, problem):
    payload = checkpoint_factory()
    now = NOW
    if problem == "missing":
        payload = {}
    elif problem == "failure":
        payload["error"] = "TimeoutError"
    elif problem == "unreconciled":
        payload["report"]["issues"] = ["Cash mismatch"]
    elif problem == "stale":
        now += timedelta(seconds=61)
    elif problem == "future":
        now -= timedelta(seconds=1)
    elif problem == "wrong_account":
        payload["snapshot"]["account_id"] = "other"
    else:
        payload.pop("risk")
    with pytest.raises(ValueError):
        require_risk_checkpoint(payload, max_age_seconds=60, now=now)


@pytest.mark.parametrize("problem", ["revision", "retraction"])
def test_accepted_transfer_changes_invalidate_history_without_reset(checkpoint_factory, problem):
    transfer = {"id": "transfer", "activity_type": "CSD", "net_amount": "1000"}
    baseline = checkpoint_factory(transfers=(transfer,))
    changed = ({**transfer, "net_amount": "2000"},) if problem == "revision" else ()
    invalid = checkpoint_factory(previous=baseline, transfers=changed, observed_at=NOW + timedelta(seconds=1))
    assert invalid["risk"] == baseline["risk"]
    assert "transfer" in invalid["risk_invalidation"].lower()
    recovered_accounting = checkpoint_factory(
        previous=invalid, transfers=(transfer,), observed_at=NOW + timedelta(seconds=2)
    )
    with pytest.raises(ValueError, match="transfer"):
        require_risk_checkpoint(recovered_accounting, max_age_seconds=60, now=NOW + timedelta(seconds=3))


def test_derived_equity_preserves_signed_short_basis(checkpoint_factory):
    activities = [
        {"id": "initial", "activity_type": "CSD", "net_amount": "10000"},
        {
            "id": "short",
            "activity_type": "FILL",
            "symbol": "SPY",
            "side": "sell_short",
            "qty": "10",
            "price": "100",
            "order_id": "order",
            "transaction_time": NOW.isoformat(),
        },
    ]
    snapshot = AccountSnapshot(
        account_id="account",
        cash="11000",
        observed_at=NOW,
        positions={"SPY": {"qty": "-10", "cost_basis": "-1000", "unrealized_pl": "-500", "asset_class": "us_equity"}},
    )
    payload = {
        "snapshot": snapshot.model_dump(mode="json"),
        "report": reconcile(activities, snapshot).model_dump(mode="json"),
    }
    payload.update(advance_risk_checkpoint(checkpoint_factory(), payload, activities, account_id="account", now=NOW))
    risk = require_risk_checkpoint(payload, max_age_seconds=60, now=NOW)
    assert risk.equity == 9500 and risk.drawdown_pct == Decimal(".05")
    assert risk.equity_source == "cash_plus_signed_cost_basis_plus_unrealized"


@pytest.mark.parametrize("amount", ["-10000", "-11000"])
def test_nonpositive_initial_equity_cannot_seed_risk(checkpoint_factory, amount):
    with pytest.raises(ValueError, match="positive"):
        require_risk_checkpoint(checkpoint_factory(gain=amount), max_age_seconds=60, now=NOW)


def test_unreconciled_observation_preserves_peak_and_later_recovers(checkpoint_factory):
    baseline = checkpoint_factory(gain="1000")
    payload = checkpoint_factory(previous=baseline, gain="1000000", observed_at=NOW + timedelta(seconds=1))
    payload["report"]["issues"] = ["Cash mismatch"]
    payload.update(advance_risk_checkpoint(baseline, payload, [], account_id="account", now=NOW + timedelta(seconds=2)))
    assert payload["risk"] == baseline["risk"]
    with pytest.raises(ValueError, match="reconcil"):
        require_risk_checkpoint(payload, max_age_seconds=60, now=NOW + timedelta(seconds=2))
    restored = checkpoint_factory(previous=payload, gain="450", observed_at=NOW + timedelta(seconds=3))
    risk = require_risk_checkpoint(restored, max_age_seconds=60, now=NOW + timedelta(seconds=4))
    assert risk.high_water_mark == 11000 and risk.drawdown_pct == Decimal(".05")


@pytest.mark.parametrize(
    "kind,amount,reason", [("JNLC", "-1000", "ambiguous"), ("CSD", "-1000", "direction"), ("CSW", "1000", "direction")]
)
def test_ambiguous_or_misdirected_cash_activity_cannot_hide_drawdown(checkpoint_factory, kind, amount, reason):
    payload = checkpoint_factory(
        previous=checkpoint_factory(),
        transfers=({"id": "ambiguous", "activity_type": kind, "net_amount": amount},),
    )
    assert payload["report"]["issues"] == [], "Existing account P&L semantics are unchanged"
    with pytest.raises(ValueError, match=reason):
        require_risk_checkpoint(payload, max_age_seconds=60, now=NOW)


@pytest.mark.parametrize("prior_exists", [False, True])
def test_future_observation_cannot_seed_or_raise_high_water(checkpoint_factory, prior_exists):
    prior = checkpoint_factory() if prior_exists else {}
    future = checkpoint_factory(gain="1000000", observed_at=NOW + timedelta(days=1))
    update = advance_risk_checkpoint(prior, future, [], account_id="account", now=NOW)
    assert update["risk"] == prior.get("risk")
    assert "future" in update["risk_error"]


async def test_fenced_commits_preserve_risk_peak_through_failure_recovery_and_rebuild(temp_db, checkpoint_factory):
    store = LedgerStore(temp_db.workflows)
    observed = datetime.now(UTC) - timedelta(seconds=10)
    initial = {"id": "initial", "activity_type": "CSD", "net_amount": "10000"}
    token = await store.begin("account")
    assert await store.commit(token, [initial], checkpoint_factory(observed_at=observed))
    stale = await store.begin("account")
    current = await store.begin("account")
    high = checkpoint_factory(gain="1000", observed_at=observed + timedelta(seconds=1))
    assert await store.commit(current, [initial, {"id": "income", "activity_type": "INT", "net_amount": "1000"}], high)
    peak = await store.status()
    assert peak["risk"]["high_water_mark"] == "11000"
    assert not await store.commit(
        stale, [], checkpoint_factory(gain="1000000", observed_at=observed + timedelta(seconds=2))
    )
    await store.fail(stale, "TimeoutError")
    assert await store.status() == peak

    failed = await store.begin("account")
    await store.fail(failed, "TimeoutError")
    with pytest.raises(ValueError, match="failed"):
        require_risk_checkpoint(await store.status(), max_age_seconds=60)
    assert (await store.status())["risk"] == peak["risk"]

    recovered = await store.begin("account")
    loss = checkpoint_factory(gain="450", observed_at=observed + timedelta(seconds=3))
    assert await store.commit(recovered, [initial, {"id": "income", "activity_type": "INT", "net_amount": "450"}], loss)
    before = await store.status()
    risk = require_risk_checkpoint(before, max_age_seconds=60)
    assert risk.baseline_observed_at == observed and risk.drawdown_pct == Decimal(".05")
    inflight = await store.begin("account")
    await store.rebuild()
    assert await store.status() == before
    assert not await store.commit(inflight, [], {}), "Replay fences an older remote importer"


@pytest.mark.parametrize("kind", ["FEE", "DIV", "INT"])
def test_income_and_fees_change_performance_without_changing_transfer_total(checkpoint_factory, kind):
    prior = checkpoint_factory()
    amount = "-100" if kind == "FEE" else "100"
    activities = [
        {"id": "initial", "activity_type": "CSD", "net_amount": "10000"},
        {"id": "performance", "activity_type": kind, "net_amount": amount},
    ]
    payload = checkpoint_factory(gain=amount)
    payload.update(advance_risk_checkpoint(prior, payload, activities, account_id="account", now=NOW))
    risk = require_risk_checkpoint(payload, max_age_seconds=60, now=NOW)
    assert risk.cash_flows == 10000
    assert risk.adjusted_equity == 10000 + Decimal(amount)
    assert risk.drawdown_pct == (Decimal(".01") if kind == "FEE" else 0)


@pytest.mark.parametrize("problem", ["out_of_order", "tampered_drawdown", "mismatched_equity", "unsupported_version"])
def test_inconsistent_or_reordered_risk_evidence_is_rejected(checkpoint_factory, problem):
    prior = checkpoint_factory(observed_at=NOW + timedelta(seconds=1))
    payload = checkpoint_factory()
    if problem == "out_of_order":
        payload.update(
            advance_risk_checkpoint(prior, payload, [], account_id="account", now=NOW + timedelta(seconds=2))
        )
        assert payload["risk"] == prior["risk"]
    elif problem == "tampered_drawdown":
        payload["risk"]["drawdown_pct"] = ".5"
    elif problem == "mismatched_equity":
        payload["snapshot"]["cash"] = "9999"
    else:
        payload["risk"]["version"] = "unknown"
    with pytest.raises(ValueError):
        require_risk_checkpoint(payload, max_age_seconds=60, now=NOW + timedelta(seconds=2))


@pytest.mark.parametrize("field", ["report", "snapshot", "risk"])
@pytest.mark.parametrize("malformed", [False, True])
def test_public_unavailable_messages_do_not_expose_validation_payloads(checkpoint_factory, field, malformed):
    payload = checkpoint_factory()
    payload[field] = {"private_marker": "do-not-echo"} if malformed else None
    with pytest.raises(ValueError) as caught:
        require_risk_checkpoint(payload, max_age_seconds=60, now=NOW)
    message = str(caught.value)
    assert len(message) < 160
    assert "private_marker" not in message and "do-not-echo" not in message
    assert "pydantic" not in message.lower()


def test_first_observation_can_freeze_historical_journals_without_classifying_them_as_transfers(checkpoint_factory):
    baseline = checkpoint_factory(initial_kind="JNLC")
    risk = require_risk_checkpoint(baseline, max_age_seconds=60, now=NOW)
    assert risk.equity == 10000 and risk.baseline_equity == 10000
    assert risk.cash_flows == 0 and risk.baseline_cash_flows == 0
    assert risk.cash_transfer_fingerprints == ()
    assert len(risk.baseline_cash_journal_fingerprints) == 1
    assert risk.baseline_cash_journal_fingerprints[0][0] == "initial"
    assert risk.drawdown_pct == 0

    high = checkpoint_factory(
        initial_kind="JNLC", previous=baseline, gain="1000", observed_at=NOW + timedelta(seconds=1)
    )
    transferred = checkpoint_factory(
        initial_kind="JNLC",
        previous=high,
        gain="450",
        observed_at=NOW + timedelta(seconds=2),
        transfers=({"id": "deposit", "activity_type": "CSD", "net_amount": "5000"},),
    )
    current = require_risk_checkpoint(transferred, max_age_seconds=60, now=NOW + timedelta(seconds=3))
    assert current.cash_flows == 5000 and current.baseline_cash_flows == 0
    assert current.adjusted_equity == 10450 and current.high_water_mark == 11000
    assert current.drawdown_pct == Decimal(".05")
    assert current.baseline_cash_journal_fingerprints == risk.baseline_cash_journal_fingerprints


@pytest.mark.parametrize("change", ["new", "revised", "retracted", "retimed"])
def test_changed_baseline_cash_journals_invalidate_history_permanently(checkpoint_factory, change):
    journal = {"id": "historical_journal", "activity_type": "JNLC", "net_amount": "1000", "date": "2026-09-17"}
    baseline = checkpoint_factory(transfers=(journal,))
    require_risk_checkpoint(baseline, max_age_seconds=60, now=NOW)
    if change == "new":
        journals = (journal, {**journal, "id": "new_journal"})
    elif change == "revised":
        journals = ({**journal, "net_amount": "2000"},)
    elif change == "retimed":
        journals = ({**journal, "date": "2026-09-16"},)
    else:
        journals = ()
    changed = checkpoint_factory(previous=baseline, transfers=journals, observed_at=NOW + timedelta(seconds=1))
    assert changed["risk"] == baseline["risk"]
    assert changed["report"]["issues"] == []
    assert "journal" in changed["risk_invalidation"].lower()
    reverted = checkpoint_factory(previous=changed, transfers=(journal,), observed_at=NOW + timedelta(seconds=2))
    with pytest.raises(ValueError, match="journal"):
        require_risk_checkpoint(reverted, max_age_seconds=60, now=NOW + timedelta(seconds=3))
