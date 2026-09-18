"""Read-only artifact adapter for the shared, precharged daily research workflow."""

import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path

from agentic_trader.market.bars import SessionSchedule, utc_timestamp
from agentic_trader.research.alpha.data import load_dataset
from agentic_trader.research.alpha.forecast_controls_plan import ForecastControlsPlan
from agentic_trader.research.alpha.validation import frame_digest


def _verified_bytes(path: Path, digest: str) -> bytes:
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError(f"Retained artifact hash mismatch: {path.name}")
    return data


class RetainedPanelSource:
    """No I/O at construction. Calendar access follows journal reservation/exclusions.

    Current workflow receipts measure artifact reads. Original provider receipts and
    dataset attributes stay unchanged in the hash-bound parent artifact chain.
    """

    def __init__(self, directory: Path, plan: ForecastControlsPlan):
        self.directory = directory
        self.plan = plan
        self._result: dict | None = None
        self._datasets: dict = {}

    @property
    def parent_result(self) -> dict:
        if self._result is None:
            raise RuntimeError("Read the verified calendar after reservation before using parent forecasts")
        return self._result

    def calendar(self, start, end):
        if (start, end) != (self.plan.start, self.plan.end):
            raise ValueError("Exact retained calendar range required")
        self._result = None
        self._datasets = {}
        result = json.loads(_verified_bytes(self.directory / "result.json", self.plan.parent_result_sha256))
        documents = {
            name: json.loads(_verified_bytes(self.directory / f"{name}.json", result[f"{name}_hash"]))
            for name in ("manifest", "inputs", "calendar")
        }
        manifest, inputs, calendar = (documents[name] for name in ("manifest", "inputs", "calendar"))
        parent = self.plan.parent
        receipts = inputs["receipts"]
        now = utc_timestamp(datetime.now(UTC))
        if not (
            result["status"] == "completed"
            and result["plan_id"] == manifest["plan_id"] == parent.identity
            and result["run_id"] == manifest["run_id"]
            and result["completed_comparisons"] == result["charged_trials"] == parent.trial_count
            and manifest["plan"] == parent.document()
            and not inputs["failures"]
            and set(inputs["datasets"]) == set(parent.acquisition_symbols)
            and receipts
            and all(
                utc_timestamp(manifest["as_of"])
                <= utc_timestamp(r["requested_at"])
                <= utc_timestamp(r["received_at"])
                <= now
                for r in receipts
            )
        ):
            raise ValueError("Complete exact parent study and original observation receipts required")
        schedule = SessionSchedule.from_document(calendar)
        if (schedule.start, schedule.end, schedule.source) != (start, end, "alpaca_calendar"):
            raise ValueError("Exact original observed exchange calendar required")
        self._result = result
        self._datasets = inputs["datasets"]
        return schedule.sessions

    def daily(self, symbol, start, end, feed, adjustment="raw"):
        _ = self.parent_result
        if (start, end, feed, adjustment) != (
            self.plan.start,
            self.plan.end,
            self.plan.feed,
            self.plan.adjustment,
        ) or symbol not in self._datasets:
            raise ValueError("Exact retained member and source contract required")
        metadata = self._datasets[symbol]
        name = metadata["artifact"]
        if not isinstance(name, str) or Path(name).name != name or Path(name).suffix != ".npz":
            raise ValueError("Local immutable dataset name required")
        payload = _verified_bytes(self.directory / name, metadata["artifact_hash"])
        # Load the same verified bytes, avoiding a second read of a mutable pathname.
        frame = load_dataset(io.BytesIO(payload))
        if (
            len(frame) != metadata["rows"]
            or frame_digest(frame) != metadata["content_hash"]
            or frame.attrs != metadata["attrs"]
        ):
            raise ValueError("Retained dataset content/metadata mismatch")
        return frame
