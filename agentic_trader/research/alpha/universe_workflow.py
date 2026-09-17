"""Metadata-only prospective cohort capture over the existing diagnostic journal."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

import httpx

from agentic_trader.data.equity_metadata import (
    MetadataClockError,
    metadata_references,
    validate_capture_receipts,
    validate_receipt_sequence,
)
from agentic_trader.data.evidence import artifact_reference
from agentic_trader.data.symbol_directory import DIRECTORY_NAMES
from agentic_trader.research.alpha.equity_universe import (
    UNIVERSE_VERSION,
    EquityUniversePlan,
    build_snapshot,
    verify_snapshot,
)
from agentic_trader.storage.alpha import AlphaRepository
from agentic_trader.storage.artifacts import save_json_report


class MetadataSource(Protocol):
    def assets(self, output: Path) -> list[dict]: ...
    def directory(self, name: str, output: Path) -> dict: ...


class EquityUniverseService:
    def __init__(self, repository: AlphaRepository, source: MetadataSource):
        self.repository = repository
        self.source = source

    async def run(self, plan: EquityUniversePlan, output: Path, *, environment: dict, previous: dict | None = None):
        if previous is not None:
            await asyncio.to_thread(verify_snapshot, previous)
        await asyncio.to_thread(output.mkdir, parents=True, mode=0o700)
        run_id = uuid4().hex
        started_at = datetime.now(UTC).isoformat()
        await asyncio.to_thread(
            save_json_report,
            {
                "run_id": run_id,
                "plan_id": plan.identity,
                "plan": plan.document(),
                "created_at": started_at,
                "environment": environment,
                "previous_snapshot_id": previous["snapshot_id"] if previous else None,
            },
            output / "manifest.json",
        )
        # A fixed metadata-selection attempt, not one alpha test per listed security.
        await self.repository.reserve_run(run_id, symbol="__EQUITY_UNIVERSE__", timeframe="metadata", trials=1)
        receipts: list[dict] = []

        async def acquire(method, *args):
            receipt = {"method": method, "arguments": list(args), "requested_at": datetime.now(UTC).isoformat()}
            try:
                previous = receipts[-1]["received_at"] if receipts else started_at
                validate_receipt_sequence([{**receipt, "received_at": receipt["requested_at"]}], previous)
                return await asyncio.to_thread(lambda: getattr(self.source, method)(*args, output))
            except Exception as exc:
                receipt["error_type"] = type(exc).__name__
                receipt["http_status"] = getattr(exc, "status_code", None)
                if isinstance(exc, httpx.HTTPStatusError):
                    receipt["http_status"] = exc.response.status_code
                raise
            finally:
                receipt["received_at"] = datetime.now(UTC).isoformat()
                receipts.append(receipt)
                try:
                    validate_receipt_sequence(receipts, started_at)
                except MetadataClockError:
                    receipt["clock_error"] = "MetadataClockError"
                    raise

        snapshot = None
        try:
            assets = await acquire("assets")
            directories = tuple([await acquire("directory", name) for name in DIRECTORY_NAMES])
            snapshot = await asyncio.to_thread(
                build_snapshot,
                plan,
                assets,
                directories,
                observed_at=max(receipt["received_at"] for receipt in receipts),
                previous=previous,
                source_evidence=await asyncio.to_thread(validate_capture_receipts, output, receipts),
            )
            await asyncio.to_thread(save_json_report, snapshot, output / "snapshot.json")
            result = {
                "status": "completed",
                "snapshot_id": snapshot["snapshot_id"],
                "selected_count": snapshot["selected_count"],
                "eligible_count": snapshot["eligible_count"],
                "target_met": snapshot["target_met"],
                "snapshot": await asyncio.to_thread(artifact_reference, output / "snapshot.json"),
            }
        except Exception as exc:
            # Exception messages may contain URLs/headers; retain typed failures and raw successful responses only.
            result = {"status": "failed", "error_type": type(exc).__name__}
        inputs = {"receipts": receipts, "sources": await asyncio.to_thread(metadata_references, output)}
        await asyncio.to_thread(save_json_report, inputs, output / "inputs.json")
        result.update(
            run_id=run_id,
            plan_id=plan.identity,
            charged_trials=1,
            authorizes_promotion=False,
            price_reads=False,
            manifest=await asyncio.to_thread(artifact_reference, output / "manifest.json"),
            inputs=await asyncio.to_thread(artifact_reference, output / "inputs.json"),
        )
        await asyncio.to_thread(save_json_report, result, output / "result.json")
        reference = await asyncio.to_thread(artifact_reference, output / "result.json")
        await self.repository.record_diagnostic(
            run_id,
            {
                "kind": UNIVERSE_VERSION,
                **result,
                "artifact": reference["artifact"],
                "artifact_hash": reference["sha256"],
            },
        )
        return result
