"""Actual diagnostic composition without runtime DB, provider, broker or bot."""

import json

from click.testing import CliRunner

from agentic_trader.cli.main import cli
from agentic_trader.research.alpha.power_study import FamilySnapshot, PowerProtocol


def test_power_cli_preserves_frozen_family_and_runs_actual_search(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Power diagnostic cannot construct runtime services")

    for name in ("load_config", "SignalDatabase", "AlpacaDataProvider"):
        monkeypatch.setattr(f"agentic_trader.cli.commands.alpha.{name}", forbidden)
    snapshot = FamilySnapshot(
        captured_at="2026-09-18T10:00:00Z",
        source_revision="a" * 40,
        global_count=7826,
        sharpes=(),
        projection_events={"family/all": 1},
    )
    family = tmp_path / "family.json"
    family.write_text(snapshot.model_dump_json())
    protocol_path = tmp_path / "protocol.json"
    planned = CliRunner().invoke(
        cli,
        ["alpha", "power-plan", "--seed", "198765", "--family-snapshot", str(family), "--output", str(protocol_path)],
    )
    assert planned.exit_code == 0, planned.output
    protocol = PowerProtocol.from_document(json.loads(protocol_path.read_text()))
    assert protocol.family_snapshot_hash == snapshot.identity
    # Separate tiny fixture protocol; the operator default remains the bounded full matrix.
    protocol = protocol.model_copy(
        update={"observations": 600, "development_replicates": 1, "null_replicates": 1, "edge_replicates": 1}
    )
    protocol_path.write_text(json.dumps(protocol.document()))
    output = tmp_path / "run"
    result = CliRunner().invoke(
        cli, ["alpha", "power-study", str(protocol_path), "--family-snapshot", str(family), "--output", str(output)]
    )
    assert result.exit_code == 0, result.output
    completion = json.loads((output / "completion.json").read_text())
    assert completion["recorded_jobs"] == completion["expected_jobs"] == 16
    assert completion["failed_jobs"] == completion["unavailable_endpoints"] == 0
    assert completion["status"] == "criteria_not_met" and not completion["authorizes_promotion"]
    assert len(list((output / "selection").glob("*.json"))) == 16
    for path in output.rglob("*.json"):
        assert path.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob("*.db"))
