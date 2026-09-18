"""Read-only summaries of canonical forward decisions, never trading performance."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta

import numpy as np

from agentic_trader.market.bars import ObservationStatus, utc_timestamp
from agentic_trader.research.alpha.models import DecisionStatus
from agentic_trader.storage.alpha_daily import DailyCampaignRepository


FORWARD_EVIDENCE_VERSION = "forward_evidence_v1"
DEFAULT_FORWARD_DAYS = 7
MAX_FORWARD_DAYS = 31
DEFAULT_FORWARD_LIMIT = 10_000
MAX_FORWARD_LIMIT = 50_000
DAILY_PANEL_EVIDENCE_VERSION = "daily_panel_evidence_v1"
MAX_DAILY_EVIDENCE_LIMIT = 1000


def _distribution(values):
    return {
        "count": len(values),
        "min": float(min(values)) if values else None,
        "median": float(np.median(values)) if values else None,
        "p95": float(np.quantile(values, 0.95)) if values else None,
        "max": float(max(values)) if values else None,
    }


def _candidate(definition, symbol, cursor, records, policy, generation, now, since, truncated):
    counts = Counter(DecisionStatus(row["status"]) for row in records)
    scores, receipt_lag, read_duration = [], [], []
    directions: Counter = Counter()
    reasons: Counter = Counter()
    invalid_receipts = 0
    for row in records:
        if row["status"] == DecisionStatus.SCORED:
            forecast = row["forecast"]
            score, direction = float(forecast["score"]), forecast["decision"]
            if not np.isfinite(score) or direction not in (-1, 0, 1):
                raise ValueError("Invalid persisted forward score")
            scores.append(score)
            directions[direction] += 1
        elif row["status"] == DecisionStatus.UNAVAILABLE:
            # Provider exception messages may contain URLs or credentials. Expose stable categories only.
            reason = row.get("reason", "unknown")
            reasons[
                reason
                if reason
                in (
                    "registry_changed_during_capture",
                    "decision_expired_before_commit",
                    "decision_expired_before_read",
                    "invalid_or_expired_receipt",
                    "decision_candle_mismatch",
                    "missing_score_or_warmup",
                )
                else "capture_error"
            ] += 1
        if row.get("dataset_hash") and row.get("requested_at") and row.get("received_at"):
            closed, requested, received = (utc_timestamp(row[k]) for k in ("closed_at", "requested_at", "received_at"))
            if closed <= requested <= received:
                receipt_lag.append((received - closed).total_seconds())
                read_duration.append((received - requested).total_seconds())
            else:
                invalid_receipts += 1
    warnings = []
    if not policy.enabled:
        warnings.append("worker_disabled")
    if definition.data_feed != policy.feed:
        warnings.append("feed_not_configured")
    if symbol not in policy.symbols:
        warnings.append("symbol_not_configured")
    if cursor is None:
        warnings.append("not_enrolled")
    elif cursor["generation"] != generation:
        warnings.append("cursor_generation_differs")
    gap = cursor.get("gap") if cursor else None
    if gap and utc_timestamp(gap["end"]) > since and utc_timestamp(gap["start"]) <= now:
        warnings.append("retained_cursor_gap")
    else:
        gap = None
    if truncated:
        warnings.append("row_limit_hit")
    total = len(records)
    scored = counts[DecisionStatus.SCORED]
    return {
        "alpha_id": definition.alpha_id,
        "version_id": definition.version_id,
        "symbol": symbol,
        "timeframe": definition.timeframe,
        "feed": definition.data_feed,
        "enrolled_at": cursor.get("enrolled_at") if cursor else None,
        "cursor_checked_at": cursor.get("checked_at") if cursor else None,
        "cursor_gap": gap,
        "coverage_warnings": warnings,
        "evidence_state": "recorded_decisions" if total else "no_recorded_decisions",
        "recorded_decisions": total,
        "counts": {status.value: counts[status] for status in DecisionStatus},
        "recorded_score_fraction": scored / total if total and not truncated else None,
        "overdue_pending": sum(
            row["status"] == DecisionStatus.CLAIMED and utc_timestamp(row["expires_at"]) <= now for row in records
        ),
        "unavailable_reasons": dict(sorted(reasons.items())),
        "scores": _distribution(scores),
        "score_directions": {"long": directions[1], "flat": directions[0], "short": directions[-1]},
        "receipt_lag_seconds": _distribution(receipt_lag),
        "read_duration_seconds": _distribution(read_duration),
        "invalid_receipt_order": invalid_receipts,
        "without_measured_receipt": total - len(receipt_lag),
        "latest_scored_candle": max(
            (row["closed_at"] for row in records if row["status"] == DecisionStatus.SCORED),
            key=utc_timestamp,
            default=None,
        ),
    }


def build_forward_evidence(snapshot, policy, inputs, *, now, days):
    """Candle-time window over latest canonical outcomes, not a historical as-of reconstruction.

    The denominator is recorded decisions, not all theoretically eligible windows.
    Current cursor gaps are retained warnings, not an exhaustive historical outage census.
    """
    now = utc_timestamp(now)
    if not 1 <= days <= MAX_FORWARD_DAYS:
        raise ValueError("Forward evidence days outside supported bounds")
    since = now - timedelta(days=days)
    pairs = [(d, s) for d in (*snapshot.active, *snapshot.shadow) if d.clock for s in (d.eligible_symbols or ())]
    groups: dict = {(d.version_id, s): [] for d, s in pairs}
    other = outside = 0
    for row in inputs["decisions"]:
        if not since <= utc_timestamp(row["closed_at"]) <= now:
            outside += 1
        elif (key := (row["version_id"], row["symbol"])) not in groups:
            other += 1
        else:
            groups[key].append(row)
    return {
        "version": FORWARD_EVIDENCE_VERSION,
        "as_of": now.isoformat(),
        "since": since.isoformat(),
        "days": days,
        "registry_generation": snapshot.generation,
        "scope": "current_registry_session_versions",
        "coverage_basis": "recorded_decisions",
        "configured_feed": policy.feed,
        "truncated": inputs["truncated"],
        "rows_loaded": len(inputs["decisions"]),
        "outside_window_rows": outside,
        "other_cohort_rows": other,
        "authorizes_promotion": False,
        "limitations": [
            "Counts cover recorded decisions only; absent windows and historical cursor gaps may be unknown.",
            "Truncated counts are lower bounds; score fractions are withheld.",
            "Receipt lag includes configured delay and polling; it is not provider publication latency.",
            "Score directions are diagnostic outputs, not orders, fills, returns or qualifying shadow credit.",
        ],
        "candidates": [
            _candidate(
                d,
                s,
                inputs["cursors"].get(f"session-cursor/{d.version_id}/{s}"),
                groups[d.version_id, s],
                policy,
                snapshot.generation,
                now,
                since,
                inputs["truncated"],
            )
            for d, s in pairs
        ],
    }


async def load_forward_evidence(repository, *, days=DEFAULT_FORWARD_DAYS, limit=DEFAULT_FORWARD_LIMIT, now=None):
    """One shared application query for CLI and Telegram; no provider or business writes."""
    now = utc_timestamp(now if now is not None else datetime.now(UTC))
    if not 1 <= days <= MAX_FORWARD_DAYS or not 1 <= limit <= MAX_FORWARD_LIMIT:
        raise ValueError("Forward evidence query outside supported bounds")
    snapshot = await repository.snapshot()
    keys = [
        f"session-cursor/{d.version_id}/{s}"
        for d in (*snapshot.active, *snapshot.shadow)
        if d.clock
        for s in (d.eligible_symbols or ())
    ]
    inputs = await repository.forward_records(since=now - timedelta(days=days), limit=limit, cursor_keys=keys)
    if (await repository.snapshot()).generation != snapshot.generation:
        raise ValueError("Alpha registry changed during evidence read; request a fresh report")
    report = await asyncio.to_thread(
        build_forward_evidence, snapshot, repository.policy.decisions, inputs, now=now, days=days
    )
    report["row_limit"] = limit
    report["daily_panel"] = await load_daily_panel_evidence(repository, days=days, limit=limit, now=now)
    return snapshot, report


def _comparison_statistics(counts):
    result = {}
    for kind, statuses in (("decision", DecisionStatus), ("outcome", ObservationStatus)):
        expected = sum(counts[kind].values())
        missing = counts[kind][None]
        result.update(
            {
                f"{kind}_sessions": expected,
                f"{kind}_recorded_sessions": expected - missing,
                f"{kind}_missing_summaries": missing,
                f"{kind}_counts": {status.value: counts[kind][status] for status in statuses},
            }
        )
    return result


def _daily_comparison_evidence(inputs):
    """Count only immutable enrollment pins, including missing capture summaries."""
    metadata = {(row["campaign_id"], row["comparison_id"], row["protocol_hash"]): row for row in inputs["comparisons"]}
    groups: dict = {key: {"decision": Counter(), "outcome": Counter()} for key in metadata}
    for kind, statuses in (("decision", DecisionStatus), ("outcome", ObservationStatus)):
        for row in inputs[f"{kind}s"]:
            # Historical sessions preceding comparison enrollment have no pins.
            pins = [
                (pin["campaign_id"], pin["comparison_id"], pin["protocol_hash"]) for pin in row.get("comparisons", [])
            ]
            if len(set(pins)) != len(pins) or any(key[0] != row["campaign_id"] for key in pins):
                raise ValueError("Invalid daily comparison enrollment pins")
            recorded = {}
            for summary in row.get("evidence", {}).get("comparisons", []):
                key = (row["campaign_id"], summary["comparison_id"], summary["protocol_hash"])
                if key not in pins or key in recorded:
                    raise ValueError("Unpinned or repeated daily comparison summary")
                recorded[key] = statuses(summary["status"])
            for key in pins:
                counts = groups.setdefault(key, {"decision": Counter(), "outcome": Counter()})
                counts[kind][recorded.get(key)] += 1
    summaries = []
    aggregate: dict = {"decision": Counter(), "outcome": Counter()}
    for (campaign_id, comparison_id, protocol_hash), counts in sorted(groups.items()):
        enrollment = metadata.get((campaign_id, comparison_id, protocol_hash))
        summaries.append(
            {
                "campaign_id": campaign_id,
                "comparison_id": comparison_id,
                "protocol_hash": protocol_hash,
                "metadata_available": enrollment is not None,
                "enrolled_at": enrollment["enrolled_at"] if enrollment else None,
                "models": len(enrollment["protocol"]["models"]) if enrollment else None,
                "trial_count": enrollment["trial_count"] if enrollment else None,
                **_comparison_statistics(counts),
            }
        )
        for kind, total in aggregate.items():
            total.update(counts[kind])
    missing = sum(not row["metadata_available"] for row in summaries)
    return summaries, {
        "protocols": len(summaries),
        "models": None if missing else sum(row["models"] for row in summaries),
        "metadata_missing": missing,
        **_comparison_statistics(aggregate),
    }


def build_daily_panel_evidence(policy, inputs, *, now, days):
    """Bounded latest journal projections; no forecasts, provider errors or private paths."""
    now = utc_timestamp(now)
    if not 1 <= days <= MAX_FORWARD_DAYS:
        raise ValueError("Daily-panel evidence days outside supported bounds")
    since = now - timedelta(days=days)
    decisions, outcomes = inputs["decisions"], inputs["outcomes"]
    comparisons, comparison_totals = _daily_comparison_evidence(inputs)
    campaigns = {row["campaign_id"]: row for row in inputs["campaigns"]}
    identities = sorted(set(campaigns) | {row["campaign_id"] for row in (*decisions, *outcomes, *comparisons)})
    decision_counts = Counter(DecisionStatus(row["status"]) for row in decisions)
    outcome_counts = Counter(ObservationStatus(row["status"]) for row in outcomes)
    summaries = []
    for identity in identities:
        campaign = campaigns.get(identity)
        protocol = campaign["protocol"] if campaign else {}
        summaries.append(
            {
                "campaign_id": identity,
                "metadata_available": campaign is not None,
                "protocol_hash": campaign["protocol_hash"] if campaign else None,
                "enrolled_at": campaign["enrolled_at"] if campaign else None,
                "last_session": campaign.get("last_session") if campaign else None,
                "feed": protocol.get("feed"),
                "symbols": len(protocol.get("symbols", [])),
                "arms": len(protocol.get("models", [])),
                "decision_sessions": sum(row["campaign_id"] == identity for row in decisions),
                "outcome_sessions": sum(row["campaign_id"] == identity for row in outcomes),
            }
        )
    return {
        "version": DAILY_PANEL_EVIDENCE_VERSION,
        "as_of": now.isoformat(),
        "since": since.isoformat(),
        "days": days,
        "worker_enabled": policy.enabled,
        "scope": "current_canonical_daily_campaign_projections",
        "coverage_basis": "recorded_campaign_sessions",
        "truncated": inputs["truncated"],
        "rows_loaded": len(inputs["campaigns"]) + len(inputs["comparisons"]) + len(decisions) + len(outcomes),
        "campaigns": summaries,
        "comparisons": comparisons,
        "comparison_totals": comparison_totals,
        "decision_sessions": len(decisions),
        "outcome_sessions": len(outcomes),
        "decision_counts": {status.value: decision_counts[status] for status in DecisionStatus},
        "outcome_counts": {status.value: outcome_counts[status] for status in ObservationStatus},
        "authorizes_promotion": False,
        "limitations": [
            "Counts are campaign sessions, not individual model/symbol forecasts or qualified shadow decisions.",
            "The window uses latest projection event times, not a historical as-of reconstruction or complete scheduled coverage.",
            "Truncated counts are lower bounds; campaign metadata may be outside the loaded rows.",
            "A complete outcome is research evidence, not a fill, profit claim or qualification.",
            "Worker readiness, usable forecasts and matured outcomes are separate observations.",
            "Comparison denominators count enrollments pinned to loaded sessions, not all current enrollments or scheduled sessions.",
            "Missing comparison summaries remain unknown; primary status never substitutes for comparison evidence.",
        ],
    }


async def load_daily_panel_evidence(repository, *, days=DEFAULT_FORWARD_DAYS, limit=MAX_DAILY_EVIDENCE_LIMIT, now=None):
    """Shared passive daily summary for CLI and Telegram, bounded independently of intraday history."""
    now = utc_timestamp(now if now is not None else datetime.now(UTC))
    if not 1 <= days <= MAX_FORWARD_DAYS or not 1 <= limit <= MAX_FORWARD_LIMIT:
        raise ValueError("Daily-panel evidence query outside supported bounds")
    row_limit = min(limit, MAX_DAILY_EVIDENCE_LIMIT)
    daily_repository = DailyCampaignRepository(repository.store, policy=repository.policy)
    inputs = await daily_repository.report(since=now - timedelta(days=days), now=now, limit=row_limit)
    report = await asyncio.to_thread(
        build_daily_panel_evidence, repository.policy.daily_panel, inputs, now=now, days=days
    )
    report["row_limit"] = row_limit
    return report
