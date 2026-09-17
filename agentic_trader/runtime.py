"""Runtime identity and resource boundaries shared by CLI, workers, and tests."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy.engine import make_url

from agentic_trader.constants import RuntimeEnvironment


RUN_ID = str(uuid4())


def state_directory() -> Path:
    """Private application artifacts; tests stay within their isolated root."""
    root = os.environ.get("COPILOT_TEST_ROOT")
    return Path(root) if root else Path.home() / ".local/state/agentic-trader"


def validate_test_database(url: str, *, root: Path | None = None, postgres_url: str | None = None) -> None:
    """Fail before connecting unless the target belongs to this isolated test run."""
    if root is None:
        if os.environ.get("COPILOT_ENV") != RuntimeEnvironment.TEST:
            return
        configured_root = os.environ.get("COPILOT_TEST_ROOT")
        if not configured_root:
            raise ValueError("Test database access requires COPILOT_TEST_ROOT.")
        root = Path(configured_root)
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite":
        if parsed.database in (None, "", ":memory:"):
            return
        if Path(parsed.database).resolve().is_relative_to(root.resolve()):
            return
    else:
        allowed = postgres_url or os.environ.get("TEST_POSTGRES_URL")
        if allowed:
            expected = make_url(allowed)
            if (
                expected.database
                and expected.database.startswith("test_")
                and parsed.set(drivername="postgresql") == expected.set(drivername="postgresql")
            ):
                return
    raise ValueError("Refusing test database access outside this run's temporary directory/disposable PostgreSQL DB.")


class RuntimeLogFormatter(logging.Formatter):
    """JSON logs with UTC time and correlation IDs; only allowlisted context is serialized."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "run_id": RUN_ID,
            "environment": os.environ.get("COPILOT_ENV", RuntimeEnvironment.PRODUCTION),
        }
        for key in (
            "event",
            "observation_id",
            "artifact_hash",
            "status",
            "signal_id",
            "broker_order_id",
            "contract",
            "direction",
            "exit_reason",
            "entry_price",
            "exit_price",
            "realized_pnl",
            "quantity",
            "strategy",
        ):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        rendered = json.dumps(payload, default=str)
        return re.sub(r"(https://api\.telegram\.org/bot)[^/\s\"]+", r"\1<redacted>", rendered)


def runtime_identity() -> dict[str, str | int]:
    """Capture the source revision at process startup for operational verification."""
    root = Path(__file__).resolve().parent.parent
    try:
        revision = subprocess.check_output(
            ["git", "-C", str(root), "describe", "--always", "--dirty"], text=True
        ).strip()
    except OSError, subprocess.CalledProcessError:
        revision = "unavailable"
    return {"pid": os.getpid(), "revision": revision, "run_id": RUN_ID}
