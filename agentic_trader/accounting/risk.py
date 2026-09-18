"""Cash-flow-adjusted account drawdown from reconciled, observed ledger checkpoints.

This is a fixed-baseline dollar-equity series, not a time-weighted return. Transfers
do not erase drawdown; neither historical peaks nor marks at transfer times are
invented. The existing ledger journal persists the baseline and sampled peaks.
Cash journals already present at the first observation are frozen starting history,
not classified as external capital. Any subsequent journal change blocks new risk.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

from agentic_trader.accounting.ledger import CASH_TRANSFERS, AccountSnapshot, LedgerReport, Money, decimal


RISK_VERSION: Final = "cash_flow_adjusted_equity_v1"


class AccountRiskSnapshot(BaseModel):
    """Immutable, account-bound risk evidence; monetary values retain exact decimals."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["cash_flow_adjusted_equity_v1"] = RISK_VERSION
    equity_source: Literal["cash_plus_signed_cost_basis_plus_unrealized"] = (
        "cash_plus_signed_cost_basis_plus_unrealized"
    )
    account_id: str = Field(min_length=1)
    baseline_observed_at: AwareDatetime
    baseline_equity: Money = Field(gt=0)
    baseline_cash_flows: Money
    observed_at: AwareDatetime
    equity: Money
    cash_flows: Money
    adjusted_equity: Money
    high_water_mark: Money = Field(gt=0)
    drawdown_pct: Money = Field(ge=0)
    cash_transfer_fingerprints: tuple[tuple[str, str], ...]
    baseline_cash_journal_fingerprints: tuple[tuple[str, str], ...]

    @model_validator(mode="after")
    def consistent(self):
        if self.observed_at < self.baseline_observed_at:
            raise ValueError("Risk observation precedes its baseline")
        if self.adjusted_equity != self.equity - (self.cash_flows - self.baseline_cash_flows):
            raise ValueError("Inconsistent cash-flow-adjusted equity")
        if self.high_water_mark < max(self.baseline_equity, self.adjusted_equity):
            raise ValueError("Risk high-water mark does not cover observed equity")
        expected = max(Decimal(0), (self.high_water_mark - self.adjusted_equity) / self.high_water_mark)
        if self.drawdown_pct != expected:
            raise ValueError("Inconsistent account drawdown")
        for fingerprints in (self.cash_transfer_fingerprints, self.baseline_cash_journal_fingerprints):
            identities = [identity for identity, _ in fingerprints]
            if identities != sorted(set(identities)) or any(
                not identity or len(digest) != 64 for identity, digest in fingerprints
            ):
                raise ValueError("Invalid cash activity evidence identity")
        if dict(self.cash_transfer_fingerprints).keys() & dict(self.baseline_cash_journal_fingerprints).keys():
            raise ValueError("Cash transfer and baseline journal identities overlap")
        return self

    @property
    def fingerprint(self) -> str:
        return _digest(self.model_dump(mode="json"))


class _RiskHistoryInvalidation(ValueError):
    pass


def _digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _validated_checkpoint(payload: dict[str, Any]) -> AccountSnapshot:
    if payload.get("error"):
        raise ValueError(f"Account import failed: {payload['error']}")
    if payload.get("report") is None:
        raise ValueError("Awaiting account activity reconciliation")
    try:
        report = LedgerReport.model_validate(payload["report"])
    except ValidationError:
        raise ValueError("Account reconciliation evidence is invalid; inspect the persisted ledger") from None
    if not report.ready:
        raise ValueError("Account reconciliation unavailable: " + "; ".join(report.issues))
    if payload.get("snapshot") is None:
        raise ValueError("Account snapshot is unavailable")
    try:
        snapshot = AccountSnapshot.model_validate(payload["snapshot"])
    except ValidationError:
        raise ValueError("Account snapshot is invalid; inspect the persisted ledger") from None
    if snapshot.observed_at.utcoffset() is None or report.observed_at != snapshot.observed_at:
        raise ValueError("Account risk requires aligned timezone-aware observation times")
    return snapshot


def _risk_snapshot(payload: Any) -> AccountRiskSnapshot:
    if payload is None:
        raise ValueError("Awaiting first reconciled account risk baseline")
    try:
        return AccountRiskSnapshot.model_validate(payload)
    except ValidationError:
        raise ValueError("Account risk evidence is invalid or unsupported; inspect the persisted ledger") from None


def _equity(snapshot: AccountSnapshot) -> Decimal:
    return snapshot.cash + sum(
        (position.cost_basis + position.unrealized_pl for position in snapshot.positions.values()), Decimal(0)
    )


def _cash_history(
    activities: list[dict[str, Any]],
) -> tuple[Decimal, tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]:
    total = Decimal(0)
    transfers, journals = [], []
    for activity in activities:
        if activity.get("activity_type") not in CASH_TRANSFERS:
            continue
        amount = decimal(activity["net_amount"])
        if activity.get("status", "executed") != "executed" or decimal(activity.get("qty") or 0) != 0:
            raise ValueError("Unsupported cash transfer")
        if (activity["activity_type"] == "CSD" and amount < 0) or (activity["activity_type"] == "CSW" and amount > 0):
            raise ValueError("Cash transfer direction disagrees with activity type")
        # Financial/effective-time changes matter; cosmetic broker descriptions do not.
        evidence = {
            "activity_type": activity["activity_type"],
            "net_amount": str(amount.normalize()),
            "transaction_time": activity.get("transaction_time"),
            "date": activity.get("date"),
        }
        if activity["activity_type"] == "JNLC":
            journals.append((activity["id"], _digest(evidence)))
        else:
            transfers.append((activity["id"], _digest(evidence)))
            total += amount
    return total, tuple(sorted(transfers)), tuple(sorted(journals))


def advance_risk_checkpoint(
    previous: dict[str, Any],
    checkpoint: dict[str, Any],
    activities: list[dict[str, Any]],
    *,
    account_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Derive checkpoint fields while the caller holds the fenced ledger lock.

    Invalid imports preserve the last good baseline/peak. Revised or removed
    accepted transfer evidence invalidates the history durably, including if the
    broker later reverses that revision; it never automatically seeds a new peak.
    """
    preserved = {
        "risk": previous.get("risk"),
        "risk_error": None,
        "risk_invalidation": previous.get("risk_invalidation"),
    }
    if preserved["risk_invalidation"]:
        return {**preserved, "risk_error": preserved["risk_invalidation"]}
    try:
        snapshot = _validated_checkpoint(checkpoint)
        current = now or datetime.now(UTC)
        if current.utcoffset() is None or snapshot.observed_at > current:
            raise ValueError("Account risk observation is future-dated or current time is ambiguous")
        if snapshot.account_id != account_id:
            raise ValueError("Account risk identity differs from ledger binding")
        equity = _equity(snapshot)
        cash_flows, transfers, journals = _cash_history(activities)
        prior = _risk_snapshot(previous["risk"]) if previous.get("risk") is not None else None
        if prior is not None:
            if prior.account_id != account_id:
                raise ValueError("Account risk baseline belongs to another account")
            if snapshot.observed_at < prior.observed_at:
                raise ValueError("Account risk observation is out of order")
            if journals != prior.baseline_cash_journal_fingerprints:
                raise _RiskHistoryInvalidation(
                    "Cash journal history changed after the baseline; ambiguous external-flow classification requires review"
                )
            current_transfers = dict(transfers)
            if any(current_transfers.get(identity) != digest for identity, digest in prior.cash_transfer_fingerprints):
                raise _RiskHistoryInvalidation(
                    "Accepted cash transfer evidence was revised or retracted; risk history requires review"
                )
        elif equity <= 0:
            raise ValueError("Initial account risk equity must be positive")
        baseline_equity = prior.baseline_equity if prior else equity
        baseline_flows = prior.baseline_cash_flows if prior else cash_flows
        adjusted_equity = equity - (cash_flows - baseline_flows)
        high_water_mark = max(prior.high_water_mark if prior else equity, adjusted_equity)
        risk = AccountRiskSnapshot(
            account_id=account_id,
            baseline_observed_at=prior.baseline_observed_at if prior else snapshot.observed_at,
            baseline_equity=baseline_equity,
            baseline_cash_flows=baseline_flows,
            observed_at=snapshot.observed_at,
            equity=equity,
            cash_flows=cash_flows,
            adjusted_equity=adjusted_equity,
            high_water_mark=high_water_mark,
            drawdown_pct=max(Decimal(0), (high_water_mark - adjusted_equity) / high_water_mark),
            cash_transfer_fingerprints=transfers,
            baseline_cash_journal_fingerprints=prior.baseline_cash_journal_fingerprints if prior else journals,
        )
        return {"risk": risk.model_dump(mode="json"), "risk_error": None, "risk_invalidation": None}
    except _RiskHistoryInvalidation as exc:
        return {**preserved, "risk_error": str(exc), "risk_invalidation": str(exc)}
    except ValidationError:
        return {**preserved, "risk_error": "Account risk evidence is invalid; inspect the persisted ledger"}
    except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
        return {**preserved, "risk_error": str(exc)}


def require_risk_checkpoint(
    payload: dict[str, Any], *, max_age_seconds: float, now: datetime | None = None
) -> AccountRiskSnapshot:
    """Return fresh reconciled risk evidence, never a default zero drawdown."""
    if not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
        raise ValueError("Account risk freshness must be finite and positive")
    if payload.get("risk_invalidation") or payload.get("risk_error"):
        raise ValueError(payload.get("risk_invalidation") or payload["risk_error"])
    snapshot = _validated_checkpoint(payload)
    risk = _risk_snapshot(payload.get("risk"))
    if (
        risk.account_id != snapshot.account_id
        or risk.observed_at != snapshot.observed_at
        or risk.equity != _equity(snapshot)
    ):
        raise ValueError("Account risk does not match its reconciled checkpoint")
    current = now or datetime.now(UTC)
    if current.utcoffset() is None:
        raise ValueError("Account risk requires an aware current time")
    age = (current - risk.observed_at).total_seconds()
    if not 0 <= age <= max_age_seconds:
        raise ValueError("Account risk observation is stale or future-dated")
    return risk
