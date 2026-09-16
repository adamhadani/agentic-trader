"""Explicit sampling and artifact adapters for formulaic research."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd


BAR_DURATIONS = {
    "15m": pd.Timedelta(minutes=15),
    "1h": pd.Timedelta(hours=1),
    "4h": pd.Timedelta(hours=4),
    "1d": pd.Timedelta(days=1),
}


def completed_bars(frame: pd.DataFrame, timeframe: str, *, as_of: datetime | None = None) -> pd.DataFrame:
    """Conservative start-labelled bar closure; never include the forming bar.

    Daily bars become eligible the next UTC day. This deliberately does not guess
    holiday/early-close completion without a session-calendar observation.
    """
    if frame.empty:
        return frame
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise TypeError("Datetime observations required")
    now = pd.Timestamp(as_of or datetime.now(UTC))
    index = frame.index.tz_localize("UTC") if frame.index.tz is None else frame.index.tz_convert("UTC")
    result = frame.loc[index + BAR_DURATIONS[timeframe] <= now].copy()
    result.attrs["timeframe"] = timeframe
    return result


def save_dataset(frame: pd.DataFrame, directory: Path, digest: str) -> Path:
    """Local immutable binary artifact; never load untrusted pickles or commit data."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    metadata = json.dumps(frame.attrs, sort_keys=True)
    metadata_hash = hashlib.sha256(metadata.encode()).hexdigest()
    path = directory / f"{digest}-{metadata_hash}.npz"
    if not path.exists():
        # Publish only a complete file; concurrent identical writers are harmless.
        fd, temporary = tempfile.mkstemp(dir=directory, suffix=".npz")
        try:
            with os.fdopen(fd, "wb") as target:
                np.savez_compressed(
                    target,
                    values=frame.to_numpy(dtype=float),
                    columns=np.array(frame.columns, dtype=str),
                    timestamps=frame.index.asi8,
                    metadata=np.array(metadata),
                    index_unit=np.array(frame.index.unit),
                )
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return path


def load_dataset(path: Path) -> pd.DataFrame:

    with np.load(path, allow_pickle=False) as saved:
        index = pd.DatetimeIndex(pd.to_datetime(saved["timestamps"], unit=str(saved["index_unit"]), utc=True))
        frame = pd.DataFrame(saved["values"], columns=saved["columns"].tolist(), index=index)
        frame.attrs.update(json.loads(str(saved["metadata"])))
        return frame
