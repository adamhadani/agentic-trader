import json

import httpx
from click.testing import CliRunner

from agentic_trader.cli.commands import alpha_short_volume as cmd


HEADER = "Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market\n"


def _file(stamp: str) -> bytes:
    return (
        (HEADER + "".join(f"{stamp}|S{i:04d}|{i}|0|{2 * i + 1}|B,Q,N\n" for i in range(1000)) + "1000\n")
        .replace("\n", "\r\n")
        .encode()
    )


def _patch(monkeypatch, seen):
    def handler(request):
        stamp = request.url.path.rsplit("CNMSshvol", 1)[1].removesuffix(".txt")
        seen.append(stamp)
        return httpx.Response(200, content=_file(stamp))

    real = httpx.AsyncClient
    monkeypatch.setattr(cmd.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(cmd, "research_environment", lambda: {"python": "x"})

    async def no_pace():
        return None

    monkeypatch.setattr(cmd, "_pacer", lambda: no_pace)


def _args(tmp_path, *extra):
    return [
        "--from",
        "2026-09-03",
        "--through",
        "2026-09-09",
        "--cache",
        str(tmp_path / "cache"),
        "--output",
        str(tmp_path / "out"),
        *extra,
    ]


def test_command_requests_nyse_sessions_only_and_writes_manifest(tmp_path, monkeypatch):
    seen: list[str] = []
    _patch(monkeypatch, seen)
    result = CliRunner().invoke(cmd.short_volume_cmd, _args(tmp_path))
    assert result.exit_code == 0, result.output
    # Thu 3, Fri 4, (weekend), (Mon 7 = Labor Day), Tue 8, Wed 9
    assert sorted(seen) == ["20260903", "20260904", "20260908", "20260909"]
    manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
    assert manifest["files_sha256"] and manifest["sessions_present"] == 4 and manifest["environment"] == {"python": "x"}
    assert oct((tmp_path / "out" / "manifest.json").stat().st_mode & 0o777) == "0o600"


def test_command_refuses_existing_output(tmp_path, monkeypatch):
    _patch(monkeypatch, [])
    (tmp_path / "out").mkdir()
    result = CliRunner().invoke(cmd.short_volume_cmd, _args(tmp_path))
    assert result.exit_code != 0 and "exists" in result.output


def test_command_fails_on_absent_session_unless_allowed(tmp_path, monkeypatch):
    def handler(request):
        return httpx.Response(403)

    real = httpx.AsyncClient
    monkeypatch.setattr(cmd.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(cmd, "research_environment", lambda: {"python": "x"})

    async def no_pace():
        return None

    monkeypatch.setattr(cmd, "_pacer", lambda: no_pace)
    args = ["--from", "2026-09-08", "--through", "2026-09-08", "--cache", str(tmp_path / "c"), "--output"]
    bad = CliRunner().invoke(cmd.short_volume_cmd, [*args, str(tmp_path / "o1")])
    assert bad.exit_code != 0 and "absent" in bad.output
    ok = CliRunner().invoke(cmd.short_volume_cmd, [*args, str(tmp_path / "o2"), "--allow-absent", "2026-09-08"])
    assert ok.exit_code == 0, ok.output
    assert json.loads((tmp_path / "o2" / "manifest.json").read_text())["sessions_absent"] == ["2026-09-08"]
