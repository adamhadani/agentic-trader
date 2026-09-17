"""Immutable prospective equity candidates; never inferred historical membership."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

import pandas as pd

from agentic_trader.data.symbol_directory import DIRECTORY_NAMES, MAX_METADATA_ROWS
from agentic_trader.market.bars import utc_timestamp
from agentic_trader.market.session import ET_TZ


UNIVERSE_VERSION = "prospective_equity_candidates_v1"
MAX_CANDIDATES = 500
CLASSIFICATION = "listed_non_etf_equity_candidate"


def document_hash(document: dict) -> str:
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


@dataclass(frozen=True)
class EquityUniversePlan:
    campaign_id: str
    target_size: int = 300
    seed: str = "equity-cohort-v1"
    exchanges: tuple[str, ...] = ("AMEX", "NASDAQ", "NYSE")
    max_metadata_age_days: int = 7

    def __post_init__(self):
        if not self.campaign_id.strip() or not self.seed.strip():
            raise ValueError("Explicit campaign and sampling seed required")
        if type(self.target_size) is not int or not 1 <= self.target_size <= MAX_CANDIDATES:
            raise ValueError("Bounded integer target size required")
        if type(self.max_metadata_age_days) is not int or not 1 <= self.max_metadata_age_days <= 7:
            raise ValueError("Metadata age must be between one and seven calendar days")
        if not self.exchanges or tuple(sorted(set(self.exchanges))) != self.exchanges:
            raise ValueError("Sorted unique exchange policy required")
        if not set(self.exchanges) <= {"AMEX", "ARCA", "BATS", "NASDAQ", "NYSE", "NYSEARCA"}:
            raise ValueError("Unsupported listed exchange")

    def document(self):
        return {
            **asdict(self),
            "exchanges": list(self.exchanges),
            "version": UNIVERSE_VERSION,
            "sampling": "sha256_seed_asset_id",
            "price_reads": False,
            "trial_count": 1,
        }

    @property
    def identity(self):
        return document_hash(self.document())

    @classmethod
    def from_document(cls, document: dict):
        try:
            plan = cls(
                document["campaign_id"],
                document["target_size"],
                document["seed"],
                tuple(document["exchanges"]),
                document["max_metadata_age_days"],
            )
        except (KeyError, TypeError) as exc:
            raise ValueError("Invalid universe protocol") from exc
        if plan.document() != document:
            raise ValueError("Universe protocol must match the exact versioned contract")
        return plan


def _directory_index(directories: tuple[dict, ...], plan: EquityUniversePlan, observed_at: pd.Timestamp):
    index: dict[str, dict] = {}
    seen = set()
    observed_date = observed_at.tz_convert(ET_TZ).date()
    for directory in directories:
        name = directory["name"]
        if name not in DIRECTORY_NAMES or name in seen:
            raise ValueError("Unknown or duplicate directory")
        seen.add(name)
        age = (observed_date - date.fromisoformat(directory["file_date"])).days
        if not 0 <= age <= plan.max_metadata_age_days:
            raise ValueError("Stale or future directory generation date")
        for row in directory["rows"]:
            if any(row.get(flag) not in {"Y", "N"} for flag in ("ETF", "Test Issue")):
                raise ValueError("Unknown directory flag")
            if name == "nasdaqlisted" and row["NextShares"] not in {"Y", "N"}:
                raise ValueError("Unknown NextShares flag")
            # Exact published SIP/CQS symbols only; never guess punctuation or share-class aliases.
            symbol = row["Symbol"] if name == "nasdaqlisted" else row["CQS Symbol"]
            if not symbol or symbol in index:
                raise ValueError("Empty or duplicate directory symbol")
            index[symbol] = {"source": name, **row}
    return index


def verify_snapshot(snapshot: dict) -> None:
    if snapshot.get("version") != UNIVERSE_VERSION or snapshot.get("snapshot_id") != document_hash(
        {key: value for key, value in snapshot.items() if key != "snapshot_id"}
    ):
        raise ValueError("Universe snapshot identity mismatch")


def candidate_symbols(snapshot: dict, *, at: datetime | pd.Timestamp) -> tuple[str, ...]:
    """Read an immutable cohort for prospective diagnostics, never backdated eligibility."""
    verify_snapshot(snapshot)
    if utc_timestamp(at) < utc_timestamp(snapshot["observed_at"]):
        raise ValueError("Universe membership is prospective from actual receipt only")
    return tuple(row["symbol"] for row in snapshot["selected"])


def build_snapshot(
    plan: EquityUniversePlan,
    assets: list[dict],
    directories: tuple[dict, ...],
    *,
    observed_at,
    previous: dict | None = None,
    source_evidence: dict | None = None,
) -> dict:
    observed = utc_timestamp(observed_at)
    directory_index = _directory_index(directories, plan, observed)
    if not assets or len(assets) > MAX_METADATA_ROWS:
        raise ValueError("Nonempty bounded asset response required")
    ids, symbols = set(), set()
    members = []
    for asset in assets:
        asset_id, symbol = str(UUID(asset["id"])), asset["symbol"]
        if asset_id in ids or symbol in symbols or not symbol:
            raise ValueError("Duplicate or empty broker asset identity")
        ids.add(asset_id)
        symbols.add(symbol)
        if type(asset.get("tradable")) is not bool or asset.get("status") not in {"active", "inactive"}:
            raise ValueError("Invalid broker eligibility fields")
        directory = directory_index.get(symbol)
        reasons = []
        if asset.get("class") != "us_equity":
            reasons.append("not_us_equity")
        if asset["status"] != "active":
            reasons.append("inactive")
        if not asset["tradable"]:
            reasons.append("not_tradable")
        if asset.get("exchange") not in plan.exchanges:
            reasons.append("excluded_exchange")
        if directory is None:
            reasons.append("missing_directory")
        else:
            if directory["ETF"] == "Y":
                reasons.append("etf")
            if directory["Test Issue"] == "Y":
                reasons.append("test_issue")
            if directory.get("NextShares") == "Y":
                reasons.append("nextshares")
            if directory.get("Financial Status", "N") != "N":
                reasons.append("financial_status")
            expected_exchange = (
                "NASDAQ"
                if directory["source"] == "nasdaqlisted"
                else {"A": "AMEX", "N": "NYSE", "P": "ARCA", "Z": "BATS", "V": "IEX"}.get(directory["Exchange"])
            )
            if asset["exchange"] != expected_exchange and not (
                asset["exchange"] == "NYSEARCA" and expected_exchange == "ARCA"
            ):
                reasons.append("exchange_mismatch")
        members.append(
            {
                "asset_id": asset_id,
                "symbol": symbol,
                "broker": asset,
                "directory": directory,
                "classification": CLASSIFICATION if not reasons else "excluded",
                "instrument_subtype": "unknown",
                "reasons": reasons,
                "sampling_key": document_hash({"seed": plan.seed, "asset_id": asset_id}),
                "selection": "excluded" if reasons else "candidate_cap",
            }
        )
    eligible = sorted(
        (row for row in members if not row["reasons"]), key=lambda row: (row["sampling_key"], row["asset_id"])
    )
    for row in eligible[: plan.target_size]:
        row["selection"] = "selected"
    members.sort(key=lambda row: row["asset_id"])
    changes: dict[str, Any] = {"added_asset_ids": [], "removed_asset_ids": [], "changed": []}
    if previous is not None:
        verify_snapshot(previous)
        if observed < utc_timestamp(previous["observed_at"]):
            raise ValueError("Previous snapshot is from the future")
        before = {row["asset_id"]: row for row in previous["members"]}
        after = {row["asset_id"]: row for row in members}
        changes["added_asset_ids"] = sorted(after.keys() - before.keys())
        changes["removed_asset_ids"] = sorted(before.keys() - after.keys())
        changes["changed"] = [
            {"asset_id": key, "before": before[key], "after": after[key]}
            for key in sorted(before.keys() & after.keys())
            if before[key] != after[key]
        ]
    result = {
        "version": UNIVERSE_VERSION,
        "plan_id": plan.identity,
        "plan": plan.document(),
        "observed_at": observed.isoformat(),
        "members": members,
        "selected": [{"asset_id": row["asset_id"], "symbol": row["symbol"]} for row in eligible[: plan.target_size]],
        "eligible_count": len(eligible),
        "selected_count": min(len(eligible), plan.target_size),
        "target_met": len(eligible) >= plan.target_size,
        "changes": changes,
        "previous_snapshot_id": previous["snapshot_id"] if previous else None,
        "source_evidence": source_evidence or {},
        "point_in_time_historical_membership": False,
        "common_stock_classification": False,
        "liquidity_screened": False,
        "authorizes_promotion": False,
        "qualification_blockers": [
            "authoritative_instrument_subtype",
            "historical_membership_and_delistings",
            "corporate_action_evidence",
            "feed_and_liquidity_evidence",
        ],
    }
    return {**result, "snapshot_id": document_hash(result)}
