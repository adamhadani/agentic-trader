"""Lossless decoded Alpaca bar pages, before SDK parsing and OHLCV cleaning.

Each acquisition owns an immutable manifest, pages and completion receipt. There
is no global 'last response', new journal, replay, or automatic evidence deletion.
An unfinished directory is evidence of interruption, never a complete dataset.
"""

import hashlib
import json
import logging
import shutil
from datetime import UTC, datetime
from enum import StrEnum
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

from agentic_trader.config import MarketDataEvidenceConfig
from agentic_trader.storage.artifacts import save_json_report
from agentic_trader.transport.alpaca import ResponsePage


logger = logging.getLogger(__name__)
BAR_EVIDENCE_VERSION = "alpaca_bar_evidence_v1"
BAR_QUERY_FIELDS = frozenset(
    {"symbols", "timeframe", "start", "end", "feed", "adjustment", "limit", "page_token", "sort", "asof"}
)


class CaptureStatus(StrEnum):
    STARTED = "started"
    COMPLETE = "complete"
    FAILED = "failed"
    INCOMPLETE = "incomplete"


class EvidenceCapacityError(ValueError):
    """Acquisition cannot continue without exceeding its evidence budget."""


class BarAcquisitionError(ValueError):
    def __init__(self, error_type: str, evidence: dict):
        self.evidence = evidence
        super().__init__(f"Alpaca bars unavailable: {error_type}; evidence={evidence['artifact']}")


def artifact_reference(path: Path) -> dict:
    return {"artifact": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


class BarEvidenceStore:
    def __init__(self, directory: Path, policy: MarketDataEvidenceConfig):
        self.directory = directory
        self.policy = policy.model_copy(deep=True)

    def begin(self, request: dict) -> BarCapture:
        return BarCapture(self.directory, self.policy, request)


class BarCapture:
    def __init__(self, directory: Path, policy: MarketDataEvidenceConfig, request: dict):
        self.policy = policy
        self.capture_id = str(uuid4())
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory = directory / datetime.now(UTC).date().isoformat() / self.capture_id
        self.directory.parent.mkdir(exist_ok=True, mode=0o700)
        self.directory.mkdir(mode=0o700)
        self.pages: list[dict] = []
        self.bytes_written = 0
        self.manifest = self._write(
            {
                "version": BAR_EVIDENCE_VERSION,
                "capture_id": self.capture_id,
                "requested_at": datetime.now(UTC).isoformat(),
                "request": request,
                "sdk_version": version("alpaca-py"),
                "pandas_version": version("pandas"),
                "policy": policy.model_dump(),
            },
            "manifest.json",
            enforce_budget=False,
        )
        logger.info(
            "Bar acquisition started",
            extra={
                "event": "bar_acquisition",
                "observation_id": self.capture_id,
                "artifact_hash": self.manifest["sha256"],
                "status": CaptureStatus.STARTED,
            },
        )

    def check_capacity(self) -> None:
        if shutil.disk_usage(self.directory).free < self.policy.min_free_bytes:
            raise EvidenceCapacityError("Insufficient free space for raw data capture")

    def _write(self, document: dict, name: str, *, enforce_budget: bool = True) -> dict:
        size = len(json.dumps(document, sort_keys=True, indent=2, allow_nan=False).encode())
        if enforce_budget and (
            self.bytes_written + size > self.policy.max_capture_bytes
            or shutil.disk_usage(self.directory).free - size < self.policy.min_free_bytes
        ):
            raise EvidenceCapacityError("Raw data capture byte/free-space budget exceeded; no automatic deletion")
        path = save_json_report(document, self.directory / name)
        self.bytes_written += size
        return artifact_reference(path)

    def observe(self, page: ResponsePage) -> None:
        if page.method != "GET" or not page.path.endswith("/bars"):
            raise ValueError("Bar capture received an unrelated endpoint")
        if len(self.pages) >= self.policy.max_pages:
            raise EvidenceCapacityError("Raw data capture page budget exceeded")
        ref = self._write(
            {
                "requested_at": page.requested_at.isoformat(),
                "received_at": page.received_at.isoformat(),
                "path": page.path,
                "parameters": {
                    k: str(v) for k, v in page.parameters.items() if k in BAR_QUERY_FIELDS and v is not None
                },
                "response": page.response,
            },
            f"page-{len(self.pages):04d}.json",
        )
        bars = page.response.get("bars", {}) if isinstance(page.response, dict) else {}
        rows = (
            [r for values in bars.values() if isinstance(values, list) for r in values]
            if isinstance(bars, dict)
            else []
        )
        self.pages.append({**ref, "raw_rows": len(rows), "null_rows": sum(r is None for r in rows)})

    def finish(self, *, normalization: dict | None = None, error: Exception | None = None) -> dict:
        status = CaptureStatus.FAILED if error else CaptureStatus.COMPLETE
        document = {
            "version": BAR_EVIDENCE_VERSION,
            "capture_id": self.capture_id,
            "manifest": self.manifest,
            "finished_at": datetime.now(UTC).isoformat(),
            "pages": self.pages,
            "status": status,
            "normalization": normalization,
        }
        if error is not None:
            # Exception strings can contain request/auth details. Retain only typed status.
            document["error_type"] = type(error).__name__
            document["http_status"] = getattr(error, "status_code", None)
        reference = self._write(document, "result.json", enforce_budget=error is None)
        logger.log(
            logging.WARNING if error else logging.INFO,
            "Bar acquisition %s",
            status,
            extra={
                "event": "bar_acquisition",
                "observation_id": self.capture_id,
                "artifact_hash": reference["sha256"],
                "status": status,
            },
        )
        return reference

    def fail(self, error: Exception) -> dict:
        try:
            return self.finish(error=error)
        except OSError as write_error:
            # A full/unwritable filesystem cannot publish a completion receipt.
            # Preserve the manifest pointer and prior pages, never return bars.
            logger.error(
                "Bar evidence completion unavailable",
                extra={
                    "event": "bar_acquisition",
                    "observation_id": self.capture_id,
                    "artifact_hash": self.manifest["sha256"],
                    "status": CaptureStatus.INCOMPLETE,
                },
            )
            return {**self.manifest, "incomplete": True, "completion_error_type": type(write_error).__name__}
