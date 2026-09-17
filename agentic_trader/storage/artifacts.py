"""Private, immutable filesystem artifacts shared by adapters and research."""

import json
import os
import tempfile
from pathlib import Path


def save_json_report(payload: dict, path: Path) -> Path:
    """Publish a complete private diagnostic artifact; preserve any existing file."""
    encoded = json.dumps(payload, sort_keys=True, indent=2, allow_nan=False).encode()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".json")
    try:
        with os.fdopen(fd, "wb") as target:
            target.write(encoded)
            target.flush()
            os.fsync(target.fileno())
        # Atomic exclusive publication: an existing operator file is not overwritten.
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path
