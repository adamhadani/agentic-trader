import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from agentic_trader.cli.commands import alpha_ic
from agentic_trader.cli.commands.alpha_ic import ic_group
from agentic_trader.cli.main import cli


ROOT = Path(__file__).resolve().parents[3]
COMPOSITE = ROOT / "config" / "research" / "ic" / "composite-ic-v1.json"


def _variant(tmp_path, mutate):
    document = json.loads(COMPOSITE.read_text())
    mutate(document)
    out = tmp_path / "composite.json"
    out.write_text(json.dumps(document))
    return out


class _Clients:
    bars = calendar = pace = None
    static_symbols = ()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


@pytest.fixture
def stubbed(monkeypatch):
    """The commands without a provider or a real read: what they hand to the execute functions is captured."""
    calls = {}
    monkeypatch.setattr(alpha_ic, "_apriori_clients", _Clients)
    monkeypatch.setattr(alpha_ic, "research_environment", lambda: {"runtime": {"revision": "abc"}})

    def capture(name, result):
        async def run(loaded, output, **kwargs):
            calls[name] = {"loaded": loaded, "output": output, **kwargs}
            return result

        monkeypatch.setattr(alpha_ic, name, run)

    calls["capture"] = capture
    return calls


def _checks_args(output, composite=COMPOSITE):
    return ["checks", "--composite", str(composite), "--cache", str(output.parent / "cache"), "--output", str(output)]


def _gate_args(output, composite=COMPOSITE):
    return [
        "gate",
        "--composite",
        str(composite),
        "--checks",
        str(output.parent),
        "--cache",
        str(output.parent / "cache"),
        "--output",
        str(output),
    ]


def test_cli_checks_and_gate_are_registered_under_alpha_ic():
    ic = cli.commands["alpha"].commands["ic"]
    assert {"sectors", "checks", "gate"} <= set(ic.commands)
    assert {param.name for param in ic.commands["checks"].params} == {"composite", "cache", "output"}
    assert {param.name for param in ic.commands["gate"].params} == {"composite", "checks_dir", "cache", "output"}


@pytest.mark.parametrize("build_args", [_checks_args, _gate_args])
def test_cli_refuses_an_existing_output(tmp_path, build_args, stubbed):
    output = tmp_path / "out"
    output.mkdir()
    result = CliRunner().invoke(ic_group, build_args(output))
    assert result.exit_code != 0
    assert "already exists" in result.output


@pytest.mark.parametrize("build_args", [_checks_args, _gate_args])
@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (lambda d: d["sectors"].update(sha256="0" * 64), "sector map digest"),
        (lambda d: d["decisive"].update(sha256="0" * 64), "decisive"),
        (lambda d: d["secondary"].update(sha256="0" * 64), "secondary"),
        (lambda d: d["sectors"].update(path="config/research/ic/not-there.json"), "not-there"),
    ],
)
def test_cli_refuses_a_broken_pin_and_creates_no_output(tmp_path, build_args, mutate, needle, stubbed):
    stubbed["capture"]("execute_checks", {"status": "passed"})
    stubbed["capture"]("execute_gate", {"status": "completed", "decision": "pass"})
    output = tmp_path / "out"
    result = CliRunner().invoke(ic_group, build_args(output, _variant(tmp_path, mutate)))
    assert result.exit_code != 0
    assert needle in result.output
    assert not output.exists()
    assert "execute_checks" not in stubbed
    assert "execute_gate" not in stubbed


def test_cli_refuses_an_invalid_composite(tmp_path, stubbed):
    output = tmp_path / "out"
    result = CliRunner().invoke(ic_group, _checks_args(output, _variant(tmp_path, lambda d: d.update(surprise=1))))
    assert result.exit_code != 0
    assert not output.exists()


def test_cli_checks_hands_over_the_verified_pins(tmp_path, stubbed):
    stubbed["capture"]("execute_checks", {"status": "passed", "composite_sha256": "x"})
    output = tmp_path / "out"
    result = CliRunner().invoke(ic_group, _checks_args(output))
    assert result.exit_code == 0, result.output
    call = stubbed["execute_checks"]
    assert call["loaded"].protocol.id == "composite-ic"
    assert call["sectors"].sha256 == call["loaded"].protocol.sectors.sha256
    assert call["environment"] == {"runtime": {"revision": "abc"}}
    assert callable(call["build"])
    assert callable(call["progress"])


def test_cli_a_failed_check_exits_non_zero(tmp_path, stubbed):
    stubbed["capture"]("execute_checks", {"status": "failed", "reason": "A-IC power"})
    result = CliRunner().invoke(ic_group, _checks_args(tmp_path / "out"))
    assert result.exit_code != 0
    assert "A-IC power" in result.output


def test_cli_a_gate_fail_is_a_completed_read_and_exits_zero(tmp_path, stubbed):
    stubbed["capture"]("execute_gate", {"status": "completed", "decision": "fail"})
    result = CliRunner().invoke(ic_group, _gate_args(tmp_path / "out"))
    assert result.exit_code == 0, result.output
    assert '"decision": "fail"' in result.output
    call = stubbed["execute_gate"]
    assert call["checks_dir"] == tmp_path
    assert callable(call["build"]) and callable(call["build_secondary"])
    assert call["build"] is not call["build_secondary"]


def test_cli_shows_the_secondary_status(tmp_path, stubbed):
    stubbed["capture"](
        "execute_gate",
        {"status": "completed", "decision": "pass", "secondary": {"status": "failed", "reason": "RuntimeError"}},
    )
    result = CliRunner().invoke(ic_group, _gate_args(tmp_path / "out"))
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["secondary_status"] == "failed"


def test_cli_sends_a_raised_message_to_stderr_only(tmp_path, stubbed, monkeypatch):
    def broken_builder(*_args):
        async def build():
            raise RuntimeError("provider-secret-text")

        return build

    monkeypatch.setattr(alpha_ic, "_cube_builder", broken_builder)
    output = tmp_path / "out"
    result = CliRunner().invoke(ic_group, _checks_args(output))
    assert result.exit_code != 0
    assert "provider-secret-text" in result.stderr
    assert "provider-secret-text" not in result.stdout
    assert json.loads(result.stdout)["reason"] == "RuntimeError"
    assert "provider-secret-text" not in (output / "result.json").read_text()


@pytest.mark.parametrize("status", ["refused", "failed"])
def test_cli_a_gate_without_a_read_exits_non_zero(tmp_path, stubbed, status):
    stubbed["capture"]("execute_gate", {"status": status, "reason": "why not"})
    result = CliRunner().invoke(ic_group, _gate_args(tmp_path / "out"))
    assert result.exit_code != 0
    assert "why not" in result.output


async def test_cube_builder_returns_the_cached_cube_and_its_spy(monkeypatch, tmp_path):
    seen = {}

    def fake_pooled_build(clients, cohort, spec, cache, *, require_cached):
        seen.update(clients=clients, cohort=cohort, spec=spec, cache=cache, require_cached=require_cached)

        async def build():
            return "built"

        return build

    monkeypatch.setattr(alpha_ic, "_pooled_build", fake_pooled_build)
    monkeypatch.setattr(alpha_ic, "load_spy", lambda built, cache: f"spy of {built} in {cache}")

    class Protocol:
        @staticmethod
        def cube_spec():
            return "spec"

    class Loaded:
        protocol = Protocol

    builder = alpha_ic._cube_builder("clients", (Loaded, "cohort"), tmp_path)
    assert await builder() == ("built", f"spy of built in {tmp_path}")
    assert seen == {"clients": "clients", "cohort": "cohort", "spec": "spec", "cache": tmp_path, "require_cached": True}
