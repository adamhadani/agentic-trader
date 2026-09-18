"""Shared immutable capture and cancellation boundaries for daily research readers."""

import asyncio
import hashlib
from pathlib import Path

import pandas as pd

from agentic_trader.research.alpha.data import save_dataset
from agentic_trader.research.alpha.validation import frame_digest


def save_observations(frame: pd.DataFrame, output: Path):
    digest = frame_digest(frame)
    path = save_dataset(frame, output, digest)
    return {
        "artifact": path.name,
        "content_hash": digest,
        "artifact_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
        "rows": len(frame),
        "attrs": frame.attrs,
    }


async def drain_on_cancel(operation):
    """Finish the current bounded read/checkpoint before its source context can close."""
    task = asyncio.create_task(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        task.result()
        raise


class AcquisitionBudgetExceeded(RuntimeError):
    """The next read was not attempted because the frozen elapsed budget expired."""
