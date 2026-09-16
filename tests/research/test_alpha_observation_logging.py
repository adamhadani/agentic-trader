import json
import logging

from agentic_trader.runtime import RuntimeLogFormatter


def test_capture_log_retains_ids_needed_to_find_durable_evidence():
    record = logging.makeLogRecord(
        {
            "msg": "Session observation retained",
            "levelname": "INFO",
            "name": "copilot",
            "event": "alpha_session_observation",
            "observation_id": "capture-id",
            "status": "unavailable",
            "artifact_hash": "abc123",
            "unrelated_secret": "must-not-appear",
        }
    )
    rendered = RuntimeLogFormatter().format(record)
    payload = json.loads(rendered)
    assert payload["observation_id"] == "capture-id"
    assert payload["artifact_hash"] == "abc123"
    assert payload["status"] == "unavailable"
    assert "unrelated_secret" not in rendered
